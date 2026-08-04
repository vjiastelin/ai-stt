# ai-stt: S3-to-S3 Speech-to-Text Pipeline — Design Spec

**Date:** 2026-07-05
**Status:** Superseded by `2026-07-06-ai-stt-bpm-integration-design.md` (BPM push/callback integration replaced the S3-polling pipeline)

## 1. Purpose

A system that automatically transcribes WAV audio files appearing in an S3-compatible source bucket and publishes transcripts with timecodes (JSON + SRT) to a results bucket. Primary language: Russian. Target volume: tens to a few hundred files/day.

The system consists of **two services** developed in this repo:

1. **`whisper-api`** — a REST service wrapping **faster-whisper** on GPU, exposing an OpenAI-compatible transcription endpoint.
2. **`stt-worker`** — a lightweight polling worker that moves files between S3 and the API and formats the outputs.

## 2. Architecture

```
┌───────── Docker: stt-worker (CPU) ─────────┐      ┌── Docker: whisper-api (GPU) ──┐
│  ┌────────┐  list/diff  ┌───────────────┐  │ HTTP │  ┌─────────┐   ┌───────────┐  │
│  │ Poller ├────────────>│  API client   ├──┼──────┼─>│ FastAPI ├──>│  faster-  │  │
│  └───┬────┘             │  + Uploader   │  │ POST │  └─────────┘   │  whisper  │  │
└──────┼──────────────────└──────┬────────┘──┘      └───────────────│  (CUDA)   │──┘
       v                         v                                   └───────────┘
  s3://<source-bucket>/….wav   s3://<results-bucket>/
  (read-only)                    ├── ….json
                                 └── ….srt
```

- No queue, no database: the results bucket is the state.
- The two services communicate only over HTTP using the OpenAI-compatible contract (§6), so either side can be replaced independently — e.g. pointing the worker at the OpenAI API, or reusing `whisper-api` from other systems.
- S3 access via **boto3** with a configurable `endpoint_url` (works with MinIO and any S3-compatible store). Only the worker talks to S3.
- One file transcribed at a time (single GPU); horizontal scaling is a future concern (worker replicas + key sharding, more API instances).

## 3. Component: `stt-worker`

A single-process Python service, no GPU and no ML dependencies — just **boto3** and an HTTP client (**httpx**).

### 3.1 Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `S3_ENDPOINT_URL` | — (required) | S3-compatible endpoint, e.g. `https://minio.example.kz` |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | — (required) | Credentials |
| `SOURCE_BUCKET` | — (required) | Bucket with incoming WAV files |
| `SOURCE_PREFIX` | `""` | Optional key prefix to watch |
| `RESULTS_BUCKET` | — (required) | Bucket for transcripts (must differ from source or use a distinct prefix) |
| `RESULTS_PREFIX` | `""` | Optional key prefix for outputs |
| `POLL_INTERVAL_SECONDS` | `30` | Sleep between polling cycles |
| `WHISPER_API_URL` | `http://whisper-api:8000/v1` | Base URL of the transcription API |
| `WHISPER_API_KEY` | `""` | Bearer token; empty if the API is unauthenticated |
| `WHISPER_MODEL` | `large-v3` | Model name passed in the API request |
| `WHISPER_TIMEOUT_SECONDS` | `600` | Per-request timeout (long files transcribe slowly) |
| `LANGUAGE` | `ru` | Transcription language sent to the API; empty = auto-detect |
| `MIN_FILE_AGE_SECONDS` | `60` | Skip source objects modified more recently (upload-in-progress guard) |
| `MAX_RETRIES` | `3` | Per-file attempts before writing an error marker |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### 3.2 Processing flow

Each polling cycle:

1. **List** all objects under `SOURCE_BUCKET/SOURCE_PREFIX` with suffix `.wav` (case-insensitive), using paginated `list_objects_v2`.
2. **Map keys**: `path/file.wav` → outputs `RESULTS_PREFIX/path/file.json` and `RESULTS_PREFIX/path/file.srt`.
3. **Skip** a file when:
   - its `.json` output already exists in the results bucket (JSON is uploaded **last**, so its presence means the file is fully processed), or
   - its `.error.json` marker exists (see §3.4), or
   - `LastModified` is newer than `MIN_FILE_AGE_SECONDS` ago.
