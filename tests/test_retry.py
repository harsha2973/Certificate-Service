import pytest
from app.generator import generate_certificate_pdf
from tests.conftest import make_payload


def test_retry_generation_failures_succeeds(make_client):
    attempts = {}

    def flaky_once_generator(data, path):
        attempts[data.certificate_id] = attempts.get(data.certificate_id, 0) + 1
        if data.recipient_name == "Flaky Student" and attempts[data.certificate_id] == 1:
            raise RuntimeError("Transient network issue while generating PDF")
        generate_certificate_pdf(data, path)

    client = make_client(generator=flaky_once_generator)
    payload = make_payload(
        0,
        recipients=[
            {"name": "Alice", "email": "alice@example.com"},  # position 0: succeeds
            {"name": "", "email": "invalid@example.com"},  # position 1: validation failure
            {"name": "Flaky Student", "email": "flaky@example.com"},  # position 2: fails generation once
        ],
    )

    created = client.post("/api/v1/jobs", json=payload).json()
    job_id = created["id"]
    assert created["status"] == "completed_with_errors"
    assert created["progress"]["succeeded"] == 1
    assert created["progress"]["failed"] == 2

    # Verify positions and error types before retry
    certs = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    assert certs[0]["status"] == "succeeded"
    assert certs[1]["status"] == "failed"
    assert "Validation failed" in certs[1]["error"]
    assert certs[2]["status"] == "failed"
    assert "Generation failed" in certs[2]["error"]

    # Now call POST /api/v1/jobs/{job_id}/retry
    retry_resp = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert retry_resp.status_code == 202
    assert retry_resp.headers["Location"] == f"/api/v1/jobs/{job_id}"
    retry_body = retry_resp.json()

    # Position 2 was re-queued and succeeded; validation failure at 1 remains failed!
    assert retry_body["status"] == "completed_with_errors"
    assert retry_body["progress"]["total"] == 3
    assert retry_body["progress"]["succeeded"] == 2
    assert retry_body["progress"]["failed"] == 1

    # Check the certificates endpoint
    updated_certs = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"]
    assert updated_certs[0]["status"] == "succeeded"
    assert updated_certs[1]["status"] == "failed"
    assert "Validation failed" in updated_certs[1]["error"]
    assert updated_certs[2]["status"] == "succeeded"
    assert updated_certs[2]["download_url"] is not None

    # Check zip download now has 2 PDFs
    zip_resp = client.get(f"/api/v1/jobs/{job_id}/download")
    assert zip_resp.status_code == 200


def test_retry_unknown_job_returns_404(client):
    assert client.post("/api/v1/jobs/nonexistent/retry").status_code == 404


def test_retry_job_with_only_validation_failures_returns_400(client):
    payload = make_payload(0, recipients=[{"name": "", "email": "bad@example.com"}])
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    resp = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert resp.status_code == 400
    assert "No retriable failed certificates found" in resp.json()["detail"]


def test_retry_job_with_all_succeeded_returns_400(client):
    payload = make_payload(2)
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    resp = client.post(f"/api/v1/jobs/{job_id}/retry")
    assert resp.status_code == 400
    assert "No retriable failed certificates found" in resp.json()["detail"]


def test_retry_still_running_job_returns_409(make_client):
    import threading

    reached_blocker = threading.Event()
    release = threading.Event()

    def blocked_generator(data, path):
        reached_blocker.set()
        assert release.wait(timeout=10)
        generate_certificate_pdf(data, path)

    client = make_client(generator=blocked_generator, processing_mode="background", worker_threads=1)
    payload = make_payload(1)
    created = client.post("/api/v1/jobs", json=payload).json()
    job_id = created["id"]

    try:
        assert reached_blocker.wait(timeout=10)
        # Attempt to retry while job is running
        resp = client.post(f"/api/v1/jobs/{job_id}/retry")
        assert resp.status_code == 409
        assert "Job is still running" in resp.json()["detail"]
    finally:
        release.set()
