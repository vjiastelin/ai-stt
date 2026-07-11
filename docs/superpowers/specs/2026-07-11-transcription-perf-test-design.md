# Transcription performance test — design

Date: 2026-07-11

## Purpose

Give a repeatable, report-only measurement of how fast the production
whisper-api model transcribes a real speech clip. This complements the WER test
(`tests/whisper_api/test_wer.py`), which measures *accuracy* on the same
fixtures; this test measures *speed*.

Like the WER test, it is `slow` (skipped by default), report-only (no pass/fail
gate on timing), and skips cleanly when no fixture is present.

## Metric

**Real-time factor (RTF) = transcription wall time / audio duration.** RTF < 1
means faster-than-real-time. Wall time is measured with `time.perf_counter()`
around the `engine.transcribe()` call only; audio duration comes from
`EngineResult.duration` (reported by faster-whisper).

Model construction (`Engine(...)`, which loads the model) is timed separately
and reported once as a one-off "model load" number, so it does not pollute the
steady-state RTF.

## Layer

Direct `whisper_api.engine.Engine` call, mirroring `test_wer.py`. This isolates
pure model speed with no HTTP/serialization overhead. Runs inside the
`whisper-api` container for GPU/env parity (same as the WER test).

## Structure

New file `tests/whisper_api/test_perf.py`:

- `pytest.importorskip("faster_whisper")` and skip when
  `tests/fixtures/wer/` has no `*.mp3`.
- Module constants `MODEL` / `DEVICE` / `COMPUTE_TYPE` / `LANGUAGE`, matching
  `test_wer.py` so a local run can be pointed at a smaller/faster model.
- Reuse the fixture directory (`tests/fixtures/wer/`). The `.txt` sibling is not
  required here — any `*.mp3` is a valid perf fixture.
- Construct one `Engine`, timing construction once (reported as model-load).
- For each fixture: time only `engine.transcribe(str(mp3), language=LANGUAGE)`,
  compute RTF, `print` per-fixture line, `logger.info` the same.
- Print a summary: file count, mean RTF, total audio seconds, total wall
  seconds.
- The only assertion is a sanity check that transcription returned non-empty
  text (`result.text.strip()`). Never assert on timing.

## Output (run with `-s`)

```
model load: 12.3s
wer_test.mp3: duration=48.2s wall=31.5s RTF=0.65
[PERF] 1 file(s)  mean RTF=0.65  total_audio=48.2s  total_wall=31.5s
```

## Documentation

Add a one-line pointer in `tests/fixtures/wer/README.md` noting that the perf
test consumes the same `*.mp3` fixtures (no `.txt` needed) and reports RTF.

## Out of scope (YAGNI)

- No latency percentiles / multi-iteration warm-up runs (one pass per fixture).
- No memory profiling.
- No HTTP-endpoint timing.
- No timing gate/threshold.
