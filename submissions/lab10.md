# QuickTicket Reliability Review

## 1. SLO Compliance

| SLO | Target | Observed | Status |
| :--- | :--- | :--- | :--- |
| **Availability** | ≥ 99.90% | **99.20%** | **Breached** |
| **Error Rate (5xx)** | ≤ 0.10% | **13.32%** | **Breached** |
| **Latency (p95)** | ≤ 50 ms | **720 ms** | **Breached** |
| **Latency (p50)** | ≤ 10 ms | **310 ms** | **Breached** |
## 2. Load Test Results

| Users | Ramp |   RPS | p50 | p95 | p99 | 5xx error rate |       409 (inventory) |
| ----: | ---: | ----: | --: | --: | --: | -------------: | --------------------: |
|    10 |  2/s |  7.85 |   7 |  11 |  18 |             0% |                     0 |
|    50 |  5/s | 37.48 |   6 |  13 |  29 |             0% |  43<br>(1.92% / 100%) |
|   100 | 10/s | 59.53 | 310 | 720 | 930 |         13.32% | 44<br>(1.24% / 8.49%) |

## 3. DORA Metrics

| Metric | Value | DORA Performance Tier | Data Source / Calculation |
| :--- | :--- | :--- | :--- |
| **Deployment Frequency** | **10 rollouts** (across 57 commits) | High / Medium | `kubectl get rs` (10) & `git log --oneline` (57) |
| **Lead Time for Changes** | **~3–5 minutes** | Elite | CI build time + 3-minute ArgoCD poll interval |
| **Change Failure Rate** | **16.67%** | Medium | 1 Failed / 5 Successful `AnalysisRun` objects |
| **Time to Restore Service** | **~30 sec** (auto) / **~3 min** (manual) | Elite / High | Argo Rollouts auto-abort vs. GitOps `git revert` sync |

## 4. Top 3 Reliability Risks
1. **Traefik Load Balancer Saturation** — Under peak loads (100 users), the network proxy layer (`svclb-traefik`) crashed 82 times, cutting off client traffic and triggering 502 Bad Gateway errors. Implementing an automated Horizontal Pod Autoscaler (HPA) for the Traefik deployment and increasing its resource CPU/Memory requests/limits would fix it.
2. **Cascading Gateway Upstream Failures** — When traffic scales to 100 users, response time percentiles spike catastrophically (p95 at 720ms) and unleash 500/503 errors, indicating that the gateway is blocking on synchronous downstream bottlenecks. Adding connection pooling, configuring explicit circuit breakers, and enforcing strict request timeouts in the gateway configuration would fix it.
3. **Stateless Postgres Infrastructure Vulnerability** — The system relied on manual database seedings (`seed.sql`) over 8 times due to volatile pod restarts before a PersistentVolumeClaim (PVC) was introduced. Migrating database schema evolution and initial seeding to an automated migration framework like **Alembic** run via CI/CD pipelines or Kubernetes InitContainers would fix it.



## 5. Toil Identification

| Manual Task                              | How Often Performed                                            | How to Automate                                                                                                                       | What You'd Save (Value)                                                                                                            |
| :--------------------------------------- | :------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------ | :--------------------------------------------------------------------------------------------------------------------------------- |
| **1) Re-creating port-forwards**         | **10+ times** (after every pod restart, crash, or eviction)    | Implement an **Ingress Controller** Kubernetes object for persistent routing.                                                         | **~2–3 minutes per disconnect.** Eliminates broken connections to UI/APIs and removes the need to waste open terminal tabs.        |
| **2) Running `seed.sql` manually**       | **8+ times** (on every Postgres restart before adding the PVC) | Implement a PVC Kubernetes object that will saves the data after each Postgres pods restart.                                          | **~5 minutes per database wipe.** Guarantees immediate test data availability with zero context-switching or manual CLI execution. |
| **3) Manually watching canary rollouts** | **13+ times** (via `kubectl argo rollouts ... --watch`)        | Fully delegate release validation to automated **AnalysisTemplates** querying Prometheus metrics, combined with ArgoCD Notifications. | **~3–5 minutes per deployment.**                                                                                                   |

## 6. Monitoring Gaps
* **What you wished you had been monitoring during Lab 8 chaos experiments:**
  * **Load Balancer Internal Metrics:** Detailed connection state metrics (`traefik_entrypoint_open_connections_total`) and memory usage of the `svclb-traefik` pod to observe how close the proxy was to crashing during stress or fault injections.
  * **Database Connection Pool Saturation:** Metrics showing active vs. idle database connections to precisely detect when upstream services start blocking on PostgreSQL.
  * **Pod Restart Triggers:** Real-time event streams capturing *why* pods were killed (e.g., OOMKilled vs. failed Liveness Probes).
