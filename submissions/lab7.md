# Task 1
#### 1. **Output of `kubectl argo rollouts version`**
`kubectl-argo-rollouts: v1.9.0+838d4e7`
  `BuildDate: 2026-03-20T21:08:11Z`
  `GitCommit: 838d4e792be666ec11bd0c80331e0c5511b5010e`
  `GitTreeState: clean`
  `GoVersion: go1.24.13`
  `Compiler: gc`
  `Platform: linux/amd64`

#### 2. **Output of `kubectl argo rollouts get rollout gateway` showing Paused at 20% (during canary)**
`Name:            gateway`
`Namespace:       default`
`Status:          ॥ Paused`
`Message:         CanaryPauseStep`
`Strategy:        Canary`
  `Step:          1/5`
  `SetWeight:     20`
  `ActualWeight:  20`
`Images:          ghcr.io/sentaru-01/quickticket-gateway:f220a8c65310b0834909c1462be7be09d77bc6b6 (canary, stable)`
`Replicas:`
  `Desired:       5`
  `Current:       5`
  `Updated:       1`
  `Ready:         5`
  `Available:     5`

`NAME                                 KIND        STATUS     AGE    INFO`
`⟳ gateway                            Rollout     ॥ Paused   3h21m`  
`├──# revision:2`                                                    
`│  └──⧉ gateway-549d87dc9c           ReplicaSet  ✔ Healthy  40s    canary`
`│     └──□ gateway-549d87dc9c-wf69j  Pod         ✔ Running  38s    ready:1/1`
`└──# revision:1`                                                    
   `└──⧉ gateway-9b494bbb5            ReplicaSet  ✔ Healthy  3h21m  stable`
      `├──□ gateway-9b494bbb5-5dpth   Pod         ✔ Running  3h21m  ready:1/1`
      `├──□ gateway-9b494bbb5-5spg5   Pod         ✔ Running  3h21m  ready:1/1`
      `├──□ gateway-9b494bbb5-mf88r   Pod         ✔ Running  3h21m  ready:1/1`
      `└──□ gateway-9b494bbb5-wd268   Pod         ✔ Running  3h21m  ready:1/1`



#### 3. **Output after `promote` — showing progression to 100%**
`Name:            gateway`
`Namespace:       default`
`Status:          ✔ Healthy`
`Strategy:        Canary`
  `Step:          5/5`
  `SetWeight:     100`
  `ActualWeight:  100`
`Images:          ghcr.io/sentaru-01/quickticket-gateway:f220a8c65310b0834909c1462be7be09d77bc6b6 (stable)`
`Replicas:`
  `Desired:       5`
  `Current:       5`
  `Updated:       5`
  `Ready:         5`
  `Available:     5`

`NAME                                 KIND        STATUS        AGE    INFO`
`⟳ gateway                            Rollout     ✔ Healthy     3h33m`  
`├──# revision:2`                                                       
`│  └──⧉ gateway-549d87dc9c           ReplicaSet  ✔ Healthy     12m    stable`
`│     ├──□ gateway-549d87dc9c-wf69j  Pod         ✔ Running     12m    ready:1/1`
`│     ├──□ gateway-549d87dc9c-885nw  Pod         ✔ Running     73s    ready:1/1`
`│     ├──□ gateway-549d87dc9c-zg24t  Pod         ✔ Running     73s    ready:1/1`
`│     ├──□ gateway-549d87dc9c-ld2nl  Pod         ✔ Running     30s    ready:1/1`
`│     └──□ gateway-549d87dc9c-mghrv  Pod         ✔ Running     30s    ready:1/1`
`└──# revision:1`                                                       
   `└──⧉ gateway-9b494bbb5            ReplicaSet  • ScaledDown  3h33m`  


#### 4. **Output after `abort` — showing instant rollback**
`Name:            gateway`
`Namespace:       default`
`Status:          ✖ Degraded`
`Message:         RolloutAborted: Rollout aborted update to revision 6`
`Strategy:        Canary`
  `Step:          0/5`
  `SetWeight:     0`
  `ActualWeight:  0`
`Images:          ghcr.io/sentaru-01/quickticket-gateway:f220a8c65310b0834909c1462be7be09d77bc6b6 (stable)`
`Replicas:`
  `Desired:       5`
  `Current:       5`
  `Updated:       0`
  `Ready:         5`
  `Available:     5`

