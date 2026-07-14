import io

import pytest
from fastapi.testclient import TestClient

from whisper_api.app import create_app
from whisper_api.config import ApiConfig
from whisper_api.engine import EngineResult, InvalidAudioError


class FakeEngine:
    model_name = "fake"

    def transcribe(self, audio_path: str, language: str | None) -> EngineResult:
        return EngineResult(
            language=language or "ru",
            duration=2.5,
            segments=[{"id": 0, "start": 0.0, "end": 2.5, "text": " привет мир"}],
            text="привет мир",
        )


def make_config(**overrides) -> ApiConfig:
    base = dict(model="fake", device="cpu", compute_type="int8", api_key="", port=8000, log_level="INFO")
    base.update(overrides)
    return ApiConfig(**base)


@pytest.fixture
def client():
    app = create_app(make_config(), engine_factory=None)
    app.state.engine = FakeEngine()
    return TestClient(app)


def post_wav(client, **form):
    data = {"model": "fake", "response_format": "verbose_json", **form}
    return client.post(
        "/v1/audio/translations",
        files={"file": ("a.mp3", io.BytesIO(b"RIFF-fake"), "audio/mpeg")},
        data=data,
    )


def test_healthz_503_while_loading():
    app = create_app(make_config(), engine_factory=None)
    assert TestClient(app).get("/health").status_code == 503


def test_healthz_ok_when_loaded(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "fake"}


def test_transcription_contract(client):
    response = post_wav(client, language="ru")
    assert response.status_code == 200
    assert response.json() == {
        "task": "transcribe",
        "language": "ru",
        "duration": 2.5,
        "text": "привет мир",
        "segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": " привет мир"}],
    }


def test_transcription_503_while_loading():
    app = create_app(make_config(), engine_factory=None)
    assert post_wav(TestClient(app)).status_code == 503


def test_empty_file_400(client):
    response = client.post(
        "/v1/audio/translations",
        files={"file": ("a.mp3", io.BytesIO(b""), "audio/mpeg")},
        data={"model": "fake", "response_format": "verbose_json"},
    )
    assert response.status_code == 400


def test_unsupported_response_format_422(client):
    assert post_wav(client, response_format="srt").status_code == 422


def test_auth_enforced_when_key_set():
    app = create_app(make_config(api_key="secret"), engine_factory=None)
    app.state.engine = FakeEngine()
    client = TestClient(app)
    assert post_wav(client).status_code == 401
    ok = client.post(
        "/v1/audio/translations",
        files={"file": ("a.mp3", io.BytesIO(b"RIFF"), "audio/mpeg")},
        data={"model": "fake", "response_format": "verbose_json"},
        headers={"Authorization": "Bearer secret"},
    )
    assert ok.status_code == 200


def test_engine_failure_500(client):
    class BrokenEngine:
        def transcribe(self, audio_path, language):
            raise RuntimeError("boom")

    client.app.state.engine = BrokenEngine()
    assert post_wav(client).status_code == 500


def test_invalid_audio_400(client):
    class CorruptAudioEngine:
        def transcribe(self, audio_path, language):
            raise InvalidAudioError("bad data")

    client.app.state.engine = CorruptAudioEngine()
    response = post_wav(client)
    assert response.status_code == 400
    assert "bad data" in response.json()["detail"]


def test_openapi_documents_response_schemas(client):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert set(schemas["SegmentModel"]["required"]) == {"id", "start", "end", "text"}
    post = spec["paths"]["/v1/audio/translations"]["post"]
    ok_schema = post["responses"]["200"]["content"]["application/json"]["schema"]
    assert ok_schema["$ref"].endswith("TranscriptionResponse")
    assert "401" in post["responses"]
