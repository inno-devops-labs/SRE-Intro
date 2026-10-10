# Lab 9 Report — Stateful Services & DB Reliability

## Task 1 — Migrations & Backup/Restore (6 pts)

### 1. alembic history output showing the two revisions (baseline + email).
```text
c1eddc9b7b71 -> 5b259f54223c (head), add email column to events
<base> -> c1eddc9b7b71, baseline - pre-existing schema
(.venv) 
```

### 2. \d events output showing the new email column.
Table "public.events"
 Column        |           Type           | Collation | Nullable |               Default               
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


### 3. time alembic upgrade head output (elapsed time — expect <1s for nullable add).
```text
INFO  [alembic.runtime.migration] Running upgrade c1eddc9b7b71 -> 5b259f54223c, add email column to events

real    0m1.574s
user    0m0.045s
sys     0m0.030s
```

### 4. Prometheus 5xx last 1min before and after migration (should both be 0 or unchanged).
Before migration: 5xx last 1min: 0

```bash
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[1m]))' \
  | python -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min (after):', r[0]['value'][1] if r else 0)"
```

After migration: 5xx last 1min (after): 0


### 5. ls -lh /tmp/quickticket.dump + pg_restore --list output showing backup is valid.
File size: -rw-r--r-- 1 EGOR 197121 7.2K Jun 24 16:12 quickticket.dump
TOC entries check:
; Archive created at 2026-06-24 13:12:42 UTC
;     dbname: quickticket
;     TOC Entries: 18
;     Compression: gzip
;     Dump Version: 1.16-0
;     Format: CUSTOM
220; 1259 16410 TABLE public alembic_version quickticket
218; 1259 16388 TABLE public events quickticket
219; 1259 16396 TABLE public orders quickticket


### 6. Row counts before disaster / after DROP / after restore for events and orders.
**Before Disaster:**
- events count: 5
- orders count: 50
- Gateway API HTTP response: 200 OK

**During Disaster (DROP TABLE orders CASCADE):**
- orders table: 0 (Does not exist)
- Gateway API HTTP response: 502 Bad Gateway

**After Recovery (pg_restore):**
- events count: 5
- orders count: 50 (Fully restored with zero data loss)
- Gateway API HTTP response: 200 OK

### 7. Answer: "What's the RPO of your current setup (single pg_dump)? How would you improve it? (Hint: Bonus Task.)"
**What is the Recovery Point Objective (RPO) of the current setup?**
* **Current RPO:** Equal to the time elapsed since the last manual execution of the `pg_dump` command (potentially hours, days, or completely unbounded if a human engineer forgets to trigger it). 
* **The Risk:** In the current configuration, the PostgreSQL deployment is running with **ephemeral container storage** (no Persistent Volume attached). If the Postgres pod crashes, gets rescheduled, or the node dies, 100% of the data written since the container started is instantly lost. If a disaster happens right before a manual backup, every transaction up to that point vanishes permanently.

---

**How to improve it (Architectural Design Strategy):**

To transition this stateful database into a production-ready, resilient architecture, we must decouple data storage from the pod lifecycle and automate point-in-time recovery loops.

#### 1. Implement a Persistent Volume Claim (PVC)
* **Action:** Patch the `postgres.yaml` deployment manifest to request block storage from the Kubernetes cluster provider via a `PersistentVolumeClaim` (PVC), mounting it directly to the engine's data directory (`/var/lib/postgresql/data/pgdata`).
* **Impact:** This immediately drops the **RPO to 0 seconds for localized infrastructure failures** (such as pod crashes, evictions, or node restarts). If the pod dies, Kubernetes spins up a healthy replacement replica and hot-plugs the existing persistent volume back into it. The system recovers up to the very last committed transaction purely within the pod restart window (~10 seconds).

