import asyncio
import importlib.util
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def load(module_name, location):
    spec = importlib.util.spec_from_file_location(module_name, location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway = load("lab11_gateway", ROOT / "app/gateway/main.py")
notifications = load("lab11_notifications", ROOT / "app/notifications/main.py")


def test_retry_recovers_from_503():
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            request = httpx.Request("POST", "http://payments:8082/charge")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)
        return "paid"

    assert asyncio.run(gateway.call_with_retry(flaky, "payments", max_retries=3)) == "paid"
    assert attempts == 3


def test_retry_does_not_repeat_404():
    attempts = 0

    async def not_found():
        nonlocal attempts
        attempts += 1
        request = httpx.Request("POST", "http://payments:8082/charge")
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    try:
        asyncio.run(gateway.call_with_retry(not_found, "payments"))
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("404 should not be retried")
    assert attempts == 1


def test_breaker_opens_and_recovers():
    breaker = gateway.CircuitBreaker(threshold=4, cooldown_s=0)

    async def fail():
        raise RuntimeError("upstream down")

    async def succeed():
        return "ok"

    for _ in range(4):
        try:
            asyncio.run(breaker.call(fail))
        except RuntimeError:
            pass
    assert breaker.state == "OPEN"
    assert asyncio.run(breaker.call(succeed)) == "ok"
    assert breaker.state == "CLOSED"
    assert breaker.failures == 0


def test_sliding_window_keeps_keys_separate():
    limiter = gateway.RateLimiter(11)
    assert [limiter.allow("/events") for _ in range(14)] == [True] * 11 + [False] * 3
    assert limiter.allow("/reserve/{id}/pay") is True


def test_bulkhead_rejects_when_full():
    bulkhead = gateway.Bulkhead("test-payments", max_concurrent=9, acquire_timeout_s=0.02)

    async def slow():
        await asyncio.sleep(0.08)
        return "ok"

    async def request():
        try:
            return await bulkhead.call(slow)
        except gateway.BulkheadFullError:
            return "busy"

    async def run():
        return await asyncio.gather(*(request() for _ in range(13)))

    results = asyncio.run(run())
    assert results.count("ok") == 9
    assert results.count("busy") == 4


def test_notifications_health_and_delivery():
    async def run():
        transport = httpx.ASGITransport(app=notifications.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            health = await c.get("/health")
            sent = await c.post("/notify", json={"event": "order_confirmed", "order_id": "r-001"})
            metrics = await c.get("/metrics")
        return health, sent, metrics

    health, sent, metrics = asyncio.run(run())
    assert health.status_code == 200
    assert sent.status_code == 200
    assert sent.json()["status"] == "sent"
    assert "notifications_notify_total" in metrics.text


def test_429_appears_in_gateway_request_metrics():
    original = gateway.rate_limiter
    gateway.rate_limiter = gateway.RateLimiter(2)

    async def run():
        transport = httpx.ASGITransport(app=gateway.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as c:
            replies = [await c.get("/not-a-route") for _ in range(3)]
            metric_response = await c.get("/metrics")
        return replies, metric_response

    try:
        replies, metric_response = asyncio.run(run())
    finally:
        gateway.rate_limiter = original
    assert [r.status_code for r in replies] == [404, 404, 429]
    assert replies[-1].headers["retry-after"] == "1"
    assert 'status="429"' in metric_response.text


def test_retry_recovers_after_rate_limit():
    attempts = 0

    async def upstream():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            request = httpx.Request("POST", "http://payments:8082/charge")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("throttled", request=request, response=response)
        return "ok"

    assert asyncio.run(gateway.call_with_retry(upstream, "payments")) == "ok"
    assert attempts == 2


def test_retry_exhaustion_on_transient_error():
    attempts = 0

    async def unavailable():
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("not available")

    try:
        asyncio.run(gateway.call_with_retry(unavailable, "payments", max_retries=2))
    except httpx.ConnectError:
        pass
    else:
        raise AssertionError("retries must be exhausted")
    assert attempts == 2


def test_circuit_fast_reject_when_open():
    breaker = gateway.CircuitBreaker(threshold=2, cooldown_s=60)
    calls = 0

    async def broken():
        nonlocal calls
        calls += 1
        raise RuntimeError("offline")

    async def available():
        nonlocal calls
        calls += 1
        return "ok"

    for _ in range(2):
        try:
            asyncio.run(breaker.call(broken))
        except RuntimeError:
            pass
    try:
        asyncio.run(breaker.call(available))
    except gateway.CircuitOpenError:
        pass
    else:
        raise AssertionError("open circuit must fast reject")
    assert calls == 2


def test_rate_limiter_expires_old_requests():
    limiter = gateway.RateLimiter(1)
    assert limiter.allow("/events")
    assert not limiter.allow("/events")
    limiter.hits["/events"][0] -= 2.0
    assert limiter.allow("/events")


def test_invalid_notification_payload():
    async def send():
        transport = httpx.ASGITransport(app=notifications.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.post("/notify", json={"event": "", "order_id": ""})

    response = asyncio.run(send())
    assert response.status_code == 422
