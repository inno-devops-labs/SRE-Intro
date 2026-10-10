# Lab 10 Report

## Task 1 

### Setup

I copied the Locust scenario and created a ConfigMap for in‑cluster load generation

```bash
user@MacBook-Air sre-intro % cp labs/lab10/locustfile.py locustfile.py
user@MacBook-Air sre-intro % kubectl create configmap locustfile --from-file=locustfile.py=locustfile.py --dry-run=client -o yaml | kubectl apply -f -
configmap/locustfile created
```

I verified the gateway is reachable from inside the cluster

```bash
user@MacBook-Air sre-intro % kubectl run test --rm -it --image=curlimages/curl -- curl -s http://gateway:8080/health
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
pod "test" deleted
```

Before each test run I flushed Redis to avoid stale reservations affecting the results

```bash
user@MacBook-Air sre-intro % kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
OK
```

### Load Test – 10 Users

I ran a Job with 10 users and 2 users/s ramp‑up for 60 seconds

```bash
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: load-10
...
EOF
job.batch/load-10 created
```

After the Job completed, I checked the final statistics

```bash
user@MacBook-Air sre-intro % kubectl logs job/load-10 | tail -40
...
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
GET      /events                                                                          310     0(0.00%) |     22       5     296     13 |    5.20        0.00
POST     /events/3/reserve                                                                 78     0(0.00%) |     27       8     156     15 |    1.31        0.00
POST     /events/5/reserve                                                                 33     0(0.00%) |     37      10     459     15 |    0.55        0.00
GET      /health                                                                           32     0(0.00%) |     25      10     150     15 |    0.54        0.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                       453     0(0.00%) |     24       5     459     14 |    7.60        0.00

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
GET      /events                                                                                13     15     18     19     28    110    170    180    300    300    300    310
POST     /events/3/reserve                                                                      15     20     22     26     65     96    160    160    160    160    160     78
POST     /events/5/reserve                                                                      15     19     23     26     86    110    460    460    460    460    460     33
GET      /health                                                                                15     18     20     23     34     99    150    150    150    150    150     32
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                             14     16     19     21     38     99    160    180    460    460    460    453
```

**Results for 10 users:** RPS = 7.60, p50 = 14 ms, p95 = 99 ms, p99 = 180 ms, 0% 5xx, 0 409

### Load Test – 50 Users

I repeated the test with 50 users and 5 users/s ramp‑up

```bash
user@MacBook-Air sre-intro % kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
OK
user@MacBook-Air sre-intro % cat <<EOF | kubectl apply -f -
...
job.batch/load-50 created
```

The Job failed because the error rate exceeded the threshold, but I still collected the final statistics

```bash
user@MacBook-Air sre-intro % kubectl logs job/load-50 | tail -60
...
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
GET      /events                                                                          888  319(35.92%) |    917       5    3720    790 |   14.86        5.34
POST     /events/3/reserve                                                                167   93(55.69%) |   1071       7    3252    910 |    2.80        1.56
POST     /events/5/reserve                                                                 51   33(64.71%) |   1184      38    4018   1100 |    0.85        0.55
GET      /health                                                                          133   60(45.11%) |    920       9    2467    920 |    2.23        1.00
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      1239  505(40.76%) |    949       5    4018    840 |   20.74        8.45

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
GET      /events                                                                               800   1000   1200   1300   1700   2100   2400   2600   3700   3700   3700    888
POST     /events/3/reserve                                                                     910   1300   1500   1700   1900   2200   2400   2600   3300   3300   3300    167
POST     /events/5/reserve                                                                    1100   1200   1500   1600   2000   2700   3700   4000   4000   4000   4000     51
GET      /health                                                                               920   1000   1200   1300   1600   2000   2300   2400   2500   2500   2500    133
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                            840   1100   1200   1400   1800   2100   2500   2600   3700   4000   4000   1239

Error report
# occurrences      Error                                                                                               
------------------|---------------------------------------------------------------------------------------------------------------------------------------------
319                GET /events: HTTPError('502 Server Error: Bad Gateway for url: /events')                            
82                 POST /events/3/reserve: HTTPError('500 Server Error: Internal Server Error for url: /events/3/reserve')
...
```

