# Lab 9 — Stateful Services & DB Reliability

## Setup

```
$ kubectl apply -f labs/lab8/mixedload.yaml
deployment.apps/mixedload unchanged
deployment "mixedload" successfully rolled out

$ kubectl exec -i pod/postgres-... -- psql -U quickticket -d quickticket -c '\dt'
              List of relations
 Schema |      Name       | Type  |    Owner
--------+-----------------+-------+-------------
 public | events          | table | quickticket
 public | orders          | table | quickticket
```

Alembic initialized locally with port-forward to `svc/postgres:5432`.

---

## Task 1 — Migrations & Backup/Restore (6 pts)

### 9.1–9.3 — Alembic setup

```
$ alembic init migrations
$ alembic revision -m "baseline - pre-existing schema"
$ alembic stamp head
$ alembic revision -m "add email column to events"
```

### 9.4 — Migration under load

**5xx before migration:**

```
5xx last 1min: 1.09
```

(Background noise from mixedload checkout failures — not migration-related.)

**Apply migration:**

```
$ time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade b27d36103632 -> f622e5bf6c7b, add email column to events
alembic upgrade head  0.22s user 0.02s system 93% cpu 0.265 total
```

**5xx after migration:**

```
5xx last 1min: 1.09
```

No new 5xx introduced by the migration.

**`alembic history`:**

```
b27d36103632 -> f622e5bf6c7b (head), add email column to events
<base> -> b27d36103632, baseline - pre-existing schema
```

**`\d events` (email column added):**

```
 email         | character varying(255)   |           |          |
```

### 9.5 — pg_dump backup

```
$ kubectl exec -i pod/postgres-... -- pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
$ ls -lh /tmp/quickticket.dump
-rw-r--r-- 1 abeb-arch abeb-arch 7.2K Jul 11 01:27 /tmp/quickticket.dump
$ file /tmp/quickticket.dump
/tmp/quickticket.dump: PostgreSQL custom database dump - v1.16-0
```

**`pg_restore --list` (first 25 lines):**

```
; Archive created at 2026-07-10 22:27:07 UTC
;     dbname: quickticket
;     TOC Entries: 18
220; 1259 16409 TABLE public alembic_version quickticket
218; 1259 16386 TABLE public events quickticket
219; 1259 16394 TABLE public orders quickticket
3474; 0 16409 TABLE DATA public alembic_version quickticket
3472; 0 16386 TABLE DATA public events quickticket
3473; 0 16394 TABLE DATA public orders quickticket
```

### 9.6 — Simulate data loss → restore

| Phase | events | orders | API |
|-------|-------:|-------:|-----|
| Before DROP | 5 | 51 | 200 |
| After DROP TABLE orders | 5 | *(table gone)* | /events=502 |
| After pg_restore | 5 | 51 | /events=200 |

```
$ kubectl exec $POD -- psql ... -c 'DROP TABLE orders CASCADE'
DROP TABLE
$ curl http://gateway:8080/events → /events=502
$ kubectl exec $POD -- pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/backup.dump
$ curl http://gateway:8080/events → /events=200
```

### 9.7 — RPO answer

**RPO of a single `pg_dump`:** equal to the time since the last backup. My dump was taken at **22:27:07 UTC**; any orders written after that would be lost on restore. In this run the backup was ~15s old and order count matched (51 → 51), but in production a daily dump means up to **24 hours** of writes lost.

**How to improve:** automated CronJob backups every 5 minutes (Bonus Task) + WAL archiving / continuous replication for near-zero RPO.

---

## Task 2 — Disaster Recovery Under Load (4 pts)

### 9.8 — Kill Postgres (no PVC)

```
healthy at 01:27:22 orders=51

$ kubectl delete pod -l app=postgres --grace-period=0 --force
pod "postgres-7c7ffc4b-8tvl6" force deleted

$ kubectl exec $NEW_POD -- psql ... -c '\dt'
Did not find any relations.
```

