# Lab 12 — Bonus: Advanced Kubernetes Resilience

I finished all three tasks on the `quickticket` k3d cluster: Task 1 (multi-replica failover +
PDBs), Task 2 (graceful shutdown + zero-downtime migration), and the Bonus Task (the
expand-and-contract rename, done live). I also did the optional §12.9 HPA observation.

Every command output below actually came from this cluster. If something could not be done
honestly in this environment, I marked it with a `> ⚠️` note instead of faking it.

---

## Conventions and translations used throughout

The lab text assumes a generic cluster. Three things about *this* cluster change the commands,
and I call out all three where they matter:

| Lab text says | Here it is | Why |
|---|---|---|
| `deployment/gateway` | `rollout.argoproj.io/gateway` | Lab 7 converted the gateway to an Argo Rollout. `kubectl get deployment gateway` returns NotFound; `kubectl rollout restart deployment/gateway` fails. `kubectl argo rollouts {status,restart,promote} gateway` are the equivalents. |
| `kubectl drain` demonstrates eviction | eviction API against a specific pod | Single-node cluster: see §12.5. |
| readinessProbe `httpGet /health` on gateway | readinessProbe `httpGet /metrics` | Lab 8's deliberate fix; reverting it would re-introduce a measured 19-second total outage. The *timing* was tightened as the lab intends. See §12.6. |

### How "zero 5xx" is measured

The lab's query is `sum(increase(gateway_requests_total{status=~"5.."}[3m]))`. I ran into two
problems with this query, and both actually happened during the lab, not just in theory:

1. **When no 5xx has ever occurred the series does not exist**, so the query returns an *empty
   vector*, not `0`. So every measurement below also runs the `... or vector(0)` form, which
   forces an explicit `0` instead of nothing.
2. **`increase()` cannot see a counter's first appearance.** A series that goes from *not
   existing* to `1` and then stays flat reports `increase() == 0`, because there is no earlier
   sample to subtract from. At first I thought this was a rare corner case, but it is exactly
   what a low-volume error burst looks like, and in §12.2 it hid two real 502s from me.

Because of that, every zero-5xx claim in this submission is backed by three measurements, not
one: the lab's `increase()` query, the raw counter
`sum(gateway_requests_total{status=~"5.."}) or vector(0)`, and a `query_range` scan over the
exact test window that lists any 5xx series that existed at any point inside it. That third one
is immune to both problems above, and also to counter resets from pod replacement.

### Traffic during the lab

`labs/lab8/mixedload.yaml` (2 replicas, `/events` → `/events/1/reserve` → `/reserve/{id}/pay` in
a loop) ran the whole time, alongside the Lab 6/7 `loadgen`. Measured steady state: **~13 rps**
through the gateway.

> ⚠️ **Inventory note.** Lab 11 left ~70 tickets across events 1/2/4 (events 3 and 5 sold out),
> and I couldn't top them up because ad-hoc Postgres data writes were refused by the permission
> classifier. mixedload used up event 1 within the first minute of this lab, and after that the
> reserve leg returns **409 Conflict**, a 4xx, so it's correctly not counted as a 5xx. So the
> checkout chain degraded to `GET /events` (200) + `POST reserve` (409) for most of the lab. Both
> legs still go through the full gateway → events → Postgres path, which is the path every proof
> in this lab depends on, so the evidence still stands. But the `/pay` → payments → Redis →
> notifications leg carried little traffic after the first minute. I didn't try to top up
> inventory: it would have meant a data write to a shared resource, and it wasn't needed anyway.
> Measured status mix in the Bonus window: 3216 × 200, 2935 × 409, 0 × 5xx.

---

# Task 1 — Multi-Replica Failover + PDBs (4 pts)

## 12.1 Scale services to 2 replicas

`k8s/events.yaml`, `k8s/payments.yaml`, `k8s/notifications.yaml` now set `replicas: 2`.

### Proof 1 — `kubectl get deploy,rollout` at target replica counts

```
$ kubectl get deploy,rollout
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE
deployment.apps/events          2/2     2            2           21h
deployment.apps/loadgen         1/1     1            1           9h
deployment.apps/mixedload       2/2     2            2           32m
deployment.apps/notifications   2/2     2            2           73m
deployment.apps/payments        2/2     2            2           21h
deployment.apps/postgres        1/1     1            1           5h19m
deployment.apps/redis           1/1     1            1           21h

NAME                          DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
rollout.argoproj.io/gateway   5         5         5            5           9h
```

`loadgen` and `mixedload` are load generators, and `postgres`/`redis` are deliberately
single-replica (see the PDB file header). The four application services are at 2/2/2/5.

## 12.2 Failover test — kill pods under load

I ran this test **twice**, and the first run turned out to be the most useful result in Task 1.

### Run 1 — before the graceful-shutdown work of Task 2

```
### before (increase over 3m)
{"status":"success","data":{"resultType":"vector","result":[]}}
### 200s so far
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790101513.586,"1974"]}]}}
### pods before
events-6469fd576c-lgtsj    1/1   Running   0     4h43m
events-6469fd576c-vfhn5    1/1   Running   0     64s
gateway-6d868c98c4-4mn5h   1/1   Running   0     17m
gateway-6d868c98c4-7wkmj   1/1   Running   0     18m
gateway-6d868c98c4-r686s   1/1   Running   0     18m
gateway-6d868c98c4-wqhzx   1/1   Running   0     20m
gateway-6d868c98c4-x8n7r   1/1   Running   0     17m
### kill at 18:25:13
pod "gateway-6d868c98c4-4mn5h" deleted from default namespace
pod "events-6469fd576c-lgtsj" deleted from default namespace
```

Recovery was fast. Replacement pods were Ready in about 10 s, and the surviving replicas kept
serving the whole time:

```
t+1x5s 18:25:18
  events-6469fd576c-frk6q 0/1 Running 5s        <- replacement, not yet Ready
  events-6469fd576c-vfhn5 1/1 Running 70s       <- survivor, serving
  gateway-6d868c98c4-t5cbd 0/1 Running 5s
  ... 4 surviving gateway pods 1/1 ...
t+2x5s 18:25:24
  events-6469fd576c-frk6q 1/1 Running 10s       <- Ready after ~10s
  gateway-6d868c98c4-t5cbd 1/1 Running 10s
```

The "after" measurement with the lab's query says zero.

```
### after (increase over 1m)
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790101583.620,"0"]}]}}
### after (increase over 3m)
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790101583.736,"0"]}]}}
```

**It was not actually zero.** The raw counter disagrees with `increase()`:

```
### raw 5xx counter after the kill (the increase() query hid these)
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790101635.472,"2"]}]}}
### per-series breakdown
  GET   /events                    status=502 pod=gateway-6d868c98c4-7wkmj value=1
  POST  /events/{id}/reserve       status=502 pod=gateway-6d868c98c4-7wkmj value=1
```

A `query_range` over the window shows the exact moment the series was born:

```
18:25:17 2      <- 4 seconds after the kill at 18:25:13
```

**Diagnosis.** Both 502s show up on a *surviving* gateway pod, on paths that go through `events`.
So this is the gateway reporting that its upstream connection to the deleted **events** pod got
reset mid-request. `kubectl delete pod` sends SIGTERM right away, and the `events` container had
no `preStop` hook, so uvicorn closed its listener before the EndpointSlice removal had reached
kube-proxy, and the two requests already in flight died. Two errors out of about 1974 requests is
a 0.1% blip, but it is exactly the failure mode Task 2 is supposed to fix.

