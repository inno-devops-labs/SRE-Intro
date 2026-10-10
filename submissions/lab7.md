# Lab 7 — Progressive Delivery: Canary Deployments

## Overview

In this lab, I installed Argo Rollouts, converted the `gateway` Kubernetes Deployment into an Argo Rollout, performed a manual canary deployment, verified traffic splitting, promoted a good version, and then simulated a bad version followed by an abort.

The main goal was to practice progressive delivery and compare canary abort behavior with a traditional rollback using Git revert.

---

## Task 1 — Manual Canary Deployment

### 7.1 Install Argo Rollouts

I created the `argo-rollouts` namespace and installed the Argo Rollouts controller.

Command:

```bash
kubectl create namespace argo-rollouts
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=60s
```

Output:

```bash
✗ kubectl create namespace argo-rollouts
namespace/argo-rollouts created
➜  SRE-Intro git:(feature/lab6) ✗ kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
^C
➜  SRE-Intro git:(feature/lab6) ✗ kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
customresourcedefinition.apiextensions.k8s.io/analysisruns.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/analysistemplates.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/clusteranalysistemplates.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/experiments.argoproj.io created
customresourcedefinition.apiextensions.k8s.io/rollouts.argoproj.io created
serviceaccount/argo-rollouts created
clusterrole.rbac.authorization.k8s.io/argo-rollouts created
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-admin created
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-edit created
clusterrole.rbac.authorization.k8s.io/argo-rollouts-aggregate-to-view created
clusterrolebinding.rbac.authorization.k8s.io/argo-rollouts created
configmap/argo-rollouts-config created
secret/argo-rollouts-notification-secret created
service/argo-rollouts-metrics created
deployment.apps/argo-rollouts created
➜  SRE-Intro git:(feature/lab6) ✗ kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=60s
deployment.apps/argo-rollouts condition met
```

Then I installed the `kubectl argo rollouts` plugin.

Command:

```bash
curl -LO https://github.com/argoproj/argo-rollouts/releases/latest/download/kubectl-argo-rollouts-linux-amd64
chmod +x kubectl-argo-rollouts-linux-amd64
sudo mv kubectl-argo-rollouts-linux-amd64 /usr/local/bin/kubectl-argo-rollouts
kubectl argo rollouts version
```

Output:

```bash
  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
                                 Dload  Upload   Total   Spent    Left  Speed
  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0
  0     0    0     0    0     0      0      0 --:--:-- --:--:-- --:--:--     0
100  127M  100  127M    0     0  12.1M      0  0:00:10  0:00:10 --:--:-- 8839k
[sudo] password for slickip: 
➜  SRE-Intro git:(feature/lab6) ✗ kubectl argo rollouts version
kubectl-argo-rollouts: v1.9.0+838d4e7
  BuildDate: 2026-03-20T21:08:11Z
  GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e
  GitTreeState: clean
  GoVersion: go1.24.13
  Compiler: gc
  Platform: linux/amd64
```

---

### 7.2 Convert Gateway Deployment to Rollout

I updated `k8s/gateway.yaml` by replacing the standard Kubernetes `Deployment` with an Argo Rollouts `Rollout`.

Main changes:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: gateway
spec:
  replicas: 5
  strategy:
    canary:
      steps:
        - setWeight: 20
        - pause: {}
        - setWeight: 60
        - pause: {duration: 30s}
        - setWeight: 100
```

I kept the existing selector, pod template, container configuration, environment variables, ports, and probes from the previous gateway manifest.

Then I deleted the old Deployment and applied the new Rollout manifest.

Command:

```bash
kubectl delete deployment gateway
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ kubectl delete deployment gateway
deployment.apps "gateway" deleted
➜  SRE-Intro git:(feature/lab6) ✗ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway created
service/gateway configured
➜  SRE-Intro git:(feature/lab6) ✗ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ✔ Healthy  15s  
└──# revision:1                                                  
   └──⧉ gateway-7b85b56596           ReplicaSet  ✔ Healthy  15s  stable
      ├──□ gateway-7b85b56596-6tb68  Pod         ✔ Running  15s  ready:1/1
      ├──□ gateway-7b85b56596-8nw52  Pod         ✔ Running  15s  ready:1/1
      ├──□ gateway-7b85b56596-cdkpn  Pod         ✔ Running  15s  ready:1/1
      ├──□ gateway-7b85b56596-fxdpz  Pod         ✔ Running  15s  ready:1/1
      └──□ gateway-7b85b56596-vdk68  Pod         ✔ Running  15s  ready:1/1
```

---

### 7.3 Deploy a New Version Using Canary

To trigger a canary deployment, I changed the gateway configuration to represent a new version.

Example change in `k8s/gateway.yaml`:

```yaml
env:
  - name: APP_VERSION
    value: "v2"
