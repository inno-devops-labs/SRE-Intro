"""Run inside a gateway-image probe pod; print machine-readable HTTP evidence."""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
import time

import httpx


def p99(values):
    return round(sorted(values)[max(0, math.ceil(len(values) * 0.99) - 1)] * 1000, 3) if values else None


async def run(args):
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=100)) as c:
        async def metrics(url):
            r = await c.get(url + "/metrics")
            r.raise_for_status()
            return [line for line in r.text.splitlines() if line.startswith((
                "gateway_retry_total{", "gateway_circuit_breaker_transitions_total{",
                "gateway_rate_limit_rejections_total{", "gateway_bulkhead_rejections_total{",
                "gateway_bulkhead_in_flight{", "notifications_notify_total{"))]
        result = {"mode": args.mode, "url": args.url,
                  "started_at": datetime.now(timezone.utc).isoformat()}
        result["metrics_before"] = await metrics(args.url)
        reservations = []
        async def reserve():
            response = await c.post(f"{args.url}/events/{args.event_id}/reserve", json={"quantity": 1})
            response.raise_for_status()
            rid = response.json()["reservation_id"]
            reservations.append(rid)
            return rid
        async def pay(rid):
            start = time.perf_counter()
            r = await c.post(f"{args.url}/reserve/{rid}/pay")
            return {"status": r.status_code, "seconds": time.perf_counter() - start,
                    "detail": r.json().get("detail")}
        if args.mode in ("checkout", "recovery"):
            rows = []
            for _ in range(args.count):
                rows.append(await pay(await reserve()))
                await asyncio.sleep(0.13)
            result.update(statuses=dict(Counter(str(r["status"]) for r in rows)),
                          pay_p99_ms=p99([r["seconds"] for r in rows]), rows=rows)
            await asyncio.sleep(1)  # drain 300ms background notifications
        elif args.mode in ("burst", "sustained"):
            rows = []
            for _ in range(args.count):
                r = await c.get(args.url + "/events")
                rows.append({"status": r.status_code, "retry_after": r.headers.get("retry-after")})
                if args.mode == "sustained":
                    await asyncio.sleep(0.2)
            result.update(statuses=dict(Counter(str(r["status"]) for r in rows)), rows=rows)
        elif args.mode in ("bulkhead", "events-baseline"):
            # Pre-create reservations at a safe rate: rate-limiter rejections
            # must not be confused with bulkhead admission rejections.
            ids = []
            if args.mode == "bulkhead":
                for _ in range(args.count):
                    ids.append(await reserve())
                    await asyncio.sleep(0.01)
            samples, event_statuses, occupancy = [], [], []
            done = asyncio.Event()
            async def sample():
                while not done.is_set():
                    start = time.perf_counter()
                    r = await c.get(args.url + "/events")
                    samples.append(time.perf_counter() - start)
                    event_statuses.append(r.status_code)
                    lines = await metrics(args.url)
                    for line in lines:
                        if line.startswith('gateway_bulkhead_in_flight{target="payments"}'):
                            occupancy.append(float(line.rsplit(' ', 1)[1]))
                    await asyncio.sleep(0.1)
            task = asyncio.create_task(sample())
            rows = []
            if ids:
                # Three waves keep the cap observable over several 5s scrapes.
                for _ in range(args.waves):
                    rows.extend(await asyncio.gather(*(pay(rid) for rid in ids)))
                    # Each reservation can only be confirmed once.
                    if _ < args.waves - 1:
                        ids = [await reserve() for _ in range(args.count)]
            else:
                await asyncio.sleep(3)
            done.set()
            await task
            result.update(statuses=dict(Counter(str(r["status"]) for r in rows)),
                          pay_p99_ms=p99([r["seconds"] for r in rows]), rows=rows,
                          events_p99_ms=p99(samples), events_samples=len(samples),
                          events_ok=sum(s == 200 for s in event_statuses),
                          events_slow=sum(s > 0.5 for s in samples),
                          events_statuses=dict(Counter(map(str, event_statuses))),
                          max_in_flight=max(occupancy, default=0),
                          rejection_p99_ms=p99([r["seconds"] for r in rows if r["status"] == 503]))
        result["metrics_after"] = await metrics(args.url)
        result["notification_metrics"] = await metrics("http://notifications:8083")
        result["reservation_ids"] = reservations
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--event-id", required=True, type=int)
    p.add_argument("--mode", required=True, choices=["checkout", "recovery", "burst", "sustained", "bulkhead", "events-baseline"])
    p.add_argument("--count", type=int, default=30)
    p.add_argument("--waves", type=int, default=1)
    asyncio.run(run(p.parse_args()))