4. **Process** each remaining file, one at a time:
   1. Download the WAV to a temp directory.
   2. `POST {WHISPER_API_URL}/chat/completions` — multipart form with the WAV file, `model`, `language`, `response_format=verbose_json`; parse segments (start/end/text) from the response.
   3. Build the JSON document and SRT text.
   4. Upload the **SRT first, then the JSON** (JSON acts as the completion marker).
   5. Delete the temp file (always, via `finally`).
5. Sleep `POLL_INTERVAL_SECONDS`, repeat.

The source bucket is never written to — the worker needs only read access there.

### 3.3 Output formats

#### JSON (`…/file.json`)

```json
{
  "source": "s3://audio-in/path/file.wav",
  "language": "ru",
  "duration_seconds": 123.4,
  "model": "large-v3",
  "created_at": "2026-07-05T12:00:00Z",
  "text": "полный текст расшифровки…",
  "segments": [
    {"id": 0, "start": 0.0, "end": 4.2, "text": "первая реплика"},
    {"id": 1, "start": 4.2, "end": 9.87, "text": "вторая реплика"}
  ]
}
```

- `start`/`end` — seconds (float, 2-decimal precision is sufficient).
- `text` (top level) — concatenation of segment texts.
- `created_at` — UTC ISO-8601.

#### SRT (`…/file.srt`)

Standard SubRip built from the same segments:

```
1
00:00:00,000 --> 00:00:04,200
первая реплика

2
00:00:04,200 --> 00:00:09,870
вторая реплика
```

Both files are uploaded with `Content-Type` `application/json` / `text/plain; charset=utf-8` respectively.

### 3.4 Error handling

- **Per-file failures** (corrupt WAV, API `4xx` response for that file, upload error): log with the key and exception, count the attempt in memory, retry on subsequent cycles.
- **Whisper API unavailability** (connection errors, timeouts, `5xx`): treated as infrastructure errors, **not** counted against the file's retry limit — the worker logs, aborts the cycle, and retries after the poll interval.
- **Poison files:** after `MAX_RETRIES` failed attempts, upload `path/file.error.json` to the results bucket:
  ```json
  {"source": "s3://audio-in/path/file.wav", "error": "…", "attempts": 3, "failed_at": "…"}
  ```
  Files with an error marker are skipped until the marker is deleted (manual reprocessing = delete the marker).
- **S3 infrastructure errors** (list/HEAD failures, endpoint down): log and retry next cycle. The process never exits on a bad file or transient error.
- **Reprocessing** a successful file = delete its `.json` from the results bucket.
- In-memory retry counts reset on restart — acceptable: worst case a poison file gets another `MAX_RETRIES` attempts.

## 4. Component: `whisper-api`

A **FastAPI** service wrapping **faster-whisper** (CTranslate2) on CUDA.

### 4.1 Behavior

- Loads the configured model **once at startup** and keeps it resident in GPU memory; the service reports not-ready until the model is loaded.
- Requests are transcribed **sequentially**: a single `asyncio` lock (or single worker thread) serializes access to the GPU; concurrent HTTP requests queue. Transcription runs in a thread pool so the event loop stays responsive for health checks.
- Runs as a single uvicorn worker process (multiple workers would each load the model).
- Stateless: no persistence; audio is processed from the uploaded bytes and discarded.

### 4.2 Configuration (environment variables)

| Variable | Default | Description |
|---|---|---|
| `WHISPER_MODEL` | `large-v3` | faster-whisper model name (downloaded to the cache volume on first start) |
| `DEVICE` | `cuda` | `cuda` or `cpu` |
| `COMPUTE_TYPE` | `float16` | e.g. `float16` (GPU), `int8` (CPU) |
| `TRANSCRIBE_OPTIONS` | `""` (none) | JSON object of faster-whisper `transcribe()` options, merged over the defaults (e.g. `{"beam_size":5,"temperature":0}`); invalid JSON / non-object fails startup |
| `API_KEY` | `""` | If set, requests must send `Authorization: Bearer <key>` |
| `PORT` | `8000` | Listen port |
| `SSL_CERTFILE` | `""` | TLS cert path; set together with `SSL_KEYFILE` to serve HTTPS (else plain HTTP). In Docker, if set but no cert exists at the path, the entrypoint generates a self-signed pair there on startup |
| `SSL_KEYFILE` | `""` | TLS private-key path (paired with `SSL_CERTFILE`) |
| `SSL_KEYFILE_PASSWORD` | `""` | Optional passphrase for an encrypted `SSL_KEYFILE` |
| `DOMAIN` | `llm.example.int` | CN of the self-signed cert the Docker entrypoint generates (only used when generation kicks in) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### 4.3 Error responses

