# Lab 12 — Bonus: Advanced Kubernetes Resilience

## Setup

The cluster is in the state Lab 11 left it in: the gateway is a 5-replica Argo Rollout (canary + `gateway-error-rate` analysis) running the Lab 11 image `quickticket-gateway:lab11-v2`. `events`, `payments` and `notifications` run 1 replica each. Postgres is on a PVC (Lab 9), and the in-cluster Prometheus scrapes every gateway pod every 5 s. It is a single node: `k3d-quickticket-server-0`.

All experiments ran on 2026-10-20 from 20:40 to 21:17 UTC (23:40–00:17 MSK). Pod-log timestamps are UTC.

### Bringing the manifests in line with the cluster first

This branch is cut from `feature/lab11` and has the Lab 9 Alembic files (`alembic.ini`, `migrations/`) brought in, because the live DB is stamped at `be711dc39c96` from Lab 9. The `k8s/` files, however, had drifted from the live cluster, because each earlier lab branch only changed its own files:

| File | In the branch | Live in the cluster |
|---|---|---|
| `k8s/gateway.yaml` | Lab 4 `Deployment`, 1 replica, ghcr image | Lab 7 `Rollout` (5 replicas, canary), Lab 8 probes on `/metrics`, Lab 11 image + env |
| `k8s/events.yaml` | probes on `/health` | Lab 8 probes: liveness `/metrics`, readiness `/events` |
| all app manifests | `imagePullPolicy: Always` + `imagePullSecrets: ghcr-secret` | `ghcr-secret` **no longer exists** (found in Lab 11) |

The lab tells me to `kubectl apply` these files. Applying them as they were would have turned the Rollout back into a Deployment, put Lab 8's cascading `/health` probes back, and made every new pod depend on a registry secret that no longer exists. So before Task 1 I rewrote `k8s/gateway.yaml` as the Rollout from the live spec, ported the Lab 8 probes into `k8s/events.yaml`, and switched the pull policy to `IfNotPresent` without the secret. I checked the result with `kubectl diff`: the only change left was the field this lab adds (`topologySpreadConstraints`). Any other difference you see between the `k8s/` files and `main` in this PR comes from this sync.

Note on images: `gateway` (`quickticket-gateway:lab11-v2`), `events` (`quickticket-events:lab12-b`, see Bonus) and `notifications` are local images loaded with `k3d image import`. The CI `sed` from Lab 5 only rewrites `ghcr.io/...` lines, so it will no longer bump those two. `payments` keeps its ghcr image (cached on the node). Getting GitOps back means recreating the pull secret and pointing the manifests at CI-built tags again after merge; I listed it in the backlog.

### Traffic and a client-side probe

```bash
kubectl apply -f labs/lab8/mixedload.yaml           # 2 replicas, reserve → pay on event 1
kubectl apply -f prober.yaml                        # see below
```

`mixedload` drops every response, so it can't tell me what users saw. The lab's own check, `gateway_requests_total{status=~"5.."}`, also has two blind spots, both confirmed below:
1. **A request that never reaches a gateway process** (connection refused from a terminating pod) isn't counted at all.
2. **`increase()` on a series that didn't exist before returns 0** (the Lab 10 toil trap). The first 5xx of each kind is invisible to `sum(increase(...))`. Also, killing a gateway pod removes its counters, so the raw `sum()` can even go *down*.

So I also ran a **prober**: one pod that loops `GET /events` → `POST /events/1/reserve` → `POST /reserve/{id}/pay` every ~0.25 s and logs every non-200 code with a timestamp (`000` = connection refused/reset). I used three signals for every test: the raw Prometheus counter, prober `BAD` lines, and gateway `ERROR` logs.

Baseline at 20:41:17: no 5xx series in Prometheus, prober `total=480 bad=0`, ~26 RPS (`/events` 8.6, reserve 8.8, pay 8.7).

---

## Task 1 — Multi-Replica Failover + PDBs

### 12.1: Two replicas

`replicas: 2` in `k8s/events.yaml`, `k8s/payments.yaml`, `k8s/notifications.yaml`:

```
$ kubectl get deploy events payments notifications
NAME            READY   UP-TO-DATE   AVAILABLE   AGE
events          2/2     2            2           30d
payments        2/2     2            2           30d
notifications   2/2     2            2           72m

$ kubectl get deploy,rollout
deployment.apps/events             2/2     2            2           30d
deployment.apps/notifications      2/2     2            2           72m
deployment.apps/payments           2/2     2            2           30d
...
rollout.argoproj.io/gateway   5         5         5            5           16d
```

**This apply already caused errors.** The pod template changed as well (pull policy and secret), so `events` did a rolling update. Its old pod was killed at 20:41:52, and the prober caught it:

```
20:41:52 BAD events:502
20:41:52 BAD pay:500
20:41:53 BAD reserve:502
```
```
gateway logs:  2× events service error: All connection attempts failed
               2× reserve error: All connection attempts failed
               1× confirm error after payment: All connection attempts failed
```

