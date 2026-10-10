*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 10 - SRE Portfolio and Reliability Review

## Repository state

This branch contains the Lab 10 portfolio artifacts on top of the merged Lab 9 base:

- `locustfile.py` was copied to the repository root
- `submissions/lab10.md` was added
- `submissions/runbooks/quickticket-handbook.md` was added for Bonus Option B

The same local runtime limitation from Labs 7-9 still existed: the cluster runtime could not pull the private `ghcr.io/deldore/...` images directly, so for local verification I imported the current application images into `k3d` and temporarily applied a live-only copy of `k8s/` with `imagePullSecrets` removed and `imagePullPolicy: IfNotPresent`. I did not keep those runtime-only workarounds in the final repo diff.

One more local-specific limitation affected the DORA section: the current k3d cluster was rebuilt during the course work, so the live Argo Rollouts state retains only the latest gateway ReplicaSets and one successful AnalysisRun. For frequency and change-history numbers I therefore used Git history as the durable source of truth and cross-checked recovery examples against the earlier lab submissions.

## Setup

I started from the merged Lab 9 state on `main`, copied the provided Locust scenario into the repo root, loaded it into a ConfigMap, and removed the old Lab 8 `mixedload` so it would not pollute the capacity numbers:

```bash
kubectl delete -f labs/lab8/mixedload.yaml --ignore-not-found

kubectl create configmap locustfile \
  --from-file=locustfile.py=locustfile.py \
  --dry-run=client -o yaml | kubectl apply -f -
```

```text
deployment.apps "mixedload" deleted
configmap/locustfile created
```

Before each Locust run I flushed Redis exactly as required by the lab:

```bash
kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
```

The load-test scenario used the shipped reserve split across events `3` and `5`, whose initial ticket pools were:

```text
 id |         name         | total_tickets
----+----------------------+---------------
  3 | Cloud Native Summit  |           500
  5 | Kubernetes Deep Dive |            80
(2 rows)
```

## QuickTicket Reliability Review

### 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (5xx on user paths) | `< 0.5%` | `0.00%` at 10u, `47.96%` at 50u, `72.73%` at 100u | Pass at 10u, fail at 50u+ |
| Latency (gateway p99) | `< 500ms` | `85ms` at 10u, `2400ms` at 50u, `5600ms` at 100u | Pass at 10u, fail at 50u+ |
| Stateful recovery | `< 1h` | `52s` without PVC, `25s` with PVC (Lab 9) | Pass |

Interpretation:

- The system is healthy at the smallest tested tier.
- It falls off a cliff between `10u` and `50u`.
- The Lab 9 PVC work fixed the most painful stateful-service outage mode, but the request path still saturates far earlier than I would accept for production.

### 2. Load Test Results

I ran Locust **inside the cluster** as Kubernetes Jobs so traffic went through `gateway:8080` and kube-proxy load-balanced it across all 5 replicas.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx/system-failure rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|------------------------:|----------------:|
| 10    | 2/s  | 7.61  | 17ms   | 30ms   | 85ms   | 0.00%  | 0 |
| 50    | 5/s  | 20.65 | 980ms  | 2000ms | 2400ms | 47.96% | 0 |
| 100   | 10/s | 30.01 | 2000ms | 4300ms | 5600ms | 72.73% | 0 |

Breaking point:

- First threshold breach happened already at **50 users**
- Breaking-point throughput: **20.65 RPS**
- Why it counts: `p99 = 2400ms > 500ms` and `5xx/system failures = 47.96% > 0.5%`

Supporting excerpts:

`10u`:

```text
         Aggregated                                                                       455     0(0.00%) |     18       7     102     17 |    7.61        0.00
         Aggregated                                                                             17     18     19     19     23     30     58     85    100    100    100    455
```

`50u`:

```text
         Aggregated                                                                      1226  588(47.96%) |   1023       2    2815    980 |   20.65        9.90
         Aggregated                                                                            980   1200   1400   1500   1800   2000   2200   2400   2800   2800   2800   1226
```

`100u`:

