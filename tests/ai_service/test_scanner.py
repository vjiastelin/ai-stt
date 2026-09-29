from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from ai_service.db import JobStore
from ai_service.s3io import parse_call_record_url
from ai_service.scanner import Scanner, call_record_id_for, object_url


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="calls")
        yield client


@pytest.fixture
def store(tmp_path):
    return JobStore(str(tmp_path / "jobs.db"))


def make_scanner(service_config, store, s3, **overrides):
    cfg = service_config(**{"s3_scan_bucket": "calls", **overrides})
    return Scanner(cfg, store, s3)


def put(s3, *keys):
    for key in keys:
        s3.put_object(Bucket="calls", Key=key, Body=b"RIFF-fake")


def test_enqueues_new_recordings_once(service_config, store, s3):
    put(s3, "2026/a.mp3", "2026/b.WAV")
    scanner = make_scanner(service_config, store, s3)

    assert scanner.scan_once() == 2
    assert scanner.scan_once() == 0  # nothing new → nothing queued

    put(s3, "2026/c.mp3")
    assert scanner.scan_once() == 1

    urls = sorted(j.call_record_url for j in store.list_jobs())
    assert urls == ["s3://calls/2026/a.mp3", "s3://calls/2026/b.WAV", "s3://calls/2026/c.mp3"]
    assert all(j.status == "queued" for j in store.list_jobs())


def test_skips_non_audio_and_folder_markers(service_config, store, s3):
    put(s3, "2026/", "2026/notes.txt", "2026/rec.ogg", "2026/rec.mp3")
    assert make_scanner(service_config, store, s3).scan_once() == 1


def test_prefix_limits_scan(service_config, store, s3):
    put(s3, "in/a.mp3", "archive/b.mp3")
    scanner = make_scanner(service_config, store, s3, s3_scan_prefix="in/")
    assert scanner.scan_once() == 1
    assert [j.call_record_url for j in store.list_jobs()] == ["s3://calls/in/a.mp3"]


def test_modified_after_skips_older_objects(service_config, store, s3):
    put(s3, "old.mp3")
    future = datetime.now(timezone.utc) + timedelta(days=1)
    assert make_scanner(service_config, store, s3, s3_scan_modified_after=future).scan_once() == 0
    past = datetime.now(timezone.utc) - timedelta(days=1)
    assert make_scanner(service_config, store, s3, s3_scan_modified_after=past).scan_once() == 1


def test_failed_job_is_not_requeued_by_rescan(service_config, store, s3):
    put(s3, "a.mp3")
    scanner = make_scanner(service_config, store, s3)
    scanner.scan_once()
    job_id = call_record_id_for("calls", "a.mp3")
    store.mark_failed(job_id, "corrupt audio")

    assert scanner.scan_once() == 0
    assert store.get(job_id).status == "failed"


def test_state_survives_restart(service_config, tmp_path, s3):
    put(s3, "a.mp3")
    path = str(tmp_path / "jobs.db")
    make_scanner(service_config, JobStore(path), s3).scan_once()
    assert make_scanner(service_config, JobStore(path), s3).scan_once() == 0


def test_paginates_large_buckets(service_config, store, s3):
    put(s3, *(f"rec-{i:04d}.mp3" for i in range(1005)))  # > one 1000-key page
    assert make_scanner(service_config, store, s3).scan_once() == 1005


def test_existing_job_with_same_id_is_left_alone(service_config, store, s3):
    put(s3, "a.mp3")
    job_id = call_record_id_for("calls", "a.mp3")
    store.enqueue(job_id, "s3://calls/a.mp3")
    store.set_result(job_id, "t", "s")

    assert make_scanner(service_config, store, s3).scan_once() == 1  # recorded as seen
    assert store.get(job_id).status == "delivering"  # not reset to queued


def test_ids_are_stable_and_urls_roundtrip():
    assert call_record_id_for("calls", "a.mp3") == call_record_id_for("calls", "a.mp3")
    assert call_record_id_for("calls", "a.mp3") != call_record_id_for("calls", "b.mp3")
    key = "2026/звонок #1 100%.mp3"
    assert parse_call_record_url(object_url("calls", key)) == ("calls", key)


def test_missing_bucket_raises_for_run_forever_to_log(service_config, store, s3):
    import botocore.exceptions

    scanner = make_scanner(service_config, store, s3, s3_scan_bucket="nope")
    with pytest.raises(botocore.exceptions.ClientError):
        scanner.scan_once()


def test_run_forever_survives_errors_and_stops(service_config, store, s3, monkeypatch):
    scanner = make_scanner(service_config, store, s3, s3_scan_bucket="nope")
    calls = []

    def fake_wait(seconds):
        calls.append(seconds)
        scanner.stop()
        return True

    monkeypatch.setattr(scanner._stop, "wait", fake_wait)
    scanner.run_forever()  # error is logged, not raised
    assert calls == [300]
