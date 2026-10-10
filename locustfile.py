"""In-cluster QuickTicket capacity test, with inventory conflicts separated."""

import json
import random
from collections import Counter

from locust import HttpUser, between, events, task


statuses = Counter()
latencies = Counter()


@events.request.add_listener
def record_status(response=None, response_time=None, **kwargs):
    statuses[str(response.status_code) if response is not None else "transport"] += 1
    if response_time is not None:
        latencies["within_500ms" if response_time <= 500 else "over_500ms"] += 1


@events.quitting.add_listener
def report(environment, **kwargs):
    total = environment.stats.total
    print("LAB10_RESULT " + json.dumps({
        "requests": total.num_requests,
        "rps": total.total_rps,
        "p50_ms": total.get_response_time_percentile(0.50),
        "p95_ms": total.get_response_time_percentile(0.95),
        "p99_ms": total.get_response_time_percentile(0.99),
        "statuses": dict(statuses),
        "latency_counts": dict(latencies),
    }), flush=True)


class QuickTicketUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(7)
    def list_events(self):
        self.client.get("/events")

    @task(2)
    def reserve(self):
        event = random.choice([3, 3, 3, 5])
        with self.client.post(
            f"/events/{event}/reserve",
            json={"quantity": 1},
            name="/events/{id}/reserve",
            catch_response=True,
        ) as response:
            if response.status_code == 409:
                response.success()

    @task(1)
    def health(self):
        self.client.get("/health")
