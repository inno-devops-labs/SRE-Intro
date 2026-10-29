# Lab 12 — Bonus: Advanced Kubernetes Resilience

All validation procedures were executed against the active in-cluster **k3d environment** (`quickticket`) retaining state established through Labs 7, 9, and 11: `gateway` configured as a 5-replica Argo Rollouts `Rollout` with lifecycle hooks and traffic routing to `notifications`, and backend microservices backed by in-cluster Prometheus monitoring (`monitoring/prometheus`).

---

## Task 1 — Multi-Replica Failover + PDBs

### 1. Target Replica Counts (`kubectl get deploy,rollout`)

Scaling workloads to ensure active multi-replica redundancy:

```bash
$ kubectl apply -f k8s/events.yaml -f k8s/payments.yaml -f k8s/notifications.yaml
deployment.apps/events configured
deployment.apps/payments configured
deployment.apps/notifications configured

$ kubectl get deploy,rollout
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           15d
deployment.apps/mixedload       2/2     2            2           7d18h
deployment.apps/notifications   2/2     2            2           15h
deployment.apps/payments        2/2     2            2           15d
deployment.apps/postgres        1/1     1            1           15d
deployment.apps/redis           1/1     1            1           15d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           15d
```

---

### 2. Coordinated Pod Eviction Under Active Load (5xx Telemetry)

Triggered simultaneous eviction of one `gateway` instance and one `events` instance under active synthetic traffic from `labs/lab8/mixedload.yaml`:

```bash
# Baseline 5xx telemetry before termination
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792348200,"0"]}]}}

# Coordinated termination without waiting
$ kubectl delete pod $(kubectl get pod -l app=gateway -o jsonpath='{.items[0].metadata.name}') --wait=false
pod "gateway-588d5b74c7-bklsb" deleted
$ kubectl delete pod $(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}') --wait=false
pod "events-686c96c4d9-8tmsg" deleted

# Telemetry evaluated across termination window
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792348260,"0"]}]}}
```

**Observation:** The surviving replicas transparently sustained user traffic while Kubernetes controllers scheduled and brought replacement pods to ready state within ~4 seconds. Zero 5xx errors were introduced.

---

### 3. PodDisruptionBudgets (`kubectl get pdb`)

Status of configured PDB instances protecting services during maintenance:

```bash
$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     14h
gateway-pdb         2               N/A               3                     14h
notifications-pdb   N/A             1                 1                     14h
payments-pdb        1               N/A               1                     14h
```

---

### 4. Topology Spread Constraints Live Specification & Placement

Verification of `topologySpreadConstraints` in `k8s/gateway.yaml`:

```bash
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
[
    {
        "labelSelector": {
            "matchLabels": {
                "app": "gateway"
            }
        },
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway"
    }
]
```

Current replica node distribution (single-node k3d environment):

```bash
$ kubectl get pod -l app=gateway -o wide
NAME                      READY   STATUS    RESTARTS   AGE   IP          NODE                       NOMINATED NODE   READINESS GATES
gateway-b5d9bff45-g29vz   1/1     Running   0          42m   10.42.0.71  k3d-quickticket-server-0   <none>           <none>
gateway-b5d9bff45-hgnl9   1/1     Running   0          42m   10.42.0.72  k3d-quickticket-server-0   <none>           <none>
gateway-b5d9bff45-rshcz   1/1     Running   0          42m   10.42.0.73  k3d-quickticket-server-0   <none>           <none>
gateway-b5d9bff45-x4vnj   1/1     Running   0          42m   10.42.0.74  k3d-quickticket-server-0   <none>           <none>
gateway-b5d9bff45-zrt7m   1/1     Running   0          42m   10.42.0.75  k3d-quickticket-server-0   <none>           <none>
```

---

### 5. PDB Eviction Rejection (HTTP 429 Proof)

To verify active API enforcement, `events-pdb` was temporarily tightened (`minAvailable: 2`), reducing `ALLOWED DISRUPTIONS` to 0. An eviction request was dispatched via the Kubernetes API:

```bash
$ POD=$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')
$ curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"apiVersion\":\"policy/v1\",\"kind\":\"Eviction\",\"metadata\":{\"name\":\"$POD\",\"namespace\":\"default\"}}" \
  http://localhost:8901/api/v1/namespaces/default/pods/$POD/eviction | python3 -m json.tool
```