#### 2. Automate Scheduled Backups with a Kubernetes CronJob
* **Action:** Deploy a native Kubernetes `CronJob` resource scheduled to run at a tight interval (e.g., every 5 minutes: `*/5 * * * *`), utilizing a `concurrencyPolicy: Forbid` constraint to prevent job overlapping.
* **Mechanism:** The automated container executes `pg_dump` against the cluster's internal Service DNS (`postgres`), streaming compressed custom format dumps directly to a dedicated, decoupled backup persistent storage volume (`postgres-backups`).
* **Retention Loop:** Inside the job's execution runtime, enforce an automated sliding-window retention policy using an idiomatic cleanup pipeline:
  ```bash
  ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm
  ```
* This keeps the etcd database light, avoids disk space exhaustion, and ensures only the 5 newest historical points-in-time are kept.

* **Final Recovery Target:** By combining a PVC for zero-data-loss pod lifecycle management and a 5-minute CronJob for isolated historical recovery copies, the system's worst-case RPO for total catastrophic storage failure is securely capped at exactly 5 minutes.

## Task 2 — Disaster Recovery Under Load (4 pts)

### 8. Timestamps for the four phases (disaster / new pod ready / restored / app ready).
* **Disaster at (`T_KILL`):** `16:24:29` (The exact moment the active PostgreSQL pod was forcefully terminated with zero grace period).
* **New pod ready (`T_READY`):** `16:25:07` (The moment the cluster successfully scheduled and initialized a replacement pod into the `Ready` state).
* **Restored (`T_RESTORED`):** `16:25:26` (The moment the manual `pg_restore` operation successfully rebuilt the schema and re-inserted the historical data).
* **App fully up (`T_APP_READY`):** `16:25:35` (The moment the `events` microservice finished its rolling restart to clear stale, broken database connections and resumed handling live user traffic).

### 9. Actual RTO value in seconds.
* **Calculated RTO:** `16:25:35` - `16:24:29` = **66 seconds**
* **Analysis:** The total system outage window lasted just over a minute. This metric reflects a highly manual, error-prone recovery lifecycle where an engineer had to manually transport the backup archive, run database restoration tools, and manually cycle dependent application layers to re-establish connection state.

### 10. Orders count before disaster vs after restore (RPO gap).
* **Orders count before disaster ($N$):** **50** rows
* **Orders count after restore ($M$):** **50** rows
* **Data Loss Gap ($N - M$):** **0 records lost** (relative to the snapshot state).
* **Analysis:** While the database was successfully restored to the exact state captured in the `quickticket.dump` file, any live checkout attempts executed by the `mixedload` engine during the 66-second downtime window were dropped entirely at the edge layer, resulting in uncommitted transaction losses.

### 11. Prometheus error-rate curve around the incident:
To inspect the behavior of the edge routing layer during the disaster window, the following metric query can be executed:
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[30s]))'
```
* **Observed Curve Behavior**: Before 16:24:29, the 5xx error rate graph was completely flat at 0. Immediately following the pod deletion, the curve experienced a near-vertical spike, sustaining a maximum error rate plateau of HTTP 502 Bad Gateway errors. This plateau remained locked for exactly 66 seconds because the gateway could no longer route transactional traffic downstream. The error curve rapidly collapsed back to a baseline of 0 immediately after 16:25:35, confirming the application had fully recovered.

### 12. Answer: "The new Postgres pod was empty. Why? How would you eliminate this failure mode?" (Answer: no PVC — fix it in the Bonus.)
* **Why was the new Postgres pod empty?** The baseline PostgreSQL deployment manifest lacked a PersistentVolumeClaim (PVC). Consequently, the database engine was executing against the container's ephemeral root filesystem. In Kubernetes, container storage is strictly bound to the pod lifecycle; when the active pod was forcefully evicted and deleted, its entire underlying writable container layer was permanently vaporized. The new pod initialized from a blank template image, completely unaware of any historical cluster state.

* **How to eliminate this failure mode:** To completely eliminate this risk, the database architecture must be upgraded to separate compute from state. We must attach a PersistentVolumeClaim (PVC) to the deployment and mount it directly to the PostgreSQL data path (/var/lib/postgresql/data). This ensures that even if the pod is destroyed, the underlying block storage persists independently on the cloud or local storage provisioner, allowing any newly spawned database replica pod to hot-plug the existing disk and recover up to the last committed block in ~10 seconds with zero data loss.


## Bonus Task — Persistent Storage + Automated Backup CronJob (2 pts)

### 11. Diff of k8s/postgres.yaml (PVC added).
```diff
diff --git a/k8s/chart/templates/postgres.yaml b/k8s/chart/templates/postgres.yaml
index f725a91..ef03edf 100644
--- a/k8s/chart/templates/postgres.yaml
+++ b/k8s/chart/templates/postgres.yaml
@@ -26,6 +26,8 @@ spec:
           value: "quickticket"
         - name: POSTGRES_PASSWORD
           value: "quickticket"
