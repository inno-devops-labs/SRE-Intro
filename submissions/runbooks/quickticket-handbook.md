# QuickTicket SRE Handbook

**Scope:** operational quick reference for the SRE-Intro QuickTicket lab
environment.\
**Environment:** Kubernetes on k3d; GitHub Actions/GHCR; Argo CD; Argo
Rollouts; Prometheus and Grafana.

## 1. Architecture

``` mermaid
flowchart TD
    U[Client / in-cluster Locust] --> S[Kubernetes Service: gateway:8080]
    S --> G[Gateway Rollout]
    G --> E[Events service]
    G --> P[Payments service]
    E --> R[(Redis)]
    E --> DB[(PostgreSQL + PVC)]
    M[Prometheus] --> G
    M --> E
    M --> P
    M --> R
    M --> DB
    M --> D[Grafana dashboards and alerts]
    CI[GitHub Actions] --> REG[GHCR images]
    REG --> CD[Argo CD sync]
    CD --> RO[Argo Rollouts canary]
    RO --> G
```

-   **Gateway** handles client requests and routes event/reservation
    operations.
-   **Events** owns event and ticket-reservation behavior; **payments**
    handles payment operations.
-   **Redis** is used by the application for transient state;
    **PostgreSQL** has persistent storage through a PVC.
-   **Prometheus** collects metrics; **Grafana** is used to inspect
    dashboards and alerts.
-   The gateway is managed by **Argo Rollouts** for canary releases. The
    Service must target only the intended Rollout pods. During Lab 10, a
    legacy `Deployment/gateway` was found alongside the five-pod
    Rollout; it was scaled to zero for testing. Reconcile the manifests
    so it does not return and verify the Service endpoints after
    changes.

## 2. How to deploy

Follow the repository's GitOps flow rather than editing live objects as
the normal release process.

1.  Create a focused feature branch and commit the application or
    Kubernetes change.
2.  Push the branch and open a pull request. Review the diff and CI
    result before merging.
3.  After merge, GitHub Actions builds and publishes the container image
    to GHCR. Use the image reference/tag recorded by the workflow; do
    not guess a tag.
4.  Update the version-controlled Kubernetes configuration to reference
    the intended image and merge that change.
5.  Argo CD reconciles the desired state from Git. Check the application
    sync/health status before proceeding.
6.  Argo Rollouts performs the canary according to the Rollout steps and
    AnalysisTemplate. Watch the rollout and its AnalysisRuns; promote
    only when analysis and health checks pass. Abort or roll back when
    the rollout is unhealthy.
7.  Verify the resulting state:

``` bash
kubectl get pods,svc
kubectl get rollouts
kubectl get analysisruns
kubectl get endpoints gateway
```

If the Service endpoint count or pod ownership is unexpected, stop and
fix the source manifests before running load tests. Avoid treating an
imperative `kubectl scale` as a permanent fix; reconcile the intended
state in Git.

## 3. Monitoring

Start with Grafana's existing dashboards and Prometheus targets. Check
the golden signals---traffic, errors, latency, and saturation---across
gateway, events, and payments, and inspect Redis/PostgreSQL when a
dependency is implicated.

### Useful checks

``` bash
kubectl get pods -o wide
kubectl top pods -l app=gateway
kubectl top pods -l app=events
kubectl top pods -l app=payments
kubectl logs deploy/events --since=10m
kubectl logs deploy/payments --since=10m
kubectl get rollouts
kubectl get analysisruns
```

Use the actual resource names shown by
`kubectl get pods,deployments,rollouts` if they differ.

-   **Server errors:** inspect the existing `gateway_requests_total`
    metric and group by HTTP status/path. Track `5xx` separately from
    expected `409 Conflict` booking responses; the latter must not be
    counted as server-error SLO failures.
-   **Error-rate SLO:** the Lab 10 target is less than **0.5% `5xx`**.
-   **Latency SLO:** the Lab 10 target is **p99 below 500 ms**. Use the
    histogram already exported by the application and the matching
    bucket metric in Prometheus; verify its exact metric name before
    creating a query.
-   **Saturation:** compare per-pod CPU/memory with requests and limits;
    inspect restarts, readiness, CPU throttling, and unavailable
    replicas.
