from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, Job, JobStatus, new_id, utcnow
from app.schemas import (
    CertificateOut,
    FailureOut,
    JobCreate,
    JobOut,
    Progress,
    validate_recipient,
)

MAX_FAILURES_IN_JOB_RESPONSE = 100


def get_job_by_idempotency_key(db: Session, idempotency_key: str) -> Job | None:
    return db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))


def create_job(db: Session, payload: JobCreate, idempotency_key: str | None = None) -> Job:
    """Persist the job and one Certificate row per submitted recipient.

    Invalid recipients are stored as FAILED rows with a reason; valid ones as PENDING.
    Nothing is generated here -- see services.processing.
    """
    now = utcnow()
    job = Job(
        id=new_id(),
        title=payload.title,
        course_name=payload.course_name,
        organization=payload.organization,
        issue_date=payload.issue_date or now.date(),
        status=JobStatus.PENDING.value,
        idempotency_key=idempotency_key,
        created_at=now,
    )

    seen_emails: set[str] = set()
    certificates: list[Certificate] = []
    for position, raw in enumerate(payload.recipients):
        recipient, error = validate_recipient(raw)
        if recipient is not None:
            if recipient.email in seen_emails:
                recipient, error = None, "duplicate recipient email in this request"
            else:
                seen_emails.add(recipient.email)

        cert = Certificate(id=new_id(), job_id=job.id, position=position, created_at=now)
        if recipient is not None:
            cert.recipient_name = recipient.name
            cert.recipient_email = recipient.email
            cert.status = CertificateStatus.PENDING.value
        else:
            # Keep whatever string data was sent so the client can find the bad row.
            if isinstance(raw, dict):
                name, email = raw.get("name"), raw.get("email")
                cert.recipient_name = name[:255] if isinstance(name, str) else None
                cert.recipient_email = email[:320] if isinstance(email, str) else None
            cert.status = CertificateStatus.FAILED.value
            cert.error = f"Validation failed - {error}"
            cert.completed_at = now
        certificates.append(cert)

    if all(c.status == CertificateStatus.FAILED.value for c in certificates):
        job.status = JobStatus.FAILED.value
        job.error = "No valid recipients in request"
        job.completed_at = now

    db.add(job)
    db.add_all(certificates)
    db.commit()
    return job


def retry_failed_certificates(db: Session, job: Job) -> list[Certificate]:
    """Re-queue generation failures for a finished job. Validation errors are left intact."""
    failed_certs = db.scalars(
        select(Certificate).where(
            Certificate.job_id == job.id,
            Certificate.status == CertificateStatus.FAILED.value,
        )
    ).all()

    retriable = [
        c
        for c in failed_certs
        if c.error is not None and c.error.startswith("Generation failed")
    ]
    if not retriable:
        return []

    for cert in retriable:
        cert.status = CertificateStatus.PENDING.value
        cert.error = None
        cert.completed_at = None
        cert.file_path = None

    job.status = JobStatus.PENDING.value
    job.error = None
    job.completed_at = None
    db.commit()
    return retriable


def get_counts(db: Session, job_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Certificate.status, func.count())
        .where(Certificate.job_id == job_id)
        .group_by(Certificate.status)
    ).all()
    counts = {s.value: 0 for s in CertificateStatus}
    counts.update({status: n for status, n in rows})
    return counts


def get_progress(db: Session, job_id: str) -> Progress:
    counts = get_counts(db, job_id)
    total = sum(counts.values())
    done = counts[CertificateStatus.SUCCEEDED.value] + counts[CertificateStatus.FAILED.value]
    return Progress(
        total=total,
        succeeded=counts[CertificateStatus.SUCCEEDED.value],
        failed=counts[CertificateStatus.FAILED.value],
        pending=counts[CertificateStatus.PENDING.value],
        percent_complete=round(done / total * 100, 1) if total else 100.0,
    )


def certificate_download_url(cert: Certificate) -> str | None:
    if cert.status == CertificateStatus.SUCCEEDED.value:
        return f"/api/v1/certificates/{cert.id}/download"
    return None


def to_certificate_out(cert: Certificate) -> CertificateOut:
    return CertificateOut(
        id=cert.id,
        job_id=cert.job_id,
        position=cert.position,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        status=cert.status,
        error=cert.error,
        completed_at=cert.completed_at,
        download_url=certificate_download_url(cert),
    )


def build_job_out(db: Session, job_id: str) -> JobOut | None:
    job = db.get(Job, job_id)
    if job is None:
        return None
    db.refresh(job)  # worker updates the row from another session

    progress = get_progress(db, job_id)
    failures = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.FAILED.value)
        .order_by(Certificate.position)
        .limit(MAX_FAILURES_IN_JOB_RESPONSE)
    ).all()

    finished = JobStatus(job.status).is_terminal
    return JobOut(
        id=job.id,
        status=job.status,
        title=job.title,
        course_name=job.course_name,
        organization=job.organization,
        issue_date=job.issue_date,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        error=job.error,
        progress=progress,
        failures=[
            FailureOut(
                position=f.position,
                recipient_name=f.recipient_name,
                recipient_email=f.recipient_email,
                error=f.error,
            )
            for f in failures
        ],
        failures_truncated=progress.failed > len(failures),
        certificates_url=f"/api/v1/jobs/{job.id}/certificates",
        download_url=(
            f"/api/v1/jobs/{job.id}/download" if finished and progress.succeeded else None
        ),
    )


def list_jobs(
    db: Session,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[JobOut]]:
    conditions = []
    if status is not None:
        conditions.append(Job.status == status)

    total = db.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0
    job_ids = db.scalars(
        select(Job.id)
        .where(*conditions)
        .order_by(Job.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    items = [build_job_out(db, j_id) for j_id in job_ids]
    return total, [item for item in items if item is not None]
