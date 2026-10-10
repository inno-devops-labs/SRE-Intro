# QuickTicket Reliability Review — Lab 10

All new measurements were taken on 2026-10-10 UTC in the local `k3d-quickticket` cluster. This branch starts from `main`; earlier student manifests and reports remain on `feature/lab3`–`feature/lab9`, while the live cluster retains their deployed resources. The current ArgoCD Application is `Healthy` but has `Unknown` sync status because its tracked `main` source has no `k8s/` directory. Results below describe the live cluster and the cited historical lab evidence, not a successful deployment from this branch.

## 1. SLO Compliance

Lab 3 defined **99.5% gateway availability over seven days**, where only HTTP 5xx consumes the availability budget, and **95% of requests below 500 ms**, calculated by a rolling five-minute rule. These one-minute tests cannot establish seven-day compliance. They do show that the workload crossed the Lab 10 breaking criterion of **5xx > 0.5% or p99 > 500 ms**.

| Objective | Target | Observed in this review | Assessment |
|---|---:|---|---|
| Availability | ≥99.5% over seven days | 55 users: 99.919% non-5xx; 60 users: 98.704%; 100 users: 65.137% in separate 60-second runs | Seven-day result unverified; overloaded runs fail the short-run threshold |
| Latency | ≥95% under 500 ms (rolling five-minute rule) | Locust p95: 20 ms at 55 users, 120 ms at 60, 560 ms at 100 | 100-user run breaches the comparable short-run threshold; five-minute SLO remains unverified |

The observed availability percentages use **HTTP responses** as the denominator. No transport errors occurred. An HTTP 409 is a product/inventory outcome, not a gateway 5xx; it remains in the denominator. Lab 3's source definition is in `git show feature/lab3:submissions/lab3.md` under “Task 2 — SLOs & Recording Rules.”

## 2. Load Test Results

Locust 2.43.4 ran as a Kubernetes Job in namespace `default`, addressed `http://gateway:8080`, and used 60-second headless runs. The scenario weights were 7:2:1 for `GET /events`, `POST /events/{id}/reserve`, and `GET /health`, with a 0.5–2.0 second wait, quantity 1, and event IDs `[3,3,3,5]`. Gateway Service had five Ready endpoints. No gateway port-forward was used. Before each run, both target events had zero confirmed orders (`3|500|0`, `5|80|0`), Redis was flushed in this isolated local test cluster, and the prior load Job had finished. The actual Job manifest, timestamps, full Locust log, Job status, and per-pod CPU snapshot are in [`evidence/lab10/`](evidence/lab10/).

| Users | Ramp/s | Requests | RPS | p50 ms | p95 ms | p99 ms | HTTP 5xx | 5xx % | HTTP 409 | 409 % | Transport | Result |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 2 | 461 | 7.71 | 7 | 15 | 45 | 0 | 0 | 0 | 0 | 0 | Within threshold |
| 50 | 5 | 2,236 | 37.41 | 6 | 22 | 83 | 0 | 0 | 33 | 1.48 | 0 | Within threshold |
| 55 | 6 | 2,458 | 41.10 | 6 | 20 | 85 | 2 | 0.08 | 59 | 2.40 | 0 | Within threshold |
| **60** | **6** | **2,624** | **43.90** | **6** | **120** | **290** | **34** | **1.30** | **53** | **2.02** | **0** | **First tested threshold breach** |
| 75 | 8 | 3,263 | 54.57 | 6 | 150 | 290 | 48 | 1.47 | 67 | 2.05 | 0 | 5xx breach |
| 100 | 10 | 3,835 | 64.11 | 190 | 560 | 750 | 1,337 | 34.86 | 3 | 0.08 | 0 | 5xx and p99 breach |

Formula: `5xx % = count(status 500–599) / count(all HTTP responses) × 100`; `409 %` uses the same denominator. RPS and percentiles are Locust's aggregate final statistics. Its failure total includes 409 and therefore was **not** used as the 5xx numerator. At 100 users, the HTTP 5xx breakdown is 320×500, 906×502, and 111×503; the three 409 responses are separate. The additional runs bracket the limit: **55 users / 41.10 RPS** was the highest measured level under both criteria, while **60 users / 43.90 RPS / 1.30% 5xx / 290 ms p99** was the first tested breach. The exact transition inside this five-user interval remains unmeasured.