Prometheus counted these 5 (raw `sum` went from no series to 5), but `sum(increase(...[2m]))` returned **0** for all three new series, because of the new-series trap. The cause is the same one Lab 11 saw on payments: when SIGTERM arrives, uvicorn closes its listening socket right away, while kube-proxy keeps sending new connections to that pod IP for a moment. Also note that one of the five was a `/pay` whose charge succeeded and whose **confirm** failed (the "charged but not confirmed" case from Lab 11).

### 12.2: Failover — kill a gateway and an events pod under load

```bash
kubectl delete pod <gateway-pod> <events-pod> --wait=false
```

I ran this **5 times** (the first run went with a broken query helper, so I re-ran it with all three signals):

| Run | gateway ERROR logs | prober BAD | What failed |
|---|---:|---:|---|
| 1 (20:42:37) | 2 | 0 | 2× `reserve error: All connection attempts failed` (gateway → dying events pod) |
| 2 (20:44:17) | 0 | 0 | — |
| 3 | 0 | 0 | — |
| 4 | 1 | 1 | `confirm error after payment: All connection attempts failed` → `pay:500` |
| 5 | 0 | 0 | — |

Run 2 in detail:

```
20:44:17 sum(gateway_requests_total{5xx}) = 7      ← 5 from the 12.1 rollout + 2 from run 1
kill at 20:44:17
t+2s 5 of 7 Ready; not ready: events-58f9484995-dwb2m(Running) gateway-7b886b8dc5-qkgpc(Running)
t+6s 5 of 7 Ready; not ready: events-58f9484995-dwb2m(Running) gateway-7b886b8dc5-qkgpc(Running)
t+8s 7 of 7 Ready
20:45:00 sum(gateway_requests_total{5xx}) = 7      sum(increase(...[1m])) = 0
```

The replacements were Ready after ~8 s. The surviving replicas carried the traffic, and the gateway kill itself never produced a visible error. The **events kill did, in 2 of 5 runs** (3 failed requests in total), always the same `ConnectError` to the terminating pod. With 1 replica (Labs 8–11) this would have been an outage. With 2 it's a race at shutdown, and that is what preStop is for (12.6). After adding preStop I re-ran the same 5 kills: **0 / 0 / 0 / 0 / 0** (see 12.6).

In run 3, the raw counter went **down** from 7 to 5: the killed gateway pod took its 2 counted 5xx with it. Before/after comparisons of a raw `sum()` are only valid when no gateway pod restarts in between.

### 12.3: `k8s/pdb.yaml`

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

```
$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     3s
gateway-pdb         2               N/A               3                     3s
notifications-pdb   N/A             1                 1                     3s
payments-pdb        1               N/A               1                     3s
```

### 12.4: Topology spread

Added to the Rollout's `spec.template.spec`:

```yaml
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app: gateway
```

```
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

$ kubectl get pod -l app=gateway -o wide
NAME                      READY   STATUS    RESTARTS   AGE     IP            NODE
gateway-d87d65bbb-8bw9g   1/1     Running   0          50s     10.42.0.140   k3d-quickticket-server-0
gateway-d87d65bbb-cbgbt   1/1     Running   0          22s     10.42.0.144   k3d-quickticket-server-0
gateway-d87d65bbb-cq8pd   1/1     Running   0          50s     10.42.0.141   k3d-quickticket-server-0
gateway-d87d65bbb-qjwjq   1/1     Running   0          22s     10.42.0.143   k3d-quickticket-server-0
gateway-d87d65bbb-tl5zc   1/1     Running   0          2m57s   10.42.0.139   k3d-quickticket-server-0
```

All 5 on the only node, as expected with `ScheduleAnyway`. The canary rollout for this change passed analysis. But **the prober caught 5 failed requests** while the old pods were terminating (no preStop yet), and none of them reached Prometheus:

```
20:50:08 BAD pay:000
20:50:15 BAD events:000
20:50:15 BAD reserve:000
20:50:15 BAD events:000
20:50:16 BAD pay:000
```

### 12.5: A PDB actually blocks an eviction

`kubectl drain --dry-run=server` lists every pod (`evicting pod default/gateway-…` × 5, events × 2, payments × 2, …). As the lab says, that is not a PDB check: drain evicts one pod at a time.

Tightened `events-pdb` to `minAvailable: 2` (ALLOWED DISRUPTIONS → 0) and sent one eviction through `kubectl proxy`:

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"events-58f9484995-gwz4w","namespace":"default"}}' \
  http://localhost:8901/api/v1/namespaces/default/pods/events-58f9484995-gwz4w/eviction
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

The pod stayed `1/1 Running`. Restored `minAvailable: 1`.

I also wanted to see the budget work at its **normal** setting, the case a real drain hits. My first attempt surprised me: with `minAvailable: 1` I evicted pod A (201), then "the other pod" (201 again, while ALLOWED DISRUPTIONS showed 0). The events timeline explained it: my selector had picked **A's not-yet-Ready replacement**, not the healthy pod B. With the default `unhealthyPodEvictionPolicy: IfHealthyBudget`, a pod that isn't Ready may be evicted as long as the healthy count is still ≥ `minAvailable`, because removing it doesn't reduce availability. Pod B was untouched, and the prober saw 0 errors. A clean repeat, picking pods by name:

