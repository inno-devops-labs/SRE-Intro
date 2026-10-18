"""Mark exact experiment windows; save results without overwriting earlier runs."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from types import SimpleNamespace

from probe import window

p=argparse.ArgumentParser()
p.add_argument('action',choices=['start','end'])
p.add_argument('name')
a=p.parse_args()
path=Path('/tmp/'+a.name+'-mark.json')
if a.action=='start':
    if path.exists():
        raise RuntimeError('Use a separate name for a repeated experiment')
    data={'start':time.time(),'start_utc':datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(data))
    print(json.dumps(data))
else:
    data=json.loads(path.read_text())
    if 'end' in data:
        raise RuntimeError('This window was already closed')
    data.update(end=time.time(),end_utc=datetime.now(timezone.utc).isoformat())
    path.write_text(json.dumps(data))
    time.sleep(6)  # Let the final scrape complete before querying the fixed window.
    asyncio.run(window(SimpleNamespace(start=data['start'],end=data['end'],label=a.name)))
