"""Summary generation via an OpenAI-compatible chat endpoint (spec §3.3 step 5)."""
import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError, PermanentJobError


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
    }
    try:
        response = httpx.post(
            f"{cfg.llm_api_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=cfg.llm_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"LLM request failed: {exc}") from exc

    if response.status_code >= 500:
        raise InfrastructureError(f"LLM returned {response.status_code}")
    if response.status_code >= 400:
        raise PermanentJobError(f"LLM returned {response.status_code}: {response.text[:500]}")

    return response.json()["choices"][0]["message"]["content"].strip()
