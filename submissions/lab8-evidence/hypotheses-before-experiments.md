# Lab 8 — hypotheses recorded before failure injection

1. Pod kill under load:
   If I delete one gateway pod while mixed traffic is flowing, the other
   gateway replicas should continue serving requests through the Service.
   Kubernetes should create a replacement; I expect zero or only a brief
   increase in HTTP 5xx responses.

2. Payment latency:
   If Payments adds 2000 ms per charge, /pay latency should increase by about
   two seconds while /events reads remain unaffected. The Gateway timeout is
   5000 ms, so I expect the 2000 ms delay not to cause 5xx responses.

3. Redis outage:
   If Redis is stopped, reservations should fail because Events stores holds
   in Redis. Events health/readiness also checks Redis, so its endpoint may be
   removed from the Service. Since Gateway readiness checks Events health,
   even GET /events traffic may be disrupted during the outage.

4. Combined scenario:
   If Payments injects 30% failures and 500 ms latency while Events is limited
   to three database connections under three mixedload replicas, I expect
   /pay to have the largest 5xx rate and p99 latency. Reservation latency may
   also rise if the smaller DB pool becomes contended; ordinary /events reads
   should be less affected.
