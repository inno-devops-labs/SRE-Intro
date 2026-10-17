

"""QuickTicket Notifications — mock notification delivery service."""

import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import (
    Counter,
    Histogram,
    CONTENT_TYPE_LATEST,
    generate_latest,
)
from starlette.responses import Response


# Configuration
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))


# Logging
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s",'
           '"service":"notifications","msg":"%(message)s"}',
)
log = logging.getLogger("notifications")


# FastAPI application
app = FastAPI(title="QuickTicket Notifications", version="1.0.0")


# Prometheus metrics
REQUEST_COUNT = Counter(
    "notifications_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
)

NOTIFY_TOTAL = Counter(
    "notifications_notify_total",
    "Notification attempts",
    ["result"],
)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)

    duration = time.time() - start
    path = request.url.path

    if not path.startswith("/metrics"):
        REQUEST_COUNT.labels(
            request.method, path, str(response.status_code)
        ).inc()

        REQUEST_DURATION.labels(
            request.method, path
        ).observe(duration)

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
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/notify")
def notify(body: dict):
    event = body.get("event")
    order_id = body.get("order_id")

    if event != "order_confirmed" or not order_id:
        raise HTTPException(
            status_code=422,
            detail="Expected event=order_confirmed and order_id",
        )

    # Inject artificial latency
    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    # Inject artificial failures
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed for order={order_id}")
        raise HTTPException(
            status_code=500,
            detail="Notification delivery failed",
        )

    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent: event={event} order={order_id}")

    return {
        "status": "sent",
        "event": event,
        "order_id": order_id,
    }
