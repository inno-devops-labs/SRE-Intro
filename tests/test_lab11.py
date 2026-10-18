"""Run with: python -m unittest discover -s tests -v."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from app.gateway import main as g
from app.notifications import main as n


def status_error(status):
    response = httpx.Response(status, request=httpx.Request('POST', 'http://payments/charge'))
    return httpx.HTTPStatusError('injected', request=response.request, response=response)


class Patterns(unittest.IsolatedAsyncioTestCase):
    async def test_retry_transient_statuses_and_transport(self):
        for exc in [status_error(s) for s in (408, 429, 500, 503)] + [httpx.ConnectError('x'), httpx.ReadTimeout('x')]:
            with self.subTest(exc=exc), patch.object(g.asyncio, 'sleep', new_callable=AsyncMock) as sleep, patch.object(g.random, 'uniform', return_value=.05):
                f = AsyncMock(side_effect=[exc, exc, 'ok'])
                self.assertEqual(await g.call_with_retry(f, 'test'), 'ok')
                self.assertEqual(sleep.await_count, 2)
                self.assertAlmostEqual(sleep.await_args_list[0].args[0], .15)
                self.assertAlmostEqual(sleep.await_args_list[1].args[0], .25)

    async def test_retry_rejects_permanent_and_unknown_errors(self):
        for exc in [status_error(404), status_error(422), ValueError('x'), g.CircuitOpenError()]:
            f = AsyncMock(side_effect=exc)
            with self.assertRaises(type(exc)):
                await g.call_with_retry(f, 'test')
            self.assertEqual(f.await_count, 1)

    async def test_retry_exhaustion_and_invalid_attempts(self):
        f = AsyncMock(side_effect=httpx.ConnectError('x'))
        with patch.object(g.asyncio, 'sleep', new_callable=AsyncMock):
            with self.assertRaises(httpx.ConnectError):
                await g.call_with_retry(f, 'test', 3)
        self.assertEqual(f.await_count, 3)
        with self.assertRaises(ValueError):
            await g.call_with_retry(f, 'test', 0)

    async def test_breaker_open_recovery_and_single_probe(self):
        cb = g.CircuitBreaker(2, 30, 'test')
        fail = AsyncMock(side_effect=ValueError('x'))
        for _ in range(2):
            with self.assertRaises(ValueError):
                await cb.call(fail)
        with self.assertRaises(g.CircuitOpenError):
            await cb.call(fail)
        self.assertEqual(fail.await_count, 2)
        cb.opened_at -= 31
        entered, release = asyncio.Event(), asyncio.Event()
        async def probe():
            entered.set()
            await release.wait()
            return 'ok'
        t = asyncio.create_task(cb.call(probe))
        await entered.wait()
        with self.assertRaises(g.CircuitOpenError):
            await cb.call(fail)
        release.set()
        self.assertEqual(await t, 'ok')
        self.assertEqual((cb.state, cb.failures), (cb.CLOSED, 0))

    async def test_failed_and_cancelled_probe_reopen(self):
        for cancel in (False, True):
            cb = g.CircuitBreaker(1, 0, 'test')
            cb._transition(cb.OPEN)
            exc = asyncio.CancelledError() if cancel else ValueError()
            with self.assertRaises(type(exc)):
                await cb.call(AsyncMock(side_effect=exc))
            self.assertEqual(cb.state, cb.OPEN)

    async def test_stale_success_cannot_close_open_breaker(self):
        cb = g.CircuitBreaker(1, 30, 'test')
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
        t = asyncio.create_task(cb.call(slow))
        await entered.wait()
        with self.assertRaises(ValueError):
            await cb.call(AsyncMock(side_effect=ValueError()))
        release.set()
        await t
        self.assertEqual(cb.state, cb.OPEN)

    async def test_bulkhead_timeout_cancellation_and_release(self):
        bh = g.Bulkhead('test', 1, .01)
        entered = asyncio.Event()
        async def hang():
            entered.set()
            await asyncio.Event().wait()
        t = asyncio.create_task(bh.call(hang))
        await entered.wait()
        f = AsyncMock(return_value='ok')
        with self.assertRaises(g.BulkheadFullError):
            await bh.call(f)
        f.assert_not_awaited()
        t.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await t
        self.assertEqual(await bh.call(f), 'ok')
        with self.assertRaises(ValueError):
            await bh.call(AsyncMock(side_effect=ValueError()))
        self.assertEqual(await bh.call(f), 'ok')
        self.assertEqual(g.BULKHEAD_IN_FLIGHT.labels('test')._value.get(), 0)

    def test_limiter_boundary_and_independent_keys(self):
        rl = g.RateLimiter(2)
        with patch.object(g.time, 'time', return_value=100):
            self.assertTrue(rl.allow('/events'))
            self.assertTrue(rl.allow('/events'))
            self.assertFalse(rl.allow('/events'))
            self.assertTrue(rl.allow('/other'))
        with patch.object(g.time, 'time', return_value=101):
            self.assertFalse(rl.allow('/events'))
        with patch.object(g.time, 'time', return_value=101.001):
            self.assertTrue(rl.allow('/events'))

    async def test_http_rate_limit_headers_and_exemptions(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=g.app), base_url='http://test') as c:
            with patch.object(g, 'rate_limiter', g.RateLimiter(1)):
                await c.get('/unknown')
                r = await c.get('/unknown')
                self.assertEqual(r.status_code, 429)
                self.assertEqual(r.headers['Retry-After'], '1')
                self.assertGreater(g.REQUEST_COUNT.labels('GET', '/unknown', 429)._value.get(), 0)
                self.assertEqual((await c.get('/metrics')).status_code, 200)

    async def test_notifications_faults_validation_and_metrics(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=n.app), base_url='http://test') as c:
            body = {'event': 'order_confirmed', 'order_id': 'abc'}
            self.assertEqual((await c.post('/notify', json={})).status_code, 422)
            with patch.object(n, 'NOTIFY_FAILURE_RATE', 1):
                self.assertEqual((await c.post('/notify', json=body)).status_code, 500)
                self.assertEqual((await c.get('/health')).status_code, 200)
            with patch.object(n, 'NOTIFY_FAILURE_RATE', 0), patch.object(n, 'NOTIFY_LATENCY_MS', 300), patch.object(n.asyncio, 'sleep', new_callable=AsyncMock) as sleep:
                self.assertEqual((await c.post('/notify', json=body)).json()['status'], 'sent')
                sleep.assert_awaited_once_with(.3)
            metrics = (await c.get('/metrics')).text
            for name in ('notifications_requests_total', 'notifications_request_duration_seconds', 'notifications_notify_total'):
                self.assertIn(name, metrics)

    async def test_checkout_does_not_wait_for_notification(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def notify(_):
            entered.set()
            await release.wait()
        async def downstream(request):
            return httpx.Response(200, json={'payment_ref': 'p', 'status': 'confirmed'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(downstream)) as c:
            with patch.object(g, 'client', c), patch.object(g, '_notify_order_confirmed', notify):
                result = await asyncio.wait_for(g.pay_reservation('abc'), .2)
                self.assertEqual(result['status'], 'confirmed')
                await entered.wait()
                self.assertTrue(g.notification_tasks)
                release.set()
                await asyncio.gather(*g.notification_tasks)

    async def test_notification_failure_does_not_gate_health(self):
        async def downstream(request):
            if request.url.host == 'notifications':
                return httpx.Response(500)
            return httpx.Response(200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(downstream)) as c:
            with patch.object(g, 'client', c), patch.object(g, 'NOTIFICATIONS_URL', 'http://notifications:8083'):
                await g._notify_order_confirmed('abc')
                self.assertEqual((await g.health()).status_code, 200)
