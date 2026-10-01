import pytest

from ai_service.config import ServiceConfig


@pytest.fixture
def service_config(tmp_path):
    def make(**overrides):
        base = dict(
            s3_endpoint_url="http://localhost:9000",
            s3_access_key="test",
            s3_secret_key="test",
            s3_scan_bucket="",
            s3_scan_prefix="",
            s3_scan_interval_seconds=300,
            s3_scan_modified_after=None,
            whisper_api_url="http://whisper-api:8000/v1",
            whisper_model="large-v3",
            whisper_timeout_seconds=5,
            whisper_api_key="",
            whisper_verify_ssl=True,
            whisper_prompt="",
            language="ru",
            summary_enabled=True,
            llm_api_url="http://llm:8000/v1",
            llm_api_key="",
            llm_model="test-model",
            llm_timeout_seconds=5,
            llm_verify_ssl=True,
            llm_extra_body={},
            summary_prompt="Составь краткое содержание.",
            prompt_profile="call",
            bpm_callback_url="http://bpm",
            bpm_csrf_token="",
            callback_timeout_seconds=5,
            smtp_host="",
            smtp_port=587,
            smtp_security="starttls",
            smtp_username="",
            smtp_password="",
            smtp_timeout_seconds=5,
            email_from="",
            email_to=(),
            max_retries=3,
            retry_backoff_cap_seconds=300,
            db_path=str(tmp_path / "jobs.db"),
            port=8080,
            log_level="INFO",
        )
        base.update(overrides)
        return ServiceConfig(**base)

    return make
