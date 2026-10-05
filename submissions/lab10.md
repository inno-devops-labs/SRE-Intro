# Lab 10 — SRE Portfolio & Reliability Review

Liubov Utenysheva, CBS-03

---

# QuickTicket Reliability Review

## 1. SLO Compliance

SLO defined in Lab 6: **99.5% availability** (non-5xx ratio), alerted via the
`QuickTicket SLO Burn Rate` rule (`(1 - ratio) / (1 - 0.995)`, 6× sustainable burn).
409 Conflict is product behavior (inventory exhausted), not an SLO failure, and is
excluded from the error ratio.

| SLO | Target | Observed | Status |
|-----|--------|----------|--------|
| Availability (non-5xx), 60 s window @ 10u | 99.5% | 100.00% (0/471) | ✅ met |
| Availability (non-5xx), 60 s window @ 50u | 99.5% | 100.00% (0/2222) | ✅ met |
| Availability (non-5xx), 60 s window @ 100u | 99.5% | 54.60% / 56.59% (two runs) | ❌ breached — overload, see §2 |
| p99 latency @ ≤ 50u | < 500 ms (capacity threshold) | 15 ms / 110 ms | ✅ met |

At nominal load (≤ 50 users ≈ 37 RPS) the SLO is met with a wide margin. The 100u
breach is the deliberate overload that defines the capacity ceiling, not a
violation under normal operating conditions.

## 2. Load Test Results

Locust 2.43.4 ran **in-cluster** as a Job (`locustio/locust:2.43.4`, host
`http://gateway:8080`) so traffic is load-balanced by kube-proxy across all 5
gateway replicas. Redis was `FLUSHDB`-ed before each run so stale reservation
holds from Labs 7–9 didn't pollute inventory. Task mix: 70% `GET /events`,
20% `POST /events/{3,5}/reserve` (split across events 3 and 5 so a single
event's inventory can't dominate the error count), 10% `GET /health`. 60 s
duration, `--only-summary`.

| Users | Ramp | RPS | p50 | p95 | p99 | 5xx error rate | 409 (inventory) |
|------:|-----:|----:|----:|----:|----:|---------------:|----------------:|
| 10    | 2/s  | 7.88  | 7 ms  | 10 ms  | 15 ms   | 0% (0/471)      | 0 |
| 50    | 5/s  | 37.15 | 7 ms  | 25 ms  | 110 ms  | 0% (0/2222)     | 32 (1.44% — event 5 sold out, expected) |
| 100   | 10/s | 52.95 | 460 ms | 1300 ms | 2300 ms | **45.40%** (1436/3163) | 0 |
| 100 (re-run, CPU sampling) | 10/s | 57.54 | 260 ms | 890 ms | 5000 ms | **43.41%** (1495/3444) | 0 |

### Breaking point

**100 users is the breaking point** — both stop criteria are exceeded:
5xx rate 43–45% (threshold 0.5%) and p99 2.3–5.0 s (threshold 500 ms). No 200u
run was needed. **Capacity ceiling: 50 users @ ≈ 37 RPS** (last healthy level).

5xx composition at 100u (re-run): 502 Bad Gateway ×1083 (gateway→events
upstream unavailable), 500 ×213, 503 ×150, 504 Gateway Timeout ×49. The
502/504 dominance points at the single `events` replica as the first domino —
confirmed by per-pod CPU in §7. Note 409s drop to zero at 100u: requests fail
with 5xx before they ever reach the inventory check.

## 3. DORA Metrics

Source data (commands + output, captured 2026-10-05):

```bash
$ kubectl get rs -l app=gateway -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep -v '^$' | wc -l
8
$ git log --oneline origin/main | wc -l
48
$ kubectl get analysisrun -o jsonpath='{.items[*].status.phase}' | tr ' ' '\n' | sort | uniq -c
      2 Failed
      2 Successful
```

