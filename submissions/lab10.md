# QuickTicket Reliability Review

Scope: a single-node k3d learning environment, reviewed on
2026-10-09 UTC. Locust 2.43.4 ran inside Kubernetes against
`http://gateway:8080`; no gateway port-forward was used.

The application retained 5 gateway replicas, one Events, one Payments,
one PostgreSQL and one Redis pod. Each application container had a 200m
CPU limit. PostgreSQL used the data PVC added in Lab 9.
The database retained 11,999 orders from the earlier checkout experiments.

This review distinguishes measured results, historical evidence and proposed
changes. Short experiments do not establish seven-day SLO compliance.

## 1. SLO Compliance

Historical targets come from Lab 3:
99.5% non-5xx availability over seven days, and at least 95% of requests
completed within 500 ms. Locust results cover approximately 60 seconds per
run, including ramp-up.

| SLO or test guardrail | Target | Observed | Status |
|---|---|---|---|
| Availability SLO | ≥99.5% non-5xx over 7 days | 10u/25u: 100%; 50u: 98.145%; 100u: 45.436% in sampled runs | 50u/100u fail the short-run target; weekly compliance unproven |
| Latency SLO | ≥95% within 500 ms | p95: 33/35/330/1700 ms at 10/25/50/100u | 10u–50u approximately meet percentile criterion; 100u fails |
| Lab 10 capacity guardrail | 5xx ≤0.5% and p99 ≤500 ms | 25u: 0%, 90 ms; 50u: 1.855%, 570 ms | First tested breach at 50u |

Percentiles are Locust's approximations; exact latency compliance fractions
were not captured. A p95 below 500 ms supports the percentile criterion,
but is not a precise measured compliance ratio. The p99 guardrail is stricter
than the historical p95-oriented SLO.

409 Conflict is expected inventory contention and remains separate from 5xx.
Network failures are also reported separately; none occurred in these runs.
These synthetic stress failures are not presented as production error-budget
consumption.

## 2. Load Test Results

The task mix uses listing/reservation/health weights 7:2:1 and
`between(0.5, 2.0)` waits. Reservations select events 3 and 5 in a 3:1 ratio.
Redis was flushed before each run. Event capacities remained 500 and 80.
409s were explicitly marked as expected responses while retaining a separate
status counter. No payment requests are included, so this test does not
establish payment or full-checkout capacity.

These are closed-loop user tests, not fixed-RPS injection. Response slowdown
changes achieved throughput. Results include ramp-up and are single samples.

| Users | Ramp /s | RPS | p50 ms | p95 ms | p99 ms | 5xx % (count) | 409 % (count) |

|---:|---:|---:|---:|---:|---:|---:|---:|

| 10 | 2 | 7.71 | 13 | 33 | 130 | 0.000 (0) | 0.000 (0) |

| 25 | 5 | 19.48 | 9 | 35 | 90 | 0.000 (0) | 0.000 (0) |

| 50 | 5 | 35.16 | 9 | 330 | 570 | 1.855 (39) | 1.760 (37) |

| 100 | 10 | 48.68 | 510 | 1700 | 2300 | 54.564 (1590) | 0.000 (0) |



| Users | Start UTC | Finish UTC | Requests | HTTP status counts |

|---:|---|---|---:|---|

| 10 | 2026-10-09T20:53:18.995210+00:00 | 2026-10-09T20:54:18.803733+00:00 | 459 | 200: 459 |

| 25 | 2026-10-09T21:12:27.385947+00:00 | 2026-10-09T21:13:27.188652+00:00 | 1164 | 200: 1164 |

| 50 | 2026-10-09T20:58:02.940178+00:00 | 2026-10-09T20:59:02.778703+00:00 | 2102 | 200: 2026, 502: 24, 500: 14, 503: 1, 409: 37 |

