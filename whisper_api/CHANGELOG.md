# Changelog

## [0.4.1](https://github.com/vjiastelin/ai-stt/compare/whisper-api-v0.4.0...whisper-api-v0.4.1) (2026-07-21)


### Bug Fixes

* change whipser api to transcriptions openai api compatible ([#11](https://github.com/vjiastelin/ai-stt/issues/11)) ([d22aa85](https://github.com/vjiastelin/ai-stt/commit/d22aa859b71f44cb9603b6dab2d2e6b0ac4ff944))

## [0.4.0](https://github.com/vjiastelin/ai-stt/compare/whisper-api-v0.3.2...whisper-api-v0.4.0) (2026-07-14)


### Features

* faster whisper tuning transcribe ([1570042](https://github.com/vjiastelin/ai-stt/commit/15700429e471c4a07242f27711c262a335a1fe39))
* faster-whisper engine wrapper with serialized access ([13d43bc](https://github.com/vjiastelin/ai-stt/commit/13d43bc248001e9e6910022d7d5a3461cae9e568))
* strict MP3-only policy per production workload ([a53ba63](https://github.com/vjiastelin/ai-stt/commit/a53ba6398f1e4b40a11d7bed9bf793738c6851fe))
* typed request/response schemas for OpenAPI/Swagger docs ([5035a3c](https://github.com/vjiastelin/ai-stt/commit/5035a3c0547aea7d9f9390a8fce896f9de696ec0))
* VAD and conditioning options for call-recording transcription ([58590e6](https://github.com/vjiastelin/ai-stt/commit/58590e69088451223afb4c06e6c3f7125232c9e3))
* whisper-api config with cpu/gpu compute-type auto ([e0c5ac7](https://github.com/vjiastelin/ai-stt/commit/e0c5ac7c5812b67ef24925e4dfac43868fd7372f))
* whisper-api FastAPI app with health and auth ([f7acce1](https://github.com/vjiastelin/ai-stt/commit/f7acce168f6b2874b8c5e1b779fa5e49e5c5dd74))
* **whisper-api:** chat/completions path, configurable transcribe options, optional HTTPS ([216589d](https://github.com/vjiastelin/ai-stt/commit/216589d7a2a0fd5de5a41313414ecd9c43948dc9))


### Bug Fixes

* classify corrupt-audio and malformed-200 failures; wire whisper API key end-to-end ([0aa3758](https://github.com/vjiastelin/ai-stt/commit/0aa375856b361de74d8c89f161e1a0cdb2f7c2b2))
* honor condition_on_previous_text flag, default it on ([a394f7d](https://github.com/vjiastelin/ai-stt/commit/a394f7d9edf78c9f8c718a53fb3f792f88027721))
* revert whisper api to open ai api ([#2](https://github.com/vjiastelin/ai-stt/issues/2)) ([02fe77e](https://github.com/vjiastelin/ai-stt/commit/02fe77e940aaaf9d70cb9654f9f62ec5795c3c2b))
