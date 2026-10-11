# Lab 10. QuickTicket reliability review

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), k3d v5.9.0, k3s v1.35.5+k3s1, kubectl v1.37.0, Locust 2.43.4 (`locustio/locust:2.43.4`), Argo Rollouts v1.10.0 (gateway is the 5 replica Rollout from Lab 7), ArgoCD v3.5.3, in-cluster Prometheus v3.11.2 scraping gateway pods every 5 s, Postgres on the Lab 9 PVC

Every number below comes from a capture file taken on 2026-10-11 between 20:50 and 21:09 local time (UTC+3). Load ran as Kubernetes Jobs inside the cluster against `http://gateway:8080`, with the provided `locustfile.py` unchanged (it is committed at the repo root). Redis was flushed before every run and the script refused to continue if `DBSIZE` was not 0 afterwards. During every run the script sampled `kubectl top` every 10 s, read the events container's `cpu.stat` before and after, and queried Prometheus for the per-pod split of gateway requests.

A run counts as **over the ceiling** when 5xx plus connection errors exceed 0.5 % of requests, or p99 exceeds 500 ms (the lab's definition). 409 is counted separately and never as a failure of the system.

---

## 1. SLO compliance

The SLOs are the ones from Lab 3: **availability**, 99.5 % of gateway requests without a 5xx, and **latency**, 95 % of gateway requests under 500 ms, both over 7 days.

The first thing I tried to read was a week of history from Prometheus, and there was none:

```plaintext
===== Prometheus coverage window [20:50:17.736] =====
startTime 2026-10-11T17:43:52.69442394Z retention 15d
head minTime 2026-10-11 17:43:59Z maxTime 2026-10-11 17:50:17Z series 105
prometheus image=prom/prometheus:v3.11.2 volumes=[{"configMap":{"defaultMode":420,"name":"prometheus-config"},"name":"config"}]
```

The Prometheus Deployment from Lab 7 has a volume for its config and none for `/prometheus`. The WSL VM shut down at 20:39:57 when it went idle, the pod's container restarted at 17:43:52 UTC, and the nine days of series behind Labs 7 to 9 were gone. So the SLO table is built from what I could still measure: today's runs, and the incidents recorded in the earlier lab reports.

| SLO | Target | Observed | Status |
|---|---|---|---|
| Availability at normal load (10 and 30 users, 7.8 and 22.5 RPS) | 99.5 % | 100 % (0 failures in 464 + 1345 + 1324 requests) | met |
| Latency at normal load | 95 % < 500 ms | p99 17 ms at 10 users, 96 ms at 30 users | met |
| Availability at the ceiling (40 users, 29 RPS) | 99.5 % | 98.84 % and 99.60 % in two runs | borderline |
| Availability over today's session (Prometheus, 23,338 requests incl. deliberate overload) | 99.5 % | 86.56 % | violated |
| Latency over today's session | 95 % < 500 ms | 83.56 % | violated |
| Lab 3: payments down 2 min 17 s | 35 failed requests per week | 21.4 failed requests, 61 % of the weekly budget | one incident |
| Lab 8: Redis down 87 s | | 73 s with no successful `GET /events` | would breach the latency and availability budget in one event |
| Lab 9: Postgres pod lost, with PVC | | 24.0 s end-to-end outage, 0 orders lost | within budget at 1000 req/day |

```plaintext
===== SLO compliance over the Prometheus window [21:08:59.315] =====
--- window 7d
requests (all paths):            23327.90007028232
requests 5xx:                    3133.770558803329
availability (non-5xx share):    0.8655680402878858
latency share < 500 ms:          0.8357000340603223
--- 5xx by path and status, 30d
    {'path': '/events', 'status': '502'} 1846
    {'path': '/events', 'status': '504'} 569
    {'path': '/events/{id}/reserve', 'status': '502'} 183
    {'path': '/events/{id}/reserve', 'status': '504'} 175
    {'path': '/health', 'status': '503'} 358
```

The session numbers are dominated by the 100 and 200 user runs, which were meant to break the system. They are still worth keeping: at the Lab 3 budget of 35 failed requests per week, one minute at 100 users (1536 failures) spends the budget 44 times over.

> The 5xx list above has no `status="500"` series at all, yet Locust received 441 responses with status 500 during the same session. The gateway's reserve handler calls `e.response.json()` inside its `except` block, events answers a crash with a plain-text 500, the JSON parse raises again, and that exception leaves the handler before the metrics middleware records anything. Server-side availability is therefore only an upper bound on what users got.

---

## 2. Load test results

### 2.1 Setup

```bash
git show upstream/main:labs/lab10/locustfile.py > locustfile.py
kubectl create configmap locustfile --from-file=locustfile.py=locustfile.py --dry-run=client -o yaml | kubectl apply -f -
```

```plaintext
locustfile.py at repo root = labs/lab10/locustfile.py from upstream
configmap/locustfile created
ConfigMap content verified against locustfile.py
imported and verified on node: docker.io/locustio/locust:2.43.4
```

Each level is the Job from the lab with `-u`, `-r` changed and `-t 60s --only-summary`. Before each run:

```plaintext
$ kubectl exec -i $(kubectl get pod -l app=redis -o name) -- redis-cli FLUSHDB
OK
redis DBSIZE after FLUSHDB: 0
inventory before the run:
  event 3: total=500 confirmed=0 held=0 reservable=500
  event 5: total=80 confirmed=0 held=0 reservable=80
job.batch/load-50 created
20:53:14.097 locust pod load-50-9kxcj: phase=Running podIP=10.42.0.183 node=k3d-quickticket-server-0 target=http://gateway:8080 (Service 10.43.92.21)
```

One practical detail: Locust exits with code 1 whenever any request failed, 409 included, so every Job above 10 users ends as `Failed` in Kubernetes even when the system was healthy. The script reads the summary from the logs instead of trusting the Job status.

### 2.2 The table (10.1 and 10.2)

`kubectl logs job/load-<N> | tail -40` for every level is in `lab10-capture-phase1.txt` and `lab10-capture-phase2.txt`. The rows below are parsed from those summaries; percentiles are Locust's aggregated column.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) | Verdict |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|---|
| 10  | 2/s  | 7.8  | 8 ms    | 12 ms    | 17 ms    | 0.00 % (0/464)       | 0   | healthy |
| 30  | 3/s  | 22.5 | 8 ms    | 19 ms    | 96 ms    | 0.00 % (0/1345)      | 0   | healthy |
| 40  | 4/s  | 28.8 | 9 ms    | 310 ms   | 540 ms   | 1.16 % (20/1720)     | 5   | over |
| 40 (repeat) | 4/s | 29.1 | 9 ms | 210 ms | 480 ms | 0.40 % (7/1742)     | 4   | at the edge |
| 50  | 5/s  | 35.4 | 8 ms    | 500 ms   | 990 ms   | 3.03 % (64/2115)     | 18  | over |
| 100 | 10/s | 43.2 | 910 ms  | 1900 ms  | 2300 ms  | 59.49 % (1536/2582)  | 0   | over |
| 200 | 20/s | 52.7 | 1100 ms | 5000 ms  | 7300 ms  | 57.24 % (1803/3150)  | 0   | over |

**Capacity ceiling: 40 users, about 29 RPS.** At 30 users the system is clean; at 40 users one run crossed both limits and the repeat landed just inside them. Everything above 40 users fails on both criteria.

The 50 user run, from the log:

```plaintext
         Aggregated                                                                      2115    82(3.88%) |     77       5    1413      8 |   35.42        1.37
         Aggregated                                                                              8     10     21     75    270    500    670    990   1300   1400   1400   2115
Error report
41                 GET /events: HTTPError('502 Server Error: Bad Gateway for url: /events')
4                  POST /events/5/reserve: HTTPError('500 Server Error: Internal Server Error for url: /events/5/reserve')
8                  GET /health: HTTPError('503 Server Error: Service Unavailable for url: /health')
10                 POST /events/3/reserve: HTTPError('500 Server Error: Internal Server Error for url: /events/3/reserve')
18                 POST /events/5/reserve: HTTPError('409 Client Error: Conflict for url: /events/5/reserve')
1                  POST /events/5/reserve: HTTPError('502 Server Error: Bad Gateway for url: /events/5/reserve')

events cgroup cpu.stat over 65s: avg cpu=104m  throttled periods=150/608 (25%)  throttled time=12.7s
events log during the run: PoolError=100 Traceback=50
    raise PoolError("connection pool exhausted")
```

Three things in this output explain the whole table.

**The failures come from the events DB pool, not from gateway.** Every 5xx traces back to `psycopg2.pool.PoolError: connection pool exhausted` in events. `ThreadedConnectionPool` does not wait for a free connection, it raises at once. `/reserve` holds one connection and then takes a second one inside `_get_available()`, so a pool of 10 serves at most 5 concurrent reserves while FastAPI runs up to 40 sync handlers in its thread pool. The gateway turns the crash into 502 on `/events`, a 500 on `/reserve`, and `/health` into 503 because events' own health check also needs a pool connection.

**The pool runs dry because events is starved of CPU.** Requests hold connections longer when the process is throttled. Throttling grew from 0 % of CFS periods at 10 users to 25 % at 50 and 94 % at 100 users, where `kubectl top` showed events pinned at its 200m limit:

```plaintext
10 users:  events cgroup cpu.stat over 66s: avg cpu=18m   throttled periods=0/522 (0%)    throttled time=0.0s
50 users:  events cgroup cpu.stat over 65s: avg cpu=104m  throttled periods=150/608 (25%) throttled time=12.7s
100 users: events cgroup cpu.stat over 64s: avg cpu=183m  throttled periods=582/617 (94%) throttled time=45.1s
```

**At 200 users the liveness probe made it worse.** `/metrics` stopped answering within its 1 s timeout, and kubelet killed events in the middle of the run:

```plaintext
events-6dd9c45f9-knmpx restarts=4 lastState=Error exit=137 started=2026-10-11T17:44:03Z finished=2026-10-11T17:56:33Z
200-user job window (epoch start end):
   17:55:35 -> 17:56:39 UTC
Warning   Unhealthy   pod/events-6dd9c45f9-knmpx   Liveness probe failed: Get "http://10.42.0.181:8081/metrics": context deadline exceeded (Client.Timeout exceeded w
Normal    Killing     pod/events-6dd9c45f9-knmpx   Container events failed liveness probe, will be restarted
PoolError=858 Traceback=429
```

The 200 user summary also shows the gateway's 5 s timeout as a wall: 474 `GET /events` ended in 504, and p95 sits at exactly 5000 ms.

> Throughput kept rising after the ceiling (35 → 43 → 53 RPS) while the success rate collapsed. Locust's RPS counts failures too, and fast failures are cheap, so "requests per second" is a misleading capacity number past the breaking point. Goodput (non-5xx RPS) peaked around 29 RPS at 40 users and fell to about 18 RPS at 100 users.

My first version of the script read events' log only from the current container. After the restart it reported `PoolError=0 Traceback=0` for the 200 user run, and the `cpu.stat` delta came out negative. Both were fixed (the script now reads `--previous` logs after a restart and refuses to print a delta when counters go backwards). The numbers above for 200 users come from the corrected capture `lab10-capture-phase1b.txt`.

### 2.3 Why in-cluster, measured

The lab warns against load-testing through `kubectl port-forward svc/gateway`. I ran the comparison instead of assuming it, reading each gateway pod's own `/metrics` before and after so the split is exact. The script's counter regex skips paths containing `{id}`, so the per-pod numbers below cover `GET /events` only; they match Locust's `/events` count exactly (905 and 905).

```plaintext
port-forward: 300 x GET /events in 4.9s
  per pod, port-forward:
    gateway-9ffb8f8d9-74cx9     300  100.0%
    gateway-9ffb8f8d9-d5v56       0    0.0%
    gateway-9ffb8f8d9-kb5rp       0    0.0%
    gateway-9ffb8f8d9-lbhpk       0    0.0%
    gateway-9ffb8f8d9-zsnrz       0    0.0%
in-cluster via Service: 300 x GET /events in 2.5s
  per pod, Service from inside the cluster:
    gateway-9ffb8f8d9-74cx9      50   16.7%
    gateway-9ffb8f8d9-d5v56      68   22.7%
    gateway-9ffb8f8d9-kb5rp      69   23.0%
    gateway-9ffb8f8d9-lbhpk      54   18.0%
    gateway-9ffb8f8d9-zsnrz      59   19.7%
```

Then the same 30 users with the same locustfile, once through the port-forward (Locust in Docker on the host) and once as an in-cluster Job:

| Path | RPS | p50 | p95 | p99 | 5xx | gateway pods used |
|---|---:|---:|---:|---:|---:|---|
| `kubectl port-forward svc/gateway` | 21.9 | 13 ms | 67 ms | 140 ms | 0 | 1 of 5 (905 of 905 `/events`) |
| in-cluster Job through the Service | 22.1 | 8 ms | 15 ms | 30 ms | 0 | 5 of 5 (from 9.7 % to 34.2 % each) |

The port-forward sends every request to one pod and adds its own tunnel (one `connection reset by peer` in its log). In this system it hardly moved the ceiling, because the bottleneck is events behind all five gateways, not gateway itself. It did make latency 4.7 times worse at p99 and left 4 of 5 replicas untested. A port-forward test would have measured one gateway pod plus the tunnel, and its latency would have pointed at gateway.

The in-cluster split is uneven (9.7 % to 34.2 %) for a reason that matters when sizing gateway. kube-proxy balances **connections**, and each Locust user keeps one keep-alive connection for the whole run, so 30 users means 30 random picks among 5 pods. The 300 sequential `urllib` requests, which open a new connection each time, came out close to even.

> During the first runs one pod, `gateway-9ffb8f8d9-lbhpk`, got no traffic at all while it was Ready and listed in the EndpointSlice. Its counter stayed at 53 for the whole 10 user run, and the 300 new-connection requests in the first control skipped it too, which cannot happen by chance. It started receiving its share at about 20:53:10, nine minutes after the cluster came back from the WSL restart in which its sandbox and IP changed. I could not find the cause in the k3s logs, and by the time I looked, iptables had all five endpoints. "5 replicas Ready" did not mean 5 replicas serving, and nothing in the monitoring would have shown it.

---

## 3. DORA metrics

Source data: `lab10-capture-phase0.txt` (Git, ArgoCD history, ReplicaSets, AnalysisRuns), the GitHub Actions page of the fork, and the Lab 5 and Lab 7 reports for events that happened on the earlier cluster.

```plaintext
$ kubectl get rs -l app=gateway -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | wc -l
3
  rollout.argoproj.io/revision=13
  gateway-5849674d98 rev=9 created=2026-10-03T15:40:07Z replicas=0
  gateway-64768b6dcb rev=11 created=2026-10-03T15:42:33Z replicas=0
  gateway-6f98c95cf9 rev=7 created=2026-10-03T15:37:55Z replicas=0
  gateway-9ffb8f8d9 rev=13 created=2026-10-03T16:34:38Z replicas=5
$ git log --oneline main | wc -l
43
commits I authored, all branches: 26
```

The lab's ReplicaSet count prints 3 for 4 ReplicaSets: `tr` leaves no newline after the last name and `wc -l` counts newlines. It also only sees the last 3 old revisions (`revisionHistoryLimit: 3`); the Rollout annotation says revision 13.

```plaintext
===== DORA: lead time, ArgoCD sync history vs commit time =====
https://github.com/KORTEZZ12/SRE-Intro.git path=k8s rev= automated=
  sync#0 rev=a417307 committed=2026-09-21T13:46:38+03:00 deployed=2026-09-23T21:03:43Z lead=209825s  "Revert "feat: deploy new gateway version""
  sync#1 rev=f86f617 committed=2026-09-24T00:52:15+03:00 deployed=2026-09-23T21:55:34Z lead=199s  "feat: stamp the gateway deployment for a GitOps round trip"
  sync#2 rev=200b58e committed=2026-09-23T22:09:15Z deployed=2026-09-23T22:13:03Z lead=228s  "ci: update image tags to d300862309bb539f5f735671cbf51d3a896"
  sync#3 rev=6b947f5 committed=2026-09-24T20:46:33Z deployed=2026-09-24T20:48:58Z lead=145s  "ci: update image tags to a2dfee5f0663bd645857f685c294c0646bd"
  sync#4 rev=6b947f5 committed=2026-09-24T20:46:33Z deployed=2026-09-24T20:52:48Z lead=375s  "ci: update image tags to a2dfee5f0663bd645857f685c294c0646bd"

$ kubectl get analysisrun -o jsonpath='{.items[*].status.phase}' | tr ' ' '\n' | sort | uniq -c
      1 Failed
      1 Successful
  gateway-5849674d98-9-2 Failed created=2026-10-03T15:40:35Z values=[0.4432989690721649] [0.47619047619047616]
  gateway-9ffb8f8d9-13-2 Successful created=2026-10-03T16:35:05Z values=[0] [0] [0]
```

`sync#0` is the first sync of a freshly created cluster, not a lead time, and `sync#4` re-applied the same revision. For the two `ci:` commits the lead time has to start at the human commit that triggered CI: d300862 at 22:08:17Z deployed 22:13:03Z (286 s), a2dfee5 at 20:45:39Z deployed 20:48:58Z (199 s). CI runs on the fork: 9 runs, all `completed successfully`, 40 s to 59 s each (the first run 1 m 59 s).

| Metric | Value | Source | DORA band (2023) |
|---|---|---|---|
| Deployment frequency | 38 workload revisions on this cluster in 18 days (gateway Rollout 13, events 12, payments 9, postgres 3, redis 1), about 2 per day; only 4 distinct revisions went through CI and ArgoCD | ReplicaSet annotations, ArgoCD history | on demand (elite), but mostly by hand |
| Lead time for changes | 199 s, 199 s, 286 s from commit to running pods (CI about 50 s, the rest ArgoCD's 180 s poll) | ArgoCD history vs `git show -s --format=%cI` | elite (< 1 day) |
| Change failure rate | 4 of 20 gateway releases failed (20 %): the Lab 5 missing image tag, the Lab 7 bad version, the Lab 7 canary that never became Ready, the AnalysisRun `Failed` above. All 4 were broken on purpose; unplanned failures 0 | 12 Rollout changes + 8 CI-built commits on fork `main`; Lab 5 and Lab 7 reports | 16-30 % as counted, 0 % unplanned |
| Time to restore | canary abort: users off the bad version in 0.6 s, full stable capacity in 9.3 s; automatic abort 107.6 s after apply; `git revert`: 189.8 s | Lab 7 and Lab 5 measurements | elite (< 1 hour) |

> The lead time is elite and the deployment path is mostly not the pipeline. Only 8 of the 38 revisions on this cluster were created by ArgoCD (their creation times equal the sync times 21:03:43Z, 22:13:03Z and 20:48:58Z in `lab10-capture-state.txt`); the other 30 came from `kubectl apply`, `patch`, `scale` or `set env` in Labs 7 to 10. ArgoCD has been `OutOfSync` on 6 objects since Lab 7 with auto-sync suspended. The Lab 5 bad deploy is the honest recovery number for that mode: nobody was paged when ArgoCD showed `Degraded`, and the revert landed 11 h 55 min later (01:51 to 13:46). No user saw an error, because the old ReplicaSet kept serving.

---

## 4. Top 3 reliability risks

1. **events is a single, CPU-capped instance with a pool that fails instead of waiting.** Why it matters: it limits the whole product to about 29 RPS, and past that it fails fast with 5xx on reads as well as writes (Section 2). The liveness probe on a 1 s timeout then kills it under load, which adds an outage on top of the overload. Fix: run 3 replicas (Task 2 shows 2 replicas double the ceiling), make `/reserve` reuse its connection instead of taking a second one in `_get_available`, wrap `getconn` in a short wait (a semaphore sized to the pool) so a burst queues instead of crashing, and give liveness `timeoutSeconds: 3, failureThreshold: 6` so CPU starvation alone cannot trigger a restart.
2. **Data safety is weaker than Lab 9 left it.** There is no backup CronJob and no `postgres-backups` PVC in the cluster today (`kubectl get cronjob -A`: `No resources found`), so RPO is unbounded again. Redis keeps reservations and the `event:N:held` counters in memory only, and those counters are wrong anyway: they are incremented on every reserve and never decremented, so each paid ticket is counted twice. Fix: put `k8s/backup-cronjob.yaml` and the backup PVC into the GitOps path so they cannot silently disappear, run the weekly restore test from the handbook, and fix the held accounting (decrement on confirm and on expiry, or derive held from live reservation keys).
3. **What runs is not what Git says, and the monitoring cannot tell.** ArgoCD is `OutOfSync` (`Deployment/gateway`, `Deployment/events`, `Deployment/postgres`, `Service/gateway`, `Service/postgres`, `ConfigMap/postgres-seed`) with auto-sync off; Prometheus scrapes only gateway and has no volume. Today that meant the bottleneck service had no metrics at all, a pod received no traffic unnoticed, and nine days of history disappeared in one VM restart. Fix: merge the lab branches into fork `main`, re-enable `selfHeal`, give Prometheus a PVC, and scrape events and the kubelet's cAdvisor endpoint (Section 6).

---

## 5. Toil identification

| Toil | How often | How to automate | What it saves |
|---|---|---|---|
| Resetting ticket inventory by hand: `UPDATE events SET total_tickets=...`, `redis-cli DEL event:1:held`, `FLUSHDB` | Lab 8 once per experiment set, Lab 9 before each of 3 disaster tests, Lab 10 before every one of 12 load runs | Fix the double count in events (decrement `held` on confirm and expiry); until then, a Locust `@events.test_start` hook or an initContainer in the load Job that flushes Redis and checks `DBSIZE` | 15+ manual steps per lab and, more importantly, the silent failure mode where a load generator stops exercising `/pay` after 20 seconds (Lab 8) |
| Bringing the environment back after the WSL VM sleeps: start the k3d cluster, wait for the API, re-create port-forwards, discover that Prometheus history is gone | every session since Lab 4; today the VM powered off at 20:39:57 between two commands and `runc` then failed to start the k3s container (`unable to start unit docker-...scope`) | `vmIdleTimeout=-1` in `.wslconfig` while working, a `make up` script (start cluster, wait for `/readyz`, check Rollout health, print a summary), a PVC for Prometheus | about 5 minutes per session and a whole category of "numbers changed because the VM restarted" |
| Applying manifests by hand because ArgoCD auto-sync is suspended (`kubectl apply/patch/scale/set env`) | 30 of 38 revisions on this cluster | merge each lab branch into fork `main` and let ArgoCD apply it; experiments use a separate namespace or an ArgoCD `ignoreDifferences` entry | removes drift (6 objects `OutOfSync` today) and gives every change a commit, a CI run and a revert path |

---

## 6. Monitoring gaps

**What I wished I had during the Lab 8 chaos experiments.**

- **events metrics.** events already exposes `events_db_pool_size` and request counters on `/metrics`, but Prometheus only scrapes `app=gateway`. In Lab 8 Task 2 the pool errors were invisible, and today the component that set the ceiling had no series at all.
- **CPU throttling.** `kubectl top` at the ceiling showed events at 98m of a 200m limit, which reads as "half used". The cgroup said 15 % of periods were throttled in the same run, and 94 % at 100 users. `container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total` from cAdvisor is the series that shows it.
- **A client-side SLI.** The Lab 8 probe found that the Redis outage was invisible in gateway metrics because the Service refused traffic before any gateway saw it. Today 441 responses with status 500 never reached `gateway_requests_total`. A blackbox probe or a small in-cluster prober should be the availability SLI, with gateway metrics as the explanation.
- **Restarts and pod traffic share.** There is no kube-state-metrics, so the liveness kill at 17:56:33 shows only in `kubectl get events`, and the gateway pod that received no traffic while Ready showed nowhere.

**What alerts would have caught what actually broke.**

| What broke | Alert |
|---|---|
| events pool exhaustion (Lab 8 Task 2, Lab 10 at 40+ users) | `max(events_db_pool_size) >= 10 for 1m` (pool fully used), plus any `PoolError` log line |
| Slow but successful (Lab 8 E2 at 2000 ms, Lab 10 with `DB_MAX_CONNS=40`: 0 % 5xx, p99 1.3 s) | latency SLO burn: `1 - sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(gateway_request_duration_seconds_count[5m])) > 0.05 for 5m`. Lab 6 alerts only on error rate and would stay silent |
| `/pay` 504 with real charges and no orders (Lab 8 E2 at 6000 ms) | per-endpoint 5xx ratio on `/reserve/{id}/pay` > 10 % for 2m (the Lab 6 postmortem action item) |
| Liveness restarts under load | `increase(kube_pod_container_status_restarts_total[10m]) > 0` |
| A replica receiving no traffic | `sum by (pod) (rate(gateway_requests_total[5m])) == 0 and on() sum(rate(gateway_requests_total[5m])) > 1` |
| Prometheus losing its history | `time() - prometheus_tsdb_lowest_timestamp_seconds < 86400` (less than a day of data) |

---

## 7. Capacity plan (summary; numbers in Task 2)

- **Current ceiling: about 29 RPS** (40 users with the provided task mix), set by the single events replica.
- **For 2× traffic (about 60 RPS):** events 3 replicas, gateway 3, payments 2, postgres 1 with more CPU, redis 1. Measured: 2 events replicas carry 59 RPS but only at the edge (0.57 % 5xx, p99 500 ms), so the third replica is the headroom.
- **Cost:** 10 pods at $5 per pod per month = **$50/month**, against $45 today for 9 pods. Gateway goes from 5 to 3 replicas, which pays for the extra events replicas.

---

## Task 2. Capacity plan with numbers

### 10.7 Per-pod CPU at the breaking point

`kubectl top` sampled every 10 s during the 40 user run with one events replica (the ceiling). The sample at +42.8 s, during steady load:

```plaintext
  -- 21:00:26.259 +42.8s
     events-6dd9c45f9-knmpx                  98m     48Mi
     gateway-9ffb8f8d9-74cx9                 37m     45Mi
     gateway-9ffb8f8d9-d5v56                 37m     46Mi
     gateway-9ffb8f8d9-kb5rp                 15m     44Mi
     gateway-9ffb8f8d9-lbhpk                 35m     44Mi
     gateway-9ffb8f8d9-zsnrz                 42m     45Mi
     payments-65558445c-x967b                 8m     36Mi
     postgres-5576b6c8b7-mp6n2               36m     24Mi
     redis-6cf6c6f989-57zg5                  10m      4Mi
     load-40-e1-bsgtr                        51m     39Mi  (load generator)
events cgroup cpu.stat over 74s: avg cpu=77m  throttled periods=63/651 (10%)  throttled time=4.5s
events log during the run: PoolError=10 Traceback=5
```

and at 100 users, past the ceiling:

```plaintext
  -- 20:55:04.187 +43.3s
     events-6dd9c45f9-knmpx                 200m     57Mi
events cgroup cpu.stat over 64s: avg cpu=183m  throttled periods=582/617 (94%)  throttled time=45.1s
```

All limits are `200m` (`cpu.max` in the events container: `20000 100000`, 20 ms of CPU per 100 ms period). Per pod at the ceiling:

| Service | CPU at ceiling | of limit | Verdict |
|---|---:|---:|---|
| events (1) | 77 to 98m, throttled in 10 to 15 % of periods; 200m and 94 % throttled at 100 users | 50 %, bursting to 100 % | **the constrained one** |
| gateway (5) | 15 to 42m each | 8 to 21 % | idle; 5 replicas is more than this load needs |
| postgres | 36m | 18 % | headroom |
| payments | 8m | 4 % | idle (the locustfile never calls `/pay`) |
| redis | 10m | 5 % | idle |

> `kubectl top` says events uses half its limit at the ceiling, and the cgroup says it is throttled. metrics-server averages over its scrape window, while CFS enforces the limit every 100 ms. A Python process that handles a burst of requests spends its 20 ms quota early in the period and waits out the rest of it with connections checked out. That is how a pool of 10 runs dry on a pod that looks half idle.

**Control experiments, same 40 users unless stated.** I changed one thing at a time to see which change moves the ceiling:

```plaintext
===== phase 2 table [21:05:34.783] =====
             run    reqs     rps    p50    p95    p99    5xx   conn sysfail%    409 broken
     events=1@40    1742    29.1      9    210    480      7      0     0.40      4 no
     events=2@40    1769    29.6     10     20     57      0      0     0.00     10 no
     events=2@80    3525    59.0      8     66    500     20      0     0.57    130 YES
events=1,pool40@40    1658    27.7     11    720   1300      0      0     0.00      0 YES
```

- **Two events replicas** at the same load: p99 from 480 ms to 57 ms, 5xx from 7 to 0, throttling from 10 % to 2 % of periods.
- **Two events replicas at double the load** (80 users): 59.0 RPS, twice the single-replica ceiling, with p99 exactly at 500 ms and 0.57 % 5xx. Capacity scales linearly with events replicas, and two replicas give 2× with no headroom.
- **One replica with `DB_MAX_CONNS=40`:** 5xx disappear completely and p99 rises to 1300 ms, with throttling at 23 %. A bigger pool turns the same CPU shortage from fast errors into slow successes, and the ceiling stays where it was.

At 59 RPS with two events replicas the rest of the system was still light: gateway 23 to 71m per pod (about 280m in total), postgres 56 to 65m, redis 10 to 11m, each events pod 71 to 98m.

### 10.8 Plan for 2× traffic (about 60 RPS with headroom)

| Service | Replicas now → plan | Requests / limits now | Plan | Reasoning |
|---|---|---|---|---|
| events | 1 → **3** | 50m / 200m | **100m / 300m**, `DB_MAX_CONNS=10` | 2 replicas reach 59 RPS at the SLO edge; 3 give about 33 % headroom and survive one pod loss at 2× load. Requests at 100m match the observed 70 to 98m per pod, so the scheduler stops overcommitting the node |
| gateway | 5 → **3** | 50m / 200m | **100m / 300m** | 280m in total at 59 RPS; 3 pods run at about 95m each. Fewer, larger pods also make the keep-alive imbalance in 2.3 smaller. Add a PDB `minAvailable: 2` |
| payments | 1 → **2** | 50m / 200m | 50m / 200m | not exercised by the locustfile; 2 replicas for availability, not CPU |
| postgres | 1 → 1 | 50m / 200m | **250m / 500m** | 65m at 59 RPS, 18 % of the limit at the ceiling; the extra limit is for checkpoints, backups and `pg_dump` running next to traffic |
| redis | 1 → 1 | 50m / 200m | 50m / 200m, AOF on | 10m at 59 RPS. A single pod is fine for 2× traffic. What it needs is persistence (`appendonly yes` on a PVC), because reservations would otherwise vanish on restart; replication is not needed for 2× |

**DB connections.** The single Postgres is not the bottleneck at 2×. Three events replicas with 10 connections each open at most 30 of `max_connections = 100`. The limit sits inside each events process: 10 connections, 2 per reserve, no waiting. Raising the per-pod pool did not help (the `pool40` row). The fixes from Risk 1 (one connection per reserve, a wait instead of a `PoolError`) are worth more than any pool size.

**Liveness.** With `timeoutSeconds: 1` the probe killed events under CPU starvation at 200 users. Plan: `timeoutSeconds: 3`, `failureThreshold: 6` for liveness; readiness stays at 1 s so a starved pod leaves the Service quickly and comes back without a restart.

**Autoscaling.** An HPA on events at 60 % of the CPU request (`minReplicas: 3`, `maxReplicas: 6`). With throttling hidden by averaging (above), I would scale on the throttling ratio or on gateway p99 through a custom metric rather than CPU alone.

**Cost** at $5 per pod per month:

| | Pods | Monthly |
|---|---:|---:|
| Today (5 gateway, 1 events, 1 payments, 1 postgres, 1 redis) | 9 | $45 |
| 2× plan (3 gateway, 3 events, 2 payments, 1 postgres, 1 redis) | 10 | **$50** |
| 2× plan with HPA at its maximum (6 events) | 13 | $65 |

Sum of CPU requests in the plan: 3 × 100m + 3 × 100m + 2 × 50m + 250m + 50m = **1.0 core**, limits 3.05 cores. Both fit the single k3d node (12 cores) many times over. On real infrastructure the plan fits two small 2-vCPU nodes, which also gives the replicas somewhere else to go when a node fails.

---

## Bonus. SRE handbook (Option B)

[`submissions/runbooks/quickticket-handbook.md`](runbooks/quickticket-handbook.md): architecture diagram, the GitOps deploy and rollback flow with measured times, the Prometheus queries for each question, a distilled incident runbook with the Lab 6 alerts and escalation, and the Lab 9 backup and restore procedure. It also records the two things that are broken right now (no backup CronJob, ArgoCD auto-sync off) so a new team member does not trust the wrong thing.

---

## Results

| Item | Result |
|---|---|
| Load test 10 / 30 / 40 / 50 / 100 / 200 users | 7.8 / 22.5 / 28.8 / 35.4 / 43.2 / 52.7 RPS; 5xx 0 / 0 / 1.16 / 3.03 / 59.49 / 57.24 % |
| Capacity ceiling | 40 users, about 29 RPS (repeat at 40: 0.40 % 5xx, p99 480 ms) |
| Cause | events: DB pool of 10 raises `PoolError` instead of waiting, `/reserve` takes 2 connections, CPU throttled at its 200m limit |
| At 200 users | events killed by liveness (1 s timeout) mid-run, 504 at the 5 s gateway timeout |
| Port-forward vs in-cluster, 30 users | 1 of 5 pods vs 5 of 5; same RPS, p99 140 ms vs 30 ms |
| SLO at normal load | 100 % availability, p99 under 100 ms: met |
| DORA | ~2 deploys/day (4 through GitOps), lead time 199 to 286 s, CFR 20 % (all deliberate), restore 0.6 s to 190 s |
| events ×2 | p99 480 → 57 ms at 40 users; 59.0 RPS at 80 users (2×) |
| events pool 10 → 40 | 0 % 5xx but p99 1300 ms |
| 2× plan | 10 pods, 1.0 core requested, $50/month |

The load test found the limit in a service that Prometheus does not scrape. Gateway, the only component with metrics, had five replicas running at a fifth of their CPU while a single events pod ran out of pool connections and CPU quota, and the most useful readings came from places the monitoring does not look: Locust's error report, the events log and the container's `cpu.stat`. Several of those readings contradicted the usual tools. `kubectl top` showed events at half its limit while it was being throttled, and gateway metrics had no 500s while users received 441. That is why the capacity plan has two parts. events gets three replicas, one more than the control run needed to reach 2× at the edge, and a pool that waits instead of crashing. Monitoring gets the events pool, CFS throttling, restarts and a client-side probe, so the next ceiling shows up on a dashboard before a load test has to find it.
