# ai-stt

BPM-driven speech-to-text service. BPMSoft(Omni) pushes a transcription
request; the service downloads the call record (MP3, ~5 min / ~4.5 MB typical,
~850 calls/day) from S3-compatible storage, transcribes it, optionally
summarizes it, and delivers the result to BPM and/or by email. `CallRecordUrl` must point
to an `.mp3` or `.wav` object — anything else is rejected with 400.

Two services:

- **ai-service** — FastAPI + durable SQLite job queue.
  `POST /requestTranscription` (`CallRecordId`, `CallRecordUrl`) → 200 accepted;
  the result is `POST`ed to
  `{BPM_CALLBACK_URL}/0/ServiceModel/AnGetTranscriptionResultService.svc/transcriptions/{CallRecordId}/result`
  as `{Summary, FullText, Error, ErrorDescription}` (retried until BPM answers
  200). A permanently-failed job is reported the same way with `Error: true`
  and the reason in `ErrorDescription`. When `BPM_CSRF_TOKEN` is set it is sent
  as the `x-api-key` request header.
  **Delivery channels** are chosen by configuration: BPM when `BPM_CALLBACK_URL`
  is set, email (SMTP) when `SMTP_HOST` + `EMAIL_FROM` + `EMAIL_TO` are set,
  both when both are — at least one is required. The email carries Summary and
  FullText (plus FullText as a `{CallRecordId}.txt` attachment), or the error
  reason for a failed job. Each channel is retried independently until it
  accepts; one that already accepted is not resent.
  `EMAIL_ROUTES` (`"AWAD_IVRrecord_*=a@x.ru; GATE_IVRrecord_*=b@y.ru"`) routes mail
  by the recording's file name: the first matching glob (case-sensitive, folder
  ignored) replaces `EMAIL_TO`, which stays the default for everything else.
  The full routing table lives in a TOML file (`EMAIL_ROUTING_FILE`; format and
  the current table: [`config/email-routing.toml`](config/email-routing.toml)):
  file-name rules first, then client routes by the e-mail domain (subdomains
  included, most specific wins) or — as a fallback — the company name recognized
  in the summary (or the name of an unknown domain matching a client alias:
  `delain.ru` → «Delain», how Whisper hears «Билайн»), then a known domain spelled out in the transcript (when the
  summary lost the address — Whisper sometimes glues it into one word, e.g.
  `annasobakabayer.com`), then the file's `default`. A domain or company listed in two
  routes stops startup. Helm: `--set-file emailRouting=config/email-routing.toml`
  renders it into a ConfigMap and sets `EMAIL_ROUTING_FILE`.
  **Bucket scanner (optional):** with `S3_SCAN_URL=s3://bucket/prefix/` the
  service also polls that prefix every `S3_SCAN_INTERVAL_SECONDS` (300) and
  queues each *new* `.mp3`/`.wav` object exactly once — seen keys are stored in
  the job DB, so scans are incremental and survive restarts, and a failed job is
  not re-queued by the next scan. `S3_SCAN_MODIFIED_AFTER` skips older objects
  (e.g. the historical backlog). Scanned jobs get a stable UUID `CallRecordId`
  derived from the object path; BPM doesn't know those ids, so deliver them by
  email (the message shows the file path).
  Inspection endpoints: `GET /jobs` (list, newest first, `?status=` filter +
  `limit`/`offset`), `GET /jobs/{CallRecordId}` (status), and
  `GET /jobs/{CallRecordId}/result` (the `Summary` and `FullText`).
  `GET /routing/unmatched?days=30` lists clients (company + e-mail domain) whose
  mail went to the default mailbox, most calls first — candidates for the routing
  table (summaries are re-routed with the current table, so added clients drop out);
  `unrecognized_examples` lists the calls where neither was recognized.
  `GET /routing/stats?days=30` — delivered results per team (route) and rule, as
  recorded at delivery, for any channel (BPM-only setups too), with the mailboxes
  e-mails went to. `GET /jobs/{CallRecordId}/delivery` — what was / would be sent:
  the BPM payload, the e-mail (recipients, subject, body, attachment), the
  recorded routing and where it would go with the current table.
  `POST /routing/preview` (`{"CallRecordUrl": "…", "Summary": "…"}`) — dry run:
  which team / mailbox a summary would be routed to, without a call.
  `GET /healthz` liveness.
