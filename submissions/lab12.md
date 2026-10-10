# Lab 12

## Task 1 — Multi-Replica Failover + PDBs

### kubectl get deploy,rollout showing all services at their target replica counts.
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl get deploy events payments notifications
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
events          2/2     2            2           24d
payments        2/2     2            2           24d
notifications   2/2     2            2           31h
(.venv)
```

### The before/after 5xx count from Prometheus around the pod-kill test (should both be 0).
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(status)+(rate(gateway_requests_total%5B3m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{"status":"503"},"value":[1784156771.709,"0.022742331574638838"]},{"metric":{"status":"200"},"value":[1784156771.709,"7.170608613210985"]},{"metric":{"status":"502"},"value":[1784156771.709,"0.011453133361628991"]},{"metric":{"status":"500"},"value":[1784156771.709,"0"]},{"metric":{"status":"409"},"value":[1784156771.709,"5.783747308896672"]}]}}(.venv)


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl delete pod $(kubectl get pod -l app=gateway -o jsonpath='{.items[0].metadata.name}') --wait=false
kubectl delete pod $(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}') --wait=false
pod "gateway-7545b4b9b8-5922t" deleted from default namespace
pod "events-5ffcd78d47-6khzb" deleted from default namespace
(.venv)


axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784156824.259,"0"]}]}}(.venv)

```


### kubectl get pdb output.
```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     0s
gateway-pdb         2               N/A               3                     0s
notifications-pdb   N/A             1                 1                     0s
payments-pdb        1               N/A               1                     0s
(.venv)
```


### kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' output showing the constraint is in the live spec, plus kubectl get pod -l app=gateway -o wide showing the actual placement.
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python -m json.tool
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
(.venv)
```


### The HTTP 429 JSON body from the tightened-PDB eviction test (proves PDB enforcement).
```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ POD=$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')
curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"apiVersion\":\"policy/v1\",\"kind\":\"Eviction\",
       \"metadata\":{\"name\":\"$POD\",\"namespace\":\"default\"}}" \
  http://localhost:8901/api/v1/namespaces/default/pods/$POD/eviction \
  | python -m json.tool
{
    "kind": "Status",
    "apiVersion": "v1",
    "metadata": {},
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
(.venv)
```

### Answer: "With 3 gateway replicas and minAvailable: 1, what's the maximum number of pods that can be evicted simultaneously? Why is your gateway-pdb set to minAvailable: 2 with 5 replicas?"

**With 3 gateway replicas and minAvailable: 1:**
- Maximum pods that can be evicted simultaneously = **2**
- Because `minAvailable: 1` means at least 1 pod must remain running
- `3 - 1 = 2` pods can be evicted at once

**Why gateway-pdb is set to minAvailable: 2 with 5 replicas:**
- With 5 replicas and `minAvailable: 2`, maximum evictions = `5 - 2 = 3`
- This allows **3 pods** to be evicted simultaneously during node maintenance
- Why not `minAvailable: 4`? Because that would only allow 1 eviction, blocking node drains
- Why `minAvailable: 2`? It balances resilience (enough capacity for ~half normal RPS) with operational flexibility (can still drain nodes efficiently)
- Gateway is the critical path — we need some redundancy, but we also need to be able to actually replace nodes



### Answer: "Your topology-spread constraint has no observable effect on single-node k3d. In a 3-node cluster, what placement would maxSkew: 1 produce for 5 gateway pods? What about for 7?"

**With `maxSkew: 1` on a 3-node cluster:**

**For 5 gateway pods:**
- The scheduler spreads pods as evenly as possible
- Best possible placement: **2 pods on one node, 2 on another, 1 on the third**
- The difference between the most-loaded and least-loaded node is at most 1 (skew = 1)

**For 7 gateway pods:**
- Best possible placement: **3 pods on two nodes, 2 pods on one node** (3, 3, 2)
- Or: **3, 2, 2** — still maintaining skew ≤ 1

**Why this matters:**
- On a single-node cluster (k3d), all pods land on the same node — you can't see the effect
- In production with 3+ nodes, this constraint prevents all pods from piling onto one node
- It ensures that if one node fails, the remaining pods are spread across other nodes


## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### The preStop / readinessProbe block as it appears in your k8s/gateway.yaml.

```
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

### 5xx count before / after the rolling restart (both should be 0).

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
{"status":"success","data":{"resultType":"vector","result":[]}}(.venv)

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl argo rollouts restart gateway
kubectl argo rollouts status gateway --timeout=240s
rollout 'gateway' restarts in 0s
Progressing - waiting for rollout spec update to be observed
Progressing - rollout is restarting
Healthy
(.venv)

axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ sleep 10
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784157984.091,"0"]}]}}(.venv)
```

### Your migration code (the autocommit_block wrapper is the key detail).

```
"""index events.event_date concurrently

Revision ID: b58e1cc349da
Revises: 
Create Date: 2026-07-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b58e1cc349da'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            postgresql_concurrently=True,
            if_not_exists=True
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events', if_exists=True)

```

### 5xx count before / after the migration (both should be 0).

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
{"status":"success","data":{"resultType":"vector","result":[]}}(.venv)
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ time alembic upgrade head
Handling connection for 5432
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> b58e1cc349da, index events.event_date concurrently

real    0m1.991s
user    0m0.000s
sys     0m0.015s
(.venv)
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events' | grep idx_events
    "idx_events_event_date" btree (event_date)
(.venv)
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
{"status":"success","data":{"resultType":"vector","result":[]}}(.venv)
```



### \d events output showing the new idx_events_event_date index.

```
axmed@honorfifi MINGW64 ~/SRE-Intro (feature/lab12)
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events' | grep idx_events
    "idx_events_event_date" btree (event_date)
(.venv)
```

### The 3-migration + 2-deploy expand-and-contract sketch from 12.8 (write it as a numbered list, no code required).

**Goal:** Rename `event_date` to `scheduled_at` with zero downtime.

**Step 1 — Migration 1: Add new column (expand)**
- Add column `scheduled_at` as nullable (`NULL`)
- Why nullable? Adding a NOT NULL column would rewrite the table and take an ACCESS EXCLUSIVE lock
- Adding a nullable column is a metadata-only change — instant and safe under load

**Step 2 — Code Deploy A: Dual-write, fallback-read**
- Reads: Use `COALESCE(scheduled_at, event_date) AS event_date` so existing rows still show data
- Writes: Write to BOTH `event_date` AND `scheduled_at`
- Why? New code must work with old schema; old code must work with new schema
- Deploy this code before any data migration

**Step 3 — Migration 2: Backfill**
- Update all rows: `SET scheduled_at = event_date WHERE scheduled_at IS NULL`
- After backfill, make `scheduled_at` NOT NULL
- Why safe? Code Deploy A handles NULL via COALESCE, so reads continue to work
- For production scale: batch the UPDATE in chunks of 10k rows with sleeps between batches

**Step 4 — Code Deploy B: Switch to new column only**
- Reads: Use `scheduled_at` only (remove COALESCE)
- Writes: Write only to `scheduled_at`
- After this deploy, `event_date` is no longer used by any running code

**Step 5 — Migration 3: Drop old column (contract)**
- Drop column `event_date`
- Why safe NOW (but not earlier)? Code Deploy B is fully rolled out — no running code reads or writes `event_date`
- If this ran before Code Deploy B, any remaining pods on Deploy A would 500 because `COALESCE(scheduled_at, event_date)` would reference a missing column

**Why this order matters:**
- The key insight: **at every intermediate point, both old and new code must work**
- Migration 1: Adds new column without breaking old code
- Deploy A: Makes both columns available to both code versions
- Migration 2: Populates new column while reads still work via COALESCE
- Deploy B: Switches to new column only after data is ready
- Migration 3: Drops old column only after no code uses it

**What goes wrong if you reorder:**
- If Migration 3 (drop old column) runs before Deploy B: → 500 errors on every request from Deploy A pods
- If Deploy B runs before Migration 2 (backfill): → NULL values in `scheduled_at` appear to users
- The order is carefully designed to keep the system working at every step






### Answer: "Why does CREATE INDEX CONCURRENTLY matter? What happens if you omit it on a table with 10M rows?"

**Without CONCURRENTLY** → `ACCESS EXCLUSIVE` lock → **blocks ALL reads and writes** for minutes/hours on a 10M-row table → **downtime**.

**With CONCURRENTLY** → `SHARE UPDATE EXCLUSIVE` lock → **allows reads and writes** to continue → **zero downtime**.

**What happens if you omit it:** Every query (SELECT, INSERT, UPDATE, DELETE) waits behind the lock. Connection pools exhaust. Users see errors. That's a self-inflicted outage.



### Answer (from 12.8): "In your expand-and-contract sketch, why MUST migration 3 (drop old column) come after deploy B has fully rolled out? What goes wrong if it runs before?"

**Migration 3 drops the old column `event_date`.**

If it runs **before** Deploy B is fully rolled out, any pods still running **Deploy A** would crash on every request, because Deploy A uses `COALESCE(scheduled_at, event_date)`. The query tries to read `event_date`, but the column no longer exists → **500 Internal Server Error**.

Deploy B removes the `COALESCE` and reads only `scheduled_at`, so it survives the column drop.

**Therefore:** Drop the old column only after **every single pod** has switched to Deploy B. Otherwise — errors for users.
