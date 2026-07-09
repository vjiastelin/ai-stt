"""Client for whisper-api's OpenAI-compatible transcription endpoint (spec §4.3)."""
from dataclasses import dataclass
from pathlib import Path

import httpx

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError, PermanentJobError
from ai_service.formats import Segment


@dataclass(frozen=True)
class Transcription:
    language: str
    duration: float
    segments: list[Segment]


def transcribe_file(cfg: ServiceConfig, wav_path: Path) -> Transcription:
    data = {"model": cfg.whisper_model, "response_format": "verbose_json"}
    if cfg.language:
        data["language"] = cfg.language
    headers = {}
    if cfg.whisper_api_key:
        headers["Authorization"] = f"Bearer {cfg.whisper_api_key}"
    try:
        with wav_path.open("rb") as fh:
            response = httpx.post(
                f"{cfg.whisper_api_url}/audio/transcriptions",
                files={"file": (wav_path.name, fh, "audio/wav")},
                data=data,
                headers=headers,
                timeout=cfg.whisper_timeout_seconds,
            )
    except httpx.HTTPError as exc:
        raise InfrastructureError(f"whisper-api request failed: {exc}") from exc

    if response.status_code >= 500:
        raise InfrastructureError(f"whisper-api returned {response.status_code}")
    if response.status_code >= 400:
        raise PermanentJobError(
            f"whisper-api returned {response.status_code}: {response.text[:500]}"
        )

    try:
        payload = response.json()
        segments = [
            Segment(id=i, start=float(seg["start"]), end=float(seg["end"]), text=seg["text"])
            for i, seg in enumerate(payload["segments"])
        ]
        return Transcription(
            language=payload["language"],
            duration=float(payload["duration"]),
            segments=segments,
        )
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise InfrastructureError(
            f"whisper-api returned malformed 200 response: {exc}"
        ) from exc
