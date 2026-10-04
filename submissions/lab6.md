# Lab 6 — Alerting and Incident Response

## Environment

The work was performed on 2026-09-26 with Docker Desktop on Windows PowerShell.

```text
Docker client/server: 29.2.1 / 29.2.1
Docker Compose: v5.0.2
Grafana: 13.0.1
Prometheus: v3.11.2
```

The final Compose status contained all seven services.

```text
NAME               SERVICE      STATUS
app-events-1       events       Up
app-gateway-1      gateway      Up
app-grafana-1      grafana      Up
app-payments-1     payments     Up
app-postgres-1     postgres     Up (healthy)
app-prometheus-1   prometheus   Up
app-redis-1        redis        Up (healthy)
```

Prometheus scraped all application targets successfully.

```text
scrapePool  health  scrapeUrl
events      up      http://events:8081/metrics
gateway     up      http://gateway:8080/metrics
payments    up      http://payments:8082/metrics
```

The baseline used `app/loadgen/run.ps1`, a PowerShell implementation of the supplied generator: 70% event reads, 20% reservations, and 10% full reservation-and-payment flows. It sends HTTP requests to the gateway and counts actual responses.

## Alert Rules

Grafana provisioning is stored in `monitoring/grafana/provisioning/alerting/quickticket-alerts.yaml`. The real Prometheus data source UID obtained from `GET /api/datasources` was `PBFA97CFB590B2093`.

| Rule | UID | Evaluation | Pending | Severity |
|---|---|---:|---:|---|
| QuickTicket High Error Rate | `quickticket-high-error-rate` | 1m | 2m | critical |
| QuickTicket SLO Burn Rate | `quickticket-slo-burn-rate` | 1m | 5m | warning |

```promql
# QuickTicket High Error Rate
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m]))
* 100
```

The threshold is greater than `5`.

```promql
# QuickTicket SLO Burn Rate
(
  1 -
  (
    sum(rate(gateway_requests_total{status!~"5.."}[30m]))
    /
    sum(rate(gateway_requests_total[30m]))
  )
)
/
(1 - 0.995)
```

The threshold is greater than `6`.

Grafana API evidence after provisioning:

```text
GET /api/v1/provisioning/alert-rules
quickticket-high-error-rate: title=QuickTicket High Error Rate, for=2m,
  datasourceUid=PBFA97CFB590B2093, labels={severity: critical}
quickticket-slo-burn-rate: title=QuickTicket SLO Burn Rate, for=5m,
  datasourceUid=PBFA97CFB590B2093, labels={severity: warning}
```

`gateway_requests_total`, `gateway_request_duration_seconds_bucket`, `payments_charges_total`, and `events_db_pool_size` were present in Prometheus. The baseline query `sum(rate(gateway_requests_total[1m]))` returned `3.2815294727969673` requests/s at `2026-09-26T08:37:05Z`.

## Contact Point and Notification Policy

The contact point is a webhook named `quickticket-alerts`. A temporary local receiver ran as `quickticket-webhook-receiver` on the Docker network; it writes received JSON lines only to `/tmp/quickticket-webhooks.jsonl` inside that temporary container. No credentials or receiver data were committed.

```text
GET /api/v1/provisioning/contact-points
uid=quickticket-webhook
name=quickticket-alerts
type=webhook
url=http://quickticket-webhook-receiver:18080/alerts
disableResolveMessage=false
```

The Grafana container made a successful POST connectivity test to that receiver at `2026-09-26T08:35:54.792362Z`. The actual alert delivery is documented below with both a firing and a resolved webhook.

```text
GET /api/v1/provisioning/policies
receiver=quickticket-alerts
group_by=[alertname]
group_wait=30s
group_interval=5m
repeat_interval=5m
```

Relevant received webhook fields for the real firing notification:

```json
{
  "timestamp": "2026-09-26T08:43:10.022832+00:00",
  "status": "firing",
  "payload": {
    "receiver": "quickticket-alerts",
    "status": "firing",
    "alerts": [{
      "status": "firing",
      "labels": {"alertname": "QuickTicket High Error Rate", "severity": "critical"},
      "annotations": {"summary": "Gateway error rate is 6.641001195999182%"}
    }]
  }
}
```

