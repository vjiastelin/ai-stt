# Remote WER/CER test against an external whisper-api

Date: 2026-07-13

## Goal

Measure Word/Char Error Rate of a whisper-api instance running **elsewhere**
(e.g. a GPU host), reached over its HTTP URL, without loading the model
in-process. This complements — does not replace — the existing in-process test
that loads `whisper_api.engine.Engine` directly.

Both tests reuse the same `tests/fixtures/wer/` `<name>.mp3` + `<name>.txt`
pairs and the same `tests/wer.py` helpers (`wer`, `cer`, `alignment`,
`normalize`), so results are directly comparable.

## Location

A second `@pytest.mark.slow` function, `test_wer_against_fixtures_remote`, added
to `tests/whisper_api/test_wer.py`. The existing `test_wer_against_fixtures`
(local in-process model) is left untouched.

## Configuration (env-var driven)

| Env var | Default | Role |
|---|---|---|
| `WHISPER_API_URL` | *(unset)* | Base URL including `/v1` (e.g. `https://gpu-host/v1`). Unset → `pytest.skip`. |
| `WHISPER_API_KEY` | `""` | Optional bearer token. |
| `WHISPER_MODEL` | `large-v3` | Sent as the `model` field. |
| `LANGUAGE` | `ru` | Transcription language. |
| `WHISPER_TIMEOUT_SECONDS` | `600` | Per-request timeout (the `service_config` fixture default of 5s is too short for a real transcription). |

These reuse the same names the ai_service uses in `ai_service/config.py`, so a
deployment's existing env carries over.

## Client

Reuses the production HTTP path rather than re-implementing it:

1. Build a `ServiceConfig` via the `service_config()` fixture, overriding
   `whisper_api_url`, `whisper_api_key`, `whisper_model`, `language`, and
   `whisper_timeout_seconds` from the env vars above.
2. For each fixture pair, call `ai_service.transcribe.transcribe_file(cfg, mp3)`.
3. Reconstruct the hypothesis as `" ".join(s.text for s in result.segments)`.
4. Feed reference + hypothesis into `wer()` / `cer()`; print the per-file line,
   `alignment()`, and the mean summary — identical reporting to the in-process
   test.

No `faster-whisper` import is needed (no local engine is instantiated).

## Skip logic

Two gates, checked in order:

1. `WHISPER_API_URL` unset → `pytest.skip`.
2. `_fixture_pairs()` empty → `pytest.skip` (reuses the existing helper).

## Error handling

`transcribe_file` raises `InfrastructureError` (dependency down / 5xx / timeout)
or `PermanentJobError` (4xx / malformed input). These propagate as test errors —
the correct signal that the external service is unreachable or rejecting
requests. No custom handling.

## Reporting, not gating

Report-only (prints WER/CER, run with `-s`), no threshold assertion — matching
the existing test's behavior.

## README

Add a short paragraph under the existing "WER accuracy test" section showing the
remote invocation:

```
WHISPER_API_URL=https://gpu-host/v1 WHISPER_API_KEY=... \
  .venv/bin/pytest tests/whisper_api/test_wer.py -k remote -m slow -s
```

## Out of scope (YAGNI)

- No pass/fail threshold gate.
- No new fixture format.
- No changes to the in-process test or to `transcribe.py`.
