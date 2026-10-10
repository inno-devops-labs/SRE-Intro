# Lab 12 Report

## Task 1 — Pod Disruption Budgets & Anti-Affinity

### 12.1: Scale Backend Services to 2 Replicas

I scaled events, payments, and notifications from 1 to 2 replicas:

```yaml
# k8s/events.yaml, k8s/payments.yaml, k8s/notifications.yaml
spec:
  replicas: 2
```

```bash
user@MacBook-Air sre-intro % kubectl apply -f k8s/events.yaml -f k8s/payments.yaml -f k8s/notifications.yaml
deployment.apps/events configured
deployment.apps/payments configured
deployment.apps/notifications configured
```

### 12.2: Pod Disruption Budgets

I created `k8s/pdb.yaml` with PDBs for all three backend services, each with `minAvailable: 1`:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: events-pdb
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
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: notifications
```

```bash
user@MacBook-Air sre-intro % kubectl apply -f k8s/pdb.yaml
poddisruptionbudget.policy/events-pdb created
poddisruptionbudget.policy/payments-pdb created
poddisruptionbudget.policy/notifications-pdb created
```

Verified PDBs:

```bash
user@MacBook-Air sre-intro % kubectl get pdb -A
NAMESPACE   NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
default     events-pdb          1               N/A               1                     6m57s
default     notifications-pdb   1               N/A               1                     6m57s
default     payments-pdb        1               N/A               1                     6m57s
```

### 12.3: Gateway Topology Spread Constraints & preStop

I added `topologySpreadConstraints` and a `preStop` sleep hook to the gateway Rollout:

```yaml
spec:
  template:
    spec:
      topologySpreadConstraints:
      - maxSkew: 1
        topologyKey: kubernetes.io/hostname
        whenUnsatisfiable: DoNotSchedule
        labelSelector:
          matchLabels:
            app: gateway
      containers:
      - name: gateway
        lifecycle:
          preStop:
            exec:
              command: ["sh", "-c", "sleep 3"]
```

Verified on the running rollout:

```bash
user@MacBook-Air sre-intro % kubectl get rollout gateway -o jsonpath='{.spec.template.spec}' | python3 -m json.tool | grep -A5 "topology\|preStop\|terminationGrace"
                "preStop": {
                    "exec": {
                        "command": [
                            "sh",
                            "-c",
                            "sleep 3"
                        ]
                    }
                },
    "terminationGracePeriodSeconds": 30,
    "topologySpreadConstraints": [
        {
            "labelSelector": {
                "matchLabels": {
                    "app": "gateway"
                }
            },
            "maxSkew": 1,
            "topologyKey": "kubernetes.io/hostname",
            "whenUnsatisfiable": "DoNotSchedule"
        }
    ]
```

### PDB Test — Delete Pod Under Load

I deleted an events pod while mixedload was generating traffic. The PDB allowed exactly 1 disruption:

```bash
user@MacBook-Air sre-intro % kubectl get pdb events-pdb
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   1               N/A               1                     6m57s

user@MacBook-Air sre-intro % kubectl delete pod events-74d5bcc797-gtn8l
pod "events-74d5bcc797-gtn8l" deleted

user@MacBook-Air sre-intro % kubectl get pdb events-pdb
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   1               N/A               0                     7m
```

After deletion, ALLOWED DISRUPTIONS dropped to 0 (only 1 pod remaining = minAvailable). The new pod was created automatically:

```bash
user@MacBook-Air sre-intro % kubectl get pods -l app=events
NAME                      READY   STATUS    RESTARTS   AGE
events-74d5bcc797-8jslw   0/1     Running   0          2s
events-74d5bcc797-tt5qj   1/1     Running   0          7m6s
```

---

## Task 2 — Graceful Shutdown & Rolling Restarts

### 12.4: terminationGracePeriodSeconds

I added `terminationGracePeriodSeconds: 30` to all service deployments and the gateway rollout:

```yaml
# k8s/events.yaml, k8s/payments.yaml, k8s/notifications.yaml, k8s/gateway.yaml
spec:
  template:
    spec:
      terminationGracePeriodSeconds: 30
