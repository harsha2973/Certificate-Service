from tests.conftest import make_payload


def test_create_job_returns_accepted_with_location(client):
    resp = client.post("/api/v1/jobs", json=make_payload(3))

    assert resp.status_code == 202
    body = resp.json()
    assert resp.headers["Location"] == f"/api/v1/jobs/{body['id']}"
    assert body["course_name"] == "Python Bootcamp 2026"
    assert body["title"] == "Certificate of Completion"  # default
    assert body["progress"]["total"] == 3


def test_job_is_retrievable_after_creation(client):
    job_id = client.post("/api/v1/jobs", json=make_payload(2)).json()["id"]

    resp = client.get(f"/api/v1/jobs/{job_id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == job_id


def test_unknown_job_is_404(client):
    assert client.get("/api/v1/jobs/doesnotexist").status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