```text
         Aggregated                                                                      1797 1307(72.73%) |   1795       4    6398   2000 |   30.01       21.83
         Aggregated                                                                           2000   2000   2100   2500   3800   4300   5100   5600   6400   6400   6400   1797
```

The `409` column stayed at `0` in these 60-second windows. The reserve split across events 3 and 5 did its job: inventory contention did not dominate the measurement, and the failures I saw were real system failures (`500`, `502`, `503`, `504`, plus a few connection refusals during overload), not sold-out behavior.

### 3. DORA Metrics

| Metric | Observed | Source / reasoning |
|--------|----------|--------------------|
| Deployment Frequency | `7` merged delivery PRs on `main`; roughly `~0.8 deploys/week` over the lab sequence | `git log --oneline --merges main | wc -l` |
| Lead Time for Changes | approximately `6-10 minutes` | CI image build/push + image-tag update commit + ArgoCD poll interval approximation from the lab workflow |
| Change Failure Rate | approximately `14.3%` | I count the documented Lab 7 aborted rollout as one failed deployment event across `7` merged delivery changes |
| Recovery Time | `7 seconds` for rollout abort; `25 seconds` for PVC-backed DB restart; `52 seconds` for non-PVC DB recovery | Lab 7 abort timeline and Lab 9 disaster-recovery timelines |

Source data:

```text
git log --oneline --merges main
49db3f5 Merge pull request #2 from Deldore/feature/lab2
6b6f1db Merge pull request #4 from Deldore/feature/lab4
f2b83a9 Merge pull request #5 from Deldore/feature/lab5
adae7fd Merge pull request #6 from Deldore/feature/lab6
2d14519 Merge pull request #7 from Deldore/feature/lab7
6ad7565 Merge pull request #8 from Deldore/feature/lab8
7cec0f6 Merge pull request #9 from Deldore/feature/lab9
```

```text
git log --oneline main | wc -l
52
```

```text
kubectl get analysisrun -o jsonpath='{.items[*].status.phase}' | tr ' ' '\n' | sort | uniq -c
      1 Successful
```

Why I did not use the current cluster alone for all DORA numbers:

- The local k3d cluster was rebuilt during verification, so it only retains the latest gateway history.
- Earlier rollout failures and recovery timings are still honestly documented in the previous submissions, so those reports are the durable source for CFR/MTTR examples.

### 4. Top 3 Reliability Risks

1. **Single Postgres bottleneck and SPOF on the hot path.**  
   At the 50-user breaking point, Postgres was the most loaded component I sampled (`189m` CPU) while the whole request path was already producing gateway errors and timeouts. A single database pod without pooling leaves very little headroom. I would add PgBouncer, raise Postgres resources, and eventually move to a more production-like managed or replicated PostgreSQL setup.

2. **The service degrades sharply instead of gracefully between 10u and 50u.**  
   There is no soft landing here: `10u` is clean, but `50u` already gives `47.96%` failures and `p99=2400ms`. That means there is no meaningful safety margin. I would add load-shedding, explicit queue/backpressure strategy, and capacity-driven scaling for `events` and Postgres before adding more traffic.

3. **Health and dependency errors still surface as user-visible gateway failures.**  
   Under load, the system generated `500/502/503/504` rather than isolating the failing dependency. I already saw the same pattern in Lab 8 during Redis and payments experiments. I would decouple health checks from hard dependency failures where possible and add more targeted failure handling instead of letting the gateway collapse into generic upstream errors.

### 5. Toil Identification

| Toil | How often I did it | How I would automate it | What it would save |
|------|---------------------|--------------------------|--------------------|
| Re-seeding Postgres after state loss or fresh rollout | At least 4 times across Labs 4, 7, 8, 9 before PVC | Init Job or migration/bootstrap Job that verifies base tables and seed data automatically | 5-10 minutes and many manual `kubectl exec ... < app/seed.sql` runs |
| Re-creating port-forwards for Prometheus or Postgres | Repeated across Labs 7, 9, and local verification reruns | Use in-cluster Jobs for DB work and a fixed helper script for Prometheus access | Fewer broken sessions and less manual reconnection work |
| Manually watching / promoting / aborting rollouts | Several times in Lab 7 and again during local verification | Rely on AnalysisTemplate gates and a tiny helper wrapper for rollout status/promote | Less operator babysitting during delivery and less chance of forgetting a paused rollout |

