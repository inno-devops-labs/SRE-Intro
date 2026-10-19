# Lab 12 — Advanced Kubernetes Resilience

Author: Walkerino. Task 1, Task 2 and the expand-and-contract bonus are implemented and exercised on 2026-10-19. Lab 11's application and prerequisite infrastructure are included so this PR can be reviewed/applied against the original upstream main independently.

## Environment and reproduction

Three-node k3d `quickticket-lab1112`, Kubernetes v1.35.5+k3s1, Argo Rollouts v1.9.0, in-cluster Prometheus and two mixedload replicas. Gateway is a five-replica **Rollout**, not a Deployment. The three downstream services each have two replicas; PostgreSQL retains its PVC. Stock was increased to 100000 per event to avoid sold-out responses during extended checkout tests.

Follow [k8s/README.md](../k8s/README.md) for fresh-schema installation and the gated existing-database migration workflow. Install [requirements-labs.txt](../requirements-labs.txt), port-forward `svc/postgres` to 15432 and `monitoring/svc/prometheus` to 19091. All raw observations are in [evidence/lab12](evidence/lab12).

## Task 1: failover, PDB and topology spread

[Manifests](../k8s): events/payments/notifications have two replicas. Four `policy/v1` PDBs select `app=<service>`:

```text
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
events-pdb          1               N/A               1
gateway-pdb         2               N/A               3
notifications-pdb   N/A             1                 1
payments-pdb        1               N/A               1
```

The live gateway template contains:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

This cluster has three nodes, so the actual placement can be observed (see `lab12-placement.txt`), rather than only checking the field on a single-node cluster. `ScheduleAnyway` is a soft preference; it encourages balanced placement and permits scheduling when the preference cannot be met.

For real PDB enforcement, temporarily set `events-pdb.minAvailable=2`, wait for allowed disruptions to become zero, and POST an eviction through `kubectl proxy`:

```sh
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
kubectl wait --for=jsonpath='{.status.disruptionsAllowed}'=0 pdb/events-pdb
# POST /api/v1/namespaces/default/pods/<events-pod>/eviction
# body: policy/v1 Eviction with metadata.name and namespace=default
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":1}}'
```

Actual response (full JSON committed):

```json
{"http_status":429,"body":{"reason":"TooManyRequests","details":{"causes":[{"reason":"DisruptionBudget","message":"The disruption budget events-pdb needs 2 healthy pods and has 2 currently"}]},"code":429}}
```

The normal PDB was restored in a `finally` block. Direct pod deletion bypasses PDB admission; therefore the separate eviction test is necessary.

Under continuous checkout traffic, one gateway and one events pod were deleted together, then replacement readiness was checked. The one-minute 5xx increase was **0 before and 0 after** the coordinated deletion. The before/after Prometheus snapshots and the independent HTTP observer are committed as `lab12-failover-*.json` and `lab12-maintenance-observer.json`.

**How many evictions?** Three replicas with `minAvailable=1` allow two simultaneous voluntary evictions if all are healthy. Five gateways with `minAvailable=2` allow three, keeping enough critical-path capacity without blocking ordinary maintenance as aggressively as minAvailable=4.

**Placement?** Balanced placement for five replicas over three nodes is 2/2/1; seven gives 3/2/2. On a single node, the soft constraint still schedules all replicas there and cannot provide node-failure resilience. PDBs protect voluntary evictions, not hardware failures.

## Task 2: graceful shutdown and concurrent indexing

Gateway readiness checks `/health` every two seconds with failureThreshold=1. Its preStop runs `sleep 10`; terminationGracePeriodSeconds is 40 and `UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=30` bounds application draining. The sleep gives endpoint removal time to propagate while the process still serves existing connections. Downstream Deployments also get a five-second preStop delay to cover endpoint propagation when their replicas are replaced.

```sh
kubectl argo rollouts restart gateway
# Also wait for all pre-restart pod names to disappear, not just a Healthy phase.
kubectl argo rollouts status gateway --timeout 240s
```

The gateway's canary AnalysisRun completed successfully before maintenance tests. The restart also produced **0 before / 0 after** in the one-minute 5xx increase query. Restart and failover were observed through Prometheus and a separate client exercising `/events`, reserve and pay; their raw before/after snapshots are committed.

Migration [0003](../migrations/versions/0003_index_events_event_date.py) uses:

```python
with op.get_context().autocommit_block():
    op.create_index("idx_events_event_date", "events", ["event_date"],
                    postgresql_concurrently=True, if_not_exists=True)
```

Downgrade drops the index with `postgresql_concurrently=True, if_exists=True` inside the same autocommit wrapper. Live `upgrade 0003`, `downgrade 0002`, `upgrade 0003` all succeeded. PostgreSQL reported:

```text
idx_events_event_date | CREATE INDEX idx_events_event_date ON public.events USING btree (event_date)
```

The catalog prints the resulting index definition, not the historical CONCURRENTLY execution option; the migration source and successful execution establish that option. Prometheus's one-minute 5xx increase was **0 before and 0 after** the index experiment. The index is later intentionally replaced with `idx_events_scheduled_at` during the rename.

**Why CONCURRENTLY?** A normal CREATE INDEX holds a SHARE table lock that blocks writes while building the index; ordinary reads can continue. On a large table that can cause a long write outage. CONCURRENTLY permits ordinary reads/writes while building, requires extra work/scans and cannot execute inside a transaction block. An interrupted concurrent build can leave an invalid index: check `pg_index.indisvalid` before retrying; IF NOT EXISTS alone cannot repair one.

