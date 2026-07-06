# ai-stt BPM Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the BPM-driven speech-to-text system from the approved spec (`docs/superpowers/specs/2026-07-06-ai-stt-bpm-integration-design.md`): `ai-service` (FastAPI + SQLite job queue + background worker) and `whisper-api` (FastAPI + faster-whisper, GPU or CPU).

**Architecture:** BPMSoft POSTs `/requestTranscription` (CallRecordId, CallRecordUrl); `ai-service` stores the job durably in SQLite, downloads the WAV from S3-compatible storage, transcribes via `whisper-api` (OpenAI-compatible endpoint), optionally summarizes via an external OpenAI-compatible LLM, and POSTs `{CallRecordId, Summary, FullText}` to BPM's callback URL, retrying until 200. Infrastructure failures retry forever with capped exponential backoff; permanent per-call failures retry `MAX_RETRIES` then mark the job `failed`.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, boto3, httpx, sqlite3 (stdlib), faster-whisper; tests: pytest, moto, respx.

## Global Constraints

- Python `>=3.11`; packages `ai_service` and `whisper_api` per spec §6 layout (plus `ai_service/errors.py` for the two shared error classes).
- `ai-service` has **no ML dependencies** (`fastapi`, `uvicorn`, `boto3`, `httpx` only); `whisper-api` owns `faster-whisper`.
- Defaults exactly per spec §3.4/§4.2 (e.g. `WHISPER_MODEL=large-v3`, `LANGUAGE=ru`, `SUMMARY_ENABLED=true`, `MAX_RETRIES=3`, `RETRY_BACKOFF_CAP_SECONDS=300`, `DB_PATH=/data/jobs.db`, ai-service `PORT=8080`, whisper-api `PORT=8000`).
- `COMPUTE_TYPE` empty → auto: `float16` if `DEVICE=cuda`, `int8` if `DEVICE=cpu`.
- Error classes: **InfrastructureError** (unreachable/timeout/5xx from S3, whisper-api, LLM; any non-200 from BPM callback) → retry forever with backoff, never counted; **PermanentJobError** (missing S3 object, corrupt audio, whisper/LLM 4xx) → counted, `failed` after `MAX_RETRIES`.
- Every job state change is committed to SQLite before it takes effect; `/requestTranscription` is idempotent by `CallRecordId` (repeat → 200 no-op; `failed` job → re-queued).
- No auth on `/requestTranscription` or the BPM callback (trusted network, v1). `whisper-api` supports optional `API_KEY`.
- BPM field names are PascalCase JSON: `CallRecordId`, `CallRecordUrl`, `Summary`, `FullText`.
- Validation errors on `/requestTranscription` return **400** (not FastAPI's default 422).
- Tests needing model download are marked `slow`, excluded by default via `addopts = -m "not slow"`.
- Conventional commit messages; commit after every green task.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `ai_service/__init__.py`, `whisper_api/__init__.py`, `tests/__init__.py`, `tests/ai_service/__init__.py`, `tests/whisper_api/__init__.py`
- Modify: `docs/superpowers/plans/2026-07-05-ai-stt-pipeline.md:1-3` (mark superseded)

**Interfaces:**
- Consumes: nothing.
- Produces: installable project; `pip install -e .[dev]` works; `pytest` runs (0 tests).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "ai-stt"
version = "0.2.0"
description = "BPM-driven speech-to-text: ai-service + whisper-api"
requires-python = ">=3.11"

[project.optional-dependencies]
service = ["fastapi>=0.111", "uvicorn>=0.30", "boto3>=1.34", "httpx>=0.27"]
api = ["fastapi>=0.111", "uvicorn>=0.30", "python-multipart>=0.0.9", "faster-whisper>=1.0"]
dev = [
    "fastapi>=0.111",
    "uvicorn>=0.30",
    "boto3>=1.34",
    "httpx>=0.27",
    "python-multipart>=0.0.9",
    "pytest>=8",
    "moto[s3]>=5",
    "respx>=0.21",
]

[tool.setuptools]
packages = ["ai_service", "whisper_api"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not slow'"
markers = ["slow: needs model download / real transcription"]
```

(`dev` deliberately excludes `faster-whisper` — heavy download; slow tests use `pytest.importorskip`.)

- [ ] **Step 2: Write `.gitignore`**

```
__pycache__/
*.pyc
*.egg-info/
.venv/
.env
.pytest_cache/
*.db
```

- [ ] **Step 3: Create empty package files**

Create empty: `ai_service/__init__.py`, `whisper_api/__init__.py`, `tests/__init__.py`, `tests/ai_service/__init__.py`, `tests/whisper_api/__init__.py`.

- [ ] **Step 4: Mark the old plan superseded**

In `docs/superpowers/plans/2026-07-05-ai-stt-pipeline.md`, insert directly under the H1 title line:

```markdown
> **SUPERSEDED** by `2026-07-06-ai-stt-bpm-integration.md` — the S3-polling design was replaced by the BPM push/callback integration. Do not execute this plan.
```

- [ ] **Step 5: Install and verify**

Run: `python3 -m venv .venv && .venv/bin/pip install -e .[dev]`
Run: `.venv/bin/pytest`
Expected: `no tests ran` (exit code 5 is fine at this stage).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore ai_service whisper_api tests docs/superpowers/plans/2026-07-05-ai-stt-pipeline.md
git commit -m "build: project scaffolding for ai-service and whisper-api"
```

---

### Task 2: Errors and text formatting (`errors.py`, `formats.py`)

**Files:**
- Create: `ai_service/errors.py`, `ai_service/formats.py`
- Test: `tests/ai_service/test_formats.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ai_service.errors.InfrastructureError(Exception)` — retry forever with backoff
  - `ai_service.errors.PermanentJobError(Exception)` — counts toward `MAX_RETRIES`
  - `Segment(id: int, start: float, end: float, text: str)` — frozen dataclass
  - `format_timecode(seconds: float) -> str` — `[HH:MM:SS]`
  - `to_full_text(segments: list[Segment]) -> str` — one `[HH:MM:SS] text` line per segment
  - `to_plain_text(segments: list[Segment]) -> str` — space-joined text for the LLM

- [ ] **Step 1: Write `ai_service/errors.py`** (no test needed — declarations only)

```python
"""Error taxonomy for the job pipeline (spec §3.5)."""


class InfrastructureError(Exception):
    """A dependency is unavailable (connect/timeout/5xx; any non-200 from BPM).

    Retried indefinitely with capped exponential backoff; never counted
    toward a job's attempts.
    """


class PermanentJobError(Exception):
    """This job's input is bad (missing object, corrupt audio, 4xx for it).

    Counted toward attempts; job becomes `failed` after MAX_RETRIES.
    """
```

- [ ] **Step 2: Write the failing tests**

`tests/ai_service/test_formats.py`:

```python
from ai_service.formats import Segment, format_timecode, to_full_text, to_plain_text


def test_format_timecode():
    assert format_timecode(0.0) == "[00:00:00]"
    assert format_timecode(4.9) == "[00:00:04]"      # truncated, not rounded
    assert format_timecode(3661.5) == "[01:01:01]"


def test_to_full_text():
    segments = [
        Segment(id=0, start=0.0, end=4.2, text=" Добрый день, компания Аэроклуб."),
        Segment(id=1, start=4.2, end=9.87, text=" Здравствуйте, я по поводу брони."),
    ]
    assert to_full_text(segments) == (
        "[00:00:00] Добрый день, компания Аэроклуб.\n"
        "[00:00:04] Здравствуйте, я по поводу брони."
    )


def test_to_full_text_skips_blank_segments_and_empty_list():
    assert to_full_text([]) == ""
    assert to_full_text([Segment(id=0, start=0.0, end=1.0, text="  ")]) == ""


def test_to_plain_text():
    segments = [
        Segment(id=0, start=0.0, end=4.2, text=" первая реплика"),
        Segment(id=1, start=4.2, end=9.87, text=" вторая реплика"),
    ]
    assert to_plain_text(segments) == "первая реплика вторая реплика"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_formats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.formats'`

- [ ] **Step 4: Implement `ai_service/formats.py`**

```python
"""Build FullText (with timecodes) and plain text from segments (spec §3.3)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    id: int
    start: float
    end: float
    text: str


def format_timecode(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"


def to_full_text(segments: list[Segment]) -> str:
    return "\n".join(
        f"{format_timecode(seg.start)} {seg.text.strip()}"
        for seg in segments
        if seg.text.strip()
    )


def to_plain_text(segments: list[Segment]) -> str:
    return " ".join(seg.text.strip() for seg in segments if seg.text.strip())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_formats.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add ai_service/errors.py ai_service/formats.py tests/ai_service/test_formats.py
git commit -m "feat: error taxonomy and FullText formatting"
```

---

### Task 3: Service configuration (`ai_service/config.py`)

**Files:**
- Create: `ai_service/config.py`
- Test: `tests/ai_service/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ServiceConfig` — frozen dataclass, fields: `s3_endpoint_url, s3_access_key, s3_secret_key, whisper_api_url, whisper_model, whisper_timeout_seconds: int, language, summary_enabled: bool, llm_api_url, llm_api_key, llm_model, llm_timeout_seconds: int, summary_prompt, bpm_callback_url, callback_timeout_seconds: int, max_retries: int, retry_backoff_cap_seconds: int, db_path, port: int, log_level`
  - `load_config(env: Mapping[str, str] = os.environ) -> ServiceConfig`
  - `ConfigError(Exception)`
  - `DEFAULT_SUMMARY_PROMPT: str`
  - Test fixture `service_config(**overrides)` in `tests/conftest.py`

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_config.py`:

```python
import pytest

from ai_service.config import DEFAULT_SUMMARY_PROMPT, ConfigError, load_config

REQUIRED = {
    "S3_ENDPOINT_URL": "http://minio:9000",
    "S3_ACCESS_KEY": "ak",
    "S3_SECRET_KEY": "sk",
    "BPM_CALLBACK_URL": "http://bpm/onTranscriptionComplete",
    "LLM_API_URL": "http://vllm:8000/v1",
    "LLM_MODEL": "qwen2.5",
}


def test_defaults_applied():
    cfg = load_config(REQUIRED)
    assert cfg.whisper_api_url == "http://whisper-api:8000/v1"
    assert cfg.whisper_model == "large-v3"
    assert cfg.whisper_timeout_seconds == 600
    assert cfg.language == "ru"
    assert cfg.summary_enabled is True
    assert cfg.llm_api_key == ""
    assert cfg.llm_timeout_seconds == 120
    assert cfg.summary_prompt == DEFAULT_SUMMARY_PROMPT
    assert cfg.callback_timeout_seconds == 30
    assert cfg.max_retries == 3
    assert cfg.retry_backoff_cap_seconds == 300
    assert cfg.db_path == "/data/jobs.db"
    assert cfg.port == 8080
    assert cfg.log_level == "INFO"


def test_missing_required_var_raises():
    env = dict(REQUIRED)
    del env["BPM_CALLBACK_URL"]
    with pytest.raises(ConfigError, match="BPM_CALLBACK_URL"):
        load_config(env)


def test_llm_vars_required_only_when_summary_enabled():
    env = {k: v for k, v in REQUIRED.items() if not k.startswith("LLM_")}
    with pytest.raises(ConfigError, match="LLM_API_URL"):
        load_config(env)
    cfg = load_config({**env, "SUMMARY_ENABLED": "false"})
    assert cfg.summary_enabled is False
    assert cfg.llm_api_url == ""


def test_url_trailing_slashes_stripped():
    cfg = load_config({**REQUIRED, "WHISPER_API_URL": "http://w:8000/v1/", "LLM_API_URL": "http://l:8000/v1/"})
    assert cfg.whisper_api_url == "http://w:8000/v1"
    assert cfg.llm_api_url == "http://l:8000/v1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.config'`

- [ ] **Step 3: Implement `ai_service/config.py`**

```python
"""ai-service configuration from environment variables (spec §3.4)."""
import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_SUMMARY_PROMPT = (
    "Составь краткое содержание телефонного разговора на русском языке: "
    "основная тема, договорённости, следующие шаги. "
    "Отвечай только текстом краткого содержания."
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class ServiceConfig:
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    whisper_api_url: str
    whisper_model: str
    whisper_timeout_seconds: int
    language: str
    summary_enabled: bool
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: int
    summary_prompt: str
    bpm_callback_url: str
    callback_timeout_seconds: int
    max_retries: int
    retry_backoff_cap_seconds: int
    db_path: str
    port: int
    log_level: str


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def load_config(env: Mapping[str, str] = os.environ) -> ServiceConfig:
    summary_enabled = env.get("SUMMARY_ENABLED", "true").strip().lower() in ("1", "true", "yes")
    if summary_enabled:
        llm_api_url = _require(env, "LLM_API_URL").rstrip("/")
        llm_model = _require(env, "LLM_MODEL")
    else:
        llm_api_url = env.get("LLM_API_URL", "").rstrip("/")
        llm_model = env.get("LLM_MODEL", "")
    return ServiceConfig(
        s3_endpoint_url=_require(env, "S3_ENDPOINT_URL"),
        s3_access_key=_require(env, "S3_ACCESS_KEY"),
        s3_secret_key=_require(env, "S3_SECRET_KEY"),
        whisper_api_url=env.get("WHISPER_API_URL", "http://whisper-api:8000/v1").rstrip("/"),
        whisper_model=env.get("WHISPER_MODEL", "large-v3"),
        whisper_timeout_seconds=int(env.get("WHISPER_TIMEOUT_SECONDS", "600")),
        language=env.get("LANGUAGE", "ru"),
        summary_enabled=summary_enabled,
        llm_api_url=llm_api_url,
        llm_api_key=env.get("LLM_API_KEY", ""),
        llm_model=llm_model,
        llm_timeout_seconds=int(env.get("LLM_TIMEOUT_SECONDS", "120")),
        summary_prompt=env.get("SUMMARY_PROMPT", DEFAULT_SUMMARY_PROMPT),
        bpm_callback_url=_require(env, "BPM_CALLBACK_URL"),
        callback_timeout_seconds=int(env.get("CALLBACK_TIMEOUT_SECONDS", "30")),
        max_retries=int(env.get("MAX_RETRIES", "3")),
        retry_backoff_cap_seconds=int(env.get("RETRY_BACKOFF_CAP_SECONDS", "300")),
        db_path=env.get("DB_PATH", "/data/jobs.db"),
        port=int(env.get("PORT", "8080")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Add the shared config fixture**

`tests/conftest.py`:

```python
import pytest

from ai_service.config import ServiceConfig


@pytest.fixture
def service_config(tmp_path):
    def make(**overrides):
        base = dict(
            s3_endpoint_url="http://localhost:9000",
            s3_access_key="test",
            s3_secret_key="test",
            whisper_api_url="http://whisper-api:8000/v1",
            whisper_model="large-v3",
            whisper_timeout_seconds=5,
            language="ru",
            summary_enabled=True,
            llm_api_url="http://llm:8000/v1",
            llm_api_key="",
            llm_model="test-model",
            llm_timeout_seconds=5,
            summary_prompt="Составь краткое содержание.",
            bpm_callback_url="http://bpm/onTranscriptionComplete",
            callback_timeout_seconds=5,
            max_retries=3,
            retry_backoff_cap_seconds=300,
            db_path=str(tmp_path / "jobs.db"),
            port=8080,
            log_level="INFO",
        )
        base.update(overrides)
        return ServiceConfig(**base)

    return make
```

- [ ] **Step 6: Commit**

```bash
git add ai_service/config.py tests/ai_service/test_config.py tests/conftest.py
git commit -m "feat: ai-service config with SUMMARY_ENABLED-aware validation"
```

---

### Task 4: SQLite job store (`ai_service/db.py`)

**Files:**
- Create: `ai_service/db.py`
- Test: `tests/ai_service/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Job(call_record_id: str, call_record_url: str, status: str, attempts: int, error: str | None, full_text: str | None, summary: str | None, created_at: str, updated_at: str)` — frozen dataclass
  - `JobStore(db_path: str)` with methods:
    - `enqueue(call_record_id: str, call_record_url: str) -> Job` — idempotent; re-queues `failed`
    - `get(call_record_id: str) -> Job | None`
    - `next_pending() -> Job | None` — oldest with status `queued` or `processing`
    - `list_delivering() -> list[Job]` — oldest first
    - `set_status(call_record_id: str, status: str) -> None`
    - `set_result(call_record_id: str, full_text: str, summary: str) -> None` — stores texts, status → `delivering`
    - `increment_attempts(call_record_id: str, error: str) -> int` — returns new attempts value
    - `mark_failed(call_record_id: str, error: str) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.db'`

- [ ] **Step 3: Implement `ai_service/db.py`**

```python
"""SQLite-backed durable job queue (spec §3.2)."""
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    call_record_id  TEXT PRIMARY KEY,
    call_record_url TEXT NOT NULL,
    status          TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    full_text       TEXT,
    summary         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


@dataclass(frozen=True)
class Job:
    call_record_id: str
    call_record_url: str
    status: str
    attempts: int
    error: str | None
    full_text: str | None
    summary: str | None
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class JobStore:
    """Thread-safe: shared by the FastAPI thread and the worker thread."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def _row_to_job(self, row) -> Job:
        return Job(**{key: row[key] for key in row.keys()})

    def _fetch(self, call_record_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE call_record_id = ?", (call_record_id,)
        ).fetchone()
        return self._row_to_job(row) if row else None

    def _update(self, call_record_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self._conn.execute(
            f"UPDATE jobs SET {assignments} WHERE call_record_id = ?",
            (*fields.values(), call_record_id),
        )
        self._conn.commit()

    def enqueue(self, call_record_id: str, call_record_url: str) -> Job:
        with self._lock:
            existing = self._fetch(call_record_id)
            if existing is None:
                now = _now()
                self._conn.execute(
                    "INSERT INTO jobs (call_record_id, call_record_url, status, attempts,"
                    " created_at, updated_at) VALUES (?, ?, 'queued', 0, ?, ?)",
                    (call_record_id, call_record_url, now, now),
                )
                self._conn.commit()
            elif existing.status == "failed":
                self._update(
                    call_record_id,
                    call_record_url=call_record_url,
                    status="queued",
                    attempts=0,
                    error=None,
                )
            return self._fetch(call_record_id)

    def get(self, call_record_id: str) -> Job | None:
        with self._lock:
            return self._fetch(call_record_id)

    def next_pending(self) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'processing')"
                " ORDER BY created_at, call_record_id LIMIT 1"
            ).fetchone()
            return self._row_to_job(row) if row else None

    def list_delivering(self) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = 'delivering'"
                " ORDER BY created_at, call_record_id"
            ).fetchall()
            return [self._row_to_job(row) for row in rows]

    def set_status(self, call_record_id: str, status: str) -> None:
        with self._lock:
            self._update(call_record_id, status=status)

    def set_result(self, call_record_id: str, full_text: str, summary: str) -> None:
        with self._lock:
            self._update(
                call_record_id, full_text=full_text, summary=summary, status="delivering"
            )

    def increment_attempts(self, call_record_id: str, error: str) -> int:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET attempts = attempts + 1, error = ?, updated_at = ?"
                " WHERE call_record_id = ?",
                (error, _now(), call_record_id),
            )
            self._conn.commit()
            return self._fetch(call_record_id).attempts

    def mark_failed(self, call_record_id: str, error: str) -> None:
        with self._lock:
            self._update(call_record_id, status="failed", error=error)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_db.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add ai_service/db.py tests/ai_service/test_db.py
git commit -m "feat: durable SQLite job store with idempotent enqueue"
```

---

### Task 5: S3 download (`ai_service/s3io.py`)

**Files:**
- Create: `ai_service/s3io.py`
- Test: `tests/ai_service/test_s3io.py`

**Interfaces:**
- Consumes: `ServiceConfig` (Task 3), `InfrastructureError`/`PermanentJobError` (Task 2).
- Produces:
  - `parse_call_record_url(url: str) -> tuple[str, str]` — `(bucket, key)`; raises `ValueError` on bad input
  - `make_client(cfg: ServiceConfig)` — boto3 S3 client with `endpoint_url`
  - `download(client, bucket: str, key: str, dest_path: Path) -> None` — maps errors per spec §3.5

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_s3io.py`:

```python
import boto3
import pytest
from moto import mock_aws

from ai_service.errors import PermanentJobError
from ai_service.s3io import download, parse_call_record_url


def test_parse_s3_scheme():
    assert parse_call_record_url("s3://call-records/2026/07/rec 1.wav") == (
        "call-records",
        "2026/07/rec 1.wav",
    )


def test_parse_path_style_https():
    assert parse_call_record_url("https://minio.example.kz/call-records/2026/rec.wav") == (
        "call-records",
        "2026/rec.wav",
    )


def test_parse_unquotes_percent_encoding():
    assert parse_call_record_url("https://host/bucket/%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D1%8C.wav") == (
        "bucket",
        "запись.wav",
    )


@pytest.mark.parametrize(
    "url",
    ["ftp://x/y.wav", "s3://bucket-only", "https://host/bucket-only", "not-a-url", ""],
)
def test_parse_rejects_bad_urls(url):
    with pytest.raises(ValueError):
        parse_call_record_url(url)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="call-records")
        yield client


def test_download(s3, tmp_path):
    s3.put_object(Bucket="call-records", Key="rec.wav", Body=b"wav-bytes")
    dest = tmp_path / "rec.wav"
    download(s3, "call-records", "rec.wav", dest)
    assert dest.read_bytes() == b"wav-bytes"


def test_download_missing_object_is_permanent_error(s3, tmp_path):
    with pytest.raises(PermanentJobError):
        download(s3, "call-records", "missing.wav", tmp_path / "x.wav")


def test_download_missing_bucket_is_permanent_error(s3, tmp_path):
    with pytest.raises(PermanentJobError):
        download(s3, "no-such-bucket", "rec.wav", tmp_path / "x.wav")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_s3io.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.s3io'`

- [ ] **Step 3: Implement `ai_service/s3io.py`**

```python
"""CallRecordUrl parsing and S3 download (spec §3.1, §3.3)."""
import urllib.parse
from pathlib import Path

import boto3
import botocore.exceptions

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError, PermanentJobError


def parse_call_record_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "s3":
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
    elif parsed.scheme in ("http", "https"):
        # path-style object URL: host is ignored, configured endpoint is used
        parts = parsed.path.lstrip("/").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    else:
        raise ValueError(f"unsupported CallRecordUrl scheme: {url!r}")
    if not bucket or not key:
        raise ValueError(f"CallRecordUrl must contain bucket and key: {url!r}")
    return bucket, urllib.parse.unquote(key)


def make_client(cfg: ServiceConfig):
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
    )


def download(client, bucket: str, key: str, dest_path: Path) -> None:
    try:
        client.download_file(bucket, key, str(dest_path))
    except botocore.exceptions.ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if 400 <= status < 500:
            raise PermanentJobError(f"cannot download s3://{bucket}/{key}: {exc}") from exc
        raise InfrastructureError(f"S3 error for s3://{bucket}/{key}: {exc}") from exc
    except botocore.exceptions.BotoCoreError as exc:
        raise InfrastructureError(f"S3 unreachable: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_s3io.py -v`
Expected: 11 passed (3 parse + 5 parametrized bad-URL cases + 3 download)

- [ ] **Step 5: Commit**

```bash
git add ai_service/s3io.py tests/ai_service/test_s3io.py
git commit -m "feat: CallRecordUrl parsing and S3 download with error mapping"
```

---

### Task 6: Whisper API client (`ai_service/transcribe.py`)

**Files:**
- Create: `ai_service/transcribe.py`
- Test: `tests/ai_service/test_transcribe.py`

**Interfaces:**
- Consumes: `ServiceConfig` (Task 3), `Segment` (Task 2), errors (Task 2).
- Produces:
  - `Transcription(language: str, duration: float, segments: list[Segment])` — frozen dataclass
  - `transcribe_file(cfg: ServiceConfig, wav_path: Path) -> Transcription` — raises `PermanentJobError` (4xx) / `InfrastructureError` (connect/timeout/5xx)

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_transcribe.py`:

```python
import httpx
import pytest
import respx

from ai_service.errors import InfrastructureError, PermanentJobError
from ai_service.transcribe import transcribe_file

URL = "http://whisper-api:8000/v1/audio/transcriptions"

VERBOSE_JSON = {
    "task": "transcribe",
    "language": "ru",
    "duration": 9.87,
    "text": "первая реплика вторая реплика",
    "segments": [
        {"id": 0, "start": 0.0, "end": 4.2, "text": " первая реплика"},
        {"id": 1, "start": 4.2, "end": 9.87, "text": " вторая реплика"},
    ],
}


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "rec.wav"
    path.write_bytes(b"RIFF-fake")
    return path


@respx.mock
def test_success_parses_segments(service_config, wav):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=VERBOSE_JSON))

    result = transcribe_file(service_config(), wav)

    assert result.language == "ru"
    assert result.duration == 9.87
    assert [seg.text for seg in result.segments] == [" первая реплика", " вторая реплика"]
    assert result.segments[1].start == 4.2

    request = route.calls.last.request
    assert b'name="file"' in request.content
    assert b'name="model"' in request.content
    assert b"verbose_json" in request.content
    assert b'name="language"' in request.content


