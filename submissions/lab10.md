# QuickTicket Reliability Review

Repository: https://github.com/Ars5njo/SRE-Intro
Branch: `feature/lab10`
Date: 2026-07-08
Timezone: Europe/Moscow (UTC+03:00)

## Environment

- Locust scenario committed at repo root: `locustfile.py`.
- Locust ran as Kubernetes Jobs inside the `default` namespace against `http://gateway:8080`.
- Gateway was scaled to 5 replicas before testing so traffic went through kube-proxy across all gateway pods.
- Background `mixedload` from Lab 8 was scaled to 0 before final measurements.
- Redis was flushed before each load test with `redis-cli FLUSHDB`.
- Live images during the run: `quickticket-gateway:lab8`, `quickticket-events:lab8`, `quickticket-payments:lab8`.

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability | 99.5% of gateway requests avoid 5xx | 10u: 100%; 50u: about 99.17% excluding 409; 100u: about 49.87% | Pass at 10u, fail at 50u+ |
| Latency | 95% of gateway requests under 500ms | 10u p95 26ms; 50u p95 200ms; 100u p95 1100ms | Pass at 10u/50u, fail at 100u |
| Capacity ceiling | Stay below 0.5% 5xx and p99 under 500ms | First failed level: 50 users at about 36 RPS, 5xx about 0.83% in the clean run | Ceiling is below 50 users / 36 RPS |
| Product conflict handling | Do not count sold-out inventory as 5xx | 409s were separated from server failures | Pass |

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 inventory |
|------:|-----:|----:|----:|----:|----:|---------------:|--------------:|
| 10 | 2/s | 7.79 | 12ms | 26ms | 77ms | 0.00% (0/465) | 0 |
| 50 | 5/s | 36.31 | 7ms | 200ms | 390ms | 0.83% (18/2169) | 15 |
| 100 | 10/s | 53.25 | 470ms | 1100ms | 1500ms | 50.13% (1597/3186, no 409s reported) | 0 |

Breaking point: 50 users at about 36 RPS. That was the first measured level where true 5xx exceeded the 0.5% threshold. A repeat 50u run after the 100u overload still failed, with p99 about 910ms and 35 true 5xx plus 19 expected 409 conflicts.

Proof snippets:

```text
load-10 Aggregated: 465 reqs, 0 fails, 7.79 req/s, p50 12ms, p95 26ms, p99 77ms
load-50 Aggregated: 2169 reqs, 33 fails, 36.31 req/s, p50 7ms, p95 200ms, p99 390ms
load-50 error split: 18 true 5xx and 15 inventory 409s
load-100 Aggregated: 3186 reqs, 1597 fails, 53.25 req/s, p50 470ms, p95 1100ms, p99 1500ms
```

## 3. DORA Metrics

| Metric | Source data | Result | Notes |
|--------|-------------|--------|-------|
| Deployment Frequency | `git branch --list 'feature/lab*'` shows Lab 1-10 branches; `git log --oneline main \| wc -l` returned 42; live `kubectl get rs -l app=gateway` showed 3 gateway ReplicaSets | Course cadence is roughly one deployment-oriented lab per week; live gateway has 3 rollout generations retained | Current cluster no longer has ArgoCD Application CRD, so live GitOps sync state was unavailable |
| Lead Time for Changes | Lab 5 GitOps pattern: CI build plus ArgoCD poll interval | Approximately CI duration + 3 minutes | This is an approximation from the lab workflow, not a currently observable Application object |
| Change Failure Rate | Lab 7 submission recorded one successful AnalysisRun, one failed AnalysisRun, and bad canaries that were aborted; current cluster lacks `analysisrun` CRD | About 1 failed automated analysis out of 2 recorded AnalysisRuns; canary safety worked | Treat as lab-scale, not production DORA |
| Mean Time to Recover | Lab 7 manual abort removed bad canary pods in about 3 seconds; Lab 9 DB disaster RTO was 21s without PVC and 12-20s with PVC | Best app rollback recovery: seconds; DB recovery: 12-21s in the lab | Git revert + ArgoCD would be slower, roughly CI plus sync |

## 4. Top 3 Reliability Risks

1. Events service and database path saturate first. At 50u, events was a single pod at about 60m CPU and the gateway surfaced events failures as 500/502/503. Fix: scale events horizontally, increase and tune DB connection pooling, and load-test with per-service saturation dashboards.
2. Dependency-heavy readiness can cascade outages. Lab 8 showed Redis degradation could remove otherwise-running API pods from Service endpoints. Fix: keep Kubernetes readiness/liveness dependency-light and expose dependency state through `/health` plus alerts.
3. Capacity is fragile after overload. The 100u run caused gateway pod restarts and connection refusals. Fix: set realistic gateway/events resource limits, add HPA/PDB, and add overload protection such as request concurrency limits and graceful degradation.

## 5. Toil Identification

