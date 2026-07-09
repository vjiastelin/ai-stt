"""Word/character error rate helpers, backed by ``jiwer``.

Wraps jiwer with a Cyrillic-safe normalization transform (lowercase, strip
punctuation, collapse whitespace) shared by both the word- and char-level
metrics, so callers get one consistent normalization.
"""
import jiwer

# Lowercase, drop punctuation, collapse whitespace. jiwer's RemovePunctuation
# uses the Unicode ``P`` category, so Cyrillic text is preserved.
_CLEAN = jiwer.Compose([
    jiwer.ToLowerCase(),
    jiwer.RemovePunctuation(),
    jiwer.RemoveMultipleSpaces(),
    jiwer.Strip(),
])

_WORDS = jiwer.Compose([_CLEAN, jiwer.ReduceToListOfListOfWords()])
_CHARS = jiwer.Compose([_CLEAN, jiwer.ReduceToListOfListOfChars()])


def normalize(text: str) -> list[str]:
    """Return the normalized token list used for WER."""
    reduced = _WORDS(text)
    return reduced[0] if reduced else []


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate over normalized tokens."""
    return jiwer.wer(
        reference,
        hypothesis,
        reference_transform=_WORDS,
        hypothesis_transform=_WORDS,
    )


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate over the normalized strings."""
    return jiwer.cer(
        reference,
        hypothesis,
        reference_transform=_CHARS,
        hypothesis_transform=_CHARS,
    )
