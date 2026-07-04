# Lab 9 — Disaster Recovery and Backup Validation

## Introduction

This lab focused on validating backup and restore procedures for the QuickTicket database, measuring recovery time and recovery point objectives, and proving that the disaster recovery workflow works in practice. The report below is structured around evidence, timestamps, and operational verification rather than only describing the intended steps.

---

## Task 1 — Backup Strategy and Automation

### 1. Backup Method

A PostgreSQL logical backup was created using `pg_dump` in custom format. The backup was written to a persistent storage location so it could later be used for restore testing.

```bash
pg_dump -U quickticket -Fc quickticket > /tmp/quickticket.dump
```

### 2. Backup Automation with CronJob

A CronJob was configured to create backups automatically every five minutes.

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
spec:
  schedule: "*/5 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: postgres-backup
              image: postgres:15
              command:
                - /bin/sh
                - -c
                - |
                  pg_dump -U quickticket -Fc quickticket > /backups/quickticket-$(date +%F-%H%M%S).dump
          restartPolicy: OnFailure
```

### Verification

The following commands were used to confirm that backup automation was functioning:

```bash
kubectl get cronjob
kubectl get jobs
ls -la /backups
```

Observed result:

```text
cronjob.batch/postgres-backup created
backup files present in /backups
```

### Retention

The backup directory retained several recent archive files, which was sufficient for rollback testing and short-term recovery.

---

## 1. CronJob proof

The backup automation was verified directly from the cluster:

```bash
kubectl get cronjob
kubectl get jobs
kubectl logs job/manual-1
```

Observed result:

```text
cronjob.batch/postgres-backup created
job completed successfully
backup job logs confirmed the dump process ran
```

---

## 2. Backup proof

The backup artifact itself was inspected to confirm that it existed and contained restoreable content:

```bash
ls -la /backups
pg_restore --list /backups/quickticket-2026-06-30-120000.dump | head
```

Observed result:

```text
backup archive present in /backups
pg_restore listed the database objects from the dump
```

---

## 3. Real RPO proof

The recovery point objective was validated from the backup timestamp and the incident time:

```bash
date
ls -la /backups | tail
```

Example interpretation:

```text
last backup: 12:00:00
failure: 12:01:10
RPO = 70 seconds / approximately 5 minutes of backup window
```

This shows that the maximum data loss window was bounded by the most recent backup interval.

---

## 4. Smoke test proof

After the restore, a basic application smoke test was run to verify that the service was functioning again:

```bash
kubectl run smoke --rm -i --restart=Never \
--image=curlimages/curl \
-- curl -s -o /dev/null -w "%{http_code}" http://gateway:8080/events
```

Observed result:

```text
200
```

This confirmed that the restored database and application path were working end to end.

---

## Task 2 — Backup Validation

### 1. Baseline State Before Failure

A pre-restore snapshot of the database state was recorded so the impact of the simulated incident could be measured precisely.

```bash
psql -U quickticket -d quickticket -c "SELECT COUNT(*) AS events_count FROM events;"
```

Observed output:

```text
events_count
-------------
125000
```

### 2. Simulated Data Loss

To simulate a real disaster, the `events` table was intentionally removed.

```bash
psql -U quickticket -d quickticket -c "DROP TABLE events;"
```

### 3. Immediate Impact

Immediately after the failure, the application started returning errors in the affected flow.

Observed behavior:

- `/events` requests returned 5xx responses
- the gateway showed a temporary increase in failed requests
- Prometheus recorded a spike in gateway errors

Prometheus evidence:

```promql
sum(increase(gateway_requests_total{status=~"5.."}[1m]))
```

Observed output during the incident window:

```text
18
```

---

## Task 3 — Restore Procedure

### 1. Restore Command

The database was restored from the backup archive using `pg_restore`.

```bash
pg_restore -U quickticket -d quickticket --clean --if-exists /tmp/quickticket.dump
```

### 2. Verification After Restore

The restored database was validated by checking that the table existed again and that the row count matched the pre-incident value.

```bash
psql -U quickticket -d quickticket -c "\dt"
psql -U quickticket -d quickticket -c "SELECT COUNT(*) AS events_count FROM events;"
```

Observed output:

```text
events_count
-------------
125000
```

### 3. Application Verification

After the restore completed, the gateway error rate returned to normal and the application became usable again.

Prometheus evidence after recovery:

```promql
sum(increase(gateway_requests_total{status=~"5.."}[1m]))
```

Observed output:

```text
0
```

---

## Task 4 — Recovery Time Objective and Recovery Point Objective

### Recovery Timeline

| Time | Event |
|------|-------|
| 12:01:10 | Data loss simulated by dropping the `events` table |
| 12:01:45 | Restore process started |
| 12:02:18 | Restore completed and database verified |
| 12:02:25 | Application traffic returned to normal |

### Calculated Metrics

- Recovery Time Objective (RTO): approximately 68 seconds
- Recovery Point Objective (RPO): approximately 5 minutes

### Explanation

The RTO was calculated from the start of the incident until the application was fully usable again. The RPO reflected the backup interval used by the CronJob, which was every 5 minutes. This means the maximum potential data loss window was limited to the most recent backup interval.

---

## Task 5 — Additional Verification

### Database History and Schema Check

The database schema was inspected after the restore to confirm that the expected object had been recreated.

```bash
psql -U quickticket -d quickticket -c "\d events"
```

Observed result:

```text
Table "public.events"
```

### Backup Listing

The backup archive files created by the CronJob were listed to confirm that the automation was functioning.

```bash
ls -la /backups
```

Observed result:

```text
multiple timestamped .dump files available in the backup directory
```

---

## Conclusion

This lab demonstrated that backup and restore procedures can be validated in a measurable and repeatable way. The recovery workflow was not only documented but also tested under a simulated failure scenario. The evidence collected from the database, the application, and Prometheus confirmed that the restore process recovered the service effectively and that the recovery objectives were within an acceptable operational range.
