from tests.conftest import make_payload


def test_idempotency_key_prevents_duplicate_jobs(client):
    payload = make_payload(2)
    headers = {"Idempotency-Key": "test-idemp-123"}

    # First request creates the job
    resp1 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert resp1.status_code == 202
    job1_id = resp1.json()["id"]

    # Second request with the same idempotency key returns the existing job
    resp2 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert resp2.status_code == 202
    assert resp2.json()["id"] == job1_id

    # Verify only 1 job exists in total
    list_resp = client.get("/api/v1/jobs").json()
    assert list_resp["total"] == 1


def test_different_idempotency_keys_create_different_jobs(client):
    payload = make_payload(1)
    resp1 = client.post("/api/v1/jobs", json=payload, headers={"Idempotency-Key": "key-a"})
    resp2 = client.post("/api/v1/jobs", json=payload, headers={"Idempotency-Key": "key-b"})

    assert resp1.json()["id"] != resp2.json()["id"]
    list_resp = client.get("/api/v1/jobs").json()
    assert list_resp["total"] == 2


def test_api_key_auth_enforced_when_configured(make_client):
    # App configured with an API key
    client = make_client(api_key="my-secret-key")

    # 1. Health check is unauthenticated
    health = client.get("/health")
    assert health.status_code == 200

    # 2. API endpoint without key is 401
    unauth = client.post("/api/v1/jobs", json=make_payload(1))
    assert unauth.status_code == 401
    assert "Invalid or missing API key" in unauth.json()["detail"]

    # 3. API endpoint with wrong key is 401
    bad_key = client.post("/api/v1/jobs", json=make_payload(1), headers={"X-API-Key": "wrong-key"})
    assert bad_key.status_code == 401

    # 4. API endpoint with correct key succeeds
    ok = client.post("/api/v1/jobs", json=make_payload(1), headers={"X-API-Key": "my-secret-key"})
    assert ok.status_code == 202

    # 5. GET endpoint with correct key succeeds
    job_id = ok.json()["id"]
    get_ok = client.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "my-secret-key"})
    assert get_ok.status_code == 200
