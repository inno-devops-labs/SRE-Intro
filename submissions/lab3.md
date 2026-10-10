=== LAB 3 ===

**Lab 3 — Monitoring, Observability & SLOs**
Branch: `feature/lab3` · Tasks done: **Task 1, Task 2, Bonus**
All output below is real terminal output. Raw unedited logs: `raw.log` (section `=== LAB 3 ===`).

**Method notes (deviations, stated up front):**

1. The spec drives the dashboard through the Grafana UI. The dashboard is a provisioned file
   (`monitoring/grafana/dashboards/golden-signals.json`, mounted `:ro`), so panels were written
   directly into that JSON and verified through the **Grafana HTTP API** — not by looking at a browser.
2. §3.6 says `./loadgen/run.sh 5 60 &`, but the observation window is ~5 minutes. Traffic was kept
   continuous (`run.sh 5 60` back to back) so `rate()` windows stay populated; otherwise the rates
   decay to zero mid-outage and the recovery numbers are meaningless. `stop`/`start` timing follows the spec.
3. "Watch the dashboard" steps were replaced with `query_range` against the exact PromQL the panels use,
   step 15s, so the observations are numbers with timestamps rather than impressions of a graph.

---

# Task 1 — Configure Monitoring & Build Dashboard

## 3.1 — `monitoring/prometheus/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  - job_name: gateway
    static_configs:
      - targets: ["gateway:8080"]

  - job_name: events
    static_configs:
      - targets: ["events:8081"]

  - job_name: payments
    static_configs:
      - targets: ["payments:8082"]
```

(`rule_files` was added in Task 2; internal ports are used, not the published `3080`.)

## 3.2 — All 7 services running

```
$ docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps

NAME               IMAGE                     COMMAND                  SERVICE      STATUS
app-events-1       app-events                "uvicorn main:app --…"   events       Up 30 seconds
app-gateway-1      app-gateway               "uvicorn main:app --…"   gateway      Up 30 seconds
app-grafana-1      grafana/grafana:13.0.1    "/run.sh"                grafana      Up 35 seconds
app-payments-1     app-payments              "uvicorn main:app --…"   payments     Up 35 seconds
app-postgres-1     postgres:17-alpine        "docker-entrypoint.s…"   postgres     Up 35 seconds (healthy)
app-prometheus-1   prom/prometheus:v3.11.2   "/bin/prometheus --c…"   prometheus   Up 35 seconds
app-redis-1        redis:7-alpine            "docker-entrypoint.s…"   redis        Up 35 seconds (healthy)
```

7/7 up.

## 3.3 — Prometheus targets — all three `up`

```
$ curl -s http://localhost:9090/api/v1/targets | python3 ...

events       up       http://events:8081/metrics               lastScrape=2026-09-20T22:03:20Z err=''
gateway      up       http://gateway:8080/metrics              lastScrape=2026-09-20T22:03:12Z err=''
payments     up       http://payments:8082/metrics             lastScrape=2026-09-20T22:03:11Z err=''
```

## 3.4 — Custom metrics

**Before any traffic**, only four series existed:

```
events_db_pool_size
events_orders_created
events_orders_total
events_reservations_active
```

`gateway_*` and `payments_*` were absent — those metrics carry labels (`method,path,status`), and
`prometheus_client` does not emit a labelled child until it is first observed. After `./loadgen/run.sh 5 20`:

```
$ curl -s http://localhost:9090/api/v1/label/__name__/values | python3 ...   (filtered)

events_db_pool_size                         gateway_request_duration_seconds_bucket
events_orders_created                       gateway_request_duration_seconds_count
events_orders_total                         gateway_request_duration_seconds_created
events_request_duration_seconds_bucket      gateway_request_duration_seconds_sum
events_request_duration_seconds_count       gateway_requests_created
events_request_duration_seconds_created     gateway_requests_total
events_request_duration_seconds_sum         payments_charges_created
events_requests_created                     payments_charges_total
events_requests_total                       payments_request_duration_seconds_bucket
events_reservations_active                  payments_request_duration_seconds_count
                                            payments_request_duration_seconds_created
                                            payments_request_duration_seconds_sum
                                            payments_requests_created
                                            payments_requests_total
```

Raw from the gateway:

```
$ curl -s http://localhost:3080/metrics | grep -E "^gateway_" | head -10

