"""In-cluster observer/window collector. Logs contain data, never credentials.

Start observe in lab12-client; window reads its persistent /tmp/lab12.jsonl.
Each window stores raw Prometheus results and client status breakdowns.
"""
import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import httpx

LOG = Path('/tmp/lab12.jsonl')
PROM = 'http://prometheus.monitoring.svc.cluster.local:9090'
TOKEN = Path('/var/run/secrets/kubernetes.io/serviceaccount/token').read_text().strip()
CA = '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
API = 'https://kubernetes.default.svc'


def utc():
    return datetime.now(timezone.utc).isoformat()


def write(kind, **data):
    row = dict(timestamp=utc(), ts=time.time(), kind=kind, **data)
    with LOG.open('a') as f:
        f.write(json.dumps(row) + '\n')
    return row


async def request(client, method, path, **kwargs):
    started = time.perf_counter()
    try:
        r = await client.request(method, 'http://gateway:8080' + path, **kwargs)
        try:
            body = r.json()
        except ValueError:
            body = r.text[:200]
        row = write('request', method=method, path=path, status=r.status_code,
                    latency_ms=round((time.perf_counter()-started)*1000, 3), body=body)
    except httpx.HTTPError as exc:
        row = write('request', method=method, path=path, status=0,
                    latency_ms=round((time.perf_counter()-started)*1000, 3), transport_error=str(exc))
    return row


async def traffic(duration):
    deadline = time.monotonic() + duration
    async with httpx.AsyncClient(timeout=20) as client:
        while time.monotonic() < deadline and not Path('/tmp/lab12-stop').exists():
            await request(client, 'GET', '/events')
            await request(client, 'GET', '/events/3')
            r = await request(client, 'POST', '/events/3/reserve', json={'quantity': 1})
            if r['status'] == 200 and isinstance(r['body'], dict) and r['body'].get('reservation_id'):
                rid = r['body']['reservation_id']
                await request(client, 'POST', f'/reserve/{rid}/pay')
            await asyncio.sleep(.7)


async def telemetry(duration):
    deadline = time.monotonic() + duration
    async with httpx.AsyncClient(verify=CA, headers={'Authorization': 'Bearer '+TOKEN}, timeout=10) as kube, httpx.AsyncClient(timeout=2) as metrics:
        while time.monotonic() < deadline and not Path('/tmp/lab12-stop').exists():
            try:
                pods = (await kube.get(API+'/api/v1/namespaces/default/pods')).json()['items']
                selected = [p for p in pods if p['metadata'].get('labels', {}).get('app') in ('gateway','events','payments','notifications')]
                states = []
                counters = {}
                for p in selected:
                    m, s = p['metadata'], p.get('status', {})
                    states.append(dict(name=m['name'], uid=m['uid'], app=m['labels']['app'],
                                       created=m['creationTimestamp'], deleting=m.get('deletionTimestamp'),
                                       ready=any(c['type']=='Ready' and c['status']=='True' for c in s.get('conditions', [])),
                                       restarts=sum(c['restartCount'] for c in s.get('containerStatuses', [])),
                                       image=p['spec']['containers'][0]['image'], phase=s.get('phase')))
                    if m['labels']['app'] == 'gateway' and s.get('podIP'):
                        try:
                            text = (await metrics.get('http://'+s['podIP']+':8080/metrics')).text
                            counters[m['name']] = [line for line in text.splitlines() if line.startswith('gateway_requests_total{')]
                        except httpx.HTTPError as exc:
                            counters[m['name']] = {'unavailable': str(exc)}
                endpoints = (await kube.get(API+'/apis/discovery.k8s.io/v1/namespaces/default/endpointslices')).json()['items']
                slices = [{ 'service': e['metadata'].get('labels', {}).get('kubernetes.io/service-name'),
                            'endpoints': e.get('endpoints', [])} for e in endpoints
                          if e['metadata'].get('labels', {}).get('kubernetes.io/service-name') in ('gateway','events')]
                write('telemetry', pods=states, gateway_counters=counters, endpoints=slices)
            except Exception as exc:
                write('telemetry_error', error=str(exc))
            await asyncio.sleep(3)


async def observe(args):
    write('observer_start', duration=args.duration)
    await asyncio.gather(traffic(args.duration), telemetry(args.duration))
    write('observer_stop')


def normalized(path):
    path = re.sub(r'/events/\d+', '/events/{id}', path)
    return re.sub(r'/reserve/[a-f0-9-]+', '/reserve/{id}', path)


async def window(args):
    end = time.time() if args.end is None else args.end
    start = args.start
    rows = [json.loads(line) for line in LOG.read_text().splitlines()]
    requests = [r for r in rows if r['kind']=='request' and start <= r['ts'] <= end]
    snapshots = [r for r in rows if r['kind']=='telemetry' and start <= r['ts'] <= end]
    seconds = max(10, int(end-start+.999))
    data = dict(label=args.label, start_epoch=start, end_epoch=end,
                start_utc=datetime.fromtimestamp(start, timezone.utc).isoformat(),
                end_utc=datetime.fromtimestamp(end, timezone.utc).isoformat(),
                client_requests=len(requests), statuses=dict(Counter(r['status'] for r in requests)),
                endpoint_statuses=dict(Counter(normalized(r['path'])+' '+str(r['status']) for r in requests)),
                checkout_confirmed=sum(r['status']==200 and r['path'].endswith('/pay') and r.get('body',{}).get('status')=='confirmed' for r in requests),
                transport_errors=sum(r['status']==0 for r in requests),
                client_5xx=sum(500 <= r['status'] < 600 for r in requests),
                client_429=sum(r['status']==429 for r in requests),
                client_409=sum(r['status']==409 for r in requests),
                telemetry_samples=len(snapshots), pod_snapshots=snapshots,
                examples=[requests[0], requests[-1]] if requests else [],
                client_errors=[r for r in requests if r['status']==0 or r['status']>=400],prometheus=[])
    traffic_q=f'sum(increase(gateway_requests_total[{seconds}s]))'
    queries=[traffic_q,
             f'(sum(increase(gateway_requests_total{{status=~"5.."}}[{seconds}s]))) or on() (0 * {traffic_q})',
             f'sum by(pod,path,status) (increase(gateway_requests_total[{seconds}s]))',
             f'sum by(pod) (resets(gateway_requests_total[{seconds}s]))',
             f'sum by(pod) (count_over_time(gateway_requests_total[{seconds}s]))']
    async with httpx.AsyncClient(timeout=20) as client:
        for query in queries:
            response = await client.get(PROM+'/api/v1/query', params={'query':query, 'time':end})
            data['prometheus'].append(dict(query=query, evaluated_at_utc=utc(), response=response.json()))
    Path('/tmp/'+args.label+'.json').write_text(json.dumps(data))
    # Detailed snapshots stay in the saved file, not a huge terminal transcript.
    print(json.dumps({k:v for k,v in data.items() if k not in ('pod_snapshots','prometheus')},indent=2))
    print(json.dumps({'prometheus':data['prometheus'][:2]},indent=2))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('mode', choices=['observe','window'])
    p.add_argument('--duration',type=int, default=7200)
    p.add_argument('--start',type=float)
    p.add_argument('--end',type=float)
    p.add_argument('--label',default='window')
    a=p.parse_args()
    asyncio.run(observe(a) if a.mode=='observe' else window(a))
