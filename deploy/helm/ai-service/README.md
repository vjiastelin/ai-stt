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

## Storage is node-local — pin the pod

The `local-path` PV is a hostPath directory (`/opt/local-path-provisioner/<pv>_<ns>_<pvc>`) on the
single node that first ran the pod. That binding is **not enforced by the scheduler**: the PV's
`nodeAffinity` uses `matchFields: metadata.name`, which the volume-binding check ignores for
hostPath PVs. If the pod is rescheduled elsewhere, kubelet (`type: DirectoryOrCreate`) silently
creates a fresh, empty, `root:root 0755` directory on the new node — so the pod either starts on an
**empty job queue** or, because it runs as uid 1000, dies at startup with:

```
sqlite3.OperationalError: unable to open database file
```

So `nodeSelector.kubernetes.io/hostname` must name the node that holds the PV — the same mechanism
the cluster's other local-path workloads use (`ai-portal-pg` → `mow2ksw20`, `ai-portal` →
`mow2ksw22`). Find the node:

```bash
kubectl get pv $(kubectl -n <ns> get pvc ai-stt-ai-service -o jsonpath='{.spec.volumeName}') \
  -o jsonpath='{.metadata.annotations.local\.path\.provisioner/selected-node}'
```

To move the service to another node, copy `jobs.db` from that directory to the new node first —
otherwise queued jobs are left behind (`helm` will happily start with an empty DB).

No `chown` initContainer is needed: the provisioner creates its directory `0777`, so the non-root
container can write it as long as the pod is on the right node.

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
`istio-system/services-gateway` — the convention for plain (non-Knative) HTTP services in the
cluster. It deliberately does **not** use the Knative gateways (`knative-ingress-gateway` /
`knative-local-gateway` / `mesh`): those are auto-generated for Knative `ksvc` workloads, which a
SQLite singleton must not be.

The host is **namespace-bound**: when `istio.virtualService.hosts` is empty (the default) the chart
derives `ai-stt.<release-namespace>.aeroclub.int`, so one values file yields `ai-stt.beta.aeroclub.int`
in `beta` and `ai-stt.production.aeroclub.int` in `production`. Set `istio.virtualService.hosts`
explicitly to override (e.g. a namespace-less vanity host `ai-stt.aeroclub.int`). Add `- mesh` to
`istio.virtualService.gateways` only if in-cluster services need to reach ai-service by that host
(not needed when callers are external). Routing is under path `/`.

No `Gateway` or `DestinationRule` is created (mTLS is permissive; the cluster uses no
DestinationRules). External DNS for the chosen host must point at the Istio ingress LB. Set
`istio.virtualService.enabled=false` for a ClusterIP-only deployment.

## Monitoring

ai-service serves Prometheus metrics at `GET /metrics` on the same port as the API (8080).
The chart creates a `ServiceMonitor` (`metrics.serviceMonitor.enabled`, default `true`) so
kube-prometheus-stack picks the target up. Selection happens in three hops, and each one
fails **silently** — no error, just a missing target:

1. **Prometheus → ServiceMonitor.** The Prometheus CR selects ServiceMonitors by
   `matchLabels: {release: prometheus-stack}`, so the object carries that label
   (`metrics.serviceMonitor.releaseLabel`). Its namespace selector is empty cluster-side, so
   any namespace works.
2. **ServiceMonitor → Service.** `spec.selector` matches the **Service's `metadata.labels`**
   (not its `spec.selector`, not pod labels). The chart uses only the selector labels
   `app.kubernetes.io/name` + `app.kubernetes.io/instance` — deliberately *not* the full label
   set, since `helm.sh/chart` and `app.kubernetes.io/version` change on every bump and would
   break the match.
3. **Service → targets.** The operator scrapes the **pod IPs behind the Service's Endpoints**,
   not the ClusterIP. `endpoints[].port` is the port *name* (`http`).

The resulting `job` label is the Service name (`<release>-ai-service`), which is what the
Grafana dashboard variables and the `AiServiceDown` alert key on.

The **dashboard and alert rules are not part of this chart.** They are Grafana-managed
resources on `grafana-ai.aeroclub.int` (dashboard uid `ai-stt`, folder `AI Services`),
versioned in the `services-ai-grafana` repo — the cluster convention for both. See
[`docs/metrics.md`](../../../docs/metrics.md).

Verify without a cluster:

```bash
helm template ai-stt deploy/helm/ai-service -n beta -s templates/servicemonitor.yaml
```

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
| `metrics.serviceMonitor.enabled` | `true` | creates the ServiceMonitor; without it nothing scrapes `/metrics` |
| `metrics.serviceMonitor.releaseLabel` | `prometheus-stack` | must match the Prometheus CR's `serviceMonitorSelector` |
| `metrics.serviceMonitor.interval` | `30s` | matches the cluster's global `scrapeInterval` |
| `istio.virtualService.enabled` | `true` | |
| `istio.virtualService.gateways` | `[istio-system/services-gateway]` | shared gateway, referenced not created |
| `istio.virtualService.hosts` | `[]` | empty ⇒ derived `ai-stt.<namespace>.aeroclub.int`; set to override |
| `istio.virtualService.pathPrefix` | `/` | |
| `istio.virtualService.rewriteUri` | `""` | set to `/` when routing under a path prefix |
| `persistence.storageClass` | `local-path` | pinned; NFS is unsafe for SQLite |
| `persistence.size` | `1Gi` | |
| `persistence.existingClaim` | `""` | reuse a PVC instead of creating one |
| `nodeSelector` | `kubernetes.io/hostname: mow2ksw24` | **required with local-path** — the PV is node-local; see below |
| `config.WHISPER_API_URL` | `http://whisper-api:8000/v1` | in-cluster whisper-api Service |
| `config.BPM_CALLBACK_URL` | example | **must** be set to your BPM endpoint |
| `secrets.existingSecret` | `""` | reference a pre-created Secret |

See [`values.yaml`](./values.yaml) for the full list and inline comments.

## Validate without a cluster

```bash
helm lint deploy/helm/ai-service
helm template ai-stt deploy/helm/ai-service -n production
```
