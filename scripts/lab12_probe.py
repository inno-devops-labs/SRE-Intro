"""Client-side HTTP availability probe; output real status counts and timing.

Usage:
    python3 scripts/lab12_probe.py --url http://localhost:8080/events --seconds 60 --interval 0.15
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import time
import urllib.error
import urllib.request


def probe(url, seconds, interval, timeout):
    counts = Counter()
    failures = []
    started = time.monotonic()
    latencies_ms = []
    while time.monotonic() - started < seconds:
        lap = time.monotonic()
        code = "connection-error"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as result:
                code = str(result.status)
                result.read(128)
        except urllib.error.HTTPError as exc:
            code = str(exc.code)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            failures.append({"time": datetime.now(timezone.utc).isoformat(), "error": str(exc)})
        counts[code] += 1
        latencies_ms.append(round((time.monotonic() - lap) * 1000, 2))
        if code != "200" and code != "connection-error":
            failures.append({"time": datetime.now(timezone.utc).isoformat(), "http_status": code})
        time.sleep(max(0, interval - (time.monotonic() - lap)))
    ordered = sorted(latencies_ms)
    def percentile(p):
        return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))] if ordered else None
    print(json.dumps({
        "url": url, "duration_s": seconds, "interval_s": interval,
        "requests": sum(counts.values()), "statuses": dict(counts),
        "p50_ms": percentile(0.50), "p95_ms": percentile(0.95), "p99_ms": percentile(0.99),
        "failures": failures,
    }, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8080/events")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--interval", type=float, default=0.15)
    parser.add_argument("--timeout", type=float, default=2)
    args = parser.parse_args()
    probe(args.url, args.seconds, args.interval, args.timeout)
