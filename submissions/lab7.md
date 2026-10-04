# Lab 7 — Progressive Delivery: Canary Deployments

## Environment

The exercise used the existing `quickticket` k3d cluster on macOS ARM64,
namespace `default`, and Argo Rollouts v1.10.0. QuickTicket application
images were the public multi-platform GHCR images from Lab 5.

ArgoCD automatic synchronization was temporarily disabled during the
experiments so its Lab 5 source would not restore the old gateway Deployment.
The gateway Deployment was removed and replaced by a Rollout.
PostgreSQL had no events table after the cluster restarted; `app/seed.sql`
initialized the schema and five events before the tests.

New revisions were distinguished by the `APP_VERSION` environment variable.
Changing this variable changed the pod template and ReplicaSet; the image
digest/tag remained unchanged. It was a configuration rollout, not a change
to the application's response format.

## Task 1 — Manual canary deployment

### Controller and plugin version

```text
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:26:09Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: darwin/arm64
```

```text
NAME                             READY   STATUS    RESTARTS   AGE    IP           NODE                       NOMINATED NODE   READINESS GATES
argo-rollouts-69645d4879-m5tts   1/1     Running   0          103s   10.42.0.67   k3d-quickticket-server-0   <none>           <none>
```

### Gateway conversion and manual strategy

The gateway now uses `apiVersion: argoproj.io/v1alpha1`, `kind: Rollout`,
and five replicas. The gateway Service selects `app: gateway`, allowing
both stable and canary pods to receive traffic.

```yaml
replicas: 5
strategy:
  canary:
    maxSurge: 1
    maxUnavailable: 0
    steps:
      - setWeight: 20
      - pause: {}
      - setWeight: 60
      - pause:
          duration: 30s
      - setWeight: 100
```

Both gateway probes use `/metrics` in this exercise. This checks that the
gateway process responds without removing a canary from Service endpoints
when its Events dependency fails. Dependency health is checked separately
through `/health`. This deliberate setup allows the bad canary to receive
requests and produce measurable 5xx responses. A production readiness
policy would need a separate design decision about dependency failures.

### V2 paused at 20%

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS         AGE    INFO
⟳ gateway                            Rollout     ॥ Paused       2m53s
├──# revision:2
│  └──⧉ gateway-6fbcc554b7           ReplicaSet  ✔ Healthy      11s    canary
│     └──□ gateway-6fbcc554b7-5hf9r  Pod         ✔ Running      10s    ready:1/1
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet  ✔ Healthy      2m53s  stable
      ├──□ gateway-6c74c88f7d-2wfpd  Pod         ✔ Running      2m53s  ready:1/1
      ├──□ gateway-6c74c88f7d-78cgb  Pod         ✔ Running      2m53s  ready:1/1
      ├──□ gateway-6c74c88f7d-97mmr  Pod         ◌ Terminating  2m53s  ready:0/1
      ├──□ gateway-6c74c88f7d-hrfsk  Pod         ✔ Running      2m53s  ready:1/1
      └──□ gateway-6c74c88f7d-lpxmq  Pod         ✔ Running      2m53s  ready:1/1
