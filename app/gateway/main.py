"""QuickTicket Gateway — API router and entry point.

Lab 11 — the resilience patterns are implemented. What was a scaffold of no-op
bodies is now:

  - call_with_retry        exponential backoff + jitter, retryable-vs-4xx split
  - CircuitBreaker.call    CLOSED / OPEN / HALF_OPEN state machine
  - RateLimiter.allow      per-path sliding 1-second window
  - Bulkhead.call          bounded per-target concurrency (bonus §11.9)

Composition on the /pay path, outside -> inside:

    bulkhead -> circuit breaker -> retry -> httpx call

Everything is tunable from the environment (see the config block) so the
behaviour can be changed with `kubectl set env` without a rebuild, and every
pattern emits a Prometheus series so it is visible when it fires.
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
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

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

# Bulkhead (Lab 11 bonus §11.9) — bounded concurrency per downstream target.
# MAX is the number of simultaneous occupants; TIMEOUT_S is how long a caller
# waits for a free slot before being fast-failed with BulkheadFullError.
BULKHEAD_PAYMENTS_MAX = int(os.getenv("BULKHEAD_PAYMENTS_MAX", "10"))
BULKHEAD_PAYMENTS_TIMEOUT_S = float(os.getenv("BULKHEAD_PAYMENTS_TIMEOUT_S", "0.5"))

# --- Logging ---
logging.basicConfig(
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"gateway","msg":"%(message)s"}',
    level=logging.INFO,
)
log = logging.getLogger("gateway")

# --- App ---
app = FastAPI(title="QuickTicket Gateway", version="1.1.0")

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
# Lab 11 bonus — bulkhead observability (Reading 11 §Observability: "pool
# saturation, queue depth per dependency").
BULKHEAD_IN_FLIGHT = Gauge(
    "gateway_bulkhead_in_flight", "Calls currently holding a bulkhead slot", ["target"]
)
BULKHEAD_REJECTIONS = Counter(
    "gateway_bulkhead_rejections_total",
    "Calls rejected because the bulkhead was full at acquire timeout",
    ["target"],
)

client = httpx.AsyncClient(timeout=GATEWAY_TIMEOUT_MS / 1000)


# --- Helpers ---


def _normalize_path(path: str) -> str:
    """Normalize URL paths to avoid high-cardinality labels from UUIDs/IDs."""
    path = re.sub(r"/events/\d+", "/events/{id}", path)
    path = re.sub(r"/reserve/[a-f0-9-]+", "/reserve/{id}", path)
    return path


# --- Resilience patterns (Lab 11) --------------------------------------------
#
# Four primitives, all wired into the request path: the rate limiter in the
# middleware (incoming traffic), and bulkhead -> circuit breaker -> retry
# around the payments call in /pay (outgoing traffic).
#
# All state here is PER PROCESS. With 5 gateway replicas the effective
# cluster-wide numbers are 5x the configured ones (5 independent breakers,
# 5 windows, 5 pools). That is a real limitation, not an oversight — sharing
# it needs Redis or a service mesh. It is why the tests below need ~5x the
# request volume you'd expect to trip every pod's breaker.


def _is_retryable(exc: Exception) -> bool:
    """Transient errors only.

    Retryable: the request never got a verdict (connect/timeout), or the server
    said "my fault / come back later" (5xx, 408 Request Timeout, 429 Too Many
    Requests).

    NOT retryable: any other 4xx. A 404 or a 422 is a statement about the
    *request*, not about the server's health — it will fail identically on
    attempt 2 and 3, so retrying it only wastes a round trip and adds load to a
    service that is behaving correctly (Lab 11 "Common Pitfalls" #1).
    """
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in (408, 429)
    return False


async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    """Call `func` with retry-on-transient-error (Lab 11 §11.4).

    Exponential backoff with full-width jitter:

        delay = base * 2**attempt + uniform(0, base)

    The `2**attempt` term spreads successive attempts out so a briefly
    overloaded downstream gets progressively more breathing room. The jitter
    term is what stops N concurrent gateway callers from re-converging on the
    same instants and re-hammering a service the moment it comes back — the
    classic thundering herd (Lab 11 "Common Pitfalls" #2).

    Every branch is counted on `gateway_retry_total{target,result}` so the
    pattern is visible in Prometheus:
        retried               — a transient failure we are about to sleep on
        succeeded_after_retry — attempt > 0 eventually returned a result
        exhausted             — all `max_retries` attempts were transient failures
        non_retryable         — bailed out immediately on a 4xx
    """
    base_delay = RETRY_BASE_DELAY_MS / 1000

    for attempt in range(max_retries):
        try:
            result = await func()
        except Exception as e:
            if not _is_retryable(e):
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                log.warning(f"retry[{target}] non-retryable error, giving up: {e!r}")
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                log.error(
                    f"retry[{target}] exhausted after {max_retries} attempts: {e!r}"
                )
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            log.warning(
                f"retry[{target}] attempt {attempt + 1}/{max_retries} failed "
                f"({e!r}), backing off {delay * 1000:.0f}ms"
            )
            await asyncio.sleep(delay)
            continue

        if attempt > 0:
            RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            log.info(f"retry[{target}] succeeded on attempt {attempt + 1}")
        return result


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
        """Run func with circuit-breaker protection (Lab 11 §11.7).

        State machine:

            CLOSED    — calls pass through. `threshold` consecutive-ish
                        failures trip it to OPEN.
            OPEN      — every call is rejected immediately with
                        CircuitOpenError. No socket, no timeout, microseconds
                        instead of `retries × timeout`. After `cooldown`
                        seconds the next caller is promoted to HALF_OPEN.
            HALF_OPEN — exactly one trial call is let through. Success closes
                        the circuit and resets the failure count; failure
                        re-opens it and restarts the cooldown.
        """
        if self.state == self.OPEN:
            if time.time() - self.opened_at >= self.cooldown:
                # Cooldown elapsed: let ONE probe through to test recovery.
                self._transition(self.HALF_OPEN)
            else:
                # Fast-fail: this is the whole point of the pattern. We do not
                # open a socket, we do not burn the gateway's timeout budget,
                # and the struggling downstream gets zero extra load.
                raise CircuitOpenError(f"circuit[{self.name}] OPEN")

        try:
            result = await func()
        except Exception:
            self.failures += 1
            self.opened_at = time.time()
            # A failed HALF_OPEN probe means "still broken" — straight back to
            # OPEN without waiting for the threshold again.
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                self._transition(self.OPEN)
            raise

        self.failures = 0
        self._transition(self.CLOSED)
        return result


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
        """Sliding 1-second window per key (Lab 11 §11.8).

        `self.hits[key]` is a deque of the timestamps of the requests admitted
        for that key. Every call first evicts everything older than the window,
        then compares what is left against the limit. A sliding window has no
        boundary artefact: a fixed-window limiter of 10 rps happily admits 20
        requests spanning the tick (10 at 0.99s, 10 at 1.01s); this one never
        admits more than `rps` in ANY one-second interval.

        Per-process state — with N gateway replicas the cluster-wide ceiling is
        `rps × N`. A genuinely shared limit needs Redis or an edge proxy.
        """
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
    """Raised by Bulkhead.call when no slot became free within the timeout."""


class Bulkhead:
    """Bounded per-dependency concurrency (Lab 11 bonus §11.9, Reading 11 §6).

    Reading 11's ship metaphor: a hull flooded in one compartment must not sink
    the ship. Here the "compartments" are per-target `asyncio.Semaphore`s. At
    most `max_concurrent` in-flight calls may be outstanding against a given
    downstream; a caller that cannot get a slot within `acquire_timeout_s` is
    rejected immediately instead of piling onto an unbounded queue.

    That bound is the resource-isolation guarantee: whatever payments does to
    itself, it can never occupy more than `max_concurrent` of the gateway's
    connections, sockets and pending tasks, so events/health/metrics keep their
    share.
    """

    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.max_concurrent = max_concurrent
        self.acquire_timeout_s = acquire_timeout_s
        self._sem = asyncio.Semaphore(max_concurrent)
        # Materialise both series at 0 so they exist in /metrics before the
        # first call (an absent series and a zero series look very different
        # in a PromQL alert).
        BULKHEAD_IN_FLIGHT.labels(name).set(0)
        BULKHEAD_REJECTIONS.labels(name).inc(0)

    async def call(self, func):
        try:
            await asyncio.wait_for(
                self._sem.acquire(), timeout=self.acquire_timeout_s
            )
        except asyncio.TimeoutError:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            log.warning(
                f"bulkhead[{self.name}] FULL "
                f"(max={self.max_concurrent}) — rejecting after "
                f"{self.acquire_timeout_s}s wait"
            )
            raise BulkheadFullError(
                f"bulkhead[{self.name}] full (max={self.max_concurrent})"
            )

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            # try/finally, not a plain pair of calls: if func() raises — and
            # under failure injection it does, constantly — a leaked slot would
            # shrink the pool permanently until the pod is restarted.
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self._sem.release()


payments_cb = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_COOLDOWN_S, name="payments")
rate_limiter = RateLimiter(RATE_LIMIT_RPS)
payments_bulkhead = Bulkhead(
    "payments", BULKHEAD_PAYMENTS_MAX, BULKHEAD_PAYMENTS_TIMEOUT_S
)


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
        r = await client.post(
            f"{NOTIFICATIONS_URL}/notify",
            json={"event": "order_confirmed", "order_id": reservation_id},
            timeout=2.0,
        )
        # Lab 11: log non-2xx too. The user is unaffected either way, but a
        # silently dropped notification is exactly the kind of failure that
        # only shows up in a support ticket three days later.
        if r.status_code >= 400:
            log.warning(
                f"notify rejected (non-critical) order={reservation_id} "
                f"status={r.status_code}"
            )
    except Exception as e:
        log.warning(f"notify failed (non-critical) order={reservation_id} err={e}")


@app.post("/reserve/{reservation_id}/pay")
async def pay_reservation(reservation_id: str):
    # 1. Call payments — wrapped in bulkhead + circuit breaker + retry.
    #
    # Composition order (outside -> inside): bulkhead -> CB -> retry -> call.
    #
    # cb.call(retry(_charge)) means each CB-tracked invocation includes its
    # retries internally; the CB only sees the FINAL outcome. The reverse —
    # retry(cb.call(_charge)) — would retry past the CircuitOpenError,
    # defeating the fast-fail. See lab 11 §11.4.
    #
    # The bulkhead sits OUTSIDE both (lab 11 §11.9): it gates ENTRY, so one
    # user request occupies exactly one slot no matter how many internal retry
    # attempts it makes. Put it innermost and 3 retries would each take their
    # own slot — the cap would bound *attempts*, not *requests*, which is not a
    # meaningful bound on anything.
    async def _charge():
        resp = await client.post(
            f"{PAYMENTS_URL}/charge",
            json={"reservation_id": reservation_id, "amount": 0},
        )
        resp.raise_for_status()
        return resp

    try:
        pay_resp = await payments_bulkhead.call(
            lambda: payments_cb.call(
                lambda: call_with_retry(_charge, target="payments")
            )
        )
        payment_ref = pay_resp.json().get("payment_ref", "unknown")
    except CircuitOpenError:
        log.error("circuit open, skipping payments call")
        raise HTTPException(503, "Payment service temporarily unavailable (circuit open)")
    except BulkheadFullError:
        # Same class of signal as CircuitOpenError: a deliberate, immediate
        # refusal to start work we have no capacity for. 503 + a reason the
        # client can act on, rather than a queued request that times out.
        log.error("payments bulkhead full, shedding /pay request")
        raise HTTPException(503, "Payment service at capacity (bulkhead full)")
    except httpx.ConnectError:
        log.warning(f"payments unreachable, reservation {reservation_id} held for retry")
        return JSONResponse(
            status_code=503,
            content={
                "error": "payments_unavailable",
                "message": "Payment service is temporarily down. Your reservation is held — try again in a few minutes.",
                "reservation_id": reservation_id,
            },
        )
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
