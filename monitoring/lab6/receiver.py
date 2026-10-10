"""Local-only lab webhook receiver; records Grafana POSTs as JSON lines."""
import datetime
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Receiver(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        except (ValueError, TypeError):
            self.send_error(400)
            return
        print(json.dumps({"received_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          "payload": body}), flush=True)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, *_):
        pass


HTTPServer(("0.0.0.0", 8080), Receiver).serve_forever()
