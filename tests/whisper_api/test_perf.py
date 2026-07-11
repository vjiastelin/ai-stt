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
