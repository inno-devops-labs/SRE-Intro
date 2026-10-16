import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._respond(200, {"status": "healthy"})

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body}

        record = {
            "received_at": datetime.now(timezone.utc).isoformat(),
            "path": self.path,
            "payload": payload,
        }
        print(json.dumps(record, separators=(",", ":")), flush=True)
        self._respond(200, {"status": "received"})

    def log_message(self, format: str, *args: object) -> None:
        return

    def _respond(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), WebhookHandler).serve_forever()
