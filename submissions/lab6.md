# Lab 6 — Alerting & Incident Response

**Branch:** `feature/lab6`  
**Date:** 2026-10-25  
**Environment:** QuickTicket Docker Compose, Prometheus, Grafana  
**SLO target:** 99.5% availability  
**Scope:** Grafana alerting, controlled Payments failure, recovery, and Redis runbook validation.

> **Evidence note.** This report records observed results from the test session. Exact wall-clock timestamps for every incident milestone were not captured at the time, so they are not invented below. Screenshots from Grafana/Prometheus and terminal output should be committed or attached separately if required by the course.

---

## 1. Summary of work completed

- Configured Grafana contact point `quickticket-alerts` using a Webhook receiver and successfully sent a test notification.
- Set the default notification policy to use `quickticket-alerts`, group by `alertname`, wait `30s` before the first notification, and repeat every `5m`.
- Created two Grafana-managed alert rules: `QuickTicket High Error Rate` and `QuickTicket SLO Burn Rate`.
- Injected a controlled Payments failure with `PAYMENT_FAILURE_RATE=0.5`, generated payment requests, observed HTTP 500 responses and the High Error Rate alert entering `Firing`, then restored Payments and observed the alert return to `Normal`.
- Tested the second runbook scenario by stopping Redis, observing the Events health check degrade and reservation requests fail, then starting Redis and verifying recovery.
- Wrote the second runbook at `submissions/runbooks/redis-down.md`.
- The independent classmate test for the bonus was **not performed**; no peer-test result is claimed.

## 2. Environment and monitoring configuration

The stack was started from `app/` using the application Compose file and monitoring overlay:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --build
```

The Postgres host port was mapped to `5433:5432` to avoid a conflict with a service already using host port `5432`.

Prometheus scrape configuration is in `monitoring/prometheus/prometheus.yml`:

```yaml
global:
  scrape_interval: 5s
  evaluation_interval: 1m

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["prometheus:9090"]
  - job_name: gateway
    metrics_path: /metrics
    static_configs:
      - targets: ["gateway:8080"]
  - job_name: events
    metrics_path: /metrics
    static_configs:
      - targets: ["events:8081"]
  - job_name: payments
    metrics_path: /metrics
    static_configs:
      - targets: ["payments:8082"]
```

During validation, Prometheus `/targets` showed the Prometheus, Gateway, Events, and Payments targets as `UP`.

### Useful local endpoints

| Component | Endpoint |
|---|---|
| Grafana | `http://localhost:3000` |
| Prometheus | `http://localhost:9090` |
| Gateway | `http://localhost:3080` |
| Events | `http://localhost:8081` |
| Payments | `http://localhost:8082` |

## 3. Grafana contact point and notification policy

**Contact point:** `quickticket-alerts`  
**Integration:** Webhook  
**Test result:** Grafana displayed `Test notification sent successfully` after sending a test notification to the configured Webhook receiver.

**Default notification policy:**

| Setting | Value |
|---|---|
| Default contact point | `quickticket-alerts` |
| Group by | `alertname` |
| Group wait | `30s` |
| Repeat interval | `5m` |

The configured contact point was also shown on the SLO Burn Rate rule details page as the notification destination (`lab6-webhook` in the displayed UI). The contact point was tested successfully; the evidence available from the session confirms the test-send success, not a retained copy of every incident notification payload.

## 4. Alert rule configurations

### 4.1 QuickTicket High Error Rate

**Type:** Grafana-managed  
**Severity:** `critical`  
**Evaluation interval:** `1m`  
**Pending period:** `2m`  
**Condition:** above `5` (percentage points)

