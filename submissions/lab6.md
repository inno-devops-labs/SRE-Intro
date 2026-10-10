# Lab 6 — Alerting & Incident Response

Date: 2026-09-27. Author: Walkerino. Environment: k3d Kubernetes
v1.33.6-k3s1, Prometheus v3.11.2, Grafana 13.0.1. Availability SLO: **99.5%**
over 30 days. This report documents an actual incident in the training cluster.

## Task 1 — Alerting configuration

The course moves to Kubernetes from Lab 4. This exercise monitors that same
QuickTicket deployment, rather than the second Compose deployment shown in
some Lab 6 commands. Reproduce with [bootstrap-lab6.sh](../scripts/bootstrap-lab6.sh);
configuration and operational notes are in [monitoring/lab6](../monitoring/lab6/README.md).

### Rule 1: QuickTicket High Error Rate

```promql
(
  (sum(rate(gateway_requests_total{status=~"5.."}[5m]))
   or 0 * sum(rate(gateway_requests_total[5m])))
  / sum(rate(gateway_requests_total[5m]))
) * 100
```

Grafana-managed rule, condition **above 5**, evaluate every **1m**, pending **2m**,
`severity=critical`. The percentage includes health probes, matching the Lab 3
instrumentation. `/metrics` is excluded by the application.

### Rule 2: QuickTicket SLO Burn Rate

```promql
(
  (sum(rate(gateway_requests_total{status=~"5.."}[30m]))
   or 0 * sum(rate(gateway_requests_total[30m])))
  / sum(rate(gateway_requests_total[30m]))
) / (1 - 0.995)
```

Condition **above 6**, evaluate every **1m**, pending **5m**,
`severity=warning`. This is mathematically the error-ratio form of the
specification's `(1 - availability) / error_budget` query. At sustained 6x burn,
a 30-day budget would be exhausted in five days under constant request rate.

Both rules use an instant Prometheus query and a Grafana threshold expression.
The zero fallback is scoped to existing traffic: a missing 5xx label series
means zero errors; missing scrapes or no traffic do **not** become 100% success.
NoData is configured as NoData and query errors as Error. A 30m rate can
calculate with two or more samples before 30m elapses, but its early result
represents only available history, not a full 30-minute baseline.

Full provisioned definitions: [alerts.yml](../monitoring/lab6/alerts.yml).
Live exported definitions: [rules.json](evidence/lab6/rules.json).

### Contact point and routing

`quickticket-alerts` uses a **webhook** at
`http://alert-receiver:8080/alerts` inside the cluster. The receiver records the
actual JSON POST and its UTC receipt time in pod logs. This tests the whole
Grafana notification path without sending messages to another person.

Default policy: group by `alertname`, wait **30s**, group interval **1m**,
repeat every **5m**, resolved notifications enabled.
[Contact point](evidence/lab6/contact-points.json), [policy](evidence/lab6/policy.json).

```bash
python3 scripts/lab6-grafana.py test-contact
kubectl logs deploy/alert-receiver
```

```json
{"status":"success","duration":"1ms"}
```

The test arrived at **11:00:18.322 UTC** with `alertname=Lab6ContactPointTest`.
[Actual receiver output](evidence/lab6/webhook-test.jsonl).
The helper uses the Grafana 13 receiver-test API; the legacy test endpoint
returns HTTP 410 and was replaced, without changing the exercise thresholds.

## Runbook: QuickTicket High Error Rate

### Alert

- Fires when aggregate gateway 5xx exceeds 5% for 2m; evaluation every 1m.
- Dashboard: [QuickTicket — Golden Signals](http://localhost:3000/d/quickticket-golden-signals).
- Open Grafana using `kubectl port-forward svc/grafana 3000:3000`.
- Scope: synthetic QuickTicket training cluster, default namespace.

### Diagnosis

1. Record UTC time (`date -u`) and inspect `python3 scripts/lab6-grafana.py`.
   Preserve the firing state and webhook logs before changing anything.
2. Check `kubectl get pods` and `kubectl get endpoints gateway events payments`.
   Healthy pods do not guarantee successful checkout transactions.
3. Query gateway health from its pod (no local port-forward is required):

   ```bash
   kubectl exec deploy/gateway -- python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8080/health").read().decode())'
   ```

4. Query dependencies directly, including the **failure_rate** field:

   ```bash
   kubectl exec deploy/payments -- python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8082/health").read().decode())'
   kubectl exec deploy/events -- python -c 'import urllib.request; print(urllib.request.urlopen("http://localhost:8081/health").read().decode())'
   kubectl get deploy/payments -o jsonpath='{.spec.template.spec.containers[0].env}'
   ```

   If urllib reports HTTP 503, that is a degraded health result; inspect logs
   and endpoints rather than interpreting the command failure as missing evidence.
5. Correlate service logs and real checkout responses:

   ```bash
   kubectl logs deploy/gateway --tail=40 --since=5m
   kubectl logs deploy/payments --tail=40 --since=5m
   kubectl logs deploy/events --tail=40 --since=5m
   kubectl logs deploy/lab6-loadgen --tail=20
   kubectl exec deploy/postgres -- pg_isready -U quickticket -d quickticket
   kubectl exec deploy/redis -- redis-cli ping
   ```

| Cause | Evidence | Mitigation |
|---|---|---|
| Payment failure injection | payments health 200, `failure_rate > 0`, injected failures in logs, `/pay` 500 | Revert the faulty env-var commit in Git |
| Payments unavailable | No ready endpoints, pod events, connection errors | Correct image/config/resources in Git; await rollout |
| Events or DB unhealthy | events health 503, pool errors, pg_isready fails | Restore dependency/config; investigate connection saturation before restart |
| Excess payment latency | `latency_ms`, gateway 504 and duration spike | Revert latency/timeout change in Git; verify real checkout |
| No alert despite broken payments | Little payment traffic or failed reservations | Check loadgen responses and inventory; restore valid checkout traffic |

### Mitigation and verification

Determine the Application's source branch before modifying anything:

```bash
argocd app get quickticket
git log --oneline -5 -- k8s/payments.yaml
```

In the checkout for that branch, revert **the identified faulty commit**, not
an arbitrary latest commit, and push. During this exercise the branch was
`experiment/lab6-payments` and the injection commit was `19f056b`:

```bash
git revert 19f056b --no-edit
git push origin experiment/lab6-payments
argocd app get quickticket --hard-refresh
argocd app wait quickticket --sync --health --timeout 120
```

Do not use `kubectl set env` as a durable fix with ArgoCD selfHeal enabled: Git
would restore the bad value. Also do not restart everything indiscriminately;
it can erase diagnostics and introduce unrelated availability problems.

Recheck payments `failure_rate=0.0`, successful purchases and declining 5xx.
Keep traffic running until the alert returns Normal (`inactive` in the API)
and the receiver logs `status=resolved`. A healthy pod is only the first step:
the 5m/30m windows can retain errors after service recovery. Record recovery
and alert resolution separately. Restore the Application source to
`feature/lab5` when the isolated exercise is complete.

### Escalation

If no cause or mitigation is found within 10 minutes, involve the course
instructor/TA and the application owner. Include UTC timeline, alert values,
recent config commits, pod events and redacted logs. Escalate immediately if
payments succeed but order confirmation fails; do not blindly retry charges.

## Failure injection and evidence

The load generator performs one read, one reservation and one payment per loop
against dedicated event 6006 (1,000,000 synthetic tickets). It avoids stock
exhaustion and produces roughly 16.7% aggregate errors at 50% failed payments,
so the original 5% threshold is meaningful. The measured rate is subject to
randomness and health-probe dilution.

The failure is an isolated Git change, reconciled by ArgoCD. The experiment
branch preserves the exact injection and its revert without leaving a broken
configuration in the submission branch. UTC timestamps and Grafana state
transitions are recorded in [incident-events.jsonl](evidence/lab6/incident-events.jsonl)
and [alert-timeline.jsonl](evidence/lab6/alert-timeline.jsonl).

### Observed timeline (UTC)

| Timestamp | Event |
|---|---|
| 2026-09-27T11:01:13.409166+00:00 | Injection started: Git commit sets failure rate to 0.5 |
| 2026-09-27T11:01:23.449623+00:00 | Faulty payments rolled out; ArgoCD remains Healthy |
| 2026-09-27T11:03:10Z | Critical condition entered Pending |
| 2026-09-27T11:05:10Z | Critical rule evaluated Firing |
| 2026-09-27T11:05:45.015534+00:00 | Firing webhook received |
| 2026-09-27T11:05:46.682459+00:00 | Runbook investigation started |
| 2026-09-27T11:05:47.851169+00:00 | Root cause identified |
| 2026-09-27T11:05:47.851467+00:00 | Git revert started |
| 2026-09-27T11:05:58.579549+00:00 | Revert deployed; payments failure_rate=0.0 |
| 2026-09-27T11:10:10Z | Critical alert returned Normal |
| 2026-09-27T11:10:45.027011+00:00 | Resolved webhook received |

Grafana was sampled every 10s. The table uses its `lastEvaluation` timestamps
for state transitions and the receiver's clock for delivery; observation time
can be up to a polling interval later. Complete JSON is linked above.

### Firing and recovery evidence

```text
firing.json
  QuickTicket High Error Rate: state=firing, health=ok
  QuickTicket SLO Burn Rate: state=pending, health=ok
resolved.json
  QuickTicket High Error Rate: state=inactive, health=ok
  QuickTicket SLO Burn Rate: state=firing, health=ok
```

[Firing rule response](evidence/lab6/firing.json),
[resolved rule response](evidence/lab6/resolved.json),
[actual webhook POSTs](evidence/lab6/webhook-complete.jsonl).
`inactive` is the Prometheus-compatible API representation of Grafana's Normal.
The 30m warning can still be Firing after the 5m critical alert resolves;
this is retained historical error budget burn, not a failed mitigation.
We did not shorten the windows, silence the alert or remove its errors.

Diagnosis and mitigation:

```text
payments before: {"status":"healthy","failure_rate":0.5,"latency_ms":0}
payments after:  {"status":"healthy","failure_rate":0.0,"latency_ms":0}
fe7e973 Revert "test(lab6): inject 50 percent payment failures in training cluster"
19f056b test(lab6): inject 50 percent payment failures in training cluster
8648fea Revert "test(lab5): deploy nonexistent gateway tag for rollback exercise"
```

[Gateway health](evidence/lab6/diagnosis-gateway-health.json),
[events health](evidence/lab6/diagnosis-events-health.json),
[payments logs](evidence/lab6/diagnosis-payments-logs.txt),
[failed purchases](evidence/lab6/diagnosis-load.jsonl),
[healthy purchases after fix](evidence/lab6/load-after-fix.jsonl),
[ArgoCD recovery](evidence/lab6/argocd-fix-healthy.txt).

### How long from injection to firing, and why?

**236.59 seconds (3.94 minutes)** from starting the injected
Git change to the Firing evaluation. This includes GitOps rollout, 15s scrapes,
the rising 5m error ratio reaching 5%, alignment to a 1m evaluation, and the
full 2m pending period. Webhook delivery adds the configured 30s grouping wait
plus scheduler overhead; it is distinct from detection time. After the fix,
old errors must age out enough for the rolling ratio to fall below 5%, so
successful new purchases precede alert resolution.

## Task 2 — Blameless postmortem

### Payment failures hidden behind healthy readiness

**Date:** 2026-09-27. **Author:** Walkerino.
**Severity:** SEV-2 for the simulated checkout degradation (training only;
no real customers or payments).
**Impact interval:** 11:01:13–11:05:58 UTC,
approximately 285.2s; this bounds injection initiation through verified
mitigation rollout. Alert clearing is recorded separately above.

### Summary and impact

The payments deployment accepted a configuration that failed approximately
half of charge attempts while reporting itself healthy. Reads and reservations
continued, but affected checkout requests returned HTTP 500 through the gateway.
Prometheus `increase()` estimates **486.5 gateway 5xx out of 3087.2
requests (15.76%)** over the injection-to-mitigation interval.
These are extrapolated counter estimates, not exact billing counts; raw
queries and evaluation timestamps are in [incident-metrics.json](evidence/lab6/incident-metrics.json).

### Timeline

The UTC table above is the incident timeline, including injection, alerting,
investigation, root cause, mitigation, service recovery and alert resolution.
It is derived from recorded events, Grafana API state and receiver logs.

### Root cause

The deployment/configuration process permits `PAYMENT_FAILURE_RATE=0.5` without
an environment-specific guardrail. Payments readiness validates process
availability but not transaction success; gateway dependency health therefore
also remains green while users experience failed checkouts. The intentional
fault exposed this separation between infrastructure health and the
transaction SLI. There was no deployment gate checking checkout error rate.

The gateway propagates the payments HTTP 500; it does **not** convert these
particular failures into 502. Blaming the person changing an environment
variable would miss the missing config validation and transaction-level gate.
We cannot infer a monthly budget percentage consumed without a defensible
30-day request-volume denominator, so none is invented.

### What went well

- Both Grafana-managed rules evaluated successfully, and the actual critical
  firing and resolved notifications reached the webhook receiver.
- The runbook's direct payments health/config check distinguished fault
  injection from an unavailable service even though readiness was green.
- A single Git revert restored the configuration through ArgoCD; no manual
  cluster drift, threshold reduction or alert silencing was required.
- A dedicated stocked event kept the checkout load representative throughout.

### What went wrong / opportunities

- Aggregating all gateway requests dilutes checkout failures with successful
  reads and health probes. The default lab load can miss this incident.
- Readiness and ArgoCD Healthy alone did not reveal failed transactions.
- The long-window burn warning outlives the acute incident; responders need
  to distinguish service recovery from error-budget recovery.
- The legacy Grafana contact-test endpoint was removed in version 13;
  the test helper needed the supported receiver API before injection.

### Action items

| Action | Owner | Priority | Acceptance criterion |
|---|---|---|---|
| Reject nonzero payment fault injection outside explicitly designated exercise environments in deployment validation | Walkerino, application owner | High | A config with rate 0.5 is rejected in normal deployment CI and allowed only in a named test environment |
| Add a checkout-specific SLI/alert with sufficient-traffic gating | Walkerino, SRE role | High | The 50% payments experiment fires even under the normal read-heavy traffic mix |
| Add a post-deployment reserve/pay smoke check | Walkerino, CI owner | High | A bad payments rollout is detected before being treated as successful delivery |
| Make injected failure/latency visible on the operational dashboard and keep the health-field check in the runbook | Walkerino, observability owner | Medium | A responder can identify the configured injection without searching pod environment manually |
| Arrange a blinded Redis runbook test with a classmate | Walkerino + volunteer classmate | Medium | Record actual duration and feedback, then update the runbook |

**Most important action:** validate fault-injection settings at deployment
boundaries. This prevents a deliberately destructive test knob from reaching
a normal environment while preserving explicit, isolated training experiments;
alerts alone can only report impact after requests fail.

### Validation and scope

Prometheus configuration passed `promtool check config`; all Kubernetes
resources passed server-side dry-run; the helper/receiver/loadgen compiled
and provisioning YAML parsed. Live Grafana definitions, webhook traffic and
successful/failed purchases are recorded in evidence files.

- [x] Task 1: two alerts, tested contact point, runbook, real incident and resolution.
- [x] Task 2: blameless postmortem with assigned, verifiable actions.
- [ ] Bonus: second runbook below; no classmate cross-test is claimed.


## Bonus — Second runbook: Redis unavailable

This runbook is provided, but **peer testing has not been performed**. The
bonus requires a real classmate; an automated execution is not a substitute,
and no peer result or feedback is claimed.

### Alert and impact

Reservation calls fail while listing events may continue to work. The gateway
or events readiness may also fail because Redis is a critical dependency;
therefore do not rely solely on aggregate 5xx (traffic may stop at the Service).
Start with endpoint/readiness checks and reservation errors.

### Diagnosis

```bash
kubectl get pods -l app=redis
kubectl get endpoints redis events gateway
kubectl describe pod -l app=redis
kubectl logs deploy/redis --tail=40 --since=5m
kubectl logs deploy/events --tail=40 --since=5m
kubectl exec deploy/redis -- redis-cli ping
kubectl get deploy/events -o jsonpath='{.spec.template.spec.containers[0].env}'
```

Expected healthy Redis response: `PONG`. If the Redis pod is absent, inspect
the Deployment's desired replica count, image and events. If Redis is running
but events cannot connect, verify `REDIS_HOST`, Service selector/port, endpoints
and network policy. Do not flush Redis or delete DB data as a repair.

### Mitigation

Find the responsible change in the active GitOps branch and revert it. If the
Redis manifest is healthy and only one pod is stuck, preserve logs, then delete
only that pod so its Deployment recreates it. Wait for Redis and events rollout
readiness, query gateway health, and perform a reservation followed by payment
using the dedicated test event. Previously lost/expired reservations may not
be recoverable; do not promise that restarting Redis restores them.

### Escalation and peer exercise

Escalate to instructor/TA if still unresolved after 10 minutes; include Redis
pod events, endpoints and events connection errors. For a future peer test,
give only this runbook to a classmate, inject a Redis fault in an isolated
GitOps branch, record their start/end time and ambiguities, then update this
section with their actual feedback. **No bonus points are claimed yet.**

## References

- [Grafana file provisioning](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/)
- [Grafana 13 API migration](https://grafana.com/whats-new/2026-04-07-legacy-alertmanager-configuration-api-endpoints-changed/)
- [ArgoCD self-healing](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/)

## Final cleanup

After the critical alert and its resolved notification were captured, the
Application source was restored to `feature/lab5` and verified Synced/Healthy.
The load generator was scaled to zero; no fault-injection setting remains.
The monitoring stack stays available for review. The 30m burn warning retained
the past incident at cleanup time, as shown in the recorded state; it was not
silenced or falsely reported as recovered. [Final pods](evidence/lab6/final-pods.txt).
