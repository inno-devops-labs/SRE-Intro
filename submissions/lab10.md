# QuickTicket Reliability Review

## 1. SLO Compliance

Availability and latency SLOs are derived from the production recording rules configured in Prometheus (`monitoring/prometheus/rules.yml`), while migration and recovery indicators reflect validation conducted during database resilience testing.

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (non-5xx) | ≥ 99.5% (≤ 0.5% 5xx) | 100% at ≤10 users (~7.7 RPS); **0.82% 5xx at 20 users (~14.3 RPS)** | ✅ within capacity / ❌ above ~14 RPS |
| Latency (share < 500ms) | ≥ 95% of requests < 500ms | p95 23ms at 10u; **p95 470ms / p99 1100ms at 20u** | ✅ at ≤10u / ❌ above ~14 RPS |
| Migration safety | 0 downtime | The `email` column migration was executed as an additive nullable column on `events` (catalog metadata update only without table rewrite or long locks) — zero downtime achieved under load. | ✅ |
| DB recovery (RTO) | < 5 min | **~12s – 20s** recorded upon forced PostgreSQL pod deletion; replacement pod mounted the `postgres-data` PVC with all table records fully preserved. | ✅ |

---

## 2. Load Test Results

Each test iteration ran for 60 seconds using the in-cluster Locust runner (`--headless`, `--only-summary`). Redis was flushed between test runs to eliminate inventory exhaustion artifacts. HTTP 409 responses reflect anticipated business inventory depletion, whereas HTTP 5xx responses represent system degradations violating SLO criteria.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10 | 2/s | 7.72 | 15ms | 23ms | 78ms | 0.00% | 0 |
| 20 | 3/s | 14.34 | 20ms | 470ms | 1100ms | 0.82% | 0 |
| 50 | 5/s | 21.97 | 630ms | 2000ms | 7200ms | 48.48% | 0 |
| 100 | 10/s | 26.25 | 2000ms | 5800ms | 15000ms | 70.09% | 0 |

**Breaking point analysis:**  
The architecture experiences its initial SLO breach at **20 concurrent users (~14.3 RPS)**, where the 5xx error rate climbs to 0.82% (exceeding the 0.5% threshold) and p99 latency degrades to 1100ms (exceeding 500ms). System throughput saturates near **~26 RPS** at 100 users; increasing concurrency beyond 20 users primarily induces internal connection queuing and cascading timeouts rather than processing valid transactions.

---

## 3. DORA Metrics

| Metric | Value | Source data | DORA tier |
|--------|-------|-------------|-----------|
| Deployment Frequency | 10 gateway rollout revisions deployed; 5 GitOps automated image-tag deployments synced via CI | `kubectl get rs -l app=gateway` = 10; 5 `"ci: update image tags"` Git commits; 54 commits on `main` | Medium |
| Lead Time for Changes | ~10–15 minutes (sequential CI build/push for 3 images ~5 min + 3-min ArgoCD reconciliation interval + ~5-min canary AnalysisRun evaluation) | Workflow execution history; ArgoCD synchronization interval | Elite (< 1 day) |
| Change Failure Rate | 2/5 canary AnalysisRuns flagged **Failed** (40% at validation gate), but **0% reached stable production** | `kubectl get analysisrun`: 2 Failed / 3 Successful; automatic abort kept stable pods healthy | Elite (user-facing impact) |
| Mean Time to Restore (MTTR) | ~5–7 seconds for in-cluster automated abort (`ActualWeight: 0`); ~3–5 minutes for end-to-end `git revert` GitOps reconciliation | Argo Rollouts controller abort telemetry; ArgoCD GitOps sync | Elite (< 1 hour) |

---

## 4. Top 3 Reliability Risks

1. **Liveness probes checking downstream dependencies with aggressive 1s timeouts:**  
   The gateway liveness probe targets `/health`, which performs synchronous connectivity checks against downstream dependencies. Under saturation, `/health` latency exceeds the 1s timeout, causing kubelet to mark healthy containers dead and restart all gateway pods simultaneously. This amplifies load into an outright outage.  
   *Remediation:* Decouple probes by introducing an isolated `/livez` endpoint (inspecting local process liveness only) and reserve downstream connectivity checks for `/readyz` with `timeoutSeconds: 3-5s`.

