"""Independent end-to-end observer; records errors the gateway cannot count."""
import asyncio
from collections import Counter
import json
import os
import time
import httpx

counts = Counter()
started = time.time()


def save():
    snapshot = {'started': started, 'time': time.time(), 'counts': dict(counts)}
    with open('/tmp/observations.new', 'w') as f:
        json.dump(snapshot, f)
    os.replace('/tmp/observations.new', '/tmp/observations.json')


async def main():
    event = os.environ['LAB12_EVENT_ID']
    async with httpx.AsyncClient(base_url='http://gateway:8080', timeout=20,
                                limits=httpx.Limits(max_keepalive_connections=0)) as c:
        async def request(method, path, label, **kwargs):
            try:
                r = await c.request(method, path, **kwargs)
                counts[f'{label}:{r.status_code}'] += 1
                return r
            except httpx.HTTPError as exc:
                counts[f'{label}:transport_error:{type(exc).__name__}'] += 1
            finally:
                save()
        while True:
            await request('GET', '/events', 'list')
            r = await request('POST', f'/events/{event}/reserve', 'reserve', json={'quantity': 1})
            if r is not None and r.status_code == 200:
                rid = r.json()['reservation_id']
                r = await request('POST', f'/reserve/{rid}/pay', 'pay')
                if r is not None and r.status_code == 200:
                    counts['confirmed'] += r.json().get('status') == 'confirmed'
                    save()
            await asyncio.sleep(1)

save()
asyncio.run(main())