- `400` — missing/unreadable audio file in the request (counts as a per-file failure in the worker).
- `422` — invalid parameters (unknown `response_format`, bad `language`).
- `503` — model still loading (worker treats as infrastructure error and retries later).
- `500` — unexpected transcription failure.

## 5. API contract between the services

`POST {base}/chat/completions` — the transcription endpoint (OpenAI `verbose_json` transcription contract on the chat/completions path):

- **Request:** `multipart/form-data` with fields `file` (the WAV), `model`, `language` (optional), `response_format=verbose_json`.
- **Response `200`** (`verbose_json`):
  ```json
  {
    "task": "transcribe",
    "language": "ru",
    "duration": 123.4,
    "text": "полный текст…",
    "segments": [
      {"id": 0, "start": 0.0, "end": 4.2, "text": "первая реплика"}
    ]
  }
  ```
- `GET /healthz` (server root, outside `/v1`) → `200 {"status": "ok", "model": "large-v3"}` once the model is loaded, `503` before that.

Because the contract is OpenAI-compatible, the worker can be pointed at any other compatible server (speaches, whisper.cpp server, the OpenAI API) by changing `WHISPER_API_URL`, and `whisper-api` can serve other clients.

## 6. Deployment

- **Two Dockerfiles:**
  - `stt-worker`: slim `python:3.11-slim` base, installs `boto3`, `httpx`.
  - `whisper-api`: `nvidia/cuda:12.x-cudnn-runtime-ubuntu22.04` base, Python 3.11+, installs `faster-whisper`, `fastapi`, `uvicorn`, `python-multipart`.
- **docker-compose.yml:** both services on one network. `whisper-api` gets GPU reservation (`deploy.resources.reservations.devices` / `gpus: all`) and a named volume for the model cache (`~/.cache/huggingface`) so the model downloads once. `stt-worker` depends on `whisper-api` (healthcheck-gated) and reads env vars from `.env`.
- Worker startup validates config and waits for the API healthcheck; logs API URL, model, buckets.
- Logging: structured single-line logs to stdout in both services (key, duration of download/API call/upload, realtime factor). Metrics/Prometheus are out of scope for v1.

## 7. Project layout

```
ai-stt/
├── stt_worker/
│   ├── __main__.py      # entrypoint: config → loop
│   ├── config.py        # env parsing/validation
│   ├── s3io.py          # list/head/download/upload helpers (boto3)
│   ├── discovery.py     # diff source vs results, skip rules
│   ├── transcribe.py    # Whisper API client (httpx) → segments
│   ├── formats.py       # segments → JSON doc / SRT text
│   └── worker.py        # per-file pipeline + retry/error markers
├── whisper_api/
│   ├── __main__.py      # uvicorn entrypoint
│   ├── config.py        # env parsing/validation
│   ├── app.py           # FastAPI app: /v1/audio/transcriptions, /health, auth
│   └── engine.py        # faster-whisper wrapper: load once, serialized transcribe
├── tests/
│   ├── worker/
│   └── whisper_api/
├── docker/
│   ├── worker.Dockerfile
│   └── whisper-api.Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## 8. Testing

- **`stt-worker` unit (pytest):** key mapping (`.wav`→`.json`/`.srt`, prefixes, unicode keys), SRT timestamp formatting (incl. hour rollover, rounding), skip logic (exists / error marker / too-new), error-marker content.
- **`stt-worker` S3 layer:** against **moto** (mocked S3) — list pagination, upload ordering (SRT before JSON).
- **`stt-worker` API client:** against **respx** (mocked httpx) — request shape (multipart, `verbose_json`), segment parsing, 4xx vs 5xx/timeout classification.
- **`whisper-api`:** FastAPI `TestClient` with the engine mocked — contract shape, auth, 400/422/503 paths; plus one real-engine test using `WHISPER_MODEL=tiny`, `DEVICE=cpu` on a bundled short WAV sample, so CI needs no GPU.
- **Integration:** end-to-end run of the worker against moto S3 plus the real `whisper-api` app (tiny model, CPU) — asserts both outputs appear in the results bucket and the JSON schema is valid.
