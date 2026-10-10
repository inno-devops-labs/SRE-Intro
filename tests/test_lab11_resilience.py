"""Deterministic Lab 11 tests; no cluster or downstream services required."""

import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway = load_module("lab11_gateway", "app/gateway/main.py")
notifications = load_module("lab11_notifications", "app/notifications/main.py")


def status_error(status):
    request = httpx.Request("POST", "http://payments/charge")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("injected", request=request, response=response)


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_without_retry(self):
        func = AsyncMock(return_value="ok")
        with patch.object(gateway, "RETRY_TOTAL") as counter:
            self.assertEqual(await gateway.call_with_retry(func, "payments"), "ok")
            counter.labels.assert_not_called()
        func.assert_awaited_once()

    async def test_backoff_jitter_and_recovery(self):
        func = AsyncMock(side_effect=[status_error(500), status_error(429), "ok"])
        with (
            patch.object(gateway, "RETRY_TOTAL") as counter,
            patch.object(gateway, "RETRY_BASE_DELAY_MS", 100),
            patch.object(gateway.random, "uniform", return_value=0.05),
            patch.object(gateway.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            self.assertEqual(await gateway.call_with_retry(func, "payments", 3), "ok")
            self.assertEqual(func.await_count, 3)
            delays = [call.args[0] for call in sleep.await_args_list]
            self.assertAlmostEqual(delays[0], 0.15)
            self.assertAlmostEqual(delays[1], 0.25)
            results = [call.args[1] for call in counter.labels.call_args_list]
            self.assertEqual(results, [
                "retried", "retried", "succeeded_after_retry",
            ])
            self.assertEqual(counter.labels.return_value.inc.call_count, 3)

    async def test_all_retryable_error_types(self):
        errors = [
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("unreachable"),
            status_error(408), status_error(429),
            status_error(500), status_error(503), status_error(599),
        ]
        for error in errors:
            with self.subTest(error=repr(error)):
                func = AsyncMock(side_effect=[error, "ok"])
                with (
                    patch.object(gateway, "RETRY_TOTAL"),
                    patch.object(gateway.asyncio, "sleep", new_callable=AsyncMock),
                ):
                    self.assertEqual(
                        await gateway.call_with_retry(func, "payments", 2), "ok"
                    )
                self.assertEqual(func.await_count, 2)

    async def test_non_retryable_errors_fail_immediately(self):
        for error in [
            status_error(400), status_error(404), status_error(422),
            ValueError("bug"), gateway.CircuitOpenError("OPEN"),
        ]:
            with self.subTest(error=repr(error)):
                func = AsyncMock(side_effect=error)
                with (
                    patch.object(gateway, "RETRY_TOTAL") as counter,
                    patch.object(gateway.asyncio, "sleep", new_callable=AsyncMock) as sleep,
                ):
                    with self.assertRaises(type(error)):
                        await gateway.call_with_retry(func, "payments")
                    func.assert_awaited_once()
                    sleep.assert_not_awaited()
                    counter.labels.assert_called_once_with("payments", "non_retryable")
                    counter.labels.return_value.inc.assert_called_once()

    async def test_exhaustion_is_three_total_attempts(self):
        func = AsyncMock(side_effect=status_error(500))
        with (
            patch.object(gateway, "RETRY_TOTAL") as counter,
            patch.object(gateway.asyncio, "sleep", new_callable=AsyncMock) as sleep,
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                await gateway.call_with_retry(func, "payments", 3)
            self.assertEqual(func.await_count, 3)
            self.assertEqual(sleep.await_count, 2)
            self.assertEqual(
                [call.args[1] for call in counter.labels.call_args_list],
                ["retried", "retried", "exhausted"],
            )

    async def test_invalid_attempt_count(self):
        func = AsyncMock()
        with self.assertRaises(ValueError):
            await gateway.call_with_retry(func, "payments", 0)
        func.assert_not_awaited()


class CircuitBreakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_fast_fail_half_open_and_close(self):
        cb = gateway.CircuitBreaker(2, 30, "test")
        fail = AsyncMock(side_effect=RuntimeError("down"))
        success = AsyncMock(return_value="recovered")
        with (
            patch.object(gateway, "CB_STATE_TRANSITIONS") as counter,
            patch.object(gateway.time, "time", return_value=100) as clock,
        ):
            for expected in (cb.CLOSED, cb.OPEN):
                with self.assertRaises(RuntimeError):
                    await cb.call(fail)
                self.assertEqual(cb.state, expected)
            clock.return_value = 129
            with self.assertRaises(gateway.CircuitOpenError):
                await cb.call(success)
            success.assert_not_awaited()
            clock.return_value = 130
            self.assertEqual(await cb.call(success), "recovered")
            self.assertEqual(cb.state, cb.CLOSED)
            self.assertEqual(cb.failures, 0)
            self.assertEqual(
                [call.args[0] for call in counter.labels.call_args_list],
                [cb.OPEN, cb.HALF_OPEN, cb.CLOSED],
            )

    async def test_failed_half_open_probe_reopens(self):
        cb = gateway.CircuitBreaker(1, 30, "test")
        func = AsyncMock(side_effect=RuntimeError("still down"))
        with (
            patch.object(gateway, "CB_STATE_TRANSITIONS"),
            patch.object(gateway.time, "time", return_value=100) as clock,
        ):
            with self.assertRaises(RuntimeError):
                await cb.call(func)
            clock.return_value = 130
            with self.assertRaises(RuntimeError):
                await cb.call(func)
            self.assertEqual(cb.state, cb.OPEN)
            self.assertEqual(cb.opened_at, 130)

    async def test_retry_exhaustion_counts_as_one_cb_failure(self):
        cb = gateway.CircuitBreaker(5, 30, "test")
        charge = AsyncMock(side_effect=status_error(500))
        with (
            patch.object(gateway, "RETRY_TOTAL"),
            patch.object(gateway.asyncio, "sleep", new_callable=AsyncMock),
        ):
            with self.assertRaises(httpx.HTTPStatusError):
                await cb.call(
                    lambda: gateway.call_with_retry(charge, "payments", 3)
                )
        self.assertEqual(charge.await_count, 3)
        self.assertEqual(cb.failures, 1)
        self.assertEqual(cb.state, cb.CLOSED)


class RateLimiterTests(unittest.TestCase):
    def test_window_expiry_and_independent_keys(self):
        limiter = gateway.RateLimiter(2)
        with patch.object(gateway.time, "time", return_value=100) as clock:
            self.assertTrue(limiter.allow("/events"))
            self.assertTrue(limiter.allow("/events"))
            self.assertFalse(limiter.allow("/events"))
            self.assertTrue(limiter.allow("/reserve/{id}/pay"))
            clock.return_value = 101
            # The contract expires timestamps strictly older than the cutoff.
            self.assertFalse(limiter.allow("/events"))
            clock.return_value = 101.001
            self.assertTrue(limiter.allow("/events"))

    def test_uuid_paths_share_one_limit_key(self):
        first = gateway._normalize_path("/reserve/abc123-def456/pay")
        second = gateway._normalize_path("/reserve/fff999-aaa111/pay")
        self.assertEqual(first, "/reserve/{id}/pay")
        self.assertEqual(first, second)


class BulkheadTests(unittest.IsolatedAsyncioTestCase):
    async def test_cap_rejection_and_reuse(self):
        with (
            patch.object(gateway, "BULKHEAD_IN_FLIGHT") as gauge,
            patch.object(gateway, "BULKHEAD_REJECTIONS") as counter,
        ):
            bulkhead = gateway.Bulkhead("test", 1, 0.02)
            entered = asyncio.Event()
            release = asyncio.Event()

            async def slow():
                entered.set()
                await release.wait()
                return "ok"

            task = asyncio.create_task(bulkhead.call(slow))
            await asyncio.wait_for(entered.wait(), 1)
            rejected = AsyncMock()
            with self.assertRaises(gateway.BulkheadFullError):
                await bulkhead.call(rejected)
            rejected.assert_not_awaited()
            counter.labels.return_value.inc.assert_called_once()
            gauge.labels.return_value.inc.assert_called_once()
            release.set()
            self.assertEqual(await task, "ok")
            gauge.labels.return_value.dec.assert_called_once()
            self.assertEqual(
                await bulkhead.call(AsyncMock(return_value="reused")), "reused"
            )

    async def test_cancellation_releases_slot(self):
        with (
            patch.object(gateway, "BULKHEAD_IN_FLIGHT") as gauge,
            patch.object(gateway, "BULKHEAD_REJECTIONS"),
        ):
            bulkhead = gateway.Bulkhead("test", 1, 0.02)
            entered = asyncio.Event()

            async def blocked():
                entered.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(bulkhead.call(blocked))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            gauge.labels.return_value.dec.assert_called_once()
            self.assertEqual(
                await bulkhead.call(AsyncMock(return_value="ok")), "ok"
            )

    async def test_exception_releases_slot(self):
        with (
            patch.object(gateway, "BULKHEAD_IN_FLIGHT"),
            patch.object(gateway, "BULKHEAD_REJECTIONS"),
        ):
            bulkhead = gateway.Bulkhead("test", 1, 0.02)
            with self.assertRaises(ValueError):
                await bulkhead.call(AsyncMock(side_effect=ValueError("failed")))
            self.assertEqual(
                await bulkhead.call(AsyncMock(return_value="ok")), "ok"
            )


class NotificationsTests(unittest.TestCase):
    def test_delivery_failure_latency_and_metrics(self):
        with TestClient(notifications.app) as client:
            body = {"event": "order_confirmed", "order_id": "test-order"}
            with (
                patch.object(notifications, "NOTIFY_FAILURE_RATE", 0),
                patch.object(notifications, "NOTIFY_LATENCY_MS", 300),
                patch.object(
                    notifications.asyncio, "sleep", new_callable=AsyncMock
                ) as sleep,
            ):
                response = client.post("/notify", json=body)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"status": "sent", **body})
                sleep.assert_awaited_once_with(0.3)
            with (
                patch.object(notifications, "NOTIFY_FAILURE_RATE", 1),
                patch.object(notifications, "NOTIFY_LATENCY_MS", 0),
            ):
                self.assertEqual(client.post("/notify", json=body).status_code, 500)
                self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(client.post("/notify", json={}).status_code, 422)
            metrics = client.get("/metrics").text
            for name in (
                "notifications_requests_total",
                "notifications_request_duration_seconds",
                'notifications_notify_total{result="success"}',
                'notifications_notify_total{result="failed"}',
            ):
                self.assertIn(name, metrics)

    def test_metric_exposition_contains_bulkhead_metrics(self):
        metrics = generate_latest().decode()
        self.assertIn("gateway_bulkhead_in_flight", metrics)
        self.assertIn("gateway_bulkhead_rejections_total", metrics)


if __name__ == "__main__":
    unittest.main()