```

### Rolling Restart Test — Events

I restarted the events deployment while mixedload generated traffic (300ms sleep between requests):

```bash
user@MacBook-Air sre-intro % kubectl rollout restart deployment/events
deployment.apps/events restarted
user@MacBook-Air sre-intro % kubectl rollout status deployment/events --timeout=120s
deployment "events" successfully rolled out
```

Checked gateway status codes during restart — **zero 5xx**:

```bash
user@MacBook-Air sre-intro % GW_POD=$(kubectl get pods -l app=gateway -o jsonpath='{.items[0].metadata.name}')
user@MacBook-Air sre-intro % kubectl logs "$GW_POD" 2>&1 | grep -Eo 'HTTP/1.1" [0-9]+' | awk '{print $2}' | sort | uniq -c | sort -rn
  601 200
  408 409
```

601 × 200 OK, 408 × 409 (expected — not enough tickets). Zero 5xx.

### Rolling Restart Test — Payments

I also restarted payments under the same traffic:

```bash
user@MacBook-Air sre-intro % kubectl rollout restart deployment/payments
deployment.apps/payments restarted
user@MacBook-Air sre-intro % kubectl rollout status deployment/payments --timeout=120s
deployment "payments" successfully rolled out
```

```bash
user@MacBook-Air sre-intro % kubectl logs "$GW_POD" --tail=200 2>&1 | grep -Eo 'HTTP/1.1" [0-9]+' | awk '{print $2}' | sort | uniq -c | sort -rn
  58 200
  35 409
```

Zero 5xx. The preStop hook (sleep 3) gives the pod time to drain in-flight requests before termination, and the topology spread constraints ensure pods are distributed across nodes (on multi-node clusters).

---

## Bonus Task — Online Schema Migration

### 12.5: Alembic Migration with `CREATE INDEX CONCURRENTLY`

I created a new Alembic migration `004_add_orders_event_id_index` that uses `CREATE INDEX CONCURRENTLY` — this allows the index to be built without locking the table:

```python
# migrations/versions/004_add_orders_event_id_index.py
import psycopg2
from alembic import op

def upgrade() -> None:
    url = op.get_bind().engine.url
    conn = psycopg2.connect(str(url))
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("CREATE INDEX CONCURRENTLY idx_orders_event_id ON orders (event_id)")
        cur.close()
    finally:
        conn.close()
```

Key design decisions:
- Opens a **separate raw psycopg2 connection** with `autocommit=True` because `CREATE INDEX CONCURRENTLY` cannot run inside a transaction block
- Alembic wraps migrations in a transaction by default, so we bypass SQLAlchemy's connection entirely

### Migration Execution

```bash
user@MacBook-Air sre-intro % source .venv/bin/activate
user@MacBook-Air sre-intro % alembic stamp 729c84a02c1a
INFO  [alembic.runtime.migration] Running stamp_revision 004_add_orders_event_id_index -> 729c84a02c1a

user@MacBook-Air sre-intro % alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 729c84a02c1a -> 004_add_orders_event_id_index, add orders event_id index concurrently
```

### Verification

```bash
user@MacBook-Air sre-intro % python3 -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5432, dbname='quickticket', user='quickticket', password='quickticket')
cur = conn.cursor()
cur.execute('SELECT * FROM alembic_version')
print('alembic_version:', cur.fetchall())
cur.execute(\"SELECT indexname FROM pg_indexes WHERE tablename = 'orders'\")
print('orders indexes:', cur.fetchall())
conn.close()
"
alembic_version: [('004_add_orders_event_id_index',)]
orders indexes: [('orders_pkey',), ('idx_orders_event_id',)]
```

The index `idx_orders_event_id` now exists on `orders(event_id)`, and the migration ran without locking the table — live traffic continued serving throughout.

### Why `CREATE INDEX CONCURRENTLY` Matters

A regular `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock on the table, blocking all reads and writes for the duration of the index build. On a large orders table under live traffic, this would cause timeouts and 5xx errors. `CONCURRENTLY` builds the index in the background while allowing normal reads and writes, at the cost of slightly more disk I/O and time. It's the standard approach for zero-downtime schema changes on production databases.