```

### Traffic split through the ClusterIP Service

The provided load generator ran inside the cluster. Gateway traffic did
not use a Service port-forward. Requests were counted from each active
pod's logs for the same 60-second interval.

The original collector encountered a terminating pod that had disappeared.
The completed sample was recovered using the current active pod list and
the original interval, rather than counting a different time window.

```text
SAMPLE_START=2026-10-04T11:23:41Z
SAMPLE_END=2026-10-04T11:24:41Z
POD ROLE APP_VERSION EVENTS_REQUESTS
gateway-6c74c88f7d-2wfpd stable v1 53
gateway-6c74c88f7d-78cgb stable v1 57
gateway-6c74c88f7d-hrfsk stable v1 55
gateway-6c74c88f7d-lpxmq stable v1 40
gateway-6fbcc554b7-5hf9r canary v2 55
TOTAL_REQUESTS=260
CANARY_REQUESTS=55
CANARY_REQUEST_PERCENT=21.15
Traffic source: in-cluster loadgen through gateway ClusterIP Service.
```

The canary received 55 of 260 `/events` requests, or **21.15%**, close to
the requested 20%. Without a traffic router, this strategy approximates
traffic weights through replica counts; it does not enforce an exact
percentage for every request sample.

### Manual promotion to 100%

Promotion began at `2026-10-04T11:26:20Z`. The Rollout reached three updated
replicas at the 60% step, waited for its timed pause, and reached five
updated replicas with `Healthy` status at `11:27:25Z`.

```text
2026-10-04T11:26:20Z attempt=1 phase/step/ready/updated=Progressing 2 5 1
2026-10-04T11:26:22Z attempt=2 phase/step/ready/updated=Progressing 2 5 2
2026-10-04T11:26:24Z attempt=3 phase/step/ready/updated=Progressing 2 5 2
2026-10-04T11:26:26Z attempt=4 phase/step/ready/updated=Progressing 2 5 2
2026-10-04T11:26:28Z attempt=5 phase/step/ready/updated=Progressing 2 5 3
2026-10-04T11:26:30Z attempt=6 phase/step/ready/updated=Progressing 2 5 3
2026-10-04T11:26:33Z attempt=7 phase/step/ready/updated=Progressing 2 5 3
2026-10-04T11:26:35Z attempt=8 phase/step/ready/updated=Progressing 2 5 3
2026-10-04T11:26:37Z attempt=9 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:39Z attempt=10 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:41Z attempt=11 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:43Z attempt=12 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:45Z attempt=13 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:47Z attempt=14 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:49Z attempt=15 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:51Z attempt=16 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:54Z attempt=17 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:56Z attempt=18 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:26:58Z attempt=19 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:27:00Z attempt=20 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:27:02Z attempt=21 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:27:04Z attempt=22 phase/step/ready/updated=Paused 3 5 3
2026-10-04T11:27:06Z attempt=23 phase/step/ready/updated=Progressing 4 5 4
2026-10-04T11:27:08Z attempt=24 phase/step/ready/updated=Progressing 4 5 4
2026-10-04T11:27:10Z attempt=25 phase/step/ready/updated=Progressing 4 5 4
2026-10-04T11:27:12Z attempt=26 phase/step/ready/updated=Progressing 4 5 4
2026-10-04T11:27:14Z attempt=27 phase/step/ready/updated=Progressing 4 5 4
2026-10-04T11:27:17Z attempt=28 phase/step/ready/updated=Progressing 4 5 5
2026-10-04T11:27:19Z attempt=29 phase/step/ready/updated=Progressing 4 5 5
2026-10-04T11:27:21Z attempt=30 phase/step/ready/updated=Progressing 4 5 5
2026-10-04T11:27:23Z attempt=31 phase/step/ready/updated=Progressing 4 5 5
2026-10-04T11:27:25Z attempt=32 phase/step/ready/updated=Healthy 5 5 5
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     6m43s
├──# revision:2
│  └──⧉ gateway-6fbcc554b7           ReplicaSet  ✔ Healthy     4m1s   stable
│     ├──□ gateway-6fbcc554b7-5hf9r  Pod         ✔ Running     4m     ready:1/1
│     ├──□ gateway-6fbcc554b7-d2b77  Pod         ✔ Running     65s    ready:1/1
│     ├──□ gateway-6fbcc554b7-mqdm8  Pod         ✔ Running     57s    ready:1/1
│     ├──□ gateway-6fbcc554b7-m4t45  Pod         ✔ Running     19s    ready:1/1
│     └──□ gateway-6fbcc554b7-j8z7v  Pod         ✔ Running     10s    ready:1/1
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet  • ScaledDown  6m43s
```

### Bad canary and manual abort

The bad revision used `APP_VERSION=v3-bad` and
`EVENTS_URL=http://broken-on-purpose:8081`.
The unresolved dependency produced an observed HTTP **502** response.
The bad canary remained Ready because its probes used `/metrics`.

