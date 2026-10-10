"""Runs jobs off the request path.

``JobRunner`` is the seam for swapping the execution backend: today an in-process
thread pool, tomorrow Celery/RQ/SQS -- only ``submit`` has to change.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.generator import Generator
from app.models import Job, JobStatus
from app.services.processing import mark_job_failed, process_job

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        generator: Generator,
        storage_dir: Path,
        mode: str = "background",
        workers: int = 2,
    ) -> None:
        if mode not in ("background", "inline"):
            raise ValueError("processing_mode must be 'background' or 'inline'")
        self._session_factory = session_factory
        self._generator = generator
        self._storage_dir = storage_dir
        self._mode = mode
        self._executor = (
            ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cert-worker")
            if mode == "background"
            else None
        )

    def submit(self, job_id: str) -> None:
        if self._executor is None:
            self._run(job_id)
        else:
            self._executor.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        try:
            process_job(self._session_factory, self._generator, self._storage_dir, job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s crashed", job_id)
            mark_job_failed(self._session_factory, job_id, str(exc))

    def resume_unfinished(self) -> int:
        """Re-queue jobs that were pending/processing when the process last stopped."""
        with self._session_factory() as db:
            ids = db.scalars(
                select(Job.id).where(
                    Job.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value])
                )
            ).all()
        for job_id in ids:
            self.submit(job_id)
        return len(ids)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
