# Changelog

## [0.10.0](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.9.0...ai-service-v0.10.0) (2026-10-01)


### Features

* [[route.client]] — one table for routing and the prompt's known domains ([265b54f](https://github.com/vjiastelin/ai-stt/commit/265b54f66cb2d5a2d6287e4a70ec493efa758941))
* PROMPT_PROFILE with a tested `voicemail` prompt set ([824109c](https://github.com/vjiastelin/ai-stt/commit/824109cbd054b36881286354a55f835bb9583027))
* **prompts:** voicemail — known domains may list several per company ([cbc670b](https://github.com/vjiastelin/ai-stt/commit/cbc670bfdd5f16deaa9bc9f1c75e21237a579957))
* routing stats per team, delivery view and routing dry run ([e1ca454](https://github.com/vjiastelin/ai-stt/commit/e1ca4546915c42dc4ec6428ba72360f955cfaee3))
* **routing:** assign every domain to a client, add spoken aliases ([3448ec4](https://github.com/vjiastelin/ai-stt/commit/3448ec41f1eea8860811201e725b3be385d31252))
* see which clients miss the routing table (log, metric, report) ([a61c34a](https://github.com/vjiastelin/ai-stt/commit/a61c34a4c364ed7e884ededec8915b4b4968c9f5))
* TOML email routing file — route by client e-mail domain or company ([3f9c9e9](https://github.com/vjiastelin/ai-stt/commit/3f9c9e9ca061004efd11682d7f831b96e1a796fe))

## [0.9.0](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.8.0...ai-service-v0.9.0) (2026-10-01)


### Features

* EMAIL_ROUTES — route emails by the recording's file name ([b95b81f](https://github.com/vjiastelin/ai-stt/commit/b95b81f4881914b75083ec98119b4f0719c4f912))

## [0.8.0](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.7.0...ai-service-v0.8.0) (2026-09-30)


### Features

* KNOWN_EMAIL_DOMAINS list rendered into the summary prompt ([b891069](https://github.com/vjiastelin/ai-stt/commit/b891069fc66963f9860a94880b3f405f3052d01d))
* KNOWN_EMAIL_DOMAINS list rendered into the summary prompt ([cfa1bfa](https://github.com/vjiastelin/ai-stt/commit/cfa1bfa924c9fe6284ae7bf4dea65a6f159361ae))

## [0.7.0](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.6.1...ai-service-v0.7.0) (2026-09-30)


### Features

* accept .wav call records in addition to .mp3 ([d80257d](https://github.com/vjiastelin/ai-stt/commit/d80257dd554ba2673834bf1eaaca468670ddcf3d))
* deliver results by email in addition to (or instead of) BPM ([af3c3c0](https://github.com/vjiastelin/ai-stt/commit/af3c3c03ace663211a4a8ecd09394be2bace3bc2))
* deliver results by email in addition to (or instead of) BPM ([e907188](https://github.com/vjiastelin/ai-stt/commit/e90718879efb88d7426f8e3a0079036e2463d710))
* incremental S3 bucket scanner as a second way to start transcriptions ([#23](https://github.com/vjiastelin/ai-stt/issues/23)) ([6dba1fe](https://github.com/vjiastelin/ai-stt/commit/6dba1feedb51dc96a5676b260e76caecd1415d65))
* LLM_EXTRA_BODY for extra LLM request fields; strip &lt;think&gt; from summaries ([#24](https://github.com/vjiastelin/ai-stt/issues/24)) ([d2076ff](https://github.com/vjiastelin/ai-stt/commit/d2076ff904f14a9119ec2ae97d4c3fea2d99e73a))
* show the caller phone parsed from the IVR file name in emails ([7a93448](https://github.com/vjiastelin/ai-stt/commit/7a93448cc410bbfbb02827c4f207ea7c29a0bdae))
* WHISPER_PROMPT vocabulary hint for Whisper (OpenAI `prompt` field) ([#25](https://github.com/vjiastelin/ai-stt/issues/25)) ([2b34257](https://github.com/vjiastelin/ai-stt/commit/2b342575c6c98c71745d96ba84f62be20f034f7d))
* WHISPER_VERIFY_SSL / LLM_VERIFY_SSL to accept self-signed certs ([4fe25dd](https://github.com/vjiastelin/ai-stt/commit/4fe25dd17625b6d096a077d76eb5b4f463294c36))

## [0.6.1](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.6.0...ai-service-v0.6.1) (2026-09-15)


### Bug Fixes

* use llm-proxy model id whisper/large-v3 and retry 429 ([#18](https://github.com/vjiastelin/ai-stt/issues/18)) ([2664c88](https://github.com/vjiastelin/ai-stt/commit/2664c88373c57e222e8fe7e7bf2edd4004178566))

## [0.6.0](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.5.1...ai-service-v0.6.0) (2026-08-04)


### Features

* report transcription results to BPM result endpoint with CSRF + failures ([#14](https://github.com/vjiastelin/ai-stt/issues/14)) ([08ed53a](https://github.com/vjiastelin/ai-stt/commit/08ed53abe716f69b129d94da1f693c1eb1c02ba5))

## [0.5.1](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.5.0...ai-service-v0.5.1) (2026-07-21)


### Bug Fixes

* change whipser api to transcriptions openai api compatible ([#11](https://github.com/vjiastelin/ai-stt/issues/11)) ([d22aa85](https://github.com/vjiastelin/ai-stt/commit/d22aa859b71f44cb9603b6dab2d2e6b0ac4ff944))

## [0.5.0](https://github.com/vjiastelin/ai-stt/compare/ai-service-v0.4.0...ai-service-v0.5.0) (2026-07-20)


### Features

* **ai-service:** add Prometheus /metrics endpoint and pipeline metrics ([#8](https://github.com/vjiastelin/ai-stt/issues/8)) ([2f6ab44](https://github.com/vjiastelin/ai-stt/commit/2f6ab4480c612d8f878194b400b33c37944111da))

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