```text
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ॥ Paused      9m22s
├──# revision:3
│  └──⧉ gateway-84ff8d47c6           ReplicaSet  ✔ Healthy     11s    canary
│     └──□ gateway-84ff8d47c6-s9cwc  Pod         ✔ Running     11s    ready:1/1
├──# revision:2
│  └──⧉ gateway-6fbcc554b7           ReplicaSet  ✔ Healthy     6m40s  stable
│     ├──□ gateway-6fbcc554b7-5hf9r  Pod         ✔ Running     6m39s  ready:1/1
│     ├──□ gateway-6fbcc554b7-d2b77  Pod         ✔ Running     3m44s  ready:1/1
│     ├──□ gateway-6fbcc554b7-m4t45  Pod         ✔ Running     2m58s  ready:1/1
│     └──□ gateway-6fbcc554b7-j8z7v  Pod         ✔ Running     2m49s  ready:1/1
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet  • ScaledDown  9m22s
```

```text
APP_VERSION=v3-bad
HTTP_STATUS=502
{"detail":"Events service unavailable"}
```

```text
ABORT_STARTED_AT=2026-10-04T11:30:08.516598+00:00
STABLE_HASH=6fbcc554b7
rollout 'gateway' aborted
elapsed=0.61s ready_endpoints=5 all_stable=False
elapsed=1.21s ready_endpoints=5 all_stable=False
elapsed=1.77s ready_endpoints=5 all_stable=False
elapsed=2.36s ready_endpoints=5 all_stable=False
elapsed=3.52s ready_endpoints=5 all_stable=False
elapsed=4.09s ready_endpoints=5 all_stable=False
elapsed=4.66s ready_endpoints=5 all_stable=False
elapsed=5.23s ready_endpoints=5 all_stable=False
elapsed=5.85s ready_endpoints=5 all_stable=False
elapsed=6.42s ready_endpoints=5 all_stable=False
elapsed=7.00s ready_endpoints=5 all_stable=False
elapsed=7.72s ready_endpoints=5 all_stable=False
elapsed=8.51s ready_endpoints=5 all_stable=True
ALL_STABLE_ENDPOINTS_SECONDS=8.51
FULL_STABLE_CAPACITY_SECONDS=8.51
RECOVERED_AT=2026-10-04T11:30:17.025381+00:00
Measurement scope: ready Service endpoints for new connections; existing in-flight requests may finish separately.
```

```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✖ Degraded    9m35s
├──# revision:3
│  └──⧉ gateway-84ff8d47c6           ReplicaSet  • ScaledDown  24s    canary
├──# revision:2
│  └──⧉ gateway-6fbcc554b7           ReplicaSet  ✔ Healthy     6m53s  stable
│     ├──□ gateway-6fbcc554b7-5hf9r  Pod         ✔ Running     6m52s  ready:1/1
│     ├──□ gateway-6fbcc554b7-d2b77  Pod         ✔ Running     3m57s  ready:1/1
│     ├──□ gateway-6fbcc554b7-m4t45  Pod         ✔ Running     3m11s  ready:1/1
│     ├──□ gateway-6fbcc554b7-j8z7v  Pod         ✔ Running     3m2s   ready:1/1
│     └──□ gateway-6fbcc554b7-zlrs2  Pod         ✔ Running     9s     ready:1/1
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet  • ScaledDown  9m35s
```

```text
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
200
```

**Abort versus Git revert:** all ready gateway Service endpoints pointed
to stable pods, with five ready endpoints, **8.51 seconds** after manual
abort. Thirty subsequent Service requests returned HTTP 200.

The measurement concerns endpoints for new connections; in-flight requests
and network rule propagation are not proven to finish at that exact instant.
In Lab 5, Git revert reached the observed `Synced Healthy` state in
**5.87 seconds**. Abort was therefore slower in these particular samples.
The measurements use different completion criteria and are not a general
benchmark. Abort acts directly on the running Rollout, while Git revert
also requires a Git update and ArgoCD reconciliation.

