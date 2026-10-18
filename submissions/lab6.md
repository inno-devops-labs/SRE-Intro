# Lab 6 — Alerting & Incident Response

> **Platform note: read this first.**
> The lab text runs everything through
> `docker compose -f docker-compose.yaml -f ../docker-compose.monitoring.yaml`. By Lab 4
> QuickTicket had already moved to the k3d cluster `quickticket`, and Lab 5 put it under
> ArgoCD. So the Compose stack from labs 1-3 doesn't exist anymore. Every step below was done
> against the cluster instead:
>
> | Lab step | Lab's Compose version | What was actually done |
> |---|---|---|
> | 6.1 start the stack | `docker compose up -d --build` | already running in k3d ns `default`; monitoring stack deployed into a new ns `monitoring` |
> | 6.1 loadgen | `./loadgen/run.sh 3 300 &` | `monitoring/k8s/loadgen.yaml`: same 70/20/10 read/reserve/purchase mix, in-cluster, continuous |
> | 6.6 inject failure | `PAYMENT_FAILURE_RATE=0.5 docker compose up -d payments` | `PAYMENT_FAILURE_RATE: "1.0"` committed to `k8s/payments.yaml` and **shipped through ArgoCD** (see §6.6 for why `kubectl set env` does not work any more) |
> | runbook commands | `docker compose logs …` | `kubectl logs …` |
>
> Prometheus and Grafana had to go **inside** the cluster: the Lab 3 Compose Prometheus scrapes
> `gateway:8080` over the Docker-compose network, and it can't reach pod IPs in the k3d bridge
> network. I reused the Lab 3 artefacts instead of rewriting them, the same
> `monitoring/prometheus/rules.yml` recording rules and the same
> `monitoring/grafana/dashboards/golden-signals.json` dashboard are just mounted into the
> in-cluster Grafana.
>
> **Every command output in this file is real output from this run.** Times are MSK (UTC+3).
> Grafana and Kubernetes log in UTC, so `08:32:00Z` = `11:32:00 MSK`.

---

## What this lab added to the repo

```text
monitoring/k8s/prometheus.yaml                                 # in-cluster Prometheus
monitoring/k8s/grafana.yaml                                    # in-cluster Grafana 13.0.1
monitoring/k8s/webhook-receiver.yaml                           # alert notification sink
monitoring/k8s/loadgen.yaml                                    # continuous traffic generator
monitoring/k8s/deploy.sh                                       # one-shot deploy of the above
monitoring/grafana/provisioning/alerting/alert-rules.yaml      # the two SLO alert rules
monitoring/grafana/provisioning/alerting/contact-points.yaml   # quickticket-alerts
monitoring/grafana/provisioning/alerting/notification-policies.yaml
monitoring/grafana/provisioning/datasources/datasources.yml    # (modified) pinned datasource uid
```

Deployed with:

```console
$ ./monitoring/k8s/deploy.sh
deployment.apps/prometheus configured
service/prometheus configured
configmap/prometheus-rules created
configmap/grafana-datasources created
configmap/grafana-dashprovider created
configmap/grafana-alerting created
configmap/grafana-dashboards created
configmap/webhook-receiver-code created
deployment.apps/webhook-receiver created
service/webhook-receiver created
deployment.apps/grafana created
service/grafana created
deployment.apps/loadgen created
deployment "prometheus" successfully rolled out
deployment "grafana" successfully rolled out
deployment "webhook-receiver" successfully rolled out
deployment "loadgen" successfully rolled out
```

```console
$ kubectl get pods -n monitoring
NAME                                READY   STATUS    RESTARTS   AGE
grafana-69f47b978c-lxmp6            1/1     Running   0          3m53s
prometheus-5dfb495b54-mgwpt         1/1     Running   0          5m46s
webhook-receiver-55dcfbd45c-25vxh   1/1     Running   0          5m46s

$ curl -s 'http://localhost:9090/api/v1/targets?state=active' | jq -r \
    '.data.activeTargets[] | "\(.labels.job)\t\(.scrapeUrl)\t\(.health)"'
events	http://10.42.0.88:8081/metrics	up
gateway	http://10.42.0.87:8080/metrics	up
payments	http://10.42.0.86:8082/metrics	up
```

---

## Task 1 — Create Alerts & Respond to an Incident (6 pts)

### 6.1 Stack + background traffic

The load generator (`monitoring/k8s/loadgen.yaml`) reproduces `app/loadgen/run.sh`'s mix
in-cluster: 70% `GET /events`, 20% reserve, 10% full reserve→pay purchase, at ~1 iteration per
second. It runs for the whole lab instead of just 300 seconds.

Steady state before the incident:

```console
$ curl -s -G http://localhost:9090/api/v1/query \
    --data-urlencode 'query=sum by (status,path) (rate(gateway_requests_total[10m]))' \
  | jq -r '.data.result[] | "\(.metric.status)\t\(.metric.path)\t\(.value[1])"' | sort
200	/events	0.4860513882352941
200	/events/{id}/reserve	0.2076152142857143
200	/health	0.14511933333333335
200	/openapi.json	0
200	/reserve/{id}/pay	0.06920507142857144
503	/health	0.005062302325581396
```

Worth noticing before I even start: the **baseline is not zero**. About 3 requests per
10 minutes fail, all `503` on `/health` (the gateway's readiness probe catching a dependency
being slow for a moment). That's a ~0.5% error rate, a **burn rate of ~1.0** against the 99.5%
SLO. So QuickTicket already spends its whole error budget just sitting idle. I didn't expect
that, and it turned into action item A4.

> **One deliberate addition to the load generator.** The events service increments
> `event:<id>:held` in Redis on every reservation and only decrements it on *confirmation*.
> So an expired reservation leaks its hold forever. Over a multi-hour load test every event
> sells out, reserve starts returning `409`, and the purchase path stops generating traffic,
> which would quietly ruin the experiment. A `reaper` sidecar clears the leaked holds every
> 60 s. **The leak itself is a real application bug I found while building this lab**, action
> item A5.
>
> A second load-harness bug bit me mid-lab, and it's worth writing down because it's the kind
> of thing that quietly invalidates a measurement without telling you. The first version of
> the script derived the event id from the same counter as the request-type selector, so the
> two stayed in lockstep and **every** purchase hit event 5. After 80 confirmed orders event 5
> sold out, reserve started returning `409`, and the pay traffic vanished entirely. The Bonus
> Task's fault was live for 10 minutes before I noticed there were no requests left for it to
> break. I fixed it by pinning the purchase flow to the highest-capacity event and rotating
> reads and reserves independently. A load generator that can quietly stop generating the load
> you care about is its own kind of monitoring blind spot.

### 6.2 Contact point

`monitoring/grafana/provisioning/alerting/contact-points.yaml`:

```yaml
apiVersion: 1

contactPoints:
  - orgId: 1
    name: quickticket-alerts
    receivers:
      - uid: quickticket-webhook
        type: webhook
        settings:
          url: http://webhook-receiver.monitoring.svc.cluster.local:8090/alerts
          httpMethod: POST
        disableResolveMessage: false
```

**Type:** Webhook. Instead of webhook.site, the target is a 30-line HTTP server running
*inside the cluster* (`monitoring/k8s/webhook-receiver.yaml`) that pretty-prints every POST
body to stdout. That way the evidence is reproducible with `kubectl logs` and doesn't depend
on a third-party site. It also means it can actually be shown in a PR, unlike webhook.site.

```console
$ curl -s -u admin:admin http://localhost:3000/api/v1/provisioning/contact-points \
  | jq -r '.[] | "\(.name)\t\(.type)\t\(.settings.url // "-")"'
quickticket-alerts	webhook	http://webhook-receiver.monitoring.svc.cluster.local:8090/alerts
```

**Test** (Grafana UI → Alerting → Contact points → quickticket-alerts → **Test** →
*Send test notification*; Grafana answered *"Test notification sent successfully"*):

```console
$ kubectl logs -n monitoring deploy/webhook-receiver
webhook-receiver listening on :8090
===== 2026-09-22T08:19:51Z POST /alerts =====
{
  "alerts": [
    {
      "annotations": {
        "summary": "Notification test"
      },
      "fingerprint": "57c6d9296de2ad39",
      "labels": {
        "alertname": "TestAlert",
        "instance": "Grafana"
      },
      "startsAt": "2026-09-22T08:19:51.764830553Z",
      "status": "firing",
      "valueString": "[ metric='foo' labels={instance=bar} value=10 ]"
    }
  ],
  "appVersion": "13.0.1",
  "groupKey": "webhook-57c6d9296de2ad39-1790065191",
  "receiver": "webhook",
  "state": "alerting",
  "status": "firing",
  "title": "[FIRING:1] TestAlert Grafana ",
  "version": "1"
}
```

### 6.3 Alert rules

Both rules are **provisioned as code** in
`monitoring/grafana/provisioning/alerting/alert-rules.yaml` instead of clicked together in
the UI. That way they're reviewable in the PR and survive a Grafana restart. In the UI they
show up as Grafana-managed rules in folder `QuickTicket`, group `quickticket-slo`, marked
*Provisioned*.

**Alert 1: `QuickTicket High Error Rate`** (severity `critical`, evaluate every 1m, `for: 2m`):