gateway_requests_total{method="GET",path="/events",status="200"} 63.0
gateway_requests_total{method="POST",path="/events/{id}/reserve",status="200"} 15.0
gateway_requests_total{method="POST",path="/reserve/{id}/pay",status="200"} 3.0
gateway_requests_created{method="GET",path="/events",status="200"} 1.7899418170581977e+09
gateway_requests_created{method="POST",path="/events/{id}/reserve",status="200"} 1.789941817576218e+09
gateway_requests_created{method="POST",path="/reserve/{id}/pay",status="200"} 1.7899418201656094e+09
gateway_request_duration_seconds_bucket{le="0.005",method="GET",path="/events"} 6.0
gateway_request_duration_seconds_bucket{le="0.01",method="GET",path="/events"} 61.0
gateway_request_duration_seconds_bucket{le="0.025",method="GET",path="/events"} 62.0
gateway_request_duration_seconds_bucket{le="0.05",method="GET",path="/events"} 63.0
```

## 3.4 — Request rate (Traffic golden signal)

```
$ curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
    http://localhost:9090/api/v1/query

Request rate: 0.29 req/s
```

By path:

```
/events                      0.2226 req/s
/events/{id}/reserve         0.0532 req/s
/reserve/{id}/pay            0.0097 req/s
```

The generator ran at ~4.3 rps but only for 20 s; `rate(...[5m])` averages those 81 requests over a
300-second window, so 81/300 ≈ 0.27–0.29 req/s is the expected value, not a sign of lost traffic.

## 3.5 — Dashboard panels

Both `type: "text"` placeholder panels were replaced in place. They carry no `id` and no `datasource`
field, matching the three existing panels — Grafana resolves the datasource through
`isDefault: true` on the provisioned Prometheus. Their `gridPos` slots were kept as-is.

**PromQL used — Latency panel** (`type: timeseries`, `unit: "s"`, 3 targets):

```promql
histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))   # p50, refId A
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))   # p95, refId B
histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))   # p99, refId C
```

**PromQL used — Saturation panel** (`type: gauge`, `min: 0`, `max: 10`, thresholds green / yellow 7 / red 9):

```promql
events_db_pool_size
```

Live values at the time of writing the panels:

```
histogram_quantile(0.50, ...)  = 0.007673021331602983
histogram_quantile(0.95, ...)  = 0.02008333333333333
histogram_quantile(0.99, ...)  = 0.02401666666666667
events_db_pool_size            = 0
```

`events_db_pool_size = 0` is a real reading — the pool is idle between requests — not "no data".

### Verification that Grafana actually serves the panels

Not "the file was written" — this is Grafana's own API response after `restart grafana`:

```
$ curl -s -u admin:admin http://localhost:3000/api/dashboards/uid/quickticket-golden-signals

meta: provisioned=True  file=golden-signals.json  version=2  updated=2026-09-20T22:05:07Z
dashboard.uid=quickticket-golden-signals  title=QuickTicket — Golden Signals

type=timeseries  gridPos(h8 w12 x0 y0)    Request Rate (Traffic)
    [A] sum(rate(gateway_requests_total[1m])) by (path)
type=timeseries  gridPos(h8 w12 x12 y0)   Error Rate
    [A] sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m])) * 100
type=table       gridPos(h4 w24 x0 y8)    Service Health (up/down)
    [A] up
type=timeseries  gridPos(h8 w12 x0 y12)   Latency (p50 / p95 / p99)
    [A] histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
    [B] histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
    [C] histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[5m])) by (le))
    fieldConfig: {'unit': 's'}
type=gauge       gridPos(h8 w12 x12 y12)  Saturation (events DB connection pool)
    [A] events_db_pool_size
    fieldConfig: {'unit': 'none', 'min': 0, 'max': 10}
    thresholds: [('green', None), ('yellow', 7), ('red', 9)]

