"""Background job loop: pick → process → deliver, with retry taxonomy (spec §3.3, §3.5)."""
import logging
import tempfile
import threading
import time
from pathlib import Path

from ai_service import callback, formats, s3io, summarize, transcribe
from ai_service.config import ServiceConfig
from ai_service.db import Job, JobStore
from ai_service.errors import InfrastructureError, PermanentJobError

logger = logging.getLogger(__name__)


class Backoff:
    def __init__(self, cap: float, base: float = 5.0):
        self.base = base
        self.cap = cap
        self._step = 0

    def next(self) -> float:
        delay = min(self.base * (2 ** self._step), self.cap)
        self._step += 1
        return delay

    def reset(self) -> None:
        self._step = 0


class Worker:
    def __init__(self, cfg: ServiceConfig, store: JobStore, s3_client, poll_interval: float = 2.0):
        self.cfg = cfg
        self.store = store
        self.s3 = s3_client
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._backoff = Backoff(cap=cfg.retry_backoff_cap_seconds)

    def stop(self) -> None:
        self._stop.set()

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except InfrastructureError as exc:
                delay = self._backoff.next()
                logger.warning("dependency unavailable (retry in %.0fs): %s", delay, exc)
                self._sleep(delay)
                continue
            except Exception:
                delay = self._backoff.next()
                logger.exception("unexpected worker error (retry in %.0fs)", delay)
                self._sleep(delay)
                continue
            self._backoff.reset()
            if not worked:
                self._sleep(self.poll_interval)

    def run_once(self) -> bool:
        """Handle at most one processing step. Returns True if any job advanced."""
        worked = False
        delivery_error: InfrastructureError | None = None

        for job in self.store.list_delivering():
            try:
                self._deliver(job)
                worked = True
            except InfrastructureError as exc:
                delivery_error = exc
                logger.warning("callback for %s failed, will retry: %s", job.call_record_id, exc)

        job = self.store.next_pending()
        if job is not None:
            self._process(job)  # InfrastructureError propagates to run_forever
            worked = True

        if not worked and delivery_error is not None:
            raise delivery_error  # nothing else to do: back off instead of hammering BPM
        return worked

    def _process(self, job: Job) -> None:
        self.store.set_status(job.call_record_id, "processing")
        started = time.monotonic()
        try:
            bucket, key = s3io.parse_call_record_url(job.call_record_url)
            with tempfile.TemporaryDirectory() as tmp:
                wav_path = Path(tmp) / "audio.wav"
                s3io.download(self.s3, bucket, key, wav_path)
                result = transcribe.transcribe_file(self.cfg, wav_path)
            full_text = formats.to_full_text(result.segments)
            summary = summarize.summarize(self.cfg, formats.to_plain_text(result.segments))
        except InfrastructureError:
            raise
        except ValueError as exc:  # unparseable URL slipped past API validation
            self.store.mark_failed(job.call_record_id, str(exc))
            logger.error("job %s has invalid URL: %s", job.call_record_id, exc)
            return
        except PermanentJobError as exc:
            attempts = self.store.increment_attempts(job.call_record_id, str(exc))
            if attempts >= self.cfg.max_retries:
                self.store.mark_failed(job.call_record_id, str(exc))
                logger.error(
                    "job %s failed after %d attempts: %s", job.call_record_id, attempts, exc
                )
            else:
                self.store.set_status(job.call_record_id, "queued")
                delay = min(5.0 * (2 ** attempts), self.cfg.retry_backoff_cap_seconds)
                logger.warning(
                    "job %s attempt %d/%d failed (%s), retry in %.0fs",
                    job.call_record_id, attempts, self.cfg.max_retries, exc, delay,
                )
                self._sleep(delay)
            return
        self.store.set_result(job.call_record_id, full_text, summary)
        logger.info(
            "processed %s in %.1fs (%d segments)",
            job.call_record_id, time.monotonic() - started, len(result.segments),
        )

    def _deliver(self, job: Job) -> None:
        callback.deliver(self.cfg, job.call_record_id, job.summary or "", job.full_text or "")
        self.store.set_status(job.call_record_id, "done")
        logger.info("delivered %s to BPM", job.call_record_id)
