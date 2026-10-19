"""Sample the Lab 7 Prometheus through localhost:9091; print JSONL evidence."""

import datetime
import json
import sys
import time
import urllib.parse
import urllib.request


QUERIES = {
    "rps": "sum(rate(gateway_requests_total[1m]))",
    "user_error_ratio": '(sum(rate(gateway_requests_total{status=~"5..",path!="/health"}[1m])) or vector(0)) / sum(rate(gateway_requests_total{path!="/health"}[1m]))',
    "p99_by_path": 'histogram_quantile(0.99, sum by (le,path) (rate(gateway_request_duration_seconds_bucket{path!="/health"}[1m])))',
    "status_rates": 'sum by (path,status) (rate(gateway_requests_total{path!="/health"}[1m]))',
    "per_pod_rps": "sum by (pod) (rate(gateway_requests_total[1m]))",
    "5xx_increase_3m": 'sum(increase(gateway_requests_total{status=~"5.."}[3m]))',
}


def snapshot(label):
    data = {"at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "phase": label}
    for name, expression in QUERIES.items():
        url = "http://localhost:9091/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.load(response)
        if payload["status"] != "success":
            raise RuntimeError(payload)
        data[name] = payload["data"]["result"]
    print(json.dumps(data), flush=True)


def main():
    label = sys.argv[1]
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if duration < 0:
        raise ValueError("Duration must be non-negative")
    end = time.monotonic() + duration
    while True:
        snapshot(label)
        if time.monotonic() >= end:
            break
        time.sleep(min(15, max(0, end - time.monotonic())))


if __name__ == "__main__":
    main()
