"""Timestamped final HTTP, pod-health and Prometheus checks (no credentials)."""
import asyncio
from datetime import datetime, timezone
import json
import time

import httpx
from probe import API, CA, TOKEN, PROM


async def main():
    output={'timestamp':datetime.now(timezone.utc).isoformat(),'http':[]}
    async with httpx.AsyncClient(timeout=20) as c:
        async def check(method,url,**kwargs):
            start=time.perf_counter()
            r=await c.request(method,url,**kwargs)
            row={'timestamp':datetime.now(timezone.utc).isoformat(),'method':method,'url':url,
                 'status':r.status_code,'latency_ms':round((time.perf_counter()-start)*1000,3),'body':r.json()}
            output['http'].append(row)
            r.raise_for_status()
            return row['body']
        for service,port in [('gateway',8080),('events',8081),('payments',8082),('notifications',8083)]:
            await check('GET',f'http://{service}:{port}/health')
        async with httpx.AsyncClient(verify=CA,headers={'Authorization':'Bearer '+TOKEN}) as k:
            pods=(await k.get(API+'/api/v1/namespaces/default/pods')).json()['items']
        for pod in pods:
            app=pod['metadata'].get('labels',{}).get('app')
            if app in ('events','payments','notifications') and not pod['metadata'].get('deletionTimestamp'):
                port={'events':8081,'payments':8082,'notifications':8083}[app]
                await check('GET',f"http://{pod['status']['podIP']}:{port}/health")
        await check('GET','http://gateway:8080/events/3')
        reserve=await check('POST','http://gateway:8080/events/3/reserve',json={'quantity':1})
        order=await check('POST','http://gateway:8080/reserve/'+reserve['reservation_id']+'/pay')
        if order.get('status')!='confirmed':
            raise RuntimeError('Checkout was not confirmed')
        await asyncio.sleep(2)
        output['prometheus_up']=(await c.get(PROM+'/api/v1/query',params={'query':'up'})).json()
        output['targets']=[{'labels':t['labels'],'health':t['health'],'lastError':t['lastError'],'lastScrape':t['lastScrape']}
                           for t in (await c.get(PROM+'/api/v1/targets')).json()['data']['activeTargets']]
    print(json.dumps(output))


asyncio.run(main())
