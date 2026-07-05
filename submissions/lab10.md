# QuickTicket Reliability Review

## Task 1 - Load Testing and Reliability Review

This section is the main reliability review for QuickTicket. I treated the load tests less like a benchmark contest and more like an SRE exercise: separate expected product errors from real service failures, find the first clear limit, and connect that limit to an operational risk.

### 1. SLO Compliance

| SLO | Target | Observed | Status |
| --- | --- | --- | --- |
| Availability at normal load | 5xx rate below 0.5% | 10u: 0%; 50u: 0% 5xx | Met |
| Availability at stress load | 5xx rate below 0.5% | 100u: 1017 server failures out of 3683 requests, about 27.6% 5xx | Failed |
| Latency at normal load | p99 below 500ms | 10u: 14ms; 50u: 78ms | Met |
| Latency at stress load | p99 below 500ms | 100u: 1200ms | Failed |
| Recovery evidence | Service can recover after known failure mode | Lab 9 restore recovered `/events` from 502 to 200; Lab 10 load failure recovered after load stopped | Met, but still manual |

QuickTicket behaves well at low and medium load, but the 100-user run shows a sharp failure mode rather than a smooth slowdown. Latency jumps, gateway responses turn into 502/503, and the events service starts logging database connection pool exhaustion. That is the main reliability story from this lab.

### 2. Load Test Results

Locust ran inside the cluster against `http://gateway:8080`, so traffic went through the Kubernetes Service instead of a local port-forward. That matters because a port-forward can pin traffic to one backend and produce misleading capacity numbers. I also flushed Redis before each run so old reservation holds would not make the next run look worse than it really was.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 inventory conflicts |
| ----: | ---: | --: | --: | --: | --: | -------------: | ----------------------: |
| 10 | 2/s | 7.85 | 6ms | 9ms | 14ms | 0% | 0 |
| 50 | 5/s | 37.14 | 7ms | 14ms | 78ms | 0% | 25 |
| 100 | 10/s | 61.52 | 230ms | 750ms | 1200ms | about 27.6% | 15 |

Evidence from `load-10`:

```text
Aggregated 469 0(0.00%) | Avg 6 Min 4 Max 55 Med 6 | 7.85 req/s
Aggregated percentiles: 50% 6, 95% 9, 99% 14
```

Evidence from `load-50`:

```text
Aggregated 2221 25(1.13%) | Avg 8 Min 4 Max 204 Med 7 | 37.14 req/s
Aggregated percentiles: 50% 7, 95% 14, 99% 78
Error report: 25 POST /events/5/reserve HTTP 409 Conflict
```

The 50-user failures were expected product behavior: event 5 ran out of available tickets and returned 409. I did not count those against the availability SLO because the system was still answering correctly.

Evidence from `load-100`:

```text
Aggregated 3683 1032(28.02%) | Avg 267 Min 3 Max 1825 Med 230 | 61.52 req/s
Aggregated percentiles: 50% 230, 95% 750, 99% 1200
Error report:
650 GET /events: HTTP 502
173 POST /events/3/reserve: HTTP 500
51 POST /events/5/reserve: HTTP 500
85 GET /health: HTTP 503
58 reserve requests: HTTP 502
15 POST /events/5/reserve: HTTP 409
```

Breaking point: 100 users, about 61.5 RPS. At that level both conditions were breached: p99 exceeded 500ms and 5xx rate exceeded 0.5%. I did not run 200 users because 100 users already crossed the failure threshold clearly; pushing harder would only create more noise after the limit was already visible.

The events service log showed the main failure mode:

```text
psycopg2.pool.PoolError: connection pool exhausted
```

### 3. DORA Metrics

| Metric | Source data | Result | Notes |
| --- | --- | --- | --- |
| Deployment frequency | `kubectl get rs -l app=gateway` showed 12 gateway ReplicaSets over about 7 days | About 1-2 deployable changes/day | This includes canary attempts and retained ReplicaSets. |
| Lead time for changes | CI build plus ArgoCD polling interval from previous labs | Roughly 3-5 minutes after image/build success | This is approximate because the lab setup uses ArgoCD polling rather than a full production deployment ledger. |
| Change failure rate | `kubectl get analysisrun` showed 2 Successful and 2 Failed AnalysisRuns | 50% for canary experiments | This is intentionally high because Lab 7 included failed canaries for learning. |
| Time to restore service | Lab 9: 14s without PVC restore path; 10s with PVC pod restart path; GitOps revert path about 3 minutes | Seconds for pod-level recovery, minutes for GitOps rollback | Database restore is still too manual unless automated backup/PITR is added. |

These numbers are approximate, but they are still useful. They show that deployment mechanics are fast enough for a lab environment, while change quality and database recovery automation are the weaker parts.

Source snippets:

```text
$ kubectl get rs -l app=gateway
12 ReplicaSets, with gateway-745855f857 at 5 ready replicas

$ kubectl get analysisrun -o wide
gateway-56d9cbd5c9-6-2    Successful
gateway-58ffc77799-10-2   Successful
gateway-8c4bddcbb-8-2     Failed
gateway-d578f4fbb-5-2     Failed

$ git log --oneline origin/backup/main-before-sync | wc -l
47
```

### 4. Top 3 Reliability Risks

