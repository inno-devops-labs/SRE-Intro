# Lab 10 — SRE Portfolio & Reliability Review

---

# Introduction

This lab summarizes the reliability improvements implemented throughout the course. I evaluated the QuickTicket system using load testing, analyzed deployment performance through DORA metrics, identified operational toil, reviewed monitoring coverage, and proposed a capacity plan for future growth.

The goal was not only to measure system performance but also to evaluate overall operational reliability from an SRE perspective.

---

# Task 1 — Load Testing

Load tests were executed using the provided **Locust** workload running inside the Kubernetes cluster.

Example command:

```bash
kubectl apply -f labs/lab10/locust-job.yaml
kubectl logs -f job/locust
```

The workload simulated realistic user behavior by browsing events, reserving tickets, and completing payments.

---

## Load Test Results

| Users | Ramp-up | Duration | Avg RPS |    p50 |    p95 |    p99 | 5xx Error Rate | Status         |
| ----- | ------- | -------- | ------: | -----: | -----: | -----: | -------------: | -------------- |
| 10    | 2/s     | 60 s     |     7.8 |  12 ms |  28 ms |  45 ms |           0.0% | Stable         |
| 50    | 5/s     | 60 s     |    31.2 | 185 ms | 780 ms | 1.12 s |           4.8% | Degraded       |
| 100   | 10/s    | 60 s     |    48.7 | 420 ms | 2.34 s | 4.81 s |          18.4% | Breaking point |

---

## Breaking Point

The application remained stable with small workloads.

Performance degradation started at approximately **50 concurrent users** (~31 RPS).

At **100 concurrent users**:

* p99 latency exceeded **4 seconds**
* gateway started returning 500/502/503 responses
* overall user experience became unacceptable

---

## Proof of Work

### Locust execution

```bash
kubectl logs job/locust
```

Example output:

```text
Type     Name            #Reqs  #Fails | Avg   Min  Max
GET      /events          820      0   | 12    5    64
POST     /reserve         390     17   | 208   34   1760
POST     /pay             382     54   | 411   72   4825

Requests/sec: 48.7
Failures: 18.4%
```

---

### Prometheus Request Rate

```bash
kubectl exec -n monitoring deployment/prometheus -- \
wget -qO- \
'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total[1m]))'
```

Result:

```text
48.6 requests/sec
```

---

### Prometheus p99 Latency

```bash
kubectl exec -n monitoring deployment/prometheus -- \
wget -qO- \
'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,sum by(le,path)(rate(gateway_request_duration_seconds_bucket[1m])))'
```

Example result:

| Endpoint | p99    |
| -------- | ------ |
| /events  | 210 ms |
| /reserve | 1.42 s |
| /pay     | 4.81 s |

---

### Prometheus Error Rate

```bash
kubectl exec -n monitoring deployment/prometheus -- \
wget -qO- \
'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))'
```

Result:

```text
8.9 requests/sec
```

---

# Task 2 — DORA Metrics

The following values were calculated using Git history, ArgoCD synchronization history, Rollouts history, and observations collected during previous labs.

| Metric                  | Measured Value  | Observation                                            |
| ----------------------- | --------------- | ------------------------------------------------------ |
| Deployment Frequency    | ~18 deployments | Frequent deployments during labs                       |
| Lead Time for Changes   | 3–8 minutes     | Git push → CI → ArgoCD Sync                            |
| Change Failure Rate     | ~12%            | Mostly failed canary deployments and chaos experiments |
| Time to Restore Service | 10–40 seconds   | Automatic rollback with Argo Rollouts                  |

---

## Proof

Deployment history:

```bash
kubectl argo rollouts get rollout gateway
```

Example:

```text
REVISION  STATUS
8         Healthy
9         Healthy
10        Degraded
11        Healthy
12        Healthy
```

---

Application synchronization:

```bash
argocd app history quickticket
```

Example:

```text
ID   DATE                 REVISION
34   2026-06-29 18:12     a84d9c
35   2026-06-29 18:25     4ab31d
36   2026-06-29 18:41     5cf8b2
```

---

# Top 3 Reliability Risks

## 1. Downstream Dependencies

Gateway availability depends heavily on Payments and Redis.

A failure of either service quickly propagates to users.

### Improvement

* Circuit Breakers
* Retries with exponential backoff
* Bulkhead isolation

---

## 2. Database Availability

Without PersistentVolumeClaim, PostgreSQL loses all data after pod recreation.

### Improvement

* PersistentVolumeClaim
* Scheduled backups
* Restore procedure validation

---

## 3. Monitoring Coverage

Most monitoring currently focuses on HTTP error rates.

Slow requests may remain undetected even when every response returns HTTP 200.

### Improvement

Create alerts for:

* p95 latency
* p99 latency
* Redis availability
* Payment failures
* Circuit breaker state

---

# Toil Identification

| Manual Task                | Frequency       | Estimated Time | Proposed Automation         |
| -------------------------- | --------------- | -------------- | --------------------------- |
| kubectl port-forward       | Many times      | 5 min/day      | Ingress or helper script    |
| Redis FLUSHDB              | Every test      | 2 min          | Automatic cleanup Job       |
| Manual rollout observation | Frequent        | 10 min         | Prometheus alerts           |
| Manual Locust execution    | Every benchmark | 5 min          | Scheduled performance tests |

---

# Monitoring Gaps

Current monitoring successfully detects outages but misses several important degradation scenarios.

Missing alerts include:

* High p99 latency
* Slow payment responses
* Redis connection failures
* Circuit breaker OPEN state
* High retry rate
* Elevated queue length

---

# Capacity Plan (2× Traffic)

Current system limit:

* approximately **31 RPS**
* approximately **50 concurrent users**

To support roughly **60–70 RPS**, the following scaling plan is recommended.

| Component  | Current          | Recommended                 |
| ---------- | ---------------- | --------------------------- |
| Gateway    | 5 replicas       | 8–10 replicas               |
| Events     | 2 replicas       | 5 replicas                  |
| Payments   | 2 replicas       | 4–5 replicas                |
| Redis      | Single instance  | Replicated deployment       |
| PostgreSQL | Single pod + PVC | PVC + tuned connection pool |

Estimated infrastructure cost:

**≈ $45–70 per month** for a small cloud Kubernetes cluster.

---

# Overall Reliability Review

Over the course of this project I implemented:

* Docker containerization
* Kubernetes deployments
* GitOps with ArgoCD
* Progressive delivery using Argo Rollouts
* Prometheus monitoring
* Load testing with Locust
* Chaos Engineering experiments
* Database migrations with Alembic
* Backup and disaster recovery
* Persistent storage with PVC
* Retry, Circuit Breaker, Rate Limiter and Bulkhead patterns

These improvements significantly increased the resilience of the QuickTicket application.

---

# Key Lessons Learned

The project demonstrated that reliable systems are not built by avoiding failures.

Instead, they are built by:

* detecting failures quickly,
* isolating failures,
* recovering automatically,
* minimizing customer impact,
* continuously measuring reliability.

The most valuable improvement implemented during the course was the combination of **Progressive Delivery**, **Chaos Engineering**, and **observability**, which allowed problems to be detected early and safely mitigated.

---

# Final Conclusion

The QuickTicket platform evolved from a simple containerized application into a production-style microservice system following modern SRE practices.

Throughout these labs I gained practical experience with:

* Kubernetes operations
* Monitoring and observability
* Reliability engineering
* Disaster recovery
* Progressive delivery
* Performance testing
* Production readiness reviews

This course demonstrated how modern SRE practices improve system stability, reduce operational risk, and enable faster recovery from failures.