```

Then I applied the updated manifest.

Command:

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

Output showing the rollout paused at 20%:

```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      11m    
├──# revision:3                                                       
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy     41s    canary
│     └──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running     40s    ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown  6m32s  
└──# revision:1                                                       
   └──⧉ gateway-7b85b56596           ReplicaSet  ✔ Healthy     11m    stable
      ├──□ gateway-7b85b56596-6tb68  Pod         ✔ Running     11m    ready:1/1
      ├──□ gateway-7b85b56596-8nw52  Pod         ✔ Running     11m    ready:1/1
      ├──□ gateway-7b85b56596-cdkpn  Pod         ✔ Running     11m    ready:1/1
      └──□ gateway-7b85b56596-vdk68  Pod         ✔ Running     11m    ready:1/1
```

At this point, the rollout was paused at the first manual step. The expected state was:

* 5 gateway replicas in total;
* 4 stable pods running the old version;
* 1 canary pod running the new version;
* actual canary weight around 20%.

---

### 7.4 Verify Traffic Split

To verify traffic splitting correctly, I used an in-cluster load generator. This is necessary because `kubectl port-forward` can stick to a single endpoint and does not show real service-level traffic distribution.

Command:

```bash
kubectl apply -f labs/lab7/loadgen.yaml

sleep 30
for pod in $(kubectl get pods -l app=gateway -o name); do
  count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
  img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
  echo "$pod image=$img events_requests=$count"
done

kubectl delete -f labs/lab7/loadgen.yaml
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ kubectl apply -f labs/lab7/loadgen.yaml
deployment.apps/loadgen created
➜  SRE-Intro git:(feature/lab6) ✗ sleep 30
for pod in $(kubectl get pods -l app=gateway -o name); do
  count=$(kubectl logs $pod 2>/dev/null | grep -c 'GET /events')
  img=$(kubectl get $pod -o jsonpath='{.spec.containers[0].image}')
  echo "$pod image=$img events_requests=$count"
done
pod/gateway-7b85b56596-6tb68 image=ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 events_requests=14
pod/gateway-7b85b56596-8nw52 image=ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 events_requests=21
pod/gateway-7b85b56596-cdkpn image=ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 events_requests=17
pod/gateway-7b85b56596-vdk68 image=ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 events_requests=25
pod/gateway-855c8b68f8-9fwj5 image=ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 events_requests=25
➜  SRE-Intro git:(feature/lab6) ✗ kubectl delete -f labs/lab7/loadgen.yaml
deployment.apps "loadgen" deleted
```

The result showed that requests were distributed across gateway pods. Since the canary weight was configured to 20%, the observed traffic distribution was reasonably close to the expected split. Small deviations are normal because of the short observation period and the kube-proxy load balancing algorithm.

Small deviations are expected because the observation window was short.

---

### 7.5 Promote the Canary

After verifying that the new version worked correctly, I manually promoted the rollout to the next step.

Command:

```bash
kubectl argo rollouts promote gateway
kubectl argo rollouts get rollout gateway --watch
```

Output:

```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/5
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      16m    
├──# revision:3                                                       
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy     5m24s  canary
│     ├──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running     5m23s  ready:1/1
│     ├──□ gateway-855c8b68f8-455pj  Pod         ✔ Running     16s    ready:1/1
│     └──□ gateway-855c8b68f8-vw49p  Pod         ✔ Running     16s    ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown  11m    
└──# revision:1                                                       
   └──⧉ gateway-7b85b56596           ReplicaSet  ✔ Healthy     16m    stable
      ├──□ gateway-7b85b56596-6tb68  Pod         ✔ Running     16m    ready:1/1
      └──□ gateway-7b85b56596-8nw52  Pod         ✔ Running     16m    ready:1/1
```

The rollout moved from 20% to 60%. After the configured 30-second pause, it continued automatically to 100%.

Final rollout status:

```bash
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     17m    
├──# revision:3                                                       
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy     6m31s  stable
│     ├──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running     6m30s  ready:1/1
│     ├──□ gateway-855c8b68f8-455pj  Pod         ✔ Running     83s    ready:1/1
│     ├──□ gateway-855c8b68f8-vw49p  Pod         ✔ Running     83s    ready:1/1
│     ├──□ gateway-855c8b68f8-9nnlz  Pod         ✔ Running     43s    ready:1/1
│     └──□ gateway-855c8b68f8-zqh7q  Pod         ✔ Running     43s    ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown  12m    
└──# revision:1                                                       
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown  17m
```

The rollout eventually became `Healthy`, meaning the new version fully replaced the old stable version.

---

### 7.6 Deploy a Bad Version and Abort

Next, I simulated a bad version by changing the gateway version again.

Example change in `k8s/gateway.yaml`:

```yaml
env:
  - name: APP_VERSION
    value: "v3-bad"
