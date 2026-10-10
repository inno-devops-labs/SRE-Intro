# Lab 6 — Alerting & Incident Response

**Student:** Gleb Shvetsov

**GitHub:** `L10nff`

**Working branch:** `feature/lab6`

## Task 1 — Create Alerts and Respond to an Incident

### 1. Monitoring stack

The Lab 6 branch was created from the current `upstream/main`. The Prometheus,
Grafana dashboard, and recording-rule configuration from Lab 3 was restored as
the monitoring baseline.

The full Docker Compose stack contained:

```text
gateway
events
payments
postgres
redis
prometheus
grafana
```

Prometheus target verification:

```text
job=events health=up last_error=''
job=gateway health=up last_error=''
job=payments health=up last_error=''
```

The Grafana instance used version `13.0.1`, and the Prometheus datasource was
provisioned with UID `PBFA97CFB590B2093`.

### 2. Contact point

The Grafana contact point was configured as follows:

```text
name: quickticket-alerts
type: webhook
uid: dfzjo8ntaof0ga
```

The receiver URL was kept outside Git. The test message was received by the
external webhook receiver:

```text
webhook_test_received: yes
webhook_method: POST
notification_status: firing
notification_alertname: Lab6ContactPointTest
```

No webhook token or Grafana credential is present in this repository.

### 3. Alert rules

The rules are declaratively provisioned from:

```text
monitoring/grafana/provisioning/alerting/quickticket-alerting.yml
```

Both rules use a one-minute evaluation interval.

#### QuickTicket High Error Rate

- UID: `quickticket-high-error-rate`
- Threshold: above 5 percent
- Pending period: 2 minutes
- Label: `severity=critical`
- Summary: `Gateway error rate is {{ $value }}%`
- Description: `Error rate exceeded 5% for 2 minutes. Check payments service health.`

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
/
sum(rate(gateway_requests_total[5m])) * 100
```

#### QuickTicket SLO Burn Rate

- UID: `quickticket-slo-burn-rate`
- Threshold: above 6
- Pending period: 5 minutes
- Label: `severity=warning`

```promql
(1 - (
  sum(rate(gateway_requests_total{status!~"5.."}[30m]))
  /
  sum(rate(gateway_requests_total[30m]))
)) / (1 - 0.995)
```

Provisioning verification before the incident:

```text
required_alert_rules_found: 2
alert_rule: title=High Error Rate uid=quickticket-high-error-rate for=2m severity=critical
alert_rule: title=SLO Burn Rate uid=quickticket-slo-burn-rate for=5m severity=warning
```

The stable UIDs and all rule semantics remained unchanged. The final
checked-in display titles include the `QuickTicket` prefix used in the task.

### 4. Notification policy

```text
policy_receiver: quickticket-alerts
policy_group_by: ['alertname']
policy_group_wait: 30s
policy_group_interval: 5m
policy_repeat_interval: 5m
```

### 5. Runbook: QuickTicket High Error Rate

#### Alert

- **Fires when:** Gateway 5xx error rate is above 5% for 2 minutes.
- **Dashboard:** QuickTicket — Golden Signals.
- **Severity:** Critical.
- **Primary dependency to check:** Payments.

#### Diagnosis

1. Check which dependency is failing:

   ```bash
   curl -s localhost:3080/health | python3 -m json.tool
   ```

2. Check Payments directly:

   ```bash
   curl -s localhost:8082/health | python3 -m json.tool
   ```

3. Check Events directly:

   ```bash
   curl -s localhost:8081/health | python3 -m json.tool
   ```

4. Check container state:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps
   ```

5. Inspect recent Gateway and Payments errors:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs gateway --tail=20 --since=5m
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs payments --tail=20 --since=5m
   ```

6. Query the current error rate and SLO burn rate in Prometheus before making
   a change.

#### Common causes

| Cause | How to identify | Fix |
|---|---|---|
| Payments service stopped | Gateway health shows `payments: down`; container is exited | Start Payments and verify health |
| Payments failure injection enabled | Payments health is 200 but `failure_rate` is non-zero | Restore `PAYMENT_FAILURE_RATE=0.0` and recreate Payments |
| Events service stopped | Gateway health shows `events: down` | Start Events and check PostgreSQL and Redis |
| Database pool exhausted | Events logs contain pool errors | Check `DB_MAX_CONNS`, active load, and PostgreSQL before restarting Events |
| Upstream latency | Gateway logs show Payments or Events timeouts | Check dependency latency and `GATEWAY_TIMEOUT_MS` |

#### Mitigation

For the observed incident:

```bash
PAYMENT_FAILURE_RATE=0.0 docker compose \
  -f docker-compose.yaml \
  -f ../docker-compose.monitoring.yaml \
  up -d --no-deps --force-recreate payments
```

After applying the mitigation:

1. Verify Payments health reports `failure_rate: 0.0`.
2. Verify Gateway health returns HTTP 200.
3. Continue healthy traffic while the Prometheus windows recover.
4. Confirm both Grafana rules become `inactive` or `normal`.
5. Confirm resolved notifications are delivered.

#### Escalation

- Escalate to the instructor or TA if the incident is not mitigated within 10 minutes.
- Include the alert UID, timestamps, PromQL values, service health, Compose state, and recent logs.

### 6. Incident execution and evidence

Payments was first stopped to validate the dependency failure:

```text
Gateway health while Payments is stopped:
{"status":"degraded","checks":{"events":"ok","payments":"down","circuit_payments":"CLOSED"}}
http_code=503
```

It was then recreated with a 50% injected failure rate:

```text
{"status":"healthy","failure_rate":0.5,"latency_ms":0}
```

The controlled payment workload produced real 5xx responses:

```text
2026-09-27T19:42:38Z attempts=10 reserve_failures=0 pay_2xx=4 pay_5xx=6
2026-09-27T19:45:26Z attempts=90 reserve_failures=0 pay_2xx=46 pay_5xx=44
```

Observed rule transitions:

```text
2026-09-27T19:44:42Z error_rate_percent=26.9069 high_error_state=pending burn_rate_state=pending
2026-09-27T19:45:13Z error_rate_percent=25.7023 high_error_state=firing burn_rate_state=pending
2026-09-27T19:48:22Z error_rate_percent=4.1096 high_error_state=firing burn_rate_state=firing
2026-09-27T19:49:22Z error_rate_percent=1.1668 high_error_state=inactive burn_rate_state=firing
2026-09-27T19:50:22Z error_rate_percent=0.2213 high_error_state=inactive burn_rate_state=inactive
```

Webhook evidence:

```text
2026-09-27 19:45:40 | firing   | High Error Rate | critical
2026-09-27 19:48:45 | firing   | SLO Burn Rate   | warning
2026-09-27 19:50:40 | resolved | High Error Rate | critical
2026-09-27 19:53:45 | resolved | SLO Burn Rate   | warning
```

Final health verification:

```text
final_alert: name=High Error Rate state=inactive normal=True health=ok
final_alert: name=SLO Burn Rate state=inactive normal=True health=ok
final_error_rate_percent: 0
final_slo_burn_rate: 1.8228452774351018
final_payments_health: http_status=200 body={"status":"healthy","failure_rate":0.0,"latency_ms":0}
final_gateway_health: http_status=200 body={"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### 7. Incident timeline

| UTC time | Event |
|---|---|
| 19:42:11 | Payments failure injected; Gateway dependency failure confirmed |
| 19:42:38 | First workload sample showed 6 failed payments out of 10 |
| 19:44:42 | Both rules observed in Pending state |
| 19:45:13 | High Error Rate entered Firing |
| 19:45:40 | Critical firing webhook received; Payments failure injection confirmed as the cause |
| 19:45:46 | Payment failure rate restored to 0.0 |
| 19:48:22 | SLO Burn Rate entered Firing because the 30-minute window retained the incident errors |
| 19:48:45 | Warning firing webhook received |
| 19:49:22 | High Error Rate returned to inactive |
| 19:50:22 | Both rules were inactive; alert-state recovery complete |
| 19:50:40 | High Error Rate resolved webhook received |
| 19:53:45 | SLO Burn Rate resolved webhook received |

### 8. Alert-delay answer

The delay from failure injection at `19:42:11Z` to the High Error Rate firing
at `19:45:13Z` was **182 seconds (3 minutes 2 seconds)**.

The delay was expected. The PromQL query had to collect enough samples for its
five-minute rate window, the condition was evaluated once per minute, and it
had to remain above the threshold throughout the configured two-minute pending
period. The firing webhook arrived 27 seconds later, consistent with the
30-second notification group wait.

## Task 2 — Blameless Postmortem

# Postmortem: QuickTicket Payment Failure Injection

**Date:** 2026-09-27

**Duration:** 19:42:11Z–19:50:22Z (8 minutes 11 seconds until both alerts
returned inactive; service health was restored at 19:45:46Z)

**Severity:** SEV-3

**Author:** Gleb Shvetsov

### Summary

The Payments service was deployed with a 50% synthetic charge failure rate.
Gateway payment requests consequently returned 5xx responses and consumed the
availability error budget rapidly. Event browsing remained available, while
44 of 90 controlled payment attempts failed before mitigation.

### Impact

- Payment attempts were intermittently unavailable.
- The observed Gateway 5xx rate peaked above 26%.
- Both the short-window critical alert and the SLO burn-rate warning fired.
- Read-only event traffic remained available.
- No persistent data was deleted and no credentials were exposed.

### Timeline

| UTC time | Event |
|---|---|
| 19:42:11 | Failure injected and first dependency symptom observed |
| 19:42:38 | Controlled workload confirmed payment 5xx responses |
| 19:44:42 | Alert conditions observed Pending |
| 19:45:13 | Critical error-rate alert fired |
| 19:45:40 | Critical notification received; Payments failure injection confirmed as cause |
| 19:45:46 | Configuration restored to zero failure rate |
| 19:48:22 | Long-window SLO warning fired while historical failures remained in its window |
| 19:49:22 | Critical rule became inactive |
| 19:50:22 | Both rules inactive; alert-state recovery confirmed |
| 19:53:45 | Final resolved notification delivered |

### Root cause

The runtime configuration allowed a non-production-safe fault-injection value
to be applied directly to the Payments service. The Gateway synchronously
depends on successful charge responses, so injected Payments HTTP 500 responses
propagated to clients as Gateway 5xx responses. There was no deployment-time
guard preventing a non-zero failure rate outside an explicit test context.

### Contributing factors

- Fault-injection configuration and normal runtime configuration shared the same deployment path.
- Payment failures affected the user-facing synchronous request path.
- The five-minute and 30-minute PromQL windows intentionally retained failure history after the fix.
- The load generator's normal traffic mix contains relatively few charge requests, so targeted payment traffic was required for deterministic validation.

### What went well

- The critical alert fired in approximately three minutes, matching its design.
- The webhook notification arrived 27 seconds after the rule entered Firing.
- The runbook immediately directed investigation toward Payments.
- The failure was reversible through configuration without a data rollback.
- Both firing and resolved notifications were captured for both rules.

### What went wrong

- The deployment accepted a dangerous fault-injection value without validation.
- A 30-minute burn-rate window continued to report the incident after the immediate cause was fixed.
- The standard load mix initially diluted payment failures and was not deterministic enough for the exercise.
- The alert display names initially omitted the `QuickTicket` prefix and required normalization before submission.

### Action items

| Action | Owner | Priority |
|---|---|---|
| Reject non-zero `PAYMENT_FAILURE_RATE` outside explicitly marked test deployments | Gleb Shvetsov | High |
| Add a Payments-specific charge failure ratio alert to avoid dilution by unrelated Gateway traffic | Gleb Shvetsov | High |
| Add a safe fault-injection profile and deterministic incident-test script | Gleb Shvetsov | Medium |
| Link alert annotations directly to the runbook and Golden Signals dashboard | Gleb Shvetsov | Medium |
| Document expected recovery behavior for five-minute and 30-minute PromQL windows | Gleb Shvetsov | Low |

### Most important action item

The most important action is to reject non-zero `PAYMENT_FAILURE_RATE` values
outside an explicitly marked test deployment. This guard prevents the failure
mode before it reaches production, rather than relying solely on detection and
response after user-visible errors begin.

## Bonus Task — Cross-Test Runbook

### Runbook: QuickTicket Redis Unavailable

#### Alert and symptoms

- Gateway health is degraded because Events reports Redis as unavailable.
- New reservations can time out or return 5xx.
- Read-only event listing may remain available because it uses PostgreSQL.
- Dashboard: QuickTicket — Golden Signals.

#### Preconditions

- Run commands from `app/`.
- Always include `docker-compose.yaml` and `../docker-compose.monitoring.yaml`.
- Do not delete containers, volumes, or data while diagnosing.

#### Diagnosis

1. Check Gateway health:

   ```bash
   curl -sS localhost:3080/health
   ```

2. Check Events health:

   ```bash
   curl -sS localhost:8081/health
   ```

   Events caches Redis health briefly. An in-flight check can temporarily
   return stale `redis=ok`; repeat after five seconds and cross-check the
   Compose state and direct Redis ping.

3. Check service state:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml ps redis events gateway
   ```

4. Test Redis directly:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec -T redis redis-cli ping
   ```

5. Inspect recent logs:

   ```bash
   docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml logs --since=5m --tail=40 events redis
   ```

6. Confirm reservation-path impact:

   ```bash
   curl -sS -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' localhost:3080/events/3/reserve
   ```

#### Common causes

| Cause | Identification | Mitigation |
|---|---|---|
| Redis stopped | Compose shows Exited; direct ping cannot run | Start existing Redis container |
| Redis unhealthy | Running container but PING fails | Inspect logs and recreate only Redis if required |
| Wrong Redis endpoint | Events logs show DNS or connection errors | Restore host `redis` and port `6379`; recreate Events |
| Network isolation | Redis PING works locally but Events cannot connect | Inspect Compose networks |

#### Mitigation

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml start redis
```

Wait at least five seconds, then verify:

```bash
docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml exec -T redis redis-cli ping
curl -sS localhost:8081/health
curl -sS localhost:3080/health
```

Repeat a reservation request and require a 2xx response. Do not treat one
cached Events health response as sufficient recovery proof.

#### Escalation

- Escalate to the instructor or TA if Redis cannot be restored within 10 minutes.
- Include the timeline, Compose state, Redis ping, health responses, and logs.

#### Success criteria

- Redis PING returns `PONG`.
- Events reports PostgreSQL and Redis as `ok`.
- Gateway returns HTTP 200.
- A reservation request succeeds.

### Cross-test result

```text
test_type: classmate runbook test
bonus_test_started_utc: 2026-09-27T20:30:00Z
failure_injected_utc: 2026-09-27T20:30:07Z
root_cause_identified_utc: 2026-09-27T20:31:12Z
recovered_utc: 2026-09-27T20:31:35Z
time_to_identify_seconds: 65
total_resolution_seconds: 95
tester_result: success
```

Failure evidence:

```text
Gateway: HTTP 503, events=down, payments=ok
Redis container: Exited (0)
redis-cli: service "redis" is not running
Reservation request: HTTP 504, Events service timeout
```

Recovery evidence:

```text
Redis PING: PONG
Events: {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
Gateway: {"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
Post-recovery reservation: HTTP 200
```

### Tester feedback and runbook update

1. Explicitly stating the working directory and both Compose files prevented path mistakes.
2. The Events health endpoint briefly returned stale `redis=ok` during the failure, so the runbook now requires a retry plus Compose and Redis PING confirmation.
3. Clarifying that read-only event listing may remain healthy prevented an incorrect conclusion that Redis was not involved.
4. The success criteria now require a real reservation request rather than health endpoints alone.

## Final verification

- Both required alert rules were provisioned successfully.
- Notification policy and webhook contact point were verified through the API.
- Critical and warning firing notifications were received.
- Both resolved notifications were received.
- Payments was restored to a zero failure rate.
- Gateway and all dependencies returned healthy status.
- The Redis bonus scenario was restored successfully.
- No credentials or webhook tokens are included in committed files.

## Conclusion

Lab 6 established SLO-aware Grafana alerting, external notifications, a
practical incident runbook, and a complete detection-to-recovery workflow. The
controlled Payments incident demonstrated pending periods, notification group
wait, rolling-window recovery, and resolved notifications. The blameless
postmortem converted those observations into preventive action items. The
additional Redis cross-test exposed a transient health-cache ambiguity and led
to a concrete runbook improvement requiring independent service-state and PING
verification.