### Run 2 — after `preStop` was added (Task 2, §12.6)

Same test, run again after every service got a `preStop` hook and a 40 s grace period:

```
=== RUN 2 — coordinated pod kill, now WITH preStop on gateway+events ===
### before  18:37:47
  5xx increase[3m]: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102267.929,"0"]}]}}
  5xx raw counter: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102268.045,"0"]}]}}
### kill @ 18:37:48
pod "gateway-564c76b555-4b5pr" deleted from default namespace
pod "events-64459db69f-rj75s" deleted from default namespace
  t+5s:  events-...-jxcvv=0/1 events-...-rj75s=1/1 events-...-sg48s=1/1 gateway-...-4b5pr=1/1 ... gateway-...-x48ww=0/1
  t+10s: events-...-jxcvv=1/1 events-...-rj75s=1/1 events-...-sg48s=1/1 gateway-...-4b5pr=1/1 ... gateway-...-x48ww=1/1
  t+15s: events-...-jxcvv=1/1 events-...-sg48s=1/1 gateway-...-6ld22=1/1 ... gateway-...-x48ww=1/1
```

Notice `rj75s` and `4b5pr`, the pods that were *deleted*, are still showing `1/1 Running` at
t+10 s and only disappear between t+10 s and t+15 s. That's the 10-second `preStop` sleep: they
kept serving while kube-proxy removed them from the endpoints.

### Proof 2 — before/after 5xx around the pod kill

```
### after 18:39:03
  5xx increase[1m]: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102343.925,"0"]}]}}
  5xx increase[3m]: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102344.044,"0"]}]}}
  5xx raw counter: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102344.164,"0"]}]}}
### range scan over the kill window (immune to counter resets)
  5xx series present in window: NONE — zero 5xx
```

Before **0**, after **0**, and no 5xx series came into existence at any point in the window. So
multi-replica failover with graceful shutdown really is lossless here. Without it, it wasn't.

## 12.3 `k8s/pdb.yaml`

Four PDBs, one per **application service**. The lab's list (`gateway`, `events`, `payments`,
`notifications`) is the four app services, not "the original three plus a datastore", so that's
what I wrote. `postgres` and `redis` are deliberately left out: both are single-replica here, and
a PDB over one replica either blocks every drain forever (`minAvailable: 1`) or protects nothing
(`maxUnavailable: 1`). The real fix for a single-replica datastore is replication, not a budget.

`gateway` being a `Rollout` instead of a `Deployment` doesn't affect the PDB at all, since a PDB
selects *pods* by label, never by controller kind.

### Proof 3 — `kubectl get pdb`

```
$ kubectl apply -f k8s/pdb.yaml
poddisruptionbudget.policy/gateway-pdb created
poddisruptionbudget.policy/events-pdb created
poddisruptionbudget.policy/payments-pdb created
poddisruptionbudget.policy/notifications-pdb created

$ kubectl get pdb
NAME                MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb          1               N/A               1                     0s
gateway-pdb         2               N/A               3                     0s
notifications-pdb   N/A             1                 1                     0s
payments-pdb        1               N/A               1                     0s
```

These match what I expected.

## 12.4 Topology spread

Added to the gateway Rollout pod template:

```yaml
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        app: gateway
```

Applied through the canary strategy (`kubectl apply` → `kubectl argo rollouts status gateway`),
and it reached `Healthy`.

### Proof 4 — constraint live in spec, plus actual placement

```
$ kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}' | python3 -m json.tool
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

$ kubectl get pod -l app=gateway -o wide
NAME                       READY   STATUS    RESTARTS   AGE     IP           NODE                       NOMINATED NODE   READINESS GATES
gateway-545bff855f-2fr5k   1/1     Running   0          17s     10.42.0.30   k3d-quickticket-server-0   <none>           <none>
gateway-545bff855f-4p6xq   1/1     Running   0          44s     10.42.0.28   k3d-quickticket-server-0   <none>           <none>
gateway-545bff855f-b84xd   1/1     Running   0          17s     10.42.0.31   k3d-quickticket-server-0   <none>           <none>
gateway-545bff855f-r5sj6   1/1     Running   0          2m52s   10.42.0.27   k3d-quickticket-server-0   <none>           <none>
gateway-545bff855f-tr4xt   1/1     Running   0          44s     10.42.0.29   k3d-quickticket-server-0   <none>           <none>

$ kubectl get nodes
NAME                       STATUS   ROLES           AGE   VERSION
k3d-quickticket-server-0   Ready    control-plane   21h   v1.35.5+k3s1

$ kubectl get pod -l app=gateway -o jsonpath='...nodeName...' | sort | uniq -c
      5 k3d-quickticket-server-0
```

> ⚠️ **No observable effect, as the lab predicts.** With one node, every distribution trivially
> has skew 0. The YAML itself is correct and would work unchanged on a multi-node cluster.

## 12.5 Prove a PDB actually blocks eviction

> ⚠️ **`kubectl drain` was NOT run for real on this cluster, and that was on purpose.** This is a
> single-node k3d cluster and the only node is also the control plane. A real drain would cordon
> that node and evict everything on it: the app, ArgoCD, the in-cluster git server, Prometheus,
> CoreDNS, with nowhere for any of it to reschedule to. That would take down the whole
> environment just to run a demo. So I ran `--dry-run=server` instead, and tested the *actual*
> PDB enforcement through the Eviction API against one specific pod, which is the same admission
> check `drain` calls anyway.

```
### 0. drain --dry-run=server on the only node (the lab's point: it lists everything)
evicting pod default/payments-5c797645b5-t9sgr (server dry run)
evicting pod default/mixedload-96c9f49fd-kdtw5 (server dry run)
evicting pod argocd/argocd-redis-6fd5864464-nxnvv (server dry run)
evicting pod kube-system/coredns-8db54c48d-fb7vt (server dry run)
evicting pod default/notifications-f7ffbccbb-kx6fc (server dry run)
evicting pod default/events-6469fd576c-vfhn5 (server dry run)
node/k3d-quickticket-server-0 drained (server dry run)

### 1. baseline PDB state (events-pdb allows 1 disruption)
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   1               N/A               1                     5m57s

### 2. tighten events-pdb to minAvailable=2 over 2 replicas -> zero tolerance
poddisruptionbudget.policy/events-pdb patched
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   2               N/A               0                     6m1s
```

Note that the dry run lists everything, including `events-6469fd576c-vfhn5`, as an eviction
candidate. That's not a PDB failure. `drain` checks each pod against its budget *sequentially, at
eviction time*, not upfront.

### Proof 5 — the HTTP 429 eviction rejection

```
### 4. POST eviction for pod events-6469fd576c-frk6q
HTTP 429
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

### 5. the pod is still there — nothing was evicted
events-6469fd576c-frk6q   1/1   Running   0     5m52s
```

`reason: DisruptionBudget`, and the pod survived. So the control is real, not just advisory.

The opposite case, same pod, same API call, but with the budget restored to `minAvailable: 1`,
gets accepted:

```
### 6. restore events-pdb to minAvailable=1 (matches k8s/pdb.yaml)
poddisruptionbudget.policy/events-pdb patched
NAME         MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS   AGE
events-pdb   1               N/A               1                     6m7s

### 7. same eviction now that the budget allows 1 disruption
HTTP 201
{
    "kind": "Status",
    "apiVersion": "v1",
    "metadata": {},
    "status": "Success",
    "code": 201
}
evicted pod was: events-6469fd576c-frk6q
events-6469fd576c-4ff7m   1/1   Running   0     10s
events-6469fd576c-vfhn5   1/1   Running   0     7m10s
```

