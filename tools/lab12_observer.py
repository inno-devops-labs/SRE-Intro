"""Exercise read + complete checkout paths during maintenance/migrations."""
import asyncio,time,json,sys
from collections import Counter
import httpx
async def main():
 end=time.monotonic()+float(sys.argv[1] if len(sys.argv)>1 else 240)
 counts=Counter();errors=[]
 async with httpx.AsyncClient(timeout=15,limits=httpx.Limits(max_keepalive_connections=0)) as c:
  i=0
  while time.monotonic()<end:
   try:
    r=await c.get('http://gateway:8080/events');counts[f'events:{r.status_code}']+=1
    if r.status_code>=500:errors.append(r.text)
    if i%5==0:
     r=await c.post('http://gateway:8080/events/3/reserve',json={'quantity':1});counts[f'reserve:{r.status_code}']+=1
     if r.status_code==200:
      rid=r.json()['reservation_id'];r=await c.post(f'http://gateway:8080/reserve/{rid}/pay');counts[f'pay:{r.status_code}']+=1
      if r.status_code>=500:errors.append(r.text)
   except Exception as exc:
    counts['transport_error']+=1;errors.append(str(exc))
   i+=1
   await asyncio.sleep(.1)
 print(json.dumps({'counts':dict(counts),'errors':errors},indent=2))
asyncio.run(main())
