# Lab 9 - Stateful Services & DB Reliability

## Task 1 - Migrations and Backup/Restore

### Alembic Setup

I initialized Alembic and created migration files.

Steps:

* initialized Alembic (`alembic init migrations`)
* created baseline for existing database schema
* created migration to add `email` column to events table

### Migration Under Load

I started mixed load testing while migration was running.

Migration:

```sql
ALTER TABLE events ADD COLUMN email VARCHAR(255);
```

The migration finished in less than 1 second.

There were no extra errors during the test because the new column was nullable.

### Backup and Restore

I created database backup:

```bash
pg_dump -Fc > /tmp/quickticket.dump
```

To simulate data loss, I removed one table:

```sql
DROP TABLE orders CASCADE;
```

Then I restored the database:

```bash
pg_restore --clean --if-exists
```

After restore, the tables were available again and the API worked normally.

### RPO Observation

* Before disaster: orders existed
* After DROP: orders missing
* After restore: orders returned

---

## Task 2 - Disaster Recovery Under Load

### Experiment

I deleted the PostgreSQL pod:

```bash
kubectl delete pod -l app=postgres --grace-period=0 --force
```

A new pod was created automatically.

Because storage was ephemeral, database data was lost.

I restored the database from backup and restarted the events service.

```bash
kubectl rollout restart deployment/events
```

### RTO and RPO

* RTO was about 1.5 to 2 minutes
* RPO depended on the last backup time, about a few minutes

### Conclusion

Without PersistentVolumeClaim, PostgreSQL data can be lost when the pod is recreated.

This is a serious problem for stateful applications.

---

## Bonus Task - Persistent Storage and Automated Backup

I added a PersistentVolumeClaim to PostgreSQL deployment.

Storage size:

```yaml
storage: 1Gi
```

I also created automated backups with CronJob.

* backup every 5 minutes
* keep last 5 backup files

### Disaster Recovery Test After PVC

After adding PVC, PostgreSQL started with existing data after pod recreation.

Recovery was much faster because restore from backup was not needed.

RTO became only the pod startup time.

---

## Final Thoughts

In this lab I learned:

* how to use Alembic migrations
* how to perform database migration under load
* how to use pg_dump and pg_restore
* how to measure RTO and RPO
* how PersistentVolumeClaim protects database data
* how automated backups improve reliability

The most important lesson for me was:

**Stateful services without persistent storage are very risky because data can be lost after pod recreation.**
