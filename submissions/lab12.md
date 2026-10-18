# Lab 12 — Advanced Kubernetes Resilience

Task 1, Task 2 and the live expand-and-contract bonus were executed against the
local `k3d-quickticket` cluster. The branch starts directly at `main`
(`cf2ad63f3d3d56546c3bd5a18b09867ffb74b200`). Required manifests, the Lab 11
Gateway/Notifications implementation and the Lab 9 Alembic baseline were
carried into this branch as prerequisites; no other lab branch was merged.
There was no push, PR, or change to the Argo CD Application.

The cluster has one node (`k3d-quickticket-server-0`, Kubernetes
`v1.35.5+k3s1`), Argo Rollouts `v1.10.0`, PostgreSQL 17 and an existing PVC.
Timestamps in the evidence are UTC; Moscow time is UTC+3. All experiments ran
on 2026-10-18. Full command output is in [the evidence directory](../labs/lab12/evidence/).

## Workload and measurement

The Lab 8 mixedload deployment was applied first, then updated with
[`labs/lab12/mixedload.yaml`](../labs/lab12/mixedload.yaml): the same
read → reserve → pay loop, two replicas, one-second pause, HTTP outcome logging
and a 15-second request deadline. Checkout targets existing event **6** with
100,000 tickets, rather than consuming the small public event 1. Its existing
data was preserved. This is real checkout traffic through Gateway, Redis,
Events, Payments, PostgreSQL and background Notifications.

For every checkpoint the capture script records:

```promql
sum(gateway_requests_total{status=~"5.."}) or vector(0)
sum(increase(gateway_requests_total{status=~"5.."}[3m])) or vector(0)
sum(increase(gateway_requests_total{path!="/health"}[3m]))
sum(up{job="gateway"})
sum(increase(gateway_requests_total{path="/reserve/{id}/pay",status="200"}[3m])) or vector(0)
```

A missing 5xx series means no observed 5xx only when scraping and traffic are
also present; the other queries and client logs establish both. An instant
sum of process counters can **decrease** when pods disappear. Therefore the
pod-replacement proofs also use per-series `increase`, successful checkout
traffic and client-side outcomes. For the bonus, Gateway pods stay unchanged,
so baseline/final instant totals are comparable. JSON timestamps are not
compared as if they were counters.

During preparation, before the measured tests, a dependency health check
returned 503. The warmup snapshots preserve that observation: their three-minute
window includes one earlier `/health` error, and their instant totals include
historical errors from preceding labs. They are not the zero-error acceptance
measurements. The tests below were repeated after preparation and the complete
Gateway canary rollout had settled. No metrics were reset or deleted.

## Task 1 — Replicas, failover, PDBs and placement

Events, Payments and Notifications each have two replicas; Gateway remains a
five-replica Argo Rollout. Events uses `maxUnavailable: 0`, `maxSurge: 1` and a
10-second preStop delay to allow endpoint updates during its two bonus deploys.

The four `policy/v1` PDBs select `app: <service>`:

| PDB | Policy | Healthy replicas | Allowed disruptions |
|---|---|---:|---:|
| gateway-pdb | minAvailable: 2 | 5 | 3 |
| events-pdb | minAvailable: 1 | 2 | 1 |
| payments-pdb | minAvailable: 1 | 2 | 1 |
| notifications-pdb | maxUnavailable: 1 | 2 | 1 |

PDBs protect Eviction API requests, including drains; direct pod deletion,
node failure and Deployment rolling updates are not prevented by a PDB.
Controllers' rollout strategies and application termination behavior matter too.

