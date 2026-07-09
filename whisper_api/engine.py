"""faster-whisper wrapper: load once, serialize model access (spec §4.1)."""
import threading
from dataclasses import dataclass
import logging

class InvalidAudioError(Exception):
    """The input could not be decoded as audio (corrupt/unsupported file)."""


def _is_decode_error(exc: BaseException) -> bool:
    return type(exc).__module__.split(".")[0] == "av"

logger = logging.getLogger(__name__)

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
        self._vad_filter = vad_filter
        self._condition_on_previous_text = condition_on_previous_text

    def transcribe(self, audio_path: str, language: str | None) -> EngineResult:
        with self._lock:  # one model instance: serialize concurrent requests
            try:
                segments_iter, info = self._model.transcribe(
                    audio_path,
                    language=language or None,
                    beam_size=10,
                    vad_filter=self._vad_filter,
                    condition_on_previous_text=True,
                    temperature=0,
                    compression_ratio_threshold=2.2,
                    log_prob_threshold=-1.0,
                    no_speech_threshold=0.5,
                    vad_parameters={
                        "min_silence_duration_ms": 700,
                        "speech_pad_ms": 500,
                    },
                )

                segments = []
                for i, seg in enumerate(segments_iter):
                    logger.debug(
                        "%.2f-%.2f  logprob=%6.2f  compression=%4.2f  no_speech=%4.2f  %s",
                        seg.start,
                        seg.end,
                        seg.avg_logprob,
                        seg.compression_ratio,
                        seg.no_speech_prob,
                        seg.text.strip(),
                    )

                    segments.append({
                        "id": i,
                        "start": float(seg.start),
                        "end": float(seg.end),
                        "text": seg.text,
                    })
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
