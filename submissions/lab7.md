*Emil Nabiullin, e.nabiullin@innopolis.university*
---
# Lab 7 - Progressive Delivery: Canary Deployments

## Repository state

For this lab I changed only the gateway progressive-delivery configuration and added the analysis template:

- `k8s/gateway.yaml` was converted from `Deployment` to `Rollout`
- `k8s/analysis-template.yaml` was added for the Prometheus-based canary check
- lab-provided assets from `labs/lab7/` were used for in-cluster Prometheus and load generation

The host environment did not have `kubectl` or `k3d`, and the k3s node could not pull the existing `ghcr.io/deldore/...` images (`403 Forbidden` from GHCR inside the cluster runtime), so for the local verification run I built the application images from the current repository and imported them into `k3d`. I did not keep those image-source workarounds in the final repo diff; the submitted manifest changes stay focused on the Rollout strategy and the AnalysisTemplate.

## Tooling and base cluster

`kubectl argo rollouts version`:

```bash
kubectl argo rollouts version
```

```text
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

The k3d cluster and the initial QuickTicket deployment were healthy before the canary tests:

```bash
kubectl get pods -o wide
```

```text
NAME                        READY   STATUS    RESTARTS   AGE
events-5b65b6bcfb-h44ls     1/1     Running   0          40s
gateway-854d489886-v69vg    1/1     Running   0          40s
payments-6bdd4c8b4f-pvtsj   1/1     Running   0          40s
postgres-85ffd4fb9f-4qbgv   1/1     Running   0          3m
redis-6d65768944-ftmml      1/1     Running   0          3m
```

After seeding PostgreSQL, the gateway returned healthy responses:

```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/events
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

```json
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]
```

## Task 1 - Manual canary deployment

### Rollout conversion

`k8s/gateway.yaml` now uses `argoproj.io/v1alpha1`, `kind: Rollout`, `replicas: 5`, and a canary strategy. I verified Task 1 first with a shorter manual strategy (`20 -> pause -> 60 -> pause -> 100`). After that I expanded the same Rollout to the final multi-step strategy kept in the repository for Task 2 and Bonus:

```yaml
strategy:
  canary:
    maxSurge: 1
    maxUnavailable: 0
    steps:
      - setWeight: 20
      - pause: {duration: 20s}
      - analysis:
          templates:
            - templateName: gateway-error-rate
          args:
            - name: canary-hash
              valueFrom:
                podTemplateHashValue: Latest
      - setWeight: 40
      - pause: {duration: 20s}
      - setWeight: 60
      - pause: {duration: 20s}
      - setWeight: 80
      - pause: {duration: 20s}
      - setWeight: 100
```

### Canary at 20%

For the first manual canary I changed the gateway image marker from `v1` to `v2` and waited for the pause step:

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          quickticket-gateway:v1 (stable)
                 quickticket-gateway:v2 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
```

### Traffic split check

I used the in-cluster `labs/lab7/loadgen.yaml` and counted `/events` requests per gateway pod:

```bash
kubectl apply -f labs/lab7/loadgen.yaml
sleep 30
for pod in $(kubectl get pods -l app=gateway -o name); do
  count=$(kubectl logs "$pod" 2>/dev/null | grep -c 'GET /events')
  image=$(kubectl get "$pod" -o jsonpath='{.spec.containers[0].image}')
  echo "$pod image=$image events_requests=$count"
done
kubectl delete -f labs/lab7/loadgen.yaml
```

```text
pod/gateway-69b9fcd6c-pxwjk image=quickticket-gateway:v2 events_requests=22
pod/gateway-74b54c6b54-8ch9p image=quickticket-gateway:v1 events_requests=24
pod/gateway-74b54c6b54-p8bps image=quickticket-gateway:v1 events_requests=17
pod/gateway-74b54c6b54-wdhrt image=quickticket-gateway:v1 events_requests=15
pod/gateway-74b54c6b54-zvp8c image=quickticket-gateway:v1 events_requests=20
```

The canary handled `22 / 98 = 22.4%` of the sampled `/events` traffic, which is consistent with the configured `20%` split.

### Manual promote to 100%

After `kubectl argo rollouts promote gateway`, the rollout first advanced to `60%` and then completed:

```bash
kubectl argo rollouts promote gateway
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
Images:          quickticket-gateway:v1 (stable)
                 quickticket-gateway:v2 (canary)
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

### Manual abort

I started another rollout, paused it at `20%`, and aborted it:

```bash
date --iso-8601=seconds
kubectl argo rollouts abort gateway
date --iso-8601=seconds
kubectl argo rollouts get rollout gateway
```

```text
abort_start=2026-07-03T16:07:55+03:00
abort_end=2026-07-03T16:08:02+03:00
abort_seconds=7
rollout 'gateway' aborted
```

```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 4
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5
```

The rollback to stable-only traffic took 7 seconds in this run. This is much faster than the GitOps rollback style from Lab 5 because `kubectl argo rollouts abort` does not need a commit, push, or ArgoCD reconciliation loop.

## Task 2 - Multi-step canary with observation

The final rollout strategy used `20 -> 40 -> 60 -> 80 -> 100` with pauses between steps and an analysis step after the initial `20%` canary.

