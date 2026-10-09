# QuickTicket Reliability Review

**Lab 10 — SRE Portfolio & Reliability Review**  
**Test date:** 2026-10-09  
**Environment:** local k3d Kubernetes cluster, Locust 2.43.4 running as in-cluster Jobs  
**Scope:** QuickTicket gateway, events, payments, PostgreSQL, Redis, Prometheus and Argo Rollouts

## Executive summary

The last observed load level satisfying **both Lab 10 breaking-point criteria** was **12 concurrent Locust users at 9.24 requests/second (RPS)**, with **p99 = 97 ms** and no HTTP 5xx. The first tested level that breached a criterion was **15 users**: two independent 60-second runs yielded **p99 = 700 ms and 930 ms** at **11.16 and 10.92 RPS**, respectively. Thus the *observed* breaking point is **15 users / approximately 11 RPS**, defined by tail latency rather than errors. This is not a statistically guaranteed capacity ceiling: the tests were short, and 13–14 users were not tested.

At 50 and 100 users, the service deteriorated markedly; liveness probe failures restarted gateway containers during heavy load. The review recommends separating liveness from dependency readiness, handling non-JSON downstream errors safely, and scaling only after measuring the actual bottleneck.

## 1. SLO Compliance

The lab's load-test acceptance thresholds are **HTTP 5xx rate <= 0.5%** and **aggregate p99 <= 500 ms** for a 60-second run. Earlier Lab 3 SLOs were **availability >= 99.5% (7 days)** and **at least 95% of requests below 500 ms**; the short Locust tests below do **not** establish seven-day compliance.

| Indicator | Target | Observed | Assessment |
|---|---|---|---|
| Load-test HTTP 5xx, 12 users | <= 0.5% | 0 / 550 = 0% | Pass |
| Load-test aggregate p99, 12 users | <= 500 ms | 97 ms | Pass |
| Load-test HTTP 5xx, 15 users (repeat) | <= 0.5% | 0 / 649 = 0% | Pass |
| Load-test aggregate p99, 15 users (repeat) | <= 500 ms | 930 ms | **Fail** |
| Load-test HTTP 5xx, 50 users (cleaned topology) | <= 0.5% | 466 / 1,347 = 34.60% | **Fail** |
| Load-test aggregate p99, 50 users | <= 500 ms | 5,000 ms | **Fail** |
| Load-test HTTP 5xx, 100 users | <= 0.5% | 901 / 1,417 = 63.58% | **Fail** |
| Load-test aggregate p99, 100 users | <= 500 ms | 7,700 ms | **Fail** |
| Historical 7-day availability | >= 99.5% | Not measured over a complete 7-day window here | Not assessed |
| Historical 95%-under-500-ms latency | >= 95% | Not measured over a complete SLO window here | Not assessed |

**Definitions:** `409 Conflict` is an expected inventory/contention result and is **not** an HTTP 5xx failure. Locust's overall *failure* count can additionally include network exceptions and 4xx, so it must not be substituted for the 5xx rate.

## 2. Load Test Results

### Test setup

- Locust executed **inside Kubernetes** against `http://gateway:8080`, avoiding `kubectl port-forward` targeting a single backend.
- Scenario: `GET /events` weight 7; `POST /events/{event}/reserve` weight 2, randomly selecting events 3 and 5 with a 3:1 weighting; `GET /health` weight 1. Think time: 0.5–2 seconds.
- Each run lasted 60 seconds. Redis `FLUSHDB` was executed between runs to clear stale reservation holds.
- An old `mixedload` deployment was scaled to zero. An overlapping standalone `gateway` Deployment initially added one extra endpoint alongside Argo Rollouts' five stable gateway pods; its replicas were scaled to zero before the *reported* clean-topology 50-user and later runs.
- The initial 50-user run with six gateway endpoints is retained only as a **diagnostic comparison**, not as the final 50-user row.

