import threading

from app.generator import generate_certificate_pdf
from tests.conftest import make_payload, wait_for_job


def test_completed_job_reports_full_progress(client):
    body = client.post("/api/v1/jobs", json=make_payload(4)).json()

    assert body["status"] == "completed"
    assert body["progress"] == {
        "total": 4, "succeeded": 4, "failed": 0, "pending": 0, "percent_complete": 100.0,
    }
    assert body["started_at"] and body["completed_at"]
    assert body["download_url"] == f"/api/v1/jobs/{body['id']}/download"


def test_progress_is_visible_while_job_is_running(make_client):
    reached_blocker = threading.Event()
    release = threading.Event()

    def gated(data, path):
        if data.recipient_name == "Blocker":
            reached_blocker.set()
            assert release.wait(timeout=10)
        generate_certificate_pdf(data, path)

    client = make_client(generator=gated, processing_mode="background", worker_threads=1)
    payload = make_payload(
        0,
        recipients=[
            {"name": "First", "email": "first@example.com"},
            {"name": "Blocker", "email": "blocker@example.com"},
        ],
    )

    created = client.post("/api/v1/jobs", json=payload)
    assert created.status_code == 202  # returned without waiting for generation
    job_id = created.json()["id"]

    try:
        assert reached_blocker.wait(timeout=10)
        running = client.get(f"/api/v1/jobs/{job_id}").json()
        assert running["status"] == "processing"
        assert running["progress"]["succeeded"] == 1
        assert running["progress"]["pending"] == 1
        assert running["progress"]["percent_complete"] == 50.0
        assert running["download_url"] is None  # not finished yet
        assert client.get(f"/api/v1/jobs/{job_id}/download").status_code == 409
    finally:
        release.set()

    done = wait_for_job(client, job_id)
    assert done["status"] == "completed"
    assert done["progress"]["succeeded"] == 2
