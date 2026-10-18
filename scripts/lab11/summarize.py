"""Summarize a probe JSONL file without dropping failures or changing timings."""
import argparse
from collections import Counter
import json


def summarize(path):
    with open(path, "rb") as source:
        bom = source.read(2)
    encoding = "utf-16" if bom in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    with open(path, encoding=encoding) as source:
        rows = [json.loads(line) for line in source if line.strip()]
    requests = [r for r in rows if r["kind"] == "request"]
    result = {"file": path, "start": rows[0]["timestamp"], "end": rows[-1]["timestamp"],
              "summaries": [r for r in rows if r["kind"] == "summary"],
              "peak_client_pay_in_flight": max((r.get("peak_client_pay_in_flight", 0) for r in rows), default=0)}
    pay = [r for r in requests if r["path"].endswith("/pay")]
    reasons = Counter()
    for r in pay:
        body = r.get("body", {})
        reason = body.get("error", body.get("detail", "confirmed")) if isinstance(body, dict) else str(body)
        reasons[f"{r['status']} {reason}"] += 1
    result["payment_reasons"] = dict(reasons)
    result["examples"] = {}
    for record in requests:
        group = "pay" if record["path"].endswith("/pay") else "reserve" if record["path"].endswith("/reserve") else record["path"]
        key = f"{group} HTTP {record['status']}"
        result["examples"].setdefault(key, record)
    result["transport_errors"] = sum(r["status"] == 0 for r in requests)
    result["rate_limited"] = sum(r["status"] == 429 for r in requests)
    result["retry_after_values"] = dict(Counter(r["retry_after"] for r in requests if r["status"] == 429))
    events = [r for r in requests if r["path"] == "/events"]
    result["events_ok_below_500ms"] = sum(r["status"] == 200 and r["duration_ms"] <= 500 for r in events)
    result["events_slow_above_500ms"] = sum(r["duration_ms"] > 500 for r in events)
    result["per_target_metrics"] = {}
    for r in rows:
        if r["kind"] != "metrics":
            continue
        state = result["per_target_metrics"].setdefault(r["target"], {"before": {}, "after": {}, "peak_occupancy": 0})
        for line in r["lines"]:
            # Numeric values only; labels contain no whitespace in these metrics.
            metric, value = line.rsplit(" ", 1)
            if metric.split("{", 1)[0].endswith("_created"):
                continue
            if r["label"] in ("before", "after"):
                state[r["label"]][metric] = float(value)
            if metric.startswith("gateway_bulkhead_in_flight{"):
                state["peak_occupancy"] = max(state["peak_occupancy"], float(value))
    for state in result["per_target_metrics"].values():
        state["delta"] = {key: value - state["before"].get(key, 0)
                          for key, value in state["after"].items()}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    summarize(parser.parse_args().path)
