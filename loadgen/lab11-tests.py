"""Run inside the gateway image: python < loadgen/lab11-tests.py."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import main as gateway


def status_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "http://payments/charge"))
    return httpx.HTTPStatusError("injected", request=response.request, response=response)


class PatternsTest(unittest.IsolatedAsyncioTestCase):
    async def test_retry_transient_errors(self):
        errors = [httpx.ConnectError("offline"), httpx.ReadTimeout("slow")]
        errors += [status_error(s) for s in (408, 429, 500, 503)]
        for error in errors:
            with self.subTest(error=error):
                call = AsyncMock(side_effect=[error, error, "ok"])
                with patch.object(gateway.asyncio, "sleep", new_callable=AsyncMock) as sleep:
                    with patch.object(gateway.random, "uniform", return_value=0.05):
                        self.assertEqual(await gateway.call_with_retry(call, "test"), "ok")
                self.assertEqual(call.await_count, 3)
                self.assertAlmostEqual(sleep.await_args_list[0].args[0], 0.15)
                self.assertAlmostEqual(sleep.await_args_list[1].args[0], 0.25)

    async def test_non_retryable(self):
        for error in [status_error(s) for s in (400, 404, 422)] + [ValueError("bad")]:
            call = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                await gateway.call_with_retry(call, "test")
            self.assertEqual(call.await_count, 1)

    async def test_exhausted(self):
        call = AsyncMock(side_effect=httpx.ConnectError("offline"))
        with patch.object(gateway.asyncio, "sleep", new_callable=AsyncMock):
            with self.assertRaises(httpx.ConnectError):
                await gateway.call_with_retry(call, "test", max_retries=3)
        self.assertEqual(call.await_count, 3)

    async def test_cancel_is_not_retried(self):
        call = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await gateway.call_with_retry(call, "test")
        self.assertEqual(call.await_count, 1)

    async def test_breaker_opens_and_recovers(self):
        breaker = gateway.CircuitBreaker(2, 30, "test")
        fail = AsyncMock(side_effect=RuntimeError("failed"))
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                await breaker.call(fail)
        self.assertEqual(breaker.state, breaker.OPEN)
        with self.assertRaises(gateway.CircuitOpenError):
            await breaker.call(fail)
        self.assertEqual(fail.await_count, 2)
        breaker.opened_at -= 31
        self.assertEqual(await breaker.call(AsyncMock(return_value="ok")), "ok")
        self.assertEqual(breaker.state, breaker.CLOSED)
        self.assertEqual(breaker.failures, 0)

    async def test_half_open_failure_and_single_probe(self):
        breaker = gateway.CircuitBreaker(1, 0, "test")
        breaker.state = breaker.OPEN
        entered, release = asyncio.Event(), asyncio.Event()

        async def probe():
            entered.set()
            await release.wait()
            raise RuntimeError("still down")

        task = asyncio.create_task(breaker.call(probe))
        await entered.wait()
        with self.assertRaises(gateway.CircuitOpenError):
            await breaker.call(AsyncMock())
        release.set()
        with self.assertRaises(RuntimeError):
            await task
        self.assertEqual(breaker.state, breaker.OPEN)

    async def test_limiter_expiry_and_separate_keys(self):
        limiter = gateway.RateLimiter(2)
        with patch.object(gateway.time, "time", return_value=100):
            self.assertTrue(limiter.allow("/events"))
            self.assertTrue(limiter.allow("/events"))
            self.assertFalse(limiter.allow("/events"))
            self.assertTrue(limiter.allow("/pay"))
        with patch.object(gateway.time, "time", return_value=101.01):
            self.assertTrue(limiter.allow("/events"))
            self.assertEqual(len(limiter.hits["/events"]), 1)

    async def test_bulkhead_capacity_timeout_and_cancel_release(self):
        bulkhead = gateway.Bulkhead("test", 1, 0.01)
        entered = asyncio.Event()

        async def slow():
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(bulkhead.call(slow))
        await entered.wait()
        with self.assertRaises(gateway.BulkheadFullError):
            await bulkhead.call(AsyncMock())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(await bulkhead.call(AsyncMock(return_value="released")), "released")

    async def test_bulkhead_releases_after_exception(self):
        bulkhead = gateway.Bulkhead("test", 1, 0.01)
        with self.assertRaises(ValueError):
            await bulkhead.call(AsyncMock(side_effect=ValueError))
        self.assertEqual(await bulkhead.call(AsyncMock(return_value=42)), 42)


unittest.main(verbosity=2)
