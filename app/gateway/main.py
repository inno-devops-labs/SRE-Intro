"""QuickTicket Gateway — routing, observability and dependency resilience."""

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
# Empty by default so labs 1-10 don't try to call a notifications service that
# doesn't exist yet. Lab 11 students set this in k8s/gateway.yaml env.
NOTIFICATIONS_URL = os.getenv("NOTIFICATIONS_URL", "")
GATEWAY_TIMEOUT_MS = int(os.getenv("GATEWAY_TIMEOUT_MS", "5000"))

# Retry (Lab 11)
RETRY_MAX = int(os.getenv("RETRY_MAX", "3"))
RETRY_BASE_DELAY_MS = int(os.getenv("RETRY_BASE_DELAY_MS", "100"))

# Circuit breaker (Lab 11) — protects payments
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "5"))
CB_COOLDOWN_S = float(os.getenv("CB_COOLDOWN_S", "30"))

# Rate limiter (Lab 11) — per endpoint, sliding window
RATE_LIMIT_RPS = int(os.getenv("RATE_LIMIT_RPS", "10"))

BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))

# --- Logging ---
logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"gateway","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("gateway")

# --- App ---
app = FastAPI(title="QuickTicket Gateway", version="1.0.0")

# --- Prometheus metrics ---
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
    "gateway_bulkhead_in_flight", "Active dependency operations", ["target"]
)
BULKHEAD_REJECTIONS = Counter(
    "gateway_bulkhead_rejections_total", "Dependency admission timeouts", ["target"]
)

client = httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_MS / 1000)


# --- Helpers ---


def _normalize_path(path: str) -> str:
    """Normalize URL paths to avoid high-cardinality labels from UUIDs/IDs."""
    path = re.sub(r"/events/\d+", "/events/{id}", path)
    path = re.sub(r"/reserve/[a-f0-9-]+", "/reserve/{id}", path)
    return path


# --- Resilience patterns ---


async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Retry transient failures; max_retries is the TOTAL number of attempts."""
    if max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    base_delay = RETRY_BASE_DELAY_MS / 1000
    for attempt in range(max_retries):
        try:
            result = await func()
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if not (500 <= status < 600 or status in (408, 429)):
                    RETRY_TOTAL.labels(target, "non_retryable").inc()
                    raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
        else:
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result


class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call when the circuit is open (fast-fail)."""


class CircuitBreaker:
    """Per-process breaker with a single recovery probe and stale-call fencing."""

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
        self._generation = 0

    def _transition(self, new_state: str):
        """Record a state change. Use this from your .call implementation
        so transitions show up in Prometheus."""
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
            self._generation += 1
        self.state = new_state

    async def call(self, func):
        # Admission and state updates contain no awaits: they are atomic within
        # this process's event loop. Never hold a lock during downstream I/O.
        if self.state == self.OPEN:
            if time.monotonic() - self.opened_at < self.cooldown:
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")
            self._transition(self.HALF_OPEN)
        elif self.state == self.HALF_OPEN:
            raise CircuitOpenError(f"circuit[{self.name}] recovery probe in flight")

        generation = self._generation
        probe = self.state == self.HALF_OPEN
        try:
            result = await func()
        except asyncio.CancelledError:
            # A cancelled probe must not strand the circuit in HALF_OPEN.
            if probe and generation == self._generation:
                self.opened_at = time.monotonic()
                self._transition(self.OPEN)
            raise
        except Exception:
            if generation == self._generation:
                self.failures += 1
                if probe or self.failures >= self.threshold:
                    self.opened_at = time.monotonic()
                    self._transition(self.OPEN)
            raise
        else:
            # A request admitted before another request opened the breaker
            # cannot close it again with a late success.
            if generation == self._generation:
                self.failures = 0
                self._transition(self.CLOSED)
            return result


class RateLimiter:
    """Per-process, per-path sliding one-second window."""

    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        cutoff = now - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= self.rps:
            return False
        q.append(now)
        return True


class BulkheadFullError(Exception):
    """Dependency concurrency pool stayed full until the admission deadline."""


class Bulkhead:
    """Bound concurrent logical operations, including their retries/backoff."""

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        if max_concurrent < 1 or acquire_timeout_s <= 0:
            raise ValueError("bulkhead capacity and timeout must be positive")
        self.name = name
        self.semaphore = asyncio.BoundedSemaphore(max_concurrent)
        self.acquire_timeout_s = acquire_timeout_s
        self.in_flight = BULKHEAD_IN_FLIGHT.labels(name)
        self.rejections = BULKHEAD_REJECTIONS.labels(name)

    async def call(self, func):
        try:
            await asyncio.wait_for(self.semaphore.acquire(), self.acquire_timeout_s)
        except asyncio.TimeoutError as exc:
            self.rejections.inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc
        self.in_flight.inc()
        try:
            return await func()
        finally:
            self.in_flight.dec()
            self.semaphore.release()


payments_cb = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_COOLDOWN_S, name="payments")
rate_limiter = RateLimiter(RATE_LIMIT_RPS)
payments_bulkhead = Bulkhead("payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S)
notification_tasks: set[asyncio.Task] = set()


# --- Middleware ---


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
    """Apply the per-path limiter, exempting health checks and scraping."""
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


# --- Routes ---


@app.get("/health")
async def health():
    checks = {}
    for name, url in (("events", EVENTS_URL), ("payments", PAYMENTS_URL)):
        try:
            r = await client.get(f"{url}/health", timeout=2)
            checks[name] = "ok" if r.status_code == 200 else "degraded"
        except Exception:
            checks[name] = "down"

    # Notifications is gated on NOTIFICATIONS_URL being configured (Lab 11+).
    # Even when present, notifications status MUST NOT gate the system's
    # critical "healthy" verdict — it's a best-effort dependency.
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
    """Fire-and-forget notification; failure MUST NOT break the user flow.

    No-op when NOTIFICATIONS_URL is unset (labs 1-10). When configured (Lab 11+)
    POSTs to /notify and swallows errors with a warning.
    """
    if not NOTIFICATIONS_URL:
        return
    try:
        response = await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={"event": "order_confirmed", "order_id": reservation_id},
            timeout=2.0,
        )
        response.raise_for_status()
    except Exception as e:
        log.warning(f"notify failed (non-critical) order={reservation_id} err={e}")


@app.post("/reserve/{reservation_id}/pay")
async def pay_reservation(reservation_id: str):
    # Admission → circuit breaker → retry → HTTP. The breaker observes the
    # final logical outcome; retries and backoff hold one bulkhead slot.
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
    except BulkheadFullError:
        raise HTTPException(503, "Payment service busy (bulkhead full)")
    except CircuitOpenError:
        log.error("circuit open, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
    except httpx.TimeoutException:
        raise HTTPException(504, "Payment service timeout")
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, "Payment failed")
    except Exception as e:
        log.error(f"payment error: {e}")
        raise HTTPException(502, "Payment service unavailable")

    # 2. Confirm reservation in events.
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

    # 3. Fire-and-forget notify (don't await → don't add latency, don't fail user).
    task = asyncio.create_task(_notify_order_confirmed(reservation_id))
    notification_tasks.add(task)
    task.add_done_callback(notification_tasks.discard)

    return result
