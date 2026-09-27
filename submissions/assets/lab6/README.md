# Evidence

- `alert-rules.json`: live Grafana rule definitions exported after configuration.
- `contact-point.json`, `notification-policy.json`: webhook and routing configuration.
- `notifications.jsonl`: actual receiver receipt timestamps and unmodified Grafana payloads.
- `observations.jsonl`: Grafana state API and actual Prometheus query sampled every 10s.
- `state-*.json`: first observed snapshot for each state (Grafana API `Alerting` means UI `Firing`).
- `timeline.jsonl`: UTC timestamps captured at actions and state changes.
- `diagnosis-*`: health, container status and logs captured by runbook execution.
- `loadgen.log`: supplied load generator output; its failures include 4xx and are not the SLO 5xx percentage.
- `compose.log`: injection/recovery command output.

NoData/TestAlert payloads are distinct from the actual High Error Rate incident notification.
