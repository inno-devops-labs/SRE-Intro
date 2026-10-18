"""QuickTicket Gateway — API router and entry point.

Lab 11: retry, circuit breaker, rate limiter, bulkhead.
"""

import asyncio
import os
import random
import re
import time
import logging
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# --- Config ---
EVENTS_URL = os.getenv("EVENTS_URL", "http://events:8081")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:8082")
NOTIFICATIONS_URL = os.getenv("NOTIFICATIONS_URL", "")
GATEWAY_TIMEOUT_MS = int(os.getenv("GATEWAY_TIMEOUT_MS", "5000"))

RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))
RETRY_BASE_DELAY_MS = int(os.getenv("RETRY_BASE_DELAY_MS", "100"))

CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
CB_COOLDOWN_S = float(os.getenv("CB_COOLDOWN_S", "30"))

RATE_LIMIT_RPS = int(os.getenv("RATE_LIMIT_RPS", "10"))

BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))

logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"gateway","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("gateway")

app = FastAPI(title="QuickTicket Gateway", version="1.0.0")

REQUEST_COUNT = Counter(
    "gateway_requests_total", "Total requests", ["method", "path", "status"]
)
REQUEST_DURATION = Histogram(
    "gateway_request_duration_seconds", "Request duration", ["method", "path"]
)
RETRY_TOTAL = Counter("gateway_retry_total", "Retry attempts", ["target", "result"])
CB_STATE_TRANSITIONS = Counter(
    "gateway_circuit_breaker_transitions_total", "Circuit breaker state changes", ["to"]
)
RATE_LIMIT_REJECTIONS = Counter(
    "gateway_rate_limit_rejections_total", "Requests rejected by rate limiter", ["path"]
)
BULKHEAD_IN_FLIGHT = Gauge(
    "gateway_bulkhead_in_flight", "Current bulkhead occupants", ["target"]
)
BULKHEAD_REJECTIONS = Counter(
    "gateway_bulkhead_rejections_total", "Bulkhead full-and-timed-out rejections", ["target"]
)

client = httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_MS / 1000)


def _normalize_path(path: str) -> str:
    path = re.sub(r"/events/\d+", "/events/{id}", path)
    path = re.sub(r"/reserve/[a-f0-9-]+", "/reserve/{id}", path)
    return path


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in (408, 429)
    return False


async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000.0
    last_exc = None
    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as e:
            last_exc = e
            if not _is_retryable(e):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
    raise last_exc


class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call when the circuit is open (fast-fail)."""


class CircuitBreaker:
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, threshold: int, cooldown_s: float, name: str = "cb"):
        self.threshold = threshold
        self.cooldown = cooldown_s
        self.name = name
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0

    def _transition(self, new_state: str):
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
        self.state = new_state

    async def call(self, func):
        if self.state == self.OPEN:
            if time.time() - self.opened_at >= self.cooldown:
                self._transition(self.HALF_OPEN)
            else:
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")
        try:
            result = await func()
            self.failures = 0
            self._transition(self.CLOSED)
            return result
        except Exception:
            self.failures += 1
            self.opened_at = time.time()
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                self._transition(self.OPEN)
            raise


class RateLimiter:
    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self.hits[key]
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.rps:
            return False
        q.append(now)
        return True


class BulkheadFullError(Exception):
    """Raised when the semaphore cannot be acquired within the timeout."""


class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.max_concurrent = max_concurrent
        self.acquire_timeout_s = acquire_timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full")
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self._sem.release()


payments_cb = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_COOLDOWN_S, name="payments")
rate_limiter = RateLimiter(RATE_LIMIT_RPS)
payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)


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


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = _normalize_path(request.url.path)
    if path in ("/metrics", "/health"):
        return await call_next(request)
    if not rate_limiter.allow(path):
        RATE_LIMIT_REJECTIONS.labels(path).inc()
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "path": path, "limit_rps": RATE_LIMIT_RPS},
            headers={"Retry-After": "1"},
        )
    return await call_next(request)


@app.get("/health")
async def health():
    checks = {}
    for name, url in (("events", EVENTS_URL), ("payments", PAYMENTS_URL)):
        try:
            r = await client.get(f"{url}/health", timeout=2)
            checks[name] = "ok" if r.status_code == 200 else "degraded"
        except Exception:
            checks[name] = "down"

    if NOTIFICATIONS_URL:
        try:
            r = await client.get(f"{NOTIFICATIONS_URL}/health", timeout=2)
            checks["notifications"] = "ok" if r.status_code == 200 else "degraded"
        except Exception:
            checks["notifications"] = "down"

    checks["circuit_payments"] = payments_cb.state

    critical_ok = checks["events"] == "ok" and checks["payments"] == "ok"
    return JSONResponse(
        status_code=200 if critical_ok else 503,
        content={"status": "healthy" if critical_ok else "degraded", "checks": checks},
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


async def _notify_order_confirmed(reservation_id: str):
    if not NOTIFICATIONS_URL:
        return
    try:
        await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={"event": "order_confirmed", "order_id": reservation_id},
            timeout=2.0,
        )
    except Exception as e:
        log.warning(f"notify failed (non-critical) order={reservation_id} err={e}")


@app.post("/reserve/{reservation_id}/pay")
async def pay_reservation(reservation_id: str):
    async def _charge():
        resp = await client.post(
            f"{PAYMENTS_URL}/charge",
            json={"reservation_id": reservation_id, "amount": 0},
        )
        resp.raise_for_status()
        return resp

    try:
        pay_resp = await payments_bulkhead.call(
            lambda: payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
        )
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except CircuitOpenError:
        log.error("circuit open, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
    except BulkheadFullError:
        log.error("bulkhead full, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (bulkhead full)")
    except httpx.TimeoutException:
        raise HTTPException(504, "Payment service timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Payment failed")
    except Exception as e:
        log.error(f"payment error: {e}")
        raise HTTPException(502, "Payment service unavailable")

    try:
        confirm_resp = await client.post(
            f"{EVENTS_URL}/reservations/{reservation_id}/confirm",
            json={"payment_ref": payment_ref},
        )
        confirm_resp.raise_for_status()
        result = confirm_resp.json()
    except Exception as e:
        log.error(f"confirm error after payment: {e}")
        raise HTTPException(500, "Payment succeeded but confirmation failed — contact support")

    asyncio.create_task(_notify_order_confirmed(reservation_id))
    return result
