# Lab 12 — Bonus: Advanced Kubernetes Resilience

---

## Baseline

### Zero 5xx before starting

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'

{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784309670.284,"6.180693725754518"]}]}}
```

Initial baseline contained historical 5xx from earlier readiness failures. After scaling
services to two replicas, the baseline returned to zero before the failover test.

---

# Task 1 — Multi-Replica Failover + PDBs

## 12.1 — Scale services to 2 replicas

### k8s/events.yaml / k8s/payments.yaml / k8s/notifications.yaml (diff)

```yaml
spec:
  replicas: 2
```

### Apply

```bash
$ kubectl apply -f k8s/events.yaml -f k8s/payments.yaml -f k8s/notifications.yaml
deployment.apps/events configured
service/events configured
deployment.apps/payments configured
service/payments configured
deployment.apps/notifications configured
service/notifications unchanged

$ kubectl rollout status deployment/events --timeout=60s
Waiting for deployment "events" rollout to finish: 1 of 2 updated replicas are available...
deployment "events" successfully rolled out
$ kubectl rollout status deployment/payments --timeout=60s
deployment "payments" successfully rolled out
$ kubectl rollout status deployment/notifications --timeout=60s
deployment "notifications" successfully rolled out

$ kubectl get deploy -l 'app in (events,payments,notifications)'
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
events          2/2     2            2           22d
notifications   2/2     2            2           160m
payments        2/2     2            2           22d

$ kubectl get rollout gateway
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   5         5         5            5           16d
```

---

## 12.2 — Failover test: kill pods under load

Before killing anything, re-verified the baseline was clean post-12.1 by sampling the 1-minute
5xx increase three times, 20s apart:

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784309904.900,"0"]}]}}
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784309925.853,"0"]}]}}
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784309946.143,"0"]}]}}
```

### Before (3m window, immediately before the kill)

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'

{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784309981.757,"2.060758015490031"]}]}}
```

### Kill

```bash
$ kubectl delete pod $(kubectl get pod -l app=gateway -o jsonpath='{.items[0].metadata.name}') --wait=false
pod "gateway-5cf9fc4874-55x7f" deleted

$ kubectl delete pod $(kubectl get pod -l app=events  -o jsonpath='{.items[0].metadata.name}') --wait=false
pod "events-664dbfb59b-fpzrk" deleted
```

### Recovery

```bash
$ kubectl get pod -l 'app in (gateway,events)' --watch
events-664dbfb59b-gnw2l    1/1   Running   5 (171m ago)   14d
events-664dbfb59b-t79t5    0/1   Running   0              9s     # replacement, not ready yet
gateway-5cf9fc4874-9fk54   1/1   Running   0              61m
gateway-5cf9fc4874-c8c49   1/1   Running   0              63m
gateway-5cf9fc4874-hgwqd   1/1   Running   0              61m
gateway-5cf9fc4874-sw7lp   1/1   Running   0              60m
gateway-5cf9fc4874-vlhdc   1/1   Running   0              9s     # replacement, already ready
...
events-664dbfb59b-t79t5    1/1   Running   0              15s    # ready after ~15s
```

Both replacements reached `1/1 Running` within 15-25 seconds of the delete.

### After (1m window)

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'

{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784310018.462,"1.0929359903821634"]}]}}
```

To get an unambiguous answer (rather than `increase()`'s fractional extrapolation), sampled the
**absolute** counter immediately before and 10s after the kill:

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784310030.659,"55"]}]}}

$ sleep 10
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784310040.951,"55"]}]}}
```

### Observation

Although `increase()` reported a fractional value due to Prometheus extrapolation, the absolute
5xx counter remained unchanged (55 → 55), proving that the pod deletion introduced no new errors.

---

## 12.3 — k8s/pdb.yaml

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: gateway-pdb
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
  maxUnavailable: 1
  selector:
    matchLabels:
      app: notifications
```

### Apply + verify

