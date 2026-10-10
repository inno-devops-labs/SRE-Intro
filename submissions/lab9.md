1. `alembic history` output showing the two revisions (baseline + email).
2. `\d events` output showing the new `email` column.
3. `time alembic upgrade head` output (elapsed time — expect <1s for nullable add).
4. Prometheus `5xx last 1min` before and after migration (should both be 0 or unchanged).
5. `ls -lh /tmp/quickticket.dump` + `pg_restore --list` output showing backup is valid.
6. Row counts **before disaster / after DROP / after restore** for events and orders.
7. Answer: "What's the RPO of your current setup (single `pg_dump`)? How would you improve it? (Hint: Bonus Task.)"