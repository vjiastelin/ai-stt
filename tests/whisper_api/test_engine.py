import pytest

from tests.wavgen import write_test_wav
from whisper_api.engine import _is_decode_error


def test_is_decode_error_true_for_av_exceptions():
    fake_av_exc = type("InvalidDataError", (Exception,), {"__module__": "av.error"})()
    assert _is_decode_error(fake_av_exc) is True


def test_is_decode_error_false_for_other_exceptions():
    assert _is_decode_error(RuntimeError()) is False


@pytest.mark.slow
def test_engine_transcribes_real_wav(tmp_path):
    pytest.importorskip("faster_whisper")
    from whisper_api.engine import Engine

    wav = tmp_path / "tone.wav"
    write_test_wav(wav, seconds=2.0)

    engine = Engine("tiny", device="cpu", compute_type="int8")
    result = engine.transcribe(str(wav), language="ru")

    assert result.duration == pytest.approx(2.0, abs=0.5)
    assert isinstance(result.segments, list)
    for seg in result.segments:
        assert set(seg) == {"id", "start", "end", "text"}
    assert result.text == "".join(s["text"] for s in result.segments).strip()
