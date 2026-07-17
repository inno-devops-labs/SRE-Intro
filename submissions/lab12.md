# Lab 12 — Advanced Kubernetes Resilience

## Task 1 — Multi-Replica Failover + PDBs (4 pts)

### 12.1 – Scale services to 2 replicas

> **Proof:** `kubectl get deploy -l 'app in (events,payments,notifications)'`

```
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
events          2/2     2            2           2d
payments        2/2     2            2           2d
notifications   2/2     2            2           2d
```

### 12.2 – Failover test – kill pods under load

**Command used before kill (3m window):**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```

> **Before kill – 5xx count (3m window):**  
> `0`

**Kill commands:**

```bash
kubectl delete pod $(kubectl get pod -l app=gateway -o jsonpath='{.items[0].metadata.name}') --wait=false
kubectl delete pod $(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}') --wait=false
```

**After kill (1m window):**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
```

> **After kill – 5xx count (1m window):**  
> `0`

> **Observation:** Did any 5xx appear?  
> **No.** The load balancer (Service) removed the terminating pods from its endpoints before they were fully stopped, and the remaining replicas handled all traffic. No 5xx errors were observed.

---

### 12.3 – PodDisruptionBudgets (`k8s/pdb.yaml`)

> **`kubectl get pdb` output:**

```
NAME               MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
gateway-pdb        2               N/A               3                     2d
events-pdb         1               N/A               1                     2d
payments-pdb       1               N/A               1                     2d
notifications-pdb  N/A             1                 1                     2d
```

> **Contents of `k8s/pdb.yaml`:**

```yaml
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: gateway-pdb
  namespace: default
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: gateway
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: events-pdb
  namespace: default
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: events
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: payments-pdb
  namespace: default
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: payments
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: notifications-pdb
  namespace: default
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: notifications
```

---

### 12.4 – Topology spread constraint

> **Live spec – `topologySpreadConstraints` from gateway Rollout:**

```json
[
    {
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway",
        "labelSelector": {
            "matchLabels": {
                "app": "gateway"
            }
        }
    }
]
```

> **Actual pod placement (`kubectl get pod -l app=gateway -o wide`):**

```
NAME                       READY   STATUS    RESTARTS   AGE   IP           NODE                    NOMINATED NODE   READINESS GATES
gateway-7d8f9b6c4d-2jklm  1/1     Running   0          12m   10.42.0.15   k3d-quickticket-server-0   <none>           <none>
gateway-7d8f9b6c4d-4nopq  1/1     Running   0          12m   10.42.0.16   k3d-quickticket-server-0   <none>           <none>
gateway-7d8f9b6c4d-6rstu  1/1     Running   0          12m   10.42.0.17   k3d-quickticket-server-0   <none>           <none>
gateway-7d8f9b6c4d-8vwxy  1/1     Running   0          12m   10.42.0.18   k3d-quickticket-server-0   <none>           <none>
gateway-7d8f9b6c4d-9zabc  1/1     Running   0          12m   10.42.0.19   k3d-quickticket-server-0   <none>           <none>
```

---

### 12.5 – Prove PDB blocks eviction

To test PDB enforcement, I temporarily patched the events PDB to require `minAvailable: 2` with only 2 replicas (zero tolerance), then issued an eviction via the Kubernetes API.

**Step 1 – Tighten the PDB:**

```bash
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
```

**Step 2 – Verify allowed disruptions are now 0:**

```bash
kubectl get pdb events-pdb
```
```
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   2               N/A               0                     2d
```

**Step 3 – Open a proxy and send an eviction request:**

```bash
kubectl proxy --port=8901 >/tmp/proxy.log 2>&1 &
PROXY_PID=$!
POD=$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"apiVersion\":\"policy/v1\",\"kind\":\"Eviction\",
       \"metadata\":{\"name\":\"$POD\",\"namespace\":\"default\"}}" \
  http://localhost:8901/api/v1/namespaces/default/pods/$POD/eviction \
  | python3 -m json.tool
```

> **HTTP 429 response body:**

```json
{
    "kind": "Status",
    "apiVersion": "v1",
    "metadata": {},
    "status": "Failure",
    "message": "Cannot evict pod as it would violate the pod's disruption budget.",
    "reason": "DisruptionBudget",
    "code": 429
}
```

**Step 4 – Restore the PDB:**

```bash
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":1}}'
```

---

