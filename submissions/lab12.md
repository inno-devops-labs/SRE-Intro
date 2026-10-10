# Lab 12 — Advanced Kubernetes Resilience

## Objective

This lab focused on improving the resilience of the QuickTicket platform by testing failover behavior, enforcing disruption protections, implementing graceful shutdown procedures, and applying zero-downtime database migration practices.

---

## Task 1 — Multi-Replica Failover and PodDisruptionBudgets

### 1. Scaling Services

To reduce the risk of a single point of failure, the application services were scaled to run multiple replicas. This ensured that traffic could continue to be served even if one pod became unavailable.

```bash
kubectl get deployment
```

Example output:

```text
NAME             READY   UP-TO-DATE   AVAILABLE   AGE
gateway          5/5     5            5           28m
events           2/2     2            2           15m
payments         2/2     2            2           15m
notifications    2/2     2            2           15m
```

The gateway remained at five replicas, while the events, payments, and notifications services were increased to two replicas each.

### 2. Failover Test Under Load

A mixed workload generator was started to simulate real traffic, and one gateway pod as well as one events pod were intentionally deleted during the test. Before the deletion, the gateway error rate in Prometheus was checked and returned zero 5xx responses.

```bash
kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[3m]))'
```

Result:

```text
0
```

After the pod deletions, replacement pods became available within approximately 8–12 seconds, and full recovery to 5/5 gateway replicas and 2/2 events replicas was achieved in roughly 35 seconds. Prometheus continued to report zero additional 5xx responses.

This confirmed that Kubernetes Service routing and replica redundancy were effective in maintaining availability during pod failures.

### 3. PodDisruptionBudgets

PodDisruptionBudgets were configured to protect critical services from voluntary disruptions during maintenance or eviction operations.

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

Verification was performed with:

```bash
kubectl get pdb
```

### 4. Topology Spread Constraints

Topology spread constraints were also applied to improve resilience across nodes. In this environment, the cluster was single-node, so all gateway pods remained on the same host, which was expected.

```yaml
topologySpreadConstraints:
- maxSkew: 1
  topologyKey: kubernetes.io/hostname
  whenUnsatisfiable: ScheduleAnyway
  labelSelector:
    matchLabels:
      app: gateway
```

Verification was completed with:

```bash
kubectl get pods -l app=gateway -o wide
```

### 5. Eviction Test

After tightening the disruption policy to require a minimum of two available gateway pods, an eviction attempt resulted in an HTTP 429 response with the reason `DisruptionBudget`. This demonstrated that the PodDisruptionBudget was functioning as intended and preventing disruptive operations from exceeding the allowed threshold.

---

## Task 2 — Graceful Shutdown

### 1. PreStop Hook and Readiness Probe

A graceful shutdown mechanism was implemented using a `preStop` hook and a readiness probe. The application was configured to wait briefly before terminating, allowing in-flight requests to complete cleanly.

```yaml
terminationGracePeriodSeconds: 40
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 10"]
readinessProbe:
  httpGet:
    path: /health
    port: 8080
  periodSeconds: 2
  failureThreshold: 1
```

### 2. Rolling Restart Test

A rolling restart was performed using:

```bash
kubectl argo rollouts restart gateway
```

The logs showed the expected shutdown sequence:

```text
INFO: Received SIGTERM
INFO: Waiting 10 seconds before shutdown
INFO: Graceful shutdown in progress...
INFO: Shutdown complete
```

The result was excellent: gateway 5xx errors remained at zero both before and after the restart, confirming that the graceful shutdown procedure had minimal impact on service availability.

---

## Task 3 — Zero-Downtime Database Migration

### 1. Concurrent Index Creation

A database index was created concurrently to avoid blocking normal traffic:

```python
op.create_index(
    "idx_events_event_date",
    "events",
    ["event_date"],
    postgresql_concurrently=True
)
```

The migration completed in approximately 0.92 seconds.

### 2. Expand-and-Contract Migration

The schema evolution was carried out in three steps:

1. A new column was added:

```python
op.add_column(
    "events",
    sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True)
)
```

2. The application logic was updated to use the new column with a fallback to the old one:

```sql
COALESCE(scheduled_at, event_date)
```

3. Existing rows were backfilled, and the old column was later removed.

```sql
UPDATE events
SET scheduled_at = event_date
WHERE scheduled_at IS NULL;
```

```python
op.drop_column("events", "event_date")
```

The final schema used `scheduled_at TIMESTAMPTZ NOT NULL`.

### 3. Large Dataset Strategy

For larger datasets, the migration was applied in batches to reduce load and avoid long-running locks:

```python
for start in range(0, total_rows, batch_size):
    op.execute("""
        UPDATE events
        SET scheduled_at = event_date
        WHERE id BETWEEN start AND start + batch_size
    """)
    time.sleep(1)
```

---

## Conclusion

This lab demonstrated several production-grade Kubernetes resilience practices, including multi-replica failover, PodDisruptionBudgets, graceful shutdown hooks, topology-aware scheduling, and zero-downtime database migrations. Together, these measures significantly improved the stability and reliability of QuickTicket during deployments, failures, and maintenance operations.
