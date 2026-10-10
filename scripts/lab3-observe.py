#!/usr/bin/env python3
"""Run Lab 3 on an isolated Compose project; emit timestamped JSON evidence.

Start the stack first with -p sre-lab3. Run from any directory:
  python3 scripts/lab3-observe.py > /tmp/lab3-observations.jsonl
This deliberately stops/recreates payments. A finally block restores defaults.
"""
import datetime
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DC = ['docker', 'compose', '-p', 'sre-lab3', '-f', str(ROOT/'app/docker-compose.yaml'), '-f', str(ROOT/'docker-compose.monitoring.yaml')]
STOP = threading.Event()
LOCK = threading.Lock()
PHASE = 'baseline'
QUERIES = {
    'rps': 'sum(rate(gateway_requests_total[1m]))',
    'error_percent': '(sum(rate(gateway_requests_total{status=~"5.."}[1m])) or 0 * sum(rate(gateway_requests_total[1m]))) / sum(rate(gateway_requests_total[1m])) * 100',
    'p50': 'histogram_quantile(0.5, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))',
    'p95': 'histogram_quantile(0.95, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))',
    'p99': 'histogram_quantile(0.99, sum by (le) (rate(gateway_request_duration_seconds_bucket[1m])))',
    'availability': 'gateway:sli_availability:ratio_rate5m',
    'latency_sli': 'gateway:sli_latency_500ms:ratio_rate5m',
    'burn_rate': 'gateway:error_budget_burn_rate:ratio_rate5m',
    'db_pool': 'events_db_pool_size',
    'payments_up': 'up{job="payments"}',
}

def emit(kind, **data):
    with LOCK:
        print(json.dumps(dict(time=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds'), phase=PHASE, kind=kind, **data)), flush=True)

def request(path, body=None):
    req = urllib.request.Request('http://localhost:3080'+path, data=json.dumps(body).encode() if body is not None else None, headers={'Content-Type':'application/json'})
    if path.endswith('/pay'): req.method='POST'
    start=time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=10) as r: code=r.status; data=json.load(r)
    except urllib.error.HTTPError as e:
        code=e.code; data=e.read().decode()
    except (OSError, ValueError) as e:
        code=0; data=str(e)
    if code>=500 or code==0 or path.endswith('/pay'):
        emit('request', path=path, status=code, elapsed=round(time.monotonic()-start,4), body=data)
    return code, data

def traffic():
    i=0
    while not STOP.is_set():
        i+=1
        if i%10==0:
            code,data=request('/events/3/reserve', {'quantity':1})
            if code==200: request('/reserve/'+data['reservation_id']+'/pay')
        elif i%10==9: request('/health')
        else: request('/events')
        STOP.wait(0.3)

def sample():
    while not STOP.is_set():
        values={}
        for name,query in QUERIES.items():
            try:
                url='http://localhost:9090/api/v1/query?'+urllib.parse.urlencode({'query':query})
                with urllib.request.urlopen(url,timeout=5) as r: result=json.load(r)['data']['result']
                values[name]=result[0]['value'][1] if result else None
            except Exception as e: values[name]=str(e)
        emit('metrics', **values)
        STOP.wait(5)

def compose(*args, **env):
    result=subprocess.run(DC+list(args),env={**os.environ,**env},capture_output=True,text=True,check=True)
    emit('compose', command=args, output=result.stdout+result.stderr)

threads=[threading.Thread(target=traffic),threading.Thread(target=sample)]
try:
    for t in threads:t.start()
    emit('phase_start'); time.sleep(60)
    PHASE='payments_stopped'; emit('injection_start'); compose('stop','payments'); emit('injection_complete'); time.sleep(120)
    PHASE='restarted'; compose('start','payments'); emit('recovery_start'); time.sleep(60)
    PHASE='injected_latency_errors'; emit('injection_start'); compose('up','-d','--no-deps','--force-recreate','payments', PAYMENT_FAILURE_RATE='0.5',PAYMENT_LATENCY_MS='1000'); emit('injection_complete'); time.sleep(120)
    PHASE='recovery'; compose('up','-d','--no-deps','--force-recreate','payments', PAYMENT_FAILURE_RATE='0.0',PAYMENT_LATENCY_MS='0'); emit('recovery_start'); time.sleep(330)
finally:
    STOP.set()
    for t in threads:
        if t.ident:t.join(timeout=15)
    compose('up','-d','--no-deps','payments', PAYMENT_FAILURE_RATE='0.0',PAYMENT_LATENCY_MS='0')