@respx.mock
def test_4xx_is_permanent(service_config, wav):
    respx.post(URL).mock(return_value=httpx.Response(400, json={"detail": "bad audio"}))
    with pytest.raises(PermanentJobError):
        transcribe_file(service_config(), wav)


@respx.mock
def test_5xx_is_infrastructure(service_config, wav):
    respx.post(URL).mock(return_value=httpx.Response(503, json={"detail": "loading"}))
    with pytest.raises(InfrastructureError):
        transcribe_file(service_config(), wav)


@respx.mock
def test_timeout_is_infrastructure(service_config, wav):
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(InfrastructureError):
        transcribe_file(service_config(), wav)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_transcribe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.transcribe'`

- [ ] **Step 3: Implement `ai_service/transcribe.py`**

```python
"""Client for whisper-api's OpenAI-compatible transcription endpoint (spec §4.3)."""
from dataclasses import dataclass
from pathlib import Path

import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError, PermanentJobError
from ai_service.formats import Segment


@dataclass(frozen=True)
class Transcription:
    language: str
    duration: float
    segments: list[Segment]


def transcribe_file(cfg: ServiceConfig, wav_path: Path) -> Transcription:
    data = {"model": cfg.whisper_model, "response_format": "verbose_json"}
    if cfg.language:
        data["language"] = cfg.language
    try:
        with wav_path.open("rb") as fh:
            response = httpx.post(
                f"{cfg.whisper_api_url}/audio/transcriptions",
                files={"file": (wav_path.name, fh, "audio/wav")},
                data=data,
                timeout=cfg.whisper_timeout_seconds,
            )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"whisper-api request failed: {exc}") from exc

    if response.status_code >= 500:
        raise InfrastructureError(f"whisper-api returned {response.status_code}")
    if response.status_code >= 400:
        raise PermanentJobError(
            f"whisper-api returned {response.status_code}: {response.text[:500]}"
        )

    payload = response.json()
    segments = [
        Segment(id=i, start=float(seg["start"]), end=float(seg["end"]), text=seg["text"])
        for i, seg in enumerate(payload.get("segments", []))
    ]
    return Transcription(
        language=payload.get("language", cfg.language),
        duration=float(payload.get("duration", 0.0)),
        segments=segments,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_transcribe.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add ai_service/transcribe.py tests/ai_service/test_transcribe.py
git commit -m "feat: whisper-api client with error classification"
```