| 100 | 2026-10-09T20:59:11.375931+00:00 | 2026-10-09T21:00:11.240963+00:00 | 2914 | 200: 1324, 502: 1119, 500: 322, 503: 148, 504: 1 |



### Capacity boundary and diagnosis

The highest tested healthy level was **25 users / 19.48 RPS**.
The first tested failing level was **50 users / 35.16 RPS**.
The threshold lies somewhere between those tested user levels; 35.16 RPS is
a measured failing point, not a safe sustainable throughput claim.

At 100 users, achieved throughput was 48.68 RPS, but 54.564% of responses
were 5xx. Increasing throughput while returning errors is not useful capacity.
No 200-user run was needed because both limits were already breached.

Events logs show `psycopg2.pool.PoolError: connection pool exhausted`.
The running Events code uses `ThreadedConnectionPool(minconn=2,maxconn=10)`;
reservation processing holds an outer connection and `_get_available()`
borrows another. Pool pressure can therefore fail requests before CPU reaches
its limit. At 100u, Events also reached 198m against its 200m CPU limit.

CPU samples alone do not prove throttling, and the PostgreSQL query cost,
connection occupancy and thread scheduling were not traced. The pool error
is directly observed; the relative contributions need further measurement.

```text
2026-10-09T21:00:01.034877923Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.034878881Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.039333631Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.039337798Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.131730798Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.131731548Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.137475673Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.137480090Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.185636798Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.185642090Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.227604965Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.227605840Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.236076923Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.236077881Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.328201340Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.328202173Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.486708090Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.486709048Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.529410757Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.529411590Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.585563840Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.585564632Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.637038673Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.637041965Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.730917507Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.730918382Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.834504257Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.834505257Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.926657048Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.926659632Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.931156507Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.931157298Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:01.934735882Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:01.934736632Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:02.636462674Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:02.636463465Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:02.729846507Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:02.729847465Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:02.937571757Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:02.937572549Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:02.983454549Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:02.983458299Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:03.030516341Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:03.030517216Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:03.033320924Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:03.033321799Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:03.036195257Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:03.036196049Z psycopg2.pool.PoolError: connection pool exhausted
2026-10-09T21:00:03.083407924Z     raise PoolError("connection pool exhausted")
2026-10-09T21:00:03.083408674Z p
```

### In-cluster distribution evidence

Prometheus query evaluated at each recorded finish time:
`sum by (pod) (increase(gateway_requests_total[65s]))`.
Counts are scrape-window estimates and are not expected to equal Locust totals.

| Gateway pod | 10u | 25u | 50u | 100u |

|---|---:|---:|---:|---:|

| gateway-58bd6b745f-dbzhz | 146.25 | 295.75 | 408.44 | 447.96 |

| gateway-58bd6b745f-lq9g5 | 98.58 | 343.39 | 677.40 | 635.40 |

| gateway-58bd6b745f-pc5hf | 96.42 | 323.92 | 408.81 | 469.18 |

| gateway-58bd6b745f-pkrsv | 145.17 | 191.75 | 338.19 | 546.50 |

| gateway-58bd6b745f-zl9qw | 0.00 | 46.58 | 328.90 | 553.16 |



All five gateway pods received traffic at 50u and 100u.
At 10u, one pod had zero observed requests. Persistent connections and a short
run can produce uneven distribution even through the ClusterIP Service.

## 3. DORA Metrics

These are local learning-environment proxies with explicitly defined
denominators, not an elite-production classification.

| Metric | Calculation | Result | Scope / limitation |
|---|---|---|---|
| Deployment frequency | 8 retained ArgoCD sync records / 13 calendar days | 0.615 syncs/day | 2026-09-27 through 2026-10-09; config syncs, not necessarily new application releases |
| Lead time for changes | Commit timestamp → recorded ArgoCD deployedAt | median 11.5 s; range 5–189 s | Manifest/config commit proxy; excludes earlier image-build work and does not measure application readiness |
| Change failure rate | Failed Lab 7 update revisions 3 and 7 / 7 updates (revisions 2–8) | 28.57% | Includes deliberate bad canaries and restore updates; excludes initial revision 1 |
| Failed-deployment recovery | Manual abort invocation → five stable ready Service endpoints | 8.51 s | Lab 7 measured endpoint recovery, not failure onset-to-detection or all in-flight requests |