| Metric | Value | Source / evidence |
|--------|-------|-------------------|
| Deployment Frequency | 8 gateway rollouts in ~15 course days (≈ 4/week, 0.5/day) | 8 distinct ReplicaSets `gateway-*`; 48 commits on `main` since 2026-09-20 |
| Lead Time (commit → running) | < 10 min | CI build+push ≈ 1–2 min + ArgoCD 3-min poll; e.g. commit `7d7163c` 09:40 → tag update `6b78071` → rollout live 10:15 (same day, < 40 min end-to-end including my lab work) |
| Change Failure Rate | 50% (2/4 AnalysisRuns Failed) | `gateway-5898845c97-7-2` Failed, `gateway-65f669b67f-5-2` Failed, then `…-5-2.1` Successful; the two failures were the **intentional bad deploys** from the Lab 7 canary + Lab 8 chaos exercises — the analysis caught them before full rollout, which is the mechanism working as designed |
| Recovery Time | ≈ 2 min (analysis abort) / ≈ 3 min (git revert) | AnalysisTemplate: 60 s initial delay + 3×20 s checks → abort; revert path = commit revert + 3-min ArgoCD poll |

Against DORA elite targets: deploy on demand ✅, lead time < 1 day ✅,
recovery < 1 h ✅, change failure rate 0–15% ❌ (50% — inflated by deliberate
failure injection; without the two intentional bad deploys it would be 0%).

## 4. Top 3 Reliability Risks

1. **`events` is a single replica and the first saturation point.**
   Why it matters: at 100u it was the hottest pod (201 m CPU vs ~50 m/pod for
   gateway) and the gateway's 502/504s show traffic dying at the
   gateway→events hop. One node loss or one hot query takes down all
   read-heavy traffic (70% of the mix).
   Fix: scale `events` to 3 replicas with requests/limits and an HPA on CPU;
   re-run the Locust scenario to confirm the ceiling moves.

2. **Fixed capacity ceiling (~37 RPS) with no autoscaling anywhere.**
   Why it matters: 5 gateway replicas are hard-coded; a 2× traffic spike
   (75 RPS) breaks the SLO within seconds — exactly what the 100u run showed.
   Fix: HPA on gateway and events (CPU + RPS), and a scheduled capacity
   regression (the committed `locustfile.py` makes this a one-command Job).

3. **Redis is a single pod with no persistence — reservation holds are volatile.**
   Why it matters: a Redis restart drops all in-flight holds; two clients
   holding the same seat can both succeed at pay time (oversell race). At 2×
   traffic the blast radius of an unhandled Redis failure grows.
   Fix: enable RDB/AOF snapshots on the existing Redis (cheap, keeps
   single-pod simplicity), and make the payments step the authoritative
   inventory check so a lost hold degrades to a 409, not an oversell.

## 5. Toil Identification

