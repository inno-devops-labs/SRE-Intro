# Lab 8 - Hypotheses recorded before failure injection

1. Pod kill under load:
   If I delete one Gateway pod while mixed traffic is running, the remaining
   replicas should continue serving requests through the Service. Kubernetes
   should create a replacement quickly, with no significant 5xx increase.

2. Payment latency:
   If Payments adds 2000 ms to each charge, the payment endpoint p99 should
   rise by about two seconds while event reads remain fast. Because the Gateway
   timeout is 5000 ms, I do not expect the 2000 ms delay alone to cause 5xx.

3. Redis outage:
   If Redis is unavailable, reservation should fail because Events stores
   reservation holds in Redis. Event listing should remain available, while
   health should report dependency degradation.

4. Combined scenario:
   If Payments injects a 25% failure rate and 600 ms latency while Events is
   limited to four database connections under three mixedload replicas, I
   expect the payment path to show the largest error rate and p99 latency.