```
events pods (both Ready): events-58f9484995-g5fkw events-58f9484995-xclnt
events-pdb   1     N/A   1
evict events-58f9484995-g5fkw -> 201 Success
evict events-58f9484995-xclnt -> 429 Failure | The disruption budget events-pdb needs 1 healthy pods and has 1 currently
events-pdb   1     N/A   0
events-58f9484995-g5fkw   1/1   Terminating         0     29s
events-58f9484995-v8fd8   0/1   ContainerCreating   0     0s
events-58f9484995-xclnt   1/1   Running             0     6m
```

This is exactly what a drain does: the first pod goes, and the second is refused until the replacement is healthy.

### Answers

**With 3 gateway replicas and `minAvailable: 1`, how many pods can be evicted simultaneously? Why `minAvailable: 2` with 5?**
**2.** Allowed disruptions = healthy − minAvailable = 3 − 1. That leaves a single pod carrying all gateway traffic during a drain, with no redundancy left: one crash or OOM in that window and checkout is down.
With 5 replicas, `minAvailable: 2` allows 3 evictions at once. A drain can move most of the gateway in one step, so nodes can still be replaced quickly, while 2 pods remain. Two pods are both redundancy and enough capacity: Lab 10 measured the gateway at ≤ 30 % of its CPU limit at the breaking point, so 2 of 5 pods handle normal traffic, and the ceiling is set by `events`/Postgres anyway. `minAvailable: 4` would allow only 1 eviction at a time, so a drain crawls. Combined with a canary that has a pod in an unhealthy state, it could block a drain completely.
Two caveats from the tests above. A PDB only covers **voluntary** disruptions through the eviction API. `kubectl delete pod` (12.2), an OOM kill, or a node crash ignore it completely. And with a fixed number like `minAvailable: 2`, the budget doesn't change when the HPA scales to 12 (12.9). A percentage (`minAvailable: 40%`) would scale with the replica count.

**`maxSkew: 1` in a 3-node cluster: placement for 5 and for 7 gateway pods?**
5 pods → **2 / 2 / 1**; 7 pods → **3 / 2 / 2**. The most and least loaded node differ by at most 1, so `ceil(n/3)` and `floor(n/3)` per node. Two caveats: with `ScheduleAnyway` this is a *preference* (a scoring factor), so the scheduler may break it when nodes lack resources. And skew is only checked when a pod is scheduled. After a node dies and comes back, the pods are not rebalanced (that takes the descheduler or a rollout restart).

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration

### 12.6: preStop + readinessProbe

From `k8s/gateway.yaml`:

```yaml
    spec:
      ...
      # Give in-flight requests time to finish after SIGTERM (10s preStop + up to 30s drain).
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          ...
          lifecycle:
            # Sleep BEFORE SIGTERM reaches the app: the endpoints controller and
            # kube-proxy on every node need a few seconds to stop routing new
            # connections here. Without it uvicorn closes its socket while
            # iptables still sends traffic → connection refused (seen in Lab 12).
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
          ...
          # Stays on /metrics, NOT /health: gateway /health is 503 whenever events or
          # payments is degraded, which would pull all 5 replicas at once (Lab 8, Exp 3).
          readinessProbe:
            httpGet:
              path: /metrics
              port: 8080
            periodSeconds: 2
            failureThreshold: 2
```

**I did not use `/health` for readiness**, as the lab suggests. Lab 8 Experiment 3 found that gateway `/health` returns 503 whenever `events` or `payments` is degraded. As a readiness probe, that takes **all** gateway replicas out of the Service at once and turns a dependency problem into a total outage. This lab proved it again: during the 12.9 load test gateway `/health` returned **811 × 503**. With `/health` + `failureThreshold: 1`, all 12 gateway pods would have left the Service. With `/metrics` they stayed in and kept serving what they could. I kept the faster cadence (`periodSeconds: 2`), but with `failureThreshold: 2`: a single slow probe under load shouldn't remove a pod.

Readiness is not what makes preStop work, though. When a pod starts terminating, the endpoints controller removes it from the Service right away, whatever its probe says. The preStop sleep only makes sure the process keeps accepting connections until that change has reached kube-proxy.

12.1/12.2 showed the same race on `events`, so I gave **events, payments and notifications** the same `preStop: sleep 10` + `terminationGracePeriodSeconds: 40`. The python-slim images have `sh`.

#### Rolling restart under load: before vs after

Control run, **without preStop** (current spec, 20:51:44):

```
20:51:43 sum(gateway_requests_total{5xx}) = <empty: no 5xx series>
restart at 20:51:44   (kubectl argo rollouts restart gateway)
all 5 replaced in 38s
20:52:32 sum(gateway_requests_total{5xx}) = <empty: no 5xx series>    sum(increase(...[3m])) = 0
prober:
20:51:44 BAD reserve:000
20:51:52 BAD pay:000
20:51:53 BAD reserve:000
20:52:07 BAD reserve:000
20:52:07 BAD reserve:000
```