```promql
sum(rate(gateway_requests_total{status=~"5.."}[5m]))
  /
sum(rate(gateway_requests_total[5m])) * 100
```

condition: **IS ABOVE 5**

**Alert 2: `QuickTicket SLO Burn Rate`** (severity `warning`, evaluate every 1m, `for: 5m`):

```promql
(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m]))
      / sum(rate(gateway_requests_total[30m]))))
  / (1 - 0.995)
```

condition: **IS ABOVE 6**

As registered by Grafana:

```console
$ curl -s http://localhost:3000/api/v1/provisioning/alert-rules | jq -r '.[] |
    "\(.title)\n  uid=\(.uid) for=\(.for) labels=\(.labels|tostring)\n  expr=\(.data[0].model.expr)\n  condition=\(.data[1].model.conditions[0].evaluator.type) \(.data[1].model.conditions[0].evaluator.params|tostring)"'
QuickTicket High Error Rate
  uid=qt-high-error-rate for=2m labels={"service":"gateway","severity":"critical"}
  expr=sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100
  condition=gt [5]
QuickTicket SLO Burn Rate
  uid=qt-slo-burn-rate for=5m labels={"service":"gateway","severity":"warning"}
  expr=(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)
  condition=gt [6]
```

### 6.4 Notification policy

`monitoring/grafana/provisioning/alerting/notification-policies.yaml`: default policy points
at `quickticket-alerts`, grouped by `alertname`, 30 s group wait, 5 m repeat interval:

```console
$ curl -s http://localhost:3000/api/v1/provisioning/policies | jq -c .
{"receiver":"quickticket-alerts","group_by":["alertname"],"group_wait":"30s",
 "group_interval":"1m","repeat_interval":"5m","provenance":"file"}
```

### 6.5 Runbook

<a name="runbook-quickticket-high-error-rate"></a>

> This is the runbook the alert's `runbook_url` annotation points at, and it's the document I
> actually followed during the incident in §6.6.
>
> **One honest amendment.** The version I wrote *before* the incident had a section claiming
> that because the ArgoCD Application has `selfHeal: false`, a `kubectl set env` fix "will
> stick". The incident proved me wrong (§6.6), so I rewrote the **Mitigation** section
> afterwards to go through git and added a break-glass procedure. Everything else is as I
> originally wrote it. If a runbook survives its first real use unchanged, that usually just
> means nobody actually followed it.

> **Runbook: QuickTicket High Error Rate**
>
> **Severity:** critical (page) · **Owner:** QuickTicket on-call · **Last tested:** 2026-09-22

#### Alert
- **Fires when:** gateway 5xx rate > 5% of all gateway requests for 2 minutes
  (`sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100`)
- **Dashboard:** QuickTicket: Golden Signals (`http://localhost:3000/d/quickticket-golden-signals`)
- **Contact point:** `quickticket-alerts` (webhook → `webhook-receiver.monitoring`)
- **Platform:** k3d cluster `quickticket`, namespace `default` (kubectl, not docker compose)

#### 0. Before you start
```bash
kubectl config use-context k3d-quickticket
kubectl port-forward -n default     svc/gateway    3080:8080 &
kubectl port-forward -n monitoring  svc/prometheus 9090:9090 &
kubectl port-forward -n monitoring  svc/grafana    3000:3000 &
```

#### 1. Confirm the alert is real (not a monitoring artefact)
```bash
# Is the gateway actually returning 5xx right now?
curl -s -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (status,path) (rate(gateway_requests_total[5m]))' | jq -r \
  '.data.result[] | "\(.metric.status)\t\(.metric.path)\t\(.value[1])"' | sort
```
The `status` column tells you **which endpoint** is failing. That single line usually
identifies the blast radius: `/reserve/{id}/pay` only ⇒ payments; everything ⇒ a shared
dependency (events, postgres, redis).

#### 2. Which service is failing?
```bash
curl -s http://localhost:3080/health | python3 -m json.tool
kubectl get pods -n default -o wide
kubectl get deploy -n default
```
`gateway /health` returns `checks.events` / `checks.payments` = `ok | degraded | down`.
- `down` → the pod is gone / not listening.
- `degraded` → the pod answers but its own `/health` is 503 (its dependency is broken).
- **all `ok` while 5xx continue** → the failure is *inside* a request path that `/health`
  does not exercise (e.g. injected charge failures, `/health` never calls `/charge`).

#### 3. Check the dependencies directly
```bash
kubectl exec -n default deploy/gateway -- \
  python -c "import httpx;print(httpx.get('http://payments:8082/health').text)"
kubectl exec -n default deploy/gateway -- \
  python -c "import httpx;print(httpx.get('http://events:8081/health').text)"
```
The payments `/health` body echoes its fault-injection configuration:
`{"status":"healthy","failure_rate":0.0,"latency_ms":0}`. A non-zero `failure_rate`
here is the smoking gun.

#### 4. Logs
```bash
kubectl logs -n default deploy/gateway  --tail=30 --since=5m
kubectl logs -n default deploy/payments --tail=30 --since=5m
kubectl logs -n default deploy/events   --tail=30 --since=5m
```
Look for `Payment failed (injected)`, `payments unreachable`, `pool exhausted`,
`Redis unavailable`.

#### 5. Metric cross-check (payments)
```bash
curl -s -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (result) (rate(payments_charges_total[5m]))' | jq -r \
  '.data.result[] | "\(.metric.result)\t\(.value[1])"'
```
`result="failed"` climbing while `result="success"` is flat ⇒ payments is rejecting charges.

#### Common causes

| Cause | How to identify | Fix |
|---|---|---|
| Payments pod down / crashlooping | `kubectl get pods` shows 0/1 or CrashLoopBackOff; gateway `/health` → `payments: down`; gateway logs `payments unreachable` | `kubectl rollout restart deploy/payments -n default` |
| Payments high injected failure rate | pods Healthy, `/health` shows `failure_rate > 0`, 5xx only on `/reserve/{id}/pay`, `payments_charges_total{result="failed"}` climbing | set `PAYMENT_FAILURE_RATE=0.0` (see *Mitigation*) |
| Payments slow → gateway timeouts | 5xx are **504** not 500; p99 latency panel spikes to ≈ `GATEWAY_TIMEOUT_MS`; `/health` shows `latency_ms > 0` | set `PAYMENT_LATENCY_MS=0` (see the second runbook) |
| Events pod down | gateway `/health` → `events: down`; 5xx on `/events` *and* `/events/{id}/reserve` | `kubectl rollout restart deploy/events -n default` |
| DB connection pool exhausted | events logs show `connection pool exhausted`; `events_db_pool_size` gauge pinned at `DB_MAX_CONNS` | raise `DB_MAX_CONNS`, then restart events |
| Postgres / Redis down | events `/health` → `postgres: down` / `redis: down`; everything 5xx | `kubectl rollout restart deploy/postgres -n default` (or `redis`) |

#### Mitigation — ⚠️ this cluster is managed by ArgoCD

**A `kubectl set env` fix here is a stop-gap, not a fix.** The ArgoCD Application
`quickticket` has automated sync on with `selfHeal: false`, so your drift is *not* reverted
immediately, but git still says the broken value, and **the next sync of any revision,
pushed by anyone for any reason, silently re-breaks production.** Check with
`kubectl get app quickticket -n argocd` (if it says `selfHeal: true`, your fix is reverted
within minutes instead). **The incident is not closed until ArgoCD reports `Synced` on a
revision that contains the fix.** So:

```bash
# 1. make the change in the manifest
$EDITOR k8s/payments.yaml          # e.g. PAYMENT_FAILURE_RATE: "0.0"

# 2. ship it
git commit -am "fix(payments): <what and why>"
git push cluster HEAD:refs/heads/main
kubectl annotate application quickticket -n argocd argocd.argoproj.io/refresh=normal --overwrite

# if the bad value arrived in a known commit, prefer a revert — it is faster and auditable
git revert --no-edit <bad-sha> && git push cluster HEAD:refs/heads/main
```

**Break-glass**: if git is unavailable and you must stop the bleeding *now*, suspend
automated sync first so no unrelated push can re-break you, then use kubectl, and restore
the policy once the real fix has landed in git:

```bash
kubectl patch application quickticket -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}'
kubectl set env deploy/payments -n default PAYMENT_FAILURE_RATE=0.0
# ... after the incident, and after landing the fix in git:
kubectl patch application quickticket -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":false}}}}'
```

Then:
1. Watch the rollout: `kubectl rollout status deploy/<svc> -n default`.
2. Confirm recovery on the **metric**, not on the pod:
   ```bash
   watch -n 10 "curl -s -G http://localhost:9090/api/v1/query \
     --data-urlencode 'query=sum(rate(gateway_requests_total{status=~\"5..\"}[1m])) \
       / sum(rate(gateway_requests_total[1m])) * 100' | jq -r '.data.result[0].value[1]'"
   ```
   Use the **1m** window while verifying. The alert's 5m window lags by design.
3. Confirm a real purchase succeeds end to end:
   ```bash
   RID=$(curl -s -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' \
     http://localhost:3080/events/3/reserve | jq -r .reservation_id)
   curl -s -X POST http://localhost:3080/reserve/$RID/pay | jq .
   ```
