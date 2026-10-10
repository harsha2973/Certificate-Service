import io
import zipfile

from tests.conftest import make_payload


def _create(client, n=3, **kw):
    return client.post("/api/v1/jobs", json=make_payload(n, **kw)).json()["id"]


def test_list_certificates_with_pagination(client):
    job_id = _create(client, 5)

    page = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"limit": 2, "offset": 2}).json()

    assert page["total"] == 5
    assert [c["position"] for c in page["items"]] == [2, 3]
    assert all(c["status"] == "succeeded" and c["download_url"] for c in page["items"])


def test_list_certificates_filter_by_status(client):
    recipients = [{"name": "Good", "email": "g@example.com"}, {"name": "Bad", "email": "nope"}]
    job_id = _create(client, recipients=recipients)

    failed = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "failed"}).json()
    ok = client.get(f"/api/v1/jobs/{job_id}/certificates", params={"status": "succeeded"}).json()

    assert (failed["total"], ok["total"]) == (1, 1)
    assert failed["items"][0]["position"] == 1


def test_list_certificates_unknown_job_404(client):
    assert client.get("/api/v1/jobs/nope/certificates").status_code == 404


def test_download_single_certificate_as_pdf(client):
    job_id = _create(client, 1)
    cert = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]

    resp = client.get(cert["download_url"])

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert "student-0.pdf" in resp.headers["content-disposition"]


def test_download_unknown_certificate_404(client):
    assert client.get("/api/v1/certificates/nope/download").status_code == 404


def test_download_job_archive_contains_every_successful_certificate(client):
    recipients = [
        {"name": "Alice", "email": "a@example.com"},
        {"name": "", "email": "bad@example.com"},  # fails validation -> not in the zip
        {"name": "Carol", "email": "c@example.com"},
    ]
    job_id = _create(client, recipients=recipients)

    resp = client.get(f"/api/v1/jobs/{job_id}/download")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.namelist() == ["00000-alice.pdf", "00002-carol.pdf"]
        assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_download_job_archive_without_certificates_404(client):
    job_id = _create(client, recipients=[{"name": "x"}])
    assert client.get(f"/api/v1/jobs/{job_id}/download").status_code == 404
