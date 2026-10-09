"""QuickTicket in-cluster capacity test.

Task weights: listing 7, reservation 2, health 1.
Reservations use events 3 and 5 in a 3:1 mix.
409 is expected inventory contention and is counted separately from 5xx.
This scenario does not exercise payments.
"""
import datetime
import json
import random
from collections import Counter

from locust import HttpUser, between, events, task

statuses = Counter()
started_at = None


@events.test_start.add_listener
def record_start(environment, **kwargs):
    global started_at
    statuses.clear()
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()


@events.request.add_listener
def record_status(response=None, exception=None, **kwargs):
    if response is not None and response.status_code:
        statuses[str(response.status_code)] += 1
    else:
        statuses["network_error"] += 1


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
            name="/events/{id}/reserve",
            json={"quantity": 1},
            catch_response=True,
        ) as response:
            if response.status_code == 409:
                response.success()

    @task(1)
    def health(self):
        self.client.get("/health")


@events.quitting.add_listener
def report_results(environment, **kwargs):
    total = environment.stats.total
    requests = total.num_requests
    server_errors = sum(
        count for status, count in statuses.items()
        if status.isdigit() and 500 <= int(status) < 600
    )
    endpoints = []
    for (_, _), stat in environment.stats.entries.items():
        endpoints.append({
            "method": stat.method,
            "name": stat.name,
            "requests": stat.num_requests,
            "failures": stat.num_failures,
            "p50_ms": stat.get_response_time_percentile(0.50),
            "p95_ms": stat.get_response_time_percentile(0.95),
            "p99_ms": stat.get_response_time_percentile(0.99),
        })
    result = {
        "started_at": started_at,
        "finished_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "users": environment.parsed_options.num_users,
        "ramp_per_second": environment.parsed_options.spawn_rate,
        "requests": requests,
        "rps": total.total_rps,
        "p50_ms": total.get_response_time_percentile(0.50),
        "p95_ms": total.get_response_time_percentile(0.95),
        "p99_ms": total.get_response_time_percentile(0.99),
        "five_xx_count": server_errors,
        "five_xx_percent": 100 * server_errors / requests if requests else None,
        "inventory_409_count": statuses["409"],
        "inventory_409_percent":
            100 * statuses["409"] / requests if requests else None,
        "network_errors": statuses["network_error"],
        "locust_failures": total.num_failures,
        "status_counts": dict(statuses),
        "endpoints": endpoints,
        "scope": "Read/reserve/health mix; payment path excluded",
    }
    print("LAB10_RESULT=" + json.dumps(result), flush=True)