The good V2 desired configuration was restored after collecting the
aborted `Degraded` snapshot.

## Task 2 — Multi-step canary with observation

### Applied strategy

```yaml
strategy:
    canary:
      maxSurge: 1
      maxUnavailable: 0
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

The new pod template used `APP_VERSION=v4-multistep`.
The strategy progressed through 20%, 40%, 60%, 80%, and 100%.
Updated replicas climbed from one to five.

### Rollout watch output

```text
Name:            gateway
Status:          ◌ Progressing
Message:         waiting for rollout spec update to be observed
  Step:          5/9
  SetWeight:     60
  ActualWeight:  0
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

Name:            gateway
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          0/9
  SetWeight:     20
  ActualWeight:  0
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

Name:            gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

Name:            gateway
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          2/9
  SetWeight:     40
  ActualWeight:  20
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

Name:            gateway
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

Name:            gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

Name:            gateway
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          4/9
  SetWeight:     60
  ActualWeight:  40
  Desired:       5
  Current:       5
  Updated:       2
  Ready:         5
  Available:     5

Name:            gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

Name:            gateway
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          6/9
  SetWeight:     80
  ActualWeight:  60
  Desired:       5
  Current:       5
  Updated:       3
  Ready:         5
  Available:     5

Name:            gateway
Status:          ॥ Paused
Message:         CanaryPauseStep
  Step:          7/9
  SetWeight:     80
  ActualWeight:  80
  Desired:       5
  Current:       5
  Updated:       4
  Ready:         5
  Available:     5

Name:            gateway
Status:          ◌ Progressing
Message:         more replicas need to be updated
  Step:          8/9
  SetWeight:     100
  ActualWeight:  80
  Desired:       5
  Current:       5
  Updated:       4
  Ready:         5
  Available:     5

Name:            gateway
Status:          ✔ Healthy
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

Timestamped observations collected alongside the watch:

