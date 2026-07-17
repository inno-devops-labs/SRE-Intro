# Lab 12 — Bonus: Advanced Kubernetes Resilience

> Environment note: the running k3d cluster carries the accumulated state from Labs 7/9/11
> (gateway is a 5-replica Argo `Rollout` on the local `quickticket-gateway:v1` image with
> notifications wired in; events runs with `DB_MAX_CONNS`; `k8s/notifications.yaml` exists).
> The manifests in this PR were reconciled to that live state before the Lab-12 deltas were
> applied, so `kubectl apply` only changes what each task asks for (replicas, PDBs, topology
> spread) and never regresses the gateway image.

---

## Task 1 — Multi-Replica Failover + PDBs (4 pts)

### 1. `kubectl get deploy,rollout` — all services at target replica counts

```
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

events / payments / notifications scaled to **2/2**; gateway stays a 5-replica Rollout.

### 2. Before / after 5xx around the coordinated pod kill

Coordinated kill with the lab's command — `kubectl delete pod <gateway> <events> --wait=false` —
one gateway pod and one events pod deleted together under live mixedload.

> Note on the metric: gateway `/health` runs a *deep* dependency check, so kubelet probes can
> occasionally log an internal `GET /health` 503 that has nothing to do with user traffic. Both
> the raw counter and a `path!="/health"` (user-facing) counter are shown; here both are 0.

```
# BEFORE
raw          sum(increase(gateway_requests_total{status=~"5.."}[3m]))                 = 0
user-facing  sum(increase(gateway_requests_total{status=~"5..",path!="/health"}[3m])) = 0 (empty vector)

# kubectl delete pod (real)
pod "gateway-588d5b74c7-bklsb" deleted
pod "events-686c96c4d9-8tmsg"  deleted
# recovery: events back to 2/2, gateway back to 5/5 within seconds

# AFTER (1m window covers the kill)
raw          sum(increase(gateway_requests_total{status=~"5.."}[1m]))                 = 0
user-facing  sum(increase(...,path!="/health"}[1m]))                                   = 0
user-facing  sum(increase(...,path!="/health"}[3m]))                                   = 0
```

**Zero 5xx (raw and user-facing) through the kill.** The surviving replica served all traffic while
the Deployment/ReplicaSet rescheduled the deleted pod. (Deleting a pod not created via `kubectl run`
prints a harmless "no need to specify a resource type" hint on some kubectl versions; the delete
still succeeds.)

### 3. `kubectl get pdb`

```
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     14h
gateway-pdb         2               N/A               3                     14h
notifications-pdb   N/A             1                 1                     14h
payments-pdb        1               N/A               1                     14h
```

Matches the spec: gateway `2 / N/A / 3`, events `1 / N/A / 1`, payments `1 / N/A / 1`,
notifications `N/A / 1 / 1`.

### 4. Topology spread — live in spec + actual placement

`kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}'`:

```json
[
  {
    "labelSelector": { "matchLabels": { "app": "gateway" } },
    "maxSkew": 1,
    "topologyKey": "kubernetes.io/hostname",
    "whenUnsatisfiable": "ScheduleAnyway"
  }
]
```

`kubectl get pod -l app=gateway` placement (single-node k3d — all on the only node, as expected):

```
gateway-b5d9bff45-g29vz   Running   k3d-quickticket-server-0
gateway-b5d9bff45-hgnl9   Running   k3d-quickticket-server-0
gateway-b5d9bff45-rshcz   Running   k3d-quickticket-server-0
gateway-b5d9bff45-x4vnj   Running   k3d-quickticket-server-0
gateway-b5d9bff45-zrt7m   Running   k3d-quickticket-server-0
```

The constraint is correct and live; it simply has nothing to spread across on a 1-node cluster.

### 5. PDB actually blocks eviction — HTTP 429 body

`events-pdb` tightened to `minAvailable: 2` (2 replicas → `ALLOWED DISRUPTIONS = 0`), then one
eviction fired via `POST /api/v1/namespaces/default/pods/<pod>/eviction`:

