# Lab 3. Monitoring, observability and SLOs

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), Docker Engine 29.7.2, Docker Compose v5.5.1, Prometheus v3.11.2, Grafana 13.0.1, QuickTicket stack from `app/docker-compose.yaml` plus `docker-compose.monitoring.yaml`

Every number below comes from a live run. Task 1 and Task 2 were captured in one pass on a freshly started stack, the failure injection in a second pass, and the cross service correlation in a third. Timings quoted to a tenth of a second come from a one second polling loop against the Prometheus HTTP API, which reads the same series the dashboard panels render.

---

## Task 1. Configure monitoring and build the dashboard

### 3.1 The Prometheus configuration

`monitoring/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - "rules.yml"

scrape_configs:
  - job_name: "gateway"
    static_configs:
      - targets: ["gateway:8080"]

  - job_name: "events"
    static_configs:
      - targets: ["events:8081"]

  - job_name: "payments"
    static_configs:
      - targets: ["payments:8082"]
```

Targets are Compose service names on the container network, on internal ports. Prometheus sits on `app_default` itself, so the published host ports play no part: `gateway:8080` is what the scraper dials, while `localhost:3080` is only how I reach the same service from the host.

### 3.2 Seven services running

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

```plaintext
NAME               IMAGE                     SERVICE      STATUS                    PORTS
app-events-1       sha256:238a2aeb651a...    events       Up 18 seconds             0.0.0.0:8081->8081/tcp
app-gateway-1      app-gateway               gateway      Up 18 seconds             0.0.0.0:3080->8080/tcp
app-grafana-1      grafana/grafana:13.0.1    grafana      Up 25 seconds             0.0.0.0:3000->3000/tcp
app-payments-1     sha256:dd705dbc6d02...    payments     Up 24 seconds             0.0.0.0:8082->8082/tcp
app-postgres-1     postgres:17-alpine        postgres     Up 24 seconds (healthy)   0.0.0.0:5432->5432/tcp
app-prometheus-1   prom/prometheus:v3.11.2   prometheus   Up 24 seconds             0.0.0.0:9090->9090/tcp
app-redis-1        redis:7-alpine            redis        Up 25 seconds (healthy)   0.0.0.0:6379->6379/tcp
```

### 3.3 Prometheus is scraping all three services

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
```

```plaintext
events       up       http://events:8081/metrics
gateway      up       http://gateway:8080/metrics
payments     up       http://payments:8082/metrics
```

All three reached `up` fifteen seconds after the stack came alive, which is one scrape interval. That number turns out to set the floor for everything measured in section 3.6.

### 3.4 What the services expose

```bash
curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
import sys, json
for n in json.load(sys.stdin)['data']:
    if any(x in n for x in ['gateway_', 'events_', 'payments_']):
        print(n)
"
```

```plaintext
events_db_pool_size                        gateway_request_duration_seconds_bucket
events_orders_created                      gateway_request_duration_seconds_count
events_orders_total                        gateway_request_duration_seconds_created
events_request_duration_seconds_bucket     gateway_request_duration_seconds_sum
events_request_duration_seconds_count      gateway_requests_created
events_request_duration_seconds_created    gateway_requests_total
events_request_duration_seconds_sum        payments_request_duration_seconds_bucket
events_requests_created                    payments_request_duration_seconds_count
events_requests_total                      payments_request_duration_seconds_created
events_reservations_active                 payments_request_duration_seconds_sum
                                           payments_requests_created
                                           payments_requests_total
```

Twenty two series names for three services. Only `events` exposes anything beyond the request counter and the duration histogram, and `payments_charges_total` is missing from this list because no charge had been attempted yet: a labelled counter in `prometheus_client` does not exist until its first `.labels(...).inc()`.

Traffic, then the traffic signal:

```bash
./loadgen/run.sh 5 40
sleep 25
```

```plaintext
Done. total=152 success=152 fail=0 error_rate=0%
```

```bash
curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' http://localhost:9090/api/v1/query
```

```plaintext
sum(rate(gateway_requests_total[5m]))            = 0.4928 req/s

