"""whisper-api configuration (spec §4.2), incl. COMPUTE_TYPE auto-resolution."""
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiConfig:
    model: str
    device: str
    compute_type: str
    api_key: str
    port: int
    log_level: str
    vad_filter: bool = True
    condition_on_previous_text: bool = False


def _parse_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes")


def load_config(env: Mapping[str, str] = os.environ) -> ApiConfig:
    device = env.get("DEVICE", "cuda").strip() or "cuda"
    compute_type = env.get("COMPUTE_TYPE", "").strip()
    if not compute_type:
        compute_type = "float16" if device == "cuda" else "int8"
    return ApiConfig(
        model=env.get("WHISPER_MODEL", "large-v3"),
        device=device,
        compute_type=compute_type,
        api_key=env.get("API_KEY", ""),
        port=int(env.get("PORT", "8000")),
        log_level=env.get("LOG_LEVEL", "INFO"),
        vad_filter=_parse_bool(env, "VAD_FILTER", True),
        condition_on_previous_text=_parse_bool(env, "CONDITION_ON_PREVIOUS_TEXT", False),
    )