```
HTTP_STATUS=429
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

PDB restored to `minAvailable: 1` afterward.

### 6. PDB math

**"With 3 gateway replicas and `minAvailable: 1`, what's the maximum number of pods that can be
evicted simultaneously?"** — The API computes `ALLOWED DISRUPTIONS = healthy − minAvailable = 3 − 1 = 2`.
So at most **2** pods can be voluntarily evicted at once; the 3rd eviction is rejected with 429
until a replacement becomes healthy.

**"Why is `gateway-pdb` set to `minAvailable: 2` with 5 replicas?"** — It permits `5 − 2 = 3`
simultaneous evictions, which is enough to let a node drain reschedule pods while still keeping
~half the normal RPS capacity serving. Setting `minAvailable: 4` would allow only 1 eviction and
would make a rolling node drain block (potentially forever) because the drain can't get enough
pods off the node at once. `2` balances "keep serving" against "let maintenance actually happen."

### 7. Topology spread placement on a real multi-node cluster

With `maxSkew: 1` across `kubernetes.io/hostname` on a **3-node** cluster:

- **5 gateway pods → 2 / 2 / 1.** The most-loaded node has 2, the least-loaded has 1, skew = 1 ✓.
  Placements like 3/1/1 (skew 2) or 4/1/0 are forbidden.
- **7 gateway pods → 3 / 2 / 2.** Max = 3, min = 2, skew = 1 ✓. (3/3/1 would be skew 2 → rejected.)

On single-node k3d the skew is trivially 0 (everything on one node), which is why the effect is
not observable here.

---

## Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts)

### 12.6 — `preStop` + `readinessProbe` (as in `k8s/gateway.yaml`)

```yaml
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          ...
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

Verified live in the Rollout spec:

```
termGrace=40
preStop={"exec":{"command":["sh","-c","sleep 10"]}}
readiness={"failureThreshold":1,"httpGet":{"path":"/health","port":8080},"periodSeconds":2}
```

### Rolling restart under load — 5xx before / after

Restarted with the lab's command, `kubectl argo rollouts restart gateway` (plugin v1.9.1):

```
$ kubectl argo rollouts restart gateway
rollout 'gateway' restarts in 0s
$ kubectl argo rollouts status gateway --timeout=240s
Progressing - rollout is restarting
Healthy

BEFORE  raw[1m]          = 0 (empty vector)
BEFORE  user-facing[3m]  = 0
# all 5 pods replaced one at a time, each draining gracefully via preStop
AFTER   raw[3m]          = 0
AFTER   user-facing[3m]  = 0
```

**Zero 5xx during the rolling restart.** preStop held SIGTERM 10s while the endpoint removal
propagated, and the fast readinessProbe pulled each pod out of the Service before uvicorn stopped.

### 12.7 — `CREATE INDEX CONCURRENTLY` migration

`migrations/versions/de677748fa9d_index_events_event_date_concurrently.py`:

```python
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

The `autocommit_block()` is the key detail — without it Alembic runs the DDL inside its default
transaction and Postgres rejects `CREATE INDEX CONCURRENTLY` with `ActiveSqlTransaction`.

> Env note: on this machine port 5432 is already held by a native Postgres, so Alembic was
> pointed at a `kubectl port-forward svc/postgres 15432:5432` for the run; `alembic.ini` is left
> at the lab-standard `5432`. Also, `migrations/env.py` + `script.py.mako` were missing from the
> working tree and were restored from the Lab 9 branch so the toolchain would run.

Run under live mixedload — 5xx before / after (counter, not increase):

```
BEFORE 5xx counter: {"...","result":[]}          # empty = 0

$ time alembic upgrade head
INFO ... Running upgrade  -> de677748fa9d, index events.event_date concurrently
real    0m1.355s

AFTER  5xx counter: {"...","result":[]}          # empty = 0
diff /tmp/5xx.before /tmp/5xx.after  ->  NO CHANGE (zero 5xx delta)
```

`\d events` after the migration:

```
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

### 12.8 — Expand-and-contract sketch: rename `event_date` → `scheduled_at` (design only)

**3 migrations + 2 code deploys, interleaved. At every step both old and new code must work.**

