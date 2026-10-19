import asyncio, httpx, time, json
from collections import Counter
async def main():
 async with httpx.AsyncClient(timeout=15,limits=httpx.Limits(max_connections=100,max_keepalive_connections=0)) as c:
  base='http://bulkhead-probe:8080'
  ids=[]
  for i in range(30):
   r=await c.post(base+'/events/3/reserve',json={'quantity':1});r.raise_for_status();ids.append(r.json()['reservation_id'])
  times=[]
  async def pay(rid):
   r=await c.post(base+f'/reserve/{rid}/pay');return r.status_code
  async def events():
   for _ in range(30):
    start=time.perf_counter();r=await c.get(base+'/events');r.raise_for_status();times.append(time.perf_counter()-start);await asyncio.sleep(.1)
  results=await asyncio.gather(asyncio.gather(*(pay(rid) for rid in ids)), events())
  print(json.dumps({'pay_statuses':dict(Counter(results[0])),'events_ok':len(times),'events_slow_over_500ms':sum(t>.5 for t in times),'events_p99_ms':round(max(times)*1000,2)}))
  print('\n'.join(l for l in (await c.get(base+'/metrics')).text.splitlines() if l.startswith('gateway_bulkhead_')))
asyncio.run(main())