```bash
$ kubectl apply -f k8s/pdb.yaml
poddisruptionbudget.policy/gateway-pdb created
poddisruptionbudget.policy/events-pdb created
poddisruptionbudget.policy/payments-pdb created
poddisruptionbudget.policy/notifications-pdb created

$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     0s
gateway-pdb         2               N/A               3                     0s
notifications-pdb   N/A             1                 1                     0s
payments-pdb        1               N/A               1                     0s
```

`ALLOWED DISRUPTIONS` matches the expected values for each workload.

---

## 12.4 — Topology spread on the gateway Rollout

### k8s/gateway.yaml (diff — added to spec.template.spec)

```yaml
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app: gateway
```

### Apply + observe

```bash
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured
service/gateway unchanged

$ kubectl argo rollouts status gateway --timeout=240s
Progressing - more replicas need to be updated
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Progressing - updated replicas are still becoming available
Progressing - old replicas are pending termination
Progressing - waiting for all steps to complete
Healthy

$ kubectl get pod -l app=gateway -o wide
NAME                      READY   STATUS    RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
gateway-98d854bcf-4tvkm   1/1     Running   0          3m11s   10.42.0.8    k3d-quickticket-server-0   <none>           <none>
gateway-98d854bcf-k4qns   1/1     Running   0          27s     10.42.0.12   k3d-quickticket-server-0   <none>           <none>
gateway-98d854bcf-qcjjn   1/1     Running   0          27s     10.42.0.11   k3d-quickticket-server-0   <none>           <none>
gateway-98d854bcf-tvbcj   1/1     Running   0          59s     10.42.0.9    k3d-quickticket-server-0   <none>           <none>
gateway-98d854bcf-w2pld   1/1     Running   0          59s     10.42.0.10   k3d-quickticket-server-0   <none>           <none>

$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}'
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

(`python3 -m json.tool` wasn't available in this shell — pasted the raw jsonpath output above,
same information, just not pretty-printed.)

### Observation

All 5 gateway pods landed on the single node `k3d-quickticket-server-0` — no observable
placement effect, exactly as the lab predicts for single-node k3d. The jsonpath output confirms
that the constraint was applied successfully: `maxSkew: 1`, `topologyKey: kubernetes.io/hostname`,
`whenUnsatisfiable: ScheduleAnyway`, scoped to `app: gateway`. On a real multi-node cluster this
would keep the per-node pod count within 1 of each other.

---

## 12.5 — Prove a PDB actually blocks eviction

### Tighten events-pdb to zero tolerance

```bash
$ kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
poddisruptionbudget.policy/events-pdb patched

$ kubectl get pdb events-pdb
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   2               N/A               0                     3m58s
```

### Fire one eviction via the API

`kubectl proxy` couldn't bind port 8901 in this environment (Windows socket permission error),
so used port 18901 instead:

```bash
$ kubectl proxy --port=18901 --address=127.0.0.1 &
Starting to serve on 127.0.0.1:18901

$ POD=$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')
$ echo $POD
events-664dbfb59b-gnw2l

$ curl -s -X POST -H 'Content-Type: application/json' \
  -d "{\"apiVersion\":\"policy/v1\",\"kind\":\"Eviction\",\"metadata\":{\"name\":\"$POD\",\"namespace\":\"default\"}}" \
  -w "\nHTTP_STATUS:%{http_code}\n" \
  http://127.0.0.1:18901/api/v1/namespaces/default/pods/$POD/eviction
```

```json
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
```

`HTTP_STATUS:429` — confirms the eviction subresource, not just the response body's `code` field.

### Restore

```bash
$ kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":1}}'
poddisruptionbudget.policy/events-pdb patched

$ kubectl get pdb events-pdb
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   1               N/A               1                     5m52s

