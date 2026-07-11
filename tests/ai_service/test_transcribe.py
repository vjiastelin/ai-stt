import httpx
import pytest
import respx

from ai_service.errors import InfrastructureError, PermanentJobError
from ai_service.transcribe import transcribe_file

URL = "http://whisper-api:8000/v1/chat/completions"

VERBOSE_JSON = {
    "task": "transcribe",
    "language": "ru",
    "duration": 9.87,
    "text": "первая реплика вторая реплика",
    "segments": [
        {"id": 0, "start": 0.0, "end": 4.2, "text": " первая реплика"},
        {"id": 1, "start": 4.2, "end": 9.87, "text": " вторая реплика"},
    ],
}


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "rec.mp3"
    path.write_bytes(b"RIFF-fake")
    return path


@respx.mock
def test_success_parses_segments(service_config, wav):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))

    result = transcribe_file(service_config(), wav)

    assert result.language == "ru"
    assert result.duration == 9.87
    assert [seg.text for seg in result.segments] == [" первая реплика", " вторая реплика"]
    assert result.segments[1].start == 4.2

    request = route.calls.last.request
    assert b'name="file"' in request.content
    assert b'name="model"' in request.content
    assert b"verbose_json" in request.content
    assert b'name="language"' in request.content


@respx.mock
def test_4xx_is_permanent(service_config, wav):
    respx.post(URL).mock(return_value=httpx.Response(400, json={"detail": "bad audio"}))
    with pytest.raises(PermanentJobError):
        transcribe_file(service_config(), wav)


@respx.mock
def test_5xx_is_infrastructure(service_config, wav):
    respx.post(URL).mock(return_value=httpx.Response(503, json={"detail": "loading"}))
    with pytest.raises(InfrastructureError):
        transcribe_file(service_config(), wav)


@respx.mock
def test_timeout_is_infrastructure(service_config, wav):
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(InfrastructureError):
        transcribe_file(service_config(), wav)


@respx.mock
def test_malformed_200_html_body_is_infrastructure(service_config, wav):
    respx.post(URL).mock(
        return_value=httpx.Response(200, content=b"<html>gateway</html>")
    )
    with pytest.raises(InfrastructureError):
        transcribe_file(service_config(), wav)


@respx.mock
def test_malformed_200_wrong_shape_is_infrastructure(service_config, wav):
    respx.post(URL).mock(return_value=httpx.Response(200, json={"error": "quota"}))
    with pytest.raises(InfrastructureError):
        transcribe_file(service_config(), wav)


@respx.mock
def test_authorization_header_sent_when_configured(service_config, wav):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    transcribe_file(service_config(whisper_api_key="secret"), wav)
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


@respx.mock
def test_authorization_header_absent_by_default(service_config, wav):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    transcribe_file(service_config(), wav)
    assert "Authorization" not in route.calls.last.request.headers