sum(rate(gateway_requests_total[1m])) by (path)
  {path="/events"}               = 1.5557
  {path="/events/{id}/reserve"}  = 0.7556
  {path="/reserve/{id}/pay"}     = 0.1556
  {path="/health"}               = 0
```

The per path split reproduces the generator's 70 / 20 / 10 mix. The 5m figure is four times smaller than the 1m figure for the same traffic, because a forty second burst spread over a five minute window averages down. Both are correct and they answer different questions.

### 3.5 The two panels I added

Both were written into `monitoring/grafana/dashboards/golden-signals.json`, which Grafana provisions from a read only mount. That makes the file in the repository the thing under review, so the panels arrive in the PR as a diff rather than as a screenshot. Grafana confirms it is serving them:

```bash
curl -s -u admin:admin http://localhost:3000/api/dashboards/uid/quickticket-golden-signals
```

```plaintext
title      : QuickTicket — Golden Signals
provisioned: True  file: golden-signals.json
panels     : 7
  timeseries  Request Rate (Traffic)
  timeseries  Error Rate
  table       Service Health (up/down)
  timeseries  Latency (p50 / p95 / p99)
      p50: histogram_quantile(0.50, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
      p95: histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
      p99: histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))
  gauge       Saturation: events DB connection pool
      connections in use: events_db_pool_size
  gauge       SLO: availability (5m) against a 99.5% target
      availability %: gateway:sli_availability:ratio_rate5m * 100
  timeseries  Error budget burn rate
      burn rate: gateway:error_budget_burn_rate:ratio_rate5m
```

Latency panel: time series, unit seconds, three queries as above. Saturation panel: gauge on `events_db_pool_size`, min 0, max 10 to match `DB_MAX_CONNS`, yellow at 7 and red at 9. The last two panels belong to Task 2.

On idle traffic the latency panel reads:

```plaintext
p50 = 0.0160 s     p95 = 0.0346 s     p99 = 0.0469 s
```

### 3.6 What the saturation panel actually measures

The gauge reads zero. It read zero in every one of the 182 samples taken during the outage in the next section, and `max_over_time` over ten minutes agrees:

```bash
curl -s --data-urlencode 'query=events_db_pool_size' ...
curl -s --data-urlencode 'query=max_over_time(events_db_pool_size[10m])' ...
```

```plaintext
events_db_pool_size{instance="events:8081"}                  = 0
max_over_time(events_db_pool_size[10m]){instance="events:8081"} = 0
```

A flat zero is usually a broken exporter, so before writing that down I checked the other two gauges the same service exposes:

```plaintext
events_reservations_active{instance="events:8081"} = 37
events_orders_total{instance="events:8081"}        = 13
```

Those move. The collector works, the scrape works, and the difference is where the number is produced. In `events/main.py` the pool gauge is set on line 134, inside the `/metrics` handler:

```python
@app.get("/metrics")
def metrics():
    if db_pool:
        DB_POOL_SIZE.set(len(db_pool._used))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

> The value is computed during the scrape, about a request that is not a database request, so it reports how many connections were checked out while Prometheus was asking. Between two scrapes the pool could have gone to its ceiling of ten and come back, and the gauge would still publish zero every fifteen seconds with complete confidence. Saturation is the one golden signal that cannot be sampled this way, because what matters about a pool is its peak rather than its value at an arbitrary instant. Fixing it means either setting the gauge where connections are taken and returned, or exporting a high water mark that the handler reads and resets.

### 3.7 Killing payments under load

```bash
./loadgen/run.sh 5 240 &
sleep 20
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

```plaintext
T_KILL = 2026-09-13T16:17:56.231
 Container app-payments-1 Stopping
 Container app-payments-1 Stopped
