"""Run the lab incident and save raw evidence. Always restore payment settings."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import urllib.error

OUT = Path('submissions/evidence/lab6')
OUT.mkdir(parents=True, exist_ok=True)
COMPOSE = ['docker', 'compose', '-f', 'app/docker-compose.yaml', '-f', 'docker-compose.monitoring.yaml']


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def event(name, **extra):
    entry = {'time': now(), 'event': name, **extra}
    with (OUT / 'timeline.jsonl').open('a') as stream:
        stream.write(json.dumps(entry) + '\n')
    print(json.dumps(entry), flush=True)


def get(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Basic YWRtaW46YWRtaW4=', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        return {'http_status': error.code, 'body': error.read().decode()}


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + '\n')


def rules():
    result = get('http://localhost:3000/api/prometheus/grafana/api/v1/rules')
    return result, {r['uid']: r['state'] for group in result['data']['groups'] for r in group['rules']}


def payments(rate):
    env = {**os.environ, 'PAYMENT_FAILURE_RATE': str(rate)}
    subprocess.run(COMPOSE + ['stop', 'payments'], check=True, env=env)
    subprocess.run(COMPOSE + ['up', '-d', 'payments'], check=True, env=env)


subprocess.run([os.sys.executable, 'scripts/test_lab6_contact.py'], check=True)
save('baseline-health.json', get('http://localhost:3080/health'))
seen = set()
try:
    event('injection_started', rate=0.5)
    payments(0.5)
    event('failure_injected', rate=0.5)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        raw, states = rules()
        for uid, state in states.items():
            key = (uid, state)
            if key not in seen:
                seen.add(key)
                event('alert_state', uid=uid, state=state)
                save(f'{uid}-{state}.json', raw)
        notifications = get('http://localhost:8090')
        save('notifications.json', notifications)
        if all(states.get(uid) == 'firing' for uid in ['high-errors', 'slo-burn']) and any(n['payload'].get('status') == 'firing' and any(a.get('labels', {}).get('alertname') == 'QuickTicket High Error Rate' for a in n['payload'].get('alerts', [])) for n in notifications):
            event('diagnosis_started')
            save('diagnosis-health.json', {service: get(url) for service, url in {'gateway': 'http://localhost:3080/health', 'payments': 'http://localhost:8082/health', 'events': 'http://localhost:8081/health'}.items()})
            for service in ['gateway', 'payments']:
                output = subprocess.check_output(COMPOSE + ['logs', service, '--tail=20', '--since=5m'], text=True)
                (OUT / f'{service}-logs.txt').write_text(output)
            output = subprocess.check_output(COMPOSE + ['exec', '-T', 'payments', 'printenv', 'PAYMENT_FAILURE_RATE', 'PAYMENT_LATENCY_MS'], text=True)
            (OUT / 'payment-env.txt').write_text(output)
            event('cause_identified', payment_environment=output.strip())
            break
        time.sleep(15)
    else:
        raise RuntimeError('Required firing evidence did not arrive in 15 minutes')
finally:
    payments(0.0)
    event('fix_applied', rate=0.0)

deadline = time.monotonic() + 2400
recovered = set()
while time.monotonic() < deadline:
    raw, states = rules()
    for uid, state in states.items():
        if state == 'inactive' and uid not in recovered:
            recovered.add(uid)
            event('alert_resolved', uid=uid)
            save(f'{uid}-resolved.json', raw)
    notifications = get('http://localhost:8090')
    save('notifications.json', notifications)
    resolved_names = {a.get('labels', {}).get('alertname') for n in notifications if n['payload'].get('status') == 'resolved' for a in n['payload'].get('alerts', [])}
    if len(recovered) == 2 and {'QuickTicket High Error Rate', 'QuickTicket SLO Burn Rate'} <= resolved_names:
        save('recovered-health.json', get('http://localhost:3080/health'))
        event('complete')
        break
    time.sleep(15)
else:
    raise RuntimeError('Recovery evidence did not arrive in 40 minutes')
