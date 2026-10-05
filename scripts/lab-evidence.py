"""Shared CLI/Prometheus evidence helpers. Run only in the isolated lab cluster."""
import datetime
import json
import os
import pathlib
import subprocess
import time
import urllib.parse

os.environ['PATH'] = os.path.expanduser('~/.local/bin') + ':' + os.environ['PATH']

def stamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def run(*args):
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print('$ ' + ' '.join(args) + '\n' + p.stdout, flush=True)
    if p.returncode:
        raise RuntimeError(p.stdout)
    return p.stdout

def k(*args):
    return run('kubectl', *args)

def obj(kind, name):
    return json.loads(subprocess.check_output(['kubectl', 'get', kind, name, '-o', 'json']))

def wait(test, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(2)
    raise TimeoutError('condition not met')

def healthy():
    def ready():
        d = obj('rollout', 'gateway'); s = d.get('status', {})
        return (s.get('observedGeneration') == str(d['metadata']['generation']) or s.get('observedGeneration') == d['metadata']['generation']) and s.get('phase') == 'Healthy' and s.get('updatedReplicas') == 5 and s.get('readyReplicas') == 5
    wait(ready, 360)

def query(q):
    url = 'http://localhost:9090/api/v1/query?' + urllib.parse.urlencode({'query': q})
    raw = subprocess.check_output(['kubectl', 'exec', '-n', 'monitoring', 'deploy/prometheus', '--', 'wget', '-qO-', url], text=True)
    data = json.loads(raw)
    if data['status'] != 'success':
        raise RuntimeError(raw)
    return data['data']['result']

def metrics():
    return {q: query(q) for q in [
        'sum(rate(gateway_requests_total[1m]))',
        '(sum(rate(gateway_requests_total{status=~"5.."}[1m])) or vector(0))/sum(rate(gateway_requests_total[1m]))',
        'sum by(pod)(rate(gateway_requests_total[1m]))',
        'histogram_quantile(0.99,sum by(le,path)(rate(gateway_request_duration_seconds_bucket[1m])))',
        'sum(increase(gateway_requests_total{status=~"5.."}[3m]))',
    ]}

def save(path, value):
    pathlib.Path(path).write_text(json.dumps({'timestamp': stamp(), 'data': value}, indent=2) + '\n')