4. The alert returns to `Normal` roughly 5 minutes after the last error leaves the 5m rate
   window. A `resolved` webhook lands in
   `kubectl logs -n monitoring deploy/webhook-receiver`.

#### Escalation
- **10 minutes** without a root cause, or error rate > 25%: escalate to the course
  instructor / TA and declare a SEV-2.
- If `QuickTicket SLO Burn Rate` is also firing, freeze deploys until the postmortem.

### 6.6 Inject failure and respond

#### A detour that turned into the most useful finding of the lab

My first attempt used the course's `kubectl set env` approach:

```console
$ date '+injected_at: %H:%M:%S'; kubectl set env deploy/payments -n default PAYMENT_FAILURE_RATE=1.0
injected_at: 11:23:54
deployment.apps/payments env updated

$ kubectl get pods -n default -l app=payments
NAME                        READY   STATUS        RESTARTS   AGE
payments-59c8b6fdd8-hsctg   0/1     Terminating   0          2s
payments-5c797645b5-vtj29   1/1     Running       0          15m
```

The new pod started **terminating two seconds after it was created** and the old pod stayed
running. ArgoCD had already reverted the change:

```console
$ kubectl get application -n argocd quickticket -o jsonpath='{.spec.syncPolicy}' | jq .
{
  "automated": {
    "selfHeal": false
  }
}

$ kubectl get application -n argocd quickticket \
    -o jsonpath='{range .status.history[*]}{.id} {.revision} {.deployedAt}{"\n"}{end}' | tail -2
4 8309022863fac38c4416eaa09f850c606d561c28 2026-09-22T08:08:31Z
5 a40e5d1735061a691c730af6536e770cef26ba54 2026-09-22T08:23:55Z
```

At this point I figured `selfHeal: false` just doesn't do what it says on the tin. That was my
first conclusion, and I was about to write it down as a finding. **It turned out to be wrong**,
and the Bonus Task is what proved it wrong. The real explanation took me a while to piece
together, but it's worth getting right:

- ArgoCD's last sync before the incident was **history id 4 = `8309022`** (08:08:31Z).
- The tip of the deploy branch, though, was already sitting at **`a40e5d1`**, pushed at the
  end of Lab 5 and *never synced*.
- At 08:23:55Z the periodic reconciliation finally noticed that pending revision and synced
  it. That sync re-applied `k8s/payments.yaml` wholesale, and **wiped my `kubectl set env` as
  a side effect**. One second after my command, purely by coincidence.

So it wasn't self-heal reverting my change at all, it was an unrelated sync landing almost at
the same moment. The control experiment came during the Bonus Task: the peer responder
applied a `kubectl set env` fix at 12:15:52 when there was *no* pending revision, and it
survived unreverted for over four minutes with the Application sitting at `OutOfSync`:

```console
$ kubectl get application -n argocd quickticket \
    -o jsonpath='{.status.sync.status}{" selfHeal="}{.spec.syncPolicy.automated.selfHeal}{"\n"}'
OutOfSync selfHeal=false
```

So `selfHeal: false` behaves exactly as documented: **drift alone is never reverted.** The
real hazard is subtler than that, and worse. An imperative `kubectl` fix survives only until
the *next* sync of *any* revision, including one someone else pushed for a completely
unrelated reason. It's a time bomb with no visible fuse. That's the lesson that went into the
runbook and into action item A6.

Either way, the right response wasn't to fight the platform, it was to use it. From Lab 5
onward the *only* durable way a change reaches production is through git. So I injected the
incident the way a real one would happen: **a one-line config change merged and shipped by
the deploy pipeline.**

#### T0 — the bad config ships

```console
$ git diff k8s/payments.yaml
@@ -42,7 +42,7 @@ spec:
           imagePullPolicy: Never
           env:
             - name: PAYMENT_FAILURE_RATE
-              value: "0.0"
+              value: "1.0"
             - name: PAYMENT_LATENCY_MS
               value: "0"

$ date '+T0_push: %Y-%m-%d %H:%M:%S %Z'; git push cluster HEAD:refs/heads/main
T0_push: 2026-09-22 11:25:41 MSK
To http://127.0.0.1:9418/quickticket.git
   35f0a8e..7b03f0f  HEAD -> main
```

ArgoCD synced it 1 second later and the new pod was serving within ~20 s:

```console
$ kubectl get application -n argocd quickticket \
    -o jsonpath='{.status.sync.status}{" "}{.status.health.status}{" "}{.status.sync.revision}{"\n"}'
Synced Healthy 7b03f0fb91b5b00f5c71b9c87af6c673354325ce

$ kubectl get pods -n default -l app=payments
NAME                        READY   STATUS    RESTARTS   AGE
payments-59c8b6fdd8-s8dll   1/1     Running   0          28s

$ kubectl exec -n default deploy/gateway -- \
    python -c "import httpx;print(httpx.get('http://payments:8082/health').text)"
{"status":"healthy","failure_rate":1.0,"latency_ms":0}
```

#### Measured 30-second series across the whole incident

Sampled independently of Grafana, straight from Prometheus + the Grafana rule API:

| time (MSK) | rps | 5xx rate % (5m) | burn rate (30m) | High Error Rate | SLO Burn Rate |
|---|---:|---:|---:|---|---|
| 11:25:13 | 1.28 | 0.27 | 0.92 | normal | normal |
| 11:25:43 | 1.28 | 0.26 | 0.86 | normal | normal |
| 11:26:13 | 1.28 | 0.60 | 1.17 | normal | normal |
| 11:26:43 | 1.28 | 1.42 | 1.91 | normal | normal |
| 11:27:13 | 1.28 | 2.21 | 2.57 | normal | normal |
| 11:27:43 | 1.28 | 2.73 | 3.16 | normal | normal |
| 11:28:13 | 1.28 | 3.78 | 3.92 | normal | normal |
| 11:28:43 | 1.28 | 4.57 | 4.41 | normal | normal |
| 11:29:13 | 1.28 | **5.36** | 4.85 | normal | normal |
| 11:29:44 | 1.28 | 6.41 | 5.47 | normal | normal |
| 11:30:14 | 1.28 | 7.20 | 5.84 | **pending** | normal |
| 11:30:44 | 1.28 | 7.99 | 6.19 | pending | normal |
| 11:31:14 | 1.28 | 8.49 | **6.69** | pending | **pending** |
| 11:31:44 | 1.28 | 8.47 | 6.99 | pending | pending |
| 11:32:14 | 1.28 | 8.49 | 7.27 | **firing** | pending |
| 11:33:14 | 1.28 | 7.96 | 7.61 | firing | pending |
| 11:34:14 | 1.28 | 8.20 | 8.21 | firing | pending |
| 11:35:14 | 1.28 | 8.20 | 8.62 | firing | pending |
| 11:36:14 | 1.28 | 7.94 | 8.97 | firing | **firing** |
| 11:36:45 | 1.29 | 7.65 | 9.00 | firing | firing |
| 11:38:15 | 1.28 | 5.28 | 8.36 | firing | firing |
| 11:38:45 | 1.28 | 4.49 | 8.18 | firing | firing |
| 11:39:15 | 1.28 | 3.44 | 8.00 | **normal** | firing |
| 11:40:45 | 1.27 | 1.07 | 7.51 | normal | firing |

#### Alert firing evidence

```console
$ curl -s http://localhost:3000/api/prometheus/grafana/api/v1/rules | jq -r \
   '.data.groups[] | select(.name=="quickticket-slo") | .rules[] |
    "\(.name)\n  state=\(.state) for=\(.duration)s severity=\(.labels.severity)\n  \(.alerts[]? |
     "instance: state=\(.state) activeAt=\(.activeAt)\n  summary: \(.annotations.summary)")"'
QuickTicket High Error Rate
  state=firing for=120s severity=critical
  instance: state=Alerting activeAt=2026-09-22T08:32:00Z
  summary: Gateway error rate is 7.9155672823218985%
QuickTicket SLO Burn Rate
  state=firing for=300s severity=warning
  instance: state=Alerting activeAt=2026-09-22T08:36:00Z
  summary: Error budget burning at 8.949196812007402x the sustainable rate

$ curl -s http://localhost:3000/api/alertmanager/grafana/api/v2/alerts | jq -r \
   '.[] | "\(.labels.alertname)\t\(.labels.severity)\tstate=\(.status.state)\tstartsAt=\(.startsAt)"'
QuickTicket High Error Rate	critical	state=active	startsAt=2026-09-22T08:32:00.000Z
QuickTicket SLO Burn Rate	warning	state=active	startsAt=2026-09-22T08:36:00.000Z
```

In the UI (Alerting → Alert rules, filtered `state:firing`) the rule shows as
**`QuickTicket High Error Rate` · Provisioned · Firing · 1 instance · 2 labels**, in folder
`QuickTicket > quickticket-slo`.

#### Notification received

Every delivery to the webhook contact point during this run:

