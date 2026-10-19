"""Create reservations sequentially, then submit concurrent QuickTicket payments.

Requirements: pip install httpx
Example: python scripts/lab11_burst.py --requests 100 --parallel 100 --output bulkhead-run.json
"""

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from pathlib import Path

import httpx


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    samples = sorted(values)
    index = max(0, min(len(samples) - 1, math.ceil(len(samples) * fraction) - 1))
    return round(samples[index] * 1000, 2)


async def run(args: argparse.Namespace) -> dict:
    base = args.url.rstrip("/")
    limits = httpx.Limits(max_connections=max(100, args.parallel + 20))
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        reservations = []
        reservation_statuses = Counter()
        for _ in range(args.requests):
            try:
                response = await client.post(
                    f"{base}/events/{args.event}/reserve", json={"quantity": 1}
                )
                reservation_statuses[str(response.status_code)] += 1
                if response.is_success:
                    reservation_id = response.json().get("reservation_id")
                    if reservation_id:
                        reservations.append(reservation_id)
                    else:
                        reservation_statuses["missing_reservation_id"] += 1
            except httpx.HTTPError:
                reservation_statuses["transport_error"] += 1
            await asyncio.sleep(args.reserve_delay)

        gate = asyncio.Semaphore(args.parallel)

        async def pay(reservation_id: str) -> tuple[str, float]:
            async with gate:
                started = time.perf_counter()
                try:
                    response = await client.post(f"{base}/reserve/{reservation_id}/pay")
                    code = str(response.status_code)
                except httpx.HTTPError as error:
                    code = f"transport:{type(error).__name__}"
                return code, time.perf_counter() - started

        payments = await asyncio.gather(*(pay(rid) for rid in reservations))
    statuses = Counter(code for code, _ in payments)
    latencies = [elapsed for _, elapsed in payments]
    return {
        "endpoint": base,
        "event_id": args.event,
        "requested_reservations": args.requests,
        "successful_reservations": len(reservations),
        "reservation_statuses": dict(sorted(reservation_statuses.items())),
        "parallel_payments": args.parallel,
        "payment_statuses": dict(sorted(statuses.items())),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies) * 1000, 2) if latencies else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="QuickTicket checkout concurrency probe")
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--event", type=int, default=3)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--parallel", type=int, default=100)
    parser.add_argument("--reserve-delay", type=float, default=0.15)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.parallel < 1 or args.reserve_delay < 0 or args.timeout <= 0:
        parser.error("requests/parallel must be positive; delay must be nonnegative; timeout positive")
    results = asyncio.run(run(args))
    content = json.dumps(results, indent=2, ensure_ascii=False)
    print(content)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
