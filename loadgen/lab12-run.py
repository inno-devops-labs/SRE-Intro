"""Bounded local experiments; append commands, timings and raw results."""

import argparse
import datetime
import json
import subprocess
import time
import runpy
import re
import urllib.error
import urllib.request
from pathlib import Path


observer = runpy.run_path(str(Path(__file__).with_name("lab12-observe.py")))
run = observer["run"]
snapshot = observer["snapshot"]


def pods(app):
    data = json.loads(run("kubectl", "get", "pods", "-n", "default", "-l", "app=" + app, "-o", "json"))
    return [p for p in data["items"] if not p["metadata"].get("deletionTimestamp")
            and p["status"].get("containerStatuses")
            and all(c["ready"] for c in p["status"]["containerStatuses"])]


def wait_replacements(old_uids, apps, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = {app: pods(app) for app in apps}
        ready_uids = {p["metadata"]["uid"] for items in current.values() for p in items}
        if all(len(current[app]) == count for app, count in apps.items()) and not ready_uids.intersection(old_uids):
            return {app: [p["metadata"]["name"] for p in items] for app, items in current.items()}
        time.sleep(1)
    raise TimeoutError("Replacement pods did not become Ready")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["capture", "kill", "evict", "restart", "migrate", "deploy"])
    parser.add_argument("value", nargs="?", default="checkpoint")
    args = parser.parse_args()
    if run("kubectl", "config", "current-context").strip() != "k3d-quickticket":
        raise RuntimeError("Wrong cluster")
    record = {"action": args.action, "value": args.value, "before": snapshot(args.action + "-before")}
    started = time.monotonic()
    try:
        if args.action == "kill":
            victims = [pods(app)[0] for app in ("gateway", "events")]
            record["victims"] = [p["metadata"]["name"] for p in victims]
            run("kubectl", "delete", "pod", *record["victims"], "-n", "default", "--wait=false")
            record["replacements"] = wait_replacements({p["metadata"]["uid"] for p in victims}, {"gateway": 5, "events": 2})
        elif args.action == "restart":
            old = {p["metadata"]["uid"] for p in pods("gateway")}
            stamp = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            record["restartAt"] = stamp
            run("kubectl", "patch", "rollout", "gateway", "-n", "default", "--type=merge", "-p", json.dumps({"spec": {"restartAt": stamp}}))
            record["replacements"] = wait_replacements(old, {"gateway": 5})
        elif args.action == "migrate":
            result = subprocess.run([".venv/bin/alembic", "upgrade", args.value], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            record["output"] = result.stdout
            record["returncode"] = result.returncode
            if result.returncode:
                raise RuntimeError(result.stdout)
        elif args.action == "deploy":
            record["output"] = run("kubectl", "apply", "-f", "k8s/events.yaml")
            record["output"] += run("kubectl", "rollout", "status", "deployment/events", "-n", "default", "--timeout=120s")
            record["image"] = run("kubectl", "get", "deployment", "events", "-n", "default", "-o", "jsonpath={.spec.template.spec.containers[0].image}")
        elif args.action == "evict":
            proxy = None
            try:
                run("kubectl", "patch", "pdb", "events-pdb", "-n", "default", "--type=merge", "-p", '{"spec":{"minAvailable":2}}')
                run("kubectl", "wait", "--for=jsonpath={.status.disruptionsAllowed}=0", "pdb/events-pdb", "-n", "default", "--timeout=30s")
                record["tightened_pdb"] = json.loads(run("kubectl", "get", "pdb", "events-pdb", "-n", "default", "-o", "json"))
                name = pods("events")[0]["metadata"]["name"]
                proxy = subprocess.Popen(["kubectl", "proxy", "--port=0"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                line = proxy.stdout.readline()
                match = re.search(r"127\.0\.0\.1:(\d+)", line)
                if not match:
                    raise RuntimeError(line)
                url = f"http://127.0.0.1:{match[1]}/api/v1/namespaces/default/pods/{name}/eviction"
                body = {"apiVersion": "policy/v1", "kind": "Eviction", "metadata": {"name": name, "namespace": "default"}}
                request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        record["http_status"] = response.status
                        record["eviction"] = json.load(response)
                except urllib.error.HTTPError as exc:
                    record["http_status"] = exc.code
                    record["eviction"] = json.loads(exc.read())
                if record["http_status"] != 429:
                    raise RuntimeError("Expected eviction to be rejected")
            finally:
                run("kubectl", "patch", "pdb", "events-pdb", "-n", "default", "--type=merge", "-p", '{"spec":{"minAvailable":1}}')
                if proxy is not None:
                    proxy.terminate()
                    proxy.wait(timeout=10)
        record["operation_seconds"] = round(time.monotonic() - started, 3)
        time.sleep(10)
        record["after"] = snapshot(args.action + "-after")
    except Exception as exc:
        record["error"] = str(exc)
        raise
    finally:
        with open("submissions/lab12-transitions.jsonl", "a", encoding="utf-8") as output:
            output.write(json.dumps(record) + "\n")
        summary = {k: v for k, v in record.items() if k not in ("before", "after", "tightened_pdb")}
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