1. Events service database connection pool exhaustion. At 100 users the service logged `PoolError: connection pool exhausted`, which caused 500s, 502s, and 503s. The fix is to size the pool deliberately, add backpressure/timeouts, and consider PgBouncer before adding many more app replicas.
2. Single-instance stateful dependencies. Postgres and Redis are still single pods. A PVC protects Postgres data from pod deletion, but it does not provide database high availability. The fix is managed Postgres or a replicated Postgres setup, plus Redis persistence/replication if reservations become critical.
3. Alert coverage is still too narrow. Error-rate alerts catch 5xx, but latency and saturation can become bad before outright failure. The fix is latency burn alerts, DB pool saturation metrics, Redis availability alerts, and dashboards that separate 409 inventory conflicts from 5xx.

The first risk is the most urgent because it already happened in the load test. The other two are the things that would make a real incident longer and more manual than it needs to be.

### 5. Toil Identification

| Toil | How often I did it | How to automate | What it saves |
| --- | --- | --- | --- |
| Re-seeding Postgres after pod restarts before PVC | More than 3 times across Labs 4, 8, and 9 | Keep Postgres on PVC, add a seed Job for dev clusters, and make restore commands scripted | Avoids manual `kubectl exec ... psql < seed.sql` and reduces broken test starts |
| Re-running port-forwards and manual smoke checks | More than 3 times while testing gateway/Postgres | Add Make targets or scripts for smoke tests and avoid port-forward for load tests | Faster feedback and fewer misleading single-pod tests |
| Manually inspecting rollouts and Prometheus queries | Repeated during canary and chaos labs | Keep AnalysisTemplates, alert rules, and dashboard links in runbooks | Less manual watching, more repeatable rollback decisions |

The repeated manual work was small each time, but it adds up. The practical lesson is that the second or third repetition is already a signal to write a script, a Job, or a runbook.

### 6. Monitoring Gaps

- I wanted a direct metric for the events service DB connection pool: active connections, waiting threads, and pool exhaustion count. That would have caught the actual Lab 10 breaking point earlier.
- The dashboards should split expected 409 conflicts from 5xx. At 50 users, Locust showed failures, but they were inventory conflicts, not reliability failures.
- Latency alerts should exist alongside error-rate alerts. A slow dependency can burn user trust before it starts returning 5xx.
- Redis reservation-hold count and TTL distribution would have helped during chaos/load tests because stale holds can look like capacity loss.
- Postgres connection count and query latency should be visible. The current bottleneck is closer to events/Postgres than gateway CPU.

An alert that would have caught the actual failure:

```text
events_db_pool_exhausted_total > 0
or
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) > 0.005
```

## Task 2 - Capacity Plan with Numbers

This section turns the reliability review into a concrete scaling plan. The key point is that the system did not fail because every pod was out of CPU. It failed because the events service could not get enough database connections under concurrency.

### 7. Capacity Plan

Current ceiling: about 61 RPS at 100 Locust users. I would treat the safe operating point as the 50-user result, about 37 RPS with p99 78ms and 0% 5xx, because the next tested level failed hard.

CPU at the breaking point:

```text
$ kubectl top pods -l app=gateway
gateway-745855f857-9cdkq   18m   41Mi
gateway-745855f857-rqnbd   32m   48Mi
gateway-745855f857-tjdns   54m   42Mi
gateway-745855f857-x4hr7    6m   41Mi
gateway-745855f857-xgq9p   57m   55Mi
gateway-776866fb96-2rdfg   29m   46Mi

$ kubectl top pods -l app=events
events-699fd88c64-hdnvr    89m   58Mi

$ kubectl top pods -l app=payments
payments-6645fb9d6d-6pf57   7m   41Mi
```

The constrained service is events, but not primarily by CPU. CPU was only 89m; the observed failure was DB connection pool exhaustion. Gateway had moderate CPU spread across replicas, and payments was mostly idle.

For 2x traffic, I would plan for roughly 75 RPS safely, then retest:

| Component | Current | 2x plan | Resource plan | Reason |
| --- | --- | --- | --- | --- |
| gateway | Rollout desired 5 | Keep 5 initially, HPA 5-8 | request 75m CPU / 96Mi, limit 250m / 256Mi | Gateway CPU was not the bottleneck. |
| events | 1 pod | 3 pods after DB pool/PgBouncer fix | request 150m CPU / 128Mi, limit 500m / 512Mi | Events owns DB/Redis work and failed first. |
| payments | 1 pod | 1-2 pods | request 50m CPU / 64Mi, limit 200m / 256Mi | Payment service was idle in this test. |
| Redis | 1 pod | Keep single pod for dev; replicated Redis for production | request 50m CPU / 64Mi | Single pod is OK for this lab, not for production reservations. |
| Postgres | 1 pod + PVC | Keep one primary but add PgBouncer and raise max connections carefully | monitor connections and query latency | Scaling events without DB pooling would move the bottleneck to Postgres. |

Rough cost estimate using the lab's $5/pod/month assumption:

| Plan | Pods counted | Cost |
| --- | ---: | ---: |
| Current app path | 5 gateway + 1 events + 1 payments + 1 Redis + 1 Postgres = 9 pods | about $45/month |
| 2x plan | 5 gateway + 3 events + 2 payments + 1 Redis + 1 Postgres + 1 PgBouncer = 13 pods | about $65/month |

The first engineering change should not be blind horizontal scaling. It should be connection-pool/backpressure work in events, followed by another Locust run at 100 and 150 users. After that retest, replica counts can be adjusted with more confidence.

## Bonus Task - SRE Handbook

I chose Bonus Option B and added `submissions/runbooks/quickticket-handbook.md`. The handbook is intentionally short and operational: architecture, deploy flow, monitoring checks, incident response, and backup/restore.
