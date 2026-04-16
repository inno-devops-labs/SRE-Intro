"""QuickTicket Payments — Mock payment processor with tunable failures."""

import os
import uuid
import time
import random
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# --- Config (fault injection via env vars) ---
PAYMENT_FAILURE_RATE = float(os.getenv("PAYMENT_FAILURE_RATE", "0.0"))
PAYMENT_LATENCY_MS = int(os.getenv("PAYMENT_LATENCY_MS", "0"))

# --- Logging ---
logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"payments","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("payments")

# --- App ---
app = FastAPI(title="QuickTicket Payments", version="1.0.0")

# --- Prometheus metrics ---
REQUEST_COUNT = Counter("payments_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("payments_request_duration_seconds", "Request duration", ["method", "path"])
CHARGES_TOTAL = Counter("payments_charges_total", "Total charge attempts", ["result"])


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
    return {"status": "healthy", "failure_rate": PAYMENT_FAILURE_RATE, "latency_ms": PAYMENT_LATENCY_MS}


@app.get("/metrics")
def metrics():
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/charge")
def charge(body: dict = None):
    reservation_id = (body or {}).get("reservation_id", "unknown")

    # Inject latency
    if PAYMENT_LATENCY_MS > 0:
        delay = PAYMENT_LATENCY_MS / 1000
        log.info(f"Injecting {PAYMENT_LATENCY_MS}ms latency for {reservation_id}")
        time.sleep(delay)

    # Inject failures
    if random.random() < PAYMENT_FAILURE_RATE:
        CHARGES_TOTAL.labels("failed").inc()
        log.warning(f"Payment failed (injected) for {reservation_id}")
        raise HTTPException(500, "Payment processing failed")

    payment_ref = f"PAY-{uuid.uuid4().hex[:8].upper()}"
    CHARGES_TOTAL.labels("success").inc()
    log.info(f"Payment success: {payment_ref} for {reservation_id}")
    return {"status": "charged", "payment_ref": payment_ref}
