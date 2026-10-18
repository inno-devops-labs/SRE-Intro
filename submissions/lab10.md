# Lab 10 — SRE Portfolio & Reliability Review

## Task 1 — Load Testing & Reliability Review (6 pts)

### Test Setup and Execution

The Locust runner used image `locustio/locust:2.43.4`, ran for 60 seconds, and targeted the Gateway Service from inside the cluster. The Locust scenario exercised event listing, ticket reservations for events 3 and 5, and the health endpoint. Redis was flushed between runs to avoid stale reservation holds affecting inventory results.

The runner ConfigMap was already present and was checked before testing:

```bash
kubectl get configmap locustfile
kubectl create configmap locustfile \
  --from-file=locustfile.py=locustfile.py \
  --dry-run=client -o yaml | kubectl apply -f -
```

The capacity run used the same runner template with 100 users, ramp-up 10 users/second, and a 60-second duration:

```bash
kubectl exec -i "$(kubectl get pod -l app=redis -o name | head -n 1)" -- redis-cli FLUSHDB
kubectl apply -f /tmp/lab10-load-capacity.yaml
kubectl logs job/load-capacity --tail=80
```

The job completed its 60-second Locust run but exited with code 1 because Locust recorded request failures. This is a test result, not a failure to start the load generator.

### 1. SLO Compliance

Lab 3 defined an availability target of 99.5% over a seven-day window and a latency target that at least 95% of gateway requests complete in less than 500 ms. The 60-second load tests are stress samples and cannot by themselves prove compliance over the complete seven-day SLO window.

| SLI / SLO | Target | Observed under load | Assessment |
|---|---|---|---|
| Availability | HTTP 5xx rate no greater than 0.5% | 10 users: 0/455 5xx. 50 users: 1/2,169 5xx (0.046%). 100 users: 777/2,400 5xx (32.38%). Capacity re-run: 661/2,429 5xx (27.21%). | The 10- and 50-user samples met the 5xx threshold. Both 100-user runs exceeded it substantially. |
| Latency | At least 95% of requests complete in less than 500 ms | 10 users: maximum 207 ms. 50 users: aggregate p99 380 ms, but `/events/3/reserve` p99 was 510 ms. First 100-user run: aggregate p99 3,400 ms. Capacity re-run: p95 2,200 ms and p99 9,200 ms. | Light load met the target. Endpoint tail latency was already above threshold at 50 users; both 100-user runs clearly failed the latency objective. |

HTTP `409 Conflict` on a reservation request indicates exhausted ticket inventory and is expected product behavior. These responses are not counted as system 5xx errors. Connection-refused errors did not return an HTTP status, so they are reported separately, but still count as failed client requests operationally.

### 2. Load Test Results

The aggregate results recorded by Locust are summarized below. The reported request rate is the rate printed by Locust; percentile values are copied from the captured summaries. Percentiles not retained for the earlier runs are marked unavailable rather than estimated.

| Users | Ramp-up | RPS | p50 | p95 | p99 | HTTP 5xx rate | HTTP 409 count | Notes |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 2/s | ~7.58 | 17 ms | Not retained | 120 ms | 0/455 (0%) | 0 | Aggregate maximum latency was 207 ms; no failures. |
| 50 | 5/s | ~36.15 | 10 ms | Not retained | 380 ms | 1/2,169 (0.046%) | 34 | The single 5xx was `502` on `GET /events`. `POST /events/3/reserve` p99 reached 510 ms. |
| 100 | 10/s | ~40.00 | 950 ms | Not retained | 3,400 ms | 777/2,400 (32.38%) | 0 | 802 total Locust failures included 25 connection-refused errors as well as the 777 HTTP 5xx responses. |
| 100 (capacity re-run) | 10/s | 40.56 | 820 ms | 2,200 ms | 9,200 ms | 661/2,429 (27.21%) | 4 | 700 total failures: 661 HTTP 5xx, 35 connection-refused errors and 4 expected `409` responses. Maximum response time was 20,674 ms. |

The capacity re-run produced the following endpoint-level results:

