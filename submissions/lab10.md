# Lab 10 — SRE Portfolio & Reliability Review

**Student:** Gleb Shvetsov

**GitHub:** `L10nff`

**Working branch:** `feature/lab10`

## Test environment and method

The tests ran from Kubernetes Jobs inside the `k3d-quickticket` cluster against
`http://gateway:8080`. This ensured that kube-proxy distributed requests across
all five Gateway replicas. No Gateway port-forward was used for load traffic.

The supplied Locust scenario was copied to the repository root. Its task mix
was seven event-list requests, two reservation requests, and one health request.
Reservations were distributed across events 3 and 5. Redis was flushed before
every run, so stale holds from an earlier run could not distort the result.

Each load level ran for 60 seconds. The 10-user run used a ramp of 2 users/s,
the 50-user run used 5 users/s, and the 100-user run used 10 users/s. Locust
2.43.4 ran as an in-cluster Job with `--only-summary`.

To reduce local resource use, the full Grafana/operator monitoring stack and
Argo CD controllers were temporarily scaled to zero. The lightweight in-cluster
Prometheus required for measurements remained available.

## Task 1 — Load Testing and Reliability Review

The required QuickTicket reliability review is organized into the seven
sections below. Sections 1-6 contain the load-test, DORA, risk, toil, and
monitoring analysis. Section 7 is the detailed numerical capacity plan from
Task 2.

### 1. SLO Compliance

| SLO | Target | Observed | Status |
|---|---:|---:|---|
| Gateway availability at reference load | >= 99.5% non-5xx | 100% at 10 users | Met |
| Gateway p99 latency at reference load | < 500 ms | 370 ms at 10 users | Met |
| Capacity error budget | < 0.5% 5xx | 9.45% at 50 users | Failed at capacity ceiling |
| Critical alert detection | < 5 minutes | 182 seconds in Lab 6 | Met |
| Canary rollback to full stable capacity | < 60 seconds | 12.13 seconds in Lab 7 | Met |
| Postgres pod recovery with PVC | < 60 seconds | 4.79 seconds in Lab 9 | Met |

QuickTicket met the availability and latency objectives at the 10-user
reference load. It did not degrade gradually enough at higher load: the first
tested step above the healthy level, 50 users, crossed both the 5xx and latency
limits. This makes the current capacity boundary too sharp for safe operation
without an earlier saturation alert and additional Events capacity.

The 10-user Locust process reported zero failed requests. Prometheus contained
some 503 counters from cluster startup and the initial Locust image pull before
the timed process began; those background counters were excluded from the
load-specific result.

### 2. Load Test Results

#### Results

| Users | Ramp | Requests | RPS | p50 | p95 | p99 | 5xx error rate | 409 inventory |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 458 | 7.67 | 11 ms | 120 ms | 370 ms | 0.00% (0/458) | 0.00% (0/458) |
| 50 | 5/s | 2,001 | 33.44 | 13 ms | 700 ms | 1,500 ms | 9.45% (189/2,001) | 0.10% (2/2,001) |
| 100 | 10/s | 3,293 | 55.18 | 290 ms | 1,500 ms | 2,200 ms | 40.48% (1,333/3,293) | 0.00% (0/3,293) |

The 100-user Locust failure total was 1,395. Of these, 1,333 were HTTP 5xx
responses and 62 were connection-refused transport failures. The table reports
only the 5xx fraction in the 5xx column. The transport failures are additional
evidence of overload, but they were not incorrectly counted as either 409 or
HTTP 5xx responses.

#### Representative Locust output

```text
10 users:
Aggregated   requests=458   failures=0 (0.00%)
average=31ms median=11ms p95=120ms p99=370ms rps=7.67

50 users:
Aggregated   requests=2001  failures=191 (9.55%)
average=165ms median=13ms p95=700ms p99=1500ms rps=33.44
HTTP 500/502/503=189, HTTP 409=2

100 users:
Aggregated   requests=3293  failures=1395 (42.36%)
average=430ms median=290ms p95=1500ms p99=2200ms rps=55.18
HTTP 500/502/503=1333, connection refused=62, HTTP 409=0
```

#### Breaking point

The first tested breaking point was **50 users at 33.44 RPS**. At that level,
the 5xx rate was 9.45%, above the 0.5% threshold, and p99 was 1,500 ms, above
the 500 ms threshold. Because the breaking point was already found, a 200-user
test was unnecessary. The mandatory 100-user test was still completed and
confirmed further nonlinear degradation.