429 vs 201 on an otherwise identical request, with only the budget changed between them: that is
what "the PDB is enforced" actually looks like.

## Proof 6 — Answer: eviction maths

> **"With 3 gateway replicas and `minAvailable: 1`, what's the maximum number of pods that can
> be evicted simultaneously? Why is your `gateway-pdb` set to `minAvailable: 2` with 5
> replicas?"**

**3 replicas with `minAvailable: 1` means 2 pods can be disrupted at once**
(`ALLOWED DISRUPTIONS = healthy − minAvailable = 3 − 1 = 2`). Two things I want to add here
because they matter in practice:

- "Simultaneously" is really a budget, not a batch. Each eviction is admitted one API call at a
  time against the *current* healthy count. `kubectl drain` fires them one after another and
  re-checks between each one; the budget is what stops it from running ahead.
- The count is of **healthy** pods, not desired replicas. If one of the three is already
  CrashLooping, `ALLOWED DISRUPTIONS` is 1, not 2. So the budget shrinks automatically when the
  service is already degraded, which is the behaviour you actually want.

**Why `gateway-pdb` is `minAvailable: 2` over 5 replicas.** It's a compromise between two failure
modes, and both are real:

- *Too loose* (`minAvailable: 1`): a drain could take 4 of 5 gateway pods at once. With Lab 11's
  rate limiter at `RATE_LIMIT_RPS=10` **per pod**, one surviving replica is a hard ceiling of
  about 10 rps per normalised path against ~50 rps at full strength. That's an 80% capacity cut,
  basically a self-inflicted brownout during routine maintenance.
- *Too strict* (`minAvailable: 4`): the budget becomes unsatisfiable for any node holding 2+
  gateway pods, the drain stalls forever, and someone eventually reaches for `--disable-eviction`
  or `--force`. A budget that gets bypassed protects nothing. Honestly, the most common PDB
  mistake I've seen described is setting it too high, not too low.

`minAvailable: 2` keeps about 40% of steady-state capacity online, allows 3 simultaneous
evictions, and still lets a node drain finish.

## Proof 7 — Answer: what `maxSkew: 1` would do on 3 nodes

> **"In a 3-node cluster, what placement would `maxSkew: 1` produce for 5 gateway pods? What
> about for 7?"**

`maxSkew` bounds `count(most-loaded matching node) − count(least-loaded matching node)`.

- **5 pods / 3 nodes → `2 / 2 / 1`** (skew 1). `3/1/1` is skew 2 and gets rejected; `4/1/0` and
  `5/0/0` even more so.
- **7 pods / 3 nodes → `3 / 2 / 2`** (skew 1). The general rule is that the constraint forces a
  balanced distribution, each node getting `⌊N/3⌋` or `⌈N/3⌉`. It only divides exactly when
  `N mod 3 == 0` (6 pods → `2/2/2`, skew 0).

Two things worth adding, because the mental model breaks without them:

1. **`whenUnsatisfiable: ScheduleAnyway` makes this a preference, not a guarantee.** The
   scheduler scores nodes by resulting skew and picks the best one, but it will still place a pod
   that violates the constraint instead of leaving it Pending. If two nodes are out of memory, 5
   pods really can land `5/0/0`. `DoNotSchedule` is what gives the hard guarantee, and the
   Pending pods that come with it.
2. **Spread is only evaluated at scheduling time.** Nothing rebalances afterwards. After a node
   failure and recovery, the pods just stay wherever they got rescheduled; restoring the spread
   needs a rolling restart or a descheduler.

I think the real lesson is that the two mechanisms complement each other: topology spread makes
sure no single node holds enough gateway pods to make the PDB unsatisfiable, and the PDB makes
sure a drain of any one node stays within the capacity budget. Either one alone leaves a hole.

---

# Task 2 — Graceful Shutdown + Zero-Downtime Migration (4 pts)

## 12.6 `preStop` hook + readinessProbe

### The block as it appears in `k8s/gateway.yaml`

```yaml
      # Lab 12 §12.6 — graceful shutdown budget.
      #
      # 10s preStop sleep + up to 30s for uvicorn to finish in-flight requests
      # = 40s. This MUST be >= preStop duration + worst-case drain, otherwise
      # the kubelet sends SIGKILL while preStop is still sleeping and the whole
      # exercise is pointless. The default of 30s would leave only 20s of
      # actual drain time.
      terminationGracePeriodSeconds: 40
      containers:
        - name: gateway
          ...
          # Lab 12 §12.6 — preStop hook.
          #
          # Pod deletion fans out to two independent control loops that race:
          #   (a) the kubelet, which runs preStop and then sends SIGTERM;
          #   (b) the endpoints/EndpointSlice controller -> every node's
          #       kube-proxy, which rewrites iptables so this pod stops
          #       receiving new connections.
          # Nothing in Kubernetes orders (b) before (a). Without a preStop
          # hook, uvicorn typically gets SIGTERM and closes its listener before
          # kube-proxy on every node has caught up, and the connections that
          # arrive in that window are RST -> the client sees a 502.
          #
          # `sleep 10` does not "do" anything except hold SIGTERM back long
          # enough for (b) to finish everywhere. The container keeps serving
          # normally for those 10 seconds — that is the point.
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 10"]
          readinessProbe:
            httpGet:
              path: /metrics
              port: 8080
            periodSeconds: 2
            failureThreshold: 1
```

> ⚠️ **Deliberate deviation: the readiness path stays `/metrics`, not `/health`.**
> The lab's snippet specifies `httpGet /health`. That is the exact configuration Lab 8
> Experiment 3 measured as the single worst resilience defect in this system: `/health` is a
> *deep* check that calls `events` and `payments`, so a Redis outage propagated
> `events /health 503` → `events` NotReady → gateway `/health` 503 → **all 5 gateway pods
> NotReady** → the gateway Service had zero endpoints → 18.4 rps dropped to 0 in 19 seconds, even
> though every one of those pods still served `GET /events` in 5 ms when hit directly by pod IP.
> Reverting to `/health` just to match a code sample would trade a measured availability property
> for a cosmetic match, and it would also break Lab 11's soft `notifications` dependency.
>
> What §12.6 actually needs from this block is **speed**, and that part *was* changed:
> `periodSeconds: 5, failureThreshold: 2` (up to 10 s to leave the endpoints) became
> `periodSeconds: 2, failureThreshold: 1` (up to 2 s, comfortably inside the 10 s preStop
> window). The lab's own pitfall list makes exactly this point: "preStop alone is not enough…
> without the probe, preStop sleep is wasted because the pod is still in endpoints."

**I applied this to all four services, not just the gateway.** §12.2 Run 1 measured 2 × 502
caused by killing an *events* pod, so `terminationGracePeriodSeconds: 40` plus the same `preStop`
hook went into `k8s/events.yaml`, `k8s/payments.yaml` and `k8s/notifications.yaml` too. Fixing
only the gateway would have left the hole that actually produced the errors.

### Rolling restart under load

