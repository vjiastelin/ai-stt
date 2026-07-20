# ai-service Helm chart

Deploys **ai-service** — the BPM-facing transcription orchestrator — to Kubernetes. `whisper-api`
and the LLM are treated as external endpoints, reached via `WHISPER_API_URL` / `LLM_API_URL`.

## Singleton by design

ai-service runs the FastAPI API **and** the background worker in one process, backed by a single
SQLite file (`DB_PATH=/data/jobs.db`). The chart therefore deploys it as a singleton:

- `replicas: 1` (hardcoded in the Deployment — not a value).
- `strategy: Recreate` (a ReadWriteOnce volume can't be mounted by two pods during a rollout).
- One ReadWriteOnce PVC mounted at `/data`.

**Do not scale up and do not add an HPA** — two pods sharing the SQLite file would double-process
and corrupt the queue.

## Install

```bash
# create a values file with your real S3 / BPM / LLM / whisper settings first
helm install ai-stt deploy/helm/ai-service -n production -f my-values.yaml
```

Upgrade / rollback:

```bash
helm upgrade ai-stt deploy/helm/ai-service -n production -f my-values.yaml
helm rollback ai-stt -n production
```

Target namespace `production` has `istio-injection=enabled`, so the Istio sidecar is injected
automatically. In a namespace without that label, add
`podAnnotations."sidecar.istio.io/inject": "true"`.

## Networking (Istio)

The chart creates a `VirtualService` (`networking.istio.io/v1`) bound to the shared cluster gateway
`istio-system/services-gateway`. By default it exposes host `ai-stt.aeroclub.int` under path `/`.
No `Gateway` or `DestinationRule` is created (mTLS is the mesh default; the cluster uses no
DestinationRules). Set `istio.virtualService.enabled=false` for a ClusterIP-only deployment.

## Secrets

Two modes:

- **Chart-managed (default):** put values under `secrets.data`. A `Secret` is rendered; empty keys
  are omitted.
- **Existing Secret:** set `secrets.existingSecret: <name>`. The chart renders no Secret and mounts
  yours instead. It must carry the keys `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and (when used)
  `LLM_API_KEY`, `WHISPER_API_KEY`.

Non-secret configuration lives under `config` and is rendered into a ConfigMap. Both the ConfigMap
and the Secret are checksummed into the pod template, so changing either triggers a rollout on
`helm upgrade`.

## Key values

| Key | Default | Notes |
|-----|---------|-------|
| `image.repository` | `ghcr.io/vjiastelin/ai-service` | |
| `image.tag` | `""` | falls back to chart `appVersion` |
| `imagePullSecrets` | `[]` | add a `dockerconfigjson` secret only if the GHCR package is private |
| `service.port` | `8080` | container listens on 8080 |
| `istio.virtualService.enabled` | `true` | |
| `istio.virtualService.gateways` | `[istio-system/services-gateway]` | shared gateway, referenced not created |
| `istio.virtualService.hosts` | `[ai-stt.aeroclub.int]` | |
| `istio.virtualService.pathPrefix` | `/` | |
| `istio.virtualService.rewriteUri` | `""` | set to `/` when routing under a path prefix |
| `persistence.storageClass` | `local-path` | pinned; NFS is unsafe for SQLite |
| `persistence.size` | `1Gi` | |
| `persistence.existingClaim` | `""` | reuse a PVC instead of creating one |
| `config.WHISPER_API_URL` | `http://whisper-api:8000/v1` | in-cluster whisper-api Service |
| `config.BPM_CALLBACK_URL` | example | **must** be set to your BPM endpoint |
| `secrets.existingSecret` | `""` | reference a pre-created Secret |

See [`values.yaml`](./values.yaml) for the full list and inline comments.

## Validate without a cluster

```bash
helm lint deploy/helm/ai-service
helm template ai-stt deploy/helm/ai-service -n production
```
