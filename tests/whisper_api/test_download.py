import threading
import time

import pytest
from fastapi.testclient import TestClient

from whisper_api.download import DownloadError, ensure_model, resolve_repo


def test_resolve_repo():
    assert resolve_repo("deepdml/faster-whisper-large-v3-turbo-ct2") == "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert resolve_repo("large-v3").endswith("faster-whisper-large-v3")


def test_local_directory_is_used_as_is(tmp_path):
    def boom(repo):  # pragma: no cover - must not be called
        raise AssertionError("no download for a local model")

    assert ensure_model(str(tmp_path), download=boom) == str(tmp_path)


def test_success_returns_snapshot_path():
    calls = []
    path = ensure_model("large-v3", download=lambda repo: calls.append(repo) or "/cache/snap",
                        measure=lambda: 0, progress_interval=0.01)
    assert path == "/cache/snap"
    assert calls == [resolve_repo("large-v3")]


def test_failed_attempts_are_retried_with_backoff():
    attempts, sleeps = [], []

    def flaky(repo):
        attempts.append(repo)
        if len(attempts) < 3:
            raise ConnectionError("reset by peer")
        return "/cache/snap"

    assert ensure_model("org/m", download=flaky, measure=lambda: 0, sleep=sleeps.append,
                        progress_interval=0.01) == "/cache/snap"
    assert len(attempts) == 3
    assert sleeps == [10, 20]


def test_gives_up_after_the_last_retry():
    with pytest.raises(DownloadError, match="could not be fetched: boom"):
        ensure_model("org/m", retries=2, download=lambda r: (_ for _ in ()).throw(OSError("boom")),
                     measure=lambda: 0, sleep=lambda s: None, progress_interval=0.01)


def test_offline_does_not_retry(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    attempts = []

    def missing(repo):
        attempts.append(repo)
        raise FileNotFoundError("not in cache")

    with pytest.raises(DownloadError):
        ensure_model("org/m", download=missing, measure=lambda: 0, sleep=lambda s: None,
                     progress_interval=0.01)
    assert len(attempts) == 1


def test_progress_is_reported_while_downloading():
    size = {"bytes": 0}
    release = threading.Event()

    def slow(repo):
        for _ in range(5):
            size["bytes"] += 50_000_000
            time.sleep(0.02)
        release.wait(1)
        return "/cache/snap"

    statuses = []
    timer = threading.Timer(0.3, release.set)
    timer.start()
    assert ensure_model("org/m", download=slow, measure=lambda: size["bytes"],
                        status=statuses.append, progress_interval=0.05) == "/cache/snap"
    assert statuses and "downloading model org/m:" in statuses[-1]
    assert "MB so far" in statuses[-1] and "MB/s" in statuses[-1]


def test_deadline_stops_a_stuck_download():
    hang = threading.Event()  # never set: the download is stuck on a dead connection
    with pytest.raises(DownloadError, match="not fetched within 0s"):
        ensure_model("org/m", download=lambda r: hang.wait(5) or "/x", measure=lambda: 0,
                     deadline_seconds=0.2, progress_interval=0.05)


# --- wiring into the app ---------------------------------------------------------------

def _app_with(factory, on_fatal=lambda: None):
    from tests.whisper_api.test_app import make_config
    from whisper_api.app import create_app

    return create_app(make_config(), engine_factory=factory, on_fatal=on_fatal)


def test_health_shows_download_progress_while_loading():
    proceed = threading.Event()

    def factory(status):
        status("downloading model org/m: 1200 MB so far, 40.0 MB/s, 30s elapsed")
        proceed.wait(2)
        raise RuntimeError("stop here")

    with TestClient(_app_with(factory)) as client:
        for _ in range(50):
            body = client.get("/health")
            if "downloading" in body.text:
                break
            time.sleep(0.02)
        assert body.status_code == 503
        assert "1200 MB so far" in body.json()["detail"]
        proceed.set()


def test_download_error_exits_the_process():
    exited = threading.Event()

    def factory(status):
        raise DownloadError("model org/m not fetched within 1800s")

    with TestClient(_app_with(factory, on_fatal=exited.set)) as client:
        assert exited.wait(2)
        assert client.get("/health").status_code == 500
