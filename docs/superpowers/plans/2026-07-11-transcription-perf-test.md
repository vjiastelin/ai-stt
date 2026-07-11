# Transcription Performance Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `slow`, report-only pytest that measures the whisper-api model's real-time factor (RTF) on the committed MP3 fixture(s).

**Architecture:** A new `tests/whisper_api/test_perf.py` mirrors `test_wer.py`: it constructs one `whisper_api.engine.Engine` (timed once as model-load), then times only `engine.transcribe()` per fixture and prints RTF = wall / audio-duration. No timing gate; skips cleanly when `faster_whisper` or fixtures are absent.

**Tech Stack:** Python ≥ 3.11, pytest, faster-whisper (lazy/optional), `time.perf_counter`.

## Global Constraints

- Python >= 3.11.
- `faster_whisper` is NOT in the `dev` extra — the test MUST `pytest.importorskip("faster_whisper")` and import `whisper_api.engine` only after that.
- Test MUST be marked `@pytest.mark.slow` (excluded from the default suite by `addopts = -m 'not slow'`).
- Report-only: the ONLY assertion is a non-empty-text sanity check. Never assert on timing.
- User-facing language default is `ru`.
- No linter/formatter configured; match the style of `test_wer.py`.

---

### Task 1: Performance test file

**Files:**
- Create: `tests/whisper_api/test_perf.py`
- Reference (do not modify): `tests/whisper_api/test_wer.py`, `whisper_api/engine.py`

**Interfaces:**
- Consumes: `whisper_api.engine.Engine(model_name, device, compute_type)` and `Engine.transcribe(audio_path: str, language: str | None) -> EngineResult`, where `EngineResult` has `.text: str` and `.duration: float` (audio seconds).
- Produces: nothing consumed by later tasks (leaf test file).

- [ ] **Step 1: Write the failing test file**

Create `tests/whisper_api/test_perf.py` with exactly this content:

```python
"""Transcription speed (real-time factor) of the production model on fixtures.

Report-only companion to ``test_wer.py`` (which measures accuracy). This test
is ``slow`` (skipped by default) and additionally skips when ``faster_whisper``
is not installed or no ``*.mp3`` fixture is present. It *reports* RTF
(prints it; run with ``-s``) and never gates on timing.

RTF = transcription wall time / audio duration; RTF < 1 is faster than
real-time. Model load is timed separately as a one-off number so it does not
pollute the steady-state RTF.
"""
import logging
import time
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wer"

# Edit these for a faster/cheaper local run (e.g. "tiny"/"int8").
MODEL = "large-v3"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
LANGUAGE = "ru"

logger = logging.getLogger(__name__)


def _fixture_mp3s() -> list[Path]:
    if not FIXTURES.is_dir():
        return []
    return sorted(FIXTURES.glob("*.mp3"))


@pytest.mark.slow
def test_transcription_rtf_against_fixtures():
    pytest.importorskip("faster_whisper")
    from whisper_api.engine import Engine

    mp3s = _fixture_mp3s()
    if not mp3s:
        pytest.skip(f"no *.mp3 fixtures in {FIXTURES}")

    load_start = time.perf_counter()
    engine = Engine(MODEL, device=DEVICE, compute_type=COMPUTE_TYPE)
    load_wall = time.perf_counter() - load_start
    print(f"model load: {load_wall:.1f}s")
    logger.info("model load: %.1fs", load_wall)

    total_audio = 0.0
    total_wall = 0.0
    rtfs = []
    for mp3 in mp3s:
        start = time.perf_counter()
        result = engine.transcribe(str(mp3), language=LANGUAGE)
        wall = time.perf_counter() - start

        assert isinstance(result.text, str) and result.text.strip()

        rtf = wall / result.duration if result.duration else float("nan")
        total_audio += result.duration
        total_wall += wall
        rtfs.append(rtf)

        line = f"{mp3.name}: duration={result.duration:.1f}s wall={wall:.1f}s RTF={rtf:.2f}"
        print(line)
        logger.info(line)

    summary = (
        f"[PERF] {len(mp3s)} file(s)  "
        f"mean RTF={sum(rtfs) / len(rtfs):.2f}  "
        f"total_audio={total_audio:.1f}s  "
        f"total_wall={total_wall:.1f}s"
    )
    print(summary)
    logger.info(summary)
```

- [ ] **Step 2: Verify the fast suite still collects and skips the slow test**

Run: `.venv/bin/pytest tests/whisper_api/test_perf.py -v`
Expected: `1 deselected` (the `slow` mark is excluded by the default `-m 'not slow'`), no collection errors.

- [ ] **Step 3: Verify the test runs and reports (requires faster-whisper + fixture)**

Run (inside the `whisper-api` container for env parity, per README):
`.venv/bin/pytest tests/whisper_api/test_perf.py -m slow -s`
Expected without `faster_whisper` installed: SKIPPED ("could not import 'faster_whisper'").
Expected with `faster_whisper` installed and `wer_test.mp3` present: PASS, printing a `model load:` line, a `wer_test.mp3: duration=… wall=… RTF=…` line, and a `[PERF] 1 file(s) …` summary.

- [ ] **Step 4: Commit**

```bash
git add tests/whisper_api/test_perf.py
git commit -m "test: report transcription RTF on real mp3 fixtures"
```

---

### Task 2: Document the perf fixture usage

**Files:**
- Modify: `tests/fixtures/wer/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing.

- [ ] **Step 1: Add a pointer to the perf test**

Append this section to the end of `tests/fixtures/wer/README.md`:

```markdown
## Performance test

`tests/whisper_api/test_perf.py::test_transcription_rtf_against_fixtures`
reuses the same `*.mp3` files (no `.txt` needed) and reports the real-time
factor (RTF = transcription wall time / audio duration; RTF < 1 is faster than
real-time). Like the WER test it is `slow` and report-only. Run with `-s` to
see the per-file RTF and the `[PERF]` summary.
```

- [ ] **Step 2: Commit**

```bash
git add tests/fixtures/wer/README.md
git commit -m "docs: note the RTF performance test in fixtures README"
```

---

## Self-Review

**Spec coverage:**
- RTF metric, wall via `perf_counter`, duration via `EngineResult.duration` → Task 1 ✓
- Model load timed separately once → Task 1 (`load_wall`) ✓
- Direct `Engine` layer, mirrors `test_wer.py` → Task 1 ✓
- `slow`, report-only, `importorskip`, skip when no fixture → Task 1 ✓
- Module constants matching `test_wer.py` → Task 1 ✓
- Reuse `tests/fixtures/wer/`, `.txt` not required → Task 1 (`_fixture_mp3s`) ✓
- Per-fixture + summary output → Task 1 ✓
- Sanity assertion only, no timing gate → Task 1 ✓
- README pointer → Task 2 ✓
- Out-of-scope items (percentiles, memory, HTTP, threshold) → not implemented ✓

**Placeholder scan:** none — full test source and full README block are inline.

**Type consistency:** `Engine(model_name, device, compute_type)` and `Engine.transcribe(str, language)` returning `EngineResult(.text, .duration)` match `whisper_api/engine.py`. ✓