At 50 users, failures included 500 responses on reservation, 502 responses on
event listing and reservation, and 503 responses from health checks. Only two
requests returned 409, so inventory exhaustion did not explain the failure.

### 3. DORA Metrics

| Metric | Measured value | Interpretation |
|---|---:|---|
| Deployment frequency | 6 Gateway revisions retained in the cluster | Frequent exercise deployments; not a production time series |
| Lead time for changes | approximately 3 minutes | CI build plus the Argo CD polling/synchronization interval |
| Change failure rate | 33.3% (2 failed changes / 6 revisions) | Both failures were intentional bad-version exercises |
| Mean time to restore service | 12.13 seconds | Manual Rollout abort to five stable endpoints |

Source data:

```text
Gateway ReplicaSets: 6
gateway-74d7cff5bd
gateway-769fbf8874
gateway-7db8bbbcc6
gateway-7fbfff666d
gateway-845778cd78
gateway-9dfff959b

AnalysisRuns:
gateway-769fbf8874-7-2   Failed
gateway-9dfff959b-6-2    Successful

Manual bad-version rollout: aborted
Lab 7 abort to all five stable endpoints: 12.13 seconds
Lab 5 Git-revert recovery: 147 seconds
upstream/main commits at measurement time: 34
```

The failed AnalysisRun and the manual bad-version abort are two failed changes
among six recorded Gateway revisions. This 33.3% rate should not be treated as
a forecast of production quality because the course deliberately introduced
bad releases to test recovery. It does demonstrate that automatic analysis and
fast rollback materially reduce the impact of a bad change.

The 12.13-second Rollout recovery was about 12.1 times faster than the
147-second Git-revert recovery measured in Lab 5. The direct Rollout abort kept
the stable ReplicaSet in place and avoided waiting for a commit, push, Argo CD
poll, and replacement pod startup.

### 4. Top 3 Reliability Risks

1. **Events is a single-replica bottleneck.** One Events pod served all read and
   reservation traffic and reached 117m CPU at the first failure point. Scale
   Events horizontally, size its database pool deliberately, and alert on its
   saturation, queueing, and upstream error rate.
2. **Stateful dependencies still have single points of failure.** The PVC
   protects Postgres from a pod restart but not from node, volume, or database
   corruption; Redis is also a single pod. Use a replicated/managed Postgres
   service with tested backups and a replicated Redis deployment with failover.
3. **Dependency degradation is visible too late.** Error-rate alerts catch hard
   failures, but slow successful calls and pool saturation can violate the
   latency SLO first. Add per-route latency, Events pool utilization, dependency
   timeout, and saturation alerts before the error budget is consumed.

### 5. Toil Identification

| Repeated manual task | Observed frequency | Automation proposal | Expected saving |
|---|---:|---|---|
| Recreate port-forwards after restarts | more than 10 sessions | Provide a single development command with process supervision and readiness checks | 1-2 minutes and fewer stale sessions per restart |
| Run health, pod, log, and Prometheus checks separately | more than 15 incident checks | Create one diagnostic script that timestamps and bundles service health, recent logs, rollout state, and golden signals | 3-5 minutes per investigation |
| Watch and manually promote/abort canaries | more than 5 rollout steps | Keep the AnalysisTemplate in the delivery pipeline and gate promotion on error rate plus latency | Several minutes and less operator attention per release |
| Re-seed or manually restore Postgres before persistent storage | more than 3 recoveries | Keep the PVC, scheduled backups, retention, and a tested restore Job | 5-10 minutes per database recovery |

The highest-value automation is the combined diagnostic bundle. It reduces
human correlation work during every incident and produces evidence in the same
format for the postmortem.

### 6. Monitoring Gaps

- A per-route p95/p99 latency alert was missing. It would have detected slow
  Payments in Lab 8 even while calls still returned 200.
- Events exposed no direct connection-pool utilization, queue depth, or pool
  acquisition latency. These metrics would have explained the 50-user failure
  before Gateway began returning 502.
- HTTP 409 inventory conflicts must be charted separately from 5xx. Combining
  them would misclassify expected business behavior as an availability outage.
- Dependency timeout totals should be labeled by dependency and route. A spike
  in `events` timeouts would have identified the load-test bottleneck directly.
- Redis and Postgres need persistence/failover health, backup age, and restore
  verification metrics, not only process health.
- Saturation alerts should fire before p99 exceeds 500 ms: Events CPU above
  70% of its limit, DB pool usage above 80%, or sustained Gateway dependency
  timeouts would be suitable early signals.

