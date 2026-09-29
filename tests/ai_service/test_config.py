import pytest

from ai_service.config import DEFAULT_SUMMARY_PROMPT, ConfigError, load_config

REQUIRED = {
    "S3_ENDPOINT_URL": "http://minio:9000",
    "S3_ACCESS_KEY": "ak",
    "S3_SECRET_KEY": "sk",
    "BPM_CALLBACK_URL": "http://mow2crm6:5000",
    "LLM_API_URL": "http://vllm:8000/v1",
    "LLM_MODEL": "qwen2.5",
}


def test_defaults_applied():
    cfg = load_config(REQUIRED)
    assert cfg.whisper_api_url == "http://whisper-api:8000/v1"
    assert cfg.whisper_model == "large-v3"
    assert cfg.whisper_timeout_seconds == 600
    assert cfg.language == "ru"
    assert cfg.summary_enabled is True
    assert cfg.llm_api_key == ""
    assert cfg.llm_timeout_seconds == 120
    assert cfg.summary_prompt == DEFAULT_SUMMARY_PROMPT
    assert cfg.callback_timeout_seconds == 30
    assert cfg.bpm_csrf_token == ""
    assert cfg.max_retries == 3
    assert cfg.retry_backoff_cap_seconds == 300
    assert cfg.db_path == "/data/jobs.db"
    assert cfg.port == 8080
    assert cfg.log_level == "INFO"


def test_bpm_callback_url_strips_trailing_slash():
    cfg = load_config({**REQUIRED, "BPM_CALLBACK_URL": "http://mow2crm6:5000/"})
    assert cfg.bpm_callback_url == "http://mow2crm6:5000"


def test_missing_required_var_raises():
    env = dict(REQUIRED)
    del env["S3_ENDPOINT_URL"]
    with pytest.raises(ConfigError, match="S3_ENDPOINT_URL"):
        load_config(env)


EMAIL = {"SMTP_HOST": "smtp.example.kz", "EMAIL_FROM": "stt@example.kz", "EMAIL_TO": "a@x.kz"}


def test_no_delivery_channel_raises():
    env = {k: v for k, v in REQUIRED.items() if k != "BPM_CALLBACK_URL"}
    with pytest.raises(ConfigError, match="no delivery channel"):
        load_config(env)


def test_bpm_only_by_default():
    cfg = load_config(REQUIRED)
    assert (cfg.bpm_enabled, cfg.email_enabled) == (True, False)
    assert cfg.email_to == ()


def test_email_only():
    env = {k: v for k, v in REQUIRED.items() if k != "BPM_CALLBACK_URL"}
    cfg = load_config({**env, **EMAIL})
    assert (cfg.bpm_enabled, cfg.email_enabled) == (False, True)
    assert cfg.bpm_callback_url == ""
    assert cfg.smtp_host == "smtp.example.kz"
    assert cfg.smtp_port == 587
    assert cfg.smtp_security == "starttls"
    assert cfg.smtp_timeout_seconds == 30
    assert cfg.email_from == "stt@example.kz"
    assert cfg.email_to == ("a@x.kz",)


def test_bpm_and_email():
    cfg = load_config({**REQUIRED, **EMAIL})
    assert (cfg.bpm_enabled, cfg.email_enabled) == (True, True)


def test_email_to_is_comma_separated():
    cfg = load_config({**REQUIRED, **EMAIL, "EMAIL_TO": " a@x.kz, b@x.kz ,"})
    assert cfg.email_to == ("a@x.kz", "b@x.kz")


@pytest.mark.parametrize("missing", ["SMTP_HOST", "EMAIL_FROM", "EMAIL_TO"])
def test_partial_email_config_raises(missing):
    env = {**REQUIRED, **EMAIL}
    del env[missing]
    with pytest.raises(ConfigError, match=missing):
        load_config(env)


@pytest.mark.parametrize("security,port", [("ssl", 465), ("none", 25), ("starttls", 587)])
def test_smtp_port_defaults_follow_security(security, port):
    cfg = load_config({**REQUIRED, **EMAIL, "SMTP_SECURITY": security})
    assert cfg.smtp_port == port
    assert load_config({**REQUIRED, **EMAIL, "SMTP_SECURITY": security, "SMTP_PORT": "2525"}).smtp_port == 2525


def test_invalid_smtp_security_raises():
    with pytest.raises(ConfigError, match="SMTP_SECURITY"):
        load_config({**REQUIRED, **EMAIL, "SMTP_SECURITY": "tls"})


def test_llm_vars_required_only_when_summary_enabled():
    env = {k: v for k, v in REQUIRED.items() if not k.startswith("LLM_")}
    with pytest.raises(ConfigError, match="LLM_API_URL"):
        load_config(env)
    cfg = load_config({**env, "SUMMARY_ENABLED": "false"})
    assert cfg.summary_enabled is False
    assert cfg.llm_api_url == ""


def test_url_trailing_slashes_stripped():
    cfg = load_config({**REQUIRED, "WHISPER_API_URL": "http://w:8000/v1/", "LLM_API_URL": "http://l:8000/v1/"})
    assert cfg.whisper_api_url == "http://w:8000/v1"
    assert cfg.llm_api_url == "http://l:8000/v1"