panel count: 5 | text placeholders remaining: 0
```

Incidental finding: Grafana stores a provisioned dashboard's JSON **verbatim** and does not assign
panel `id`s, so the API response has no `id` field either.

## 3.6 / 3.7 item 6 — Observations: normal traffic vs payments failure

Timeline (UTC): loadgen start `22:06:34` → `stop payments` **`22:06:49`** → `start payments` **`22:08:50`**.

`query_range`, step 15s, on the exact PromQL behind the panels:

```
time(UTC)   t-Tstop |   req/s   5xx/s    err% |   p99[1m]   p50[1m]   p99[5m] | up:gw ev pay
--------------------------------------------------------------------------------------------
22:06:45         -4 |   0.800       -       - |    0.0241    0.0079    0.0241 |    1  1  1
22:07:00        +11 |   2.089       -       - |    0.0250    0.0078    0.0246 |    1  1  0  <== payments STOPPED
22:07:15        +26 |   3.507   0.084    2.40 |    0.0245    0.0077    0.0244 |    1  1  0
22:07:30        +41 |   4.179   0.246    5.88 |    0.0245    0.0078    0.0243 |    1  1  0
22:07:45        +56 |   4.289   0.378    8.81 |    0.0239    0.0078    0.0243 |    1  1  0
22:08:00        +71 |   4.279   0.378    8.83 |    0.0240    0.0078    0.0242 |    1  1  0
22:08:15        +86 |   4.067   0.200    4.92 |    0.0241    0.0078    0.0242 |    1  1  0
22:08:30       +101 |   3.973   0.067    1.68 |    0.0238    0.0077    0.0242 |    1  1  0
22:08:45       +116 |   4.067   0.089    2.19 |    0.0239    0.0077    0.0241 |    1  1  0
22:09:00       +131 |   4.156   0.111    2.67 |    0.0241    0.0078    0.0242 |    1  1  1  <== payments STARTED
22:09:30       +161 |   4.200   0.022    0.53 |    0.0244    0.0082    0.0242 |    1  1  1
22:09:45       +176 |   4.155   0.000    0.00 |    0.0243    0.0080    0.0242 |    1  1  1
22:10:00       +191 |   4.155   0.000    0.00 |    0.0241    0.0079    0.0242 |    1  1  1
```

**Normal traffic:** ~4.2 req/s, 0% 5xx, p50 ≈ 7.8 ms, p99 ≈ 24 ms, all three targets `up`,
DB pool gauge at 0–1.

**During the payments failure:** Traffic was flat — demand does not change when a dependency dies.
Errors appeared only on `/reserve/{id}/pay`, as **502 Bad Gateway**, peaking at **8.83% of all requests**.
The ceiling is structural: the generator sends 70% reads, 20% reserves, 10% purchases, so only the
last tenth ever touches payments. **Latency did not move at all** — p99 stayed at ~0.024 s for the
entire outage.

Status-code breakdown, cumulative counters (step 15s):

```
time       t-Tstop |   /events:200  /reserve:200  /reserve:409  /pay:200  /pay:502
22:06:45        -4 |            87            24             0         6          0
22:07:00       +11 |           129            39             0         7          6   <== STOP
22:07:30       +41 |           213            74             0         7         17
22:08:00       +71 |           300            95            10         7         26
22:09:00      +131 |           478           131            35        10         31   <== START
22:10:00      +191 |           646           167            69        20         31
```

Scrape-level edges (1-second resolution — the gateway is scraped at :13/:28/:43/:58, payments at :12/:27/:42/:57):

```
### payments killed at 22:06:49 ###
up{job="payments"}                          22:06:57  (+8s)   1 -> 0
sum(gateway_requests_total{status=~"5.."})  22:06:58  (+9s)   series first visible = 6

### payments restarted at 22:08:50 ###
up{job="payments"}                          22:08:57  (+7s)   0 -> 1
sum(gateway_requests_total{status=~"5.."})  22:08:58  (+8s)   30 -> 31   (last increment, then flat)
sum(gateway_requests_total{status="200",path="/reserve/{id}/pay"})
                                            22:08:58  (+8s)   7 -> 10    (pay traffic succeeding again)
```

**On retries and the circuit breaker:** `gateway_retry_total` and
`gateway_circuit_breaker_transitions_total` have **no series at all** for this incident. That is not a
scrape problem — `gateway/main.py` ships `call_with_retry`, `CircuitBreaker.call` and
`RateLimiter.allow` as no-op stubs marked `# TODO (Lab 11)`. They pass the call straight through, so
neither counter is ever incremented. This also explains the flat latency: with no retry loop and no
listener on `payments:8082`, the connection is refused immediately and the gateway converts that into
a 502 in about the same time it would have served a success.

## 3.7 item 7 — Which golden signal showed the failure first?

I don't think "first" is a meaningful answer at this resolution, and I'd rather say that than force
a clean story the data doesn't support. `up{job="payments"}` flipped at +8s and the error-rate series
first appeared at +9s — one second apart, and both numbers are ceilings imposed by the same 15-second
scrape interval, not measurements of when the failure actually happened. The 502s were already being
returned by the application well before either metric caught up: the scrape at 22:06:43 (six seconds
*before* `stop payments` even ran) shows zero 5xx, and the very next scrape, 15 seconds later, already
shows 6 of them. So the real detection latency here is bounded by *how often Prometheus asks*, not by
which signal is "faster" — with a 15s interval, both signals are stuck reporting on the same clock, and
the honest answer is "both, within one scrape of each other," not "X beat Y."

