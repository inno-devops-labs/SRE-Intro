# QuickTicket Reliability Review

**Student:** Diana Kalugina

**Date:** 2026-10-12

## 1. SLO Compliance

The availability target is 99.5% of user requests without 5xx responses. The latency target is 95% of requests below 500 ms.

| SLO | Target | Observed over recent lab window | Steady state | Status |
|---|---:|---:|---:|---|
| Availability | 99.5% | 94.1% | 100% | missed over the full window, healthy at steady state |
| Latency below 500 ms | 95% | 95.1% | 99.8% | met overall and at steady state |

The full-window availability is reduced mainly by deliberate failures from Labs 8 and 9 and by the overload runs in this lab. Under normal mixed traffic the system returned to zero 5xx and sub-500 ms latency.

The error budget for a 99.5% availability target is small, so the chaos and overload periods consume it very quickly. I therefore treat the steady-state window separately from controlled failure testing.

## 2. Load Test Results

Locust ran inside the cluster against `http://gateway:8080`, so traffic passed through the Kubernetes Service and was distributed across all five Gateway replicas. Redis was flushed before each run.

The database continued from Lab 9 with roughly 19.5k orders, so the load test reflects the state accumulated by the previous labs rather than a freshly seeded database.

| Users | Ramp | RPS | Goodput | p50 | p95 | p99 | 5xx error rate | 409 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.7 | 7.7 | 15 ms | 27 ms | 76 ms | 0% | 0 |
| 20 | 4/s | 15.0 | 15.0 | 13 ms | 61 ms | 170 ms | 0% | 0 |
| 30 | 5/s | 22.3 | 22.3 | 11 ms | 116 ms | 270 ms | 0% | 0 |
| **40** | 5/s | **28.1** | 27.7 | 12 ms | 395 ms | **580 ms** | **1.4%** | 12 |
| 50 | 5/s | 31.0 | 26.6 | 185 ms | 590 ms | 740 ms | 14.2% | 0 |
| 100 | 10/s | 59.4 | 29.9 | 240 ms | 650 ms | 830 ms | 49.6% | 0 |
| 200 | 20/s | 60.1 | 19.5 | 1.5 s | 4.1 s | 7.6 s | 66.8% | 0 |

The breaking point is approximately **40 users / 28 RPS**. This is the first level where both failure criteria are exceeded: 5xx is above 0.5% and p99 is above 500 ms. The last healthy level is 30 users at about 22 RPS.

Above the breaking point, offered traffic continues to increase but successful throughput stays close to 27-30 RPS. This indicates a backend saturation point rather than a limitation in the Locust generator.

### Why the system reaches the ceiling

The main bottleneck is Postgres CPU. The `/events` path aggregates data from the growing `orders` table, which contains about 19.5k rows after Labs 8 and 9. The cost of this query grows with the amount of order history.

At 40 users, Postgres approaches its 200m CPU limit. The Events service then spends more time waiting for database work and its connection pool becomes pressured. Gateway and Payments still have significant CPU headroom.

This continues the Lab 9 observation that Postgres is stateful and operationally important: the PVC fixed pod-restart data loss, but it does not solve capacity limits.

## 3. DORA Metrics

| Metric | Value | Source |
|---|---:|---|
| Deployment frequency | about 11 production changes over 14 days, ~0.8/day | Git history, Argo CD sync history, Gateway Rollout revisions |
| Lead time for changes | about 2 min 8 s for a normal code-to-cluster change | CI build plus Argo CD reconciliation |
| Change failure rate | about 12.5% | one remediation-required change out of eight deployment changes |
| Time to restore service | 1.21 s to stable-only traffic after canary abort; 9.74 s to full stable capacity | Lab 7 |
| Git revert recovery | about 17 s | Lab 7 comparison with Lab 5 |
| Postgres recovery without PVC | 171 s | Lab 9 |
| Postgres recovery with PVC | 13 s | Lab 9 |

The deployment and recovery numbers show that progressive delivery gives much faster recovery than rebuilding state from backup. Lab 7 already demonstrated that the healthy ReplicaSet can immediately take traffic back, while Lab 9 showed why persistent storage matters for stateful workloads.

The failed automated canary from Lab 7 is included in the change-failure calculation because it required rejecting the release. The AnalysisTemplate prevented that failed revision from reaching full traffic.

## 4. Top 3 Reliability Risks

1. **Postgres CPU becomes the capacity ceiling as the orders table grows.**
   The read path performs work proportional to accumulated order history. At around 28 RPS this begins producing latency and 5xx responses. I would replace the repeated aggregate with a maintained sold-ticket counter or a short cache, then raise the Postgres CPU limit.

2. **Deep dependency health can turn partial degradation into a larger outage.**
   Gateway health depends on downstream services, and Events depends on its own dependencies. A slow or unavailable dependency can therefore make otherwise healthy pods NotReady. I would separate `/livez` from `/readyz`, keep liveness local to the process, and use readiness only for dependencies required to serve the current operation.