```console
$ kubectl logs -n monitoring deploy/webhook-receiver   # summarised
received (UTC)         status    alerts
2026-09-22T08:19:51Z   firing    TestAlert[firing]                        <- 6.2 contact-point test
2026-09-22T08:32:30Z   firing    QuickTicket High Error Rate[firing]      <- group_wait 30s after 08:32:00
2026-09-22T08:36:35Z   firing    QuickTicket SLO Burn Rate[firing]
2026-09-22T08:38:30Z   firing    QuickTicket High Error Rate[firing]      <- repeat_interval 5m
2026-09-22T08:39:30Z   resolved  QuickTicket High Error Rate[resolved]
```

The firing payload:

```json
{
  "alerts": [
    {
      "annotations": {
        "description": "Error rate exceeded 5% for 2 minutes. Check payments service health.",
        "runbook_url": "https://github.com/Vanady39/SRE-Intro-Ivan-Vavilov/blob/main/submissions/lab6.md#runbook-quickticket-high-error-rate",
        "summary": "Gateway error rate is 8.48806366047745%"
      },
      "labels": {
        "alertname": "QuickTicket High Error Rate",
        "grafana_folder": "QuickTicket",
        "service": "gateway",
        "severity": "critical"
      },
      "ruleUID": "qt-high-error-rate",
      "startsAt": "2026-09-22T08:32:00Z",
      "status": "firing",
      "valueString": "[ var='A' labels={} type='query' value=8.48806366047745 ], [ var='C' labels={} type='threshold' value=1 ]",
      "values": { "A": 8.48806366047745, "C": 1 }
    }
  ],
  "groupKey": "{}:{alertname=\"QuickTicket High Error Rate\"}",
  "status": "firing",
  "title": "[FIRING:1] QuickTicket High Error Rate "
}
```

#### Following the runbook

**Step 1: confirm the alert is real** (11:32:40). The `status`/`path` breakdown bounds the
blast radius right away: only the pay endpoint is broken, and it's broken 100%.

```console
$ curl -s -G http://localhost:9090/api/v1/query \
    --data-urlencode 'query=sum by (status,path) (rate(gateway_requests_total[5m]))' \
  | jq -r '.data.result[] | "\(.metric.status)\t\(.metric.path)\t\(.value[1])"' | sort
200	/events	0.688135593220339
200	/events/{id}/reserve	0.29491525423728815
200	/health	0.18983050847457628
200	/openapi.json	0
200	/reserve/{id}/pay	0
500	/reserve/{id}/pay	0.0983050847457627
503	/health	0.010169491525423728
```

**Step 2: which service is failing** (11:32:40). Nothing looks wrong. Every pod is `1/1
Running`, and the gateway reports itself **healthy**:

```console
$ curl -s http://localhost:3080/health | python3 -m json.tool
{
    "status": "healthy",
    "checks": {
        "events": "ok",
        "payments": "ok",
        "circuit_payments": "CLOSED"
    }
}

$ kubectl get pods -n default
NAME                        READY   STATUS    RESTARTS   AGE
events-7dc944bd56-t86dv     1/1     Running   0          24m
gateway-858884796-fhpz9     1/1     Running   0          24m
loadgen-74dfddf4bc-tfbvr    2/2     Running   0          16m
payments-59c8b6fdd8-s8dll   1/1     Running   0          6m58s
postgres-67977f4df6-rrh44   1/1     Running   0          11h
redis-87cf6bc6b-kn267       1/1     Running   0          11h
```

This is exactly the case the runbook's step-2 note covers: *"all `ok` while 5xx continue ⇒
the failure is inside a request path that `/health` does not exercise."*

**Step 3: check dependencies directly** (11:32:40). The payments `/health` body echoes its
fault-injection configuration, and there is the smoking gun:

```console
$ kubectl exec -n default deploy/gateway -- \
    python -c "import httpx;print('payments:',httpx.get('http://payments:8082/health').text)"
payments: {"status":"healthy","failure_rate":1.0,"latency_ms":0}

$ kubectl exec -n default deploy/gateway -- \
    python -c "import httpx;print('events:  ',httpx.get('http://events:8081/health').text)"
events:   {"status":"healthy","checks":{"postgres":"ok","redis":"ok"}}
```

**Step 4: logs** (11:32:55):

```console
$ kubectl logs -n default deploy/payments --since=3m | grep 'Payment failed'
{"time":"2026-09-22 08:29:58,326","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 94211e41-9f74-4afc-b2cb-ad23026f5c8c"}
{"time":"2026-09-22 08:30:08,521","level":"WARNING","service":"payments","msg":"Payment failed (injected) for ddd9dbeb-1274-41cf-a6cb-bf6db363c11b"}
{"time":"2026-09-22 08:30:18,740","level":"WARNING","service":"payments","msg":"Payment failed (injected) for 7d86f04a-7ba1-4a91-86ab-9672154793f6"}

$ kubectl logs -n default deploy/gateway --since=3m | grep -m2 '500 Internal'
{"time":"2026-09-22 08:29:58,327","level":"INFO","service":"gateway","msg":"HTTP Request: POST http://payments:8082/charge \"HTTP/1.1 500 Internal Server Error\""}
INFO:     10.42.0.93:36060 - "POST /reserve/94211e41-9f74-4afc-b2cb-ad23026f5c8c/pay HTTP/1.1" 500 Internal Server Error
```

**Step 5: payments charge metrics** (11:32:49). `success` has disappeared entirely:

```console
$ curl -s -G http://localhost:9090/api/v1/query \
    --data-urlencode 'query=sum by (result) (rate(payments_charges_total[5m]))' \
  | jq -r '.data.result[] | "\(.metric.result)\t\(.value[1])"'
failed	0.0983050847457627
```

**Root cause found: 11:32:49.** The runbook's "If ArgoCD is managing the workload" section
sends you to the sync revision, which names the commit:

```console
$ kubectl get application -n argocd quickticket \
    -o jsonpath='{range .status.history[-2:]}{.deployedAt}  {.revision}{"\n"}{end}'
2026-09-22T08:23:55Z  a40e5d1735061a691c730af6536e770cef26ba54
2026-09-22T08:25:42Z  7b03f0fb91b5b00f5c71b9c87af6c673354325ce

$ git show --stat --oneline 7b03f0f
7b03f0f chore(payments): raise PAYMENT_FAILURE_RATE for a payment-provider drill
 k8s/payments.yaml | 2 +-
```

**Fix: 11:36:19.** Because the change arrived through git, the fix is a `git revert`, not a
`kubectl` snowflake. It can't be undone by the next sync:

```console
$ git revert --no-edit 7b03f0f
[feature/lab6 fe611dc] Revert "chore(payments): raise PAYMENT_FAILURE_RATE for a payment-provider drill"
 1 file changed, 1 insertion(+), 1 deletion(-)

$ date '+revert_push: %H:%M:%S'; git push cluster HEAD:refs/heads/main
revert_push: 11:36:19
To http://127.0.0.1:9418/quickticket.git
   7b03f0f..fe611dc  HEAD -> main
```

> **Honest note on the response time.** I had the root cause at 11:32:49, but I deliberately
> held the fix until 11:36:19 so I could also watch the slower 30-minute `SLO Burn Rate` rule
> (`for: 5m`) transition to Firing. In a real incident that's 3.5 minutes of avoidable customer
> impact. I count it as impact in the postmortem, not excuse it.

**Recovery: 11:36:33, 14 seconds after the push:**

```console
$ date '+### Service recovered at %H:%M:%S'
### Service recovered at 11:36:33

$ kubectl exec -n default deploy/gateway -- \
    python -c "import httpx;print(httpx.get('http://payments:8082/health').text)"
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

$ kubectl get pods -n default -l app=payments
NAME                        READY   STATUS    RESTARTS   AGE
payments-5c797645b5-8jnzs   1/1     Running   0          14s

$ kubectl get application -n argocd quickticket \
    -o jsonpath='{.status.sync.status}{" "}{.status.health.status}{" "}{.status.sync.revision}{"\n"}'
Synced Healthy fe611dc6bc3e803d704259e5f9be5e0f9faa44e9

# runbook mitigation step 3 — a real purchase, end to end
$ RID=$(curl -s -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' \
        http://localhost:3080/events/3/reserve | jq -r .reservation_id)
$ curl -s -w '\nHTTP %{http_code}\n' -X POST http://localhost:3080/reserve/$RID/pay
{"order_id":"432a4d1e-c45f-4b86-a059-2575ff575f07","event_id":3,"quantity":1,"total_cents":15000,"status":"confirmed"}
HTTP 200
```

**Alert resolved: 11:39:00** (`High Error Rate` → Normal; resolved webhook at 11:39:30). The
`SLO Burn Rate` warning kept firing much longer, because its 30-minute window still had to
slide past the incident. More on that below.

### 6.7 Proof of work — timeline

| # | time (MSK) | event | source of the timestamp |
|---|---|---|---|
| 0 | 11:20:12 | baseline sampling starts: 1.28 rps, 0.5% errors, burn rate ≈ 1.0 | sampler |
| 1 | 11:23:54 | *first* injection attempt via `kubectl set env`: **reverted by ArgoCD in ~1 s** | `kubectl`, ArgoCD history |
| 2 | **11:25:41** | **failure injected**: `PAYMENT_FAILURE_RATE="1.0"` pushed to the GitOps remote | `git push` |
| 3 | 11:25:42 | ArgoCD syncs revision `7b03f0f` | ArgoCD `status.history` |
| 4 | ~11:26:00 | first customer-visible failure (`500` on `/reserve/{id}/pay`) | payments log |
| 5 | 11:29:13 | 5-minute error-rate ratio crosses the 5% threshold (5.36%) | sampler |
| 6 | 11:30:00 | `QuickTicket High Error Rate` → **Pending** | Grafana rule API |
| 7 | **11:32:00** | `QuickTicket High Error Rate` → **Firing** (value 8.49%) | Grafana `startsAt` |
| 8 | 11:32:30 | webhook notification delivered | webhook-receiver log |
| 9 | 11:32:40 | investigation starts (runbook step 1) | shell timestamps |
| 10 | 11:32:49 | **root cause identified**: payments `failure_rate=1.0`, shipped by commit `7b03f0f` | shell timestamps |
| 11 | 11:36:00 | `QuickTicket SLO Burn Rate` → **Firing** (8.95x) | Grafana `startsAt` |
| 12 | 11:36:19 | **fix applied**: `git revert 7b03f0f` + push to GitOps remote | `git`, shell |
| 13 | 11:36:33 | payments back to `failure_rate=0.0`; successful end-to-end purchase | `kubectl exec`, curl |
| 14 | 11:39:00 | `QuickTicket High Error Rate` → **Normal** | Grafana rule API |
| 15 | 11:39:30 | resolved notification delivered | webhook-receiver log |
| 16 | 11:57:05 | `QuickTicket SLO Burn Rate` → **Normal** (20 min 32 s after recovery, its 30 m window had to slide past the incident) | Grafana rule API |

**Impact measured over the incident window (11:25:40 → 11:39:40):**

```console
$ curl -s -G http://localhost:9090/api/v1/query \
    --data-urlencode 'query=sum(increase(gateway_requests_total[14m]))' --data-urlencode "time=$END"
1075.4809523148824
$ ... 'sum(increase(gateway_requests_total{status=~"5.."}[14m]))'
66.47496429093033
$ ... 'sum(increase(payments_charges_total{result="failed"}[14m]))'
62.492063492063494
```

66 failed requests, 62 of them rejected charges. That's **every single checkout attempt for
13 minutes**. Against a 99.5% SLO at 1.28 rps, a 30-day error budget is 16 589 failed
requests, so this incident used up **0.40% of the monthly budget**. The burn rate peaked at
**9.0x**, and if that had kept up, the budget would have been gone in **3.3 days**.

### 6.7 Answer — "How long from failure injection to alert firing? Why the delay?"

**6 minutes 19 seconds** from injection (11:25:41) to Firing (11:32:00), and **6 minutes
49 seconds** until the notification actually landed (11:32:30).

That delay isn't lag, it's five deliberate design choices stacked on top of each other:

| # | contribution | ≈ time | why it exists |
|---|---|---|---|
| 1 | GitOps sync + pod rollout before the first request could fail | ~20 s | ArgoCD reconcile + rolling update |
| 2 | **the 5-minute `rate()` window filling up** | **~3 min 15 s** | the largest term, and the least obvious |
| 3 | 1-minute evaluation interval (rounds up to the next tick) | ≤ 60 s | cost/noise trade-off |
| 4 | `for: 2m` pending period | 120 s | flap suppression |
| 5 | `group_wait: 30s` before notifying | 30 s | lets related alerts batch into one page |

**Term 2 is the interesting one.** `rate(...[5m])` is an average over the last 5 minutes, so
when a service jumps from 0% to 8.5% errors instantly, the *measured* ratio ramps up linearly
instead of stepping, it only reaches the true value once the whole window has turned over.
The measured series above shows exactly that ramp: 0.60 → 1.42 → 2.21 → 2.73 → 3.78 → 4.57 →
**5.36** → 6.41 → 7.20 → 7.99 → 8.49. Crossing a 5% threshold when the steady-state value is
8.5% takes `5/8.5 × 5 min ≈ 2.9 min` of window fill on its own. **A window longer than the
threshold's headroom ends up dominating detection time.** Shrinking `for: 2m` would barely
help. Shrinking the rate window to `[1m]` would cut detection by about 2.5 minutes.

The same effect, inverted, explains why the burn-rate alert took **20 minutes 32 seconds** to
clear after the service recovered at 11:36:33: its window is 30 minutes, so the incident's
errors have to physically age out of it. That's correct behaviour for a *budget* alert, it's
telling you about a budget that really was spent, but it's also why a burn-rate rule should
never page someone directly, only open a ticket. That's why Alert 2 is `severity: warning`
and Alert 1 is `severity: critical`.

**The threshold itself needed tuning too.** The lab's hint is right: at
`PAYMENT_FAILURE_RATE=0.5` the overall error rate would have been ~4%, *below* the 5%
threshold, so the alert would never have fired even though half of all checkouts were
failing. Even at `1.0` the steady-state error rate was only **8.5%**, because the pay
endpoint is just 1 request in 12 of the total mix. A threshold on *global* 5xx ratio is blind
to a total outage of a minority endpoint, hence action item **A1**.

---

## Task 2 — Blameless Postmortem (4 pts)

# Postmortem: All QuickTicket checkouts failed for 13 minutes after a config change

**Date:** 2026-09-22
**Duration:** 11:26:00 → 11:36:33 MSK customer impact (10 min 33 s); alert clear at 11:39:00
**Severity:** SEV-2: complete loss of the revenue path (checkout); browsing unaffected
**Author:** Ivan Vavilov (QuickTicket on-call)
**Status:** Resolved · action items open

## Summary

A one-line configuration change to the payments service (`PAYMENT_FAILURE_RATE` `0.0` → `1.0`)
got merged and shipped by the GitOps pipeline. For the next 13 minutes payments rejected
**100% of charge requests**, so every checkout returned HTTP 500 while browsing and reserving
kept working normally. 62 charges failed. Detection took 6 min 19 s, and the fix was a `git
revert` that took effect 14 seconds after it was pushed.

## Timeline

| Time (MSK) | Event |
|---|---|
| 11:25:41 | Commit `7b03f0f` (`PAYMENT_FAILURE_RATE: "1.0"`) pushed to the deploy branch |
| 11:25:42 | ArgoCD syncs the revision; payments rolling update begins |
| ~11:26:00 | First customer-visible failure: `500` on `POST /reserve/{id}/pay` |
| 11:29:13 | 5-minute error-rate ratio crosses the 5% alert threshold (5.36%) |
| 11:30:00 | `QuickTicket High Error Rate` → **Pending** |
| **11:32:00** | `QuickTicket High Error Rate` → **Firing** (8.49%) |
| 11:32:30 | Page delivered to the `quickticket-alerts` webhook contact point |
| 11:32:40 | On-call acknowledges, opens the runbook, runs the status/path breakdown |
| 11:32:45 | Blast radius bounded: only `/reserve/{id}/pay`, and it is failing 100% |
| 11:32:49 | **Root cause identified**: payments `/health` reports `failure_rate: 1.0`; ArgoCD's synced revision names commit `7b03f0f` |
| 11:36:00 | `QuickTicket SLO Burn Rate` → **Firing** (8.95x) |
| 11:36:19 | **Mitigation applied**: `git revert 7b03f0f`, pushed to the deploy branch |
| 11:36:33 | Payments back to `failure_rate: 0.0`; end-to-end purchase verified successful |
| 11:39:00 | `QuickTicket High Error Rate` → **Normal**; resolved notification at 11:39:30 |
| 11:57:05 | `QuickTicket SLO Burn Rate` → **Normal** (30-minute window had to slide past the incident) |

## Impact

- **1 075** gateway requests in the incident window, **66** of them 5xx.
- **62 failed charges**, i.e. *every* checkout attempt for 13 minutes. Zero successful
  purchases.
- Browsing (`GET /events`) and reserving were unaffected, the failure was confined to the
  single endpoint that calls payments.
- **0.40% of the 30-day error budget** used up (66 of 16 589 allowed failures at 1.28 rps).
  Peak burn rate **9.0x**, and if that had kept up, the monthly budget would have been gone in
  **3.3 days**.
- No data was lost or corrupted. Reservations stayed held in Redis, so affected customers
  could have retried once payments recovered.

## Root Cause

The proximate trigger was a config value: `PAYMENT_FAILURE_RATE` was set to `1.0` in
`k8s/payments.yaml`, which makes the payments service reject every charge by design.

The **systemic** cause is that nothing in the delivery path could tell the difference between
that value and a safe one:

1. **The fault-injection knob is a plain environment variable with no guard.** `0.0` and `1.0`
   are equally valid to the pipeline. There's no schema, no admission policy, and no CI check
   asserting that a production manifest carries `PAYMENT_FAILURE_RATE: "0.0"`. A one-character
   diff is enough to disable all revenue.
2. **Health checks don't touch the failing path.** `payments /health` returns `200` and
   `"status":"healthy"` *while rejecting 100% of charges*, because `/health` never calls
   `/charge`. Readiness stayed green, the pod was never restarted, the rollout was declared
   successful, and ArgoCD reported `Synced / Healthy` the whole time. Every automated signal
   in the system said the deploy was fine.
3. **There's no deploy-time verification.** The rollout completed on "pod is Ready", not on
   "a synthetic purchase still works".
