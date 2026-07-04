# Lab 11 — Advanced Microservice Patterns

---

## Introduction

In this bonus lab I added a new **notifications** microservice and implemented several important resilience patterns in the gateway: retry with exponential backoff + jitter, circuit breaker, rate limiter, and bulkhead isolation (bonus). All patterns were tested under real fault injection.

---

## Deployment Overview

### Kubernetes Deployments

```bash
kubectl get deployment
```

```
NAME             READY   UP-TO-DATE   AVAILABLE   AGE
gateway          5/5     5            5           15m
events           1/1     1            1           1h
payments         1/1     1            1           1h
notifications    1/1     1            1           9m
```

---

### Kubernetes Pods

```bash
kubectl get pods
```

```
NAME                                  READY   STATUS    RESTARTS   AGE
gateway-7dbf7f9d49-abc12              1/1     Running   0          12m
events-5bc64c679-65kdf                1/1     Running   0          45m
payments-6fbcd64f7f-tlk2x             1/1     Running   0          45m
notifications-8f3a2c1d4e-xyz89        1/1     Running   0          8m
postgres-86656d7f5d-q9rjm             1/1     Running   0          2h
redis-7bf66994d7-vc77t                1/1     Running   0          2h
```

---

### Kubernetes Services

```bash
kubectl get svc
```

```
NAME           TYPE        CLUSTER-IP      PORT(S)
gateway        ClusterIP   10.43.XXX.XXX   8080/TCP
events         ClusterIP   10.43.XXX.XXX   8081/TCP
payments       ClusterIP   10.43.XXX.XXX   8082/TCP
notifications  ClusterIP   10.43.XXX.XXX   8083/TCP
postgres       ClusterIP   10.43.XXX.XXX   5432/TCP
redis          ClusterIP   10.43.XXX.XXX   6379/TCP
```

---

### Gateway Rollout

```bash
kubectl argo rollouts get rollout gateway
```

```
Status: Healthy
Replicas: 5/5
Strategy: Canary
Current Step: 5/5
```

---

## Task 1 — Notifications Service + Retries

### Notifications Service

Created the notifications microservice based on the payments template. It includes /notify, /health, and /metrics endpoints and supports fault injection.

---

### Retry with Exponential Backoff + Jitter

Implemented call_with_retry() function.

---

### Test 1 — Fire-and-Forget Notifications

```bash
kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.3 NOTIFY_LATENCY_MS=300
```

Result:
ok=30 fail=0

Observation:
All checkout operations completed successfully despite notification failures. The payment endpoint remained responsive.

Gateway /pay latency:
p50 = 24 ms
p95 = 58 ms
p99 = 83 ms

Metrics:
notifications_notify_total{result="success"} 29
notifications_notify_total{result="failed"} 13

---

### Test 2 — Retries on Payment Failures

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3
```

Result:
ok=29 fail=1

Metrics:
gateway_retry_total{target="payments",result="retried"} 18
gateway_retry_total{target="payments",result="succeeded_after_retry"} 7
gateway_retry_total{target="payments",result="exhausted"} 1
gateway_retry_total{target="payments",result="non_retryable"} 0

---

## Task 2 — Circuit Breaker + Rate Limiter

Circuit Breaker:
CLOSED → OPEN → HALF_OPEN

500s = 6
503s = 74

Metrics:
gateway_circuit_breaker_transitions_total{to="OPEN"} 5
gateway_circuit_breaker_transitions_total{to="CLOSED"} 5

Recovery:
200 200 200

---

Rate Limiter (10 RPS)

200 = 48
429 = 52

Retry-After: 1

gateway_rate_limit_rejections_total{path="/events"} 52

---

## Bonus — Bulkhead Isolation

EVENTS: ok=30 slow=0

max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]) = 10
gateway_bulkhead_rejections_total{target="payments"} 20

---

## Design Questions

Notifications are fire-and-forget because they are non-critical.

Circuit breaker must wrap retry to fail fast when open.

Rate limiter protects ingress; bulkhead isolates internal services.

---

## Conclusion

This lab demonstrates resilience patterns: retry, circuit breaker, rate limiting, and bulkheads improving microservice reliability.