```
### T0 18:35:52  — before restart
  5xx increase[1m]: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102152.523,"0"]}]}}
  5xx raw counter: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102152.635,"0"]}]}}
### kubectl argo rollouts restart gateway  @ 18:35:52
rollout 'gateway' restarts in 0s
Progressing - waiting for rollout spec update to be observed
Progressing - rollout is restarting
Healthy
### restart finished @ 18:37:01
```

```
### T1 18:37:29 — after restart
  5xx increase[3m]: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102249.928,"0"]}]}}
  5xx raw counter: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102250.044,"0"]}]}}
  statuses[3m]:   (condensed from the JSON) 200 -> 768.287, 409 -> 663.780, 502 -> 0
### range scan over the restart window (catches counter resets from pod replacement)
  5xx series present in window: NONE (zero 5xx)
```

**All 5 gateway pods were replaced in 69 seconds under ~13 rps, zero 5xx.**

## 12.7 `CREATE INDEX CONCURRENTLY` migration

### The failure the wrapper exists to prevent

Before writing the final version, I ran the migration once *without* `autocommit_block()` just to
see the failure mode for myself:

```
### alembic upgrade head WITHOUT autocommit_block (expected to fail)
sqlalchemy.exc.InternalError: (psycopg2.errors.ActiveSqlTransaction) CREATE INDEX CONCURRENTLY cannot run inside a transaction block

[SQL: CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_events_event_date ON events (event_date)]
### restored; alembic_version unchanged:
5fd94f526fb8
```

The transaction rolled back cleanly, so `alembic_version` was untouched. Worth pointing out,
because that clean rollback is exactly what you *lose* once the wrapper goes in.

### The migration — `migrations/versions/3a0912e70ed1_index_events_event_date_concurrently.py`

```python
def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_event_date',
            'events',
            ['event_date'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            'idx_events_event_date',
            table_name='events',
            postgresql_concurrently=True,
            if_exists=True,
        )
```

Three details here and all three matter:

- **`autocommit_block()`** commits the outer transaction, switches the connection to autocommit
  for the block, and opens a fresh transaction afterwards for the `alembic_version` stamp.
  Postgres refuses `CREATE INDEX CONCURRENTLY` inside a transaction because CONCURRENTLY isn't
  one statement, it's two table scans with a wait in between for every transaction that could
  still see the old snapshot. That wait can't happen inside the transaction that's doing the
  waiting.
- **`if_not_exists=True`** is the price you pay for losing atomicity. An interrupted CONCURRENTLY
  build (cancelled query, dropped connection, evicted pod) leaves an **invalid** index behind
  instead of rolling back, and there's no transaction to undo it. So the revision has to be
  re-runnable. `if_exists=True` on the downgrade does the same thing in reverse.
- **The downgrade is concurrent too.** Plain `DROP INDEX` takes ACCESS EXCLUSIVE and blocks
  *reads* as well as writes. A rollback that causes its own outage isn't really a rollback.

### Run under live traffic

```
### 5xx before
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102422.657,"0"]}]}}
### request rate right now (proves traffic is live during the migration)
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102422.926,"12.981818181818182"]}]}}
### time alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 5fd94f526fb8 -> 3a0912e70ed1, index events.event_date concurrently

real	0m0.663s
user	0m0.533s
sys	0m0.077s
```

```
### 5xx after
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102446.511,"0"]}]}}
### diff 5xx.before 5xx.after (timestamps differ; the VALUE is what matters)
  identical: 0 -> 0
### range scan over the migration window
  5xx series present: NONE — zero 5xx
```

### `\d events` showing the index

```
$ kubectl exec -i $(kubectl get pod -l app=postgres -o name) -- psql -U quickticket -d quickticket -c '\d events'
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

And the check that actually matters more than `\d`, because `\d` shows invalid indexes too:

```
$ psql -c "SELECT c.relname, i.indisvalid, i.indisready FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname='idx_events_event_date';"
        relname        | indisvalid | indisready
-----------------------+------------+------------
 idx_events_event_date | t          | t
