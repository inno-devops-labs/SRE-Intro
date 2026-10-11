# Lab 9 — Stateful Services & DB Reliability

## Setup

### Fixing the environment first

As in Lab 8, the cluster had been restarted, and the database was empty again:

```
$ kubectl exec deploy/postgres -- psql -U quickticket -d quickticket -c '\dt'
Did not find any relations.
```
```
$ kubectl get pods -l app=events
events-7dc5cfc745-ffrnx   0/1     Running   0          6d16h
$ kubectl logs deploy/events
psycopg2.errors.UndefinedTable: relation "events" does not exist
```

The `events` readiness probe (`GET /events`) was returning 500, so the pod stayed `0/1`. This is the same no-PVC problem the lab warns about. I re-seeded the same way as in Lab 8. I raised `total_tickets` for event 1 again so that `mixedload` keeps producing real `/reserve` + `/pay` traffic and `orders` keeps growing:

```bash
kubectl exec -i deploy/postgres -- psql -U quickticket -d quickticket < app/seed.sql
kubectl exec deploy/postgres -- psql -U quickticket -d quickticket \
  -c "UPDATE events SET total_tickets=1000000 WHERE id=1;"
kubectl exec deploy/redis -- redis-cli DEL event:1:held
```

About 20 s later `events` was `1/1`, and `orders` was already filling up (146 rows).

### Python / Alembic

