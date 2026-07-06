import pytest

from ai_service.config import DEFAULT_SUMMARY_PROMPT, ConfigError, load_config

REQUIRED = {
    "S3_ENDPOINT_URL": "http://minio:9000",
    "S3_ACCESS_KEY": "ak",
    "S3_SECRET_KEY": "sk",
    "BPM_CALLBACK_URL": "http://bpm/onTranscriptionComplete",
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
    assert cfg.max_retries == 3
    assert cfg.retry_backoff_cap_seconds == 300
    assert cfg.db_path == "/data/jobs.db"
    assert cfg.port == 8080
    assert cfg.log_level == "INFO"


def test_missing_required_var_raises():
    env = dict(REQUIRED)
    del env["BPM_CALLBACK_URL"]
    with pytest.raises(ConfigError, match="BPM_CALLBACK_URL"):
        load_config(env)


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
