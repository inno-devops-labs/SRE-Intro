# Lab 7. Progressive delivery: canary deployments with Argo Rollouts

**Student:** Kirill Fadeev
**Email:** ki.fadeev@innopolis.university
**Environment:** WSL2 (kernel 6.18.33.2-microsoft-standard-WSL2, x86_64), Docker Engine 29.8.0, k3d v5.9.0, k3s v1.35.5+k3s1, kubectl v1.37.0, Argo Rollouts v1.10.0 (controller and kubectl plugin), in-cluster Prometheus v3.11.2, ArgoCD v3.5.3 from Lab 5

Every number below comes from a capture file taken during the run. Timings are client side stamps from the shell running the experiment, and request outcomes were recorded by an in-cluster client (a Python loop inside the `payments` pod hitting `http://gateway:8080/events` through the Service), so traffic went through kube-proxy exactly as it does for real callers.

The three "versions" of the gateway are the three images CI built in Lab 5, which are already on the node: `a2dfee5` (stable at the start), `8782cb7` and `d300862`. Task 1 changes `APP_VERSION` as the lab text suggests; Task 2 and the bonus change the image tag.

---

## Task 1. Manual canary

### 7.1 Installing Argo Rollouts

The install command from the lab text does not work with the current release.

```bash
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
```

```plaintext
Error from server (Invalid): error when creating "https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml":
CustomResourceDefinition.apiextensions.k8s.io "rollouts.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
Error from server (Invalid): ... "analysisruns.argoproj.io" is invalid: metadata.annotations: Too long: may not be more than 262144 bytes
deployment.apps/argo-rollouts configured
```

Client side `apply` stores the whole object in the `kubectl.kubernetes.io/last-applied-configuration` annotation, and the v1.10 schemas for `Rollout` and `AnalysisRun` are larger than the 256 KiB annotation limit. The rest of the manifest went in, so `kubectl wait` on the controller succeeded and only `kubectl get crd` showed that two of three CRDs were missing. Server side apply keeps field ownership in `managedFields` instead of an annotation and installs everything:

```bash
kubectl apply --server-side --force-conflicts -n argo-rollouts \
  -f https://github.com/argoproj/argo-rollouts/releases/latest/download/install.yaml
kubectl wait --for=condition=Available deployment/argo-rollouts -n argo-rollouts --timeout=180s
```

```plaintext
$ kubectl get crd rollouts.argoproj.io analysistemplates.argoproj.io analysisruns.argoproj.io
NAME                            CREATED AT
rollouts.argoproj.io            2026-10-02T16:18:44Z
analysistemplates.argoproj.io   2026-10-02T16:17:42Z
analysisruns.argoproj.io        2026-10-02T16:18:42Z
$ kubectl argo rollouts version
kubectl-argo-rollouts: v1.10.0+d90700a
  BuildDate: 2026-08-27T15:22:01Z
  GitCommit: d90700ae8d71d141561f0c546e19f999bb335cbd
  GitTreeState: clean
  GoVersion: go1.26.7
  Compiler: gc
  Platform: linux/amd64
$ kubectl -n argo-rollouts get deploy argo-rollouts -o jsonpath={.spec.template.spec.containers[0].image}
quay.io/argoproj/argo-rollouts:v1.10.0
```

The plugin went to `~/.local/bin` without sudo (option B).

> A successful `wait` on the controller says nothing about the CRDs. The install is only done when the API server can answer `kubectl get crd rollouts.argoproj.io`, and that check belongs in any script that installs an operator.

### 7.2 Converting the Deployment to a Rollout

Two things from earlier labs had to be dealt with before the conversion.

**ArgoCD.** The `quickticket` Application from Lab 5 syncs `k8s/` from the fork's `main` with `selfHeal: true`. Lab 5 measured that self-heal recreates a deleted object in under ten seconds, so `kubectl delete deployment gateway` would be undone and the Deployment would end up running next to the Rollout, both behind the same `app: gateway` selector. Auto-sync was suspended for the duration of the lab:

```plaintext
before: {"automated":{"selfHeal":true}}
$ kubectl patch application quickticket -n argocd --type json -p [{"op":"remove","path":"/spec/syncPolicy"}]
application.argoproj.io/quickticket patched
after:
```

**Image pulls.** The Lab 5 manifest uses private ghcr.io images with `imagePullPolicy: Always` and an `imagePullSecrets` entry. The `ghcr-secret` no longer exists in the cluster (`secrets "ghcr-secret" not found`), and an anonymous manifest request to ghcr.io returns `401`. A Deployment with one long-lived pod never noticed, but a canary creates new pods on every step, and each of them would go to `ImagePullBackOff`. The three CI images are already in the node's containerd store, so the Rollout uses `imagePullPolicy: IfNotPresent`.

The resulting `k8s/gateway.yaml` (the strategy shown here is the Task 1 one; the committed file carries the bonus strategy, see below):

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
        - setWeight: 20               # 1 of 5 pods = canary
        - pause: {}                   # wait for manual promotion
        - setWeight: 60               # 3 of 5 pods = canary
        - pause: {duration: 30s}      # auto-proceed after 30s
        - setWeight: 100
  selector:
    matchLabels:
      app: gateway
  template:
    ...                               # probes, resources, env as in Lab 4
