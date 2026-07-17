# Lab 8 Report

## Task 1

### Experiment 1: Pod Kill Under Load

**Hypothesis:**  
If I delete one gateway pod while traffic is flowing, there will be a short burst of 5xx errors (~5–10 seconds), then recovery. Errors should be <20

**Method:**
```bash
user@MacBook-Air sre-intro % VICTIM=$(kubectl get pods -l app=gateway -o name | head -1)
user@MacBook-Air sre-intro % echo "Killing $VICTIM at $(date +%H:%M:%S)"
Killing pod/gateway-869df7b46c-82d6l at 15:53:08
user@MacBook-Air sre-intro % kubectl delete "$VICTIM"
pod "gateway-869df7b46c-82d6l" deleted
```

**Observations:**

```bash
user@MacBook-Air sre-intro % kubectl get pods -l app=gateway -w
gateway-869df7b46c-7j56c   1/1     Running   0            15s
gateway-869df7b46c-8fc7v   1/1     Running   1 (2d ago)   3d14h
gateway-869df7b46c-9mpjp   1/1     Running   1 (2d ago)   3d14h
gateway-869df7b46c-g2mw2   1/1     Running   1 (2d ago)   3d14h
gateway-869df7b46c-zpm5m   1/1     Running   1 (2d ago)   3d14h
```
New pod appeared with AGE 15s

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[3m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783421666.328,"994.7819173032293"]}]}}
```

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(increase(gateway_requests_total{status=~"5.."}[3m] offset 3m))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783421695.175,"706.7637565217391"]}]}}
```

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum+by+(pod)+(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[
{"metric":{"pod":"gateway-869df7b46c-8fc7v"},"value":[1783422458.086,"0.5604553144323884"]},
{"metric":{"pod":"gateway-869df7b46c-g2mw2"},"value":[1783422458.086,"0.9049362692158979"]},
{"metric":{"pod":"gateway-869df7b46c-zpm5m"},"value":[1783422458.086,"0.6838669007341206"]},
{"metric":{"pod":"gateway-869df7b46c-9mpjp"},"value":[1783422458.086,"0.6270833333333333"]},
{"metric":{"pod":"gateway-869df7b46c-7j56c"},"value":[1783422458.086,"0.6222040000000001"]}
]}}
```

**Comparison:**  
Hypothesis expected few errors (~10–20). Actual extra errors ~288. Service endpoint update slower than expected

**Improvement:**  
Reduce `terminationGracePeriodSeconds` and tune `readinessProbe` for faster endpoint removal

---

### Experiment 2: Payment Latency Injection

**Hypothesis:**  
With 2000 ms payment latency, p99 `/pay` latency rises to ~2 s, no 5xx errors (timeout 5000 ms), other paths unaffected.

**Method:**
```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_LATENCY_MS=2000
deployment.apps/payments env updated
user@MacBook-Air sre-intro % kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

**Observations:**

Error rate with 2000 ms latency:
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783422755.646,"0.803440024727009"]}]}}
```

p99 latency at 2000 ms:
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket[1m])))'
{"status":"success","data":{"resultType":"vector","result":[
{"metric":{"path":"/health"},"value":[1783422950.597,"0.12404833793003373"]},
{"metric":{"path":"/events"},"value":[1783422950.597,"0.056663923437736366"]},
{"metric":{"path":"/events/{id}/reserve"},"value":[1783422950.597,"0.09825050352010452"]}
]}}
```

Error rate with 6000 ms latency:
```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_LATENCY_MS=6000
deployment.apps/payments env updated
```
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783422967.125,"0.7965684682020819"]}]}}
```
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783422978.612,"0.7944161723217913"]}]}}
```

Restore:
```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_LATENCY_MS=0
deployment.apps/payments env updated
user@MacBook-Air sre-intro % kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
```

**Comparison:**  
Hypothesis not confirmed. Expected 0 errors at 2000 ms, got ~80%. Hidden timeout in system

**Improvement:**  
Audit timeouts across all services. Add monitoring and distributed tracing

---

### Experiment 3: Redis Failure

**Hypothesis:**  
If Redis goes down, `GET /events` and `/health` will work, `POST /reserve` will fail with 5xx.

**Method:**
```bash
user@MacBook-Air sre-intro % kubectl scale deployment/redis --replicas=0
deployment.apps/redis scaled
user@MacBook-Air sre-intro % kubectl get pods -l app=redis -w
redis-6b47846c46-lb7h7   1/1     Terminating   1 (2d1h ago)   3d19h
redis-6b47846c46-lb7h7   0/1     Completed     1 (2d1h ago)   3d19h
```

Check with mixedload pod (Redis down):
```bash
user@MacBook-Air sre-intro % kubectl exec deployment/mixedload -- sh -c 'echo "GET /events:"; curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" http://gateway:8080/events; echo "POST /reserve:"; curl -s -X POST -w "%{http_code} %{time_total}s\n" -H "Content-Type: application/json" -d "{\"quantity\":1}" http://gateway:8080/events/1/reserve; echo "GET /health:"; curl -s http://gateway:8080/health'
GET /events:
000 19.376627s
POST /reserve:
000 2.041349s
GET /health:
command terminated with exit code 7
```

