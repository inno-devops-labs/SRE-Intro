# Lab 6

## Task 1

I first checked the current monitoring stack instead of recreating it. Grafana and Prometheus were already running, and the live Grafana state already contained two lab6 alert rules, one webhook contact point, and a notification policy. I exported this live configuration and saved it under `monitoring/grafana/provisioning/alerting/` so the alerting setup is now reproducible from the repository.

```bash
curl -fsS http://localhost:3000/api/health
curl -fsS http://localhost:9090/-/healthy
```

```text
{
  "database": "ok",
  "version": "13.0.1",
  "commit": "a100054f"
}
```

```text
Prometheus Server is Healthy.
```

The application was healthy after I restored the previously stopped `payments` service:

```bash
curl -s http://localhost:3080/health
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

The two Grafana-managed alert rules are:

1. `QuickTicket High Error Rate`
2. `QuickTicket SLO Burn Rate`

I verified them through the Grafana API:

```bash
curl -fsS -u admin:admin http://localhost:3000/api/v1/provisioning/alert-rules | jq .
```

```text
[
  {
    "uid": "afqc6ochnn11cb",
    "title": "QuickTicket High Error Rate",
    "ruleGroup": "lab6",
    "for": "2m",
    "labels": {
      "severity": "critical"
    },
    "annotations": {
      "description": "Error rate exceeded 5% for 2 minutes. Check payments service health.",
      "runbook_url": "submissions/lab6.md",
      "summary": "Gateway error rate is above 5%."
    }
  },
  {
    "uid": "dfqc6ocxthdkwa",
    "title": "QuickTicket SLO Burn Rate",
    "ruleGroup": "lab6",
    "for": "5m",
    "labels": {
      "severity": "warning"
    },
    "annotations": {
      "description": "Error budget is burning too fast for the 99.5% availability target.",
      "runbook_url": "submissions/lab6.md",
      "summary": "SLO burn rate is above 6x."
    }
  }
]
```

PromQL queries:

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
```

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
```

The configured contact point is a webhook named `quickticket-alerts`:

```bash
curl -fsS -u admin:admin http://localhost:3000/api/v1/provisioning/contact-points | jq .
curl -fsS -u admin:admin http://localhost:3000/api/v1/provisioning/policies | jq .
```

```text
[
  {
    "uid": "bfqc5vew4w3y9d",
    "name": "quickticket-alerts",
    "type": "webhook",
    "settings": {
      "httpMethod": "POST",
      "url": "http://webhook-receiver:19090/alerts"
    }
  }
]
```

```text
{
  "receiver": "quickticket-alerts",
  "group_by": [
    "grafana_folder",
    "alertname"
  ],
  "group_wait": "5s",
  "group_interval": "30s",
  "repeat_interval": "5m"
}
```

Notification delivery evidence came from the local receiver container:

```bash
docker logs --tail=20 app-webhook-receiver-1
```

```text
{"timestamp": "2026-06-26T21:45:25.034535+00:00", "path": "/alerts", "body": "{\"receiver\":\"quickticket-alerts\",\"status\":\"firing\",\"alerts\":[{\"labels\":{\"alertname\":\"QuickTicket High Error Rate\",\"grafana_folder\":\"Lab 6\",\"severity\":\"critical\"},\"annotations\":{\"description\":\"Error rate exceeded 5% for 2 minutes. Check payments service health.\",\"runbook_url\":\"submissions/lab6.md\",\"summary\":\"Gateway error rate is above 5%.\"},\"startsAt\":\"2026-06-26T21:45:20Z\",\"values\":{\"B\":7.432456178178379,\"C\":1}}]}"}
{"timestamp": "2026-06-26T21:47:25.011502+00:00", "path": "/alerts", "body": "{\"receiver\":\"quickticket-alerts\",\"status\":\"firing\",\"alerts\":[{\"labels\":{\"alertname\":\"QuickTicket SLO Burn Rate\",\"grafana_folder\":\"Lab 6\",\"severity\":\"warning\"},\"annotations\":{\"description\":\"Error budget is burning too fast for the 99.5% availability target.\",\"runbook_url\":\"submissions/lab6.md\",\"summary\":\"SLO burn rate is above 6x.\"},\"startsAt\":\"2026-06-26T21:47:20Z\",\"values\":{\"B\":13.497264849572332,\"C\":1}}]}"}
{"timestamp": "2026-06-26T21:49:25.030502+00:00", "path": "/alerts", "body": "{\"receiver\":\"quickticket-alerts\",\"status\":\"resolved\",\"alerts\":[{\"labels\":{\"alertname\":\"QuickTicket High Error Rate\",\"grafana_folder\":\"Lab 6\",\"severity\":\"critical\"},\"annotations\":{\"description\":\"Error rate exceeded 5% for 2 minutes. Check payments service health.\",\"runbook_url\":\"submissions/lab6.md\",\"summary\":\"Gateway error rate is above 5%.\"},\"startsAt\":\"2026-06-26T21:45:20Z\",\"endsAt\":\"2026-06-26T21:49:20Z\",\"values\":{\"B\":4.961411245865491,\"C\":0}}]}"}
{"timestamp": "2026-06-26T22:15:25.048818+00:00", "path": "/alerts", "body": "{\"receiver\":\"quickticket-alerts\",\"status\":\"resolved\",\"alerts\":[{\"labels\":{\"alertname\":\"QuickTicket SLO Burn Rate\",\"grafana_folder\":\"Lab 6\",\"severity\":\"warning\"},\"annotations\":{\"description\":\"Error budget is burning too fast for the 99.5% availability target.\",\"runbook_url\":\"submissions/lab6.md\",\"summary\":\"SLO burn rate is above 6x.\"},\"startsAt\":\"2026-06-26T21:47:20Z\",\"endsAt\":\"2026-06-26T22:15:20Z\",\"values\":{\"B\":5.827160493827183,\"C\":0}}]}"}
```

So both alerts fired and later resolved.

### Runbook: QuickTicket High Error Rate

Alert:

- Fires when the gateway `5xx` rate is above `5%` for `2` minutes.
- Severity: `critical`
- Contact point: `quickticket-alerts`

Diagnosis:

1. Check the gateway health:
   `curl -s http://localhost:3080/health`
