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
# base "http://bpm" + fixed result path; the call_record_id is a path variable
BPM_URL_REGEX = r"http://bpm/0/ServiceModel/AnGetTranscriptionResultService\.svc/transcriptions/[^/]+/result"

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
        s3.put_object(Bucket="call-records", Key="rec.mp3", Body=b"RIFF-fake")
        store = JobStore(str(tmp_path / "worker.db"))
        worker = Worker(service_config(), store, s3)
        monkeypatch.setattr(worker, "_sleep", lambda seconds: None)
        yield store, worker


@respx.mock
def test_happy_path_process_then_deliver(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    respx.post(LLM_URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    store.enqueue("id-1", "s3://call-records/rec.mp3")

    assert worker.run_once() is True   # process → delivering
    assert store.get("id-1").status == "delivering"
    assert worker.run_once() is True   # deliver → done
    assert store.get("id-1").status == "done"

    assert bpm.calls.last.request.url.path.endswith("/transcriptions/id-1/result")
    body = json.loads(bpm.calls.last.request.content)
    assert body == {
        "Summary": "Суть звонка.",
        "FullText": "[00:00:00] привет мир",
        "Error": False,
        "ErrorDescription": "",
    }
    assert worker.run_once() is False  # nothing left


@respx.mock
def test_summary_disabled_skips_llm(env, service_config):
    store, _ = env
    s3 = boto3.client("s3", region_name="us-east-1")  # same moto backend as the fixture
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    llm = respx.post(LLM_URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    worker = Worker(service_config(summary_enabled=False), store, s3)
    store.enqueue("id-1", "s3://call-records/rec.mp3")

    worker.run_once()
    worker.run_once()

    assert store.get("id-1").status == "done"
    assert not llm.called
    assert json.loads(bpm.calls.last.request.content)["Summary"] == ""


@respx.mock
def test_permanent_error_delivers_failure_after_max_retries(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(400, json={"detail": "corrupt"}))
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    store.enqueue("id-1", "s3://call-records/rec.mp3")

    for expected_attempts in (1, 2):
        worker.run_once()
        job = store.get("id-1")
        assert (job.status, job.attempts) == ("queued", expected_attempts)

    worker.run_once()  # third attempt exhausts retries → route to delivering
    job = store.get("id-1")
    assert (job.status, job.attempts) == ("delivering", 3)

    assert worker.run_once() is True  # deliver the failure → failed
    job = store.get("id-1")
    assert job.status == "failed"
    assert "400" in job.error

    body = json.loads(bpm.calls.last.request.content)
    assert body["Error"] is True
    assert "400" in body["ErrorDescription"]
    assert body["FullText"] == ""

    assert worker.run_once() is False  # failed job is not picked up again


@respx.mock
def test_infrastructure_error_propagates_without_counting(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(503))
    store.enqueue("id-1", "s3://call-records/rec.mp3")

    with pytest.raises(InfrastructureError):
        worker.run_once()
    job = store.get("id-1")
    assert (job.status, job.attempts) == ("processing", 0)  # resumed next cycle


@respx.mock
def test_malformed_200_from_whisper_is_infrastructure_not_failed(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, content=b"<html>gateway</html>"))
    store.enqueue("id-1", "s3://call-records/rec.mp3")

    with pytest.raises(InfrastructureError):
        worker.run_once()
    job = store.get("id-1")
    assert (job.status, job.attempts) == ("processing", 0)  # not failed, resumed next cycle


@respx.mock
def test_bpm_down_does_not_block_processing(env):
    store, worker = env
    respx.post(WHISPER_URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))
    respx.post(LLM_URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(500))
    store.enqueue("id-1", "s3://call-records/rec.mp3")
    store.enqueue("id-2", "s3://call-records/rec.mp3")

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
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    store.enqueue("id-1", "s3://call-records/rec.mp3")
    store.set_result("id-1", "[00:00:00] сохранённый текст", "сохранённая суть")

    assert worker.run_once() is True

    assert store.get("id-1").status == "done"
    assert not whisper.called  # delivered from stored result
    assert json.loads(bpm.calls.last.request.content)["FullText"] == "[00:00:00] сохранённый текст"


EMAIL_CFG = dict(smtp_host="smtp.example.kz", email_from="stt@example.kz", email_to=("ops@example.kz",))


@pytest.fixture
def sent_emails(monkeypatch):
    """Replaces the SMTP transport; set `.fail = True` to simulate an outage."""
    from ai_service import mailer

    class Outbox(list):
        fail = False

    outbox = Outbox()

    def fake_deliver(cfg, call_record_id, summary, full_text, error=False, error_description=""):
        if outbox.fail:
            raise InfrastructureError("smtp down")
        outbox.append(dict(id=call_record_id, summary=summary, full_text=full_text,
                           error=error, error_description=error_description))

    monkeypatch.setattr(mailer, "deliver", fake_deliver)
    return outbox


def _email_worker(store, service_config, monkeypatch, **overrides):
    s3 = boto3.client("s3", region_name="us-east-1")  # same moto backend as the env fixture
    worker = Worker(service_config(**{**EMAIL_CFG, **overrides}), store, s3)
    monkeypatch.setattr(worker, "_sleep", lambda seconds: None)
    return worker


@respx.mock
def test_email_only_delivers_by_email(env, service_config, monkeypatch, sent_emails):
    store, _ = env
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    worker = _email_worker(store, service_config, monkeypatch, bpm_callback_url="")
    store.enqueue("id-1", "s3://call-records/rec.mp3")
    store.set_result("id-1", "[00:00:00] текст", "суть")

    assert worker.run_once() is True

    assert store.get("id-1").status == "done"
    assert not bpm.called
    assert sent_emails == [dict(id="id-1", summary="суть", full_text="[00:00:00] текст",
                                error=False, error_description="")]


@respx.mock
def test_bpm_and_email_both_receive_result(env, service_config, monkeypatch, sent_emails):
    store, _ = env
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    worker = _email_worker(store, service_config, monkeypatch)
    store.enqueue("id-1", "s3://call-records/rec.mp3")
    store.set_result("id-1", "[00:00:00] текст", "суть")

    assert worker.run_once() is True

    job = store.get("id-1")
    assert (job.status, job.delivered_channels) == ("done", {"bpm", "email"})
    assert bpm.call_count == 1
    assert len(sent_emails) == 1


@respx.mock
def test_failed_channel_retried_without_resending_delivered_one(
    env, service_config, monkeypatch, sent_emails
):
    store, _ = env
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    worker = _email_worker(store, service_config, monkeypatch)
    store.enqueue("id-1", "s3://call-records/rec.mp3")
    store.set_result("id-1", "t", "s")
    sent_emails.fail = True

    with pytest.raises(InfrastructureError):
        worker.run_once()  # BPM accepted, SMTP down → stays delivering
    job = store.get("id-1")
    assert (job.status, job.delivered_channels) == ("delivering", {"bpm"})

    sent_emails.fail = False
    assert worker.run_once() is True
    assert store.get("id-1").status == "done"
    assert bpm.call_count == 1  # not resent
    assert len(sent_emails) == 1


@respx.mock
def test_failure_notification_goes_to_email_too(env, service_config, monkeypatch, sent_emails):
    store, _ = env
    bpm = respx.post(url__regex=BPM_URL_REGEX).mock(return_value=httpx.Response(200))
    worker = _email_worker(store, service_config, monkeypatch)
    store.enqueue("id-1", "s3://call-records/rec.mp3")
    store.set_failed_result("id-1", "corrupt audio")

    assert worker.run_once() is True

    assert store.get("id-1").status == "failed"
    assert json.loads(bpm.calls.last.request.content)["Error"] is True
    assert sent_emails[0]["error"] is True
    assert sent_emails[0]["error_description"] == "corrupt audio"
