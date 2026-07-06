import httpx
import pytest
import respx

from ai_service.errors import InfrastructureError, PermanentJobError
from ai_service.summarize import summarize

URL = "http://llm:8000/v1/chat/completions"

CHAT_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": " Клиент уточнил бронь.\n"}}]
}


@respx.mock
def test_summarize_calls_llm(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))

    result = summarize(service_config(), "клиент звонил по поводу брони")

    assert result == "Клиент уточнил бронь."
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.2
    assert body["messages"][0] == {"role": "system", "content": "Составь краткое содержание."}
    assert body["messages"][1] == {"role": "user", "content": "клиент звонил по поводу брони"}


@respx.mock
def test_api_key_sent_when_configured(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    summarize(service_config(llm_api_key="secret"), "текст")
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


@respx.mock
def test_disabled_returns_empty_without_calling_llm(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    assert summarize(service_config(summary_enabled=False), "текст") == ""
    assert not route.called


def test_blank_transcript_returns_empty(service_config):
    assert summarize(service_config(), "   ") == ""


@respx.mock
def test_4xx_is_permanent(service_config):
    respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "context length"}))
    with pytest.raises(PermanentJobError):
        summarize(service_config(), "текст")


@respx.mock
def test_5xx_and_timeout_are_infrastructure(service_config):
    respx.post(URL).mock(return_value=httpx.Response(502))
    with pytest.raises(InfrastructureError):
        summarize(service_config(), "текст")
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(InfrastructureError):
        summarize(service_config(), "текст")