1. **Migration 1 — expand.** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;`
   Nullable so it's an instant metadata-only change (a `NOT NULL`/defaulted add would rewrite the
   whole table under an `ACCESS EXCLUSIVE` lock).
2. **Code deploy A — dual-write, fallback-read.** App writes both `event_date` and `scheduled_at`;
   reads `COALESCE(scheduled_at, event_date)`. Works whether or not a row is backfilled yet.
3. **Migration 2 — backfill + tighten.** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;`
   then `ALTER COLUMN scheduled_at SET NOT NULL`. Safe under traffic: Deploy A tolerates NULL via
   COALESCE, and the `WHERE ... IS NULL` makes it idempotent/re-runnable. (At 10M rows you'd batch
   by id range with sleeps between chunks to keep each transaction short.)
4. **Code deploy B — switch to new column only.** Read and write only `scheduled_at` (drop the
   COALESCE and the dual-write). Now nothing references `event_date`.
5. **Migration 3 — contract.** `ALTER TABLE events DROP COLUMN event_date;` Only safe once Deploy B
   is fully rolled out — see the answer below.

### Task 2 answers

**"Why does `CREATE INDEX CONCURRENTLY` matter? What happens if you omit it on a 10M-row table?"**
A plain `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock on the table for the entire build — on 10M
rows that's minutes during which *every* read and write blocks (an effective outage). `CONCURRENTLY`
builds the index with a milder `SHARE UPDATE EXCLUSIVE` lock that doesn't block reads or writes; it
costs a second table scan and can't run inside a transaction, but the table stays fully available.

**"Why MUST migration 3 (drop `event_date`) come after Deploy B is fully rolled out?"**
Until Deploy B is everywhere, some pods still run Deploy A, which reads/writes `event_date`
(via COALESCE and dual-write). If migration 3 drops the column while any Deploy-A pod is live, every
`/events` request that pod serves 500s (SELECT/INSERT references a column that no longer exists), and
new writes lose data. Dropping is only safe once *no running code* touches `event_date` — i.e. after
`kubectl rollout status` confirms Deploy B fully replaced Deploy A.

---

## Bonus Task — Execute the Expand-and-Contract Rename (2 pts)

Executed live on the cluster under mixedload, following the same shape as reference
[PR #331](https://github.com/inno-devops-labs/SRE-Intro/pull/331). `events` was first hardened with
a `preStop` hook + `terminationGracePeriodSeconds: 40` and moved to a locally-built
`quickticket-events:v1` image (`imagePullPolicy: Never`) so both code deploys are genuinely
zero-downtime. QuickTicket has **no runtime INSERT** of event rows — the only writer is
`app/seed.sql` at boot — so there is no dual-**write** path; only the reads change, and `seed.sql`
is switched to `scheduled_at` in Deploy B.

### 1. The three migration files (upgrade bodies)

```python
# M1 — add_events_scheduled_at_column  (revises: index migration)
def upgrade():
    op.add_column("events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))

# M2 — backfill_events_scheduled_at
def upgrade():
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)

# M3 — drop_events_event_date
def upgrade():
    op.drop_column("events", "event_date")
```

Alembic history (chained after the 12.7 index migration):

```
<base> -> de677748fa9d, index events.event_date concurrently
de677748fa9d -> 62e2f6d2502b, add events.scheduled_at column
62e2f6d2502b -> 716413418772, backfill events.scheduled_at
716413418772 -> 70b3c0332518 (head), drop events.event_date
```

### 2. `app/events/main.py` — Deploy A → Deploy B (read path)

```diff
- # Deploy A: prefer scheduled_at, fall back to event_date (COALESCE).
-   COALESCE(e.scheduled_at, e.event_date) AS event_date,
-   ... GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ # Deploy B: scheduled_at is backfilled + NOT NULL, read it directly.
+   e.scheduled_at AS event_date,
+   ... GROUP BY e.id ORDER BY e.scheduled_at
```

The `AS event_date` alias is kept through Deploy B so the `/events` response shape stays
byte-for-byte identical — the gateway and clients never see the rename. `app/seed.sql` switched
`event_date` → `scheduled_at` in the CREATE TABLE and the INSERT (so a fresh cluster boots on the
new schema).

### 3. `\d events` before M1 vs after M3

```console
# BEFORE M1 (has event_date NOT NULL + the 12.7 index)
 event_date    | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)

