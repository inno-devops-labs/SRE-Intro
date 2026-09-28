# Lab 6 — Alerting and Incident Response

## Setup and alert configuration

The experiment used an isolated Docker Compose project, `lab6`, with QuickTicket, Prometheus, and Grafana. The Prometheus configuration scrapes gateway, events, and payments every 15 seconds. The existing Kubernetes deployment was not used for fault injection.

Both rules are Grafana-managed, in folder `QuickTicket Lab 6`, evaluation group `quickticket` (60 seconds).

| Rule | Condition | Pending | Label |
|---|---|---|---|
| QuickTicket High Error Rate | A > 5 | 2 minutes | `severity=critical` |
| QuickTicket SLO Burn Rate | A > 6 | 5 minutes | `severity=warning` |

High Error Rate query:

```promql
(sum(rate(gateway_requests_total{status=~"5.."}[5m])) or vector(0))
/ sum(rate(gateway_requests_total[5m])) * 100
```

`or vector(0)` supplies zero before the first 5xx series exists. Summary: `Gateway error rate is {{ $value }}%`. Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

SLO Burn Rate query (99.5% availability target):

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m]))
/ sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

A burn rate of 6 means consuming the error budget six times faster than allowed. This short experiment does not establish a full 30-minute baseline: the query uses available samples within that window.

Contact point: `quickticket-alerts`, type **Webhook**, URL `http://lab6-webhook:9000/`. A local HTTP receiver on the lab's Docker network records the actual JSON POSTs and returns HTTP 200. Resolved notifications are enabled.

Default notification policy: contact `quickticket-alerts`, group by `alertname`, group wait **30 seconds**, group interval **1 minute**, repeat interval **5 minutes**. No-data state is Normal; evaluation errors remain Error.

The contact point test succeeded at **08:24:23 UTC** with a received `TestAlert` notification. This test is separate from the real incident evidence below.

## Runbook: QuickTicket High Error Rate

### Alert

