from tests.conftest import make_payload


def test_list_jobs_empty(client):
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_jobs_with_pagination_and_ordering(client):
    # Create 3 jobs
    id1 = client.post("/api/v1/jobs", json=make_payload(1, course_name="Course 1")).json()["id"]
    id2 = client.post("/api/v1/jobs", json=make_payload(1, course_name="Course 2")).json()["id"]
    id3 = client.post("/api/v1/jobs", json=make_payload(1, course_name="Course 3")).json()["id"]

    # All 3 returned, newest first
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    ids = [item["id"] for item in data["items"]]
    assert ids == [id3, id2, id1]

    # Test limit and offset
    page1 = client.get("/api/v1/jobs?limit=2&offset=0").json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert [item["id"] for item in page1["items"]] == [id3, id2]

    page2 = client.get("/api/v1/jobs?limit=2&offset=2").json()
    assert page2["total"] == 3
    assert len(page2["items"]) == 1
    assert [item["id"] for item in page2["items"]] == [id1]


def test_list_jobs_filter_by_status(client):
    # 1. Successful job
    client.post("/api/v1/jobs", json=make_payload(1))
    # 2. Failed job (all invalid recipients)
    client.post("/api/v1/jobs", json=make_payload(0, recipients=[{"name": "bad"}]))

    all_jobs = client.get("/api/v1/jobs").json()
    assert all_jobs["total"] == 2

    completed_jobs = client.get("/api/v1/jobs?status=completed").json()
    assert completed_jobs["total"] == 1
    assert completed_jobs["items"][0]["status"] == "completed"

    failed_jobs = client.get("/api/v1/jobs?status=failed").json()
    assert failed_jobs["total"] == 1
    assert failed_jobs["items"][0]["status"] == "failed"
