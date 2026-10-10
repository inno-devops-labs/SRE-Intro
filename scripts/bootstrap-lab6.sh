#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# QuickTicket must already be running in the current context/default namespace.
kubectl rollout status deploy/gateway --timeout=120s
if ! kubectl get secret lab6-grafana-admin >/dev/null 2>&1; then
  kubectl create secret generic lab6-grafana-admin \
    --from-literal=password="$(openssl rand -hex 20)"
fi
kubectl apply -k monitoring/lab6
kubectl rollout status deployment/prometheus --timeout=120s
kubectl rollout status deployment/grafana --timeout=120s
kubectl rollout status deployment/alert-receiver --timeout=120s
kubectl exec -i deploy/postgres -- psql -U quickticket -d quickticket < monitoring/lab6/seed.sql
kubectl scale deployment lab6-loadgen --replicas=1
printf '%s\n' 'Forward Grafana: kubectl port-forward svc/grafana 3000:3000' \
  'Forward Prometheus: kubectl port-forward svc/prometheus 9090:9090' \
  'Test delivery: python3 scripts/lab6-grafana.py test-contact' \
  'Stop synthetic traffic after the experiment: kubectl scale deploy/lab6-loadgen --replicas=0'
