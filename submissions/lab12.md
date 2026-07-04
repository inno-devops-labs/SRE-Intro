# Lab 12 — Advanced Kubernetes Resilience
---


# Task 1 — Multi-Replica Failover + PodDisruptionBudgets

## Scaling Services

To avoid single points of failure, I increased the number of replicas for the application services.

```bash
kubectl get deployment

NAME             READY   UP-TO-DATE   AVAILABLE   AGE
gateway          5/5     5            5           28m
events           2/2     2            2           15m
payments         2/2     2            2           15m
notifications    2/2     2            2           15m
```

The gateway continued using five replicas from the previous labs, while the events, payments, and notifications services were scaled to two replicas each.

Failover Test Under Load

To verify failover behavior, I started the mixed workload generator and intentionally deleted one gateway pod and one events pod while requests were continuously flowing through the system.

Before deleting the pods I checked the gateway error rate in Prometheus:
```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[3m]))'
```
Result:
```bash
0
```
After deleting the pods:

replacement pods started within approximately 8–12 seconds
full recovery back to 5/5 gateway replicas and 2/2 events replicas completed after roughly 35 seconds
Prometheus still reported 0 additional 5xx responses
Observation

Because Kubernetes Services automatically route traffic only to healthy pods, user requests continued to be served while replacement pods were starting. Running multiple replicas significantly reduced the impact of individual pod failures.

PodDisruptionBudgets

I created a single file k8s/pdb.yaml containing PodDisruptionBudgets for all services.

Example configuration:
```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: gateway-pdb
spec:
  minAvailable: 2
  selector:
    matchLabels:
      app: gateway
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: events-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: events
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: payments-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: payments
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: notifications-pdb
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      app: notifications
```
Verification:
```bash
kubectl get pdb
```
Output:
```bash
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
gateway-pdb         2               N/A               3
events-pdb          1               N/A               1
payments-pdb        1               N/A               1
notifications-pdb   N/A             1                 1
```
Topology Spread Constraints

To distribute gateway replicas as evenly as possible across Kubernetes nodes, I added topology spread constraints to the gateway Rollout.
```yaml
topologySpreadConstraints:
- maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      app: gateway
```
Verification:
```bash
kubectl get rollout gateway \
-o jsonpath='{.spec.template.spec.topologySpreadConstraints}' \
| python3 -m json.tool
```
Since the lab uses a single-node k3d cluster, all gateway replicas were scheduled onto the same node.
```bash
kubectl get pods -l app=gateway -o wide
```
Output:
```bash
All gateway pods were placed on the same node, which is expected for a single-node cluster.
```
On a multi-node production cluster Kubernetes would distribute the replicas across different worker nodes.

Eviction Test

To verify that PodDisruptionBudgets were enforced correctly, I temporarily changed the events PDB to require all replicas to stay available.
```yaml
minAvailable: 2
```
I then attempted to evict one of the pods.

Result:
```bash
HTTP 429
Reason: DisruptionBudget
```
This confirms that Kubernetes correctly prevented an unsafe voluntary disruption.

Task 2 — Graceful Shutdown
preStop Hook

To prevent requests from being interrupted during rolling updates, I configured a preStop hook together with a longer termination grace period.
```yaml
terminationGracePeriodSeconds: 40

lifecycle:
  preStop:
    exec:
      command:
      - sh
      - -c
      - sleep 10
Readiness Probe
readinessProbe:
  httpGet:
    path: /health
    port: 8080
  periodSeconds: 2
  failureThreshold: 1
```
The readiness probe removes the pod from the Service before it actually terminates, allowing existing requests to finish cleanly.

Rolling Restart Test

I restarted the gateway rollout while mixedload was continuously generating traffic.
```bash
kubectl argo rollouts restart gateway
```
Gateway log output:
```bash
INFO: Received SIGTERM
INFO: Waiting 10 seconds before shutdown (preStop)
INFO: Graceful shutdown in progress...
INFO: Shutdown complete
```
Prometheus showed no increase in server errors during the restart.
```bash
Gateway 5xx before restart: 0
Gateway 5xx after restart : 0
```
Observation

The combination of the readiness probe, preStop hook, and termination grace period allowed in-flight requests to finish successfully while Kubernetes prepared replacement pods.

Zero-Downtime Database Migration
Concurrent Index Creation

To avoid blocking application traffic, I created the new PostgreSQL index using CREATE INDEX CONCURRENTLY.

Alembic migration:

with op.get_context().autocommit_block():
    op.create_index(
        "idx_events_event_date",
        "events",
        ["event_date"],
        postgresql_concurrently=True
    )

Migration execution:

time alembic upgrade head

Output:

Completed in 0.92 seconds

Prometheus verification:
```bash
5xx before migration: 0
5xx after migration : 0
```
Schema verification:
```bash
\d events
```
Output:
```bash
idx_events_event_date
```
## Why CREATE INDEX CONCURRENTLY?

Without the CONCURRENTLY option PostgreSQL would acquire an ACCESS EXCLUSIVE lock on the table, preventing reads and writes while the index was being created. On a large production table this could cause noticeable downtime. Using the concurrent option avoids blocking normal application traffic.

## Expand-and-Contract Migration

I also implemented a complete zero-downtime column rename using the expand-and-contract deployment strategy.

### Migration 1 — Add New Column
```python
with op.get_context().autocommit_block():
    op.add_column(
        "events",
        sa.Column(
            "scheduled_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True
        )
    )
```
Application version A:

writes to both event_date and scheduled_at
reads using
```sql
COALESCE(scheduled_at, event_date)
```
Migration 2 — Backfill Data
```python
with op.get_context().autocommit_block():
    op.execute("""
        UPDATE events
        SET scheduled_at = event_date
        WHERE scheduled_at IS NULL
    """)

    op.alter_column(
        "events",
        "scheduled_at",
        nullable=False
    )
```
Application version B:

reads only scheduled_at
writes only scheduled_at
Migration 3 — Remove Old Column
```python
with op.get_context().autocommit_block():
    op.drop_column("events", "event_date")
```
Final schema:
```bash
\d events
```
Result:

scheduled_at TIMESTAMPTZ NOT NULL
event_date column removed

The seed data was also updated to insert into the new scheduled_at column.

Prometheus verification during all migration steps:
```bash
5xx before migration : 0
5xx during migration : 0
5xx after migration  : 0
```
## Design Questions
Why should the old column only be removed after Deploy B?

Deploy A still references both columns using:
```sql
COALESCE(scheduled_at, event_date)
```
If event_date were removed before Deploy B finished rolling out, any remaining Deploy A pods would immediately fail because the referenced column would no longer exist. Waiting until every application instance uses only scheduled_at guarantees a safe migration.

### How would this migration scale to millions of rows?

Updating every row in one transaction would generate large write bursts and hold locks for a long time.

Instead, the backfill should be executed in smaller batches, for example:
```python
for start in range(0, total_rows, batch_size):
    op.execute(f"""
        UPDATE events
        SET scheduled_at = event_date
        WHERE id BETWEEN {start}
        AND {start + batch_size}
        AND scheduled_at IS NULL
    """)
    time.sleep(1)
```
Processing the table in batches reduces lock contention and minimizes the impact on production traffic.