```text
2026-10-04T11:37:18.012197+00:00 phase=Progressing step=0 weight=20% updated=1 ready=5 events_rps=4.08
2026-10-04T11:37:23.459087+00:00 phase=Progressing step=0 weight=20% updated=1 ready=5 events_rps=3.96
2026-10-04T11:37:28.601921+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=3.75
2026-10-04T11:37:33.701599+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=3.50
2026-10-04T11:37:38.817003+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=3.52
2026-10-04T11:37:43.957432+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=3.76
2026-10-04T11:37:49.072952+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=3.80
2026-10-04T11:37:54.193899+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=4.03
2026-10-04T11:37:59.318539+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=4.20
2026-10-04T11:38:04.469599+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=4.28
2026-10-04T11:38:09.567366+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=3.80
2026-10-04T11:38:14.714365+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=4.00
2026-10-04T11:38:19.844926+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=3.80
2026-10-04T11:38:24.947959+00:00 phase=Paused step=1 weight=20% updated=1 ready=5 events_rps=4.00
2026-10-04T11:38:30.074478+00:00 phase=Progressing step=2 weight=40% updated=2 ready=5 events_rps=4.36
2026-10-04T11:38:35.159082+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.36
2026-10-04T11:38:40.317087+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=3.89
2026-10-04T11:38:45.448682+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.36
2026-10-04T11:38:50.632039+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=3.95
2026-10-04T11:38:55.720131+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=3.88
2026-10-04T11:39:00.825056+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=3.82
2026-10-04T11:39:05.894894+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.28
2026-10-04T11:39:11.048283+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.04
2026-10-04T11:39:16.141002+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.32
2026-10-04T11:39:21.250240+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.24
2026-10-04T11:39:26.333402+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.24
2026-10-04T11:39:31.475252+00:00 phase=Paused step=3 weight=40% updated=2 ready=5 events_rps=4.24
2026-10-04T11:39:36.889284+00:00 phase=Progressing step=4 weight=60% updated=3 ready=5 events_rps=4.36
2026-10-04T11:39:42.014475+00:00 phase=Progressing step=4 weight=60% updated=3 ready=5 events_rps=4.12
2026-10-04T11:39:47.170137+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=4.08
2026-10-04T11:39:52.258501+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=3.31
2026-10-04T11:39:57.387536+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=2.41
2026-10-04T11:40:02.480250+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=1.55
2026-10-04T11:40:07.601988+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=0.94
2026-10-04T11:40:12.690863+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=0.12
2026-10-04T11:40:17.950843+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=0.00
2026-10-04T11:40:23.099889+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=0.00
2026-10-04T11:40:28.194678+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=0.12
2026-10-04T11:40:33.329640+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=0.96
2026-10-04T11:40:38.421717+00:00 phase=Paused step=5 weight=60% updated=3 ready=5 events_rps=2.04
2026-10-04T11:40:43.561852+00:00 phase=Progressing step=6 weight=80% updated=3 ready=5 events_rps=2.88
2026-10-04T11:40:48.646996+00:00 phase=Progressing step=6 weight=80% updated=4 ready=5 events_rps=3.68
2026-10-04T11:40:53.789579+00:00 phase=Paused step=7 weight=80% updated=4 ready=5 events_rps=4.36
2026-10-04T11:40:58.885261+00:00 phase=Paused step=7 weight=80% updated=4 ready=5 events_rps=3.90
2026-10-04T11:41:03.993502+00:00 phase=Paused step=7 weight=80% updated=4 ready=5 events_rps=2.81
2026-10-04T11:41:09.100041+00:00 phase=Paused step=7 weight=80% updated=4 ready=5 events_rps=2.13
2026-10-04T11:41:14.231344+00:00 phase=Paused step=7 weight=80% updated=4 ready=5 events_rps=1.43
2026-10-04T11:41:19.326271+00:00 phase=Paused step=7 weight=80% updated=4 ready=5 events_rps=0.40
2026-10-04T11:41:24.465457+00:00 phase=Progressing step=8 weight=100% updated=5 ready=5 events_rps=0.00
2026-10-04T11:41:29.677441+00:00 phase=Progressing step=8 weight=100% updated=5 ready=5 events_rps=0.00
2026-10-04T11:41:34.801688+00:00 phase=Healthy step=9 weight=100% updated=5 ready=5 events_rps=0.04
{
  "start_epoch": 1791113837.794945,
  "end_epoch": 1791114094.8019881,
  "duration_seconds": 257.01,
  "observed_paused_weights": [
    20,
    40,
    60,
    80
  ],
  "final_phase": "Healthy",
  "final_updated_replicas": 5
}
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          9/9
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS        AGE    INFO
⟳ gateway                            Rollout     ✔ Healthy     20m
├──# revision:5
│  └──⧉ gateway-68f99f8b8c           ReplicaSet  ✔ Healthy     4m19s  stable
│     ├──□ gateway-68f99f8b8c-x4qbn  Pod         ✔ Running     4m18s  ready:1/1
│     ├──□ gateway-68f99f8b8c-pp6ns  Pod         ✔ Running     3m9s   ready:1/1
│     ├──□ gateway-68f99f8b8c-md2rm  Pod         ✔ Running     2m1s   ready:1/1
│     ├──□ gateway-68f99f8b8c-b29wc  Pod         ✔ Running     52s    ready:1/1
│     └──□ gateway-68f99f8b8c-58jcr  Pod         ✔ Running     12s    ready:1/1
├──# revision:4
│  └──⧉ gateway-6fbcc554b7           ReplicaSet  • ScaledDown  18m
├──# revision:3
│  └──⧉ gateway-84ff8d47c6           ReplicaSet  • ScaledDown  11m
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet  • ScaledDown  20m
```

### Grafana dashboard and metric observations

In-cluster Prometheus scraped gateway pods individually and copied
`rollouts-pod-template-hash` into the `rs_hash` metric label.
In-cluster Grafana used that Prometheus datasource. The provisioned
**QuickTicket Canary** dashboard contained:

