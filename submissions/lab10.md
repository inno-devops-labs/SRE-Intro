# QuickTicket Reliability Review

## 1. SLO Compliance

QuickTicket previously defined two primary service-level objectives:

| SLO | Target | Observed | Status |
|---|---:|---:|---|
| Availability | 99.5% non-5xx over 7 days | 100% during healthy operation | Met |
| Latency | 95% of requests below 500 ms | 100% during healthy baseline | Met |
| Availability during induced outage | 99.5% | 96.82% | Violated |
| Availability at 100-user breaking point | 99.5% | approximately 78.1% non-5xx | Violated |
| Latency at 100-user breaking point | 95% below 500 ms | p95 440 ms, p99 570 ms | SLO approximately met, but capacity threshold violated |

The availability SLI is defined as the percentage of gateway requests that do
not return a 5xx response.

The latency SLI is defined as the percentage of gateway requests completing
within 500 ms.

During normal operation both SLOs were satisfied.

A previous controlled service failure reduced availability to approximately:

```text
96.82%
```

with an error-budget burn rate of approximately:

```text
6.36x
```

The 100-user Locust test caused a much more severe overload. Out of 3982 total
requests, 873 returned genuine 5xx responses.

```text
Availability = 1 - (873 / 3982)
             ≈ 78.08%
```

HTTP 409 reservation conflicts were not counted as availability failures because
they represent expected product behavior rather than infrastructure failure.

The 100-user test had:

```text
p95 = 440 ms
p99 = 570 ms
```

The 95%-under-500-ms latency SLO was therefore approximately still satisfied,
but the separate Lab 10 capacity threshold of p99 below 500 ms was violated.

---

## 2. Load Test Results

Locust was executed from inside the Kubernetes cluster so traffic passed through
the gateway Service and was distributed across all five gateway replicas.

Redis was flushed between tests so reservation holds from previous runs did not
contaminate later measurements.

### Load Test Table

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 conflicts |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.70 | 5 ms | 8 ms | 11 ms | 0% | 0 |
| 50 | 5/s | 37.25 | 5 ms | 8 ms | 18 ms | 0% | 28 |
| 100 | 10/s | 66.55 | 130 ms | 440 ms | 570 ms | 21.92% | 77 |

### 10 Users

The 10-user test completed 459 requests at:

```text
7.70 requests/second
```

with:

```text
p50 = 5 ms
p95 = 8 ms
p99 = 11 ms
```

There were no failures.

### 50 Users

The 50-user test completed 2229 requests at:

```text
37.25 requests/second
```

with:

```text
p50 = 5 ms
p95 = 8 ms
p99 = 18 ms
```

There were 28 HTTP 409 responses on event 5 because its ticket inventory was
exhausted.

There were no genuine 5xx failures.

The system therefore remained healthy at approximately 37 RPS.

### 100 Users

The first 100-user run completed:

```text
3982 requests
66.55 requests/second
```

Latency increased sharply:

```text
p50 = 130 ms
p95 = 440 ms
p99 = 570 ms
max = 780 ms
```

The error report contained:

```text
569 GET /events                  -> 502
152 POST /events/3/reserve       -> 500
54  POST /events/5/reserve       -> 500
27  POST /events/3/reserve       -> 502
7   POST /events/5/reserve       -> 502
64  GET /health                  -> 503
77  POST /events/5/reserve       -> 409
```

The genuine 5xx count was:

```text
569 + 152 + 54 + 27 + 7 + 64 = 873
```

Therefore:

```text
5xx error rate = 873 / 3982
               ≈ 21.92%
```

The 77 HTTP 409 responses were excluded from this error rate.

### Breaking Point

Lab 10 defines the breaking point as the first tested level where either:

```text
5xx error rate > 0.5%
```

or:

```text
p99 latency > 500 ms
```

The 50-user level remained healthy:

```text
37.25 RPS
0% 5xx
18 ms p99
```

The 100-user level violated both thresholds:

```text
66.55 RPS
21.92% 5xx
570 ms p99
```

Therefore:

```text
Highest tested healthy throughput: 37.25 RPS
First observed failing throughput:  66.55 RPS
Breaking-point user count:          100 users
```

The true sustainable ceiling lies somewhere between approximately 37 and 67
RPS.

---

## 3. DORA Metrics

