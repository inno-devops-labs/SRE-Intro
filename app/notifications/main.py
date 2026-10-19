"""Best-effort notification endpoint used by the QuickTicket gateway."""

import logging
import os
import random
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"notifications","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("notifications")
app = FastAPI(title="QuickTicket Notifications", version="1.2.0")

REQUEST_COUNT = Counter("notifications_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "HTTP latency", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Notification outcomes", ["result"])


class NotificationEvent(BaseModel):
    event: str = Field(min_length=1)
    order_id: str = Field(min_length=1)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    if request.url.path != "/metrics":
        path = request.url.path
        REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_DURATION.labels(request.method, path).observe(time.monotonic() - start)
    return response


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "failure_rate": NOTIFY_FAILURE_RATE,
        "latency_ms": NOTIFY_LATENCY_MS,
    }


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
def notify(payload: NotificationEvent):
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000.0)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning("Injected notify failure, order=%s", payload.order_id)
        raise HTTPException(status_code=500, detail="Notification delivery failed")

    notification_id = f"NTF-{uuid.uuid4().hex[:12].upper()}"
    NOTIFY_TOTAL.labels("success").inc()
    log.info("Notification %s for order=%s event=%s", notification_id, payload.order_id, payload.event)
    return {
        "status": "sent",
        "notification_id": notification_id,
        "event": payload.event,
        "order_id": payload.order_id,
    }