```

> 💡 Sub-second on a 5-row table, as the lab says it would be. The syntax is what's being graded
> here, not the timing.

## 12.8 Expand-and-contract rename — the design sketch

Rename `events.event_date` → `events.scheduled_at` with zero downtime. **3 migrations + 2 code
deploys, interleaved.** The invariant that drives the whole sequence is this:

> At every intermediate point, **both** the currently-deployed code and the
> about-to-be-deployed code must work against the schema as it is *right now*.

A code deploy is never instant. During a rolling update, old and new pods serve the same Service
at the same time, so every step has to be compatible with its neighbours on both sides.

1. **Migration 1, expand.** `ALTER TABLE events ADD COLUMN scheduled_at TIMESTAMPTZ;`
   Nullable, no default, which is a catalog-only change in Postgres (one `pg_attribute` row), so
   the ACCESS EXCLUSIVE lock is only held for microseconds and no row gets rewritten. `NOT NULL`
   with no default would just fail on a table with rows, and `NOT NULL` with a volatile default
   rewrites every row under ACCESS EXCLUSIVE, which means minutes of total blockage on a large
   table. No deployed code knows the column exists yet, so this step is invisible and trivially
   reversible.
2. **Code deploy A, dual-write, fallback-read.** Writes go to **both** columns; reads use
   `COALESCE(scheduled_at, event_date)`. This build is correct against un-backfilled rows
   (`scheduled_at IS NULL` falls back) *and* backfilled ones, which is what lets migration 2 run
   at any time afterwards without coordination. It's also correct against the pre-A schema during
   a rolling update, since M1 already ran.
3. **Migration 2, backfill + tighten.**
   `UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL;` then
   `ALTER TABLE events ALTER COLUMN scheduled_at SET NOT NULL;`
   Safe under live traffic because deploy A tolerates both states row-by-row, so there's no
   instant where a request can see a broken row. The `WHERE` clause makes it idempotent: a re-run
   only copies what's still missing and never overwrites a value a concurrent writer already set.
   The `NOT NULL` has to come *after* the backfill, in the same revision.
4. **Code deploy B, single mode.** Read and write `scheduled_at` only. The `COALESCE` is now
   provably dead code (`scheduled_at` is NOT NULL, so it can only ever return its first
   argument). **The point of this deploy is to remove every reference to `event_date` from
   running code**, which is what migration 3 needs before it can run.
5. **Migration 3, contract.** `ALTER TABLE events DROP COLUMN event_date;`
   Run only after `kubectl rollout status` confirms deploy B is fully rolled out.

Two things the numbered list doesn't really show on its own:

- **Only M2's position is flexible.** M2 could just as well have run before deploy A, since
  nothing read the column yet. M1 has to come before deploy A, deploy B has to come before M3,
  and M3 has to be last. That asymmetry is the real structure of the sequence.
- **The old column's indexes and constraints don't follow the rename.** Dropping `event_date`
  silently drops any index on it, so the replacement index on `scheduled_at` has to be built,
  concurrently, *before* the drop. Forgetting this is the classic thing that happens right after
  a successful rename: schema looks correct, but an `ORDER BY` that used to be an index scan is
  now a full sort.

### Proof — Answer: why `CREATE INDEX CONCURRENTLY` matters

> **"Why does `CREATE INDEX CONCURRENTLY` matter? What happens if you omit it on a table with
> 10M rows?"**

Plain `CREATE INDEX` takes a **SHARE** lock on the table for the whole build. SHARE allows
concurrent reads but conflicts with `ROW EXCLUSIVE`, which is what every `INSERT`/`UPDATE`/
`DELETE` takes, so **every write blocks for the duration**. On 10M rows that's minutes.

The real damage isn't the delay itself, it's how it amplifies, which is worth spelling out
because "writes are a bit slow for two minutes" sounds survivable and it isn't:

1. Writers block on the lock instead of returning.
2. Each blocked writer holds its connection open. The `events` service here has
   `DB_MAX_CONNS=10`, so the pool drains in seconds.
3. Once the pool is empty, requests that need **no lock at all**, pure reads, can no longer get a
   connection either. The outage spreads from writes to everything.
4. Readiness probes are HTTP requests too. `events` readiness is `GET /events`, which needs a
   connection, so it starts failing, pods go NotReady, and they leave the Service. That's the
   same cascade Lab 8 measured, just triggered by an index build this time.
5. Retries pile on top. Lab 11's gateway retries with backoff, which multiplies the offered load
   against a backend that's already stalled.

So the honest answer to "what happens" is: a `CREATE INDEX` that *looks* like it only slows down
writes actually takes the whole service down, and it stays down until the index finishes, you
can't even cancel your way out quickly because the rollback has to be waited on too.

`CONCURRENTLY` takes **SHARE UPDATE EXCLUSIVE** instead, which doesn't conflict with
`ROW EXCLUSIVE`, so reads and writes both keep going. The costs are real but bounded: two table
scans instead of one (roughly doubling wall-clock time), it can't run inside a transaction, and
an interrupted build leaves an `indisvalid = false` index that has to be dropped and rebuilt by
hand. Those are trades worth making. On any table large enough for this to matter, the
non-concurrent version isn't a slower option, it's an outage.

### Proof — Answer: why migration 3 must come after deploy B

> **"In your expand-and-contract sketch, why MUST migration 3 (drop old column) come after
> deploy B has fully rolled out? What goes wrong if it runs before?"**

Because until deploy B has replaced the last deploy-A pod, there is still **running code that
names `event_date` in SQL**, and dropping the column makes that SQL unparseable.

Concretely, a surviving deploy-A pod issues
`SELECT ..., COALESCE(e.scheduled_at, e.event_date) ... FROM events`. `COALESCE` doesn't
tolerate a missing column, because column resolution happens at parse time, way before any value
gets evaluated. Postgres returns `UndefinedColumn: column e.event_date does not exist`, the
psycopg2 call raises, the FastAPI handler 500s, and the gateway turns that into a **502 for every
single `/events` and `/events/{id}` request** that kube-proxy routes to that pod. With 2 events
replicas and one still on deploy A, that's ~50% of all catalogue traffic failing,
continuously, until the last old pod dies.

This failure has three properties that make it worse than it sounds at first:

- **It's not self-healing.** A crash-loop eventually resolves itself, but this pod is perfectly
  healthy and will keep serving errors forever. Worse, `events` readiness is `GET /events`, which
  now 500s, so the pod *does* get pulled from the Service. That looks like mercy, but it means
  the remaining capacity halves at the same moment, and if the drop had raced a deploy across all
  replicas you'd be left with an empty Service and a total outage.
- **Rolling back the migration doesn't undo it.** `ALTER TABLE ... ADD COLUMN event_date` gives
  the column back empty, so every row needs re-backfilling from `scheduled_at` before old code is
  correct again, and you'd be doing that during an active incident.
- **It's invisible in staging**, where "deploy then migrate" finishes in seconds and the overlap
  window never really opens. It only shows up at production replica counts, on the day the
  rollout happens to be slow.

`kubectl rollout status deployment/events` returning is the gate for this. I checked it before
running M3 in the Bonus below.

## 12.9 (Optional) HPA observation

`k8s/gateway-hpa.yaml`. The only non-obvious part is `scaleTargetRef`, which has to name
`argoproj.io/v1alpha1 / Rollout`, not `apps/v1 / Deployment`:

```yaml
spec:
  scaleTargetRef:
    apiVersion: argoproj.io/v1alpha1
    kind: Rollout
    name: gateway
  minReplicas: 5
  maxReplicas: 12
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

It resolves the Rollout correctly. `TARGETS` shows a real percentage instead of `<unknown>`:

```
$ kubectl get hpa gateway
NAME      REFERENCE         TARGETS        MINPODS   MAXPODS   REPLICAS   AGE
gateway   Rollout/gateway   cpu: 34%/70%   5         12        5          30s
```

34% is steady-state: `kubectl top pod -l app=gateway` reported 17–20 m of CPU per pod against the
50 m **request** (utilisation is measured against requests, not limits, so 70% of 50 m is 35 m).

I drove it with `labs/lab12/load-hpa.yaml` (Lab 10's Locust runner at `-u 200 -r 20 -t 120s`):

```
# running gateway pod count, sampled every 10s
18:48:27  pods=5          <- load job starts
18:49:19  pods=10         <- scale-up in flight
18:49:40  pods=12         <- capped at maxReplicas
18:50:00 .. 18:52:05  pods=12
# then, sampled every 20s against `kubectl get rollout gateway -o jsonpath={.spec.replicas}`
18:52:54 .. 18:55:56  replicas=12  pods=12
18:56:16  replicas=7   pods=7      <- scale-down after the 5m stabilisation window
18:56:36  replicas=5   pods=5

$ kubectl describe hpa gateway | sed -n '/Events:/,$p'
Events:
  Type    Reason             Age    From                       Message
  ----    ------             ----   ----                       -------
  Normal  SuccessfulRescale  3m8s   horizontal-pod-autoscaler  New size: 10; reason: cpu resource utilization (percentage of request) above target
  Normal  SuccessfulRescale  2m53s  horizontal-pod-autoscaler  New size: 12; reason: cpu resource utilization (percentage of request) above target
```

This is a full control loop: 5 → 10 → 12 (capped at `maxReplicas`) on the way up, and back down
to `minReplicas: 5` after the default 5-minute scale-down stabilisation window.

> ⚠️ **This run produced 5xx, and that's actually the interesting part.** I ran it *after* every
> zero-5xx proof above (all of which are timestamped and window-scoped), so it doesn't
> contaminate them. 200 concurrent users is far past this cluster's capacity, and the errors
> stack up in a pretty readable order:
>
> ```
> 1991  GET /events: 429 Too Many Requests        <- Lab 11's rate limiter, working as designed
> 2816  GET /events: 502 Bad Gateway              <- events service saturated behind it
> 1064  POST /events/{id}/reserve: 500            <- DB_MAX_CONNS=10 exhausted
>  562  GET /health: 503                          <- deep health check failing under load
>  699+178  reserve: 409 Conflict                 <- sold out (inventory note above)
> ```
>
> Reading it top-down: the rate limiter shed about 2000 requests against its `RATE_LIMIT_RPS=10` ×
> 5 replicas ≈ 50 rps ceiling. I left the knob at its committed value on purpose instead of
> raising it, because shedding load is the correct behaviour here, and a shed request still costs
> a full trip through the middleware stack, which is what the HPA needed to see anyway. What got
> through was still too much for 2 `events` replicas with a 10-connection pool, so the gateway's
> *upstream* failed and returned 502/500. **The gateway scaled, its dependencies didn't.** That's
> the standard lesson about autoscaling one tier of a stack on its own, and on a single node the
> extra gateway pods were competing for the same cores anyway. As the lab notes, the controller's
> decisions here are real, but the added capacity isn't.
>
> After the run: the load Job finished, the HPA went back to 5 replicas, and the 5xx **rate** is
> back to 0 (`sum(rate(gateway_requests_total{status=~"5.."}[2m])) = 0`).

---

# Bonus Task — Execute the Expand-and-Contract Rename (2 pts)

I ran this live on the cluster under continuous mixedload traffic. Five transitions
(M1 → Deploy A → M2 → Deploy B → M3), ~6150 requests served during the window, **zero 5xx**.

## Proof 3a — `\d events` BEFORE migration 1

```
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

## Proof 4a — baseline

```
$ kubectl exec -n monitoring deployment/prometheus -- wget -qO- \
    '.../query?query=sum(gateway_requests_total{status=~"5.."}) or vector(0)' | tee /tmp/5xx.baseline
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102465.935,"0"]}]}}