| Endpoint | Requests | Failures | Average latency | p95 | p99 | Maximum |
|---|---:|---:|---:|---:|---:|---:|
| `GET /events` | 1,691 | 440 (26.02%) | 1,022 ms | 2,200 ms | 12,000 ms | 20,674 ms |
| `POST /events/3/reserve` | 369 | 142 (38.48%) | 978 ms | 2,000 ms | 4,200 ms | 9,209 ms |
| `POST /events/5/reserve` | 141 | 61 (43.26%) | 1,067 ms | 2,000 ms | 7,200 ms | 11,845 ms |
| `GET /health` | 228 | 57 (25.00%) | 1,182 ms | 2,400 ms | 6,000 ms | 20,551 ms |

The 700 failures in the capacity re-run were reported as 661 HTTP 5xx responses, 35 connection-refused errors and four `409` responses. The HTTP 5xx rate was therefore 661 / 2,429 = 27.21%; the connection-refused errors are not included in that HTTP rate.

The error report showed 414 `502` responses on `GET /events`, 136 `500` responses on `POST /events/3/reserve`, 54 `500` responses on `POST /events/5/reserve`, and 55 `503` responses on `GET /health`. A further small number of `502` errors occurred on reservation requests. The Events service also logged downstream disconnects during the earlier high-load run.

#### Breaking Point Analysis

At 50 users, aggregate p99 was 380 ms and the HTTP 5xx rate was 0.046%, but the p99 for `POST /events/3/reserve` was 510 ms. Therefore, the first endpoint-level latency threshold breach was observed at 50 users.

At 100 users, the system-wide failure was unambiguous in both runs. The load generator produced approximately 40–41 requests per second, HTTP 5xx rates reached 27.21–32.38%, p99 reached 3.4–9.2 seconds, and connection-refused errors appeared. For the remainder of this report, approximately 40 requests/second is treated as the demonstrated failure ceiling. The target for a 2× capacity plan is approximately 80–82 requests/second, subject to validation after changes.

### 3. DORA Metrics

DORA metrics were reviewed using Git history and the available Kubernetes rollout state. The history was incomplete, so metrics requiring a complete set of deployment or incident events are marked unavailable rather than inferred from commit counts.

| DORA metric | Observed result | Source and limitation |
|---|---|---|
| Deployment Frequency | One Gateway ReplicaSet was visible in the current cluster; `kubectl rollout history deployment/gateway` reported revision 1. A reliable deployments-per-day or deployments-per-week value could not be calculated. | `kubectl get rs -l app=gateway` and `kubectl rollout history deployment/gateway`. Git commits and merges are not treated as deployments. |
| Lead Time for Changes | Approximately **14 days, 18 hours, 35 minutes, 57 seconds** from commit `1aaff1a` to the observed Gateway Pod becoming Ready. | The deployed image referenced commit `1aaff1a`. The observed ReplicaSet creation and Pod readiness timestamps were recorded on 12 October 2026. This is one commit-to-ready interval, not a team-wide average. |
| Change Failure Rate | Not reliably calculable from the available records. | Git history includes a deliberate broken-image test (`a2dad9d`) followed by a revert (`66cc6f5`), but the current cluster snapshot does not contain a complete denominator of successful and failed deployments or all Argo Rollouts AnalysisRun outcomes. The intentional test is not counted as a production change failure without evidence of its rollout outcome. |
| Time to Restore | Not reliably calculable from the available records. | Load testing revealed health-check failures and downstream errors, but a complete incident-start-to-recovery timeline was not recorded. Pod events alone do not establish a reliable restoration duration. |

The principal DORA data gap is the lack of a retained deployment and incident timeline. Retaining CI deployment timestamps, Argo Rollouts AnalysisRun outcomes, abort/rollback events, and recovery timestamps would make all four metrics measurable.

Commands used to inspect the available history included:

```bash
git log --all --since="2026-09-12" --date=iso-strict --format='%h | %cI | %s'
kubectl rollout history deployment/gateway
kubectl get rs -l app=gateway --sort-by=.metadata.creationTimestamp \
  -o custom-columns='CREATED:.metadata.creationTimestamp,NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image'
kubectl get events -n default --sort-by=.metadata.creationTimestamp | \
  grep -Ei 'gateway|deployment|rollout|rollback|unhealthy'
```