| Users | Ramp (users/s) | Requests | RPS | p50 (ms) | p95 (ms) | p99 (ms) | HTTP 5xx (rate) | HTTP 409 | Locust failures | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 2 | 456 | 7.69 | 20 | 60 | 110 | 0 (0%) | 0 | 0% | Pass |
| 12 | 2 | 550 | 9.24 | 16 | 49 | 97 | 0 (0%) | 0 | 0% | Pass |
| 15 | 3 | 664 | 11.16 | 19 | 230 | 700 | 0 (0%) | 0 | 0% | **Fail: p99** |
| 15 (repeat, CPU profiling) | 3 | 649 | 10.92 | 21 | 540 | 930 | 0 (0%) | 0 | 0% | **Fail: p99** |
| 20 | 4 | 841 | 14.10 | 23 | 740 | 1,400 | 2 (0.24%) | 0 | 0.24% | **Fail: p99** |
| 50 (5 gateway endpoints) | 5 | 1,347 | 22.62 | 78 | 3,200 | 5,000 | 466 (34.60%) | 0 reported | 35.34% | **Fail** |
| 100 | 10 | 1,417 | 23.87 | 2,000 | 6,000 | 7,700 | 901 (63.58%) | 0 reported | 67.18% | **Fail** |

**Initial diagnostic run at 50 users (six gateway endpoints):** 1,050 requests, 17.65 RPS, p50 1,100 ms, p95 3,400 ms, p99 6,400 ms, Locust failures 44.29%. After removing the extra Deployment endpoint, throughput increased to 22.62 RPS and p99 decreased to 5,000 ms, but the system was still outside the accepted limits. The difference is observational, not proof that the extra endpoint alone caused the failure.

**Failure pattern:** The 50- and 100-user runs produced 500/502/503/504 responses, connection refusals and disconnected connections. At 100 users, throughput rose only marginally versus 50 users (23.87 vs 22.62 RPS) while failures grew sharply. Five stable gateway pods each accumulated a restart during the 100-user run. Kubernetes events identified failed liveness probes and repeated readiness HTTP 503s; no `OOMKilled` was found in the inspected gateway status. The `reserve_tickets` handler also raised `JSONDecodeError` while trying to parse a non-JSON downstream error response.

**Capacity statement:** Highest passing tested level: **12 users / 9.24 RPS**. First failing tested level: **15 users / 11.16 RPS** (repeat: 10.92 RPS). Do **not** describe 23.87 RPS at 100 users as sustainable throughput. Longer and repeated tests, including 13/14 users and varying traffic mixes, are needed to establish a robust limit.

**Raw evidence in the local working tree:** `submissions/lab10-results/load-10.log`, `load-12.log`, `load-15.log`, `load-15-repeat.log`, `load-20.log`, `load-50.log`, `load-50-before-fix.log`, `load-100.log`, and `cpu-breaking-point.log`. Logs must be included in the PR if referenced as repository evidence.

## 3. DORA Metrics

**Measurement limitations:** The Git history and retained Argo Rollouts objects provide partial deployment evidence. A ReplicaSet, Git commit or AnalysisRun is not necessarily a successful production deployment. The values below deliberately distinguish *measurements* from *proxies*.

| Metric | Result / best available evidence | Confidence and limitation |
|---|---|---|
| **Deployment Frequency** | Two CI-generated image-tag manifest commits visible in the reviewed period: `e4d3138` (2026-09-24) and `732df48` (2026-10-03). Argo Rollouts shows revisions 1–10, but revision 10 was aborted and stable remained revision 7. | **Not a verified production deployment frequency.** Need successful Argo CD sync / healthy rollout timestamps per deployment. |
| **Lead Time for Changes** | CI pipeline builds and pushes three images, commits updated image tags, then GitOps sync is expected. Lab approximation: **CI build/push duration + up to ~3 min Argo CD polling/sync delay**. | **Estimate only;** workflow runtime and actual sync timestamps were not collected. |
| **Change Failure Rate** | Retained AnalysisRuns: 2 `Successful` (revisions 5, 7), 1 `Error` (revision 9), 1 `Failed` (revision 10): **2/4 = 50% problematic AnalysisRuns**. | **Proxy, not DORA CFR**; includes canary gates, not a verified denominator of production deployments causing incidents. |
| **Failed Deployment Recovery Time** | Revision 10 AnalysisRun started `2026-10-03T11:52:53Z`; rollout aborted at `2026-10-03T11:54:13Z`: **80 seconds from AnalysisRun start to abort**. Stable ReplicaSet `gateway-695f674c8f` remained available with five replicas at inspection. | **Abort/detection interval, not end-to-end recovery time.** Exact first customer impact and restoration times were not measured. |

Other Git evidence: commit `dd8001f` introduced a gateway change and `90277bd` reverted it on 2026-09-24. This is a clear example of rollback activity, but deployment timestamps are needed to connect it to an exact DORA incident/recovery measurement.

