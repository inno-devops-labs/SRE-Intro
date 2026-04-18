"""Locust load-test for QuickTicket gateway.

Runs in-cluster as a Job so traffic goes through kube-proxy and is
load-balanced across all gateway replicas. The task mix mirrors real
traffic: listing events dominates, some checkout, some health-polling.

Reserves are spread across events 3 and 5 (500 + 80 tickets) so a 60s
run at 50-100 users doesn't exhaust a single event's inventory and
pollute the error count with 409 Conflicts.
"""
import random
from locust import HttpUser, task, between


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
