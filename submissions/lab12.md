# Lab 12. Advanced Kubernetes resilience

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2), Docker 29.8.0, k3d v5.9.0, k3s v1.35.5+k3s1 (single node), kubectl v1.37.0, Argo Rollouts v1.10.0 (gateway is the 5 replica canary Rollout from Lab 7), in-cluster Prometheus 3.11.2 scraping gateway pods every 5 s, PostgreSQL 17.11 on the Lab 9 PVC, Alembic 1.18.4, SQLAlchemy 2.0.49

All numbers come from capture files taken on 2026-10-18 between 17:39 and 18:15 local time (UTC+3). Traffic came from `labs/lab8/mixedload.yaml` (2 pods) for the whole session.

Two measuring tools ran next to the lab's Prometheus query:

- **A client-side prober** (`submissions/lab12/prober.yaml`). One pod sends `GET http://gateway:8080/events` 5 times a second, each time over a new TCP connection, and logs `epoch status`. It exists because `gateway_requests_total` is written by the gateway process itself: a request that dies together with a gateway pod (connection refused or reset) is never counted anywhere in Prometheus.
- **Raw counter listing.** Besides `sum(increase(...[Nm]))` I printed every `gateway_requests_total{status=~"5.."}` series with its absolute value. Section 1.2 shows why this turned out to be necessary.

The manifests for previous labs live on separate branches (each branch starts from `upstream/main`), so this branch brings in the current version of every manifest it touches: `gateway.yaml` and `notifications.yaml` from Lab 11, `events.yaml` from Lab 8, `postgres.yaml` and the Alembic setup from Lab 9, `analysis-template.yaml` from Lab 7. Before changing anything I ran `kubectl diff` on them against the live cluster, and the output was empty.

---

## Task 1. Multi-replica failover and PDBs

### 12.1 Scale events, payments, notifications to 2

`replicas: 2` in `k8s/events.yaml`, `k8s/payments.yaml`, `k8s/notifications.yaml`.

One change beyond the lab: events ran `imagePullPolicy: Always` on a ghcr.io image, and the `ghcr-secret` the manifest references no longer exists in the cluster (`kubectl get secret -A | grep ghcr` is empty). A second replica would have needed a fresh pull from a private registry. The tag is an immutable commit SHA that is already on the node, so I switched it to `IfNotPresent`. Payments got the same policy.

```bash
kubectl apply -f k8s/events.yaml -f k8s/payments.yaml -f k8s/notifications.yaml
kubectl get deploy,rollout
```

```plaintext
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           24d
deployment.apps/mixedload       2/2     2            2           3m41s
deployment.apps/notifications   2/2     2            2           2d3h
deployment.apps/payments        2/2     2            2           24d
deployment.apps/postgres        1/1     1            1           24d
deployment.apps/prober          1/1     1            1           3m41s
deployment.apps/redis           1/1     1            1           24d

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           15d
```

### 12.2 Failover under load

Three rounds of the lab's test: delete `items[0]` of gateway and of events with `--wait=false`, watch until every pod is Ready, wait 20 s, read the 1 minute 5xx increase and the prober.

```plaintext
--- round 1
before: 5xx increase[3m]: 0.00
kill at 17:45:28.095: gateway-f8bc49cf-cx4lx events-76b4b58749-59j5p
  +0.2s 59j5p/true/Running/TERM  97lkl/true/Running  rrwd7/false/Pending/PodInitializing  95pq4/false/Pending/ContainerCreating  cx4lx/true/Running/TERM ...
  +11.4s 97lkl/true/Running  rrwd7/true/Running  95pq4/true/Running  g877q/true/Running ...
recovered after 11.7s
after: 5xx increase[1m]: 1.09
by status [1m]: status=200:659 status=409:355 status=502:1
prober {'200': 190} non-2xx: 0
--- round 2
recovered after 11.3s
after: 5xx increase[1m]: 0.00
prober {'200': 186} non-2xx: 0
--- round 3
recovered after 12.4s
after: 5xx increase[1m]: 0.00
prober {'200': 190} non-2xx: 0
```

The result was not zero. Round 1 produced one `502` on `GET /events` (a range query puts it at 17:45:33, five seconds after the kill). Replacement pods were Ready after 11 to 12 s each time. The new events pod left its init containers (`pg_isready`, `redis-cli ping`) after about 5 s and needed about 6 s more until its first successful readiness probe.

The gateway does not retry calls to events (Lab 11 put retries only in front of payments), so any failed connection to a dying events pod goes straight back to the client as 502. The events container had no `preStop`: on delete the pod's IP is removed from the Service endpoints and uvicorn receives SIGTERM at the same moment, and for a short time kube-proxy still routes new connections to a socket that is already closed. The canary in 12.4 replaced every gateway pod before I read their logs, so I could not get the exception for this particular 502. A second, logged occurrence came later (below) and confirmed the mechanism.