A separate automated-analysis sample had 1 Failed and 1 Successful AnalysisRun:
50% of analysis trials failed. This fraction has a different denominator and
is not substituted for the change failure rate.

Six retained distinct gateway ReplicaSets do not equal eight rollout revisions:
restoring an earlier template reuses its ReplicaSet. ReplicaSet retention can
also remove older evidence. `git log main` includes course commits and is not
a deployment counter.

### Git and ArgoCD source timestamps

| Commit | Source branch | Commit time | ArgoCD deployedAt | Seconds |

|---|---|---|---|---:|

| 00298d6e8c93 | feature/lab5 | 2026-09-27T16:03:01Z | 2026-09-27T16:06:10Z | 189.0 |

| 23583e28ce9f | feature/lab5 | 2026-09-27T19:10:35+03:00 | 2026-09-27T16:11:56Z | 81.0 |

| bc62a6ce185d | feature/lab5 | 2026-09-27T16:14:04Z | 2026-09-27T16:14:22Z | 18.0 |

| cb9444dfba86 | feature/lab5 | 2026-09-27T19:16:38+03:00 | 2026-09-27T16:16:43Z | 5.0 |

| fabe9e8f0abe | feature/lab5 | 2026-09-27T19:18:53+03:00 | 2026-09-27T16:18:58Z | 5.0 |

| 3bc3cc9543a3 | feature/lab5 | 2026-09-27T16:28:21Z | 2026-09-27T16:28:38Z | 17.0 |

| 63f6bae7f76c | feature/lab7 | 2026-10-04T15:04:28+03:00 | 2026-10-04T12:04:34Z | 6.0 |

| d17bde23c8a7 | feature/lab9 | 2026-10-09T23:38:54+03:00 | 2026-10-09T20:38:59Z | 5.0 |



### CI evidence

| Workflow run | Branch | Commit | Lifecycle seconds | Conclusion |

|---|---|---|---:|---|

| 36333128425 | feature/lab5 | b6f70905f9d2 | 237 | success |

| 36332260202 | feature/lab5 | 23583e28ce9f | 212 | success |

| 36331544158 | feature/lab5 | 948c0e6c9cd4 | 228 | success |

| 36330720830 | feature/lab5 | 61b2fa9d5924 | 40 | success |



Workflow elapsed times use createdAt→updatedAt and are lifecycle proxies.
The latest image-producing run took 237 s on this measure. Adding a nominal
0–180 s ArgoCD polling delay would suggest 237–417 s, but that is a pipeline
estimate, not the measured manifest-commit lead time above.

Sources: retained ArgoCD application history, commit timestamps from `git show`,
ReplicaSet/AnalysisRun JSON and GitHub Actions run metadata. Historical Lab 7
and Lab 9 evidence is available in the previously submitted reports.

```text
gateway-58bd6b745f created=2026-10-04T11:51:13Z latest_revision=8 replicas=5
gateway-5ff48c5c7d created=2026-10-04T11:56:34Z latest_revision=7 replicas=0
gateway-68f99f8b8c created=2026-10-04T11:37:16Z latest_revision=5 replicas=0
gateway-6c74c88f7d created=2026-10-04T11:20:42Z latest_revision=1 replicas=0
gateway-6fbcc554b7 created=2026-10-04T11:23:24Z latest_revision=4 replicas=0
gateway-84ff8d47c6 created=2026-10-04T11:29:53Z latest_revision=3 replicas=0
```

```text
gateway-58bd6b745f-6-2 phase=Successful created=2026-10-04T11:51:42Z
gateway-5ff48c5c7d-7-2 phase=Failed created=2026-10-04T11:57:02Z
```