---

### Task 7: LLM summarization (`ai_service/summarize.py`)

**Files:**
- Create: `ai_service/summarize.py`
- Test: `tests/ai_service/test_summarize.py`

**Interfaces:**
- Consumes: `ServiceConfig` (Task 3), errors (Task 2).
- Produces:
  - `summarize(cfg: ServiceConfig, transcript_text: str) -> str` — returns `""` when `summary_enabled=False` or transcript is blank; raises `PermanentJobError` (4xx) / `InfrastructureError` (connect/timeout/5xx)

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_summarize.py`:

```python
import httpx
import pytest
import respx

from ai_service.errors import InfrastructureError, PermanentJobError
from ai_service.summarize import summarize

URL = "http://llm:8000/v1/chat/completions"

CHAT_RESPONSE = {
    "choices": [{"message": {"role": "assistant", "content": " Клиент уточнил бронь.\n"}}]
}


@respx.mock
def test_summarize_calls_llm(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))

    result = summarize(service_config(), "клиент звонил по поводу брони")

    assert result == "Клиент уточнил бронь."
    import json

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.2
    assert body["messages"][0] == {"role": "system", "content": "Составь краткое содержание."}
    assert body["messages"][1] == {"role": "user", "content": "клиент звонил по поводу брони"}


@respx.mock
def test_api_key_sent_when_configured(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    summarize(service_config(llm_api_key="secret"), "текст")
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"


@respx.mock
def test_disabled_returns_empty_without_calling_llm(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=CHAT_RESPONSE))
    assert summarize(service_config(summary_enabled=False), "текст") == ""
    assert not route.called


