# ai-stt

BPM-driven speech-to-text service. BPMSoft(Omni) pushes a transcription
request; the service downloads the call record (MP3, ~5 min / ~4.5 MB typical,
~850 calls/day) from S3-compatible storage, transcribes it, optionally
summarizes it, and posts the result back to BPM. `CallRecordUrl` must point
to an `.mp3` object — anything else is rejected with 400.

Two services:

- **ai-service** — FastAPI + durable SQLite job queue.
  `POST /requestTranscription` (`CallRecordId`, `CallRecordUrl`) → 200 accepted;
  result is delivered to `BPM_CALLBACK_URL` as
  `{CallRecordId, Summary, FullText}` (retried until BPM answers 200).
  Inspection endpoints: `GET /jobs` (list, newest first, `?status=` filter +
  `limit`/`offset`), `GET /jobs/{CallRecordId}` (status), and
  `GET /jobs/{CallRecordId}/result` (the `Summary` and `FullText`).
  `GET /healthz` liveness.
- **whisper-api** — FastAPI + faster-whisper (GPU or CPU, `DEVICE=cuda|cpu`),
  OpenAI-compatible `POST /v1/audio/transcriptions`.

Summaries come from an external OpenAI-compatible LLM (`LLM_API_URL`);
set `SUMMARY_ENABLED=false` to skip summarization (Summary is sent as `""`).

Interactive API docs (Swagger UI) with request/response schemas:
`http://localhost:8080/docs` (ai-service) and `http://<whisper-api-host>:8000/docs`.

Design spec: `docs/superpowers/specs/2026-07-06-ai-stt-bpm-integration-design.md`.

## Run

    cp .env.example .env   # fill in S3, BPM callback, LLM endpoint
    docker compose up --build

First start downloads the Whisper model into the `model-cache` volume.
On CPU-only hosts, set `DEVICE=cpu` in `.env` (the default is `cuda`).
A `failed` job (see `GET /jobs/{id}`) is retried by re-POSTing
`/requestTranscription` with the same `CallRecordId`.

BPM's callback endpoint (`BPM_CALLBACK_URL`) should be idempotent: delivery
is at-least-once, so the same `{CallRecordId, Summary, FullText}` payload
may be posted more than once (e.g. after a retry that BPM actually received
but did not acknowledge with `200`).

## Develop

    python3 -m venv .venv
    .venv/bin/pip install -e .[dev]
    .venv/bin/pytest            # fast suite (no model download)
    .venv/bin/pip install faster-whisper
    .venv/bin/pytest -m slow    # real tiny-model tests
