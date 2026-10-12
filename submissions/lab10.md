# QuickTicket Reliability Review

**Lab:** SRE Intro --- Lab 10\
**Test date:** 12 October 2026\
**Environment:** k3d, in-cluster Locust 2.43.4, Kubernetes Service
`gateway:8080`

## 1. SLO Compliance

The lab's reliability thresholds are a server-side `5xx` rate below 0.5%
and aggregate p99 latency below 500 ms. `409 Conflict` responses are
reported separately: they are booking conflicts and must not be counted
as `5xx` SLO failures.

  -------------------------------------------------------------------------
  SLO                           Target   Observed in latest Status
                                            comparable runs 
  --------------- -------------------- -------------------- ---------------
  Server error                `< 0.5%`      0% at 10 and 50 Breached at 150
  rate                                 users; 0.02% at 100; and 200 users
                                        0.59% at 150; 0.93% 
                                                     at 200 

  Aggregate p99             `< 500 ms`  28, 33, 44, 54, and Met at all
  latency                                  60 ms at 10, 50, tested levels
                                          100, 150, and 200 
                                         users respectively 
  -------------------------------------------------------------------------

The first tested load level that breaches a reliability threshold is
**150 users**: its `5xx` rate is 0.59%. The exact boundary has not been
measured; it lies between the 100- and 150-user test points under this
setup. The 200-user test also breaches the error-rate threshold. Neither
test breaches the p99 latency threshold.

## 2. Load Test Results

All five results below were collected after scaling the duplicate
`Deployment/gateway` to zero replicas, leaving the five intended Argo
Rollout gateway pods selected by the Service. Locust ran **inside the
cluster** against `http://gateway:8080`; Redis was flushed before each
run. Each run lasted 60 seconds. Ramp-up was 2, 5, 10, 15, and 20
users/second.

  -------------------------------------------------------------------------------------------------
    Users   Ramp-up   Requests      RPS    p50    p95    p99       `5xx`   `5xx`   `409`     Locust
                                                               responses    rate   count   failures
  ------- --------- ---------- -------- ------ ------ ------ ----------- ------- ------- ----------
       10       2/s        467     7.82   9 ms  19 ms  28 ms           0   0.00%       0  0 (0.00%)

       50       5/s      2,202    36.78   6 ms  18 ms  33 ms           0   0.00%      28 28 (1.27%)

      100      10/s      4,467    74.63   5 ms  15 ms  44 ms           1   0.02%     287        288
                                                                                            (6.45%)

      150      15/s      6,657   111.15   4 ms  15 ms  54 ms          39   0.59%     744        783
                                                                                           (11.76%)

      200      20/s      8,933   149.14   4 ms  16 ms  60 ms          83   0.93%   1,139      1,222
                                                                                           (13.68%)
  -------------------------------------------------------------------------------------------------

Percentages are calculated from the request counts shown in each run.
Locust marks `409` responses as failed requests, so total Locust failure
rate is not the same as the server-side `5xx` rate.

### Error observations

-   **10 users:** no failed requests.
-   **50 users:** 28 `409` conflicts on `POST /events/5/reserve`; no
    `5xx`.
-   **100 users:** one `502` on `GET /events` and 287 `409` conflicts;
    no other `5xx`.
-   **150 users:** 18 `502` on `GET /events`, 2 `503` on `/health`, and
    19 `500` responses on reserve endpoints; 744 `409` conflicts.
-   **200 users:** 49 `502` on `GET /events`, 7 `503` on `/health`, 26
    `500` and 1 `502` on reserve endpoints; 1,139 `409` conflicts.

The high `409` count is not evidence of a server-side SLO breach by
itself. It is consistent with contention for limited ticket inventory.
The underlying cause of the `5xx` responses has not been conclusively
identified; the gateway logs inspected earlier did not show matching
errors for the original test window.

### Test validity and limitations

A configuration issue was discovered during testing: the Service
selector `app=gateway` initially selected seven pods --- five from the
Argo Rollout and two from a separate Deployment. The duplicate
Deployment was scaled to zero in the live cluster, and the Service was
verified to have five endpoints before the latest runs. The Deployment
manifest and Service selector still need to be reconciled in source
control so this ambiguity does not return.

The tests were run sequentially with Redis flushed before each. The
10--200 user results in this table are from the five-replica
configuration after the duplicate Deployment was scaled down. The tests
use a mixed read/reserve/health workload, so the `409` counts depend on
reservation contention and should not be interpreted as infrastructure
errors.

## 3. DORA Metrics