Restore Redis:
```bash
user@MacBook-Air sre-intro % kubectl scale deployment/redis --replicas=1
deployment.apps/redis scaled
user@MacBook-Air sre-intro % kubectl wait --for=condition=Available deployment/redis --timeout=60s
deployment.apps/redis condition met
```

Verify:
```bash
user@MacBook-Air sre-intro % kubectl exec deployment/mixedload -- curl -s http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

**Comparison:**  
Hypothesis wrong. Gateway became completely unresponsive, not just `/reserve`

**Improvement:**  
Make Redis optional for read endpoints. Add circuit breaker and graceful degradation

---

## Task 2

**Scenario design:**  
Combined failures:
- Payments: 30% failure + 500 ms latency
- Events: DB_MAX_CONNS=3
- Load: mixedload scaled to 3 replicas

**Method:**
```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.3 PAYMENT_LATENCY_MS=500
deployment.apps/payments env updated
user@MacBook-Air sre-intro % kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
user@MacBook-Air sre-intro % kubectl set env deployment/events DB_MAX_CONNS=3
deployment.apps/events env updated
user@MacBook-Air sre-intro % kubectl rollout status deployment/events --timeout=30s
deployment "events" successfully rolled out
user@MacBook-Air sre-intro % kubectl scale deployment/mixedload --replicas=3
deployment.apps/mixedload scaled
user@MacBook-Air sre-intro % kubectl rollout status deployment/mixedload --timeout=30s
deployment "mixedload" successfully rolled out
```

**Observations (every ~1 min):**

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783493961.628,"0.8654174459325263"]}]}}
```
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket[1m])))'
{"status":"success","data":{"resultType":"vector","result":[
{"metric":{"path":"/health"},"value":[1783493962.207,"0.23221424953570824"]},
{"metric":{"path":"/events"},"value":[1783493962.207,"0.05324952271859395"]},
{"metric":{"path":"/events/{id}/reserve"},"value":[1783493962.207,"0.07074995909016527"]}
]}}
```

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783494003.460,"0.8635582242196351"]}]}}
```
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket[1m])))'
{"status":"success","data":{"resultType":"vector","result":[
{"metric":{"path":"/health"},"value":[1783494003.524,"0.23650000000110197"]},
{"metric":{"path":"/events"},"value":[1783494003.524,"0.08237535453768596"]},
{"metric":{"path":"/events/{id}/reserve"},"value":[1783494003.524,"0.04904998872636291"]}
]}}
```

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783494063.664,"0.8670121214830422"]}]}}
```
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket[1m])))'
{"status":"success","data":{"resultType":"vector","result":[
{"metric":{"path":"/health"},"value":[1783494063.741,"0.23000014545366393"]},
{"metric":{"path":"/events"},"value":[1783494063.741,"0.05112452953694177"]},
{"metric":{"path":"/events/{id}/reserve"},"value":[1783494063.741,"0.0730000454538843"]}
]}}
```

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783494123.218,"0.8686007932496342"]}]}}
```
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket[1m])))'
{"status":"success","data":{"resultType":"vector","result":[
{"metric":{"path":"/health"},"value":[1783494123.291,"0.2359999333321579"]},
{"metric":{"path":"/events"},"value":[1783494123.291,"0.07541665757599672"]},
{"metric":{"path":"/events/{id}/reserve"},"value":[1783494123.291,"0.04956250142041969"]}
]}}
```

```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total{status=~"5.."}[1m]))/sum(rate(gateway_requests_total[1m]))'
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1783494187.335,"0.8647260681263519"]}]}}
```
```bash
user@MacBook-Air sre-intro % kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
  'http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,+sum+by+(le,path)+(rate(gateway_request_duration_seconds_bucket[1m])))'
{"status":"success","data":{"resultType":"vector","result":[
{"metric":{"path":"/events"},"value":[1783494187.399,"0.06306250113630181"]},
{"metric":{"path":"/events/{id}/reserve"},"value":[1783494187.399,"0.04931249772733472"]},
{"metric":{"path":"/health"},"value":[1783494187.399,"0.21924997613272204"]}
]}}
```

Restore:
```bash
user@MacBook-Air sre-intro % kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0
deployment.apps/payments env updated
user@MacBook-Air sre-intro % kubectl rollout status deployment/payments --timeout=30s
deployment "payments" successfully rolled out
user@MacBook-Air sre-intro % kubectl set env deployment/events DB_MAX_CONNS=10
deployment.apps/events env updated
user@MacBook-Air sre-intro % kubectl rollout status deployment/events --timeout=30s
deployment "events" successfully rolled out
user@MacBook-Air sre-intro % kubectl scale deployment/mixedload --replicas=2
deployment.apps/mixedload scaled
user@MacBook-Air sre-intro % kubectl rollout status deployment/mixedload --timeout=30s
deployment "mixedload" successfully rolled out
```

**Which golden signal reacted first?**  
Error rate jumped to ~86-87% immediately after applying failures

**Which path shows worst latency amplification?**  
No path showed significant latency growth. p99 remained low (0.05-0.24s). System fails fast

**Weakest link:**  
Payments service – 30% failure rate caused 86% error rate. DB limit didn't matter because most requests failed before DB

**How to make more resilient:**  
Add circuit breaker for payments with fallback response. Implement retries with backoff. Scale payments replicas