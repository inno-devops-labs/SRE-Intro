"""Print compact real window evidence; keep full captures outside Git.

No observations are inferred from missing samples. Raw Prometheus responses,
including per-pod/path/status increases, resets and sample counts, are retained.
"""
import json
from pathlib import Path
import sys

windows=[]
for name in sys.argv[1:]:
    d=json.loads(Path('/tmp/'+name+'.json').read_text())
    snapshots=d.pop('pod_snapshots')
    pod_states={}
    changes=[]
    edges={}
    endpoint_changes=[]
    previous=None
    for snapshot in snapshots:
        for pod in snapshot['pods']:
            state=(pod['ready'],pod['deleting'],pod['phase'])
            if pod_states.get(pod['uid'])!=state:
                changes.append({'timestamp':snapshot['timestamp'],**pod})
                pod_states[pod['uid']]=state
        for pod,counters in snapshot['gateway_counters'].items():
            item={'timestamp':snapshot['timestamp'],'counters':counters}
            if pod not in edges:
                edges[pod]={'first':item}
            edges[pod]['last']=item
        counts={s['service']:sum(e.get('conditions',{}).get('ready',False) for e in s['endpoints']) for s in snapshot['endpoints']}
        if counts!=previous:
            endpoint_changes.append({'timestamp':snapshot['timestamp'],'ready':counts,
                                     'raw_endpoints':snapshot['endpoints']})
            previous=counts
    # Keep small successful order responses and actual errors, not full event lists.
    d['examples']=[r for r in d['examples'] if r['path'].endswith('/pay')]
    d.update(pod_state_changes=changes,per_pod_counter_edges=edges,endpoint_changes=endpoint_changes)
    windows.append(d)
print(json.dumps({'windows':windows},separators=(',',':')))
