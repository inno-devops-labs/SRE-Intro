# Lab 6

## Task 1. Alerting + Incident Response

### Alert Rules

#### 1. QuickTicket High Error Rate

PromQL:

```promql
100 * sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) > 5
```

Grafana rule settings:

- Evaluation interval: `30s`
- For: `2m`
- Folder: `Lab 6`

#### 2. QuickTicket SLO Burn Rate

PromQL:

```promql
(sum(rate(gateway_requests_total{status=~"5.."}[30m])) / sum(rate(gateway_requests_total[30m]))) / 0.001 > 6
```

Grafana rule settings:

- Evaluation interval: `30s`
- For: `5m`
- Folder: `Lab 6`

### Contact Point

Type: `webhook`

Receiver:

```text
quickticket-alerts -> http://webhook-receiver:8080/
```

Notification evidence from `webhook-receiver` logs:

```text
2025-10-28T14:50:35Z status=firing
2025-10-28T14:53:10Z status=firing
2025-10-28T14:57:35Z status=resolved
2025-10-28T14:59:35Z status=resolved
```

### Runbook

1. Check which rule fired in Grafana Alerting and read the labels/annotations.
2. Verify service health in Docker:

```bash
docker compose ps payments
```

3. Confirm the target is down from metrics:

```promql
up{job="payments"}
```

Expected unhealthy value: `0`.

4. Check whether gateway errors are increasing:

```promql
100 * sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m]))
```

5. Restore the failed service:

```bash
docker compose up -d payments
```

6. Keep traffic running and wait until Grafana moves the rule from `firing` back to `normal`.

### Incident Simulation

Traffic generation:

```bash
docker compose exec -T loadgen sh -lc 'loadgen -c 10 -r 5 -t 10m http://gateway:8080'
```

Failure injection:

```bash
docker compose stop payments
```

Diagnosis:

```bash
docker compose ps payments
```

Observed result: `payments` was stopped, `up{job="payments"} = 0`, and gateway 5xx rate increased enough to trigger both alert rules.

Recovery:

```bash
docker compose up -d payments
```

### Alert Evidence

Grafana API state during incident:

```text
QuickTicket High Error Rate -> firing at 2025-10-28T14:50:35Z
QuickTicket SLO Burn Rate -> firing at 2025-10-28T14:53:10Z
```

Grafana API state after recovery:

```text
QuickTicket High Error Rate -> normal at 2025-10-28T14:57:35Z
QuickTicket SLO Burn Rate -> normal at 2025-10-28T14:59:35Z
```

### Timeline

```text
2025-10-28T14:46:39Z  injected failure: stopped payments
2025-10-28T14:50:35Z  QuickTicket High Error Rate -> firing
2025-10-28T14:52:09Z  diagnosed: payments confirmed down via docker compose ps
2025-10-28T14:53:10Z  QuickTicket SLO Burn Rate -> firing
2025-10-28T14:54:22Z  fixed: started payments
2025-10-28T14:57:35Z  QuickTicket High Error Rate -> resolved
2025-10-28T14:59:35Z  QuickTicket SLO Burn Rate -> resolved
```

### Why the Alert Did Not Fire Immediately

The alerts were intentionally delayed by design:

- `High Error Rate` uses `rate(...[5m])` and `for: 2m`, so Grafana needed both enough failed traffic in the 5-minute window and 2 minutes of continuous breach before switching to `firing`.
- `SLO Burn Rate` uses a longer `rate(...[30m])` window and `for: 5m`, so it fired later and also stayed active longer after recovery because the 30-minute window still contained the earlier failures.
