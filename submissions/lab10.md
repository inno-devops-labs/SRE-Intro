# Lab 10

The provided Locust scenario was copied to the repo root and loaded into a ConfigMap so the load generator could run inside the cluster:

```bash
/home/andrey-debian/.local/bin/kubectl create configmap locustfile \
  --from-file=locustfile.py=locustfile.py \
  --dry-run=client -o yaml | /home/andrey-debian/.local/bin/kubectl apply -f -
```

```text
configmap/locustfile created
```

Before each run, Redis was flushed so stale reservation holds did not pollute the next test:

```bash
/home/andrey-debian/.local/bin/kubectl exec -i $(/home/andrey-debian/.local/bin/kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
```

```text
OK
```

## Task 1

### Load test results

Locust was run from Kubernetes Jobs, not through port-forward. The three required levels produced:

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 7.80  | 9ms    | 18ms    | 69ms     | 0.00%  | 0 |
| 50    | 5/s  | 34.23 | 12ms   | 600ms   | 980ms    | 5.08%  | 11 |
| 100   | 10/s | 20.82 | 1100ms | 20000ms | 36000ms  | 57.66% | 0 |

Source data for `10u`:

```text
Aggregated                                                                       465     0(0.00%) |     11       4     150      9 |    7.80        0.00
Aggregated                                                                              9     11     12     13     15     18     21     69    150    150    150    465
```

Source data for `50u`:

```text
Aggregated                                                                      2048   115(5.62%) |    109       4    1300     12 |   34.23        1.92
Aggregated                                                                             12     19    100    180    400    600    820    980   1300   1300   1300   2048
11                 POST /events/5/reserve: HTTPError('409 Client Error: Conflict for url: /events/5/reserve')
```

The SLO-relevant `5xx` error rate at `50u` is `104 / 2048 = 5.08%`, because `11` of the `115` Locust failures were expected `409 Conflict`.

Source data for `100u`:

```text
Aggregated                                                                      1247  719(57.66%) |   3063       1   39679   1100 |   20.82       12.00
Aggregated                                                                           1100   2000   3000   3500   6500  20000  22000  36000  39000  40000  40000   1247
```

Breaking point:

- The system already crossed both thresholds at `50u`.
- `5xx` exceeded `0.5%`.
- Aggregated `p99` exceeded `500ms`.
- So the observed capacity ceiling was about `34 RPS` at `50` users.

No run at `200u` was needed, because `100u` was already catastrophically unhealthy.

## QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Gateway 5xx rate | `< 0.5%` | `0.00%` at `10u`, `5.08%` at `50u`, `57.66%` at `100u` | Fails at `50u` |
| Gateway p99 latency | `< 500ms` | `69ms` at `10u`, `980ms` at `50u`, `36000ms` at `100u` | Fails at `50u` |
| Bad rollout recovery | `< 1 hour` | `< 10s` in Lab 7 abort run | Meets |

## 2. Load Test Results

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 7.80  | 9ms    | 18ms    | 69ms     | 0.00%  | 0 |
| 50    | 5/s  | 34.23 | 12ms   | 600ms   | 980ms    | 5.08%  | 11 |
| 100   | 10/s | 20.82 | 1100ms | 20000ms | 36000ms  | 57.66% | 0 |

Observed breaking point: `50u`, about `34 RPS`.

## 3. DORA Metrics

Source data:

```bash
git log --oneline origin/main | wc -l
git log --oneline --merges origin/main | wc -l
git log --oneline origin/main --grep='^ci: update image tags' | wc -l
```

```text
58
9
5
```

Recent deployment-like commits on `main`:

```text
db9fb2f ci: update image tags to a6067a821f94ea499503168631743723fea0ac69
83dc09f ci: update image tags to fdee075bdcef8aa2b6b4b9dd41ca72c47ff3e8ba
53b55eb ci: update image tags to 5ba5ce49f683457243ad48727afe800e81f538e2
f4034c3 ci: update image tags to 6e00f402a3d7fefd8dd296dcc76bb18348f3287a
e060554 ci: update image tags to dc2494986a08dc231c6ee868e7a0a8c450f79e86
```