4. **Detection was slow by construction.** A 5-minute averaging window against a 5% threshold
   can't detect an 8.5% error rate in less than ~3 minutes, whatever the pending period is
   (see §6.7). Most of the 6 min 19 s was the metric window, not the alerting rule.
5. **The threshold is measured against global traffic, so it's blind to minority endpoints.**
   Checkout is ~1 request in 12. A *complete* checkout outage only moves the global 5xx ratio
   to 8.5%. A 50% checkout failure rate would have produced ~4% and **never fired at all**,
   even with half of all revenue failing.

## What Went Well

- **The alert fired, notified, repeated and resolved without anyone touching it**, and the
  resolved notification arrived automatically 30 s after the rule cleared.
- **The runbook's first step was the right first step.** The `sum by (status, path)` breakdown
  bounded the blast radius in one command: one endpoint, failing 100%. Everything after that
  was just confirmation.
- **The runbook anticipated the trap.** Its step-2 note, *"all `ok` while 5xx continue ⇒ the
  failure is inside a request path that `/health` does not exercise"*, is what stopped me from
  wasting time on pod status when every pod looked healthy.
- **Root cause in 9 seconds of investigation** (11:32:40 → 11:32:49), because payments
  `/health` echoes its own fault-injection config. Services that report their own config are
  so much easier to debug.
- **GitOps made the fix trivial and safe.** `git revert` + push restored service in
  **14 seconds**, and because the fix went through the same pipeline, it can't be silently
  undone by the next sync.
- **The two-tier alert design worked as intended.** The fast symptom alert paged. The slow
  budget alert stayed firing long after recovery, correctly reporting that budget really had
  been spent.

## What Went Wrong

- **10.5 minutes of complete checkout failure, of which only ~6 were unavoidable.** The rest
  was self-inflicted: I held the fix from 11:32:49 to 11:36:19 (3.5 min) just to watch the
  second alert transition. In a real incident that's pure avoidable customer impact, and I'm
  counting it here as impact rather than excusing it.
- **A bad config reached production with no gate at all.** No CI assertion, no admission
  policy, no canary, no post-deploy smoke test.
- **Every automated health signal lied.** Pods `1/1 Running`, gateway `/health` `"healthy"`,
  ArgoCD `Synced / Healthy`, all during a total outage of the revenue path.
- **The 5% global threshold is mis-tuned for this traffic mix** and would have missed a 50%
  checkout failure entirely.
- **An imperative `kubectl` change to this cluster is silently unsafe, and nothing says so.**
  My first attempt to inject the fault with `kubectl set env` got wiped ~1 second later.
  Not by self-heal, which is off and works exactly as documented, but because ArgoCD happened
  to sync a *pending, unrelated* revision at that exact moment and re-applied the whole
  manifest. The same thing applies to an emergency mitigation: a `kubectl` fix survives until
  the next sync of any revision, pushed by anyone, for any reason. The on-call gets no warning
  and no explanation when it evaporates. I only understood this correctly after the Bonus Task
  produced the control case.
- **Severity is a label, not a route.** Both alerts go to the same contact point, so a
  `warning` budget ticket and a `critical` outage page look the same to whoever receives them.

## Action Items

| # | Action | Owner | Priority |
|---|---|---|---|
| **A1** | Add a per-endpoint availability alert: `sum(rate(gateway_requests_total{path="/reserve/{id}/pay",status=~"5.."}[2m])) / sum(rate(gateway_requests_total{path="/reserve/{id}/pay"}[2m])) > 0.2`. A total checkout outage must page regardless of how small a share of global traffic checkout is. | Ivan Vavilov | **High** |
| **A2** | Add a CI check that fails the build if any manifest in `k8s/` sets `PAYMENT_FAILURE_RATE` or `PAYMENT_LATENCY_MS` to a non-zero value. Fault-injection knobs must be opt-in per environment, never mergeable to the deploy branch. | Ivan Vavilov | **High** |
| **A3** | Add a post-deploy smoke test as an ArgoCD `PostSync` hook: reserve one ticket and pay for it; fail the sync if the purchase does not return 200. Deploy success must mean "the business transaction works", not "the process is listening". | Ivan Vavilov | **High** |
| **A4** | Investigate and fix the ~0.5% steady-state `503` rate on gateway `/health` (readiness probe seeing a dependency as slow). At baseline QuickTicket already burns its entire 99.5% error budget, which leaves zero headroom and makes every threshold harder to tune. | Ivan Vavilov | Medium |
| **A5** | Fix the Redis reservation-hold leak: `event:<id>:held` is incremented on reserve and only decremented on confirm, so an expired reservation holds its tickets forever. Give the hold the same TTL as the reservation. | Ivan Vavilov | Medium |
| **A6** | Document that an imperative `kubectl` mitigation on this cluster survives only until the next ArgoCD sync of *any* revision. `selfHeal: false` protects against nothing here. Every runbook mitigation must end in a git commit, and "incident closed" must mean `Synced` on a revision containing the fix. The break-glass `kubectl patch application … syncPolicy` procedure is now in the runbook. | Ivan Vavilov | Medium |
| **A7** | Route `severity=critical` and `severity=warning` to different contact points so a budget-burn ticket cannot be mistaken for an outage page. | Ivan Vavilov | Low |
| **A8** | Re-evaluate the 5-minute rate window on the critical alert. Detection cost ~3 minutes of window fill; a `[2m]` window would roughly halve time-to-detect at the cost of noise the 2-minute pending period already absorbs. | Ivan Vavilov | Low |

## Answer — "What is the most important action item from your postmortem? Why?"

**A3, the post-deploy smoke test.**

A1 and A2 are both narrower versions of the same lesson, and both are worth doing, but they
only defend against the outage I just had: A2 blocks *this specific env var*, A1 catches
*this specific endpoint*. The next incident won't be `PAYMENT_FAILURE_RATE=1.0`. It'll be a
bad image tag, a wrong database URL, a broken migration, an expired credential.

A3 is the only item that is **independent of the cause**. It closes the actual gap that made
this outage both possible and invisible: *every health signal in the system can be green
while the product doesn't work*, because liveness and readiness only check that a process is
listening, not that a customer can buy a ticket. A synthetic purchase in the deploy path turns
that whole class of failure, any change that breaks checkout, for any reason, from "detected
in 6 minutes by an error-rate alert, after real customers have already failed" into "the
deploy never completes". It would have cut this incident's customer impact from 10.5 minutes
down to zero.

It also makes the slow-detection problem matter less. I should still tune the thresholds (A1,
A8), but tuning alerts only makes us find out faster that customers are already suffering. A
deploy gate stops them from suffering in the first place.

---

## Bonus Task — Cross-Test Runbooks (2 pts)

> ### ⚠️ Who the "classmate" was
>
> **No human classmate was available for this run.** Rather than invent a peer review, the
> test was run against a **separate, deliberately uninformed operator**: a fresh agent session
> with cluster access (`kubectl`, `curl`, Prometheus, Grafana) and **nothing else**. It was
> given the runbook file and an explicit prohibition on reading anything in
> `/home/iwon/SRE-Intro-Ivan-Vavilov`: no source, no manifests, no `git log`, no
> `submissions/`. It did not know which fault had been injected, or that a fault had been
> injected at all: it was simply paged.
>
> That is not the same as a human classmate and is not presented as one. But the constraint
> that matters for this exercise (*the responder knows only what the runbook tells them*)
> was genuinely enforced, and the transcript, timings and critique below are real.

### B.1 — Second runbook (failure mode: payment latency → gateway timeouts)

This is a different failure mode from Task 1: the dependency is **slow**, not **broken**.
Nothing returns an error downstream, every health check is green, and the gateway itself is
the one that manufactures the 5xx, a `504`, when its own client timeout expires. I chose
`PAYMENT_LATENCY_MS=6000` against the gateway's `GATEWAY_TIMEOUT_MS=5000`.

The runbook handed to the tester is reproduced below **in the form they received it** (the
improved version is in B.3). Its diagnostic spine:

1. **Separate "slow" from "broken"**: split the 5xx by status code: `504` ⇒ timeout,
   `500` ⇒ downstream error, `503` ⇒ unreachable.
2. **Confirm latency and locate it**: compare gateway p99 against payments p99. Both high
   ⇒ payments really is slow; only the gateway high ⇒ suspect the gateway's HTTP client.
3. **Time the dependency by hand**: `kubectl exec` a single `POST /charge` and measure it.
   Explicitly warns: *"`/health` stays fast. Health checks do not catch this, which is why
   the pods all look green."*
4. **Read the injected configuration**: `payments /health` echoes `latency_ms`; also check
   `GATEWAY_TIMEOUT_MS`, because the same symptom appears if *payments got slower* **or** if
   *the timeout got shorter*.
5. **Logs + `kubectl top`**: confirm the injected-latency log line, rule out CPU throttling.

Plus a Common Causes table (injected latency / timeout too low / CPU throttling / slow
Postgres), a three-step mitigation (`PAYMENT_LATENCY_MS=0`, verify p99 on the 1m window,
verify a real end-to-end purchase), and escalation.

### B.2 — The blind test

