# Lab 10 - SRE Portfolio & Reliability Review

## Load Test Results

I ran load tests with Locust inside the cluster.

| Users | Ramp | Duration | RPS   | p50   | p95   | p99   | 5xx Error Rate | Notes                  |
| ----- | ---- | -------- | ----- | ----- | ----- | ----- | -------------- | ---------------------- |
| 10    | 2/s  | 60s      | 7.7   | 8ms   | 16ms  | 37ms  | 0%             | Stable                 |
| 50    | 5/s  | 60s      | 31.24 | 170ms | 740ms | 990ms | 15.34%         | System started to fail |

### Breaking Point

The system started to degrade at about 50 users and around 31 RPS.

I saw more 500, 502 and 503 errors and higher response times.

---

## DORA Metrics

### Deployment Frequency

Medium.

I deployed new versions many times during the labs.

### Lead Time for Changes

Usually a few minutes.

After git push, ArgoCD deployed changes automatically.

### Change Failure Rate

Around 10% to 15%.

This is based on failed canary deployments and chaos testing.

### Time to Restore Service

Usually 10 to 30 seconds.

Argo Rollouts abort helped restore service quickly.

---

## Top 3 Reliability Risks

### 1. Downstream Service Problems

If payments or Redis has problems, gateway can also have problems.

Better circuit breakers are needed.

### 2. Database Storage Risk

Before adding PVC, PostgreSQL could lose data after pod restart.

### 3. Monitoring Gaps

Only error rate was monitored.

Latency and partial failures need more alerts.

---

## Toil Identification

| Toil                                  | Frequency       | Automation Idea          |
| ------------------------------------- | --------------- | ------------------------ |
| Manual port-forward                   | Many times      | Create script            |
| Manual Redis FLUSHDB                  | Every load test | Automatic cleanup        |
| Manual pod deletion for chaos testing | Several times   | Use Chaos Mesh or Litmus |

These tasks are repetitive and should be automated.

---

## Monitoring Gaps

Some monitoring improvements are still needed:

* No alert for high p99 latency on `/pay`
* No separate health alert for payments service
* No Redis health alert
* No circuit breaker metrics

---

## Capacity Plan for 2x Traffic

If traffic becomes two times higher, I would use:

* Gateway: 8 to 10 replicas
* Events: 4 to 5 replicas
* Payments: 4 replicas and circuit breaker
* Redis with replication

Estimated monthly cost would be around $30 to $50 for a small cluster.

---

## Most Important Improvement

The most important improvement would be:

**Add circuit breakers and latency SLO alerts.**

This would help detect problems faster and stop failures from affecting users.

---

## Final Thoughts

During this course I learned:

* Docker and containers
* Monitoring with Prometheus and Grafana
* Kubernetes
* GitOps with ArgoCD
* Canary deployments
* Chaos engineering
* Database backup and recovery
* Reliability engineering concepts

The labs helped me understand how modern SRE and DevOps systems work in practice.
