import pytest

from ai_service.db import JobStore


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "jobs.db"))


def test_enqueue_and_get(store):
    job = store.enqueue("id-1", "s3://bucket/a.wav")
    assert (job.call_record_id, job.status, job.attempts) == ("id-1", "queued", 0)
    assert store.get("id-1") == job
    assert store.get("missing") is None


def test_enqueue_is_idempotent(store):
    store.enqueue("id-1", "s3://bucket/a.wav")
    store.set_status("id-1", "processing")
    again = store.enqueue("id-1", "s3://bucket/a.wav")
    assert again.status == "processing"  # not reset


def test_enqueue_requeues_failed_job(store):
    store.enqueue("id-1", "s3://bucket/a.wav")
    store.increment_attempts("id-1", "boom")
    store.mark_failed("id-1", "boom")
    job = store.enqueue("id-1", "s3://bucket/a.wav")
    assert (job.status, job.attempts, job.error) == ("queued", 0, None)


def test_next_pending_returns_oldest_queued_or_processing(store):
    store.enqueue("id-1", "s3://b/1.wav")
    store.enqueue("id-2", "s3://b/2.wav")
    store.set_status("id-1", "processing")  # restart-resume case
    assert store.next_pending().call_record_id == "id-1"
    store.mark_failed("id-1", "x")
    assert store.next_pending().call_record_id == "id-2"


def test_set_result_moves_to_delivering(store):
    store.enqueue("id-1", "s3://b/1.wav")
    store.set_result("id-1", "[00:00:00] привет", "краткое содержание")
    job = store.get("id-1")
    assert job.status == "delivering"
    assert job.full_text == "[00:00:00] привет"
    assert job.summary == "краткое содержание"
    assert store.next_pending() is None
    assert [j.call_record_id for j in store.list_delivering()] == ["id-1"]


def test_attempts_and_failed(store):
    store.enqueue("id-1", "s3://b/1.wav")
    assert store.increment_attempts("id-1", "err1") == 1
    assert store.increment_attempts("id-1", "err2") == 2
    assert store.get("id-1").error == "err2"
    store.mark_failed("id-1", "final")
    job = store.get("id-1")
    assert (job.status, job.error) == ("failed", "final")
    assert store.next_pending() is None


def test_survives_reopen(store, tmp_path):
    store.enqueue("id-1", "s3://b/1.wav")
    store.set_result("id-1", "текст", "суть")
    reopened = JobStore(str(tmp_path / "jobs.db"))
    job = reopened.get("id-1")
    assert (job.status, job.full_text, job.summary) == ("delivering", "текст", "суть")