### Source Data

The current cluster retained four gateway ReplicaSets:

```text
gateway-684b75c5f5
gateway-7fc67f59b8
gateway-8479c9c4f9
gateway-b45bb474c
```

Relevant deployment-related Git history included:

```text
1173365 ci: add CI pipeline for QuickTicket
174b390 feat: use ghcr.io images in K8s manifests
23fbf7c feat: add version label to gateway
6ac3146 feat: deploy new gateway version
a5921b9 Revert "feat: deploy new gateway version"
4dafe26 feat(lab7): add canary rollout for gateway
```

The Kubernetes cluster no longer contained old AnalysisRun resources because
the experimental resources had already been cleaned up.

### DORA Table

| Metric | Measurement |
|---|---|
| Deployment Frequency | 4 retained gateway revisions; multiple deployment exercises during Labs 5-7 |
| Lead Time for Changes | approximately 44 s CI + up to 3 min ArgoCD polling, roughly <4 min |
| Change Failure Rate | approximately 2 deliberate failed changes among 6 deployment/recovery exercises, approximately 33% |
| Recovery Time | approximately 1.36 s for automated canary abort; approximately 25 s for Git revert + GitOps recovery |

### Deployment Frequency

The running cluster retains four distinct gateway ReplicaSets, demonstrating
multiple gateway revisions.

The Git history also shows multiple deployment-related changes during the
course, including image updates, GitOps changes, an intentionally broken
deployment, its revert, and the later Argo Rollouts canary configuration.

This is not a production workload with daily business releases, so the useful
measurement is the number of demonstrated deployment revisions rather than a
long-term releases-per-day value.

### Lead Time for Changes

The GitHub Actions image-build pipeline completed in approximately 44 seconds.

ArgoCD's normal polling interval is approximately three minutes.

A normal automatic path is therefore approximately:

```text
CI build       ≈ 44 seconds
ArgoCD polling ≤ 180 seconds
---------------------------
Worst typical automatic lead time ≈ 224 seconds
                                 ≈ 3 min 44 sec
```

Manual ArgoCD sync can reduce the polling portion significantly.

### Change Failure Rate

Two deliberate failure exercises were observed during the deployment work:

1. a non-existent gateway image that caused `ImagePullBackOff`;
2. a bad canary revision that was aborted.

Using six deployment/recovery exercises from the recorded project work as the
exercise denominator gives:

```text
2 / 6 ≈ 33%
```

This number is intentionally much higher than a normal production change
failure rate because this course deliberately introduced failed deployments to
test recovery behavior.

### Recovery Time

Two recovery mechanisms were measured:

```text
Argo Rollouts automatic abort -> stable traffic:
approximately 1.36 seconds

Git revert + ArgoCD recovery:
approximately 25 seconds
```

Automated progressive delivery therefore recovered substantially faster than
the manual GitOps rollback path.

---

## 4. Top 3 Reliability Risks

### 1. PostgreSQL CPU Saturation

At the 100-user breaking point, PostgreSQL consumed:

```text
188m CPU in sample 1
160m CPU in sample 2
```

against a configured limit of:

```text
200m
```

The first sample therefore reached approximately:

```text
188 / 200 = 94%
```

of the configured CPU limit.

PostgreSQL is the clearest capacity bottleneck.

**Fix:** increase PostgreSQL CPU resources, monitor query latency and
connections, and introduce connection pooling before substantially increasing
application replicas.

### 2. Single Events Replica

The events service increased from:

```text
44m CPU
```

to:

```text
170m CPU
```

against a 200m limit.

At the same time, reservation and event-list requests began returning 500 and
502 responses.

A single events replica therefore has insufficient headroom for the measured
high-load condition.

**Fix:** run multiple events replicas and ensure their combined database
connection pools do not overload PostgreSQL.

### 3. Stateful Dependencies Remain Single Points of Failure

Redis uses very little CPU:

```text
4m
```

so it is not a capacity bottleneck.

However, it remains a single pod. Earlier chaos testing demonstrated that losing
Redis breaks ticket reservations even when event listing remains available.

PostgreSQL is now protected from pod-local data loss by its PVC but is still a
single database instance.

**Fix:** use replicated or managed Redis for production and provide PostgreSQL
HA/replication in addition to backups and persistent storage.

---