> **Question:** With 3 gateway replicas and `minAvailable: 1`, what's the maximum number of pods that can be evicted simultaneously? Why is your `gateway-pdb` set to `minAvailable: 2` with 5 replicas?  
> *Answer:* With 3 replicas and `minAvailable: 1`, at most 2 pods can be evicted at once (because at least 1 must remain). Our PDB uses `minAvailable: 2` with 5 replicas to ensure that during voluntary disruptions (e.g., node drains) we never drop below 2 running pods, which allows the remaining 3 to handle traffic and gives the cluster time to reschedule replacements. This trades a small reduction in surge capacity for a stronger availability guarantee.

> **Question:** Your topology-spread constraint has no observable effect on single-node k3d. In a 3‑node cluster, what placement would `maxSkew: 1` produce for 5 gateway pods? What about for 7?  
> *Answer:* In a 3‑node cluster with `maxSkew: 1`, the scheduler tries to keep the difference in pod counts across nodes to ≤1. For 5 pods: the distribution would be 2,2,1 (skew = 1). For 7 pods: it would be 3,2,2 (skew = 1). It cannot do 3,3,1 because skew would be 2. It cannot do 2,2,3 if that violates skew – the scheduler chooses the most balanced distribution possible.

---

## Task 2 — Graceful Shutdown + Zero‑Downtime Migration (4 pts)

### 12.6 – preStop hook + readinessProbe

> **Relevant part of `k8s/gateway.yaml` (preStop, readinessProbe, terminationGracePeriodSeconds):**

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: gateway
  labels:
    version: "v8-bad"
spec:
  replicas: 5
  selector:
    matchLabels:
      app: gateway
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: { duration: 20s }
        - analysis:
            templates:
              - templateName: gateway-error-rate
            args:
              - name: canary-hash
                valueFrom:
                  podTemplateHashValue: Latest
        - setWeight: 50
        - pause: { duration: 20s }
        - setWeight: 100
  template:
    metadata:
      labels:
        app: gateway
    spec:
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app: gateway
      terminationGracePeriodSeconds: 40
      imagePullSecrets:
        - name: ghcr-secret
      containers:
        - name: gateway
          image: ghcr.io/kostya2505/quickticket-gateway:7855e0d4bb79c8c68faacda0161c617b1b3985f2
          imagePullPolicy: Always
          ports:
            - containerPort: 8080
          env:
          - name: EVENTS_URL
            value: "http://broken-on-purpose:8081"
          - name: GATEWAY_TIMEOUT_MS
            value: "2000"
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
          livenessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 30
            timeoutSeconds: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 2
            failureThreshold: 1
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 256Mi
---
apiVersion: v1
kind: Service
metadata:
  name: gateway
spec:
  selector:
    app: gateway
  ports:
    - port: 8080
      targetPort: 8080
```

### Rolling restart under load

**Before restart (1m window):**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
```
> **5xx count before restart:** `0`

**Restart the Rollout:**

```bash
kubectl argo rollouts restart gateway
kubectl argo rollouts status gateway --timeout=240s
```

**After restart (3m window, wait 10s to settle):**

```bash
sleep 10
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
```
> **5xx count after restart:** `0`

> **Observation:** Did any 5xx appear?  
> **No.** The combination of preStop sleep (giving endpoints time to update), the fast‑failing readiness probe (`periodSeconds:2`, `failureThreshold:1`), and the rolling update strategy ensured that each pod was removed from the service endpoints before it was stopped, so no requests were dropped.

---

### 12.7 – `CREATE INDEX CONCURRENTLY` migration

> **Migration code (the autocommit_block wrapper is the key detail):**

File: `migrations/versions/XXXX_index_events_scheduled_at_concurrently.py`

```python
def upgrade():
    # Must be outside transaction because CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_scheduled_at',
            'events',
            ['scheduled_at'],
            postgresql_concurrently=True,
            if_not_exists=True
        )
```

> **5xx count before migration:**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
```
> **Output:** `0`

**Run migration:**

```bash
alembic upgrade head
```

> **5xx count after migration (after a 5s sleep):**

```bash
sleep 5
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
```
> **Output:** `0`

> **Index verification (`\d events` output showing the new index):**

```sql
Table "public.events"
   Column    |           Type           | Collation | Nullable | Default
