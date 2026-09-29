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
    whisper_verify_ssl: bool
    language: str
    summary_enabled: bool
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: int
    llm_verify_ssl: bool
    summary_prompt: str
    bpm_callback_url: str
    bpm_csrf_token: str
    callback_timeout_seconds: int
    smtp_host: str
    smtp_port: int
    smtp_security: str
    smtp_username: str
    smtp_password: str
    smtp_timeout_seconds: int
    email_from: str
    email_to: tuple[str, ...]
    max_retries: int
    retry_backoff_cap_seconds: int
    db_path: str
    port: int
    log_level: str

    @property
    def bpm_enabled(self) -> bool:
        return bool(self.bpm_callback_url)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host)


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


SMTP_SECURITY_MODES = ("starttls", "ssl", "none")
_SMTP_DEFAULT_PORTS = {"starttls": 587, "ssl": 465, "none": 25}


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "").strip().lower()
    return default if not raw else raw in ("1", "true", "yes")


def load_config(env: Mapping[str, str] = os.environ) -> ServiceConfig:
    summary_enabled = env.get("SUMMARY_ENABLED", "true").strip().lower() in ("1", "true", "yes")
    if summary_enabled:
        llm_api_url = _require(env, "LLM_API_URL").rstrip("/")
        llm_model = _require(env, "LLM_MODEL")
    else:
        llm_api_url = env.get("LLM_API_URL", "").rstrip("/")
        llm_model = env.get("LLM_MODEL", "")

    # delivery channels: each is enabled by setting its variables; at least one is required
    bpm_callback_url = env.get("BPM_CALLBACK_URL", "").strip().rstrip("/")
    email_enabled = bool(env.get("SMTP_HOST", "").strip() or env.get("EMAIL_TO", "").strip())
    if not bpm_callback_url and not email_enabled:
        raise ConfigError(
            "no delivery channel configured: set BPM_CALLBACK_URL and/or SMTP_HOST + EMAIL_TO"
        )
    smtp_security = env.get("SMTP_SECURITY", "starttls").strip().lower()
    if smtp_security not in SMTP_SECURITY_MODES:
        raise ConfigError(
            f"SMTP_SECURITY must be one of {', '.join(SMTP_SECURITY_MODES)}: {smtp_security!r}"
        )
    if email_enabled:
        smtp_host = _require(env, "SMTP_HOST")
        email_from = _require(env, "EMAIL_FROM")
        email_to = tuple(a.strip() for a in _require(env, "EMAIL_TO").split(",") if a.strip())
    else:
        smtp_host, email_from, email_to = "", env.get("EMAIL_FROM", ""), ()
    smtp_port = env.get("SMTP_PORT", "").strip()

    return ServiceConfig(
        s3_endpoint_url=_require(env, "S3_ENDPOINT_URL"),
        s3_access_key=_require(env, "S3_ACCESS_KEY"),
        s3_secret_key=_require(env, "S3_SECRET_KEY"),
        whisper_api_url=env.get("WHISPER_API_URL", "http://whisper-api:8000/v1").rstrip("/"),
        whisper_model=env.get("WHISPER_MODEL", "large-v3"),
        whisper_timeout_seconds=int(env.get("WHISPER_TIMEOUT_SECONDS", "600")),
        whisper_api_key=env.get("WHISPER_API_KEY", ""),
        # false accepts self-signed/mismatched certs (ephemeral GPU instances)
        whisper_verify_ssl=_flag(env, "WHISPER_VERIFY_SSL", True),
        language=env.get("LANGUAGE", "ru"),
        summary_enabled=summary_enabled,
        llm_api_url=llm_api_url,
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=llm_model,
        llm_timeout_seconds=int(env.get("LLM_TIMEOUT_SECONDS", "120")),
        llm_verify_ssl=_flag(env, "LLM_VERIFY_SSL", True),
        summary_prompt=env.get("SUMMARY_PROMPT", DEFAULT_SUMMARY_PROMPT),
        bpm_callback_url=bpm_callback_url,
        bpm_csrf_token=env.get("BPM_CSRF_TOKEN", ""),
        callback_timeout_seconds=int(env.get("CALLBACK_TIMEOUT_SECONDS", "30")),
        smtp_host=smtp_host,
        smtp_port=int(smtp_port) if smtp_port else _SMTP_DEFAULT_PORTS[smtp_security],
        smtp_security=smtp_security,
        smtp_username=env.get("SMTP_USERNAME", ""),
        smtp_password=env.get("SMTP_PASSWORD", ""),
        smtp_timeout_seconds=int(env.get("SMTP_TIMEOUT_SECONDS", "30")),
        email_from=email_from,
        email_to=email_to,
        max_retries=int(env.get("MAX_RETRIES", "3")),
        retry_backoff_cap_seconds=int(env.get("RETRY_BACKOFF_CAP_SECONDS", "300")),
        db_path=env.get("DB_PATH", "/data/jobs.db"),
        port=int(env.get("PORT", "8080")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )
