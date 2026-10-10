# Lab 3 monitoring

From the repository root:

```bash
docker compose -p sre-lab3 -f app/docker-compose.yaml -f docker-compose.monitoring.yaml up -d --build
```

Gateway: <http://localhost:3080>, Prometheus: <http://localhost:9090>,
Grafana: <http://localhost:3000> (`admin/admin`, local lab credentials).
The provisioned **QuickTicket — Golden Signals** dashboard has traffic, errors,
scrape health, p50/p95/p99 latency, DB pool saturation and an availability SLI gauge.
Configuration and recording-rule mounts are read-only; `rule_files` resolves relative
to `/etc/prometheus/prometheus.yml`.

## Validation and experiment

```bash
docker compose -p sre-lab3 -f app/docker-compose.yaml -f docker-compose.monitoring.yaml \
  exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker run --rm --entrypoint promtool \
  -v "$PWD/monitoring/prometheus:/rules:ro" -w /rules \
  prom/prometheus:v3.11.2 test rules rules.test.yml
python3 scripts/lab3-observe.py > /tmp/lab3-observations.jsonl
```

The experiment takes about 12 minutes. It generates read/health/checkout traffic,
stops payments for two minutes, restores it, injects 50% payment failures and
1000ms latency for two minutes, then observes 5.5 minutes of recovery. It restores
payments in a `finally` block. Run only against the dedicated `sre-lab3` project.
The load generator consumes tickets: use a fresh lab database for each full run.
The supplied `app/loadgen/run.sh` is another way to generate mixed traffic.

To correlate logs during the fault phase:

```bash
docker compose -p sre-lab3 -f app/docker-compose.yaml -f docker-compose.monitoring.yaml \
  logs --timestamps --since 2m gateway payments
```

Capture payment logs **before** recreating the container at recovery. A Compose
`restart` does not change environment variables; the script uses `up --force-recreate`.

## SLI scope and error budget

Availability: non-5xx / all instrumented gateway requests, target **99.5% over 7 days**.
Latency: gateway requests taking **at most 500ms**, target **95% over 7 days**.
The histogram bucket `le="0.5"` includes the boundary; it approximates the lab's
“under 500ms” wording. Both SLIs include `/health`, but middleware excludes `/metrics`.
4xx counts as available under this lab definition, even though a checkout may fail
for business reasons. Latency includes fast failures, so availability must be read
alongside latency. In production a separate checkout SLI would better capture
purchase experience.

At 1,000 requests/day, 7,000 requests/week × 0.005 = **35 allowed 5xx/week**.
The latency budget is 7,000 × 0.05 = **350 requests above 500ms/week**.
An equivalent time budget at uniform load is 50.4 minutes/week, but the actual
availability SLO is request-based, not a stopwatch budget.

The three recording rules use a **5-minute operational window**, not a complete
7-day compliance measurement. Burn rate = (1 − availability) / 0.005;
20 means the error fraction is 20 times the sustainable budget fraction.
Absent 5xx series yields zero errors when traffic exists; zero traffic yields NaN
(unknown), and missing metrics remain absent. The tests cover these distinctions.

For the request-weighted 7-day availability SLI after collecting sufficient history:

```promql
1 - (
  (sum(increase(gateway_requests_total{status=~"5.."}[7d]))
   or 0 * sum(increase(gateway_requests_total[7d])))
  / sum(increase(gateway_requests_total[7d]))
)
```

For the 7-day latency SLI:

```promql
sum(increase(gateway_request_duration_seconds_bucket{le="0.5"}[7d]))
/ sum(increase(gateway_request_duration_seconds_count[7d]))
```

Do not average the 5-minute ratios to obtain weekly compliance: different windows
can have different traffic volumes. This short experiment does not establish a
week of compliance. Prometheus storage is local to this lab container, so recreating
it also loses history. `events_db_pool_size` measures active connections only at
scrape time; a zero sample does not prove saturation never occurred between scrapes.
