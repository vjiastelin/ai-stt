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
        json={"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.wav"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "CallRecordId": "id-1"}
    assert store.get("id-1").status == "queued"


def test_request_transcription_is_idempotent(client_and_store):
    client, store = client_and_store
    body = {"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.wav"}
    assert client.post("/requestTranscription", json=body).status_code == 200
    store.set_status("id-1", "processing")
    assert client.post("/requestTranscription", json=body).status_code == 200
    assert store.get("id-1").status == "processing"  # untouched


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"CallRecordId": "id-1"},
        {"CallRecordUrl": "s3://bucket/rec.wav"},
        {"CallRecordId": "", "CallRecordUrl": "s3://bucket/rec.wav"},
        {"CallRecordId": "id-1", "CallRecordUrl": "ftp://x/y.wav"},
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
        json={"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.wav"},
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