| Toil item | How often observed | Automation proposal | What it saves |
|-----------|-------------------|---------------------|---------------|
| Re-seeding or restoring Postgres after restarts before PVC | Repeated in Labs 4, 8, and 9 whenever state was reset | Keep PVC enabled, run migrations automatically, and schedule verified backups | Avoids manual `psql`/`pg_restore` recovery and preserves test data |
| Recreating port-forwards or local access paths | Used repeatedly in Labs 3-6 for gateway, Prometheus, and Grafana | Provide a `make dev-tunnels` script or use in-cluster jobs for tests | Reduces setup time and avoids testing through one sticky service endpoint |
| Manual rollout and chaos observation | Repeated in Labs 7-8 with `kubectl argo rollouts get`, pod watches, and ad hoc Prometheus queries | Keep AnalysisTemplates, alert rules, and runbooks as code; add scripted experiment runners | Turns manual watching into pass/fail evidence and shortens incident drills |

## 6. Monitoring Gaps

- During Lab 8 I wanted a latency burn-rate alert, not only a high 5xx alert. Slow payments below the gateway timeout created bad UX before hard failures appeared.
- I wanted dependency-specific alerts for Redis and Postgres. The Redis experiment showed readiness cascades, but an app-level Redis degradation alert would have made the root cause clearer.
- I wanted per-service saturation panels: CPU, memory, DB connection pool usage, and request queue/concurrency. Lab 10 showed events/gateway failures before raw CPU looked fully exhausted.
- The alert that would have caught the actual Lab 10 breakage early: gateway 5xx over 0.5% for 2 minutes plus events service 5xx/DB pool exhaustion, grouped with p99 latency over 500ms.

## 7. Capacity Plan

- Current ceiling: below 36 RPS for this mixed workload if the target is less than 0.5% 5xx and p99 under 500ms.
- Practical safe operating point: about 8 RPS from the 10u run, with large latency headroom and 0% failures.
- For 2x traffic from the measured breaking level, target about 72 RPS with headroom instead of merely doubling gateway pods.

CPU at breaking-point load (`load-50b`, sampled during the run):

```text
gateway-746b97bf4-hltfn   31m   45Mi
gateway-746b97bf4-lkf97   25m   40Mi
gateway-746b97bf4-pk6np   32m   40Mi
gateway-746b97bf4-qwhjx   29m   43Mi
gateway-746b97bf4-v4hgq   36m   38Mi
events-bbd9569dd-dcn22    60m   57Mi
payments-6bd5bf9df8-xl8k7 10m   36Mi
```

2x plan:

| Component | Current | 2x plan | Requests / limits | Reason |
|-----------|---------|---------|-------------------|--------|
| gateway | 5 replicas, 50m/200m CPU, 64Mi/256Mi memory | 5-6 replicas | 100m request, 300m limit, 128Mi request, 256Mi limit | Gateway is not the only bottleneck, but needs restart headroom |
| events | 1 replica, 50m/200m CPU, 64Mi/256Mi memory | 3 replicas | 100m request, 300m limit, 128Mi request, 256Mi limit | Events handles reads/reserves and showed the highest single-pod load |
| payments | 1 replica, 50m/200m CPU, 64Mi/256Mi memory | 2 replicas | 50m request, 200m limit, 64Mi request, 256Mi limit | Mostly idle in this mix, but payment path needs availability |
| Redis | 1 pod | Keep single pod for this lab; production would use managed/replicated Redis | 100m request, 300m limit, 128Mi request, 256Mi limit | Reservation state is critical; single pod is a lab SPOF |
| Postgres | 1 pod with PVC | Keep one primary for lab; add pgbouncer and monitor pool usage | 250m request, 500m limit, 512Mi request, 1Gi limit | DB/pool path is likely part of events failures |

Rough cost estimate at $5/pod/month: gateway 6 + events 3 + payments 2 + redis 1 + postgres 1 + pgbouncer 1 = 14 pods, about $70/month. Without pgbouncer and with 5 gateway pods, about 12 pods or $60/month.

## Bonus Task - SRE Handbook

Bonus Option B completed: `submissions/runbooks/quickticket-handbook.md`.

## PR Checklist

```text
- [x] Task 1 done - load tests, DORA, toil, reliability review (all 7 sections)
- [x] Task 2 done - detailed capacity plan with numbers
- [x] Bonus Task done - SRE handbook
```

## Acceptance Criteria Checklist

### Task 1

- [x] Load-test table covers 10u, 50u, 100u, and the breaking point.
- [x] Locust ran in-cluster as Kubernetes Jobs against `http://gateway:8080`, not through port-forward.
- [x] DORA metrics were calculated from project Git history, live ReplicaSets, and Lab 7 rollout history.
- [x] Three toil items were identified with concrete automation proposals.
- [x] All seven reliability-review sections are present and filled in.

### Task 2

- [x] `kubectl top pods` output was captured at the 50u breaking-point load.
- [x] Replica, resource, Redis, DB, and cost plan for 2x capacity is included.

### Bonus Task

- [x] Completed 2-page SRE handbook at `submissions/runbooks/quickticket-handbook.md`.