## 4. Top 3 Reliability Risks

1. **Events pool exhaustion and dependency-coupled health.**
   Pool acquisition fails under concurrency; Redis failure in Lab 8 also
   made Events unready, disrupting reads. Reuse the existing connection
   within reservation processing, add bounded acquisition/admission control,
   expose pool occupancy/errors, and separate process liveness from dependency
   health. Add Events replicas only after budgeting total DB connections.

2. **Single-node state and recovery dependence.**
   PostgreSQL now survives pod replacement via PVC, but local-path storage and
   a single database do not survive every node failure. Lab 9 observed
   218.868 s recovery including transfer diagnosis, compared with 21.138 s
   for the PVC test with different deletion conditions. Use independent backup
   storage, scheduled restore drills, WAL/PITR and a tested failover design.

3. **Reservation integrity and limited checkout visibility.**
   409s are expected, but concurrent inventory checks and Redis hold management
   need atomicity and expiry reconciliation. Lab 8 showed reservation failure
   and dependency-wide disruption. Use atomic Redis operations or database
   constraints/transactions, reconcile expired holds, and add end-to-end
   reserve→pay→confirm synthetic checks. This load scenario omits payments,
   so an idle Payments pod does not establish checkout headroom.

## 5. Toil Identification

Counts below describe documented routine operations, not counts of matching
words in reports. Estimated time savings are planning assumptions.

| Toil | Documented frequency / source | Automation | Estimated saving |
|---|---|---|---|
| Manually verifying health after setup/recovery | At least 4 separate lab sessions: Labs 1, 4, 5 and 9 contain successful health evidence | CI/GitOps smoke Job checking health, listing and checkout, with saved JSON results | Assume 2 min/check: ≥8 min across those sessions |
| Repeating rollout waits and readiness inspection | At least 6 documented `rollout status` invocations across Labs 8 and 9: payments/events/mixedload and Events recovery | A shared deploy/recovery command with bounded waits and automatic diagnostics on failure | Assume 1 min operator attention/wait: ≥6 min |
| Restoring dependency configuration after fault experiments | 4 Lab 8 cycles: payment latency, Redis outage, combined failures, repeated latency-alert trial | Snapshot baseline; restore through a cleanup trap and verify recovery before proceeding | Assume 2 min/cycle: ≥8 min, plus fewer forgotten fault settings |

The command blocks already automate parts of these workflows. The remaining
toil is operator-driven repetition and verification. Preserve human review
for hypothesis design and destructive recovery exercises; automate routine
execution and evidence capture.

## 6. Monitoring Gaps

- **Events pool and database pressure:** collect pool in-use/max, acquisition
  failures, query duration, locks and DB connections. Alert on pool exhaustion
  and connect saturation to per-path gateway 5xx.
- **CPU throttling:** `kubectl top` shows averaged usage, not throttled time.
  Scrape container throttling metrics before attributing latency solely to CPU.
- **Dependency-specific health:** alert when Events has zero ready endpoints
  or repeatedly fails probes; separate Redis availability from read-path
  availability. This would expose the Lab 8 Redis→Events→gateway cascade.
- **Latency beyond payments:** the installed Lab 8 alert detects payment p99
  above 1 s for 1 min, with a request-rate guard. It does not detect reserve/read
  latency like the Lab 10 test; add per-path latency and SLO burn-rate alerts.
- **Payments behavior and traceability:** instrument retries, circuit-breaker
  transitions and payment/confirmation consistency. The current capacity mix
  cannot reveal payment latency or retry amplification.
- **Backups and restore readiness:** monitor last successful backup age, failures,
  usable archive size, retention and restore-drill results. Lab 9's truncated
  copy demonstrates why size/checksum checks belong before destructive steps.
- **Coverage and notification:** Prometheus currently scrapes gateway pods and
  has the payment latency rule. Broader rules, Alertmanager routing and external
  notification delivery were not demonstrated. A firing rule alone is not a page.