The base query specified in the lab is:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
* 100
```

To avoid a missing numerator being treated as an empty result when there are no 5xx samples, the working rule used this equivalent defensive numerator:

```promql
(
  sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0)
)
/
sum(rate(gateway_requests_total[5m]))
* 100
```

**Annotations:**

- Summary: `Gateway error rate is {{ $value }}%`
- Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

**Observed result:** The rule was observed in `Firing` during the controlled Payments failure and returned to `Normal` after Payments was restored. A Grafana screenshot from the session showed the `Firing` state; a later check showed `Normal`.

### 4.2 QuickTicket SLO Burn Rate

**Type:** Grafana-managed  
**Severity:** `warning`  
**Evaluation interval:** `1m`  
**Pending period:** `5m`  
**Condition:** above `6`

```promql
(
  1 - (
    sum(rate(gateway_requests_total{status!~"5.."}[30m]))
    /
    sum(rate(gateway_requests_total[30m]))
  )
)
/
(1 - 0.995)
```

The query uses the 99.5% availability SLO: the observed error ratio is divided by the allowed error ratio (`1 - 0.995`). A burn rate greater than `6` breaches the configured threshold.

**Observed result:** The rule details page showed the expression and threshold condition `Is above 6`, an evaluation interval of every `1m`, and the rule in `Normal` at the time of inspection. Prometheus graph results for this expression showed values around `35`, `75`, and `180` during periods in which data was present. The Grafana history also contained `No data`, `Pending (Nodata)`, `Normal (Missingseries)`, and `Error` entries. These intermittent states are documented as a reliability limitation to investigate; they are not represented as a consistently firing alert.

## 5. Incident runbook — High Error Rate

### Alert

- **Name:** `QuickTicket High Error Rate`
- **Fires when:** Gateway 5xx error rate is greater than 5% for the configured pending period of 2 minutes.
- **Dashboard/tooling:** Grafana alert rule details and Prometheus metrics.

### Diagnosis

Run these commands from `app/`:

```bash
curl -s http://localhost:3080/health | python3 -m json.tool
curl -s http://localhost:8082/health
curl -s http://localhost:8081/health
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m
```

Check the Gateway and Payments health endpoints, inspect recent logs, and confirm whether payment requests are returning 5xx responses.

### Common causes and mitigation

| Possible cause | Evidence to inspect | Mitigation |
|---|---|---|
| Payments service stopped | Payments health check and Compose status | Start/recreate Payments and verify health |
| Payments failure rate elevated | Payments health response and `PAYMENT_FAILURE_RATE` configuration | Restore `PAYMENT_FAILURE_RATE=0.0`, recreate Payments, retest |
| Events service unavailable | Events health check and Events logs | Restore Events and verify Postgres/Redis dependencies |
| Database connection issue | Events logs and health checks | Investigate DB connectivity and pool configuration before restarting |

### Escalation

If the error rate does not recover within 10 minutes, preserve logs and alert-state evidence and escalate to the instructor/TA.

## 6. Incident simulation and observed metrics

### Failure injection

Payments was recreated with a 50% configured failure rate. From `app/`, the test command was:

```bash
PAYMENT_FAILURE_RATE=0.5 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --force-recreate payments
```

The Payments health endpoint confirmed the configuration:

```json
{"status":"healthy","failure_rate":0.5,"latency_ms":0}
```

Payment requests were generated against valid reservations. In one batch of ten requests, **4 returned HTTP 500 and 6 returned HTTP 200**. Additional repeated requests against a reservation were used to generate enough 5xx traffic for the alert test. The Gateway metrics endpoint showed the following observed counters at that point:

| Gateway route/status series | Observed count |
|---|---:|
| `/health`, status `200` | 36 |
| `/orders`, status `404` | 1 |
| `/events`, status `200` | 1 |
| `/events/{id}/reserve`, status `409` | 1 |
| `/events/{id}/reserve`, status `200` | 11 |
| `/reserve/{reservation_id}/pay`, status `200` | 7 |
| `/reserve/{reservation_id}/pay`, status `500` | 4 |

These are counters observed during the test session, not a clean isolated benchmark; counts may include earlier requests in the running stack.

The Prometheus error-rate query reached **100%** during a repeated-error test window. Grafana showed `QuickTicket High Error Rate` as `Firing`. After the failure rate was reset, the rule later returned to `Normal`.

### Recovery

Payments was restored using:

```bash
PAYMENT_FAILURE_RATE=0.0 docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --force-recreate payments
```

The health endpoint then returned:

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

A subsequent Grafana check showed the High Error Rate alert in `Normal`.

### Timeline and alert-delay answer

Exact timestamps for injection, first alert firing, diagnosis, fix, and resolution were not recorded as a complete timestamped sequence during the experiment. To avoid fabricating evidence, the timeline is recorded as observed order rather than invented clock times:

| Order | Event | Evidence/result |
|---:|---|---|
| 1 | Payments recreated with `PAYMENT_FAILURE_RATE=0.5` | Health endpoint reported `failure_rate: 0.5` |
| 2 | Payment requests generated | Batch showed 4 HTTP 500 and 6 HTTP 200; Gateway metrics showed 5xx counter activity |
| 3 | Error-rate metric queried | Prometheus error-rate query reached 100% during repeated-error testing |
| 4 | Alert observed | `QuickTicket High Error Rate` shown as `Firing` in Grafana |
| 5 | Payments restored with `PAYMENT_FAILURE_RATE=0.0` | Health endpoint reported `failure_rate: 0.0` |
| 6 | Recovery confirmed | Grafana High Error Rate alert shown as `Normal` |

**Answer: “How long from failure injection to alert firing? Why the delay?”**

The exact elapsed time cannot be established from the retained evidence because the injection and firing timestamps were not recorded together. The configured alert has a `2m` pending period and evaluates every `1m`, so a continuously breached condition is expected to fire after roughly 2–3 minutes, depending on where the failure falls relative to an evaluation cycle. Metric collection and the 5-minute `rate()` window also affect how quickly the query reflects new traffic.

## 7. Blameless postmortem

**Incident:** Controlled Payments failure-rate test  
**Date:** 2026-10-25  
**Duration:** Exact start/end duration not captured; test ran until Payments was restored and the alert returned to `Normal`  
**Severity:** SEV-3 (controlled, non-production lab exercise; no real customer impact)  
**Author:** Lab participant

### Summary

A controlled failure-rate increase in the Payments service caused payment requests to return HTTP 500 errors. Prometheus recorded 5xx activity, the Gateway High Error Rate alert entered `Firing`, and the service was restored by returning the configured failure rate to zero. The alert subsequently returned to `Normal`.

### Timeline

| Sequence | Event |
|---:|---|
| 1 | Set Payments failure rate to `0.5` and recreated the service |
| 2 | Confirmed configured failure rate using `/health` |
| 3 | Generated payment requests; observed HTTP 500 responses and increased 5xx metrics |
| 4 | Observed `QuickTicket High Error Rate` in `Firing` |
| 5 | Recreated Payments with failure rate `0.0` |
| 6 | Confirmed healthy Payments endpoint and `Normal` alert state |

Exact clock times were not retained, so sequence is provided instead of fabricated timestamps.

### Root cause

The controlled Payments configuration deliberately introduced a 50% failure probability for charge requests. Gateway propagated unsuccessful payment responses as 5xx responses, increasing the measured Gateway error rate. The incident was made visible by the High Error Rate alert. This was an intentional test injection, not an accidental production failure.

### What went well

- The Payments health endpoint exposed the configured failure rate.
- Gateway metrics made the 5xx responses observable in Prometheus.
- The High Error Rate alert entered `Firing` during the injected failure.
- Restoring the failure rate to zero returned Payments to a healthy state, and the alert later returned to `Normal`.
- The contact point's test notification succeeded.

### What could be improved

- A timestamped incident log should be started at injection and updated at every response milestone.
- The SLO Burn Rate rule showed intermittent `No data`, `Missingseries`, and `Error` states, which should be investigated and tested under steady traffic.
- The notification payload for the actual firing event should be captured, not only the contact-point test result.
- The runbook should include a clear evidence checklist for alert firing and recovery.

### Action items

| Action | Owner | Priority | Status |
|---|---|---|---|
| Capture injection, firing, diagnosis, fix, and resolution timestamps in the next test | Lab participant | High | Open |
| Investigate SLO Burn Rate `No data`/`Missingseries`/`Error` history under low or absent traffic | Lab participant | High | Open |
| Save a screenshot/export of the actual incident notification payload | Lab participant | Medium | Open |
| Keep the Payments runbook steps for checking health, logs, failure-rate configuration, and recovery verification | Lab participant | Medium | Runbook documented |

**Most important action item and why:** Investigate and stabilize the SLO Burn Rate rule's intermittent `No data`/`Missingseries`/`Error` states. An alert that does not evaluate consistently may fail to warn reliably about SLO risk, even when its PromQL expression returns values during active traffic.

## 8. Second runbook — Redis unavailable

The second runbook is stored separately at `submissions/runbooks/redis-down.md`.

### Test procedure and observations

The test was performed with the same local Compose stack.

1. Before the test, `http://localhost:8081/health` returned:

   ```json
   {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
   ```

2. Redis was stopped:

   ```bash
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml stop redis
   ```

3. Events health changed to:

   ```json
   {"status":"degraded","checks":{"postgres":"ok","redis":"down"}}
   ```

4. A reservation request through Gateway failed with HTTP `504` and response:

   ```json
   {"detail":"Events service timeout"}
   ```

5. Redis was restarted:

   ```bash
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml start redis
   ```

6. Events health returned to `healthy` with both Postgres and Redis checks `ok`. A new reservation request then returned HTTP `200` with a reservation ID, `event_id: 3`, `quantity: 1`, `total_cents: 15000`, and `expires_in_seconds: 300`.

### Bonus status

The failure injection and recovery path were tested by the author, and the second runbook documents the procedure and observed results. **A classmate did not independently test this runbook**, so the peer-test requirement and feedback-based update are not claimed as completed.

## 9. Evidence checklist

Attach or commit the actual screenshots/outputs available from the test session. Do not replace them with fabricated screenshots.

- [x] Grafana list shows `QuickTicket High Error Rate` and `QuickTicket SLO Burn Rate`.
- [x] High Error Rate rule observed in `Firing` during the Payments failure.
- [x] High Error Rate rule later observed in `Normal` after recovery.
- [x] SLO Burn Rate rule details show PromQL, condition above `6`, evaluation every `1m`, and `Normal` at inspection time.
- [x] Prometheus graph shows SLO burn-rate query values around `35`, `75`, and `180` during periods with data.
- [x] Contact point test reported `Test notification sent successfully`.
- [x] Payments health response recorded both `failure_rate: 0.5` during injection and `failure_rate: 0.0` after recovery.
- [x] Redis-down test recorded degraded health and HTTP `504`; after Redis restart, health recovered and reservation returned HTTP `200`.
- [ ] Exact timestamps for every incident milestone — not captured.
- [ ] Captured actual incident webhook payload — not retained in the evidence available for this report.
- [ ] Independent peer test and feedback — not performed.

## 10. Files included in the submission

- `monitoring/prometheus/prometheus.yml` — scrape configuration for Prometheus, Gateway, Events, and Payments.
- `submissions/runbooks/redis-down.md` — second runbook for Redis unavailability.
- `submissions/lab6.md` — this report.

## 11. Final assessment against the lab rubric

| Requirement | Status |
|---|---|
| Two Grafana-managed alert rules | Configured; High Error Rate failure/recovery behavior observed |
| Contact point and notification policy | Configured; contact-point test succeeded |
| High Error Rate runbook | Included in this report |
| Failure injection and recovery | Completed and observed |
| Alert timeline with exact timestamps | Partial: sequence documented; exact timestamps not captured |
| Blameless postmortem | Included |
| Second runbook | Included and manually tested by author |
| Peer test and feedback update (bonus) | Not completed |

**Submission note:** The report intentionally distinguishes verified observations from missing evidence. To strengthen the submission before opening the PR, attach the actual Grafana/Prometheus screenshots and, if time permits, repeat the incident once while recording timestamps for each milestone.