`python3 -m venv .venv` failed on this host because `ensurepip` is missing (the `python3.12-venv` package isn't installed). I didn't want to install system packages, so I created the venv without pip and installed into it with the system pip (24.0 supports `--python`):

```bash
python3 -m venv --without-pip .venv
pip3 --python .venv/bin/python install alembic==1.18.4 psycopg2-binary==2.9.11 sqlalchemy==2.0.49
.venv/bin/alembic --version     # alembic 1.18.4
```

```bash
kubectl port-forward svc/postgres 5432:5432 &
.venv/bin/python3 -c "import psycopg2; ...SELECT count(*) FROM events..."
# events: 5
```

Prometheus queries go through the same small wrapper as in Lab 8, around `kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=…'`.

---

## Task 1 — Migrations & Backup/Restore

### 9.1–9.2: Alembic init + baseline

```bash
alembic init migrations
# alembic.ini:
#   sqlalchemy.url = postgresql://quickticket:quickticket@localhost:5432/quickticket
alembic revision -m "baseline - pre-existing schema"
alembic stamp head
alembic current
```
```
INFO  [alembic.runtime.migration] Running stamp_revision  -> 4ca253cd1007
4ca253cd1007 (head)
```

The baseline revision is empty on purpose. `seed.sql` already created `events` and `orders`, and `stamp head` only writes `4ca253cd1007` into `alembic_version` without running any DDL.

### 9.3: The migration

`migrations/versions/be711dc39c96_add_email_column_to_events.py`:

```python
def upgrade() -> None:
    # Adding a nullable column is a metadata-only change in PostgreSQL 11+ —
    # no table rewrite, no blocking lock on SELECT/INSERT. Safe under load.
    op.add_column('events', sa.Column('email', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'email')
```

**`alembic history`:**

```
4ca253cd1007 -> be711dc39c96 (head), add email column to events
<base> -> 4ca253cd1007, baseline - pre-existing schema
```

### 9.4: Migration under load

`mixedload` from Lab 8 was running (`2/2`). Baseline at **16:48:52**:

```
sum(increase(gateway_requests_total{status=~"5.."}[1m]))   => 0     (5xx last 1min)

sum by (path,status) (rate(gateway_requests_total[1m]))
   /events               200 => 5.67
   /events/{id}/reserve  200 => 5.58
   /reserve/{id}/pay     200 => 5.75
```

(The 502/504 series from the empty-DB period before the re-seed were still present, but their 1-minute rate had already dropped to 0.)

**`time alembic upgrade head`**, started at 16:48:58.426:

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 4ca253cd1007 -> be711dc39c96, add email column to events

real	0m0.547s
user	0m0.436s
sys	0m0.055s
```

Most of the 0.55 s is Python/Alembic startup and the round trip through the port-forward. The `ALTER TABLE` itself only takes a brief `ACCESS EXCLUSIVE` lock to update the catalog and does not rewrite the table.

**`\d events` after the migration:**

```
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
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

**Error rate after the migration**, at **16:50:09** (~70 s later, so the 1-minute and 2-minute windows both fully cover the migration):

```
sum(increase(gateway_requests_total{status=~"5.."}[1m]))   => 0
sum(increase(gateway_requests_total{status=~"5.."}[2m]))   => 0

   /events               200 => 5.65
   /events/{id}/reserve  200 => 5.75
   /reserve/{id}/pay     200 => 5.64
```

| | before (16:48:52) | after (16:50:09) |
|---|---|---|
| 5xx last 1 min | 0 | 0 |
| total RPS (3 API paths) | 17.0 | 17.0 |

There were no additional 5xx and no throughput change. Another reason the column is invisible to the app: `events` selects explicit columns (`SELECT e.id, e.name, e.venue, …`) instead of `SELECT *`.

### 9.5: pg_dump backup

```bash
kubectl exec -i deploy/postgres -- pg_dump -U quickticket -Fc quickticket > quickticket.dump   # 16:50:16
ls -lh quickticket.dump
file quickticket.dump
```
```
-rw-rw-r-- 1 georgiy georgiy 45K Oct 11 16:50 quickticket.dump
quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

(I kept the dump in a local scratch directory instead of `/tmp/quickticket.dump`. Only the location differs.)

```bash
POD=postgres-78489d7f5f-8h7nc
kubectl cp quickticket.dump $POD:/tmp/backup.dump
kubectl exec $POD -- pg_restore --list /tmp/backup.dump
```
```
;
; Archive created at 2026-10-11 13:50:16 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
;     Integer: 4 bytes
;     Offset: 8 bytes
;     Dumped from database version: 17.11
;     Dumped by pg_dump version: 17.11
;
;
; Selected TOC Entries:
;
220; 1259 16413 TABLE public alembic_version quickticket
218; 1259 16389 TABLE public events quickticket
217; 1259 16388 SEQUENCE public events_id_seq quickticket
3481; 0 0 SEQUENCE OWNED BY public events_id_seq quickticket
219; 1259 16397 TABLE public orders quickticket
3316; 2604 16392 DEFAULT public events id quickticket
3474; 0 16413 TABLE DATA public alembic_version quickticket
3472; 0 16389 TABLE DATA public events quickticket
3473; 0 16397 TABLE DATA public orders quickticket
3482; 0 0 SEQUENCE SET public events_id_seq quickticket
3324; 2606 16417 CONSTRAINT public alembic_version alembic_version_pkc quickticket
3320; 2606 16396 CONSTRAINT public events events_pkey quickticket
3322; 2606 16405 CONSTRAINT public orders orders_pkey quickticket
3325; 2606 16406 FK CONSTRAINT public orders orders_event_id_fkey quickticket
```

The TOC is valid: all 3 tables, the data for each of them, the sequence, the PKs and the FK. `alembic_version` is included as well, so a restored DB also knows which migration it is at.

### 9.6: Data loss → restore

Everything ran in one go, under the same `mixedload` traffic:

```
== BEFORE 16:50:27
 events: 5
 orders: 1162

== DROP 16:50:27.933
DROP TABLE

== AFTER DROP 16:50:28
 events: 5
ERROR:  relation "orders" does not exist

/events=502
/events/1/reserve=500

== RESTORE START 16:50:33.223
pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump   -> rc=0
== RESTORE END   16:50:33.516

== AFTER RESTORE 16:50:33
 events: 5
 orders: 1100

/events=200
/events/1/reserve=200
```

**Row counts:**

| | events | orders |
|---|---:|---:|
| Before disaster (16:50:27) | 5 | 1162 |
| After `DROP TABLE orders CASCADE` | 5 | — (relation does not exist) |
| After `pg_restore` (16:50:33) | 5 | **1100** (1099 from the dump + 1 new order) |

- **Data lost: 63 orders.** The dump contains exactly 1099 `orders` rows (checked later with `pg_restore -a -t orders -f - backup.dump`). The count of 1100 already includes one order that `mixedload` created in the moment between the restore and my `SELECT`. 1162 − 1099 = 63 rows were written between the dump (16:50:16) and the drop (16:50:27). That is about 11 s at ~5.7 reservations/s, which matches. The restore can only return what was in the dump. In this run the RPO was 11 s, or 63 rows.
- **Outage:** about 5.6 s from the DROP to the end of the restore. Most of that was me running the smoke checks. `pg_restore` itself took 0.29 s. The restored DB is still at migration `be711dc39c96` (`alembic_version` and the `email` column both came back).
- **Recovery without restarts:** `events` didn't need a restart because it never lost its connections; only one table disappeared. 60 s later `orders` had grown to 1440, `5xx rate[30s] = 0`, and all pods were `1/1` with 0 restarts.

**What the users saw, and what Prometheus missed.** At 16:51:33, `increase(gateway_requests_total{status=~"5.."}[2m])` showed only **~34× `/events` 502** for the incident. During the DROP window `events` returned 500 for both `GET /events` and `POST /reserve`, yet there was no `/events/{id}/reserve` 5xx at all, even though my own smoke request got `500`. The gateway logs explain it:

```
$ kubectl logs -l app=gateway --since=150s | grep -c 'JSONDecodeError: Expecting value'
32
    raise HTTPException(e.response.status_code, e.response.json())
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
ERROR:    Exception in ASGI application
```

The two gateway routes handle an upstream 500 differently. `list_events` catches every exception and maps it to a clean 502, which is counted. In `reserve_tickets`, the `HTTPStatusError` handler calls `e.response.json()`. `events` returned a non-JSON `Internal Server Error` body, so that call raised a second exception inside the `except` block. That exception escaped the route, `metrics_middleware` never reached `REQUEST_COUNT.labels(...).inc()`, and Starlette produced a bare 500. **32 failed reservations never appeared in `gateway_requests_total`.** An alert based on gateway 5xx would have undercounted this incident by about half. The same Lab 8 lesson applies here: error metrics measured inside the process are blind to the failures that crash the handler. The fix would be to use `e.response.text` the way `get_event` already does (or wrap the JSON parse in a `try`), and to record the metric in a `try/finally` in the middleware.

(A smaller trap: `/reserve/{id}/pay` 500 was a *new* series created in this window, with a raw counter of 1. `increase()` shows 0 for a series whose first sample is already non-zero, so one failed `/pay` is also missing from the windowed query.)

### 9.7: RPO of the current setup

**What's the RPO of a single `pg_dump`?** The RPO equals the time since the last dump, and it grows without limit. Right after the dump it's ~0. In my run it was 11 s / 63 orders in Task 1, and 3.5 min / 1102 orders in Task 2. With a manual dump, nobody re-runs it, so an hour later it's an hour of orders, and a week later a week. There are two more weak points:
1. The dump was stored in only two places: my laptop, and `/tmp` inside the Postgres pod itself. The copy in the pod has no PVC and dies together with the database it's meant to protect. If the laptop copy is missing, the effective RPO in a pod-loss scenario is **everything**.
2. Nobody tests the dump regularly. A backup that has never been restored is a hope, not a backup. This run is the first proof that this particular dump restores.

**How to improve it:**
- **Schedule the dumps** (the Bonus Task): a CronJob running `pg_dump -Fc` every 5 min onto its own PVC, with retention (keep the newest N). That caps the RPO at ≤ 5 min plus the dump duration.
- **Keep the backups off the failure domain:** the backup PVC must not live in the Postgres pod, and in production the backups should go to object storage in another zone or region.
- **Give Postgres itself a PVC**, so that a pod restart (what happened before this lab) is no longer a data-loss event at all and needs no restore.
- **For RPO in seconds:** continuous WAL archiving / PITR (e.g. `pgBackRest`, `wal-g`, or the CloudNativePG operator), or a streaming replica. That moves the RPO from "minutes since the last dump" to "the last few seconds of WAL".
- **Test restores automatically:** periodically restore the latest dump into a scratch DB and check row counts.

---

## Task 2 — Disaster Recovery Under Load

### 9.8: Kill Postgres and recover

`mixedload` was running the whole time. Baseline at 16:53:39: `sum(rate(gateway_requests_total{status=~"5.."}[30s])) => 0`, total ≈ 17 RPS. `k8s/postgres.yaml` has `replicas: 1` and no `volumes`/`volumeMounts`, so the data lives in the container's writable layer.

As the backup I used **the same dump from Task 1 (16:50:16)**. I didn't take a fresh one right before the kill: a backup that is a few minutes old is the realistic case, and it gives a real RPO gap instead of ~0.

I ran the script from the lab with millisecond timestamps added:

```
2201
healthy at 16:53:47.208
pod "postgres-78489d7f5f-8h7nc" force deleted from default namespace
pod/postgres-78489d7f5f-nnttw condition met                        <- "Ready" at 16:53:49.665
new pod: postgres-78489d7f5f-nnttw
psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: No such file or directory
	Is the server running locally and accepting connections on that socket?
pg_restore: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: No such file or directory
restore rc=1
deployment.apps/events restarted
Waiting for deployment "events" rollout to finish: 1 old replicas are pending termination...
error: timed out waiting for the condition
ERROR:  relation "orders" does not exist
```

**The scripted runbook failed.** `kubectl wait --for=condition=Ready` returned 1.7 s after the kill, but Postgres wasn't accepting connections yet. The `postgres` container has **no readinessProbe**, so "Ready" only means "the container process has started", and `initdb` + startup were still in progress. The script carried on: `\dt` and `pg_restore` both hit a closed socket, and `pg_restore` exited with rc=1. Then `events` was restarted against a DB without tables, so its new pod never became Ready and the rollout timed out after 60 s. Only at that point did I see the problem.

Second attempt, this time gated on Postgres actually accepting connections:

```bash
until kubectl exec $NEW_POD -- pg_isready -U quickticket -q; do sleep 0.5; done   # OK on first try, 16:54:57.492
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
kubectl exec $NEW_POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
kubectl rollout status deployment/events --timeout=90s
```
```
pg_isready OK 16:54:57.492
Did not find any relations.                       <- the new pod is EMPTY
restore rc=0 16:54:57.946
1099                                              <- orders after restore
events-74fff5bd8f-khfww   0/1     Running   0     68s
deployment "events" successfully rolled out       <- 16:54:58.764
```

The new `events` pod (created by the `rollout restart` at ~16:53:51) had been failing its readiness probe for 68 s. It became Ready within a second of the restore, and the rollout completed.

Smoke check at 16:55:41:

```
/events=200
/events/1/reserve=200
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
alembic_version: be711dc39c96
```

### 9.9: RTO and RPO

**Timeline:**

| Phase | Time | Since kill |
|---|---|---:|
| Healthy (T0), orders = 2201 | 16:53:47.208 | — |
| **Disaster** (force delete) | 16:53:47.936 | 0 s |
| **New pod "Ready"** (container started, Postgres not yet accepting connections) | 16:53:49.665 | 1.7 s |
| First restore attempt fails (`rc=1`, no socket) | 16:53:50.758 | 2.8 s |
| Postgres accepting connections (`pg_isready` OK on the first check of the 2nd attempt) | 16:54:57.492 | 69.6 s |
| **Restored** (`pg_restore` rc=0) | 16:54:57.946 | 70.0 s |
| **App fully up** (`events` rollout complete) | 16:54:58.764 | **70.8 s** |

- **Actual RTO = 16:54:58.764 − 16:53:47.936 = 70.8 s.**
- Almost all of it was the broken runbook: ~67 s passed between the failed restore and me noticing it and running the second attempt. The mechanical work (pod rescheduling, Postgres startup, a 0.45 s restore, `events` becoming Ready) fits into a few seconds. Postgres was ready at the very first `pg_isready` check, so I only know an upper bound for its startup time. A runbook gated on `pg_isready` from the start would have given an RTO of roughly **5–10 s**. **The RTO was set by the procedure, not by the infrastructure.**

**RPO:**

| | orders |
|---|---:|
| Before disaster (T0 16:53:47) | 2201 |
| After restore (16:54:57) | 1099 |
| **Lost (RPO gap)** | **1102** |

- **Actual RPO = 16:53:47.9 − 16:50:16 (time of the dump) ≈ 3 min 32 s.** The 1102 lost orders (plus a few that were created in the 0.7 s between T0 and the kill) are everything `mixedload` wrote since the backup: 212 s × ~5.2 orders/s ≈ 1100. For a real ticketing system, this means 1102 customers who paid and now have no order.
- The RPO doesn't depend on how fast we recover. Even an instant restore can't bring back what was never backed up.

**Prometheus error-rate curve.** This is a `query_range` over the incident with step 10 s. Each value is a 30 s `rate()`, so the curve lags behind the events by up to 30 s:

```
time      2xx/s   5xx/s  | /events 502 | /events 504 | /reserve 502 | /reserve 504
16:53:20  17.08    0.00  |  0.00 |  0.00 |  0.00 |  0.00
16:53:40  17.24    0.00  |  0.00 |  0.00 |  0.00 |  0.00
16:53:50  16.96    0.00  |  0.00 |  0.00 |  0.00 |  0.00     <- kill at 16:53:47.9
16:54:00  10.44    0.08  |  0.04 |  0.04 |  0.00 |  0.00
16:54:10   3.68    2.12  |  1.04 |  0.04 |  0.96 |  0.08
16:54:20   0.00    3.76  |  1.76 |  0.08 |  1.84 |  0.08     <- 0% success
16:54:30   0.00    4.40  |  2.20 |  0.04 |  2.12 |  0.04
16:54:40   0.00    4.88  |  2.44 |  0.04 |  2.36 |  0.04
16:54:50   0.00    4.68  |  2.36 |  0.00 |  2.28 |  0.04
16:55:00   0.00    3.48  |  1.64 |  0.04 |  1.80 |  0.00     <- restored at 16:54:57.9
16:55:10   5.48    1.96  |  0.92 |  0.04 |  1.00 |  0.00
16:55:20  12.24    0.88  |  0.40 |  0.04 |  0.44 |  0.00
16:55:30  16.72    0.00  |  0.00 |  0.00 |  0.00 |  0.00     <- back to baseline
```

```
sum(rate(gateway_requests_total{status=~"5.."}[30s]))   at 16:55:40  => 0
```

Raw counter deltas for the incident: `/events` 502 **+128**, `/events/{id}/reserve` 502 **+128**, `/events` 504 +4, `/reserve` 504 +3, `/reserve/{id}/pay` 500 +1. That is ~264 failed requests in ~71 s.

Two observations:
1. **Total throughput collapsed from 17 to ~5 RPS** instead of staying at 17 RPS with 100% errors. Every `mixedload` loop is sequential: failing calls are evidently slower than successful ones (a few even hit the gateway's 5 s timeout as 504s), and `/pay` is never reached because reserve failed. So the error *rate* (~4.9/s) understates how much of the normal workload was lost. The success ratio (0 %) is the honest SLI here.
2. This time the reserve failures *were* counted as clean 502s, unlike the uncounted 500s in Task 1. With Postgres completely gone, `events` couldn't respond at all (or its pod was unready and dropped from endpoints), so the gateway hit the generic `except Exception → 502` branch instead of the `HTTPStatusError → .json()` crash.

**The new Postgres pod was empty. Why? How would you eliminate this failure mode?**

`k8s/postgres.yaml` mounts no volume, so `PGDATA` (`/var/lib/postgresql/data`) is on the container's ephemeral writable layer, which is tied to the pod. `kubectl delete pod` destroys that layer. The Deployment creates a *new* pod from the same image, and its entrypoint finds an empty data directory and runs `initdb`. The result is a fresh cluster with the `quickticket` user and DB but no tables. We hit the same failure mode at the start of Lab 8 and again at the start of this lab: a simple node/cluster restart was a full data-loss event. Restore from backup is the only recovery path, and it inherits the backup's RPO (here 3.5 min / 1102 orders).

How to eliminate it:
- **A PersistentVolumeClaim for `PGDATA`** (Bonus Task). With a PVC, a pod kill becomes a plain restart: the new pod mounts the same volume, Postgres replays the WAL, and the data is there. No restore is needed and the RPO is ~0 for committed transactions. Better still, use a StatefulSet with `volumeClaimTemplates`. Also set `strategy: Recreate` on the Deployment so that two pods never mount the same RWO volume during a rollout.
- **A readinessProbe on Postgres** (`exec: pg_isready -U quickticket`). This would have made `kubectl wait --for=condition=Ready` mean what the runbook assumed, and it would have saved ~65 s of my RTO. It would also keep the Service from routing to a Postgres that isn't up yet.
- **Fix the runbook:** wait for `pg_isready`, check the `pg_restore` exit code, and stop on failure instead of running on into `rollout restart events`. My script printed "Restored" with a timestamp even though the restore had failed. A runbook that doesn't check its own steps produces timestamps that look like success.
- **For RPO:** scheduled `pg_dump` CronJob on separate storage (Bonus). For node/disk loss (a PVC on k3d's `local-path` is still a single node), add WAL archiving/PITR or a streaming replica, as described in 9.7.

---

## Bonus Task — Persistent Storage + Automated Backup CronJob

### B.1: PVC for Postgres

**Diff of `k8s/postgres.yaml`:**

```diff
diff --git a/k8s/postgres.yaml b/k8s/postgres.yaml
index 1524c5f..18a3652 100644
--- a/k8s/postgres.yaml
+++ b/k8s/postgres.yaml
@@ -4,6 +4,9 @@ metadata:
   name: postgres
 spec:
   replicas: 1
+  # RWO volume: never run old and new pod against the same PGDATA at once
+  strategy:
+    type: Recreate
   selector:
     matchLabels:
       app: postgres
@@ -24,6 +27,16 @@ spec:
               value: "quickticket"
             - name: POSTGRES_PASSWORD
               value: "quickticket"
+            - name: PGDATA
+              value: /var/lib/postgresql/data/pgdata  # subdir — avoid lost+found
+          volumeMounts:
+            - name: data
+              mountPath: /var/lib/postgresql/data
+          readinessProbe:
+            exec:
+              command: ["pg_isready", "-h", "127.0.0.1", "-U", "quickticket", "-d", "quickticket"]
+            periodSeconds: 2
+            failureThreshold: 3
           resources:
             requests:
               cpu: 50m
@@ -31,6 +44,20 @@ spec:
             limits:
               cpu: 200m
               memory: 256Mi
+      volumes:
+        - name: data
+          persistentVolumeClaim:
+            claimName: postgres-data
+---
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes: [ReadWriteOnce]
+  resources:
+    requests:
+      storage: 1Gi
 ---
 apiVersion: v1
 kind: Service
```

Besides the PVC and `PGDATA` from the lab, I added two more changes. Both come straight from what broke in Task 2:
- **`readinessProbe: pg_isready -h 127.0.0.1`.** Now `kubectl wait --for=condition=Ready` means "accepting connections", not "the process started". I used `-h 127.0.0.1` on purpose: on first init, the image's entrypoint runs a temporary server that listens only on the unix socket. A socket-based `pg_isready` would report Ready during `initdb`, and a TCP one doesn't.
- **`strategy: Recreate`.** With the default `RollingUpdate`, a template change starts the new Postgres pod *before* the old one stops. On k3d's single node, the `local-path` RWO volume can be mounted by both pods, so two postmasters would run against the same `PGDATA`. Recreate stops the old pod first.

**Migrating to the PV without re-seeding.** The lab suggests re-seeding the fresh PV from `seed.sql`. That would have thrown away the Alembic migration and ~2200 orders, so instead I did a dump → switch → restore:

```
dump   16:58:08.648   kubectl exec deploy/postgres -- pg_dump -Fc > migrate.dump    (83K)
apply  16:58:09.344   kubectl apply -f k8s/postgres.yaml
         deployment.apps/postgres configured
         persistentvolumeclaim/postgres-data created
rolled out 16:58:33.638
postgres-data   Bound    pvc-548da7bc-…   1Gi   RWO   local-path
\dt on new pod  -> Did not find any relations.      (fresh PV, as expected)
pg_restore      -> rc=0  16:58:34.596
events rollout  -> 16:58:42.644
orders = 2195, alembic_version = be711dc39c96, PGDATA = /var/lib/postgresql/data/pgdata
```

This planned migration cost ~33 s of downtime. Most of it was the `Recreate` rollout, including first-time PV provisioning by `local-path`.

**Disaster test from 9.8 repeated with the PVC** (same script, `mixedload` running):

```
healthy at 16:59:41.417 orders=2518
pod "postgres-598c57c4cf-57bch" force deleted from default namespace
pod/postgres-598c57c4cf-jthmd condition met
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+-------------
 public | alembic_version | table | quickticket
 public | events          | table | quickticket
 public | orders          | table | quickticket
orders after restart=2520

$ kubectl logs -l app=postgres
PostgreSQL Database directory appears to contain a database; Skipping initialization
… LOG:  database system was shut down at 2026-10-11 13:59:41 UTC
… LOG:  database system is ready to accept connections          (13:59:43.634 UTC)
```

The new pod found the existing cluster on the PV, skipped `initdb`, and was accepting connections 2 s after the kill. No `pg_restore` was needed.

**I tested whether the `events` restart is actually necessary.** This time I did *not* run `rollout restart deployment/events` straight away. Gateway request deltas from Prometheus, 5 s buckets, health checks excluded:

```
time       Δ2xx  Δ5xx
16:59:35     80     0
16:59:40     84     0       <- kill at 16:59:41.8, Postgres ready 16:59:43.9
16:59:45     65     1
16:59:50      1     2
16:59:55      0     7
17:00:00      0    37
17:00:05     62     5       <- events serving again, without any restart
17:00:10     80     0
17:00:15     80     0
17:00:20     91     2   \
17:00:25     76     4   /   <- my (unnecessary) events rollout restart at ~17:00:06
```

Postgres was back in 2 s, but `events` kept failing for another ~20 s. Its `ThreadedConnectionPool` still held connections to the dead Postgres (`This probably means the server terminated abnormally` in its logs), and its readiness probe (`GET /events`, `failureThreshold: 2`) took it out of the Service. psycopg2's pool discards connections that are already closed when they're returned, so after the stale connections had failed once, the pool reconnected by itself. At 17:00:05 `events` was `1/1` again, before I got to the restart. The `rollout restart` I ran afterwards was not needed, and it even produced 6 extra 5xx of its own during the pod swap. So the "reconnect events" step in the runbook is optional here. A pool with pre-ping/validation on checkout would shorten those ~20 s to ~0.

**RTO/RPO comparison:**

| | Task 2 (no PVC) | Bonus (PVC) |
|---|---:|---:|
| Postgres accepting connections after kill | ~2 s (wasn't measured, broken probe) | **2.1 s** |
| Restore needed | yes (`pg_restore` from a 3.5-min-old dump) | **no** |
| App serving again (RTO) | **70.8 s** | **≈ 20 s** (Prometheus 5 s resolution: errors 16:59:45–17:00:00) |
| Orders before → after | 2201 → 1099 | 2518 → 2520 |
| **Data lost (RPO)** | **1102 orders / 3 min 32 s** | **0** |
| 5xx during incident | ~264 | ~52 |

The biggest gain is the RPO. A pod kill is no longer a data-loss event: every committed transaction is on the PV. The remaining RTO isn't Postgres any more (it's back in 2 s). It's the `events` connection pool healing itself.

### B.2: Automated backup CronJob

`kubectl apply -f labs/lab9/backup-storage.yaml` → `postgres-backups` PVC (`Bound`, 1Gi, `local-path`) + `backup-inspector`.

**`k8s/backup-cronjob.yaml`:**

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "*/5 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 2
      template:
        metadata:
          labels:
            app: postgres-backup
        spec:
          restartPolicy: OnFailure
          containers:
            - name: pg-dump
              image: postgres:17-alpine
              env:
                - name: PGHOST
                  value: "postgres"
                - name: PGUSER
                  value: "quickticket"
                - name: PGDATABASE
                  value: "quickticket"
                - name: PGPASSWORD
                  value: "quickticket"
              command:
                - /bin/sh
                - -c
                - |
                  set -eu
                  cd /backups
                  FILE="quickticket_$(date -u +%Y%m%dT%H%M%SZ).dump"
                  # Dump to a temp name first, so a failed/partial dump never
                  # counts as one of the 5 "newest" backups during retention.
                  pg_dump -Fc -f "$FILE.partial"
                  mv "$FILE.partial" "$FILE"
                  echo "backup written: $FILE ($(wc -c < "$FILE") bytes)"
                  # Retention: keep the 5 newest dumps, delete the rest.
                  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm -v
                  echo "backups kept: $(ls -1 quickticket_*.dump | wc -l)"
              volumeMounts:
                - name: backups
                  mountPath: /backups
              resources:
                requests:
                  cpu: 50m
                  memory: 64Mi
                limits:
                  cpu: 200m
                  memory: 256Mi
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Design choices beyond the requirements:
- **`pg_dump` writes to `*.partial`, then `mv`.** With `set -eu`, a failed dump exits before the `mv`. So a truncated file never gets a `quickticket_*.dump` name, and retention can't keep a broken dump while deleting a good one.
- **`rm -v`**, so the retention log shows exactly what was deleted.
- **`backoffLimit: 2`**, so a broken backup fails fast and shows up in `failedJobsHistoryLimit` instead of retrying 6 times.

First manual run:

```
$ kubectl create job --from=cronjob/postgres-backup manual-1
$ kubectl wait --for=condition=Complete job/manual-1 --timeout=90s
job.batch/manual-1 condition met
$ kubectl logs job/manual-1
backup written: quickticket_20261011T140143Z.dump (119179 bytes)
backups kept: 1
```

**Retention, runs 2–7:**

```
--- manual-2 logs:
backup written: quickticket_20261011T140150Z.dump (120698 bytes)
backups kept: 2
--- manual-3 logs:
backup written: quickticket_20261011T140154Z.dump (121396 bytes)
backups kept: 3
--- manual-4 logs:
backup written: quickticket_20261011T140158Z.dump (122196 bytes)
backups kept: 4
--- manual-5 logs:
backup written: quickticket_20261011T140202Z.dump (122935 bytes)
backups kept: 5
--- manual-6 logs:
backup written: quickticket_20261011T140205Z.dump (123603 bytes)
removed 'quickticket_20261011T140143Z.dump'
backups kept: 5
--- manual-7 logs:
backup written: quickticket_20261011T140208Z.dump (124262 bytes)
removed 'quickticket_20261011T140150Z.dump'
backups kept: 5
```

**`kubectl exec deployment/backup-inspector -- ls -la /backups` after 7 runs:**

```
total 620
drwxrwxrwx    2 root     root          4096 Oct 11 14:02 .
drwxr-xr-x    1 root     root          4096 Oct 11 14:01 ..
-rw-r--r--    1 root     root        121396 Oct 11 14:01 quickticket_20261011T140154Z.dump
-rw-r--r--    1 root     root        122196 Oct 11 14:01 quickticket_20261011T140158Z.dump
-rw-r--r--    1 root     root        122935 Oct 11 14:02 quickticket_20261011T140202Z.dump
-rw-r--r--    1 root     root        123603 Oct 11 14:02 quickticket_20261011T140205Z.dump
-rw-r--r--    1 root     root        124262 Oct 11 14:02 quickticket_20261011T140208Z.dump
```

Exactly 5 files: runs 1 and 2 were rotated out. The job history limit also works for jobs created with `--from=cronjob`, because they carry the CronJob's ownerReference. After 7 runs `kubectl get jobs` lists only `manual-5/6/7`.

**The backup is actually restorable.** I copied the newest dump out of the backup PVC and listed it in the Postgres pod:

```
; Archive created at 2026-10-11 14:02:08 UTC
3471; 0 16385 TABLE DATA public alembic_version quickticket
3472; 0 16388 TABLE DATA public events quickticket
3474; 0 16394 TABLE DATA public orders quickticket
orders rows in dump: 3227
```

**The schedule works on its own.** I waited for the first scheduled run instead of trusting the manual ones:

```
$ kubectl logs job/postgres-backup-29862125
backup written: quickticket_20261011T140500Z.dump (158092 bytes)
removed 'quickticket_20261011T140154Z.dump'
backups kept: 5

$ kubectl get cronjob postgres-backup
NAME              SCHEDULE      TIMEZONE   SUSPEND   ACTIVE   LAST SCHEDULE   AGE
postgres-backup   */5 * * * *   <none>     False     0        4s              3m22s
```

With this, the backup RPO is capped at **≤ 5 min**, compared to "since whenever someone last ran `pg_dump` by hand". The 5 dumps × 5 min give a 25-minute recovery window. That is enough for an accidental `DROP TABLE`, but too short for corruption that is noticed hours later; for that, add a daily/weekly tier.

**What's still weak:** `postgres-data` and `postgres-backups` are both `local-path` volumes on the **same k3d node, on the same disk**. A pod kill is covered now, and an accidental `DROP` can be restored with ≤ 5 min of loss. Losing the node or disk would still take out the DB *and* all its backups together. The real next step is to ship dumps off-node (object storage in another zone), add WAL archiving for second-level RPO, and run an automated restore test that checks the dump against a scratch DB on a schedule.
