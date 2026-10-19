"""QuickTicket notifications service with test fault injection."""

import logging
import os
import random
import time

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

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Notification attempts", ["result"])


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    if not request.url.path.startswith("/metrics"):
        REQUEST_COUNT.labels(request.method, request.url.path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, request.url.path).observe(time.time() - start)
    return response


@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}


@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
def notify(body: dict):
    event = body.get("event")
    order_id = body.get("order_id")
    if event != "order_confirmed" or not order_id:
        raise HTTPException(422, "Expected order_confirmed and order_id")
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning("Notification failed (injected) for order %s", order_id)
        raise HTTPException(500, "Notification failed")
    NOTIFY_TOTAL.labels("success").inc()
    log.info("Sent %s for order %s", event, order_id)
    return {"status": "sent", "event": event, "order_id": order_id}
