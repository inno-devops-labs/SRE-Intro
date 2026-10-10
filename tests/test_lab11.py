import asyncio
import importlib.util
import pathlib

import httpx
import pytest

ROOT = pathlib.Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway = load("lab11_gateway", "app/gateway/main.py")
notifications = load("lab11_notifications", "app/notifications/main.py")


@pytest.mark.asyncio
async def test_retry_recovers_and_rejects_non_retryable(monkeypatch):
    monkeypatch.setattr(gateway, "RETRY_BASE_DELAY_MS", 0)
    attempts = 0

    async def transient():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary")
        return "ok"

    assert await gateway.call_with_retry(transient, "test", 3) == "ok"
    assert attempts == 3

    async def bad_request():
        request = httpx.Request("GET", "http://test")
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("not found", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await gateway.call_with_retry(bad_request, "test", 3)


@pytest.mark.asyncio
async def test_circuit_opens_fast_and_recovers(monkeypatch):
    now = 100.0
    monkeypatch.setattr(gateway.time, "time", lambda: now)
    cb = gateway.CircuitBreaker(2, 10, "test")

    async def fail():
        raise RuntimeError("upstream")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await cb.call(fail)
    assert cb.state == cb.OPEN
    with pytest.raises(gateway.CircuitOpenError):
        await cb.call(fail)

    now = 111.0
    assert await cb.call(lambda: asyncio.sleep(0, result="ok")) == "ok"
    assert cb.state == cb.CLOSED


def test_rate_limiter_sliding_window(monkeypatch):
    now = 10.0
    monkeypatch.setattr(gateway.time, "time", lambda: now)
    limiter = gateway.RateLimiter(2)
    assert limiter.allow("/events")
    assert limiter.allow("/events")
    assert not limiter.allow("/events")
    now = 11.1
    assert limiter.allow("/events")


@pytest.mark.asyncio
async def test_bulkhead_rejects_and_releases():
    bulkhead = gateway.Bulkhead("test", 1, 0.01)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()

    occupant = asyncio.create_task(bulkhead.call(slow))
    await entered.wait()
    with pytest.raises(gateway.BulkheadFullError):
        await bulkhead.call(lambda: asyncio.sleep(0))
    release.set()
    await occupant
    assert await bulkhead.call(lambda: asyncio.sleep(0, result="ok")) == "ok"


@pytest.mark.asyncio
async def test_notifications_contract_and_metrics(monkeypatch):
    monkeypatch.setattr(notifications, "NOTIFY_FAILURE_RATE", 0.0)
    transport = httpx.ASGITransport(app=notifications.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/notify", json={"event": "order_confirmed", "order_id": "R-1"}
        )
        assert response.status_code == 200
        assert response.json() == {
            "status": "sent",
            "event": "order_confirmed",
            "order_id": "R-1",
        }
        health = (await client.get("/health")).json()
        assert health["status"] == "healthy"
        metrics = (await client.get("/metrics")).text
        assert "notifications_requests_total" in metrics
        assert "notifications_request_duration_seconds" in metrics
        assert 'notifications_notify_total{result="success"}' in metrics