- Events requests per second by ReplicaSet.
- Gateway 5xx ratio by ReplicaSet.
- Gateway scrape availability by pod.
- Events p95 latency by ReplicaSet.

The following observations were collected from the Prometheus queries
used for the dashboard during the rollout.

```text
weight=20% samples=12 mean_events_rps=3.87 max_observed_error_ratio=0.0
weight=40% samples=12 mean_events_rps=4.14 max_observed_error_ratio=0.0
weight=60% samples=11 mean_events_rps=1.41 max_observed_error_ratio=0.0
weight=80% samples=6 mean_events_rps=2.51 max_observed_error_ratio=0.0
```

The multi-step rollout completed in **257.01 seconds**.
The observed error ratio was zero at the sampled pause steps.

Request rate did **not** stay steady: mean Events RPS fell from approximately
3.87 and 4.14 at 20% and 40% to 1.41 and 2.51 at 60% and 80%.
Some samples were zero. Later checks found all five scrape targets up,
HTTP 200 Service responses, and no recent Events errors. Those later
checks do not establish the cause of the earlier dips.

The original sequential load generator had no request timeout.
For subsequent analysis tests, it was patched to bound connection and
request duration and to log HTTP status and curl exit codes. This prevents
a single request from blocking the generator indefinitely; it does not
prove that a blocked request caused the earlier dips.

**Automated abort percentage:** start analysis at **20%**, before increasing
exposure. Abort if the canary's measured error ratio exceeds the analysis
threshold, or if it cannot be measured reliably. Percentage alone is not
a failure signal: canary-specific metrics and adequate traffic must decide
whether promotion is safe.

## Bonus Task — Automated canary analysis

### AnalysisTemplate

`k8s/gateway-analysis.yaml` scopes the Prometheus query to the current
canary's hash. It waits 60 seconds before the first measurement, then
measures every 20 seconds, with three planned measurements.

The numerator falls back to zero when no 5xx series exists. The denominator
has no fallback: an unmeasurable canary must not be promoted. Conditions
guard empty results and reject values that do not satisfy the success
threshold. With `failureLimit: 1`, the second failed measurement causes
analysis failure; three consecutive failures are not required.

```text
NAME                 AGE
gateway-error-rate   0s
```

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
      # Allow discovery, scraping, and rate samples before analysis.
      initialDelay: 60s
      interval: 20s
      count: 3
      # Empty or non-finite results must not promote an unmeasured canary.
      successCondition: len(result) == 1 && result[0] < 0.05
      failureCondition: len(result) != 1 || !(result[0] < 0.05)
      # One failed measurement is tolerated; the second fails the analysis.
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

### Final strategy with inline analysis

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
        - setWeight: 50
        - pause:
            duration: 20s
        - setWeight: 100
```

The requested intermediate weight is 50%. With five replicas and no
traffic router, it was represented by three canary replicas, approximately
60% of replicas. This rounding does not affect the initial one-pod 20%
analysis step.

### Good version automatically promoted

`APP_VERSION=v5-analysis-good` created a new canary pod template.
No manual promotion was used.

```json
{
  "analysisrun": "gateway-58bd6b745f-6-2",
  "analysis_phase": "Successful",
  "measurement_values": [
    "[0]",
    "[0]",
    "[0]"
  ],
  "final_rollout_phase": "Healthy",
  "updated_replicas": 5,
  "stable_hash": "58bd6b745f",
  "elapsed_seconds": 186.01,
  "manual_promotion_used": false
}
```

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✔ Healthy     33m
├──# revision:6
│  ├──⧉ gateway-58bd6b745f           ReplicaSet   ✔ Healthy     3m7s   stable
│  │  ├──□ gateway-58bd6b745f-f9pj2  Pod          ✔ Running     3m6s   ready:1/1
│  │  ├──□ gateway-58bd6b745f-zl9qw  Pod          ✔ Running     58s    ready:1/1
│  │  ├──□ gateway-58bd6b745f-8p5lj  Pod          ✔ Running     50s    ready:1/1
│  │  ├──□ gateway-58bd6b745f-5j7p2  Pod          ✔ Running     21s    ready:1/1
│  │  └──□ gateway-58bd6b745f-lq9g5  Pod          ✔ Running     14s    ready:1/1
│  └──α gateway-58bd6b745f-6-2       AnalysisRun  ✔ Successful  2m38s  ✔ 3
├──# revision:5
│  └──⧉ gateway-68f99f8b8c           ReplicaSet   • ScaledDown  17m
├──# revision:4
│  └──⧉ gateway-6fbcc554b7           ReplicaSet   • ScaledDown  30m
├──# revision:3
│  └──⧉ gateway-84ff8d47c6           ReplicaSet   • ScaledDown  24m
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet   • ScaledDown  33m
```