By the lab's criterion (Prometheus 5xx = 0) **this run passes**, yet 5 user requests failed with connection refused. Prometheus can't see a request that never reached a process.

After applying the preStop changes (`kubectl apply -f k8s/{events,payments,notifications,gateway}.yaml`), **with preStop** (20:56:09):

```
restart at 20:56:09
all 5 replaced in 59s
20:57:18 sum(gateway_requests_total{5xx}) = <empty: no 5xx series>    sum(increase(...[3m])) = 0
prober:
20:56:05 total=8520 bad=28
20:56:19 total=8640 bad=28
20:56:32 total=8760 bad=28
20:56:46 total=8880 bad=28
20:56:59 total=9000 bad=28
20:57:12 total=9120 bad=28
gateway ERROR log lines: 0
```

**0 of ~600 prober requests failed during the restart**, versus 5 without preStop. The restart took 21 s longer (5 × the 10 s sleep, partly overlapping); that is the price.

The 12.2 kill test with preStop on events, 5 runs:

```
run 1 (with preStop): gateway ERROR logs=0  prober BAD=0
run 2 (with preStop): gateway ERROR logs=0  prober BAD=0
run 3 (with preStop): gateway ERROR logs=0  prober BAD=0
run 4 (with preStop): gateway ERROR logs=0  prober BAD=0
run 5 (with preStop): gateway ERROR logs=0  prober BAD=0
21:01:17 sum(gateway_requests_total{5xx}) = <empty: no 5xx series>
```

(3 errors in 5 runs before → 0 in 5 after.)

One honest caveat: **the rollout that *introduced* preStop still produced errors.** The prober went 20 → 28 BAD, and Prometheus counted 2 × reserve 502 + 1 × pay 500, because the pods being replaced were the old ones without the hook. A graceful-shutdown fix takes effect only for the pods it is deployed to, not for the deploy that rolls it out.

### 12.7: `CREATE INDEX CONCURRENTLY` migration

`migrations/versions/4d00886794be_index_events_event_date_concurrently.py`:

```python
def upgrade() -> None:
    """Upgrade schema."""
    # CONCURRENTLY builds the index without the ACCESS EXCLUSIVE-style write lock
    # of a plain CREATE INDEX (it takes SHARE UPDATE EXCLUSIVE: reads and writes
    # keep going). Postgres refuses to run it inside a transaction block, and
    # Alembic wraps every migration in one, so step out with autocommit_block().
    # if_not_exists keeps it re-runnable if a previous attempt was interrupted.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date",
            "events",
            ["event_date"],
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

Generated SQL (`alembic upgrade … --sql`): the DDL sits **outside** Alembic's `BEGIN … COMMIT`:

```
BEGIN;
COMMIT;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_event_date ON events (event_date);
BEGIN;
COMMIT;
```

What happens without the wrapper:

```
$ psql -c "BEGIN; CREATE INDEX CONCURRENTLY idx_tmp ON events (name); ROLLBACK;"
BEGIN
ERROR:  CREATE INDEX CONCURRENTLY cannot run inside a transaction block
```

Run under mixedload + prober (port-forward to `svc/postgres` for Alembic):

```
5xx before: {"status":"success","data":{"resultType":"vector","result":[]}}
total requests before: 6055

$ time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade be711dc39c96 -> 4d00886794be, index events.event_date concurrently
real	0m0.469s

5xx after:  {"status":"success","data":{"resultType":"vector","result":[]}}
total requests after: 6248
diff /tmp/5xx.before /tmp/5xx.after → identical      prober BAD during the migration: 0
```

```
$ psql -c '\d events'
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)