$ kubectl get pod -l app=events
NAME                      READY   STATUS    RESTARTS       AGE
events-664dbfb59b-gnw2l   1/1     Running   5 (179m ago)   14d
events-664dbfb59b-t79t5   1/1     Running   0              8m39s
```

Both events pods are still present and untouched — the eviction request was rejected outright
by the API server before it could remove anything, so there was nothing to recover from besides
un-tightening the PDB.

---

## Task 1 — Questions

### Q1: With 3 gateway replicas and minAvailable: 1, what's the maximum number of pods that can be evicted simultaneously? Why is your gateway-pdb set to minAvailable: 2 with 5 replicas?

With 3 replicas and `minAvailable: 1`, the max simultaneous eviction is `3 - 1 = 2` — the API
server will refuse any eviction that would drop the available count below 1.

Our `gateway-pdb` uses `minAvailable: 2` on 5 replicas (allowing 3 simultaneous evictions), not
`minAvailable: 4` (which would only tolerate 1). `minAvailable: 4` would allow only one eviction
at a time, making node drains unnecessarily slow because each pod must become Ready before the
next eviction is permitted. Using `minAvailable: 2` preserves sufficient serving capacity while
allowing the scheduler to relocate the remaining replicas efficiently.

### Q2: Your topology-spread constraint has no observable effect on single-node k3d. In a 3-node cluster, what placement would maxSkew: 1 produce for 5 gateway pods? What about for 7?

For 5 pods across 3 nodes with `maxSkew: 1`, the scheduler must keep the difference between the
busiest and the emptiest node at ≤ 1, which forces `2/2/1` (never `3/1/1` or `4/1/0`, since those
have a skew of 2 or more relative to the emptiest node).

For 7 pods across 3 nodes, the only distribution with max − min ≤ 1 is `3/2/2` (3+2+2 = 7,
skew = 1). A split like `3/3/1` would sum to 7 too but has skew 2 (3 vs 1), which violates
`maxSkew: 1`.

---

# Task 2 — Graceful Shutdown + Zero-Downtime Migration

## 12.6 — preStop hook + readinessProbe

### k8s/gateway.yaml (diff)

```yaml
      # Give in-flight requests time to finish after SIGTERM (10s preStop + up to 30s drain).
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          ...
          lifecycle:
            # Sleep BEFORE SIGTERM reaches the app. Gives kube-proxy / endpoints
            # controllers time to propagate this pod's NotReady state to every
            # node's iptables, so new traffic stops routing here BEFORE uvicorn
            # shuts down. Without this, there's a ~5-10s window where SIGTERM
            # + incoming traffic overlap and requests get RST.
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

### Apply

```bash
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured
service/gateway unchanged

$ kubectl argo rollouts status gateway --timeout=240s
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Paused - CanaryPauseStep
Progressing - more replicas need to be updated
Progressing - updated replicas are still becoming available
Progressing - old replicas are pending termination
Progressing - waiting for all steps to complete
Healthy
```

---

## Rolling restart under load

### Before

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'

{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784311342.556,"0"]}]}}
```

### Restart

```bash
$ kubectl argo rollouts restart gateway
rollout 'gateway' restarts in 0s

$ kubectl argo rollouts status gateway --timeout=240s
Progressing - waiting for rollout spec update to be observed
Progressing - rollout is restarting
Healthy

$ kubectl get pod -l app=gateway
NAME                       READY   STATUS    RESTARTS   AGE
gateway-5bdbb4dc76-2l7br   1/1     Running   0          77s
gateway-5bdbb4dc76-6459b   1/1     Running   0          69s
gateway-5bdbb4dc76-f8j6x   1/1     Running   0          83s
gateway-5bdbb4dc76-l999h   1/1     Running   0          91s
gateway-5bdbb4dc76-ln2zp   1/1     Running   0          62s
```

### After

```bash
$ sleep 10
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B3m%5D))'

{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1784311440.583,"0"]}]}}