Lab 8 slow successful payments escaped error-only detection; the added payment
latency rule closed that specific gap. Redis failure still requires dependency
and endpoint alerts, while reserve/load failures require pool and path signals.

## 7. Capacity Plan

### Measured boundary and 2× objective

Use **19.48 RPS** as the highest verified healthy sample, not 35.16 RPS.
The primary 2× target is **38.96 RPS** with the same task mix while
keeping 5xx ≤0.5% and p99 ≤500 ms. A separate aggressive target would be
70.32 RPS, twice the first failing point, and needs additional validation.

The proposed configuration below was not applied or benchmarked. It is a
testable capacity hypothesis, not a linear-scaling guarantee.

### Per-pod CPU at the breaking point and overload

Every current application container has a 200m CPU limit.
The values below are maxima among `kubectl top pods` samples, with measurement
lag and averaging; they are not instantaneous peaks.

| Pod | 50u max CPU m | 100u max CPU m | 100u / 200m limit |

|---|---:|---:|---:|

| gateway-58bd6b745f-dbzhz | 26 | 58 | 29.0% |

| gateway-58bd6b745f-lq9g5 | 40 | 66 | 33.0% |

| gateway-58bd6b745f-pc5hf | 23 | 61 | 30.5% |

| gateway-58bd6b745f-pkrsv | 26 | 57 | 28.5% |

| gateway-58bd6b745f-zl9qw | 25 | 62 | 31.0% |

| events-66695685b-x8lh9 | 92 | 198 | 99.0% |

| payments-7bbc479c88-7pf27 | 8 | 19 | 9.5% |

| postgres-698c969578-cskn4 | 147 | 169 | 84.5% |

| redis-68f999b745-lb2jj | 12 | 13 | 6.5% |



### Raw CPU evidence: first failing level (50u)

```text
TIMESTAMP=2026-10-09T20:58:34.885421+00:00
app=gateway
NAME                       CPU(cores)   MEMORY(bytes)
gateway-58bd6b745f-dbzhz   22m          39Mi
gateway-58bd6b745f-lq9g5   24m          46Mi
gateway-58bd6b745f-pc5hf   23m          53Mi
gateway-58bd6b745f-pkrsv   21m          40Mi
gateway-58bd6b745f-zl9qw   17m          40Mi
app=events
NAME                     CPU(cores)   MEMORY(bytes)
events-66695685b-x8lh9   92m          47Mi
app=payments
NAME                        CPU(cores)   MEMORY(bytes)
payments-7bbc479c88-7pf27   7m           39Mi
app=postgres
NAME                        CPU(cores)   MEMORY(bytes)
postgres-698c969578-cskn4   98m          29Mi
app=redis
NAME                     CPU(cores)   MEMORY(bytes)
redis-68f999b745-lb2jj   10m          18Mi
load_generator
NAME                  CPU(cores)   MEMORY(bytes)
lab10-load-50-t8q26   39m          39Mi
```

### Raw CPU evidence: overloaded level (100u)

```text
TIMESTAMP=2026-10-09T21:00:01.912376+00:00
app=gateway
NAME                       CPU(cores)   MEMORY(bytes)
gateway-58bd6b745f-dbzhz   53m          41Mi
gateway-58bd6b745f-lq9g5   66m          48Mi
gateway-58bd6b745f-pc5hf   61m          55Mi
gateway-58bd6b745f-pkrsv   55m          41Mi
gateway-58bd6b745f-zl9qw   62m          42Mi
app=events
NAME                     CPU(cores)   MEMORY(bytes)
events-66695685b-x8lh9   198m         59Mi
app=payments
NAME                        CPU(cores)   MEMORY(bytes)
payments-7bbc479c88-7pf27   12m          39Mi
app=postgres
NAME                        CPU(cores)   MEMORY(bytes)
postgres-698c969578-cskn4   169m         42Mi
app=redis
NAME                     CPU(cores)   MEMORY(bytes)
redis-68f999b745-lb2jj   11m          18Mi
load_generator
NAME                   CPU(cores)   MEMORY(bytes)
lab10-load-100-jd7t9   68m          42Mi
```