$ psql -tAc "select indexrelid::regclass, indisvalid, indisready from pg_index where indexrelid='idx_events_event_date'::regclass"
idx_events_event_date|t|t
```

I checked `indisvalid`: a `CONCURRENTLY` build that fails halfway leaves an **INVALID** index behind. `IF NOT EXISTS` would then silently skip it on a re-run, so a re-runnable migration also has to check validity (or `DROP INDEX CONCURRENTLY IF EXISTS` first).

#### Making the difference visible

On 5 rows both variants are instant, so I measured them on a throwaway table with **1.5 M rows (98 MB)**. Each test starts the index build, waits 1 s, then times an `INSERT` and a `SELECT` from a second session:

| | index build | `INSERT` issued 1 s into the build | `SELECT` issued right after |
|---|---:|---:|---:|
| `CREATE INDEX` | 71.2 s | **69.5 s** (blocked until the build finished) | 3.2 s (not blocked; a seq scan on a CPU-starved Postgres) |
| `CREATE INDEX CONCURRENTLY` | 74.8 s | **0.10 s** | 4.8 s (not blocked) |

Two corrections to the lab text:
- A plain `CREATE INDEX` takes a **SHARE** lock, not ACCESS EXCLUSIVE. It blocks **writes** (INSERT/UPDATE/DELETE) for the whole build, while reads keep working. That is bad enough: every reserve and confirm hangs. (ACCESS EXCLUSIVE, which blocks everything, is what `ALTER TABLE … SET NOT NULL`, `DROP COLUMN` and table rewrites take. That matters in the bonus.)
- `CONCURRENTLY` removes the *lock*, not the *cost*. Both builds kept Postgres (200m CPU limit) at its cap for 2.5 min. The app had 0 errors, but gateway p99 rose to **~2.0 s on `/events`** and 1.4 s on reserve, and the prober's loop slowed ~4×. CONCURRENTLY is also slower overall (two table scans) and can't run in a transaction. On a real system you'd still schedule a large index build for low traffic, or give Postgres the CPU (Lab 10).

The demo table was dropped afterwards.

### 12.8: Expand-and-contract sketch — `events.event_date` → `events.scheduled_at`

Rule: at every step, **both the code that is running and the code that might still be running** must work with the current schema.

1. **Migration 1 (expand):** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;` Nullable and no default, so it's a metadata-only change. The running (old) code doesn't know about the column and isn't affected.
2. **Deploy A (dual-write, fallback-read):** writes set *both* `event_date` and `scheduled_at`; reads use `COALESCE(scheduled_at, event_date)`. It works on any row, filled or not. It must come after M1: before it, the column doesn't exist and every read 500s.
3. **Migration 2 (backfill):** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL` (in batches on a big table), then `SET NOT NULL`. This is safe under traffic because Deploy A is the only code running and it doesn't care whether a row is filled yet. New rows are already dual-written. The `WHERE … IS NULL` makes it idempotent. It must come after Deploy A: otherwise old code (writing only `event_date`) could insert rows after the backfill, and `SET NOT NULL` would fail or leave a NULL.
4. **Deploy B (switch):** reads and writes only `scheduled_at`. `event_date` is now dead weight that nothing touches. It must come after M2: Deploy B reading only `scheduled_at` on a row that isn't backfilled gets `NULL` (and `None.isoformat()` → 500 in our `events` code).
5. **Migration 3 (contract):** `ALTER TABLE events DROP COLUMN event_date;` only after Deploy B is **fully** rolled out: no pod anywhere still runs Deploy A or older code, including pods still `Terminating` in their 10 s preStop.

**Why must migration 3 come after deploy B has fully rolled out?** M3 is the only step that **removes** something. Every earlier step only adds, so old code keeps working. Until the last Deploy-A pod is gone, there is code running whose SQL names `event_date` (`COALESCE(e.scheduled_at, e.event_date)`). The moment the column is dropped, every `/events` and `/events/{id}` request on such a pod fails with `UndefinedColumn` → 500 → gateway 502, and the readiness probe (`/events`) then fails too and pulls the pod. With a rolling update, "B is deployed" is not the same as "A is gone": `kubectl rollout status` returns while old pods are still terminating. That's why I waited for zero `lab12-a` pods in the bonus. Also, after M3 the only rollback is a schema change (see bonus Q7). Before M3, rolling back is just redeploying the previous image.

**Why does `CREATE INDEX CONCURRENTLY` matter? What happens without it on a 10M-row table?**
Measured above on 1.5 M rows: a plain `CREATE INDEX` blocked a single INSERT for **69.5 s**. On 10 M rows the build takes minutes (~7× the data, plus sort spills), and for that whole time **every write to the table queues**: reserves, confirms, payments. Each waiting statement holds a DB connection, the 10-connection `events` pool runs out within seconds (Lab 10), and the service returns 5xx for everything, including reads, even though reads were never locked. It's a write outage for as long as the build runs, triggered by a "harmless" index. `CONCURRENTLY` takes SHARE UPDATE EXCLUSIVE instead: the INSERT took 0.1 s during the build. The cost is a slower build that can't run in a transaction (hence `autocommit_block`) and can leave an INVALID index behind if it fails.

### 12.9 (optional): HPA observation

`k8s/gateway-hpa.yaml`:

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
  minReplicas: 5    # don't drop below the Lab 7 base
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

Resources on the gateway: `requests.cpu: 50m`, `limits.cpu: 200m`. Load: the Lab 10 Locust Job with `-u 200 -r 20 -t 150s`, on top of mixedload.

```
21:06:45 cpu: 62%/70% replicas=5  ready=5      ← mixedload alone is already 62 % of a 50m request
21:07:16 cpu: 78%/70% replicas=5  ready=6
21:07:26 cpu: 140%/70% replicas=6  ready=6
21:07:46 cpu: 150%/70% replicas=11  ready=12
21:07:56 cpu: 103%/70% replicas=12  ready=12
21:08:27 cpu: 74%/70% replicas=12  ready=12
21:09:07 cpu: 70%/70% replicas=12  ready=12
21:09:58 cpu: 32%/70% replicas=12  ready=12    ← load over; stays at 12 (5 min scale-down stabilization)
```
```
Normal  SuccessfulRescale  horizontal-pod-autoscaler  New size: 6; reason: cpu resource utilization (percentage of request) above target
Normal  SuccessfulRescale  horizontal-pod-autoscaler  New size: 11; reason: cpu resource utilization (percentage of request) above target
Normal  SuccessfulRescale  horizontal-pod-autoscaler  New size: 12; reason: cpu resource utilization (percentage of request) above target
```

The HPA works as designed: 5 → 12 in ~30 s, utilization back to target. **But it didn't help users at all:**

```
Locust, 200 users:  9373 requests, 7595 failures (81.03 %)
  4516  GET /events: 502        846  POST /events/3/reserve: 500     811  GET /health: 503
   492  GET /events: 429        135  GET /events: 504                ...
