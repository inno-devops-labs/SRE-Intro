import datetime,json,os,subprocess,sys,time,urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'labs/lab12/evidence'
os.chdir(ROOT)
def run(args, check=True, input=None):
    print('$ '+ __import__('shlex').join(args),flush=True)
    p=subprocess.run(args,text=True,input=input,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    print(p.stdout,flush=True)
    if check and p.returncode: raise RuntimeError(f'exit={p.returncode}')
    return p.stdout
def query(q):
    url='http://localhost:9090/api/v1/query?'+urllib.parse.urlencode({'query':q})
    result=json.loads(run(['kubectl','exec','-n','monitoring','deployment/prometheus','--','wget','-qO-',url]))
    assert result['status']=='success'
    return result
mode=sys.argv[1]
with (OUT/f'{mode}.log').open('w') as logfile:
    class Tee:
        def write(self,s): sys.__stdout__.write(s); logfile.write(s)
        def flush(self):
            sys.__stdout__.flush()
            if not logfile.closed: logfile.flush()
    sys.stdout=Tee()
    print(datetime.datetime.now(datetime.timezone.utc).isoformat())
    if mode=='prepare':
        run(['kubectl','apply','-f','k8s/events.yaml','-f','k8s/payments.yaml','-f','k8s/notifications.yaml','-f','k8s/pdb.yaml'])
        for service in ['events','payments','notifications']:
            run(['kubectl','rollout','status',f'deployment/{service}','--timeout=180s'])
        run(['kubectl','apply','-f','labs/lab8/mixedload.yaml'])
        run(['kubectl','apply','-f','labs/lab12/mixedload.yaml'])
        run(['kubectl','rollout','status','deployment/mixedload','--timeout=120s'])
        run(['kubectl','get','deploy,rollout,pdb'])
        run(['kubectl','exec','deployment/postgres','--','psql','-U','quickticket','-d','quickticket','-c',r'\d events'])
    elif mode=='pdb-eviction':
        try:
            run(['kubectl','patch','pdb','events-pdb','--type=merge','-p','{"spec":{"minAvailable":2}}'])
            for _ in range(30):
                p=json.loads(subprocess.check_output(['kubectl','get','pdb','events-pdb','-o','json']))
                if p['status'].get('observedGeneration')==p['metadata']['generation'] and p['status'].get('disruptionsAllowed')==0: break
                time.sleep(1)
            else: raise RuntimeError('PDB not reconciled')
            run(['kubectl','get','pdb','events-pdb'])
            pod=json.loads(subprocess.check_output(['kubectl','get','pod','-l','app=events','-o','json']))['items'][0]['metadata']['name']
            proxy=subprocess.Popen(['kubectl','proxy','--port=8901'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                time.sleep(2)
                body=json.dumps({'apiVersion':'policy/v1','kind':'Eviction','metadata':{'name':pod,'namespace':'default'}})
                result=run(['curl','-sS','-w','\nHTTP %{http_code}\n','-X','POST','-H','Content-Type: application/json','-d',body,f'http://localhost:8901/api/v1/namespaces/default/pods/{pod}/eviction'])
                assert 'HTTP 429' in result and 'DisruptionBudget' in result
            finally: proxy.terminate(); proxy.wait()
        finally:
            run(['kubectl','patch','pdb','events-pdb','--type=merge','-p','{"spec":{"minAvailable":1}}'])
            time.sleep(2)
            run(['kubectl','get','pdb'])
    elif mode.startswith('snapshot-'):
        for key,q in {
            '5xx_total':'sum(gateway_requests_total{status=~"5.."}) or vector(0)',
            '5xx_increase_3m':'sum(increase(gateway_requests_total{status=~"5.."}[3m])) or vector(0)',
            'requests_increase_3m':'sum(increase(gateway_requests_total{path!="/health"}[3m]))',
            'scrape_targets':'sum(up{job="gateway"})',
            'checkout_success_3m':'sum(increase(gateway_requests_total{path="/reserve/{id}/pay",status="200"}[3m])) or vector(0)',
        }.items():
            print(key); query(q)
        run(['kubectl','get','deploy,rollout,pdb'])
        run(['kubectl','logs','deployment/mixedload','--tail=12','--all-pods=true'])
    elif mode=='kill':
        for app in ['gateway','events']:
            pod=json.loads(subprocess.check_output(['kubectl','get','pods','-l',f'app={app}','-o','json']))['items'][0]['metadata']['name']
            run(['kubectl','delete','pod',pod,'--wait=false'])
        for _ in range(90):
            pods=json.loads(subprocess.check_output(['kubectl','get','pod','-l','app in (gateway,events)','-o','json']))['items']
            ready=[p for p in pods if not p['metadata'].get('deletionTimestamp') and any(c['type']=='Ready' and c['status']=='True' for c in p['status'].get('conditions',[]))]
            if len(ready)==7: break
            time.sleep(1)
        else: raise RuntimeError('Recovery timeout')
        run(['kubectl','get','pods','-l','app in (gateway,events)','-o','wide'])
    else: raise ValueError(mode)
