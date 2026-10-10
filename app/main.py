from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings
from app.database import apply_migrations, make_engine, make_session_factory
from app.generator import Generator, generate_certificate_pdf
from app.worker import JobRunner

logging.basicConfig(level=logging.INFO)


def create_app(settings: Settings | None = None, generator: Generator | None = None) -> FastAPI:
    """Application factory. ``generator`` can be injected (used by the tests)."""
    settings = settings or Settings.from_env()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.database_url)
    apply_migrations(settings.database_url)
    session_factory = make_session_factory(engine)
    runner = JobRunner(
        session_factory,
        generator or generate_certificate_pdf,
        settings.storage_dir,
        mode=settings.processing_mode,
        workers=settings.worker_threads,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.resume_unfinished_jobs:
            resumed = runner.resume_unfinished()
            if resumed:
                logging.getLogger(__name__).info("resumed %d unfinished job(s)", resumed)
        yield
        runner.shutdown()
        engine.dispose()

    app = FastAPI(title="Certificate Service", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.runner = runner
    app.include_router(router)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root():
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/docs")

    return app

