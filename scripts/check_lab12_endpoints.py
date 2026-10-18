"""Smoke-test both live Events replicas and the Gateway after contract."""
import json
import subprocess

pods = json.loads(subprocess.check_output(['kubectl', 'get', 'pods', '-l', 'app=events', '-o', 'json']))['items']
assert len(pods) == 2
probe = '''import json,urllib.request
for base in ('http://localhost:8081', 'http://gateway:8080'):
    for path in ('/health', '/events', '/events/1'):
        with urllib.request.urlopen(base+path, timeout=10) as response:
            body=json.load(response)
            assert response.status == 200
            if path == '/events': assert len(body) >= 5 and all('date' in row for row in body)
            if path == '/events/1': assert body['date'] == '2026-09-15T09:00:00+00:00'
            print(base+path, '200', 'PASS')
'''
for pod in pods:
    assert pod['spec']['containers'][0]['image'] == 'quickticket-events:lab12-b'
    print(pod['metadata']['name'], flush=True)
    subprocess.run(['kubectl', 'exec', '-i', pod['metadata']['name'], '--', 'python', '-'],
                   input=probe, text=True, check=True)