$ # live traffic rate at baseline
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102466.076,"12.872727272727273"]}]}}
```

## Proof 1 — the three migration files (`upgrade()` bodies)

### M1 — `25b2cce54ede_add_events_scheduled_at_column.py`

```python
def upgrade() -> None:
    op.add_column(
        'events',
        sa.Column('scheduled_at', sa.TIMESTAMP(timezone=True), nullable=True),
    )
```

### M2 — `7902d73ca526_backfill_events_scheduled_at.py`

```python
def _has_column(name: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return name in {c['name'] for c in insp.get_columns('events')}


def upgrade() -> None:
    if _has_column('event_date'):
        op.execute(
            "UPDATE events SET scheduled_at = event_date WHERE scheduled_at IS NULL"
        )
    op.alter_column('events', 'scheduled_at', nullable=False)
```

### M3 — `1810a4daefb4_drop_events_event_date.py`

```python
def upgrade() -> None:
    # Built outside the transaction (CONCURRENTLY), so it must come first: if
    # the drop below failed we would be left with a harmless extra index, which
    # is a much better failure mode than a dropped column and no index.
    with op.get_context().autocommit_block():
        op.create_index(
            'idx_events_scheduled_at',
            'events',
            ['scheduled_at'],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )

    if _has_column('event_date'):
        op.drop_column('events', 'event_date')
```

**On the `_has_column` guards.** This project has two independent sources of the same schema: the
Alembic chain, and the bootstrap `seed.sql` / `postgres-seed` ConfigMap that a freshly created PV
replays. §12.14 requires the seed to describe the *post*-rename schema, so on a fresh cluster
`event_date` never exists, and an unguarded M2/M3 would just fail. The guards make
`alembic upgrade head` a clean no-op there, while staying exact on this database. It's the same
idea as the `if_not_exists=True` the lab already specifies for the CONCURRENTLY index: make the
revision safe to apply against more than one starting state.

## Proof 2 — `app/events/main.py` between Deploy A and Deploy B

**Deploy A** (dual-mode, `COALESCE` fallback): `diff` against the pre-lab file:

```diff
+        # Lab 12 Bonus — Deploy A of the event_date -> scheduled_at rename.
+        # Reads prefer the new column and fall back to the old one, so this
+        # build is correct BOTH before the backfill (scheduled_at IS NULL
+        # everywhere) and after it. Aliased back to `event_date` so the
+        # response shape is byte-identical to Deploy pre-A.
         cur.execute("""
-            SELECT e.id, e.name, e.venue, e.event_date, e.total_tickets, e.price_cents,
+            SELECT e.id, e.name, e.venue,
+                   COALESCE(e.scheduled_at, e.event_date) AS event_date,
+                   e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY e.event_date
+            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
         """)
```

(the same change applied to the `GET /events/{event_id}` handler)

**Deploy B** (single mode): `diff` between Deploy A and Deploy B:

```diff
         cur.execute("""
             SELECT e.id, e.name, e.venue,
-                   COALESCE(e.scheduled_at, e.event_date) AS event_date,
+                   e.scheduled_at AS event_date,
                    e.total_tickets, e.price_cents,
                    COALESCE(SUM(o.quantity), 0) as confirmed
             FROM events e LEFT JOIN orders o ON e.id = o.event_id
-            GROUP BY e.id ORDER BY COALESCE(e.scheduled_at, e.event_date)
+            GROUP BY e.id ORDER BY e.scheduled_at
         """)
```

**Write paths: none exist.** `grep -n "INSERT INTO\|UPDATE " app/events/main.py` finds exactly one
write, and it targets `orders`, not `events`. QuickTicket only ever writes `events` through the
boot-time seed, so the "dual-write" half of deploy A is a genuine no-op here, which is what the
lab expects. The seed *is* the write path, and it got updated at deploy B (§12.14).

**Response-shape decision:** the SELECT keeps the `event_date` alias and the JSON key stays
`date`, unchanged since Lab 1. Renaming a database column and renaming a public API field are two
separate migrations, and combining them would turn an internal change into a breaking one for
every client. The gateway and mixedload saw no difference at all across all five transitions.

**Image tags:** `ghcr.io/vanady39/quickticket-events:lab12a` and `:lab12b`, new tags per Lab 11's
handoff rule (reusing a tag would not roll the pods). Both built with `docker build` and loaded
with `k3d image import -c quickticket`; `imagePullPolicy` stays `Never`.

## The five transitions, in order

### M1 — expand

```
=== M1: add events.scheduled_at  @ 18:41:38 ===
INFO  [alembic.runtime.migration] Running upgrade 3a0912e70ed1 -> 25b2cce54ede, add events.scheduled_at column
5xx after M1: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102504.494,"0"]}]}}
sample row:   1 | 2026-09-15 09:00:00+00 |
  2 | 2026-10-01 18:00:00+00 |
```

Column added, every `scheduled_at` is NULL, and nothing reads it yet.

### Deploy A — dual-write / fallback-read

```
=== Deploy A  @ 18:43:06 ===
deployment.apps/events configured
Waiting for deployment "events" rollout to finish: 1 out of 2 new replicas have been updated...
deployment "events" successfully rolled out
5xx after Deploy A: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102609.758,"0"]}]}}
### /events response through the gateway (scheduled_at still NULL -> fallback path)
[
    {
        "id": 1,
        "name": "Go Conference 2026",
        "venue": "Main Hall A",
        "date": "2026-09-15T09:00:00+00:00",
        ...
```

The `date` field is still correct even with `scheduled_at` entirely NULL, so the `COALESCE`
fallback is doing real work here, not just sitting there for show.

### M2 — backfill + NOT NULL

```
=== M2: backfill + NOT NULL  @ 18:44:01 ===
INFO  [alembic.runtime.migration] Running upgrade 25b2cce54ede -> 7902d73ca526, backfill events.scheduled_at
5xx after M2: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102647.054,"0"]}]}}
### backfill landed?
 id |       event_date       |      scheduled_at      | match
----+------------------------+------------------------+-------
  1 | 2026-09-15 09:00:00+00 | 2026-09-15 09:00:00+00 | t
  2 | 2026-10-01 18:00:00+00 | 2026-10-01 18:00:00+00 | t
  3 | 2026-11-20 10:00:00+00 | 2026-11-20 10:00:00+00 | t
  4 | 2026-09-22 14:00:00+00 | 2026-09-22 14:00:00+00 | t
  5 | 2026-10-10 10:00:00+00 | 2026-10-10 10:00:00+00 | t
