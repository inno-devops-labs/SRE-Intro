"""Windows-friendly checkout traffic; about three gateway requests per second."""
import argparse
import collections
import json
import time
import urllib.request
import urllib.error

parser = argparse.ArgumentParser()
parser.add_argument('--seconds', type=int, default=3600)
parser.add_argument('--url', default='http://localhost:3080')
parser.add_argument('--event', type=int, default=3)
args = parser.parse_args()
counts = collections.Counter()


def request(path, body=None):
    req = urllib.request.Request(args.url + path, data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            counts[str(response.status)] += 1
            return json.load(response)
    except urllib.error.HTTPError as error:
        counts[str(error.code)] += 1
    except Exception:
        counts['network_error'] += 1
    return {}


deadline = time.monotonic() + args.seconds
iteration = 0
while time.monotonic() < deadline:
    started = time.monotonic()
    request('/events')
    reservation = request(f'/events/{args.event}/reserve', {'quantity': 1})
    if 'reservation_id' in reservation:
        request('/reserve/' + reservation['reservation_id'] + '/pay', {})
    iteration += 1
    if iteration % 30 == 0:
        print(json.dumps({'utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'counts': counts}), flush=True)
    time.sleep(max(0, 1 - (time.monotonic() - started)))
print(json.dumps(dict(counts)), flush=True)