```

Before trusting the outage I checked that the service had actually stopped serving and had not merely lost its network name, because a stopped container can keep answering on its published port through an orphaned `docker-proxy`:

```plaintext
direct request to the published port 8082: HTTP 000
name resolution for 'payments' from inside the gateway container:
    socket.gaierror: [Errno -2] Name or service not known
```

Both checks agree, so no escalation to `kill` was needed. The polling loop then sampled six signals once a second. Abridged, with the rows where something changed:

```plaintext
    t+s  up_pay  err_pct      p95      p99  pay_p95  pool     avail     burn
   15.6       1     none    0.024    0.027    0.024     0   1.00000     0.00
   16.6       1     none    0.025    4.092    4.625     0   1.00000     0.00   <- latency
   17.6       0     none    0.025    4.092    4.625     0   1.00000     0.00   <- up
   31.1       0     none    0.025    4.092    4.625     0   1.00000     0.00
   32.1       0     1.06    0.037    4.300    4.719     0   1.00000     0.00   <- errors
   47.6       0     3.91    3.396    4.679    4.875     0   1.00000     0.00   <- p95 finally
   70.3       0     6.74    3.146    4.629    4.875     0   0.99288     1.42   <- SLO
  130.1       0     9.72    3.714    4.743    4.875     0   0.96923     6.15
  148.6       1     9.33    3.661    4.732    4.875     0   0.96923     6.15   <- restarted
  181.8       1     1.08    0.033    0.048    3.375     0   0.96079     7.84
```

The window as a whole:

```bash
curl -s --data-urlencode 'query=sum(increase(gateway_requests_total[5m])) by (status, path)' ...
```

```plaintext
{path="/events",               status="200"} = 442.0
{path="/events/{id}/reserve",  status="200"} = 153.8
{path="/events/{id}/reserve",  status="409"} =  27.0
{path="/reserve/{id}/pay",     status="200"} =  26.0
{path="/reserve/{id}/pay",     status="502"} =  21.4
{path="/health",               status="200"} =   1.1

loadgen: total=618 success=548 fail=70 error_rate=11.3%
```

**Which golden signal showed the failure first, and how long after killing payments?**

Latency, at **t+16.6 s**, as a jump in p99 from 0.027 s to 4.092 s and in the pay path p95 from 0.024 s to 4.625 s. Service health followed one second later at t+17.6 s when `up{job="payments"}` went to zero. The error rate panel stayed empty until **t+32.1 s**, about sixteen seconds behind the other two. Saturation never moved at all.

Two things set those numbers, and only one of them is about the signals.

The floor is the scrape interval. Nothing in this stack can be noticed faster than fifteen seconds, because that is how often Prometheus asks. The first failing request happened within two seconds of the stop command, since the generator sends a purchase roughly every two seconds at five requests per second, and it sat invisible in the gateway's counters until the next scrape collected them. Latency and `up` both landed in the same scrape cycle and differ only by the phase offset between two jobs.

The extra sixteen seconds on the error rate is a property of the panel's query rather than of the data. Before the outage no gateway request had ever returned 5xx, so the series `gateway_requests_total{status="502"}` did not exist. It was created at the scrape that also carried the latency jump, and a counter with exactly one sample has no rate, so `rate(...[1m])` returned nothing for one further interval and the whole ratio evaluated to an empty vector. The latency buckets were already there and moved immediately. I did not get this from Prometheus directly, but the same expression behaved in two distinguishable ways across the run, which is what the reading rests on: on the idle system in section 3.4 it returned an empty vector, and in the later degradation run, once the 5xx series existed, it returned a clean `0.00`.

> The Error Rate panel goes blind for one scrape interval after every new kind of error, and it shows that blindness as "No data" instead of as zero. The cause sits in the query: it takes `rate()` of a label value that comes into existence at the moment of the first failure, and a counter with one sample has no rate. So an operator watching this dashboard gets sixteen seconds of climbing latency and a red health row next to an empty errors panel, which reads as a slow dependency rather than a dead one. Wrapping the numerator in `or vector(0)` fixes the display and shortens the gap:
>
> ```plaintext
> sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(...) * 100                 = (empty vector)
> sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(...) * 100 or vector(0)    = 0
> ```

There is a second reason to read the latency panel carefully. The aggregate p95 did not cross half a second until **t+47.6 s**, thirty one seconds after p99 did, because the pay path carries only a tenth of the traffic and the ninety fifth percentile of the whole mix stayed inside the healthy majority. The per path query caught it at the same instant as p99:

```plaintext
histogram_quantile(0.95, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le, path))
  {path="/events"}               = 0.0239
  {path="/events/{id}/reserve"}  = 0.0429
  {path="/reserve/{id}/pay"}     = 4.875