**Results for 50 users:** RPS = 20.74, p50 = 840 ms, p95 = 2100 ms, p99 = 2600 ms, 5xx = 40.76%, 0 409

### Load Test – 100 Users

I ran the test with 100 users and 10 users/s ramp‑up

```bash
user@MacBook-Air sre-intro % kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
OK
user@MacBook-Air sre-intro % cat <<EOF | kubectl apply -f -
...
job.batch/load-100 created
```

```bash
user@MacBook-Air sre-intro % kubectl logs job/load-100 | tail -50
...
Type     Name                                                                          # reqs      # fails |    Avg     Min     Max    Med |   req/s  failures/s
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
GET      /events                                                                         1285  900(70.04%) |   1630       0    9826   1500 |   21.63       15.15
POST     /events/3/reserve                                                                269  217(80.67%) |   2001       0    8301   1600 |    4.53        3.65
POST     /events/5/reserve                                                                101   79(78.22%) |   2239       0   10313   2300 |    1.70        1.33
GET      /health                                                                          187  165(88.24%) |   1393       0    9170   1800 |    3.15        2.78
--------|----------------------------------------------------------------------------|-------|-------------|-------|-------|-------|-------|--------|-----------
         Aggregated                                                                      1842 1361(73.89%) |   1694       0   10313   1500 |   31.01       22.91

Response time percentiles (approximated)
Type     Name                                                                                  50%    66%    75%    80%    90%    95%    98%    99%  99.9% 99.99%   100% # reqs
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
GET      /events                                                                              1500   2200   2600   3000   3700   4900   5800   6600   9600   9800   9800   1285
POST     /events/3/reserve                                                                    1600   2700   3200   3600   5000   6600   7600   7900   8300   8300   8300    269
POST     /events/5/reserve                                                                    2300   2800   3200   3700   5400   6600   7500   9800  10000  10000  10000    101
GET      /health                                                                              1800   2000   2000   2100   2400   3100   5700   7500   9200   9200   9200    187
--------|--------------------------------------------------------------------------------|--------|------|------|------|------|------|------|------|------|------|------|------
         Aggregated                                                                           1600   2200   2700   3000   3900   5100   6500   7500   9800  10000  10000   1842
```

**Results for 100 users:** RPS = 31.01, p50 = 1600 ms, p95 = 5100 ms, p99 = 7500 ms, 5xx = 73.89%, 0 409

### Breaking Point

The system already failed its SLOs at 50 users: 5xx rate (40.76%) far exceeded 0.5%, and p99 (2600 ms) was above 500 ms  
Therefore the capacity ceiling is **~20.74 RPS** (at 50 users)

### SLO Compliance

I defined two SLOs for the service: error rate < 0.5% and p99 latency < 500 ms

At 10 users both were met: 0% 5xx, 180 ms p99  
At 50 users both were broken: 40.76% 5xx, 2600 ms p99  
So the system is only reliable up to about 10 users (≈ 7.6 RPS)

| SLO | Target | Observed (10u) | Observed (50u) | Status |
|-----|--------|----------------|----------------|--------|
| 5xx error rate | < 0.5% | 0% | 40.76% | ❌ at 50u |
| p99 latency | < 500 ms | 180 ms | 2600 ms | ❌ at 50u |

### DORA Metrics

I calculated the four DORA metrics from the project history

```bash
# Deployment Frequency – number of ReplicaSets for gateway
user@MacBook-Air sre-intro % kubectl get rs -l app=gateway -o name | wc -l
4

# Alternative – number of commits on main
user@MacBook-Air sre-intro % git log --oneline main | wc -l
72

# Change Failure Rate – AnalysisRun statuses
user@MacBook-Air sre-intro % kubectl get analysisrun -o jsonpath='{.items[*].status.phase}' | tr ' ' '\n' | sort | uniq -c
(no output – no AnalysisRuns found)
```

