"""Query the read SLI at the two recorded Redis-outage snapshot times."""
import json
from pathlib import Path
import subprocess
import urllib.parse
root=Path('submissions/evidence/lab8')
queries=[
    '(sum(rate(gateway_requests_total{path="/events",status=~"5.."}[1m])) or vector(0))/sum(rate(gateway_requests_total{path="/events"}[1m]))',
    'histogram_quantile(0.99,sum by(le)(rate(gateway_request_duration_seconds_bucket{path="/events"}[1m])))',
]
result={}
for phase,filename in [('before','redis-before-fix.json'),('after','redis-after-fix.json')]:
    snapshot=json.loads((root/filename).read_text())
    timestamp=snapshot['data']['metrics']['sum(rate(gateway_requests_total[1m]))'][0]['value'][0]
    result[phase]={'evaluation_time':timestamp,'metrics':{}}
    for query in queries:
        url='http://localhost:9090/api/v1/query?'+urllib.parse.urlencode({'query':query,'time':timestamp})
        raw=subprocess.check_output(['kubectl','exec','-n','monitoring','deploy/prometheus','--','wget','-qO-',url])
        data=json.loads(raw)
        if data['status']!='success' or not data['data']['result']:
            raise RuntimeError('Missing comparison data: '+str(data))
        result[phase]['metrics'][query]=data['data']['result']
(root/'read-sli-before-after.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
