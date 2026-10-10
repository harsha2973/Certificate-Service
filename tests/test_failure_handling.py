from app.generator import generate_certificate_pdf
from tests.conftest import make_payload


def flaky_generator(data, path):
    if data.recipient_name == "Explode Me":
        raise RuntimeError("renderer crashed")
    generate_certificate_pdf(data, path)


def test_one_generation_failure_does_not_block_others(make_client):
    client = make_client(generator=flaky_generator)
    payload = make_payload(
        0,
        recipients=[
            {"name": "Alice", "email": "alice@example.com"},
            {"name": "Explode Me", "email": "boom@example.com"},
            {"name": "Carol", "email": "carol@example.com"},
        ],
    )

    body = client.post("/api/v1/jobs", json=payload).json()

    assert body["status"] == "completed_with_errors"
    assert body["progress"]["succeeded"] == 2
    assert body["progress"]["failed"] == 1
    assert [f["position"] for f in body["failures"]] == [1]
    assert "renderer crashed" in body["failures"][0]["error"]
    assert body["failures"][0]["recipient_email"] == "boom@example.com"


def test_failed_certificate_has_no_download(make_client):
    client = make_client(generator=flaky_generator)
    payload = make_payload(0, recipients=[{"name": "Explode Me", "email": "boom@example.com"}])
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]

    assert cert["status"] == "failed"
    assert cert["download_url"] is None
    assert client.get(f"/api/v1/certificates/{cert['id']}/download").status_code == 409


def test_job_fails_when_every_certificate_fails(make_client):
    def always_fail(data, path):
        raise RuntimeError("nope")

    client = make_client(generator=always_fail)
    body = client.post("/api/v1/jobs", json=make_payload(2)).json()

    assert body["status"] == "failed"
    assert body["progress"]["failed"] == 2