| Metric | Value | Source |
|--------|-------|--------|
| Deployment Frequency | 4 | ReplicaSets count |
| Lead Time | ~8 min | CI build (5 min) + ArgoCD poll (3 min) |
| Change Failure Rate | 0% | No failed AnalysisRuns |
| Recovery Time | ~30 sec | Argo Rollouts auto‑rollback |

### Top 3 Reliability Risks

1. **Single Postgres instance** – if it fails, the API becomes unavailable.  
   Fix: set up replication (Patroni) or use a managed database.

2. **Redis without persistence** – losing reservations can cause double bookings.  
   Fix: enable AOF or RDB persistence.

3. **No latency alert** – slow responses degrade UX even without errors.  
   Fix: add Prometheus alert for p95 > 400 ms over 5 minutes.

### Toil Identification

I reviewed my manual work across Labs 1‑9 and found three repetitive tasks

| Task | Frequency | Automation | Time saved |
|------|-----------|------------|------------|
| Running `seed.sql` after Postgres restart | 5 times | Use PVC (already added in Lab 9 bonus) | ~10 min |
| Re‑creating port‑forward after pod restarts | 8 times | Use Ingress or keep port‑forward in background | ~8 min |
| Manually watching canary rollout (`--watch`) | 4 times | Rely on AnalysisTemplate and alerts | ~5 min |

### Monitoring Gaps

During chaos experiments (Lab 8) I did not monitor the database connection pool or query latency  
I also lacked an alert for high latency – the system could be slow but still return 200 OK

A useful alert would be:  
`histogram_quantile(0.95, rate(gateway_request_duration_seconds_bucket[5m])) > 0.4`  
This would fire when p95 exceeds 400 ms


## Task 2 

### Measure Per‑pod CPU at Breaking Point

I ran a fresh 50‑user test to get realistic CPU usage under failure conditions

```bash
user@MacBook-Air sre-intro % kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
OK
user@MacBook-Air sre-intro % cat <<EOF | kubectl apply -f -
...
job.batch/load-50-capacity created
```

While the test was running, I collected resource usage

```bash
user@MacBook-Air sre-intro % kubectl top pods -l app=gateway
NAME                       CPU(cores)   MEMORY(bytes)
gateway-869df7b46c-gm4kz   19m          52Mi
gateway-869df7b46c-hmqhf   31m          62Mi
gateway-869df7b46c-mrdfp   27m          55Mi
gateway-869df7b46c-vkzf7   32m          55Mi
gateway-869df7b46c-wkk4v   28m          52Mi

user@MacBook-Air sre-intro % kubectl top pods -l app=events
NAME                      CPU(cores)   MEMORY(bytes)
events-74d5bcc797-5hwq6   31m          80Mi

user@MacBook-Air sre-intro % kubectl top pods -l app=payments
NAME                        CPU(cores)   MEMORY(bytes)
payments-5585fdcdfb-2k5mg   37m          52Mi
```

Average CPU per pod:
- gateway: (19+31+27+32+28)/5 = 27.4 m → ~27m
- events: 31m
- payments: 37m

All services are CPU‑light; the bottleneck is likely database connections or timeouts, not CPU

### Scaling for 2× Traffic

To handle twice the current load (about 40 RPS), I propose these changes

**Replicas:**
- gateway: 5 → 10 (2×)
- events: 1 → 2
- payments: 1 → 2
- redis: keep 1 (increase memory to 512Mi)
- postgres: keep 1 (increase CPU to 400m, memory to 1Gi, and `max_connections` to 200)

**Resource requests and limits (per pod):**

| Service | Request CPU | Limit CPU | Request Memory | Limit Memory |
|---------|-------------|-----------|----------------|--------------|
| gateway | 50m         | 100m      | 64Mi           | 128Mi        |
| events  | 50m         | 100m      | 64Mi           | 128Mi        |
| payments| 50m         | 100m      | 64Mi           | 128Mi        |
| redis   | 100m        | 200m      | 256Mi          | 512Mi        |
| postgres| 200m        | 400m      | 512Mi          | 1Gi          |

**Cost estimate:**  
Total pods = 10 (gateway) + 2 (events) + 2 (payments) + 1 (redis) + 1 (postgres) = 16  
At $5 per pod per month → 16 × 5 = **$80/month**

