"""Run one reproducible in-cluster QuickTicket load level and save evidence."""

import argparse
import datetime as dt
import json
import pathlib
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "submissions" / "evidence" / "lab10"


def kubectl(*args, input_text=None, check=True):
    result = subprocess.run(
        ["kubectl", *args], input=input_text, text=True,
        capture_output=True, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(f"kubectl {' '.join(args)}: {result.stderr}")
    return result.stdout


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def clean_output(value):
    return "\n".join(line.rstrip() for line in value.rstrip().splitlines()) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("users", type=int)
    parser.add_argument("ramp", type=int)
    args = parser.parse_args()
    if kubectl("config", "current-context").strip() != "k3d-quickticket":
        raise RuntimeError("Expected local k3d-quickticket context")

    pods = json.loads(kubectl("get", "pods", "-o", "json"))["items"]
    for service, count in {"gateway": 5, "events": 1, "payments": 1, "redis": 1, "postgres": 1}.items():
        ready = [pod for pod in pods if pod["metadata"]["labels"].get("app") == service
                 and pod["status"].get("phase") == "Running"
                 and all(item["ready"] for item in pod["status"].get("containerStatuses", []))]
        if len(ready) != count:
            raise RuntimeError(f"Expected {count} ready {service} pods; got {len(ready)}")

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    name = f"load-{args.users}-{int(time.time())}"
    inventory = kubectl("exec", "deployment/postgres", "--", "psql", "-U", "quickticket",
                        "-d", "quickticket", "-Atc",
                        "SELECT id,total_tickets,COALESCE((SELECT SUM(quantity) FROM orders "
                        "WHERE event_id=events.id),0) FROM events WHERE id IN (3,5) ORDER BY id")
    if inventory.strip() != "3|500|0\n5|80|0":
        raise RuntimeError(f"Test event inventory is not at baseline: {inventory}")
    previous_jobs = json.loads(kubectl("get", "jobs", "-o", "json"))["items"]
    if any(job["metadata"]["name"].startswith("load-") and job["status"].get("active", 0)
           for job in previous_jobs):
        raise RuntimeError("Another load test is active")
    flush = kubectl("exec", "deployment/redis", "--", "redis-cli", "FLUSHDB")
    if flush.strip() != "OK":
        raise RuntimeError(f"Redis reset failed: {flush}")

    job = {
        "apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name},
        "spec": {"backoffLimit": 0, "ttlSecondsAfterFinished": 600,
                 "template": {"spec": {"restartPolicy": "Never", "containers": [{
                     "name": "locust", "image": "locustio/locust:2.43.4",
                     "command": ["locust"],
                     "args": ["-f", "/mnt/locust/locustfile.py", "--host=http://gateway:8080",
                              "--headless", "-u", str(args.users), "-r", str(args.ramp),
                              "-t", "60s", "--only-summary", "--exit-code-on-error", "0"],
                     "volumeMounts": [{"name": "locustfile", "mountPath": "/mnt/locust"}],
                 }], "volumes": [{"name": "locustfile", "configMap": {"name": "locustfile"}}]}}},
    }
    manifest = json.dumps(job, indent=2)
    kubectl("apply", "--dry-run=server", "-f", "-", input_text=manifest)
    (EVIDENCE / f"{name}.json").write_text(manifest + "\n")
    start = timestamp()
    kubectl("apply", "-f", "-", input_text=manifest)
    print(f"{name} started {start}", flush=True)
    for _ in range(60):
        if "Starting Locust" in kubectl("logs", f"job/{name}", check=False):
            break
        time.sleep(2)
    else:
        raise RuntimeError(f"Locust did not start in job/{name}")
    time.sleep(30)
    cpu = [f"UTC sample: {timestamp()}\n"]
    for service in ("gateway", "events", "payments", "redis", "postgres"):
        cpu.append(f"$ kubectl top pods -l app={service}\n")
        cpu.append(kubectl("top", "pods", "-l", f"app={service}", check=False))
    cpu.append(f"$ kubectl top pods -l job-name={name}\n")
    cpu.append(kubectl("top", "pods", "-l", f"job-name={name}", check=False))
    cpu.append("$ kubectl top nodes\n")
    cpu.append(kubectl("top", "nodes", check=False))
    (EVIDENCE / f"{name}-cpu.txt").write_text(clean_output("".join(cpu)))
    wait = subprocess.run(["kubectl", "wait", "--for=condition=complete", f"job/{name}",
                           "--timeout=120s"], capture_output=True, text=True)
    end = timestamp()
    logs = kubectl("logs", f"job/{name}", check=False)
    (EVIDENCE / f"{name}.log").write_text(clean_output(logs))
    state = kubectl("get", "job", name, "-o", "json")
    (EVIDENCE / f"{name}-status.json").write_text(state)
    (EVIDENCE / f"{name}-run.txt").write_text(
        f"context=k3d-quickticket namespace=default\nstart={start}\nend={end}\n"
        f"inventory_before={inventory.strip()!r}\nredis_flush={flush.strip()}\n"
        f"wait_exit={wait.returncode}\n{wait.stdout}{wait.stderr}"
    )
    print(f"{name} finished {end}; wait exit {wait.returncode}", flush=True)
    print("\n".join(line for line in logs.splitlines()
                    if line.startswith(("HTTP_STATUS_COUNTS=", "LOAD_SUMMARY="))), flush=True)
    if wait.returncode:
        raise RuntimeError(f"Job did not complete: {wait.stderr}")


if __name__ == "__main__":
    main()