def test_blank_transcript_returns_empty(service_config):
    assert summarize(service_config(), "   ") == ""


@respx.mock
def test_4xx_is_permanent(service_config):
    respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "context length"}))
    with pytest.raises(PermanentJobError):
        summarize(service_config(), "текст")


@respx.mock
def test_5xx_and_timeout_are_infrastructure(service_config):
    respx.post(URL).mock(return_value=httpx.Response(502))
    with pytest.raises(InfrastructureError):
        summarize(service_config(), "текст")
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(InfrastructureError):
        summarize(service_config(), "текст")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_summarize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.summarize'`

- [ ] **Step 3: Implement `ai_service/summarize.py`**

```python
"""Summary generation via an OpenAI-compatible chat endpoint (spec §3.3 step 5)."""
import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError, PermanentJobError


def summarize(cfg: ServiceConfig, transcript_text: str) -> str:
    if not cfg.summary_enabled or not transcript_text.strip():
        return ""

    headers = {}
    if cfg.llm_api_key:
        headers["Authorization"] = f"Bearer {cfg.llm_api_key}"
    payload = {
        "model": cfg.llm_model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": cfg.summary_prompt},
            {"role": "user", "content": transcript_text},
        ],
    }
    try:
        response = httpx.post(
            f"{cfg.llm_api_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=cfg.llm_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"LLM request failed: {exc}") from exc

    if response.status_code >= 500:
        raise InfrastructureError(f"LLM returned {response.status_code}")
    if response.status_code >= 400:
        raise PermanentJobError(f"LLM returned {response.status_code}: {response.text[:500]}")

    return response.json()["choices"][0]["message"]["content"].strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_summarize.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add ai_service/summarize.py tests/ai_service/test_summarize.py
git commit -m "feat: LLM summarization with enable toggle"
```

---

### Task 8: BPM callback delivery (`ai_service/callback.py`)

**Files:**
- Create: `ai_service/callback.py`
- Test: `tests/ai_service/test_callback.py`

**Interfaces:**
- Consumes: `ServiceConfig` (Task 3), `InfrastructureError` (Task 2).
- Produces:
  - `deliver(cfg: ServiceConfig, call_record_id: str, summary: str, full_text: str) -> None` — returns on BPM `200`; raises `InfrastructureError` on **any** other outcome (per the diagram, callback retries until 200 — even on BPM 4xx)

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_callback.py`:

```python
import json

import httpx
import pytest
import respx

from ai_service.callback import deliver
from ai_service.errors import InfrastructureError

URL = "http://bpm/onTranscriptionComplete"