What's more interesting is that **Latency never reacted at all** — p99 sat at ~0.024s for the entire
two-minute outage. That's not a flaw in the panel, it's a consequence of how this specific failure
looks at the network layer: `stop payments` removes the container entirely, so the gateway's connection
attempt gets refused immediately (`connection refused` is fast) instead of hanging until a timeout. A
slow dependency shows up in latency; a dead one just shows up as a fast error. On top of that, the
retry and circuit-breaker logic in the gateway are still no-op stubs for a later lab, so there's no
retry loop that could have added visible delay before failing. If those existed, I'd expect latency to
spike briefly as retries burned time before the circuit opened — but as shipped, a hard-down dependency
here is a Traffic-signal non-event and an Error-signal event, with Latency staying completely blind to
it. That's the actual lesson: which golden signal "catches" a failure first depends on the failure mode,
not on some fixed ranking of the four signals.

---

# Task 2 — Define SLOs & Recording Rules

## 3.8 — SLI / SLO definitions

| | SLI | SLO target | Window |
|---|---|---|---|
| **SLI 1 — Availability** | share of gateway requests **not** returning 5xx | **99.5%** | 7 days |
| **SLI 2 — Latency** | share of gateway requests completing **under 500 ms** | **95%** | 7 days |

### Error budget math

Traffic assumption from the spec: **~1000 requests/day**.

```
Requests per week          = 1000 req/day × 7 days           = 7000 requests
```

**Availability budget (SLO 99.5%):**

```
Error budget (fraction)    = 1 − SLO = 1 − 0.995             = 0.005  (0.5%)
Allowed failed requests/wk = 7000 × 0.005                    = 35 requests
Allowed failed requests/d  = 1000 × 0.005                    = 5 requests
```

Expressed as time, if the service were fully down rather than partially failing:

```
Minutes in 7 days          = 7 × 24 × 60                     = 10 080 min
Allowed downtime/week      = 10 080 × 0.005                  = 50.4 min
```

**Latency budget (SLO 95% under 500 ms):**

```
Latency budget (fraction)  = 1 − 0.95                        = 0.05  (5%)
Allowed slow requests/wk   = 7000 × 0.05                     = 350 requests
Allowed slow requests/day  = 1000 × 0.05                     = 50 requests
```

**Burn rate** normalises the current failure ratio against the budget:

```
burn_rate = (1 − availability) / (1 − 0.995) = observed_error_ratio / 0.005
```

`burn_rate = 1.0` means the budget is being consumed exactly as fast as the 7-day window replenishes it —
sustained, it is exhausted precisely at the end of the window. `burn_rate = 2.0` exhausts it in 3.5 days.
Peak measured in the Bonus incident was **3.07**, i.e. the weekly budget would be gone in
`7 / 3.07 ≈ 2.3 days` if sustained.

## 3.9 — Recording rules

`monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      - record: gateway:sli_availability:ratio_rate5m
        expr: |
          sum(rate(gateway_requests_total{status!~"5.."}[5m]))
          /
          sum(rate(gateway_requests_total[5m]))

      - record: gateway:sli_latency_500ms:ratio_rate5m
        expr: |
          sum(rate(gateway_request_duration_seconds_bucket{le="0.5"}[5m]))
          /
          sum(rate(gateway_request_duration_seconds_count[5m]))

      - record: gateway:error_budget_burn_rate:ratio_rate5m
        expr: |
          (1 - gateway:sli_availability:ratio_rate5m) / (1 - 0.995)
```

`le="0.5"` exists because the gateway histogram is built without an explicit `buckets=`, so it uses
`prometheus_client`'s defaults, which include a 0.5 s boundary.

Mount added to `docker-compose.monitoring.yaml`:

```yaml
    volumes:
      - ../monitoring/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

**`restart` is not sufficient for this step.** A new bind mount only takes effect when the container is
recreated, so `up -d prometheus` was used instead (it recreates because the config changed). Confirmed:

```
$ docker inspect app-prometheus-1 --format '{{range .Mounts}}...'
/Users/revik/dev/uni/SRE-Intro/monitoring/prometheus/prometheus.yml -> /etc/prometheus/prometheus.yml (rw=false)
/Users/revik/dev/uni/SRE-Intro/monitoring/prometheus/rules.yml      -> /etc/prometheus/rules.yml      (rw=false)
```

### Rules loaded

Immediately after the restart all three report `health=unknown` — loaded but not yet evaluated, since
the group interval is 30 s. After the first evaluation:

```
$ curl -s http://localhost:9090/api/v1/rules | python3 ...

