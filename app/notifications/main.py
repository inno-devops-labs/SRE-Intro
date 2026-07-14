"""QuickTicket Notifications — best-effort order notification service."""

import asyncio
import logging
import os
import random

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"notifications","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("notifications")

app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration", ["method", "path"]
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Total notification attempts", ["result"]
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    import time

    start = time.monotonic()
    response = await call_next(request)
    if not request.url.path.startswith("/metrics"):
        REQUEST_COUNT.labels(
            request.method, request.url.path, response.status_code
        ).inc()
        REQUEST_DURATION.labels(request.method, request.url.path).observe(
            time.monotonic() - start
        )
    return response


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "failure_rate": NOTIFY_FAILURE_RATE,
        "latency_ms": NOTIFY_LATENCY_MS,
    }


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
async def notify(body: dict | None = None):
    payload = body or {}
    event = payload.get("event", "unknown")
    order_id = payload.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning("Notification failed (injected): event=%s order=%s", event, order_id)
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    log.info("Notification sent: event=%s order=%s", event, order_id)
    return {"status": "sent", "event": event, "order_id": order_id}