events: 2006 × psycopg2.pool.PoolError: connection pool exhausted
```

This is the Lab 10 conclusion seen from the other side: the bottleneck is `events` + Postgres (10-connection non-queueing pool, 200m CPU), not the gateway. Scaling the gateway on CPU adds capacity to the tier that isn't saturated. With the Lab 11 rate limiter per pod, it even *raises* the ceiling on how much traffic gets through to `events` (12 × 10 req/s per path instead of 5 × 10). An autoscaler on the wrong signal can amplify an overload. The right signals here would be `events` pool saturation or latency, and an HPA on `events` would only help after its pool and Postgres are fixed.

The HPA also owns `spec.replicas` while it exists: the Rollout's `replicas: 5` is just the starting point, and a `kubectl apply` of `k8s/gateway.yaml` would conflict with it. The `gateway-pdb` stays at a fixed `minAvailable: 2` whether there are 5 or 12 pods. I **deleted the HPA after the observation** and re-applied `k8s/gateway.yaml` (back to 5). The file is in the repo for reference; to use it for real, remove `replicas` from the Rollout first.

---

## Bonus — Executing the Expand-and-Contract Rename

### Method

mixedload + prober ran during all 5 steps. **No gateway pod restarted during the bonus** (the same 5 pods, `gateway-554c6467f7-*`, before and after; per-pod counters identical), so the raw `sum(gateway_requests_total{status=~"5.."})` is a valid before/after counter here. The 3770 in it is all from the 12.9 overload test, which finished before the baseline. The `events` code changes were real image builds with unique tags (`quickticket-events:lab12-a`, `:lab12-b`), set in `k8s/events.yaml` and rolled out with `kubectl apply`. The rollouts used the preStop from 12.6.

Each migration was run to an **explicit revision** (`alembic upgrade e200bcca805a`, then `e20694da4601`, then `head`). All three files existed before the first step, so a plain `upgrade head` at M1 would have run M1 → M2 → M3 in one go, with no Deploy A/B in between: exactly the outage this pattern prevents.

`\d events` **before migration 1**:

```
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
```

### The five transitions

```
21:14:20 [baseline]                          5xx total=3770 baseline=3770 delta=0 | prober BAD +0
         M1: Running upgrade 4d00886794be -> e200bcca805a, add events.scheduled_at column    real 0m0.518s
21:14:27 [after M1 (add scheduled_at)]       5xx total=3770 baseline=3770 delta=0 | prober BAD +0
         Deploy A: events-b65d58656-z4shc / -zzp8n   quickticket-events:lab12-a   ready
21:15:26 [after Deploy A (COALESCE read)]    5xx total=3770 baseline=3770 delta=0 | prober BAD +0
         M2: Running upgrade e200bcca805a -> e20694da4601, backfill events.scheduled_at     real 0m0.654s
21:15:39 [after M2 (backfill + NOT NULL)]    5xx total=3770 baseline=3770 delta=0 | prober BAD +0
         Deploy B: events-6469fdc4c5-p6tld / -q5stq  quickticket-events:lab12-b   ready  (0 lab12-a pods left)
21:16:41 [after Deploy B (scheduled_at only)] 5xx total=3770 baseline=3770 delta=0 | prober BAD +0
         M3: Running upgrade e20694da4601 -> 85eeb559c536, drop events.event_date           real 0m0.446s
21:17:03 [after M3 (drop event_date)]        5xx total=3770 baseline=3770 delta=0 | prober BAD +0
```

```
baseline: t=1792530832.608 value=3770
final:    t=1792531023.844 value=3770
elapsed 191s, 5xx delta = 0
diff /tmp/5xx.baseline /tmp/5xx.final (counter values) → identical
requests served in the last 3 min: 4314
prober over the whole bonus: total 15360 → 17280 requests, bad 133 → 133
```

**Zero 5xx and zero client-side errors across all five transitions**, ~4300 requests served during them, including two `events` rolling updates.

### 1. The migrations (upgrade bodies)

`e200bcca805a_add_events_scheduled_at_column.py`:

```python
    # Expand. Nullable + no default = metadata-only change (no table rewrite,
    # lock held for milliseconds). Old code doesn't know the column exists.
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

`e20694da4601_backfill_events_scheduled_at.py`:

