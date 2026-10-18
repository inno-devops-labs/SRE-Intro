"""Wait for the current Rollout generation and any restart to finish."""
import datetime
import json
import subprocess
import time

end = time.monotonic() + 360
last = None
while time.monotonic() < end:
    rollout = json.loads(subprocess.check_output(['kubectl', 'get', 'rollout', 'gateway', '-o', 'json']))
    status, spec = rollout['status'], rollout['spec']
    state = (status.get('phase'), status.get('updatedReplicas'), status.get('readyReplicas'), status.get('replicas'))
    if state != last:
        print('phase, updated, ready, total:', state, flush=True)
        last = state
    current = str(status.get('observedGeneration')) == str(rollout['metadata']['generation'])
    done = current and state == ('Healthy', spec['replicas'], spec['replicas'], spec['replicas'])
    if done and spec.get('restartAt'):
        pods = json.loads(subprocess.check_output(['kubectl', 'get', 'pods', '-l', 'app=gateway', '-o', 'json']))['items']
        done = all(p['metadata']['creationTimestamp'] >= spec['restartAt'] and not p['metadata'].get('deletionTimestamp') for p in pods)
    if done:
        print('Current generation is healthy; stableRS:', status.get('stableRS'), flush=True)
        break
    time.sleep(3)
else:
    raise SystemExit('Timed out waiting for gateway rollout/restart')
