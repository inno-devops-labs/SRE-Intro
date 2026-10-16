"""Run one bounded in-cluster Locust Job and print its evidence as JSON."""

import argparse
import datetime
import json
import subprocess
import time


def kubectl(*args, input_text=None):
    result = subprocess.run(
        ["kubectl", "--namespace=default", *args], input=input_text, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True,
    )
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("users", type=int)
    parser.add_argument("ramp", type=int)
    parser.add_argument("--suffix", default="")
    args = parser.parse_args()
    if args.users < 1 or args.ramp < 1:
        parser.error("users and ramp must be positive")
    if kubectl("config", "current-context").strip() != "k3d-quickticket":
        raise RuntimeError("Refusing to load-test outside k3d-quickticket")
    name = f"lab10-load-{args.users}{args.suffix}"
    if kubectl("get", "job", name, "--ignore-not-found", "-o", "name").strip():
        raise RuntimeError("Job already exists; choose a new suffix")
    existing = json.loads(kubectl("get", "jobs", "-l", "experiment=lab10", "-o", "json"))
    if any(job.get("status", {}).get("active", 0) for job in existing["items"]):
        raise RuntimeError("Another Lab 10 Job is active; wait before flushing Redis")
    redis = json.loads(kubectl("get", "pods", "-l", "app=redis", "-o", "json"))
    assert len(redis["items"]) == 1
    kubectl("exec", redis["items"][0]["metadata"]["name"], "--", "redis-cli", "FLUSHDB")
    job = {
        "apiVersion": "batch/v1", "kind": "Job",
        "metadata": {"name": name, "labels": {"experiment": "lab10"}},
        "spec": {"backoffLimit": 0, "template": {"spec": {
            "restartPolicy": "Never", "containers": [{
                "name": "locust", "image": "locustio/locust:2.43.4",
                "command": ["locust"], "args": ["-f", "/mnt/locust/locustfile.py",
                    "--host=http://gateway:8080", "--headless", "-u", str(args.users),
                    "-r", str(args.ramp), "-t", "60s", "--only-summary"],
                "volumeMounts": [{"name": "locustfile", "mountPath": "/mnt/locust"}],
            }], "volumes": [{"name": "locustfile", "configMap": {"name": "locustfile"}}],
        }}},
    }
    kubectl("create", "-f", "-", input_text=json.dumps(job))
    samples = []
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        state = json.loads(kubectl("get", "job", name, "-o", "json"))
        status = state.get("status", {})
        if status.get("succeeded") or status.get("failed"):
            break
        samples.append({"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "top": kubectl("top", "pods"),
                        "nodes": kubectl("top", "nodes")})
        time.sleep(10)
    else:
        raise RuntimeError("Job did not finish within 240 seconds")
    logs = kubectl("logs", "job/" + name)
    result = next(json.loads(line.split("LAB10_RESULT ", 1)[1])
                  for line in logs.splitlines() if line.startswith("LAB10_RESULT "))
    print(json.dumps({"users": args.users, "ramp": args.ramp, "result": result,
                      "samples": samples, "logs": logs}), flush=True)


if __name__ == "__main__":
    main()