2. **Restrictive per-pod CPU limits without horizontal autoscaling:**  
   Gateway and events deployments enforce rigid `cpu: 200m` limits without a configured HorizontalPodAutoscaler (HPA). Under stress, the single `events` pod reaches 181m (~90% allocation), triggering CPU throttling. This slows request processing, backing up database connection pools and tripping timeouts.  
   *Remediation:* Increase resource limits (`events` to 1000m, `gateway` to 500m) and configure an HPA targeting 70% average CPU utilization.

3. **Single points of failure across the data and application tiers:**  
   The `events` service operates as a single replica despite handling database-intensive workloads. Similarly, PostgreSQL and Redis run as standalone instances without high-availability clustering.  
   *Remediation:* Scale `events` to $\ge 2$ replicas with a `PodDisruptionBudget` (`minAvailable: 1`) and evaluate PostgreSQL read replicas with connection pooling via PgBouncer.

---

## 5. Toil Identification

| Toil Task | Frequency | Automation Strategy | Operational Savings |
|-----------|-----------|---------------------|---------------------|
| Executing ad-hoc Prometheus queries via `kubectl exec` into monitoring pods due to isolated network bridge boundaries | Dozens of executions across performance and chaos testing | Deploy Grafana directly inside the k3d cluster with in-cluster Prometheus pre-configured as the default datasource | ~1–2 minutes per query across testing workflows |
| Re-initiating `kubectl port-forward` tunnels following pod restarts or session timeouts | Repeated daily across development workflows | Deploy a cluster Ingress controller (e.g. Traefik / NGINX Ingress) mapped to host ports | Eliminates repetitive tunnel management |
| Manually observing canary rollouts via `kubectl argo rollouts get rollout --watch` | Every deployment iteration | Establish automated Slack/Webhook alerting triggered directly by Argo Rollouts promote and abort events | Saves 5–8 minutes of manual monitoring per release cycle |

---

## 6. Monitoring Gaps

- **Absence of dedicated latency SLO alerts:**  
  Alert configurations historically tracked only 5xx error ratios. A degraded downstream service responding with 200 OK after multiple seconds of latency (as observed during payment latency injection) fails to trip error alerts. Implementing a multi-window burn-rate alert on p95/p99 duration bridges this blind spot.
- **Missing container restart alerts:**  
  Cascading liveness probe failures triggering pod restart loops produced no direct operational page. An alert monitoring `rate(kube_pod_container_status_restarts_total[5m]) > 0` would immediately surface container instability.
- **Unmonitored resource saturation:**  
  Connection pool saturation and container CPU throttle metrics lacked active alert definitions.
- **Alert that would have detected the primary failure:**  
  In Redis failure scenarios, aggregate error ratios took several minutes to breach the 5m evaluation window. An SLO alert pairing a fast-burn rate (~2% budget burn over 1m) with the standard 5m window would alert operators within 1–2 minutes.

---

## 7. Capacity Plan

### Breaking-Point Telemetry (50-User Load)
During high-load testing, CPU utilization across components demonstrated uneven allocation:

| Service | Measured CPU | Configured Limit | Replicas | Operational Assessment |
|---------|-------------:|-----------------:|---------:|------------------------|
| gateway | 120–148m per pod | 200m | 5 | ~74% utilization across pods |
| events | 150–181m | 200m | 1 | ~90% limit saturation; primary bottleneck |
| payments | 13–19m | 200m | 1 | Low utilization under test workload |
| redis | 11–12m | Uncapped | 1 | Low CPU consumption |
| postgres | 74–122m | 200m | 1 | Moderate database utilization |

The system bottleneck is primarily driven by per-pod container throttling on the single `events` pod rather than physical node saturation.

### Scaling Architecture for 2× Throughput (~30 RPS)
1. **Probe and Limit Adjustments:**  
   Separate liveness and readiness probe targets and raise container resource allocations: `gateway` (requests: 100m, limits: 500m), `events` (requests: 250m, limits: 1000m), `postgres` (requests: 250m, limits: 1000m).
2. **Replication Strategy:**  
   - `events`: scale from 1 to **3 replicas** to distribute database processing and connection pool load.
   - `gateway`: maintain **5 replicas** with an HPA configured to scale between 3 and 8 pods based on CPU.
   - `payments`: maintain 1 replica (sufficient for expected checkout volume).
   - `redis`: retain single instance (12m consumption demonstrates ample headroom).
   - `postgres`: maintain single instance with connection pool settings adjusted (`max_connections` and pooler capacity increased proportionally to match 3 `events` replicas).
3. **Infrastructure Cost Projection:**  
   Expanding from 9 to 11 total pods represents an estimated incremental cost of ~$10/month (from ~$45/month to ~$55/month based on small-cloud container economics).
