"""In-cluster QuickTicket load test for the Lab 10 traffic mix."""

import json
import random
from collections import Counter

from locust import HttpUser, between, events, task


status_counts = Counter()


@events.test_start.add_listener
def reset_counts(environment, **kwargs):
    status_counts.clear()


@events.request.add_listener
def count_response(request_type, name, response=None, exception=None, **kwargs):
    if response is None:
        status_counts["transport_error"] += 1
        return
    code = response.status_code
    status_counts[str(code)] += 1


@events.test_stop.add_listener
def report_counts(environment, **kwargs):
    print("HTTP_STATUS_COUNTS=" + json.dumps(dict(sorted(status_counts.items())), sort_keys=True), flush=True)
    total = environment.stats.total
    print("LOAD_SUMMARY=" + json.dumps({
        "requests": total.num_requests,
        "failures": total.num_failures,
        "rps": total.total_rps,
        "p50_ms": total.get_response_time_percentile(0.50),
        "p95_ms": total.get_response_time_percentile(0.95),
        "p99_ms": total.get_response_time_percentile(0.99),
    }, sort_keys=True), flush=True)


class QuickTicketUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(7)
    def list_events(self):
        self.client.get("/events")

    @task(2)
    def reserve(self):
        event = random.choice([3, 3, 3, 5])
        self.client.post(
            f"/events/{event}/reserve",
            json={"quantity": 1},
            headers={"Content-Type": "application/json"},
            name="/events/{id}/reserve",
        )

    @task(1)
    def health(self):
        self.client.get("/health")
