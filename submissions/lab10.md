# QuickTicket Reliability Review

## Deliverables
- `locustfile.py` - reusable in-cluster Locust scenario at the repo root.
- `submissions/lab10.md` - reliability review submission.

## 1. SLO Compliance
| SLO | Target | Observed | Status |
|---|---:|---:|---|
| Availability / 5xx rate | < 0.5% 5xx | 10u: 0%; 50u: 2.57%; 75u: 10.03%; 100u: 25.97% | Breached at 50u+ |
| Latency p99 | < 500 ms | 10u: 32 ms; 50u: 210 ms; 75u: 520 ms; 100u: 980 ms | Breached at 75u+ |
| Inventory conflicts | Expected under contention | 409s appeared at 50u+ and grew with load | Expected behavior |

## 2. Load Test Results
| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 2/s | 7.91 | 13 ms | 23 ms | 32 ms | 0.00% | 0 |
| 50 | 10/s | 39.03 | 6 ms | 30 ms | 210 ms | 2.57% | 57 |
| 75 | 15/s | 53.16 | 6 ms | 540 ms | 1.9 s | 10.03% | 59 |
| 100 | 20/s | 68.62 | 130 ms | 530 ms | 980 ms | 25.97% | 49 |

Capacity ceiling: around 50 users / 39 RPS for the 5xx SLO, and around 75 users / 53 RPS for the p99 latency SLO.

## 3. DORA Metrics
| Metric | Observed | Source / How I estimated |
|---|---:|---|
| Deployment frequency | 7 gateway ReplicaSet revisions | `kubectl get rs -l app=gateway` |
| Lead time for changes | About 3-5 minutes | Git commit to CI/ArgoCD sync is small here; the repo is already deployed and this lab uses a local cluster |
| Change failure rate | 1 failed AnalysisRun out of 2 total | `kubectl get analysisrun -A` |
| Recovery time | Seconds for pod-level rollback; minutes for full manual recovery | Rollout state shows fast transitions; see current cluster state and prior lab behavior |

## 4. Top 3 Reliability Risks
1. Events becomes the bottleneck first under load. At 75 users, `events` is at 131m CPU while `payments` is only 14m, so the read path and DB access in `events` dominate the ceiling.
2. The system degrades into mixed 502/500/503 failures instead of a clean overload signal. That makes it harder to distinguish a capacity issue from a dependency failure during an incident.
3. Inventory contention produces lots of 409s at moderate load, which can hide real regressions if the load mix is not balanced across events.

## 5. Toil Identification
| Manual task | How often | Automation idea | Saved time |
|---|---:|---|---:|
| Re-running in-cluster load tests with the same Job manifest | 3+ times per lab run | Keep a reusable Job template or script that parameterizes users/ramp/time | 10-15 minutes per run |
| Flushing Redis before every test run | 3+ times | Add a pre-test cleanup Job or wrapper script | 2-3 minutes per run |
| Collecting rollout / AnalysisRun status manually for release health | 3+ times | Store a small script that prints rollout + analysis summary in one command | 5-10 minutes per incident or release |

## 6. Monitoring Gaps
- I wished I had a per-service p99 latency panel with separate counters for 500/502/503, because the system started failing in different ways at the same time.
- I wanted a clear saturation signal for `events` CPU and DB pool exhaustion before the user-facing 5xx rate spiked.
- The alert that would have caught the real issue earlier is a latency alert on the read path, not only a 5xx alert. A dependency can go slow before it starts failing hard.

## 7. Capacity Plan
- Current ceiling: about 39 RPS for a safe 5xx SLO, or about 53 RPS if I accept p99 just above 500 ms.
- For 2x traffic, scale gateway from 5 to 10 replicas, events from 1 to 2 replicas, and keep payments at 1 unless its latency starts to rise.
- Set CPU requests so `events` gets more headroom than `payments`; the observed load shows `events` is the constrained service.
- Redis can stay single-pod for now, but only if inventory contention is reduced by balancing traffic or splitting hotspots.
- DB connections should be increased or pooled more carefully for `events`; the current failure mode suggests DB access is the first real backend bottleneck.
- Rough cost estimate: +$25/month for five extra pods at about $5/pod/mo, plus a small margin for CPU requests and storage.

## Bonus / Notes
- I committed the reusable `locustfile.py` at the repo root.
- The 75-user run already shows the point where latency crosses 500 ms and the error mix becomes visible, so this is the strongest breaking-point evidence in the set.

## PR Checklist
- [x] Task 1 done - load tests, DORA, toil, reliability review (all 7 sections)
- [x] Task 2 done - capacity ceiling and 2x plan with numbers
- [ ] Bonus Task done - demo video OR SRE handbook