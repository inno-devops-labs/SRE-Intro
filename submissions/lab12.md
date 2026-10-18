# Lab 12: Advanced Kubernetes Resilience

## Environment, baseline and backup

All experiments below ran on 2026-10-18 UTC (2026-10-19 locally in Moscow).
This branch started at fork feature/lab11, commit `75bb615`. The local Lab 12
specification was compared with `origin/main`: submission placement differed,
not the experiment requirements. Labs 1–11 results were not rewritten.

Windows PowerShell 5, Docker Desktop 4.61.0 / Docker 29.2.1, kubectl 1.34.1,
k3d 5.9.0, ArgoCD 3.5.3 and Rollouts plugin 1.10.1 were used. The existing
single-node cluster and both PVCs were retained.

```text
kubectl get nodes -o wide
NAME                       STATUS   ROLES           VERSION        INTERNAL-IP
k3d-quickticket-server-0    Ready    control-plane   v1.35.5+k3s1   172.30.0.3
```

Baseline at 22:08:27.612154Z: gateway, events, payments and notifications health
returned HTTP 200. PostgreSQL/Redis checks were ok; payments and notifications
fault injection was 0.0/0. Reservation `1cf1a7ff-700a-474f-947f-faf4c009b922`
was confirmed and its notification was sent at 22:08:27.835034656Z.

PostgreSQL was **already persistent**, version 17.11; both `postgres-data` and
`postgres-backups` were Bound, 1Gi, local-path/RWO. The enabled five-minute
backup CronJob and monitoring were kept. Current Alembic was `9a0002`.
The five event dates are saved before/after in [schema evidence](evidence/lab12/schemas.json).

Node capacity was 12 CPUs / 7,797,980Ki memory. Allocated requests were 700m CPU
and 980Mi memory; adding one Events, Payments and Notifications pod requests
150m/192Mi more. DB max_connections was 100: two Events pools at DB_MAX_CONNS=10
allow 20 connections, or 30 with one rollout surge, leaving capacity for backups,
migrations and administration. I did not double pool size.

Inventory was inherited from the repaired Lab 11 test state: event 1 total
100,000; events 3/5 total 10,000 each. Existing orders/holds were preserved,
with no Redis flush, order deletion or reseeding of the working database.

### Verified external backups

Commands used the discovered PostgreSQL pod, not a hardcoded container name:

```powershell
$pg=(kubectl get pod -l app=postgres -o json | ConvertFrom-Json).items[0].metadata.name
kubectl exec $pg -- pg_dump -U quickticket -d quickticket -Fc -f /tmp/lab12-before.dump
kubectl exec $pg -- pg_restore --list /tmp/lab12-before.dump
# Run kubectl cp from the external evidence directory with a relative local path:
kubectl cp "${pg}:/tmp/lab12-before.dump" ./lab12-before.dump
Get-FileHash ./lab12-before.dump -Algorithm SHA256
```

| Backup | UTC start / completed isolated restore | Host bytes | Snapshot counts events/orders | Restored revision |
|---|---|---:|---|---|
| Before schema changes | 22:10:04.5236753 / verified by 22:12:02 | 335777 | 5 / 8952 | 9a0002 |
| First pre-contract | 22:26:48.0158838 / 22:26:52.1486905 | 561646 | 5 / 15054 | 12a0003 |
| Repeat pre-contract | 22:33:58.0999739 / 22:34:02.3200027 | 666478 | 5 / 17896 | 12a0003 |

The counts are from each isolated restored snapshot, not a comparison with
the continually changing working order count. TOCs had 18, 19 and 19 entries.

```text
SHA256 lab12-before.dump:
1551956935472AADEADEDE64D030CA4B844C97259E307771FDB6CFCD72A2422E
SHA256 lab12-precontract.dump:
AF452AB01B6FDD3B786D4EDFE127F70ABA98EDA62710798BD45A49578B1D48C7
SHA256 lab12-repeat-precontract.dump:
50583F7E7C539A406E786E3996E0BAFA5317D7B532A799337831C4A44FB1AB50

pg_restore --list /tmp/lab12-repeat-precontract.dump
Archive created at 2026-10-18 22:33:58 UTC
TOC Entries: 19
Format: CUSTOM
Dumped from database version: 17.11
TABLE public alembic_version
TABLE public events
TABLE public orders
INDEX public idx_events_event_date
```

Host and pod SHA256 matched. Each dump was restored with
`pg_restore --exit-on-error` into a separate `lab12_*` database. Dumps and
the 6.9 MB full observer trace remain outside Git in the host temporary evidence
directory, not in the repository. A first Windows absolute-destination
`kubectl cp` failed because of the drive colon; using the external directory
and relative destination fixed it. No working data was restored over.

### Measurement method and GitOps

`labs/lab8/mixedload.yaml` ran with two replicas throughout the main tests.
An independent in-cluster [observer](../scripts/lab12/probe.py) repeatedly did
list → detail event 3 → reserve → pay, logging status, latency, UTC, bodies and
transport errors. It sampled pods/UIDs, EndpointSlices and per-pod raw counters
every approximately three seconds; the resilience runner polled every second.

