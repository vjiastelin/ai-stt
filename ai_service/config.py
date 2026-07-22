"""ai-service configuration from environment variables (spec §3.4)."""
import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_SUMMARY_PROMPT = (
    "Составь краткое содержание телефонного разговора на русском языке: "
    "основная тема, договорённости, следующие шаги. "
    "Отвечай только текстом краткого содержания."
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ServiceConfig:
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    whisper_api_url: str
    whisper_model: str
    whisper_timeout_seconds: int
    whisper_api_key: str
    language: str
    summary_enabled: bool
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: int
    summary_prompt: str
    bpm_callback_url: str
    bpm_csrf_token: str
    callback_timeout_seconds: int
    max_retries: int
    retry_backoff_cap_seconds: int
    db_path: str
    port: int
    log_level: str


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def load_config(env: Mapping[str, str] = os.environ) -> ServiceConfig:
    summary_enabled = env.get("SUMMARY_ENABLED", "true").strip().lower() in ("1", "true", "yes")
    if summary_enabled:
        llm_api_url = _require(env, "LLM_API_URL").rstrip("/")
        llm_model = _require(env, "LLM_MODEL")
    else:
        llm_api_url = env.get("LLM_API_URL", "").rstrip("/")
        llm_model = env.get("LLM_MODEL", "")
    return ServiceConfig(
        s3_endpoint_url=_require(env, "S3_ENDPOINT_URL"),
        s3_access_key=_require(env, "S3_ACCESS_KEY"),
        s3_secret_key=_require(env, "S3_SECRET_KEY"),
        whisper_api_url=env.get("WHISPER_API_URL", "http://whisper-api:8000/v1").rstrip("/"),
        whisper_model=env.get("WHISPER_MODEL", "large-v3"),
        whisper_timeout_seconds=int(env.get("WHISPER_TIMEOUT_SECONDS", "600")),
        whisper_api_key=env.get("WHISPER_API_KEY", ""),
        language=env.get("LANGUAGE", "ru"),
        summary_enabled=summary_enabled,
        llm_api_url=llm_api_url,
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=llm_model,
        llm_timeout_seconds=int(env.get("LLM_TIMEOUT_SECONDS", "120")),
        summary_prompt=env.get("SUMMARY_PROMPT", DEFAULT_SUMMARY_PROMPT),
        bpm_callback_url=_require(env, "BPM_CALLBACK_URL").rstrip("/"),
        bpm_csrf_token=env.get("BPM_CSRF_TOKEN", ""),
        callback_timeout_seconds=int(env.get("CALLBACK_TIMEOUT_SECONDS", "30")),
        max_retries=int(env.get("MAX_RETRIES", "3")),
        retry_backoff_cap_seconds=int(env.get("RETRY_BACKOFF_CAP_SECONDS", "300")),
        db_path=env.get("DB_PATH", "/data/jobs.db"),
        port=int(env.get("PORT", "8080")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )
