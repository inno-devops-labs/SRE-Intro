"""Locust load-test for QuickTicket gateway.

Runs in-cluster as a Job so traffic goes through kube-proxy and is
load-balanced across all gateway replicas. The task mix mirrors real
traffic: listing events dominates, some checkout, some health-polling.

For Lab 12, reservations target the dedicated event 6, created with enough
inventory for the experiment. The task mix remains the Lab 10 mix.
"""
from locust import HttpUser, task, between


class QuickTicketUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(7)
    def list_events(self):
        self.client.get("/events")

    @task(2)
    def reserve(self):
        event = 6  # Dedicated Lab 12 load event
        self.client.post(
            f"/events/{event}/reserve",
            json={"quantity": 1},
            headers={"Content-Type": "application/json"},
        )

    @task(1)
    def health(self):
        self.client.get("/health")