Live rollout-state source:

```bash
/home/andrey-debian/.local/bin/kubectl get rs --show-labels
```

```text
gateway-8c56d5945     5         5         5       35m    app=gateway,rollouts-pod-template-hash=8c56d5945,version=v2
```

The current cluster was rebuilt during the course work, so it does not retain the old `AnalysisRun` objects. For failure and recovery evidence, the committed project-history record in `submissions/lab7.md` was used:

```text
The abort returned the rollout to stable-only traffic in less than `10s` in this run.
```

For the failed rollout evidence, the same recorded history in `submissions/lab7.md` contains:

```text
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 17
```

For lead-time estimation, actual commit timestamps from recent labs were combined with the ArgoCD poll note from Lab 5:

```text
aa5ca2b 2026-07-10T23:21:45+03:00 Lab 9 done
db9fb2f 2026-07-10T20:25:09Z ci: update image tags to a6067a821f94ea499503168631743723fea0ac69

c905ffc 2026-07-03T22:33:25+03:00 Lab 8 done
83dc09f 2026-07-03T19:36:21Z ci: update image tags to fdee075bdcef8aa2b6b4b9dd41ca72c47ff3e8ba
```

```text
ArgoCD polls Git every 3 minutes by default.
```

So the recent examples show about `3-4` minutes from lab commit to image-tag update on `main`, and then up to another `3` minutes for ArgoCD to pick it up.

For rollback-path comparison, the Lab 5 procedure was used:

```text
git revert HEAD --no-edit
git push origin main
```

DORA table:

| Metric | Value | Notes |
|--------|-------|-------|
| Deployment Frequency | about `1/week` | `5` deployment-like image-tag commits on `main` since Lab 5 |
| Lead Time for Changes | about `6-7 min` | recent examples show `3-4 min` to image-tag update, plus up to `3 min` ArgoCD poll |
| Change Failure Rate | about `20%` | `1` recorded failed rollout / `5` deployment-like image-tag updates |
| Recovery Time | `< 10s` for canary abort | the Git revert path would be slower because it needs revert, push, CI, and ArgoCD sync |

## 4. Top 3 Reliability Risks

1. The first real load jump from `10u` to `50u` already broke the service. This matters because the system has almost no safe headroom above the low-load baseline. Recommended fix: profile the gateway→events→Postgres path, then scale `events` horizontally and add a connection pooler in front of Postgres.
2. Postgres and Redis are still single-instance stateful dependencies. The PVC and scheduled dumps are an improvement, but a single pod restart or node loss can still cause service pain. Recommended fix: managed Postgres or replication + PITR, and a replicated Redis setup when the project grows past this lab scale.
3. Monitoring is still too error-centric. At `50u`, latency and readiness degradation were already severe before the system became completely unusable at `100u`. Recommended fix: add p95/p99 latency alerts on gateway and events, plus alerts on restart spikes and readiness flapping.

## 5. Toil Identification

| Toil | How often | How to automate | What it would save |
|------|-----------|-----------------|--------------------|
| Re-creating port-forwards to Postgres / Prometheus | `8+` times across Labs 7-9 | `make pf-postgres` / `make pf-prometheus` wrappers or a small `tmux` script | 2-3 minutes and fewer “why did the command hang?” detours |
| Manual load/chaos Job creation + wait + log collection | `10+` runs across Labs 7-10 | `make load USERS=50` and `make chaos POD=gateway` helpers | removes repetitive kubectl boilerplate and gives repeatable experiment runs |
| Manual Redis resets and state cleanup between experiments | `10+` resets across Labs 8-10 | a `make reset-lab-state` target that runs `FLUSHDB`, clears old jobs, and verifies health | avoids false failures from stale holds and speeds up reruns |

## 6. Monitoring Gaps

