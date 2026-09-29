"""Incremental S3 bucket scanner: an alternative way to start transcriptions.

Besides BPM calling POST /requestTranscription, ai-service can poll
S3_SCAN_URL (s3://bucket[/prefix]) and queue every new .mp3/.wav object.
Each object is enqueued exactly once (tracked in the s3_objects table), so a
rescan only picks up keys it has not seen before.
"""
import logging
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import botocore.exceptions

from ai_service import metrics
from ai_service.config import ServiceConfig
from ai_service.db import JobStore
from ai_service.s3io import AUDIO_CONTENT_TYPES

logger = logging.getLogger(__name__)


def object_url(bucket: str, key: str) -> str:
    # quoted so keys with spaces/#/% survive parse_call_record_url's unquote
    return f"s3://{bucket}/{urllib.parse.quote(key, safe='/')}"


def call_record_id_for(bucket: str, key: str) -> str:
    """Stable id for a scanned object (same object → same id across restarts)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"s3://{bucket}/{key}"))


class Scanner:
    def __init__(self, cfg: ServiceConfig, store: JobStore, s3_client):
        self.cfg = cfg
        self.store = store
        self.s3 = s3_client
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def scan_once(self) -> int:
        """List the bucket once and enqueue unseen recordings. Returns how many."""
        bucket, prefix = self.cfg.s3_scan_bucket, self.cfg.s3_scan_prefix
        known = self.store.known_s3_keys(bucket, prefix)
        added = 0
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key in known or Path(key).suffix.lower() not in AUDIO_CONTENT_TYPES:
                    continue
                after = self.cfg.s3_scan_modified_after
                if after is not None and obj["LastModified"] < after:
                    continue
                call_record_id = call_record_id_for(bucket, key)
                if self.store.enqueue_discovered(
                    bucket, key, obj.get("ETag"), call_record_id, object_url(bucket, key)
                ):
                    added += 1
                    metrics.JOBS_ENQUEUED.inc()
                    metrics.S3_SCAN_DISCOVERED.inc()
                    logger.info("discovered s3://%s/%s → %s", bucket, key, call_record_id)
        return added

    def run_forever(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                added = self.scan_once()
            except (botocore.exceptions.BotoCoreError, botocore.exceptions.ClientError) as exc:
                metrics.S3_SCAN_ERRORS.inc()
                logger.warning("S3 scan of %s failed, retry next interval: %s",
                               self.cfg.s3_scan_bucket, exc)
            except Exception:
                metrics.S3_SCAN_ERRORS.inc()
                logger.exception("unexpected S3 scan error")
            else:
                metrics.S3_SCAN_LAST_SUCCESS.set_to_current_time()
                logger.log(
                    logging.INFO if added else logging.DEBUG,
                    "S3 scan: %d new recording(s) in %.1fs", added, time.monotonic() - started,
                )
            self._stop.wait(self.cfg.s3_scan_interval_seconds)