**Fix.** `preStop: sleep 5` on events and payments (`k8s/events.yaml`, `k8s/payments.yaml`). The events pod keeps serving for 5 s after it leaves the endpoints, which is enough for kube-proxy on this node.

Applying the fix replaced both events pods, and that rollout was a free extra run of the "before" case: the old pods had no preStop. The gateway logged both failures:

```plaintext
pod/gateway-7bc5c954c4-hmpnr {"time":"2026-10-18 14:53:21,134","level":"ERROR","service":"gateway","msg":"reserve error: All connection attempts failed"}
pod/gateway-7bc5c954c4-hmpnr INFO:     10.42.0.148:44244 - "POST /events/1/reserve HTTP/1.1" 502 Bad Gateway
pod/gateway-7bc5c954c4-kkw99 INFO:     10.42.0.147:46892 - "GET /events HTTP/1.1" 504 Gateway Timeout
```

`All connection attempts failed` is httpx's `ConnectError`: the gateway opened a new connection to an IP that was still in the routing rules while nothing listened there any more. That is exactly the window preStop closes.

Then five more rounds of the same test with the fix in place (one line per round, condensed from the capture):

```plaintext
--- round 1   recovered after 10.0s   after: 5xx increase[1m]: 0.00   prober {'200': 176} non-2xx: 0
--- round 2   recovered after 11.6s   after: 5xx increase[1m]: 0.00   prober {'200': 185} non-2xx: 0
--- round 3   recovered after 11.9s   after: 5xx increase[1m]: 0.00   prober {'200': 187} non-2xx: 0
--- round 4   recovered after 10.2s   after: 5xx increase[1m]: 0.00   prober {'200': 181} non-2xx: 0
--- round 5   recovered after 12.0s   after: 5xx increase[1m]: 0.00   prober {'200': 191} non-2xx: 0
all 5 rounds: 5xx increase[8m]: 0.00
```

With the lab's query this reads as a clean zero, and inside the kill windows it was. The raw counters showed something the query cannot see.

#### What `increase()` does not count

```plaintext
raw 5xx series: hmpnr POST /events/{id}/reserve 502=1 | kkw99 GET /events 504=1 | scbfn GET /events 502=1
```

All three of these errors were invisible to `sum(increase(gateway_requests_total{status=~"5.."}[1m]))`. The two 17:53 errors fell in a 1 minute window that the query covered and still reported 0. The reason is how the Python client creates labelled series: a `(method, path, status)` child exists only from its first `.inc()`. So Prometheus first sees the series already at value 1, `increase()` takes that first sample as the baseline, and the first error of every new combination on every new pod is dropped. Since each kill and each rollout creates new pods, the most likely error is exactly the one that never gets counted.

The third series, `scbfn GET /events 502=1`, is the 502 the rerun missed:

```plaintext
872:{"time":"2026-10-18 15:00:26,064","level":"ERROR","service":"gateway","msg":"events service error: "}
873:INFO:     10.42.0.148:45974 - "GET /events HTTP/1.1" 502 Bad Gateway
```

It happened at 18:00:26, about a minute after the last kill round had finished, with no pod being deleted anywhere. The exception text is empty, which fits a read error on a reused connection rather than a failed connect (that one says `All connection attempts failed`, as above). The defaults of the two sides are a likely explanation:

```plaintext
gateway image httpx 0.28.1 default keepalive_expiry = 5.0
events image uvicorn 0.44.0 timeout_keep_alive default = 5
```