## 5. Toil Identification

| Manual toil | Frequency | Automation | Expected saving |
|---|---|---|---|
| Re-seeding PostgreSQL after ephemeral pod loss | More than 3 times during earlier labs | PVC initialization/migrations | Removes repeated manual recovery and seed commands |
| Recreating and managing port-forwards | Repeated throughout monitoring, ArgoCD and DB work | ingress/service exposure or scripted development command | Removes repetitive setup and failed/stale forwarding sessions |
| Manually watching deployments, pods and rollouts | Repeated for most deployment experiments | Argo Rollouts AnalysisTemplates, CI checks and automated health verification | Faster deployments with less operator attention |

### PostgreSQL Re-Seeding

Before the Lab 9 PVC was added, deleting or restarting Postgres could destroy
the database state.

This required repeatedly running commands such as:

```text
psql -U quickticket -d quickticket < app/seed.sql
```

The PVC eliminated this toil for ordinary pod restarts.

### Port-Forward Management

Prometheus, ArgoCD and PostgreSQL development workflows repeatedly required
starting, checking and restarting port-forward processes.

This can be reduced by exposing development endpoints consistently or wrapping
the required forwards in a single script or Make target.

### Manual Rollout Observation

Canary rollouts were manually watched with repeated `kubectl` commands.

An AnalysisTemplate tied directly to application SLOs can make promotion or
rollback decisions automatically and remove much of this operator toil.

---

## 6. Monitoring Gaps

### Latency Alerting

One of the clearest monitoring gaps was exposed during the Lab 8 payment latency
experiment.

The payment service was intentionally delayed by approximately two seconds.

The observed result was:

```text
/pay p99 ≈ 2.485 seconds
5xx ratio = 0
```

An alert based only on errors would therefore report a healthy system while
users experienced multi-second checkout latency.

A production alert should monitor endpoint-specific p95/p99 latency in addition
to error rate.

### Database Saturation

The 100-user load test showed PostgreSQL at approximately 94% of its configured
CPU limit.

A useful alert would therefore include:

```text
Postgres CPU > 80% for several minutes
```

along with database connection-pool saturation and query latency.

### Dependency-Specific Error Rates

During the breaking-point test:

- `/events` returned large numbers of 502 responses;
- `/reserve` returned 500 and 502 responses;
- `/health` returned 503 responses.

Aggregated gateway errors identify that something is wrong but not which
dependency is responsible.

Dashboards and alerts should split failures by path, status, and downstream
service.

### Most Useful New Alert

The most useful immediate addition would be:

```text
gateway p99 latency > 500 ms for 2 minutes
```

combined with a database saturation alert.

This would detect both slow-but-successful dependencies and the capacity
degradation observed during the 100-user test.

---

## 7. Capacity Plan

### Current Capacity

The highest tested healthy level was:

```text
37.25 RPS
```

The first tested failing level was:

```text
66.55 RPS
```

The measured capacity boundary is therefore between approximately 37 and 67
RPS.

For planning purposes, 66.55 RPS is treated as the measured breaking-point
load, not a safe operating target.

A 2x version of that load is approximately:

```text
66.55 × 2 = 133.1 RPS
```

---

# Task 2 — Capacity Plan With Numbers

## Per-Pod Resource Usage at Breaking Point

### CPU Sample 1

| Service | CPU |
|---|---:|
| gateway-1 | 45m |
| gateway-2 | 55m |
| gateway-3 | 47m |
| gateway-4 | 22m |
| gateway-5 | 19m |
| events | 44m |
| payments | 6m |
| postgres | 188m |
| redis | 4m |

### CPU Sample 2

| Service | CPU |
|---|---:|
| gateway-1 | 46m |
| gateway-2 | 55m |
| gateway-3 | 45m |
| gateway-4 | 32m |
| gateway-5 | 53m |
| events | 170m |
| payments | 11m |
| postgres | 160m |
| redis | 4m |

### Bottleneck Analysis

PostgreSQL was the most consistently CPU-constrained service:

```text
peak observed = 188m / 200m = 94%
```

The events service also reached:

```text
170m / 200m = 85%
```

Gateway replicas remained well below their individual limits.

Payments and Redis had substantial unused CPU capacity.

---

## Concrete 2x Capacity Plan

Target:

```text
approximately 133 RPS
```