DORA metrics should count unique production deployments and relate them
to changes and incidents. The collected evidence is insufficient to
calculate all four DORA metrics reliably, so unmeasured values are
explicitly marked rather than estimated as facts.

  -------------------------------------------------------------------------
  Metric                  Collected evidence        Result / limitation
  ----------------------- ------------------------- -----------------------
  Deployment frequency    Current Argo Rollout      **Not reliably
                          revision is 13. The Git   measurable** from this
                          history on `main`         evidence: a fixed
                          available during this     reporting window and a
                          review ended at `0c99b99` list of unique
                          dated 2026-09-29; the     successful production
                          current Lab 10 branch was releases are required.
                          not yet merged.           A revision number is
                                                    not a deployment count.

  Change failure rate     Nine AnalysisRun objects: The unsuccessful
                          6 `Successful`, 2         AnalysisRun share is
                          `Failed`, 1 `Error`.      `3/9 = 33.3%`. This is
                                                    an indicator, **not**
                                                    the DORA change failure
                                                    rate, because
                                                    AnalysisRuns have not
                                                    been mapped to unique
                                                    production deployments.

  Lead time for changes   Rollout creation          **Not measured.** The
                          timestamp                 Argo CD polling
                          `2026-10-03T17:28:57Z`;   interval can add up to
                          `RolloutCompleted`        about 3 minutes, but
                          transition                this is an estimate of
                          `2026-10-03T21:46:55Z`;   one component, not
                          Healthy transition        measured end-to-end
                          `2026-10-12T06:58:46Z`.   lead time.
                          End-to-end                
                          commit-to-production      
                          timestamps were not       
                          collected.                

  Time to restore service No paired incident-start  **Not measured.**
                          and service-restoration   Rollout status
                          timestamps were           timestamps alone do not
                          collected.                establish incident
                                                    recovery time.
  -------------------------------------------------------------------------

**DORA improvement action:** Record the release commit SHA, CI
start/end, Argo CD sync time, Rollout start/completion, AnalysisRun
outcome, and incident/restoration timestamps for each release. Calculate
all four metrics over a fixed reporting period and count unique
production releases.

## 4. Top 3 Reliability Risks

1.  **Ambiguous gateway Service selection.** The selector `app=gateway`
    originally included five Rollout pods and two Deployment pods. This
    makes capacity tests and traffic distribution ambiguous and can
    cause configuration drift. **Fix:** remove or reconcile the legacy
    Deployment in the version-controlled configuration; ensure the
    Service targets only the intended Rollout; verify EndpointSlice
    membership after every deployment.
2.  **Unexplained `5xx` under load.** The measured server-error rate
    rises above the 0.5% threshold at 150 users and reaches 0.93% at 200
    users. The source of `502`, `500`, and `503` responses has not been
    proven. **Fix:** correlate gateway, events, payments, Redis, and
    PostgreSQL logs by request ID and timestamp; capture dependency
    latency and errors; reproduce at intermediate load levels.
3.  **Single-instance dependencies and limited failure isolation.**
    Events, payments, Redis, and PostgreSQL were observed as single pods
    during this review. A single-instance dependency can become a
    bottleneck or a single point of failure. **Fix:** define recovery
    objectives, verify backups and restore procedures, and evaluate HA
    for Redis/PostgreSQL and safe horizontal scaling for stateless
    services. Validate each change with failure-injection tests.

## 5. Toil Identification

The exact number of manual repetitions was not instrumented, so
frequency is described qualitatively rather than presented as a
fabricated count.

  -------------------------------------------------------------------------
  Repeated manual   Frequency         Automation proposal Expected saving
  task              evidence                              
  ----------------- ----------------- ------------------- -----------------
  Recreating        Repeated during   Use stable          Saves several
  port-forwards and Labs 1--9; exact  in-cluster Service  minutes per
  rechecking        count not         DNS for in-cluster  recurrence and
  endpoints after   recorded          clients; provide a  reduces testing
  pod/rollout                         small diagnostic    against one
  changes                             script for local    unintended pod
                                      access and endpoint 
                                      checks              

  Manually watching Repeated during   Keep Argo Rollouts  Reduces
  canary rollout    rollout           AnalysisTemplates   continuous
  status and        exercises; exact  and automated       terminal watching
  inspecting        count not         promotion/abort     and makes
  AnalysisRuns      recorded          criteria; add a     promotion
                                      concise status      decisions
                                      command/script for  reproducible
                                      evidence capture    

  Re-seeding or     Repeated during   Automate safe       Saves setup time
  manually checking lab setup and     test-environment    and prevents
  application state load testing;     reset and           stale state from
  after restarts,   exact count not   seed/health checks  contaminating
  and flushing      recorded          as a documented     load-test results
  Redis between                       script or           
  load runs                           Kubernetes Job;     
                                      guard destructive   
                                      operations by       
                                      namespace/context   
  -------------------------------------------------------------------------

Savings are qualitative estimates; no time-tracking data was collected.

## 6. Monitoring Gaps

-   **Latency alerts:** track p95/p99 latency for gateway and downstream
    services, not only error rate. A slow dependency can degrade user
    experience before `5xx` increases.
-   **Dependency-level golden signals:** collect request rate, error
    rate, and latency for events, payments, Redis, and PostgreSQL;
    correlate them with gateway request IDs.
