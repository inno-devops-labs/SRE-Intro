"""Run inside a pod: python lab11_probe.py [checkout|burst] [count]."""
import asyncio
from collections import Counter
import json
import sys
import time
import httpx

async def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'checkout'
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    counts, latencies = Counter(), []
    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_keepalive_connections=0)) as client:
        async def run(i):
            if mode == 'burst':
                response = await client.get('http://gateway:8080/events')
            else:
                res = await client.post('http://gateway:8080/events/3/reserve', json={'quantity': 1})
                if res.status_code != 200:
                    counts[f'reserve_{res.status_code}'] += 1
                    return
                rid = res.json()['reservation_id']
                start = time.perf_counter()
                response = await client.post(f'http://gateway:8080/reserve/{rid}/pay')
                latencies.append((time.perf_counter() - start)*1000)
            counts[str(response.status_code)] += 1
            if response.status_code == 429:
                assert response.headers['Retry-After'] == '1'
        if mode == 'burst':
            for i in range(n):
                await run(i)
        else:
            for i in range(n):
                await run(i)
                await asyncio.sleep(.12)
        print(json.dumps({'mode': mode, 'count': n, 'statuses':dict(counts), 'pay_p99_ms': round(sorted(latencies)[min(len(latencies)-1,int(len(latencies)*.99))],2) if latencies else None, 'retry_after': '1' if counts['429'] else None}))
        for svc,port in [('notifications',8083),('payments',8082)]:
            text=(await client.get(f'http://{svc}:{port}/metrics')).text
            for line in text.splitlines():
                if not line.startswith('#') and ('notify_total' in line or 'charges_total' in line): print(line)

asyncio.run(main())
