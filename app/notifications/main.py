import os
import random
import time
import uuid
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Histogram, make_asgi_app

NOTIFY_FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
NOTIFY_LATENCY_MS = int(os.getenv("NOTIFY_LATENCY_MS", "0"))

app = FastAPI(title="QuickTicket Notifications")

REQUEST_COUNT = Counter("notifications_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("notifications_request_duration_seconds", "Request duration", ["method", "path"])
NOTIFY_TOTAL = Counter("notifications_notify_total", "Total notify attempts", ["result"])

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "failure_rate": NOTIFY_FAILURE_RATE,
        "latency_ms": NOTIFY_LATENCY_MS
    }

@app.post("/notify")
def notify(body: dict = None):
    body = body or {}
    event = body.get("event", "unknown")
    order_id = body.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        raise HTTPException(status_code=500, detail="Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    return {
        "status": "sent",
        "notification_ref": f"NOTIF-{uuid.uuid4().hex[:8].upper()}",
        "event": event,
        "order_id": order_id
    }

app.mount("/metrics", make_asgi_app())