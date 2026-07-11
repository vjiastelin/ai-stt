"""whisper-api configuration (spec §4.2), incl. COMPUTE_TYPE auto-resolution."""
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Raised when the environment holds an invalid configuration value."""


@dataclass(frozen=True)
class ApiConfig:
    model: str
    device: str
    compute_type: str
    api_key: str
    port: int
    log_level: str
    vad_filter: bool = True
    condition_on_previous_text: bool = True
    # Open pass-through of faster-whisper transcribe options (merged over engine
    # defaults). Lets any option be tuned via env without code changes.
    transcribe_options: dict = field(default_factory=dict)
    # Optional TLS: when both cert and key are set, uvicorn serves HTTPS.
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    ssl_keyfile_password: str = ""


def _parse_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes")


def _parse_transcribe_options(env: Mapping[str, str]) -> dict:
    raw = env.get("TRANSCRIBE_OPTIONS", "").strip()
    if not raw:
        return {}
    try:
        options = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"TRANSCRIBE_OPTIONS is not valid JSON: {exc}") from exc
    if not isinstance(options, dict):
        raise ConfigError("TRANSCRIBE_OPTIONS must be a JSON object")
    return options


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
        condition_on_previous_text=_parse_bool(env, "CONDITION_ON_PREVIOUS_TEXT", True),
        transcribe_options=_parse_transcribe_options(env),
        ssl_certfile=env.get("SSL_CERTFILE", "").strip(),
        ssl_keyfile=env.get("SSL_KEYFILE", "").strip(),
        ssl_keyfile_password=env.get("SSL_KEYFILE_PASSWORD", ""),
    )
