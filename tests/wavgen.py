"""Generate a small deterministic WAV file for tests (no binary fixtures in repo)."""
import math
import struct
import wave


def write_test_wav(path, seconds: float = 1.0, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
        wav.writeframes(bytes(frames))
