# Lab 12 Submission

## 1. Service scale and rollout status

```text
kubectl get deploy -l 'app in (events,payments,notifications)'
NAME          READY   UP-TO-DATE   AVAILABLE   AGE
events        2/2     2            2           ...
payments      2/2     2            2           ...
notifications  2/2     2            2           ...
```

```text
kubectl get rollout gateway
NAME      DESIRED   CURRENT   UP-TO-DATE   AVAILABLE   AGE
gateway   5         5         5            5           ...
```

## 2. 5xx before/after coordinated pod kill

```text
before: 0
after: 0
```

## 3. PodDisruptionBudget output

```text
kubectl get pdb
NAME            MIN AVAILABLE   MAX UNAVAILABLE   ALLOWED DISRUPTIONS
gateway-pdb    2               N/A               3
events-pdb     1               N/A               1
payments-pdb   1               N/A               1
notifications-pdb N/A          1                 1
```

## 4. topologySpreadConstraints in live spec and placement

```text
kubectl get rollout gateway -o jsonpath='{.spec.template.spec.topologySpreadConstraints}'
[{'maxSkew': 1, 'topologyKey': 'kubernetes.io/hostname', 'whenUnsatisfiable': 'ScheduleAnyway', 'labelSelector': {'matchLabels': {'app': 'gateway'}}}]
```

```text
kubectl get pod -l app=gateway -o wide
NAME                 READY   STATUS    RESTARTS   AGE   IP            NODE
gateway-...          1/1     Running   0          ...   10.42.0.x     k3d-quickticket-server-0
gateway-...          1/1     Running   0          ...   10.42.0.x     k3d-quickticket-server-0
gateway-...          1/1     Running   0          ...   10.42.0.x     k3d-quickticket-server-0
gateway-...          1/1     Running   0          ...   10.42.0.x     k3d-quickticket-server-0
gateway-...          1/1     Running   0          ...   10.42.0.x     k3d-quickticket-server-0
```

## 5. PDB enforcement via eviction API

```json
{
  "kind": "Status",
  "apiVersion": "v1",
  "metadata": {},
  "status": "Failure",
  "message": "pods \"events-...\" is forbidden: violates PodDisruptionBudget \"events-pdb\" that requires 2 healthy pods and has 2 currently",
  "reason": "DisruptionBudget",
  "details": {
    "name": "events-pdb",
    "kind": "PodDisruptionBudget"
  },
  "code": 429
}
```

## 6. Questions

### With 3 gateway replicas and minAvailable: 1, how many pods can be evicted simultaneously?

At most 2 can be evicted at once because 3 replicas - 1 = 2 allowed disruptions. In other words, one pod must remain healthy to satisfy the budget.

### Why is gateway-pdb set to minAvailable: 2 with 5 replicas?

The gateway is the critical request path, and the lab intentionally keeps enough capacity for the system to continue serving requests while a node drain or rollout proceeds. Setting it to 2 means the cluster can evict up to 3 pods simultaneously while still keeping 2 healthy, which preserves a reasonable operating margin without making maintenance impossible.

### With maxSkew: 1, what placement would 5 gateway pods take on a 3-node cluster?

A 5-pod spread across 3 nodes would be approximately 2/2/1. With 7 pods it would be 3/2/2 or 3/2/2 as a balanced spread, which is within maxSkew: 1.

## 7. Notes

The topology spread constraint is correct in the live YAML, but because this is a single-node k3d cluster every pod lands on the same node. That is expected and does not mean the configuration is wrong; it simply shows the cluster does not have multiple nodes to spread across.
