from dataclasses import replace
import pytest
from sqlalchemy import select
from app.config import Settings
from app.database import apply_migrations, make_engine, make_session_factory
from app.models import Certificate, CertificateStatus, Job, JobStatus, utcnow
from app.schemas import JobCreate
from app.services import jobs as job_service
from app.services.processing import mark_job_failed
from tests.conftest import make_payload


def test_restart_recovery_resumes_pending_and_processing_jobs(make_client, tmp_path):
    db_path = tmp_path / "recovery.db"
    db_url = f"sqlite:///{db_path}"
    storage_dir = tmp_path / "certs"

    apply_migrations(db_url)
    engine = make_engine(db_url)
    session_factory = make_session_factory(engine)

    # 1. Create a job left in PENDING state
    with session_factory() as db:
        payload_1 = JobCreate.model_validate(
            make_payload(2, course_name="Pending Course")
        )
        job_pending = job_service.create_job(db, payload_1)
        job_pending_id = job_pending.id
        assert job_pending.status == JobStatus.PENDING.value

    # 2. Create another job left in PROCESSING state where 1 cert succeeded, 1 is pending
    with session_factory() as db:
        payload_2 = JobCreate.model_validate(
            make_payload(2, course_name="Interrupted Course")
        )
        job_interrupted = job_service.create_job(db, payload_2)
        job_interrupted_id = job_interrupted.id
        job_interrupted.status = JobStatus.PROCESSING.value
        job_interrupted.started_at = utcnow()

        # Mark first certificate as already succeeded
        certs = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job_interrupted_id)
            .order_by(Certificate.position)
        ).all()
        certs[0].status = CertificateStatus.SUCCEEDED.value
        certs[0].file_path = f"{job_interrupted_id}/{certs[0].id}.pdf"
        certs[0].completed_at = utcnow()
        db.commit()

    # Now start an app with resume_unfinished_jobs=True and processing_mode="inline"
    client = make_client(
        database_url=db_url,
        storage_dir=storage_dir,
        processing_mode="inline",
        resume_unfinished_jobs=True,
    )

    # Both jobs should have been resumed and completed
    res_pending = client.get(f"/api/v1/jobs/{job_pending_id}").json()
    assert res_pending["status"] == "completed"
    assert res_pending["progress"]["succeeded"] == 2
    assert res_pending["progress"]["pending"] == 0

    res_interrupted = client.get(f"/api/v1/jobs/{job_interrupted_id}").json()
    assert res_interrupted["status"] == "completed"
    assert res_interrupted["progress"]["succeeded"] == 2
    assert res_interrupted["progress"]["pending"] == 0


def test_unexpected_job_level_crash_marks_job_failed(make_client, monkeypatch):
    client = make_client(processing_mode="inline")

    # Force process_job to raise an unexpected exception
    def crash_process_job(session_factory, generator, storage_dir, job_id):
        raise RuntimeError("Catastrophic disk corruption")

    monkeypatch.setattr("app.worker.process_job", crash_process_job)

    payload = make_payload(2)
    resp = client.post("/api/v1/jobs", json=payload)
    assert resp.status_code == 202
    job_id = resp.json()["id"]

    job_data = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job_data["status"] == "failed"
    assert "Catastrophic disk corruption" in job_data["error"]
    assert job_data["download_url"] is None


def test_mark_job_failed_directly(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'mark_failed.db'}"
    apply_migrations(db_url)
    engine = make_engine(db_url)
    session_factory = make_session_factory(engine)

    with session_factory() as db:
        payload = JobCreate.model_validate(make_payload(1))
        job = job_service.create_job(db, payload)
        job_id = job.id

    mark_job_failed(session_factory, job_id, "Worker process killed unexpectedly")

    with session_factory() as db:
        updated = db.get(Job, job_id)
        assert updated.status == JobStatus.FAILED.value
        assert "Worker process killed unexpectedly" in updated.error
        assert updated.completed_at is not None