* **What alert would have caught the thing that actually broke?**
  * A **KubePodCrashLooping** or high restart rate alert on the `kube-system` namespace (`sum(changes(kube_pod_container_status_restarts_total[5m])) > 2`) would have caught the 82 `svclb-traefik` proxy restarts instantly before they escalated into a 13% error rate cascade.
  * An **HTTP 5xx Spike Alert** triggering if `sum(rate(nginx_ingress_controller_requests{status=~"5.."}[1m])) / sum(rate(nginx_ingress_controller_requests[1m])) > 0.01` (greater than 1% for 2 consecutive minutes).

## 7. Capacity Plan
* **Current ceiling:** **37.48 RPS** (at 50 users). This is the highest stable throughput where the system maintains perfect SLO compliance (0% 5xx errors, p95 latency under 13ms). 
* **For 2x traffic, scale:** 
  * Target capacity needed: **~75–80 RPS** (to comfortably sustain double the current 37.48 RPS ceiling).
  * **Gateway & App Pods:** Scale the `gateway`, `events`, and `payments` deployments from the current static count to a minimum of **6 replicas each** using a Horizontal Pod Autoscaler (HPA) targeting 60% CPU utilization.
  * **Load Balancer:** Increase `svclb-traefik` resource limits (`limits.cpu` to `500m`, `limits.memory` to `512Mi`) to prevent the 82-restart crashing loop witnessed under heavy load.
* **Rough cost estimate:**
  * Assuming a standard cloud provider environment (e.g., AWS EKS using `t3.medium` worker nodes at ~$30/month per node), doubling the compute footprints and resource limits will require adding **2 additional worker nodes** to the cluster.
  * Estimated total incremental infrastructure cost: **+$60 / month**.
# Task 2
### 10.7: Per-Pod Headroom Analysis at Breaking Point

| Service Deployment | Replicas | Observed CPU Range (per pod) | Status                          | Analysis                                                                                                           |
| :----------------- | :------: | :--------------------------- | :------------------------------ | :----------------------------------------------------------------------------------------------------------------- |
| `app=events`       |    1     | **76m – 145m**               | **CPU-Constrained**             | This single-replica service is the main system bottleneck, drawing the highest CPU to process ticket reservations. |
| `app=gateway`      |    5     | **9m – 45m**                 | **Idle / Substantial Headroom** | Traffic is well-distributed via kube-proxy across 5 pods, leaving plenty of compute headroom.                      |
| `app=payments`     |    1     | **8m – 9m**                  | **Idle / Idle Headroom**        | Minimally utilized during checkout flows, sitting completely safe from resource exhaustion.                        |

The bottleneck is strictly localized at the `events` service layer. Under peak load, its single replica hits its performance ceiling, slowing down response times down the stack. To push past the breaking point, scaling must prioritize the `events` deployment rather than the already well-provisioned `gateway` or under-utilized `payments` services.

### 10.8: Capacity Plan for 2× Traffic

To reliably sustain double the current stable traffic (targeting a stable ~75–80 RPS ceiling), the following structural scaling and infrastructure configurations are required:

#### 1. Replica Counts & Resource Specs
* **`gateway`**: Maintain **5 replicas** (already sufficient based on current headroom). 
  * *Requests/Limits:* `cpu: 100m/200m`, `memory: 64Mi/128Mi`
* **`events`**: Scale from 1 to **3 replicas** to remove the single-pod CPU constraint.
  * *Requests/Limits:* `cpu: 200m/400m`, `memory: 128Mi/256Mi`
* **`payments`**: Scale from 1 to **2 replicas** for baseline high-availability (HA) redundancy.
  * *Requests/Limits:* `cpu: 50m/100m`, `memory: 64Mi/128Mi`

#### 2. Stateful Layer Infrastructure Assessment
* **Redis Performance:** A **single-pod setup is still perfectly OK**. Redis is an in-memory, single-threaded key-value store that can handle tens of thousands of requests per second per core. At ~80 RPS, a replicated setup is complete overkill and adds unnecessary architectural complexity.
* **Database Connection Bottleneck:** Yes, the single-pooler-to-single-Postgres path **is a critical bottleneck**. As application replicas multiply, simultaneous unpooled TCP handshakes will exhaust PostgreSQL's `max_connections` and cause locks. Introducing a connection pooler like **PgBouncer** is mandatory to multiplex connections efficiently.

#### 3. Rough Cost Estimate
* **Total Pod Count:** 10 application pods (`5 gateway` + `3 events` + `2 payments`) + 2 database/cache pods (`1 postgres` + `1 redis`) = **12 pods total**.
* **Monthly Infrastructure Cost:** At an estimated small-cloud benchmark of $5/pod/month:
  $\text{Total Cost} = 12 \text{ pods} \times \$5 = \$60 / \text{month}$
