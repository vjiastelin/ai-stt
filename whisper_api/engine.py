"""faster-whisper wrapper: load once, serialize model access (spec §4.1)."""
import threading
from dataclasses import dataclass


class InvalidAudioError(Exception):
    """The input could not be decoded as audio (corrupt/unsupported file)."""


def _is_decode_error(exc: BaseException) -> bool:
    return type(exc).__module__.split(".")[0] == "av"


@dataclass(frozen=True)
class EngineResult:
    language: str
    duration: float
    segments: list[dict]
    text: str


class Engine:
    def __init__(
        self,
        model_name: str,
        device: str,
        compute_type: str,
        vad_filter: bool = True,
        condition_on_previous_text: bool = False,
    ):
        from faster_whisper import WhisperModel  # lazy: heavy import, needs the `api` extra

        self.model_name = model_name
        self._model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self._lock = threading.Lock()
        # call-recording defaults: VAD trims silence/hold music (fewer hallucinations),
        # no cross-window conditioning avoids repetition loops on noisy phone audio
        self._vad_filter = vad_filter
        self._condition_on_previous_text = condition_on_previous_text

    def transcribe(self, audio_path: str, language: str | None) -> EngineResult:
        with self._lock:  # one model instance: serialize concurrent requests
            try:
                segments_iter, info = self._model.transcribe(
                    audio_path,
                    language=language or None,
                    vad_filter=self._vad_filter,
                    condition_on_previous_text=self._condition_on_previous_text,
                )
                segments = [
                    {"id": i, "start": float(seg.start), "end": float(seg.end), "text": seg.text}
                    for i, seg in enumerate(segments_iter)
                ]
            except Exception as exc:
                # PyAV raises for corrupt/undecodable input; can surface lazily
                # during segment iteration, not just on the initial call.
                if _is_decode_error(exc):
                    raise InvalidAudioError(str(exc)) from exc
                raise
        return EngineResult(
            language=info.language,
            duration=float(info.duration),
            segments=segments,
            text="".join(seg["text"] for seg in segments).strip(),
        )
