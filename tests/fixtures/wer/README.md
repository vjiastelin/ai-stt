# WER test fixtures

Drop matched pairs here, keyed by basename:

- `<name>.mp3` — a real speech clip (MP3, per the production MP3-only policy)
- `<name>.txt` — its ground-truth transcript

`tests/whisper_api/test_wer.py::test_wer_against_fixtures` auto-discovers every
`*.mp3` that has a sibling `.txt`, transcribes it with `large-v3`, and prints
WER/CER. It skips cleanly when this directory holds no such pairs.

## `.txt` format

Plain UTF-8 text — just the spoken words as prose. **No** timestamps, speaker
labels, JSON, or metadata. Casing, punctuation, and line breaks are normalized
away before scoring, so paste a natural human transcript as-is.

```
Здравствуйте, меня зовут Иван. Я хотел бы забронировать билет
на рейс до Москвы на завтра. Сколько это будет стоить?
```

## Interpreting the numbers (`wer_test.mp3`, large-v3)

Baseline run: **WER ≈ 0.36, CER ≈ 0.059** — `substitutions=59 deletions=1
insertions=44 hits=227` (287 reference words).

Read the *shape* of the errors, not just the WER:

- **CER 5.9% + only 1 deletion** means the model captures essentially all the
  content accurately. The transcription is good; the WER overstates the error.
- **44 insertions vs. 1 deletion** is a verbatim-vs-condensed signature, not an
  accuracy problem. The reference is a *cleaned* human transcript (filler,
  repeats, false starts removed); Whisper transcribes them faithfully, so it
  reads as "extra" words. Only a **verbatim** reference would collapse these.
- **Numbers are not the issue.** large-v3 keeps digits as digits (`0`, `2`,
  `9 по 12`, `1030`, `25238327`) and they match the reference — so digit→word
  normalization would *not* meaningfully lower the WER here. (Confirmed via the
  alignment dump; don't re-litigate it.)
- **Inflectional endings** (`остальное→остальные`, `ответа→ответ`) count as full
  word errors under WER but are one-character CER blips — expected for Russian.

Genuine mishearings are a small minority (~10–12 words), e.g. the operator name
`Олег → Татьяна` and `отель Казахстан → …за 100`. **Watch speaker-name errors**
in anything downstream that attributes statements to a named speaker.

Rule of thumb for spontaneous phone Russian scored against a cleaned reference:
WER in the 25–40% range is normal; trust CER + the deletion count as the real
accuracy signal. Run with `-s` to see the per-fixture word-level alignment.
