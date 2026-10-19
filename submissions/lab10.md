# QuickTicket Reliability Review

I ran Locust from Kubernetes Jobs inside `k3d-quickticket` on 12 October 2026. Each run lasted 60 seconds. I cleared Redis holds before each run, so an earlier test did not use the next test's seats. The load script is `locustfile.py` at the repo root. Its reserve requests use events 3 and 5. Raw Job logs, Job YAML, and CPU snapshots are in [evidence/lab10](evidence/lab10).

## Task 1 — 10.5: Reliability review

### 1. SLO Compliance

The Prometheus rule uses **99.5% availability** (5xx budget: 0.5%). For this review I also use the lab's **p99 below 500 ms** capacity limit. A `409 Conflict` means no seats are left, so I do not count it as a server error.

| Test | 5xx rate | p99 | Result |
|---|---:|---:|---|
| 10 users | 0% | 19 ms | Pass |
| 20 users | 0% | 20 ms | Pass |
| 30 users | 0% | 31 ms | Pass |
| 40 users | 0% | 92 ms | Pass; 22 expected 409s |
| 50 users | 0.78% | 1,100 ms | Fail on both limits |
| 100 users | 35.49% HTTP 5xx | 5,000 ms | Severe failure; also 161 connection errors |

These are one-minute test results, not a monthly SLO score. I cannot claim a monthly SLO from this short test.

### 2. Load Test Results — 10.1 and 10.2

The generator ran in the cluster against `http://gateway:8080`. This lets the Service spread requests across all five gateway pods. A host `port-forward` would have selected one pod.

#### 10.1: Run Locust at three load levels

| Users | Ramp | Requests | RPS | p50 | p95 | p99 | HTTP 5xx / rate | 409 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 460 | 7.72 | 7 ms | 9 ms | 19 ms | 0 / 0% | 0 |
| 20 | 2/s | 891 | 14.92 | 7 ms | 10 ms | 20 ms | 0 / 0% | 0 |
| 30 | 3/s | 1,335 | 22.35 | 7 ms | 17 ms | 31 ms | 0 / 0% | 0 |
| 40 | 4/s | 1,781 | 29.81 | 8 ms | 25 ms | 92 ms | 0 / 0% | 22 |
| **50** | **5/s** | **2,043** | **34.19** | **8 ms** | **710 ms** | **1,100 ms** | **16 / 0.78%** | **28** |
| 100 | 10/s | 2,508 | 42.04 | 780 ms | 3,700 ms | 5,000 ms | 890 / 35.49% | 0 |

At 50 users, the 16 server errors were 8 HTTP 500, 6 HTTP 502, and 2 HTTP 503. Locust also counted 28 HTTP 409 responses as task failures, so its total `44 fails` is **not** the SLO error count. At 100 users, Locust recorded 1,051 failed requests: 890 HTTP 5xx and 161 connection refusals. The connection refusals are also real user failures, but they are not HTTP 5xx. A Locust Job with any failed request exits 1; the `Failed` Job status for 40 users came only from expected 409s.

#### 10.2: Find the breaking point

**First measured breaking point:** **50 users, 34.19 RPS**. The latest measured passing point was **40 users, 29.81 RPS**. The exact threshold lies between these loads. I did not run 200 users because 50 already exceeded both failure limits and 100 caused much larger failures. This test mix does not call `/pay`, so it does not prove payment capacity.

### 3. DORA Metrics — 10.3

#### 10.3: Calculate DORA metrics

I used local Git history, the gateway ReplicaSets, two AnalysisRuns, and the timed Lab 9 recovery tests. `main` has 34 commits and the current `feature/lab8` history has 41; those totals are **not** deployment counts. The gateway has five retained ReplicaSets, all created on 4 October. The AnalysisRuns show one `Successful` and one `Failed` canary.

| Metric | Result | Source and limit |
|---|---|---|
| Deployment frequency | 1 confirmed successful analyzed canary in the observed 4 October test session; 5 retained ReplicaSets | `kubectl get rs -l app=gateway` and `kubectl get analysisruns`. A ReplicaSet can come from a test or retry, so 5 is not 5 successful deployments. This short session cannot give a weekly rate. |
| Lead time for changes | **Not measurable** | `kubectl -n argocd get application quickticket` returned `NotFound`, so there is no Argo CD sync timestamp to pair with a Git commit. The lab's planned estimate is CI build time plus up to 3 minutes for Argo CD polling, but CI build time was not measured. |
| Change failure rate | **1 / 2 = 50%** for analyzed canaries | One `Successful` and one `Failed` AnalysisRun. This is a tiny sample, not a long-term team rate. |
| Time to restore service | **66.5 seconds average** for two measured DB incidents | Lab 9 end-to-end RTOs: `(70.6 + 62.4) / 2`. This is an incident recovery proxy. The earlier canary abort was described as seconds in Lab 7, but no exact abort timer was saved, so I do not call 66.5 seconds a deployment MTTR. |

The missing Argo CD Application is itself a release-process risk. Before using these metrics for a real team, I would store CI start/end, commit SHA, Argo CD sync time, and a first-good-request time for each release.

### 4. Top 3 Reliability Risks

