#!/usr/bin/env bash
# Deploy the QuickTicket monitoring + alerting stack into the k3d cluster.
#
#   ./monitoring/k8s/deploy.sh
#
# Everything lands in namespace `monitoring`. The Grafana provisioning files
# and dashboards in monitoring/grafana/ are the single source of truth — they
# are turned into ConfigMaps here rather than being duplicated in YAML.
#
# Prerequisites: kubectl context `k3d-quickticket`, and these images present
# in the node (they are not pulled from a registry by default):
#   k3d image import grafana/grafana:13.0.1 curlimages/curl:8.11.1 -c quickticket
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MON="$REPO_ROOT/monitoring"
NS=monitoring

kubectl apply -f "$MON/k8s/prometheus.yaml"

# Prometheus recording rules (Lab 3 SLIs) --------------------------------
kubectl create configmap prometheus-rules -n "$NS" \
  --from-file=rules.yml="$MON/prometheus/rules.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

# Grafana provisioning + dashboards -------------------------------------
kubectl create configmap grafana-datasources -n "$NS" \
  --from-file="$MON/grafana/provisioning/datasources/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-dashprovider -n "$NS" \
  --from-file="$MON/grafana/provisioning/dashboards/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-alerting -n "$NS" \
  --from-file="$MON/grafana/provisioning/alerting/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-dashboards -n "$NS" \
  --from-file="$MON/grafana/dashboards/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f "$MON/k8s/webhook-receiver.yaml"
kubectl apply -f "$MON/k8s/grafana.yaml"
kubectl apply -f "$MON/k8s/loadgen.yaml"

# Pick up ConfigMap changes on re-runs.
kubectl rollout restart deployment/prometheus deployment/grafana -n "$NS"
kubectl rollout status deployment/prometheus -n "$NS" --timeout=180s
kubectl rollout status deployment/grafana    -n "$NS" --timeout=180s
kubectl rollout status deployment/webhook-receiver -n "$NS" --timeout=180s
kubectl rollout status deployment/loadgen    -n default --timeout=180s

cat <<'EOF'

Deployed. Port-forwards:
  kubectl port-forward -n monitoring svc/grafana    3000:3000   # admin/admin
  kubectl port-forward -n monitoring svc/prometheus 9090:9090
  kubectl logs -n monitoring deploy/webhook-receiver -f          # alert payloads
EOF
