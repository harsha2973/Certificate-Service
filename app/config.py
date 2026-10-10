from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/certificates.db"
    storage_dir: Path = Path("./data/certificates")
    # "background": jobs run on an in-process worker pool (default).
    # "inline": jobs run inside the request (handy for tests / debugging).
    processing_mode: str = "background"
    worker_threads: int = 2
    max_recipients_per_job: int = 5000
    resume_unfinished_jobs: bool = True
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            database_url=os.getenv("DATABASE_URL", d.database_url),
            storage_dir=Path(os.getenv("STORAGE_DIR", str(d.storage_dir))),
            processing_mode=os.getenv("PROCESSING_MODE", d.processing_mode),
            worker_threads=int(os.getenv("WORKER_THREADS", d.worker_threads)),
            max_recipients_per_job=int(
                os.getenv("MAX_RECIPIENTS_PER_JOB", d.max_recipients_per_job)
            ),
            resume_unfinished_jobs=_bool(
                os.getenv("RESUME_UNFINISHED_JOBS"), d.resume_unfinished_jobs
            ),
            api_key=os.getenv("API_KEY", d.api_key),
        )