The actual eviction response, with Events temporarily at `minAvailable: 2`, was:

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "status": "Failure",
  "message": "Cannot evict pod as it would violate the pod's disruption budget.",
  "reason": "TooManyRequests",
  "details": {"causes": [{
    "reason": "DisruptionBudget",
    "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
  }]},
  "code": 429
}
```

The HTTP status was **429**. `DisruptionBudget` is the nested cause, not the
top-level status reason on this Kubernetes version. The PDB was restored in a
`finally` block to `minAvailable: 1`, with one allowed disruption.
See [`pdb-eviction.log`](../labs/lab12/evidence/pdb-eviction.log).

The live Gateway constraint is:

```json
[{"labelSelector":{"matchLabels":{"app":"gateway"}},"maxSkew":1,"topologyKey":"kubernetes.io/hostname","whenUnsatisfiable":"ScheduleAnyway"}]
```

All five pods run on the single node; placement and the live spec were captured.
`ScheduleAnyway` expresses a scheduling preference, so it cannot guarantee a
maximum skew. With three equally eligible nodes, the intended balanced placement
is **2/2/1** for five replicas and **3/2/2** for seven. Other scheduling constraints
or capacity can produce a less balanced result. A single-node cluster also cannot
provide resilience against losing its only node.

With **3 replicas and minAvailable: 1**, at most **2 healthy pods** may be
voluntarily evicted before replacements become healthy. With five Gateway
replicas, `minAvailable: 2` retains two serving processes while allowing up to
three evictions, balancing capacity with maintenance progress. This is a pod
count policy; it does not by itself prove the remaining two pods can handle peak
production load.

## Task 2 — Graceful restart and concurrent index

The Gateway pod template contains:

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
        port: http
      periodSeconds: 2
      failureThreshold: 1
```

