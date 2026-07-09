import json

import boto3
import httpx
import pytest
import respx
from moto import mock_aws

from ai_service.db import JobStore
from ai_service.errors import InfrastructureError
from ai_service.worker import Backoff, Worker

WHISPER_URL = "http://whisper-api:8000/v1/audio/transcriptions"
LLM_URL = "http://llm:8000/v1/chat/completions"
BPM_URL = "http://bpm/onTranscriptionComplete"

VERBOSE_JSON = {
    "task": "transcribe",
    "language": "ru",
    "duration": 2.5,
    "text": "привет мир",
    "segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": " привет мир"}],
}
CHAT_RESPONSE = {"choices": [{"message": {"role": "assistant", "content": "Суть звонка."}}]}


def test_backoff_doubles_and_caps():
    backoff = Backoff(cap=300)
    assert [backoff.next() for _ in range(8)] == [5, 10, 20, 40, 80, 160, 300, 300]
    backoff.reset()
    assert backoff.next() == 5


@pytest.fixture
def env(service_config, tmp_path, monkeypatch):
    """moto S3 with one call record + store + worker with sleeps disabled."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="call-records")
        s3.put_object(Bucket="call-records", Key="rec.wav", Body=b"RIFF-fake")
        store = JobStore(str(tmp_path / "worker.db"))
        worker = Worker(service_config(), store, s3)
        monkeypatch.setattr(worker, "_sleep", lambda seconds: None)
        yield store, worker


@respx.mock
def test_happy_path_process_then_deliver(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    respx.post(LLM_URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    bpm = respx.post(BPM_URL).mock(return_value=httpx.Response(200))
    store.enqueue("id-1", "s3://call-records/rec.wav")

    assert worker.run_once() is True   # process → delivering
    assert store.get("id-1").status == "delivering"
    assert worker.run_once() is True   # deliver → done
    assert store.get("id-1").status == "done"

    body = json.loads(bpm.calls.last.request.content)
    assert body == {
        "CallRecordId": "id-1",
        "Summary": "Суть звонка.",
        "FullText": "[00:00:00] привет мир",
    }
    assert worker.run_once() is False  # nothing left


@respx.mock
def test_summary_disabled_skips_llm(env, service_config):
    store, _ = env
    s3 = boto3.client("s3", region_name="us-east-1")  # same moto backend as the fixture
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    llm = respx.post(LLM_URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    bpm = respx.post(BPM_URL).mock(return_value=httpx.Response(200))
    worker = Worker(service_config(summary_enabled=False), store, s3)
    store.enqueue("id-1", "s3://call-records/rec.wav")

    worker.run_once()
    worker.run_once()

    assert store.get("id-1").status == "done"
    assert not llm.called
    assert json.loads(bpm.calls.last.request.content)["Summary"] == ""


@respx.mock
def test_permanent_error_fails_after_max_retries(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(400, json={"detail": "corrupt"}))
    store.enqueue("id-1", "s3://call-records/rec.wav")

    for expected_attempts in (1, 2):
        worker.run_once()
        job = store.get("id-1")
        assert (job.status, job.attempts) == ("queued", expected_attempts)
    worker.run_once()
    job = store.get("id-1")
    assert (job.status, job.attempts) == ("failed", 3)
    assert "400" in job.error
    assert worker.run_once() is False  # failed job is not picked up


@respx.mock
def test_infrastructure_error_propagates_without_counting(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(503))
    store.enqueue("id-1", "s3://call-records/rec.wav")

    with pytest.raises(InfrastructureError):
        worker.run_once()
    job = store.get("id-1")
    assert (job.status, job.attempts) == ("processing", 0)  # resumed next cycle


@respx.mock
def test_malformed_200_from_whisper_is_infrastructure_not_failed(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, content=b"<html>gateway</html>"))
    store.enqueue("id-1", "s3://call-records/rec.wav")

    with pytest.raises(InfrastructureError):
        worker.run_once()
    job = store.get("id-1")
    assert (job.status, job.attempts) == ("processing", 0)  # not failed, resumed next cycle


@respx.mock
def test_bpm_down_does_not_block_processing(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    respx.post(LLM_URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    respx.post(BPM_URL).mock(return_value=httpx.Response(500))
    store.enqueue("id-1", "s3://call-records/rec.wav")
    store.enqueue("id-2", "s3://call-records/rec.wav")

    worker.run_once()  # processes id-1 → delivering
    assert worker.run_once() is True  # delivery of id-1 fails (logged), id-2 still processed
    assert store.get("id-1").status == "delivering"
    assert store.get("id-2").status == "delivering"

    # nothing left to process and delivery still failing → raises to trigger backoff
    with pytest.raises(InfrastructureError):
        worker.run_once()


@respx.mock
def test_delivering_job_resumes_without_retranscribing(env):
    store, worker = env
    whisper = respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    bpm = respx.post(BPM_URL).mock(return_value=httpx.Response(200))
    store.enqueue("id-1", "s3://call-records/rec.wav")
    store.set_result("id-1", "[00:00:00] сохранённый текст", "сохранённая суть")

    assert worker.run_once() is True

    assert store.get("id-1").status == "done"
    assert not whisper.called  # delivered from stored result
    assert json.loads(bpm.calls.last.request.content)["FullText"] == "[00:00:00] сохранённый текст"