```

Then I applied the manifest.

Command:

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

Output showing the bad version starting before abort:

```bash
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          0/5
  SetWeight:     20
  ActualWeight:  0
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ◌ Progressing  18m    
├──# revision:4                                                        
│  └──⧉ gateway-6cdb6cffc            ReplicaSet  ◌ Progressing  8s     canary
│     └──□ gateway-6cdb6cffc-s8cdd   Pod         ✔ Running      7s     ready:0/1
├──# revision:3                                                        
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy      7m31s  stable
│     ├──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running      7m30s  ready:1/1
│     ├──□ gateway-855c8b68f8-455pj  Pod         ✔ Running      2m23s  ready:1/1
│     ├──□ gateway-855c8b68f8-vw49p  Pod         ✔ Running      2m23s  ready:1/1
│     └──□ gateway-855c8b68f8-9nnlz  Pod         ✔ Running      103s   ready:1/1
├──# revision:2                                                        
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown   13m    
└──# revision:1                                                        
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown   18m
```

Once the bad canary started, I aborted the rollout.

Command:

```bash
kubectl argo rollouts abort gateway
kubectl argo rollouts get rollout gateway
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ kubectl argo rollouts abort gateway
rollout 'gateway' aborted
➜  SRE-Intro git:(feature/lab6) ✗ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 4
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ✖ Degraded     19m    
├──# revision:4                                                        
│  └──⧉ gateway-6cdb6cffc            ReplicaSet  • ScaledDown   51s    canary
├──# revision:3                                                        
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ◌ Progressing  8m14s  stable
│     ├──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running      8m13s  ready:1/1
│     ├──□ gateway-855c8b68f8-455pj  Pod         ✔ Running      3m6s   ready:1/1
│     ├──□ gateway-855c8b68f8-vw49p  Pod         ✔ Running      3m6s   ready:1/1
│     ├──□ gateway-855c8b68f8-9nnlz  Pod         ✔ Running      2m26s  ready:1/1
│     └──□ gateway-855c8b68f8-wn587  Pod         ✔ Running      6s     ready:0/1
├──# revision:2                                                        
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown   14m    
└──# revision:1                                                        
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown   19m
```

After aborting, the canary pod was removed and traffic continued to be served by the stable version.

## How long from abort to all traffic serving the stable version? Compare with git revert rollback from Lab 5.

After running `kubectl argo rollouts abort gateway`, traffic returned to the stable version almost immediately. The canary pod was removed, and the stable ReplicaSet continued serving traffic without requiring a new image build, Git commit, or ArgoCD synchronization cycle

In my case, the abort took less than 10 seconds

Compared with the Git revert rollback from Lab 5, Argo Rollouts abort is faster and safer for an active failed deployment. With Git revert, the rollback requires creating a revert commit, pushing it, waiting for CI/CD or ArgoCD reconciliation, and then waiting for Kubernetes to apply the reverted manifests. With Argo Rollouts, the controller already knows the stable ReplicaSet, so aborting the canary simply stops the rollout and shifts traffic back to the stable version

Therefore, canary abort provides a much faster operational rollback path than Git revert. Git revert is still useful for permanently fixing the desired state in Git, but Argo Rollouts abort is better for immediate incident response during a bad progressive deployment

## Task 2 — Multi-Step Canary with Observation

### 7.8 Multi-Step Canary Strategy

I updated the gateway Rollout strategy to use a more granular canary deployment. Instead of moving directly from 20% to 60%, this strategy gradually increases traffic through 20%, 40%, 60%, 80%, and then 100%.

Updated strategy in `k8s/gateway.yaml`:

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause:
          duration: 60s
      - setWeight: 40
      - pause:
          duration: 60s
      - setWeight: 60
      - pause:
          duration: 60s
      - setWeight: 80
      - pause:
          duration: 30s
      - setWeight: 100
```

This strategy gives more time to observe the application behavior before the new version receives all traffic.

---

### 7.9 Rollout Observation

I started the in-cluster load generator to continuously send traffic to the gateway service. Then I triggered a new rollout by updating the gateway version and applying the manifest.

I watched the rollout progress in real time.

Command:

```bash
kubectl argo rollouts get rollout gateway --watch
```

Output showing at least three rollout steps:

```bash
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5
  ├──# revision:4                                                       
│  └──⧉ gateway-6cdb6cffc            ReplicaSet  • ScaledDown  12m    
├──# revision:3                                                       
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy     20m    stable
│     ├──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running     20m    ready:1/1
│     └──□ gateway-855c8b68f8-455pj  Pod         ✔ Running     14m    ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown  25m    
└──# revision:1                                                       
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown  31m    

Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      31m    
├──# revision:5                                                       
│  └──⧉ gateway-5889c5c7bd           ReplicaSet  ✔ Healthy     2m51s  canary
│     ├──□ gateway-5889c5c7bd-42dxb  Pod         ✔ Running     2m50s  ready:1/1
│     ├──□ gateway-5889c5c7bd-qbtbf  Pod         ✔ Running     100s   ready:1/1
│     └──□ gateway-5889c5c7bd-g94fz  Pod         ✔ Running     30s    ready:1/1
├──# revision:4                                                       
│  └──⧉ gateway-6cdb6cffc            ReplicaSet  • ScaledDown  12m    
├──# revision:3                                                       
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy     20m    stable
│     ├──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running     20m    ready:1/1
│     └──□ gateway-855c8b68f8-455pj  Pod         ✔ Running     15m    ready:1/1
├──# revision:2                                                       
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown  26m    
└──# revision:1                                                       
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown  31m    

Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          6/9
  SetWeight:     80
  ActualWeight:  75
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       4
  Ready:         4
  Available:     4

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ◌ Progressing  31m    
├──# revision:5                                                        
│  └──⧉ gateway-5889c5c7bd           ReplicaSet  ◌ Progressing  3m38s  canary
│     ├──□ gateway-5889c5c7bd-42dxb  Pod         ✔ Running      3m37s  ready:1/1
│     ├──□ gateway-5889c5c7bd-qbtbf  Pod         ✔ Running      2m27s  ready:1/1
│     ├──□ gateway-5889c5c7bd-g94fz  Pod         ✔ Running      77s    ready:1/1
│     └──□ gateway-5889c5c7bd-99f9d  Pod         ✔ Running      7s     ready:0/1
├──# revision:4                                                        
│  └──⧉ gateway-6cdb6cffc            ReplicaSet  • ScaledDown   13m    
├──# revision:3                                                        
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy      20m    stable
│     └──□ gateway-855c8b68f8-9fwj5  Pod         ✔ Running      20m    ready:1/1
├──# revision:2                                                        
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown   26m    
└──# revision:1                                                        
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown   31m     

Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     35m    
├──# revision:5                                                       
│  └──⧉ gateway-5889c5c7bd           ReplicaSet  ✔ Healthy     6m45s  stable
│     ├──□ gateway-5889c5c7bd-42dxb  Pod         ✔ Running     6m44s  ready:1/1
│     ├──□ gateway-5889c5c7bd-qbtbf  Pod         ✔ Running     5m34s  ready:1/1
│     ├──□ gateway-5889c5c7bd-g94fz  Pod         ✔ Running     4m24s  ready:1/1
│     ├──□ gateway-5889c5c7bd-99f9d  Pod         ✔ Running     3m14s  ready:1/1
│     └──□ gateway-5889c5c7bd-s9f2k  Pod         ✔ Running     2m34s  ready:1/1
├──# revision:4                                                       
│  └──⧉ gateway-6cdb6cffc            ReplicaSet  • ScaledDown  16m    
├──# revision:3                                                       
│  └──⧉ gateway-855c8b68f8           ReplicaSet  • ScaledDown  24m    
├──# revision:2                                                       
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown  29m    
└──# revision:1                                                       
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown  35m 
```

Command:

```bash
kubectl delete -f labs/lab7/loadgen.yaml
```

Output:

```bash
deployment.apps "loadgen" deleted
```

---

### Automated Abort Threshold

I would configure an automated abort at around 40% canary traffic. At 20%, only one pod out of five receives canary traffic, so small random errors or temporary startup issues may not be enough to confidently decide that the version is bad. At 40%, two pods are already serving canary traffic, so there is more signal and the impact is still limited. If error rate, latency, or failed health checks increase at 40%, aborting there would prevent the bad version from reaching the majority of users. Therefore, 40% is a reasonable balance between collecting enough evidence and limiting user impact


## Bonus Task — Automated Canary Analysis

### B.1 Install In-Cluster Prometheus

I installed the provided in-cluster Prometheus configuration.

Command:

```bash
kubectl apply -f labs/lab7/prometheus.yaml
kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ kubectl apply -f labs/lab7/prometheus.yaml
namespace/monitoring created
serviceaccount/prometheus created
clusterrole.rbac.authorization.k8s.io/prometheus created
clusterrolebinding.rbac.authorization.k8s.io/prometheus created
configmap/prometheus-config created
deployment.apps/prometheus created
service/prometheus created
➜  SRE-Intro git:(feature/lab6) ✗ kubectl -n monitoring rollout status deployment/prometheus --timeout=60s
Waiting for deployment "prometheus" rollout to finish: 0 of 1 updated replicas are available...
deployment "prometheus" successfully rolled out
```

Then I verified that Prometheus discovered the gateway pods and that each pod had the `rs_hash` label.

Command:

```bash
kubectl port-forward -n monitoring svc/prometheus 9091:9090 &
curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
import sys,json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
kill %1 2>/dev/null
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ kubectl port-forward -n monitoring svc/prometheus 9091:9090
Forwarding from 127.0.0.1:9091 -> 9090
Forwarding from [::1]:9091 -> 9090
Handling connection for 9091

➜  SRE-Intro git:(feature/lab6) ✗ curl -s 'http://localhost:9091/api/v1/targets?state=active' | python3 -c "
import sys,json
for t in json.load(sys.stdin)['data']['activeTargets']:
    print(t['labels'].get('pod'), 'rs=', t['labels'].get('rs_hash'), t['health'])"
kill %1 2>/dev/null
gateway-855c8b68f8-fln6z rs= 855c8b68f8 up
gateway-855c8b68f8-ngt7r rs= 855c8b68f8 up
gateway-855c8b68f8-xtdd9 rs= 855c8b68f8 up
gateway-855c8b68f8-8sbgz rs= 855c8b68f8 up
gateway-855c8b68f8-rjgwz rs= 855c8b68f8 up
```

The `rs_hash` label is important because it allows the analysis query to distinguish canary pods from stable pods.

---

### B.2 Install the AnalysisTemplate

I applied the provided `AnalysisTemplate`.

Command:

```bash
kubectl apply -f labs/lab7/analysis-template.yaml
kubectl get analysistemplate gateway-error-rate
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ kubectl apply -f labs/lab7/analysis-template.yaml
analysistemplate.argoproj.io/gateway-error-rate created
➜  SRE-Intro git:(feature/lab6) ✗ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   8s
```

It defines the Prometheus-based canary check:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: gateway-error-rate
spec:
  args:
    - name: canary-hash
  metrics:
    - name: error-rate
      initialDelay: 60s
      interval: 20s
      count: 3
      successCondition: result[0] < 0.05
      failureLimit: 1
      provider:
        prometheus:
          address: http://prometheus.monitoring.svc.cluster.local:9090
          query: |
            (
              sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))
              or on() vector(0)
            )
            /
            sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
```

This template measures the 5xx error ratio only for the current canary ReplicaSet by using the `canary-hash` argument. The rollout succeeds if the measured error rate stays below 5%. If the metric fails more than the allowed `failureLimit`, Argo Rollouts aborts the canary automatically.

The `initialDelay: 60s` gives Prometheus enough time to discover and scrape the new canary pod. The numerator uses `or on() vector(0)` because zero 5xx responses should count as a valid zero value, while the denominator intentionally has no fallback because a canary with no measurable traffic should not be promoted blindly.


---

### B.3 Wire Analysis into the Rollout Strategy

I updated the gateway Rollout strategy to include an automated analysis step after the 20% canary phase.

Updated strategy:

```yaml
strategy:
  canary:
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
      - setWeight: 50
      - pause:
          duration: 20s
      - setWeight: 100
```

Command:

```bash
kubectl apply -f k8s/gateway.yaml
```

Output:

```bash
rollout.argoproj.io/gateway configured
service/gateway unchanged
```

---

### B.4 Good Version Auto-Promotion

First, I started the load generator so that Prometheus had traffic to measure.

Command:

```bash
kubectl apply -f labs/lab7/loadgen.yaml
```

Output:

```bash
deployment.apps/loadgen created
```

Then I triggered a rollout with a good version.

Command:

```bash
docker tag quickticket-gateway:v1 quickticket-gateway:v2
k3d image import -c quickticket quickticket-gateway:v2
kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2
kubectl argo rollouts get rollout gateway --watch
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ docker tag quickticket-gateway:v1 quickticket-gateway:v2
k3d image import -c quickticket quickticket-gateway:v2
kubectl argo rollouts set image gateway gateway=quickticket-gateway:v2
kubectl argo rollouts get rollout gateway --watch
INFO[0000] Importing image(s) into cluster 'quickticket' 
INFO[0000] Starting existing tools node k3d-quickticket-tools... 
INFO[0000] Starting node 'k3d-quickticket-tools'        
INFO[0001] Saving 1 image(s) from runtime...            
INFO[0003] Importing images into nodes...               
INFO[0003] Importing images from tarball '/k3d/images/k3d-quickticket-images-20260701125659.tar' into node 'k3d-quickticket-server-0'... 
INFO[0005] Removing the tarball(s) from image volume... 
INFO[0006] Removing k3d-tools node...                   
INFO[0006] Successfully imported image(s)               
INFO[0006] Successfully imported 1 image(s) into 1 cluster(s) 
rollout "gateway" image updated
Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          0/6
  SetWeight:     20
  ActualWeight:  0
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS         AGE   INFO
⟳ gateway                            Rollout     ◌ Progressing  11h   
├──# revision:7                                                       
│  └──⧉ gateway-74dcb4c9fd           ReplicaSet  • ScaledDown   0s    canary
├──# revision:6                                                       
│  └──⧉ gateway-855c8b68f8           ReplicaSet  ✔ Healthy      11h   stable
│     ├──□ gateway-855c8b68f8-fln6z  Pod         ✔ Running      10h   ready:1/1,restarts:1
│     ├──□ gateway-855c8b68f8-8sbgz  Pod         ✔ Running      10h   ready:1/1,restarts:1
│     ├──□ gateway-855c8b68f8-xtdd9  Pod         ✔ Running      10h   ready:1/1,restarts:1
│     ├──□ gateway-855c8b68f8-ngt7r  Pod         ✔ Running      103m  ready:1/1,restarts:1
│     └──□ gateway-855c8b68f8-rjgwz  Pod         ◌ Terminating  102m  ready:1/1,restarts:1
├──# revision:5                                                       
│  └──⧉ gateway-5889c5c7bd           ReplicaSet  • ScaledDown   11h   
├──# revision:4                                                       
│  └──⧉ gateway-6cdb6cffc            ReplicaSet  • ScaledDown   11h   
├──# revision:2                                                       
│  └──⧉ gateway-7d9cf94876           ReplicaSet  • ScaledDown   11h   
└──# revision:1                                                       
   └──⧉ gateway-7b85b56596           ReplicaSet  • ScaledDown   11h 
   Name:            gateway