```python
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
    # Deploy B sorts by scheduled_at, and M3 drops event_date together with
    # idx_events_event_date (Lab 12.7) — build the replacement index first.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at",
            "events",
            ["scheduled_at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
```

The index part is my addition. Dropping a column drops its indexes, so without it M3 would silently undo 12.7, and `ORDER BY scheduled_at` would have no index.

`85eeb559c536_drop_events_event_date.py`:

```python
    # Contract. Only after Deploy B is fully rolled out: no running code reads
    # or writes event_date any more. Dropping the column also drops
    # idx_events_event_date.
    op.drop_column("events", "event_date")
```

(The downgrades: M1 drops the column; M2 drops the new index and makes the column nullable again; M3 re-adds `event_date`, backfills it from `scheduled_at`, sets NOT NULL and rebuilds `idx_events_event_date` CONCURRENTLY.)

After M2:

```
 id |       event_date       |      scheduled_at      | same
----+------------------------+------------------------+------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00 | t
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00 | t
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00 | t
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00 | t
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00 | t
```

### 2. `app/events/main.py`: Deploy A → Deploy B

**Writes:** the `events` service never writes to the `events` table at runtime (`grep -n "INSERT INTO events\|UPDATE events" app/` → only `seed.sql`). Rows come only from the seed, so **the dual-write step was a no-op**, and the "write" side of the rename is the `seed.sql` change below.

Deploy A (two SELECTs + one ORDER BY):

```python
            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date,
                   e.total_tickets, e.price_cents,
                   COALESCE(SUM(o.quantity), 0) as confirmed
            FROM events e LEFT JOIN orders o ON e.id = o.event_id
            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
```

Deploy A → Deploy B diff:

```diff
@@ -141,11 +141,11 @@
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date,
+            SELECT e.id, e.name, e.venue, e.scheduled_at,
                    e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
         """)
@@ -167,7 +167,7 @@
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date,
+            SELECT e.id, e.name, e.venue, e.scheduled_at,
                    e.total_tickets, e.price_cents,
```

**Response shape: kept unchanged.** The JSON field was already called `"date"` (built positionally from `r[3]`), not `event_date`, so the rename is internal to the DB and invisible to the gateway and clients. A public field rename would be its own expand-and-contract at the API level (serve both fields, migrate clients, drop the old one).

`app/seed.sql` now creates and fills `scheduled_at`:

```diff
-    event_date TIMESTAMPTZ NOT NULL,
+    scheduled_at TIMESTAMPTZ NOT NULL,
 ...
-INSERT INTO events (name, venue, event_date, total_tickets, price_cents) VALUES
+INSERT INTO events (name, venue, scheduled_at, total_tickets, price_cents) VALUES
```

### 3. `\d events` after migration 3

```
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
 scheduled_at  | timestamp with time zone |           | not null |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_scheduled_at" btree (scheduled_at)

$ alembic current
85eeb559c536 (head)
```

`event_date` and its index are gone, and `scheduled_at` is `NOT NULL` with its own index. `/events` still returns `"date":"2026-09-15T09:00:00+00:00"`.

### 4. 5xx baseline / final

See the transition log above: baseline **3770**, final **3770**, delta **0**, values identical. The prober (which also catches connection-level failures) stayed at 133 BAD over 1920 requests during the bonus.

### Finding: `seed.sql` and the migrations now disagree

I tested what a **fresh** cluster would do with the new seed and a throwaway Postgres (`docker run postgres:17-alpine` with `seed.sql` in `initdb.d`):

```
--- fresh DB from new seed.sql, then 'alembic upgrade head':
Running upgrade  -> 4ca253cd1007, baseline - pre-existing schema
Running upgrade 4ca253cd1007 -> be711dc39c96, add email column to events
Running upgrade be711dc39c96 -> 4d00886794be, index events.event_date concurrently
psycopg2.errors.UndefinedColumn: column "event_date" does not exist
```

The seed already creates the *final* column, but the migration chain assumes the *original* schema (the Lab 9 baseline is an empty "pre-existing schema" stamp). `alembic stamp head` gets past it, but then `idx_events_scheduled_at` doesn't exist and `email` depends on which migrations happened to run. There are two sources of truth for the schema, and after the rename they no longer agree. The proper fix is to have the baseline migration *create* the tables and keep `seed.sql` to `INSERT`s only, so a fresh DB is always `alembic upgrade head` + seed data. (The lab says to update the seed so a recreated cluster "won't fail at startup". It won't fail at startup, it fails at the first migration.)

### 5. Which single step would have caused 5xx if reordered earlier?