I injected it at 11:57:43 via GitOps (`PAYMENT_LATENCY_MS: "6000"`, commit `ad4627b`). The
alert fired at 12:15:00 at 8.52% error rate, and the responder was paged at 12:15:16 with
nothing but the alert summary and the runbook path.

**Result: resolved, but only partly using the runbook alone.**

| Milestone | Time | Elapsed |
|---|---|---|
| Paged, opened runbook | 12:15:16 | – |
| Step 1: split 5xx by status → `504 /reserve/{id}/pay 0.0678` | 12:15:20 | 4 s |
| Step 2: gateway p99 **7.139 s**, payments p99 **7.283 s** | 12:15:24 | 8 s |
| Step 3: hand-timed `POST /charge` → `200 6.212 s` | 12:15:27 | 11 s |
| **Step 4: root cause**: `{"latency_ms":6000}`, `GATEWAY_TIMEOUT_MS=5000` | **12:15:40** | **24 s** |
| Step 5: `"Injecting 6000ms latency"` in logs; `kubectl top` → 4m CPU, throttling ruled out | 12:15:43 | 27 s |
| Mitigation applied (`kubectl set env PAYMENT_LATENCY_MS=0`), rollout complete | 12:15:57 | 41 s |
| End-to-end purchase succeeds in 0.047 s (was 6+ s) | 12:16:02 | 46 s |
| 504 rate reaches zero, p99 settles at 0.03 s | 12:16:57 | 1 m 41 s |
| **Improvised**: discovers the app is ArgoCD-managed and now `OutOfSync` | 12:19:23 | 4 m 7 s |

Independent confirmation from the cluster side (my own observer, polling every 5 s, which the
tester could not see):

```text
12:15:20  latency=6000 gen=8 argocd=ad4627b alerts=High Error Rate=firing
12:15:52  latency=0    gen=9 argocd=ad4627b alerts=High Error Rate=firing
12:18:01  latency=0    gen=9 argocd=ad4627b alerts=High Error Rate=inactive
```

**Did they succeed? Yes, root cause in 24 seconds, service restored in 41.** Every command in
the runbook ran verbatim with no errors. **But the fix wasn't durable**, and the runbook gave
them no way to know that.

#### What they said was unclear or missing

Their critique, condensed (their emphasis):

> **Most useful step.** *"Step 1, decisively. Splitting on status code before doing any
> latency work is what made this fast: one query, four seconds."* They also singled out
> Step 3's warning that `/health` stays fast: *"That pre-empts the exact wrong turn a
> stressed on-call makes at 3am."* Their Step-5 logs confirmed it: payments served
> `GET /health 200 OK` roughly once a second throughout, while every `/charge` hung for 6 s.

| # | Finding | Severity |
|---|---|---|
| 1 | **The runbook does not match the alert, and its routing is a dead end.** They were paged for `QuickTicket High Error Rate`; the runbook is titled *"Payment Latency / Gateway Timeouts"*. Worse, its Step 1 routes the `500` and `503` branches to a *"High Error Rate runbook"* **that they had no link to**, as did Escalation. On a 500 they would have dead-ended. | **High** |
| 2 | **The "Fires when" condition contradicts the page.** The runbook says it fires on 504s or p99 > 3 s; the page said *"error rate is 8.5%"*, a ratio that appears nowhere in the runbook. They had to construct the ratio query themselves to check recovery. | **High** |
| 3 | **`kubectl set env` does not durably fix an ArgoCD-managed app, and the runbook never says so.** Post-fix the Application was `OutOfSync` with git still holding `6000`. *"My mitigation is a time bomb, not a fix."* | **High** |
| 4 | **Mitigation step 2 has no target value and no wait.** Run 4 s after a successful rollout it returned p99 **7.04**, indistinguishable from broken, because the 1m window had not rolled over. *"A less patient responder reads that as 'the fix didn't work' and starts thrashing."* | Medium |
| 5 | **No "how do I know the alert cleared?" step.** They improvised a Grafana API call. *"Closing a page on 'the graph looks better' is how incidents get re-opened."* | Medium |
| 6 | **Common Causes row 2 is unfalsifiable as written.** "Gateway timeout too low" is identified by `GATEWAY_TIMEOUT_MS < payments p99`, but that was *also* true in their case (5000 < 7283) while the cause was row 1. The real discriminator, *"payments p99 unchanged from normal"*, is useless because **the runbook never states what normal is.** They measured it post-fix: **~0.03 s**. | Medium |
| 7 | Step 3's probe writes real data. Payments logged `Payment success: PAY-8D85B4F0 for runbook-probe`. Harmless here, worth a warning in a real system. | Low |
| 8 | `kubectl top` step doesn't say what to compare against (the 200m limit is three sections away). | Low |
| 9 | The 10-minute escalation budget is "generous to the point of being unhelpful" when the documented path takes under a minute. | Low |

> Their bottom line: *"The diagnostic path (Steps 1→5) is genuinely good… The weakness is
> entirely at the two ends: routing in, and closing out. Fix the GitOps gap first; it is the
> one that will cause a repeat incident."*

Finding #3 is also what corrected my own wrong conclusion back in §6.6. Their fix
**surviving** unreverted is the control case that proves `selfHeal: false` works exactly as
documented, and that the earlier revert was just a coincidental revision sync.

### B.3 — Runbook v2, updated from the feedback

Every one of the nine findings is addressed. Changes are marked **[Bn]**.

> **Runbook: QuickTicket Payment Latency / Gateway Timeouts**
>
> **Severity:** critical (page) · **Owner:** QuickTicket on-call · **v2, 2026-09-22**
> (updated after a blind peer test, see §B.2)

