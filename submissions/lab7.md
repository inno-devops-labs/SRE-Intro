# Lab 7

## Task 1

I verified that Argo Rollouts was installed and that the plugin worked from the `k3d` server container.

```bash
docker exec k3d-quickticket-server-0 kubectl argo rollouts version
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

For the manual canary check I first switched `gateway` to a short temporary strategy with a manual pause at `20%`, then deployed a new gateway image:

```bash
docker exec k3d-quickticket-server-0 kubectl patch rollout gateway --type merge -p \
  '{"spec":{"strategy":{"canary":{"maxSurge":1,"maxUnavailable":0,"steps":[{"setWeight":20},{"pause":{}},{"setWeight":60},{"pause":{"duration":"30s"}},{"setWeight":100}]}}}}'
docker exec k3d-quickticket-server-0 kubectl argo rollouts set image gateway \
  gateway=ghcr.io/krojiak/quickticket-gateway:6e00f402a3d7fefd8dd296dcc76bb18348f3287a
docker exec k3d-quickticket-server-0 kubectl argo rollouts get rollout gateway
```

```text
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Replicas:
  Desired:       5
  Updated:       1
  Ready:         5
```

I used the provided in-cluster load generator and counted `GET /events` requests in gateway logs. The canary pod received about one fifth of the traffic:

```bash
cat labs/lab7/loadgen.yaml | docker exec -i k3d-quickticket-server-0 kubectl apply -f -
docker exec k3d-quickticket-server-0 sh -lc '
  for pod in $(kubectl get pods -l app=gateway -o name); do
    count=$(kubectl logs "$pod" --since=30s 2>/dev/null | grep -c "GET /events")
    img=$(kubectl get "$pod" -o jsonpath="{.spec.containers[0].image}")
    echo "$pod image=$img events_requests=$count"
  done
'
```

```text
pod/gateway-647cd65c6f-4q5f8 events_requests=24
pod/gateway-647cd65c6f-6rxl8 events_requests=30
pod/gateway-647cd65c6f-r52jf events_requests=25
pod/gateway-647cd65c6f-r75pv events_requests=26
pod/gateway-6d99b8498-wlbmh events_requests=9
```

After that I promoted the rollout:

```bash
docker exec k3d-quickticket-server-0 kubectl argo rollouts promote gateway
docker exec k3d-quickticket-server-0 kubectl argo rollouts get rollout gateway
```

```text
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/krojiak/quickticket-gateway:6e00f402a3d7fefd8dd296dcc76bb18348f3287a (stable)
Replicas:
  Desired:       5
  Updated:       5
  Ready:         5
```

I then deployed a broken image tag and aborted the rollout:

```bash
docker exec k3d-quickticket-server-0 kubectl argo rollouts set image gateway \
  gateway=ghcr.io/krojiak/quickticket-gateway:does-not-exist
docker exec k3d-quickticket-server-0 kubectl argo rollouts get rollout gateway
docker exec k3d-quickticket-server-0 kubectl argo rollouts abort gateway
docker exec k3d-quickticket-server-0 kubectl argo rollouts get rollout gateway
```

```text
Status:          ◌ Progressing
Strategy:        Canary
  Step:          0/5
  SetWeight:     20
  ActualWeight:  0
Images:          ghcr.io/krojiak/quickticket-gateway:6e00f402a3d7fefd8dd296dcc76bb18348f3287a (stable)
                 ghcr.io/krojiak/quickticket-gateway:does-not-exist (canary)
...
gateway-7fb45c84d9-nl5pk  Pod  ⚠ ErrImagePull  ready:0/1
```

```text
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 17
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/krojiak/quickticket-gateway:6e00f402a3d7fefd8dd296dcc76bb18348f3287a (stable)
Replicas:
  Desired:       5
  Updated:       0
  Ready:         5
```

The abort returned the rollout to stable-only traffic in less than `10s` in this run. This is faster than the Lab 5 rollback because `kubectl argo rollouts abort` does not need a Git revert, a push, and an ArgoCD sync cycle.

## Task 2

The final manifest in `k8s/gateway.yaml` is a `Rollout` with a multi-step canary strategy and an analysis step after the first `20%` phase:

```yaml
strategy:
  canary:
    maxSurge: 1
    maxUnavailable: 0
    steps:
      - setWeight: 20
      - pause:
          duration: 20s
      - analysis:
          templates:
            - templateName: gateway-error-rate
          args:
            - name: canary-hash
              valueFrom:
                podTemplateHashValue: Latest
      - setWeight: 40
      - pause:
          duration: 20s
      - setWeight: 60
      - pause:
          duration: 20s
      - setWeight: 80
      - pause:
          duration: 20s
      - setWeight: 100