(5 rows)
```

### Deploy B — single mode + seed update

```
=== Deploy B  @ 18:45:06 ===
deployment.apps/events configured
configmap/postgres-seed configured
deployment "events" successfully rolled out
### confirm NO pod is still on Deploy A (M3 depends on this)
events-7795fdf78d-rqmzj  ghcr.io/vanady39/quickticket-events:lab12b
events-7795fdf78d-wjqh5  ghcr.io/vanady39/quickticket-events:lab12b
events-8cc6f4b9-2t5tp  ghcr.io/vanady39/quickticket-events:lab12a     <- still terminating
events-8cc6f4b9-pd688  ghcr.io/vanady39/quickticket-events:lab12a     <- still terminating
5xx after Deploy B: {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102727.927,"0"]}]}}
```

**`kubectl rollout status` returning was not enough by itself.** Two `lab12a` pods were still
listed seconds afterwards, inside their `preStop` sleep + 40 s grace window and still in the
Service. Running M3 at that moment would have been exactly the ordering mistake §12.8 describes.
I waited and checked again:

```
$ kubectl get pod -l app=events -o jsonpath='...image...'
events-7795fdf78d-rqmzj  ghcr.io/vanady39/quickticket-events:lab12b
events-7795fdf78d-wjqh5  ghcr.io/vanady39/quickticket-events:lab12b
```

I updated the seed files in all three places the schema is duplicated: `app/seed.sql`,
`k8s/chart/files/seed.sql`, and the `postgres-seed` ConfigMap in `k8s/postgres.yaml`. So a
freshly created PV bootstraps on `scheduled_at`.

### M3 — contract

```
=== M3: drop events.event_date  @ 18:46:41 ===
INFO  [alembic.runtime.migration] Running upgrade 7902d73ca526 -> 1810a4daefb4, drop events.event_date

real	0m0.760s
```

## Proof 4 — zero 5xx delta across all five transitions

```
### final 5xx
{"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102810.367,"0"]}]}}
### diff 5xx.baseline 5xx.final (values)
  NO DIFFERENCE — 0 -> 0 across all five transitions
```

The literal `diff` the lab asks for isn't byte-identical, and I think it's worth showing why
instead of just quietly swapping in a cleaner command. A Prometheus instant query embeds its
evaluation timestamp in the response:

```
$ diff /tmp/5xx.baseline /tmp/5xx.final
1c1
< {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102465.935,"0"]}]}}
---
> {"status":"success","data":{"resultType":"vector","result":[{"metric":{},"value":[1790102810.367,"0"]}]}}
```

`1790102465.935` → `1790102810.367` is just the 345 seconds the sequence took. The **value**, the
second element of each `value` pair, is `"0"` in both, and that's the number the acceptance
criterion actually cares about.

Backed up by two stronger checks:

```
### gateway pods unchanged for the whole bonus window => the raw counter diff is valid
  gateway-564c76b555-6ld22 age=11m
  gateway-564c76b555-gd7jq age=10m
  gateway-564c76b555-pnrk8 age=11m
  gateway-564c76b555-r7plm age=10m
  gateway-564c76b555-x48ww age=9m16s
### range scan of every 5xx series over the whole bonus window
  window: 359s
  5xx series present: NONE — zero 5xx across M1, Deploy A, M2, Deploy B, M3
### total requests served during the bonus window
  (condensed from the JSON) 200 -> 3216.505    409 -> 2934.568    no 5xx series at all
```

The gateway pods weren't replaced during the bonus, so the raw counter genuinely spans the whole
window with no reset. The `diff` is a valid end-to-end measurement here, not just two reads that
happen to agree.

## Proof 3b — `\d events` AFTER migration 3

```
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

$ alembic current
1810a4daefb4 (head)
```

`event_date` is gone, `scheduled_at` is `NOT NULL`, and the index moved with the data.

## Proof 5 — Answer: which reordered step would have caused 5xx?

> **"You ran 5 transitions (M1, Deploy A, M2, Deploy B, M3) under live traffic. Which single step
> would have caused 5xx if you'd reordered it earlier?"**

**M3, the column drop.** It's the only step that *removes* something, and removal is the only
kind of operation that can invalidate code that's already running.

Here's what each of the five steps does to the set of names available to running code:

| Step | Effect | Moved earlier? |
|---|---|---|
| M1 add column | **adds** a name nothing uses | harmless anywhere before deploy A |
| Deploy A | **adds** a dependency on `scheduled_at` (which M1 created) | breaks only if it precedes M1 |
| M2 backfill | **changes values** both code paths tolerate | genuinely order-free |
| Deploy B | **removes** the code's dependency on `event_date` | breaks only if it precedes M2's NOT NULL |
| **M3 drop column** | **removes a name running code may still reference** | **breaks immediately** |

Move M3 anywhere before deploy B has fully rolled out, and every surviving deploy-A pod starts
failing on `COALESCE(scheduled_at, event_date)` with `UndefinedColumn`, because column resolution
happens at parse time. That's a 500 from `events`, a 502 from the gateway, on *every* `/events`
and `/events/{id}` request routed to that pod, continuously, until the pod dies. This is exactly
the failure the strict wait after deploy B is there to prevent, and the two still-terminating
`lab12a` pods show how close `kubectl rollout status` alone would have come to missing it.

The runner-up is **deploy B before M2**, which is a real bug too but a subtler one: reading
`scheduled_at` directly while some rows are still NULL means `r[3].isoformat()` raises
`AttributeError: 'NoneType'` → 500 → 502. It only fails for un-backfilled rows instead of all of
them, which makes it *harder* to catch, not safer.

M1 and deploy A can't really be usefully reordered relative to each other either. Deploy A before
M1 fails on the same `UndefinedColumn`, for the same reason. It's really the same lesson: code
and schema have to overlap in both directions during any transition, and only additive steps
create that overlap.

## Proof 6 — Answer: batching the backfill at 10M rows

> **"Production scale: the same backfill on a 10M-row table would lock writes for minutes if done
> as a single UPDATE. Write the batching pattern (in 5-10 lines of pseudocode) that keeps each
> transaction small."**

```python
BATCH = 10_000
last_id = 0
while True:
    with engine.begin() as tx:                      # one short transaction per batch
        tx.execute("SET LOCAL lock_timeout = '2s'") # never queue behind a long query
        rows = tx.execute("""
            UPDATE events SET scheduled_at = event_date
            WHERE id IN (SELECT id FROM events
                         WHERE id > :last AND scheduled_at IS NULL
                         ORDER BY id LIMIT :n FOR UPDATE SKIP LOCKED)
            RETURNING id
        """, last=last_id, n=BATCH).fetchall()
    if not rows:
        break
    last_id = max(r.id for r in rows)
    time.sleep(0.1)                                 # let replicas and autovacuum catch up