### 4. Top 3 Reliability Risks

1. **Gateway CPU saturation and limited redundancy.** During the capacity re-run, the highest sampled Gateway CPU was 192m against a 200m limit (96%). The live cluster had one Gateway replica, so it had little CPU headroom and no replica-level redundancy. Reconcile the live configuration with the intended Lab 7 Rollout topology, add Gateway replicas as supported by the cluster, and verify that traffic is distributed across them.

2. **Fragile Gateway-to-Events error handling.** High load produced many `502` responses from `GET /events`, and the Events service logged downstream disconnects. Gateway error handling also attempted to parse an upstream error body with `e.response.json()`; when the body was empty or non-JSON, this raised `JSONDecodeError` and obscured the original failure. Handle non-JSON upstream responses safely, preserve the upstream status and context in logs, and investigate Events, database, Redis and connection-pool behavior.

3. **Health-check and monitoring blind spots under saturation.** During the capacity re-run, `/health` returned 57 failures out of 228 requests, including `503` responses and connection failures. Kubernetes readiness and liveness probe timeouts were also observed. Add per-endpoint latency and error dashboards, track saturation per pod, and review health-check behavior under load so health probes do not become another source of failure.

### 5. Toil Identification

The following manual activities were repeated during Labs 1–9. Exact command-by-command counts were not centrally logged, so time savings are estimates based on the repeated workflows rather than stopwatch measurements.

| Manual toil | Frequency / impact | Automation proposal | Estimated time saved |
|---|---|---|---|
| Re-seeding PostgreSQL after restarts before persistent storage was configured | Repeated during database setup and recovery work. Exact number of repetitions was not logged. | Preserve database files on a PVC and use an idempotent initialization or migration job instead of replaying `seed.sql` manually after each restart. | Approximately 5–10 minutes per avoided manual recovery, depending on troubleshooting. |
| Re-creating port-forwards after Pods or Services changed | Repeated during local service verification and monitoring work. Exact number was not logged. | Use in-cluster DNS for service-to-service checks and a helper script for the local UI/endpoint port-forwards that are still needed. | Approximately 2–5 minutes per repetition, plus less context switching. |
| Manually watching canary rollouts and deciding whether to abort | Repeated while testing progressive delivery in Lab 7. Exact number was not logged. | Use Argo Rollouts `AnalysisTemplate` thresholds for automatic analysis and abort/rollback, then use a short status command or script for verification. | Approximately 5–10 minutes per rollout observation and fewer missed or delayed abort decisions. |

The largest benefit comes from automating recovery and rollout decisions, rather than only shortening individual terminal commands. The task-frequency figures should be treated as estimates because a central repetition counter was not maintained during Labs 1–9.

### 6. Monitoring Gaps

The experiments exposed several gaps in the existing monitoring approach:

- **Endpoint latency needs to complement aggregate latency.** At 50 users, the aggregate p99 was below 500 ms, but `POST /events/3/reserve` had a p99 of 510 ms. Record and alert on p95/p99 by endpoint as well as aggregate gateway latency.
- **The alert threshold should reflect the 0.5% SLO.** A severe-failure alert set at 5% would catch the 100-user failure, but could miss SLO violations between 0.5% and 5%. Add an SLO burn-rate alert and retain a separate critical alert for severe degradation.
- **CPU saturation must be captured over time and per pod.** Periodic `kubectl top` samples showed Gateway at 192m and Events at 154m, but do not show precise sustained peaks or CPU throttling. Record CPU usage/throttling, memory, restarts, readiness transitions and request rates for every pod throughout each load test.
- **Downstream failures need request-level correlation.** Gateway logs should record the upstream service, upstream status, timeout/connection error and request ID. The non-JSON error-body parsing failure made the original downstream failure harder to diagnose.
- **Health checks need validation under load.** `/health` had 57 failures in 228 requests in the capacity re-run, and Kubernetes probe timeouts were observed. Keep liveness checks cheap and predictable, and separate process liveness from dependency readiness where appropriate.
- **The data path needs direct visibility.** Collect Redis latency/memory and PostgreSQL pool utilization, query latency and connection counts. A post-test PostgreSQL snapshot showed one active session, two idle sessions and five sessions with a null state; this snapshot does not prove that the database path was healthy at peak load.

