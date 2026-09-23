"""Locust load-test for QuickTicket gateway.

Runs in-cluster as a Job so traffic goes through kube-proxy and is
load-balanced across all gateway replicas. The task mix mirrors real
traffic: listing events dominates, some checkout, some health-polling.

Reserves are spread across events 3 and 5 (500 + 80 tickets) in the
course-provided version so a 60s run at 50-100 users doesn't exhaust a
single event's inventory and pollute the error count with 409 Conflicts.

NOTE (Lab 10, this fork): events 3 and 5 are already sold out (500/500
and 80/80 confirmed) from Labs 6-9's own load generators — see
submissions/lab9.md and the Lab 9 handoff. Pointing this file at them
would make every /reserve call 409 from second one of every run, which
would tell us nothing about the breaking point. Per the Lab 10 README's
own guidance ("point it at events with stock or reset stock"), this copy
targets events 1 and 2 instead (100 and 30 tickets, both 0 confirmed at
the start of this lab) rather than deleting orders out of events 3/5,
which would erase real history other labs' submissions reference.
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
        event = random.choice([1, 1, 1, 2])
        self.client.post(
            f"/events/{event}/reserve",
            json={"quantity": 1},
            headers={"Content-Type": "application/json"},
        )

    @task(1)
    def health(self):
        self.client.get("/health")
