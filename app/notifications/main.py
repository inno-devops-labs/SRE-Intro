"""QuickTicket Notifications — Mock notification service with fault injection."""

import logging
import os
import random
import time

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.responses import Response


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format=(
        '{"time":"%(asctime)s","level":"%(levelname)s",'
        '"service":"notifications","msg":"%(message)s"}'
    ),
    level=logging.INFO,
)

log = logging.getLogger("notifications")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="QuickTicket Notifications",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "notifications_requests_total",
    "Total notifications service requests",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "notifications_request_duration_seconds",
    "Notifications request duration",
    ["method", "path"],
)

NOTIFY_TOTAL = Counter(
    "notifications_notify_total",
    "Total notification attempts",
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
            request.method,
            path,
            str(response.status_code),
        ).inc()

        REQUEST_DURATION.labels(
            request.method,
            path,
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
def notify(body: dict | None = None):
    payload = body or {}

    event = payload.get("event")
    order_id = payload.get("order_id")

    if not event or not order_id:
        raise HTTPException(
            status_code=422,
            detail="event and order_id are required",
        )

    if NOTIFY_LATENCY_MS > 0:
        delay_seconds = NOTIFY_LATENCY_MS / 1000
        log.info(
            "Injecting %sms latency for event=%s order=%s",
            NOTIFY_LATENCY_MS,
            event,
            order_id,
        )
        time.sleep(delay_seconds)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()

        log.warning(
            "Notification failed (injected): event=%s order=%s",
            event,
            order_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Notification delivery failed",
        )

    NOTIFY_TOTAL.labels("success").inc()

    log.info(
        "Notification sent: event=%s order=%s",
        event,
        order_id,
    )

    return {
        "status": "sent",
        "event": event,
        "order_id": order_id,
    }