The receiver also recorded `status=resolved` at `2026-09-26T08:48:10.011334Z`.

# Runbook: QuickTicket High Error Rate

## Alert

- **Fires when:** gateway 5xx rate is above 5% for two minutes.
- **Dashboard:** QuickTicket — Golden Signals in Grafana at `http://localhost:3000`.
- **Impact:** checkout requests can fail even while event listing remains available.

## Diagnosis

1. Check the gateway and its direct dependencies.

   ```powershell
   Invoke-RestMethod http://localhost:3080/health | ConvertTo-Json
   Invoke-RestMethod http://localhost:8081/health | ConvertTo-Json
   Invoke-RestMethod http://localhost:8082/health | ConvertTo-Json
   ```

2. Check the failing request ratio and the SLO burn rate.

   ```powershell
   Invoke-RestMethod 'http://localhost:9090/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B5m%5D))%2Fsum(rate(gateway_requests_total%5B5m%5D))*100'
   Invoke-RestMethod 'http://localhost:9090/api/v1/query?query=(1-(sum(rate(gateway_requests_total%7Bstatus!~%225..%22%7D%5B30m%5D))%2Fsum(rate(gateway_requests_total%5B30m%5D))))%2F(1-0.995)'
   ```

3. Inspect Compose state and the relevant logs.

   ```powershell
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml ps
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs --tail=20 --since=5m gateway
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs --tail=20 --since=5m payments
   docker inspect app-payments-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | Select-String PAYMENT_
   ```

4. If payments has a non-zero `PAYMENT_FAILURE_RATE`, restore it safely.

   ```powershell
   $env:PAYMENT_FAILURE_RATE='0.0'
   $env:PAYMENT_LATENCY_MS='0'
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml up -d --force-recreate payments
   Remove-Item Env:PAYMENT_FAILURE_RATE -ErrorAction SilentlyContinue
   Remove-Item Env:PAYMENT_LATENCY_MS -ErrorAction SilentlyContinue
   ```

5. Verify `/health`, the payment health payload, error rate, and Grafana alert state. Continue normal traffic until the rolling query window clears. Escalate to the instructor or TA if the issue is not resolved within 10 minutes.

## Common Causes

| Cause | How to identify | Safe response |
|---|---|---|
| Payments is stopped | gateway health has `payments: down` | `docker compose ... start payments` |
| Injected payment failures | payments health reports a non-zero failure rate; logs show `Payment failed (injected)` | recreate payments with `PAYMENT_FAILURE_RATE=0.0` |
| Events is unavailable | gateway health has `events: down` | inspect events, PostgreSQL, and Redis health before restarting events |
| Payment latency or timeout | payments health reports latency; gateway shows timeouts | restore `PAYMENT_LATENCY_MS=0`, then verify the timeout budget |

## Incident Experiment

### Baseline

Before injection, gateway, events, and payments health checks were healthy and payments reported:

```json
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

### Timeline

| UTC timestamp | Observation |
|---|---|
| 08:39:55.254646Z | Payments was recreated with `PAYMENT_FAILURE_RATE=0.5`, `PAYMENT_LATENCY_MS=0`. |
| 08:40:47.843405Z | First recorded sample: error rate `10.015761093679105%`, burn rate `20.048843130567434`, availability `0.8997525172257823`; High Error Rate was Pending. |
| 08:41:49.508867Z | Error rate `9.695753896171668%`, burn rate `15.645223142014384`, availability `0.9030139209548627`; both rules were Pending. |
| 08:42:40Z | Grafana recorded High Error Rate as Alerting. |
| 08:42:55.739430Z | Sample: error rate `6.122086480334698%`, burn rate `9.873156140241326`, availability `0.938769385806538`. |
| 08:43:10.022832Z | Webhook receiver recorded the High Error Rate firing notification. |
| 08:44:07.190030Z | Runbook diagnosis began. Payments health still showed `failure_rate: 0.5`. |
| 08:44:07.597748Z | Payments recovery started with failure rate and latency reset to zero. |
| 08:46:51.629208Z | Sample after normal traffic: error rate `0`, burn rate `4.03428430970378`, availability `1`; both Grafana rules were Normal. |
| 08:48:10.011334Z | Webhook receiver recorded `status=resolved`. |

The first detection signal was the High Error Rate evaluation: it entered Pending at `08:40:40Z`, about 45 seconds after injection. It fired at `08:42:40Z`, about 2 minutes 45 seconds after injection. The webhook arrived about 30 seconds after the firing state, matching `group_wait=30s`.

The most important logs were produced by the payments service during the failure:

```text
2026-09-26T08:39:57.871581183Z WARNING Payment failed (injected) for 8ab48bb8-cc05-4efd-8874-0c9c64775518
2026-09-26T08:39:58.109864359Z WARNING Payment failed (injected) for 72e18759-e9dc-4bf9-8ff3-5f0cfa84a42f
2026-09-26T08:39:58.581Z INFO Payment success: PAY-97F325B5 for bf1c1587-06f7-4f24-8a5a-b75093fd788f
2026-09-26T08:39:59.078Z WARNING Payment failed (injected) for dbcbc10a-e141-4dba-9de6-7759331e3d94
```

The payment-heavy generator sent actual reservation and gateway payment requests. Its observed output began as follows:

```text
[15s] pay_requests=64 success=22 fail=42 error_rate=65.6%
[30s] pay_requests=130 success=37 fail=93 error_rate=71.5%
[60s] pay_requests=266 success=51 fail=215 error_rate=80.8%
```

Many later generator failures were reservation `409` responses after tickets for the selected events were no longer available. This is why the High Error Rate rule became Normal at `08:43:40Z` before the payments configuration was restored. The recovery action was still completed at `08:44:07Z`, and final health, Normal alert states, and the resolved notification were verified.

### Incident Metrics

The maximum sampled gateway error rate was `10.015761093679105%`. The maximum sampled burn rate was `20.048843130567434`. The minimum sampled availability was `0.8997525172257823`.

After the experiment, the gateway payment-route counter query returned these actual values:

```text
sum by (status) (gateway_requests_total{path="/reserve/{id}/pay"})
200 = 104
500 = 62
502 = 4
```

The first sampled threshold crossing occurred 52.589 seconds after injection. The firing delay was 164.745 seconds, notification delay was 30.023 seconds, and diagnosis began 57.167 seconds after the notification. The configuration mitigation started 0.408 seconds after diagnosis started. The observable interval from injection to the resolved webhook was 494.757 seconds. The delay is explained by the 15-second Prometheus scrape interval, one-minute Grafana evaluation interval, two-minute pending period, 30-second notification group wait, and the rolling PromQL windows.

## Recovery Verification

Final checks were:

```json
GET /health via gateway
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}