[Window evidence a](evidence/lab12/windows-a.json),
[b](evidence/lab12/windows-b.json), [c](evidence/lab12/windows-c.json) and
[d](evidence/lab12/windows-d.json) preserve raw Prometheus query responses,
evaluation timestamps, per-pod/path/status increases, sample counts, resets,
counter first/last observations, pod state changes and endpoint changes.
The generated JSON is compact; it is not a replacement for the separate
client observations. [Lifecycle evidence](evidence/lab12/lifecycle.json)
retains exact original/deleted/new UIDs and polling outputs.

For each fixed start/end window, queries were evaluated at its end after
waiting six seconds for scraping (five-second scrape interval):

```promql
sum(increase(gateway_requests_total[WINDOWs]))
(sum(increase(gateway_requests_total{status=~"5.."}[WINDOWs])))
  or on() (0 * sum(increase(gateway_requests_total[WINDOWs])))
sum by(pod,path,status) (increase(gateway_requests_total[WINDOWs]))
sum by(pod) (resets(gateway_requests_total[WINDOWs]))
sum by(pod) (count_over_time(gateway_requests_total[WINDOWs]))
```

WINDOW is the ceiling of actual elapsed seconds, minimum 10 for the failed
CLI attempts. Extrapolated increases are fractional and include gateway
health traffic, so they are not exact mixedload checkout counts. A zero
fallback is accepted only alongside positive request volume and scrape samples,
never from an empty vector alone. A newly created error series can miss its
first increment; this happened in the first A deploy below. Disappearing pods
are separate series, not negative differences of summed counters. Observed
resets were zero per series; new UID/series still means a new counter.

The branch was pushed before ArgoCD switched to `feature/lab12`. Original
`automated: {prune: true, selfHeal: true}` was saved, then removed for controlled
PDB patches and migration/deploy ordering. Every application manifest change
was committed/pushed and manually synced to its exact Git revision.
Final automation was restored. An existing unrelated ApplicationSet-controller
CrashLoopBackOff remains visible; the QuickTicket Application controller
successfully synced and no controller reverted these temporary settings.

## Multi-replica failover

Committed Events/Payments/Notifications replicas=2 and gateway Rollout=5.

```powershell
./scripts/lab12/resilience.ps1 -Experiment failover
kubectl delete pod gateway-64fbcc698f-6cbv4 --wait=false
kubectl delete pod events-78cfdf7984-d2sf4 --wait=false
```

| UTC | Actual observation |
|---|---|
| 22:17:27.2130350 | Gateway delete, UID 46f6bf03-0195-402a-bcab-5f5a0d025e15 |
| 22:17:27.3452780 | Events delete, UID a2627992-2678-401b-af20-20b8a38c2fc0 |
| 22:17:27 | Replacement gateway hkw9t and Events ts879 created |
| 22:17:28.1614347 | First poll: replacements Pending/not Ready; surviving ready endpoints 4 gateway / 1 Events |
| 22:17:33.3149563 | Gateway hkw9t first observed Ready, new UID dd3ad6da-c6c6-4391-9b3f-ba0d4ed97bcc |
| 22:17:37.7943680 | Events ts879 first observed Ready, UID 547d93b4-1ed1-482c-b44a-ee327e66ed36 |
| 22:17:37.7959475 | Five gateway and two Events Ready; recovery observed |

First delete → both replacement Ready was **10.583 seconds**; the script's
11.090-second stopwatch also included preparation before deletion.
There were 112 client HTTP 200 responses and 28 confirmed payments.
Prometheus reported 513.079 request increase and zero 5xx in the same window.

Direct pod deletion bypasses PDB admission and is not a node-failure experiment.
Surviving Service endpoints supplied continuity here; a single-node host failure
would affect every replica and cannot be tolerated by this cluster.

## PDB configuration and real API rejection

`k8s/pdb.yaml` has four policy/v1 budgets, selectors app=<service>.
At final validation:

```text
kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

| PDB | currentHealthy | desiredHealthy | expectedPods | disruptionsAllowed |
|---|---:|---:|---:|---:|
| gateway | 5 | 2 | 5 | 3 |
| events | 2 | 1 | 2 | 1 |
| payments | 2 | 1 | 2 | 1 |
| notifications | 2 | 1 | 2 | 1 |

Gateway minAvailable=2 keeps more than one serving replica while allowing
maintenance of up to three healthy replicas; it is not a measured guarantee
of half capacity. With three healthy replicas and minAvailable=1, at most two
simultaneous voluntary evictions are allowed. A PDB does not protect against
involuntary host failure.

For the real admission test, Events minAvailable was temporarily 2. After
observedGeneration caught up: currentHealthy=2, desiredHealthy=2,
expectedPods=2, disruptionsAllowed=0.

```http
2026-10-18T22:14:24.5187169Z
POST http://127.0.0.1:18001/api/v1/namespaces/default/pods/events-78cfdf7984-d2sf4/eviction
Content-Type: application/json

