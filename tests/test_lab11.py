"""Deterministic checks for resilience and HTTP integration (no cluster needed)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from prometheus_client import REGISTRY

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.gateway import main as g
from app.notifications import main as n

pytestmark = pytest.mark.asyncio


def metric(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def status_error(code):
    response = httpx.Response(code, request=httpx.Request("POST", "http://payments/charge"))
    return httpx.HTTPStatusError("injected", request=response.request, response=response)


async def test_retry_immediate_success():
    f = AsyncMock(return_value=42)
    assert await g.call_with_retry(f, "immediate") == 42
    assert f.await_count == 1
    assert metric("gateway_retry_total", target="immediate", result="retried") == 0


@pytest.mark.parametrize("error", [status_error(500), status_error(503), status_error(408),
                                       status_error(429), httpx.ReadTimeout("timeout"),
                                       httpx.ConnectError("connection")])
async def test_transient_retry_and_metrics(error, monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(g.asyncio, "sleep", sleep)
    target = f"transient-{type(error).__name__}-{getattr(error, 'response', None)}"
    before = metric("gateway_retry_total", target=target, result="retried")
    f = AsyncMock(side_effect=[error, "ok"])
    assert await g.call_with_retry(f, target) == "ok"
    assert f.await_count == 2
    assert metric("gateway_retry_total", target=target, result="retried") == before + 1
    assert metric("gateway_retry_total", target=target, result="succeeded_after_retry") >= 1
    sleep.assert_awaited_once()


@pytest.mark.parametrize("code", [400, 401, 404, 422])
async def test_non_retryable(code):
    f = AsyncMock(side_effect=status_error(code))
    target = f"nonretry-{code}"
    with pytest.raises(httpx.HTTPStatusError):
        await g.call_with_retry(f, target)
    assert f.await_count == 1
    assert metric("gateway_retry_total", target=target, result="non_retryable") == 1


async def test_unexpected_exception_fails_fast():
    f = AsyncMock(side_effect=ValueError("bug"))
    with pytest.raises(ValueError):
        await g.call_with_retry(f, "unexpected")
    assert f.await_count == 1


async def test_exact_attempts_backoff_jitter_and_exhaustion(monkeypatch):
    sleep = AsyncMock()
    jitter_calls = []
    monkeypatch.setattr(g.asyncio, "sleep", sleep)
    monkeypatch.setattr(g, "RETRY_BASE_DELAY_MS", 100)
    def jitter(low, high):
        jitter_calls.append((low, high))
        return 0.05
    monkeypatch.setattr(g.random, "uniform", jitter)
    error = status_error(500)
    f = AsyncMock(side_effect=error)
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await g.call_with_retry(f, "exhaustion", max_retries=3)
    assert caught.value is error
    assert f.await_count == 3
    assert [c.args[0] for c in sleep.await_args_list] == pytest.approx([0.15, 0.25])
    assert jitter_calls == [(0, 0.1), (0, 0.1)]
    assert metric("gateway_retry_total", target="exhaustion", result="retried") == 2
    assert metric("gateway_retry_total", target="exhaustion", result="exhausted") == 1


async def test_retry_invalid_attempt_count():
    with pytest.raises(ValueError):
        await g.call_with_retry(AsyncMock(), "invalid", max_retries=0)


async def test_breaker_opens_recovers_and_single_probe(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: now[0]))
    cb = g.CircuitBreaker(2, 30)
    assert cb.state == cb.CLOSED
    fail = AsyncMock(side_effect=RuntimeError("down"))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(fail)
    assert cb.state == cb.OPEN
    downstream = AsyncMock()
    with pytest.raises(g.CircuitOpenError):
        await cb.call(downstream)
    downstream.assert_not_awaited()
    now[0] += 30
    entered, release = asyncio.Event(), asyncio.Event()
    async def probe():
        entered.set()
        await release.wait()
        return "recovered"
    task = asyncio.create_task(cb.call(probe))
    await entered.wait()
    assert cb.state == cb.HALF_OPEN
    with pytest.raises(g.CircuitOpenError):
        await cb.call(downstream)
    release.set()
    assert await task == "recovered"
    assert cb.state == cb.CLOSED and cb.failures == 0


async def test_breaker_failed_probe_restarts_cooldown(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: now[0]))
    cb = g.CircuitBreaker(1, 30)
    fail = AsyncMock(side_effect=RuntimeError())
    for t in (100.0, 130.0):
        now[0] = t
        with pytest.raises(RuntimeError):
            await cb.call(fail)
        assert cb.state == cb.OPEN and cb.opened_at == t
    now[0] = 140
    with pytest.raises(g.CircuitOpenError):
        await cb.call(fail)
    assert fail.await_count == 2


async def test_breaker_success_resets_consecutive_failures():
    cb = g.CircuitBreaker(2, 30)
    with pytest.raises(RuntimeError):
        await cb.call(AsyncMock(side_effect=RuntimeError()))
    await cb.call(AsyncMock(return_value=True))
    assert cb.failures == 0 and cb.state == cb.CLOSED


async def test_breaker_late_success_cannot_close_open_circuit():
    cb = g.CircuitBreaker(1, 30)
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow():
        entered.set()
        await release.wait()
    task = asyncio.create_task(cb.call(slow))
    await entered.wait()
    with pytest.raises(RuntimeError):
        await cb.call(AsyncMock(side_effect=RuntimeError()))
    release.set()
    await task
    assert cb.state == cb.OPEN


async def test_cancelled_probe_does_not_strand_half_open():
    cb = g.CircuitBreaker(1, 0)
    cb._transition(cb.OPEN)
    entered = asyncio.Event()
    async def probe():
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(cb.call(probe))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cb.state == cb.OPEN


async def test_rate_window_expiry_and_independent_keys(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(g, "time", SimpleNamespace(monotonic=lambda: now[0]))
    limiter = g.RateLimiter(2)
    assert limiter.allow("/events") and limiter.allow("/events")
    assert not limiter.allow("/events")
    assert limiter.allow("/reserve/{id}/pay")
    now[0] += 1.001
    assert limiter.allow("/events")
    assert len(limiter.hits["/events"]) == 1
    assert g._normalize_path("/reserve/12345678-abcd/pay") == "/reserve/{id}/pay"
    assert g._normalize_path("/events/123/reserve") == "/events/{id}/reserve"


async def test_bulkhead_concurrency_rejection_and_gauge():
    bulkhead = g.Bulkhead("test-pool", 2, 0.02)
    entered, release = asyncio.Event(), asyncio.Event()
    active = peak = 0
    async def slow():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        await release.wait()
        active -= 1
        return "done"
    tasks = [asyncio.create_task(bulkhead.call(slow)) for _ in range(2)]
    await entered.wait()
    assert metric("gateway_bulkhead_in_flight", target="test-pool") == 2
    with pytest.raises(g.BulkheadFullError):
        await bulkhead.call(slow)
    assert metric("gateway_bulkhead_rejections_total", target="test-pool") == 1
    release.set()
    assert await asyncio.gather(*tasks) == ["done", "done"]
    assert peak == 2
    assert metric("gateway_bulkhead_in_flight", target="test-pool") == 0
    assert await bulkhead.call(AsyncMock(return_value="reuse")) == "reuse"


@pytest.mark.parametrize("exception", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_bulkhead_releases_after_error_or_cancellation(exception):
    bulkhead = g.Bulkhead(f"release-{type(exception).__name__}", 1, 0.02)
    with pytest.raises(type(exception)):
        await bulkhead.call(AsyncMock(side_effect=exception))
    assert await bulkhead.call(AsyncMock(return_value=42)) == 42
    assert metric("gateway_bulkhead_in_flight", target=bulkhead.name) == 0


async def test_notifications_endpoints_injection_validation_and_metrics(monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=n.app), base_url="http://notify") as c:
        assert (await c.get("/health")).json()["status"] == "healthy"
        body = {"event": "order_confirmed", "order_id": "order-test"}
        before_success = metric("notifications_notify_total", result="success")
        assert (await c.post("/notify", json=body)).json()["status"] == "sent"
        assert metric("notifications_notify_total", result="success") == before_success + 1
        monkeypatch.setattr(n, "NOTIFY_FAILURE_RATE", 1.0)
        before_failed = metric("notifications_notify_total", result="failed")
        assert (await c.post("/notify", json=body)).status_code == 500
        assert metric("notifications_notify_total", result="failed") == before_failed + 1
        assert (await c.post("/notify", json={})).status_code == 422
        text = (await c.get("/metrics")).text
        for name in ("notifications_requests_total", "notifications_request_duration_seconds", "notifications_notify_total"):
            assert name in text
        monkeypatch.setattr(n, "NOTIFY_FAILURE_RATE", 0.0)
        monkeypatch.setattr(n, "NOTIFY_LATENCY_MS", 300)
        sleep = AsyncMock()
        monkeypatch.setattr(n.asyncio, "sleep", sleep)
        assert (await c.post("/notify", json=body)).status_code == 200
        sleep.assert_awaited_once_with(0.3)


async def test_gateway_notify_failure_is_background_and_health_noncritical(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def upstream(request):
        if request.url.host == "notify":
            if request.url.path == "/health":
                return httpx.Response(500)
            entered.set()
            await release.wait()
            return httpx.Response(500)
        if request.url.path == "/health":
            return httpx.Response(200)
        if request.url.path == "/charge":
            return httpx.Response(200, json={"payment_ref": "PAY-test"})
        return httpx.Response(200, json={"status": "confirmed"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as downstream:
        monkeypatch.setattr(g, "client", downstream)
        monkeypatch.setattr(g, "NOTIFICATIONS_URL", "http://notify")
        monkeypatch.setattr(g, "payments_cb", g.CircuitBreaker(5, 30))
        monkeypatch.setattr(g, "payments_bulkhead", g.Bulkhead("http-integration", 10, 0.5))
        monkeypatch.setattr(g, "rate_limiter", g.RateLimiter(10))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=g.app), base_url="http://gateway") as c:
            response = await asyncio.wait_for(c.post("/reserve/abc-def/pay"), 0.5)
            assert response.status_code == 200
            await asyncio.wait_for(entered.wait(), 0.5)
            assert not release.is_set()  # response returned while notify still waits
            health = await c.get("/health")
            assert health.status_code == 200
            assert health.json()["checks"]["notifications"] == "degraded"
            release.set()
            await asyncio.gather(*g.notification_tasks)


async def test_rate_limit_http_header_and_exemptions(monkeypatch):
    monkeypatch.setattr(g, "rate_limiter", g.RateLimiter(1))
    upstream = httpx.MockTransport(lambda req: httpx.Response(200, json=[]))
    async with httpx.AsyncClient(transport=upstream) as downstream:
        monkeypatch.setattr(g, "client", downstream)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=g.app), base_url="http://gateway") as c:
            assert (await c.get("/events")).status_code == 200
            limited = await c.get("/events")
            assert limited.status_code == 429 and limited.headers["Retry-After"] == "1"
            for _ in range(3):
                assert (await c.get("/metrics")).status_code == 200
                assert (await c.get("/health")).status_code == 200


@pytest.mark.parametrize("error,detail", [(g.BulkheadFullError(), "bulkhead full"),
                                            (g.CircuitOpenError(), "circuit open")])
async def test_pay_maps_admission_errors_to_503(monkeypatch, error, detail):
    monkeypatch.setattr(g.payments_bulkhead, "call", AsyncMock(side_effect=error))
    with pytest.raises(g.HTTPException) as caught:
        await g.pay_reservation("test")
    assert caught.value.status_code == 503 and detail in caught.value.detail


async def test_bulkhead_cancelled_waiter_never_releases_another_call_slot():
    bulkhead = g.Bulkhead("cancelled-waiter", 1, 1)
    entered, release = asyncio.Event(), asyncio.Event()
    async def occupied():
        entered.set()
        await release.wait()
    owner = asyncio.create_task(bulkhead.call(occupied))
    await entered.wait()
    downstream = AsyncMock()
    waiter = asyncio.create_task(bulkhead.call(downstream))
    await asyncio.sleep(0.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert metric("gateway_bulkhead_in_flight", target="cancelled-waiter") == 1
    downstream.assert_not_awaited()
    release.set()
    await owner
    assert await bulkhead.call(AsyncMock(return_value=True)) is True
    assert metric("gateway_bulkhead_in_flight", target="cancelled-waiter") == 0


async def test_composition_counts_one_failure_and_holds_slot_during_backoff(monkeypatch):
    cb = g.CircuitBreaker(2, 30)
    bulkhead = g.Bulkhead("composition", 1, 0.01)
    async def sleep_during_retry(delay):
        assert metric("gateway_bulkhead_in_flight", target="composition") == 1
        assert cb.failures == 0  # inner attempts have not reached the breaker
    monkeypatch.setattr(g.asyncio, "sleep", sleep_during_retry)
    charge = AsyncMock(side_effect=status_error(500))
    with pytest.raises(httpx.HTTPStatusError):
        await bulkhead.call(lambda: cb.call(lambda: g.call_with_retry(charge, "composition")))
    assert charge.await_count == 3
    assert cb.failures == 1 and cb.state == cb.CLOSED
    assert metric("gateway_bulkhead_in_flight", target="composition") == 0


async def test_bulkhead_rejection_does_not_increment_breaker_failures():
    cb = g.CircuitBreaker(2, 30)
    bulkhead = g.Bulkhead("admission-composition", 1, 0.01)
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow_charge():
        entered.set()
        await release.wait()
    owner = asyncio.create_task(bulkhead.call(lambda: cb.call(slow_charge)))
    await entered.wait()
    with pytest.raises(g.BulkheadFullError):
        await bulkhead.call(lambda: cb.call(AsyncMock()))
    assert cb.failures == 0
    release.set()
    await owner
    assert cb.failures == 0
