import pytest

from tests.conftest import make_payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"course_name": ""},
        {"organization": "   "},
        {"recipients": []},
        {"issue_date": "not-a-date"},
        {"course_name": "数据科学"},  # not renderable with the built-in font
    ],
)
def test_invalid_request_envelope_is_rejected(client, overrides):
    resp = client.post("/api/v1/jobs", json=make_payload(1, **overrides))
    assert resp.status_code == 422


def test_missing_required_fields_rejected(client):
    assert client.post("/api/v1/jobs", json={"recipients": [{"name": "A", "email": "a@x.io"}]}).status_code == 422


def test_too_many_recipients_rejected(make_client):
    client = make_client(max_recipients_per_job=2)
    resp = client.post("/api/v1/jobs", json=make_payload(3))
    assert resp.status_code == 422
    assert "Too many recipients" in resp.json()["detail"]


def test_invalid_recipients_are_reported_not_fatal(client):
    payload = make_payload(
        0,
        recipients=[
            {"name": "Good One", "email": "good@example.com"},  # 0 ok
            {"name": "", "email": "empty@example.com"},  # 1 empty name
            {"name": "No Email"},  # 2 missing email
            {"name": "Bad Email", "email": "not-an-email"},  # 3
            "just a string",  # 4 wrong type
            {"name": 123, "email": "num@example.com"},  # 5 name not a string
            {"name": "Dupe", "email": "GOOD@example.com"},  # 6 duplicate of #0 (case-insensitive)
        ],
    )

    body = client.post("/api/v1/jobs", json=payload).json()

    assert body["status"] == "completed_with_errors"
    assert body["progress"] == {
        "total": 7,
        "succeeded": 1,
        "failed": 6,
        "pending": 0,
        "percent_complete": 100.0,
    }
    failures = {f["position"]: f["error"] for f in body["failures"]}
    assert sorted(failures) == [1, 2, 3, 4, 5, 6]
    assert "name" in failures[1]
    assert "email: Field required" in failures[2]
    assert "valid email" in failures[3]
    assert "must be an object" in failures[4]
    assert "name" in failures[5]
    assert "duplicate" in failures[6]


def test_all_invalid_recipients_marks_job_failed(client):
    body = client.post(
        "/api/v1/jobs", json=make_payload(0, recipients=[{"name": "x"}, {"email": "y@z.io"}])
    ).json()

    assert body["status"] == "failed"
    assert body["progress"]["failed"] == 2
    assert body["download_url"] is None


def test_names_and_emails_are_normalised(client):
    job_id = client.post(
        "/api/v1/jobs",
        json=make_payload(0, recipients=[{"name": "  Ada Lovelace ", "email": " ADA@Example.COM "}]),
    ).json()["id"]

    item = client.get(f"/api/v1/jobs/{job_id}/certificates").json()["items"][0]

    assert item["recipient_name"] == "Ada Lovelace"
    assert item["recipient_email"] == "ada@example.com"


def test_openapi_schema_documents_recipients(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    job_create = schema["components"]["schemas"]["JobCreate"]
    recipients_prop = job_create["properties"]["recipients"]

    assert recipients_prop["type"] == "array"
    items = recipients_prop["items"]
    # Check that recipient items are properly typed with name and email, not empty or null
    assert "properties" in items or "$ref" in items
    if "properties" in items:
        assert "name" in items["properties"]
        assert "email" in items["properties"]
    assert "example" in job_create
    assert isinstance(job_create["example"]["recipients"], list)
    assert len(job_create["example"]["recipients"]) > 0
    assert "name" in job_create["example"]["recipients"][0]
    assert "email" in job_create["example"]["recipients"][0]