3. **Monitoring coverage is incomplete.**
   Lab 8 added `GatewayPayP99High` for payment latency above 0.6 s for 40 s, but this covers only one path and there is no complete alert-routing setup for all failure modes. I would add Alertmanager, a blackbox Service probe, Postgres saturation alerts, pool-exhaustion metrics, and user-path error alerts.

## 5. Toil Identification

| Toil | Frequency | Automation | Benefit |
|---|---:|---|---|
| Flushing Redis / resetting test inventory before repeated load and chaos runs | more than 10 times across Labs 8-10 | pre-test script or Job init step | removes manual reset work and prevents stale holds from contaminating results |
| Watching canary state and deciding whether to promote or abort | repeated through Lab 7 rollout steps | AnalysisTemplate on error rate plus latency | removes several minutes of manual observation from each deployment |
| Checking that services returned to healthy state after fault injection | after every chaos and DR action | one steady-state verification script using kubectl + Prometheus queries | reduces repeated post-test checks and catches forgotten fault settings |
| Running manual database backup / copy / restore commands | repeated through Lab 9 | existing 5-minute CronJob plus scripted restore verification | reduces recovery steps and caps logical-backup RPO |

## 6. Monitoring Gaps

The payment latency gap identified in Lab 8 was partially addressed by `GatewayPayP99High`. It correctly moved from pending to firing when payment p99 stayed around 0.84 s.

Important gaps still remain:

- no end-to-end blackbox probe through the Gateway Service;
- no Postgres exporter or direct alert for CPU throttling;
- no useful Events connection-pool saturation metric;
- no alert for sustained throughput loss while requests are still returning 200;
- no alert for Redis availability;
- no single incident-routing path that combines the Prometheus rules into an Alertmanager workflow;
- user-visible errors should be measured at the outer request boundary so handler failures cannot bypass application counters.

The alert that would have detected the Lab 10 failure earliest is a **Postgres CPU throttling / saturation alert**, followed by an Events pool-exhaustion alert. Those signals rise before the system reaches the visible 5xx ceiling.

## 7. Capacity Plan

Current reliable capacity is approximately **22 RPS** with 0% 5xx and p99 around 270 ms. The system begins to break near **28 RPS**.

The target for 2x traffic is about **45 RPS** while keeping 5xx below 0.5% and p99 below 500 ms.

The highest-leverage change is to remove the growing aggregation cost on `/events`. After that:

- Postgres: 1 replica, raise to 250m request / 500m limit, 512Mi memory limit;
- Events: 2 replicas, 150m request / 300m limit each;
- Gateway: 4 replicas, 50m request / 200m limit each;
- Payments: 2 replicas, mainly for availability rather than CPU;
- Redis: 1 replica is enough for current capacity, but persistence or replication should be added if reservation availability must survive a node loss.

At approximately $5 per ordinary pod per month, the current layout is about $45/month. The proposed layout is 10 application/database pods, about $50/month, plus roughly $5/month for the larger Postgres allocation, for a total near **$55/month**.

---

## Task 2 - Capacity Plan with Numbers

### Per-pod CPU at the breaking point

The following values were observed around the 40-user run:

```text
gateway-1   34m
gateway-2   25m
gateway-3   22m
gateway-4   20m
gateway-5   18m
events      84m
payments     9m
postgres   158m
redis        7m
```

Postgres is the CPU-constrained service. Events is the second most loaded component. Payments, Redis, and the Gateway replicas still have significant headroom.

At 50-100 users, Postgres approaches roughly 180-195m of its 200m limit and the Events service reaches roughly 125-175m.

### Concrete 2x plan

| Service | Current | 2x plan | Resource target |
|---|---|---|---|
| Postgres | 1 x 200m | 1 x larger instance | 250m request / 500m limit, 512Mi |
| Events | 1 x 200m | 2 replicas | 150m request / 300m limit each |
| Gateway | 5 x 200m | 4 replicas | 50m request / 200m limit each |
| Payments | 1 x 200m | 2 replicas | 50m request / 200m limit each |
| Redis | 1 x 200m | 1 replica | 50m request / 200m limit |

The Postgres connection budget remains acceptable with two Events replicas and a pool of roughly 10 connections per replica. If the Events tier grows further, PgBouncer becomes useful to keep connection count controlled.

### Scaling order

1. remove the expensive per-request order aggregation from `/events`;
2. raise the Postgres CPU limit to 500m;
3. make Events pool behavior wait with a bounded timeout instead of failing immediately;
4. separate liveness and readiness;
5. scale Events and Payments to two replicas;
6. re-run Locust at 60 and 80 users and require 5xx < 0.5% and p99 < 500 ms.

---

## Bonus - SRE Handbook

The operational handbook is in `submissions/runbooks/quickticket-handbook.md`.

---

## Final Verification

- [x] Locust scenario committed at repository root
- [x] 10 / 50 / 100-user load levels documented
- [x] breaking point identified near 40 users
- [x] 409 inventory responses separated from 5xx
- [x] DORA metrics calculated
- [x] three reliability risks documented
- [x] repeated toil and automation opportunities documented
- [x] monitoring gaps documented
- [x] detailed 2x capacity plan included
- [x] SRE handbook included
