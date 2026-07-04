from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import asyncio
import os
import random
import time
import logging
import httpx

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gateway")

EVENTS_URL = os.getenv("EVENTS_URL", "http://events:8081")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:8082")
NOTIFICATIONS_URL = os.getenv("NOTIFICATIONS_URL", "")

app = FastAPI()
client = httpx.AsyncClient(timeout=5.0)

rate_hits = {}

def can_make_request(path):
    now = time.time()
    if path not in rate_hits:
        rate_hits[path] = []
    q = rate_hits[path]
    while q and q[0] < now - 1:
        q.pop(0)
    if len(q) >= 10:
        return False
    q.append(now)
    return True

@app.middleware("http")
async def rate_limit_mw(request: Request, call_next):
    if request.url.path in ["/metrics", "/health"]:
        return await call_next(request)
    if not can_make_request(request.url.path):
        return JSONResponse(status_code=429, content={"error": "too many requests"})
    return await call_next(request)

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/events")
async def list_events():
    try:
        r = await client.get(f"{EVENTS_URL}/events")
        r.raise_for_status()
        return r.json()
    except Exception:
        raise HTTPException(502, "Events service unavailable")

@app.post("/events/{event_id}/reserve")
async def reserve(event_id: int, request: Request):
    body = await request.json()
    try:
        r = await client.post(f"{EVENTS_URL}/events/{event_id}/reserve", json=body)
        r.raise_for_status()
        return r.json()
    except Exception:
        raise HTTPException(502, "Reserve failed")

@app.post("/reserve/{reservation_id}/pay")
async def pay(reservation_id: str):
    try:
        r = await client.post(
            f"{PAYMENTS_URL}/charge", 
            json={"reservation_id": reservation_id, "amount": 0}
        )
        r.raise_for_status()
    except Exception:
        return JSONResponse(
            status_code=503, 
            content={"error": "payments_unavailable", "message": "Payment service is down"}
        )

    if NOTIFICATIONS_URL:
        asyncio.create_task(
            client.post(f"{NOTIFICATIONS_URL}/notify", json={"reservation_id": reservation_id})
        )

    return {"status": "confirmed"}

@app.get("/metrics")
async def metrics():
    return {"metrics": "not implemented in simple version"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
