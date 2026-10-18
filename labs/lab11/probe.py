"""In-cluster evidence probe: kubectl exec -i POD -- python - MODE [URL...] < this-file."""
import asyncio
from collections import Counter
import json
import math
import os
import sys
import time
import httpx


def summary(values):
    values = sorted(values)
    return {'p99_ms': round(values[max(0, math.ceil(.99 * len(values)) - 1)] * 1000, 2),
            'max_ms': round(max(values) * 1000, 2)} if values else {}


async def main():
    mode = sys.argv[1]
    event_id = int(os.getenv('LAB11_EVENT_ID', '3'))
    urls = sys.argv[2:] or ['http://gateway:8080']
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=100, max_keepalive_connections=0)) as c:
        async def checkout(url):
            r = await c.post(url + f'/events/{event_id}/reserve', json={'quantity': 1})
            if r.status_code != 200:
                return 'reserve_' + str(r.status_code), 0
            start = time.monotonic()
            r = await c.post(url + '/reserve/' + r.json()['reservation_id'] + '/pay')
            return str(r.status_code), time.monotonic() - start
        if mode in ('checkout', 'cb', 'recovery'):
            async def series(url, count):
                rows = []
                for _ in range(count):
                    rows.append(await checkout(url))
                    await asyncio.sleep(.15)
                return rows
            count = 8 if mode == 'cb' else 3 if mode == 'recovery' else 30
            rows = sum(await asyncio.gather(*(series(url, count) for url in urls)), [])
            print(json.dumps({'mode': mode, 'outcomes': dict(Counter(r[0] for r in rows)), 'pay_latency': summary([r[1] for r in rows])}))
        elif mode == 'rate':
            rows = await asyncio.gather(*(c.get(urls[0] + '/events') for _ in range(100)))
            print(json.dumps({'mode': mode, 'outcomes': dict(Counter(r.status_code for r in rows)), 'retry_after': sorted(set(r.headers.get('Retry-After', '') for r in rows if r.status_code == 429))}))
            await asyncio.sleep(1.1)
            rows = []
            for _ in range(5):
                rows.append((await c.get(urls[0] + '/events')).status_code)
                await asyncio.sleep(.2)
            print(json.dumps({'below_limit': dict(Counter(rows))}))
        elif mode == 'bulkhead':
            url = urls[0]
            ids = []
            for _ in range(30):
                r = await c.post(url + f'/events/{event_id}/reserve', json={'quantity': 1})
                r.raise_for_status()
                ids.append(r.json()['reservation_id'])
            async def pay(rid):
                start = time.monotonic()
                r = await c.post(url + '/reserve/' + rid + '/pay')
                return str(r.status_code), time.monotonic() - start
            tasks = [asyncio.create_task(pay(rid)) for rid in ids]
            times, codes, peaks = [], [], []
            for _ in range(30):
                start = time.monotonic()
                r = await c.get(url + '/events')
                times.append(time.monotonic() - start)
                codes.append(r.status_code)
                metrics = (await c.get(url + '/metrics')).text
                for line in metrics.splitlines():
                    if line.startswith('gateway_bulkhead_in_flight{target="payments"}'):
                        peaks.append(float(line.split()[-1]))
                await asyncio.sleep(.1)
            rows = await asyncio.gather(*tasks)
            print(json.dumps({'mode': mode, 'pay_outcomes': dict(Counter(r[0] for r in rows)), 'rejected_latency': summary([r[1] for r in rows if r[0] == '503']), 'events_outcomes': dict(Counter(codes)), 'events_latency': summary(times), 'EVENTS': {'ok':sum(t <= .5 for t in times), 'slow':sum(t > .5 for t in times)}, 'peak_occupancy': max(peaks, default=0)}))
        elif mode == 'metrics':
            for url in urls:
                response = await c.get(url + '/metrics')
                print(url)
                print('\n'.join(line for line in response.text.splitlines() if line.startswith(('gateway_retry_total', 'gateway_circuit_breaker_transitions_total', 'gateway_rate_limit_rejections_total', 'gateway_bulkhead_', 'notifications_notify_total'))))
        elif mode == 'prometheus':
            queries = [
                'sum by(target,result)(gateway_retry_total)',
                'sum by(to)(gateway_circuit_breaker_transitions_total)',
                'sum by(path)(gateway_rate_limit_rejections_total)',
                'sum by(target)(gateway_bulkhead_rejections_total)',
                'max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m])',
                'histogram_quantile(0.99,sum by(le,path)(rate(gateway_request_duration_seconds_bucket{path="/reserve/{id}/pay"}[2m])))',
            ]
            for query in queries:
                r = await c.get('http://prometheus.monitoring:9090/api/v1/query', params={'query':query})
                print(json.dumps({'query':query, 'response':r.json()}))

asyncio.run(main())