**Instrumentation to improve DORA:** export timestamps for `commit -> CI start -> image push -> manifest commit -> Argo CD sync -> Rollout Healthy`, and log incidents/restoration timestamps tied to deployed revisions.

## 4. Top 3 Reliability Risks

1. **Dependency-coupled gateway liveness probes lead to unnecessary restarts.** The current `/health` checks `events`/`payments`, with a 1-second probe timeout. Under load the gateway returned HTTP 503, kubelet declared liveness failures and restarted otherwise responsive containers. **Fix:** provide independent `/live` (process only) and `/ready` (dependencies and actual serving ability) endpoints; reserve restarts for real deadlocks, tune thresholds/timeouts, and load-test probe behavior.
2. **Fragile downstream error parsing turns upstream failures into HTTP 500.** The gateway's `reserve_tickets` exception handler calls `e.response.json()` without checking whether the body is valid JSON, causing an observed `JSONDecodeError`. **Fix:** safely parse fallback text, preserve appropriate downstream status codes, use bounded timeouts/retries where safe, and test empty/non-JSON error bodies.
3. **Topology and dependency bottlenecks remain insufficiently controlled.** A standalone Deployment and an Argo Rollout originally selected the same gateway Service, serving six endpoints with differing image tags. Events and a single PostgreSQL instance/connection path could also constrain throughput under peak load. **Fix:** enforce one gateway workload owner in GitOps, validate EndpointSlice against desired replicas, add per-service latency/pool/DB telemetry, and tune capacity based on measured bottlenecks rather than CPU alone.

## 5. Toil Identification

The following are recurring manual workflows from the earlier labs. They are **candidate toil**; the exact number of repetitions and minutes per operation were not recorded, so projected savings below are illustrative rather than observed measurements.

| Manual toil | Frequency / evidence | Automation | Expected time saved |
|---|---|---|---|
| Re-establishing `kubectl port-forward` for Prometheus/Grafana or other temporary endpoints | Repeated during monitoring and chaos labs; exact count not logged | A restartable `make monitoring` script, or an authenticated ingress / stable access path | Removes repeated discovery and reconnection; measure minutes saved per interruption |
| Manually watching rollouts and running `kubectl get`/logs after changes | Repeated during Labs 7–8; exact count not logged | Argo Rollouts AnalysisTemplates + Prometheus alerts + automatic abort and status notifications | Fewer manual watch sessions; immediate signal on unsuccessful canaries |
| Reseeding or managing PostgreSQL state/backups by hand | Repeated across database reliability work; Lab 9 added PVC, migrations and a backup CronJob | Persistent volume, Alembic migrations, scheduled backups and tested restore runbooks | Avoids repeated seed work and backup execution; quantify from future run logs |

**Tracking method:** count occurrences for one week, time each manual operation, and use `saved minutes = frequency × (manual minutes − automated minutes)` to report a measured benefit. A weekly manual occurrence threshold of more than three should be verified before claiming a strict toil count.

## 6. Monitoring Gaps

- **Tail latency:** alert on gateway and dependency p95/p99, not only 5xx rate. The 15-user test failed p99 with **0% errors**, so an error-only alert would miss it.
- **Probes/restarts:** monitor `kube_pod_container_status_restarts_total`, liveness/readiness failures and ready-endpoint count. Alert on a restart increase or fewer than five ready stable gateway endpoints.
- **Downstream attribution:** track gateway-to-events and gateway-to-payments request latency, non-2xx totals and timeouts separately; distinguish HTTP 502/503/504 and connection errors.
- **Database saturation:** monitor active PostgreSQL connections, connection pool queue/wait times, transaction latency, storage fullness and backup age/failures.
- **Traffic correctness:** distinguish expected inventory-related `409` from `5xx`, and tag reserve outcomes by route/event without unbounded metric-label cardinality.
- **GitOps consistency:** alert when a standalone Deployment and Rollout both target `app=gateway`, when desired vs serving endpoints differ, or when rollout stays `Degraded`.

**Specific candidate alerts:** (a) p99 > 500 ms over a meaningful sustained evaluation window; (b) 5xx ratio > 0.5% with a minimum request-volume guard; (c) gateway restarts increased during the last 10 minutes; (d) missing / stale database backup. Thresholds and durations should be tuned against normal traffic, not sent to paging unconditionally.

## 7. Capacity Plan

### 7.1 Measured per-pod headroom at the first failing load level

At **15 users**, eight `kubectl top` samples were collected during a repeat 60-second Locust run. Peaks below are **largest observed samples**, not instantaneous CPU maxima. The repeat run produced **649 requests, 10.92 RPS, 0% errors, p99 930 ms**.

