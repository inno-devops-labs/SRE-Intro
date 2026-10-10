"""Local lab webhook receiver: keeps actual Grafana notifications as JSON lines."""
import datetime
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FILE = Path('/data/notifications.jsonl')


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        record = {'received_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'payload': payload}
        with FILE.open('a') as stream:
            stream.write(json.dumps(record) + '\n')
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        records = [json.loads(line) for line in FILE.read_text().splitlines()] if FILE.exists() else []
        self.wfile.write(json.dumps(records).encode())


ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
