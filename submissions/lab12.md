# Lab 12 — Advanced Kubernetes Resilience

## Task 1 — Multi-Replica Failover and PDBs

I scaled the services:

* events: 2 replicas
* payments: 2 replicas
* notifications: 2 replicas
* gateway: 5 replicas (Rollout)

I also created PodDisruptionBudgets:

* gateway-pdb: minAvailable 2
* events-pdb: minAvailable 1
* payments-pdb: minAvailable 1
* notifications-pdb: maxUnavailable 1

### Failover Test

I deleted one gateway pod and one events pod while load test was running.

Results:

* 5xx error rate stayed at 0
* Kubernetes created new pods automatically
* Traffic continued to work without problems

### topologySpreadConstraints

I added topologySpreadConstraints to the gateway Rollout.

On my single-node k3d cluster I could not see any difference, but the YAML configuration was correct.

### Eviction Test

I changed events-pdb to minAvailable: 2.

When I tried pod eviction, Kubernetes returned 429 with DisruptionBudget reason.

This showed that the PDB was working correctly.

---

## Task 2 — Graceful Shutdown and Zero-Downtime Migration

### preStop and readinessProbe

I added:

* terminationGracePeriodSeconds: 40
* preStop sleep 10
* readinessProbe on /health

### Rolling Restart Test

I ran:

```bash
kubectl argo rollouts restart gateway
```

while load test was running.

Result:

* 5xx error rate stayed at 0
* No visible downtime

### Concurrent Migration

I created a migration using:

```sql
CREATE INDEX CONCURRENTLY
```

The migration finished while traffic was running.

Error rate did not increase.

### Expand and Contract Example

Steps:

1. Add new column `scheduled_at`
2. Deploy application that writes to both columns
3. Backfill old data and make new column NOT NULL
4. Deploy application that only uses new column
5. Remove old column

This method helps avoid downtime when changing database schema.

---

## Conclusion

In this lab I learned:

* How to use multiple replicas for better availability
* How PodDisruptionBudgets protect running services
* How to use graceful shutdown
* How to do database changes without downtime
* How expand-and-contract migration works

The most important thing I learned is to always think about how changes affect real users and live traffic.