`NAME                                 KIND        STATUS        AGE    INFO`
`⟳ gateway                            Rollout     ✖ Degraded    3h42m`  
`├──# revision:6`                                                       
`│  └──⧉ gateway-869954cbd4           ReplicaSet  • ScaledDown  2m35s  canary`
`├──# revision:5`                                                       
`│  └──⧉ gateway-84749997b9           ReplicaSet  • ScaledDown  5m19s`  
`├──# revision:4`                                                       
`│  └──⧉ gateway-549d87dc9c           ReplicaSet  ✔ Healthy     21m    stable`
`│     ├──□ gateway-549d87dc9c-wf69j  Pod         ✔ Running     21m    ready:1/1`
`│     ├──□ gateway-549d87dc9c-885nw  Pod         ✔ Running     10m    ready:1/1`
`│     ├──□ gateway-549d87dc9c-zg24t  Pod         ✔ Running     10m    ready:1/1`
`│     ├──□ gateway-549d87dc9c-ld2nl  Pod         ✔ Running     9m21s  ready:1/1`
`│     └──□ gateway-549d87dc9c-2dkv6  Pod         ✔ Running     101s   ready:1/1`
`├──# revision:3`                                                       
`│  └──⧉ gateway-7794d89865           ReplicaSet  • ScaledDown  6m55s`  
`└──# revision:1`                                                       
   `└──⧉ gateway-9b494bbb5            ReplicaSet  • ScaledDown  3h42m`  
#### 5. **Answer: "How long from `abort` to all traffic serving the stable version? Compare with `git revert` rollback from Lab 5."**

**1. Time from `abort` to stable version:** **Almost instantly (less than 1-2 seconds).** Argo Rollouts immediately updates the traffic routing rules (Canary to 0%, Stable to 100%). Client traffic instantly stops hitting the broken version, even though Kubernetes takes a few extra seconds in the background to physically terminate the bad pods and spin up a replacement stable pod. 
**2. Comparison with `git revert` rollback (Lab 5):** A `git revert` rollback is much slower and takes **several minutes (typically 2–5 minutes)**. It requires a full deployment pipeline cycle: 1. Pushing the revert commit to Git. 2. Argo CD detecting the Git change and triggering a cluster sync. 3. Kubernetes performi- Your multi-step canary strategy YAML
- Output of `kubectl argo rollouts get rollout gateway --watch` showing at least 3 steps
- Dashboard observation during the rollout
- Answer: "At what canary percentage would you want an automated abort? Why?"ng a standard Rolling Update (pulling the image, starting new pods, waiting for Readiness Probes, and gracefully terminating the broken pods).

# Task 2

- **Your multi-step canary strategy YAML**
`strategy:` 
	`canary:` 
		`steps:` 
		`- setWeight: 20` 
		`- pause: {duration: 60s}` 
		`- setWeight: 40` 
		`- pause: {duration: 60s}` 
		`- setWeight: 60` 
		`- pause: {duration: 60s}` 
		`- setWeight: 80` 
		`- pause: {duration: 30s}` 
		`- setWeight: 100`

- **Output of `kubectl argo rollouts get rollout gateway --watch` showing at least 3 steps**
`Name:            gateway`
`Namespace:       default`
`Status:          ✖ Degraded`
`Message:         RolloutAborted: Rollout aborted update to revision 8`
`Strategy:        Canary`
  `Step:          0/9`
  `SetWeight:     0`
  `ActualWeight:  0`
`Images:          ghcr.io/sentaru-01/quickticket-gateway:f220a8c65310b0834909c1462be7be09d77bc6b6 (stable)`
`Replicas:`
  `Desired:       5`
  `Current:       5`
  `Updated:       0`
  `Ready:         5`
  `Available:     5`

`NAME                                 KIND        STATUS        AGE    INFO`
`⟳ gateway                            Rollout     ✖ Degraded    4h2m`   
`├──# revision:8`                                                       
`│  └──⧉ gateway-64675cbdf5           ReplicaSet  • ScaledDown  5m55s  canary`
`├──# revision:7`                                                       
`│  └──⧉ gateway-549d87dc9c           ReplicaSet  ✔ Healthy     41m    stable`
`│     ├──□ gateway-549d87dc9c-wf69j  Pod         ✔ Running     41m    ready:1/1`
`│     ├──□ gateway-549d87dc9c-885nw  Pod         ✔ Running     30m    ready:1/1`
`│     ├──□ gateway-549d87dc9c-zg24t  Pod         ✔ Running     30m    ready:1/1`
`│     ├──□ gateway-549d87dc9c-ld2nl  Pod         ✔ Running     29m    ready:1/1`
`│     └──□ gateway-549d87dc9c-p4lrk  Pod         ✔ Running     30s    ready:1/1`
`├──# revision:6`                                                       
`│  └──⧉ gateway-869954cbd4           ReplicaSet  • ScaledDown  22m`    
`├──# revision:5`                                                       
`│  └──⧉ gateway-84749997b9           ReplicaSet  • ScaledDown  25m`    
`├──# revision:3`                                                       
`│  └──⧉ gateway-7794d89865           ReplicaSet  • ScaledDown  27m`    
`└──# revision:1`                                                       
   `└──⧉ gateway-9b494bbb5            ReplicaSet  • ScaledDown  4h2m` 
   
