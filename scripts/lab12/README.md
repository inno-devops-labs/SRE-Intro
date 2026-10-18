# Lab 12 evidence tools

These are controlled lab tools, not an unattended production migration job.
Read `submissions/lab12.md` and the schema recovery supplement before replaying.
They assume the existing quickticket cluster, healthy dependencies, sufficient
inventory and the existing `postgres-backup-credentials` secret. No secret is
printed or included in the evidence. The observer Role only reads pods and
EndpointSlices in `default`.

Build the tools from repository root:

```powershell
docker build -f scripts/lab12/Dockerfile -t quickticket-lab12-tools:v1 .
k3d image import quickticket-lab12-tools:v1 -c quickticket
kubectl create configmap lab12-scripts --from-file=scripts/lab12/probe.py --from-file=scripts/lab12/migrate.py --from-file=scripts/lab12/stage.py --from-file=scripts/lab12/schema.py
kubectl apply -f scripts/lab12/runner.yaml
kubectl wait --for=condition=Ready pod/lab12-client --timeout=90s
kubectl cp alembic.ini lab12-client:/work/alembic.ini
kubectl cp migrations lab12-client:/work/migrations
foreach ($name in @('probe.py','stage.py','schema.py','export.py','final_check.py')) {
    kubectl cp "scripts/lab12/$name" "lab12-client:/work/$name"
}
```

Copy migrations only into a clean `/work/migrations`; copying an already present
directory again can nest it. Check `python /scripts/migrate.py heads` before use.
The tools derive DATABASE_URL from the injected environment; do not echo it.

Run the observer in the tools pod, retaining `/tmp/lab12.jsonl`:

```powershell
kubectl exec lab12-client -- python -c "import subprocess; p=subprocess.Popen(['python','/scripts/probe.py','observe','--duration','7200'],stdout=subprocess.DEVNULL,stderr=open('/tmp/observer-errors.txt','w')); print(p.pid)"
kubectl apply -f labs/lab8/mixedload.yaml
kubectl exec lab12-client -- python /work/stage.py start baseline
# Observe traffic, then close this exact window once:
kubectl exec lab12-client -- python /work/stage.py end baseline
```

Use unique window names for repetitions. `resilience.ps1` performs the explicit
pod deletion or Rollout restart, polls real UIDs/endpoints and closes its window
in finally. Its plugin path matches the official binary already installed in
this environment; adapt that path if reproducing elsewhere.

`sync-events.ps1` syncs the already pushed HEAD and waits for successful ArgoCD
operation, two Ready intended-image pods and disappearance of all old consumers.
It does not push, patch schema or bypass an analysis gate. Keep automation paused
only for the explicitly controlled migration sequence, then restore it.

For A and B, build Events at commits `207fa15` and `edef8ae` respectively in
separate clean checkouts, using their source-hash tags recorded in the report.
Import both images into k3d. Never build the current B source under the A tag.
Apply the five exact stages separately; do not use `upgrade head` to skip the
application compatibility deployments. `compatibility.ps1` is intended only for
explicit temporary databases restored/created for validation, not production.

`export.py` prints compact JSON from previously saved window files. Store generated
evidence separately from dumps; never put credentials or the full traffic log
into Git. Before deleting the tools pod, stop the observer with
`kubectl exec lab12-client -- touch /tmp/lab12-stop`, wait for it to exit and copy
its log to the host. On Windows, use a relative local destination from the desired
external directory with `kubectl cp` to avoid drive-colon parsing. Preserve the
working DB, PVCs, backups and Prometheus when removing your temporary resources.
