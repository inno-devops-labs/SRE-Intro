"""In-cluster Lab 11 probe. Print JSONL; keep stdout outside Git as raw evidence.

python /probe/probe.py checkout --count 30 --gap .2
python /probe/probe.py circuit --targets http://POD_IP:8080 ... --count 16
python /probe/probe.py rate --targets http://POD_IP:8080 --count 100
python /probe/probe.py bulkhead --targets http://POD_IP:8080 --count 30
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
import math
import time

import httpx


def emit(kind, **data):
    print(json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(), kind=kind, **data)), flush=True)


def quantiles(values):
    values = sorted(values)
    return {f"p{p}_ms": round(values[max(0, math.ceil(len(values) * p / 100) - 1)], 3)
            for p in (50, 95, 99)} if values else {}


records = []
active_pay = 0
peak_pay = 0


async def request(client, target, method, path, **kwargs):
    global active_pay, peak_pay
    start = time.perf_counter()
    pay = path.endswith("/pay")
    if pay:
        active_pay += 1
        peak_pay = max(peak_pay, active_pay)
    try:
        response = await client.request(method, target + path, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = response.text[:200]
        if path == "/metrics":
            body = {"prometheus_exposition": response.status_code == 200}
        record = dict(target=target, method=method, path=path, status=response.status_code,
                      duration_ms=round((time.perf_counter()-start)*1000, 3), body=body,
                      retry_after=response.headers.get("retry-after"))
    except httpx.HTTPError as exc:
        record = dict(target=target, method=method, path=path, status=0,
                      duration_ms=round((time.perf_counter()-start)*1000, 3), transport_error=str(exc))
    finally:
        if pay:
            active_pay -= 1
    records.append(record)
    emit("request", **record)
    return record


async def snapshot(client, targets, label):
    for target in dict.fromkeys(targets + ["http://notifications:8083", "http://payments:8082"]):
        try:
            response = await client.get(target + "/metrics")
            lines = [line for line in response.text.splitlines() if line.startswith((
                "gateway_retry_total{", "gateway_circuit_breaker_transitions_total{",
                "gateway_bulkhead_", "gateway_rate_limit_rejections_total{",
                "notifications_notify_total{", "payments_charges_total{"))]
            emit("metrics", label=label, target=target, lines=lines)
        except httpx.HTTPError as exc:
            emit("metrics_error", label=label, target=target, error=str(exc))


async def checkout(client, target, event):
    reservation = await request(client, target, "POST", f"/events/{event}/reserve", json={"quantity": 1})
    rid = reservation.get("body", {}).get("reservation_id") if isinstance(reservation.get("body"), dict) else None
    if reservation["status"] != 200 or not rid:
        emit("reserve_failed", target=target, status=reservation["status"])
        return
    await request(client, target, "POST", f"/reserve/{rid}/pay")


async def main(args):
    targets = args.targets or ["http://gateway:8080"]
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=120, max_keepalive_connections=50)) as client:
        emit("start", mode=args.mode, targets=targets, count=args.count)
        await snapshot(client, targets, "before")
        if args.mode == "snapshot":
            for target in targets + ["http://events:8081", "http://payments:8082", "http://notifications:8083"]:
                await request(client, target, "GET", "/health")
        elif args.mode in ("checkout", "circuit"):
            async def lane(target):
                for _ in range(args.count):
                    await checkout(client, target, args.event)
                    await asyncio.sleep(args.gap)
            if args.mode == "circuit":
                await asyncio.gather(*(lane(target) for target in targets))
            else:
                await lane(targets[0])
        elif args.mode == "rate":
            target = targets[0]
            started = time.perf_counter()
            await asyncio.gather(*(request(client, target, "GET", "/events") for _ in range(args.count)),
                                 request(client, target, "GET", "/health"),
                                 request(client, target, "GET", "/metrics"))
            emit("burst_elapsed", seconds=time.perf_counter()-started)
            await asyncio.sleep(1.2)
            for _ in range(20):
                await request(client, target, "GET", "/events")
                await asyncio.sleep(.2)
        elif args.mode == "bulkhead":
            target = targets[0]
            reservations = []
            for _ in range(args.count):
                r = await request(client, target, "POST", f"/events/{args.event}/reserve", json={"quantity": 1})
                if r["status"] == 200:
                    reservations.append(r["body"]["reservation_id"])
                await asyncio.sleep(.05)
            emit("prepared", reservations=len(reservations))
            done = asyncio.Event()
            async def pay_wave():
                await asyncio.gather(*(request(client, target, "POST", f"/reserve/{rid}/pay") for rid in reservations))
                done.set()
            async def read_lane():
                # Separate connection pool from the concurrent pay client.
                async with httpx.AsyncClient(timeout=10) as reads:
                    for _ in range(40):
                        await request(reads, target, "GET", "/events")
                        await asyncio.sleep(.1)
            async def occupancy():
                while not done.is_set():
                    await snapshot(client, targets, "during")
                    await asyncio.sleep(.15)
            await asyncio.gather(pay_wave(), read_lane(), occupancy())
        await asyncio.sleep(2)
        await snapshot(client, targets, "after")
        for group, rows in [("pay", [r for r in records if r["path"].endswith("/pay")]),
                            ("reserve", [r for r in records if r["path"].endswith("/reserve")]),
                            ("events", [r for r in records if r["path"] == "/events"])]:
            emit("summary", group=group, count=len(rows), statuses=dict(Counter(r["status"] for r in rows)),
                 **quantiles([r["duration_ms"] for r in rows]))
        emit("end", peak_client_pay_in_flight=peak_pay)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["checkout", "circuit", "rate", "bulkhead", "snapshot"])
    parser.add_argument("--targets", nargs="+")
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--event", type=int, default=3)
    parser.add_argument("--gap", type=float, default=.2)
    asyncio.run(main(parser.parse_args()))
