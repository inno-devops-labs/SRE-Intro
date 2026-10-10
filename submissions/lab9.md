# Lab 9 Report

## Task 1 

I set up Alembic and configured it to connect to the local PostgreSQL instance through port-forwarding. The existing schema was baselined and stamped as the current version

A migration was created to add a nullable `email` column to the `events` table. The migration ran under load and completed quickly

```bash
(.venv) user@MacBook-Air sre-intro % alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 6937f4406734 -> 729c84a02c1a, add email column to events
(.venv) user@MacBook-Air sre-intro % kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events'
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
```

The error rate stayed at zero during the migration

```bash
(.venv) user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- 'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[1m]))' | python3 -c "import sys,json;r=json.load(sys.stdin)['data']['result'];print('5xx last 1min:', r[0]['value'][1] if r else 0)"
5xx last 1min: 0
```

A backup was created with `pg_dump`, and I verified it was valid by listing its contents.ъ

```bash
(.venv) user@MacBook-Air sre-intro % ls -lh /tmp/quickticket.dump
-rw-r--r--  1 user  wheel   7.1K Jul  9 13:26 /tmp/quickticket.dump
(.venv) user@MacBook-Air sre-intro % kubectl exec $POD -- pg_restore --list /tmp/backup.dump | head -25
; Archive created at 2026-07-09 08:26:30 UTC
;     dbname: quickticket
;     TOC Entries: 18
; ...
220; 1259 16411 TABLE public alembic_version quickticket
218; 1259 16389 TABLE public events quickticket
219; 1259 16397 TABLE public orders quickticket
```

To simulate data loss, I dropped the `orders` table. The API started returning 502 errors

```bash
(.venv) user@MacBook-Air sre-intro % kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=502
```

After restoring from the backup, the table was recovered and the API returned to normal

```bash
(.venv) user@MacBook-Air sre-intro % kubectl exec $POD -- psql -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
     5
(1 row)

 count
-------
    50
(1 row)

(.venv) user@MacBook-Air sre-intro % kubectl run smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- curl -s -o /dev/null -w "/events=%{http_code}\n" http://gateway:8080/events
/events=200
```

The RPO of this setup is determined by how often backups are taken. With a single `pg_dump`, any data written after the backup was created would be lost. To improve RPO, backups should be taken more frequently


## Task 2 

I killed the Postgres pod while traffic was flowing. The new pod had no tables because the deployment did not use a PersistentVolumeClaim

```bash
(.venv) user@MacBook-Air sre-intro % NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl exec $NEW_POD -- psql -U quickticket -d quickticket -c '\dt'
Did not find any relations.
```

The backup was copied into the new pod and restored. The events service was restarted to refresh its database connections

```
Disaster at      13:49:29
New pod ready    13:49:36
Restored         13:49:48
App fully up     13:50:10
```

RTO = 41 seconds. RPO = 0 rows lost because the backup was recent

The new pod was empty because there was no PVC. This would not happen if persistent storage was configured


## Bonus 

I added a PVC named `postgres-data` to `k8s/postgres.yaml` and mounted it in the deployment. After recreating the deployment, the pod started using the PVC

```bash
(.venv) user@MacBook-Air sre-intro % NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl describe pod $NEW_POD | grep -A 10 "Volumes:"
Volumes:
  data:
    Type:       PersistentVolumeClaim
    ClaimName:  postgres-data
```

I seeded the database and then deleted the pod. The new pod retained the data

```bash
(.venv) user@MacBook-Air sre-intro % kubectl delete pod -l app=postgres --force --grace-period=0
...
(.venv) user@MacBook-Air sre-intro % NEW_POD=$(kubectl get pod -l app=postgres -o name | cut -d/ -f2)
kubectl exec $NEW_POD -- psql -h localhost -U quickticket -d quickticket -c 'SELECT count(*) FROM events; SELECT count(*) FROM orders'
 count
-------
     5
(1 row)

 count
-------
     0
(1 row)
```

The RTO improved because no restore step was needed

```
Disaster at      16:17:25
New pod ready    16:17:36
App fully up     16:17:51
```

RTO = 26 seconds, compared to 41 seconds without PVC

I also set up a CronJob that runs `pg_dump` every 5 minutes and keeps only the 5 newest backups

```bash
(.venv) user@MacBook-Air sre-intro % kubectl apply -f k8s/backup-cronjob.yaml
cronjob.batch/postgres-backup created
```

I ran the job manually and confirmed it created dump files

```bash
(.venv) user@MacBook-Air sre-intro % kubectl exec deployment/backup-inspector -- ls -la /backups
total 32
drwxrwxrwx    2 root     root          4096 Jul  9 11:22 .
drwxr-xr-x    1 root     root          4096 Jul  9 11:22 ..
-rw-r--r--    1 root     root          4967 Jul  9 11:22 quickticket_20260709_112213.dump
-rw-r--r--    1 root     root          4967 Jul  9 11:22 quickticket_20260709_112214.dump
-rw-r--r--    1 root     root          4967 Jul  9 11:22 quickticket_20260709_112230.dump
```

After 7 runs, only 5 files remained

```bash
(.venv) user@MacBook-Air sre-intro % for i in 3 4 5 6 7; do
  kubectl create job --from=cronjob/postgres-backup manual-$i
  kubectl wait --for=condition=Complete job/manual-$i --timeout=30s
done
kubectl exec deployment/backup-inspector -- ls -la /backups
total 48
drwxrwxrwx    2 root     root          4096 Jul  9 11:23 .
drwxr-xr-x    1 root     root          4096 Jul  9 11:22 ..
-rw-r--r--    1 root     root          4967 Jul  9 11:22 quickticket_20260709_112251.dump
-rw-r--r--    1 root     root          4967 Jul  9 11:22 quickticket_20260709_112254.dump
-rw-r--r--    1 root     root          4967 Jul  9 11:22 quickticket_20260709_112257.dump
-rw-r--r--    1 root     root          4967 Jul  9 11:23 quickticket_20260709_112300.dump
-rw-r--r--    1 root     root          4967 Jul  9 11:23 quickticket_20260709_112304.dump
```

The CronJob wrote dumps to the PVC and kept only the 5 newest files after 7 runs