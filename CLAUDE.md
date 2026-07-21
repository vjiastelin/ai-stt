# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
.venv/bin/pip install -e .[dev]        # setup (Python >= 3.11)
.venv/bin/pytest                       # fast suite — `addopts = -m 'not slow'` excludes slow tests
.venv/bin/pytest tests/ai_service/test_worker.py -k retry   # single file / test
.venv/bin/pytest -m slow               # real-model tests; needs `pip install faster-whisper` first
docker compose up --build              # run both services (whisper model downloads on first start)
```

There is no linter or formatter configured.

Docker builds install deps from the committed `uv.lock` with `uv sync --frozen`. **After changing dependencies in `pyproject.toml`, regenerate the lock** or the build fails:

```bash
docker run --rm -v "$PWD":/app -w /app --entrypoint sh \
  python:3.11-slim -c "pip install -q uv && uv lock"
```

The WER accuracy test (`tests/whisper_api/test_wer.py`, `slow`, report-only — no pass/fail gate) should run inside the `whisper-api` container for GPU/env parity; see README "WER accuracy test" for the exact `docker compose run` incantation (tests/ must be volume-mounted because `.dockerignore` excludes them from the image).

## Architecture

Two independently deployable FastAPI services in one repo, wired by docker-compose. Design spec: `docs/superpowers/specs/2026-07-06-ai-stt-bpm-integration-design.md` (code comments cite its § numbers).

**ai_service** (port 8080) — BPM-facing orchestrator. `POST /requestTranscription` enqueues into a SQLite-backed durable queue (`db.py JobStore`, thread-safe, shared by API + worker threads); a single background worker thread (`worker.py`) drives each job through the pipeline: download MP3 from S3 (`s3io.py`) → transcribe via whisper-api's OpenAI-compatible endpoint (`transcribe.py`) → format segments into timecoded FullText (`formats.py`) → optional summary via external OpenAI-compatible LLM (`summarize.py`) → POST result to `BPM_CALLBACK_URL` (`callback.py`).

**whisper_api** (port 8000) — faster-whisper wrapper exposing OpenAI-compatible transcription at `POST /v1/audio/transcriptions` (multipart upload → `verbose_json` with timecoded segments; the `ai_service` pipeline depends on those timecodes). `engine.py` loads the model once and serializes access with a lock; PyAV decode errors are mapped to `InvalidAudioError`. Decode tuning is an open `TRANSCRIBE_OPTIONS` JSON object merged over `DEFAULT_TRANSCRIBE_OPTIONS` and splatted into `model.transcribe(**options)`. Optional HTTPS via `SSL_CERTFILE`/`SSL_KEYFILE` (env-only; plain HTTP when unset). `faster-whisper` is deliberately NOT in the `dev` extra (heavy) — `engine.py` imports it lazily, so fast tests run without it.

Key invariants that span files:

- **Job state machine** (`db.py`): `queued → processing → delivering → done | failed`. Enqueue is idempotent by `CallRecordId`; re-POSTing a `failed` job resets it to `queued`. Callback delivery is at-least-once (`delivering` survives restarts and is retried until BPM answers 200).
- **Error taxonomy** (`ai_service/errors.py`, honored throughout worker/s3io/transcribe/summarize/callback): `InfrastructureError` (dependency down: connect/timeout/5xx) is retried forever with capped exponential backoff and never counted against the job; `PermanentJobError` (bad input: missing object, corrupt audio, 4xx) increments `attempts` and marks the job `failed` after `MAX_RETRIES`. Put new failure modes in the right bucket.
- **API validation returns 400, not FastAPI's default 422** (spec §3.1 — a custom `RequestValidationError` handler in `app.py`). `CallRecordUrl` must be `s3://bucket/key.mp3` or a path-style http(s) URL ending in `.mp3`; anything else is rejected at the API and again defensively in the worker.
- **Config is env-var-driven** via `load_config(env)` in each service's `config.py` (frozen dataclasses; required vars raise `ConfigError`). whisper-api auto-resolves `COMPUTE_TYPE`: `float16` on cuda, `int8` on cpu.

## Tests

Fast tests stub all I/O: `moto` for S3, `respx` for whisper/LLM/BPM HTTP, and a fake engine for whisper-api. `tests/conftest.py` provides the `service_config(**overrides)` fixture — use it instead of constructing `ServiceConfig` by hand. `tests/test_integration.py` runs the full chain (request → moto S3 → real whisper-api app with stubbed engine → LLM/BPM stubs) over real uvicorn sockets.

User-facing texts (summary prompt, transcripts) are Russian; `LANGUAGE` defaults to `ru`.