{"metadata":{"namespace":"default","name":"events-78cfdf7984-d2sf4"},"apiVersion":"policy/v1","kind":"Eviction"}

HTTP/1.1 429 Too Many Requests
{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"Cannot evict pod as it would violate the pod's disruption budget.","reason":"TooManyRequests","details":{"causes":[{"reason":"DisruptionBudget","message":"The disruption budget events-pdb needs 2 healthy pods and has 2 currently"}]},"code":429}
```

The pod still had UID a2627992-2678-401b-af20-20b8a38c2fc0 and no deletion
timestamp. Finally minAvailable returned to 1/allowed=1. Only the two proxy
processes created for this test (PIDs 4056 and 28888) were stopped.

The first API attempt also returned 429, but PowerShell consumed its response
body; the shown repeat used curl to preserve the complete response. A later
GitOps sync exposed a bad last-applied annotation from the temporary PDB
round-trip: resourceVersion=0 update was rejected. Resetting only the
last-applied annotation with `kubectl apply set-last-applied -f k8s/pdb.yaml
--create-annotation=true` fixed sync without deleting the budget.

## Topology spread

Live gateway template:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

The actual `kubectl get pods -o wide` output placed all five gateway pods on
`k3d-quickticket-server-0`; there is no other node. Preferred distributions
on three eligible nodes would be 2/2/1 for five pods, 3/2/2 for seven.
ScheduleAnyway is a soft preference, not a hard maxSkew guarantee. Whether
DoNotSchedule leaves pods Pending depends on eligible domains and other
constraints; single-node placement alone does not prove it would.
The node remains a single point of failure.

## Graceful restart

The gateway template keeps liveness on /metrics and uses readiness /health,
period=2s/failureThreshold=1, preStop sleep 10, grace=40. Uvicorn's explicit
graceful timeout is 25 seconds, leaving five seconds of margin after preStop.
The command is configured in the manifest, not a Python change or image retag:

```text
$pod=(kubectl get pods -l app=gateway -o json | ConvertFrom-Json).items[0].metadata.name
kubectl exec $pod -- python -c "print(open('/proc/1/cmdline','rb').read().replace(bytes([0]),b' ').decode())"
/usr/local/bin/python3.13 /usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8080 --timeout-graceful-shutdown 25
```

Sleep does not itself flip readiness. Deletion marks EndpointSlices terminating
and ready=false; preStop leaves the server alive while routing changes propagate.
Uvicorn then receives SIGTERM and drains. The budget covers the observed short
requests, not an untested arbitrary 25-second operation.

Before the explicit restart, normal canary steps finished without bypass:
`gateway-64fbcc698f-11-2` Successful, three [0] measurements at
22:13:24, 22:13:44, 22:14:04Z. RolloutCompleted at 22:16:50Z.

An independent notifications-down check at 22:16:38.6646755Z returned HTTP 200:
gateway status healthy, notifications down, critical dependencies ok.
The 75-response observation window still confirmed 18 payments; notifications
was immediately restored to two replicas.

```powershell
# Official Windows plugin was invoked directly after PATH lookup failed:
& (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Temp\sre-lab11-tools\kubectl-argo-rollouts.exe') restart gateway
```

Restart command timestamp: **22:19:13.7167770Z**; restartAt: **22:19:15Z**.
All five original UIDs were gone and five new gateway pods Ready at
**22:19:53.1706212Z**: **39.454 seconds** command-to-observation.
The script stopwatch was 39.541 seconds including log setup.

| New pod suffix (gateway-64fbcc698f-) | Created UTC | First Ready observed UTC |
|---|---|---|
| sb684 | 22:19:17 | 22:19:23.1538842 |
| 7xpqs | 22:19:22 | 22:19:29.4288142 |
| 6vvpw | 22:19:29 | 22:19:34.5939719 |
| m54cw | 22:19:34 | 22:19:40.8706919 |
| 2v9d7 | 22:19:40 | 22:19:46.6692568 |

Original suffixes were 8fd6b, hjhq2, hkw9t, pg54k, z7rqt. Their full UIDs
and new UIDs are in lifecycle evidence. The observer saw at least four Ready
gateway endpoints during restart; five were again sampled at 22:19:48.154908Z.
It recorded 260 HTTP 200 / 65 confirmed pay / no transport errors.
The complete original-UID disappearance, not an immediate Healthy status,
defined restart completion.

Real old-pod log excerpts:

```text
2026-10-18T22:19:44.384154662Z Shutting down
2026-10-18T22:19:44.485716053Z Application shutdown complete.
2026-10-18T22:19:44.485996572Z Finished server process [1]
2026-10-18T22:19:50.684856451Z Shutting down
2026-10-18T22:19:50.785836495Z Application shutdown complete.
2026-10-18T22:19:50.785855722Z Finished server process [1]
```

restartAt retained the same template hash; it did not start another canary
AnalysisRun. Two earlier attempts are retained as windows `restart` and
`restart-repeat`: plugin PATH and Unicode-path resolution failed before any
restart. Neither is counted as successful restart evidence.

## Concurrent index migration

`12a0001` follows the actual head 9a0002; the existing Alembic setup was reused.

```python
def upgrade():
    with op.get_context().autocommit_block():
        op.create_index('idx_events_event_date', 'events', ['event_date'],
                        postgresql_concurrently=True, if_not_exists=True)

def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index('idx_events_event_date', table_name='events',
                      postgresql_concurrently=True, if_exists=True)
```

```text
kubectl exec lab12-client -- python /scripts/migrate.py upgrade 12a0001
start=2026-10-18T22:20:33.473614Z
end=2026-10-18T22:20:33.614181Z elapsed_ms=140.570
current=12a0001

pg_index / pg_get_indexdef:
idx_events_event_date | indisvalid=true | indisready=true
CREATE INDEX idx_events_event_date ON public.events USING btree (event_date)
```

The 17.075-second observation window recorded 92 client HTTP 200 / 23 confirmed
payments and positive Prometheus volume, with no observed 5xx or transport errors.
Index evidence was saved **before** the rename removed its column.

Normal CREATE INDEX blocks writes but not ordinary SELECT; it does not take an
ACCESS EXCLUSIVE table lock. CONCURRENTLY allows writes during index building,
but is not lock-free and cannot run inside Alembic's normal transaction.
IF NOT EXISTS does not validate an existing invalid index: check indisvalid/
indisready and repair/retry deliberately after a failure. This five-row table
proves the procedure, not performance or lock duration on ten million rows.

## Expand-and-contract design and execution

The required ordering is **M1 → Deploy A → M2 → Deploy B → M3**.
M3 is the step that would break A/legacy readers if moved earlier: COALESCE
still resolves the referenced old column. Deploy A before M1 would also reference
a missing new column. Backfill precedes NOT NULL; all legacy consumers must be
gone before DROP.

There is no runtime event-date write API: Events only reads event dates and
writes orders. Seed is the date writer, and it was not rerun on the live DB.
No dual-write handler is needed here. In production, inventory/admin/import
writers must be migrated or paused and dual-write must preserve both values.

### Migration bodies

The separate reversible files are in `migrations/versions/`:

```python
# 12a0002 M1, down_revision=12a0001
def upgrade():
    op.add_column('events', sa.Column('scheduled_at',
                  sa.TIMESTAMP(timezone=True), nullable=True))
def downgrade():
    op.drop_column('events', 'scheduled_at')

# 12a0003 M2, down_revision=12a0002
def upgrade():
    op.execute('UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL')
    op.alter_column('events', 'scheduled_at', nullable=False)
def downgrade():
    op.alter_column('events', 'scheduled_at', nullable=True)

# 12a0004 M3, down_revision=12a0003
def upgrade():
    op.drop_column('events', 'event_date')
def downgrade():
    op.add_column('events', sa.Column('event_date',
                  sa.TIMESTAMP(timezone=True), nullable=True))
    op.execute('UPDATE events SET event_date = scheduled_at')
    op.alter_column('events', 'event_date', nullable=False)
    with op.get_context().autocommit_block():
        op.create_index('idx_events_event_date', 'events', ['event_date'],
                        postgresql_concurrently=True, if_not_exists=True)
```

M3 downgrade also restores the index required by the still-applied 12a0001.
The separate 12a0005 builds `idx_events_scheduled_at` concurrently; downgrade
drops it concurrently. The already-applied old-index revision was not rewritten.

### Immutable application stages

| Stage | Source commit / image | Observed image ID |
|---|---|---|
| A | 207fa15 / quickticket-events:lab12-a-4f0dd1dfda5e | sha256:c9aa4f4f79b7f96ea5a62eb0f09ca4155ed01aa877338b4a2db74b9d7ac3388e |
| B | edef8ae / quickticket-events:lab12-b-a0bc4751a840 | sha256:44ae178ff018278bdfadfc519bb19b81797e77e037fd1dbfbb72de48961e49b8 |

Tags contain the first 12 digits of the actual Events source SHA256. Both were
built with Docker and imported into quickticket using k3d; manifests use those
exact tags and Never pull policy, not stale GHCR references. Reproduction on
another node needs image import. JSON keeps `date` at the same selected-field
position. GROUP BY e.id is valid because it is the Events primary key.

Actual committed A → B diff:

```diff
diff --git a/app/events/main.py b/app/events/main.py
index 3fdd42d..c5436ee 100644
--- a/app/events/main.py
+++ b/app/events/main.py
@@ -141,10 +141,10 @@ def list_events():
     try:
         cur = conn.cursor()
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date), e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
         """)
         rows = cur.fetchall()
         cur.close()
@@ -166,7 +166,7 @@ def get_event(event_id: int):
     try:
         cur = conn.cursor()
         cur.execute("""
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date), e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
             WHERE e.id = %s GROUP BY e.id
