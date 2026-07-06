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
