"""Reproduce Lab 11 on the existing local k3d deployment (changes fault settings).

Requires a healthy updated Gateway Rollout, Notifications, Events, Payments,
Postgres, Redis and in-cluster Prometheus. Restores dependency fault settings
and removes only its own probe pods and test data, including on failure.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "submissions/lab11"
PROBES = ["lab11-client", "lab11-bounded", "lab11-unbounded"]


def kubectl(*args, stdin=None):
    p = subprocess.run(["kubectl", *args], input=stdin, text=True, capture_output=True)
    if p.returncode:
        raise RuntimeError(f"kubectl {' '.join(args)}: {p.stderr or p.stdout}")
    return p.stdout


def obj(*args):
    return json.loads(kubectl(*args, "-o", "json"))


def save(name, data):
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")


def apply(value):
    kubectl("apply", "-f", "-", stdin=json.dumps(value))


def env(deployment, **values):
    print(f"{deployment}: {values}", flush=True)
    kubectl("set", "env", f"deployment/{deployment}", *(f"{k}={v}" for k, v in values.items()))
    kubectl("rollout", "status", f"deployment/{deployment}", "--timeout=90s")


def create_probe(name, server=False, bypass=False):
    container = {"name": name, "image": "quickticket-gateway:lab11", "imagePullPolicy": "Never"}
    if server:
        container.update(env=[{"name": "RATE_LIMIT_RPS", "value": "1000"},
                              {"name": "NOTIFICATIONS_URL", "value": "http://notifications:8083"}],
                         readinessProbe={"httpGet": {"path": "/health", "port": 8080}, "periodSeconds": 2})
        if bypass:
            container["command"] = ["python", "-c", "import main, uvicorn; "
                "main.payments_bulkhead.call = lambda func: func(); "
                "uvicorn.run(main.app, host='0.0.0.0', port=8080)"]
    else:
        container["command"] = ["python", "-c", "import time; time.sleep(3600)"]
    apply({"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name,
           "labels": {"app": "lab11-gateway" if server else "lab11-client"}},
           "spec": {"restartPolicy": "Never", "containers": [container]}})
    kubectl("wait", "--for=condition=Ready", f"pod/{name}", "--timeout=90s")
    return "http://" + obj("get", "pod", name)["status"]["podIP"] + ":8080"


def prom_query(query):
    from urllib.parse import urlencode
    url = "http://localhost:9090/api/v1/query?" + urlencode({"query": query})
    return json.loads(kubectl("exec", "-n", "monitoring", "deployment/prometheus", "--", "wget", "-qO-", url))


def configure_scrapes(include_probes):
    cm = obj("get", "cm", "prometheus-config", "-n", "monitoring")
    config = yaml.safe_load(cm["data"]["prometheus.yml"])
    jobs = config["scrape_configs"]
    jobs[:] = [job for job in jobs if job["job_name"] not in ("notifications", "lab11-gateway")]
    jobs.extend(yaml.safe_load((ROOT / "monitoring/prometheus/lab11-jobs.yaml").read_text()))
    if include_probes:
        jobs.append({"job_name": "lab11-gateway", "scrape_interval": "1s",
            "kubernetes_sd_configs": [{"role": "pod", "namespaces": {"names": ["default"]}}],
            "relabel_configs": [
                {"source_labels": ["__meta_kubernetes_pod_label_app"], "regex": "lab11-gateway", "action": "keep"},
                {"source_labels": ["__meta_kubernetes_pod_ip"], "regex": "(.+)",
                 "target_label": "__address__", "replacement": "${1}:8080"},
                {"source_labels": ["__meta_kubernetes_pod_name"], "target_label": "pod"}]})
    rendered = yaml.safe_dump(config, sort_keys=False)
    kubectl("patch", "cm", "prometheus-config", "-n", "monitoring", "--type=merge", "-p",
            json.dumps({"data": {"prometheus.yml": rendered}}))
    # Projected ConfigMaps update asynchronously. Signal reload only after the
    # mounted file is current; preserve TSDB and all existing alert rules.
    for _ in range(120):
        mounted = kubectl("exec", "-n", "monitoring", "deployment/prometheus", "--", "cat", "/etc/prometheus/prometheus.yml")
        if mounted == rendered:
            kubectl("exec", "-n", "monitoring", "deployment/prometheus", "--", "kill", "-HUP", "1")
            print("Prometheus config reloaded", flush=True)
            return
        time.sleep(1)
    raise RuntimeError("Prometheus ConfigMap projection did not update")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="store_true", required=True, help="apply local fault injections and run tests")
    p.add_argument("--retry-only", action="store_true", help="repeat only the statistical payment experiment with failure rate 0.3")
    args = p.parse_args()
    if kubectl("config", "current-context").strip() != "k3d-quickticket":
        raise RuntimeError("Select the local k3d-quickticket context before running fault injection")
    OUT.mkdir(parents=True, exist_ok=True)
    existing_pods = {pod["metadata"]["name"] for pod in obj("get", "pods")["items"]}
    for name in PROBES:
        if name in existing_pods:
            raise RuntimeError(f"{name} already exists; inspect it before rerunning")
    originals = {}
    for name in ("notifications", "payments"):
        originals[name] = {e["name"]: e.get("value", "") for e in obj("get", "deploy", name)["spec"]["template"]["spec"]["containers"][0]["env"]}
    event_id = None
    reservations = []
    configured = False
    summary = {}
    try:
        create_probe(PROBES[0])
        bounded = unbounded = None
        if not args.retry_only:
            bounded = create_probe(PROBES[1], server=True)
            unbounded = create_probe(PROBES[2], server=True, bypass=True)
            configured = True
            configure_scrapes(True)
        pods = obj("get", "pods", "-l", "app=gateway")["items"]
        rollout = obj("get", "rollout", "gateway")
        current_hash = rollout["status"]["currentPodHash"]
        pod = next(p for p in pods if p["metadata"]["labels"].get("rollouts-pod-template-hash") == current_hash
                   and all(c["ready"] for c in p["status"].get("containerStatuses", [])))
        url = "http://" + pod["status"]["podIP"] + ":8080"
        sql = "INSERT INTO events (name,venue,event_date,total_tickets,price_cents) VALUES ('Lab 11 experiment','Lab','2026-12-01',10000,0) RETURNING id;"
        output = kubectl("exec", "deployment/postgres", "--", "psql", "-U", "quickticket", "-d", "quickticket", "-v", "ON_ERROR_STOP=1", "-At", "-c", sql)
        event_id = int(output.splitlines()[0])
        summary.update(started_at=datetime.now(timezone.utc).isoformat(), gateway_pod=pod["metadata"]["name"],
                       event_id=event_id, bounded_url=bounded, unbounded_url=unbounded)
        print(f"Target {pod['metadata']['name']}; dedicated event {event_id}", flush=True)
        probe_source = (ROOT / "scripts/lab11_probe.py").read_text()
        def probe(name, mode="checkout", target=url, count=30, waves=1):
            print(f"Running {name}", flush=True)
            output = kubectl("exec", "-i", PROBES[0], "--", "python", "-", "--url", target,
                             "--event-id", str(event_id), "--mode", mode, "--count", str(count),
                             "--waves", str(waves), stdin=probe_source)
            data = json.loads(output)
            reservations.extend(data.pop("reservation_ids"))
            save(name, data)
            print(json.dumps({k: v for k, v in data.items() if k not in
                 ("rows", "metrics_before", "metrics_after", "notification_metrics")}), flush=True)
            summary[name] = {k: v for k, v in data.items() if k not in
                            ("rows", "metrics_before", "metrics_after", "notification_metrics")}
            return data
        if args.retry_only:
            env("payments", PAYMENT_FAILURE_RATE="0.3", PAYMENT_LATENCY_MS="0")
            probe("11-payment-retry-repeat")
            time.sleep(6)
            save("11-prometheus", prom_query('sum by (target,result) (gateway_retry_total)'))
        else:
            env("payments", PAYMENT_FAILURE_RATE="0.0", PAYMENT_LATENCY_MS="0")
            env("notifications", NOTIFY_FAILURE_RATE="0.0", NOTIFY_LATENCY_MS="0")
            probe("01-baseline")
            env("notifications", NOTIFY_FAILURE_RATE="0.3", NOTIFY_LATENCY_MS="300")
            probe("02-notify-failure")
            time.sleep(6)
            save("02-prometheus", {"notifications": prom_query('sum by (result) (notifications_notify_total)'),
                 "pay_p99": prom_query('histogram_quantile(0.99,sum by (le)(rate(gateway_request_duration_seconds_bucket{method="POST",path="/reserve/{id}/pay",pod="' + pod["metadata"]["name"] + '"}[2m])))')})
            env("notifications", NOTIFY_FAILURE_RATE="0.0", NOTIFY_LATENCY_MS="0")
            env("payments", PAYMENT_FAILURE_RATE="0.3", PAYMENT_LATENCY_MS="0")
            probe("03-payment-retry")
            time.sleep(6)
            save("03-prometheus", prom_query('sum by (target,result) (gateway_retry_total)'))
            env("payments", PAYMENT_FAILURE_RATE="1.0", PAYMENT_LATENCY_MS="0")
            probe("04-circuit-open")
            env("payments", PAYMENT_FAILURE_RATE="0.0", PAYMENT_LATENCY_MS="0")
            print("Waiting 31s for circuit cooldown", flush=True)
            time.sleep(31)
            probe("05-circuit-recovery", mode="recovery", count=15)
            time.sleep(1.1)
            probe("06-rate-burst", mode="burst", count=100)
            time.sleep(1.1)
            probe("07-rate-sustained", mode="sustained", count=30)
            time.sleep(6)
            save("07-prometheus", {"transitions": prom_query('sum by (to) (gateway_circuit_breaker_transitions_total)'),
                                    "rate_limit": prom_query('sum by (path) (gateway_rate_limit_rejections_total)')})
            probe("08-events-baseline", mode="events-baseline", target=bounded)
            env("payments", PAYMENT_FAILURE_RATE="0.0", PAYMENT_LATENCY_MS="3000")
            probe("09-bulkhead", mode="bulkhead", target=bounded, waves=3)
            time.sleep(2)
            save("09-prometheus", {"rejections": prom_query('gateway_bulkhead_rejections_total{job="lab11-gateway",pod="lab11-bounded"}'),
                "max_in_flight": prom_query('max_over_time(gateway_bulkhead_in_flight{job="lab11-gateway",pod="lab11-bounded",target="payments"}[2m])')})
            probe("10-without-bulkhead", mode="bulkhead", target=unbounded)
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        print("Restoring fault settings and removing experiment resources", flush=True)
        failures = []
        for name, values in originals.items():
            keys = ("NOTIFY_FAILURE_RATE", "NOTIFY_LATENCY_MS") if name == "notifications" else ("PAYMENT_FAILURE_RATE", "PAYMENT_LATENCY_MS")
            try:
                env(name, **{key: values.get(key, "0") for key in keys})
            except Exception as exc:
                failures.append(str(exc))
        if event_id:
            try:
                sql = f"DELETE FROM orders WHERE event_id={event_id}; DELETE FROM events WHERE id={event_id};"
                kubectl("exec", "deployment/postgres", "--", "psql", "-U", "quickticket", "-d", "quickticket", "-v", "ON_ERROR_STOP=1", "-c", sql)
                keys = [f"reservation:{rid}" for rid in reservations] + [f"event:{event_id}:held"]
                for i in range(0, len(keys), 100):
                    kubectl("exec", "deployment/redis", "--", "redis-cli", "DEL", *keys[i:i+100])
            except Exception as exc:
                failures.append(str(exc))
        for name in PROBES:
            try:
                kubectl("delete", "pod", name, "--ignore-not-found", "--wait=false")
            except Exception as exc:
                failures.append(str(exc))
        if configured:
            try:
                configure_scrapes(False)
            except Exception as exc:
                failures.append(str(exc))
        summary["cleanup_errors"] = failures
        save("summary-retry-repeat" if args.retry_only else "summary", summary)
        if failures:
            raise RuntimeError("; ".join(failures))


if __name__ == "__main__":
    main()