```

After applying the final manifest I observed the rollout at several steps:

```bash
cat k8s/gateway.yaml | docker exec -i k3d-quickticket-server-0 kubectl apply -f -
docker exec k3d-quickticket-server-0 kubectl argo rollouts get rollout gateway
```

```text
Status:          ◌ Progressing
Strategy:        Canary
  Step:          2/10
  SetWeight:     20
  ActualWeight:  20
...
gateway-b74ddbdcd-18-2  AnalysisRun  ◌ Running
```

```text
Status:          ◌ Progressing
Strategy:        Canary
  Step:          3/10
  SetWeight:     40
  ActualWeight:  20
Replicas:
  Desired:       5
  Updated:       2
  Ready:         5
...
gateway-b74ddbdcd-18-2  AnalysisRun  ✔ Successful  ✔ 3
```

I also checked the request rate directly from Prometheus while the rollout was paused at `20%`. Both the stable and the canary ReplicaSet had non-zero traffic:

```bash
cat labs/lab7/loadgen.yaml | docker exec -i k3d-quickticket-server-0 kubectl apply -f -
docker exec k3d-quickticket-server-0 sh -lc '
  POD=$(kubectl get pods -n monitoring -l app=prometheus -o jsonpath="{.items[0].metadata.name}")
  kubectl exec -n monitoring "$POD" -- wget -qO- \
    "http://localhost:9090/api/v1/query?query=sum%20by%20(rs_hash)(rate(gateway_requests_total%5B1m%5D))"
' | python3 -c 'import json,sys; j=json.load(sys.stdin); [print(f"rs_hash={r[\"metric\"].get(\"rs_hash\",\"\")} rate={r[\"value\"][1]}") for r in j["data"]["result"]]'
```

```text
rs_hash=b74ddbdcd rate=8.599720303030303
rs_hash=5d56b44cb9 rate=0.9902955555555555
```

```text
Status:          ✔ Healthy
Strategy:        Canary
  Step:          10/10
  SetWeight:     100
  ActualWeight:  100
Replicas:
  Desired:       5
  Updated:       5
  Ready:         5
```

The request rate stayed stable during the rollout because the traffic generator kept sending requests through the ClusterIP service. Replica counts changed as expected: `1` canary pod at `20%`, then `2`, then `5` by the end.

I would add automated abort already at `20%`. With `5` replicas this means only one canary pod receives production traffic, so the blast radius stays small while Prometheus still gets enough data for a decision.

## Bonus Task

I added `k8s/analysis-template.yaml` and verified that the template exists in the cluster:

```bash
cat k8s/analysis-template.yaml | docker exec -i k3d-quickticket-server-0 kubectl apply -f -
docker exec k3d-quickticket-server-0 kubectl get analysistemplate gateway-error-rate
```

```text
NAME                 AGE
gateway-error-rate   4h42m
```

The cluster contains both a successful and a failed run for this template:

```bash
docker exec k3d-quickticket-server-0 kubectl get analysisrun \
  gateway-5d56b44cb9-23-2 gateway-6b7ddcb957-21-2
```

```text
NAME                     STATUS       AGE
gateway-5d56b44cb9-23-2  Successful   35s
gateway-6b7ddcb957-21-2  Failed       3m49s
```

The failed run shows that the measured canary error ratio was far above the `5%` threshold:

```bash
docker exec k3d-quickticket-server-0 kubectl get analysisrun gateway-6b7ddcb957-21-2 -o yaml
```

```text
status:
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - phase: Failed
      value: '[0.38461538461538464]'
    - phase: Failed
      value: '[0.34374999999999994]'
    name: error-rate
    phase: Failed
  phase: Failed
```

The rollout state immediately after the failed canary shows automatic abort and stable pods still serving traffic:

```bash
docker exec k3d-quickticket-server-0 kubectl argo rollouts get rollout gateway
```

```text
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 21: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/10
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/krojiak/quickticket-gateway:6e00f402a3d7fefd8dd296dcc76bb18348f3287a (stable)
Replicas:
  Desired:       5
  Updated:       0
  Ready:         5
```

Beyond error rate, I would add a latency metric such as `p95` for `GET /events` and `POST /reserve/{id}/pay`. A canary can stay below the error threshold but still be too slow for users.
