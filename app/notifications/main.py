import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
import uvicorn

# --- Configuration ---
FAILURE_RATE = float(os.getenv("NOTIFY_FAILURE_RATE", "0.0"))
LATENCY_MS = float(os.getenv("NOTIFY_LATENCY_MS", "0"))

# --- Metrics ---
requests_total = Counter(
    "notifications_requests_total",
    "Total requests by method, path, status",
    ["method", "path", "status"]
)
request_duration = Histogram(
    "notifications_request_duration_seconds",
    "Request duration by method, path",
    ["method", "path"]
)
notify_total = Counter(
    "notifications_notify_total",
    "Notifications sent by result",
    ["result"]   # success / failed
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    method = request.method
    path = request.url.path
    start = time.time()
    try:
        response = await call_next(request)
        status = str(response.status_code)
        requests_total.labels(method=method, path=path, status=status).inc()
        request_duration.labels(method=method, path=path).observe(time.time() - start)
        return response
    except Exception:
        requests_total.labels(method=method, path=path, status="500").inc()
        request_duration.labels(method=method, path=path).observe(time.time() - start)
        raise

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "failure_rate": FAILURE_RATE,
        "latency_ms": LATENCY_MS,
    }

@app.get("/metrics")
async def metrics():
    return Response(generate_latest(REGISTRY), media_type="text/plain")

@app.post("/notify")
async def notify(request: Request):
    if LATENCY_MS > 0:
        time.sleep(LATENCY_MS / 1000.0)

    if random.random() < FAILURE_RATE:
        notify_total.labels(result="failed").inc()
        return Response("Simulated failure", status_code=500)

    body = await request.json()
    event = body.get("event")
    order_id = body.get("order_id")
    print(f"NOTIFICATION: event={event} order={order_id}")
    notify_total.labels(result="success").inc()
    return {"status": "sent", "event": event, "order_id": order_id}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8083, log_level="info")