@respx.mock
def test_deliver_posts_pascal_case_payload(service_config):
    route = respx.post(URL).mock(return_value=httpx.Response(200))

    deliver(service_config(), "id-1", "суть", "[00:00:00] привет")

    body = json.loads(route.calls.last.request.content)
    assert body == {
        "CallRecordId": "id-1",
        "Summary": "суть",
        "FullText": "[00:00:00] привет",
    }


@respx.mock
@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_non_200_raises_infrastructure_error(service_config, status):
    respx.post(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(InfrastructureError):
        deliver(service_config(), "id-1", "s", "t")


@respx.mock
def test_timeout_raises_infrastructure_error(service_config):
    respx.post(URL).mock(side_effect=httpx.ConnectTimeout("boom"))
    with pytest.raises(InfrastructureError):
        deliver(service_config(), "id-1", "s", "t")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_callback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.callback'`

- [ ] **Step 3: Implement `ai_service/callback.py`**

```python
"""Deliver results to BPM's onTranscriptionComplete endpoint (spec §3.3 step 7)."""
import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError


def deliver(cfg: ServiceConfig, call_record_id: str, summary: str, full_text: str) -> None:
    payload = {"CallRecordId": call_record_id, "Summary": summary, "FullText": full_text}
    try:
        response = httpx.post(
            cfg.bpm_callback_url, json=payload, timeout=cfg.callback_timeout_seconds
        )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"BPM callback failed: {exc}") from exc
    if response.status_code != 200:
        # the diagram retries the callback until 200 — any non-200 keeps the job delivering
        raise InfrastructureError(f"BPM callback returned {response.status_code}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_callback.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add ai_service/callback.py tests/ai_service/test_callback.py
git commit -m "feat: BPM callback delivery retried until 200"
```

---

### Task 9: HTTP API (`ai_service/app.py`)

**Files:**
- Create: `ai_service/app.py`
- Test: `tests/ai_service/test_app.py`

**Interfaces:**
- Consumes: `ServiceConfig` (Task 3), `JobStore` (Task 4), `parse_call_record_url` (Task 5).
- Produces:
  - `create_app(cfg: ServiceConfig, store: JobStore) -> FastAPI` with routes `POST /requestTranscription`, `GET /jobs/{call_record_id}`, `GET /healthz`

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_app.py`:

```python
import pytest
from fastapi.testclient import TestClient

from ai_service.app import create_app
from ai_service.db import JobStore


@pytest.fixture
def client_and_store(service_config, tmp_path):
    store = JobStore(str(tmp_path / "api.db"))
    app = create_app(service_config(), store)
    return TestClient(app), store


def test_request_transcription_accepts_and_enqueues(client_and_store):
    client, store = client_and_store
    response = client.post(
        "/requestTranscription",
        json={"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.wav"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "CallRecordId": "id-1"}
    assert store.get("id-1").status == "queued"


def test_request_transcription_is_idempotent(client_and_store):
    client, store = client_and_store
    body = {"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.wav"}
    assert client.post("/requestTranscription", json=body).status_code == 200
    store.set_status("id-1", "processing")
    assert client.post("/requestTranscription", json=body).status_code == 200
    assert store.get("id-1").status == "processing"  # untouched


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"CallRecordId": "id-1"},
        {"CallRecordUrl": "s3://bucket/rec.wav"},
        {"CallRecordId": "", "CallRecordUrl": "s3://bucket/rec.wav"},
        {"CallRecordId": "id-1", "CallRecordUrl": "ftp://x/y.wav"},
    ],
)
def test_request_transcription_400_on_invalid(client_and_store, body):
    client, _ = client_and_store
    assert client.post("/requestTranscription", json=body).status_code == 400