Namespace:       default
Status:          ◌ Progressing
Message:         more replicas need to be updated
Strategy:        Canary
  Step:          2/6
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (stable)
                 quickticket-gateway:v2 (canary)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS         AGE   INFO
⟳ gateway                            Rollout      ◌ Progressing  11h   
├──# revision:7                                                        
│  ├──⧉ gateway-74dcb4c9fd           ReplicaSet   ✔ Healthy      39s   canary
│  │  └──□ gateway-74dcb4c9fd-nhj6x  Pod          ✔ Running      37s   ready:1/1
│  └──α gateway-74dcb4c9fd-7-2       AnalysisRun  ◌ Running      7s    
├──# revision:6                                                        
│  └──⧉ gateway-855c8b68f8           ReplicaSet   ✔ Healthy      11h   stable
│     ├──□ gateway-855c8b68f8-fln6z  Pod          ✔ Running      10h   ready:1/1,restarts:1
│     ├──□ gateway-855c8b68f8-8sbgz  Pod          ✔ Running      10h   ready:1/1,restarts:1
│     ├──□ gateway-855c8b68f8-xtdd9  Pod          ✔ Running      10h   ready:1/1,restarts:1
│     └──□ gateway-855c8b68f8-ngt7r  Pod          ✔ Running      103m  ready:1/1,restarts:1
├──# revision:5                                                        
│  └──⧉ gateway-5889c5c7bd           ReplicaSet   • ScaledDown   11h   
├──# revision:4                                                        
│  └──⧉ gateway-6cdb6cffc            ReplicaSet   • ScaledDown   11h   
├──# revision:2                                                        
│  └──⧉ gateway-7d9cf94876           ReplicaSet   • ScaledDown   11h   
└──# revision:1                                                        
   └──⧉ gateway-7b85b56596           ReplicaSet   • ScaledDown   11h
   Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✔ Healthy     11h    
├──# revision:7                                                        
│  ├──⧉ gateway-74dcb4c9fd           ReplicaSet   ✔ Healthy     6m41s  stable
│  │  ├──□ gateway-74dcb4c9fd-nhj6x  Pod          ✔ Running     6m39s  ready:1/1
│  │  ├──□ gateway-74dcb4c9fd-8vcjk  Pod          ✔ Running     4m27s  ready:1/1
│  │  ├──□ gateway-74dcb4c9fd-9tx2j  Pod          ✔ Running     4m27s  ready:1/1
│  │  ├──□ gateway-74dcb4c9fd-5kgnk  Pod          ✔ Running     3m57s  ready:1/1
│  │  └──□ gateway-74dcb4c9fd-wxb7c  Pod          ✔ Running     3m57s  ready:1/1
│  └──α gateway-74dcb4c9fd-7-2       AnalysisRun  ✔ Successful  6m9s   ✔ 3
├──# revision:6                                                        
│  └──⧉ gateway-855c8b68f8           ReplicaSet   • ScaledDown  11h    
├──# revision:5                                                        
│  └──⧉ gateway-5889c5c7bd           ReplicaSet   • ScaledDown  11h    
├──# revision:4                                                        
│  └──⧉ gateway-6cdb6cffc            ReplicaSet   • ScaledDown  11h    
├──# revision:2                                                        
│  └──⧉ gateway-7d9cf94876           ReplicaSet   • ScaledDown  11h    
└──# revision:1                                                        
   └──⧉ gateway-7b85b56596           ReplicaSet   • ScaledDown  11