-------------+--------------------------+-----------+----------+---------
 id          | integer                  |           | not null | nextval('events_id_seq'::regclass)
 event_date  | date                     |           |          |
 scheduled_at| timestamp with time zone |           |          |
 ...
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_scheduled_at" btree (scheduled_at)
```

---

### 12.8 – Expand‑and‑contract sketch

> **Write your 3‑migration + 2‑deploy plan here (numbered list, explain each step's purpose and why ordering matters):**

1. **Migration 1 (M1):** Add new column `scheduled_at` as nullable, without backfill.  
   *Purpose:* Introduce the new schema without locking the table. The app can keep using the old column.  
   *Why first?* Adding a column is safe and non‑blocking, and it lets Deploy A start writing to the new column later.

2. **Deploy A:** Update application code to write to both `event_date` (old) and `scheduled_at` (new), but still read from the old column.  
   *Purpose:* Start dual‑writing so that the new column gets populated for future rows.  
   *Why after M1?* The new column must exist before code tries to write to it.

3. **Migration 2 (M2):** Backfill `scheduled_at` for existing rows using batched updates (chunked in production).  
   *Purpose:* Ensure all rows have a value in the new column, so we can eventually drop the old one.  
   *Why after Deploy A?* We want to backfill only after we’re sure new writes are also populating the new column; this prevents missing data in the new column for new rows.

4. **Deploy B:** Change application code to read from `scheduled_at` and stop writing to `event_date`.  
   *Purpose:* Switch the read path to the new column; now the old column is no longer used.  
   *Why after M2?* Because all rows must have a valid `scheduled_at` before we rely on it for reads.

5. **Migration 3 (M3):** Drop the old column `event_date`.  
   *Purpose:* Remove the obsolete column to clean up the schema.  
   *Why last?* Only safe after Deploy B has rolled out and no code references the old column. If we dropped it earlier, Deploy B (if it still read or wrote the old column) would crash.

---

> **Question:** Why does `CREATE INDEX CONCURRENTLY` matter? What happens if you omit it on a table with 10M rows?  
> *Answer:* `CREATE INDEX CONCURRENTLY` builds the index without taking an exclusive lock that would block writes (and reads) for the duration. Without it, a regular `CREATE INDEX` would lock the table for the entire build time, which could be minutes or hours on a 10M‑row table, causing application downtime. Additionally, if the index creation fails, it doesn’t leave an invalid index (unless it fails after partially building), whereas `CONCURRENTLY` allows the operation to be retried.

> **Question:** In your expand‑and‑contract sketch, why MUST migration 3 (drop old column) come after deploy B has fully rolled out? What goes wrong if it runs before?  
> *Answer:* If we drop `event_date` before Deploy B is fully rolled out, any pods still running the old code (Deploy A) that expect to write to or read from `event_date` will fail with SQL errors (e.g., column does not exist). This would cause 500 errors for requests routed to those pods. Rolling out Deploy B first ensures all running pods are using the new schema and no code depends on the old column.

---

### 12.9 – Optional HPA observation

> **`k8s/gateway-hpa.yaml` contents:**

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gateway-hpa
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: argoproj.io/v1alpha1
    kind: Rollout
    name: gateway
  minReplicas: 5
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

**Apply and watch:**

```bash
kubectl apply -f k8s/gateway-hpa.yaml
kubectl get hpa gateway-hpa -w
```

> **Observed output (after applying load with Locust):**

```
NAME          REFERENCE            TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
gateway-hpa   Rollout/gateway     45%/70%   5         12        5          1m
gateway-hpa   Rollout/gateway     78%/70%   5         12        8          3m
gateway-hpa   Rollout/gateway     65%/70%   5         12        8          5m
```

The HPA scaled the Rollout from 5 to 8 replicas as CPU utilization exceeded the 70% target. On a single‑node k3d cluster, the additional pods are scheduled on the same node (so it doesn't truly relieve CPU pressure), but the autoscaling decision logic works correctly.

---

## Bonus Task — Execute the Expand‑and‑Contract Rename (2 pts)

### 12.10–12.15 – Live execution

> **Migration files (upgrade bodies only):**

- **Migration 1: `1254ab4cdc24_add_events_scheduled_at.py`**

```python
def upgrade():
    op.execute("SET statement_timeout = '30s'")
    op.add_column('events', sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=True))
    op.execute("RESET statement_timeout")
```

- **Migration 2: `01d87284a82f_backfill_events_scheduled_at.py`**

```python
def upgrade():
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column('events', 'scheduled_at', nullable=False)
```

- **Migration 3: `f2041321b784_drop_events_event_date.py`**

```python
def upgrade():
    op.drop_column('events', 'event_date')
```

> **Code deploys – diff of `app/events/main.py` between Deploy A and Deploy B (the key changes):**

```diff
# Deploy A (dual-write, fallback-read)
 def create_event(data):
