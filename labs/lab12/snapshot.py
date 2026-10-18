"""Read-only evidence snapshot. Run from repo root with the lab cluster selected."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode


def kubectl(*args):
    return subprocess.check_output(['kubectl', *args], text=True)


def query(expression):
    url = 'http://localhost:9090/api/v1/query?' + urlencode({'query': expression})
    return json.loads(kubectl('exec','-n','monitoring','deployment/prometheus','--','wget','-qO-',url))

result = {'time': time.time(), 'phase': sys.argv[1]}
result['observer'] = json.loads(kubectl('exec','deployment/lab12-observer','--','cat','/tmp/observations.json'))
result['prometheus'] = {q: query(q) for q in [
    'sum(gateway_requests_total{status=~"5.."}) or vector(0)',
    'sum(increase(gateway_requests_total{status=~"5.."}[3m])) or vector(0)',
    'sum(increase(gateway_requests_total{path="/reserve/{id}/pay",status="200"}[1m])) or vector(0)',
    'sum(up{job="gateway"})',
]}
result['pods'] = json.loads(kubectl('get','pods','-l','app in (gateway,events,payments,notifications)','-o','json'))['items']
# Keep only fields needed to identify image, replicas and restarts, not full specs.
result['pods'] = [{'name': p['metadata']['name'], 'node':p['spec'].get('nodeName'),
                   'images':[c['image'] for c in p['spec']['containers']],
                   'status':p['status'].get('containerStatuses',[])} for p in result['pods']]
path = Path('submissions/lab12-evidence') / (sys.argv[1]+'.json')
path.write_text(json.dumps(result, indent=2)+'\n')
print(path)
print(json.dumps(result['observer']))
print(json.dumps({q:v['data']['result'] for q,v in result['prometheus'].items()}))