The gateway keeps an idle connection for 5 s and the events server closes an idle connection after 5 s. Now and then the gateway reuses the connection just as the server closes it. I did not capture packets, so this is the best fit to the evidence, not a proven cause. Over the whole session (about 35 minutes of mixedload plus the prober) this is the only error with no disruption behind it. I report it as a background rate rather than a failover result, and I did not change the gateway for it: the fix (`keepalive_expiry` below the server's keep-alive, or a retry on idempotent GETs) belongs to the Lab 11 code, not to this lab.

> A zero from `increase()` after a pod kill is weak evidence, because new pods are where the first error lands and `increase()` skips first errors. Reading the raw series, or pre-initializing the label combinations to 0 at startup, is what makes "no errors" checkable.

### 12.3 PDBs

`k8s/pdb.yaml`: four `policy/v1` budgets, all selecting on `app: <service>`.

```bash
kubectl apply -f k8s/pdb.yaml
kubectl get pdb
```

```plaintext
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     6s
gateway-pdb         2               N/A               3                     6s
notifications-pdb   N/A             1                 1                     5s
payments-pdb        1               N/A               1                     5s
```

### 12.4 Topology spread

Added to the Rollout pod template in `k8s/gateway.yaml`:

```yaml
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: kubernetes.io/hostname
          whenUnsatisfiable: ScheduleAnyway
          labelSelector:
            matchLabels:
              app: gateway
```

The template change starts a canary with the Lab 7 analysis. It took 163 s, and both counters stayed clean during it (`5xx increase[5m]: 0.00`, prober 821 of 821 requests returned 200).

```bash
kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
kubectl get pod -l app=gateway -o wide
```

```plaintext
[
    {
        "labelSelector": {
            "matchLabels": {
                "app": "gateway"
            }
        },
        "maxSkew": 1,
        "topologyKey": "kubernetes.io/hostname",
        "whenUnsatisfiable": "ScheduleAnyway"
    }
]
NAME                       READY   STATUS    RESTARTS   AGE     IP            NODE
gateway-7bc5c954c4-2jvm4   1/1     Running   0          7s      10.42.0.163   k3d-quickticket-server-0
gateway-7bc5c954c4-4phtr   1/1     Running   0          35s     10.42.0.161   k3d-quickticket-server-0
gateway-7bc5c954c4-hmpnr   1/1     Running   0          2m43s   10.42.0.160   k3d-quickticket-server-0
gateway-7bc5c954c4-kkw99   1/1     Running   0          35s     10.42.0.162   k3d-quickticket-server-0
gateway-7bc5c954c4-zkzkx   1/1     Running   0          7s      10.42.0.164   k3d-quickticket-server-0
```

The constraint is in the live spec and in every new pod's spec, and all five pods sit on the only node, as expected on single-node k3d.

### 12.5 PDB blocks an eviction

`kubectl proxy --port=8901` in the background with `trap 'kill $PROXY_PID' EXIT`, `events-pdb` tightened to `minAvailable: 2`, then one eviction through the API:

```bash
kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
curl -s -o /tmp/evict.json -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
  -d '{"apiVersion":"policy/v1","kind":"Eviction","metadata":{"name":"events-76b4b58749-6crkq","namespace":"default"}}' \
  http://localhost:8901/api/v1/namespaces/default/pods/events-76b4b58749-6crkq/eviction
```

```plaintext
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   2               N/A               0                     3m37s
HTTP 429  (pod events-76b4b58749-6crkq)
{
    "kind": "Status",
    "apiVersion": "v1",
    "metadata": {},
    "status": "Failure",
    "message": "Cannot evict pod as it would violate the pod's disruption budget.",
    "reason": "TooManyRequests",
    "details": {
        "causes": [
            {
                "reason": "DisruptionBudget",
                "message": "The disruption budget events-pdb needs 2 healthy pods and has 2 currently"
            }
        ]
    },
    "code": 429
}
events-76b4b58749-6crkq   1/1   Running   0     4m37s
```

As a control I restored `minAvailable: 1` and sent two evictions back to back, one per events pod:

```plaintext
events-pdb   1               N/A               1                     3m42s
HTTP 201  (pod events-76b4b58749-6crkq)
HTTP 429  (pod events-76b4b58749-rrwd7)
            "message": "The disruption budget events-pdb needs 1 healthy pods and has 1 currently"
events-pdb   1               N/A               0                     3m43s
events-76b4b58749-6crkq   1/1     Terminating   0          4m41s
events-76b4b58749-lnz6m   0/1     Init:0/2      0          0s
events-76b4b58749-rrwd7   1/1     Running       0          6m42s
budget back to 1 after 9.6s
prober {'200': 50} non-2xx: 0
```

The first eviction is accepted, and the second one, sent under a second later, is refused. The evicted pod stopped counting as healthy the moment it went into Terminating, while its replacement was still in `Init:0/2`. The budget came back only when the new pod was Ready, 9.6 s later.

> The budget is checked against pods that are healthy now, not against the replica count. A drain moves on only as fast as the replacements become Ready, so slow init containers make drains slow, and the PDB is what turns that into waiting instead of an outage.

### Answers

**With 3 gateway replicas and minAvailable: 1, how many pods can be evicted at once? Why minAvailable: 2 with 5 replicas?** Allowed disruptions = healthy pods minus minAvailable = 3 - 1 = 2, so two pods at once, and only while all three are healthy (the control in 12.5 shows the count drops as soon as one pod is terminating). With 5 replicas and minAvailable 2 the budget allows 3, so a drain can move most of the gateway in one step and the two pods left still serve. A tighter value such as 4 allows one eviction at a time, and if one pod is already unhealthy for an unrelated reason it allows none, so the drain hangs until someone intervenes. minAvailable 5 would block every voluntary eviction for good. The two remaining pods also have to carry the load; each pod has its own 10 rps per path rate limiter (Lab 11), so with two pods the gateway sheds more as 429 during a drain, which is acceptable for a maintenance window.

**In a 3-node cluster, what placement does maxSkew: 1 give for 5 and for 7 gateway pods?** 5 pods: 2/2/1. 7 pods: 3/2/2. The scheduler puts each new pod on a node with the fewest matching pods, so the fullest and the emptiest node never differ by more than one. With `ScheduleAnyway` this is a scoring preference: if the "right" node has no room, the pod still schedules elsewhere and the skew can grow, instead of the pod staying Pending.

---

## Task 2. Graceful shutdown and zero-downtime migration

### 12.6 preStop and readiness on the gateway

The block as it is in `k8s/gateway.yaml`:

```yaml
      # Lab 12: covers the 10 s preStop sleep plus up to 30 s for uvicorn to finish
      # in-flight requests after SIGTERM. The default 30 s would leave 20 s.
      terminationGracePeriodSeconds: 40
      ...
          readinessProbe:
            httpGet:
              path: /metrics
              port: 8080
            # Lab 12: one failed probe 2 s apart takes the pod out of the Service,
            # instead of two failures 5 s apart (up to 10 s of traffic to a sick pod).
            periodSeconds: 2
            failureThreshold: 1
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
```

One deliberate difference from the lab text: the readiness probe stays on `/metrics`, not `/health`. In the Lab 8 bonus, readiness on `/health` took all 5 gateway replicas out of the Service during a Redis outage, because the gateway's `/health` calls events' `/health`, which checks Redis, although `GET /events` needs only Postgres. With `failureThreshold: 1` that cascade would get even faster. What the probe has to do for graceful shutdown, failing fast, is set by `periodSeconds: 2` and `failureThreshold: 1`. For a pod that is being deleted the probe hardly matters anyway: the endpoints controller removes a terminating pod from the Service on its deletion timestamp, whatever its readiness. The time that has to be covered is kube-proxy catching up, and that is what the `preStop` sleep is for.

Applying it started a canary again (154 s, `5xx increase[5m]: 0.00`, prober 770 of 770 requests returned 200). Every running pod was then checked for the template:

```plaintext
gateway-84f9cffbcb-9ndx5 40 ["sh","-c","sleep 10"]
gateway-84f9cffbcb-clfh9 40 ["sh","-c","sleep 10"]
gateway-84f9cffbcb-qfxgd 40 ["sh","-c","sleep 10"]
gateway-84f9cffbcb-rzrwc 40 ["sh","-c","sleep 10"]
gateway-84f9cffbcb-z4b97 40 ["sh","-c","sleep 10"]
```

#### Rolling restart, with a control run

I ran the restart before applying the change too. Without that run, "zero 5xx after the change" would not tell whether the change did anything.

```bash
kubectl argo rollouts restart gateway
kubectl argo rollouts status gateway --timeout=300s
```

| Run | Restart took | Prometheus 5xx increase [3m] | Raw 5xx series after | Prober (client) |
|---|---|---|---|---|
| Control: no preStop, readiness 5 s x 2, grace 30 s | 39.3 s | 0.00 | none | 195 x 200, **1 x `ERR:URLError`** at 18:02:19 |
| preStop 10 s, readiness 2 s x 1, grace 40 s | 62.6 s | empty (= 0) | none | 313 x 200, 0 errors |
| Same, repeated | 62.1 s | empty (= 0) | none | 310 x 200, 0 errors |

```plaintext
=== [18:01:53.455] rolling restart under load: CONTROL: no preStop, readiness 5s x2, grace 30s
before: 5xx increase[1m]: 0.00
restart took 39.3s
after: 5xx increase[3m]: 0.00
by status [3m]: status=200:1735 status=409:859 status=502:0 status=504:0
raw 5xx series: none
prober {'200': 195, 'ERR:URLError': 1} non-2xx: 1
    1792335739.171 ERR:URLError
```

In the control run every server-side measure says the restart was clean, while a client got a connection error. The lab's success criterion could not have caught this failure: a refused connection to a dying gateway pod never reaches any gateway process, so there is nothing to count. With preStop the client saw no error in two restarts. The price is time: each pod now stays 10 s in Terminating, so the restart takes 62 s instead of 39 s.

> On this service the metric that defines success is blind to the failure mode being fixed. Proving graceful shutdown requires measuring at the client, and only the control run showed that there was something to fix.

### 12.7 `CREATE INDEX CONCURRENTLY` migration

`migrations/versions/5d1c2e8f9a01_index_events_event_date_concurrently.py`:

```python
def upgrade() -> None:
    """Upgrade schema."""
    # CREATE INDEX CONCURRENTLY refuses to run inside a transaction block, and
    # Alembic wraps every migration in one. autocommit_block() commits that
    # transaction, runs the DDL in autocommit mode and opens a new one after.
    # if_not_exists keeps a re-run safe after an interrupted build.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_event_date", "events", ["event_date"],
            postgresql_concurrently=True, if_not_exists=True,
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_events_event_date", table_name="events",
            postgresql_concurrently=True, if_exists=True,
        )
```

Control first: the same revision with the `autocommit_block()` removed.

```plaintext
INFO  [alembic.runtime.migration] Running upgrade 43b43772c5f3 -> 5d1c2e8f9a01, index events.event_date concurrently
psycopg2.errors.ActiveSqlTransaction: CREATE INDEX CONCURRENTLY cannot run inside a transaction block
43b43772c5f3
no idx_events index (transaction rolled back)
```

The failed run left nothing behind: the revision stayed at `43b43772c5f3` and no index was created. Then the real file under mixedload:

```plaintext
{"status":"success","data":{"resultType":"vector","result":[]}}
raw 5xx series: none
INFO  [alembic.runtime.migration] Running upgrade 43b43772c5f3 -> 5d1c2e8f9a01, index events.event_date concurrently

real	0m0.507s
5d1c2e8f9a01 (head)
    "idx_events_event_date" btree (event_date)
{"status":"success","data":{"resultType":"vector","result":[]}}
before empty after empty
diff exit 0
raw 5xx series: none
prober {'200': 34} non-2xx: 0
```

```plaintext
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
```

A note on the lab's `diff /tmp/5xx.before /tmp/5xx.after`: here it returned 0 only because both results were empty vectors. When a 5xx series exists, every `wget` answer carries its own evaluation timestamp, so the two files always differ even when the value has not moved. I compared the values instead.

#### The lock difference, measured

On 5 rows nothing can be seen, so I repeated both statements on a 500 000 row table in a scratch database (`lockdemo`, dropped afterwards). While each statement ran, one connection inserted a row every 50 ms, a second one ran a primary key SELECT every 50 ms, and a third read `pg_locks` for the building session.

```plaintext
filled demo with 500000 rows in 7.7s
CREATE INDEX demo_v_idx ON demo (v)
  build 2.00s | inserts during build: 2 | slowest insert during build 1986 ms | median insert before build 3.9 ms
  selects during build: 26 | slowest select 52 ms
  lock held by the build on demo: [('ShareLock', True)]
CREATE INDEX CONCURRENTLY demo_v_idx ON demo (v)
  build 2.34s | inserts during build: 32 | slowest insert during build 159 ms | median insert before build 3.5 ms
  selects during build: 28 | slowest select 73 ms
  lock held by the build on demo: [('ShareUpdateExclusiveLock', True)]
```

This corrects the lab text in one detail. A plain `CREATE INDEX` takes `SHARE`, not `ACCESS EXCLUSIVE`: reads went on undisturbed (slowest select 52 ms), and every write waited for the whole build (one insert waited 1986 of the 2000 ms). `CONCURRENTLY` held `SHARE UPDATE EXCLUSIVE`, and 32 inserts went through, the slowest at 159 ms. It cost 17% more build time, because it scans the table twice.

### 12.8 Expand-and-contract sketch: `event_date` to `scheduled_at`

1. **Migration 1, expand.** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ NULL;` Nullable, no default: a catalog-only change, the lock lasts milliseconds, no row is rewritten. Running code never names the column, so it keeps working.
2. **Deploy A, dual-write and fallback read.** Writes set both columns. Reads use `COALESCE(scheduled_at, event_date)`, `ORDER BY` too, so a row reads the same whether it has been backfilled or not.
3. **Migration 2, backfill.** `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;` then `SET NOT NULL`. Safe under traffic because every running pod reads through the COALESCE, and the `WHERE` makes a re-run a no-op.
4. **Deploy B, switch.** Reads and writes only `scheduled_at`. During the rollout A pods and B pods run side by side, and both work because both columns still exist.
5. **Migration 3, contract.** `ALTER TABLE events DROP COLUMN event_date;` only after `rollout status` for Deploy B has finished and no A pod is left.

### Answers

**Why does `CREATE INDEX CONCURRENTLY` matter? What happens without it on 10M rows?** A plain `CREATE INDEX` holds a `SHARE` lock for the whole build: SELECTs continue, but every INSERT, UPDATE and DELETE on the table waits until the build ends (measured above: a 2 s build blocked writes for 1986 ms). On 10M rows the build takes minutes, so writes queue for minutes, request handlers hold their DB connections while waiting, the pool (`DB_MAX_CONNS=10` per events pod) runs out, and reads start failing too, only one step later. `CONCURRENTLY` takes `SHARE UPDATE EXCLUSIVE`, which does not conflict with row writes, at the cost of two table scans and a longer build. It cannot run inside a transaction, and if it fails it leaves an `INVALID` index that has to be dropped before a retry; that is what `if_not_exists` and `if_exists` are there for.

**Why must migration 3 come after Deploy B has fully rolled out?** Until then some running pod still names `event_date` in its SQL, either the original code or Deploy A's `COALESCE(scheduled_at, event_date)`. Dropping the column first turns each of those queries into `column "event_date" does not exist`: events answers 500, the gateway 502. The bonus shows this directly, inside a rolled-back transaction. Old code never breaks on an extra column, only on a missing one, so the order is always to add first and remove last.

---

## Bonus. Expand-and-contract executed live

All five steps ran in one script under mixedload, with the prober running. Before each code deploy the script built the events image under a new tag (`quickticket-events:v12a`, `v12b`), imported it into k3d, checked that it was in `ctr images ls` on the node, applied `k8s/events.yaml` with `imagePullPolicy: Never`, and after the rollout used `grep` inside every events pod to confirm the new code was running. With a fresh tag per deploy, the image name of each pod says which code it runs.

### Migrations (upgrade bodies)

`7a3e9b2c4d10_add_events_scheduled_at.py`:

```python
    op.add_column(
        "events",
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

`8b4f0c3d5e21_backfill_events_scheduled_at.py`:

```python
    op.execute("UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL")
    op.alter_column("events", "scheduled_at", nullable=False)
    # Dropping event_date in the next migration also drops idx_events_event_date
    # (12.7), so the replacement index is built here, before anything reads by
    # scheduled_at alone.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_events_scheduled_at", "events", ["scheduled_at"],
            postgresql_concurrently=True, if_not_exists=True,
        )
```

`9c5a1d4e6f32_drop_events_event_date.py`:

```python
    op.drop_column("events", "event_date")
```

One addition to the lab's M2: dropping a column silently drops every index on it, so without the extra step M3 would also have removed the index built in 12.7. M2 builds the replacement on `scheduled_at` with `CONCURRENTLY` after the backfill, so the drop in M3 loses no index. The downgrades mirror each step (M3's re-adds `event_date`, copies `scheduled_at` back and sets `NOT NULL`).

### Deploy A to Deploy B diff (`app/events/main.py`)

There is no runtime write path for the date: rows come only from `app/seed.sql`, so the dual-write in Deploy A has nothing to change. The three read sites are two SELECTs and one ORDER BY.

```diff
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date,
-                   e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at, e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
 ...
-            SELECT e.id, e.name, e.venue, COALESCE(e.scheduled_at, e.event_date) AS event_date,
-                   e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue, e.scheduled_at, e.total_tickets, e.price_cents,
```

Response shape: the code reads the row by position and returns it under the key `"date"`, so the SQL alias never reached the API. Deploy B drops the alias and the response is unchanged:

```plaintext
{'id': 1, 'name': 'Go Conference 2026', 'venue': 'Main Hall A', 'date': '2026-09-15T09:00:00+00:00', 'total_tickets': 100, 'price_cents': 5000, 'available': 5}
```

### The run

Excerpt from the capture; after each step the two counter lines are joined into one.

```plaintext
=== [18:10:07.621] 12.10 setup
5d1c2e8f9a01
{"status":"success","data":{"resultType":"vector","result":[]}}
raw 5xx series: none
=== [18:10:09.485] 12.11 M1 expand
Running upgrade 5d1c2e8f9a01 -> 7a3e9b2c4d10, add events.scheduled_at column
5xx raw sum: empty(=0)   raw 5xx series: none
=== [18:10:15.341] 12.12 Deploy A (COALESCE read)
built quickticket-events:v12a sha256:0d1bd269fca1
docker.io/library/quickticket-events:v12a
deployment "events" successfully rolled out
pod/events-589fc9d97b-x5shx image=quickticket-events:v12a coalesce=3
pod/events-589fc9d97b-zxpql image=quickticket-events:v12a coalesce=3
5xx raw sum: empty(=0)   raw 5xx series: none
=== [18:12:47.936] 12.13 M2 backfill + NOT NULL + index on scheduled_at
Running upgrade 7a3e9b2c4d10 -> 8b4f0c3d5e21, backfill events.scheduled_at
5xx raw sum: empty(=0)   raw 5xx series: none
 id |       event_date       |      scheduled_at
----+------------------------+------------------------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00
=== [18:12:53.865] 12.14 Deploy B (scheduled_at only) + seed
built quickticket-events:v12b sha256:ebd93cf51729
deployment "events" successfully rolled out
pod/events-589fc9d97b-zxpql image=quickticket-events:v12a event_date_refs=5
pod/events-cbb594c5-l8grp image=quickticket-events:v12b event_date_refs=1
pod/events-cbb594c5-wvx8h image=quickticket-events:v12b event_date_refs=1
configmap/postgres-seed configured
5xx raw sum: empty(=0)   raw 5xx series: none
=== [18:13:32.190] 12.15 M3 contract
Running upgrade 8b4f0c3d5e21 -> 9c5a1d4e6f32, drop events.event_date
9c5a1d4e6f32 (head)
5xx raw sum: empty(=0)   raw 5xx series: none
baseline empty  final empty  delta 0
diff exit 0
gateway pods unchanged (counters comparable)
bonus window 211.5s
prober {'200': 1056} non-2xx: 0
```

The Deploy B listing caught the overlap the pattern depends on: one Deploy A pod (still in its 5 s preStop) next to two Deploy B pods, all serving. The one `event_date` hit in the B pods is the header comment. The gateway pod list was identical at the start and end, so the summed counters compare like with like: no pod disappeared and took its errors with it.

5xx baseline and final, as the lab asks for:

```plaintext
$ cat /tmp/5xx.baseline
{"status":"success","data":{"resultType":"vector","result":[]}}
$ cat /tmp/5xx.final
{"status":"success","data":{"resultType":"vector","result":[]}}
$ diff /tmp/5xx.baseline /tmp/5xx.final; echo "diff exit $?"
diff exit 0
```

Zero delta over 211 s and five transitions, with no 5xx series at all on the five gateway pods, and 1056 out of 1056 prober requests returned 200. The rollout to Deploy A took most of the window (2.5 min) because the image build ran `pip install`.

Schema before M1 and after M3:

Before M1 (18:10:09):

```plaintext
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 event_date    | timestamp with time zone |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_event_date" btree (event_date)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

After M3 (18:13:39):

```plaintext
                                        Table "public.events"
    Column     |           Type           | Collation | Nullable |              Default
---------------+--------------------------+-----------+----------+------------------------------------
 id            | integer                  |           | not null | nextval('events_id_seq'::regclass)
 name          | text                     |           | not null |
 venue         | text                     |           | not null |
 total_tickets | integer                  |           | not null |
 price_cents   | integer                  |           | not null |
 email         | character varying(255)   |           |          |
 scheduled_at  | timestamp with time zone |           | not null |
Indexes:
    "events_pkey" PRIMARY KEY, btree (id)
    "idx_events_scheduled_at" btree (scheduled_at)
Referenced by:
    TABLE "orders" CONSTRAINT "orders_event_id_fkey" FOREIGN KEY (event_id) REFERENCES events(id)
```

`scheduled_at` is now the last column: a rename by expand-and-contract is really add plus drop, and PostgreSQL appends new columns at the end. Nothing reads the table with `SELECT *`, so the order does not matter here.

### What M3 would have done one step early

Right before M3, inside a transaction that was rolled back, I dropped the column and ran Deploy A's query:

```plaintext
BEGIN
SET
ALTER TABLE
ERROR:  column e.event_date does not exist
LINE 1: SELECT e.id, COALESCE(e.scheduled_at, e.event_date) AS event...
                                              ^
ROLLBACK
1
```

The final `1` confirms the column was still there afterwards. `SET LOCAL lock_timeout = '1s'` made sure the `ACCESS EXCLUSIVE` lock of the drop could not queue behind live traffic.

### Seed for a fresh cluster

`app/seed.sql` and the `postgres-seed` ConfigMap in `k8s/postgres.yaml` now create the head schema: `scheduled_at TIMESTAMPTZ NOT NULL`, the Lab 9 `email` column and `idx_events_scheduled_at`. A database created from it is already at head, so it must be `alembic stamp head`ed, not upgraded (the old migrations would try to add columns that exist); the file says so in a comment. Checked on a throwaway `postgres:17-alpine` container with the new file mounted into `/docker-entrypoint-initdb.d`:

```plaintext
/usr/local/bin/docker-entrypoint.sh: running /docker-entrypoint-initdb.d/01-seed.sql
 scheduled_at  | timestamp with time zone |           | not null |
 email         | character varying(255)   |           |          |
    "idx_events_scheduled_at" btree (scheduled_at)
 id |         name         |      scheduled_at
----+----------------------+------------------------
  1 | Go Conference 2026   | 2026-09-15 09:00:00+00
  4 | Python Workshop      | 2026-09-22 14:00:00+00
  2 | SRE Meetup           | 2026-10-01 18:00:00+00
  5 | Kubernetes Deep Dive | 2026-10-10 10:00:00+00
  3 | Cloud Native Summit  | 2026-11-20 10:00:00+00
```

### Answers

**Which single step would have caused 5xx if moved earlier?** M3, the drop. Every other step only adds something (a column, a fallback read, data, a constraint that the data already satisfies) or stops using something that still exists. The drop is the only step that removes something running code depends on, and any earlier position leaves pods (original code or Deploy A) that select `event_date`; the rolled-back transaction above shows the exact error they would get. Moving Deploy A before M1 breaks for the mirror reason (its COALESCE names a column that does not exist yet), but that is moving an add later, not a removal earlier. The NOT NULL in M2 is the only other step with a precondition, and it is about data: it fails as a migration if a writer were still inserting rows without `scheduled_at`, which is why Deploy A, the dual-write, comes before it.

**Batching the backfill on 10M rows:**

```text
last_id = 0
loop:
    ids = UPDATE events SET scheduled_at = event_date
          WHERE id IN (SELECT id FROM events
                       WHERE id > :last_id AND scheduled_at IS NULL
                       ORDER BY id LIMIT 10000)
          RETURNING id
    COMMIT                         -- one short transaction per batch
    if ids is empty: break
    last_id = max(ids)
    sleep(0.1)                     -- let replicas and autovacuum keep up
ALTER TABLE events ADD CONSTRAINT scheduled_at_nn CHECK (scheduled_at IS NOT NULL) NOT VALID;
ALTER TABLE events VALIDATE CONSTRAINT scheduled_at_nn;    -- scans without blocking writes
ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL; -- PG 12+ uses the valid check, no scan
```

Each batch locks at most 10 000 rows for a few milliseconds. The plain `SET NOT NULL` from M2 would scan 10M rows under `ACCESS EXCLUSIVE`, so at that size it is replaced by the check-constraint sequence.

**Why is M3's downgrade not enough for rollback safety once Deploy B is live?** It brings the column back, and the running service gets nothing from that. The `UPDATE events SET event_date = scheduled_at` copies the data as of the moment of the downgrade, but Deploy B never writes `event_date`, so the column goes stale again with every new row unless Deploy A is back first. And Deploy A cannot come back until the column exists and is filled. So schema and code have to move back together, in the reverse expand-and-contract order: re-add the column and keep it in sync with `scheduled_at` (a trigger, or B writing both), redeploy A, and only then consider anything else. Rollback is safe only if the old column keeps being written for as long as rolling back is a real option. In practice this means a dual-write period after Deploy B, and dropping the column only when nobody expects to roll back.

---

## 12.9 HPA

Not done; it is optional and ungraded.

---

## Results

| Check | Result |
|---|---|
| events, payments, notifications at 2 replicas, gateway Rollout at 5 | 2/2, 2/2, 2/2, 5/5 |
| Pod kill under load, as delivered (3 rounds) | 1 x 502 in round 1 |
| Pod kill after `preStop` on events and payments (5 rounds) | 0 5xx in every kill window, prober 0 errors; one unrelated keep-alive 502 a minute after the rounds |
| `kubectl get pdb` | allowed 3 / 1 / 1 / 1 |
| `topologySpreadConstraints` in the live Rollout spec | yes, all 5 pods on the single node |
| Eviction against a tightened PDB | HTTP 429, `reason: DisruptionBudget` |
| Eviction control at `minAvailable: 1` | 201, then 429 for the second pod until the replacement was Ready (9.6 s) |
| Rolling restart, control without preStop | 0 server-side 5xx, 1 client connection error |
| Rolling restart with preStop (2 runs) | 0 / 0 client errors, 0 5xx, 62 s per restart |
| `CONCURRENTLY` without `autocommit_block` | `ActiveSqlTransaction`, nothing changed |
| `CONCURRENTLY` migration under load | 0.5 s, 0 5xx, index in `\d events` |
| Plain vs concurrent build, 500k rows | writes blocked 1986 ms vs slowest write 159 ms |
| Expand-and-contract, 5 transitions | 211 s, 5xx delta 0, prober 1056/1056 OK |
| Final schema | `event_date` gone, `scheduled_at` NOT NULL, indexed |
| Seed on a fresh database | head schema, 5 events |

The same blind spot showed up in every task. The gateway's own 5xx counter is the lab's measure of success, and it misses three kinds of failure: requests that die together with a gateway pod (section 12.6), the first error on every new pod (section 1.2), and anything that never reaches the gateway at all. The PDB, preStop and migration work held up in this lab only because each claim was checked from a second point: the prober, the raw series, the logs. In the one place where only the official metric was available, the round 1 502 in 12.2, I could see that it happened but not why. The disruption mechanics (endpoints, kube-proxy, preStop) and the schema mechanics (expand before code, contract after code) follow the same rule: the old and the new version must both work while they overlap. Each of those overlaps was measured while it happened.