group: slo_rules  file=/etc/prometheus/rules.yml  interval=30s  lastEval=2026-09-20T22:14:35Z  evalTime=0.0024s
  gateway:sli_availability:ratio_rate5m         = health=ok   lastError=''
  gateway:sli_latency_500ms:ratio_rate5m        = health=ok   lastError=''
  gateway:error_budget_burn_rate:ratio_rate5m   = health=ok   lastError=''
```

All three `health=ok`. The recorded series return values:

```
gateway:sli_availability:ratio_rate5m          = 1
gateway:sli_latency_500ms:ratio_rate5m         = 1
gateway:error_budget_burn_rate:ratio_rate5m    = 0
```

## 3.10 — SLO gauge panel

Sixth panel added at `gridPos y=20`, verified through the Grafana API (`panel count: 6`):

```
type=gauge       gridPos(h8 w12 x0 y20)  SLO — Availability (7d target 99.5%)
    [A] gateway:sli_availability:ratio_rate5m * 100
    fieldConfig: {'unit': 'percent', 'min': 99, 'max': 100, 'decimals': 3}
    thresholds: [('red', None), ('green', 99.5)]
```

Thresholds are ordered `red` as the base and `green` from 99.5 upward, so the gauge is red exactly when
the SLI sits below the SLO target.

### Observation during a 1-minute payments outage

`stop payments` **`22:16:51`** → `start payments` **`22:17:51`**.

```
time(UTC)   t-Tstop |  availability   avail % |  burn_rate | latency SLI |  5xx/s | up:pay
22:16:00        -51 |      1.000000  100.0000 |     0.0000 |    1.000000 | 0.0000 |   1
22:16:30        -21 |      1.000000  100.0000 |     0.0000 |    1.000000 | 0.0000 |   1
22:17:00         +9 |      1.000000  100.0000 |     0.0000 |    1.000000 | 0.0035 |   0  <== STOPPED
22:17:30        +39 |      0.997462   99.7462 |     0.5076 |    1.000000 | 0.0035 |   0
22:18:00        +69 |      0.998069   99.8069 |     0.3861 |    1.000000 | 0.0070 |   1  <== STARTED
22:18:30        +99 |      0.996885   99.6885 |     0.6231 |    1.000000 | 0.0070 |   1
22:19:00       +129 |      0.997392   99.7392 |     0.5215 |    1.000000 | 0.0069 |   1
22:19:30       +159 |      0.997758   99.7758 |     0.4484 |    1.000000 | 0.0070 |   1
22:21:30       +279 |      0.998321   99.8321 |     0.3359 |    1.000000 | 0.0070 |   1
```

**The gauge dropped from 100% to a minimum of 99.6885% (at `22:18:30`, +99 s) but stayed green —
it never crossed the 99.5% threshold**, and burn rate peaked at only 0.6231, below 1.0.
Availability climbed back above 99.5%… it never left it. Reported as measured.

Two reasons, both verified rather than assumed:

1. **Only 2 failing requests occurred during the whole 60-second outage** (5xx counter `31 → 33`).
2. The recorded rule uses `rate(...[5m])`, so a 60-second outage is averaged across a 300-second window
   and its depth is divided by roughly five.

Measured with a sharp `[1m]` window instead of the recorded 5m rule, the same incident reads:

```
22:16:45  ( -6s)  100.0000%
22:17:00  ( +9s)   99.4536%   *** below 99.5 ***
22:17:30  (+39s)   99.4624%   *** below 99.5 ***
22:18:15  (+84s)   99.4681%   *** below 99.5 ***
22:18:30  (+99s)  100.0000%
MIN over window: 99.4536%
```

So the SLO *was* breached in real terms; the 5-minute averaging in the recording rule hid it.
That is a genuine property of the rule as specified in the lab, not a mistake in the query.

### Why only 2 failures — and an application bug found along the way

Comparing the pay path across the two incidents:

| incident | reserve 200 | reserve 409 | pay attempts |
|---|---:|---:|---:|
| §3.6 (22:06) | 71 | 8 | **21** (1×200 + 20×502) |
| §3.10 (22:16) | 20 | **72** | **3** (1×200 + 2×502) |

By the second incident most reserve calls were returning `409 Not enough tickets`, so traffic rarely
reached payments at all. Cause, from Redis:

```
$ redis-cli TTL reservation:bff43b63-...   ->  141      (reservation expires)
$ redis-cli TTL event:3:held               ->  -1       (hold counter never expires)

