#!/usr/bin/env python3

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import sys
from datetime import datetime, timezone


HOST = "0.0.0.0"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
LOG_PATH = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/grafana-webhook.jsonl")


class Handler(BaseHTTPRequestHandler):
    def _write_entry(self, body: bytes) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": self.command,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": body.decode("utf-8", errors="replace"),
        }
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def do_POST(self) -> None:  
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self._write_entry(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"listening on {HOST}:{PORT}, logging to {LOG_PATH}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
