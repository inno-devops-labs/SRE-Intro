"""Minimal local webhook receiver for reproducible Grafana alert tests."""
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUTPUT_PATH = os.environ.get("WEBHOOK_OUTPUT", "/tmp/quickticket-webhooks.jsonl")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw_body)
            status = payload.get("status", "unknown")
        except json.JSONDecodeError:
            payload, status = raw_body, "unparsed"
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "status": status,
                  "headers": dict(self.headers), "payload": payload}
        with open(OUTPUT_PATH, "a", encoding="utf-8") as output:
            output.write(json.dumps(record) + "\n")
        print(f"{record['timestamp']} status={status}", flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, _format, *_args):
        return


if __name__ == "__main__":
    print("QuickTicket webhook receiver listening on :18080/alerts", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 18080), Handler).serve_forever()