def test_request_transcription_400_on_non_json(client_and_store):
    client, _ = client_and_store
    response = client.post(
        "/requestTranscription", content=b"not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400


def test_job_status_endpoint(client_and_store):
    client, store = client_and_store
    client.post(
        "/requestTranscription",
        json={"CallRecordId": "id-1", "CallRecordUrl": "s3://bucket/rec.wav"},
    )
    response = client.get("/jobs/id-1")
    assert response.status_code == 200
    data = response.json()
    assert data["CallRecordId"] == "id-1"
    assert data["status"] == "queued"
    assert data["attempts"] == 0
    assert data["error"] is None
    assert client.get("/jobs/nope").status_code == 404


def test_healthz(client_and_store):
    client, _ = client_and_store
    assert client.get("/healthz").json() == {"status": "ok"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.app'`

- [ ] **Step 3: Implement `ai_service/app.py`**

```python
"""HTTP API for BPM integration (spec §3.1)."""
import logging

from fastapi import FastAPI, HTTPException, Request

from ai_service.config import ServiceConfig
from ai_service.db import JobStore
from ai_service.s3io import parse_call_record_url

logger = logging.getLogger(__name__)


def create_app(cfg: ServiceConfig, store: JobStore) -> FastAPI:
    app = FastAPI(title="ai-service")

    @app.post("/requestTranscription")
    async def request_transcription(request: Request):
        # manual parsing so validation errors are 400 (spec), not FastAPI's 422
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be a JSON object")
        call_record_id = str(body.get("CallRecordId") or "").strip()
        call_record_url = str(body.get("CallRecordUrl") or "").strip()
        if not call_record_id or not call_record_url:
            raise HTTPException(
                status_code=400, detail="CallRecordId and CallRecordUrl are required"
            )
        try:
            parse_call_record_url(call_record_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        job = store.enqueue(call_record_id, call_record_url)
        logger.info("accepted %s (status=%s)", call_record_id, job.status)
        return {"status": "accepted", "CallRecordId": call_record_id}

    @app.get("/jobs/{call_record_id}")
    def job_status(call_record_id: str):
        job = store.get(call_record_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return {
            "CallRecordId": job.call_record_id,
            "status": job.status,
            "attempts": job.attempts,
            "error": job.error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_app.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add ai_service/app.py tests/ai_service/test_app.py
git commit -m "feat: ai-service HTTP API with idempotent requestTranscription"
```

---

### Task 10: Worker loop and entrypoint (`ai_service/worker.py`, `ai_service/__main__.py`)

**Files:**
- Create: `ai_service/worker.py`, `ai_service/__main__.py`
- Test: `tests/ai_service/test_worker.py`

**Interfaces:**
- Consumes: everything from Tasks 2–9.
- Produces:
  - `Backoff(cap: float, base: float = 5.0)` with `next() -> float`, `reset() -> None` (5, 10, 20, … capped)
  - `Worker(cfg: ServiceConfig, store: JobStore, s3_client, poll_interval: float = 2.0)` with `run_forever() -> None`, `run_once() -> bool`, `stop() -> None`
  - `python -m ai_service` entrypoint (uvicorn app + worker thread)

- [ ] **Step 1: Write the failing tests**

`tests/ai_service/test_worker.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/ai_service/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ai_service.worker'`

- [ ] **Step 3: Implement `ai_service/worker.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/ai_service/test_worker.py -v`
Expected: 7 passed

- [ ] **Step 5: Implement `ai_service/__main__.py`**

```python
import logging
import threading

import uvicorn

from ai_service.app import create_app
from ai_service.config import load_config
from ai_service.db import JobStore
from ai_service.s3io import make_client
from ai_service.worker import Worker


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "starting ai-service: whisper=%s summary=%s llm=%s callback=%s db=%s",
        cfg.whisper_api_url, cfg.summary_enabled, cfg.llm_api_url or "-",
        cfg.bpm_callback_url, cfg.db_path,
    )
    store = JobStore(cfg.db_path)
    worker = Worker(cfg, store, make_client(cfg))
    threading.Thread(target=worker.run_forever, name="worker", daemon=True).start()
    app = create_app(cfg, store)
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Smoke-check the entrypoint fails cleanly without config**

Run: `.venv/bin/python -m ai_service; echo "exit=$?"`
Expected: traceback ending in `ConfigError: missing required environment variable: S3_ENDPOINT_URL`, non-zero exit.

- [ ] **Step 7: Run the whole ai_service suite**

Run: `.venv/bin/pytest tests/ai_service -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add ai_service/worker.py ai_service/__main__.py tests/ai_service/test_worker.py
git commit -m "feat: worker loop with backoff, retries and entrypoint"
```

---

### Task 11: whisper-api configuration (`whisper_api/config.py`)

**Files:**
- Create: `whisper_api/config.py`
- Test: `tests/whisper_api/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ApiConfig(model: str, device: str, compute_type: str, api_key: str, port: int, log_level: str)` — frozen dataclass
  - `load_config(env: Mapping[str, str] = os.environ) -> ApiConfig` — `COMPUTE_TYPE` empty → `float16` (cuda) / `int8` (cpu)

- [ ] **Step 1: Write the failing tests**

`tests/whisper_api/test_config.py`:

```python
from whisper_api.config import load_config


def test_defaults():
    cfg = load_config({})
    assert cfg.model == "large-v3"
    assert cfg.device == "cuda"
    assert cfg.compute_type == "float16"  # auto for cuda
    assert cfg.api_key == ""
    assert cfg.port == 8000
    assert cfg.log_level == "INFO"


def test_compute_type_auto_for_cpu():
    assert load_config({"DEVICE": "cpu"}).compute_type == "int8"


def test_explicit_compute_type_wins():
    cfg = load_config({"DEVICE": "cpu", "COMPUTE_TYPE": "float32"})
    assert cfg.compute_type == "float32"


def test_overrides():
    cfg = load_config({"WHISPER_MODEL": "tiny", "DEVICE": "cpu", "PORT": "9001"})
    assert (cfg.model, cfg.device, cfg.port) == ("tiny", "cpu", 9001)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/whisper_api/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'whisper_api.config'`

- [ ] **Step 3: Implement `whisper_api/config.py`**

```python
"""whisper-api configuration (spec §4.2), incl. COMPUTE_TYPE auto-resolution."""
import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiConfig:
    model: str
    device: str
    compute_type: str
    api_key: str
    port: int
    log_level: str


def load_config(env: Mapping[str, str] = os.environ) -> ApiConfig:
    device = env.get("DEVICE", "cuda").strip() or "cuda"
    compute_type = env.get("COMPUTE_TYPE", "").strip()
    if not compute_type:
        compute_type = "float16" if device == "cuda" else "int8"
    return ApiConfig(
        model=env.get("WHISPER_MODEL", "large-v3"),
        device=device,
        compute_type=compute_type,
        api_key=env.get("API_KEY", ""),
        port=int(env.get("PORT", "8000")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/whisper_api/test_config.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add whisper_api/config.py tests/whisper_api/test_config.py
git commit -m "feat: whisper-api config with cpu/gpu compute-type auto"
```

---

### Task 12: Transcription engine (`whisper_api/engine.py`)

**Files:**
- Create: `whisper_api/engine.py`, `tests/wavgen.py`
- Test: `tests/whisper_api/test_engine.py` (marked `slow`)

**Interfaces:**
- Consumes: nothing (faster-whisper imported lazily inside `Engine.__init__`, so the module imports without it).
- Produces:
  - `EngineResult(language: str, duration: float, segments: list[dict], text: str)` — frozen dataclass; segment dicts are `{"id": int, "start": float, "end": float, "text": str}`
  - `Engine(model_name: str, device: str, compute_type: str)` with `transcribe(audio_path: str, language: str | None) -> EngineResult` (model access serialized with `threading.Lock`)
  - `tests/wavgen.py: write_test_wav(path, seconds=1.0, rate=16000)`

- [ ] **Step 1: Write the WAV generator helper**

`tests/wavgen.py`:

```python
"""Generate a small deterministic WAV file for tests (no binary fixtures in repo)."""
import math
import struct
import wave


def write_test_wav(path, seconds: float = 1.0, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        wav.writeframes(bytes(frames))
```

- [ ] **Step 2: Write the failing test**

`tests/whisper_api/test_engine.py`:

```python
import pytest

from tests.wavgen import write_test_wav


@pytest.mark.slow
def test_engine_transcribes_real_wav(tmp_path):
    pytest.importorskip("faster_whisper")
    from whisper_api.engine import Engine

    wav = tmp_path / "tone.wav"
    write_test_wav(wav, seconds=2.0)

    engine = Engine("tiny", device="cpu", compute_type="int8")
    result = engine.transcribe(str(wav), language="ru")

    assert result.duration == pytest.approx(2.0, abs=0.5)
    assert isinstance(result.segments, list)
    for seg in result.segments:
        assert set(seg) == {"id", "start", "end", "text"}
    assert result.text == "".join(s["text"] for s in result.segments).strip()
```

- [ ] **Step 3: Verify wiring**

Run: `.venv/bin/pytest tests/whisper_api/test_engine.py -v`
Expected: no tests ran (deselected by `-m 'not slow'`).
Run: `.venv/bin/pytest tests/whisper_api/test_engine.py -v -m slow`
Expected: SKIPPED (`faster_whisper` not installed) — or FAIL with `No module named 'whisper_api.engine'` if it is. Either confirms wiring.

- [ ] **Step 4: Implement `whisper_api/engine.py`**

```python
"""faster-whisper wrapper: load once, serialize model access (spec §4.1)."""
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineResult:
    language: str
    duration: float
    segments: list[dict]
    text: str


class Engine:
    def __init__(self, model_name: str, device: str, compute_type: str):
        from faster_whisper import WhisperModel  # lazy: heavy import, needs the `api` extra

        self.model_name = model_name
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self._lock = threading.Lock()

    def transcribe(self, audio_path: str, language: str | None) -> EngineResult:
        with self._lock:  # one model instance: serialize concurrent requests
            segments_iter, info = self._model.transcribe(audio_path, language=language or None)
            segments = [
                {"id": i, "start": float(seg.start), "end": float(seg.end), "text": seg.text}
                for i, seg in enumerate(segments_iter)
            ]
        return EngineResult(
            language=info.language,
            duration=float(info.duration),
            segments=segments,
            text="".join(seg["text"] for seg in segments).strip(),
        )
```

- [ ] **Step 5: Verify module imports without faster-whisper**

Run: `.venv/bin/python -c "from whisper_api.engine import EngineResult; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add whisper_api/engine.py tests/whisper_api/test_engine.py tests/wavgen.py
git commit -m "feat: faster-whisper engine wrapper with serialized access"
```

---

### Task 13: whisper-api FastAPI app and entrypoint (`whisper_api/app.py`, `whisper_api/__main__.py`)

**Files:**
- Create: `whisper_api/app.py`, `whisper_api/__main__.py`
- Test: `tests/whisper_api/test_app.py`

**Interfaces:**
- Consumes: `ApiConfig` (Task 11), `EngineResult` (Task 12).
- Produces:
  - `create_app(cfg: ApiConfig, engine_factory: Callable | None) -> FastAPI` — engine loads in a background thread at startup; `app.state.engine` starts `None` (tests set it directly with `engine_factory=None`)
  - Routes: `GET /healthz` (503 until loaded), `POST /v1/audio/transcriptions` (contract spec §4.3)
  - `python -m whisper_api` entrypoint

- [ ] **Step 1: Write the failing tests**

`tests/whisper_api/test_app.py`:

```python
import io

import pytest
from fastapi.testclient import TestClient

from whisper_api.app import create_app
from whisper_api.config import ApiConfig
from whisper_api.engine import EngineResult


class FakeEngine:
    model_name = "fake"

    def transcribe(self, audio_path: str, language: str | None) -> EngineResult:
        return EngineResult(
            language=language or "ru",
            duration=2.5,
            segments=[{"id": 0, "start": 0.0, "end": 2.5, "text": " привет мир"}],
            text="привет мир",
        )


def make_config(**overrides) -> ApiConfig:
    base = dict(model="fake", device="cpu", compute_type="int8", api_key="", port=8000, log_level="INFO")
    base.update(overrides)
    return ApiConfig(**base)


@pytest.fixture
def client():
    app = create_app(make_config(), engine_factory=None)
    app.state.engine = FakeEngine()
    return TestClient(app)


def post_wav(client, **form):
    data = {"model": "fake", "response_format": "verbose_json", **form}
    return client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", io.BytesIO(b"RIFF-fake"), "audio/wav")},
        data=data,
    )


def test_healthz_503_while_loading():
    app = create_app(make_config(), engine_factory=None)
    assert TestClient(app).get("/healthz").status_code == 503


def test_healthz_ok_when_loaded(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "fake"}


def test_transcription_contract(client):
    response = post_wav(client, language="ru")
    assert response.status_code == 200
    assert response.json() == {
        "task": "transcribe",
        "language": "ru",
        "duration": 2.5,
        "text": "привет мир",
        "segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": " привет мир"}],
    }


def test_transcription_503_while_loading():
    app = create_app(make_config(), engine_factory=None)
    assert post_wav(TestClient(app)).status_code == 503


def test_empty_file_400(client):
    response = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", io.BytesIO(b""), "audio/wav")},
        data={"model": "fake", "response_format": "verbose_json"},
    )
    assert response.status_code == 400


def test_unsupported_response_format_422(client):
    assert post_wav(client, response_format="srt").status_code == 422


def test_auth_enforced_when_key_set():
    app = create_app(make_config(api_key="secret"), engine_factory=None)
    app.state.engine = FakeEngine()
    client = TestClient(app)
    assert post_wav(client).status_code == 401
    ok = client.post(
        "/v1/audio/transcriptions",
        files={"file": ("a.wav", io.BytesIO(b"RIFF"), "audio/wav")},
        data={"model": "fake", "response_format": "verbose_json"},
        headers={"Authorization": "Bearer secret"},
    )
    assert ok.status_code == 200


def test_engine_failure_500(client):
    class BrokenEngine:
        def transcribe(self, audio_path, language):
            raise RuntimeError("boom")

    client.app.state.engine = BrokenEngine()
    assert post_wav(client).status_code == 500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/whisper_api/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'whisper_api.app'`

- [ ] **Step 3: Implement `whisper_api/app.py`**

```python
"""FastAPI app: OpenAI-compatible transcription endpoint (spec §4)."""
import logging
import tempfile
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from whisper_api.config import ApiConfig

logger = logging.getLogger(__name__)


def create_app(cfg: ApiConfig, engine_factory: Callable | None) -> FastAPI:
    def _load_engine(app: FastAPI) -> None:
        logger.info("loading model %s on %s (%s)", cfg.model, cfg.device, cfg.compute_type)
        try:
            app.state.engine = engine_factory()
            logger.info("model loaded")
        except Exception:
            logger.exception("model loading failed")
            app.state.load_error = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if engine_factory is not None:
            threading.Thread(target=_load_engine, args=(app,), daemon=True).start()
        yield

    app = FastAPI(title="whisper-api", lifespan=lifespan)
    app.state.engine = None
    app.state.load_error = False

    def _check_auth(authorization: str | None) -> None:
        if cfg.api_key and authorization != f"Bearer {cfg.api_key}":
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    def _require_engine():
        if app.state.load_error:
            raise HTTPException(status_code=500, detail="model failed to load")
        if app.state.engine is None:
            raise HTTPException(status_code=503, detail="model loading")
        return app.state.engine

    @app.get("/healthz")
    def healthz():
        _require_engine()
        return {"status": "ok", "model": cfg.model}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: str = Form(""),
        language: str = Form(""),
        response_format: str = Form("verbose_json"),
        authorization: str | None = Header(None),
    ):
        _check_auth(authorization)
        if response_format != "verbose_json":
            raise HTTPException(
                status_code=422, detail="only response_format=verbose_json is supported"
            )
        engine = _require_engine()
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="empty or missing audio file")
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            tmp.write(contents)
            tmp.flush()
            try:
                result = await run_in_threadpool(engine.transcribe, tmp.name, language or None)
            except Exception as exc:
                logger.exception("transcription failed")
                raise HTTPException(status_code=500, detail=f"transcription failed: {exc}") from exc
        return {
            "task": "transcribe",
            "language": result.language,
            "duration": result.duration,
            "text": result.text,
            "segments": result.segments,
        }

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/whisper_api/test_app.py -v`
Expected: 8 passed

- [ ] **Step 5: Implement `whisper_api/__main__.py`**

```python
import logging