### 7. Capacity Plan

The live service configuration contained one replica each of Gateway, Events and Payments. The measured 100-user failure ceiling was approximately 40–41 requests/second, so the planning target for 2× traffic is approximately 80–82 requests/second.

The preliminary plan is to use five Gateway replicas (restoring the topology intended by Lab 7), two Events replicas, and one Payments replica. Redis and PostgreSQL remain single-instance initially while their behavior under load is measured. The detailed resource requests/limits and the estimated cost increase of $25/month are documented in Task 2 below.

---

## Task 2 — Capacity Plan with Numbers (4 pts, optional)

### 1. Baseline Cluster State

The live cluster was checked before and during the capacity run:

```bash
kubectl get deploy gateway events payments postgres redis
```

```text
NAME       READY   UP-TO-DATE   AVAILABLE
 gateway   1/1     1            1
 events    1/1     1            1
 payments  1/1     1            1
 postgres  1/1     1            1
 redis     1/1     1            1
```

The live Gateway had one replica at measurement time. This differs from the intended Lab 7 topology of five Gateway replicas, so the current measurement is a one-replica baseline and must not be presented as a benchmark of the five-replica configuration.

### 2. Per-Pod CPU and Memory at the Breaking Point

Resource consumption was sampled periodically during the 100-user capacity re-run. The values below are the highest samples in the captured output, not guaranteed absolute peaks.

| Service | Current replicas | CPU request / limit | Highest sampled CPU | CPU limit used | Highest sampled memory | Memory limit used |
|---|---:|---|---:|---:|---:|---:|
| Gateway | 1 | 50m / 200m | 192m | 96% | 66Mi | 25.8% of 256Mi |
| Events | 1 | 50m / 200m | 154m | 77% | 76Mi | 29.7% of 256Mi |
| Payments | 1 | 50m / 200m | 13m | 6.5% | 49Mi | 19.1% of 256Mi |

The resource requests and limits were confirmed with:

```bash
kubectl get deploy gateway events payments \
  -o custom-columns='NAME:.metadata.name,REPLICAS:.spec.replicas,CPU_REQUEST:.spec.template.spec.containers[0].resources.requests.cpu,CPU_LIMIT:.spec.template.spec.containers[0].resources.limits.cpu,MEM_REQUEST:.spec.template.spec.containers[0].resources.requests.memory,MEM_LIMIT:.spec.template.spec.containers[0].resources.limits.memory'
```

The measurements identify Gateway as the most CPU-constrained service. It reached 96% of its CPU limit. Events reached 77% and is the next likely constraint. Payments remained lightly loaded in the captured samples. None of these services approached its 256Mi memory limit, so the first capacity changes should focus on CPU headroom and replica count rather than a memory increase.

Post-test PostgreSQL connections were inspected with:

```bash
kubectl exec deployment/postgres -- \
  psql -U quickticket -d quickticket \
  -c "SELECT state, count(*) FROM pg_stat_activity GROUP BY state;"
```

```text
 state  | count
--------+------
        | 5
 active | 1
 idle   | 2
(3 rows)
```

This was a snapshot after the test, not a time series. It does not establish the peak number of connections or rule out transient query latency while the load was active. PostgreSQL and Redis should therefore continue to be measured during the next run.

### 3. Capacity Target and Replica Plan

The observed failure ceiling was approximately 40–41 requests/second. Doubling that rate gives a planning target of approximately **80–82 requests/second**. The proposed configuration below is an initial sizing hypothesis based on the captured CPU use and an approximate linear-scaling assumption; it is not a guarantee of performance.