New pod came up **empty** — no PersistentVolumeClaim, data lived on ephemeral container storage.

### 9.9 — RTO / RPO

| Phase | Timestamp |
|-------|-----------|
| Disaster (pod killed) | **01:27:22** |
| New pod Ready | **01:27:23** |
| pg_restore complete | **01:27:24** |
| App fully up (events restarted) | **01:27:31** |

- **Actual RTO** = 01:27:31 − 01:27:22 = **9 seconds** (includes manual `pg_restore` + events rollout restart)
- **RPO gap in rows:** orders before = **51**, after restore = **51** (0 rows lost — backup was 15s old)
- **RPO in time:** ~**15 seconds** since last `pg_dump`

**Prometheus error rate during incident:**

```
5xx rate [30s]: 0.616/s
```

**Why was the new pod empty?** The original `k8s/postgres.yaml` had no `volumeMounts` or PVC — Postgres data lived on the container filesystem and was deleted with the pod. **Fix:** mount a PersistentVolumeClaim on `/var/lib/postgresql/data` (Bonus Task).

---

## Bonus Task — PVC + Automated Backup CronJob (2 pts)

### B.1 — PVC added to `k8s/postgres.yaml`

```diff
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
 containers:
   - name: postgres
+    env:
+      - name: PGDATA
+        value: /var/lib/postgresql/data/pgdata
+    volumeMounts:
+      - { name: data, mountPath: /var/lib/postgresql/data }
+volumes:
+  - name: data
+    persistentVolumeClaim:
+      claimName: postgres-data
```

### B.2 — Re-run disaster test with PVC

| Phase | Timestamp |
|-------|-----------|
| Pod killed | **01:28:23** |
| New pod Ready | **01:28:25** |
| App fully up | **01:28:33** |

After kill, `\dt` showed **all tables intact** on the new pod — data survived on the PV. No `pg_restore` needed.

- **RTO with PVC:** **10 seconds** (pod restart + events connection pool refresh only)
- **Improvement:** eliminated the manual restore step entirely

### B.3 — CronJob backup with rotation

Full `k8s/backup-cronjob.yaml`:

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
          restartPolicy: OnFailure
          containers:
            - name: backup
              image: postgres:17-alpine
              env:
                - name: PGHOST
                  value: postgres
                - name: PGUSER
                  value: quickticket
                - name: PGDATABASE
                  value: quickticket
                - name: PGPASSWORD
                  value: quickticket
              command:
                - /bin/sh
                - -c
                - |
                  set -e
                  cd /backups
                  TS=$(date -u +%Y%m%dT%H%M%SZ)
                  pg_dump -Fc -f "quickticket_${TS}.dump"
                  ls -1t quickticket_*.dump | tail -n +6 | while read -r f; do
                    echo "removed '${f}'"
                    rm -f "$f"
                  done
              volumeMounts:
                - name: backups
                  mountPath: /backups
          volumes:
            - name: backups
              persistentVolumeClaim:
                claimName: postgres-backups
```

Runs every 5 minutes, `concurrencyPolicy: Forbid`, keeps 5 newest dumps.

**`manual-7` logs (rotation):**

```
removed 'quickticket_20260710T222908Z.dump'
```

**`/backups` after 7 manual runs:**

```
-rw-r--r--  6329  quickticket_20260710T222911Z.dump
-rw-r--r--  6329  quickticket_20260710T222914Z.dump
-rw-r--r--  6329  quickticket_20260710T222917Z.dump
-rw-r--r--  6329  quickticket_20260710T222920Z.dump
-rw-r--r--  6329  quickticket_20260710T222923Z.dump
```

Exactly **5 files** remain after 7 runs — retention works.

---

## Cleanup

```
$ kubectl delete -f labs/lab8/mixedload.yaml
$ kubectl delete -f k8s/backup-cronjob.yaml
$ kubectl delete -f labs/lab9/backup-storage.yaml
```
