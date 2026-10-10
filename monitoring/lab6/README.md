# Lab 6: Kubernetes alerting

Run `scripts/bootstrap-lab6.sh` with Lab 4/5 QuickTicket running in the default
namespace. This uses Kubernetes to match the course progression, rather than
starting a second Compose application with unrelated metrics. The manifests
are separate from the ArgoCD application path and do not replace the app.

The script creates a random Grafana admin password in a Kubernetes Secret
only if it is absent. Retrieve it locally with:

```bash
kubectl get secret lab6-grafana-admin -o jsonpath='{.data.password}' | base64 -d
```

Access Grafana on localhost:3000 using `kubectl port-forward svc/grafana 3000:3000`.
Prometheus is available through `kubectl port-forward svc/prometheus 9090:9090`.
The dashboard is `/d/quickticket-golden-signals`.

`alerts.yml` provisions both Grafana-managed rules; `notifications.yml` provides
the webhook and routing policy. They are versioned files, not manual UI state.
The local receiver accepts real Grafana notifications and prints JSON with
UTC receipt timestamps. It is a ClusterIP service; no Slack/Discord account or
external webhook is needed. `python3 scripts/lab6-grafana.py test-contact` uses
the Grafana 13 receiver API. Inspect `kubectl logs deploy/alert-receiver`.

The error numerator uses `or 0 * sum(rate(...))` so the lack of a 5xx series
correctly means zero errors while traffic exists. Missing traffic/metrics does
not silently become healthy: NoData and Error are explicit states. An actual
production setup should add a separate scrape/traffic alert. Both rules keep
the lab's 1m evaluation interval, 2m/5m pending periods and 5%/6x thresholds.

The SLI includes all instrumented gateway requests (including health checks),
matching Lab 3. The synthetic load performs one read, one reservation and one
payment per iteration; 50% failed payments therefore produce roughly 16.7%
aggregate 5xx, not 50%. It uses dedicated event 6006 with one million tickets
so stock depletion does not suppress the payment traffic. Do not run this
synthetic checkout scenario against real customer inventory.

The monitoring data and webhook log are ephemeral lab data. Export evidence
before removing/restarting the stack. `kubectl apply -k monitoring/lab6` resets
the load generator to its safe default of zero replicas; enable it explicitly
when starting an exercise. To update provisioning while it is running, reapply
and re-enable traffic. Changing a ConfigMap produces a new hash and rolls the
corresponding deployment. Avoid doing this in the middle of timing an alert.

The report contains GitOps-aware diagnosis, injection and rollback instructions.
An imperative env-var patch is undone when ArgoCD selfHeal is enabled; change
Git and revert that change. The actual test used the temporary branch
`experiment/lab6-payments`, then restored the Application to `feature/lab5`.

After the test, stop the load generator. Keep the application and monitoring
running for review, or delete only the lab resources with `kubectl delete -k
monitoring/lab6` (the standalone admin Secret may be deleted separately).

References:
- https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/file-provisioning/
- https://grafana.com/whats-new/2026-04-07-legacy-alertmanager-configuration-api-endpoints-changed/
