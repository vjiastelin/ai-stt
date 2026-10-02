"""Fetch the model before loading it: timeouts, retries, a deadline and progress.

faster-whisper downloads a missing model from the Hugging Face Hub inside
`WhisperModel(...)`, silently and with no time limit — on a fresh vast.ai
instance that looks like a hang. Here the download runs first, in a worker
thread watched by the loader:

- progress (MB so far, MB/s) goes to the log and to the /health 503 detail —
  measured on the cache directory, so it moves only with plain HTTP downloads
  (HF_HUB_DISABLE_XET=1, the image default): Xet writes a file only at the end;
- a failed attempt is retried with backoff; the hub resumes partial files;
- WHISPER_DOWNLOAD_TIMEOUT_SECONDS bounds the whole download — past it the
  loader gives up (a stuck HTTP read can't be cancelled from Python, so the
  caller exits the process and the container restart resumes the download).

A model given as a local directory, or HF_HUB_OFFLINE=1 with a filled cache,
never touches the network.
"""
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# the files faster-whisper's own download_model() fetches
ALLOW_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json",
                  "vocabulary.*"]


class DownloadError(Exception):
    """The model could not be fetched within the retries / deadline."""


def resolve_repo(model: str) -> str:
    """Hub repo id for a faster-whisper size name ("large-v3") or an explicit "org/repo"."""
    if "/" in model:
        return model
    try:
        from faster_whisper.utils import _MODELS  # size name → repo id, as WhisperModel does
    except ImportError:  # pragma: no cover - only without the `api` extra
        _MODELS = {}
    return _MODELS.get(model, f"Systran/faster-whisper-{model}")


def cache_root() -> Path:
    """Where the hub keeps downloads (HF_HUB_CACHE, else $HF_HOME/hub)."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def _tree_size(path: Path) -> int:
    """Bytes under `path`; snapshot entries are symlinks to blobs, getsize follows them."""
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:  # file renamed/removed while walking (.incomplete → blob)
                pass
    return total


def _offline() -> bool:
    return os.environ.get("HF_HUB_OFFLINE", "").strip().lower() in ("1", "true", "yes")


def _snapshot_download(repo_id: str) -> str:  # pragma: no cover - real network
    from huggingface_hub import snapshot_download

    return snapshot_download(repo_id, allow_patterns=ALLOW_PATTERNS, local_files_only=_offline())


def ensure_model(
    model: str,
    *,
    retries: int = 5,
    deadline_seconds: float = 1800,
    progress_interval: float = 15,
    status: Callable[[str], None] = lambda text: None,
    download: Callable[[str], str] = _snapshot_download,
    measure: Callable[[], int] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Return a local directory holding the model, downloading it if needed."""
    if os.path.isdir(model):
        return model
    repo_id = resolve_repo(model)
    measure = measure or (lambda: _tree_size(cache_root().parent))  # hub + xet caches
    started, baseline = clock(), measure()
    deadline = started + deadline_seconds

    for attempt in range(1, retries + 1):
        result: dict = {}

        def work() -> None:
            try:
                result["path"] = download(repo_id)
            except BaseException as exc:  # noqa: BLE001 - reported to the waiting loader
                result["error"] = exc

        thread = threading.Thread(target=work, name=f"model-download-{attempt}", daemon=True)
        thread.start()
        logger.info("fetching model %s (attempt %d/%d)", repo_id, attempt, retries)
        last_bytes, last_time = measure(), clock()
        while thread.is_alive():
            thread.join(min(progress_interval, max(0.0, deadline - clock())))
            now = clock()
            if thread.is_alive() and now >= deadline:
                raise DownloadError(
                    f"model {repo_id} not fetched within {deadline_seconds:.0f}s "
                    f"({(measure() - baseline) / 1e6:.0f} MB downloaded)"
                )
            if thread.is_alive():
                size = measure()
                speed = (size - last_bytes) / max(now - last_time, 1e-6) / 1e6
                text = (f"downloading model {repo_id}: {(size - baseline) / 1e6:.0f} MB so far, "
                        f"{speed:.1f} MB/s, {now - started:.0f}s elapsed")
                logger.info(text)
                status(text)
                last_bytes, last_time = size, now
        if "path" in result:
            logger.info("model %s ready at %s (%.0f MB, %.0fs)", repo_id, result["path"],
                        _tree_size(Path(result["path"])) / 1e6, clock() - started)
            return result["path"]
        error = result.get("error")
        if attempt == retries or _offline():
            raise DownloadError(f"model {repo_id} could not be fetched: {error}") from error
        delay = min(10 * 2 ** (attempt - 1), 120, max(0.0, deadline - clock()))
        logger.warning("model download attempt %d failed (%s), retrying in %.0fs", attempt, error, delay)
        status(f"model download attempt {attempt} failed, retrying")
        sleep(delay)
    raise DownloadError(f"model {repo_id} could not be fetched")  # pragma: no cover