### Bad version automatically aborted

The bad canary used `APP_VERSION=v6-analysis-bad`,
`EVENTS_URL=http://broken-on-purpose:8081`, and a 2000 ms gateway timeout.
Analysis measured the canary's 5xx ratio as `[1]` twice.
No manual abort was used.

```text
NAME                     STATUS       AGE
gateway-58bd6b745f-6-2   Successful   6m51s
gateway-5ff48c5c7d-7-2   Failed       91s
```

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisRun
metadata:
  annotations:
    rollout.argoproj.io/revision: "7"
  creationTimestamp: "2026-10-04T11:57:02Z"
  generation: 4
  labels:
    app: gateway
    rollout-type: Step
    rollouts-pod-template-hash: 5ff48c5c7d
    step-index: "2"
  name: gateway-5ff48c5c7d-7-2
  namespace: default
  ownerReferences:
  - apiVersion: argoproj.io/v1alpha1
    blockOwnerDeletion: true
    controller: true
    kind: Rollout
    name: gateway
    uid: a9d8fc51-5f24-432b-89f9-ff8ac61fb02a
  resourceVersion: "10009"
  uid: 41b84e36-ff57-41e1-8d79-1c80ac21c98e
spec:
  args:
  - name: canary-hash
    value: 5ff48c5c7d
  metrics:
  - count: 3
    failureCondition: len(result) != 1 || !(result[0] < 0.05)
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
    successCondition: len(result) == 1 && result[0] < 0.05
status:
  completedAt: "2026-10-04T11:58:22Z"
  dryRunSummary: {}
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-10-04T11:58:02Z"
      phase: Failed
      startedAt: "2026-10-04T11:58:02Z"
      value: '[1]'
    - finishedAt: "2026-10-04T11:58:22Z"
      phase: Failed
      startedAt: "2026-10-04T11:58:22Z"
      value: '[1]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="5ff48c5c7d",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="5ff48c5c7d"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  runSummary:
    count: 1
    failed: 1
  startedAt: "2026-10-04T11:57:02Z"