import uvicorn

from whisper_api.app import create_app
from whisper_api.config import load_config


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    def engine_factory():
        from whisper_api.engine import Engine

        return Engine(cfg.model, cfg.device, cfg.compute_type)

    app = create_app(cfg, engine_factory)
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Smoke-check the server boots**

Run:
```bash
DEVICE=cpu .venv/bin/python -m whisper_api &
SERVER_PID=$!
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/healthz
kill $SERVER_PID
```
Expected: `503` or `500` (model can't load — faster-whisper not in the dev venv; routing works). `200` if faster-whisper is installed and a tiny model was configured and finished loading.

- [ ] **Step 7: Commit**

```bash
git add whisper_api/app.py whisper_api/__main__.py tests/whisper_api/test_app.py
git commit -m "feat: whisper-api FastAPI app with health and auth"
```

---

### Task 14: End-to-end integration test

**Files:**
- Create: `tests/test_integration.py`

**Interfaces:**
- Consumes: `create_app`/`JobStore`/`Worker` (Tasks 4, 9, 10), whisper-api `create_app` (Task 13), `EngineResult` (Task 12), `write_test_wav` (Task 12), `service_config` fixture (Task 3).
- Produces: proof the full chain works over real sockets: BPM request → S3 → whisper-api → LLM stub → BPM callback stub.

- [ ] **Step 1: Write the test**

`tests/test_integration.py`:

```python
"""Full-chain integration: request → S3 → whisper-api → LLM stub → BPM stub (spec §7)."""
import socket
import threading
import time

import boto3
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws

from ai_service.app import create_app as create_service_app
from ai_service.db import JobStore
from ai_service.worker import Worker
from tests.wavgen import write_test_wav
from whisper_api.app import create_app as create_whisper_app
from whisper_api.config import ApiConfig
from whisper_api.engine import EngineResult


class FakeEngine:
    model_name = "fake"

    def transcribe(self, audio_path: str, language: str | None) -> EngineResult:
        return EngineResult(
            language=language or "ru",
            duration=2.0,
            segments=[{"id": 0, "start": 0.0, "end": 2.0, "text": " тестовая запись"}],
            text="тестовая запись",
        )


def _serve(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, port
        time.sleep(0.05)
    raise RuntimeError("uvicorn did not start")


def _make_llm_stub():
    app = FastAPI()

    @app.post("/v1/chat/completions")
    def chat():
        return {"choices": [{"message": {"role": "assistant", "content": "Суть звонка."}}]}

    return app


def _make_bpm_stub(received: list):
    app = FastAPI()

    @app.post("/onTranscriptionComplete")
    async def on_complete(payload: dict):
        received.append(payload)
        return {"ok": True}

    return app


def test_full_chain(tmp_path, service_config):
    whisper_app = create_whisper_app(
        ApiConfig(model="fake", device="cpu", compute_type="int8", api_key="", port=0, log_level="INFO"),
        engine_factory=None,
    )
    whisper_app.state.engine = FakeEngine()
    received: list = []
    servers = [_serve(whisper_app), _serve(_make_llm_stub()), _serve(_make_bpm_stub(received))]
    (_, _, whisper_port), (_, _, llm_port), (_, _, bpm_port) = servers
    try:
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="call-records")
            wav = tmp_path / "rec.wav"
            write_test_wav(wav, seconds=2.0)
            s3.put_object(Bucket="call-records", Key="2026/rec.wav", Body=wav.read_bytes())

            cfg = service_config(
                whisper_api_url=f"http://127.0.0.1:{whisper_port}/v1",
                llm_api_url=f"http://127.0.0.1:{llm_port}/v1",
                bpm_callback_url=f"http://127.0.0.1:{bpm_port}/onTranscriptionComplete",
            )
            store = JobStore(cfg.db_path)
            api = TestClient(create_service_app(cfg, store))

            response = api.post(
                "/requestTranscription",
                json={"CallRecordId": "call-1", "CallRecordUrl": "s3://call-records/2026/rec.wav"},
            )
            assert response.status_code == 200

            worker = Worker(cfg, store, s3)
            worker.run_once()  # process
            worker.run_once()  # deliver

            assert api.get("/jobs/call-1").json()["status"] == "done"
            assert received == [
                {
                    "CallRecordId": "call-1",
                    "Summary": "Суть звонка.",
                    "FullText": "[00:00:00] тестовая запись",
                }
            ]
    finally:
        for server, thread, _ in servers:
            server.should_exit = True
            thread.join(timeout=5)
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/test_integration.py -v`
Expected: 1 passed (implementation exists — this task adds the proof; if it fails, fix the revealed bug before continuing).

- [ ] **Step 3: Run the full default suite**

Run: `.venv/bin/pytest -v`
Expected: all tests pass, slow tests deselected.

- [ ] **Step 4: (network permitting) run the slow real-model test**

Run: `.venv/bin/pip install faster-whisper && .venv/bin/pytest -m slow -v`
Expected: real tiny-model engine test passes (first run downloads ~75 MB).

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: full-chain integration over real sockets"
```

---

### Task 15: Docker, compose, env template, README

**Files:**
- Create: `docker/ai-service.Dockerfile`, `docker/whisper-api.Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: both packages, `pyproject.toml` extras (`service`, `api`).
- Produces: `docker compose up` runs both services against configured S3/LLM/BPM endpoints.

- [ ] **Step 1: Write `docker/ai-service.Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY ai_service ./ai_service
COPY whisper_api ./whisper_api
RUN pip install --no-cache-dir .[service]

VOLUME /data
EXPOSE 8080
CMD ["python", "-m", "ai_service"]
```

(`whisper_api` is copied only because `pyproject.toml` lists both packages; the `service` extra installs no ML dependencies.)

- [ ] **Step 2: Write `docker/whisper-api.Dockerfile`**

```dockerfile
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3.11 python3.11-venv \
 && rm -rf /var/lib/apt/lists/*
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python3.11 -m venv /opt/venv

WORKDIR /app
COPY pyproject.toml ./
COPY ai_service ./ai_service
COPY whisper_api ./whisper_api
RUN pip install --no-cache-dir .[api]

ENV HF_HOME=/cache/huggingface
EXPOSE 8000
CMD ["python", "-m", "whisper_api"]
```

(The CUDA base also runs on CPU-only hosts — CUDA libs are unused when `DEVICE=cpu`.)

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  whisper-api:
    build:
      context: .
      dockerfile: docker/whisper-api.Dockerfile
    environment:
      WHISPER_MODEL: ${WHISPER_MODEL:-large-v3}
      DEVICE: ${DEVICE:-cuda}
      COMPUTE_TYPE: ${COMPUTE_TYPE:-}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    volumes:
      - model-cache:/cache/huggingface
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"]
      interval: 15s
      timeout: 5s
      retries: 40
      start_period: 300s   # first start downloads the model
    # Enable for DEVICE=cuda; leave commented on CPU-only hosts.
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]

  ai-service:
    build:
      context: .
      dockerfile: docker/ai-service.Dockerfile
    env_file: .env
    environment:
      WHISPER_API_URL: http://whisper-api:8000/v1
    ports:
      - "${AI_SERVICE_PORT:-8080}:8080"
    volumes:
      - ai-service-data:/data
    depends_on:
      whisper-api:
        condition: service_healthy
    restart: unless-stopped

