"""Prometheus metrics for the transcription pipeline.

Counters/histograms are updated by the worker and API as events happen;
queue gauges are recomputed from the JobStore on every /metrics scrape
(no background thread, no custom collector — keeps tests re-entrant).
"""
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from ai_service.db import JobStore, parse_ts
from ai_service.errors import InfrastructureError, PermanentJobError

JOB_STATUSES = ("queued", "processing", "delivering", "done", "failed")

JOBS_ENQUEUED = Counter(
    "ai_service_jobs_enqueued_total",
    "Transcription requests accepted into the queue (incl. re-queued failed jobs)",
)
JOBS_RESOLVED = Counter(
    "ai_service_jobs_resolved_total",
    "Jobs that reached a terminal state",
    ["status"],  # done | failed
)
JOB_RETRIES = Counter(
    "ai_service_job_retries_total",
    "Retries by error taxonomy (spec §3.5): infrastructure retries don't count "
    "against the job, permanent ones increment its attempts",
    ["kind"],  # infrastructure | permanent
)
STAGE_DURATION = Histogram(
    "ai_service_stage_duration_seconds",
    "Duration of successful pipeline stages",
    ["stage"],  # download | transcribe | summarize | callback
    buckets=(0.5, 1, 2.5, 5, 10, 20, 40, 60, 120, 300, 600),
)
STAGE_ERRORS = Counter(
    "ai_service_stage_errors_total",
    "Stage failures by error taxonomy",
    ["stage", "kind"],
)
E2E_SECONDS = Histogram(
    "ai_service_job_end_to_end_seconds",
    "Enqueue (created_at) to successful delivery to BPM",
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 3600),
)
TRANSCRIBE_RTF = Histogram(
    "ai_service_transcribe_rtf",
    "Transcribe stage wall time divided by audio duration (real-time factor)",
    buckets=(0.02, 0.05, 0.1, 0.2, 0.4, 0.7, 1.0, 1.5),
)
AUDIO_SECONDS = Counter(
    "ai_service_audio_seconds_total",
    "Total seconds of audio transcribed",
)
QUEUE_JOBS = Gauge(
    "ai_service_queue_jobs",
    "Jobs currently in each state (recomputed on scrape)",
    ["status"],
)
OLDEST_QUEUED_AGE = Gauge(
    "ai_service_oldest_queued_age_seconds",
    "Age of the oldest queued job, 0 when the queue is empty (recomputed on scrape)",
)


@contextmanager
def observe_stage(stage: str):
    """Time a pipeline stage; classify failures by the error taxonomy.

    Durations are recorded only for successful runs so timeouts/errors don't
    skew the latency histograms.
    """
    started = time.monotonic()
    try:
        yield
    except InfrastructureError:
        STAGE_ERRORS.labels(stage=stage, kind="infrastructure").inc()
        raise
    except PermanentJobError:
        STAGE_ERRORS.labels(stage=stage, kind="permanent").inc()
        raise
    else:
        STAGE_DURATION.labels(stage=stage).observe(time.monotonic() - started)


def observe_delivered(created_at: str) -> None:
    JOBS_RESOLVED.labels(status="done").inc()
    age = (datetime.now(timezone.utc) - parse_ts(created_at)).total_seconds()
    E2E_SECONDS.observe(max(0.0, age))


def render(store: JobStore) -> tuple[bytes, str]:
    """Refresh queue gauges from the store and render the exposition text."""
    counts = store.counts_by_status()
    for status in JOB_STATUSES:
        QUEUE_JOBS.labels(status=status).set(counts.get(status, 0))
    oldest = store.oldest_queued_created_at()
    age = 0.0
    if oldest is not None:
        age = max(0.0, (datetime.now(timezone.utc) - parse_ts(oldest)).total_seconds())
    OLDEST_QUEUED_AGE.set(age)
    return generate_latest(), CONTENT_TYPE_LATEST