### Gateway

Current:

```text
5 replicas
request: 50m CPU / 64Mi memory
limit:   200m CPU / 256Mi memory
```

Observed CPU per pod was approximately 19-55m at the breaking point.

For 2x traffic:

```text
6 replicas
request: 100m CPU
limit:   300m CPU
memory request: 64Mi
memory limit:   256Mi
```

Six replicas provide additional failure and traffic-distribution headroom.

### Events

Current:

```text
1 replica
peak observed CPU: 170m / 200m
```

A single replica cannot safely absorb 2x traffic.

Plan:

```text
3 replicas
request: 150m CPU
limit:   400m CPU
memory request: 128Mi
memory limit:   256Mi
```

Three replicas should distribute event and reservation processing while
maintaining headroom.

Database pool sizes must be coordinated because adding events replicas also
multiplies potential PostgreSQL connections.

### Payments

Observed CPU remained extremely low:

```text
6-11m
```

One replica is sufficient from a pure capacity perspective.

For availability, the planned configuration is:

```text
2 replicas
request: 50m CPU
limit:   200m CPU
memory request: 64Mi
memory limit:   256Mi
```

The second replica is primarily for resilience rather than throughput.

### PostgreSQL

Current:

```text
1 replica
request: 50m CPU
limit:   200m CPU
peak observed: 188m
```

PostgreSQL is already close to its current CPU limit.

For approximately 2x traffic:

```text
1 PostgreSQL instance
request: 300m CPU
limit:   750m CPU
memory request: 256Mi
memory limit:   512Mi or higher
```

The database should also use a connection pooler or carefully bounded
per-service connection pools.

Three events replicas with independent pools can otherwise increase connection
pressure even if application CPU capacity improves.

For a production environment, PostgreSQL HA or a managed PostgreSQL service
would also be appropriate.

### Redis

Observed Redis CPU was only:

```text
4m
```

A single Redis instance is sufficient for 2x traffic from a capacity
perspective.

However, Redis remains a single point of failure.

Therefore:

- for this capacity exercise, one Redis pod is sufficient;
- for production reliability, use replicated/managed Redis.

### Replica Plan

| Service | Current | 2x Plan |
|---|---:|---:|
| Gateway | 5 | 6 |
| Events | 1 | 3 |
| Payments | 1 | 2 |
| PostgreSQL | 1 | 1 larger instance |
| Redis | 1 | 1 for capacity, replicated for production HA |

### Rough Cost

Using the lab assumption of approximately:

```text
$5 per pod per month
```

Current application footprint:

```text
5 gateway
1 events
1 payments
1 postgres
1 redis
---------
9 pods

9 × $5 = approximately $45/month
```

Proposed 2x footprint:

```text
6 gateway
3 events
2 payments
1 postgres
1 redis
---------
13 pods

13 × $5 = approximately $65/month
```

A three-node Redis setup instead of one Redis pod would increase this to:

```text
15 pods × $5 = approximately $75/month
```

This estimate intentionally ignores storage, load balancers, managed database
premiums, monitoring infrastructure and data transfer.

---

## Capacity Recommendation

The next scaling priority should not be the gateway.

The measured data indicates this order:

1. increase PostgreSQL CPU capacity and monitor connections;
2. scale events from one replica to three;
3. retain at least five or six gateway replicas;
4. add a second payments replica primarily for availability;
5. keep Redis single-node for this capacity target, but remove the Redis SPOF
   before treating the design as production-ready.

After making these changes, the Locust tests should be repeated rather than
assuming linear scaling.

The goal should be to support approximately 133 RPS while retaining meaningful
headroom and keeping:

```text
5xx < 0.5%
p99 < 500 ms
```

---

# Conclusion

QuickTicket remained very healthy at 50 concurrent users and approximately
37 RPS, with zero server errors and an 18 ms p99.

At 100 users and approximately 67 RPS, the system crossed its capacity limit.
Latency increased sharply and large numbers of 500, 502 and 503 responses
appeared.

CPU measurements identified PostgreSQL and the single events service as the
primary constrained components. Gateway, payments and Redis retained
significant CPU headroom.

The most important reliability improvements are therefore database capacity and
connection management, horizontal scaling of the events service, better
latency/saturation alerting, and removal of remaining stateful single points of
failure.
