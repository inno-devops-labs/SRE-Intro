"""Run with: python -m unittest discover -s tests -p test_lab11_patterns.py -v."""

import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx

spec = importlib.util.spec_from_file_location("gateway_lab11", Path(__file__).resolve().parents[1] / "app/gateway/main.py")
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
notify_spec = importlib.util.spec_from_file_location("notifications_lab11", Path(__file__).resolve().parents[1] / "app/notifications/main.py")
notifications = importlib.util.module_from_spec(notify_spec)
notify_spec.loader.exec_module(notifications)


def http_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "http://payments/charge"))
    return httpx.HTTPStatusError("test", request=response.request, response=response)


class PatternTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_transient_success_and_metrics(self):
        calls = 0
        before = gateway.RETRY_TOTAL.labels("test-success", "retried")._value.get()
        async def operation():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise http_error(500)
            return "ok"
        with patch.object(gateway, "RETRY_BASE_DELAY_MS", 0):
            self.assertEqual(await gateway.call_with_retry(operation, "test-success"), "ok")
        self.assertEqual(calls, 3)
        self.assertEqual(gateway.RETRY_TOTAL.labels("test-success", "retried")._value.get() - before, 2)
        self.assertEqual(gateway.RETRY_TOTAL.labels("test-success", "succeeded_after_retry")._value.get(), 1)

    async def test_retry_classification_exhaustion_and_original_exception(self):
        for error, expected in [(http_error(404), 1), (http_error(422), 1),
                                (ValueError("unknown"), 1), (http_error(429), 3),
                                (http_error(408), 3), (http_error(503), 3),
                                (httpx.ConnectError("down"), 3),
                                (httpx.ReadTimeout("slow"), 3)]:
            calls = 0
            async def operation():
                nonlocal calls
                calls += 1
                raise error
            with patch.object(gateway, "RETRY_BASE_DELAY_MS", 0):
                with self.assertRaises(type(error)) as caught:
                    await gateway.call_with_retry(operation, "test-errors")
            self.assertIs(caught.exception, error)
            self.assertEqual(calls, expected)

    async def test_retry_cancellation_is_not_retried(self):
        calls = 0
        async def operation():
            nonlocal calls
            calls += 1
            raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await gateway.call_with_retry(operation, "cancel")
        self.assertEqual(calls, 1)

    async def test_circuit_cooldown_single_probe_and_recovery(self):
        cb = gateway.CircuitBreaker(2, 30, "test")
        calls = 0
        async def fail():
            nonlocal calls
            calls += 1
            raise http_error(500)
        with patch.object(cb, "_clock", return_value=100):
            for _ in range(2):
                with self.assertRaises(httpx.HTTPStatusError):
                    await cb.call(fail)
            with self.assertRaises(gateway.CircuitOpenError):
                await cb.call(fail)
        self.assertEqual(calls, 2)
        entered, release = asyncio.Event(), asyncio.Event()
        async def probe():
            entered.set()
            await release.wait()
            return "ok"
        with patch.object(cb, "_clock", return_value=131):
            task = asyncio.create_task(cb.call(probe))
            await entered.wait()
            self.assertEqual(cb.state, cb.HALF_OPEN)
            with self.assertRaises(gateway.CircuitOpenError):
                await cb.call(fail)
            release.set()
            self.assertEqual(await task, "ok")
        self.assertEqual((cb.state, cb.failures), (cb.CLOSED, 0))

    async def test_circuit_failed_half_open_reopens(self):
        cb = gateway.CircuitBreaker(1, 0, "test-half-fail")
        async def fail():
            raise RuntimeError("down")
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                await cb.call(fail)
            self.assertEqual(cb.state, cb.OPEN)

    async def test_stale_success_cannot_close_newly_open_circuit(self):
        cb = gateway.CircuitBreaker(1, 30, "concurrent")
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_success():
            entered.set()
            await release.wait()
        task = asyncio.create_task(cb.call(slow_success))
        await entered.wait()
        async def fail():
            raise RuntimeError("down")
        with self.assertRaises(RuntimeError):
            await cb.call(fail)
        release.set()
        await task
        self.assertEqual(cb.state, cb.OPEN)

    async def test_limiter_expiry_normalization_and_independent_keys(self):
        limiter = gateway.RateLimiter(1)
        with patch.object(limiter, "_clock", return_value=10):
            self.assertTrue(limiter.allow("/reserve/aaa-bbb/pay"))
            self.assertFalse(limiter.allow("/reserve/ccc-ddd/pay"))
            self.assertTrue(limiter.allow("/events"))
        with patch.object(limiter, "_clock", return_value=11):
            self.assertTrue(limiter.allow("/reserve/eee-fff/pay"))
        self.assertEqual(len(limiter.hits["/reserve/{id}/pay"]), 1)

    async def test_bulkhead_cap_timeout_exception_and_cancellation_release(self):
        bulkhead = gateway.Bulkhead("test-bulkhead", 1, 0.01)
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold():
            entered.set()
            await release.wait()
        task = asyncio.create_task(bulkhead.call(hold))
        await entered.wait()
        self.assertEqual(gateway.BULKHEAD_IN_FLIGHT.labels("test-bulkhead")._value.get(), 1)
        with self.assertRaises(gateway.BulkheadFullError):
            await bulkhead.call(hold)
        self.assertEqual(bulkhead.semaphore._value, 0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(bulkhead.semaphore._value, 1)
        async def fail():
            raise RuntimeError("test")
        with self.assertRaises(RuntimeError):
            await bulkhead.call(fail)
        self.assertEqual(bulkhead.semaphore._value, 1)
        self.assertEqual(gateway.BULKHEAD_IN_FLIGHT.labels("test-bulkhead")._value.get(), 0)

    async def test_bulkhead_cancelled_waiter_does_not_release_unowned_slot(self):
        bulkhead = gateway.Bulkhead("test-waiter", 1, 5)
        await bulkhead.semaphore.acquire()
        async def unused():
            self.fail("cancelled waiter must not run")
        task = asyncio.create_task(bulkhead.call(unused))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(bulkhead.semaphore._value, 0)
        bulkhead.semaphore.release()

    async def test_notify_http_failure_does_not_escape(self):
        class FakeClient:
            async def post(self, *args, **kwargs):
                return httpx.Response(500, request=httpx.Request("POST", "http://notifications/notify"))
        with patch.object(gateway, "client", FakeClient()), patch.object(gateway, "NOTIFICATIONS_URL", "http://notifications"):
            with self.assertLogs("gateway", "WARNING") as logs:
                await gateway._notify_order_confirmed("test-order")
            self.assertIn("500", logs.output[0])

    async def test_notifications_http_contract_and_fault_metrics(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=notifications.app), base_url="http://notifications") as client:
            before = notifications.NOTIFY_TOTAL.labels("failed")._value.get()
            with patch.object(notifications, "NOTIFY_FAILURE_RATE", 1.0):
                failed = await client.post("/notify", json={"event": "order_confirmed", "order_id": "test"})
                self.assertEqual(failed.status_code, 500)
                self.assertEqual(notifications.NOTIFY_TOTAL.labels("failed")._value.get() - before, 1)
            with patch.object(notifications, "NOTIFY_FAILURE_RATE", 0.0):
                success = await client.post("/notify", json={"event": "order_confirmed", "order_id": "test"})
                self.assertEqual(success.json()["status"], "sent")
                health = await client.get("/health")
                self.assertEqual(health.status_code, 200)
                metrics = (await client.get("/metrics")).text
                self.assertIn("notifications_notify_total", metrics)
                self.assertIn("notifications_request_duration_seconds_bucket", metrics)
                self.assertIn('notifications_requests_total{method="POST",path="/notify",status="500"}', metrics)

    async def test_rate_limit_exempts_metrics_and_health(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://gateway") as client:
            # Stub dependencies, but exercise actual middleware and response headers.
            class Dependencies:
                async def get(self, url, **kwargs):
                    return httpx.Response(200, json=[], request=httpx.Request("GET", url))
            with patch.object(gateway, "rate_limiter", gateway.RateLimiter(1)), patch.object(gateway, "client", Dependencies()):
                self.assertEqual((await client.get("/events")).status_code, 200)
                rejected = await client.get("/events")
                self.assertEqual(rejected.status_code, 429)
                self.assertEqual(rejected.headers["retry-after"], "1")
                self.assertEqual((await client.get("/metrics")).status_code, 200)
                self.assertEqual((await client.get("/health")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
