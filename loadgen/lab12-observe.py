"""Record real Kubernetes/Prometheus observations for the local Lab 12 drill."""

import argparse
import datetime
import json
import subprocess
import time
import urllib.parse


QUERIES = {
    "5xx_total": 'sum(gateway_requests_total{status=~"5.."}) or vector(0)',
    "5xx_1m": 'sum(increase(gateway_requests_total{status=~"5.."}[1m])) or vector(0)',
    "5xx_3m": 'sum(increase(gateway_requests_total{status=~"5.."}[3m])) or vector(0)',
    "requests_1m": 'sum(increase(gateway_requests_total{path!="/health"}[1m])) or vector(0)',
    "up": 'up{job="gateway"}',
}


def run(*args):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(result.stdout)
    return result.stdout


def snapshot(label):
    result = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "label": label}
    result["prometheus"] = {}
    for name, query in QUERIES.items():
        url = "http://localhost:9090/api/v1/query?query=" + urllib.parse.quote(query, safe="")
        result["prometheus"][name] = json.loads(run("kubectl", "exec", "-n", "monitoring", "deployment/prometheus", "--", "wget", "-qO-", url))
    pods = json.loads(run("kubectl", "get", "pods", "-n", "default", "-o", "json"))
    result["pods"] = [{"name": p["metadata"]["name"], "uid": p["metadata"]["uid"],
                       "created": p["metadata"]["creationTimestamp"],
                       "deleting": p["metadata"].get("deletionTimestamp"),
                       "node": p["spec"].get("nodeName"),
                       "ready": [c.get("ready") for c in p["status"].get("containerStatuses", [])]}
                      for p in pods["items"]]
    result["rollout"] = json.loads(run("kubectl", "get", "rollout", "gateway", "-n", "default", "-o", "json"))["status"]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--output", default="submissions/lab12-metrics.jsonl")
    args = parser.parse_args()
    if run("kubectl", "config", "current-context").strip() != "k3d-quickticket":
        raise RuntimeError("Wrong cluster context")
    try:
        with open(args.output, "a", encoding="utf-8") as output:
            while True:
                result = snapshot(args.label)
                output.write(json.dumps(result) + "\n")
                output.flush()
                scalars = {k: v["data"]["result"] for k, v in result["prometheus"].items() if k != "up"}
                print(json.dumps({"utc": result["utc"], "label": args.label, "values": scalars,
                                  "phase": result["rollout"].get("phase")}), flush=True)
                if not args.watch:
                    break
                time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