```

> A single completely broken endpoint is invisible to p95 as long as it serves less than five percent of requests, and QuickTicket's purchase path is exactly that: ten percent of traffic and the only path that takes money. The dashboard panel the lab specifies aggregates the `path` label away, so the panel most likely to be on a wall is the one least able to see the endpoint that matters. Keeping `by (le, path)` costs one label and moves the detection of a dead dependency from forty eight seconds to seventeen.

### What the gateway was actually waiting for

The failures came back as 502 rather than 504, and the gateway said why:

```plaintext
gateway-1  {"level":"ERROR","service":"gateway","msg":"payment error: [Errno -2] Name or service not known"}
gateway-1  INFO: 172.18.0.1:33590 - "POST /reserve/3fe38225-.../pay HTTP/1.1" 502 Bad Gateway
```

The handler in `gateway/main.py` catches `httpx.TimeoutException` and turns it into 504, and falls through to a bare `except Exception` for everything else, which produces 502. So this was a name resolution failure, not the client timeout. What is worth keeping is how long a name resolution failure takes. The histogram says only that the failing requests landed between 2.5 s and 5 s:

```plaintext
gateway_request_duration_seconds_bucket{le="1.0", path="/reserve/{id}/pay"} 54
gateway_request_duration_seconds_bucket{le="2.5", path="/reserve/{id}/pay"} 71
gateway_request_duration_seconds_bucket{le="5.0", path="/reserve/{id}/pay"} 92
gateway_request_duration_seconds_bucket{le="+Inf",path="/reserve/{id}/pay"} 92
```

Twenty one observations sit in the 2.5 to 5 second band, which is how many 502 responses the outage produced, and nothing at all sits above five seconds. The other bands account for themselves: 54 fast requests from the two healthy stretches, and 17 around a second from the degradation run in the bonus task. The panel reported this as `p95 = 4.875 s`, a number that appears nowhere in the data: `histogram_quantile` interpolates linearly inside whichever bucket the quantile falls into, and that bucket is 2.5 seconds wide.

A separate probe, a blocking `socket.gethostbyname('payments')` run inside the gateway container, failed after 8.0 s with a different error code:

```plaintext
payments up:      lookup 1: resolved to 172.18.0.4 in 0.007 s
payments stopped: lookup 1: failed after 8.010 s with gaierror(-3, 'Temporary failure in name resolution')
                  lookup 2: failed after 8.000 s with gaierror(-3, ...)
                  lookup 3: failed after 8.004 s with gaierror(-3, ...)