volumes:
  model-cache:
  ai-service-data:
```

- [ ] **Step 4: Write `.env.example`**

```bash
# --- S3 (required) ---
S3_ENDPOINT_URL=https://minio.example.kz
S3_ACCESS_KEY=changeme
S3_SECRET_KEY=changeme

# --- BPM callback (required) ---
BPM_CALLBACK_URL=http://bpm.example.kz/api/onTranscriptionComplete

# --- Summarization ---
SUMMARY_ENABLED=true
# required when SUMMARY_ENABLED=true:
LLM_API_URL=http://vllm.example.kz:8000/v1
LLM_MODEL=qwen2.5-32b-instruct
#LLM_API_KEY=
#LLM_TIMEOUT_SECONDS=120
#SUMMARY_PROMPT=

# --- Whisper (compose passes these to whisper-api) ---
#WHISPER_MODEL=large-v3
#DEVICE=cuda            # cuda | cpu
#COMPUTE_TYPE=          # empty = auto (float16 for cuda, int8 for cpu)
#WHISPER_TIMEOUT_SECONDS=600
#LANGUAGE=ru

# --- Service tuning (defaults shown) ---
#MAX_RETRIES=3
#RETRY_BACKOFF_CAP_SECONDS=300
#CALLBACK_TIMEOUT_SECONDS=30
#DB_PATH=/data/jobs.db
#AI_SERVICE_PORT=8080
#LOG_LEVEL=INFO
```

- [ ] **Step 5: Write `README.md`**

```markdown
# ai-stt

BPM-driven speech-to-text service. BPMSoft(Omni) pushes a transcription
request; the service downloads the call record from S3-compatible storage,
transcribes it, optionally summarizes it, and posts the result back to BPM.

Two services:

- **ai-service** — FastAPI + durable SQLite job queue.
  `POST /requestTranscription` (`CallRecordId`, `CallRecordUrl`) → 200 accepted;
  result is delivered to `BPM_CALLBACK_URL` as
  `{CallRecordId, Summary, FullText}` (retried until BPM answers 200).
  `GET /jobs/{CallRecordId}` shows job status; `GET /healthz` liveness.
- **whisper-api** — FastAPI + faster-whisper (GPU or CPU, `DEVICE=cuda|cpu`),
  OpenAI-compatible `POST /v1/audio/transcriptions`.

Summaries come from an external OpenAI-compatible LLM (`LLM_API_URL`);
set `SUMMARY_ENABLED=false` to skip summarization (Summary is sent as `""`).

Design spec: `docs/superpowers/specs/2026-07-06-ai-stt-bpm-integration-design.md`.

## Run

    cp .env.example .env   # fill in S3, BPM callback, LLM endpoint
    docker compose up --build

First start downloads the Whisper model into the `model-cache` volume.
A `failed` job (see `GET /jobs/{id}`) is retried by re-POSTing
`/requestTranscription` with the same `CallRecordId`.

## Develop

    python3 -m venv .venv
    .venv/bin/pip install -e .[dev]
    .venv/bin/pytest            # fast suite (no model download)
    .venv/bin/pip install faster-whisper
    .venv/bin/pytest -m slow    # real tiny-model tests
```

- [ ] **Step 6: Verify compose config and ai-service image**

Run: `cp .env.example .env && docker compose config -q && echo OK`
Expected: `OK`
Run: `docker build -f docker/ai-service.Dockerfile -t ai-stt-service . && docker run --rm ai-stt-service python -c "import ai_service, boto3, httpx, fastapi; print('ok')"`
Expected: `ok`. (Skip the whisper-api image build if no network/GPU box available — it is exercised at deploy time.)

- [ ] **Step 7: Commit**

```bash
git add docker docker-compose.yml .env.example README.md
git commit -m "build: dockerfiles, compose stack and docs"
```

---

## Final verification

1. `.venv/bin/pytest -v` — full fast suite green.
2. `.venv/bin/pytest -m slow -v` (with `faster-whisper` installed) — real tiny-model test green.
3. Spec coverage spot-check against `docs/superpowers/specs/2026-07-06-ai-stt-bpm-integration-design.md`: §3.1 API (Task 9), §3.2 job store (Task 4), §3.3 pipeline (Tasks 5–8, 10), §3.4 config (Task 3), §3.5 retries (Tasks 2, 10), §4 whisper-api incl. CPU/GPU (Tasks 11–13), §5 deployment (Task 15), §7 testing (Tasks 2–14).
4. Manual smoke test on the target host: `docker compose up --build`, then
   `curl -X POST http://localhost:8080/requestTranscription -H 'Content-Type: application/json' -d '{"CallRecordId":"test-1","CallRecordUrl":"s3://<bucket>/<key>.wav"}'`,
   watch logs, poll `GET /jobs/test-1` until `done`, confirm BPM (or a stub) received the callback.
```
