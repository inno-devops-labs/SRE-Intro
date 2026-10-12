"""Locust load-test for QuickTicket gateway.

Runs in-cluster as a Job so traffic goes through kube-proxy and is
load-balanced across all gateway replicas. The task mix mirrors real
traffic: listing events dominates, some checkout, some health-polling.

Reserves are spread across events 3 and 5 (500 + 80 tickets). Inventory
can still run out; the final status counts separate expected 409s from 5xx.
The summary listener is intended for this lab's single-process Jobs.
"""
import json
import random
from collections import Counter
from locust import HttpUser, task, between, events


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
        )

    @task(1)
    def health(self):
        self.client.get("/health")


status_counts = Counter()


@events.test_start.add_listener
def reset_counts(**kwargs):
    status_counts.clear()


@events.request.add_listener
def count_status(response=None, **kwargs):
    status_counts[response.status_code if response is not None else 0] += 1


@events.test_stop.add_listener
def print_summary(environment, **kwargs):
    total = environment.stats.total
    print("LAB10_SUMMARY " + json.dumps({
        "users": environment.parsed_options.num_users,
        "requests": total.num_requests,
        "rps": total.total_rps,
        "p50_ms": total.get_response_time_percentile(0.50),
        "p95_ms": total.get_response_time_percentile(0.95),
        "p99_ms": total.get_response_time_percentile(0.99),
        "status_counts": dict(status_counts),
    }), flush=True)