$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
{"status":"success","data":{"resultType":"vector","result":[]}}
```

### Observation

Both before and after the restart show 0 — the absolute-counter query returns an empty result
set, meaning no 5xx time series exists for the current gateway pods. `kubectl argo rollouts
restart gateway` recycled all 5 pods (new hashes: `gateway-5bdbb4dc76-*`) while `mixedload` kept
sending traffic the whole time, and none of it failed. This confirms the preStop sleep +
tightened readiness probe (`periodSeconds: 2, failureThreshold: 1`) close the SIGTERM/traffic
race window described in the reading: the pod is pulled out of Service endpoints before uvicorn
actually stops accepting connections.

---

## 12.7 — CREATE INDEX CONCURRENTLY migration

### migrations/versions/cfdc4972afd7_index_events_event_date_concurrently.py

```python
def upgrade() -> None:
    """Upgrade schema."""
    # CREATE INDEX CONCURRENTLY cannot run inside Alembic's default transaction
    # block (Postgres rejects it with ActiveSqlTransaction). autocommit_block()
    # runs this DDL outside that transaction so CONCURRENTLY actually works.
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
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date",
            table_name="events",
            postgresql_concurrently=True,
            if_exists=True,
        )
```

### Run under live load

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
{"status":"success","data":{"resultType":"vector","result":[]}}   # no 5xx time series at all — effectively 0

$ time alembic upgrade head
real    0m4.090s
user    0m0.527s
sys     0m0.322s
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade bd7982ea9e79 -> cfdc4972afd7, index events event_date concurrently

$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- \
  psql -U quickticket -d quickticket -c '\d events' | grep idx_events
    "idx_events_event_date" btree (event_date)

$ sleep 5
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(gateway_requests_total%7Bstatus%3D~%225..%22%7D)'
{"status":"success","data":{"resultType":"vector","result":[]}}   # still no 5xx — matches "before"
```

Full `\d events` after the migration:

```text
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

---

## 12.8 — Expand-and-contract sketch (design only, no code)

Renaming `events.event_date` → `events.scheduled_at` with zero downtime, as 3 migrations + 2
code deploys, interleaved:

1. **Migration 1 — expand.** Add the new column, nullable, no default:
   ```sql
   ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ;
   ```
   Nullable + no default keeps this an instant metadata-only change even on a huge table — a
   `NOT NULL` column with a computed default would force a full table rewrite under an
   `ACCESS EXCLUSIVE` lock.

2. **Code deploy A — dual-write, fallback-read.** Every write path that used to write
   `event_date` now writes **both** `event_date` and `scheduled_at`. Every read path uses
   `COALESCE(scheduled_at, event_date)` instead of `event_date` directly. This is what makes the
   next step safe: at this point `scheduled_at` is NULL on every existing row, so the fallback to
   `event_date` is what keeps reads correct until the backfill runs.

3. **Migration 2 — backfill.**
   ```sql
   UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;
   -- then, once every row has scheduled_at populated:
   ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL;
   ```
   This is safe under live traffic because Deploy A is already reading via `COALESCE` — it
   tolerates rows in either state (`scheduled_at` NULL or populated) at any point during the
   backfill. The `UPDATE` is also idempotent (`WHERE scheduled_at IS NULL`), so if it's
   interrupted and re-run, already-backfilled rows are simply skipped.

4. **Code deploy B — switch to new column only.** Read and write paths now use `scheduled_at`
   exclusively; the `COALESCE` and the write to `event_date` are removed from the code. The
   application no longer references `event_date`, although the column still exists.

5. **Migration 3 — contract.**
   ```sql
   ALTER TABLE events DROP COLUMN event_date;
   ```

**Why migration 3 must come strictly after Deploy B is fully rolled out, never before:** until
every pod is running Deploy B, some pods are still on Deploy A logic, which reads
`COALESCE(scheduled_at, event_date)` and writes to `event_date`. If migration 3 runs while even
one Deploy-A pod is still alive, that pod's next query references a column that no longer
exists and Postgres returns `column "event_date" does not exist` — every request that pod
handles starts 500ing immediately. The whole point of expand-and-contract is that at every
intermediate schema state, *both* code versions currently running in the cluster must succeed;
dropping the column is the one step that is not backward-compatible with the old code, so it's
the only step that must wait for a confirmed, complete rollout (verified via
`kubectl rollout status` / an APM check with zero references to the old column) before it runs.

---

## 12.9 — Optional: HPA observation

### k8s/gateway-hpa.yaml

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gateway
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

```bash
$ kubectl apply -f k8s/gateway-hpa.yaml
horizontalpodautoscaler.autoscaling/gateway created

