"""QuickTicket Notifications — best-effort order notifications."""

import asyncio
import json
import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel
from starlette.responses import Response

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "time": self.formatTime(record), "level": record.levelname,
            "service": "notifications", "msg": record.getMessage(),
        })


log = logging.getLogger("notifications")
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
log.addHandler(handler)
log.setLevel(logging.INFO)
log.propagate = False

app = FastAPI(title="QuickTicket Notifications", version="1.0.0")
REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration", ["method", "path"]
)
NOTIFY_TOTAL = Counter("notifications_notify_total", "Notification attempts", ["result"])
for result in ("success", "failed"):
    NOTIFY_TOTAL.labels(result)


class Notification(BaseModel):
    event: str
    order_id: str


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    if request.url.path != "/metrics":
        path = request.url.path if request.url.path in ("/notify", "/health") else "other"
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(time.monotonic() - start)
    return response


@app.get("/health")
async def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE,
            "latency_ms": NOTIFY_LATENCY_MS}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
async def notify(body: Notification):
    if NOTIFY_LATENCY_MS > 0:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning("Notification failed (injected): event=%s order=%s", body.event, body.order_id)
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info("Notification sent: event=%s order=%s", body.event, body.order_id)
    return {"status": "sent", "event": body.event, "order_id": body.order_id}