### 6. Monitoring Gaps

- I still do not have a latency alert that would fire **before** the system turns into obvious 5xx. In this lab the p99 threshold was already blown at `50u`.
- I do not have a first-class database saturation alert. Given the CPU snapshot, Postgres should page or at least warn well before gateway users start seeing `502/504`.
- I do not have a dashboard that cleanly separates business conflicts (`409`) from platform failures (`5xx`). This lab stayed at `0` for `409`, but the distinction matters and should be visible by default.

The alert I most wished I had during this lab:

- `gateway_request_duration_seconds` p99 > `500ms` for 2-5 minutes on user paths

The second alert I would add immediately:

- Postgres CPU high or connection saturation approaching pool limits

### 7. Capacity Plan

Current ceiling:

- **Breaking point:** `50u / 20.65 RPS`
- **Clearly healthy tier:** `10u / 7.61 RPS`

Per-pod CPU at the breaking-point rerun (`50u`):

```text
NAME                       CPU(cores)   MEMORY(bytes)
gateway-76b8589d76-dvjfn   46m          50Mi
gateway-76b8589d76-kt96f   46m          39Mi
gateway-76b8589d76-zjt6v   103m         40Mi

NAME                      CPU(cores)   MEMORY(bytes)
events-868556c986-k4cqp   136m         70Mi

NAME                        CPU(cores)   MEMORY(bytes)
payments-775d9fd94f-wvljq   21m          36Mi

NAME                        CPU(cores)   MEMORY(bytes)
postgres-68466c5ccd-2jkt7   189m         44Mi
```

Interpretation:

- `postgres` is the hottest component in the sampled overload state
- `events` is the hottest stateless service
- `payments` is comparatively idle
- `gateway` replicas are busy but not the primary bottleneck

Plan for roughly **2× the current ceiling** (`~41 RPS` target):

| Component | Current | Proposed | Requests / Limits | Why |
|-----------|---------|----------|-------------------|-----|
| gateway | 5 replicas | 6 replicas | `100m/96Mi` requests, `250m/256Mi` limits | modest headroom and HA; not the first bottleneck |
| events | 1 replica | 3 replicas | `150m/128Mi` requests, `400m/256Mi` limits | this service was materially loaded at the breaking point |
| payments | 1 replica | 2 replicas | `50m/64Mi` requests, `150m/128Mi` limits | mostly HA and some burst tolerance; CPU was low |
| postgres | 1 pod | 1 stronger pod + PgBouncer | DB pod at least `500m/512Mi` requests, `1000m/1Gi` limits | current single DB pod is already the hottest component |
| redis | 1 pod | keep 1 for now, plan replica/Sentinel if HA matters | current sizing acceptable | it did not appear CPU-bound in this lab |

DB connections:

- With `events` at 3 replicas, I would not simply multiply direct Postgres connections.
- I would add PgBouncer and reduce per-pod application pool sizes so the database is protected from connection storms.

Rough cost estimate (`$5/pod/month` assumption):

- gateway `6`
- events `3`
- payments `2`
- postgres `1`
- redis `1`
- prometheus `1`
- argo-rollouts `1`

Total:

- `15 pods * $5 ≈ $75/month`

If I also add a PgBouncer pod and a Redis replica for HA, the rough total rises to about:

- `17 pods * $5 ≈ $85/month`

Practical conclusion:

- I would **not** promise 2× traffic just by scaling gateway.
- The first real money should go into the `events -> Postgres` path, because that is where the system showed the clearest stress signal.

## Bonus Option B - SRE handbook

I chose Bonus **Option B** and added:

- `submissions/runbooks/quickticket-handbook.md`

That file contains the architecture diagram, deployment flow, monitoring guidance, incident response summary, and condensed backup/restore runbook requested by the lab.
