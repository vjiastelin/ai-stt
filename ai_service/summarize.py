"""Summary generation via an OpenAI-compatible chat endpoint (spec §3.3 step 5)."""
import re

import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError, PermanentJobError


# reasoning models served without a reasoning parser put their chain of thought
# into `content`; it must not end up in the Summary sent to BPM/email
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_reasoning(text: str) -> str:
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:  # opening tag lives in the chat template, only the close is emitted
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def summarize(cfg: ServiceConfig, transcript_text: str) -> str:
    if not cfg.summary_enabled or not transcript_text.strip():
        return ""

    headers = {}
    if cfg.llm_api_key:
        headers["Authorization"] = f"Bearer {cfg.llm_api_key}"
    payload = {
        "model": cfg.llm_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": cfg.summary_prompt},
            {"role": "user", "content": transcript_text},
        ],
        **cfg.llm_extra_body,
    }
    try:
        response = httpx.post(
            f"{cfg.llm_api_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=cfg.llm_timeout_seconds,
            verify=cfg.llm_verify_ssl,
        )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"LLM request failed: {exc}") from exc

    if response.status_code >= 500 or response.status_code == 429:
        # 429 is the LLM gateway being busy (rate/concurrency limit), not bad input.
        raise InfrastructureError(f"LLM returned {response.status_code}")
    if response.status_code >= 400:
        raise PermanentJobError(f"LLM returned {response.status_code}: {response.text[:500]}")

    try:
        return strip_reasoning(response.json()["choices"][0]["message"]["content"])
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise InfrastructureError(f"LLM returned malformed 200 response: {exc}") from exc