2. Check the payments service directly:
   `curl -s http://localhost:8082/health`
3. Check current Grafana alert state:
   `curl -fsS -u admin:admin http://localhost:3000/api/prometheus/grafana/api/v1/alerts | jq .`
4. Inspect the recent gateway logs:
   `docker logs --tail=50 app-gateway-1`
5. Inspect the recent payments logs:
   `docker logs --tail=50 app-payments-1`

Common causes:

- `payments` container is stopped
- `payments` is running with a non-zero `PAYMENT_FAILURE_RATE`
- the gateway circuit breaker is still open after upstream failures

Mitigation:

1. Start or restart `payments`:
   `docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml up -d payments`
2. Verify payments health:
   `curl -s http://localhost:8082/health`
3. Verify gateway recovery:
   `curl -s http://localhost:3080/health`
4. Wait until the alert returns to `Normal`

Escalation:

- If the gateway is still degraded after `10` minutes, escalate to the course staff and attach the gateway and payments logs.

Timeline from the recorded incident:

- `2026-06-26 21:45:20 UTC` `QuickTicket High Error Rate` became active.
- `2026-06-26 21:45:25 UTC` the critical webhook notification arrived.
- `2026-06-26 21:47:20 UTC` `QuickTicket SLO Burn Rate` became active.
- `2026-06-26 21:47:25 UTC` the warning webhook notification arrived.
- `2026-06-26 21:49:20 UTC` the high-error-rate alert resolved.
- `2026-06-26 22:15:20 UTC` the burn-rate alert resolved.

How long from failure injection to alert firing? Why the delay?

The first alert appeared after about `2` minutes. This matches the configured `for: 2m` window on `QuickTicket High Error Rate`, plus one evaluation cycle and the `5s` group wait before the webhook notification was sent. The burn-rate alert took longer because it uses `for: 5m` and a `30m` rate window, so it reacts to SLO impact instead of only the immediate spike.
