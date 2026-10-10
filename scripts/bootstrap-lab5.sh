#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Assumes a working kubectl context (Lab 4); no credential is stored in Git.
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply --server-side -n argocd -f \
  https://raw.githubusercontent.com/argoproj/argo-cd/v3.3.9/manifests/install.yaml
kubectl rollout status -n argocd deployment/argocd-server --timeout=180s
kubectl apply -f gitops/quickticket.yaml
printf '%s\n' 'Wait for CI to publish images and ArgoCD to report Synced/Healthy.' \
  'Then seed a fresh database once:' \
  'kubectl exec -i deploy/postgres -- psql -U quickticket -d quickticket < app/seed.sql'