```

```json
{
  "analysisrun": "gateway-5ff48c5c7d-7-2",
  "analysis_phase": "Failed",
  "measurement_values": [
    "[1]",
    "[1]"
  ],
  "first_observed_failed_at": "2026-10-04T11:58:27.377421+00:00",
  "stable_endpoints_observed_at": "2026-10-04T11:58:33.442490+00:00",
  "final_rollout_phase": "Degraded",
  "stable_hash": "58bd6b745f",
  "ready_stable_endpoints": 5,
  "elapsed_seconds": 119.03,
  "manual_abort_used": false,
  "scope": "Ready Service endpoints for new connections"
}
```

```text
Name:            gateway
Namespace:       default
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 7: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded    37m
├──# revision:7
│  ├──⧉ gateway-5ff48c5c7d           ReplicaSet   • ScaledDown  2m     canary
│  └──α gateway-5ff48c5c7d-7-2       AnalysisRun  ✖ Failed      92s    ✖ 2
├──# revision:6
│  ├──⧉ gateway-58bd6b745f           ReplicaSet   ✔ Healthy     7m21s  stable
│  │  ├──□ gateway-58bd6b745f-f9pj2  Pod          ✔ Running     7m20s  ready:1/1
│  │  ├──□ gateway-58bd6b745f-zl9qw  Pod          ✔ Running     5m12s  ready:1/1
│  │  ├──□ gateway-58bd6b745f-5j7p2  Pod          ✔ Running     4m35s  ready:1/1
│  │  ├──□ gateway-58bd6b745f-lq9g5  Pod          ✔ Running     4m28s  ready:1/1
│  │  └──□ gateway-58bd6b745f-9dxjd  Pod          ✔ Running     12s    ready:1/1
│  └──α gateway-58bd6b745f-6-2       AnalysisRun  ✔ Successful  6m52s  ✔ 3
├──# revision:5
│  └──⧉ gateway-68f99f8b8c           ReplicaSet   • ScaledDown  21m
├──# revision:4
│  └──⧉ gateway-6fbcc554b7           ReplicaSet   • ScaledDown  35m
├──# revision:3
│  └──⧉ gateway-84ff8d47c6           ReplicaSet   • ScaledDown  28m
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet   • ScaledDown  37m
```

```text
200
200
200
200
200
200
200
200
200
200
```

After automatic abort, the bad ReplicaSet was scaled down and five stable
endpoints served the gateway. Ten verification requests returned HTTP 200.
The bad desired configuration was then replaced with the previous good
configuration.

```text
Name:            gateway
Namespace:       default
Status:          ✔ Healthy
Strategy:        Canary
  Step:          6/6
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/esqavator/quickticket-gateway:b6f70905f9d21a6273dc7ea37996024ec5341c66 (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                 KIND         STATUS        AGE    INFO
⟳ gateway                            Rollout      ✔ Healthy     37m
├──# revision:8
│  └──⧉ gateway-58bd6b745f           ReplicaSet   ✔ Healthy     7m22s  stable
│     ├──□ gateway-58bd6b745f-f9pj2  Pod          ✔ Running     7m21s  ready:1/1
│     ├──□ gateway-58bd6b745f-zl9qw  Pod          ✔ Running     5m13s  ready:1/1
│     ├──□ gateway-58bd6b745f-5j7p2  Pod          ✔ Running     4m36s  ready:1/1
│     ├──□ gateway-58bd6b745f-lq9g5  Pod          ✔ Running     4m29s  ready:1/1
│     └──□ gateway-58bd6b745f-9dxjd  Pod          ✔ Running     13s    ready:1/1
├──# revision:7
│  ├──⧉ gateway-5ff48c5c7d           ReplicaSet   • ScaledDown  2m1s
│  └──α gateway-5ff48c5c7d-7-2       AnalysisRun  ✖ Failed      93s    ✖ 2
├──# revision:6
│  └──α gateway-58bd6b745f-6-2       AnalysisRun  ✔ Successful  6m53s  ✔ 3
├──# revision:5
│  └──⧉ gateway-68f99f8b8c           ReplicaSet   • ScaledDown  21m
├──# revision:4
│  └──⧉ gateway-6fbcc554b7           ReplicaSet   • ScaledDown  35m
├──# revision:3
│  └──⧉ gateway-84ff8d47c6           ReplicaSet   • ScaledDown  28m
└──# revision:1
   └──⧉ gateway-6c74c88f7d           ReplicaSet   • ScaledDown  37m
```

**Metric to add beyond error rate:** canary-specific p95 request latency,
compared with stable latency and an explicit latency SLO. A canary can
return successful responses while becoming significantly slower.
Traffic volume should also be checked so sparse samples do not produce
misleading decisions.

## Submission scope

The final gateway manifest retains the good configuration and automated
analysis strategy. The report includes the earlier manual and multi-step
strategies as evidence of the completed experiments.

The supporting Events, Payments, PostgreSQL, and Redis manifests are
included because course `main` did not contain this student's QuickTicket
manifests. Reports and CI workflows from previous labs are not included.