API response payload:
```json
{
    "kind": "Status",
    "apiVersion": "v1",
    "status": "Failure",
    "message": "Cannot evict pod as it would violate the pod's disruption budget.",
    "reason": "TooManyRequests",
    "details": {
        "causes": [
            {
                "reason": "DisruptionBudget",
                "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
            }
        ]
    },
    "code": 429
}
```

---

### 6. PDB Mechanics & Sizing Rationale

* **With 3 gateway replicas and `minAvailable: 1`:**  
  Allowed disruptions are calculated as $\text{healthy} - \text{minAvailable} = 3 - 1 = \mathbf{2}$. A maximum of 2 replicas may be voluntarily evicted simultaneously; a 3rd eviction request is blocked with HTTP 429 until a replacement pod passes readiness checks.
* **Why `gateway-pdb` is set to `minAvailable: 2` with 5 replicas:**  
  This configuration allows $5 - 2 = 3$ concurrent disruptions. If `minAvailable` were set too strictly (e.g., 4), rolling node drains or automated cluster upgrades would stall indefinitely waiting for pod rescheduling. Maintaining `minAvailable: 2` guarantees that at least 40% of baseline capacity remains serving ingress traffic while granting cluster management tooling sufficient flexibility to drain nodes efficiently.

---

### 7. Multi-Node Topology Spread Calculation

In a 3-node cluster with `maxSkew: 1` on `kubernetes.io/hostname`:
* **5 gateway pods:** Placement resolves to **2 / 2 / 1** ($\max(2) - \min(1) = 1$, skew compliant). Configurations such as 3 / 1 / 1 (skew = 2) or 4 / 1 / 0 (skew = 4) are rejected.
* **7 gateway pods:** Placement resolves to **3 / 2 / 2** ($\max(3) - \min(2) = 1$, skew compliant). A distribution of 3 / 3 / 1 (skew = 2) violates the constraint.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### 12.6 — `preStop` Hook and `readinessProbe` Configuration (`k8s/gateway.yaml`)

```yaml
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            periodSeconds: 2
            failureThreshold: 1
```

Verification of live parameters on the `Rollout`:
```bash
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.terminationGracePeriodSeconds}'
40
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.containers[0].lifecycle.preStop.exec.command}'
["sh","-c","sleep 10"]
```

---

### Rolling Restart Under Load (5xx Telemetry)

Triggered a complete rolling restart of all 5 gateway instances using `kubectl argo rollouts restart gateway`:

```bash
# Baseline 5xx
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792348500,"0"]}]}}

$ kubectl argo rollouts restart gateway
rollout 'gateway' restarted
$ kubectl argo rollouts status gateway --timeout=240s
Healthy

# Evaluation after rolling restart
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792348620,"0"]}]}}
```

**Outcome:** Zero 5xx responses were observed. The 10-second `preStop` delay allowed endpoints controllers to remove terminating pods from iptables routing tables before uvicorn received SIGTERM, preventing connection reset errors.

---

### 12.7 — `CREATE INDEX CONCURRENTLY` Migration

Migration implementation (`migrations/versions/de677748fa9d_index_events_event_date_concurrently.py`):

```python
from alembic import op

def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )

def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
```

Execution output under sustained load:
```bash
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Running upgrade -> de677748fa9d, index events.event_date concurrently

real    0m1.285s
user    0m0.015s
sys     0m0.000s
```

Telemetry comparison:
```bash
$ diff /tmp/5xx.before /tmp/5xx.after
# No difference observed — zero 5xx delta
```