| Component | Current live replicas | Proposed replicas | Proposed CPU request / limit | Proposed memory request / limit | Rationale |
|---|---:|---:|---|---|---|
| Gateway | 1 | 5 | 100m / 500m | 96Mi / 256Mi | Restores the five-replica topology expected from Lab 7 and adds CPU headroom and redundancy. |
| Events | 1 | 2 | 200m / 500m | 96Mi / 256Mi | Events reached 77% of its existing CPU limit and serves listing/reservation requests. Two replicas are a starting hypothesis that must be checked against database and Redis behavior. |
| Payments | 1 | 1 | 50m / 200m | 64Mi / 256Mi | Payments used little CPU during the observed test; keep one replica until measurements show it is a bottleneck. |
| Redis | 1 | 1 initially | Retain current settings until measured | Retain current settings until measured | Do not add independent Redis pods without a deliberate shared-state and replication design. Measure latency, memory and connections first. |
| PostgreSQL | 1 | 1 initially | Retain current settings until measured | Retain current settings until measured | Preserve its PVC. Track connection-pool usage and query latency before considering database scaling or pool changes. |

A simple linear estimate gives a reason for this starting point. Gateway used 192m at about 40 requests/second with one replica; at twice the load distributed evenly across five replicas, that would be about 77m per pod. Events used 154m at the observed load; at twice the load spread evenly across two replicas, that would be about 154m per pod. These estimates assume even request distribution and that downstream dependencies can support the load. They must be verified in a follow-up test.

Before applying the change, the live Gateway resource must be reconciled with the version-controlled Lab 7 manifest. The repository configuration and current live Deployment have differed; confirm whether the intended controller is an Argo Rollout or a Deployment, and apply changes through the version-controlled manifest rather than leaving a live-only edit.

### 4. Cost Estimate

Using the lab's approximate assumption of `$5` per pod per month:

| Configuration | Pod count | Estimated monthly cost |
|---|---:|---:|
| Current live configuration: one each of Gateway, Events, Payments, Redis and PostgreSQL | 5 | $25/month |
| Proposed configuration: 5 Gateway, 2 Events, 1 Payments, 1 Redis and 1 PostgreSQL | 10 | $50/month |
| **Estimated increase** | **+5** | **+$25/month** |

This is a rough lab estimate, not a cloud-provider quote. It excludes persistent storage, monitoring and other infrastructure costs, and assumes the cluster can schedule the proposed CPU requests.

### 5. Validation Plan

The capacity plan should be validated in the following order:

1. Reconcile the live Gateway configuration with the Lab 7 manifest and verify that the intended Deployment or Argo Rollout controller owns the workload.
2. Apply the replica and resource changes through the version-controlled manifest, then wait for all desired pods to become Ready.
3. Flush Redis before each run. Repeat the in-cluster tests at 10, 50 and 100 users, then increase the load in stages toward 80–82 requests/second while collecting CPU and memory samples for Gateway, Events and Payments.
4. Accept the plan only if the HTTP 5xx rate is no greater than 0.5%, aggregate p99 latency is no greater than 500 ms, and no material connection-refused errors occur. Continue to track endpoint-level p99; exclude expected inventory `409` responses from the HTTP 5xx rate.
5. If the targets are still missed, inspect Events logs, Gateway upstream error handling, PostgreSQL query/connection-pool latency and Redis metrics before adding further replicas.

---

## Conclusion

The load tests established that QuickTicket was stable in the 10-user sample and remained below the aggregate HTTP 5xx threshold at 50 users, although reservation tail latency crossed 500 ms. Both 100-user runs exposed a clear capacity failure at approximately 40–41 requests/second, with HTTP 5xx rates of 27.21–32.38% and p99 latency in the multi-second range.

The resource samples show that Gateway CPU was near its configured limit and Events also had limited CPU headroom, while Payments and memory use were comparatively low. The DORA assessment also exposed an observability gap: the current cluster history was not sufficient to calculate deployment frequency, change failure rate and time to restore reliably. Retaining rollout outcomes and incident timestamps, improving endpoint-level latency/error monitoring, and validating the proposed replica plan are the main follow-up actions.