- **Dashboard observation during the rollout**
	- **Does request rate stay steady across canary steps?** Yes. The `loadgen` tool generates a continuous and steady rate of traffic. Changing the canary steps changes which pods handle the traffic, but the overall cluster request volume remains constant.
	    
	- **Does the updated-replica count climb 1 → 2 → 3 → 4 → 5 as weight climbs?** Yes, it is designed to scale dynamically. In the log snippet, the rollout initialized at `SetWeight: 20`, which precisely triggered the creation of `1` updated canary replica (`Updated: 1`) out of 5 total desired pods.
	    
	- **At which step would you abort if you saw elevated errors?** I would abort immediately at the very first step (`SetWeight: 20`). There is no reason to proceed further if telemetry shows a critical issue right at the blast-radius perimeter.

- **Answer: "At what canary percentage would you want an automated abort? Why?"**
	**Answer:** I would want the automated abort to trigger at the **10% to 20%** stage (the first step). **Why:** This small initial percentage keeps the blast radius minimal, ensuring 80-90% of real users are completely unaffected by the faulty deployment. At the same time, under continuous load (via a load generator), 20% of traffic provides a large enough sample size for Prometheus metrics to clearly catch spikes in HTTP 5xx errors or sudden degradation in p99 latency, triggering an automated AnalysisRun rollback before things escalate.

# Bonus Task

- **`kubectl get analysistemplate gateway-error-rate` output**
	`NAME                 AGE`
	`gateway-error-rate   10s`

- **`kubectl get analysisrun` output showing Successful run (good canary) and Failed run (bad canary)**
	`NAME                     STATUS   AGE`
	`gateway-76fc9c895-16-2   Failed   11m`
	`gateway-7fd7f9896c   Successful   72m`


- **`kubectl get analysisrun <failed-name> -o yaml` showing the measurement values = `[1]`**
	`apiVersion: argoproj.io/v1alpha1`
	`kind: AnalysisRun`
	`metadata:`
	  `annotations:`
	    `rollout.argoproj.io/revision: "16"`
	  `creationTimestamp: "2026-07-02T19:00:42Z"`
	  `generation: 4`
	  `labels:`
	    `app: gateway`
	    `rollout-type: Step`
	    `rollouts-pod-template-hash: 76fc9c895`
	    `step-index: "2"`
	  `name: gateway-76fc9c895-16-2`
	  `namespace: default`
	  `ownerReferences:`
	  `- apiVersion: argoproj.io/v1alpha1`
	    `blockOwnerDeletion: true`
	    `controller: true`
	    `kind: Rollout`
	    `name: gateway`
	    `uid: f00f82ec-d46c-4291-b71c-52b3484aeb10`
	  `resourceVersion: "41354"`
	  `uid: 90d2c700-8cf9-4d6c-8e30-db4aba352d5d`
	`spec:`
	  `args:`
	  `- name: canary-hash`
	    `value: 76fc9c895`
	  `metrics:`
	  `- count: 3`
	    `failureLimit: 1`
	    `initialDelay: 60s`
	    `interval: 20s`
	    `name: error-rate`
	    `provider:`
	      `prometheus:`
	        `address: http://prometheus.monitoring.svc.cluster.local:9090`
	        `authentication:`
	          `oauth2: {}`
	          `sigv4: {}`
	        `query: |`
	          `(`
	            `sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}",status=~"5.."}[60s]))`
	            `or on() vector(0)`
	          `)`
	          `/`
	          `sum(rate(gateway_requests_total{rs_hash="{{args.canary-hash}}"}[60s]))`
	    `successCondition: result[0] < 0.05`
	`status:`
	  `completedAt: "2026-07-02T19:02:02Z"`
	  `dryRunSummary: {}`
	  `message: Metric "error-rate" assessed Failed due to failed (2) > failureLimit (1)`
	  `metricResults:`
	  `- count: 2`
	    `failed: 2`
	    `measurements:`
	    `- finishedAt: "2026-07-02T19:01:42Z"`
	      `phase: Failed`
	      `startedAt: "2026-07-02T19:01:42Z"`
	      `value: '[NaN]'`
	    `- finishedAt: "2026-07-02T19:02:02Z"`
	      `phase: Failed`
	      `startedAt: "2026-07-02T19:02:02Z"`
	      `value: '[NaN]'`
	    `metadata:`
	      `ResolvedPrometheusQuery: |`
	        `(`
	          `sum(rate(gateway_requests_total{rs_hash="76fc9c895",status=~"5.."}[60s]))`
	          `or on() vector(0)`
	        `)`
	        `/`
	        `sum(rate(gateway_requests_total{rs_hash="76fc9c895"}[60s]))`
	    `name: error-rate`
	    `phase: Failed`
	  `phase: Failed`
	  `runSummary:`
	    `count: 1`
	    `failed: 1`
	  `startedAt: "2026-07-02T19:00:42Z"`