- A direct alert on gateway aggregated `p99` latency was missing. In these tests, latency became ugly before the final collapse, but the existing error-oriented view would page only after the system was already failing.
- A dashboard that split `5xx` by `path` and by upstream dependency was missing. At `50u`, `/events`, `/health`, and `/reserve` all degraded differently; a single aggregate error ratio hides that.
- Alerting on pod restarts and rollout availability was missing. During the heavy runs, gateway pods restarted and the rollout health flipped, but this had to be noticed through `kubectl get pods` instead of proactive signals.
- DB-side visibility was missing: connection count, lock waits, slow queries. CPU was low at breaking point, which strongly suggests the real bottleneck was elsewhere in the request path.

## 7. Capacity Plan

- Current ceiling: about `34 RPS` at `50u`, but this is already an unhealthy edge, not a safe operating point.
- For `2x` the current breaking-point traffic, scaling only gateway CPU would not help much, because the observed bottleneck was not raw CPU saturation.
- Next-step scale plan:
  - `gateway`: from `5` to `8` replicas
  - `events`: from `1` to `3` replicas
  - `payments`: from `1` to `2` replicas
  - add `1` PgBouncer pod between `events` and Postgres
  - increase Postgres resources and keep the PVC-backed storage
- Rough cost estimate at `$5/pod/month`:
  - current steady-state app + platform set is about `11` pods, so about `$55/month`
  - proposed `2x` plan is about `18` pods, so about `$90/month`
  - incremental cost is about `$35/month`

## Task 2

The breaking-point level at `50u` was re-run and pod CPU was sampled during the run:

```bash
/home/andrey-debian/.local/bin/kubectl top pods -l app=gateway
/home/andrey-debian/.local/bin/kubectl top pods -l app=events
/home/andrey-debian/.local/bin/kubectl top pods -l app=payments
```

```text
NAME                      CPU(cores)   MEMORY(bytes)
gateway-8c56d5945-6mnm9   13m          42Mi
gateway-8c56d5945-76kz4   13m          41Mi
gateway-8c56d5945-7tpt2   11m          40Mi
gateway-8c56d5945-pggql   11m          41Mi
gateway-8c56d5945-sdr6p   20m          39Mi
```

```text
NAME                      CPU(cores)   MEMORY(bytes)
events-55cfb97ffc-x48vd   12m          50Mi
```

```text
NAME                        CPU(cores)   MEMORY(bytes)
payments-7cbcc896fc-rbkjr   11m          40Mi
```

The important observation is that CPU stayed low even while the service was failing. So the immediate bottleneck is not “add more CPU” but “reduce waiting and single-path pressure”.

Detailed 2× capacity plan:

| Service | Current replicas | Proposed replicas | Current requests/limits | Proposed requests/limits | Why |
|---------|-----------------:|------------------:|-------------------------|--------------------------|-----|
| gateway | 5 | 8 | `50m/64Mi` req, `200m/256Mi` lim | keep same per pod | spread concurrent client connections wider |
| events | 1 | 3 | `50m/64Mi` req, `200m/256Mi` lim | `100m/128Mi` req, `300m/512Mi` lim | single `events` pod is the clearest serial choke point |
| payments | 1 | 2 | `50m/64Mi` req, `200m/256Mi` lim | keep same per pod | write-path headroom and less blast radius from one pod |
| postgres | 1 | 1 | implicit current pod sizing | `250m/256Mi` req, `500m/512Mi` lim | more DB headroom until a real HA migration |
| pgbouncer | 0 | 1 | none | `50m/64Mi` req, `100m/128Mi` lim | smooth connection spikes from multiple events pods |

Redis judgment:

- For just `2x` this traffic, single-pod Redis is still acceptable.
- Beyond that, a move to replicated Redis would be justified because reservation holds are on the hot path.

DB connection judgment:

- Yes, the current `events -> single Postgres` path is a bottleneck candidate.
- The low CPU plus high latency/failures strongly suggests queueing or waiting, not CPU exhaustion.
- So the first DB improvement should be PgBouncer, not more gateway replicas.

## Bonus Task

Bonus Option B was completed in the handbook here:

- [submissions/runbooks/quickticket-handbook.md](/home/andrey-debian/Projects/IU/SRE/SRE-Intro/submissions/runbooks/quickticket-handbook.md)