```

Eight seconds and errno -3 against the gateway's errno -2 and under five seconds. These are different call paths, since httpx resolves through a worker thread and carries a five second client timeout, so I am recording both rather than picking whichever fits.

> A dependency that has dropped out of DNS costs seconds, not the milliseconds a refused connection costs, because the gateway sits inside `getaddrinfo` for most of its five second budget before it has anything to report. That is why a stopped container looks like a slow service on the first scrape and only later like a dead one. It also means the single number the dashboard offers here, a p95 of 4.875 seconds, is an artefact of a 2.5 second bucket. Above one second, `_sum / _count` gives a measurement and the quantile gives a bucket label.

---

## Task 2. SLOs and recording rules

### 3.8 SLIs, SLOs and the budget

**SLI 1, availability.** Share of gateway requests that did not return 5xx, measured at the gateway over a five minute rate window. **SLO: 99.5% over seven days.**

**SLI 2, latency.** Share of gateway requests completed in under 500 ms, read off the `le="0.5"` cumulative bucket. **SLO: 95%.**

At roughly 1000 requests per day:

| Quantity | Value |
|---|---:|
| Requests per seven day window | 7000 |
| Availability objective | 99.5% |
| Error budget | 0.5% |
| **Failed requests allowed per week** | **35** |
| Latency objective | 95% under 500 ms |
| **Requests allowed over 500 ms per week** | **350** |

The measured incident puts that number in context. The payments outage in section 3.7 lasted 2 minutes 17 seconds and produced 21.4 failed gateway requests. One outage of that length, on a service the users reach only for the final step of a purchase, spends **61% of the weekly availability budget**.

### 3.9 Recording rules

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
          (1 - gateway:sli_availability:ratio_rate5m)
          /
          (1 - 0.995)
```

Mounted by adding one line to the prometheus volumes in `docker-compose.monitoring.yaml`:

```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

The third rule reads a series the first rule records. That works because rules inside one group evaluate in order, and it is why all three live in `slo_rules` instead of in separate groups.

```bash
curl -s http://localhost:9090/api/v1/rules | python3 -c "..."
```

```plaintext
group slo_rules (interval 30s)
  gateway:sli_availability:ratio_rate5m         health=ok
  gateway:sli_latency_500ms:ratio_rate5m        health=ok
  gateway:error_budget_burn_rate:ratio_rate5m   health=ok
```

Prometheus also confirms it loaded the file from the mount and not from a config left over in the image:

```plaintext
rule_files:
- /etc/prometheus/rules.yml
```

### 3.10 The SLO gauge during the failure

The availability gauge and the burn rate panel read straight off the recorded series. Their behaviour during the outage, from the same polling loop:

| Moment | Availability SLI | Burn rate |
|---|---:|---:|
| before the stop | 1.00000 | 0.00 |
| t+70.3 s, first move | 0.99288 | 1.42 |
| t+100.2 s | 0.97642 | 4.72 |
| t+130.1 s | 0.96923 | 6.15 |
| t+190.1 s, lowest | 0.95997 | 8.01 |
| end of the window | 0.95997 | 8.01 |

The gauge crossed below its 99.5% threshold and stayed red, and the burn rate peaked at 8.01, meaning the budget was being spent eight times faster than a week's worth allows. At that rate the full seven day budget goes in about twenty one hours.

> The SLO gauge moved 38 seconds after the error rate panel and 54 seconds after the first visible sign of the outage. Both delays are deliberate: a 30 second rule interval and a five minute rate window exist to stop a single bad request from repainting a compliance number. That makes the SLO gauge the wrong instrument for noticing an incident and the right one for deciding what it cost. The dashboard now carries both, and they disagree by design for the first minute of every failure.

One caveat I can state but not explain. At the end of the first capture the three recorded series did not exist yet, and the same queries returned values twenty five seconds later at the start of the second capture. The rule group reported `health=ok` throughout and produced correct values for the rest of the session, so nothing downstream depends on this, but the timing does not match any cause I was able to check against the captures I have.

---

## Bonus task. Correlating a degradation across metrics and logs

```bash
./loadgen/run.sh 5 110 &
sleep 15
PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000 \
  docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml \
  up -d --force-recreate --no-deps payments