diff --git a/app/seed.sql b/app/seed.sql
index 55a1966..91f63e2 100644
--- a/app/seed.sql
+++ b/app/seed.sql
@@ -4,9 +4,10 @@ CREATE TABLE IF NOT EXISTS events (
     id SERIAL PRIMARY KEY,
     name TEXT NOT NULL,
     venue TEXT NOT NULL,
-    event_date TIMESTAMPTZ NOT NULL,
+    scheduled_at TIMESTAMPTZ NOT NULL,
     total_tickets INT NOT NULL,
-    price_cents INT NOT NULL
+    price_cents INT NOT NULL,
+    email VARCHAR(255)
 );

 CREATE TABLE IF NOT EXISTS orders (
@@ -20,7 +21,9 @@ CREATE TABLE IF NOT EXISTS orders (
 );

 -- Seed events
-INSERT INTO events (name, venue, event_date, total_tickets, price_cents) VALUES
+CREATE INDEX IF NOT EXISTS idx_events_scheduled_at ON events(scheduled_at);
+
+INSERT INTO events (name, venue, scheduled_at, total_tickets, price_cents) VALUES
     ('Go Conference 2026', 'Main Hall A', '2026-09-15 09:00:00+00', 100, 5000),
     ('SRE Meetup', 'Room 204', '2026-10-01 18:00:00+00', 30, 0),
     ('Cloud Native Summit', 'Expo Center', '2026-11-20 10:00:00+00', 500, 15000),
```

### First execution: retained failure

M1 ran at 22:21:11.093554–22:21:11.142551Z (48.985 ms), then two A pods were
Ready with the new ID by 22:22:44.1446069Z while all scheduled_at values were
still NULL. M2 ran at 22:23:09.475459–22:23:09.567520Z (92.062 ms):
five rows, zero NULLs and zero mismatches, then NOT NULL.
Two B pods and no A pods, including terminating ones, were verified at
22:26:48.0158838Z. After the verified external backup, M3 actually ran at
22:27:29.405648–22:27:29.457595Z (51.947 ms).

| Window | UTC start–end (2026-10-18) | Client requests | HTTP statuses | Confirmed pay | Transport | 429/409 | Prom increase total | Prom increase 5xx |
|---|---|---:|---|---:|---:|---|---:|---:|
| M1 | 22:21:10.302091–22:21:26.812606 | 85 | 200: 85 | 22 | 0 | 0/0 | 401.755 | 0 |
| Deploy-A | 22:22:21.779222–22:23:00.098900 | 203 | 200: 203 | 50 | 0 | 0/0 | 876.285 | 0 |
| M2 | 22:23:08.939622–22:23:25.872046 | 92 | 200: 92 | 23 | 0 | 0/0 | 398.371 | 0 |
| Deploy-B | 22:26:10.109106–22:26:44.615841 | 180 | 200: 179, 502: 1 | 45 | 0 | 0/0 | 831.902 | 2.3335277946311423 |
| M3 | 22:27:14.143942–22:27:30.061889 | 84 | 200: 84 | 21 | 0 | 0/0 | 377.067 | 0 |

The first B transition had one independently observed 502. Gateway logs also
showed errors during A, despite its client sample and scrape increase being zero:

```text
2026-10-18T22:22:40.648551276Z POST /events/1/reserve HTTP/1.1 502 Bad Gateway
2026-10-18T22:22:41.102512076Z GET /events HTTP/1.1 502 Bad Gateway
2026-10-18T22:26:21.152749147Z GET /events HTTP/1.1 502 Bad Gateway
2026-10-18T22:26:29.001638002Z events service error: All connection attempts failed
2026-10-18T22:26:29.002003368Z GET /events HTTP/1.1 502 Bad Gateway
```

These were connection failures during Events replacement, not a missing-column
SQL error. The precise packet-level route race was not traced. Events had no
preStop drain, so a stopping server could disappear while routes/connections
were still converging. I added the same 10-second preStop, 40-second grace and
25-second Uvicorn drain budget to Events (commit e4589b6), without changing
its inherited /metrics probes or weakening gateway health.

One M3 invocation initially used a nonexistent /work/migrate.py; it performed
no DDL. The corrected /scripts/migrate.py invocation ran within the saved M3
window. An attempted extra close for nonexistent M3-executed was rejected;
the original M3 window and actual DDL timestamps were retained.

### Separate full repeat after the fix

To repeat safely without restoring over live orders, while B still ran I
downgraded the schema to 12a0003 (restoring event_date). I then deployed the
original compatible legacy GHCR reader, waited for all B pods to leave, and
downgraded to 12a0001. No date writer was active; all orders and dates remained.

Then I repeated all five stages against the working database with the same
two mixedload replicas plus observer:

| Stage | Exact revision or Git deployment | Schema at the end |
|---|---|---|
| repeat-M1 | 12a0002; 22:31:30.831330–30.883910Z, 52.584 ms | both columns; scheduled_at nullable, five NULLs |
| repeat-Deploy-A | f7a51ea; two A pods/no legacy by 22:32:20.5037775Z | both columns; fallback reads while new values NULL |
| repeat-M2 | 12a0003; 22:32:38.172736–38.228856Z, 56.122 ms | both columns NOT NULL; five dates equal |
| repeat-Deploy-B | af3ad38; two B pods/no A by 22:33:53.0155704Z | B reads only scheduled_at |
| repeat-M3 | 12a0004; 22:34:24.676107–24.915503Z, 239.386 ms | old column/index removed |

| Window | UTC start–end (2026-10-18) | Client requests | HTTP statuses | Confirmed pay | Transport | 429/409 | Prom increase total | Prom increase 5xx |
|---|---|---:|---|---:|---:|---|---:|---:|
| repeat-M1 | 22:31:30.351550–22:31:46.292682 | 84 | 200: 84 | 21 | 0 | 0/0 | 374.434 | 0 |
| repeat-Deploy-A | 22:31:45.401901–22:32:31.143693 | 241 | 200: 241 | 61 | 0 | 0/0 | 1085.258 | 0 |
| repeat-M2 | 22:32:37.678232–22:32:54.049992 | 88 | 200: 88 | 22 | 0 | 0/0 | 413.085 | 0 |
| repeat-Deploy-B | 22:33:19.458068–22:34:03.718638 | 228 | 200: 228 | 57 | 0 | 0/0 | 1055.313 | 0 |
| repeat-M3 | 22:34:24.170026–22:34:40.851382 | 88 | 200: 88 | 22 | 0 | 0/0 | 408.047 | 0 |
| repeat-all | 22:31:30.034489–22:34:48.560062 | 1040 | 200: 1040 | 260 | 0 | 0/0 | 4699.447 | 0 |

The repeat's whole-window measurement includes the gaps between stages and
the replacement scheduled_at index, not just selected successful subwindows.
Its 1,040 responses were exactly 260 per list/detail/reserve/pay; all payments
were confirmed. Stage subwindows can overlap by less than one second at
marker boundaries and must not be added to estimate total traffic.

Raw whole-window Prometheus result:

```text
start=2026-10-18T22:31:30.034489Z end=2026-10-18T22:34:48.560062Z
query=sum(increase(gateway_requests_total[199s]))
evaluated_at=2026-10-18T22:34:54.792143Z
result=[1792362888.56,"4699.4474098245"]

query=(sum(increase(gateway_requests_total{status=~"5.."}[199s])))
      or on() (0 * sum(increase(gateway_requests_total[199s])))
evaluated_at=2026-10-18T22:34:54.797542Z
result=[1792362888.56,"0"]
```

Five gateway series had samples and zero observed resets. Every sampled
EndpointSlice in this repeat had two Events / five gateway ready endpoints.
This supports zero observed errors in the repeat, not retroactive success
for the failed first execution.

### Schema and data preservation

Before: event_date TIMESTAMPTZ NOT NULL; nullable email VARCHAR(255); no
scheduled_at. M1 added scheduled_at nullable; M2 made it NOT NULL and preserved
all five UTC values. Final schema evidence shows:

```text
Alembic current: 12a0005 (head)
events columns:
email         character varying     nullable
id            integer               NOT NULL
name          text                  NOT NULL
price_cents   integer               NOT NULL
scheduled_at  timestamp with time zone NOT NULL
total_tickets integer               NOT NULL
venue         text                  NOT NULL
event_date: absent
idx_events_scheduled_at: indisvalid=true, indisready=true
CREATE INDEX idx_events_scheduled_at ON public.events USING btree (scheduled_at)

id  scheduled_at
1   2026-09-15 09:00:00+00:00
2   2026-10-01 18:00:00+00:00
3   2026-11-20 10:00:00+00:00
4   2026-09-22 14:00:00+00:00
5   2026-10-10 10:00:00+00:00
```

The old index disappeared with its dropped column, as PostgreSQL requires.
12a0005 recreated the appropriate new-column index at
22:34:47.552655–22:34:47.655592Z (102.938 ms).
[Final schema JSON](evidence/lab12/final-schema.json) contains the raw
column/index/date/current-revision snapshot and its actual timestamp.

For ten million rows, use small transactions and coordinate dual writers:

```python
last_id = 0
while True:
    with transaction():
        ids = select_ids_after(last_id, limit=10_000)  # stable key order
        if not ids: break
        update_where_null(ids, scheduled_at_from="event_date")
        last_id = max(ids)
    sleep(short_pause)  # monitor locks, lag, latency and WAL pressure
verify_no_nulls_or_mismatches()
enforce_not_null_with_a_planned_lock_budget()
```

Restart/reconcile checkpoints and concurrent writers must not leave NULLs
behind. On large tables, validate constraints in a separate planned phase;
the small lab UPDATE/ALTER still takes locks and is not a lock-free promise.

## Fresh bootstrap and rollback safety

[Compatibility transcript](evidence/lab12/compatibility.txt) and
[reproduction script](../scripts/lab12/compatibility.ps1) show actual checks:

| UTC | Isolated test |
|---|---|
| 22:28:20.324638–22:28:21.655244 | Concurrent old index upgrade, downgrade to 9a0002, upgrade again |
| 22:28:31.1391362 | A on M1: /health, /events and /events/3 all HTTP 200 |
| 22:28:42.1941472 | B on M2: all three reads HTTP 200 |
| 22:28:53.2552819 | B after contract: all three reads HTTP 200 |
| 22:28:56.085576–22:28:56.242009 | Full downgrade of isolated restored DB to baseline 9a0001 |
| 22:28:56.772994–22:28:56.908600 | Upgrade isolated legacy DB through all revisions to 12a0005 |
| 22:29:08.3314565 | B on fresh updated seed: /health, /events and /events/3 HTTP 200 |

The downgrade round-trip preserved five dates and 8,952 restored orders.
The new seed created five events and zero orders in `lab12_fresh`. Before
stamping that fresh DB, bootstrap_check compared **all** Events/Orders column
types, lengths, defaults, nullability, constraints and indexes against the
fully migrated legacy DB:

```json
{"match":true,"verified_components":["columns","indexes","constraints"],"fresh_database":"lab12_fresh","stamped_revision":"12a0005"}
```

Stamping was only for the already verified fresh schema, never a substitute
for live migrations. Old migrations must not be run blindly against the
scheduled_at-only seed. Returning event_date alone is not safe application
rollback: values, meaning, active writers, constraints and every concurrent
consumer must remain compatible. The current schema-aware
[recovery supplement](runbooks/lab12-schema-recovery.md) preserves this distinction.

## Error summary and limitations

| Window | UTC start–end (2026-10-18) | Client requests | HTTP statuses | Confirmed pay | Transport | 429/409 | Prom increase total | Prom increase 5xx |
|---|---|---:|---|---:|---:|---|---:|---:|
| baseline | 22:13:04.002010–22:14:25.382911 | 419 | 200: 419 | 105 | 0 | 0/0 | 1733.337 | 0 |
| notify-health | 22:16:34.964869–22:16:48.964694 | 75 | 200: 75 | 18 | 0 | 0/0 | 291.488 | 0 |
| failover | 22:17:26.676300–22:17:48.099560 | 112 | 200: 112 | 28 | 0 | 0/0 | 513.079 | 0 |
| restart-executed | 22:19:13.599408–22:20:03.546347 | 260 | 200: 260 | 65 | 0 | 0/0 | 1202.901 | 0 |
| index | 22:20:32.942524–22:20:50.017135 | 92 | 200: 92 | 23 | 0 | 0/0 | 453.672 | 0 |
| final-index | 22:27:47.883847–22:28:04.202416 | 84 | 200: 84 | 21 | 0 | 0/0 | 396.098 | 0 |

The zero-error claims are limited to measured clients, matching gateway
scrapes and saved logs. The observer cannot cover every external client;
Prometheus cannot see requests that never reached gateway. New short-lived
series and scrape-edge extrapolation can miss first errors, as the first
A run demonstrated. Per-pod metrics and independent errors are retained
instead of treating counter sums as proof.

The observer was sequential and requests were short. This is not a maximum
capacity test, a long-running transaction drain test, real node failure or a
multi-node spread demonstration. HPA observation was optional and not attempted.

## Final recovery

After completing measurements, mixedload was deleted, the observer was stopped,
its full trace copied to the host, and only the temporary client/compatibility
pods, ConfigMap, observer RBAC and four named lab12 test databases were removed.
No PVC, production order or backup was deleted. Both test proxy processes and
old-pod log-follow processes were stopped.

At 22:36:24.0550884Z, after automation restoration:

```text
kubectl get application quickticket -n argocd
NAME          SYNC STATUS   HEALTH STATUS
quickticket   Synced        Healthy
targetRevision=feature/lab12
revision=af3ad38b5a44f2b68da7b5e19e198a8458cb81db
automated={prune:true,selfHeal:true}

kubectl get pods -l 'app in (mixedload,loadgen)'
No resources found in default namespace.
```

Live validation at 22:37:14.2636175Z: gateway phase Healthy, readyReplicas=5,
replicas=5; Events/Payments/Notifications each 2/2; Redis/PostgreSQL 1/1.
All eleven application pods remained on the one existing node.
PDB defaults and the Lab 11 CB/limiter/bulkhead settings were retained.
Gateway and Events liveness remained /metrics.

[Final HTTP/Prometheus evidence](evidence/lab12/final-http.json):

```text
2026-10-18T22:35:56.854233Z GET gateway:8080/health HTTP 200
{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}
2026-10-18T22:35:56.859816Z GET events:8081/health HTTP 200
{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
2026-10-18T22:35:56.866888Z GET payments:8082/health HTTP 200
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
2026-10-18T22:35:56.873394Z GET notifications:8083/health HTTP 200
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
2026-10-18T22:35:57.048515Z POST gateway:8080/events/3/reserve HTTP 200
{"reservation_id":"e80616eb-b32e-460e-9947-d1a7ffad89cb","event_id":3,"quantity":1,"total_cents":15000,"expires_in_seconds":300}
2026-10-18T22:35:57.070707Z POST gateway:8080/reserve/e80616eb-b32e-460e-9947-d1a7ffad89cb/pay HTTP 200
{"order_id":"e80616eb-b32e-460e-9947-d1a7ffad89cb","event_id":3,"quantity":1,"total_cents":15000,"status":"confirmed"}
2026-10-18T22:35:57.070355196Z notification sent event=order_confirmed order=e80616eb-b32e-460e-9947-d1a7ffad89cb
```

Both individual Events pods and all four Payments/Notifications pods also
returned healthy HTTP 200 with zero fault settings. Prometheus had five
gateway targets and its notifications target up, lastError empty.

```text
kubectl get cronjob quickticket-backup
NAME                 SCHEDULE      SUSPEND   ACTIVE
quickticket-backup   */5 * * * *   False     0

kubectl get jobs --sort-by=.metadata.creationTimestamp
quickticket-backup-29872705   Complete   1/1   4s
quickticket-backup-29872710   Complete   1/1   4s
quickticket-backup-29872715   Complete   1/1   5s

kubectl get pvc
postgres-backups   Bound   1Gi   RWO   local-path
postgres-data      Bound   1Gi   RWO   local-path
```

Client-side Kubernetes validation passed for gateway/events/payments/
notifications manifests and all four PDBs. Gateway Python was unchanged;
the manifest overrides Uvicorn's command. Existing Lab 11 tests were rerun:
12 tests passed in 0.395s. A first Docker test invocation mounted sources at the
wrong paths and failed import; the corrected /repo/app and /repo/tests mounts
passed, without changing tests.

Final post-cleanup recheck at **2026-10-18T22:43:10.751545Z**, executed from
an existing gateway pod (no new probe resources):

```json
[{"service":"gateway","status":200,"body":{"status":"healthy","checks":{"events":"ok","payments":"ok","notifications":"ok","circuit_payments":"CLOSED"}}},{"service":"events","status":200,"body":{"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}},{"service":"payments","status":200,"body":{"status":"healthy","failure_rate":0.0,"latency_ms":0}},{"service":"notifications","status":200,"body":{"status":"healthy","failure_rate":0.0,"latency_ms":0}}]
```

Both running Events pods returned the actual `/app/main.py` SHA256
`a0bc4751a840bc5d57f8941e7207a177f1f45698da4d4b21ee1b65c87086dc12`, matching
Deploy B. At 22:43:11.6176319Z ArgoCD was Synced/Healthy at report commit
`ca90b9f7549c3d91f1c976e7b48d7fcc661b0984`, target feature/lab12,
prune/selfHeal true. The selector for lab12-client/lab12-compat/mixedload/loadgen
returned `No resources found in default namespace.`

## Submission

All commits were pushed to the fork's `feature/lab12`. GitHub CLI is absent.
Creating the upstream PR through the connected GitHub integration returned
HTTP 403: `Resource not accessible by integration` (pulls/create API).
This is a PR-creation permission blocker, not a branch-push failure.

Create the PR manually using this
[compare link](https://github.com/inno-devops-labs/SRE-Intro/compare/main...GeorgyPyanov:SRE-Intro:feature/lab12?expand=1).
Title: **Lab 12: Advanced Kubernetes Resilience**.

```markdown
- [x] Task 1: multi-replica failover, four PDBs, topology spread, real eviction rejection
- [x] Task 2: graceful Rollout restart and concurrent index migration
- [x] Bonus: five staged expand-and-contract transitions, with failed first run retained and successful full repeat
- [ ] Optional HPA observation: not attempted
```

## Acceptance checklist

- [x] Two Events/Payments/Notifications replicas, five gateway replicas; connection/resource budget checked.
- [x] Coordinated gateway/Events deletion under real mixedload, saved recovery/endpoint/client/Prometheus evidence.
- [x] Four PDBs, actual healthy/disruption counts and real HTTP 429 eviction rejection; defaults restored.
- [x] Live hostname topology spread with the single-node limitation documented.
- [x] Gateway graceful lifecycle, /health readiness and /metrics liveness; full normal canary/analysis completed.
- [x] Real Rollout restart replaced every original UID; zero observed client/Prometheus errors in its measured window.
- [x] Concurrent index upgrade/downgrade and validity proof before the rename.
- [x] Expand-and-contract design, ordering, batching and rollback answers.
- [x] Bonus: five separate live stages, immutable A/B images, schema/data/seed verification; failed first run retained and full zero-observed-error repeat completed.
- [x] Isolated legacy upgrade/downgrade and complete fresh-schema comparison before stamp.
- [x] Final checkout/notification, healthy replicas, fault settings, PVCs, backups, Prometheus and GitOps automation restored; temporary resources removed.
- [ ] Optional HPA observation — not attempted.
