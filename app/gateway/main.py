"""QuickTicket Gateway — API router and entry point."""

import os
import time
import logging
import json

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# --- Config ---
EVENTS_URL = os.getenv("EVENTS_URL", "http://events:8081")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:8082")
GATEWAY_TIMEOUT_MS = int(os.getenv("GATEWAY_TIMEOUT_MS", "5000"))

# --- Logging ---
logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"gateway","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("gateway")

# --- App ---
app = FastAPI(title="QuickTicket Gateway", version="1.0.0")

# --- Prometheus metrics ---
REQUEST_COUNT = Counter("gateway_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("gateway_request_duration_seconds", "Request duration", ["method", "path"])

client = httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_MS / 1000)


def _normalize_path(path: str) -> str:
    """Normalize URL paths to avoid high-cardinality labels from UUIDs/IDs."""
    import re
    path = re.sub(r'/events/\d+', '/events/{id}', path)
    path = re.sub(r'/reserve/[a-f0-9-]+', '/reserve/{id}', path)
    return path


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    path = _normalize_path(request.url.path)
    if not path.startswith("/metrics"):
        REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
        REQUEST_DURATION.labels(request.method, path).observe(duration)
    return response


@app.get("/health")
async def health():
    checks = {}
    try:
        r = await client.get(f"{EVENTS_URL}/health", timeout=2)
        checks["events"] = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        checks["events"] = "down"
    try:
        r = await client.get(f"{PAYMENTS_URL}/health", timeout=2)
        checks["payments"] = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        checks["payments"] = "down"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "healthy" if all_ok else "degraded", "checks": checks},
    )


@app.get("/metrics")
async def metrics():
    from starlette.responses import Response
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/events")
async def list_events():
    try:
        r = await client.get(f"{EVENTS_URL}/events")
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException:
        log.error("events service timeout")
        raise HTTPException(504, "Events service timeout")
    except Exception as e:
        log.error(f"events service error: {e}")
        raise HTTPException(502, "Events service unavailable")


@app.get("/events/{event_id}")
async def get_event(event_id: int):
    try:
        r = await client.get(f"{EVENTS_URL}/events/{event_id}")
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "Events service timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.text)
    except Exception as e:
        log.error(f"events service error: {e}")
        raise HTTPException(502, "Events service unavailable")


@app.post("/events/{event_id}/reserve")
async def reserve_tickets(event_id: int, request: Request):
    body = await request.json()
    try:
        r = await client.post(f"{EVENTS_URL}/events/{event_id}/reserve", json=body)
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException:
        raise HTTPException(504, "Events service timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, e.response.json())
    except Exception as e:
        log.error(f"reserve error: {e}")
        raise HTTPException(502, "Events service unavailable")


@app.post("/reserve/{reservation_id}/pay")
async def pay_reservation(reservation_id: str):
    # 1. Call payments
    try:
        pay_resp = await client.post(f"{PAYMENTS_URL}/charge", json={"reservation_id": reservation_id, "amount": 0})
        pay_resp.raise_for_status()
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except httpx.TimeoutException:
        log.error("payments service timeout")
        raise HTTPException(504, "Payment service timeout")
    except httpx.HTTPStatusError as e:
        log.error(f"payment failed: {e.response.text}")
        raise HTTPException(e.response.status_code, "Payment failed")
    except Exception as e:
        log.error(f"payment error: {e}")
        raise HTTPException(502, "Payment service unavailable")

    # 2. Confirm reservation in events
    try:
        confirm_resp = await client.post(
            f"{EVENTS_URL}/reservations/{reservation_id}/confirm",
            json={"payment_ref": payment_ref},
        )
        confirm_resp.raise_for_status()
        return confirm_resp.json()
    except Exception as e:
        log.error(f"confirm error after payment: {e}")
        raise HTTPException(500, "Payment succeeded but confirmation failed — contact support")
