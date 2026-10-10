#!/usr/bin/env python3
"""Read Grafana evidence or test its contact point; no password is printed."""
import base64
import json
import os
import subprocess
import sys
import urllib.request

password = base64.b64decode(subprocess.check_output([
    'kubectl', 'get', 'secret', 'lab6-grafana-admin', '-o', 'jsonpath={.data.password}'
]))
headers = {'Authorization': 'Basic ' + base64.b64encode(b'admin:' + password).decode(),
           'Content-Type': 'application/json'}
base = os.getenv('GRAFANA_URL', 'http://localhost:3000')

def call(path, body=None):
    req = urllib.request.Request(base + path, headers=headers,
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

if sys.argv[1:] == ['test-contact']:
    path = '/apis/notifications.alerting.grafana.app/v1beta1/namespaces/default/receivers'
    receivers = call(path)['items']
    receiver = next(r for r in receivers if r['spec']['title'] == 'quickticket-alerts')
    result = call(path + '/' + receiver['metadata']['name'] + '/test', {
        'integration': receiver['spec']['integrations'][0],
        'alert': {'labels': {'alertname': 'Lab6ContactPointTest'},
                  'annotations': {'summary': 'Testing lab 6 webhook before failure injection'}}
    })
else:
    result = call('/api/prometheus/grafana/api/v1/rules')
print(json.dumps(result, indent=2))
