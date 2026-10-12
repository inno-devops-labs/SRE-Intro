"""In-cluster 7:2:1 read/reserve/health mix; HTTP status evidence is separate."""
import collections
import json
import random
import time
from locust import HttpUser, task, between, events
statuses = collections.Counter()
started = 0

@events.test_start.add_listener
def begin(environment, **kwargs):
    global started
    statuses.clear()
    started = time.monotonic()

@events.request.add_listener
def count_status(response=None, **kwargs):
    statuses[str(response.status_code) if response is not None else 'transport'] += 1

@events.test_stop.add_listener
def finish(environment, **kwargs):
    s = environment.stats.total
    seconds = time.monotonic() - started
    print('LAB10_RESULT=' + json.dumps({
        'users': environment.parsed_options.num_users,
        'seconds': seconds, 'requests': s.num_requests,
        'rps': s.num_requests / seconds,
        'p50_ms': s.get_response_time_percentile(.50),
        'p95_ms': s.get_response_time_percentile(.95),
        'p99_ms': s.get_response_time_percentile(.99),
        'statuses': dict(statuses),
        'five_xx': sum(n for code,n in statuses.items() if code.startswith('5')),
        'transport': statuses['transport'],
    }), flush=True)

class QuickTicketUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(7)
    def list_events(self):
        self.client.get('/events', timeout=10)

    @task(2)
    def reserve(self):
        event = random.choice([3, 3, 3, 5])
        with self.client.post(f'/events/{event}/reserve',
                name='/events/{id}/reserve', json={'quantity': 1},
                timeout=10, catch_response=True) as response:
            if response.status_code == 409:
                response.success()  # Expected inventory conflict, still counted above.

    @task(1)
    def health(self):
        self.client.get('/health', timeout=10)