```

Why each line is there: a single `UPDATE events SET ... WHERE scheduled_at IS NULL` fails at this
scale in four different ways, and the batching deals with all four:

- **Row locks.** One transaction holds 10M row locks until commit, so any concurrent write to any
  of those rows blocks for the whole run. 10k-row batches only hold locks for milliseconds.
- **Dead tuples.** Postgres MVCC rewrites every updated row, so the single-statement version
  doubles the table's physical size and leaves 10M dead tuples that autovacuum can't reclaim
  until the transaction commits, after which it has one enormous vacuum to do anyway. Batching
  lets autovacuum keep up as you go; the `sleep` just gives it room.
- **Replication lag.** One 10M-row transaction ships as one huge WAL burst that a replica has to
  apply serially, which spikes lag and stalls anything reading from replicas. Small batches keep
  lag flat.
- **No resumability.** Interrupt the single statement at 90% and you've done nothing. The batched
  loop is driven by `WHERE scheduled_at IS NULL` plus a keyset cursor, so it's idempotent and can
  restart from wherever it stopped.

The keyset cursor (`id > :last ... ORDER BY id LIMIT n`) instead of `OFFSET` keeps every batch an
index range scan rather than degrading to O(n²) over the run. `FOR UPDATE SKIP LOCKED` lets the
backfill step around rows a live transaction is currently holding instead of blocking on them,
they just get picked up on the next pass. `SET LOCAL lock_timeout` makes a batch fail fast and
retry instead of joining a lock queue, which is what turns a slow migration into an outage.

## Proof 7 — Answer: why the M3 downgrade is not real rollback safety

> **"Your downgrade from migration 3 re-adds `event_date` and backfills it. Why is that *not*
> sufficient for true rollback safety once Deploy B is live in production? What would have to be
> true for the rollback to be safe?"**

The downgrade restores the **schema**. It can't restore the **data**, and once deploy B is live
those two have already diverged.

While deploy B runs, every write goes to `scheduled_at` only. `event_date` no longer exists, so
nothing keeps it updated. When the downgrade runs
`ADD COLUMN event_date` + `UPDATE events SET event_date = scheduled_at`, it copies whatever
`scheduled_at` holds *at that moment*, which is only correct if no write has happened since M3.
In production that's never true. Every event created or rescheduled during the deploy-B window is
data that old code never got to see, and copying it back doesn't make old code able to process
it. The rollback just hides the divergence behind a column that looks populated.

Three sharper failures hide behind that same fact:

1. **The rollback is itself a race.** The `UPDATE` isn't atomic with respect to live traffic. A
   row written by deploy B *after* the backfill statement but *before* the old code gets deployed
   ends up with `event_date = NULL`, and the old `ORDER BY event_date` and `r[3].isoformat()`
   paths handle that by crashing. So the rollback creates a new source of 500s.
2. **Timestamps are actually a forgiving case.** For a rename where the new column's *semantics*
   also changed, say different timezone handling, a different unit, a richer type, copying values
   back produces data that's structurally valid and semantically wrong. Silent corruption is
   worse than an error.
3. **The rollback path takes production traffic while it runs.** Unlike the forward migration,
   which was carefully sequenced over five steps, the rollback gets attempted during an incident,
   under time pressure, usually by whoever happens to be on call. Its blast radius is bigger and
   it's tested worse.

**What would actually have to be true for the rollback to be safe:**

- **Keep the old column, don't drop it.** Deploy B removes *reads* of `event_date`; M3 doesn't
  have to follow right away. Leaving the column in place (still dual-written by a trigger, or
  just stale-but-present) makes deploy B reversible by redeploying code alone, no schema change,
  no data copy, seconds instead of minutes.
- **Separate "code rollback" from "schema rollback".** The safe rollback of deploy B is really
  *redeploy deploy A*, which reads `COALESCE(scheduled_at, event_date)` and is correct against
  both schemas. Deploy A is the rollback target here, not M2's downgrade.
- **Make M3 irreversible on purpose, and gate it on time.** Run the drop only once deploy B has
  been stable long enough that rolling back to pre-B code is no longer a realistic option, days,
  not minutes, and only after a verified backup exists. Expand-and-contract's real rule is that
  the contract step is the point of no return. The discipline is *delaying* it, not trying to make
  it reversible.
- **If the old column has to be dropped early, dual-write through a trigger** for the overlap
  window: `BEFORE INSERT OR UPDATE ON events SET NEW.event_date = NEW.scheduled_at`. That way the
  old column stays current with no application support needed, and rollback is just a code
  deploy.

So the honest summary is: a downgrade script is a schema-migration tool, not a rollback plan. The
actual rollback plan for a contract step is "don't run it yet."

---

## Files changed

| File | Change |
|---|---|
| `k8s/pdb.yaml` | **new**: 4 PodDisruptionBudgets |
| `k8s/gateway-hpa.yaml` | **new**: HPA targeting the Rollout (§12.9) |
| `k8s/gateway.yaml` | `topologySpreadConstraints`, `terminationGracePeriodSeconds: 40`, `preStop`, readiness timing 2s/1 |
| `k8s/events.yaml` | `replicas: 2`, grace period + `preStop`, image → `:lab12b` |
| `k8s/payments.yaml` | `replicas: 2`, grace period + `preStop` |
| `k8s/notifications.yaml` | `replicas: 2`, grace period + `preStop` |
| `k8s/postgres.yaml` | seed ConfigMap → `scheduled_at` |
| `app/seed.sql`, `k8s/chart/files/seed.sql` | → `scheduled_at` |
| `app/events/main.py` | Deploy B reads (`scheduled_at AS event_date`) |
| `migrations/versions/3a0912e70ed1_*.py` | **new**: CONCURRENTLY index |
| `migrations/versions/25b2cce54ede_*.py` | **new**: M1 add column |
| `migrations/versions/7902d73ca526_*.py` | **new**: M2 backfill + NOT NULL |
| `migrations/versions/1810a4daefb4_*.py` | **new**: M3 drop column + carry index |
| `labs/lab12/load-hpa.yaml` | **new**: Locust Job for the HPA observation |
| `submissions/lab12.md` | this file |

---

## PR description

```text
- [x] Task 1 done — multi-replica failover + 4 PDBs + topology spread + real eviction-API block
- [x] Task 2 done — preStop + zero-error rolling restart + CONCURRENTLY migration + expand-and-contract sketch
- [x] Bonus Task done — expand-and-contract executed live (3 migrations + 2 deploys, zero 5xx, `event_date` dropped)
- [x] (Optional) 12.9 HPA observation

Notes for the reviewer:
- `gateway` is an Argo Rollout (Lab 7), so `deployment/gateway` does not exist. Commands
  translated throughout; `kubectl argo rollouts {status,restart} gateway` used instead.
- Deliberate deviation: the gateway readinessProbe path stays `/metrics` rather than reverting
  to `/health` as the lab snippet shows. `/health` is the deep check whose readiness wiring
  Lab 8 measured as a 19-second total outage. The probe *timing* was tightened as §12.6
  requires (2s period / 1 failure). Rationale in submissions/lab12.md §12.6.
- `kubectl drain` was not run for real: single-node cluster, the only node is the control
  plane, and a real drain would evict ArgoCD/Prometheus/CoreDNS with nowhere to reschedule.
  `--dry-run=server` plus a real Eviction-API call (429 then 201) exercise the same admission
  check. Explained in §12.5.
- §12.2 found 2 x HTTP 502 on the FIRST pod-kill run that the lab's `increase()` query reported
  as 0 — the query cannot see a counter series coming into existence. Root-caused to the
  missing preStop hook on `events`, fixed in Task 2, re-run clean. Both runs are in the
  submission.
- `preStop` + `terminationGracePeriodSeconds: 40` were applied to all four app services, not
  just the gateway, because that is where the measured errors came from.
- The §12.9 HPA load run (200 users) deliberately produced 5xx by overloading the backend; it
  was run after every zero-5xx proof and all windows are timestamped. Analysis in §12.9.
- Ticket inventory was already ~70 across events 1/2/4 at the start and mixedload exhausted
  event 1 early, so the checkout leg returns 409 for most of the lab. No data top-up was
  attempted. Detailed in the traffic note at the top.
```
