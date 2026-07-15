# Changelog

## [0.4.0](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.3.2...ai-service-v0.4.0) (2026-07-14)


### Features

* add job list and result endpoints to ai-service ([decf89f](https://github.com/vjiastelin/ai-stt/commit/decf89f05aff787085b986691ce6dc62840cf0bb))
* ai-service config with SUMMARY_ENABLED-aware validation ([38719f4](https://github.com/vjiastelin/ai-stt/commit/38719f4e420f7677d380e80fb8ab44f64c1d5872))
* ai-service HTTP API with idempotent requestTranscription ([9f09317](https://github.com/vjiastelin/ai-stt/commit/9f0931737f2c869852d760032dafd53d06040044))
* BPM callback delivery retried until 200 ([72eed7c](https://github.com/vjiastelin/ai-stt/commit/72eed7cb75b8ed18fb21bee0427241102f78923f))
* CallRecordUrl parsing and S3 download with error mapping ([a9a6fd1](https://github.com/vjiastelin/ai-stt/commit/a9a6fd11d0b8dcb343c982282faedf2c35329e46))
* durable SQLite job store with idempotent enqueue ([7400dc2](https://github.com/vjiastelin/ai-stt/commit/7400dc26328fc18f1b733bdfe9d7c515d240abc5))
* error taxonomy and FullText formatting ([5e86962](https://github.com/vjiastelin/ai-stt/commit/5e86962b023a1ec006a75fa26ce9633ed16c8483))
* LLM summarization with enable toggle ([61061cf](https://github.com/vjiastelin/ai-stt/commit/61061cf6c67ad31b1fd1831dcd0b693958549355))
* strict MP3-only policy per production workload ([a53ba63](https://github.com/vjiastelin/ai-stt/commit/a53ba6398f1e4b40a11d7bed9bf793738c6851fe))
* typed request/response schemas for OpenAPI/Swagger docs ([5035a3c](https://github.com/vjiastelin/ai-stt/commit/5035a3c0547aea7d9f9390a8fce896f9de696ec0))
* whisper-api client with error classification ([d056d77](https://github.com/vjiastelin/ai-stt/commit/d056d77a361807cc1b784981d3c2f777314001b1))
* **whisper-api:** chat/completions path, configurable transcribe options, optional HTTPS ([216589d](https://github.com/vjiastelin/ai-stt/commit/216589d7a2a0fd5de5a41313414ecd9c43948dc9))
* worker loop with backoff, retries and entrypoint ([4563f1d](https://github.com/vjiastelin/ai-stt/commit/4563f1d4c458a98528f317e21a882e853fba82dc))


### Bug Fixes

* classify corrupt-audio and malformed-200 failures; wire whisper API key end-to-end ([0aa3758](https://github.com/vjiastelin/ai-stt/commit/0aa375856b361de74d8c89f161e1a0cdb2f7c2b2))