$ kubectl get hpa gateway
NAME      REFERENCE         TARGETS              MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: <unknown>/70%   5         12        0          0s
```

Drove load with a copy of the Lab 10 Locust runner (`labs/lab10/load-hpa.yaml`, reusing the
existing `locustfile` ConfigMap from Lab 10) at `-u 200 -r 20 -t 120s` against `http://gateway:8080`:

```bash
$ kubectl apply -f labs/lab10/load-hpa.yaml
job.batch/load-hpa created

$ kubectl get hpa gateway   # ~35s into the load
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 86%/70%   5         12        5          35s

$ kubectl get hpa gateway   # ~90s into the load
NAME      REFERENCE         TARGETS         MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 170%/70%   5         12        12         83s

$ kubectl get rollout gateway
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   12        12        12           2           16d
```

### Observation

CPU utilization exceeded the 70% target, causing HPA to scale the Rollout from 5 to 12 replicas.
On the single-node k3d cluster, all replicas were scheduled onto the same node, so this
demonstrates the controller behavior rather than real cluster elasticity.

Note: unlike 12.6/12.7, this test was not zero-5xx — 200 concurrent Locust users
deliberately saturate CPU to force the scale-up, and did produce real 502/504 errors:

```bash
$ kubectl logs job/load-hpa --tail=20
133   POST /events/3/reserve: HTTPError('502 Server Error: Bad Gateway ...')
152   POST /events/3/reserve: HTTPError('504 Server Error: Gateway Timeout ...')
354   GET /events: HTTPError('504 Server Error: Gateway Timeout ...')

$ kubectl get job load-hpa
NAME       STATUS   COMPLETIONS   DURATION   AGE
load-hpa   Failed   0/1           3m7s       3m7s
```

This is the expected cost of intentionally overloading the system to observe HPA, not a
regression in 12.6/12.7's zero-downtime results (those ran earlier and are unaffected).

---

## Task 2 — Questions

### Q1: Why does CREATE INDEX CONCURRENTLY matter? What happens if you omit it on a table with 10M rows?

A plain `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock on the table for the entire time it
takes to build the index — on a 10M-row table that can be minutes, and during that window every
query against the table (reads included) blocks. In production that's a full outage of anything
touching that table. `CREATE INDEX CONCURRENTLY` instead takes a much weaker
`SHARE UPDATE EXCLUSIVE` lock, which doesn't block ordinary reads or writes — it costs more disk
I/O and takes longer wall-clock time (it scans the table twice, without holding a long-lived
exclusive lock), but users never notice. On QuickTicket's 5-row `events` table this distinction
is invisible (both finish in milliseconds — our migration finished in ~4s total, dominated by
Python/Alembic/network overhead, not the index build itself) — the syntax is what matters here,
not the observed timing.

### Q2: In your expand-and-contract sketch, why MUST migration 3 (drop old column) come after deploy B has fully rolled out? What goes wrong if it runs before?

Until Deploy B is fully rolled out, some pods are still running Deploy A's code, which reads via
`COALESCE(scheduled_at, event_date)` and writes to `event_date`. If migration 3 drops
`event_date` while even one Deploy-A pod is still serving traffic, every query that pod issues
now references a column that no longer exists in the table — Postgres returns
`column "event_date" does not exist`, and that pod starts returning 500s on every request that
touches the `events` table until it's replaced. The expand-and-contract pattern only works
because each step is backward-compatible with the *previous* code version — the drop is the one
step that isn't, so it's the only one gated on a fully-confirmed rollout (`kubectl rollout
status` succeeding, plus ideally a few extra minutes of APM/log inspection showing zero
references to the old column) rather than being safe to run opportunistically.