- **whisper-api** — FastAPI + faster-whisper (GPU or CPU, `DEVICE=cuda|cpu`),
  OpenAI-compatible transcription at `POST /v1/audio/transcriptions` (multipart
  upload → `verbose_json` with timecoded segments). Decode options are tunable
  via `TRANSCRIBE_OPTIONS` (JSON), and it can serve HTTPS via `SSL_CERTFILE`/
  `SSL_KEYFILE` — see `.env.example`.

Whisper and LLM endpoints behind self-signed certificates (e.g. ephemeral GPU
instances reached by IP) can be used with `WHISPER_VERIFY_SSL=false` /
`LLM_VERIFY_SSL=false`: traffic stays encrypted but the server is not
authenticated, and a warning is logged at startup.

Prompts come in built-in profiles (`ai_service/prompts.py`), picked with
`PROMPT_PROFILE`: `call` (default) summarizes a regular BPM phone call;
`voicemail` handles IVR voice messages — a six-line Имя/Компания/Почта/Суть/
Детали/Срочность summary that assembles e-mails dictated in words, plus a
Whisper hint. `SUMMARY_PROMPT` (when non-empty) and `WHISPER_PROMPT` (whenever
set, empty = no hint) override the profile.

`WHISPER_PROMPT` is sent as the OpenAI `prompt` field (whisper-api maps it to
faster-whisper's `initial_prompt`, overriding one set in `TRANSCRIBE_OPTIONS`).
Spell example e-mails out in words ("… собачка … точка ру"): that keeps the
dictation intact in the transcript, while an example address with `@` made
Whisper assemble addresses itself and get them wrong.

Summaries come from an external OpenAI-compatible LLM (`LLM_API_URL`);
set `SUMMARY_ENABLED=false` to skip summarization (Summary is sent as `""`).
`KNOWN_EMAIL_DOMAINS` (`"Company|Alias=domain.ru; Other=other.com"`) is a
maintainable list of client companies and their corporate e-mail domains; it is
rendered into `SUMMARY_PROMPT` at the `{KNOWN_EMAIL_DOMAINS}` placeholder (or
appended when the prompt has none), so the LLM can complete a dictated address
whose domain was lost without guessing domains for other companies.
Extra request fields go in `LLM_EXTRA_BODY` (JSON), e.g.
`{"chat_template_kwargs":{"enable_thinking":false},"max_tokens":1024}` to turn off
the reasoning of Qwen3-family models, which otherwise can take most of the
generation time. A `<think>…</think>` block that leaks into the answer is
stripped before the Summary is stored.

Interactive API docs (Swagger UI) with request/response schemas:
`http://localhost:8080/docs` (ai-service) and `http://<whisper-api-host>:8000/docs`.

Design spec: `docs/superpowers/specs/2026-07-06-ai-stt-bpm-integration-design.md`.

## Run

    cp .env.example .env   # fill in S3, BPM callback and/or SMTP, LLM endpoint
    docker compose up --build

By default compose runs **ai-service only**, against the external whisper-api
set by `WHISPER_API_URL` (+ `WHISPER_API_KEY`). To build and run whisper-api
locally as well, leave `WHISPER_API_URL` unset and enable the profile:

    docker compose --profile local-whisper up --build

ai-service then waits for whisper-api to become healthy (Compose >= 2.20).
First start downloads the Whisper model into the `model-cache` volume. The
download runs before the model is loaded, with a per-request timeout, retries and
an overall deadline (`WHISPER_DOWNLOAD_RETRIES`, `WHISPER_DOWNLOAD_TIMEOUT_SECONDS`);
progress (MB so far, MB/s) is logged and shown in the `/health` 503 detail, and past
the deadline the process exits so a restart resumes the partial files. It uses
plain HTTP by default (`HF_HUB_DISABLE_XET=1`); with a filled cache set
`HF_HUB_OFFLINE=1` to start without contacting the hub.
On CPU-only hosts, set `DEVICE=cpu` in `.env` (the default is `cuda`); on GPU
hosts uncomment the `deploy:` block of `whisper-api` in `docker-compose.yml`.
A `failed` job (see `GET /jobs/{id}`) is retried by re-POSTing
`/requestTranscription` with the same `CallRecordId`.

BPM's result endpoint should be idempotent: delivery is at-least-once, so the
same `{Summary, FullText, Error, ErrorDescription}` payload may be posted more
than once (e.g. after a retry that BPM actually received but did not acknowledge
with `200`). The same holds for email: a message may be sent twice if the SMTP
server accepted it but the connection dropped before the reply.

## Email routing table

Recipients are chosen per record from [`config/email-routing.toml`](config/email-routing.toml)
(format and order of checks are documented at the top of the file). Enable it:

- **docker compose** — `./config` is mounted at `/app/config`; add to `.env`:

      EMAIL_ROUTING_FILE=/app/config/email-routing.toml

  After editing the table, `docker compose up -d --force-recreate ai-service` is
  enough — no image rebuild.
- **Kubernetes** — pass the file to the chart; it becomes a ConfigMap mounted into
  the pod, `EMAIL_ROUTING_FILE` is set for you, and changing the table rolls the pod:

      helm upgrade … --set-file emailRouting=config/email-routing.toml

- The AWAD/GATE file-name rules are in the table (`[[file_route]]`), so
  `EMAIL_ROUTES` can be dropped from the environment (if both are set,
  `EMAIL_ROUTES` is checked first). `EMAIL_TO` is still required and is used only
  when the table has no `default`.
- Each `[[route.client]]` (company + aliases + domains) also feeds the summary
  prompt's `{KNOWN_EMAIL_DOMAINS}` list, so the LLM can complete an address whose
  domain was lost; `KNOWN_EMAIL_DOMAINS` now only adds pairs missing from the
  table. Bare route-level `domains`/`companies` route mail but aren't shown to
  the LLM — move them into a client once the owner is known.
- The table is loaded even without the email channel: it also tells which team a
  call belongs to, which `GET /routing/stats` reports for BPM-only setups. Nothing
  is added to the BPM payload.
- To see who is missing from the table: `GET /routing/unmatched?days=30`; every
  result mail sent to the default mailbox is also logged as
  `email routed to default: id=… company=… domain=…`, and
  `ai_service_email_routed_total{by="file|domain|company|transcript|default"}` counts mails
  per rule.
- A broken table (bad TOML, unknown key, invalid domain/address, a domain or
  company listed twice) stops startup with the offending entry in the log; the
  committed table is also checked by `tests/ai_service/test_routing.py`.

## Monitoring

ai-service exposes Prometheus metrics at `GET /metrics` (port 8080): queue depth
per state, oldest-queued age, per-stage latency histograms, transcription
real-time factor, end-to-end delivery time, and error counters by the retry
taxonomy. Metric reference, PromQL, and threshold rationale: [docs/metrics.md](docs/metrics.md).

In Kubernetes the Helm chart creates a `ServiceMonitor`, so kube-prometheus-stack
scrapes the target with no extra configuration. The Grafana dashboard
(`AI STT — Transcription Pipeline`, uid `ai-stt`) and its 7 alert rules live on
`grafana-ai.aeroclub.int` and are versioned in the `services-ai-grafana` repo — the
cluster convention — not in this one.

## Releases

The two services version and release **independently** and automatically, driven by
[release-please](https://github.com/googleapis/release-please) from Conventional
Commits — there is no version to bump by hand.

- Land commits on `main` with conventional prefixes: `fix:` → patch, `feat:` → minor,
  `feat!:` (or a `BREAKING CHANGE:` footer) → major.
- A commit is attributed to a service by the package dir it touches — `ai_service/`
  or `whisper_api/`. Changes to shared paths (`docker/`, `pyproject.toml`, `tests/`)
  don't bump a service on their own; force one with a `Release-As: X.Y.Z` commit footer.
- release-please keeps a **Release PR per service** open on `main`. Merge a service's
  PR to cut it: that updates `<service>/version.txt` + `<service>/CHANGELOG.md` and
  creates the tag `ai-service-vX.Y.Z` / `whisper-api-vX.Y.Z`.
- The `release` workflow then builds **only** the just-released service and pushes it
  to `ghcr.io/<owner>/{ai-service,whisper-api}` (tags `X.Y.Z`, `X.Y`, `X`, a commit
  `sha`, and `latest` on non-prereleases).

Per-service versions are tracked in `.release-please-manifest.json` and
`release-please-config.json`. `[project].version` in `pyproject.toml` is only the
Python package build version and is not part of image releases.

## Deploy to Kubernetes (Helm)

A Helm chart for **ai-service** lives in [`deploy/helm/ai-service/`](deploy/helm/ai-service/)
(it consumes the GHCR image above). It deploys ai-service as a singleton — `replicas: 1`,
`Recreate`, one ReadWriteOnce PVC for the SQLite job queue — and exposes it through an Istio
`VirtualService` on the shared `istio-system/services-gateway` at a namespace-bound host
`ai-stt.<namespace>.aeroclub.int`. See the chart README for values and the `existingSecret` contract.

    helm install ai-stt deploy/helm/ai-service -n production -f my-values.yaml

`whisper-api` is not part of this chart; point `config.WHISPER_API_URL` at its in-cluster Service.

## Develop

    python3 -m venv .venv
    .venv/bin/pip install -e .[dev]
    .venv/bin/pytest            # fast suite (no model download)
    .venv/bin/pip install faster-whisper
    .venv/bin/pytest -m slow    # real tiny-model tests

Docker images install dependencies from a committed `uv.lock` with
`uv sync --frozen`, so builds are reproducible and a code-only change reuses
the cached dependency layer. **After changing dependencies in
`pyproject.toml`, regenerate the lock** (otherwise `--frozen` fails the build):

    docker run --rm -v "$PWD":/app -w /app --entrypoint sh \
      python:3.11-slim -c "pip install -q uv && uv lock"

### WER accuracy test

`tests/whisper_api/test_wer.py` measures Word/Char Error Rate of the production
model against real speech clips. The word-level helper tests run in the fast
suite; the real-transcription test is `slow` and auto-discovers `<name>.mp3` +
`<name>.txt` (UTF-8 transcript) pairs under `tests/fixtures/wer/`, skipping when
none are present. It **reports** WER/CER (run with `-s` to see them) — no gate.

Run it in the `whisper-api` container for environment parity (CUDA + cuDNN +
faster-whisper are already installed, `large-v3` on `cuda`/`float16`, and the
model cache is reused). Two wrinkles: `.dockerignore` keeps `tests/` out of the
image, so mount it at runtime; and the image is built `--no-dev`, so layer the
dev deps on with `uv run` (needs network to fetch pytest/jiwer):

    # uncomment the GPU `deploy:` block in docker-compose.yml first
    docker compose --profile local-whisper run --rm -v "$PWD/tests:/app/tests" whisper-api \
      uv run --extra api --extra dev pytest tests/whisper_api/test_wer.py -m slow -s

To run locally instead (CPU `large-v3`, much slower), install faster-whisper
into the venv (`.venv/bin/pip install faster-whisper`) and edit the `DEVICE` /
`COMPUTE_TYPE` constants at the top of the test if needed.

To test an **external** whisper-api reached over its URL instead (e.g. a GPU
host), set `WHISPER_API_URL` (base URL including `/v1`) and run the remote
variant — no local model is loaded:

    WHISPER_API_URL=https://gpu-host/v1 WHISPER_API_KEY=... \
      .venv/bin/pytest tests/whisper_api/test_wer.py -k remote -m slow -s

Optional overrides: `WHISPER_MODEL` (default `large-v3`), `LANGUAGE` (default
`ru`), `WHISPER_TIMEOUT_SECONDS` (default `600`).
