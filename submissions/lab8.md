# Lab 8 — Chaos Engineering and Resilience Improvement

## Environment and baseline

The work ran in the existing quickticket k3d cluster on 2026-10-04. Argo CD was changed to feature/lab8; its automated prune/self-heal policy was disabled only during injections and restored after recovery.

The supplied mixedload was applied with two replicas. I increased event 1 inventory to avoid stopping the full checkout flow during the long test windows.

~~~
UPDATE 1
 id | total_tickets
----+---------------
  1 |         10000
~~~

Baseline at 2026-10-04T12:49:49.6424396Z:

~~~
total RPS = 15.109527286539908
/events RPS = 4.5819507480031465
/events/{id}/reserve RPS = 4.600129921400586
/reserve/{id}/pay RPS = 4.436493888468962
5xx ratio = 0
p99 /events = 0.09350011591330598 s
p99 reserve = 0.17409978835747983 s
p99 pay = 0.07362498096534659 s
~~~

## Experiment 1 — gateway pod deletion

**Hypothesis, written at 2026-10-04T12:50:28.8050006Z:** deleting one of five gateway pods will leave four Service endpoints to serve traffic while the Rollout recreates it. Gateway metrics cannot prove that no transport request was lost before reaching a pod.

Victim gateway-55984f6bff-b2rdx was deleted at 2026-10-04T12:50:28.8223930Z.

~~~
2026-10-04T12:50:30.2453997Z replacement gateway-55984f6bff-ccdgn created, 0/1 ContainerCreating
2026-10-04T12:50:38.9155490Z replacement Running, 0/1 Ready
2026-10-04T12:50:43.5121394Z five gateway pods 1/1 Running
replacement creation time = 1.423 s
five-Ready recovery time = 14.690 s
~~~

The replacement events included a 902 ms image pull and an initial readiness connection refusal. After recovery, at 2026-10-04T12:52:11.5989006Z, sum(increase(gateway_requests_total{status=~"5.."}[3m])) was 0; all five pods had non-zero one-minute RPS from 2.836724674049425 to 3.4368010474060338. The application-level part of the hypothesis matched, but this does not prove no transport request was lost. I would add preStop connection draining before pod termination.

## Experiment 2 — payment latency

### 2000 ms

**Hypothesis, written at 2026-10-04T12:52:29.8954470Z:** PAYMENT_LATENCY_MS=2000 is below the 5000 ms gateway timeout, so /pay should slow without timeout 5xx. Sequential mixedload can lower RPS.

The injection started at 2026-10-04T12:52:29.9111693Z; payments health showed failure_rate 0.0 and latency_ms 2000.

~~~
sample=2026-10-04T12:53:18.7033446Z
/pay RPS: 200=2.491075052562756, 502=0
5xx ratio=0
p99 /events=0.024947882273828194 s
p99 reserve=0.07062512046111587 s
p99 /pay=2.4093475115797935 s
~~~

The hypothesis matched. /pay slowed while reads stayed low-latency; the lower endpoint RPS is also explained by the sequential checkout loop.

### 6000 ms

**Hypothesis, written at 2026-10-04T12:53:56.3435289Z:** PAYMENT_LATENCY_MS=6000 should generate payment timeouts. Histogram p99 need not equal exactly five seconds.

The injection started at 2026-10-04T12:53:56.3572290Z.

~~~
sample=2026-10-04T12:55:00.6898232Z
/pay RPS: 200=0.16365355554640237, 504=0.1904095562261778
5xx ratio=0.07041137619535495
p99 /pay=7.4609373970060595 s
p99 /events=0.024821428342583225 s
gateway log: POST /reserve/57067369-3842-4d54-89ae-e3d88244e9cb/pay HTTP/1.1 504 Gateway Timeout
~~~

This confirmed timeout protection. The p99 was 7.46 seconds, not an exact timeout measurement. Payments was restored at 2026-10-04T12:55:17.5076112Z to failure rate 0.0 and latency 0. I would add a /pay p99 alert because slow successful payments do not create errors.

## Experiment 3 — Redis outage

**Hypothesis, written at 2026-10-04T12:56:14.0110966Z:** Redis loss will make events degraded; the original /health probes will make events and gateway NotReady, so the Service may lose even the read path.

Redis was scaled to zero at 2026-10-04T12:56:16.1445767Z. At 2026-10-04T12:56:38.6273734Z, events and all gateway EndpointSlice entries had ready: false and serving: false.

~~~
GET /events: curl exit 7, HTTP 000, 1.022311 s
POST /reserve: curl exit 7, HTTP 000, 0.001773 s
GET /health: curl exit 7, HTTP 000, 1.015468 s
events restart count reached 4
~~~

This matched the cascade hypothesis. Redis was restored at 2026-10-04T12:58:36.3930986Z; Redis, events, five gateway endpoints, and health checks recovered. I would separate process liveness from dependency health.

## Task 2 — combined scenario

