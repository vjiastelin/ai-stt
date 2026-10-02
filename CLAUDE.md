# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
.venv/bin/pip install -e .[dev]        # setup (Python >= 3.11)
.venv/bin/pytest                       # fast suite — `addopts = -m 'not slow'` excludes slow tests
.venv/bin/pytest tests/ai_service/test_worker.py -k retry   # single file / test
.venv/bin/pytest -m slow               # real-model tests; needs `pip install faster-whisper` first
docker compose up --build              # ai-service only, against external WHISPER_API_URL
docker compose --profile local-whisper up --build   # + local whisper-api (model downloads on first start)
```

There is no linter or formatter configured.

Docker builds install deps from the committed `uv.lock` with `uv sync --frozen`. **After changing dependencies in `pyproject.toml`, regenerate the lock** or the build fails:

```bash
docker run --rm -v "$PWD":/app -w /app --entrypoint sh \
  python:3.11-slim -c "pip install -q uv && uv lock"
```

The WER accuracy test (`tests/whisper_api/test_wer.py`, `slow`, report-only — no pass/fail gate) should run inside the `whisper-api` container for GPU/env parity; see README "WER accuracy test" for the exact `docker compose --profile local-whisper run` incantation (tests/ must be volume-mounted because `.dockerignore` excludes them from the image).

## Architecture

Two independently deployable FastAPI services in one repo, wired by docker-compose. Design spec: `docs/superpowers/specs/2026-07-06-ai-stt-bpm-integration-design.md` (code comments cite its § numbers).

**ai_service** (port 8080) — BPM-facing orchestrator. `POST /requestTranscription` enqueues into a SQLite-backed durable queue (`db.py JobStore`, thread-safe, shared by API + worker threads); a single background worker thread (`worker.py`) drives each job through the pipeline: download MP3 from S3 (`s3io.py`) → transcribe via whisper-api's OpenAI-compatible endpoint (`transcribe.py`) → format segments into timecoded FullText (`formats.py`) → optional summary via external OpenAI-compatible LLM (`summarize.py`) → deliver to every configured channel: POST to `BPM_CALLBACK_URL` (`callback.py`) and/or SMTP email (`mailer.py`). A channel is enabled by setting its env vars (at least one required); `jobs.delivered_to` records channels that already accepted, so retries resend only to the failing ones. Email recipients are chosen in `mailer.recipients_for`: file-name globs (`EMAIL_ROUTES`, then `[[file_route]]`) → client routes from the TOML routing file (`routing.py`: domain from the summary's «Почта:» line, else company from «Компания:») → file `default` / `EMAIL_TO`; the committed table `config/email-routing.toml` is covered by `tests/ai_service/test_routing.py`.

Jobs have two entry points: BPM's `POST /requestTranscription`, and the optional bucket scanner (`scanner.py`, own thread, enabled by `S3_SCAN_URL`) that lists a bucket/prefix every `S3_SCAN_INTERVAL_SECONDS` and enqueues each new `.mp3`/`.wav` once. Incrementality lives in the `s3_objects` table (`JobStore.enqueue_discovered` inserts the seen key and the job atomically), deliberately NOT in `enqueue()` — that re-queues `failed` jobs, which a rescan must never do. Scanned jobs use `uuid5(s3://bucket/key)` as `CallRecordId`.

**whisper_api** (port 8000) — faster-whisper wrapper exposing OpenAI-compatible transcription at `POST /v1/audio/transcriptions` (multipart upload → `verbose_json` with timecoded segments; the `ai_service` pipeline depends on those timecodes). `download.py` fetches the model first (hub snapshot in a watched thread: retries, deadline → `DownloadError` → process exit, progress via `status` into the `/health` 503 detail), then `engine.py` loads it from the local path once and serializes access with a lock; PyAV decode errors are mapped to `InvalidAudioError`. Decode tuning is an open `TRANSCRIBE_OPTIONS` JSON object merged over `DEFAULT_TRANSCRIBE_OPTIONS` and splatted into `model.transcribe(**options)`. Optional HTTPS via `SSL_CERTFILE`/`SSL_KEYFILE` (env-only; plain HTTP when unset). `faster-whisper` is deliberately NOT in the `dev` extra (heavy) — `engine.py` imports it lazily, so fast tests run without it.

Key invariants that span files:

- **Job state machine** (`db.py`): `queued → processing → delivering → done | failed`. Enqueue is idempotent by `CallRecordId`; re-POSTing a `failed` job resets it to `queued`. Delivery is at-least-once per channel (`delivering` survives restarts and is retried until every enabled channel — BPM 200 / SMTP accept — has taken the result).
- **Error taxonomy** (`ai_service/errors.py`, honored throughout worker/s3io/transcribe/summarize/callback): `InfrastructureError` (dependency down or busy: connect/timeout/5xx/429) is retried forever with capped exponential backoff and never counted against the job; `PermanentJobError` (bad input: missing object, corrupt audio, 4xx other than 429) increments `attempts` and marks the job `failed` after `MAX_RETRIES`. Put new failure modes in the right bucket.
- **API validation returns 400, not FastAPI's default 422** (spec §3.1 — a custom `RequestValidationError` handler in `app.py`). `CallRecordUrl` must be `s3://bucket/key.mp3|.wav` or a path-style http(s) URL ending in `.mp3`/`.wav` (`s3io.AUDIO_CONTENT_TYPES`); anything else is rejected at the API and again defensively in the worker. The worker keeps the key's extension on the temp file and upload, since whisper-api picks the demuxer by file name.
- **Config is env-var-driven** via `load_config(env)` in each service's `config.py` (frozen dataclasses; required vars raise `ConfigError`). whisper-api auto-resolves `COMPUTE_TYPE`: `float16` on cuda, `int8` on cpu.

## Tests

Fast tests stub all I/O: `moto` for S3, `respx` for whisper/LLM/BPM HTTP, and a fake engine for whisper-api. `tests/conftest.py` provides the `service_config(**overrides)` fixture — use it instead of constructing `ServiceConfig` by hand. `tests/test_integration.py` runs the full chain (request → moto S3 → real whisper-api app with stubbed engine → LLM/BPM stubs) over real uvicorn sockets.

User-facing texts (summary prompt, transcripts) are Russian; `LANGUAGE` defaults to `ru`.
