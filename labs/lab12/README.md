# Lab 12 reproduction notes

Use the `k3d-quickticket` context. Back up PostgreSQL before changing the schema.
Do not run `alembic upgrade head` against the live old application: deployments A
and B must be interleaved with the migration revisions.

1. Build/import the existing gateway and notifications images referenced by the
   manifests. Apply `k8s/events.yaml`, `payments.yaml`, `notifications.yaml`,
   `gateway.yaml`, and `pdb.yaml` **using the old events image initially**.
2. Create a dedicated events row with ample tickets. Set that row's ID in
   `mixedload.yaml` and `observer.yaml` (the captured run used event 6).
   Apply the mixedload Deployment. Create ConfigMap `lab12-observer` from
   `observe.py`, apply `observer.yaml`, and wait for Ready.
3. `python labs/lab12/snapshot.py NAME` captures independent client counters,
   Prometheus queries, and pod/image information in the submission evidence.
4. Run the pod-deletion, tightened-PDB eviction and gateway restart experiments.
   Restore `events-pdb.spec.minAvailable` to 1 after the rejected eviction.
5. Port-forward PostgreSQL to 15432 and set
   `DATABASE_URL=postgresql://quickticket:quickticket@localhost:15432/quickticket`.
6. Upgrade only to `b12000000001` (concurrent old-column index), inspect
   `pg_index.indisvalid`, then to `b12000000002` (expand).
7. Deploy A is the pre-Lab-12 events source plus `deploy-a.patch`. Build/import
   `quickticket-events:lab12-a`, set the Deployment image and imagePullPolicy
   Never, wait for both replicas and for every old pod to terminate.
8. Upgrade to `b12000000003` (backfill, NOT NULL, concurrent new-column index).
9. Build/import the final source as `quickticket-events:lab12-b`. Apply the final
   `k8s/events.yaml`, wait for both replicas **and old pod termination**.
10. Only now upgrade to `b12000000004` (contract). Check final schema and API
    response `date` compatibility. Compare observer counters and Prometheus
    **values**, not whole timestamped API JSON responses.
11. Remove the observer Deployment/ConfigMap after final evidence collection.

`deploy-a-to-b.patch` records the two actual application revisions. Image tags
are distinct so restarting a pod cannot silently reuse an earlier stage.
The final manifests are for the final schema, not the starting point of a replay.

On a fresh database, `app/seed.sql` supplies the final schema (including the
Lab 9 email column and scheduled_at index). After loading the seed, stamp
`b12000000004` with Alembic; historical migrations target the old schema and
must not be replayed over the final seed. Stamping is appropriate only after
verifying this final schema, never as a shortcut on an existing database.

PDBs constrain Eviction API operations, not direct pod deletion. The topology
spread constraint is a scheduling preference because it uses ScheduleAnyway.
A single-node placement cannot demonstrate node-loss resilience.

The optional HPA experiment applies `k8s/gateway-hpa.yaml` and
`labs/lab12/load-hpa.yaml`, with a `lab12-locustfile` ConfigMap built from
`labs/lab12/locustfile.py`. Collect HPA samples and Job logs, then delete the
HPA/Job/ConfigMap and restore the Rollout to five replicas. Rate-limit 429s
are expected under this stress and must be reported separately from the
normal-load migration evidence. Restore `labs/lab8/mixedload.yaml` after all
experiments; do not remove the database's confirmed orders.
