# Load-test: replay a day of 3cx calls in 5 minutes

Compresses one day of real call recordings (S3 `LastModified` = arrival time) into
a short window, preserving the relative arrival distribution, and drives the full
pipeline: **replay → ai_service → external whisper → BPM mock (200)**.

For `2026.07.06`: 1198 recordings, 619 MB referenced, plateau 06:00–15:00 UTC.
At 288× (24h → 5 min) peak is **~8.7 req/s**.

## Files

- `bpm_mock.py` — BPM callback stand-in. Every POST → `200 {"status":"ok"}`. `GET /stats` for counters.
- `replay_workload.py` — fetches the S3 listing, builds the compressed schedule, fires `POST /requestTranscription`.

## Run

```bash
# 1) BPM mock (answers 200 so jobs reach `done`)
.venv/bin/python tools/loadtest/bpm_mock.py --port 9099

# 2) ai_service — point it at the external whisper and the mock.
#    In docker-compose.yml WHISPER_API_URL/WHISPER_API_KEY already target the external box.
#    Set BPM_CALLBACK_URL to the mock and (optionally) disable summary to skip the LLM:
BPM_CALLBACK_URL=http://<host>:9099/callback \
SUMMARY_ENABLED=false \
docker compose up --build
#    (S3_ENDPOINT_URL=https://s3.yandexcloud.net + S3 keys for bucket 3cx-recordings
#     must be in .env so ai_service can download the mp3s.)

# 3) Replay the day in 5 minutes
.venv/bin/python tools/loadtest/replay_workload.py \
    --target http://localhost:8080 \
    --prefix 3cx/2026.07.06/ \
    --duration 300
```

## Useful flags

- `--dry-run` — print the schedule + arrival histogram, send nothing.
- `--duration N` — replay window in seconds (compression = 86400/N; 300 = 5 min, 288×).
- `--prefix 3cx/YYYY.MM.DD/` — replay any other day.
- `--from-json objects.json` — reuse a saved listing instead of hitting S3.
- `--concurrency`, `--timeout`, `--run-id`.

`CallRecordId` is `run-<prefix>-<filename>` so re-runs don't collide with the queue's
idempotency-by-`CallRecordId`. `CallRecordUrl` is `s3://3cx-recordings/<key>`, resolved
via ai_service's configured `S3_ENDPOINT_URL`.

Watch progress: ai_service `GET /jobs`, `GET /jobs/{id}`; mock `GET /stats`.
```
