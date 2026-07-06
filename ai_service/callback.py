"""Deliver results to BPM's onTranscriptionComplete endpoint (spec §3.3 step 7)."""
import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError


def deliver(cfg: ServiceConfig, call_record_id: str, summary: str, full_text: str) -> None:
    payload = {"CallRecordId": call_record_id, "Summary": summary, "FullText": full_text}
    try:
        response = httpx.post(
            cfg.bpm_callback_url, json=payload, timeout=cfg.callback_timeout_seconds
        )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"BPM callback failed: {exc}") from exc
    if response.status_code != 200:
        # the diagram retries the callback until 200 — any non-200 keeps the job delivering
        raise InfrastructureError(f"BPM callback returned {response.status_code}")
