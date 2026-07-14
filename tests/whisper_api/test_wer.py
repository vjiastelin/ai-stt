"""Word Error Rate of the production model against committed speech fixtures.

Fast unit tests cover the WER/CER helpers. The real-transcription test is
``slow`` (skipped by default) and additionally skips when no fixture is present.
It *reports* WER/CER (prints them; run with ``-s``) and does not gate on a
threshold. Drop ``<name>.mp3`` + ``<name>.txt`` pairs into ``tests/fixtures/wer/``.
"""
import logging
import os
from pathlib import Path

import pytest

from tests.wer import alignment, cer, normalize, wer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wer"

# Edit these for a faster/cheaper local run (e.g. "tiny"/"int8").
MODEL = "large-v3"
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
LANGUAGE = "ru"

logger = logging.getLogger(__name__)


def test_wer_identical_is_zero():
    assert wer("привет мир", "привет мир") == 0.0


def test_wer_half_on_missing_words():
    assert wer("привет мир как дела", "привет мир") == 0.5


def test_normalize_strips_case_and_punctuation():
    assert normalize("Привет, мир!") == ["привет", "мир"]
    assert wer("Привет, мир!", "привет мир") == 0.0


def test_cer_identical_is_zero():
    assert cer("привет", "привет") == 0.0


def _fixture_pairs() -> list[tuple[Path, Path]]:
    if not FIXTURES.is_dir():
        return []
    pairs = []
    for mp3 in sorted(FIXTURES.glob("*.mp3")):
        txt = mp3.with_suffix(".txt")
        if txt.is_file():
            pairs.append((mp3, txt))
    return pairs


@pytest.mark.slow
def test_wer_against_fixtures():
    pytest.importorskip("faster_whisper")
    from whisper_api.engine import Engine

    pairs = _fixture_pairs()
    if not pairs:
        pytest.skip(f"no <name>.mp3 + <name>.txt fixtures in {FIXTURES}")

    engine = Engine(MODEL, device=DEVICE, compute_type=COMPUTE_TYPE)

    wers, cers = [], []
    for mp3, txt in pairs:
        reference = txt.read_text(encoding="utf-8")
        result = engine.transcribe(str(mp3), language=LANGUAGE)

        assert isinstance(result.text, str) and result.text.strip()

        w, c = wer(reference, result.text), cer(reference, result.text)
        wers.append(w)
        cers.append(c)
        line = f"{mp3.name}: WER={w:.3f} CER={c:.3f}"
        print(line)
        print(alignment(reference, result.text))
        logger.info(line)

    summary = (
        f"[WER] {len(pairs)} file(s)  "
        f"mean WER={sum(wers) / len(wers):.3f}  "
        f"mean CER={sum(cers) / len(cers):.3f}"
    )
    print(summary)
    logger.info(summary)


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
