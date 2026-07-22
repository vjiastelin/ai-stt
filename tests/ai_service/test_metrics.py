"""Prometheus metrics: /metrics endpoint, queue gauges, pipeline instrumentation.

The prometheus_client registry is global and survives across tests, so every
assertion on counters/histograms is a before/after delta, never an absolute.
"""
import boto3
import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from moto import mock_aws
from prometheus_client import REGISTRY

from ai_service.app import create_app
from ai_service.db import JobStore
from ai_service.worker import Worker

WHISPER_URL = "http://whisper-api:8000/v1/audio/transcriptions"
LLM_URL = "http://llm:8000/v1/chat/completions"
BPM_URL_REGEX = r"http://bpm/0/ServiceModel/AnGetTranscriptionResultService\.svc/transcriptions/[^/]+/result"

VERBOSE_JSON = {
    "task": "transcribe",
    "language": "ru",
    "duration": 2.5,
    "text": "привет мир",
    "segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": " привет мир"}],
}
CHAT_RESPONSE = {"choices": [{"message": {"role": "assistant", "content": "Суть звонка."}}]}

STAGES = ("download", "transcribe", "summarize", "callback")


def sample(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


@pytest.fixture
def env(service_config, tmp_path, monkeypatch):
    """moto S3 with one call record + store + worker with sleeps disabled."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="call-records")
        s3.put_object(Bucket="call-records", Key="rec.mp3", Body=b"RIFF-fake")
        store = JobStore(str(tmp_path / "worker.db"))
        worker = Worker(service_config(), store, s3)
        monkeypatch.setattr(worker, "_sleep", lambda seconds: None)
        yield store, worker


def test_store_counts_and_oldest_queued(tmp_path):
    store = JobStore(str(tmp_path / "db.sqlite"))
    assert store.counts_by_status() == {}
    assert store.oldest_queued_created_at() is None

    store.enqueue("a", "s3://b/a.mp3")
    store.enqueue("b", "s3://b/b.mp3")
    store.set_status("b", "processing")

    assert store.counts_by_status() == {"queued": 1, "processing": 1}
    assert store.oldest_queued_created_at() == store.get("a").created_at


def test_metrics_endpoint_reports_queue_state(service_config):
    cfg = service_config()
    store = JobStore(cfg.db_path)
    client = TestClient(create_app(cfg, store))
    enqueued_before = sample("ai_service_jobs_enqueued_total")

    response = client.post(
        "/requestTranscription",
        json={"CallRecordId": "m-1", "CallRecordUrl": "s3://call-records/rec.mp3"},
    )
    assert response.status_code == 200
    assert sample("ai_service_jobs_enqueued_total") == enqueued_before + 1

    scrape = client.get("/metrics")
    assert scrape.status_code == 200
    assert scrape.headers["content-type"].startswith("text/plain")
    assert 'ai_service_queue_jobs{status="queued"} 1.0' in scrape.text
    assert 'ai_service_queue_jobs{status="processing"} 0.0' in scrape.text
    assert "ai_service_oldest_queued_age_seconds" in scrape.text


def test_metrics_endpoint_empty_queue_zeroes_gauges(service_config):
    cfg = service_config()
    client = TestClient(create_app(cfg, JobStore(cfg.db_path)))
    scrape = client.get("/metrics")
    assert 'ai_service_queue_jobs{status="queued"} 0.0' in scrape.text
    assert "ai_service_oldest_queued_age_seconds 0.0" in scrape.text


@respx.mock
def test_happy_path_observes_all_stages(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    respx.post(LLM_URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))

    before_done = sample("ai_service_jobs_resolved_total", {"status": "done"})
    before_stage = {
        s: sample("ai_service_stage_duration_seconds_count", {"stage": s}) for s in STAGES
    }
    before_rtf = sample("ai_service_transcribe_rtf_count")
    before_audio = sample("ai_service_audio_seconds_total")
    before_e2e = sample("ai_service_job_end_to_end_seconds_count")

    store.enqueue("m-ok", "s3://call-records/rec.mp3")
    worker.run_once()  # process → delivering
    worker.run_once()  # deliver → done
    assert store.get("m-ok").status == "done"

    assert sample("ai_service_jobs_resolved_total", {"status": "done"}) == before_done + 1
    for stage in STAGES:
        after = sample("ai_service_stage_duration_seconds_count", {"stage": stage})
        assert after == before_stage[stage] + 1, stage
    assert sample("ai_service_transcribe_rtf_count") == before_rtf + 1
    assert sample("ai_service_audio_seconds_total") == pytest.approx(before_audio + 2.5)
    assert sample("ai_service_job_end_to_end_seconds_count") == before_e2e + 1


@respx.mock
def test_permanent_failure_counts_retries_and_failed(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(400, json={"detail": "corrupt"}))
    respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))

    before_failed = sample("ai_service_jobs_resolved_total", {"status": "failed"})
    before_retries = sample("ai_service_job_retries_total", {"kind": "permanent"})
    before_errors = sample(
        "ai_service_stage_errors_total", {"stage": "transcribe", "kind": "permanent"}
    )

    store.enqueue("m-bad", "s3://call-records/rec.mp3")
    for _ in range(3):  # MAX_RETRIES=3 → two retries, then route to delivering
        worker.run_once()
    worker.run_once()  # deliver the failure to BPM → failed
    assert store.get("m-bad").status == "failed"

    assert sample("ai_service_jobs_resolved_total", {"status": "failed"}) == before_failed + 1
    assert sample("ai_service_job_retries_total", {"kind": "permanent"}) == before_retries + 2
    assert (
        sample("ai_service_stage_errors_total", {"stage": "transcribe", "kind": "permanent"})
        == before_errors + 3
    )


@respx.mock
def test_infrastructure_error_counts_stage_error_not_duration(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(503))

    before_err = sample(
        "ai_service_stage_errors_total", {"stage": "transcribe", "kind": "infrastructure"}
    )
    before_count = sample("ai_service_stage_duration_seconds_count", {"stage": "transcribe"})

    store.enqueue("m-infra", "s3://call-records/rec.mp3")
    with pytest.raises(Exception):
        worker.run_once()

    assert (
        sample("ai_service_stage_errors_total", {"stage": "transcribe", "kind": "infrastructure"})
        == before_err + 1
    )
    # failed stage must not pollute the latency histogram
    assert sample("ai_service_stage_duration_seconds_count", {"stage": "transcribe"}) == before_count