1. **One Postgres pod and a local PVC.** The PVC saved all 50 orders during a pod restart, but a lost node or volume could still require the latest dump. I would test restore often, add WAL archiving, and use replicated or managed Postgres for real users.
2. **Slow recovery after DB restart.** Postgres accepted connections in 3.2 seconds, but the API needed 62.4 seconds. `events` had stale connections and gateway pods lost readiness. I would add a real Postgres readiness probe, reconnect logic in `events`, and a synthetic checkout probe.
3. **One Redis pod with temporary holds.** Lab 8 showed `/events` could work while `/reserve` failed and `/health` still looked healthy. I would add Redis readiness to the service health check and use a replicated Redis setup if holds must survive a pod loss.

### 5. Toil Identification — 10.4

#### 10.4: Identify three pieces of toil

| Manual task seen in Labs 1–9 | How often | Automation | Time saved |
|---|---|---|---|
| Run `psql` to check tables and row counts after DB changes | More than 8 checks in Lab 9 | A small smoke script that checks schema, counts, and an API request | About 1 minute per check; fewer missed checks |
| Make and inspect dumps by hand | 1 disaster dump plus 7 manual Bonus runs | The five-minute `postgres-backup` CronJob, backup-age alert, and a scheduled restore test | Several commands each run; about 5–10 minutes per week in this lab setup |
| Repeat `kubectl get`, logs, and `curl` while services recover | More than 10 checks in Labs 8–9 | Prometheus alerts for reserve errors, p99, pod readiness, and a synthetic checkout test | About 5 minutes of first-response work per incident |

I would keep manual checks for unusual incidents. Automation should make routine checks repeatable and give a clear alert when they fail.

### 6. Monitoring Gaps

- In Lab 8, Redis was down but gateway `/health` still said healthy. A reserve-path synthetic request and a Redis dependency status would have caught the broken user flow.
- A slow payment could keep returning success, so a 5xx alert alone would miss it. Alert when payment p99 stays above 500 ms for five minutes.
- The current Lab 6 high-error alert used 5% for two minutes. The 50-user run failed the 0.5% budget at 0.78%, so that alert could miss this level. Add an SLO burn alert that acts before the budget is used.
- During the 100-user test, gateway and events pods restarted. Show restart count, ready endpoints, DB pool wait time, and connection failures on one dashboard.
- Check backup age and the result of a test restore, not only the last CronJob status.

### 7. Capacity Plan — 10.7 and 10.8

#### 10.7: Measure per-pod headroom

**Measured current limit:** 29.81 RPS passed at 40 users; the first failing test was 34.19 RPS at 50 users. I use **29.81 RPS** as the last proven safe level, so 2× traffic means about **59.62 RPS** for this same request mix. This is a plan to test, not a proven new capacity.

At the 50-user breaking test, `kubectl top pods` showed gateway pods at **5, 6, 10, 11, and 16 mCPU**, `events` at **49 mCPU**, and `payments` at **5 mCPU**. Events used the most CPU; payments was nearly idle. None reached its 200 mCPU limit. The sharp p99 rise may be due to DB or connection waiting, so adding CPU alone is not a safe fix. At 100 users, gateway pods used 10–39 mCPU, events 45 mCPU, and payments 7 mCPU, yet errors were much worse.

#### 10.8: Plan for 2× traffic

| Component | Now | 2× plan | Request / limit per planned pod |
|---|---:|---:|---|
| Gateway | 5 | 5 | 50m / 200m CPU; 64 / 256 MiB |
| Events | 1 | 3 | 100m / 200m CPU; 128 / 256 MiB |
| Payments | 1 | 2 | 50m / 200m CPU; 64 / 256 MiB |
| Redis | 1 | 3 with failover | 50m / 200m CPU; 64 / 256 MiB |
| PgBouncer | 0 | 2 | 25m / 100m CPU; 64 / 128 MiB |
| Postgres | 1 | 1, with a larger managed instance for production | 200m / 500m CPU; 256 / 512 MiB in a local test |

Events currently opens a direct pool to one Postgres pod (`DB_MAX_CONNS=20`). Three such replicas could hold 60 connections before other clients join. I would cap each events pool at 10 and put two PgBouncer pods in front of Postgres, then measure DB waits and locks. The single Postgres remains a failure and throughput risk; real 2× traffic needs a DB load test and possibly a larger primary. Redis can carry more traffic as one pod, but it is still a single failure point, so the plan uses three pods with failover for reliability.

At the lab's rough **$5 per pod per month**, the five application/data services now use **9 pods ≈ $45/month**. The plan uses **16 pods ≈ $80/month**, about **$35/month more**. This excludes PVC storage, backups, monitoring, network, and the extra cost of a managed database. I would run the same Locust mix at 60 RPS before claiming the plan works.

## Task 2 — Capacity plan with numbers

The per-pod CPU readings, idle service, replica counts, resource requests and limits, DB/Redis choices, and cost are in section 7. The binding limit was **not proven to be CPU**; the latency and 5xx jump point to the events/DB path and gateway readiness as the first investigation targets.

## Bonus Task — SRE handbook

I chose Option B. The [QuickTicket SRE handbook](runbooks/quickticket-handbook.md) includes an architecture diagram, the GitOps deployment flow, monitoring checks, incident steps, escalation, and backup/restore steps. It is written for a new teammate.

## 10.6: Proof of work

The table for 10/50/100 and intermediate/breaking loads, DORA source data, three risks, toil table, monitoring gaps, and 2× plan are above. The raw Locust logs and CPU samples are saved in [evidence/lab10](evidence/lab10).
