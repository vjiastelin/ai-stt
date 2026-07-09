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