The image command explicitly sets Uvicorn's `--timeout-graceful-shutdown 30`.
The 40-second Kubernetes budget includes the 10-second preStop delay. Endpoint
termination makes the endpoint unready while the process continues serving
in-flight/late traffic; after the hook, Uvicorn receives SIGTERM and drains.
Readiness also gates startup. The sleep is a practical propagation allowance,
not a universal timing guarantee. See [Kubernetes pod termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination)
and [lifecycle hooks](https://kubernetes.io/docs/concepts/containers/container-lifecycle-hooks/).

The normal canary rollout and its AnalysisRun succeeded; no promotion gate was
bypassed. The restart used the official Argo Rollouts CLI against a **Rollout**,
then waited for all five replacement pods and the current generation to become
healthy. It did not address `deployment/gateway`.

The concurrent index revision is `12a_index_event_date`, after the preserved
Lab 9 revision `ddd061115f64`. Both directions run outside a transaction:

```python
def upgrade():
    with op.get_context().autocommit_block():
        op.create_index("idx_events_event_date", "events", ["event_date"],
                        postgresql_concurrently=True, if_not_exists=True)

def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index("idx_events_event_date", table_name="events",
                      postgresql_concurrently=True, if_exists=True)
```

The migration was run, its index inspected, downgraded, and then run again in
an isolated measured phase under load. `transaction_per_migration=True` bounds
the transaction for each revision; the autocommit block commits preceding work.
See [Alembic runtime documentation](https://alembic.sqlalchemy.org/en/latest/api/runtime.html#alembic.runtime.migration.MigrationContext.autocommit_block).

Why CONCURRENTLY matters: ordinary `CREATE INDEX` takes a `SHARE` table lock
that blocks writes but allows ordinary SELECTs. On 10 million rows this may
block application writes for a long time. The concurrent form permits ordinary
reads/writes during the build at the cost of additional scans/work and waiting
for old transactions. Neither syntax eliminates all waiting. The lab's claim
that normal index creation takes `ACCESS EXCLUSIVE` and blocks all reads is
incorrect. See [PostgreSQL CREATE INDEX](https://www.postgresql.org/docs/17/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY).

`IF NOT EXISTS` alone does not repair an invalid index left by an interrupted
concurrent build: inspect `pg_index.indisvalid` and remove/rebuild an invalid
index before retrying. The validation script checks the built index is valid.

## Expand-and-contract design and executed bonus

The exact sequence is:

1. **M1 — `12b_add_scheduled_at`:** add nullable `TIMESTAMPTZ scheduled_at`.
   Existing code still reads `event_date`; existing rows have NULL in the new
   column. Adding the nullable column avoids rewriting the rows, but still
   needs a brief schema lock. A two-second lock timeout bounds acquisition.
2. **Deploy A — image `quickticket-events:lab12-a`:** both date SELECTs and the
   ORDER BY use `COALESCE(scheduled_at, event_date)`. The existing JSON field
   remains **`date`**. There is no runtime INSERT/UPDATE of event dates in this
   application, so dual-write is a no-op; reservations and order inserts do
   not write either event-date column. A system with event-date writers would
   need dual-write on every writer before continuing.
3. **M2 — `12c_backfill_scheduled_at`:** update only rows with NULL scheduled_at,
   then enforce NOT NULL. Deploy A works before, during and after this step.
   With no event-date writers, nothing can introduce new NULL dates during the
   backfill. At production scale, use batches and separate validation of the
   NOT NULL constraint; the tiny-table operation here is not a scalable recipe.
4. **Deploy B — image `quickticket-events:lab12-b`:** read/order only by
   scheduled_at, still returning JSON **`date`**. Wait for full rollout and
   verify that every remaining Events pod runs B and no A pod is terminating.
5. **M3 — `12d_drop_event_date`:** drop the old column. The index on the old
   column disappears with it; the earlier index/schema output preserves proof
   of Task 2. The final seed creates/inserts `scheduled_at`, and includes the
   Lab 9 nullable email column so fresh installs match the final schema.

The destructive step **M3** would produce errors if moved ahead of the full
Deploy B rollout: old code and Deploy A still reference `event_date`, including
inside COALESCE; SQL resolves all referenced columns before evaluating values.
Deploy A also cannot precede M1, and B must not precede the completed backfill.

The actual Deploy A source is preserved in commit **`9815c67`**. The final
working tree/second code commit contains B. Their SQL change is:

```diff
- COALESCE(e.scheduled_at, e.event_date) AS event_date
+ e.scheduled_at AS event_date
- ORDER BY COALESCE(e.scheduled_at, e.event_date)
+ ORDER BY e.scheduled_at
```

Both SELECTs preserve the positional result and public `date` response. The
SQL alias is internal; it does not refer to a dropped column. The two unique
image tags and `imagePullPolicy: Never` select the locally imported images
without reusing a cached `v1` tag.

Production backfill pattern (short transaction per batch):

```text
last_id = 0
while True:
    begin transaction; set local lock_timeout = '1s'
    ids = select id where id > last_id order by id limit 1000
    if ids is empty: commit; break
    update events set scheduled_at = event_date
        where id in ids and scheduled_at is null
    commit; last_id = max(ids); sleep(0.05)
verify no nulls; validate constraint; enforce NOT NULL
```

All old and new writers must maintain the new field (or use a synchronization
trigger) before the batch pass starts. Retry batches after transient lock
failures. An UPDATE locks affected rows, not the entire table against every
write; a huge transaction still holds many row locks, generates WAL and bloat,
and delays conflicting writers/cleanup for too long.

M3 downgrade recreates event_date, copies scheduled_at, enforces NOT NULL and
rebuilds the removed index concurrently. This restores a schema snapshot,
not a general rollback guarantee. If B or another production writer continues
to write only scheduled_at, event_date immediately becomes stale; new inserts
can also fail its NOT NULL requirement. Safe rollback requires preserving all
needed data, restoring synchronization/dual-write (or pausing writers),
reconciling new writes, and deploying compatible code before relying on the
old column. Older readers must not return while the column is absent. Here
there are no event-date writers; downgrades are tested only in a disposable DB.

## Measured checkpoints

All counts below are extracted from the saved Prometheus JSON. Request and
checkout increases are estimates over overlapping three-minute windows, rounded
to one decimal; they are evidence of sustained traffic, not disjoint per-step totals.

| Checkpoint (UTC) | 5xx total | 5xx increase, 3m | Requests, 3m | Successful checkouts, 3m | Up targets |
|---|---:|---:|---:|---:|---:|
| [before-kill 09:37:04](../labs/lab12/evidence/snapshot-before-kill.log) | 0 | 0 | 1006.7 | 335.3 | 5 |
| [after-kill 09:37:27](../labs/lab12/evidence/snapshot-after-kill.log) | 0 | 0 | 1002.8 | 333.7 | 5 |
| [after-restart 09:38:48](../labs/lab12/evidence/snapshot-after-restart.log) | 0 | 0 | 1005.3 | 331.1 | 5 |
| [after-index 09:39:00](../labs/lab12/evidence/snapshot-after-index.log) | 0 | 0 | 1008.5 | 331.5 | 5 |
| [bonus-baseline 09:39:00](../labs/lab12/evidence/snapshot-bonus-baseline.log) | 0 | 0 | 1005.8 | 331.0 | 5 |
| [after-m1 09:39:11](../labs/lab12/evidence/snapshot-after-m1.log) | 0 | 0 | 1000.5 | 329.7 | 5 |
| [after-deploy-a 09:39:43](../labs/lab12/evidence/snapshot-after-deploy-a.log) | 0 | 0 | 1013.0 | 332.7 | 5 |
| [after-m2 09:39:57](../labs/lab12/evidence/snapshot-after-m2.log) | 0 | 0 | 1013.3 | 334.6 | 5 |
| [after-deploy-b 09:41:27](../labs/lab12/evidence/snapshot-after-deploy-b.log) | 0 | 0 | 1009.1 | 336.4 | 5 |
| [bonus-final 09:41:43](../labs/lab12/evidence/snapshot-bonus-final.log) | 0 | 0 | 1013.2 | 336.4 | 5 |

The measured pod-kill ran at **09:37:05 UTC**; the surviving and replacement pods
were ready at approximately **09:37:12 UTC** (about 7 seconds).
[`kill.log`](../labs/lab12/evidence/kill.log) captures the names and placement.
The restart ran at **09:37:27 UTC**, completed with five replacement pods, and
its post-scrape checkpoint at **09:38:48 UTC** remained zero.
The isolated concurrent-index migration ran at **09:38:49 UTC**; its subsequent
checkpoint also remained zero. The after-kill checkpoint is the before-restart
measurement; after-restart is the before-index measurement.

| Bonus transition | Start UTC | Evidence |
|---|---|---|
| M1 | 2026-10-18T09:39:01.298437+00:00 | [migration-1.log](../labs/lab12/evidence/migration-1.log) |
| Deploy A | 2026-10-18T09:39:12.751635+00:00 | [deploy-a.log](../labs/lab12/evidence/deploy-a.log) |
| M2 | 2026-10-18T09:39:46.410220+00:00 | [migration-2.log](../labs/lab12/evidence/migration-2.log) |
| Deploy B | 2026-10-18T09:40:53.684244+00:00 | [deploy-b.log](../labs/lab12/evidence/deploy-b.log) |
| M3 | 2026-10-18T09:41:28.328297+00:00 | [migration-3.log](../labs/lab12/evidence/migration-3.log) |

Deploy A completed at two replicas before M2. Before M3, the explicit pod check
found exactly two Ready B pods, no A pods and no terminating Events pods:

- `events-5f47656f9c-kgjl7`
- `events-5f47656f9c-wgcb7`

Their image config ID is
`sha256:8c6dab0238e0b08a9eb4a19d7254c8187bdea2d4b58a922be3228a0a2732ff71`;
A used `sha256:03e26e19e34eaebe8c72699cdab3afeb7334fab0cb495acb80f905de0d9997ac`.
See [A images](../labs/lab12/evidence/deploy-a-images.log) and
[B pods](../labs/lab12/evidence/deploy-b-pods.log).

The baseline/final raw query responses were:

```json
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792316340.771,"0"]}]}}
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1792316504.165,"0"]}]}}
```

Numeric extraction and actual comparison:

```console
$ python3 scripts/lab12_summarize.py
PASS all checkpoints: 0 observed 5xx, 5 scrape targets, positive traffic and checkout success
Bonus baseline = 0
Bonus final = 0
$ diff -u labs/lab12/evidence/5xx-baseline.txt labs/lab12/evidence/5xx-final.txt
# no output; exit status 0
```

See [numeric comparison](../labs/lab12/evidence/metric-comparison.log) and
[all numeric checkpoints](../labs/lab12/evidence/metrics-summary.json).
The captured load-generator logs contain **2,006 logged read/pay HTTP responses,
all 200**, and no curl transport errors. Reservation responses are captured by
Gateway metrics and successful checkout completion; the shell stores them to
extract the reservation ID rather than printing their bodies. Full client
logs are in [mixedload-client-log.log](../labs/lab12/evidence/mixedload-client-log.log).
This demonstrates the observed experiment at this load, not a guarantee for
all future traffic or propagation delays.

## Live resource and schema output

Final measured service/PDB state (`kubectl get deploy,rollout,pdb`):

```text
NAME                               READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/backup-inspector   1/1     1            1           8d
deployment.apps/events             2/2     2            2           27d
deployment.apps/mixedload          2/2     2            2           10m
deployment.apps/notifications      2/2     2            2           76m
deployment.apps/payments           2/2     2            2           27d
deployment.apps/postgres           1/1     1            1           27d
deployment.apps/redis              1/1     1            1           27d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           13d

NAME                                           MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
poddisruptionbudget.policy/events-pdb          1               N/A               1                     10m
poddisruptionbudget.policy/gateway-pdb         2               N/A               3                     10m
poddisruptionbudget.policy/notifications-pdb   N/A             1                 1                     10m
poddisruptionbudget.policy/payments-pdb        1               N/A               1                     10m
```

Final Gateway placement:

```text
NAME                       READY   STATUS    RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
gateway-59c65c6bbd-44lbx   1/1     Running   0          3m56s   10.42.0.68   k3d-quickticket-server-0   <none>           <none>
gateway-59c65c6bbd-5gg8m   1/1     Running   0          4m1s    10.42.0.67   k3d-quickticket-server-0   <none>           <none>
gateway-59c65c6bbd-cvl6n   1/1     Running   0          4m6s    10.42.0.66   k3d-quickticket-server-0   <none>           <none>
gateway-59c65c6bbd-p2bmr   1/1     Running   0          4m11s   10.42.0.65   k3d-quickticket-server-0   <none>           <none>
gateway-59c65c6bbd-wxgh6   1/1     Running   0          4m17s   10.42.0.64   k3d-quickticket-server-0   <none>           <none>
```

Before M1 (after the index migration):

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

After M3:

```text
Table "public.events"
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
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)

     version_num
---------------------
 12d_drop_event_date
(1 row)

 id |         name         |      scheduled_at
----+----------------------+------------------------
  1 | Go Conference 2026   | 2026-09-15 09:00:00+00
  2 | SRE Meetup           | 2026-10-01 18:00:00+00
  3 | Cloud Native Summit  | 2026-11-20 10:00:00+00
  4 | Python Workshop      | 2026-09-22 14:00:00+00
  5 | Kubernetes Deep Dive | 2026-10-10 10:00:00+00
  6 | Lab 9 Load Event     | 2026-12-01 12:00:00+00
(6 rows)
```

The backfill equality check returned `t` for every one of the six events;
see [backfill-data.log](../labs/lab12/evidence/backfill-data.log).

## Bonus migration upgrade bodies

[`12b_add_scheduled_at_add_events_scheduled_at.py`](../migrations/versions/12b_add_scheduled_at_add_events_scheduled_at.py):

```python
def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.add_column("events", sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True))
```

[`12c_backfill_scheduled_at_backfill_events_scheduled_at.py`](../migrations/versions/12c_backfill_scheduled_at_backfill_events_scheduled_at.py):

```python
def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
```

[`12d_drop_event_date_drop_events_event_date.py`](../migrations/versions/12d_drop_event_date_drop_events_event_date.py):

```python
def upgrade():
    op.execute("SET LOCAL lock_timeout = '2s'")
    op.drop_column("events", "event_date")
```

## Validation and reproduction

All of the following completed successfully:

- API-server validation of every final `k8s/` object with `--dry-run=server`.
- Python compilation and YAML parsing; Alembic has one linear head,
  `12d_drop_event_date`.
- The Gateway canary AnalysisRun `gateway-59c65c6bbd-9-2`: **Successful**.
- Both live B replicas and the Gateway returned HTTP 200 on `/health`, `/events`
  and `/events/1`; the response still has `date` with the original timestamp.
- Disposable PostgreSQL databases: legacy baseline → concurrent index (including
  reverse/reapply) → M1 (legacy and A reads) → M2 (A and B reads) → M3 (B reads)
  → all new downgrades → re-upgrade; every relevant application version reads
  five original events and preserves the event 1 date.
- Fresh final `seed.sql` → stamp head → B reads. Both disposable databases were
  dropped in `finally` blocks; the live database was never rolled back.

Evidence: [server validation](../labs/lab12/evidence/server-validation-final.log),
[endpoint smoke checks](../labs/lab12/evidence/endpoint-smoke.log),
[canary analysis](../labs/lab12/evidence/final-analysis.log),
[migration validation](../labs/lab12/evidence/migration-validation.log).

To rerun the isolated migration/schema checks (requires CREATE DATABASE,
Alembic dependencies and the Events Python requirements in the environment):

```bash
python3 -m venv .venv
.venv/bin/pip install -r migrations/requirements.txt -r app/events/requirements.txt
kubectl port-forward svc/postgres 15432:5432
# Separate terminal:
DATABASE_URL=postgresql://quickticket:quickticket@127.0.0.1:15432/quickticket \
  .venv/bin/python scripts/check_lab12_migrations.py
```

For a new **legacy-schema** rehearsal cluster, install Argo Rollouts/Prometheus
and the Lab 7 AnalysisTemplate first, bootstrap the pre-existing schema from
`git show cf2ad63:app/seed.sql`, then migrate through `ddd061115f64`. Existing
Lab 9 databases must retain their recorded revision; do not stamp them past
unexecuted migrations. Use an available high-capacity test event and adjust the
ID in mixedload; this run used the pre-existing **Lab 9 Load Event**, ID 6.

Build A from the preserved source without switching this branch:

```bash
stage_a=$(mktemp -d)
cp app/events/Dockerfile app/events/requirements.txt "$stage_a/"
git show 9815c67:app/events/main.py > "$stage_a/main.py"
docker build -t quickticket-events:lab12-a "$stage_a"
docker build -t quickticket-events:lab12-b app/events
k3d image import -c quickticket quickticket-events:lab12-a quickticket-events:lab12-b
```

Build/import `quickticket-gateway:lab12` and `quickticket-notifications:v1` from
their matching application directories if absent. The Payments manifest uses
the existing pinned Lab 5 GHCR image. Apply the Gateway, Payments,
Notifications and PDB manifests, scale the legacy Events deployment to two,
and keep mixedload running. **Do not apply the final B Events manifest to a
legacy schema before the backfill.** Execute individual revisions, never a
single `upgrade head` across the live mixed-version interval:

```bash
.venv/bin/alembic upgrade 12a_index_event_date
# Capture the index and baseline metrics here.
.venv/bin/alembic upgrade 12b_add_scheduled_at
sed 's/quickticket-events:lab12-b/quickticket-events:lab12-a/' k8s/events.yaml \
  | kubectl apply -f -
kubectl rollout status deployment/events --timeout=180s
.venv/bin/alembic upgrade 12c_backfill_scheduled_at
kubectl apply -f k8s/events.yaml
kubectl rollout status deployment/events --timeout=180s
# Verify all Events pods are B, Ready, and no A/terminating pod remains.
.venv/bin/alembic upgrade 12d_drop_event_date
```

Set `DATABASE_URL` to the forwarded port for each Alembic invocation. Capture
checkpoints with `python3 scripts/lab12_capture.py snapshot-<label>`, and use
`pdb-eviction` / `kill` for the corresponding experiments. Gateway restart is
`kubectl argo rollouts restart gateway` with the official plugin installed;
`python3 scripts/lab12_wait_gateway.py` checks generation, readiness and the
restart's pod timestamps. The exact commands used in this run are saved in the
linked logs.

For a fresh **final-schema** deployment, apply Postgres/Redis and wait for
readiness, load `app/seed.sql` into the empty database once, stamp the verified
final schema at `head`, then apply the final application manifests with their
images available. Do not run old-schema migration upgrades against an already
final-schema seed. The isolated fresh-seed check exercises this bootstrap path.

The optional ungraded HPA observation (12.9) was not performed. No claim of
multi-node placement/failure survival or production-scale backfill performance
is made. After the measurements and smoke checks, mixedload was scaled to zero
and the temporary PostgreSQL port-forward closed; application replicas, PDBs
and the final schema remain in place.
