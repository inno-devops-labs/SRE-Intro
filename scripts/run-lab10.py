"""Sequential 60s in-cluster runs; flush only the isolated course Redis."""
import json, pathlib, subprocess, time, yaml, urllib.request, urllib.parse
E=pathlib.Path('submissions/evidence/lab10');E.mkdir(parents=True,exist_ok=True)
def run(args, check=True):
    p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    with (E/'session.txt').open('a') as f:f.write('$ '+' '.join(args)+'\n'+p.stdout+'\n')
    if check and p.returncode:raise RuntimeError(p.stdout)
    return p.stdout
assert run(['kubectl','config','current-context']).strip()=='k3d-quickticket-lab78'
cm=run(['kubectl','create','configmap','locustfile','--from-file=locustfile.py','--dry-run=client','-o','yaml'])
subprocess.run(['kubectl','apply','-f','-'],input=cm,text=True,check=True)
results=[]
for users,ramp in [(10,2),(50,5),(100,10),(200,20),(400,40),(800,80),(1600,160)]:
    run(['kubectl','exec','deploy/redis','--','redis-cli','FLUSHDB'])
    job=yaml.safe_load(pathlib.Path('labs/lab10/locust-runner.yaml').read_text());name=f'lab10-load-{users}';job['metadata']['name']=name
    c=job['spec']['template']['spec']['containers'][0];a=c['args'];a[a.index('-u')+1]=str(users);a[a.index('-r')+1]=str(ramp);a+=['--exit-code-on-error=0']
    p=E/(name+'.yaml');p.write_text(yaml.safe_dump(job,sort_keys=False));run(['kubectl','apply','-f',str(p)])
    run(['kubectl','wait','--for=condition=Ready','pod','-l','job-name='+name,'--timeout=120s'])
    time.sleep(45)
    cpu=run(['kubectl','top','pods'],False);(E/(name+'-cpu.txt')).write_text(cpu)
    q='sum by(pod)(rate(gateway_requests_total[30s]))'
    d=json.load(urllib.request.urlopen('http://localhost:19090/api/v1/query?'+urllib.parse.urlencode({'query':q})));(E/(name+'-pod-rps.json')).write_text(json.dumps(d,indent=2))
    run(['kubectl','wait','--for=condition=Complete','job/'+name,'--timeout=90s'])
    logs=run(['kubectl','logs','job/'+name]);(E/(name+'.log')).write_text(logs)
    r=json.loads(next(l.split('LAB10_RESULT=',1)[1] for l in logs.splitlines() if 'LAB10_RESULT=' in l));r['ramp']=ramp;results.append(r);(E/'results.json').write_text(json.dumps(results,indent=2));print(r,flush=True)
    # Required 10/50/100 always run, even if an earlier level breaks.
    if users>=100 and (r['five_xx']/r['requests']>.005 or r['p99_ms']>500):break
run(['kubectl','get','pods,svc,rollout','-o','wide']);run(['kubectl','exec','deploy/redis','--','redis-cli','FLUSHDB'])
