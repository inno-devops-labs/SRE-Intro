"""Write portable Grafana alert provisioning with the local Prometheus UID."""
import json
import urllib.request
from pathlib import Path

req = urllib.request.Request('http://localhost:3000/api/datasources', headers={'Authorization': 'Basic YWRtaW46YWRtaW4='})
uid = next(x['uid'] for x in json.load(urllib.request.urlopen(req)) if x['name'] == 'Prometheus')
queries = [
    ('high-errors', 'QuickTicket High Error Rate', 'sum(rate(gateway_requests_total{status=~"5.."}[5m])) / sum(rate(gateway_requests_total[5m])) * 100', 5, '2m', 'critical'),
    ('slo-burn', 'QuickTicket SLO Burn Rate', '(1 - (sum(rate(gateway_requests_total{status!~"5.."}[30m])) / sum(rate(gateway_requests_total[30m])))) / (1 - 0.995)', 6, '5m', 'warning'),
]
rules = []
for key, title, query, threshold, pending, severity in queries:
    rules.append({'uid': key, 'title': title, 'condition': 'C', 'for': pending, 'noDataState': 'OK', 'execErrState': 'Error',
        'labels': {'severity': severity}, 'annotations': {'summary': 'Gateway error rate is {{ $values.A.Value }}%' if key == 'high-errors' else 'QuickTicket error budget is burning too fast',
        'description': 'Error rate exceeded 5% for 2 minutes. Check payments service health.' if key == 'high-errors' else 'Burn rate exceeded 6 for 5 minutes.'},
        'data': [
            {'refId': 'A', 'datasourceUid': uid, 'relativeTimeRange': {'from': 1800, 'to': 0}, 'model': {'refId': 'A', 'expr': query, 'instant': True, 'range': False, 'intervalMs': 1000, 'maxDataPoints': 43200}},
            {'refId': 'C', 'datasourceUid': '__expr__', 'relativeTimeRange': {'from': 0, 'to': 0}, 'model': {'refId': 'C', 'type': 'threshold', 'expression': 'A', 'conditions': [{'evaluator': {'type': 'gt', 'params': [threshold]}, 'operator': {'type': 'and'}, 'query': {'params': ['C']}, 'reducer': {'type': 'last'}, 'type': 'query'}]}}
        ]})
config = {'apiVersion': 1, 'groups': [{'orgId': 1, 'name': 'quickticket', 'folder': 'QuickTicket', 'interval': '1m', 'rules': rules}],
    'contactPoints': [{'orgId': 1, 'name': 'quickticket-alerts', 'receivers': [{'uid': 'quickticket-webhook', 'type': 'webhook', 'disableResolveMessage': False, 'settings': {'url': 'http://webhook:8080', 'httpMethod': 'POST'}}]}],
    'policies': [{'orgId': 1, 'receiver': 'quickticket-alerts', 'group_by': ['alertname'], 'group_wait': '30s', 'group_interval': '1m', 'repeat_interval': '5m'}]}
out = Path('monitoring/grafana/provisioning/alerting/quickticket.json')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(config, indent=2) + '\n')
print(out)