**Design and hypothesis, written at 2026-10-04T12:59:46.5799019Z:** payments failure rate 0.3, payments latency 500 ms, events DB_MAX_CONNS=3, and three mixedload replicas should make payment failures and /pay latency visible first. DB pool saturation is not a conclusion without a pool metric.

All injections started at 2026-10-04T12:59:46.5929090Z; rollouts completed. events_db_pool_size returned an empty Prometheus result in every sample.

| UTC timestamp | 5xx ratio | /pay p99 | /pay evidence |
|---|---:|---:|---|
| 13:00:30.697625Z | 0.02539311580124979 | 0.7494620849134319 s | endpoint RPS 3.973269987708861 |
| 13:01:38.493108Z | 0.08842444867294744 | 0.7475 s | 200=2.236536211799708; 500=1.0546257912972483 |
| 13:03:06.281467Z | 0.08943080296333461 | 0.7475 s | endpoint RPS 3.2366565557831097 |
| 13:03:26.414709Z | 0.08482859173183796 | 0.7475 s | 200=2.254781512453418; 500=1.0001038124970072 |
| 13:04:28.315332Z | 0.09569369637015189 | 0.7475 s | endpoint RPS 3.3457547381783597 |

The first changed golden signal was 5xx ratio. /pay was the worst-latency path; /events and reserve stayed under 0.12 seconds in these samples. Payments was the confirmed weakest link because its injected failures produced gateway 500s and its delay dominated checkout. DB pool saturation was not demonstrated. The scenario was restored at 2026-10-04T13:04:50.5509700Z to payments 0.0/0, events DB_MAX_CONNS=10, and mixedload 2.

## Bonus — probe fix and repeat

The Redis result confirmed a probe cascade. The Git-based fix changes liveness and readiness paths in k8s/events.yaml and k8s/gateway.yaml from /health to /metrics; /health still reports dependency degradation. The good rollout AnalysisRun gateway-5cdb8cf457-9-2 became Successful at 2026-10-04T13:08:44Z with [0].

The same Redis scale-to-zero test was repeated. Redis was scaled at 2026-10-04T13:10:11.1326338Z; sample 2026-10-04T13:10:33.5612097Z:

~~~
events_endpoint_ready=true
gateway_endpoint_ready=true
GET /events: HTTP 200, 0.021072 s
POST /reserve: HTTP 500, 0.006228 s
GET /health: HTTP 503, 2.023267 s
events_ready=true, events restarts=0
gateway_ready=5, phase=Healthy
~~~

| Metric | Before | After | Interpretation |
|---|---|---|---|
| Gateway /events | HTTP 000, curl exit 7 | HTTP 200, 0.021072 s | Read path remains routable. |
| Reserve | HTTP 000, curl exit 7 | HTTP 500, 0.006228 s | Redis-required operation fails explicitly. |
| Gateway health | HTTP 000, curl exit 7 | HTTP 503, 2.023267 s | Dependency degradation remains visible. |
| Events process | Endpoint false; 4 restarts | Endpoint true; 0 restarts | No restart cascade. |

The trade-off is that Kubernetes sees a process as Ready while /health reports a dependency outage. This preserves available reads, but checkout clients must use health or operation results. Redis was restored at 2026-10-04T13:11:41.4774385Z and dependency health returned healthy.

## Final recovery

Argo CD automated prune/self-heal was restored and it tracks feature/lab8:

~~~
target=feature/lab8
syncPolicy={"automated":{"prune":true,"selfHeal":true}}
sync=Synced health=Healthy
gateway Rollout: desired=5 current=5 up-to-date=5 available=5
mixedload: No resources found in default namespace.
Redis: 1/1 Running
~~~

Final health responses were captured at `2026-10-04T19:04:59.7275807Z`:

~~~
gateway={"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
events={"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
payments={"status":"healthy","failure_rate":0.0,"latency_ms":0}
~~~

Final real checkout:

~~~
reserve={"reservation_id":"42da0eb2-ce08-43f3-b78b-749b12465d5d","event_id":1,"quantity":1,"total_cents":5000,"expires_in_seconds":300}
payment={"order_id":"42da0eb2-ce08-43f3-b78b-749b12465d5d","event_id":1,"quantity":1,"total_cents":5000,"status":"confirmed"}
~~~

Prometheus remains running. The DB pool gauge was unavailable in the query results, so this report makes no claim about pool saturation. Gateway metrics cannot count a request that fails before reaching a process.

## Acceptance checklist

- [x] Three hypothesis-driven experiments with recovery
- [x] Pod-kill replacement and per-pod rate evidence
- [x] Payment latency at 2000 ms and 6000 ms
- [x] Redis outage with HTTP, EndpointSlice, and recovery evidence
- [x] Combined scenario observed for more than three minutes
- [x] Confirmed weakness fixed and same Redis test repeated
- [x] Argo CD, payments, dependencies, and load generators restored
