# Lab 7 — Progressive Delivery

Date: 2026-10-05. Author: Walkerino. All timestamps below are UTC (add three
hours for Moscow). Experiments ran on the isolated `k3d-quickticket-lab78`
cluster, Kubernetes v1.33.6-k3s1. ArgoCD self-heal was not active in this
cluster, so it could not undo experiment changes. The existing Lab 5 raw
manifests supply the prerequisite services; no previous lab reports are
included in this PR.

## Task 1 — Manual canary

Converted [gateway.yaml](../k8s/gateway.yaml) from Deployment to Rollout,
with five replicas and a Service selector `app: gateway` shared by both
ReplicaSets. Gateway readiness checks the TCP listener: dependency health
is still visible at `/health`, but a faulty canary must receive traffic so
its error rate can be measured. Liveness/startup probes remain TCP-based.
The same immutable image tag is used with different `APP_VERSION` values;
this changes the pod template and creates a real canary revision.

Commands and full output: [session.txt](evidence/lab7/session.txt).

```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/download/v1.9.0/install.yaml
kubectl argo rollouts version
kubectl delete deployment gateway
kubectl apply -f k8s/gateway.yaml
kubectl apply -f labs/lab7/loadgen.yaml
# Change APP_VERSION from v1 to v2; apply gateway.yaml.
kubectl argo rollouts get rollout gateway
kubectl argo rollouts promote gateway
# Change APP_VERSION to v3-bad and EVENTS_URL to broken-on-purpose; apply.
kubectl argo rollouts abort gateway
```

[Version output](evidence/lab7/version.txt):

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
Platform: darwin/arm64
```

Initial strategy:

```yaml
canary:
  maxSurge: 0
  maxUnavailable: 1
  steps:
    - setWeight: 20
    - pause: {}
    - setWeight: 60
    - pause: {duration: 30s}
    - setWeight: 100
```

At **08:05:29 UTC**, the [paused rollout](evidence/lab7/manual-20.txt) showed:

```text
Status: Paused
Step: 1/5
SetWeight: 20
ActualWeight: 20
Desired: 5  Current: 5  Updated: 1  Ready: 5
```

Traffic went through the in-cluster Service, not a port-forward. Logs counted
`GET /events` in the same trailing 30s window: stable pods **27, 24, 34, 20**;
canary **25**. Canary share was **25/130 = 19.23%**, consistent with the 20%
replica target. [Per-pod counts and hashes](evidence/lab7/traffic-split.json).
Counts were collected sequentially, so window edges differ slightly by CLI
runtime; this is a short illustrative sample, not exact traffic routing.

[After promotion](evidence/lab7/manual-60.txt), setWeight increased to 60,
with three updated replicas. The captured transition temporarily had four
ready replicas and ActualWeight 50; Kubernetes was still bringing up the
third canary. [Full promotion](evidence/lab7/manual-100.txt) reached Healthy,
setWeight 100 and five updated replicas. The transition demonstrates that
weights approximate ready replica proportions without a traffic router.

The bad revision used an unresolvable `EVENTS_URL`, causing real request
failures rather than just a cosmetic version label. [Before abort](evidence/lab7/bad-before-abort.txt),
then [after abort](evidence/lab7/manual-aborted.txt):

```text
Status: Degraded
Message: RolloutAborted: Rollout aborted update to revision 3
SetWeight: 0
ActualWeight: 0
Updated: 0
```

**Abort timing:** [2.161 seconds](evidence/lab7/abort-timing.json) from invoking
abort until the controller reported zero updated canary replicas; serving
replicas were stable, with the fifth stable replica still becoming ready.
This measures controller convergence, not the last packet or kube-proxy
propagation. Lab 5 measured **5.982 seconds** for Git revert, push, hard refresh
and sync. Abort is faster here because it reuses the existing stable
ReplicaSet, but a durable GitOps rollback also requires correcting the desired
manifest in Git. These are local measurements with different boundaries,
not a general speed guarantee.

## Task 2 — Multi-step strategy and observation

```yaml
steps:
  - setWeight: 20
  - pause: {duration: 60s}
  - setWeight: 40
  - pause: {duration: 60s}
  - setWeight: 60
  - pause: {duration: 60s}
  - setWeight: 80
  - pause: {duration: 30s}
  - setWeight: 100
