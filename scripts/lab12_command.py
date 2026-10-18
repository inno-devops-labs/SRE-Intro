"""Run one lab command and preserve its unedited output and UTC timestamp."""
import datetime
from pathlib import Path
import shlex
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
name, *command = sys.argv[1:]
if not name.replace('-', '').replace('_', '').isalnum() or not command:
    raise SystemExit('usage: lab12_command.py evidence-name command [args ...]')
output = root / 'labs/lab12/evidence' / f'{name}.log'
output.parent.mkdir(parents=True, exist_ok=True)
with output.open('w') as stream:
    for line in [datetime.datetime.now(datetime.timezone.utc).isoformat(), '$ ' + shlex.join(command)]:
        print(line, flush=True)
        stream.write(line + '\n')
    stream.flush()
    process = subprocess.Popen(command, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for line in process.stdout:
        print(line, end='', flush=True)
        stream.write(line)
        stream.flush()
    code = process.wait()
    stream.write(f'Exit status: {code}\n')
raise SystemExit(code)
