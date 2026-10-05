#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Explicit isolated context prevents fault injection into another cluster.
[[ $(kubectl config current-context) == k3d-quickticket-lab78 ]]
kubectl create namespace argo-rollouts --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argo-rollouts -f https://github.com/argoproj/argo-rollouts/releases/download/v1.9.0/install.yaml
kubectl rollout status -n argo-rollouts deploy/argo-rollouts --timeout=180s
kubectl apply -f labs/lab7/prometheus.yaml
kubectl apply -f k8s/analysis-template.yaml
kubectl delete deployment gateway --ignore-not-found
kubectl apply -k k8s
kubectl rollout status deploy/postgres --timeout=180s
if ! kubectl exec deploy/postgres -- psql -U quickticket -d quickticket -Atc "SELECT to_regclass('public.events')" | grep -q events; then
  kubectl exec -i deploy/postgres -- psql -v ON_ERROR_STOP=1 -U quickticket -d quickticket < app/seed.sql
fi
kubectl rollout status deploy/events --timeout=180s
kubectl rollout status deploy/payments --timeout=180s
kubectl rollout status -n monitoring deploy/prometheus --timeout=180s