| # | Task (done manually >3×) | How often | How to automate | What it saves |
|---|--------------------------|-----------|-----------------|---------------|
| 1 | Re-seeding Postgres after every restart: `kubectl exec -i <pg-pod> -- psql -U quickticket -d quickticket < app/seed.sql` (Labs 4–8, before the PVC) | ≥ 6 times | Persistent volume (done in Lab 9) + seed only on first boot via initContainer that checks a marker table; schema evolution via Alembic (done in Lab 9) | 2–3 min per restart **and** the data-loss risk that made every Postgres restart a mini-incident |
| 2 | Manually triggering the backup CronJob to test it: `kubectl create job --from=cronjob/postgres-backup manual-N` + `kubectl wait --for=condition=Complete` + `kubectl logs` (Lab 9) | 7 times in one session | A 3-line shell loop (or `make backup-test`) that fans out N jobs and collects logs; longer term, rely on the 5-min schedule + a backup-freshness alert | ~1 min of copy-paste per trigger, and a repeatable regression test for the backup path |
| 3 | Re-creating `kubectl port-forward` after pod restarts (`svc/postgres 5432`, gateway 8080) during Labs 4–9 | ≥ 10 times | In-cluster clients for anything load/automation-related (this lab's Locust Job is the pattern); for interactive dev, a `pf.sh` that runs port-forward in a `while true` restart loop | 30–60 s of broken-work state per pod restart; also removes the port-forward ≠ load-balancing trap entirely for tests |

## 6. Monitoring Gaps

- **No latency alert.** Lab 6 rules cover error rate (5% for 2 m) and SLO burn
  rate only. During the 100u run, p99 climbed to 2.3–5 s *before* the 5xx
  ratio crossed 5% — a slow-but-successful (then failing) dependency pages
  nobody. The alert that would have caught what actually broke:
  `histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le)) > 0.5 for 2m`.
- **No per-upstream dependency view.** The gateway's 502s to `events` only
  surfaced as gateway 5xx; I wanted a `gateway→events` client-error rate per
  upstream to see the first domino immediately.
- **No saturation alerting.** Nothing watches per-pod CPU; `events` at 201 m
  on a single replica is exactly the kind of slow creep that ends in a
  capacity incident.
- **During the Lab 8 chaos experiments** I wished for: pod restart-count and
  readiness-flapping visibility (to distinguish "killed, rescheduled cleanly"
  from "crash-looping"), and per-endpoint error breakdowns instead of one
  aggregated 5xx ratio.

## 7. Capacity Plan

### Per-pod CPU at the breaking point (100u, sampled mid-run)

```bash
$ kubectl top pods -l app=gateway
NAME                       CPU(cores)   MEMORY(bytes)
gateway-6547657858-2xl6s   50m          42Mi
gateway-6547657858-kdcbg   62m          42Mi
gateway-6547657858-mr4h7   48m          41Mi
gateway-6547657858-pp4t5   58m          42Mi
gateway-6547657858-xhw4x   52m          42Mi

$ kubectl top pods -l app=events
NAME                     CPU(cores)   MEMORY(bytes)
events-97ccb8c6f-xfjml  201m         58Mi

$ kubectl top pods -l app=payments
NAME                      CPU(cores)   MEMORY(bytes)
payments-79c9ccb45-88lpw 17m          35Mi

$ kubectl top pods -l app=redis
NAME                      CPU(cores)   MEMORY(bytes)
redis-7b68444dd5-4jnr5   8m           3Mi

$ kubectl top pods -l app=postgres
NAME                         CPU(cores)   MEMORY(bytes)
postgres-6d6dc59cb9-csdnb  111m         27Mi
```

**`events` is the CPU-constrained service** (201 m, single replica, ~4× the
per-pod CPU of gateway). `payments` and `redis` are essentially idle;
`postgres` is moderate.

### 2× traffic plan (target ≈ 75 RPS)

| Service | Now | 2× plan | Resource requests / limits | Rationale |
|---------|-----|---------|----------------------------|-----------|
| gateway | 5 replicas | **5 replicas** (keep) + HPA max 10 | 100 m / 250 m | ~55 m/pod at 53 RPS → ~90 m/pod at 75 RPS; headroom to 250 m limit, HPA covers spikes |
| events | 1 replica | **3 replicas** + HPA max 6 | 250 m / 500 m | Breaking point at 201 m/replica; 75 RPS ≈ 225 m total → 2 is the floor, 3 leaves headroom for p99 |
| payments | 1 replica | **2 replicas** | 100 m / 250 m | Idle (17 m); second replica is pure availability, no load justification |
| redis | 1 replica | **1 replica** + RDB snapshots | 50 m / 100 m | 8 m CPU, 3 Mi — single pod is fine at 2×; persistence addresses the §4 risk, not load |
| postgres | 1 replica | **1 replica** (PVC) | 200 m / 500 m | 111 m at 100u; 2× ≈ 220 m, fits. Single-pooler→single-Postgres is **not** the bottleneck yet: 5 gateway pods × pool of 10 = 50 connections, far below the default `max_connections=100`; revisit with a pooler (PgBouncer) only if connection count, not CPU, saturates |

**Cost** (@ $5/pod/mo): now 9 pods = **$45/mo** → 2× plan 12 pods =
**$60/mo** (+$15/mo, +33%). The whole increase is 3 `events` replicas + 1
`payments` replica — i.e. we pay for the actual constraint, not blanket scaling.

**Validation:** re-run the committed `locustfile.py` at 150u (≈ 2× the 50u
ceiling) after applying the plan; pass = 5xx < 0.5% and p99 < 500 ms.

---

## Bonus — SRE Handbook

See [`submissions/runbooks/quickticket-handbook.md`](runbooks/quickticket-handbook.md)
(Option B: 2-page SRE handbook).