```

```plaintext
$ kubectl delete deployment gateway
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway created
service/gateway configured
[19:19:44.444] phase=Healthy|5| after 9.18s
Name:            gateway
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Images:          ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663bd645857f685c294c0646bdb5f4b (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5
```

The first revision of a Rollout ignores the steps and goes straight to `Step: 5/5`: there is no stable version yet to compare a canary with. The Service did not change; its selector is still `{"app":"gateway"}`, which matches stable and canary pods alike.

> One honest side effect: in the first attempt, before the CRD problem was understood, the Deployment had already been deleted and the Rollout could not be created. The gateway had no pods for 86 seconds, from the delete at 19:18:18 until the Rollout reported `Healthy` at 19:19:44. Deleting the old object before the new kind is known to the API server is the step that makes this migration non-atomic.

### 7.3 Canary at 20%

The new version is the same spec with `APP_VERSION: "v2"`.

```plaintext
T_APPLY 19:19:45.963
$ kubectl apply -f k8s/gateway.yaml
rollout.argoproj.io/gateway configured
[19:19:55.084] phase=Paused|1|CanaryPauseStep after 8.36s

$ kubectl argo rollouts get rollout gateway
Name:            gateway
Namespace:       default
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/5
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663bd645857f685c294c0646bdb5f4b (canary, stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5

NAME                                 KIND        STATUS     AGE  INFO
⟳ gateway                            Rollout     ॥ Paused   28s
├──# revision:2
│  └──⧉ gateway-6bd89c5bb            ReplicaSet  ✔ Healthy  17s  canary
│     └──□ gateway-6bd89c5bb-fghhs   Pod         ✔ Running  17s  ready:1/1
└──# revision:1
   └──⧉ gateway-694c86d7f7           ReplicaSet  ✔ Healthy  28s  stable
      ├──□ gateway-694c86d7f7-jtklf  Pod         ✔ Running  28s  ready:1/1
      ├──□ gateway-694c86d7f7-nwbbj  Pod         ✔ Running  28s  ready:1/1
      ├──□ gateway-694c86d7f7-wb9vl  Pod         ✔ Running  28s  ready:1/1
      └──□ gateway-694c86d7f7-znpjz  Pod         ✔ Running  28s  ready:1/1
```

Both ReplicaSets run the same image, which is why the image line says `(canary, stable)`; they differ only in the pod template hash, and the hash is what Argo Rollouts tracks.

### 7.4 Traffic split

The provided loadgen ran for 30 seconds while the canary was paused. Per-pod access log counts:

```plaintext
stable hash=694c86d7f7 canary hash=6bd89c5bb
pod/gateway-694c86d7f7-jtklf hash=694c86d7f7 APP_VERSION=v1 events_requests=22
pod/gateway-694c86d7f7-nwbbj hash=694c86d7f7 APP_VERSION=v1 events_requests=23
pod/gateway-694c86d7f7-wb9vl hash=694c86d7f7 APP_VERSION=v1 events_requests=29
pod/gateway-694c86d7f7-znpjz hash=694c86d7f7 APP_VERSION=v1 events_requests=33
pod/gateway-6bd89c5bb-fghhs  hash=6bd89c5bb  APP_VERSION=v2 events_requests=33
total=140 canary=33 share=23.6%
```

23.6% against a configured 20%. The individual stable pods range from 22 to 33, so the canary's 33 sits inside the normal spread of a 140-request sample.

> Without a traffic router, "20%" comes from kube-proxy picking one of five endpoints at random. The weight is only as exact as the replica count allows, and a single client with keep-alive would bypass it entirely. That is why the measurement had to come from inside the cluster.

### 7.5 Promotion

```plaintext
T_PROMOTE 19:20:42.701
$ kubectl argo rollouts promote gateway
rollout 'gateway' promoted
[19:20:43.051] +0.38s  Progressing step=2 updated=1 ready=4
[19:20:44.254] +1.58s  Progressing step=2 updated=3 ready=4
[19:20:51.270] +8.60s  Paused      step=3 updated=3 ready=5
[19:21:20.402] +37.74s Progressing step=4 updated=3 ready=4
[19:21:21.716] +39.06s Progressing step=4 updated=5 ready=4
[19:21:28.774] +46.10s Progressing step=5 updated=5 ready=5
[19:21:29.964] +47.29s Healthy     step=5 updated=5 ready=5
```

```plaintext
Name:            gateway
Status:          ✔ Healthy
Strategy:        Canary
  Step:          5/5
  SetWeight:     100
  ActualWeight:  100
Replicas:
  Desired:       5
  Current:       5
  Updated:       5
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE   INFO
⟳ gateway                           Rollout     ✔ Healthy     115s
├──# revision:2
│  └──⧉ gateway-6bd89c5bb           ReplicaSet  ✔ Healthy     104s  stable
│     ├──□ gateway-6bd89c5bb-fghhs  Pod         ✔ Running     104s  ready:1/1
│     ├──□ gateway-6bd89c5bb-gp6wz  Pod         ✔ Running     47s   ready:1/1
│     ├──□ gateway-6bd89c5bb-hbszm  Pod         ✔ Running     47s   ready:1/1
│     ├──□ gateway-6bd89c5bb-5lcpx  Pod         ✔ Running     10s   ready:1/1
│     └──□ gateway-6bd89c5bb-h9v9k  Pod         ✔ Running     10s   ready:1/1
└──# revision:1
   └──⧉ gateway-694c86d7f7          ReplicaSet  • ScaledDown  115s
```

47.29 s from `promote` to `Healthy`, of which 30 s is the timed pause. The `ready=4` lines are worth noticing: the default `maxUnavailable` lets the controller scale the stable set down before the new canary pods pass readiness, so during each step change the gateway briefly ran on four ready pods out of five.

### 7.6 Bad version and abort

For the "bad" version I did not use an image that fails to start, because such a canary never receives traffic and an abort then proves nothing about users. Instead `v3-bad` points `EVENTS_URL` at the payments service. Payments answers `/health` with 200, so the gateway's readiness probe passes and the pod joins the Service, but payments has no `/events` route, so every `/events` call through this pod returns 502. This is a realistic misconfiguration: the pod looks healthy and is wrong.

The in-cluster client ran for 70 seconds, the abort was issued 20 seconds in.

```plaintext
[19:21:39.622] phase=Paused|1|CanaryPauseStep after 8.69s
T_ABORT 19:22:06.056
$ kubectl argo rollouts abort gateway
rollout 'gateway' aborted
[19:22:06.452] phase=Degraded|0|RolloutAborted: Rollout aborted update to revision 3 after 0.18s
```

Client responses in 5 second buckets, relative to the abort:

```plaintext
t -20..-15s  200=30  502=13
t -15..-10s  200=34  502=13
t -10..-5s   200=36  502=11
t  -5..+0s   200=34  502=13
t  +0..+5s   200=46
t  +5..+10s  200=47
t +10..+15s  200=45
...
t +45..+50s  200=47
requests before abort: 184, non-200: 50 (27.2%)
last non-200 at -0.133s relative to abort; non-200 after abort: 0
```

The raw lines around the abort (pod clock in UTC, the shell clock is UTC+3):

```plaintext
16:22:05.909 502
16:22:06.015 200        <- abort command issued at 16:22:06.056
16:22:06.121 200
...
16:22:06.446 200        <- kubectl returned at 16:22:06.452
16:22:06.552 200
```

Pod readiness by template hash, sampled every 0.3 s by a separate loop:

```plaintext
19:22:04.831 6bd89c5bb:ready=4 6dbbc44cd:ready=1
19:22:06.660 6bd89c5bb:notready=1 6bd89c5bb:ready=4 6dbbc44cd:terminating=1
19:22:08.831 6bd89c5bb:notready=1 6bd89c5bb:ready=4
19:22:15.361 6bd89c5bb:ready=5
```

```plaintext
$ kubectl argo rollouts get rollout gateway
Name:            gateway
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 3
Strategy:        Canary
  Step:          0/5
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663bd645857f685c294c0646bdb5f4b (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         5
  Available:     5

NAME                                KIND        STATUS        AGE    INFO
⟳ gateway                           Rollout     ✖ Degraded    3m21s
├──# revision:3
│  └──⧉ gateway-6dbbc44cd           ReplicaSet  • ScaledDown  86s    canary
├──# revision:2
│  └──⧉ gateway-6bd89c5bb           ReplicaSet  ✔ Healthy     3m10s  stable
│     ├──□ gateway-6bd89c5bb-fghhs  Pod         ✔ Running     3m10s  ready:1/1
│     ├──□ gateway-6bd89c5bb-gp6wz  Pod         ✔ Running     2m13s  ready:1/1
│     ├──□ gateway-6bd89c5bb-hbszm  Pod         ✔ Running     2m13s  ready:1/1
│     ├──□ gateway-6bd89c5bb-5lcpx  Pod         ✔ Running     96s    ready:1/1
│     └──□ gateway-6bd89c5bb-4h5f4  Pod         ✔ Running     50s    ready:1/1
```

`Degraded` stays until the spec is changed. Re-applying the good `v2` manifest returned the rollout to `Healthy` in 0.14 s without creating a pod, because its template hash equals the stable ReplicaSet's.

### How long from abort to all traffic on stable, compared with `git revert`

The canary pod was marked for deletion and left the endpoints within 0.6 s of the abort command (`terminating` at 19:22:06.660, command issued at 19:22:06.056). The last 502 the client saw was 0.13 s before the command, and not one of the 466 requests after it failed. Full capacity took longer: during the canary the stable set had been scaled to four pods, and the fifth stable pod passed readiness 9.3 s after the abort. For those nine seconds all traffic was correct but served by four pods instead of five.

The Lab 5 `git revert` rollback took 189.84 s from push to `Synced` and `Healthy`, 175.74 s of it ArgoCD's poll interval. So the abort is about 300 times faster at removing the bad version from the request path, and roughly 20 times faster even counting the capacity restore.

The two are not interchangeable. `abort` changes the cluster but not the desired state: in a GitOps flow Git still names the bad version, and with ArgoCD self-heal on the next sync would start the same canary again. It is the emergency brake, and a `git revert` still has to follow to make the decision durable. The revert alone, on the other hand, would have left 27% of requests failing for three minutes.

> The canary bounded the damage before anyone acted. During the 20 seconds the bad version was live, 50 of 184 requests failed. Without a canary the same 20 seconds would have failed all 184, and the Lab 5 revert would have stretched that to three minutes.

---

## Task 2. Multi-step canary with observation

### 7.8 Strategy

```yaml
strategy:
  canary:
    steps:
      - setWeight: 20
      - pause: {duration: 60s}
      - setWeight: 40
      - pause: {duration: 60s}
      - setWeight: 60
      - pause: {duration: 60s}
      - setWeight: 80
      - pause: {duration: 30s}
      - setWeight: 100
```

Applying a changed `strategy` alone did not start a rollout: the phase stayed `Healthy|9|` and no ReplicaSet was created, because only `spec.template` changes trigger one.

### 7.9 Observation

As the lab text notes, the docker-compose Prometheus and Grafana from Lab 3 cannot reach pod IPs inside k3d, so they have no view of a canary. I deployed the in-cluster Prometheus from `labs/lab7/prometheus.yaml` first (the bonus needs it anyway) and used it in place of the Grafana dashboard: the table below shows the same series a Grafana panel on this Prometheus would plot. Every ten seconds the script queried the per-ReplicaSet `/events` request rate and the overall 5xx rate. All five stable pods were discovered with their `rs_hash` label before the run:

```plaintext
gateway-6bd89c5bb-fghhs rs= 6bd89c5bb up
gateway-6bd89c5bb-4h5f4 rs= 6bd89c5bb up
gateway-6bd89c5bb-gp6wz rs= 6bd89c5bb up
gateway-6bd89c5bb-5lcpx rs= 6bd89c5bb up
gateway-6bd89c5bb-hbszm rs= 6bd89c5bb up
gateway targets up: all:5.00
```

```bash
kubectl apply -f labs/lab7/loadgen.yaml
kubectl argo rollouts set image gateway gateway=ghcr.io/kortezz12/quickticket-gateway:8782cb7761b00a750b49ea25256af85df2d2d9f3
kubectl argo rollouts get rollout gateway --watch
```

The `--watch` output redraws the whole screen on every change: 270 frames in four minutes, 15 of them distinct. The distinct states, one line each:

```plaintext
Status: ◌ Progressing  Step: 0/9  SetWeight: 20   ActualWeight: 0    Updated: 0
Status: ◌ Progressing  Step: 0/9  SetWeight: 20   ActualWeight: 0    Updated: 1
Status: ॥ Paused       Step: 1/9  SetWeight: 20   ActualWeight: 20   Updated: 1
Status: ◌ Progressing  Step: 2/9  SetWeight: 40   ActualWeight: 20   Updated: 1
Status: ◌ Progressing  Step: 2/9  SetWeight: 40   ActualWeight: 25   Updated: 2
Status: ॥ Paused       Step: 3/9  SetWeight: 40   ActualWeight: 40   Updated: 2
Status: ◌ Progressing  Step: 4/9  SetWeight: 60   ActualWeight: 40   Updated: 2
Status: ◌ Progressing  Step: 4/9  SetWeight: 60   ActualWeight: 50   Updated: 3
Status: ॥ Paused       Step: 5/9  SetWeight: 60   ActualWeight: 60   Updated: 3
Status: ◌ Progressing  Step: 6/9  SetWeight: 80   ActualWeight: 60   Updated: 3
Status: ◌ Progressing  Step: 6/9  SetWeight: 80   ActualWeight: 75   Updated: 4
Status: ॥ Paused       Step: 7/9  SetWeight: 80   ActualWeight: 80   Updated: 4
Status: ◌ Progressing  Step: 8/9  SetWeight: 100  ActualWeight: 80   Updated: 4
Status: ◌ Progressing  Step: 8/9  SetWeight: 100  ActualWeight: 100  Updated: 5
Status: ✔ Healthy      Step: 9/9  SetWeight: 100  ActualWeight: 100  Updated: 5
```

Three of the paused frames in full:

```plaintext
Status:          ॥ Paused
Message:         CanaryPauseStep
Strategy:        Canary
  Step:          1/9
  SetWeight:     20
  ActualWeight:  20
Images:          ghcr.io/kortezz12/quickticket-gateway:8782cb7761b00a750b49ea25256af85df2d2d9f3 (canary)
                 ghcr.io/kortezz12/quickticket-gateway:a2dfee5f0663bd645857f685c294c0646bdb5f4b (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       1
  Ready:         5
  Available:     5
├──# revision:5
│  └──⧉ gateway-5d9978bd9b           ReplicaSet  ✔ Healthy     9s     canary
│     └──□ gateway-5d9978bd9b-7p89l  Pod         ✔ Running     9s     ready:1/1
├──# revision:4
│  └──⧉ gateway-6bd89c5bb            ReplicaSet  ✔ Healthy     5m34s  stable
│     ├──□ gateway-6bd89c5bb-fghhs   Pod         ✔ Running     5m34s  ready:1/1
│     ├──□ gateway-6bd89c5bb-gp6wz   Pod         ✔ Running     4m37s  ready:1/1
│     ├──□ gateway-6bd89c5bb-hbszm   Pod         ✔ Running     4m37s  ready:1/1
│     └──□ gateway-6bd89c5bb-5lcpx   Pod         ✔ Running     4m     ready:1/1

Status:          ॥ Paused
  Step:          3/9
  SetWeight:     40
  ActualWeight:  40
  Updated:       2
│  └──⧉ gateway-5d9978bd9b           ReplicaSet  ✔ Healthy     76s    canary
│     ├──□ gateway-5d9978bd9b-7p89l  Pod         ✔ Running     76s    ready:1/1
│     └──□ gateway-5d9978bd9b-4bdzd  Pod         ✔ Running     8s     ready:1/1
│  └──⧉ gateway-6bd89c5bb            ReplicaSet  ✔ Healthy     6m41s  stable
│     ├──□ gateway-6bd89c5bb-fghhs   Pod         ✔ Running     6m41s  ready:1/1
│     ├──□ gateway-6bd89c5bb-gp6wz   Pod         ✔ Running     5m44s  ready:1/1
│     └──□ gateway-6bd89c5bb-5lcpx   Pod         ✔ Running     5m7s   ready:1/1

Status:          ॥ Paused
  Step:          5/9
  SetWeight:     60
  ActualWeight:  60
  Updated:       3
│  └──⧉ gateway-5d9978bd9b           ReplicaSet  ✔ Healthy     2m22s  canary
│     ├──□ gateway-5d9978bd9b-7p89l  Pod         ✔ Running     2m22s  ready:1/1
│     ├──□ gateway-5d9978bd9b-4bdzd  Pod         ✔ Running     74s    ready:1/1
│     └──□ gateway-5d9978bd9b-8qrpp  Pod         ✔ Running     7s     ready:1/1
```

The Prometheus side of the same run (`/events` requests per second over a 30 s window, by ReplicaSet; old stable `6bd89c5bb`, new `5d9978bd9b`):

```plaintext
+t(s)    phase       step updated/ready  events rps by rs_hash (30s rate)                  5xx rps
10.41    Paused      1    1/5            rs_hash=6bd89c5bb:4.35                            all:0.00
21.71    Paused      1    1/5            rs_hash=6bd89c5bb:3.97 rs_hash=5d9978bd9b:0.31    all:0.00
32.95    Paused      1    1/5            rs_hash=6bd89c5bb:3.84 rs_hash=5d9978bd9b:0.62    all:0.00
44.25    Paused      1    1/5            rs_hash=6bd89c5bb:3.64 rs_hash=5d9978bd9b:0.96    all:0.00
56.11    Paused      1    1/5            rs_hash=6bd89c5bb:3.28 rs_hash=5d9978bd9b:0.92    all:0.00
69.12    Progressing 2    2/4            rs_hash=6bd89c5bb:3.48 rs_hash=5d9978bd9b:0.80    all:0.00
80.95    Paused      3    2/5            rs_hash=6bd89c5bb:3.20 rs_hash=5d9978bd9b:1.04    all:0.00
92.70    Paused      3    2/5            rs_hash=6bd89c5bb:2.84 rs_hash=5d9978bd9b:1.53    all:0.00
104.65   Paused      3    2/5            rs_hash=6bd89c5bb:2.52 rs_hash=5d9978bd9b:1.92    all:0.00
117.11   Paused      3    2/5            rs_hash=6bd89c5bb:2.48 rs_hash=5d9978bd9b:1.88    all:0.00
128.64   Paused      3    2/5            rs_hash=6bd89c5bb:2.60 rs_hash=5d9978bd9b:1.72    all:0.00
140.64   Progressing 4    3/4            rs_hash=6bd89c5bb:2.77 rs_hash=5d9978bd9b:1.44    all:0.00
153.04   Paused      5    3/5            rs_hash=6bd89c5bb:2.41 rs_hash=5d9978bd9b:2.02    all:0.00
164.54   Paused      5    3/5            rs_hash=6bd89c5bb:1.92 rs_hash=5d9978bd9b:2.30    all:0.00
176.11   Paused      5    3/5            rs_hash=6bd89c5bb:2.04 rs_hash=5d9978bd9b:2.36    all:0.00
188.02   Paused      5    3/5            rs_hash=6bd89c5bb:1.84 rs_hash=5d9978bd9b:2.48    all:0.00
199.40   Paused      5    3/5            rs_hash=6bd89c5bb:1.76 rs_hash=5d9978bd9b:2.76    all:0.00
210.50   Paused      7    4/5            rs_hash=6bd89c5bb:1.61 rs_hash=5d9978bd9b:2.96    all:0.00
222.00   Paused      7    4/5            rs_hash=6bd89c5bb:1.34 rs_hash=5d9978bd9b:3.19    all:0.00
233.10   Paused      7    4/5            rs_hash=6bd89c5bb:1.00 rs_hash=5d9978bd9b:3.18    all:0.00
245.04   Progressing 8    5/4            rs_hash=6bd89c5bb:0.57 rs_hash=5d9978bd9b:3.84    all:0.00
256.45   Healthy     9    5/5            rs_hash=6bd89c5bb:0.35 rs_hash=5d9978bd9b:4.23    all:0.00
```

Answers to the observation questions:

- **Request rate across steps.** The total stayed between 4.2 and 4.6 `/events` requests per second in every sample (baseline before the rollout: 4.36), with zero 5xx. Only the split moved: in the last sample of each pause (rows 56.11, 128.64, 199.40 and 233.10) the new ReplicaSet's share was 22%, 40%, 61% and 76%, and 92% at `Healthy`. Earlier samples inside a pause read lower, because the 30 s window still holds requests from before the step. The last value is not 100% because a 30 s `rate()` window still contains the old pods' requests; it reaches 100% half a minute after the last step.
- **Updated replicas.** They climbed 1, 2, 3, 4, 5 exactly with the weights. Between pauses the watch shows intermediate `ActualWeight` values of 25, 50 and 75: a stable pod had already been removed while the new canary pod was still starting, so for a few seconds the ratio was 1 of 4, 2 of 4 and 3 of 4. Ready replicas dipped to 4 at each of those moments.
- **Where I would abort.** At the first step. Total time was 256 s, and each later step doubles or triples the number of users exposed.

> A dashboard aggregated over the whole service would hide a canary almost completely. At 20% weight, a canary failing half its requests moves the service-wide error rate by about ten percentage points, and less on a quieter endpoint. The per-ReplicaSet split is the view that makes the canary observable, and it exists only because the scrape config copies `rollouts-pod-template-hash` into a label.

### At what canary percentage would I want an automated abort, and why

At 20%, the first step, and as early inside that step as the measurement allows. The canary exists to answer "is the new version worse" with the fewest users exposed, and in this setup 20% is the smallest step that is still one whole pod. Later steps should keep the analysis running, because some failures appear only under load (connection pools, caches, rate limits), but the decision that matters most is the first one: Task 1 measured 27% of all requests failing while a bad version held one pod in five; with three pods in five the same version would fail roughly three times as many.

The threshold should be relative, not absolute: abort when the canary's error ratio is clearly above the stable set's over the same window. An absolute "5% errors" fails both ways: it promotes a canary at 4% when stable runs at 0.1%, and it aborts a good canary during an incident in a shared dependency that hurts both versions equally.

---

## Bonus. Automated canary analysis

### B.1 and B.2: Prometheus and the AnalysisTemplate

Prometheus was installed in Task 2. The template is copied unchanged from `labs/lab7/` to `k8s/analysis-template.yaml`.

```plaintext
$ kubectl apply -f k8s/analysis-template.yaml
analysistemplate.argoproj.io/gateway-error-rate created
$ kubectl get analysistemplate gateway-error-rate
NAME                 AGE
gateway-error-rate   0s
```

The four design choices from the template's comments, in my words:

1. `initialDelay: 60s`: a new pod first has to be discovered (Kubernetes SD), then scraped several times, before a 60 s `rate()` has anything to compute. Measuring earlier returns an empty vector, which counts as an error rather than a pass or a fail.
2. `or on() vector(0)` on the numerator: no 5xx series at all is a real answer, zero errors.
3. No fallback on the denominator: no requests at all means the canary is not measurable, and that must not look like success.
4. `{{args.canary-hash}}` filled from `podTemplateHashValue: Latest`: the query sees only the canary's pods, so a healthy stable set cannot dilute a broken canary.

### B.3: Strategy with analysis

```yaml
strategy:
  canary:
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
      - setWeight: 50
      - pause: {duration: 20s}
      - setWeight: 100
```

### B.4: Good version promotes itself

Loadgen running, then `kubectl argo rollouts set image gateway gateway=ghcr.io/kortezz12/quickticket-gateway:d300862...`:

```plaintext
[18:35:11.297] good +0.38s   Progressing step=0 updated=1 ready=4 | runs:
[18:35:21.293] good +10.37s  Paused      step=1 updated=1 ready=5 | runs:
[18:35:39.608] good +28.69s  Progressing step=2 updated=1 ready=5 | runs: gateway-65b977dbb6-6-2=Running
[18:37:19.000] good +128.08s Progressing step=3 updated=3 ready=4 | runs: gateway-65b977dbb6-6-2=Successful
[18:37:26.613] good +135.69s Paused      step=4 updated=3 ready=6 | runs: gateway-65b977dbb6-6-2=Successful
[18:37:54.291] good +163.37s Healthy     step=6 updated=5 ready=5 | runs: gateway-65b977dbb6-6-2=Successful

error-rate: phase=Successful count=3 successful=3
  2026-10-03T15:36:38Z value=[0] phase=Successful
  2026-10-03T15:36:58Z value=[0] phase=Successful
  2026-10-03T15:37:18Z value=[0] phase=Successful
```

No human action between `set image` and `Healthy`. The analysis took 99 s: 60 s initial delay plus two 20 s intervals between three measurements.

### B.5: Bad version, first as written in the lab

Reading the gateway code before the run suggested that the bad version from the lab text could not produce a single 5xx. `EVENTS_URL=http://broken-on-purpose:8081` breaks the gateway's `/health` as well as `/events`, because `/health` checks events through the same URL, and `/health` is the readiness probe. A pod failing readiness is never added to the Service endpoints, so no request reaches it. The run confirmed it:

```plaintext
[18:37:56.237] control +0.58s Progressing step=0 updated= ready=4 more replicas need to be updated
[18:37:58.752] control +3.09s Progressing step=0 updated=1 ready=4 more replicas need to be updated
[18:39:56.602] control: stop condition not reached in 120s
$ kubectl get pods -l app=gateway -L rollouts-pod-template-hash
NAME                       READY   STATUS    RESTARTS   AGE     ROLLOUTS-POD-TEMPLATE-HASH
gateway-65b977dbb6-4kpmk   1/1     Running   0          4m46s   65b977dbb6
gateway-65b977dbb6-6lgpr   1/1     Running   0          2m38s   65b977dbb6
gateway-65b977dbb6-l97w4   1/1     Running   0          2m11s   65b977dbb6
gateway-65b977dbb6-mmk97   1/1     Running   0          2m38s   65b977dbb6
gateway-6f98c95cf9-sgf7z   0/1     Running   0          2m      6f98c95cf9
Warning   Unhealthy   pod/gateway-6f98c95cf9-sgf7z   Readiness probe failed: Get "http://10.42.0.191:8080/health": context deadline exceeded
canary /health 503 {"status":"degraded","checks":{"events":"down","payments":"ok","circuit_payments":"CLOSED"}}
$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-65b977dbb6-6-2   Successful   4m21s
```

The rollout never left step 0: `setWeight: 20` completes only when the canary pod is available, so the analysis step was never reached and no AnalysisRun was created. Users were safe, since the readiness probe did the protecting, but the rollout would sit in `Progressing` indefinitely: according to the Argo Rollouts documentation, the default `progressDeadlineSeconds: 600` eventually marks it `Degraded` but does not abort (not waited out here), and it holds one of five stable pods scaled down the whole time (`ready=4`).

The fix is two fields, verified on the same broken version:

```yaml
spec:
  progressDeadlineSeconds: 60
  progressDeadlineAbort: true
```

```plaintext
T_APPLY 18:42:33.053
[18:42:33.753] +0.73s  Progressing step=0 updated= ready=4 more replicas need to be updated
[18:42:35.938] +2.91s  Progressing step=0 updated=1 ready=4 more replicas need to be updated
[18:43:35.965] +62.94s Degraded step=0 updated= ready=4 RolloutAborted: Rollout aborted update to revision 11: ReplicaSet "gateway-64768b6dcb" has timed out progressing.
```

Both fields are in the committed `k8s/gateway.yaml`.

### B.5: Bad version that the analysis has to catch

To exercise the AnalysisTemplate, the bad version has to pass readiness and still fail requests. The same `EVENTS_URL=http://payments:8082` trick as in Task 1 does exactly that.

```plaintext
[18:40:08.578] bad +0.55s   Progressing step=0 updated=1 ready=4 | runs: gateway-65b977dbb6-6-2=Successful
[18:40:16.604] bad +8.58s   Paused      step=1 updated=1 ready=5 | runs: gateway-65b977dbb6-6-2=Successful
[18:40:36.604] bad +28.58s  Progressing step=2 updated=1 ready=5 | runs: gateway-5849674d98-9-2=Running gateway-65b977dbb6-6-2=Successful
[18:41:55.626] bad +107.60s Degraded    step=0 updated=  ready=4 RolloutAborted: Rollout aborted update to revision 9: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1) | runs: gateway-5849674d98-9-2=Failed gateway-65b977dbb6-6-2=Successful

$ kubectl get analysisrun
NAME                     STATUS       AGE
gateway-5849674d98-9-2   Failed       80s
gateway-65b977dbb6-6-2   Successful   6m17s
```

`kubectl get analysisrun gateway-5849674d98-9-2 -o yaml`, status part:

```yaml
status:
  completedAt: "2026-10-03T15:41:55Z"
  message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
  metricResults:
  - count: 2
    failed: 2
    measurements:
    - finishedAt: "2026-10-03T15:41:35Z"
      phase: Failed
      startedAt: "2026-10-03T15:41:35Z"
      value: '[0.4432989690721649]'
    - finishedAt: "2026-10-03T15:41:55Z"
      phase: Failed
      startedAt: "2026-10-03T15:41:55Z"
      value: '[0.47619047619047616]'
    metadata:
      ResolvedPrometheusQuery: |
        (
          sum(rate(gateway_requests_total{rs_hash="5849674d98",status=~"5.."}[60s]))
          or on() vector(0)
        )
        /
        sum(rate(gateway_requests_total{rs_hash="5849674d98"}[60s]))
    name: error-rate
    phase: Failed
  phase: Failed
  startedAt: "2026-10-03T15:40:35Z"
```

The measured values are 0.44 and 0.48, not the `[1]` the lab text predicts. The ratio counts every request the canary served. The loadgen alternates `/events` (502 on this canary) with `/health` (200), and the kubelet's readiness probe adds a `/health` every 5 seconds, so a canary that fails every `/events` call still shows just under half of its requests as errors. The `[1]` in the lab text would need a canary whose every endpoint fails, and such a canary fails its own readiness probe, which is the case above that never reaches analysis. The run stopped after two measurements instead of three, as soon as `failed` exceeded `failureLimit`.

Final state after the aborted bad deploy:

```plaintext
Name:            gateway
Status:          ✖ Degraded
Message:         RolloutAborted: Rollout aborted update to revision 9: Step-based analysis phase error/failed: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)
Strategy:        Canary
  Step:          0/6
  SetWeight:     0
  ActualWeight:  0
Images:          ghcr.io/kortezz12/quickticket-gateway:d300862309bb539f5f735671cbf51d3a896e557c (stable)
Replicas:
  Desired:       5
  Current:       5
  Updated:       0
  Ready:         4
  Available:     4

NAME                                 KIND         STATUS         AGE    INFO
⟳ gateway                            Rollout      ✖ Degraded     23h
├──# revision:9
│  ├──⧉ gateway-5849674d98           ReplicaSet   • ScaledDown   109s   canary
│  └──α gateway-5849674d98-9-2       AnalysisRun  ✖ Failed       81s    ✖ 2
├──# revision:8
│  └──⧉ gateway-65b977dbb6           ReplicaSet   ◌ Progressing  6m46s  stable
│     ├──□ gateway-65b977dbb6-4kpmk  Pod          ✔ Running      6m46s  ready:1/1
│     ├──□ gateway-65b977dbb6-6lgpr  Pod          ✔ Running      4m38s  ready:1/1
│     ├──□ gateway-65b977dbb6-mmk97  Pod          ✔ Running      4m38s  ready:1/1
│     ├──□ gateway-65b977dbb6-l97w4  Pod          ✔ Running      4m11s  ready:1/1
│     └──□ gateway-65b977dbb6-8sl95  Pod          ✔ Running      1s     ready:0/1
```

The stable pods were untouched; the fifth one is the replacement for the pod the canary had borrowed. Re-applying the good manifest brought the rollout back to `Healthy` in 6.19 s.

> From `kubectl apply` of the bad version to the automatic abort took 107.6 s. For 99 s of that the canary was live and serving about 20% of traffic: the 20 s pause, the 60 s initial delay and one 20 s interval. The AnalysisRun itself ran 79 s, from 18:40:36 to 18:41:55. The initial delay trades exposure to real errors for protection against false aborts. With about 4 requests per second, half of them failing, the error was visible in the first scrapes; a shorter `initialDelay` paired with a minimum request count would have cut most of that minute.

### What metric I would add beyond error rate

Latency of the canary compared with stable: p99 from `gateway_request_duration_seconds_bucket`, filtered by `rs_hash` the same way, with a failure condition such as "canary p99 above 1.5 times stable p99". A slow version does not show up in an error ratio until it starts hitting the 5 s client timeout, and by then every request it serves is slow.

Two more that this run showed to be needed:

- **Per-path error ratio instead of a blended one.** The 0.44 above hides the fact that `/events` was failing 100% of the time on the canary. A bad version that breaks only `/pay`, which receives a small share of requests, would stay below 5% in the blended ratio and be promoted.
- **Canary readiness and restarts.** `kube_pod_container_status_restarts_total` or an `analysis` check on canary readiness turns "the canary never became ready" into an explicit failure with a reason, instead of a progress deadline timeout.

---

## Results

| Item | Result |
|---|---|
| Argo Rollouts | v1.10.0; client side install fails on CRD annotation size, server side apply works |
| First Rollout revision | goes straight to 100%, steps skipped (no stable to compare against) |
| `apply` to Paused at 20% | 8.36 s |
| Traffic split at 20% | 33 of 140 requests on the canary, 23.6% |
| Manual promote to Healthy | 47.29 s, 30 s of it the timed pause |
| Bad version at 20% | 50 of 184 requests failed (27.2%) |
| Abort to canary out of the Service | within 0.6 s; 0 of 466 requests failed after the abort |
| Abort to full stable capacity | 9.3 s |
| Lab 5 `git revert` for comparison | 189.84 s, 175.74 s of it ArgoCD polling |
| Multi-step rollout (20/40/60/80/100) | 256 s, 15 distinct watch states, 0 5xx |
| Good canary with analysis | 3 × `value=[0]`, Successful, Healthy 163 s after `set image` |
| Bad canary from the lab text | never Ready, no AnalysisRun, stuck at step 0 |
| Same, with `progressDeadlineAbort` and 60 s deadline | aborted automatically after 62.94 s |
| Bad canary that passes readiness | values 0.44 and 0.48, Failed, auto-abort 107.6 s after apply |

The thread through all three parts is that a canary is only as good as the signal that decides its fate. In Task 1 that signal was a person reading an in-cluster client, and it took 0.6 s to act on it. In the bonus it was a Prometheus query, and the bad canary stayed live for 99 s because the pause and the initial delay put off the first measurement. The control experiment showed a third signal the lab did not plan for: the readiness probe from Lab 4, which kept a broken canary away from every user but also kept it away from the analysis. That left the rollout waiting forever until a progress deadline was added. Choosing what the probe checks, what the analysis queries and how long each waits decides how quickly a bad version is caught and how many users see it first.

### State left behind

- The gateway runs as a `Healthy` Rollout with the committed `k8s/gateway.yaml` (bonus strategy, progress deadline guard).
- ArgoCD auto-sync for `quickticket` is still suspended. The fork's `main` still contains the Lab 5 Deployment, so re-enabling `selfHeal` before `main` carries the Rollout would recreate a second gateway behind the same Service. To restore after merging: `kubectl patch application quickticket -n argocd --type merge -p '{"spec":{"syncPolicy":{"automated":{"selfHeal":true}}}}'`.
- The in-cluster Prometheus in the `monitoring` namespace and the two AnalysisRuns are left in place as evidence.
