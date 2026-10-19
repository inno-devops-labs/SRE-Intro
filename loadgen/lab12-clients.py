"""Preserve mixedload receipts and classify every experiment window."""

import collections
import datetime
import json
import re
import subprocess
from pathlib import Path


def main():
    context = subprocess.check_output(["kubectl", "config", "current-context"], text=True).strip()
    if context != "k3d-quickticket":
        raise RuntimeError("Wrong cluster context")
    result = subprocess.run(["kubectl", "logs", "-n", "default", "-l", "app=mixedload", "--tail=-1", "--prefix"],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    if not result.stdout.strip():
        raise RuntimeError("No mixedload logs; refusing to overwrite existing evidence")
    Path("submissions/lab12-client.log").write_text("\n".join(line.rstrip() for line in result.stdout.splitlines()) + "\n")
    rows = []
    for line in result.stdout.splitlines():
        match = re.match(r"\[pod/([^/]+)/[^]]+\]\s+(\S+)\s+(read|pay|reserve_failed)\s*(.*)", line)
        if not match:
            raise ValueError("Unexpected client log: " + line)
        pod, stamp, operation, value = match.groups()
        rows.append({"pod": pod, "utc": stamp, "operation": operation,
                     "status": value.split()[0] if operation != "reserve_failed" else "reserve_failed"})

    def summarize(start, end):
        lower = datetime.datetime.fromisoformat(start).replace(microsecond=0)
        upper = datetime.datetime.fromisoformat(end).replace(microsecond=0) + datetime.timedelta(seconds=1)
        selected = [row for row in rows if lower <= datetime.datetime.fromisoformat(row["utc"].replace("Z", "+00:00")) <= upper]
        return {"start": start, "end": end, "receipts": len(selected),
                "statuses": dict(collections.Counter(row["status"] for row in selected)),
                "errors": [row for row in selected if row["status"] != "200"]}

    phases = [json.loads(line) for line in Path("submissions/lab12-transitions.jsonl").read_text().splitlines()]
    windows = [{"action": p["action"], "value": p["value"],
                **summarize(p["before"]["utc"], p["after"]["utc"])} for p in phases if "after" in p]
    first = next(p for p in phases if p["value"] == "0004_add_scheduled_at")
    last = next(p for p in phases if p["value"] == "0006_drop_event_date")
    summary = {"whole_bonus": summarize(first["before"]["utc"], last["after"]["utc"]), "windows": windows,
               "all_errors": [row for row in rows if row["status"] != "200"], "total_receipts": len(rows)}
    Path("submissions/lab12-client-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
