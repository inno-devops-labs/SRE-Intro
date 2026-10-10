# QuickTicket Reliability Review

Local experiments ran on `k3d-quickticket` on 2026-10-10; timestamps in evidence are UTC. The application had five gateway replicas, one events instance with a ten-connection pool, one payments instance, Redis and PostgreSQL on `postgres-data`. The database contained six events and 4,479 confirmed orders. Events 3/5 still had their original 500/80 tickets; test reservations did not create orders. Payments fault settings were zero.

## 1. SLO Compliance

Targets come from [Lab 3](lab3.md#task-2--slis-slos-and-recording-rules). Short experiments are not a seven-day compliance report.

| SLO/check | Target | Observed | Status |
|---|---|---|---|
| Availability | 99.5% non-5xx over seven days | 2u: 97/97 non-5xx; 10u: 246/250 = 98.4%; 50u: 5.13%; 100u: 0.67% | Short 2u window passes; higher levels fail. Weekly compliance unverified |
| Latency SLO | At least 95% within 500ms | 2u: 95/97 = 97.94%; 10u: 158/250 = 63.2%; 50u: 25.99%; 100u: 12% | Short 2u window passes; higher levels fail |
| Lab 10 capacity gate | 5xx <=0.5% AND p99 <=500ms | 2u p99=600ms; 10u 5xx=1.6%, p99=4,500ms | Failed; p99 gate is stricter than the latency SLO |

Neither expected `409` inventory rejection nor a missing metric is counted as a system success/failure substitute. Client-side latency counts include all responses, including errors. Transport errors would be reported separately; none occurred in these isolated runs.

## 2. Load Test Results

`locustfile.py` adapts the supplied scenario: 70% reads, 20% reservations, 10% health; random waits 0.5–2s; reservations across events 3 and 5 with a 3:1 selection weight. It adds a status-code counter, exact 500ms latency counts and a final machine-readable summary. A `409` is marked as expected in Locust, but still counted separately. The scenario **does not call payment or confirmation**, so these are not full-checkout capacity measurements.

All Jobs used `locustio/locust:2.43.4`, ran for 60s including ramp-up, and targeted `http://gateway:8080` inside the cluster. Redis was flushed before each run. The authoritative levels ran sequentially, with events restarted after overload to reset the exhausted pool; no application code, replica counts or resource limits were changed. An initial overlapping 10u/50u exploration was discarded; its 50u evidence is retained in `lab10-excluded-overlap.json` and is not used below.

| Users | Ramp | Requests | RPS | p50 ms | p95 ms | p99 ms | 5xx error rate | 409 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1/s | 47 | 0.81 | 31 | 120 | 170 | 0/47 = 0% | 0 |
| 2 | 1/s | 97 | 1.67 | 34 | 140 | 600 | 0/97 = 0% | 0 |
| 10 | 2/s | 250 | 4.28 | 100 | 3,900 | 4,500 | 4/250 = 1.60% | 0 |
| 50 | 5/s | 604 | 10.32 | 5,000 | 5,300 | 8,100 | 573/604 = 94.87% | 0 |
| 100 | 10/s | 1,050 | 17.95 | 5,000 | 5,300 | 5,400 | 1,043/1,050 = 99.33% | 0 |

**Observed bracket: 1u / 0.81 RPS passed; 2u / 1.67 RPS failed**, because p99 exceeded 500ms, despite passing the 95%-under-500ms SLO. The first mandatory level, 10u / 4.28 RPS, failed both gates. Higher throughput at 50/100u is predominantly failed responses, not usable capacity. There was no reason to expose the cluster to 200u after 100u failed this badly. Quantiles are Locust's rounded/approximate distributions; 47/97 requests make the low-load p99 especially sensitive to individual samples. This bounds the sampled transition, not a statistically established maximum. A longer repeat is required before setting a production ceiling.

The 50u logs contain `psycopg2.pool.PoolError: connection pool exhausted`; events CPU was 205m against a 200m limit. At 100u it was 206m while gateway pods were 63–130m against 200m limits. Small apparent overshoots in `kubectl top` are sampled CPU measurements, not proof the limit was absent. The generator was only 38m in the 50u diagnostic snapshot, and node CPU was well below total capacity.

Source inspection explains a concurrency risk: `reserve_tickets()` holds a connection while `_get_available()` acquires a second from the same ten-slot pool. `ThreadedConnectionPool` raises rather than queues when exhausted. Read-only transactions remain open until connection reuse/close; a live snapshot showed seven `idle in transaction` connections, not a PostgreSQL-wide max-connection shortage (`max_connections=100`). This is a bottleneck hypothesis supported by code and observed pool errors, not proof that CPU alone caused every timeout.

Gateway access logs showed requests on all five pods: 228/227/157/208/169 reads in the diagnostic ten-minute window. This is a distribution check across exploratory and isolated traffic, not a per-run throughput sum. No gateway port-forward was used for load.

Evidence: `lab10-load-{2,10,50,100}.json` contain complete Locust logs, raw status counts and timestamped `kubectl top pods/nodes` samples; `lab10-load-1.json` contains the lower-level passing log and summary. `lab10-diagnostics.txt` and `lab10-diagnostics-100.txt` retain CPU, pool errors and database observations. Reproduce one level after prior Jobs stop:

```bash
kubectl create configmap locustfile --from-file=locustfile.py=locustfile.py --dry-run=client -o yaml | kubectl apply -f -
python3 loadgen/lab10-run.py 10 2 --suffix=-repeat
```

Use a new suffix for each Job. Locust exits 1 on actual HTTP failures, so a Kubernetes `Failed` Job is expected for failing levels; its complete summary is still the evidence. Do not confuse this with an image/startup failure.

## 3. DORA Metrics

These are a **local training-system sample**, with deliberately bad changes, not production performance rankings. Source snapshots are in `lab10-history.txt`; Git history, GitHub Actions and ArgoCD history were inspected directly.

| Metric | Calculation and observed value | Interpretation |
|---|---|---|
| Deployment frequency | Four ArgoCD sync records on Sep 26 plus six gateway rollout revisions on Oct 3, including recovery reusing a prior template: 10 local deployment events / 15 calendar days (Sep 26–Oct 10 inclusive) = 0.67/day | Five retained gateway ReplicaSets are five distinct templates, not six new images. Git commits and sync events are not all production deployments |
| Lead time | Source commit `71bc18c` at 13:08:45Z Sep 26 → ArgoCD deployment of its `78fbd40` image-tag manifest at 13:24:45Z = 16m. Revert `a8020f8` at 13:27:21Z → first sync at 13:27:38Z = 17s | The first measurement includes initial setup/manual waiting; revert reused existing images |
| Pipeline/poll estimate | CI run 36244249730: 13:10:01–13:10:44Z = 43s; run 36245255630: 13:28:00–13:28:41Z = 41s. Estimated normal path: ~43s + up to 180s polling = ~223s, excluding new queue/pull/start/analysis time | Estimate, not a measured commit-to-healthy duration |
| Change failure rate | Two deliberately failed gateway changes (manual revision 3 and automatic revision 5) / six rollout revisions = 33.3% | Recovery is revision 6. The failed `gateway-no-traffic-check` is a fail-safe test, not a deployed failed change, and is excluded |
| Recovery time | Lab 7 manual abort → stable-only ready endpoints: 0.863s. Lab 5 completed revert push → Synced/Healthy: 17.01s | Different measurement endpoints; neither is the whole incident's MTTR |

Retained AnalysisRuns: `gateway-79795445b6-4-2` Successful; `gateway-5966c95bb7-5-2` Failed; `gateway-no-traffic-check` Failed. AnalysisRun count alone cannot detect the earlier manual abort. The successful run completed at 21:38:44Z and bad-canary analysis at 21:46:35Z on Oct 3. Detailed timings are in [Lab 7](lab7.md) and the rollback evidence committed as `1cbd746`.

ArgoCD auto-sync remains disabled; its source is fork-main, while the live local system includes later lab changes. Consequently this is not currently an unattended production GitOps pipeline. Align main with the intended manifests before enabling synchronization.

## 4. Top 3 Reliability Risks

1. **Events concurrency and pool exhaustion.** One 200m instance serves five gateway replicas, and a reservation nests two pool acquisitions. High load produces timeouts and pool errors. Fix nested acquisition/transaction cleanup, bound waiting with timeouts and backpressure, then validate two events replicas with higher CPU headroom. Increasing the pool alone is not a demonstrated fix.
2. **Reservation/payment correctness.** Availability checking and hold increment are separate Redis operations; the held counter has no matching TTL expiration, and successful payment can precede failed confirmation. Use an atomic reservation operation with expiration-safe accounting, idempotent payment/confirmation and reconciliation. Adding Redis replicas does not solve these consistency defects.
3. **Single-node durability and operational drift.** A PVC survived pod deletion but cannot protect against node/storage loss; Redis remains ephemeral. Lab 9 backup resources were removed after experiments, so backups are not currently running. Use durable off-site backups with restore drills, appropriate DB/Redis failover, and reconcile GitOps drift before relying on auto-sync.

## 5. Toil Identification

Only repeated tasks supported by recorded experiments are counted. Savings are proposals, not measured labour times.

| Manual work repeated >3 times | Evidence/frequency | Automation | Expected saving |
|---|---|---|---|
| Create/wait/check one-off database backups | Lab 9 ran seven manual Jobs, then validated five-file retention | Keep the tested CronJob enabled after approving durable storage; monitor last success and backup age; periodically restore automatically to a disposable DB | Replace seven routine operator-triggered runs with exception-only handling |
| Watch canary steps and copy metric readings | Lab 7 records five observation stages (20/40/60/80/100%) | Extend AnalysisTemplate with latency and minimum samples; save rollout/metric evidence automatically | Remove five manual stage checks per rollout, retaining review of failed analyses |
| Restore then manually verify database state | Lab 9 records isolated backup verification, dropped-table restore, full-loss restore, PVC cutover restore and scheduled-backup verification (at least five workflows) | A guarded restore-drill script checks archive TOC, isolated-target identity, schema revision, counts and an API smoke test; never auto-overwrite a live DB | Replace at least five repetitive verification sequences with reproducible checks; approval remains required for destructive recovery |

## 6. Monitoring Gaps

- In-cluster Prometheus currently has five healthy **gateway-only** targets. Events pool usage, acquisition errors/wait, payments durations, Redis memory/holds and PostgreSQL activity are missing from cluster dashboards. Compose targets and panels are not evidence of cluster coverage.
- Lab 6 alerts are Compose Grafana provisioning. Cluster paging/receiver delivery is not established. Verify the receiver with a controlled alert before assuming incidents page someone.
- The existing >5% error-rate alert is less sensitive than the 0.5% capacity gate; `noDataState: OK` can hide missing telemetry. Add no-target/no-traffic detection and multi-window burn-rate paging for the 99.5% SLO.
- Lab 8's 2s payment delay often returned 200 and would evade an error-only page. Add payment/gateway latency alerts and latency canary analysis; require a minimum request count. Do not blindly compare sparse p99s.
- Lab 8 Redis loss originally propagated through dependency probes. The probe fix kept reads serving, but did not make checkout healthy. Add synthetic reserve→pay→confirm checks and separate read/checkout SLIs; compare successful charges against confirmed orders.
- Today's pool failure needs a pool-exhaustion counter/alert and CPU-throttling telemetry. `kubectl top` does not measure throttling; a 200m observation alone is insufficient.
- Add backup-age/failure alerts, restore-drill results, PVC capacity and DB connection/idle-transaction alerts. Keep metrics history on persistent storage to support actual seven-day compliance.

## 7. Capacity Plan

### Measured headroom (Task 2)

At the 2u latency-gate failure, the 15:29:36Z sample showed events 32m, gateway 17/19/36/39/18m and payments 9m. There is **no demonstrated CPU ceiling at this low level**; tail latency and the small sample require further investigation. At the 10u error-gate failure (15:24:33Z):

```text
events-bd556f87d-9vbrp       152m   118Mi
gateway-79795445b6-jrdf5      41m    92Mi
gateway-79795445b6-n5rsx      30m    99Mi
gateway-79795445b6-tcv6b      41m    94Mi
gateway-79795445b6-wt9hr      40m    91Mi
gateway-79795445b6-zt6pk      39m    93Mi
payments-5744c894c8-m9s29     13m    83Mi
```

All three service types currently have 200m limits. At 50/100u, events reaches approximately that limit (205/206m sampled); gateway remains below its limits. Payments has no charge traffic in this scenario, so its headroom under real checkout is unknown. Raw `kubectl top` output is preserved in the load JSON and diagnostics, not inferred from resource requests.

### Proposed 2× plan

Use **1.61 RPS (2 × the 0.8057 RPS passing sample)** as the immediate 2× validation target; also test **3.34 RPS (2 × the first failing sample)** as a stretch target after fixing the pool. Neither is guaranteed capacity. The sampled boundary lies between 0.81 and 1.67 RPS; longer repeated tests must establish the safe operating point. Do not double the 17.95 failed-response RPS.

| Service | Current → proposed replicas | CPU request/limit per pod | Memory request/limit per pod | Reason |
|---|---|---|---|---|
| Gateway | 5 → 5 | 100m / 200m | 128Mi / 256Mi | Already five-way traffic distribution; no evidence ten replicas would fix events |
| Events | 1 → 2 | 250m / 500m | 128Mi / 256Mi | Remove nested pool acquisition first; distribute requests and increase CPU headroom |
| Payments | 1 → 1 | 50m / 200m | 64Mi / 256Mi | No demonstrated throughput constraint; validate full checkout before promising capacity |
| Redis | 1 → 1 for this local throughput experiment | 50m / 200m | 64Mi / 256Mi | Observed ~7m CPU, but single-pod is not an HA design |
| PostgreSQL | 1 → 1 with retained PVC | 100m / 500m | 256Mi / 512Mi | Observed low CPU; improve queries/transactions and measure IO before adding DB replicas |

Application-only totals: **10 pods, 1,200m CPU requests / 2,900m limits; 1,280Mi memory requests / 2,816Mi limits**. No scale/resource/source-code changes were applied as part of this plan. Validate longer repeated 1/2/10u tests after the pool fix, then a real payment/confirmation scenario; accept the 2× target only with <=0.5% 5xx and p99<=500ms.

There is **no PgBouncer/pooler pod** in the current manifests or Service list. The real path is one psycopg2 pool per events pod directly to PostgreSQL. Two pods with `DB_MAX_CONNS=10` mean at most 20 application connections versus the observed `max_connections=100`, leaving room for admin/backup sessions. Ten nested acquisitions are still unsafe within an individual pool. Start with connection reuse and bounded concurrency; consider a pooler only after measuring a connection bottleneck. PostgreSQL's [connection documentation](https://www.postgresql.org/docs/17/runtime-config-connection.html) explains that connection limits also affect resource allocation.

At the lab's explicit **$5/pod/month assumption**, nine current application pods cost $45/month; ten proposed pods cost $50/month (+$5). This is an illustrative pod-count estimate, not a cloud quote: storage, backup egress, nodes, monitoring and controllers are excluded. Production HA would additionally need Redis replication/Sentinel or managed failover, a database standby and off-site backup storage; price those separately. A replicated Redis setup improves availability, not reservation atomicity.

## Bonus — SRE handbook

[QuickTicket handbook](runbooks/quickticket-handbook.md) condenses architecture, exact GitOps flow, monitoring queries, incident response/escalation and backup/restore. It explicitly distinguishes present local state from the intended automated setup.

## Validation and checklist

- [x] Task 1: in-cluster 10/50/100u, low-load boundary, all seven review sections, actual DORA source history, three repeated toil items.
- [x] Task 2: measured per-pod CPU, replica/resource/connection/cost plan for 2× traffic.
- [x] Bonus: compact SRE handbook with architecture diagram and operational procedures.

Python syntax and actual Locust execution were verified. The runner now rejects another active Lab 10 Job, a reused Job name and any context other than `k3d-quickticket` before flushing Redis.

Cleanup removed all seven Lab 10 Jobs and the experiment ConfigMap; raw results remain in the repository. Test reservation holds were flushed. A final in-cluster smoke test returned healthy dependencies, six events, and a successfully confirmed reservation/payment on the dedicated event 6. The database now has **4,480 orders** (4,479 original + one verification order) and migration `0002_events_email`; the data PVC is still Bound. Gateway is 5/5 available; events/payments/PostgreSQL/Redis are 1/1 Running. See `lab10-cleanup.txt`. No original orders, events or database volume were deleted.

Moodle submission is a separate final step.