-   **Canary:** review Rollout status and AnalysisRun phase/reason. A
    failed analysis or stalled rollout should be investigated before
    promotion.
-   **Dependencies:** correlate gateway errors with events, payments,
    Redis, and PostgreSQL logs and latency. A gateway `502` alone does
    not identify the failing dependency.

## 4. Incident response

1.  **Confirm impact.** Check the Grafana golden-signals dashboard and
    Prometheus alerts. Determine whether the incident is elevated `5xx`,
    high p99, failed readiness, or a dependency outage.
2.  **Check current state.** Run `kubectl get pods -o wide`,
    `kubectl get rollouts`, and `kubectl get analysisruns`. Inspect
    recent logs for the affected component with
    `kubectl logs <pod> --since=10m`.
3.  **Contain safely.** If a canary coincides with the incident, stop
    promotion and use the project's Argo Rollouts procedure to
    abort/roll back. Do not scale or delete unrelated resources without
    identifying their controller and impact.
4.  **Trace dependencies.** Compare gateway errors with events/payments
    logs and Redis/PostgreSQL health. Keep `409` booking conflicts
    separate from `5xx` failures.
5.  **Recover and verify.** Confirm pods are Ready, the Rollout is
    healthy, endpoints are correct, and error rate/latency return below
    the lab thresholds. Run a small in-cluster smoke/load test.
6.  **Record follow-up.** Capture timestamps, affected release SHA,
    AnalysisRun result, logs/metrics, recovery time, root cause, and a
    prevention action. Use this evidence for DORA calculations.

Escalate to the project/infrastructure owner if PostgreSQL data
integrity, backup restoration, payment correctness, or persistent
dependency failure is involved. Do not perform destructive database
actions during an incident without confirming the backup and restore
path.

## 5. Backup and restore

The Lab 9 backup implementation is version-controlled in
`k8s/backup-cronjob.yaml`; PostgreSQL storage is defined in
`k8s/postgres.yaml`, and the submission notes are in
`submissions/lab9.md`. Treat those files as the source of truth for the
exact backup command, destination, credentials, and restore procedure.

### Before a restore

1.  Identify the actual CronJob, backup artifact location, and
    PostgreSQL pod:

``` bash
kubectl get cronjobs
kubectl get jobs
kubectl get pods -l app=postgres
kubectl get pvc
```

2.  Read `k8s/backup-cronjob.yaml` and `submissions/lab9.md` to confirm
    the backup destination and exact restore command. Verify that the
    selected backup is recent and readable.
3.  Confirm the target namespace/context and coordinate a maintenance
    window. A restore can overwrite current data; do not guess the
    backup path or pipe an unverified artifact into PostgreSQL.

### Run and validate a backup

-   Check the schedule and command in `k8s/backup-cronjob.yaml`.
-   Inspect the latest backup Job and its logs using `kubectl get jobs`
    and `kubectl logs job/<actual-backup-job-name>`.
-   Confirm the artifact exists at the configured destination and that
    the Job completed successfully. A successful Job alone does not
    prove that a restore will work.

### Restore and validate

-   Follow the exact restore procedure documented in
    `submissions/lab9.md` and the backup manifest. Use the verified
    artifact and correct database/user from the repository
    configuration; do not delete the PostgreSQL PVC as a shortcut.
-   After restoration, verify PostgreSQL readiness, expected
    schema/data, and application connectivity. Check the events and
    gateway endpoints, then run read-only smoke tests before enabling
    normal traffic.
-   Record the backup timestamp, artifact identifier, restore start/end,
    validation results, and any data loss. Periodically test restoration
    in a disposable environment.

## Operational lessons from Lab 10

-   Run Locust inside the cluster against `http://gateway:8080`; a local
    port-forward may pin the test to one endpoint.
-   Flush Redis between controlled reservation load tests so stale holds
    do not contaminate results.
-   Keep `409` inventory conflicts separate from `5xx` in SLO reporting.
-   Lab 10's comparable five-Rollout-pod tests breached the 0.5% `5xx`
    threshold at 150 users (0.59%) and 200 users (0.93%). Investigate
    the server errors before declaring a higher safe capacity.
-   Reconcile the legacy gateway Deployment and Service selector in Git,
    and verify endpoint membership after deployment.