GET /health via payments
{"status":"healthy","failure_rate":0.0,"latency_ms":0}
```

The Grafana alert API reported `Normal` for both QuickTicket High Error Rate and QuickTicket SLO Burn Rate. The webhook receiver recorded `status=resolved` at `2026-09-26T08:48:10.011334Z`.

# Postmortem: QuickTicket Payment Failures

**Date:** 2026-09-26  
**Duration:** 08:39:55Z to 08:48:10Z (observable notification interval: 8m 14.757s)  
**Severity:** SEV-3  
**Author:** Georgy Pyanov

## Summary

The payments service was intentionally configured to fail 50% of charge attempts during a controlled experiment. Gateway checkout requests returned 5xx responses, consuming availability error budget and causing the High Error Rate alert to fire.

## User Impact

Users could still list events, but some checkout requests failed. The peak sampled gateway 5xx ratio was 10.015761093679105%.

## Timeline

| Time (UTC) | Event |
|---|---|
| 08:39:55 | Payment failure rate was set to 0.5. |
| 08:40:40 | High Error Rate entered Pending. |
| 08:42:40 | High Error Rate became Alerting. |
| 08:43:10 | Firing webhook was received. |
| 08:44:07 | Investigation began and payments health identified the non-zero failure rate. |
| 08:44:07 | Payments was recreated with failure rate 0.0 and latency 0. |
| 08:46:51 | Both alert rules were observed as Normal. |
| 08:48:10 | Resolved webhook was received. |

## Detection

Grafana detected a sustained 5xx ratio above 5%. Detection was delayed by Prometheus scraping, Grafana evaluation, and the two-minute pending period. The notification was further delayed by the configured 30-second group wait.

## Systemic Root Cause

The system intentionally allowed a fault-injection configuration to make 50% of payments fail. Gateway propagates failed payment calls as 5xx checkout errors. There was no resilience mechanism enabled in this lab to isolate failures at the payment boundary.

## Contributing Factors

- The normal mixed generator creates only about 10% full purchases, so it would have produced less evidence.
- The selected events ran out of tickets during the payment-heavy traffic, changing later failures to reservation 409 responses and reducing the 5xx rate before configuration recovery.
- The 30-minute burn-rate window reacts more slowly than the five-minute error-rate query.

## What Went Well

- Prometheus targets stayed up and the Gateway metric exposed the failure.
- The error-rate rule entered Pending and then Alerting with the configured timing.
- The webhook delivered both firing and resolved messages.
- The runbook identified the effective configuration from the payments health endpoint and logs.

## What Went Wrong

- Ticket availability was not checked before starting a long payment-heavy run, so later traffic did not remain payment-heavy.
- The runbook did not initially call out that a rolling window can resolve after traffic changes even while a dependency still has a bad configuration.

## Runbook Improvement

The runbook now instructs the responder to check payment health and the payment environment even if the error-rate rule has already returned to Normal. It also requires continued normal traffic while waiting for rolling windows and notifications to settle.

## Action Items

| Action | Owner | Priority |
|---|---|---|
| Add a safe test event with enough capacity for long payment-heavy incident traffic. | Georgy Pyanov | High |
| Add a payment dependency health panel and alert. | Georgy Pyanov | Medium |
| Add the rolling-window caveat to the incident runbook. | Georgy Pyanov | Medium |
| Evaluate payment-boundary resilience patterns in the later resilience lab. | Georgy Pyanov | Medium |

The most important action is adding a safe high-capacity test event. It keeps failure traffic representative for the full pending and recovery interval, so alert behavior can be measured without ticket exhaustion changing the signal.

# Runbook: Redis Unavailable — Reservations and Events Degraded

## Alert / Symptom

Reservations fail or gateway health reports events degraded while event listing may still work.

## Diagnosis

1. Check gateway and events health:

   ```powershell
   Invoke-RestMethod http://localhost:3080/health | ConvertTo-Json
   Invoke-RestMethod http://localhost:8081/health | ConvertTo-Json
   ```

2. Check Redis and Compose state:

   ```powershell
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml ps
   docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml logs --tail=30 --since=5m events redis
   ```

3. Distinguish a dependency failure from an application failure. If Redis is not healthy and events health reports `redis` degraded, restore Redis first. If Redis is healthy but events remains degraded, inspect events logs and its Redis host/port configuration.

## Mitigation

```powershell
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml start redis
Start-Sleep -Seconds 6
Invoke-RestMethod http://localhost:8081/health | ConvertTo-Json
Invoke-RestMethod http://localhost:3080/health | ConvertTo-Json
```

If events does not recover after Redis is healthy, restart only events and repeat the health checks. Escalate to the instructor or TA after 10 minutes or if PostgreSQL is also unavailable.

## Peer Validation Pending

No real classmate peer test was available in this environment. Therefore the cross-test bonus is not claimed and no feedback has been invented.

## Conclusions

Two Grafana-managed alerts, a webhook contact point, and a reproducible notification policy were provisioned from tracked files. A real payments fault caused a real High Error Rate alert, webhook delivery, diagnosis, recovery action, final Normal alert states, and a resolved webhook. The peer-validation bonus remains pending.