During the successful rollout, I observed at least three distinct steps:

```bash
kubectl argo rollouts get rollout gateway --watch
```

```text
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Strategy:        Canary
  Step:          2/10
  SetWeight:     20
  ActualWeight:  20
```

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Strategy:        Canary
  Step:          6/10
  SetWeight:     60
  ActualWeight:  60
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          10/10
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v2 (stable)
```

For the rollout observation, I used the port-forwarded in-cluster Prometheus expression browser as the dashboard view and then recorded the same series through the API. Request rate stayed steady and the gateway 5xx rate stayed at zero:

```bash
curl -s 'http://127.0.0.1:9091/api/v1/query?query=sum(rate(gateway_requests_total%5B1m%5D))'
curl -s 'http://127.0.0.1:9091/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))'
```

```text
sum(rate(gateway_requests_total[1m])) = 9.636140242424242
sum(rate(gateway_requests_total{status=~"5.."}[1m])) = 0
```

I would put an automated abort at the first `20%` stage, exactly where the analysis step is now. It is the cheapest point to reject a bad build because only one of five pods is exposed, so the blast radius is still small.

## Bonus task - Automated canary analysis

### AnalysisTemplate and in-cluster Prometheus

`kubectl get analysistemplate gateway-error-rate`:

```bash
kubectl apply -f k8s/analysis-template.yaml
kubectl get analysistemplate gateway-error-rate
```

```text
NAME                 AGE
gateway-error-rate   0s
```

Prometheus discovered the gateway pods with the rollout hash relabel:

```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
curl -s 'http://127.0.0.1:9091/api/v1/targets?state=active' | python3 -c "
import sys, json
for t in json.load(sys.stdin)['data']['activeTargets']:
    if t['labels'].get('job') == 'gateway':
        print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])
"
kill %1 2>/dev/null
```

```text
gateway-69b9fcd6c-trqt2 rs= 69b9fcd6c up
gateway-69b9fcd6c-7lfd9 rs= 69b9fcd6c up
gateway-69b9fcd6c-kwn6r rs= 69b9fcd6c up
gateway-69b9fcd6c-pxwjk rs= 69b9fcd6c up
gateway-69b9fcd6c-tgcbm rs= 69b9fcd6c up
```

### Successful automated analysis

The good rollout completed with a successful analysis run:

```bash
kubectl get analysisrun
```

```text
NAME                     STATUS       AGE
gateway-8689676b85-5-2   Successful   15m
```

At the end of the good rollout:

```bash
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          10/10
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v2 (stable)
```

### Failed automated analysis and auto-abort

The direct `EVENTS_URL=broken-on-purpose` approach made the canary fail readiness before it could reach the analysis step because `/health` in this application checks downstream dependencies. To test the automated abort honestly, I kept the canary healthy and instead generated real 5xx on user traffic with `labs/lab8/mixedload.yaml` plus temporary `PAYMENT_FAILURE_RATE=1.0` on the shared payments service. Under this load, the gateway 5xx ratio rose above the `0.05` threshold:

```bash
kubectl apply -f labs/lab8/mixedload.yaml
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0
curl -s 'http://127.0.0.1:9091/api/v1/query?query=sum(rate(gateway_requests_total%7Bstatus%3D~%225..%22%7D%5B1m%5D))%20/%20sum(rate(gateway_requests_total%5B1m%5D))'
```

```text
sum(rate(gateway_requests_total{status=~"5.."}[1m])) / sum(rate(gateway_requests_total[1m])) = 0.406114009512913
```

The failed analysis run:

```bash
kubectl get analysisrun
```

```text
NAME                     STATUS       AGE
gateway-5db9cdb4f-9-2    Failed       3m2s
gateway-6db964b75c-8-2   Successful   6m30s
gateway-8689676b85-5-2   Successful   15m
```

Relevant excerpt from `kubectl get analysisrun gateway-5db9cdb4f-9-2 -o yaml`:

```bash
kubectl get analysisrun gateway-5db9cdb4f-9-2 -o yaml
```

```yaml
status:
  completedAt: "2026-07-03T13:25:16Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - phase: Failed
      value: '[0.4423963133640553]'
    - phase: Failed
      value: '[0.4370642029794668]'
    name: error-rate
    phase: Failed
  phase: Failed
```

Rollout state after the automatic abort:

```bash
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 9: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/10
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v2 (stable)
```

Beyond error rate, I would add a latency-based metric for the same canary hash, for example the p95 of `gateway_request_duration_seconds`. A version can stay below the 5xx threshold and still be bad for users because it times out or becomes much slower.

## Final state

After the failed analysis test, I restored `PAYMENT_FAILURE_RATE=0.0`, removed `mixedload`, reapplied the good gateway spec, and confirmed the rollout returned to `Healthy`:

```bash
kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0
kubectl delete -f labs/lab8/mixedload.yaml
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          10/10
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

The application was healthy again at the gateway endpoint:

```bash
curl -s http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/events | python3 -c 'import sys,json; data=json.load(sys.stdin); print(json.dumps(data[:2], separators=(",", ":")))'
```

```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

```json
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":84},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25}]
```