**Migration 3**, because it's the only step that *removes* something: if it runs before Deploy B is fully out, every pod still on Deploy A (or older) runs `SELECT … e.event_date …` against a column that no longer exists → 500 on `/events` and `/events/{id}` → gateway 502, and the `/events` readiness probe then fails and pulls those pods too.
The other steps only add, but their order still matters: Deploy A before M1 fails the same way (`scheduled_at` doesn't exist yet), and Deploy B before M2 reads `NULL` and crashes in `r[3].isoformat()`. Each step depends on the one before it. M3 is the one that can't be undone by redeploying an image.

### 6. Backfill at 10M rows — batching pattern

```python
BATCH = 10_000
last_id = 0
while True:
    with conn.transaction():                       # one short transaction per batch
        rows = conn.execute("""
            UPDATE events SET scheduled_at = event_date
            WHERE id IN (SELECT id FROM events
                         WHERE id > %s AND scheduled_at IS NULL
                         ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED)
            RETURNING id""", (last_id, BATCH)).fetchall()
    if not rows: break
    last_id = max(r.id for r in rows)              # keyset pagination: no growing OFFSET
    time.sleep(0.1)                                # let replication / vacuum / app traffic breathe
```

Each transaction locks at most 10k rows for milliseconds, and the job can be stopped and resumed (idempotent `IS NULL`). Then, instead of `SET NOT NULL` (an ACCESS EXCLUSIVE full scan): `ADD CONSTRAINT … CHECK (scheduled_at IS NOT NULL) NOT VALID` → `VALIDATE CONSTRAINT` (SHARE UPDATE EXCLUSIVE, no write block) → `SET NOT NULL` (Postgres 12+ uses the validated check and skips the scan) → drop the check.

### 7. Why the M3 downgrade is not enough for a safe rollback once Deploy B is live

The downgrade restores the **schema**, not **compatibility**:
- **It runs against live traffic too.** It adds `event_date` as nullable, `UPDATE`s it from `scheduled_at`, then sets NOT NULL. Deploy B is still writing only `scheduled_at` during all of this. Any row created between the UPDATE and SET NOT NULL makes the downgrade fail, or (without NOT NULL) leaves `event_date` NULL for the old code. In QuickTicket only the seed inserts, so it would work by luck. In a real system it's a race.
- **The code you roll back to must be able to run on that schema.** The code before Deploy A writes only `event_date`, so after you roll back to it, `scheduled_at` stops being filled. Then the *next* roll-forward (Deploy B again) reads NULLs. The downgrade + code rollback quietly break the forward path.
- **Locks and time:** on a big table, re-adding + backfilling + `SET NOT NULL` + rebuilding the index is the same heavy, partly ACCESS EXCLUSIVE work as the forward migration. Doing it during an incident is the worst moment for it.

For the rollback to be safe, it has to be **expand-and-contract in reverse**: re-add `event_date` nullable → deploy a dual-write build (Deploy A behaviour, both columns) → batch backfill `event_date` → only then route reads back / roll the code further back. Short of that, keep M3 behind a waiting period: don't drop the old column until Deploy B has run long enough that nobody will want to roll back to code that reads it (and have a backup from the Lab 9 CronJob from right before M3).

---

## Final state

```
$ kubectl get deploy,rollout,pdb,hpa
deployment.apps/events             2/2     2            2           30d
deployment.apps/mixedload          0/0     0            0           16d
deployment.apps/notifications      2/2     2            2           108m
deployment.apps/payments           2/2     2            2           30d
deployment.apps/postgres           1/1     1            1           30d
deployment.apps/redis              1/1     1            1           30d
rollout.argoproj.io/gateway   5         5         5            5           16d
poddisruptionbudget.policy/events-pdb          1               N/A               1
poddisruptionbudget.policy/gateway-pdb         2               N/A               3
poddisruptionbudget.policy/notifications-pdb   N/A             1                 1
poddisruptionbudget.policy/payments-pdb        1               N/A               1
(no HPA — deleted after 12.9)

$ alembic current
85eeb559c536 (head)
```

The prober, the Locust Job and the Postgres port-forward are removed. mixedload is scaled back to 0.

## Backlog from this lab

1. **Measure availability from the client side.** Prometheus missed every connection-refused failure in this lab (12.4, 12.6 control run). A blackbox probe (Prometheus blackbox-exporter or a prober like mine) belongs in the SLI set and in the canary analysis.
2. **Fix the `increase()`-on-new-series blind spot** in the alert/analysis queries (e.g. initialise status series at 0, or alert on raw counter changes as well).
3. **Single source of truth for the schema:** the baseline migration should create the tables, and `seed.sql` should only insert data. Today a fresh DB fails at `4d00886794be`.
4. **Retry `confirm` after a successful charge.** It failed in 12.1 and 12.2 run 4 again ("charged but not confirmed", also in Lab 11).
5. **Restore the GitOps image flow:** recreate `ghcr-secret` (or make the packages public) and switch gateway/events back to CI-built ghcr tags after merge.
6. **Autoscale on the real bottleneck:** fix the `events` pool and Postgres CPU first (Lab 10). An HPA on gateway CPU scales the wrong tier. If an HPA is kept, use a percentage-based `gateway-pdb` and drop `replicas` from the Rollout.
7. **ArgoCD:** `kubectl apply` removed ArgoCD's tracking annotation from `events`/`payments`. Auto-sync is still off since Lab 7, so the Application has to be re-synced (or re-enabled) after merge.
