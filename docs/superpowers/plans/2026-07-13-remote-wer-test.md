# Remote WER/CER Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a report-only WER/CER test that measures an external whisper-api instance (e.g. GPU host) over its HTTP URL, alongside the existing in-process test.

**Architecture:** A second `@pytest.mark.slow` function in `tests/whisper_api/test_wer.py`, env-var driven, that builds a `ServiceConfig` via the `service_config()` fixture and drives the production client `ai_service.transcribe.transcribe_file`. Reuses the existing fixture pairs, `_fixture_pairs()` helper, and `tests/wer.py` metrics. The existing in-process `test_wer_against_fixtures` is left untouched.

**Tech Stack:** pytest, httpx (via `transcribe_file`), jiwer (via `tests/wer.py`).

## Global Constraints

- Python >= 3.11.
- Report-only: print WER/CER (visible with `-s`), no threshold assertion.
- The test is `slow` — excluded by the default `addopts = -m 'not slow'`; must be run with `-m slow`.
- User-facing fixture transcripts are Russian; `LANGUAGE` defaults to `ru`.
- No changes to `ai_service/transcribe.py` or the in-process test.

---

### Task 1: Add remote WER/CER test + README note

**Files:**
- Modify: `tests/whisper_api/test_wer.py` (add `import os`; append new test function)
- Modify: `README.md` (WER accuracy test section, after line 84)

**Interfaces:**
- Consumes: `service_config(**overrides)` fixture (`tests/conftest.py`) → `ServiceConfig`; `ai_service.transcribe.transcribe_file(cfg: ServiceConfig, audio_path: Path) -> Transcription` where `Transcription.segments: list[Segment]` and `Segment.text: str`; module-level `FIXTURES`, `MODEL`, `LANGUAGE`, `logger`, `_fixture_pairs()`, and `wer`/`cer`/`alignment` (already imported).
- Produces: `test_wer_against_fixtures_remote` (no downstream consumers).

- [ ] **Step 1: Add the `os` import**

In `tests/whisper_api/test_wer.py`, change the top imports from:

```python
import logging
from pathlib import Path
```

to:

```python
import logging
import os
from pathlib import Path
```

- [ ] **Step 2: Append the remote test function**

Add to the end of `tests/whisper_api/test_wer.py`:

```python
@pytest.mark.slow
def test_wer_against_fixtures_remote(service_config):
    """WER/CER against an external whisper-api reached over its HTTP URL.

    Env-var driven; hits the OpenAI-compatible endpoint of a whisper-api
    running elsewhere (e.g. a GPU host) via the production client, so no local
    model / faster-whisper is needed. Skips unless ``WHISPER_API_URL`` is set.
    """
    url = os.environ.get("WHISPER_API_URL", "").strip()
    if not url:
        pytest.skip("set WHISPER_API_URL (base URL incl. /v1) to run the remote test")

    pairs = _fixture_pairs()
    if not pairs:
        pytest.skip(f"no <name>.mp3 + <name>.txt fixtures in {FIXTURES}")

    from ai_service.transcribe import transcribe_file

    cfg = service_config(
        whisper_api_url=url.rstrip("/"),
        whisper_api_key=os.environ.get("WHISPER_API_KEY", ""),
        whisper_model=os.environ.get("WHISPER_MODEL", MODEL),
        language=os.environ.get("LANGUAGE", LANGUAGE),
        whisper_timeout_seconds=int(os.environ.get("WHISPER_TIMEOUT_SECONDS", "600")),
    )

    wers, cers = [], []
    for mp3, txt in pairs:
        reference = txt.read_text(encoding="utf-8")
        result = transcribe_file(cfg, mp3)
        hypothesis = " ".join(seg.text for seg in result.segments)

        assert hypothesis.strip()

        w, c = wer(reference, hypothesis), cer(reference, hypothesis)
        wers.append(w)
        cers.append(c)
        line = f"{mp3.name}: WER={w:.3f} CER={c:.3f}"
        print(line)
        print(alignment(reference, hypothesis))
        logger.info(line)

    summary = (
        f"[WER remote] {len(pairs)} file(s)  "
        f"mean WER={sum(wers) / len(wers):.3f}  "
        f"mean CER={sum(cers) / len(cers):.3f}"
    )
    print(summary)
    logger.info(summary)
```

- [ ] **Step 3: Verify the new test is collected**

Run: `.venv/bin/pytest tests/whisper_api/test_wer.py -m slow --collect-only -q`
Expected: output lists `test_wer_against_fixtures` AND `test_wer_against_fixtures_remote` (no collection errors).

- [ ] **Step 4: Verify it skips cleanly with no URL set**

Run: `env -u WHISPER_API_URL .venv/bin/pytest tests/whisper_api/test_wer.py -k remote -m slow -rs -v`
Expected: `test_wer_against_fixtures_remote` reports SKIPPED with reason "set WHISPER_API_URL ...".

- [ ] **Step 5: Verify the fast suite is unaffected**

Run: `.venv/bin/pytest tests/whisper_api/test_wer.py -q`
Expected: the 4 fast helper tests PASS; both slow tests deselected (0 failures).

- [ ] **Step 6: Add README note**

In `README.md`, immediately after the current final line of the "WER accuracy test" section (line 84, "…`COMPUTE_TYPE` constants at the top of the test if needed."), append:

```markdown

To test an **external** whisper-api reached over its URL instead (e.g. a GPU
host), set `WHISPER_API_URL` (base URL including `/v1`) and run the remote
variant — no local model is loaded:

    WHISPER_API_URL=https://gpu-host/v1 WHISPER_API_KEY=... \
      .venv/bin/pytest tests/whisper_api/test_wer.py -k remote -m slow -s

Optional overrides: `WHISPER_MODEL` (default `large-v3`), `LANGUAGE` (default
`ru`), `WHISPER_TIMEOUT_SECONDS` (default `600`).
```

- [ ] **Step 7: Commit**

```bash
git add tests/whisper_api/test_wer.py README.md
git commit -m "test: add remote WER/CER test against external whisper-api by URL"
```
