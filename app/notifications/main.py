"""Best-effort order notifications with observable fault injection."""

import asyncio
import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s service=notifications level=%(levelname)s %(message)s')
log = logging.getLogger("notifications")
app = FastAPI(title="QuickTicket Notifications")
REQUEST_COUNT = Counter("notifications_requests_total", "Requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Notification outcomes", ["result"])
for result in ("success", "failed"):
    NOTIFY_TOTAL.labels(result)


class Notification(BaseModel):
    event: str
    order_id: str


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/metrics":
        # Unknown URLs share one label rather than creating unbounded series.
        path = request.url.path if request.url.path in ("/notify", "/health") else "other"
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(time.perf_counter() - start)
    return response


@app.get("/health")
async def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE,
            "latency_ms": NOTIFY_LATENCY_MS}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
async def notify(body: Notification):
    if NOTIFY_LATENCY_MS:
        await asyncio.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning("notification failed (injected) event=%s order=%s", body.event, body.order_id)
        raise HTTPException(500, "Notification delivery failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info("notification sent event=%s order=%s", body.event, body.order_id)
    return {"status": "sent", "event": body.event, "order_id": body.order_id}
