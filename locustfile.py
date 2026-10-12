"""Locust load test for the QuickTicket gateway.

The test runs inside the cluster so requests pass through the Kubernetes
Service and are distributed across the Gateway replicas.
"""
import random

from locust import HttpUser, between, task


class QuickTicketUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(7)
    def list_events(self):
        self.client.get("/events")

    @task(2)
    def reserve(self):
        event_id = random.choice([3, 3, 3, 5])
        self.client.post(
            f"/events/{event_id}/reserve",
            json={"quantity": 1},
            headers={"Content-Type": "application/json"},
        )

    @task(1)
    def health(self):
        self.client.get("/health")
