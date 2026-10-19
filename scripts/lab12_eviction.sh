#!/usr/bin/env bash
set -euo pipefail

kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":2}}'
restore() {
  kubectl patch pdb events-pdb --type=merge -p '{"spec":{"minAvailable":1}}' >/dev/null
  if [[ -n "${PROXY_PID:-}" ]]; then kill "$PROXY_PID" 2>/dev/null || true; fi
}
trap restore EXIT
kubectl get pdb events-pdb
kubectl proxy --port=8901 >/tmp/lab12-proxy.log 2>&1 &
PROXY_PID=$!
sleep 2
POD=$(kubectl get pod -l app=events -o jsonpath='{.items[0].metadata.name}')
curl -sS -i -X POST -H 'Content-Type: application/json' -d "{\"apiVersion\":\"policy/v1\",\"kind\":\"Eviction\",\"metadata\":{\"name\":\"$POD\",\"namespace\":\"default\"}}" "http://127.0.0.1:8901/api/v1/namespaces/default/pods/$POD/eviction"