-   **Separate expected conflicts from server failures:** dashboard
    `409` booking conflicts separately from `5xx`; alert on unexpected
    changes in conflict rate, but exclude normal inventory conflicts
    from the server-error SLO.
-   **Availability and saturation:** alert on sustained `5xx` above
    0.5%, p99 above 500 ms, pod restarts/readiness failures, CPU
    throttling, memory pressure, and unavailable replicas.
-   **Rollout visibility:** alert on failed AnalysisRuns, aborted or
    stalled Rollouts, and disagreement between desired and available
    replicas.
-   **Load-test evidence:** retain test parameters, ConfigMap revision,
    Job logs, pod resource samples, and timestamps together so results
    can be reproduced and compared.

## 7. Capacity Plan

### Current tested capacity

The highest tested level that stayed below the 0.5% `5xx` threshold was
**100 users / 74.63 RPS**. The 150-user run reached 111.15 RPS and
breached the threshold at 0.59%; 200 users reached 149.14 RPS and
breached it at 0.93%. Therefore, approximately 75 RPS is the current
demonstrated operating point under this workload, not a guarantee of
production capacity. The exact threshold boundary between 100 and 150
users has not been tested.

### Per-pod resource samples under load

`kubectl top pods` was sampled during the 150- and 200-user tests. These
are point-in-time samples, not sustained peak measurements.

  --------------------------------------------------------------------------
  Load           Gateway CPU    Gateway memory Events CPU /   Payments CPU /
                 per pod        per pod        memory         memory
  -------------- -------------- -------------- -------------- --------------
  150 users,     56--91m        56--57 MiB     140m / 60 MiB  9m / 37 MiB
  second sample                                               

  200 users,     28--63m        about 57 MiB   66m / 61 MiB   7m / 36 MiB
  sample around                                               
  20 seconds                                                  
  into run                                                    
  --------------------------------------------------------------------------

The samples do not prove a stable CPU bottleneck: `events` reached 140m
at one 150-user sample but was at 66m in the 200-user sample. Collect
longer time series and dependency latency before choosing final resource
allocations. Payments CPU remained low in these samples.

The observed gateway resource settings are requests of **100m CPU / 128
MiB memory** and limits of **500m CPU / 256 MiB memory** per pod.

### Plan for 2× traffic

A 2× target relative to the 100-user result is approximately **149
RPS**. The 200-user test reached 149.14 RPS but breached the `5xx`
threshold, so that throughput is not yet a safe operating target.

1.  **Fix configuration and errors first:** make the Service select only
    the intended Rollout pods in source control; investigate and fix the
    `5xx` causes before claiming a higher safe capacity.
2.  **Gateway:** use **8 replicas** as an initial experiment (up from
    5), retaining the current 100m/128 MiB requests and 500m/256 MiB
    limits initially. Repeat the same workload and tune only from
    sustained CPU, memory, latency, and error data. Scaling alone is not
    assumed to fix application or dependency errors.
3.  **Events:** keep the current replica count until the error source is
    understood, then test **2--3 replicas** if the service is stateless
    and supports horizontal scaling. Validate shared state and inventory
    consistency first.
4.  **Payments:** CPU was only 7--9m in the observed samples, so there
    is no evidence for CPU-driven scaling. Consider a second replica for
    availability only after validating idempotency and payment-state
    consistency.
5.  **Redis and PostgreSQL:** do not assume one pod is adequate for
    production. Assess managed/replicated Redis, PostgreSQL HA, backups,
    restore time, connection-pool limits, and failure behavior.
6.  **Acceptance test:** rerun the 10/50/100/150/200-user tests after
    each change, then test intermediate levels between 100 and 150.
    Accept the new capacity only when the `5xx` rate is below 0.5% and
    p99 below 500 ms at the target load.

### Rough cost estimate

The local k3d cluster has no cloud billing data, so this is a planning
assumption rather than an observed cost. Using the lab's small-cloud
assumption of about **US\$5 per additional pod per month**, three extra
gateway replicas (5 → 8) would cost roughly **US\$15/month**. A broader
placeholder range of US\$5--15 per pod/month gives **US\$15--45/month**.
This excludes extra events/payments replicas, HA Redis/PostgreSQL,
storage, monitoring, and traffic. Replace it with the actual provider
estimate before production budgeting.

## Reproduction notes

-   Locust ran in-cluster against `http://gateway:8080`, not through a
    local port-forward.
-   Redis was flushed before each official load run.
-   Jobs ran for 60 seconds at 10, 50, 100, 150, and 200 users.
-   Locust Jobs can exit with code 1 when they record failed requests;
    the logs show the run reached its 60-second limit and printed a
    summary. Interpret `409` separately from `5xx`.
-   The legacy `Deployment/gateway` was scaled to zero in the live
    cluster for the latest comparable runs. The source manifests still
    need to be reconciled to prevent the extra replicas from returning.