The alert most likely to catch the actual load-test failure would combine
Events pool saturation with Gateway `/events` and reservation p99 above 500 ms
for two minutes. Error rate should remain a second, faster severity escalator.

## Task 2 (Optional) — Capacity Plan with Numbers

### 7. Capacity Plan

#### Per-pod headroom at the breaking point

The CPU sample captured while the 50-user breaking-point test was running was:

```text
gateway-9dfff959b-4wjq7   56m   41Mi
gateway-9dfff959b-84shv   40m   41Mi
gateway-9dfff959b-js9hm   33m   41Mi
gateway-9dfff959b-ldvgp   36m   40Mi
gateway-9dfff959b-thbst   30m   40Mi
events-68cc9578bd-wxh5n  117m   51Mi
payments-5cfc7f5f5-m2sgw  17m   35Mi
postgres-57d896d6bd-8mtx5 89m   39Mi
redis-bcc6dc4d-g9ndf      10m    4Mi
```

Events was the most CPU-loaded application dependency and it had only one
replica. PostgreSQL was second. Payments and Redis were mostly idle. Gateway
traffic was distributed across all five replicas, so adding more Gateway pods
alone would not remove the observed dependency bottleneck.

#### Current capacity

The measured ceiling is **33.44 RPS at 50 users**, where both SLO thresholds
were already exceeded. The 10-user result, 7.67 RPS with 0% 5xx and 370 ms p99,
is the demonstrated healthy reference point. Production admission control
should leave at least 30% headroom below a re-tested healthy ceiling.

#### Plan for approximately 2x tested ceiling

The target is approximately **67 RPS** with p99 below 500 ms and 5xx below
0.5%.

| Component | Current | Proposed | Requests / limits per pod | Reason |
|---|---:|---:|---|---|
| Gateway | 5 | 10 | 100m/300m CPU, 128Mi/256Mi memory | Preserve fan-out capacity and one-pod failure headroom |
| Events | 1 | 4 | 150m/500m CPU, 128Mi/384Mi memory | Remove the measured single-pod bottleneck |
| Payments | 1 | 2 | 50m/200m CPU, 64Mi/256Mi memory | Availability headroom; current CPU was only 17m |
| Redis | 1 | 3 | 50m/200m CPU, 64Mi/256Mi memory | Primary plus replicas/Sentinel or managed failover |
| Postgres | 1 | 2 nodes | 250m/1 CPU, 512Mi/1Gi memory | Managed primary/standby with adequate IOPS |

Four Events replicas with `DB_MAX_CONNS=10` could open 40 connections. The
current Postgres `max_connections` is 100, but unconstrained per-pod pools make
future scaling risky. Add PgBouncer, cap the application pools against a shared
budget, and monitor wait time. Read replicas do not help reservation writes, so
the primary still needs sufficient CPU and storage IOPS.

At the course estimate of $5 per application/database pod per month, the
current 9 pods cost roughly $45/month. The proposed 21 pods/nodes cost roughly
$105/month, an incremental $60/month. Managed storage, load balancers, backups,
and cross-zone traffic are excluded. The scale-up should be applied only after
a repeat test proves that Events and the database pool, rather than another
limit, can sustain 67 RPS.

#### Validation criteria

The plan is complete only after repeating the same in-cluster 60-second Locust
test at 67 RPS or higher and demonstrating:

- p99 below 500 ms;
- 5xx below 0.5%;
- no connection-refused errors;
- 409 reported separately;
- Events and database pool below 70% sustained saturation;
- one-pod failure does not violate the SLO.

## Bonus Task — 5-minute Walkthrough (Option B)

Bonus Option B was completed as
`submissions/runbooks/quickticket-handbook.md`. The handbook contains:

- an architecture diagram and component summary;
- the exact GitOps deployment and verification flow;
- golden-signal queries and monitoring guidance;
- a condensed incident-response and escalation runbook;
- the PostgreSQL backup and restore procedure from Lab 9.

## Conclusion

QuickTicket is reliable at the 10-user reference load and has fast recovery
mechanisms, but the current topology is not yet safe at 50 concurrent users.
The experiment found a concrete capacity ceiling instead of inferring one from
resource limits. Scaling Events, controlling database connections, and adding
latency and saturation alerts are the highest-priority improvements. The
existing canary analysis, persistent database storage, and automated backups
provide a strong recovery foundation, while the accompanying handbook turns
the previous lab procedures into a reusable operating guide.