$ redis-cli MGET event:1:held ... event:5:held
71  19  162  20  62          (sum = 334 tickets held)
$ number of live reservation:* keys         ->  23
```

`events/main.py:218` writes `reservation:<id>` with `SETEX ... RESERVATION_TTL` (300 s), and `:224` does
`decrby("event:<id>:held", -quantity)` — **with no TTL and no release when the reservation expires**.
`_get_available()` (`events/main.py:288`) computes `total − confirmed_orders − held`, so the leaked hold
counters make availability decay monotonically and the 409 rate climb over the life of the stack.
334 tickets were held against 23 live reservations.

This is a bug in the lab application, not in the monitoring config, and it is outside this lab's scope —
left unfixed. The five leaked `event:*:held` keys were deleted before the Bonus run (and only those),
because otherwise the pay path gets no traffic and `PAYMENT_FAILURE_RATE` has nothing to act on.
After the delete, reserve returned 200 again for events 1, 2 and 4.

---

# Bonus Task — Correlate Failure Across Metrics & Logs

Injection: `stop payments`, then recreate with `PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000`.
Confirmed on the running container:

```
$ docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep PAYMENT_
PAYMENT_LATENCY_MS=1000
PAYMENT_FAILURE_RATE=0.5
```

60 s of clean traffic was recorded before the injection so that "first deviation from baseline" has a
baseline to deviate from.

## Timeline

| # | UTC timestamp | Δ from injection | Event | Source |
|---|---|---:|---|---|
| 1 | `22:26:02.000` | `0.000 s` | **Injection**: `stop payments` issued | driver script |
| 2 | `22:26:03.179` | `+1.18 s` | gateway returns **502** — one request caught the container down mid-recreate | gateway log |
| 3 | `22:26:03.492` | `+1.49 s` | payments back up: `Uvicorn running on http://0.0.0.0:8082` | payments log |
| 4 | `22:26:07.929` | `+5.93 s` | first `Injecting 1000ms latency` — latency injection live | payments log |
| 5 | `22:26:15` | `+13 s` | **first metric deviation**: p99 on `/reserve/{id}/pay` `0.0248 → 2.2000 s`; err% `0 → 0.526%` | `query_range` |
| 6 | `22:26:24.747948` | `+22.75 s` | **first error in payments**: `WARNING ... Payment failed (injected) for 25a26f7d-…` | payments log |
| 7 | `22:26:24.748879` | `+22.75 s` | payments answers `POST /charge HTTP/1.1 500` (**+0.9 ms** after #6) | payments log |
| 8 | `22:26:24.750204` | `+22.75 s` | **first reaction in gateway**: `HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500"` (**+1.3 ms** after #7) | gateway log |
| 9 | `22:26:24.751996` | `+22.75 s` | gateway returns **500** to the client (**+4.0 ms** after #6) | gateway log |
| 10 | `22:26:30` | `+28 s` | global p99 crosses 0.1 s → `1.9750 s` | `query_range` |
| 11 | `22:27:30` | `+88 s` | p95 crosses 0.5 s → `1.0469 s`; latency SLI starts falling | `query_range` |
| 12 | `22:27:45` | `+103 s` | err% peaks at **4.698%**; availability `98.9999%` → **SLO gauge goes red**, burn rate `2.000` | recording rules |
| 13 | `22:28:45` | `+163 s` | worst point: availability **98.4635%**, burn rate **3.0731** | recording rules |
| 14 | `22:30:48` | `+286 s` | **Recovery**: payments recreated with clean env (`FAILURE_RATE=0.0`, `LATENCY_MS=0`) | driver script |
| 15 | `22:31:15` | +27 s after recovery | global p99 back under 0.1 s (`0.0238 s`) | `query_range` |
| 16 | `22:31:45` | +57 s after recovery | err% back to **0**; p99 on `/pay` back to `0.0249 s` | `query_range` |
| 17 | `22:33:15` | +147 s after recovery | availability back above SLO (`99.5140%`); burn rate back under 1.0 (`0.9721`) | recording rules |

## Log excerpts at the failure moment

**payments** (`22:26:23` – `22:26:26`):

```
2026-09-20T22:26:23.744364215Z {"time":"2026-09-20 22:26:23,744","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for 25a26f7d-0cd8-410f-9a8c-a96f11560490"}
2026-09-20T22:26:24.747948216Z {"time":"2026-09-20 22:26:24,747","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 25a26f7d-0cd8-410f-9a8c-a96f11560490"}
2026-09-20T22:26:24.748879507Z INFO:     172.19.0.8:60596 - "POST /charge HTTP/1.1" 500 Internal Server Error
2026-09-20T22:26:25.563446466Z {"time":"2026-09-20 22:26:25,563","level":"INFO","service":"payments","msg":"Injecting 1000ms latency for b2cc6b80-8d42-4852-afe0-fad4cc5a7c4c"}
2026-09-20T22:26:26.567615508Z {"time":"2026-09-20 22:26:26,566","level":"INFO","service":"payments","msg":"Payment success: PAY-D5249BD6 for b2cc6b80-8d42-4852-afe0-fad4cc5a7c4c"}
2026-09-20T22:26:26.569032550Z INFO:     172.19.0.8:60596 - "POST /charge HTTP/1.1" 200 OK
```

**gateway**, same window (only lines touching payments / `/pay`):

```
2026-09-20T22:26:24.750204799Z {"time":"2026-09-20 22:26:24,749","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 500 Internal Server Error""}
2026-09-20T22:26:24.751996591Z INFO:     192.168.65.1:30604 - "POST /reserve/25a26f7d-0cd8-410f-9a8c-a96f11560490/pay HTTP/1.1" 500 Internal Server Error
2026-09-20T22:26:26.570811425Z {"time":"2026-09-20 22:26:26,570","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge "HTTP/1.1 200 OK""}
2026-09-20T22:26:26.579693550Z INFO:     192.168.65.1:50483 - "POST /reserve/b2cc6b80-8d42-4852-afe0-fad4cc5a7c4c/pay HTTP/1.1" 200 OK
```

The reservation id `25a26f7d-0cd8-410f-9a8c-a96f11560490` appears in all three log lines (#6, #8, #9),
so a single user request is traceable end to end across both services without any tracing system.

## Metric deviation vs baseline

```
time(UTC)   t-Tinj |  req/s    err% |     p50     p95     p99  p99 /pay |   avail%   burn
22:25:30       -32 |   4.31    0.00 |  0.0080  0.0210  0.0242    0.0248 | 100.0000  0.000
22:25:45       -17 |   4.31    0.00 |  0.0079  0.0205  0.0241    0.0248 | 100.0000  0.000
22:26:00        -2 |   4.44    0.00 |  0.0079  0.0208  0.0242    0.0248 | 100.0000  0.000
22:26:15       +13 |   4.22    0.53 |  0.0077  0.0197  0.0244    2.2000 | 100.0000  0.000  <== INJECTION
22:26:30       +28 |   3.87    0.57 |  0.0077  0.0224  1.9750    2.4490 | 100.0000  0.000
22:26:45       +43 |   3.62    0.61 |  0.0076  0.0232  2.0900    2.4775 |  99.7947  0.411
22:27:15       +73 |   3.58    2.43 |  0.0075  0.0206  2.0975    2.4850 |  99.5912  0.818
22:27:30       +88 |   3.44    3.03 |  0.0076  1.0469  2.2094    2.4850 |  99.5912  0.818
22:27:45      +103 |   3.31    4.70 |  0.0077  1.3825  2.2765    2.4850 |  98.9999  2.000
22:28:15      +133 |   3.44    3.23 |  0.0078  1.0469  2.2094    2.4850 |  98.6150  2.770
22:28:45      +163 |   2.71    2.46 |  0.0077  0.0233  2.1340    2.4850 |  98.4635  3.073
```

Baseline vs peak:

```
err%     baseline avg=0.0000  peak=4.6980 @22:27:45Z (+103s)   first deviation @22:26:15Z (+13s)
p50      baseline avg=0.0073  peak=0.0082 @22:29:15Z (+193s)   never exceeded 2x baseline
p95      baseline avg=0.0193  peak=1.4500 @22:29:15Z (+193s)   first >2x baseline @22:27:30Z (+88s)
p99      baseline avg=0.0222  peak=2.2900 @22:29:15Z (+193s)   first >2x baseline @22:26:30Z (+28s)
p99 /pay baseline avg=0.0248  peak=2.4850 @22:27:15Z  (+73s)   first >2x baseline @22:26:15Z (+13s)
burn     baseline avg=0.0000  peak=3.0731 @22:28:45Z (+163s)   first >0 @22:26:45Z (+43s)
```

Latency SLI (`gateway:sli_latency_500ms:ratio_rate5m`) across the whole incident:

```
22:26:00Z  100.0000%  <== INJECTION        22:30:30Z   96.6624%
22:27:00Z   98.9754%                       22:31:00Z   96.1741%  <== RECOVERY
22:28:00Z   97.9971%                       22:31:30Z   96.1487%   (minimum)
22:29:00Z   97.2345%                       22:33:00Z   98.5185%
22:30:00Z   97.0012%                       22:34:30Z   99.5829%
```

Minimum **96.1487%** — degraded, but it never breached the 95% latency SLO, while the
99.5% availability SLO was breached for roughly 5 minutes.

Injection ratios confirmed from the logs: **26** `Injecting 1000ms latency` lines, **13**
`Payment failed (injected)` lines and **13** `500` responses — exactly the configured 0.5 failure rate.
Gateway `/pay` status distribution over the window: `30 × 200`, `13 × 500`, `1 × 502`.

## Root cause explanation

**What was injected and how it propagated.** `PAYMENT_FAILURE_RATE=0.5` and `PAYMENT_LATENCY_MS=1000`
were set on payments at `22:26:02`. Every charge attempt now sleeps 1000 ms first
(`Injecting 1000ms latency`), then either returns success or logs `Payment failed (injected)` and
answers `500` — that split happened on a coin flip and landed almost exactly on 50% (13 failures out
of 26 attempts). The gateway sits directly in that path as a synchronous proxy: it logs the upstream
`500` **1.3 ms** after payments wrote its own log line, and passes that failure on to the client as
its own `500` **4.0 ms** after the original error. There's no retry and no circuit breaker in the way —
both are still no-op stubs left for a later lab — so every upstream failure becomes exactly one
user-visible failure, and every upstream delay becomes exactly one user-visible delay. Nothing here
is being absorbed or hidden by the gateway; it is a straight pass-through of whatever payments does.

**Why the response code differs from §3.6.** In §3.6, payments was stopped outright, so the gateway's
connection attempt was refused immediately and it returned a fast `502`. Here payments is alive, just
slow and occasionally failing, so the gateway gets a real HTTP response with a real body — hence `500`,
not `502`. The status code alone tells you which of the two failure modes you're looking at: `502` means
"nothing is listening," `500` means "something answered, and it says it failed."

**Why latency moved this time when it didn't in §3.6.** The injected 1000 ms is spent *inside* a request
the gateway is actively waiting on, not lost to an instant connection refusal, so it shows up directly
in the latency histogram: p99 on `/pay` jumped from 0.0248s to 2.2000s in the very first post-injection
sample — about 89x. The absolute number, 2.2–2.49s, is higher than the 1000ms that was injected, and
that's explained by the pay flow being two sequential upstream calls (charge, then confirm) plus the
fact that `histogram_quantile` interpolates between bucket boundaries — the relevant default boundaries
here are 1.0/2.5/5.0s, so a ~1s delay on one hop lands the quantile estimate up near the 2.5s bucket
edge rather than reading back exactly 1.0.

**Why the aggregate blast radius still looks small.** Only 10% of generated traffic ever reaches the
pay path, so even with a 50% failure rate on that slice, global error rate peaked at 4.698%, not
anywhere near 50%. It shows up cleanly at p99 (crossed 0.1s at +28s) well before it shows up at p95
(crossed 0.5s only at +88s), because the affected requests are a small enough share of the total that
they only dominate the tail of the distribution, not the middle.

**What this says about which signal to trust for detection.** The first *unambiguous* error in the logs
appears at +22.75s, but the metrics had already deviated at +13s — on latency, not on error rate. That
gap exists because the latency injection fires on every single charge attempt, while the failure
injection is a coin flip that first has to land on "fail" before it produces a log line or an error
metric. In other words: a consistent side effect (added latency) is detectable sooner than an
intermittent one (a 50% failure rate), simply because it doesn't need to wait for an unlucky draw. If I
were setting up an alert for this class of failure, I'd rather trigger on the latency SLI than
error-rate alone — it reacts to a degrading dependency even before that dependency starts outright
failing requests.

**Budget impact.** Availability bottomed at 98.4635% against the 99.5% SLO, and burn rate peaked at
3.0731 — sustained at that rate, the whole 7-day error budget (35 requests, from §3.8) would be gone in
about 2.3 days instead of a week. Recovery also wasn't instant even once payments was clean again: the
raw error rate hit 0% at +57s after recovery, but the SLO gauge only turned green again at +147s,
because the recording rule's `rate[5m]` window has to slide the bad samples fully out before the ratio
recovers — the dashboard lags reality by roughly the width of its own averaging window, in both
directions.

---

# Files changed in this lab

| File | Change |
|---|---|
| `monitoring/prometheus/prometheus.yml` | **created** — 3 scrape jobs + `rule_files` |
| `monitoring/prometheus/rules.yml` | **created** — 3 SLO recording rules |
| `monitoring/grafana/dashboards/golden-signals.json` | 2 placeholders replaced (Latency, Saturation) + SLO gauge added |
| `docker-compose.monitoring.yaml` | `rules.yml` mounted `:ro` into the prometheus container |
