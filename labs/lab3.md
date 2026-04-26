# Lab 3 — Monitoring, Observability & SLOs

![difficulty](https://img.shields.io/badge/difficulty-intermediate-yellow)
![topic](https://img.shields.io/badge/topic-Observability%20%26%20SLOs-blue)
![points](https://img.shields.io/badge/points-10%2B2-orange)
![tech](https://img.shields.io/badge/tech-Prometheus%20%2B%20Grafana-informational)

> **Goal:** Configure Prometheus, deploy the monitoring stack, build a golden signals dashboard, and define SLOs with recording rules.
> **Deliverable:** A PR from `feature/lab3` with `monitoring/prometheus/prometheus.yml` and `submissions/lab3.md`. Submit PR link via Moodle.

---

## Overview

In this lab you will practice:
- Writing Prometheus scrape configuration
- Deploying Prometheus and Grafana alongside QuickTicket
- Building dashboard panels in Grafana (latency + saturation)
- Defining SLIs and SLOs with Prometheus recording rules
- Observing error budget burn during a simulated incident

> **Provided:** `docker-compose.monitoring.yaml` (Prometheus + Grafana services), Grafana provisioning configs, and a partial dashboard with 3 panels. **You create:** `prometheus.yml`, 2 dashboard panels, and recording rules.

---

## Project State

**You should have from previous labs:**
- QuickTicket running via docker-compose (3 services + PostgreSQL + Redis)

**This lab adds:**
- Prometheus scraping metrics from all 3 services
- Grafana with golden signals dashboard (you complete it)
- SLO definitions with recording rules

---

## Task 1 — Configure Monitoring & Build Dashboard (6 pts)

**Objective:** Write the Prometheus config, deploy the monitoring stack, and complete the golden signals dashboard.

> **Tip:** All compose commands run from the `app/` directory:
> ```bash
> cd app/
> alias dc='docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml'
> # Now use: dc up -d --build, dc ps, dc stop payments, etc.
> ```

### 3.1: Write the Prometheus configuration

Prometheus needs to know **what to scrape**. The services expose metrics at:
- gateway: `http://gateway:8080/metrics`
- events: `http://events:8081/metrics`
- payments: `http://payments:8082/metrics`

Create `monitoring/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  # YOUR TASK: Add scrape targets for all 3 QuickTicket services
  # Each target needs:
  #   - job_name: a label identifying the service
  #   - static_configs.targets: ["hostname:port"]
  #
  # Hint: Docker Compose service names work as hostnames
  # Hint: Use the internal port (not the published port)
```

### 3.2: Start the monitoring stack

```bash
cd app/
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

Verify all 7 services:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
```

### 3.3: Verify Prometheus is scraping

```bash
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(f\"{t['labels']['job']:12} {t['health']:8} {t['scrapeUrl']}\")
"
```

All three should show `up`.

### 3.4: Explore metrics

Check what your services expose:

```bash
# Raw metrics from gateway
curl -s http://localhost:3080/metrics | grep -E "^gateway_" | head -10

# All custom metrics in Prometheus
curl -s http://localhost:9090/api/v1/label/__name__/values | python3 -c "
import sys, json
for n in json.load(sys.stdin)['data']:
    if any(x in n for x in ['gateway_', 'events_', 'payments_']):
        print(n)
"
```

Generate traffic and query:

```bash
./loadgen/run.sh 5 20
sleep 20

# Request rate (Traffic golden signal)
curl -s --data-urlencode 'query=sum(rate(gateway_requests_total[5m]))' \
  http://localhost:9090/api/v1/query | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"Request rate: {float(r['data']['result'][0]['value'][1]):.2f} req/s\")"
```

### 3.5: Complete the golden signals dashboard

Open Grafana at `http://localhost:3000` (login: admin/admin).

Find the **"QuickTicket — Golden Signals"** dashboard. You'll see 3 working panels + 2 placeholders:
- ✅ Request Rate (Traffic)
- ✅ Error Rate
- ✅ Service Health
- ❌ **"YOUR TURN: Add Latency panel"**
- ❌ **"YOUR TURN: Add Saturation panel"**

**Replace the two placeholder panels:**

**Latency panel (Golden Signal #1):**
- Visualization: **Time series**
- 3 queries: p50, p95, p99 using `histogram_quantile()` on `gateway_request_duration_seconds_bucket`
- Unit: **seconds**

**Saturation panel (Golden Signal #4):**
- Visualization: **Gauge**
- Query: `events_db_pool_size`
- Min: 0, Max: 10, Thresholds: green default, yellow at 7, red at 9

Save the dashboard.

### 3.6: Inject failure and observe

Generate steady traffic, then kill payments:

```bash
./loadgen/run.sh 5 60 &
sleep 15
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml stop payments
```

Watch the dashboard for 2 minutes, then restart:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start payments
```

### 3.7: Proof of work

**`monitoring/prometheus/prometheus.yml`** goes in your fork.

**Paste into `submissions/lab3.md`:**
1. Output of compose ps showing all 7 services
2. Prometheus targets output (all 3 `up`)
3. Custom metrics list
4. PromQL query output (request rate)
5. PromQL queries you used for Latency and Saturation panels
6. Dashboard observations: normal traffic vs payments failure
7. Answer: "Which golden signal showed the failure first? How long after killing payments?"

<details>
<summary>💡 Hints</summary>

- **prometheus.yml:** each scrape_config needs `job_name` and `static_configs` with `targets` list
- Use internal ports (8080, 8081, 8082), not published (3080)
- Wait 15-30s after starting for Prometheus to scrape
- Latency PromQL: `histogram_quantile(0.99, sum(rate(gateway_request_duration_seconds_bucket[1m])) by (le))`
- If Grafana shows "No data" — generate traffic first, wait for scrape
- In Grafana panel edit, click "Run queries" to preview

</details>

---

## Task 2 — Define SLOs & Recording Rules (4 pts)

> ⏭️ This task is optional. Skipping it will not affect future labs.

**Objective:** Define SLIs/SLOs and create Prometheus recording rules to track them.

### 3.8: Define SLIs and SLOs

**SLI 1 — Availability:** % of gateway requests returning non-5xx
- SLO target: 99.5% over a 7-day window

**SLI 2 — Latency:** % of gateway requests completing under 500ms
- SLO target: 95%

Calculate: with ~1000 requests/day, how many failures per week does the error budget allow?

### 3.9: Create recording rules

Create `monitoring/prometheus/rules.yml`:

```yaml
groups:
  - name: slo_rules
    interval: 30s
    rules:
      # YOUR TASK: Create 3 recording rules:
      # 1. gateway:sli_availability:ratio_rate5m — availability SLI
      # 2. gateway:sli_latency_500ms:ratio_rate5m — latency SLI
      # 3. gateway:error_budget_burn_rate:ratio_rate5m — burn rate (>1 = burning too fast)
      #
      # Hint: availability = rate(non-5xx) / rate(total)
      # Hint: latency = rate(bucket le=0.5) / rate(count)
      # Hint: burn_rate = (1 - availability) / (1 - 0.995)
```

Add to prometheus config:

```yaml
rule_files:
  - "rules.yml"
```

Mount the rules file (add to the prometheus volumes in `docker-compose.monitoring.yaml`):

```yaml
- ../monitoring/prometheus/rules.yml:/etc/prometheus/rules.yml:ro
```

Restart Prometheus and verify:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml restart prometheus
curl -s http://localhost:9090/api/v1/rules | python3 -c "
import sys, json
for g in json.load(sys.stdin)['data']['groups']:
    for r in g['rules']:
        print(f\"{r['name']:45} = {r.get('health', 'N/A')}\")
"
```

### 3.10: Build SLO panel

Add a Gauge panel in Grafana: `gateway:sli_availability:ratio_rate5m * 100`, min 99, max 100, threshold at 99.5.

Kill payments for 1 minute — watch the gauge drop.

**Paste into `submissions/lab3.md`:**
- SLI/SLO definitions with error budget math
- Rules loaded output
- SLO gauge observation during failure

---

## Bonus Task — Correlate Failure Across Metrics & Logs (2 pts)

> 🌟 For those who want extra challenge and experience.

**Objective:** Trace a specific failure across metrics and logs to find the root cause.

1. Start traffic: `./loadgen/run.sh 5 120 &`
2. After 30s, inject failures: restart payments with `PAYMENT_FAILURE_RATE=0.5 PAYMENT_LATENCY_MS=1000`
3. Watch Grafana for 2 minutes
4. Use `docker compose logs` to find the exact moment failures started
5. Correlate: timestamp of dashboard spike → gateway logs → payments logs

**Paste into `submissions/lab3.md`:**
- Timeline with timestamps: injection → first error in logs → spike on dashboard → recovery
- Log excerpts from gateway and payments at the failure moment
- Root cause explanation connecting metrics to logs

---

## How to Submit

```bash
git switch -c feature/lab3
git add monitoring/prometheus/ submissions/lab3.md
git commit -m "feat(lab3): add monitoring config and SLO definitions"
git push -u origin feature/lab3
```

PR checklist:
```text
- [x] Task 1 done — monitoring deployed, dashboard completed
- [ ] Task 2 done — SLOs defined, recording rules created
- [ ] Bonus Task done — failure correlation
```

---

## Acceptance Criteria

### Task 1 (6 pts)
- ✅ `prometheus.yml` committed with 3 scrape targets
- ✅ All 7 services running
- ✅ Prometheus scraping all 3 targets
- ✅ Latency panel added (p50/p95/p99)
- ✅ Saturation panel added (DB pool gauge)
- ✅ Failure observed + answer about which signal detected first

### Task 2 (4 pts)
- ✅ SLI/SLO definitions with error budget math
- ✅ Recording rules loaded in Prometheus
- ✅ SLO gauge showing drop during failure

### Bonus Task (2 pts)
- ✅ Timestamped failure timeline
- ✅ Log excerpts correlating with metrics
- ✅ Root cause explanation

---

## Rubric

| Task | Points | Criteria |
|------|-------:|----------|
| **Task 1** — Monitoring + dashboard | **6** | prometheus.yml written, stack running, 2 panels built, failure observed |
| **Task 2** — SLOs + recording rules | **4** | SLI/SLO definitions, rules loaded, SLO gauge |
| **Bonus Task** — Failure correlation | **2** | Timeline, logs, root cause |
| **Total** | **12** | 10 main + 2 bonus |

---

## Resources

<details>
<summary>📚 Documentation</summary>

- [Prometheus Getting Started](https://prometheus.io/docs/prometheus/latest/getting_started/)
- [Prometheus Configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- [Prometheus Recording Rules](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)
- [Grafana Dashboard Docs](https://grafana.com/docs/grafana/latest/dashboards/)

</details>

<details>
<summary>🛠️ PromQL Cheat Sheet</summary>

```promql
rate(gateway_requests_total[5m])                              # request rate
rate(gateway_requests_total{status=~"5.."}[5m]) / rate(gateway_requests_total[5m]) * 100  # error %
histogram_quantile(0.99, rate(gateway_request_duration_seconds_bucket[5m]))               # p99 latency
up{job="gateway"}                                             # is service up?
```

</details>

<details>
<summary>⚠️ Common Pitfalls</summary>

- **"No data" in Grafana** — generate traffic, wait 30s for scrape
- **Prometheus targets down** — check service name and internal port in prometheus.yml
- **Recording rules not loading** — check `curl localhost:9090/api/v1/rules` and add `rule_files` to prometheus config
- **Two compose files** — always use both: `-f docker-compose.yaml -f ../docker-compose.monitoring.yaml`

</details>