## Bonus: execute expand-and-contract under load

The actual sequence was:

1. **M1, 0004:** add nullable `scheduled_at TIMESTAMPTZ`.
2. **Deploy A, image v12a:** both SELECTs read `COALESCE(e.scheduled_at, e.event_date) AS event_date`; ORDER BY uses the same COALESCE. The application has no runtime INSERT/UPDATE of the date, so runtime dual-write is a no-op for this course app. Wait for readiness **and deletion of every old-version pod**.
3. **M2, 0005:** `UPDATE events SET scheduled_at=event_date WHERE scheduled_at IS NULL`, then make scheduled_at NOT NULL. The five-row backfill was inspected; every new value equals the old value.
4. **Deploy B, image v12b:** both SELECTs use `e.scheduled_at AS event_date`; ORDER BY uses scheduled_at. The public response still exposes `date`, keeping existing clients compatible. Again wait for every old-version pod to disappear.
5. **M3, 0006:** concurrently create the scheduled_at index, then drop event_date. Its dependent old index is removed by PostgreSQL. The final seed schema contains scheduled_at, email and the new index for fresh installations.

The [Deploy-A patch](../tools/lab12/events_deploy_a.patch) reconstructs the intermediate source from the checked-in final source. The [migration files](../migrations/versions) contain separate reversible steps; the README explicitly forbids running straight to head across the deployment gates on a live old application.

### Failure found, correction and repeat

The **first experiment failed** at contract: deployment rollout status returned while an old Deploy-A pod still drained existing HTTP connections. Dropping event_date at that moment caused six gateway 5xx responses. This is recorded in `lab12-rename.log`; the result is not presented as zero downtime.

The corrected workflow records old pod names before changing the image and waits for their deletion after rollout readiness:

```sh
OLD_EVENTS_PODS=$(kubectl get pods -l app=events -o name)
kubectl set image deployment/events events=quickticket-events:v12b
kubectl rollout status deployment/events --timeout=120s
for pod in $OLD_EVENTS_PODS; do
  kubectl wait --for=delete "$pod" --timeout=120s
done
# Only now run the contract migration.
alembic upgrade 0006
```

To repeat safely, downgrade to 0005 (restore/backfill event_date while Deploy B still reads scheduled_at), fully deploy the original event_date reader and wait for old pods to disappear, then downgrade to 0003. All data remained in the database. The complete corrected five-step sequence was then executed under the same live mixedload.

The original unfiltered counter sum was unsuitable for comparison because old gateway series expired after the earlier canary rollout. For the corrected test, the gateway pods stayed fixed and the query selected their stable `rs_hash=69f8f766cc`; numeric counter values were compared, excluding timestamps:

```text
stage       total gateway 5xx   delta from baseline
baseline    6                  0
M1          6                  0
Deploy A    6                  0
M2          6                  0
Deploy B    6                  0
M3/final    6                  0
```

The six pre-existing errors are retained in the baseline; **zero new errors occurred throughout the corrected sequence**. `increase()` alone can miss an error if a labeled series first appears at value 1, so stable-pod cumulative counter comparison is the primary proof here. Whole JSON responses are not diffed because query timestamps always change.

Before: `event_date TIMESTAMPTZ NOT NULL`, no scheduled_at. After:

```text
scheduled_at | timestamp with time zone | not null
idx_events_scheduled_at btree (scheduled_at)
# event_date is absent
```

On a separate temporary database, the final seed loaded successfully; stamping head, downgrading through all revisions to 0001 and upgrading back to head preserved all five dates. PostgreSQL confirmed the final scheduled_at index is valid (`indisvalid=t`). The temporary verification database was removed afterward. This tests migration reversibility without taking the live application backwards through incompatible schema states.

**Which reordered step breaks users?** Dropping event_date before every old reader has exited breaks both original code and Deploy A's COALESCE. Deploy A also cannot run before M1 because scheduled_at would not exist. Deploy B must wait for backfill/NOT NULL. In a real app with date writes, M1→dual-write deployment→backfill must account for every writer; the old NOT NULL column needs a compatibility strategy until contract.

**Why is backfill safe here?** Deploy A tolerates NULL values, and the conditional UPDATE does not overwrite rows already populated with a newer value. There are no runtime date writers in QuickTicket. Production writers must all dual-write before relying on that property.

**Production batching:**

```python
last_id = 0
while True:
    with transaction():
        ids = select_next_ids(after=last_id, limit=10_000)
        if not ids:
            break
        update_scheduled_at_from_event_date(ids, only_where_new_is_null=True)
        last_id = max(ids)
    sleep(0.05)  # outside the transaction; monitor lag/lock waits
```

**Why isn't the M3 downgrade enough for rollback safety?** Recreating and backfilling event_date only copies a point-in-time snapshot. A live Deploy-B writer could subsequently update scheduled_at alone, making event_date stale; code/schema rollback must be coordinated with dual writes or a compatibility trigger and a verified backfill. Schema downgrade must precede rolling old readers back, and dropping scheduled_at must wait until no Deploy-B readers remain.

Optional HPA YAML is supplied and server-validated with 5–12 replicas and CPU target 70%; it was not applied or claimed as a measured scaling experiment. All main manifests also pass Kubernetes server-side dry-run; five inherited resilience tests pass.

Final independent maintenance observer: `{"events:200": 1055, "reserve:200": 213, "pay:200": 213, "events:429": 10}`; errors `[]`. This separate 120-second run covered coordinated pod deletion and the rolling restart after the corrected migration. The earlier 300-second observer is retained separately and includes the failed first rename attempt.
