"""QuickTicket Gateway — API router and entry point.

Lab 11 scaffold. The wiring (middleware, /pay handler composition, helper
functions, Prometheus metrics) is in place. The three resilience-pattern
classes/functions have empty no-op bodies marked `# TODO (Lab 11): ...`.

Default behavior (no patterns implemented):
  - call_with_retry: calls func once, no retry
  - CircuitBreaker.call:  calls func, never trips
  - RateLimiter.allow:    always returns True

So labs 1-10 run unchanged. Lab 11 students replace the TODOs with real
implementations and the patterns light up.
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
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

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

client = httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_MS / 1000)


# --- Helpers ---


def _normalize_path(path: str) -> str:
    """Normalize URL paths to avoid high-cardinality labels from UUIDs/IDs."""
    path = re.sub(r"/events/\d+", "/events/{id}", path)
    path = re.sub(r"/reserve/[a-f0-9-]+", "/reserve/{id}", path)
    return path


# --- Resilience patterns (Lab 11 implementation slots) -----------------------
#
# Each of the three primitives below is wired into the request path already
# (see the middleware + /pay handler). The bodies start as no-ops, so the
# gateway behaves identically to lab 10 if you don't change them. In lab 11,
# you replace the `# TODO (Lab 11): ...` blocks with real implementations.


async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Call `func` with retry-on-transient-error.

    No-op default: calls func once and returns. Lab 11 task 11.4 replaces this
    body with exponential backoff + jitter, retryable/non-retryable branching,
    and Prometheus counters on the `gateway_retry_total{target,result}` metric.

    See lab 11 §11.4 for the behavior contract. The wiring (in /pay below)
    will pick up your implementation automatically.
    """
    # TODO (Lab 11): implement exponential backoff + jitter here.
    return await func()


class CircuitOpenError(Exception):
    """Raised by CircuitBreaker.call when the circuit is open (fast-fail)."""


class CircuitBreaker:
    """Stateful circuit breaker. Lab 11 task 11.7.

    No-op default: state is always CLOSED, .call just calls func. Replace the
    body of .call with a real CLOSED → OPEN → HALF_OPEN state machine that
    fast-fails with CircuitOpenError once `failures >= threshold`, recovers
    after `cooldown_s`, and emits `gateway_circuit_breaker_transitions_total`.
    """

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
        """Record a state change. Use this from your .call implementation
        so transitions show up in Prometheus."""
        if self.state != new_state:
            log.warning(f"circuit[{self.name}] {self.state} -> {new_state}")
            CB_STATE_TRANSITIONS.labels(new_state).inc()
        self.state = new_state

    async def call(self, func):
        """Run func with circuit-breaker protection.

        No-op default: just calls func. Lab 11 task 11.7 replaces this with
        the state machine. Raise `CircuitOpenError` when the circuit is open.
        """
        # TODO (Lab 11): implement CLOSED/OPEN/HALF_OPEN state machine here.
        return await func()


class RateLimiter:
    """Per-key sliding-window rate limiter. Lab 11 task 11.8.

    No-op default: .allow always returns True. Replace it with a sliding
    1-second window that tracks request timestamps per key and rejects
    once `len(window) >= self.rps`.
    """

    def __init__(self, rps: int):
        self.rps = rps
        self.window_s = 1.0
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Return True if the request should be allowed.

        No-op default: always True. Lab 11 task 11.8 replaces this body.
        """
        # TODO (Lab 11): implement sliding-window check here.
        return True


payments_cb = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_COOLDOWN_S, name="payments")
rate_limiter = RateLimiter(RATE_LIMIT_RPS)


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
    """Apply the rate limiter to every request (except metrics + health).

    Calls `rate_limiter.allow(path)` — which is a no-op (always-True) until
    Lab 11 students implement it.
    """
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
        await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={"event": "order_confirmed", "order_id": reservation_id},
            timeout=2.0,
        )
    except Exception as e:
        log.warning(f"notify failed (non-critical) order={reservation_id} err={e}")


@app.post("/reserve/{reservation_id}/pay")
async def pay_reservation(reservation_id: str):
    # 1. Call payments — wrapped in circuit breaker + retry.
    #
    # Composition order matters: cb.call(retry(_charge)) means each CB-tracked
    # invocation includes its retries internally; the CB only sees the FINAL
    # outcome. The reverse — retry(cb.call(_charge)) — would retry past the
    # CircuitOpenError, defeating the fast-fail. See lab 11 §11.4.
    async def _charge():
        resp = await client.post(
            f"{PAYMENTS_URL}/charge",
            json={"reservation_id": reservation_id, "amount": 0},
        )
        resp.raise_for_status()
        return resp

    try:
        pay_resp = await payments_cb.call(lambda: call_with_retry(_charge, target="payments"))
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
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
    asyncio.create_task(_notify_order_confirmed(reservation_id))

    return result