```

The analysis run succeeded because the measured error rate stayed low.

Command:

```bash
kubectl get analysisrun
```

Output:

```bash
➜  SRE-Intro git:(feature/lab6) ✗ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-74dcb4c9fd-7-2   Successful   6m28s
```

The rollout was automatically promoted and eventually became healthy.

---

### B.5 Bad Version Auto-Abort

To simulate a bad version that starts successfully but returns errors, I changed the gateway `EVENTS_URL` to an invalid service name.

Temporary bad configuration:

```yaml
env:
  - name: EVENTS_URL
    value: "http://broken-on-purpose:8081"
  - name: GATEWAY_TIMEOUT_MS
    value: "2000"
```

Then I applied the manifest.

Command:

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts get rollout gateway --watch
```

Output:

```bash
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 13: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    2d11h  
├──# revision:13                                                       
│  ├──⧉ gateway-855c8b68f8           ReplicaSet   • ScaledDown  2d11h  canary
│  └──α gateway-855c8b68f8-13-2      AnalysisRun  ✖ Failed      9m16s  ✖ 2
├──# revision:12                                                       
│  └──⧉ gateway-5bdfd7fb59           ReplicaSet   • ScaledDown  39h    
├──# revision:11                                                       
│  └──⧉ gateway-567d6d5b76           ReplicaSet   • ScaledDown  47h    
├──# revision:10                                                       
│  └──⧉ gateway-b6647c9d7            ReplicaSet   • ScaledDown  39h    
├──# revision:9                                                        
│  └──⧉ gateway-6c86b565c4           ReplicaSet   • ScaledDown  47h    
├──# revision:7                                                        
│  ├──⧉ gateway-74dcb4c9fd           ReplicaSet   ✔ Healthy     2d     stable
│  │  ├──□ gateway-74dcb4c9fd-nhj6x  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  ├──□ gateway-74dcb4c9fd-8vcjk  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  ├──□ gateway-74dcb4c9fd-5kgnk  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  ├──□ gateway-74dcb4c9fd-wxb7c  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  └──□ gateway-74dcb4c9fd-dgzk8  Pod          ✔ Running     7m54s  ready:1/1
│  └──α gateway-74dcb4c9fd-7-2       AnalysisRun  ✔ Successful  2d     ✔ 3
├──# revision:5                                                        
│  └──⧉ gateway-5889c5c7bd           ReplicaSet   • ScaledDown  2d11h  
├──# revision:4                                                        
│  └──⧉ gateway-6cdb6cffc            ReplicaSet   • ScaledDown  2d11h  
├──# revision:2                                                        
│  └──⧉ gateway-7d9cf94876           ReplicaSet   • ScaledDown  2d11h  
└──# revision:1                                                        
   └──⧉ gateway-7b85b56596           ReplicaSet   • ScaledDown  2d11h
```

The canary pod started, but `/events` requests failed because the upstream service name was invalid. Prometheus detected 5xx responses from the canary, and the analysis failed.

Command:

```bash
kubectl get analysisrun
```

Output:

```bash
NAME                     STATUS       AGE
gateway-855c8b68f8-13-2   Failed       7m42s
```

Failed AnalysisRun details:

```bash
kubectl get analysisrun <FAILED_NAME> -o yaml
```

Output:

```bash
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "13"
  creationTimestamp: "2026-07-03T10:01:29Z"
  generation: 4
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 855c8b68f8
    step-index: "2"
  name: gateway-855c8b68f8-13-2
  namespace: default
  ownerReferences:
  - apiVersion: argoproj.io/v1alpha1
    blockOwnerDeletion: true
    controller: true
    kind: Rollout
    name: gateway
    uid: 55b81b1d-3ff1-476e-bf54-33ff2d602e04
  resourceVersion: "73285"
  uid: da454874-ecb2-4f52-baf9-253572ddcad3
spec:
  args:
  - name: canary-hash
    value: 855c8b68f8
  metrics:
  - count: 3
    failureLimit: 1
    initialDelay: 60s
    interval: 20s
    name: error-rate
    provider:
      prometheus:
        address: http://prometheus.monitoring.svc.cluster.local:9090
        authentication:
          oauth2: {}
          sigv4: {}
        query: |
          (
            sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))
            or on() vector(0)
          )
          /
          sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))
    successCondition: result[0] < 0.05
status:
  completedAt: "2026-07-03T10:02:49Z"
  dryRunSummary: {}
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-07-03T10:02:29Z"
      phase: Failed
      startedAt: "2026-07-03T10:02:29Z"
      value: '[0.3937007874015748]'
    - finishedAt: "2026-07-03T10:02:49Z"
      phase: Failed
      startedAt: "2026-07-03T10:02:49Z"
      value: '[0.42000000000000004]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="855c8b68f8",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="855c8b68f8"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  runSummary:
    count: 1
    failed: 1
  startedAt: "2026-07-03T10:01:29Z"
```

