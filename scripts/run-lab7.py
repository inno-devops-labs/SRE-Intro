"""Execute Lab 7 in k3d-quickticket-lab78; records real evidence."""
import importlib.util
import pathlib
import time
import yaml
spec=importlib.util.spec_from_file_location('e','scripts/lab-evidence.py'); e=importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
if e.k('config','current-context').strip() != 'k3d-quickticket-lab78':
    raise RuntimeError('Use isolated quickticket-lab78 cluster')
root=pathlib.Path('submissions/evidence/lab7'); root.mkdir(parents=True,exist_ok=True)
p=pathlib.Path('k8s/gateway.yaml'); docs=list(yaml.safe_load_all(p.read_text())); d=docs[0]
def apply(version, broken=False):
    env=d['spec']['template']['spec']['containers'][0]['env']
    for v in env:
        if v['name']=='APP_VERSION': v['value']=version
        if v['name']=='EVENTS_URL': v['value']='http://broken-on-purpose:8081' if broken else 'http://events:8081'
    p.write_text(yaml.safe_dump_all(docs,sort_keys=False)); e.k('apply','-f',str(p))
    e.wait(lambda:str(e.obj('rollout','gateway')['status'].get('observedGeneration'))==str(e.obj('rollout','gateway')['metadata']['generation']))
def snap(name):
    text=e.stamp()+'\n'+e.k('argo','rollouts','get','rollout','gateway','--no-color')
    (root/(name+'.txt')).write_text(text)
    e.save(root/(name+'.json'),{'rollout':e.obj('rollout','gateway'),'metrics':e.metrics()})
d['spec']['strategy']['canary']['steps']=[{'setWeight':20},{'pause':{}},{'setWeight':60},{'pause':{'duration':'30s'}},{'setWeight':100}]
apply('v1'); e.k('argo','rollouts','promote','gateway','--full'); e.healthy(); (root/'version.txt').write_text(e.k('argo','rollouts','version'))
apply('v2'); e.wait(lambda:e.obj('rollout','gateway')['status'].get('phase')=='Paused'); time.sleep(35); snap('manual-20')
# Same-sized log window avoids comparing lifetime counts from old/new pods.
pods=e.obj('pods','') if False else __import__('json').loads(e.k('get','pods','-l','app=gateway','-o','json'))
counts={}
for pod in pods['items']:
    name=pod['metadata']['name']; logs=e.k('logs',name,'--since=30s')
    counts[name]={'hash':pod['metadata']['labels']['rollouts-pod-template-hash'],'events_requests':logs.count('GET /events ')}
e.save(root/'traffic-split.json',counts)
e.k('argo','rollouts','promote','gateway'); e.wait(lambda:e.obj('rollout','gateway')['status'].get('currentStepIndex')==3); snap('manual-60'); e.healthy(); snap('manual-100')
apply('v3-bad',True); e.wait(lambda:e.obj('rollout','gateway')['status'].get('phase')=='Paused'); time.sleep(10); snap('bad-before-abort')
start=time.monotonic(); e.k('argo','rollouts','abort','gateway')
e.wait(lambda:e.obj('rollout','gateway')['status'].get('updatedReplicas',0)==0)
e.save(root/'abort-timing.json',{'seconds_until_canary_scaled_to_zero':time.monotonic()-start}); snap('manual-aborted')
apply('v2'); e.healthy()
d['spec']['strategy']['canary']['steps']=sum(([{'setWeight':w},{'pause':{'duration':'60s' if w<80 else '30s'}}] for w in [20,40,60,80]),[])+[{'setWeight':100}]
apply('v4-multistep')
watch=__import__('subprocess').Popen(['kubectl','argo','rollouts','get','rollout','gateway','--watch','--no-color'],stdout=open(root/'multistep-watch.txt','w'),stderr=__import__('subprocess').STDOUT)
seen=set()
while True:
    s=e.obj('rollout','gateway')['status']; step=s.get('currentStepIndex',0)
    if s.get('phase')=='Paused' and step not in seen:
        time.sleep(15); snap('multistep-'+str(step)); seen.add(step)
    if s.get('phase')=='Healthy': break
    time.sleep(3)
watch.terminate(); watch.wait(); snap('multistep-100')
analysis={'analysis':{'templates':[{'templateName':'gateway-error-rate'}],'args':[{'name':'canary-hash','valueFrom':{'podTemplateHashValue':'Latest'}}]}}
d['spec']['strategy']['canary']['steps']=[{'setWeight':20},{'pause':{'duration':'20s'}},analysis,{'setWeight':60},{'pause':{'duration':'20s'}},{'setWeight':100}]
apply('v5-auto-good'); e.healthy(); snap('auto-good'); (root/'analysis-good.yaml').write_text(e.k('get','analysisrun','-o','yaml'))
apply('v6-auto-bad',True); e.wait(lambda:e.obj('rollout','gateway')['status'].get('phase')=='Degraded',360); snap('auto-bad'); (root/'analysis-both.yaml').write_text(e.k('get','analysisrun','-o','yaml')); (root/'analysis-template.txt').write_text(e.k('get','analysistemplate','gateway-error-rate'))
apply('v5-auto-good'); e.healthy(); e.k('delete','-f','labs/lab7/loadgen.yaml'); snap('final')