```

The runner applied this strategy with `APP_VERSION=v4-multistep`. It captured
[actual --watch output](evidence/lab7/multistep-watch.txt) and Prometheus
queries at each paused step:

| UTC timestamp | Target weight | Updated replicas | Gateway RPS |
|---|---:|---:|---:|
| 08:07:07 | 20% | 1 | 8.11 |
| 08:08:18 | 40% | 2 | 8.76 |
| 08:09:22 | 60% | 3 | 8.66 |
| 08:12:54 | 80% | 4 | 8.80 |
| 08:13:13 | 100% | 5 | 8.77 |

Raw snapshots: [20%](evidence/lab7/multistep-1.json),
[40%](evidence/lab7/multistep-3.json), [60%](evidence/lab7/multistep-5.json),
[80%](evidence/lab7/multistep-7.json), [100%](evidence/lab7/multistep-100.json).
Pause duration is a minimum observation time; image pulls and readiness
convergence add time between steps. Traffic stayed approximately steady.

An [in-cluster Grafana configuration](../monitoring/lab7/grafana.yaml) points
at the same Prometheus used by analysis. The dashboard shows request rate,
5xx ratio, scrape health, per-path p99 and per-pod rate.
[Captured dashboard](evidence/lab7/grafana-multistep.png),
[actual provisioned dashboard API output](evidence/lab7/grafana-dashboard.json).
The screenshot's trailing 15m window includes the earlier intentional manual
bad deployment as well as the granular rollout, so an error spike in that
history must not be attributed to the healthy multi-step version.

I would abort at the first **20%** analysis gate if the canary error ratio
exceeded 5% under enough traffic. Increasing exposure before investigating
errors expands the blast radius; the comparison should use canary-specific
metrics rather than the aggregate, which dilutes one bad replica with four
good ones.

## Bonus — Automated analysis

[AnalysisTemplate](../k8s/analysis-template.yaml) queries only the latest
canary's `rs_hash`, copied from `rollouts-pod-template-hash` by the provided
Prometheus relabeling. A 60s initial delay permits discovery and scraping.
The zero numerator fallback handles absence of any 5xx series; the strict
denominator leaves absent traffic as an error/NaN rather than successful
analysis. Three measurements are requested at 20s intervals, over overlapping
60s windows. Two failed measurements exceed `failureLimit: 1`; these are not
three independent or necessarily consecutive failures.

The final manifest uses 20% → analysis → 60% → 100%, with 20s pauses.
Actual results ([AnalysisTemplate output](evidence/lab7/analysis-template.txt),
[full AnalysisRun YAML](evidence/lab7/analysis-both.yaml)):

```text
NAME                     STATUS
gateway-5b45bc8f9f-6-2   Successful
gateway-8567c9fdbf-7-2   Failed
```

- Good canary `v5-auto-good`: measurements at 08:14:39, 08:14:59 and
  08:15:19 UTC were all `[0]`; [Healthy at 100%](evidence/lab7/auto-good.txt)
  at 08:15:59 UTC, without manual promotion.
- Bad canary `v6-auto-bad`: measurements at 08:17:24 and 08:17:44 UTC were
  both `[1]`. [Automatic abort](evidence/lab7/auto-bad.txt) at 08:17:45 UTC
  reported `failed (2) > failureLimit (1)`, Degraded, actualWeight 0 and zero
  updated replicas. In this application unresolved events calls return 502
  and health returns 503, rather than the timeout-based 504 in the lab example.
- Restoring the healthy pod template completed at 08:17:50 UTC:
  [final rollout](evidence/lab7/final.txt) Healthy, five ready stable replicas.
  The load generator was deleted; Prometheus and Grafana remain for Lab 8.

I would also analyze per-endpoint p99 latency, with a minimum request count
and a threshold tied to a latency SLO. A slow but successful payment can
consume the latency budget without producing any 5xx.

## Reproduction and validation

See [k8s/README.md](../k8s/README.md) and [run-lab7.py](../scripts/run-lab7.py).
The experiment runner captures live CLI output and timestamped JSON. Keep
fault injection confined to its checked context. The supplied PostgreSQL
storage is ephemeral; the bootstrap seeds only a fresh database.

Installation reference: [official Argo Rollouts installation](https://argoproj.github.io/argo-rollouts/installation/).

Validation passed: Kubernetes server-side dry-run for all application and
Grafana resources; `promtool check config` for the live in-cluster configuration;
Python compilation and shell syntax checks. Real successful/failed AnalysisRuns
and traffic observations provide the behavioral checks.

- [x] Task 1: manual canary, traffic split, promotion, bad revision and abort.
- [x] Task 2: 20/40/60/80/100 strategy, watch output, metrics and Grafana.
- [x] Bonus: automated success and failure with real measurement values.

Terminal captures have trailing padding removed; measurements and timestamps are unchanged.