#### Alert
- **[B1][B2] This runbook is reached from the alert `QuickTicket High Error Rate` when the
  5xx are `504`.** There is one alert; there are two runbooks, and the status code chooses
  between them. If the 5xx are `500` or `503`, stop and use
  [Runbook: QuickTicket High Error Rate](#runbook-quickticket-high-error-rate) instead.
- **Page you will have received:** *"Gateway error rate is N%"*, a 5xx ratio over 5 minutes,
  above 5%, sustained 2 minutes. It does **not** tell you whether the cause is slow or broken.
  Step 1 does.
- **Dashboard:** QuickTicket: Golden Signals, "Latency (p50 / p95 / p99)" panel.
- **User symptom:** checkout spins for several seconds and then fails; browsing and reserving
  still work.
- **[B6] Known-good baselines for this service** (measured 2026-09-22, ~1.3 rps):

  | Signal | Normal | Incident value seen |
  |---|---|---|
  | gateway p99 (`[5m]`) | **≈ 0.03 s** | 7.14 s |
  | payments p99 (`[5m]`) | **≈ 0.03 s** | 7.28 s |
  | `POST /charge` hand-timed | **< 0.05 s** | 6.21 s |
  | `GATEWAY_TIMEOUT_MS` | 5000 | 5000 (unchanged) |
  | payments CPU (`kubectl top`) | 4–10 m of a **200 m** limit | 4 m |
  | 5xx ratio (`[5m]`) | ≈ 0.5% | 8.5% |

#### 0. Before you start
```bash
kubectl config use-context k3d-quickticket
kubectl port-forward -n default    svc/gateway    3080:8080 &
kubectl port-forward -n monitoring svc/prometheus 9090:9090 &
kubectl port-forward -n monitoring svc/grafana    3000:3000 &
```

#### 1. Separate "slow" from "broken" — do this first
```bash
curl -s -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum by (status,path) (rate(gateway_requests_total[5m]))' | jq -r \
  '.data.result[] | "\(.metric.status)\t\(.metric.path)\t\(.value[1])"' | sort
```
- **504** ⇒ the gateway gave up waiting on a downstream. **Continue with this runbook.**
- **500** ⇒ downstream returned an error → [High Error Rate runbook](#runbook-quickticket-high-error-rate).
- **503** ⇒ downstream unreachable → [High Error Rate runbook](#runbook-quickticket-high-error-rate).

#### 2. Confirm latency, and find where it is spent
```bash
# gateway-side
curl -s -G http://localhost:9090/api/v1/query --data-urlencode \
 'query=histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket[5m])))' \
 | jq -r '.data.result[0].value[1]'
# payments-side
curl -s -G http://localhost:9090/api/v1/query --data-urlencode \
 'query=histogram_quantile(0.99, sum by (le) (rate(payments_request_duration_seconds_bucket[5m])))' \
 | jq -r '.data.result[0].value[1]'
```
Compare both against the **≈ 0.03 s** baseline above.
**Both high** ⇒ payments is genuinely slow (go on). **Only the gateway high** ⇒ suspect the
gateway's own HTTP client / connection pool.

#### 3. Time the dependency by hand
```bash
kubectl exec -n default deploy/gateway -- python -c "
import httpx,time
t=time.time(); r=httpx.post('http://payments:8082/charge',json={'reservation_id':'runbook-probe','amount':0},timeout=30)
print(r.status_code, round(time.time()-t,3), 's')"
```
A `/charge` slower than `GATEWAY_TIMEOUT_MS` (default **5000 ms**) is the root cause.
`/health` stays fast. **Health checks do not catch this, which is why every pod looks
green.**
**[B7] ⚠️ This probe performs a real charge** and leaves a `Payment success: PAY-… for
runbook-probe` entry in the payments log. Harmless in this environment; in a system with a
real provider, use a dedicated test reservation id and reconcile it afterwards.

#### 4. Read the injected configuration
```bash
kubectl exec -n default deploy/gateway -- \
  python -c "import httpx;print(httpx.get('http://payments:8082/health').text)"
kubectl get deploy payments -n default -o jsonpath='{.spec.template.spec.containers[0].env}'; echo
kubectl get deploy gateway  -n default -o jsonpath='{.spec.template.spec.containers[0].env}'; echo
```
`payments /health` echoes `latency_ms`. A non-zero value is the answer. Also check
`GATEWAY_TIMEOUT_MS`: the same symptom appears if *payments got slower* **or** if *the
timeout got shorter*.

#### 5. Logs and saturation
```bash
kubectl logs -n default deploy/payments --tail=20 --since=5m   # "Injecting NNNNms latency"
kubectl logs -n default deploy/gateway  --tail=20 --since=5m
kubectl top pods -n default   # [B8] compare payments against its 200m CPU limit
```

#### Common causes

| Cause | How to identify | Fix |
|---|---|---|
| Payments artificially slow (`PAYMENT_LATENCY_MS`) | `payments /health` shows `latency_ms > 0`; payments logs `Injecting NNNNms latency`; **payments p99 ≈ that value, i.e. far above the 0.03 s baseline** | set `PAYMENT_LATENCY_MS=0` (see Mitigation) |
| **[B6]** Gateway timeout set too low | `GATEWAY_TIMEOUT_MS` is **lower than it was** (compare to the 5000 baseline above) **and payments p99 is still ≈ 0.03 s**. If payments p99 is high, it is the row above, not this one. | restore `GATEWAY_TIMEOUT_MS`; do not raise it past ~3 s of user patience |
| Payments CPU-throttled | `kubectl top pods` shows payments near its **200 m** limit | raise `resources.limits.cpu` in `k8s/payments.yaml`, or `kubectl scale deploy/payments --replicas=2` |
| Postgres slow → events slow | 504s on `/events*` too, not only on pay | check postgres pod and `kubectl top pods` |

#### Mitigation — **[B3] this cluster is GitOps-managed, read this before you fix anything**

```bash
kubectl get application quickticket -n argocd \
  -o jsonpath='{.status.sync.status}{" selfHeal="}{.spec.syncPolicy.automated.selfHeal}{"\n"}'
```

- `kubectl set env` **stops the bleeding but is not a fix.** git remains the source of truth
  and still holds the broken value.
- With `selfHeal: false` (current setting) your change is not reverted immediately, but
  **the next ArgoCD sync of any revision, pushed by anyone for any reason, silently restores
  the broken value.** With `selfHeal: true` it is reverted within minutes.
- **The incident is not closed until ArgoCD reports `Synced` on a revision containing the
  fix.**

**Stop the bleeding (optional, if seconds matter):**
```bash
kubectl patch application quickticket -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":null}}}'      # stop any sync racing you
kubectl set env deploy/payments -n default PAYMENT_LATENCY_MS=0
kubectl rollout status deploy/payments -n default
```

**Then land the real fix (never skip this):**
```bash
$EDITOR k8s/payments.yaml            # PAYMENT_LATENCY_MS: "0"
git commit -am "fix(payments): drop injected latency"
git push cluster HEAD:refs/heads/main
kubectl annotate application quickticket -n argocd argocd.argoproj.io/refresh=normal --overwrite
# if the bad value came from a known commit, prefer: git revert --no-edit <sha> && git push cluster HEAD:refs/heads/main
kubectl patch application quickticket -n argocd --type=merge \
  -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":false}}}}'   # if you suspended it
```

#### Verify — in this order

**[B4] 1. Wait ~90 seconds before believing the latency metric.** The `[1m]` histogram window
has to roll over; queried immediately after a successful rollout it still returns the broken
value (a tester measured **7.04 s** four seconds after the fix landed and nearly concluded it
had failed).
```bash
sleep 90
curl -s -G http://localhost:9090/api/v1/query --data-urlencode \
 'query=histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))' \
 | jq -r '.data.result[0].value[1]'          # expect < 0.5 s, baseline ≈ 0.03 s
```

**2. Confirm a real purchase, end to end** (expect < 0.1 s):
```bash
RID=$(curl -s -X POST -H 'Content-Type: application/json' -d '{"quantity":1}' \
  http://localhost:3080/events/3/reserve | jq -r .reservation_id)
time curl -s -X POST http://localhost:3080/reserve/$RID/pay | jq .
```

**3. Confirm git and the cluster agree**, otherwise you have not fixed anything:
```bash
kubectl get application quickticket -n argocd \
  -o jsonpath='{.status.sync.status}{" "}{.status.health.status}{"\n"}'    # expect: Synced Healthy
```

**[B5] 4. Confirm the alert actually cleared before you stand down.** Do not close the page
on "the graph looks better":
```bash
curl -s http://localhost:3000/api/prometheus/grafana/api/v1/rules \
  | jq -r '.data.groups[].rules[] | select(.name|startswith("QuickTicket")) | "\(.name)\t\(.state)"'
# expect both "inactive"; a resolved webhook also lands in:
kubectl logs -n monitoring deploy/webhook-receiver --tail=40
```
The critical alert clears ~5 min after the last 504 leaves its 5m window. **`QuickTicket SLO
Burn Rate` uses a 30-minute window and will stay firing for up to ~20 minutes after full
recovery. That is expected and is not a reason to keep the incident open.**

#### Escalation
**[B9] Per-step budgets**: the documented path takes under a minute; escalate on the step,
not on a global 10-minute clock.

| Point | Escalate if |
|---|---|
| Step 1 | the 5xx are not 504 → switch runbooks immediately, do not spend time here |
| Steps 2–4 | 3 minutes without a non-baseline number |
| Step 5 / causes | 5 minutes without matching a row in Common Causes |
| After mitigation | p99 still above baseline 3 minutes after the 90 s settle, **or** ArgoCD not `Synced` |
| Any time | error rate > 25%, or `QuickTicket SLO Burn Rate` also firing → declare SEV-2, freeze deploys, page the instructor / TA |

---

## Acceptance criteria

### Task 1 (6 pts)
- ✅ **Two alert rules created in Grafana (error rate + burn rate)**: `QuickTicket High Error
  Rate` (critical, `>5%`, `for: 2m`) and `QuickTicket SLO Burn Rate` (warning, `>6x`,
  `for: 5m`), provisioned as code in
  `monitoring/grafana/provisioning/alerting/alert-rules.yaml`, confirmed live via the
  provisioning API (§6.3).
- ✅ **Contact point configured and tested**: `quickticket-alerts` (webhook). Tested with
  Grafana's own *Test* button; the payload is in the receiver's log (§6.2).
- ✅ **Runbook written with diagnosis + mitigation + escalation**: §6.5, and it was the
  document actually followed in §6.6.
- ✅ **Alert fired during failure injection**: Grafana rule API, Alertmanager API, UI, and
  the delivered webhook payload all shown (§6.6).
- ✅ **Timeline recorded from injection to resolution**: 16-row timeline plus a 30-second
  measured series (§6.6, §6.7).
- ✅ **Written answer about alert delay**: §6.7, with the delay decomposed into five terms.

### Task 2 (4 pts)
- ✅ Full blameless postmortem following the template.
- ✅ Focus on systems, not blame: the root cause is stated as a missing guardrail, and the
  one place a human could be blamed (the deliberate 3.5-minute delay before fixing) is
  counted as impact rather than excused.
- ✅ Action items are specific and assigned.

### Bonus Task (2 pts)
- ✅ **Second runbook for a different failure mode**: *Payment Latency / Gateway Timeouts*
  (slow dependency → `504`), distinct from Task 1's broken dependency → `500` (§B.1).
- ⚠️ **Peer tested**: tested blind by a separate, deliberately uninformed operator rather
  than by a human classmate; see the boxed disclosure at the top of the Bonus section. The
  fault was injected before the tester was paged, the tester was denied access to the repo,
  and the timings, transcript and critique in §B.2 are real.
- ✅ **Runbook updated based on feedback**: all nine findings addressed in v2 (§B.3), each
  change marked `[Bn]` against the finding that caused it. The most important one (the
  GitOps mitigation gap) was also fed back into the Task 1 runbook and into postmortem
  action item A6.

---

## Known gaps / things not done for real

- ⚠️ **No human classmate was available**, so I ran the Bonus Task's peer test against a
  fresh, deliberately uninformed operator instead. Exactly what that means is spelled out in
  the Bonus section: the transcript is real, the "classmate" is not a person.
- ⚠️ **webhook.site wasn't used.** The contact point posts to an in-cluster receiver instead,
  for the reasons in §6.2. The Grafana side of the integration (contact point type, test
  button, notification policy, delivery, repeat, resolve) is identical.
- ⚠️ The Compose-based commands in the lab text (`docker compose logs`, `docker compose start
  payments`) were **not** run, that stack no longer exists. I used the kubectl equivalents
  instead.

---

## PR description

```text
- [x] Task 1 done — alerts created, incident simulated, runbook followed
- [x] Task 2 done — blameless postmortem written
- [x] Bonus Task done — cross-tested runbook with classmate
```
