# Load-test: peak-hour injection + drain measurement

Picks the busiest hour of a day of real 3cx call recordings (S3 `LastModified` =
arrival time), replays it time-compressed against the full pipeline
(**replay → ai_service → external whisper → BPM mock (200)**), and measures:

1. **Drain**: how long ai_service takes to resolve the backlog — drain time,
   throughput (jobs/min), per-job end-to-end latency p50/p90/max (from the
   server's own `created_at`/`updated_at`).
2. **Slow-down of further transcriptions**: probe requests fired every
   `--probe-interval` seconds during the drain, each compared to a baseline
   measured on an idle queue, plus a recovery probe after the drain.

For `2026.07.06` the peak hour is **08:00 UTC, 100 calls (52 MB)**. ai_service's
worker is single-threaded FIFO, so a burst delays everything queued behind it —
the probe table shows by how much, and the recovery probe shows the service
returns to baseline once drained.

## Files

- `bpm_mock.py` — BPM callback stand-in. Every POST → `200 {"status":"ok"}`. `GET /stats` for counters.
- `replay_workload.py` — peak-hour selection, compressed injection, drain/probe measurement, report.

## Run

```bash
# 1) BPM mock (answers 200 so jobs reach `done`)
.venv/bin/python tools/loadtest/bpm_mock.py --port 9099

# 2) ai_service — point it at the external whisper and the mock.
#    In docker-compose.yml WHISPER_API_URL/WHISPER_API_KEY already target the external box,
#    and extra_hosts maps host.docker.internal -> the docker host, so the container
#    can reach the mock running on the host:
BPM_CALLBACK_URL=http://host.docker.internal:9099/callback \
SUMMARY_ENABLED=false \
docker compose up --build
#    (S3_ENDPOINT_URL=https://s3.yandexcloud.net + S3 keys for bucket 3cx-recordings
#     must be in .env so ai_service can download the mp3s.)

# 3) quick run first (20 calls), then the full peak hour (~100 real transcriptions)
.venv/bin/python tools/loadtest/replay_workload.py --target http://localhost:8080 --limit 20
.venv/bin/python tools/loadtest/replay_workload.py --target http://localhost:8080
```

The report ends with a probe table and a verdict:

```
probes during drain (new transcriptions arriving behind the backlog):
  sent at   queue depth   latency     vs baseline
  t+   30s       74         312.1s    8.4x
  ...
recovery probe (after drain): 41.2s = 1.1x baseline

verdict:
  during drain new requests were delayed up to 8.4x baseline
  service recovered to normal latency after drain: yes (1.1x)
```

## Useful flags

- `--dry-run` — print the hourly histogram + chosen peak hour, send nothing.
- `--limit N` — cap burst size (full peak hour ≈ 100 real GPU transcriptions; start small).
- `--window N` — seconds to compress the peak hour into (default 60).
- `--hour H` — replay a specific UTC hour instead of the busiest.
- `--probe-interval` (30 s), `--baseline-probes` (2), `--poll-interval` (3 s), `--max-wait` (3600 s).
- `--force` — run even if ai_service's queue is not idle (skews measurements).
- `--prefix 3cx/YYYY.MM.DD/`, `--from-json objects.json`, `--run-id`, `--concurrency`, `--timeout`.

`CallRecordId`s are run-scoped (`peak-<prefix>-<filename>`, probes `...-probe-N`) so
re-runs don't collide with the queue's idempotency-by-`CallRecordId`. The probe payload
is the median-size mp3 of the burst, so probe latency is comparable across probes.

Watch progress: ai_service `GET /jobs`, `GET /jobs/{id}`; mock `GET /stats`.
