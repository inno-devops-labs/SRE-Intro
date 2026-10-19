"""Snapshot numeric Prometheus values; never compare volatile JSON timestamps."""
import json
import os
import sys
import httpx
query = sys.argv[1] if len(sys.argv)>1 else 'sum(increase(gateway_requests_total{status=~"5.."}[1m])) or vector(0)'
r=httpx.get(os.getenv('PROMETHEUS_URL','http://localhost:19091')+'/api/v1/query',params={'query':query})
r.raise_for_status()
body=r.json()
assert body['status']=='success',body
print(json.dumps({'query':query,'values':body['data']['result']},indent=2))
