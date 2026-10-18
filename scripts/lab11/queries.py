"""Read real in-cluster Prometheus evidence; run in lab11-probe via stdin."""
from datetime import datetime, timezone
import json
import urllib.parse
import urllib.request

base = "http://prometheus.monitoring.svc.cluster.local:9090"
queries = [
    'up{job=~"gateway|notifications"}',
    'sum by(target,result) (gateway_retry_total)',
    'sum by(to) (gateway_circuit_breaker_transitions_total)',
    'sum by(path) (gateway_rate_limit_rejections_total)',
    'sum by(target) (gateway_bulkhead_rejections_total)',
    'gateway_bulkhead_in_flight',
    'sum by(result) (notifications_notify_total)',
    'histogram_quantile(0.99, sum by(le,path) (rate(gateway_request_duration_seconds_bucket[2m])))',
]
for query in queries:
    with urllib.request.urlopen(base + "/api/v1/query?" + urllib.parse.urlencode({"query": query}), timeout=15) as response:
        print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), "query": query,
                          "response": json.load(response)}), flush=True)
