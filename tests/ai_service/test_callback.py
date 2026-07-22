import json

import httpx
import pytest
import respx

from ai_service.callback import deliver
from ai_service.errors import InfrastructureError

URL = "http://bpm/onTranscriptionComplete"


@respx.mock
def test_deliver_posts_pascal_case_payload(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200))

    deliver(service_config(), "id-1", "суть", "[00:00:00] привет")

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "CallRecordId": "id-1",
        "Summary": "суть",
        "FullText": "[00:00:00] привет",
    }


@respx.mock
def test_deliver_sends_bpmcsrf_header_when_token_set(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200))

    deliver(service_config(bpm_csrf_token="secret-token"), "id-1", "s", "t")

    assert route.calls.last.request.headers["BPMCSRF"] == "secret-token"


@respx.mock
def test_deliver_omits_bpmcsrf_header_when_token_empty(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200))

    deliver(service_config(), "id-1", "s", "t")

    assert "BPMCSRF" not in route.calls.last.request.headers


@respx.mock
@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_non_200_raises_infrastructure_error(service_config, status):
    respx.post(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(InfrastructureError):
        deliver(service_config(), "id-1", "s", "t")


@respx.mock
def test_timeout_raises_infrastructure_error(service_config):
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(InfrastructureError):
        deliver(service_config(), "id-1", "s", "t")