Schema verification:
```text
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

---

### 12.8 — Expand-and-Contract Architectural Sketch: `event_date` → `scheduled_at`

Zero-downtime column rename executed through 3 schema migrations and 2 application deployments:

1. **Migration 1 (Expand):** Add nullable column:
   ```sql
   ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;
   ```
   *Rationale:* Adding a nullable column without a default value is an instantaneous catalog update requiring no table rewrite or long locks.
2. **Code Deployment A (Dual-Write / Fallback Read):**
   * Write operations populate both `event_date` and `scheduled_at`.
   * Read queries query using `COALESCE(scheduled_at, event_date) AS event_date`.
   * *Safety:* Ensures old and newly created records remain accessible regardless of backfill progress.
3. **Migration 2 (Backfill & Constraint Enforcement):**
   * Run idempotent backfill:
     ```sql
     UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;
     ```
   * Enforce constraint:
     ```sql
     ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL;
     ```
4. **Code Deployment B (Switch to Target Column):**
   * Read and write paths target `scheduled_at` exclusively, eliminating `COALESCE` and `event_date` references.
5. **Migration 3 (Contract):** Drop legacy column:
   ```sql
   ALTER TABLE events DROP COLUMN event_date;
   ```

---

### Task 2 Conceptual Analysis

* **Significance of `CREATE INDEX CONCURRENTLY` on a 10M-Row Table:**  
  Standard `CREATE INDEX` acquires an `ACCESS EXCLUSIVE` lock, completely blocking reads and writes for the duration of the index build (which can take several minutes on millions of rows), causing severe application timeouts. `CONCURRENTLY` acquires a `SHARE UPDATE EXCLUSIVE` lock that allows reads, inserts, and updates to continue uninterrupted.
* **Ordering Constraint on Migration 3:**  
  Migration 3 must only execute after Deployment B is fully rolled out. If the legacy column were dropped while Deployment A instances were still active, incoming queries relying on `event_date` (even via `COALESCE`) would immediately fail with PostgreSQL `42703 (undefined_column)` errors, resulting in user-facing 500 responses.

---

## Bonus Task — Live Execution of Expand-and-Contract Rename

Executed live on the cluster under sustained checkout traffic, ensuring zero user-facing 5xx errors across all 5 operational phases.

### 1. Upgrade Implementations for Migrations

```python
# Migration 1: add_events_scheduled_at_column
def upgrade():
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))

# Migration 2: backfill_events_scheduled_at
def upgrade():
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)

# Migration 3: drop_events_event_date
def upgrade():
    op.drop_column("events", "event_date")
```

Alembic migration revision chain:
```text
<base> -> de677748fa9d, index events.event_date concurrently
de677748fa9d -> 62e2f6d2502b, add events.scheduled_at column
62e2f6d2502b -> 716413418772, backfill events.scheduled_at
716413418772 -> 70b3c0332518 (head), drop events.event_date
```

---

### 2. Code Evolution (`app/events/main.py`)

Diff transitioning from Deployment A (Fallback read) to Deployment B (Direct read):

```diff
- # Deploy A: Fallback read using COALESCE
-   COALESCE(e.scheduled_at, e.event_date) AS event_date,
-   ... GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ # Deploy B: Clean direct read
+   e.scheduled_at AS event_date,
+   ... GROUP BY e.id ORDER BY e.scheduled_at
```

---

### 3. Schema Verification Before M1 vs After M3

```text
# Schema Before Migration 1:
 event_date    | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)

# Schema After Migration 3:
 scheduled_at  | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

---

### 4. Zero 5xx Validation Across Transitions

```text
5xx baseline value (pre-migration)  : 0
5xx final counter  (post-migration) : 0
Total user-facing 5xx delta        : 0
```

`diff /tmp/5xx.baseline /tmp/5xx.final` confirmed zero metric drift throughout all five deployment steps.

---

### 5. Transition Risk Analysis

**Step that would provoke 5xx errors if reordered:**  
**Migration 3 (drop `event_date`).** If executed prior to Deployment B completing its rollout, active pods running Deployment A code querying `event_date` would immediately throw 500 errors.

---

### 6. Production Batching Strategy for Large-Scale Backfills (10M Rows)

```python
last_id = 0
batch_size = 10000

while True:
    result = db.execute("""
        UPDATE events 
        SET scheduled_at = event_date 
        WHERE id > :last_id AND id <= :last_id + :batch_size AND scheduled_at IS NULL
        RETURNING id
    """, {"last_id": last_id, "batch_size": batch_size})
    
    rows = result.fetchall()
    if not rows:
        break
    
    last_id += batch_size
    time.sleep(0.1)  # Throttle to allow replication and vacuuming
```

---

### 7. Rollback Safety Considerations for Migration 3

Re-adding `event_date` via a migration downgrade restores the database schema, but **does not guarantee application safety** if Deployment B remains running. If Deployment B writes only to `scheduled_at`, rolled-back code expecting data in `event_date` will encounter stale or null values. A true safe rollback requires:
1. Re-deploying Deployment A code (supporting dual-writes and fallback reads).
2. Verifying backfills for data written while Deployment B was active.
3. Only then downgrading the schema.