The complete outputs for the mandatory levels are [10-user log](evidence/lab10/load-10-1791623320.log), [50-user log](evidence/lab10/load-50-1791623446.log), and [100-user log](evidence/lab10/load-100-1791623537.log); the bracket runs are [55](evidence/lab10/load-55-1791623778.log), [60](evidence/lab10/load-60-1791623705.log), and [75](evidence/lab10/load-75-1791623629.log). Each matching `.json` is the submitted Job, `-status.json` records completion, and `-run.txt` records UTC times and inventory. The `-cpu.txt` files for levels 50–100 are concurrent `kubectl top` samples; the first 10-user CPU snapshot was taken after the load period and is excluded from capacity analysis. For example, [the 60-user CPU sample](evidence/lab10/load-60-1791623705-cpu.txt) is timestamped 09:15:37 UTC.

The first observed application bottleneck is the events service's **10-connection `ThreadedConnectionPool`**, not sold-out inventory. At the [60-user breach](evidence/lab10/load-60-events-errors.txt) and again at [100 users](evidence/lab10/load-100-events-errors.txt), timestamped events logs recorded `psycopg2.pool.PoolError: connection pool exhausted`; gateway propagated downstream failures as 502 and also returned 503 on health. At 60 users, events consumed only 96m of its 200m CPU limit in one snapshot, so pool concurrency can fail before sustained CPU saturation. At 100 users, events reached 182m/200m. Locust used 77m and the node reported 11% CPU at that sample, making load-generator or whole-node CPU saturation an unlikely primary explanation. These are instantaneous `kubectl top` samples, not maxima over the full minute.

## 3. DORA Metrics

The cohort is the **five post-bootstrap GitOps deployment records from Lab 5 on 2026-09-27 UTC**, excluding the initial ArgoCD bootstrap. This is a deliberately short exercise window. [ArgoCD history](evidence/lab10/argocd-history.json) supplies `deployedAt` and revision; `git show -s --format='%h %cI %s' <sha>` supplies commit times. The five deploy records are `f5b6aa5`, `5a6510a`, `4993019`, `72b7484`, and `f016c33`.

| Source change | ArgoCD revision | Commit UTC | Deployed UTC | Commit→deploy |
|---|---|---|---|---:|
| `f5b6aa5` | `f5b6aa5` | 22:48:20 | 22:49:07 | 47 s |
| `5a6510a` | `5a6510a` | 22:49:41 | 22:49:56 | 15 s |
| `4993019` | `4993019` | 22:50:31 | 22:50:47 | 16 s |
| `72b7484` | `72b7484` | 22:52:07 | 22:52:22 | 15 s |
| `8f68349` (CI image build) | `f016c33` (generated tag update) | 22:53:40 | 22:58:48 | 308 s |

| Metric | Result | Method and limit |
|---|---|---|
| Deployment frequency | 5 GitOps deployments on 2026-09-27 | Distinct ArgoCD `deployedAt` records after bootstrap; this exercise does not support a sustained daily-rate claim |
| Lead time for changes | Median **16 s**; range 15–308 s | Source commit to ArgoCD deploy record above. The CI-built image uses its source commit, not just the generated manifest commit. ArgoCD `deployedAt` is a deployment proxy, not a measured end-user verification time |
| Change failure rate | **1/5 = 20%** | `4993019` pointed to an unavailable image and required revert `72b7484`; Lab 5 report documents the degraded rollout and recovery. This was a failed deployment requiring remediation, while the prior healthy pod stayed available |
| Recovery after failed deployment (DORA proxy) | **23 s** | Lab 5: revert push completed at 22:52:10Z; ArgoCD was `Synced/Healthy` at 22:52:33Z. This measures restoration of the desired deployment state. The previous healthy pod remained available, so there was no user-service outage from which to measure a true service MTTR |
| Canary abort containment | **About 1 s** | Lab 7: from manual abort to removal of the bad canary from eligible gateway Service endpoints, at one-second polling resolution. This does not measure client connection draining or full service recovery |

