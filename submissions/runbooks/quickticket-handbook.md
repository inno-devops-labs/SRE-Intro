# QuickTicket Platform SRE Handbook

This document serves as the single source of truth for platform architecture, continuous deployment, monitoring matrix, and incident remediation workflows for the QuickTicket microservice stack.

---

## 1. Architecture & Component Topology

The platform runs inside an isolated Kubernetes cluster, leveraging decoupled layers for ingress routing, stateless computational logic, low-latency caching, and persistent stateful storage.

### Core Architectural Diagram
```text
                     [ Ingress / External Traffic ]
                                   │ (Port 8080)
                                   ▼
                       ┌──────────────────────┐
                       │  deployment/gateway  │ (5-6 Replicas)
                       └──────────┬───────────┘
                                  │
         ┌────────────────────────┴────────────────────────┐
         ▼ (HTTP/Internal)                                 ▼ (HTTP/Internal)
┌─────────────────┐                               ┌──────────────────┐
│deployment/events│ (1-3 Replicas)                │deployment/payments│ (1-2 Replicas)
└─┬─────────────┬─┘                               └──────────────────┘
  │             │
  │ (TCP:6379)  │ (TCP:5432)
  ▼             ▼
┌───────────┐ ┌───────────────┐
│ pod/redis │ │ pod/postgres  │ (Stateful PVC attached)
└───────────┘ └───────────────┘
```
**Infrastructure Bulletins**
* **Edge Gateway Layer (deployment/gateway):** Acts as the reverse proxy and reverse connection load balancer. Evenly distributes client requests to downstream logic endpoints via kube-proxy.

* **Events Microservice (deployment/events):** The core transaction logic processor. Manages event schedules and ticket inventories. Highly dependent on both Redis (for fast ticket reservation states) and PostgreSQL (for hard relational persistence).

* **Payments Microservice (deployment/payments):** Processes checkout confirmations asynchronously. Operates independently from the database cluster to isolate financial transactions.

* **Cache Tier (pod/redis):** Ephemeral, single-pod cache engine dedicated to preventing inventory race conditions and handling short-lived database ticket holds.

* **Database Tier (pod/postgres):** Relational storage anchored to a 1Gi PersistentVolumeClaim (PVC). Configured with localized database schemas to survive individual pod eviction loops.

## 2. How to Deploy (GitOps Workflow)

The platform strictly adheres to a declarative GitOps deployment model managed by ArgoCD and Argo Rollouts. New team members must execute all infrastructure and code updates via the following progressive pipeline:

* **Step 1: Feature Isolation & Local Changes**
  * Engineers develop features or apply infrastructure modifications inside a dedicated branch (`feature/your-feature`).
  * Relational database mutations must be defined as incremental python scripts within the `migrations/` catalog using Alembic.

* **Step 2: Pull Request & CI Image Assembly**
  * Open a Pull Request targeting the `main` branch. 
  * The CI workflow catches the event, packages the updated application binaries into a minimal Docker image, tags it with the short Git commit SHA, and publishes it to the secure container registry.

* **Step 3: Manifest State Mutation (The GitOps Trigger)**
  * Update the target workload image tag inside the unified configuration file (`k8s/chart/values.yaml`) to reference the newly generated commit SHA.
  * Merge the Pull Request directly into the authoritative `main` branch.

* **Step 4: ArgoCD Automated Synchronization**
  * The internal ArgoCD controller continually monitors the `main` branch configuration state against the live state of the k3d cluster.
  * Once a state drift is discovered, ArgoCD instantly triggers a automated synchronization block, deploying the modified Helm manifests to the targeted namespace.

* **Step 5: Progressive Delivery Execution**
  * Because workloads are controlled by `Argo Rollouts`, the system avoids risky cut-over actions. It spins up a isolated canary instance and shifts an initial fraction of traffic (e.g., 20%).
  * The system validates stability using live telemetry queries run by an `AnalysisTemplate`. If error metrics remain clean, it smoothly promotes the release; if anomalies spike, it instantly rolls back to the stable baseline state.

## 3. Monitoring Matrix & Observability

The platform leverages Prometheus to track the Four Golden Signals of SRE (Latency, Traffic, Errors, and Saturation). On-call engineers must use the following production queries within the Prometheus UI to audit system health:

### Core Telemetry Overviews & PromQL Catalog

* **Global Error Ratio (Tracking SLO Compliance)**
  * **Query:** `sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m]))`
  * **Operational Target:** Must remain below **`0.005`** (0.5% error rate). Any sustained value above 0.02 indicates that the gateway is dropping connections (`502 Bad Gateway`) or downstream components have exhausted internal worker queues.

* **Tail Latency Matrix (User Experience Check)**
  * **Query:** `histogram_quantile(0.99, sum by (le, path) (rate(gateway_request_duration_seconds_bucket[1m])))`
  * **Operational Target:** `p99 <= 500ms`. 
  * **Critical Triage Note:** If a dependent microservice saturates its internal runtime, this query might return **`NaN`** (Not a Number) or drop endpoints (like `/pay`) entirely from telemetry loops. If `NaN` appears on transactional endpoints, immediately cross-reference internal pod saturation.

* **Traffic Routing Balance (Per-Pod Volume)**
  * **Query:** `sum by (pod) (rate(gateway_requests_total[1m]))`
  * **Operational Target:** Balanced load distribution (~1.0 to 1.5 RPS per pod under standard load scenarios). A complete traffic dive to 0 RPS on a single instance indicates a failing pod readiness probe or an unmitigated container lifecycle lock.

