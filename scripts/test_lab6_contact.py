"""Test the configured Grafana 13 webhook through its receiver API."""
import datetime
import json
from pathlib import Path
import urllib.request

BASE = 'http://localhost:3000/apis/notifications.alerting.grafana.app/v1beta1/namespaces/default/receivers'
headers = {'Authorization': 'Basic YWRtaW46YWRtaW4=', 'Content-Type': 'application/json'}
receivers = json.load(urllib.request.urlopen(urllib.request.Request(BASE, headers=headers)))
receiver = next(r for r in receivers['items'] if r['spec']['title'] == 'quickticket-alerts')
body = {'integration': receiver['spec']['integrations'][0], 'alert': {'labels': {'alertname': 'Grafana contact point test'}, 'annotations': {'summary': 'Lab 6 webhook test'}}}
request = urllib.request.Request(BASE + '/' + receiver['metadata']['name'] + '/test', data=json.dumps(body).encode(), headers=headers)
result = json.load(urllib.request.urlopen(request))
evidence = Path('submissions/evidence/lab6')
evidence.mkdir(parents=True, exist_ok=True)
(evidence / 'contact-test.json').write_text(json.dumps(result, indent=2) + '\n')
record = {'time': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'event': 'contact_test_v13', 'result': result}
with (evidence / 'timeline.jsonl').open('a') as stream:
    stream.write(json.dumps(record) + '\n')
print(json.dumps(record), flush=True)