The failed Lab 7 canary [AnalysisRun](evidence/lab10/analysisruns.json) was deliberately injected and auto-aborted; it is not an extra production incident or a sixth deployment in the defined Lab 5 cohort. The 23-second and approximately one-second figures start at different operator actions and end at different deployment states; neither is an end-to-end user-service MTTR. Historical details are in `git show feature/lab5:submissions/lab5.md` and `git show feature/lab7:submissions/lab7.md`.

**Separate chaos-recovery observation, excluded from DORA:** Lab 8's Redis experiment removed all gateway endpoints at 01:28:11+03; the API and endpoints were healthy again at 01:29:10+03, an observed **59-second service outage recovery**. It was a deliberately induced dependency failure, not a failed deployment. See `git show feature/lab8:submissions/lab8.md`.

## 4. Top 3 Reliability Risks

| Risk and evidence | Impact | Mitigation |
|---|---|---|
| Events has one replica and a 10-connection pool; 60-user load already exceeded the 0.5% 5xx criterion and 100-user logs show pool exhaustion. Gateway turns some downstream 500s into 502s. | Read and reserve failures together; errors grow sharply before node CPU is full. | Scale events and give each pod a bounded pool; add queueing/backpressure or controlled overload responses; optimize the `GET /events` query as orders grow; validate at the target RPS. |
| Redis is a single pod and dependency-coupled health checks propagate its failure. Lab 8 lost every gateway Service endpoint during a Redis outage. | A reservation dependency failure can remove the read path as well. | Separate process liveness from dependency readiness, preserve read availability where safe, add Redis recovery/replication and an endpoint-availability alert. |
| PostgreSQL data and backup PVCs share one `local-path` node; Lab 9's 15-second pod-replacement RTO does not cover disk/node loss. | A single node failure may remove live data and local backups together. | Copy backups off-node, test restore regularly, define an RPO/RTO for node loss, and use storage with independent failure domains before production use. |

## 5. Toil Identification

| Repeated manual work | Observed frequency | Automation and estimated saving |
|---|---|---|
| Watching/promoting canary stages in Lab 7 | At least four 20/40/60/80% stage checks in the documented multi-step run, plus the separate manual canary | The Lab 7 AnalysisTemplate already automates the error-rate decision. Keep automated promotion and add latency analysis; assuming one minute of operator checks per stage, about four minutes per comparable rollout can be returned to other work. |
| Creating and verifying individual database backup Jobs | Seven `manual-N` jobs in Lab 9's retention test, with a count check after each | The five-minute CronJob and five-file retention now automate routine backups. A scripted restore-validation check can replace repeated manual listing; at an estimated one minute per manual invocation/check, the seven-run exercise required roughly seven minutes. |
| Repeating health, latency, error and pod checks around failures | Lab 8 documents baseline, injection and recovery checks across three experiments, plus the combined test: more than four check cycles | A diagnostic snapshot script can collect pod/endpoints state, per-path PromQL and recent logs with UTC timestamps. At an assumed two minutes of manual collection per cycle, five cycles would save about ten minutes; this is an estimate, not a stopwatch measurement. |

## 6. Monitoring Gaps

1. **Events pool saturation:** `events_db_pool_size` exists, but a scrape-time gauge of used connections can miss a brief peak. Add a pool-exhaustion counter, in-use/max gauge, and alert for repeated exhaustion or sustained ≥80% use. This would have named the Lab 10 failure before a generic gateway 502 alert.
2. **Service endpoint loss:** Lab 8's Redis failure took gateway endpoints to zero through health-check coupling. Alert on gateway ready endpoints/available replicas and Redis health, not solely aggregate 5xx; when no requests can connect, gateway's own HTTP counters may stop increasing. Track client-side transport failures too.
3. **Slow successful paths and notification:** Lab 8 first found payment p99 around 2.485 seconds with no payment 5xx. Its later Prometheus payment-p99 rule detected the issue, but the in-cluster setup has no Alertmanager route. Add routed notification, per-path latency objectives and burn-rate alerts. The Lab 6 Grafana alerting setup was host-side and is not a substitute for in-cluster paging.

## 7. Capacity Plan — 2× the measured healthy traffic

