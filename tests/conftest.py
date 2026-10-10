import time
from contextlib import ExitStack
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def make_client(tmp_path):
    """Factory: ``make_client(generator=..., processing_mode=..., max_recipients_per_job=...)``.

    Defaults to *inline* processing so a job is finished by the time POST returns.
    """
    stack = ExitStack()

    def _make(generator=None, **overrides):
        settings = replace(
            Settings(
                database_url=f"sqlite:///{tmp_path / 'test.db'}",
                storage_dir=tmp_path / "certs",
                processing_mode="inline",
                resume_unfinished_jobs=False,
            ),
            **overrides,
        )
        return stack.enter_context(TestClient(create_app(settings, generator=generator)))

    yield _make
    stack.close()


@pytest.fixture
def client(make_client):
    return make_client()


def make_payload(n=3, **overrides):
    payload = {
        "course_name": "Python Bootcamp 2026",
        "organization": "Acme Academy",
        "recipients": [{"name": f"Student {i}", "email": f"student{i}@example.com"} for i in range(n)],
    }
    payload.update(overrides)
    return payload


def wait_for_job(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] not in ("pending", "processing"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish in time")