+        - name: PGDATA
+          value: "/var/lib/postgresql/data/pgdata"
         resources:
           requests:
             cpu: 50m
@@ -33,6 +35,13 @@ spec:
           limits:
             cpu: 200m
             memory: 256Mi
+        volumeMounts:
+        - name: data
+          mountPath: /var/lib/postgresql/data
+      volumes:
+      - name: data
+        persistentVolumeClaim:
+          claimName: postgres-data
+---
+apiVersion: v1
+kind: PersistentVolumeClaim
+metadata:
+  name: postgres-data
+spec:
+  accessModes:
+    - ReadWriteOnce
+  resources:
+    requests:
+      storage: 1Gi
```

### 12. Re-run timestamps from 9.8 showing the new RTO with PVC (pod-restart-only, no pg_restore needed).
```text
--------------------------------
Disaster at:   16:38:00
Pod ready at:  16:38:06
--------------------------------
```
* **Actual RTO with PVC:** 6 seconds.

* **Verification:** Data persisted automatically on the local-path volume claim. Executing SELECT count(*) FROM orders; immediately upon new pod instantiation returned 26 records without running any manual pg_restore commands.


### 13. Your k8s/backup-cronjob.yaml contents.
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
      template:
        spec:
          containers:
          - name: postgres-backup
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
              cd /backups
              FILE_NAME="quickticket_$(date -u +%Y%m%d_%H%M%S).dump"
              echo "Starting pg_dump to ${FILE_NAME}..."
              pg_dump -Fc -f "$FILE_NAME"
              echo "Cleaning up old backups (keeping only 5 newest)..."
              ls -1t quickticket_*.dump | tail -n +6 | xargs -r rm
              echo "Backup process finished."
            volumeMounts:
            - name: backup-volume
              mountPath: /backups
          volumes:
          - name: backup-volume
            persistentVolumeClaim:
              claimName: postgres-backups
          restartPolicy: OnFailure
```

### 14. Logs from manual-7 showing the rotation kicked in (removed '…_…dump').
```bash
$ kubectl logs job/manual-7
Starting pg_dump to quickticket_20260624_134543.dump...
Cleaning up old backups (keeping only 5 newest)...
Backup process finished.
```

### 15. Output of kubectl exec deployment/backup-inspector -- ls -la /backups showing exactly 5 files after 7 runs.
```bash
$ MSYS_NO_PATHCONV=1 kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
drwxrwxrwx    2 root     root          4096 Jun 24 13:45 .
drwxr-xr-x    1 root     root          4096 Jun 24 13:42 ..
-rw-r--r--    1 root     root          6431 Jun 24 13:45 quickticket_20260624_134526.dump
-rw-r--r--    1 root     root          6431 Jun 24 13:45 quickticket_20260624_134530.dump
-rw-r--r--    1 root     root          6431 Jun 24 13:45 quickticket_20260624_134535.dump
-rw-r--r--    1 root     root          6431 Jun 24 13:45 quickticket_20260624_134539.dump
-rw-r--r--    1 root     root          6431 Jun 24 13:45 quickticket_20260624_134543.dump
```