```

```plaintext
T_INJECT = 2026-09-13T16:32:52.298
payments /health: {"status":"healthy","failure_rate":0.5,"latency_ms":1000}
```

### The timeline

| Time | Where | What |
|---|---|---|
| 16:32:52.100 | gateway | last successful pay before the injection, `200 OK` |
| 16:32:52.298 | host | injection applied, payments recreated |
| 16:33:03.881 | payments | `Injecting 1000ms latency for 2572f4e3-...` first evidence in any log |
| 16:33:04.882 | payments | that one succeeds anyway, `Payment success: PAY-5562F7BC` |
| 16:33:21.168 | payments | `Injecting 1000ms latency for fba883bb-...` |
| 16:33:22.169 | payments | `Payment failed (injected) for fba883bb-...` first failure |
| 16:33:22.1698 | payments | `POST /charge HTTP/1.1" 500` |
| 16:33:22.1719 | gateway | `POST /reserve/fba883bb-.../pay HTTP/1.1" 500` |
| 16:34:27 | metrics | recovery after restore, `failure_rate` back to 0.0 |

One purchase end to end, merged from both services and sorted by the Docker side timestamp:

```plaintext
16:33:21.168652  payments-1  {"level":"INFO","service":"payments","msg":"Injecting 1000ms latency for fba883bb-de87-45c7-94c6-4eecdc8de724"}
16:33:22.169108  payments-1  {"level":"WARNING","service":"payments","msg":"Payment failed (injected) for fba883bb-de87-45c7-94c6-4eecdc8de724"}
16:33:22.169837  payments-1  INFO:  172.18.0.8:33026 - "POST /charge HTTP/1.1" 500 Internal Server Error
16:33:22.171958  gateway-1   INFO:  172.18.0.1:41248 - "POST /reserve/fba883bb-de87-45c7-94c6-4eecdc8de724/pay HTTP/1.1" 500 Internal Server Error
```

| Step | Elapsed |
|---|---:|
| injected sleep, first log line to the failure decision | 1000.46 ms |
| failure decision to the payments access line | 0.73 ms |
| payments answering to the gateway answering the client | 2.12 ms |
| **total, first payments line to the client response** | **1003.31 ms** |

Eleven seconds passed between the injection and the first affected request, because at five requests per second only one request in ten is a purchase, and the generator is synchronous. The first affected purchase then succeeded, since the failure rate is 0.5 and the latency and the failure are independent draws. The first user visible failure arrived 29.9 seconds after the injection.

### Root cause, read off the metrics

```plaintext
sum(rate(payments_request_duration_seconds_sum[2m])) / sum(rate(payments_request_duration_seconds_count[2m]))   = 1.0017 s
sum(rate(gateway_request_duration_seconds_sum{path="/reserve/{id}/pay"}[2m])) / sum(rate(..._count{...}[2m]))   = 0.8537 s

sum(increase(payments_charges_total[3m])) by (result)      {result="success"} = 2.18   {result="failed"} = 2.57
sum(increase(gateway_requests_total{path="/reserve/{id}/pay"}[3m])) by (status)
                                                           {status="200"} = 3.27   {status="500"} = 3.27
```

The payments mean of 1.0017 s reproduces `PAYMENT_LATENCY_MS=1000` to within two milliseconds, which says the whole second is the injected sleep and the service adds nothing measurable of its own. The gateway's mean for the same path is lower only because its counters kept running across the injection while the payments counters were reset by the container recreate, so the two windows cover different traffic. The charge split of roughly half failed against half succeeded reproduces `PAYMENT_FAILURE_RATE=0.5`, and the gateway's 200 and 500 counts match it one for one, which means the gateway passes the payments verdict through without adding failures or absorbing any.

The recorded SLIs for the same window:

```plaintext
gateway:sli_availability:ratio_rate5m      = 0.99507
gateway:sli_latency_500ms:ratio_rate5m     = 0.99015
gateway:error_budget_burn_rate:ratio_rate5m = 0.98522
```