The failed analysis caused the rollout to auto-abort. The stable pods continued serving traffic.

Final rollout state after the aborted bad deploy:

```bash
kubectl argo rollouts get rollout gateway
```

Output:

```bash
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 13: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          quickticket-gateway:v2 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    2d11h  
├──# revision:13                                                       
│  ├──⧉ gateway-855c8b68f8           ReplicaSet   • ScaledDown  2d11h  canary
│  └──α gateway-855c8b68f8-13-2      AnalysisRun  ✖ Failed      9m55s  ✖ 2
├──# revision:12                                                       
│  └──⧉ gateway-5bdfd7fb59           ReplicaSet   • ScaledDown  39h    
├──# revision:11                                                       
│  └──⧉ gateway-567d6d5b76           ReplicaSet   • ScaledDown  47h    
├──# revision:10                                                       
│  └──⧉ gateway-b6647c9d7            ReplicaSet   • ScaledDown  39h    
├──# revision:9                                                        
│  └──⧉ gateway-6c86b565c4           ReplicaSet   • ScaledDown  47h    
├──# revision:7                                                        
│  ├──⧉ gateway-74dcb4c9fd           ReplicaSet   ✔ Healthy     2d     stable
│  │  ├──□ gateway-74dcb4c9fd-nhj6x  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  ├──□ gateway-74dcb4c9fd-8vcjk  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  ├──□ gateway-74dcb4c9fd-5kgnk  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  ├──□ gateway-74dcb4c9fd-wxb7c  Pod          ✔ Running     2d     ready:1/1,restarts:3
│  │  └──□ gateway-74dcb4c9fd-dgzk8  Pod          ✔ Running     8m33s  ready:1/1
│  └──α gateway-74dcb4c9fd-7-2       AnalysisRun  ✔ Successful  2d     ✔ 3
├──# revision:5                                                        
│  └──⧉ gateway-5889c5c7bd           ReplicaSet   • ScaledDown  2d11h  
├──# revision:4                                                        
│  └──⧉ gateway-6cdb6cffc            ReplicaSet   • ScaledDown  2d11h  
├──# revision:2                                                        
│  └──⧉ gateway-7d9cf94876           ReplicaSet   • ScaledDown  2d11h  
└──# revision:1                                                        
   └──⧉ gateway-7b85b56596           ReplicaSet   • ScaledDown  2d11h 
```

After the test, I reverted the gateway configuration back to the correct values.

```yaml
env:
  - name: EVENTS_URL
    value: "http://events:8081"
  - name: GATEWAY_TIMEOUT_MS
    value: "5000"
```

Command:

```bash
kubectl apply -f k8s/gateway.yaml
kubectl argo rollouts retry rollout gateway
```

Output:

```bash
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/slickip/quickticket-gateway:05eaf17eab81f8e932ef2cfb331d6879afb71da9 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✔ Healthy     2d13h  
├──# revision:19                                                       
│  ├──⧉ gateway-5bdd8bb7fd           ReplicaSet   ✔ Healthy     2m22s  stable
│  │  ├──□ gateway-5bdd8bb7fd-5w2pj  Pod          ✔ Running     2m20s  ready:1/1
│  │  ├──□ gateway-5bdd8bb7fd-7xhj7  Pod          ✔ Running     108s   ready:1/1
│  │  ├──□ gateway-5bdd8bb7fd-zbjll  Pod          ✔ Running     108s   ready:1/1
│  │  ├──□ gateway-5bdd8bb7fd-sjvb9  Pod          ✔ Running     75s    ready:1/1
│  │  └──□ gateway-5bdd8bb7fd-wsl9t  Pod          ✔ Running     75s    ready:1/1
│  └──α gateway-5bdd8bb7fd-19-2      AnalysisRun  ✔ Successful  110s   
```

---

### B.6 Cleanup

I stopped the load generator.

Command:

```bash
kubectl delete -f labs/lab7/loadgen.yaml
```

---

### Question: What metric would you add beyond error rate for a more complete canary analysis?

Beyond error rate, I would add latency, especially p95 or p99 request duration.

Error rate only shows whether requests fail, but a version can still be bad even if it returns successful responses. For example, the gateway could return `200 OK` but become much slower because of inefficient code, slow upstream calls, or resource pressure. High latency directly affects user experience and can indicate problems before they become full failures

A better canary analysis would combine:

- 5xx error rate
- p95 or p99 latency
- request success rate
- pod readiness or restart count

This would make the automated canary decision safer because it would detect both hard failures and performance regressions