* **Missing Traffic Check (Dead-Man Switch alert)**
  * **Query:** `sum(rate(gateway_requests_total{path="/pay"}[2m])) == 0`
  * **Operational Target:** Must evaluate to false. If a vital business transaction path drops to zero total requests during active user windows, an automated upstream outage is likely blocking client checkout flows before traffic can reach the tracking metrics.

## 4. Incident Response & On-Call Runbooks

When an automated alert fires and delivers a payload to the webhook or paging system, the on-call engineer must execute this distilled triage sequence.

### Triage Workflow (High Error Rate / SLO Burn)

* **Step 1: Verify Global Endpoint Liveness**
  Check the platform's external health router from inside the cluster using a temporary container to isolate network rules:
```bash
  kubectl run triage-probe --image=curlimages/curl:latest --rm -i --restart=Never --quiet -- http://gateway:8080/health
```

*Expected Output:* `{"status":"ok", ...}`. If it returns a 502 or 503 code, the issue resides at the ingress or database mapping layer. If it responds with `"status":"degraded"`, check the underlying downstream service dependencies.

* **Step 2: Inspect Pod Status & Restarts**
  Look for structural scheduling failure loops, high restart counts, or `OOMKilled` states across the active workload namespaces:
  ```bash
  kubectl get pods -o wide
  ```

* **Step 3: Tail Active Workload Logs**
  If pods are running but errors persist, stream the aggregate error traces directly from the primary microservice deployments:
  ```bash
  kubectl logs deployment/gateway --tail=50
  kubectl logs deployment/events --tail=50
  ```

| Failure Mode Identified | Diagnostic Indicator | Immediate Remediation Action |
| :--- | :--- | :--- |
| **Downstream Microservice Crash** | Pod status shows `Error` or `CrashLoopBackOff`. | Force a clean rolling restart of the deployment to clear deadlock flags:<br>`kubectl rollout restart deployment/<service-name>` |
| **Database Connection Pool Starvation** | `events` logs show pool allocation timeouts or connection refused. | Temporarily scale up database pool boundaries dynamically via env patches:<br>`kubectl set env deployment/events DB_MAX_CONNS=30` |
| **Redis Cache Eviction / Loss** | `events` health reports `redis: down`. Triage probe returns status `degraded`. | Check pod status and verify the storage binding before forcing a cold restart:<br>`kubectl scale deployment/redis --replicas=0`<br>`kubectl scale deployment/redis --replicas=1` |
| **Misconfigured Failure Environment** | High volumes of 5xx metrics match recent GitOps image updates. | Revert the faulty image variable adjustment immediately or execute an official rollback command:<br>`kubectl argo rollouts abort gateway` |

---

## 5. Backup & Restore Procedures

The platform utilizes a multi-tiered stateful reliability strategy that combines persistent decoupled block storage for automated near-instant self-healing and programmatic scheduled snapshotting for point-in-time disaster recovery.

### Core Continuity Objectives
* **Recovery Time Objective (RTO):** **6 seconds**. Because the database is anchored to a persistent block layer via a `PersistentVolumeClaim` (`postgres-data`), individual container evictions or node crashes bypass manual data transplantation completely. The newly rescheduled pod hot-plugs the existing disk and achieves full operational liveness inside a 6-second window.
* **Recovery Point Objective (RPO):** **5 minutes**. Secured by a cluster-native automation loop that continually preserves isolated historical data snapshots.

### Automated Backup Architecture
The cluster executes a dedicated `batch/v1` `CronJob` named `postgres-backup` configured to run **every 5 minutes** (`*/5 * * * *`).
* **Storage Isolation:** Dumps are compressed using the custom PostgreSQL archive format (`-Fc`) and shipped onto a dedicated `postgres-backups` persistent volume claim.
* **Retention and Pruning Loop:** To prevent storage volume exhaustion, the container runs a trailing cleanup command:
  ```bash
  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm
  ```
### Critical Emergency Manual Workflows

#### Ad-hoc Backup Generation (Pre-Migration Snapshot)
Always run this script manually before executing schema transformations via Alembic or running infrastructure lifecycle alterations:

```bash
# 1. Target and fetch the active PostgreSQL pod identifier
DB_POD=$(kubectl get pods -l app=postgres -o jsonpath='{.items[0].metadata.name}')

# 2. Extract a compressed custom binary dump directly to local storage
kubectl exec -i $DB_POD -- pg_dump -Fc -U quickticket -d quickticket > backup_pre_migration_$(date +%Y%m%d_%H%M%S).dump
```

#### Catastrophic Disaster Recovery (Database Reconstruction)
If a severe data-corruption incident or manual accident occurs (such as an unmitigated `DROP TABLE orders CASCADE`), execute the following recovery protocol to rebuild the environment:

```bash
# 1. Target the running PostgreSQL pod instance
DB_POD=$(kubectl get pods -l app=postgres -o jsonpath='{.items[0].metadata.name}')

# 2. Force-drop the active corrupted database to tear down active connection locks
kubectl exec -i $DB_POD -- psql -U quickticket -d postgres -c "DROP DATABASE quickticket WITH (FORCE);"

# 3. Spin up an empty target database shell
kubectl exec -i $DB_POD -- psql -U quickticket -d postgres -c "CREATE DATABASE quickticket;"

# 4. Stream a valid clean SQL or custom snapshot back into the container instance
kubectl exec -i $DB_POD -- psql -U quickticket -d quickticket < backup_latest.sql

# 5. Cycle dependent applications to drop stale pool links and force a clean connection handshake
kubectl rollout restart deployment/events
```