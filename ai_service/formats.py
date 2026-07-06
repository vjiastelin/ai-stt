"""Build FullText (with timecodes) and plain text from segments (spec §3.3)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    id: int
    start: float
    end: float
    text: str


def format_timecode(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"


def to_full_text(segments: list[Segment]) -> str:
    return "\n".join(
        f"{format_timecode(seg.start)} {seg.text.strip()}"
        for seg in segments
        if seg.text.strip()
    )


def to_plain_text(segments: list[Segment]) -> str:
    return " ".join(seg.text.strip() for seg in segments if seg.text.strip())