# AFTER M3 (event_date gone; scheduled_at NOT NULL; index dropped with its column)
 scheduled_at  | timestamp with time zone | not null
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
```

### 4. Zero 5xx across the whole sequence

Snapshot after the events-hardening step, then a delta check after each of M1 / Deploy A / M2 /
Deploy B / M3:

```
5xx baseline value (post-hardening) : 1
5xx final    value (after M3)       : 1      -> delta 0
user-facing 5xx (path!="/health") all-time : 0   (no user-facing 5xx ever)
```

The `1` is a single internal `GET /health` 503 from the one-off ghcr→local events image switch;
it is *in the baseline*, so the **delta across the five graded transitions is 0**. Every `/events`
read stayed 2xx throughout (verified live after each deploy).

### 5. Which single step would have caused 5xx if reordered earlier?

**Migration 3 (drop `event_date`).** If it ran before Deploy B was fully rolled out, any surviving
Deploy-A pod — whose query still names `event_date` via COALESCE — would 500 on *every* `/events`
request the instant the column disappeared. (M1 add-column and M2 backfill are both additive/safe at
any time; Deploy A only adds a fallback; Deploy B only removes an already-redundant fallback.) The
drop is the one irreversible removal, so it must come strictly last.

### 6. Batching pattern for a 10M-row backfill

```
last_id = 0
while True:
    rows = execute("""
        UPDATE events SET scheduled_at = event_date
        WHERE id > :last_id AND id <= :last_id + 10000 AND scheduled_at IS NULL
        RETURNING id
    """, last_id=last_id)      # each UPDATE is its own short transaction
    if not rows:
        break
    last_id += 10000
    sleep(0.1)                 # let autovacuum + replication catch up between chunks
```

Each chunk commits independently, so no single long transaction holds locks or bloats WAL; the table
stays writable the whole time.

### 7. Why re-adding `event_date` in M3's downgrade isn't sufficient for true rollback safety

M3's `downgrade()` re-creates `event_date` and backfills it from `scheduled_at` — so the *schema* is
restored. But once Deploy B is live in production, rolling **back the schema alone doesn't roll back
the code**: any new rows written while Deploy B ran only have `scheduled_at` set through the app's
current path, and — more importantly — a true rollback also has to redeploy Deploy A (or earlier)
code, and that code must still tolerate the intermediate states. Rollback is only safe if (a) Deploy
A code is still deployable and reads via COALESCE (so it works whether or not `event_date` is
back-populated yet), and (b) the down-migration runs its backfill *before* any Deploy-A pod serves
traffic. In other words, rollback is itself an expand-and-contract in reverse — you can't just drop
the column back in and flip the image; you sequence it the same careful way.

---

## Notes / deviations from the lab text

- **Working tree vs. live cluster:** the repo was on `main`, whose manifests lagged the running
  cluster (older ghcr tags, no `k8s/notifications.yaml`, gateway on a pre-lab11 image). Each manifest
  was reconciled to the live lab-7/9/11 state before applying the Lab-12 deltas, so nothing regressed.
- **Alembic chain starts at the index migration** (`down_revision = None`), not at a lab9 baseline:
  this DB had no `alembic_version` table and no lab9 `email` column, and `migrations/env.py` +
  `script.py.mako` were missing from the tree (restored from the Lab 9 branch so the toolchain runs).
  The lab-12 migration bodies are unchanged; only the chain root differs.
- **Port 5432 held by a native Postgres** → Alembic ran against `kubectl port-forward svc/postgres
  15432:5432`; `alembic.ini` is left at the lab-standard `5432`.
- **`/health` 503 noise:** the gateway's `/health` does a deep dependency check, so kubelet probes
  can log an internal 503 unrelated to user traffic. Any raw 5xx seen are these internal probes;
  user-facing paths (`/events`, `/events/{id}/reserve`) never 5xx.
