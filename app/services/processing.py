"""The actual work: turn PENDING certificates of a job into PDFs.

Design notes
* One certificate at a time, committing after each, so progress is visible live and a
  crash loses at most the certificate being written.
* Only PENDING certificates are touched, which makes ``process_job`` safe to re-run:
  that is how unfinished jobs are resumed after a restart.
* Any exception from the generator fails *that certificate only*.
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.generator import CertificateData, Generator
from app.models import Certificate, CertificateStatus, Job, JobStatus, utcnow
from app.services.jobs import get_counts

logger = logging.getLogger(__name__)


def _process_certificate(
    db: Session, job: Job, cert: Certificate, generator: Generator, storage_dir: Path
) -> None:
    relative = f"{job.id}/{cert.id}.pdf"
    data = CertificateData(
        certificate_id=cert.id,
        recipient_name=cert.recipient_name or "",
        course_name=job.course_name,
        organization=job.organization,
        title=job.title,
        issue_date=job.issue_date,
    )
    try:
        generator(data, storage_dir / relative)
    except Exception as exc:  # noqa: BLE001 - isolate every per-certificate failure
        logger.warning("certificate %s (job %s) failed: %s", cert.id, job.id, exc)
        (storage_dir / relative).unlink(missing_ok=True)
        cert.status = CertificateStatus.FAILED.value
        cert.error = f"Generation failed - {exc or exc.__class__.__name__}"[:1000]
    else:
        cert.status = CertificateStatus.SUCCEEDED.value
        cert.file_path = relative
        cert.error = None
    cert.completed_at = utcnow()
    db.commit()


def _finalize(db: Session, job: Job) -> None:
    counts = get_counts(db, job.id)
    total = sum(counts.values())
    succeeded = counts[CertificateStatus.SUCCEEDED.value]
    if succeeded == total:
        job.status = JobStatus.COMPLETED.value
    elif succeeded == 0:
        job.status = JobStatus.FAILED.value
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS.value
    job.completed_at = utcnow()
    db.commit()


def process_job(
    session_factory: sessionmaker[Session],
    generator: Generator,
    storage_dir: Path,
    job_id: str,
) -> None:
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None or JobStatus(job.status).is_terminal:
            return
        job.status = JobStatus.PROCESSING.value
        job.started_at = job.started_at or utcnow()
        db.commit()

        cert_ids = db.scalars(
            select(Certificate.id)
            .where(
                Certificate.job_id == job_id,
                Certificate.status == CertificateStatus.PENDING.value,
            )
            .order_by(Certificate.position)
        ).all()

        for cert_id in cert_ids:
            cert = db.get(Certificate, cert_id)
            if cert is None or cert.status != CertificateStatus.PENDING.value:
                continue
            _process_certificate(db, job, cert, generator, storage_dir)

        _finalize(db, job)


def mark_job_failed(session_factory: sessionmaker[Session], job_id: str, reason: str) -> None:
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.FAILED.value
        job.error = f"Unexpected error: {reason}"[:1000]
        job.completed_at = utcnow()
        db.commit()
