# QuickTicket — Lab 8

This PR includes the healthy Lab 7 Rollout/AnalysisTemplate and the prerequisite
application manifests so it can be checked out independently from main. The
Lab 7 report and evidence remain in its separate PR.

```bash
k3d cluster create quickticket-lab78 --image rancher/k3s:v1.33.6-k3s1 --agents 1 --wait
# Install kubectl-argo-rollouts v1.9.0 for your platform, plus Python PyYAML.
./scripts/bootstrap-lab7.sh
python scripts/run-lab8.py
```

The runner checks the isolated context, restores the original events readiness
for baseline measurements, seeds dedicated event 7808 (one million zero-cost
synthetic tickets), runs the experiments, then applies the committed readiness
improvement and repeats the Redis outage. A finally block restores Redis,
payment latency and the improved events manifest, and removes the loadgen.
`python scripts/watch-lab8-load.py` can capture client-side failures alongside
Prometheus while the loadgen exists. Hypotheses are timestamped before injection.

The final readiness probe on events checks its TCP listener and verifies PostgreSQL directly with a bounded
connection and `SELECT 1`, without calling the Redis-dependent `/health`.
It never treats a failed DB check as ready. This keeps DB-only listing
available when reservations fail; `/health` remains degraded and bookings
still depend on Redis. Init containers still require Redis at cold start.
This fix preserves warm-service reads, not availability during a fresh start
with Redis already unavailable.

Gateway readiness remains the Lab 7 TCP check, so dependency failures do not
remove every gateway endpoint. The gateway still reports dependency health
honestly through `/health`.

Optional Grafana: `kubectl apply -f monitoring/lab7/grafana.yaml`, then
`kubectl port-forward -n monitoring svc/grafana 3000:3000` and open
http://localhost:3000/d/quickticket-lab7. Prometheus lives in `monitoring`.

PostgreSQL is ephemeral, credentials are demo values, and all charges are
simulated. Run this only in the isolated course cluster. ArgoCD self-heal is
intentionally absent there; GitOps environments would revert imperative fault
injection unless isolated in an experiment branch.
