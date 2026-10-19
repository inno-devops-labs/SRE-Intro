"""Deterministic boundary, failure, cancellation and concurrency checks."""
import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import httpx

spec = importlib.util.spec_from_file_location('gateway', Path(__file__).parents[1] / 'app/gateway/main.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class ResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_retry_and_exhaustion(self):
        calls = 0
        async def transient():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise httpx.ConnectError('injected')
            return 'ok'
        with patch.object(g, 'RETRY_BASE_DELAY_MS', 0):
            self.assertEqual(await g.call_with_retry(transient, 'test'), 'ok')
            self.assertEqual(calls, 3)
            calls = 0
            with self.assertRaises(httpx.ConnectError):
                await g.call_with_retry(transient, 'test', 2)
            self.assertEqual(calls, 2)

    async def test_status_retry_classification(self):
        for status in [400, 404, 422, 408, 429, 500, 503]:
            calls = 0
            async def fail():
                nonlocal calls
                calls += 1
                response = httpx.Response(status, request=httpx.Request('POST', 'http://test'))
                response.raise_for_status()
            with patch.object(g, 'RETRY_BASE_DELAY_MS', 0):
                with self.assertRaises(httpx.HTTPStatusError):
                    await g.call_with_retry(fail, 'test')
            self.assertEqual(calls, 3 if status in [408, 429, 500, 503] else 1)

    async def test_circuit_open_recovery_and_single_probe(self):
        cb = g.CircuitBreaker(2, 30)
        async def fail(): raise httpx.ConnectError('down')
        for _ in range(2):
            with self.assertRaises(httpx.ConnectError): await cb.call(fail)
        with self.assertRaises(g.CircuitOpenError): await cb.call(fail)
        cb.opened_at -= 31
        entered, release = asyncio.Event(), asyncio.Event()
        async def probe():
            entered.set()
            await release.wait()
            return 42
        task = asyncio.create_task(cb.call(probe))
        await entered.wait()
        with self.assertRaises(g.CircuitOpenError): await cb.call(probe)
        release.set()
        self.assertEqual(await task, 42)
        self.assertEqual(cb.state, cb.CLOSED)
        self.assertEqual(cb.failures, 0)

    async def test_bulkhead_releases_on_cancel(self):
        bulk = g.Bulkhead('test', 1, .01)
        started = asyncio.Event()
        async def slow():
            started.set()
            await asyncio.sleep(10)
        task = asyncio.create_task(bulk.call(slow))
        await started.wait()
        with self.assertRaises(g.BulkheadFullError): await bulk.call(slow)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        async def fast(): return 'released'
        self.assertEqual(await bulk.call(fast), 'released')

    def test_sliding_window_and_separate_paths(self):
        limiter = g.RateLimiter(2)
        with patch.object(g.time, 'time', return_value=100):
            self.assertTrue(limiter.allow('/events'))
            self.assertTrue(limiter.allow('/events'))
            self.assertFalse(limiter.allow('/events'))
            self.assertTrue(limiter.allow('/pay'))
        with patch.object(g.time, 'time', return_value=101.001):
            self.assertTrue(limiter.allow('/events'))

if __name__ == '__main__': unittest.main()
