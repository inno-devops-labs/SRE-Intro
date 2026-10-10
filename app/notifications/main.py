"""QuickTicket Notifications — best-effort delivery with fault injection."""

import asyncio
import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import (
    Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST,
)
from pydantic import BaseModel
from starlette.responses import Response

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

if not 0 <= NOTIFY_FAILURE_RATE <= 1 or NOTIFY_LATENCY_MS < 0:
    raise ValueError("Invalid notification fault-injection settings")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("notifications")
app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

REQUEST_COUNT = Counter(
    "notifications_requests_total", "Total requests",
    ["method", "path", "status"],
)
REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds", "Request duration",
    ["method", "path"],
)
NOTIFY_TOTAL = Counter(
    "notifications_notify_total", "Notification outcomes", ["result"],
)


class Notification(BaseModel):
    event: str
    order_id: str


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/metrics":
        # Only fixed routes become labels; unknown URLs share one label.
        path = request.url.path
        if path not in ("/notify", "/health"):
            path = "/other"
        REQUEST_COUNT.labels(
            request.method, path, str(response.status_code)
        ).inc()
        REQUEST_DURATION.labels(request.method, path).observe(
            time.perf_counter() - start
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
    return Response(
        content=generate_latest(), media_type=CONTENT_TYPE_LATEST
    )


@app.post("/notify")
async def notify(body: Notification):
    if NOTIFY_LATENCY_MS:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(
            "Notification failed (injected): event=%s order=%s",
            body.event, body.order_id,
        )
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info("Notification sent: event=%s order=%s", body.event, body.order_id)
    return {
        "status": "sent", "event": body.event, "order_id": body.order_id,
    }