Gateway 5xx error rate exceeds 5% for 2 minutes, evaluated every minute.
Dashboard: [QuickTicket — Golden Signals](http://localhost:3000/d/quickticket-golden-signals).
Check error rate, request rate, and service health.

### Diagnosis

Run from this worktree's `app/` directory:

```bash
alias dc='docker compose -p lab6 -f docker-compose.yaml -f ../docker-compose.monitoring.yaml'
curl -s http://localhost:3080/health | python3 -m json.tool
curl -s http://localhost:8082/health
curl -s http://localhost:8081/health
dc logs gateway --tail=20 --since=5m
dc logs payments --tail=20 --since=5m
```

Check whether payment requests fail even when health checks succeed. Payments health includes `failure_rate` and `latency_ms`.

### Common causes

| Cause | Evidence | Fix |
|---|---|---|
| Payments stopped | Gateway reports payments down | `dc start payments` |
| Injected payment failures | Nonzero `failure_rate`; charge requests return 500 | Restore the normal configuration below |
| Events stopped | Events health unavailable | `dc start events` |
| Database connection exhaustion | `dc logs events --tail=50` shows pool errors | Check PostgreSQL health and connection usage; correct `DB_MAX_CONNS` if misconfigured, then recreate events |

Restore payments after this failure injection:

```bash
PAYMENT_FAILURE_RATE=0.0 docker compose -p lab6 -f docker-compose.yaml -f ../docker-compose.monitoring.yaml up -d --no-deps payments
```

Repeat health checks and a reserve-and-pay request. Keep traffic running until the alert returns to Normal and a resolved notification arrives. Rolling windows can remain above threshold after service recovery.

### Escalation

If unresolved after 10 minutes, contact the course instructor/TA through Moodle with timestamps, health responses, relevant logs, and attempted fixes.

## Incident evidence

All timestamps are UTC on 28 September 2026.

The workload ran one event-list, one reservation, and one payment request per second (about 3 requests/s), starting at 08:24:54. This payment-focused mix makes the 50% payment failure injection visible above the unchanged 5% gateway threshold.

The failure was applied from the worktree root:

```bash
PAYMENT_FAILURE_RATE=0.5 docker compose -p lab6 -f app/docker-compose.yaml -f docker-compose.monitoring.yaml up -d --no-deps payments
```

Grafana rule-state API excerpt (`/api/prometheus/grafana/api/v1/rules`; `firing` is the Firing state):

```json
{
  "name": "QuickTicket High Error Rate",
  "state": "firing",
  "health": "ok",
  "lastEvaluation": "2026-09-28T08:29:20Z"
}
```

Actual webhook receipt excerpt:

```json
{
  "received_at": "2026-09-28T08:29:50.016344+00:00",
  "receiver": "quickticket-alerts",
  "status": "firing",
  "title": "[FIRING:1] QuickTicket High Error Rate (QuickTicket Lab 6 critical)",
  "startsAt": "2026-09-28T08:29:20Z",
  "values": {
    "B0": 11.789913464143913
  }
}
```

The runbook diagnosis at 08:29:50 found:

```text
Gateway: healthy; events=ok; payments=ok; circuit_payments=CLOSED
Payments: {"status":"healthy","failure_rate":0.5,"latency_ms":0}
Events: healthy; postgres=ok; redis=ok
Payments log: Payment failed (injected)
Gateway log: POST /reserve/.../pay HTTP/1.1 500 Internal Server Error
```

After restoring `PAYMENT_FAILURE_RATE=0.0`, successful reserve-and-pay requests verified checkout recovery. At 08:29:56, traffic switched to healthy event reads at about 20 requests/s to observe the rolling error ratios falling. The bounded traffic run ended at 08:31:12 and was resumed at 08:35:33. This workload change and pause affect clearance time; it is not a measurement of recovery under unchanged load.

The resolved webhook arrived at **08:31:50.014 UTC** with status `resolved` and title `[RESOLVED] QuickTicket High Error Rate (QuickTicket Lab 6 critical)`.

Both rules were healthy and Normal in Grafana at 08:37:20. The warning also fired during the experiment (08:32:20), with its webhook received at 08:32:55.

### Timeline

| Time (UTC) | Event |
|---|---|
| 08:26:19 | Started payment failure injection |
| 08:26:20 | Payments restarted with `PAYMENT_FAILURE_RATE=0.5` |
| 08:27:20 | High Error Rate and SLO Burn Rate entered Pending |
| 08:29:20 | High Error Rate entered Firing; evaluated error rate 11.79% |
| 08:29:50 | Firing webhook received; followed runbook health checks and logs; identified injected payment failures |
| 08:29:51 | Applied normal payment configuration (`failure_rate=0.0`) |
| 08:29:53 | First successful payment after replacement; subsequent test purchases also returned 200 |
| 08:29:56 | Switched to healthy event-read traffic, about 20 requests/s |
| 08:31:20 | High Error Rate returned to Normal |
| 08:31:50 | Critical resolved webhook received |
| 08:32:20 | SLO Burn Rate entered Firing; its longer window still included the incident |
| 08:32:55 | Warning webhook received |
| 08:35:33 | Resumed healthy read traffic after the bounded run ended |
| 08:37:20 | SLO Burn Rate returned to Normal |
| 08:37:55 | Warning resolved webhook received |

**How long from injection to firing?** About **3 minutes 1 second** from starting the replacement (08:26:19) to Firing (08:29:20). Prometheus scrapes every 15 seconds; the 5-minute ratio must cross 5%, then Grafana evaluates every minute and requires 2 continuous minutes above threshold. The first qualifying evaluation was 08:27:20. The notification arrived 30 seconds later than Firing because of `group_wait`.

## Blameless postmortem: payment failures

**Date:** 28 September 2026

**Severity:** SEV-3 (isolated lab; partial checkout failures)

**Author:** Nikita Khripunkov

**Impact duration:** 08:26:19–08:29:53 UTC (3 minutes 34 seconds). The critical alert cleared at 08:31:20; its resolved notification arrived at 08:31:50.

The incident timeline is recorded above.

### Summary and impact

A simulated 50% payment failure rate caused checkout errors through the gateway. Event browsing and reservations continued to work. The impact was limited to synthetic traffic on the Lab 6 Compose stack. During the impact interval, 214 payment attempts produced 103 successes, 108 HTTP 500 responses, and 3 HTTP 502 responses during container replacements. All 428 event-list and reservation requests succeeded.

### Root cause

The payments service accepted a fault-injection configuration and returned HTTP 500 for a random subset of charges. The gateway propagated these failures. Health checks still reported healthy because the process was reachable; they did not test successful payment processing. There were also brief gateway 502 responses while the payments container was being replaced during injection and recovery.

### What went well

- The critical alert and webhook detected the real request failures.
- The runbook identified the configured failure rate using service health and logs.
- Restoring the normal configuration recovered payments without restarting the other services.

### What went wrong

- A healthy endpoint did not mean that checkout was working.
- The original read-heavy traffic mix could hide payment failures below the gateway-wide 5% threshold; the experiment needed more payment traffic.
- Rolling windows delayed alert clearance after the repair. The 30-minute burn-rate query had only a short lab baseline.

### Action items

| Action | Owner | Priority |
|---|---|---|
| Add a payment-specific 5xx ratio alert and verify it with the original read-heavy workload | Nikita Khripunkov | High |
| Reject nonzero `PAYMENT_FAILURE_RATE` in production configuration checks | Nikita Khripunkov | High |
| Add a synthetic reserve-and-pay check so monitoring tests checkout success as well as process health | Nikita Khripunkov | Medium |

**Most important action item:** the payment-specific alert. It detects checkout failures even when successful browsing dominates total gateway traffic and keeps the overall error rate below 5%.

The classmate runbook bonus was not attempted.
