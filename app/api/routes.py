from __future__ import annotations

import os
import re
import tempfile
import zipfile
from collections.abc import Iterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import CertificateList, JobCreate, JobList, JobOut
from app.services import jobs as job_service


def verify_api_key(request: Request) -> None:
    expected_key = request.app.state.settings.api_key
    if not expected_key:
        return
    provided_key = request.headers.get("X-API-Key")
    if not provided_key or provided_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


router = APIRouter(prefix="/api/v1", dependencies=[Depends(verify_api_key)])


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as db:
        yield db


def _safe_filename(cert: Certificate) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", cert.recipient_name or "certificate").strip("-").lower()
    return f"{cert.position:05d}-{slug or 'certificate'}.pdf"


def _cert_path(request: Request, cert: Certificate):
    path = request.app.state.settings.storage_dir / (cert.file_path or "")
    if not cert.file_path or not path.is_file():
        raise HTTPException(status_code=410, detail="Certificate file is no longer available")
    return path


@router.post("/jobs", response_model=JobOut, status_code=202, tags=["jobs"])
def create_job(
    payload: JobCreate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="Optional unique key to ensure idempotent request execution.",
        examples=["9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"],
    ),
    db: Session = Depends(get_db),
):
    """Submit many recipients at once. Returns immediately; poll the job for progress."""
    if idempotency_key:
        existing = job_service.get_job_by_idempotency_key(db, idempotency_key)
        if existing:
            response.headers["Location"] = f"/api/v1/jobs/{existing.id}"
            return job_service.build_job_out(db, existing.id)

    limit = request.app.state.settings.max_recipients_per_job
    if len(payload.recipients) > limit:
        raise HTTPException(
            status_code=422,
            detail=f"Too many recipients: {len(payload.recipients)} (maximum {limit} per request)",
        )

    job = job_service.create_job(db, payload, idempotency_key=idempotency_key)
    job_id, status = job.id, job.status
    if status == JobStatus.PENDING.value:
        request.app.state.runner.submit(job_id)  # inline mode finishes before returning
        db.expire_all()

    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    return job_service.build_job_out(db, job_id)


@router.get("/jobs", response_model=JobList, tags=["jobs"])
def list_jobs(
    status: JobStatus | None = Query(default=None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List submitted jobs with pagination and optional status filter."""
    total, items = job_service.list_jobs(
        db, status=status.value if status else None, limit=limit, offset=offset
    )
    return JobList(total=total, limit=limit, offset=offset, items=items)


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
def get_job(job_id: str, db: Session = Depends(get_db)):
    """Status, progress counters and the list of failures (with reasons)."""
    out = job_service.build_job_out(db, job_id)
    if out is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return out


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=202, tags=["jobs"])
def retry_job(
    job_id: str, request: Request, response: Response, db: Session = Depends(get_db)
):
    """Re-queue generation-failed certificates of a finished job."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not JobStatus(job.status).is_terminal:
        raise HTTPException(status_code=409, detail="Job is still running")

    retried = job_service.retry_failed_certificates(db, job)
    if not retried:
        raise HTTPException(
            status_code=400, detail="No retriable failed certificates found for this job"
        )

    request.app.state.runner.submit(job_id)
    db.expire_all()

    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    return job_service.build_job_out(db, job_id)


@router.get("/jobs/{job_id}/certificates", response_model=CertificateList, tags=["certificates"])
def list_certificates(
    job_id: str,
    status: CertificateStatus | None = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    if db.get(Job, job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    conditions = [Certificate.job_id == job_id]
    if status is not None:
        conditions.append(Certificate.status == status.value)

    total = db.scalar(select(func.count()).select_from(Certificate).where(*conditions)) or 0
    rows = db.scalars(
        select(Certificate).where(*conditions).order_by(Certificate.position).limit(limit).offset(offset)
    ).all()
    return CertificateList(
        total=total,
        limit=limit,
        offset=offset,
        items=[job_service.to_certificate_out(c) for c in rows],
    )


@router.get("/certificates/{certificate_id}/download", tags=["certificates"])
def download_certificate(certificate_id: str, request: Request, db: Session = Depends(get_db)):
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    if cert.status != CertificateStatus.SUCCEEDED.value:
        raise HTTPException(
            status_code=409, detail=f"Certificate is not available (status: {cert.status})"
        )
    return FileResponse(
        _cert_path(request, cert), media_type="application/pdf", filename=_safe_filename(cert)
    )


@router.get("/jobs/{job_id}/download", tags=["certificates"])
def download_job_archive(job_id: str, request: Request, db: Session = Depends(get_db)):
    """All successfully generated certificates of a finished job as one zip."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if not JobStatus(job.status).is_terminal:
        raise HTTPException(status_code=409, detail="Job is still running")

    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.SUCCEEDED.value)
        .order_by(Certificate.position)
    ).all()
    if not certs:
        raise HTTPException(status_code=404, detail="Job has no generated certificates")

    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
        for cert in certs:
            zf.write(_cert_path(request, cert), arcname=_safe_filename(cert))
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"job-{job_id}-certificates.zip",
        background=BackgroundTask(os.unlink, tmp_path),
    )
