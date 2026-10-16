"""Run inside a separate client pod, never sharing the measured server's CPU."""

import asyncio
import collections
import datetime
import json
import sys
import time

import httpx


async def main():
    mode = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    target = sys.argv[3] if len(sys.argv) > 3 else "http://gateway:8080"
    rows = []
    async with httpx.AsyncClient(timeout=25, limits=httpx.Limits(max_connections=100, max_keepalive_connections=0)) as client:
        async def request(method, path, base=target, **kwargs):
            started = time.monotonic()
            response = await client.request(method, base + path, **kwargs)
            return {"path": path, "status": response.status_code,
                    "ms": round((time.monotonic() - started) * 1000, 3),
                    "body": response.text, "retry_after": response.headers.get("retry-after")}

        if mode == "burst":
            for _ in range(count):
                reserve = await request("POST", "/events/6/reserve", json={"quantity": 1})
                if reserve["status"] == 200:
                    rid = json.loads(reserve["body"])["reservation_id"]
                    rows.append(await request("POST", f"/reserve/{rid}/pay"))
                else:
                    rows.append(reserve)
                await asyncio.sleep(0.1)
        elif mode in ("rate", "steady"):
            if mode == "rate":
                rows = await asyncio.gather(*(request("GET", "/events") for _ in range(count)))
            else:
                for _ in range(count):
                    rows.append(await request("GET", "/events"))
                    await asyncio.sleep(0.2)
        elif mode == "bulkhead":
            reservations = []
            for _ in range(count):
                response = await request("POST", "/events/6/reserve", base="http://events:8081", json={"quantity": 1})
                assert response["status"] == 200, response
                reservations.append(json.loads(response["body"])["reservation_id"])
            occupancy = []
            done = asyncio.Event()
            query = 'timestamp(up{instance="' + target.removeprefix("http://") + '"})'
            snapshot = (await client.get("http://prometheus.monitoring.svc.cluster.local:9090/api/v1/query", params={"query": query})).json()
            samples = snapshot["data"]["result"]
            alignment = 0
            if samples:
                alignment = max(0, float(samples[0]["value"][1]) + 5 - time.time() - 1)
                await asyncio.sleep(alignment)

            async def sample():
                while not done.is_set():
                    text = (await client.get(target + "/metrics")).text
                    for line in text.splitlines():
                        if line.startswith('gateway_bulkhead_in_flight{target="payments"}'):
                            occupancy.append(float(line.split()[-1]))
                    await asyncio.sleep(0.05)

            async def payments():
                return await asyncio.gather(*(request("POST", f"/reserve/{rid}/pay") for rid in reservations))

            async def reads():
                result = []
                for _ in range(30):
                    result.append(await request("GET", "/events"))
                    await asyncio.sleep(0.15)
                return result

            sampler = asyncio.create_task(sample())
            pay_rows, read_rows = await asyncio.gather(payments(), reads())
            done.set()
            await sampler
            rows = pay_rows
            print("BULKHEAD " + json.dumps({"read_rows": read_rows, "occupancy": occupancy, "scrape_alignment_s": alignment}), flush=True)
        elif mode != "metrics":
            raise ValueError("unknown probe mode")

        await asyncio.sleep(2)
        metrics = {}
        for name, url in (("gateway", target), ("notifications", "http://notifications:8083")):
            text = (await client.get(url + "/metrics")).text
            prefixes = ("gateway_retry_", "gateway_circuit_", "gateway_rate_limit_", "gateway_bulkhead_", "notifications_")
            metrics[name] = "\n".join(line for line in text.splitlines() if line.startswith(prefixes))
        queries = ["sum by (target,result) (gateway_retry_total)",
                   "sum by (to) (gateway_circuit_breaker_transitions_total)",
                   "sum by (path) (gateway_rate_limit_rejections_total)",
                   'sum by (target) (gateway_bulkhead_rejections_total)',
                   'max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])',
                   'histogram_quantile(0.99,sum by(le,path)(rate(gateway_request_duration_seconds_bucket[2m])))']
        prometheus = {}
        for query in queries:
            response = await client.get("http://prometheus.monitoring.svc.cluster.local:9090/api/v1/query", params={"query": query})
            prometheus[query] = response.json()
        print(json.dumps({"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "mode": mode,
                          "target": target, "rows": rows,
                          "statuses": dict(collections.Counter(row["status"] for row in rows)),
                          "metrics": metrics, "prometheus": prometheus}), flush=True)


asyncio.run(main())
