import pytest
from fastapi.testclient import TestClient

from ai_service.app import create_app
from ai_service.db import JobStore


@pytest.fixture
def client_and_store(service_config, tmp_path):
    store = JobStore(str(tmp_path / "api.db"))
    app = create_app(service_config(), store)
    return TestClient(app), store


def test_request_transcription_accepts_and_enqueues(client_and_store):
    client, store = client_and_store
    response = client.post(
        "/requestTranscription",
        json={"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.mp3"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "CallRecordId": "id-1"}
    assert store.get("id-1").status == "queued"


def test_request_transcription_is_idempotent(client_and_store):
    client, store = client_and_store
    body = {"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.mp3"}
    assert client.post("/requestTranscription", json=body).status_code == 200
    store.set_status("id-1", "processing")
    assert client.post("/requestTranscription", json=body).status_code == 200
    assert store.get("id-1").status == "processing"  # untouched


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"CallRecordId": "id-1"},
        {"CallRecordUrl": "s3://bucket/rec.mp3"},
        {"CallRecordId": "", "CallRecordUrl": "s3://bucket/rec.mp3"},
        {"CallRecordId": "id-1", "CallRecordUrl": "ftp://x/y.mp3"},
        {"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.wav"},  # mp3-only policy
    ],
)
def test_request_transcription_400_on_invalid(client_and_store, body):
    client, _ = client_and_store
    assert client.post("/requestTranscription", json=body).status_code == 400


def test_request_transcription_400_on_non_json(client_and_store):
    client, _ = client_and_store
    response = client.post(
        "/requestTranscription", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


def test_job_status_endpoint(client_and_store):
    client, store = client_and_store
    client.post(
        "/requestTranscription",
        json={"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.mp3"},
    )
    response = client.get("/jobs/id-1")
    assert response.status_code == 200
    data = response.json()
    assert data["CallRecordId"] == "id-1"
    assert data["status"] == "queued"
    assert data["attempts"] == 0
    assert data["error"] is None
    assert client.get("/jobs/nope").status_code == 404


def test_healthz(client_and_store):
    client, _ = client_and_store
    assert client.get("/healthz").json() == {"status": "ok"}


def test_openapi_documents_request_and_response_schemas(client_and_store):
    client, _ = client_and_store
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert set(schemas["TranscriptionRequest"]["required"]) == {"CallRecordId", "CallRecordUrl"}
    assert "JobStatusResponse" in schemas
    post = spec["paths"]["/requestTranscription"]["post"]
    assert "400" in post["responses"]
    ok_schema = post["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok_schema["$ref"].endswith("AcceptedResponse")


def test_job_result_returns_summary_and_fulltext(client_and_store):
    client, store = client_and_store
    store.enqueue("id-1", "s3://bucket/rec.mp3")
    store.set_result("id-1", "[00:00:00] привет мир", "краткое содержание")
    response = client.get("/jobs/id-1/result")
    assert response.status_code == 200
    assert response.json() == {
        "CallRecordId": "id-1",
        "status": "delivering",
        "Summary": "краткое содержание",
        "FullText": "[00:00:00] привет мир",
    }


def test_job_result_empty_before_processing(client_and_store):
    client, store = client_and_store
    store.enqueue("id-1", "s3://bucket/rec.mp3")
    response = client.get("/jobs/id-1/result")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["Summary"] == ""
    assert body["FullText"] == ""


def test_job_result_404_for_unknown(client_and_store):
    client, _ = client_and_store
    assert client.get("/jobs/nope/result").status_code == 404


def test_list_jobs_endpoint(client_and_store):
    client, store = client_and_store
    store.enqueue("id-1", "s3://b/1.mp3")
    store.enqueue("id-2", "s3://b/2.mp3")
    store.set_status("id-2", "done")
    body = client.get("/jobs").json()
    assert body["count"] == 2
    assert [j["CallRecordId"] for j in body["jobs"]] == ["id-2", "id-1"]
    assert body["jobs"][0]["status"] == "done"


def test_list_jobs_endpoint_status_filter(client_and_store):
    client, store = client_and_store
    store.enqueue("id-1", "s3://b/1.mp3")
    store.enqueue("id-2", "s3://b/2.mp3")
    store.set_status("id-2", "done")
    body = client.get("/jobs?status=done").json()
    assert body["count"] == 1
    assert body["jobs"][0]["CallRecordId"] == "id-2"


def test_list_jobs_endpoint_rejects_bad_status(client_and_store):
    client, _ = client_and_store
    assert client.get("/jobs?status=bogus").status_code == 400
