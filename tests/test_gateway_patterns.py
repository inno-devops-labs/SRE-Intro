"""Run with gateway dependencies installed: python -m unittest discover -s tests."""
import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import httpx

# Supports both the repository and the gateway Docker image.
path = Path(__file__).resolve().parents[1] / 'app/gateway/main.py'
if not path.exists():
    path = Path('/app/main.py')
spec = importlib.util.spec_from_file_location('gateway', path)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


def status_error(status):
    response = httpx.Response(status, request=httpx.Request('POST', 'http://payments/charge'))
    return httpx.HTTPStatusError('test', request=response.request, response=response)


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_errors_recover_with_backoff(self):
        for error in [httpx.ConnectError('down'), httpx.ReadTimeout('slow'),
                      status_error(500), status_error(408), status_error(429)]:
            with self.subTest(error=error), patch.object(gateway.asyncio, 'sleep', new_callable=AsyncMock) as sleep, patch.object(gateway.random, 'uniform', return_value=0):
                call = AsyncMock(side_effect=[error, error, 'ok'])
                self.assertEqual(await gateway.call_with_retry(call, 'test', 3), 'ok')
                self.assertEqual(call.await_count, 3)
                self.assertEqual([c.args[0] for c in sleep.await_args_list], [0.1, 0.2])

    async def test_non_retryable_is_not_repeated(self):
        for error in [status_error(404), status_error(422), ValueError('bug')]:
            call = AsyncMock(side_effect=error)
            with self.assertRaises(type(error)):
                await gateway.call_with_retry(call, 'test', 3)
            self.assertEqual(call.await_count, 1)

    async def test_exhaustion_raises_last_error(self):
        error = status_error(503)
        call = AsyncMock(side_effect=error)
        with patch.object(gateway.asyncio, 'sleep', new_callable=AsyncMock):
            with self.assertRaises(httpx.HTTPStatusError) as caught:
                await gateway.call_with_retry(call, 'test', 3)
        self.assertIs(caught.exception, error)
        self.assertEqual(call.await_count, 3)

    async def test_cancellation_is_not_retried(self):
        call = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await gateway.call_with_retry(call, 'test')
        self.assertEqual(call.await_count, 1)


class CircuitTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_fast_fail_and_recovery(self):
        cb = gateway.CircuitBreaker(2, 30)
        call = AsyncMock(side_effect=RuntimeError('down'))
        with patch.object(gateway.time, 'time', return_value=100):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    await cb.call(call)
            with self.assertRaises(gateway.CircuitOpenError):
                await cb.call(call)
        self.assertEqual(call.await_count, 2)
        with patch.object(gateway.time, 'time', return_value=131):
            self.assertEqual(await cb.call(AsyncMock(return_value='ok')), 'ok')
        self.assertEqual((cb.state, cb.failures), (cb.CLOSED, 0))

    async def test_failed_half_open_probe_reopens(self):
        cb = gateway.CircuitBreaker(1, 0)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                await cb.call(AsyncMock(side_effect=RuntimeError('down')))
            self.assertEqual(cb.state, cb.OPEN)

    async def test_success_resets_consecutive_failures(self):
        cb = gateway.CircuitBreaker(2, 30)
        with self.assertRaises(RuntimeError):
            await cb.call(AsyncMock(side_effect=RuntimeError('down')))
        await cb.call(AsyncMock(return_value='ok'))
        self.assertEqual(cb.failures, 0)


class RateLimitTests(unittest.TestCase):
    def test_limit_expiration_and_independent_paths(self):
        limiter = gateway.RateLimiter(2)
        with patch.object(gateway.time, 'time', return_value=100):
            self.assertTrue(limiter.allow('/events'))
            self.assertTrue(limiter.allow('/events'))
            self.assertFalse(limiter.allow('/events'))
            self.assertTrue(limiter.allow('/reserve'))
        with patch.object(gateway.time, 'time', return_value=101.01):
            self.assertTrue(limiter.allow('/events'))
            self.assertEqual(len(limiter.hits['/events']), 1)


class BulkheadTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_timeout_and_release(self):
        bulkhead = gateway.Bulkhead('test-capacity', 1, .02)
        entered, finish = asyncio.Event(), asyncio.Event()
        async def hold():
            entered.set()
            await finish.wait()
        task = asyncio.create_task(bulkhead.call(hold))
        await entered.wait()
        rejected = AsyncMock()
        with self.assertRaises(gateway.BulkheadFullError):
            await bulkhead.call(rejected)
        rejected.assert_not_called()
        self.assertEqual(gateway.BULKHEAD_IN_FLIGHT.labels('test-capacity')._value.get(), 1)
        self.assertEqual(gateway.BULKHEAD_REJECTIONS.labels('test-capacity')._value.get(), 1)
        finish.set()
        await task
        self.assertEqual(await bulkhead.call(AsyncMock(return_value='ok')), 'ok')
        self.assertEqual(gateway.BULKHEAD_IN_FLIGHT.labels('test-capacity')._value.get(), 0)

    async def test_exception_and_cancellation_release_slot(self):
        bulkhead = gateway.Bulkhead('test-release', 1, .1)
        for error in [RuntimeError('failed'), asyncio.CancelledError()]:
            with self.assertRaises(type(error)):
                await bulkhead.call(AsyncMock(side_effect=error))
            self.assertEqual(await bulkhead.call(AsyncMock(return_value='ok')), 'ok')
        self.assertEqual(gateway.BULKHEAD_IN_FLIGHT.labels('test-release')._value.get(), 0)

    async def test_cancelled_waiter_does_not_take_slot(self):
        bulkhead = gateway.Bulkhead('test-waiter', 1, 1)
        await bulkhead.semaphore.acquire()
        call = AsyncMock()
        waiter = asyncio.create_task(bulkhead.call(call))
        await asyncio.sleep(.01)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        bulkhead.semaphore.release()
        self.assertEqual(await bulkhead.call(AsyncMock(return_value='ok')), 'ok')
        call.assert_not_called()