| Component | Peak observed CPU | Current per-pod CPU limit | Peak observed memory | Current per-pod memory limit |
|---|---:|---:|---:|---:|
| Gateway pod `bbdll` | 21m | 200m | 39 MiB | 256 MiB |
| Gateway pod `phggc` | 18m | 200m | 40 MiB | 256 MiB |
| Gateway pod `pwfqw` | 34m | 200m | 39 MiB | 256 MiB |
| Gateway pod `rt664` | 55m | 200m | 41 MiB | 256 MiB |
| Gateway pod `xj24b` | 52m | 200m | 40 MiB | 256 MiB |
| Events | **103m** | 200m | 53 MiB | 256 MiB |
| Payments | 18m | 200m | 39 MiB | 256 MiB |
| PostgreSQL | 61m | 200m | 29 MiB | 256 MiB |
| Redis | 14m | 200m | 7 MiB | 256 MiB |

**Interpretation:** Events had the highest observed CPU of the single-replica application services, while Payments and Redis were relatively idle. **No pod reached its CPU limit in these samples**; CPU bottleneck/throttling is **not proven**. Examine downstream calls, connection pools, p99 traces and throttling counters before concluding what to scale.

### 7.2 Two-times traffic proposal

Starting from the highest passing **9.24 RPS**, the planning target is approximately **18.5 RPS**. The following is a **hypothesis for verification**, not an achieved SLO.

| Component | Current replicas | Proposed replicas | Proposed CPU request / limit *per pod* | Proposed memory request / limit *per pod* |
|---|---:|---:|---|---|
| Gateway | 5 | 10 | 100m / 400m | 128Mi / 256Mi |
| Events | 1 | 2 | 100m / 400m | 128Mi / 256Mi |
| Payments | 1 | 2 | 50m / 200m | 64Mi / 256Mi |
| PostgreSQL | 1 | 1 | 200m / 500m | 256Mi / 512Mi |
| Redis | 1 | 1 | 50m / 200m | 64Mi / 256Mi |
| **Total application/data pods** | **9** | **16** | | |

- **Gateway:** distribute concurrent requests across more pods, but first decouple liveness/readiness and fix error propagation. Avoid a second standalone gateway Deployment.
- **Events:** the most CPU-active single-replica application component. Test two replicas **only if** reservation consistency and inventory locking remain correct across pods.
- **Payments:** second replica improves availability; CPU evidence alone does not require scaling.
- **PostgreSQL:** keep a single write primary and increase headroom initially. Investigate connection pool limits and use pooling (e.g. PgBouncer) if connection pressure is observed. Do **not** simply set `replicas: 2` on one PVC.
- **Redis:** a single instance may suffice for this small test, but is a single point of failure. Consider managed/replicated Redis and failover for production; test hold consistency.
- **Autoscaling:** no HPA exists in `default` currently. Add workload-appropriate HPA only after stable requests/limits, application health probes and traffic metrics are verified.

**Rough cost using the lab's assumption of $5/pod/month:** current **9 × $5 = $45/month**; proposed **16 × $5 = $80/month**, an incremental **$35/month ($420/year)**. These exclude backup/inspector pods, monitoring, Locust jobs, storage/PVC, traffic, managed services, and actual resource-based cloud pricing.

**Validation:** run the exact same in-cluster 60-second 10/12/15/20/50 workload (with Redis reset and no background generator), then at least one longer steady-state test at ~18.5 RPS; collect per-pod CPU throttling, memory, DB pools, restarts, 5xx and p99. The change passes only if both Lab 10 thresholds hold under the target workload.

## Submission checklist

- [x] In-cluster Locust tests at 10, 50 and 100 users, plus 12/15/20 and a 15-user repeat
- [x] Breaking-point level and RPS, with separate HTTP 5xx and `409` handling
- [x] DORA evidence and clearly identified measurement gaps
- [x] Three reliability risks with fixes
- [x] Three toil candidates with automation and savings methodology
- [x] Monitoring gaps and specific alerts
- [x] Numeric 2x capacity plan with measured CPU and estimated costs
- [x] Bonus Option B documented in `submissions/runbooks/quickticket-handbook.md`

**GitHub submission:** commit `locustfile.py`, this report, the handbook, and whichever raw evidence logs will be referenced in the PR; push `feature/lab10`; open a pull request into `main`; submit the PR URL through Moodle.