Events is CPU-constrained at 100u, while gateway pods remain below 33%
of their limits. At 50u, Events peaks at only 92m, yet errors already occur:
pool exhaustion cannot be solved by scaling gateway alone. Payments and Redis
are comparatively idle; Payments receives only health traffic in this scenario.

### Proposed resource and replica budget

| Service | Current replicas | Proposed replicas | CPU request / limit per pod | Memory request / limit per pod | Rationale |
|---|---:|---:|---|---|---|
| Gateway | 5 | 5 | 100m / 300m | 64Mi / 256Mi | Existing replicas have CPU headroom |
| Events | 1 | 2 | 250m / 500m | 128Mi / 256Mi | Distribute concurrency; fix nested pool borrowing first |
| Payments | 1 | 1 | 50m / 200m | 64Mi / 256Mi | Not capacity-tested; keep provisional baseline and test full checkout |
| PostgreSQL | 1 | 1 | 250m / 1000m | 512Mi / 1Gi | Add CPU/query headroom without unsafe active-active DB replicas |
| Redis | 1 | 1 | 50m / 200m | 64Mi / 256Mi | CPU adequate for this mix; single-pod failure risk remains |

Total proposed application budget: 10 pods; CPU requests **1350m**,
CPU limits **3900m**; memory requests **1216Mi**, limits **3328Mi**.
Monitoring, controllers, load generators and OS overhead are additional.
The node reports 8 CPU and approximately 5.79 GiB memory, but this is not
a guarantee that all limits can be used simultaneously with other workloads.

For reliability beyond this single-node lab, Redis needs tested replication/
failover and durable hold semantics. Replicas on the same node do not provide
node-level availability. Do not scale PostgreSQL Deployment to multiple
independent writers against the same PVC.

### Database connection budget

```text
max_connections
-----------------
 100
(1 row)

 state  | count
--------+-------
        |     5
 active |     1
 idle   |     2
(3 rows)
```

PostgreSQL reports `max_connections=100`. Two Events pods at
`DB_MAX_CONNS=10` can request up to 20 application connections.
Reserve at least 5 more for administration, backups and migrations, and budget
other clients before rollout. The application currently has direct pools to
Postgres; no PgBouncer deployment was observed.

A pooler may help control aggregate connection pressure, but would not by
itself fix nested borrowing, slow queries or application concurrency.
Consider PgBouncer after measuring pool occupancy and transaction duration.

### Cost assumption and validation

Using the assignment's illustrative $5/pod/month assumption:
current 9 application pods ≈ **$45/month**; proposed 10 ≈ **$50/month**.
An optional pooler adds one pod, bringing this model to **$55/month**.
These are arithmetic assumptions, not cloud quotes; storage, backups,
network traffic, node capacity and monitoring are excluded.

Validate by fixing connection reuse/admission control, measuring query plans
with the retained order volume, then repeating 25/50/100u and a sustained
approximately 39-RPS test. Also run a separate full checkout scenario.
Require stable latency/error behavior, pool and CPU headroom, and repeated
runs before adopting the plan. Safe 409 behavior must not hide overselling.

### Cleanup and portfolio artifacts

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

```text
phase=Healthy ready=5
```

Load Jobs and the Locust ConfigMap were deleted, and test Redis holds
were flushed. Application manifests were unchanged; ArgoCD continues to follow
`feature/lab9`, preserving PostgreSQL persistence.

Committed artifacts: root `locustfile.py`, this reliability review, and
`submissions/runbooks/quickticket-handbook.md`. Raw experiment files and copies
of older submissions remain local evidence and are not included in this PR.
