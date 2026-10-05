"""Record hypotheses before injection; restore dependencies in finally."""
import importlib.util
import json
import pathlib
import time
import yaml
spec=importlib.util.spec_from_file_location('e','scripts/lab-evidence.py'); e=importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
if e.k('config','current-context').strip() != 'k3d-quickticket-lab78': raise RuntimeError('Isolated cluster required')
r=pathlib.Path('submissions/evidence/lab8'); r.mkdir(parents=True,exist_ok=True)
def snapshot(name):
    e.save(r/(name+'.json'), {'metrics':e.metrics(),'probes':probes(),'pods':json.loads(e.k('get','pods','-o','json'))})
def hypothesis(name,text):
    e.save(r/(name+'-hypothesis.json'),text)
def probes():
    # Go through ClusterIP, not a port-forward pinned to one pod.
    code='''import json,time,urllib.request,urllib.error
out=[]
requests=[("/events",None),("/events/1/reserve",b'{"quantity":1}'),("/health",None)]
for path,data in requests:
 t=time.monotonic()
 try:
  resp=urllib.request.urlopen(urllib.request.Request("http://gateway:8080"+path,data=data,headers={"Content-Type":"application/json"}),timeout=12)
 except urllib.error.HTTPError as ex: resp=ex
 except Exception as ex:
  out.append({"path":path,"error":str(ex),"seconds":time.monotonic()-t});continue
 body=resp.read().decode()
 out.append({"path":path,"status":resp.status,"seconds":time.monotonic()-t,"body":body[:300]})
 if path.endswith("/reserve") and resp.status==200:
  requests.append(("/reserve/"+json.loads(body)["reservation_id"]+"/pay",b''))
print(json.dumps(out))'''
    return json.loads(e.k('exec','deploy/payments','--','python','-c',code))
def payment(ms):
    e.k('set','env','deploy/payments','PAYMENT_LATENCY_MS='+str(ms)); e.k('rollout','status','deploy/payments','--timeout=120s')
def restore():
    e.k('scale','deploy/redis','--replicas=1'); e.k('rollout','status','deploy/redis','--timeout=120s'); payment(0)
    e.wait(lambda:all(p.get('status')==200 for p in probes() if p['path'] in ['/health','/events']),120)
    time.sleep(75)
try:
    baseline_probe={'httpGet':{'path':'/health','port':8081},'periodSeconds':5,'timeoutSeconds':5,'failureThreshold':2}
    e.k('patch','deploy/events','--type=json','-p',json.dumps([{'op':'replace','path':'/spec/template/spec/containers/0/readinessProbe','value':baseline_probe}]))
    e.k('rollout','status','deploy/events','--timeout=120s')
    e.k('exec','deploy/postgres','--','psql','-v','ON_ERROR_STOP=1','-U','quickticket','-d','quickticket','-c',pathlib.Path('scripts/lab8-seed.sql').read_text())
    e.k('apply','-f','k8s/mixedload.yaml'); e.k('rollout','status','deploy/mixedload','--timeout=120s'); time.sleep(90); snapshot('baseline')
    hypothesis('pod-kill','Deleting one of five gateway pods under mixed load should leave four endpoints serving and trigger a replacement within seconds; in-flight connections may fail.')
    pods=json.loads(e.k('get','pods','-l','app=gateway','-o','json'))['items']; victim=next(p['metadata']['name'] for p in pods if not p['metadata'].get('deletionTimestamp'))
    started=time.monotonic(); e.k('delete','pod',victim,'--wait=false')
    def replacement():
        ps=json.loads(e.k('get','pods','-l','app=gateway','-o','json'))['items']
        return [p for p in ps if p['metadata']['name'] not in [x['metadata']['name'] for x in pods]]
    e.wait(lambda:bool(replacement())); created=time.monotonic()-started
    e.wait(lambda:sum(p['status'].get('phase')=='Running' and all(c.get('ready') for c in p['status'].get('containerStatuses',[])) and not p['metadata'].get('deletionTimestamp') for p in json.loads(e.k('get','pods','-l','app=gateway','-o','json'))['items'])==5)
    e.save(r/'pod-kill-timing.json',{'victim':victim,'replacement_observed_seconds':created,'five_ready_seconds':time.monotonic()-started}); time.sleep(75); snapshot('pod-kill'); restore()
    hypothesis('latency','Payments latency of 2000ms remains below the gateway 5000ms timeout: pay p99 rises, reads stay fast, and slow successful payments produce no 5xx.')
    payment(2000); time.sleep(90); snapshot('latency-2000')
    payment(6000); time.sleep(90); snapshot('latency-6000'); restore()
    hypothesis('redis','Redis outage should break reservations and health while DB-only event lists remain functional. Dependency-based events readiness may unexpectedly remove the entire events Service.')
    e.k('scale','deploy/redis','--replicas=0'); time.sleep(90); snapshot('redis-before-fix'); restore()
    hypothesis('combined','Redis down plus payments latency 2000ms should degrade reservations and payments independently; events readiness may instead cascade the outage to reads, making Redis the weakest link.')
    payment(2000); e.k('scale','deploy/redis','--replicas=0')
    for i in range(1,4): time.sleep(60); snapshot('combined-'+str(i))
    restore()
    hypothesis('redis-after-fix','Readiness that verifies PostgreSQL without gating on Redis should keep lists available during the same Redis outage; reservations and /health should still fail honestly.')
    # Apply only the committed readiness improvement; preserve baseline workload.
    e.k('apply','-f','k8s/events.yaml'); e.k('rollout','status','deploy/events','--timeout=120s'); time.sleep(75); snapshot('fixed-baseline')
    e.k('scale','deploy/redis','--replicas=0'); time.sleep(90); snapshot('redis-after-fix'); restore(); snapshot('final')
finally:
    e.k('apply','-f','k8s/events.yaml')
    e.k('scale','deploy/redis','--replicas=1'); payment(0)
    e.k('delete','-f','k8s/mixedload.yaml','--ignore-not-found')
    (r/'final-rollout.txt').write_text(e.k('argo','rollouts','get','rollout','gateway','--no-color'))
