"""Capture client-side HTTP/transport failures alongside Prometheus evidence."""
import datetime
import json
import subprocess
import time
while subprocess.run(['kubectl','get','deployment','mixedload'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0:
    p=subprocess.run(['kubectl','logs','deploy/mixedload','--all-pods=true','--prefix=true','--since=30s'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    print(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'returncode':p.returncode,'logs':p.stdout}),flush=True)
    time.sleep(30)