The last measured level under both breaking criteria was **55 users at 41.10 RPS**. A twofold target is therefore **82.20 RPS for this exact read/reserve/health mix**. The 100-user trial managed 64.11 RPS while badly degraded; that is not a usable capacity ceiling. At the first tested breach (60 users, 43.90 RPS), the [09:15:37 UTC CPU sample](evidence/lab10/load-60-1791623705-cpu.txt) was:

| Component/pod | CPU at 60 users | Current request / limit |
|---|---:|---:|
| gateway `8d27n`, `8jgxm`, `dsxhd`, `fkv57`, `hwr62` | 34m, 16m, 27m, 36m, 22m | 50m / 200m each |
| events `j2b5p` | **96m** | 50m / 200m |
| payments `wgdjc` | 9m | 50m / 200m |
| Redis `cvg66` | 7m | 50m / 200m |
| PostgreSQL `vs9h5` | 34m | 50m / 200m |
| Locust generator `mr9fb` | 64m | Job has no request/limit in the supplied template |

At the [100-user stress sample](evidence/lab10/load-100-1791623537-cpu.txt), events was **182m/200m**, PostgreSQL **89m/200m**, gateway pods **41–57m/200m**, Locust **77m**, and node **896m (11%)**. The events pool of **10** connections exhausted even at lower CPU. PostgreSQL reports `max_connections=100`; three events pools of ten would use up to 30, leaving room for administration and other clients under the current configuration. That count must be rechecked if more replicas or clients are added.

| Service | Current → proposed replicas | Proposed per-pod resources | Reason |
|---|---|---|---|
| Gateway | 5 → 5 | Keep 50m/200m CPU, 64Mi/256Mi memory | Per-pod CPU stayed below 57m in the stressed sample; no evidence gateway CPU is the first limit |
| Events | **1 → 3** | 100m/400m CPU, 128Mi/256Mi memory; keep pool at 10 per pod initially | Spreads concurrent calls across three independent bounded pools and reduces per-pod CPU; add wait/backpressure for pool exhaustion |
| Payments | 1 → 1 | Keep 50m/200m CPU, 64Mi/256Mi memory | This workload exercises only payment health, not charge throughput; run a checkout-specific test before claiming 2× checkout capacity |
| Redis | 1 → 1 for throughput | Keep 50m/200m CPU, 64Mi/256Mi memory | 6–7m observed; replication is an availability improvement, not required by measured CPU throughput |
| PostgreSQL | 1 → 1 | 100m/500m CPU, 128Mi/512Mi memory | 89m in stress sample and up to 30 events connections; leave headroom for more successful queries and inspect query/pool metrics |

Sizing check: extrapolating the *stressed* events CPU sample linearly gives `182m × 82.20/64.11 ÷ 3 ≈ 78m` per events pod, below the proposed 100m request and 400m limit. This is only a planning estimate: the stressed run had many failures, workload mix and query cost can shift, and pool contention is nonlinear. Validate the proposed setup with a new 82+ RPS in-cluster test and a checkout workload before accepting it.

At the lab's **$5 per application pod per month** assumption, the current 9 pods (5 gateway + events + payments + Redis + PostgreSQL) cost **$45/month**. The proposed 11 pods cost **$55/month**, an incremental **$10/month**. This excludes cluster nodes, Prometheus, ArgoCD, backup storage, traffic and any replicated/managed Redis or off-node backup service; it is not a cloud quote. The local single-node cluster has aggregate CPU headroom, but node failure remains a separate reliability limit.

## Bonus — SRE handbook and evidence

The [QuickTicket SRE handbook](runbooks/quickticket-handbook.md) contains an architecture diagram, the GitOps deployment and rollback workflow, monitoring queries, an incident procedure, and condensed backup/restore steps. It calls out the current `main`/ArgoCD source gap so a new operator will not assume automatic sync is working.

The test runner is [`../loadtest/run.py`](../loadtest/run.py). It checks cluster context and pod readiness, verifies test-event inventory, flushes Redis, dry-runs a unique in-cluster Job, captures CPU during execution, then saves the complete log and Job completion status. `python3 -m py_compile locustfile.py loadtest/run.py` and `git diff --check` were used for final local validation. After testing, Redis was flushed again (`DBSIZE=0`), gateway had five Ready pods, and events, payments, Redis and PostgreSQL each had one Ready pod. No application deployment was changed for this review.