- **Final `kubectl argo rollouts get rollout gateway` after the aborted bad deploy (Degraded, stable pods running)**
	`Name:            gateway`
	`Namespace:       default`
	`Status:          ✖ Degraded`
	`Message:         RolloutAborted: Rollout aborted update to revision 17`
	`Strategy:        Canary`
	  `Step:          0/6`
	  `SetWeight:     0`
	  `ActualWeight:  0`
	`Images:          ghcr.io/sentaru-01/quickticket-gateway:f220a8c65310b0834909c1462be7be09d77bc6b6 (stable)`
	`Replicas:`
	  `Desired:       5`
	  `Current:       5`
	  `Updated:       0`
	  `Ready:         5`
	  `Available:     5`
	
	`NAME                                 KIND         STATUS        AGE    INFO`
	`⟳ gateway                            Rollout      ✖ Degraded    6h10m`  
	`├──# revision:17`                                                       
	`│  └──⧉ gateway-667b9b594            ReplicaSet   • ScaledDown  17m    canary`
	`├──# revision:16`                                                       
	`│  ├──⧉ gateway-76fc9c895            ReplicaSet   • ScaledDown  21m`    
	`│  └──α gateway-76fc9c895-16-2       AnalysisRun  ✖ Failed      20m    ✖ 2`
	`├──# revision:15`                                                       
	`│  └──⧉ gateway-c665c7846            ReplicaSet   • ScaledDown  26m`    
	`├──# revision:14`                                                       
	`│  └──⧉ gateway-77f7d7dc68           ReplicaSet   • ScaledDown  41m`    
	`├──# revision:13`                                                       
	`│  └──⧉ gateway-7fd7f9896c           ReplicaSet   ✔ Healthy     82m    stable`
	`│     ├──□ gateway-7fd7f9896c-r7gqd  Pod          ✔ Running     27m    ready:1/1`
	`│     ├──□ gateway-7fd7f9896c-nj6cd  Pod          ✔ Running     26m    ready:1/1`
	`│     ├──□ gateway-7fd7f9896c-rbg4b  Pod          ✔ Running     26m    ready:1/1`
	`│     ├──□ gateway-7fd7f9896c-g5lnw  Pod          ✔ Running     26m    ready:1/1`
	`│     └──□ gateway-7fd7f9896c-ppwxt  Pod          ✔ Running     14m    ready:1/1`
	`├──# revision:12`                                                       
	`│  └──⧉ gateway-78dfc5f677           ReplicaSet   • ScaledDown  89m`    
	`├──# revision:11`                                                       
	`│  └──⧉ gateway-799bbfbc44           ReplicaSet   • ScaledDown  94m`    
	`├──# revision:10`                                                       
	`│  └──⧉ gateway-64fc877cd4           ReplicaSet   • ScaledDown  100m`   
	`├──# revision:8`                                                        
	`│  └──⧉ gateway-64675cbdf5           ReplicaSet   • ScaledDown  134m`   
	`├──# revision:6`                                                        
	`│  └──⧉ gateway-869954cbd4           ReplicaSet   • ScaledDown  150m`   
	`├──# revision:5`                                                        
	`│  └──⧉ gateway-84749997b9           ReplicaSet   • ScaledDown  153m`   
	`└──# revision:3`                                                        
	   `└──⧉ gateway-7794d89865           ReplicaSet   • ScaledDown  155m`  

- **Answer: "What metric would you add beyond error rate for a more complete canary analysis?"**
	`To ensure a robust canary analysis, I would add the following metrics beyond the error rate:`
	
	1. `**Latency (P95/P99):** To detect performance regressions. Even if the service returns "200 OK," excessive response times degrade the user experience.`
	2. `**Request Throughput (RPS):** To identify issues where the new version fails to handle the expected volume of traffic compared to the stable version.`
	3. `**Resource Saturation (CPU/Memory):** To detect memory leaks or abnormal CPU spikes that could lead to crashes or node instability.`
	
	`Adding these metrics ensures that the new version is not only error-free but also performant and resource-efficient.`
