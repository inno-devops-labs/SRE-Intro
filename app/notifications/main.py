"""QuickTicket Notifications — Mock notification dispatcher with tunable failures."""

import os
import uuid
import time
import random
import logging

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# --- Config (fault injection via env vars) ---
NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

# --- Logging ---
logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"notifications","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("notifications")

# --- App ---
app = FastAPI(title="QuickTicket Notifications", version="1.0.0")

# --- Prometheus metrics ---
REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notification attempts", ["result"])


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    path = request.url.path
    if not path.startswith("/metrics"):
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(duration)
    return response


@app.get("/health")
def health():
    return {"status": "healthy", "failure_rate": NOTIFY_FAILURE_RATE, "latency_ms": NOTIFY_LATENCY_MS}


@app.get("/metrics")
def metrics():
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notify")
def notify(body: dict = None):
    event = (body or {}).get("event", "unknown")
    order_id = (body or {}).get("order_id", "unknown")

    # Inject latency
    if NOTIFY_LATENCY_MS > 0:
        delay = NOTIFY_LATENCY_MS / 1000
        log.info(f"Injecting {NOTIFY_LATENCY_MS}ms latency for order {order_id}")
        time.sleep(delay)

    # Inject failures
    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected) for order {order_id}")
        raise HTTPException(500, "Notification processing failed")

    notification_id = f"NTF-{uuid.uuid4().hex[:8].upper()}"
    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent: {notification_id} for event {event} (order {order_id})")
    return {"status": "sent", "notification_id": notification_id}
