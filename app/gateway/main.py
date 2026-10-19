"""QuickTicket API gateway with fault-tolerance patterns for Lab 11."""

import asyncio
import logging
import os
import random
import re
import time
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

EVENTS_URL = os.getenv("EVENTS_URL", "http://events:8081")
PAYMENTS_URL = os.getenv("PAYMENTS_URL", "http://payments:8082")
NOTIFICATIONS_URL = os.getenv("NOTIFICATIONS_URL", "")
GATEWAY_TIMEOUT_MS = int(os.getenv("GATEWAY_TIMEOUT_MS", "5000"))
RETRY_MAX = max(1, int(os.getenv("RETRY_MAX", "3")))
RETRY_BASE_DELAY_MS = max(0, int(os.getenv("RETRY_BASE_DELAY_MS", "110")))
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "4"))
CB_COOLDOWN_S = float(os.getenv("CB_COOLDOWN_S", "28"))
RATE_LIMIT_RPS = max(1, int(os.getenv("RATE_LIMIT_RPS", "11")))
BULKHEAD_PAYMENTS_MAX = max(1, int(os.getenv("BULKHEAD_PAYMENTS_MAX", "9")))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.45"))

logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"gateway","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("gateway")
app = FastAPI(title="QuickTicket Gateway", version="1.2.0")
client = httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_MS / 1000)

REQUEST_COUNT = Counter("gateway_requests_total", "Total requests", ["method", "path", "status"])
REQUEST_DURATION = Histogram("gateway_request_duration_seconds", "Request duration", ["method", "path"])
RETRY_TOTAL = Counter("gateway_retry_total", "Retry outcomes", ["target", "result"])
CB_STATE_TRANSITIONS = Counter("gateway_circuit_breaker_transitions_total", "Circuit transitions", ["to"])
RATE_LIMIT_REJECTIONS = Counter("gateway_rate_limit_rejections_total", "Rate-limited requests", ["path"])
BULKHEAD_IN_FLIGHT = Gauge("gateway_bulkhead_in_flight", "Active calls inside bulkhead", ["target"])
BULKHEAD_REJECTIONS = Counter("gateway_bulkhead_rejections_total", "Bulkhead capacity rejections", ["target"])


def _normalize_path(path: str) -> str:
    path = re.sub(r"/events/\d+", "/events/{id}", path)
    return re.sub(r"/reserve/[a-fA-F0-9-]+", "/reserve/{id}", path)


def _retryable_payment_error(error: Exception) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status in (408, 429) or status >= 500
    return False


async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Retry transient payment failures, adding exponential backoff and jitter."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    base_delay = RETRY_BASE_DELAY_MS / 1000.0
    for attempt in range(max_retries):
        try:
            result = await func()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            if not _retryable_payment_error(exc):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt + 1 == max_retries:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * 2**attempt + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            log.warning("payment retry target=%s attempt=%s/%s sleep=%.3fs cause=%s", target, attempt + 1, max_retries, delay, exc)
            await asyncio.sleep(delay)
        else:
            if attempt:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result


class CircuitOpenError(Exception):
    pass


class CircuitBreaker:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

    def __init__(self, threshold: int, cooldown_s: float, name: str = "payments"):
        if threshold < 1 or cooldown_s < 0:
            raise ValueError("threshold must be positive and cooldown must not be negative")
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown_s
        self.failures = 0
        self.state = self.CLOSED
        self.opened_at = 0.0

    def _transition(self, state: str):
        if state != self.state:
            log.warning("circuit[%s]: %s -> %s", self.name, self.state, state)
            CB_STATE_TRANSITIONS.labels(state).inc()
        self.state = state

    async def call(self, func):
        if self.state == self.OPEN:
            if time.monotonic() - self.opened_at < self.cooldown:
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")
            self._transition(self.HALF_OPEN)
        try:
            result = await func()
        except Exception:
            self.failures += 1
            self.opened_at = time.monotonic()
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                self._transition(self.OPEN)
            raise
        self.failures = 0
        self._transition(self.CLOSED)
        return result


class RateLimiter:
    def __init__(self, rps: int):
        if rps < 1:
            raise ValueError("rps must be positive")
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self.hits[key]
        cutoff = now - self.window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.rps:
            return False
        hits.append(now)
        return True


class BulkheadFullError(Exception):
    pass


class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        if max_concurrent < 1 or acquire_timeout_s < 0:
            raise ValueError("invalid bulkhead capacity or acquire timeout")
        self.name = name
        self.timeout = acquire_timeout_s
        self.sem = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc
        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.sem.release()


payments_cb = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_COOLDOWN_S, "payments")
rate_limiter = RateLimiter(RATE_LIMIT_RPS)
payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = _normalize_path(request.url.path)
    if path not in ("/health", "/metrics") and not rate_limiter.allow(path):
        RATE_LIMIT_REJECTIONS.labels(path).inc()
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "path": path, "limit_rps": RATE_LIMIT_RPS},
            headers={"Retry-After": "1"},
        )
    return await call_next(request)


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    if request.url.path != "/metrics":
        path = _normalize_path(request.url.path)
        REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_DURATION.labels(request.method, path).observe(time.monotonic() - started)
    return response


@app.get("/health")
async def health():
    checks = {}
    for name, url in (("events", EVENTS_URL), ("payments", PAYMENTS_URL)):
        try:
            response = await client.get(f"{url}/health", timeout=2)
            checks[name] = "ok" if response.status_code == 200 else "degraded"
        except httpx.RequestError:
            checks[name] = "down"
    if NOTIFICATIONS_URL:
        try:
            response = await client.get(f"{NOTIFICATIONS_URL}/health", timeout=2)
            checks["notifications"] = "ok" if response.status_code == 200 else "degraded"
        except httpx.RequestError:
            checks["notifications"] = "down"
    checks["circuit_payments"] = payments_cb.state
    ready = checks["events"] == "ok" and checks["payments"] == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "healthy" if ready else "degraded", "checks": checks},
    )


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/events")
async def list_events():
    try:
        response = await client.get(f"{EVENTS_URL}/events")
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Events service timeout") from exc
    except Exception as exc:
        log.error("events error: %s", exc)
        raise HTTPException(502, "Events service unavailable") from exc


@app.get("/events/{event_id}")
async def get_event(event_id: int):
    try:
        response = await client.get(f"{EVENTS_URL}/events/{event_id}")
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Events service timeout") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc
    except Exception as exc:
        log.error("event lookup error: %s", exc)
        raise HTTPException(502, "Events service unavailable") from exc


@app.post("/events/{event_id}/reserve")
async def reserve_tickets(event_id: int, request: Request):
    body = await request.json()
    try:
        response = await client.post(f"{EVENTS_URL}/events/{event_id}/reserve", json=body)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Events service timeout") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, exc.response.text) from exc
    except Exception as exc:
        log.error("reservation error: %s", exc)
        raise HTTPException(502, "Events service unavailable") from exc


async def _notify_order_confirmed(reservation_id: str):
    if not NOTIFICATIONS_URL:
        return
    try:
        response = await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={"event": "order_confirmed", "order_id": reservation_id},
            timeout=2.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("async notification failed for %s: %s", reservation_id, exc)


@app.post("/reserve/{reservation_id}/pay")
async def pay_reservation(reservation_id: str):
    async def charge():
        response = await client.post(
            f"{PAYMENTS_URL}/charge", json={"reservation_id": reservation_id, "amount": 0}
        )
        response.raise_for_status()
        return response

    try:
        payment_response = await payments_bulkhead.call(
            lambda: payments_cb.call(lambda: call_with_retry(charge, "payments"))
        )
        payment_ref = payment_response.json().get("payment_ref", "unknown")
    except CircuitOpenError as exc:
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)") from exc
    except BulkheadFullError as exc:
        raise HTTPException(503, "Payment service busy (bulkhead full)") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Payment service timeout") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Payment failed") from exc
    except Exception as exc:
        log.error("payment error: %s", exc)
        raise HTTPException(502, "Payment service unavailable") from exc

    try:
        response = await client.post(
            f"{EVENTS_URL}/reservations/{reservation_id}/confirm", json={"payment_ref": payment_ref}
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:
        log.error("confirmation failed after payment: %s", exc)
        raise HTTPException(500, "Payment succeeded but confirmation failed — contact support") from exc

    asyncio.create_task(_notify_order_confirmed(reservation_id))
    return result
