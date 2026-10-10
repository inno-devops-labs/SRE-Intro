"""Full checkout traffic against a dedicated, generously stocked lab event."""
import json
import os
import time
import urllib.error
import urllib.request

base = os.getenv('GATEWAY_URL', 'http://gateway:8080')
event = os.getenv('EVENT_ID', '6006')

def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
        return 0, {}

while True:
    read, _ = request('/events')
    reserve, result = request(f'/events/{event}/reserve', {'quantity': 1})
    payment = None
    if result.get('reservation_id'):
        payment, _ = request(f"/reserve/{result['reservation_id']}/pay", {})
    print(json.dumps({'time': time.time(), 'read': read, 'reserve': reserve,
                      'payment': payment}), flush=True)
    time.sleep(0.25)