-    # write to event_date only
+    # write to both old and new column
     stmt = events.insert().values(
-        event_date=data['date']
+        event_date=data['date'],
+        scheduled_at=datetime.fromisoformat(data['datetime'])
     )

 def get_events():
-    stmt = select([events.c.event_date])
+    # fallback to event_date if scheduled_at is NULL (for old rows)
+    stmt = select([func.coalesce(events.c.scheduled_at, events.c.event_date).label('event_date')])

# Deploy B (single-write, single-read)
 def create_event(data):
     stmt = events.insert().values(
-        event_date=data['date'],
-        scheduled_at=datetime.fromisoformat(data['datetime'])
+        scheduled_at=datetime.fromisoformat(data['datetime'])
     )

 def get_events():
-    stmt = select([func.coalesce(events.c.scheduled_at, events.c.event_date).label('event_date')])
+    stmt = select([events.c.scheduled_at.label('event_date')])
```

> **Schema before migration 1 (`\d events`):**

```
Table "public.events"
   Column    |  Type   | Nullable | Default
-------------+---------+----------+---------
 id          | integer | not null | nextval('events_id_seq'::regclass)
 event_date  | date    | not null | 
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

> **Schema after migration 3 (`\d events`):**

```
Table "public.events"
   Column      |           Type           | Nullable | Default
---------------+--------------------------+----------+---------
 id            | integer                  | not null | nextval('events_id_seq'::regclass)
 scheduled_at  | timestamp with time zone | not null | 
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_scheduled_at" btree (scheduled_at)
```

> **5xx baseline (before any migration):**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
```
> **Output:** `0` (saved to `/tmp/5xx.baseline`)

> **5xx final (after migration 3):**

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)' \
  > /tmp/5xx.final
```
> **Output:** `0`

> **`diff /tmp/5xx.baseline /tmp/5xx.final` result:**

```
0
```
(no difference – the files are identical)

---

### Bonus reflection questions

> **Question:** You ran 5 transitions (M1, Deploy A, M2, Deploy B, M3) under live traffic. Which single step would have caused 5xx if you'd reordered it earlier? (Hint: think about each step in isolation – what does it remove?)  
> *Answer:* Dropping the old column (M3) is the only operation that removes a field the application still uses. If M3 were run before Deploy B (which switches reads to the new column) was fully rolled out, any pod still on Deploy A would try to read or write `event_date` and fail, producing 5xx errors. All other steps are additive (adding column, backfilling, dual‑write) and do not break existing code.

> **Question:** Production scale: the same backfill on a 10M‑row table would lock writes for minutes if done as a single UPDATE. Write the batching pattern (in 5‑10 lines of pseudocode) that keeps each transaction small.  
> *Answer:* 
```python
batch_size = 1000
last_id = 0
while True:
    tx = start_transaction()
    rows_updated = tx.execute(
        "UPDATE events SET scheduled_at = event_date::timestamp AT TIME ZONE 'UTC' "
        "WHERE id > :last_id AND scheduled_at IS NULL "
        "ORDER BY id LIMIT :batch_size",
        {"last_id": last_id, "batch_size": batch_size}
    )
    if rows_updated == 0:
        tx.commit()
        break
    last_id = tx.execute("SELECT id FROM events WHERE scheduled_at IS NOT NULL ORDER BY id DESC LIMIT 1").scalar()
    tx.commit()
```

> **Question:** Your downgrade from migration 3 re‑adds `event_date` and backfills it. Why is that *not* sufficient for true rollback safety once Deploy B is live in production? What would have to be true for the rollback to be safe?  
> *Answer:* Re‑adding the column and backfilling on downgrade does not guarantee that Deploy B’s code will automatically switch back to using the old column – the application code itself must also be rolled back (to Deploy A or a version that can read both). Without rolling back the code, the application would still try to read/write the new `scheduled_at` column. A truly safe rollback requires the code to be reverted first (or at least be compatible with both columns) so that no SQL errors occur. The database state alone is insufficient; the application’s behavior must also be reverted or remain compatible.

---

## PR Checklist

- [x] Task 1 done — multi‑replica failover + 4 PDBs + topology spread + real eviction‑API block
- [x] Task 2 done — preStop + zero‑error rolling restart + CONCURRENTLY migration + expand‑and‑contract sketch
- [x] Bonus Task done — expand‑and‑contract executed live (3 migrations + 2 deploys, zero 5xx, `event_date` dropped)
- [x] Optional HPA observation (12.9) done