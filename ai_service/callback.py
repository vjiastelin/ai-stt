"""Deliver results to BPM's transcription-result endpoint (spec §3.3 step 7)."""
import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError

# Path template appended to the configurable BPM base URL (cfg.bpm_callback_url).
# {call_record_id} is a path variable; the rest is fixed by the BPM service contract.
_RESULT_PATH = (
    "/0/ServiceModel/AnGetTranscriptionResultService.svc"
    "/transcriptions/{call_record_id}/result"
)


def result_url(cfg: ServiceConfig, call_record_id: str) -> str:
    return cfg.bpm_callback_url + _RESULT_PATH.format(call_record_id=call_record_id)


def deliver(
    cfg: ServiceConfig,
    call_record_id: str,
    summary: str,
    full_text: str,
    error: bool = False,
    error_description: str = "",
) -> None:
    payload = {
        "Summary": summary,
        "FullText": full_text,
        "Error": error,
        "ErrorDescription": error_description,
    }
    headers = {"BPMCSRF": cfg.bpm_csrf_token} if cfg.bpm_csrf_token else None
    try:
        response = httpx.post(
            result_url(cfg, call_record_id),
            json=payload,
            headers=headers,
            timeout=cfg.callback_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"BPM callback failed: {exc}") from exc
    if response.status_code != 200:
        # the diagram retries the callback until 200 — any non-200 keeps the job delivering
        raise InfrastructureError(f"BPM callback returned {response.status_code}")