> The correlation worked because payments writes the reservation UUID into its own log lines and the gateway carries the same UUID in the request URL, so the two access logs can be joined on a string that happens to be there. The `POST /charge` access line has no identifier at all: the only thing linking it to a purchase is that it sits between two lines that do, and that its client address is 172.18.0.8. Under any real concurrency that adjacency stops meaning causality, and this trace would join a charge to whichever purchase was nearest in time. What made the root cause obvious was not the logs but the agreement between three independent numbers: an injected 1000 ms that shows up as a 1.0017 s mean, a 0.5 failure rate that shows up as an even charge split, and a gateway whose status counts match the charge results exactly.

### One thing the metrics found that the lab did not ask about

The load generator reported a 23.6% client side error rate while the gateway's 5xx rate was a fraction of that, and the gap is all `409 Conflict` on reservations. Events 1 and 2 had become permanently unsellable:

```plaintext
{"detail":{"detail":"Not enough tickets (available: 0)"}}

events_reservations_active{instance="events:8081"} = 225
events_orders_total{instance="events:8081"}        =  63
```

In `events/main.py` the Redis key `event:{id}:held` is written in exactly one place, line 224, where reserving increments it, and read in one place, line 302, where `_get_available` subtracts it from the ticket count. Nothing decrements it. Confirming an order deletes `reservation:{id}` and leaves `held` alone, an expiring reservation vanishes by TTL and leaves `held` alone, and the key itself carries no TTL. So availability only ever falls, and after a few hundred reservations the catalogue sells out and stays that way. The `events_reservations_active` gauge drifts the same way for the same reason: it is incremented on reserve and decremented on confirm, and reservations that expire are never subtracted, which is why it reads 225 against 63 confirmed orders.

> This never appears on the golden signals dashboard, because a 409 is not a 5xx and the availability SLI counts it as a success. From the user's side the system is unusable, every purchase attempt is rejected, and every panel is green. The SLI was written to measure whether the service is answering rather than whether it is working, and the choice to exclude 4xx, which is the standard advice and is normally right, is what hides a server side bug that returns client side status codes.

---

## Results

| Check | Result |
|---|---|
| `prometheus.yml` with three scrape targets | committed, all three reported `up` fifteen seconds after start |
| Seven services running | gateway, events, payments, postgres, redis, prometheus, grafana |
| Prometheus scraping all three | `events:8081`, `gateway:8080`, `payments:8082`, health `up` |
| Custom metrics list | 22 series names across the three services |
| Request rate query | 0.4928 req/s over 5m, split 70 / 20 / 10 across three paths |
| Latency panel added | time series, p50 / p95 / p99, unit seconds, provisioned from the repo |
| Saturation panel added | gauge on `events_db_pool_size`, 0 to 10, thresholds at 7 and 9 |
| Failure observed | payments stopped for 2 m 17 s under 5 rps, 21.4 failed gateway requests |
| Which signal first | latency at t+16.6 s, health at t+17.6 s, errors at t+32.1 s, saturation never |
| SLI and SLO definitions with budget math | 99.5% and 95%, 35 failed requests per week, one outage spent 61% |
| Recording rules loaded | three rules, `health=ok`, `rule_files: /etc/prometheus/rules.yml` |
| SLO gauge during failure | 1.00000 down to 0.95997, burn rate to 8.01 |
| Bonus timeline | injection to first user visible failure 29.9 s, one purchase traced to 1003.31 ms |
| Bonus root cause | 1.0017 s mean against 1000 ms injected, charge split matching 0.5 |

Every detection time in this report is set by the fifteen second scrape interval before it is set by anything about the failure itself. Inside that floor the panels still disagree. Latency moved on the first scrape after the outage. The error rate needed a second one, because its series had only just come into existence. The SLO gauge waited another thirty eight seconds, which is what a thirty second rule interval and a five minute window are for. Saturation never moved at all, since it samples the connection pool during the one request that never touches the database. None of those readings is inaccurate. Each panel answers a slightly narrower question than its title suggests, and working out which question took longer than building the dashboard did.
