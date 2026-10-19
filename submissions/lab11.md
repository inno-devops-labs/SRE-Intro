**# Lab 11 — Resilience Design and Failure Handling in QuickTicket**



**## Project overview**



**\*\*Branch:\*\*** \`feature/lab11\`  

**\*\*Platform:\*\*** QuickTicket on Kubernetes (k3d)  

**\*\*Deployment model:\*\*** one notifications pod and five gateway pods controlled by an Argo Rollout  

**\*\*Scope:\*\*** asynchronous notification delivery, exponential retry, circuit breaker, sliding-window rate limiting, and payment bulkhead  

**\*\*Monitoring:\*\*** Prometheus counters, histograms, and gauges



The goal of this exercise is to control the behavior of the checkout path when a downstream service becomes unreliable or responds slowly. The solution is organized around the gateway, rather than adding independent resilience implementations to every service. The gateway can therefore decide when to retry, when to stop sending requests to payments, and when to reject incoming work.



**### Completion checklist**



\- [x] Fourth microservice exposes \`/notify\`, \`/health\`, and \`/metrics\`.

\- [x] Notification failures do not determine checkout success.

\- [x] Transient payment errors use bounded retry with exponential backoff and jitter.

\- [x] Payment requests are protected by a CLOSED/OPEN/HALF_OPEN circuit breaker.

\- [x] Request rate is limited separately for each normalized endpoint.

\- [x] A per-process bulkhead restricts concurrent payment operations.

\- [x] FastAPI and Prometheus metrics cover successes, failures, rejections and timing.

\- [x] Automated tests cover all implemented primitives.



**## 1. System architecture**



**### 1.1 Services and their responsibilities**



\| Component | Address inside Kubernetes | Responsibility |

\|---|---|---|

\| Gateway | \`http\://gateway:8080\` | Public REST interface and resilience controls |

\| Events | \`http\://events:8081\` | Event inventory, reservations and confirmation |

\| Payments | \`http\://payments:8082\` | Charge execution and payment reference |

\| Notifications | \`http\://notifications:8083\` | Best-effort order-confirmed notifications |

\| Prometheus | \`monitoring\` namespace | Scrapes and evaluates service metrics |



The normal checkout sequence is:



\`\`\`text

Client

  |

  v

Gateway: POST /events/{event_id}/reserve

  |

  v

Events: create reservation

  |

  v

Gateway: POST /reserve/{reservation_id}/pay

  |

  +-- Bulkhead acquire ------------------------+

  |                                            |

  +-- Circuit breaker ------------------------+---> Payments /charge

  |        +-- Retry + jitter -----------------+

  |

  +-- Events /reservations/{id}/confirm

  |

  +-- return checkout result to client

  |

  +-- schedule non-blocking Notifications /notify

\`\`\`



Notifications are deliberately outside the critical checkout path. A customer should not lose a confirmed ticket simply because an email or message service is temporarily unavailable. Conversely, payment errors must remain visible to the customer rather than being silently treated as success.



**### 1.2 Baseline deployment parameters**



\| Variable | Configured value | Meaning |

\|---|---:|---|

\| \`RETRY_MAX\` | 3 | Maximum number of attempts for one payment operation |

\| \`RETRY_BASE_DELAY_MS\` | 110 | Base sleep interval used in backoff |

\| \`CB_FAILURE_THRESHOLD\` | 4 | Consecutive unsuccessful logical operations before OPEN |

\| \`CB_COOLDOWN_S\` | 28 | Wait before first HALF_OPEN recovery probe |

\| \`RATE_LIMIT_RPS\` | 11 | Allowed requests per endpoint and gateway process per second |

\| \`BULKHEAD_PAYMENTS_MAX\` | 9 | Concurrent payment operations per gateway process |

\| \`BULKHEAD_PAYMENTS_TIMEOUT_S\` | 0.45 | Maximum wait for a bulkhead slot, in seconds |

\| \`GATEWAY_TIMEOUT_MS\` | 5000 | Gateway timeout for downstream HTTP requests |

\| \`NOTIFY_FAILURE_RATE\` | 0.0 | Default notification failure injection probability |

\| \`NOTIFY_LATENCY_MS\` | 0 | Default injected notification delay |



These values are also declared in the Kubernetes manifests rather than being hard-coded exclusively in application logic. The application defaults match the manifests. This makes configuration inspection straightforward using \`kubectl\` and allows fault injection without editing Python source.



**### 1.3 Canary strategy**



The gateway deployment uses an Argo Rollout with five replicas. Its canary sequence is 20% traffic, a pause, the \`gateway-error-rate\` analysis, then 50% and finally 100%. The Rollout specification keeps the original route, service name and labels so the remaining microservices can continue to call \`gateway:8080\`.



A locally built image uses tag \`quickticket-gateway:lab11-case2\`. \`IfNotPresent\` is used because the image is imported into the local k3d cluster instead of being downloaded from a public container registry. After a source change, the image must be rebuilt and imported again. A deployment that reuses an unchanged tag without restarting the pods may still run the previous image.



**### 1.4 Important operating constraints**



The gateway maintains counters and circuit state in process memory. Because there are five independent pods, there are five rate limiters, circuit breakers and bulkheads. A limit of 11 RPS is therefore not a globally synchronized limit of 11 RPS. The aggregate allowance for evenly distributed traffic is \`5 × 11 = 55\` requests per second for an individual path.



Another constraint is the shared downstream connection pool. An excessive number of slow payment calls can occupy available connections or create long queues. The bulkhead limits how many payment coroutines can compete for these resources.



**---**



**## 2. Notifications service**



**### 2.1 API design**



The notifications service is implemented with FastAPI in \`app/notifications/main.py\`. It does not require its own database. A notification is represented by a small JSON object containing \`event\` and \`order_id\`.



\| Method | URI | Expected behavior |

\|---|---|---|

\| POST | \`/notify\` | Records an attempt, applies configured fault injection and responds with an ID |

\| GET | \`/health\` | Returns status and current injected failure/latency settings |

\| GET | \`/metrics\` | Exposes Prometheus metrics in the standard text format |



Notification request:



\`\`\`http

POST /notify HTTP/1.1

Content-Type: application/json



{"event":"order_confirmed","order_id":"res-4821"}

\`\`\`



Successful response format:



\`\`\`json

{

  "status": "sent",

  "notification_id": "NTF-AB12CD34EF56",

  "event": "order_confirmed",

  "order_id": "res-4821"

}

\`\`\`





**### 2.2 Application implementation**



The payload is validated using Pydantic. Empty event names and empty order identifiers return HTTP 422 rather than creating meaningless notification records.



\`\`\`python

class NotificationEvent(BaseModel):

    event: str = Field(min_length=1)

    order_id: str = Field(min_length=1)

\`\`\`



The request handler has two injected-fault controls:



\`\`\`python

@app.post("/notify")

def notify(payload: NotificationEvent):

    if NOTIFY_LATENCY_MS > 0:

        time.sleep(NOTIFY_LATENCY_MS / 1000.0)

    if random.random() < NOTIFY_FAILURE_RATE:

        NOTIFY_TOTAL.labels("failed").inc()

        log.warning("Injected notify failure, order=%s", payload.order_id)

        raise HTTPException(status_code=500, detail="Notification delivery failed")



    notification_id = f"NTF-{uuid.uuid4().hex[:12].upper()}"

    NOTIFY_TOTAL.labels("success").inc()

    log.info("Notification %s for order=%s event=%s", notification_id, payload.order_id, payload.event)

    return {

        "status": "sent",

        "notification_id": notification_id,

        "event": payload.event,

        "order_id": payload.order_id,

    }

\`\`\`



\`NOTIFY_FAILURE_RATE\` is a probability in the range 0.0–1.0. With a value of 0.28, each independent request has a 28% failure probability. \`NOTIFY_LATENCY_MS\` adds an artificial delay before deciding the response status. Fault injection is useful because the design can be evaluated without waiting for an actual production outage.



**### 2.3 Dependency and image files**



\`app/notifications/requirements.txt\` contains:



\`\`\`text

fastapi==0.136.0

uvicorn==0.44.0

prometheus-client==0.25.0

\`\`\`



\`app/notifications/Dockerfile\` runs a single Uvicorn process on port 8083. The Kubernetes Deployment provides one replica, readiness and liveness probes, memory/CPU requests and a ClusterIP Service. Internal traffic uses the DNS name \`notifications\`.



**### 2.4 Asynchronous gateway integration**



After a successful charge and reservation confirmation, \`/pay\` schedules notification delivery:



\`\`\`python

asyncio.create_task(\_notify_order_confirmed(reservation_id))

return result

\`\`\`



The helper contacts the notification service through the existing asynchronous HTTP client:



\`\`\`python

async def \_notify_order_confirmed(reservation_id: str):

    if not NOTIFICATIONS_URL:

        return

    try:

        response = await client.post(

            f"{NOTIFICATIONS_URL}/notify",

            json={"event": "order_confirmed", "order_id": reservation_id},

            timeout=2.0,

        )

        response.raise_for_status()

    except httpx.HTTPError as exc:

        log.warning("async notification failed for %s: %s", reservation_id, exc)

\`\`\`



\`create_task()\` avoids waiting for the notification response in the checkout handler. \`raise_for_status()\` also ensures an HTTP 500 from notifications is recorded as an unsuccessful background delivery, not only a network-level exception. The error is logged but does not change an already completed checkout response.



**### 2.5 Metrics exposed by notifications**



\| Metric | Type | Interpretation |

\|---|---|---|

\| \`notifications_requests_total{method,path,status}\` | Counter | HTTP calls broken down by endpoint and status |

\| \`notifications_request_duration_seconds\` | Histogram | Distribution of notification request durations |

\| \`notifications_notify_total{result="success"}\` | Counter | Successfully completed notification attempts |

\| \`notifications_notify_total{result="failed"}\` | Counter | Fault-injected notification failures |



The metric \`notifications_notify_total\` counts attempts, not unique orders. No durable queue or retry-on-notification-failure is implemented in this lab.



**---**



**## 3. Retry protection for payments**



**### 3.1 Error classification**



A payment error should be retried only when another attempt could reasonably succeed. The implementation distinguishes temporary transport failures and server errors from ordinary client validation failures.



\| Error | Retry? | Reason |

\|---|---|---|

\| Connection failure | Yes | The upstream may recover |

\| Request timeout | Yes | A later attempt may receive a response |

\| HTTP 500–599 | Yes | Temporary server-side failure |

\| HTTP 408 | Yes | Request timeout |

\| HTTP 429 | Yes | Upstream is temporarily throttling |

\| HTTP 400/404/422 | No | Repeating an unchanged invalid request will not help |



The classifier is a separate helper:



\`\`\`python

def \_retryable_payment_error(error: Exception) -> bool:

    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError)):

        return True

    if isinstance(error, httpx.HTTPStatusError):

        status = error.response.status_code

        return status in (408, 429) or status >= 500

    return False

\`\`\`



This also means unexpected programming exceptions are not retried. Such errors should remain visible during development.



**### 3.2 Retry algorithm**



The implementation accepts an asynchronous callable and runs it up to \`RETRY_MAX\` times. Each failed retryable attempt increments \`gateway_retry_total{result="retried"}\` if another attempt follows. The final failure increments \`exhausted\`; recovery after a previous failure increments \`succeeded_after_retry\`.



\`\`\`python

async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):

    if max_retries < 1:

        raise ValueError("max_retries must be at least 1")

    base_delay = RETRY_BASE_DELAY_MS / 1000.0

    for attempt in range(max_retries):

        try:

            result = await func()

        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError) as exc:

            if not \_retryable_payment_error(exc):

                RETRY_TOTAL.labels(target, "non_retryable").inc()

                raise

            if attempt + 1 == max_retries:

                RETRY_TOTAL.labels(target, "exhausted").inc()

                raise

            delay = base_delay \* 2\*\*attempt + random.uniform(0, base_delay)

            RETRY_TOTAL.labels(target, "retried").inc()

            log.warning("payment retry target=%s attempt=%s/%s sleep=%.3fs cause=%s", target, attempt + 1, max_retries, delay, exc)

            await asyncio.sleep(delay)

        else:

            if attempt:

                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()

            return result

\`\`\`



The number \`RETRY_MAX=3\` is the total number of calls, not the number of additional retries. A failed operation can therefore have two sleeps between three attempts.



**### 3.3 Backoff arithmetic**



The formula is:



\`\`\`text

delay(attempt) = base_delay × 2^attempt + Uniform(0, base_delay)

\`\`\`



Here the attempt index starts at zero and the base delay is 0.110 seconds.



\| Failed attempt | Deterministic part | Jitter part | Total sleep range |

\|---|---:|---:|---:|

\| First | 110 ms | 0–110 ms | 110–220 ms |

\| Second | 220 ms | 0–110 ms | 220–330 ms |



The random part reduces synchronization between clients that encountered the same outage. Purely deterministic retries may otherwise form a synchronized wave and overload the service precisely when it begins recovering.



If each payment attempt independently fails with probability \`p=0.28\`, the probability of all three attempts failing is \`p³ = 0.021952\`, or about 2.20%.



**### 3.4 Fault-injection results and interpretation**



\| Test input | Result | Meaning |

\|---|---|---|

\| 30 checkout chains; notification failure probability 28%; delay 270 ms | 30 successful checkouts, 0 failed | Notification delivery is outside the checkout response path |

\| Checkout \`/pay\` p50 | 8 ms | Median checkout payment duration |

\| Checkout \`/pay\` p95 | 25 ms | 95% of calls finish within this duration |

\| Checkout \`/pay\` p99 | 73 ms | 99th-percentile latency below 100 ms |

\| Notification successes | 188 | Number of completed notification deliveries counted by the pod |

\| Notification failures | 71 | Number of injected notification failures counted by the pod |

\| Observed notification failure share | \`71 / (188 + 71) = 27.4%\` | Observed fraction is close to the configured 28% |





\| Payment retry probe | Result | Meaning |

\|---|---|---|

\| 30 checkout chains; payment failure probability 28% | 29 successful, 1 failed | Most transient failures recovered before the attempt limit |

\| \`gateway_retry_total{result="retried"}\` | 79 | Additional retry attempts were scheduled |

\| \`gateway_retry_total{result="succeeded_after_retry"}\` | 52 | Logical requests that succeeded only after retrying |

\| \`gateway_retry_total{result="exhausted"}\` | 6 | Logical requests whose attempts were all unsuccessful |

\| \`/pay\` p99 under payment faults | 405 ms | Added retry sleeps make tail latency higher |





**### 3.5 Why retry belongs inside the circuit breaker**



The breaker should assess the result of a logical customer request, after retries have handled transient network failures. One successful attempt after two errors is still a successful payment operation from the user's perspective.



Correct composition:



\`\`\`python

await payments_cb.call(lambda: call_with_retry(charge, "payments"))

\`\`\`



Incorrect alternative:



\`\`\`python

await call_with_retry(lambda: payments_cb.call(charge), "payments")

\`\`\`



With the second order, every unsuccessful *\*attempt\** can count as a distinct circuit-breaker failure, opening the circuit sooner than intended. It can also repeatedly retry an already OPEN circuit. Keeping retry inside the breaker makes the failure threshold correspond to logical payment requests.



**---**



**## 4. Circuit breaker**



**### 4.1 State model**



\`\`\`text

          successful calls

      +----------------------+

      |                      |

      v                      |

   CLOSED --threshold--> OPEN

                           |

                           | cooldown 28s

                           v

                       HALF_OPEN

                        /     \\

                   success     failure

                     /           \\

                  CLOSED         OPEN

\`\`\`



The circuit begins CLOSED and forwards requests. A failed logical call increments its consecutive failure count. With threshold 4, the fourth consecutive failure opens the circuit. While OPEN, calls fail immediately with \`CircuitOpenError\` and the gateway turns it into HTTP 503.



After 28 seconds, the next call is allowed as a recovery probe in HALF_OPEN. Success resets the failure count and closes the circuit. Failure opens it again.



**### 4.2 Implementation**



\`\`\`python

class CircuitBreaker:

    CLOSED = "CLOSED"

    OPEN = "OPEN"

    HALF_OPEN = "HALF_OPEN"



    def \_\_init\_\_(self, threshold: int, cooldown_s: float, name: str = "payments"):

        if threshold < 1 or cooldown_s < 0:

            raise ValueError("threshold must be positive and cooldown must not be negative")

        self.name = name

        self.threshold = threshold

        self.cooldown = cooldown_s

        self.failures = 0

        self.state = self.CLOSED

        self.opened_at = 0.0



    def \_transition(self, state: str):

        if state != self.state:

            log.warning("circuit[%s]: %s -> %s", self.name, self.state, state)

            CB_STATE_TRANSITIONS.labels(state).inc()

        self.state = state



    async def call(self, func):

        if self.state == self.OPEN:

            if time.monotonic() - self.opened_at < self.cooldown:

                raise CircuitOpenError(f"circuit[{self.name}] OPEN")

            self.\_transition(self.HALF_OPEN)

        try:

            result = await func()

        except Exception:

            self.failures += 1

            self.opened_at = time.monotonic()

            if self.state == self.HALF_OPEN or self.failures >= self.threshold:

                self.\_transition(self.OPEN)

            raise

        self.failures = 0

        self.\_transition(self.CLOSED)

        return result

\`\`\`



\`time.monotonic()\` is used for cooldown calculation. Unlike the wall clock, it does not jump when the system time is corrected, which is useful for calculating relative timeouts.



**### 4.3 Interpreting failure-injection status codes**



Set payments to fail every call with \`PAYMENT_FAILURE_RATE=1.0\`, then submit checkout requests. A few logical payments are attempted while each circuit is CLOSED; later requests are rejected by an OPEN circuit without contacting payments.



\| HTTP status | Source | Interpretation |

\|---|---|---|

\| 500 | Payments exhausted transient retries | Upstream work was attempted but failed |

\| 503 | Circuit OPEN | Payments was not contacted for that logical request |

\| 503 | Bulkhead full | Request rejected because no concurrency slot was available |

\| 429 | Gateway rate limiter | Client sent requests faster than the local per-path allowance |

\| 200 | Payment and confirmation succeeded | Entire checkout completed |



HTTP status distribution for the 80-attempt combined fault test:



\`\`\`text

attempts=80

checkout_500=13

checkout_503=44

checkout_429=18

reservation_failed_or_other=5

\`\`\`



These categories add up to 80. They also demonstrate why the rate limiter must be considered when interpreting the breaker experiment: once OPEN, a payment request returns much faster, so the client can reach the gateway more frequently.



**### 4.4 Recovery and transition metrics**



After resetting \`PAYMENT_FAILURE_RATE=0.0\` and waiting longer than the 28-second cooldown, a successful probe should move HALF_OPEN to CLOSED. Later calls can then reach payments normally.



The transition counter is:



\`\`\`promql

sum by (to) (gateway_circuit_breaker_transitions_total)

\`\`\`



Circuit-breaker transition totals across five gateway pods:



\`\`\`text

to="OPEN"        10

to="HALF_OPEN"   10

to="CLOSED"       5

\`\`\`



A pod can reopen more than once if the first recovery probe still fails. The total number of OPEN transitions can therefore exceed the number of pods. The transition counter records state changes, not how many individual requests received 503.



**### 4.5 Limitations of per-pod state**



One service-wide request burst is distributed between five gateway pods. A threshold of four needs four consecutive logical errors on the **\*\*same pod\*\***. A small test may fail to open every circuit because the load is spread unevenly.



This breaker does not currently enforce a single probe in HALF_OPEN when many concurrent calls arrive together. A production-grade implementation would use a probe lock or existing circuit-breaker library to avoid a recovery burst.



**---**



**## 5. Sliding-window rate limiter**



**### 5.1 Algorithm**



The gateway stores a deque of arrival timestamps for each normalized path. On every request, expired entries older than the one-second window are removed. If the deque already contains 11 timestamps, the request is rejected. Otherwise the current timestamp is recorded and the request proceeds.



\`\`\`python

class RateLimiter:

    def \_\_init\_\_(self, rps: int):

        if rps < 1:

            raise ValueError("rps must be positive")

        self.rps = rps

        self.window_s = 1.0

        self.hits: dict[str, deque] = defaultdict(deque)



    def allow(self, key: str) -> bool:

        now = time.monotonic()

        hits = self.hits[key]

        cutoff = now - self.window_s

        while hits and hits[0] <= cutoff:

            hits.popleft()

        if len(hits) >= self.rps:

            return False

        hits.append(now)

        return True

\`\`\`



The limiter uses the normalized route key, not a raw reservation identifier. Without normalization, every UUID would become a distinct key and bypass a per-endpoint limit. Health and metrics endpoints are excluded so monitoring stays available during an overload event.



**### 5.2 Rejection response**



HTTP response:



\`\`\`http

HTTP/1.1 429 Too Many Requests

Retry-After: 1

Content-Type: application/json



{"error":"rate_limited","path":"/events","limit_rps":11}

\`\`\`



The \`Retry-After\` header advises a cooperative client to delay before resending. In this implementation the value is a simple one-second recommendation, not an exact earliest-admission timestamp.



**### 5.3 Burst behavior**



With five pods and an allowance of eleven hits per pod, the combined theoretical allowance is 55 requests to a single path in one common one-second window. Actual totals depend on request duration, kube-proxy distribution, sliding-window boundaries and background traffic.



Rate-limiter burst test results:



\| Workload | HTTP 200 | HTTP 429 | Other | Interpretation |

\|---|---:|---:|---:|---|

\| 100 rapid GET \`/events\` | 56 | 44 | 0 | Rate limiting engages under a short burst |

\| 30 GET \`/events\` spaced 200 ms | 30 | 0 | 0 | Traffic below 11 requests/s per pod remains allowed |



The fast and slow request experiments measure different behavior. A limiter should refuse bursts without imposing 429 responses on ordinary sustained demand.



**### 5.4 Counter interpretation**



\`\`\`promql

sum by (path) (gateway_rate_limit_rejections_total)

\`\`\`



This shows which route is most frequently rejected. Because the outer metrics middleware also sees HTTP 429, the generic HTTP counter includes the same responses:



\`\`\`promql

sum by (path) (gateway_requests_total{status="429"})

\`\`\`



The two metrics answer different questions. \`gateway_rate_limit_rejections_total\` identifies the limiter as the reason for rejection, while \`gateway_requests_total\` reports all HTTP responses, including application and dependency errors.



**---**



**## 6. Bonus: bounded payment concurrency (bulkhead)**



**### 6.1 Problem addressed**



Rate limiting controls the **\*\*arrival rate\*\*** of new requests. It does not directly limit how many requests can be in progress at once. A dependency with three-second responses could accumulate far more concurrent calls than a healthy dependency, even at the same arrival rate.



The bulkhead caps how many checkout requests can occupy the payments path simultaneously. This is useful because one slow dependency should not exhaust the shared resources used by requests to other services.



**### 6.2 Implementation**



\`\`\`python

class Bulkhead:

    def \_\_init\_\_(self, name: str, max_concurrent: int, acquire_timeout_s: float):

        if max_concurrent < 1 or acquire_timeout_s < 0:

            raise ValueError("invalid bulkhead capacity or acquire timeout")

        self.name = name

        self.timeout = acquire_timeout_s

        self.sem = asyncio.Semaphore(max_concurrent)



    async def call(self, func):

        try:

            await asyncio.wait_for(self.sem.acquire(), timeout=self.timeout)

        except asyncio.TimeoutError as exc:

            BULKHEAD_REJECTIONS.labels(self.name).inc()

            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()

        try:

            return await func()

        finally:

            BULKHEAD_IN_FLIGHT.labels(self.name).dec()

            self.sem.release()

\`\`\`



The semaphore capacity is 9. If no slot becomes available within 450 ms, the gateway returns HTTP 503 with an overload description. The \`finally\` block guarantees semaphore release even when a request fails or raises an exception.



**### 6.3 Composition of all patterns**



The entire payments call is organized as follows:



\`\`\`python

payment_response = await payments_bulkhead.call(

    lambda: payments_cb.call(lambda: call_with_retry(charge, "payments"))

)

\`\`\`



The order from outermost to innermost is **\*\*bulkhead → circuit breaker → retry → HTTP call\*\***. The bulkhead owns capacity for one logical payment operation, including all retry sleeps, rather than reacquiring a slot for every attempt.



If the breaker were outside the bulkhead, a \`BulkheadFullError\` could be counted as a downstream payment failure and contribute to opening a breaker even when payments itself is healthy. Keeping the bulkhead outside helps distinguish overload from dependency failure.



**### 6.4 Measuring the bulkhead effect**



For an isolated experiment, reserve tickets first and then send concurrent \`/pay\` requests. Creating reservations concurrently can itself overload the events database connection pool, so it does not cleanly measure payment isolation.



During the bulkhead experiment, raise the incoming rate limit to 900 to prevent early rate-limit rejections before requests reach the payment semaphore. Set the injected payment delay to 2,800 ms.



Bulkhead test results:



\| Configuration | Concurrent \`/pay\` | Payment outcomes | Bulkhead rejects | Max active per gateway pod |

\|---|---:|---|---:|---:|

\| Bulkhead enabled, slow payments | 100 | 39×200, 9×500, 52×503 | 52 | 9 |

\| Bulkhead enabled, smaller burst | 30 | 26×200, 4×500 | 0 | 9 |

\| Bulkhead bypassed, slow payments | 100 | 77×200, 19×500, 4×504 | Not applicable | Not bounded by this pattern |



The exact breakdown depends on pod distribution, downstream errors, reservation validity, background workload and client scheduling. The important signature of a binding bulkhead is a positive rejection counter together with an in-flight gauge that does not exceed the configured capacity.



**### 6.5 What the two bulkhead metrics mean**



\| Metric | Type | Interpretation |

\|---|---|---|

\| \`gateway_bulkhead_in_flight{target="payments"}\` | Gauge | Current payments holding a concurrency slot |

\| \`gateway_bulkhead_rejections_total{target="payments"}\` | Counter | Payments refused after waiting for an available slot |



The following PromQL expression should not exceed 9 for any one pod during the experiment:



\`\`\`promql

max by (pod) (max_over_time(gateway_bulkhead_in_flight{target="payments"}[2m]))

\`\`\`



The rejection counter is aggregated with:



\`\`\`promql

sum by (target) (gateway_bulkhead_rejections_total)

\`\`\`



\`gateway_bulkhead_in_flight\` is a gauge because it goes up and down. \`gateway_bulkhead_rejections_total\` is a counter because it only increases during one process lifetime.



**### 6.6 Why a slow request does not necessarily block \`/events\`**



The gateway uses asyncio and \`httpx.AsyncClient\`. Awaiting a slow payments response yields control to the event loop; it does not block an entire Python thread for three seconds. Therefore \`/events\` may remain responsive even without a bulkhead at moderate concurrency.



The more relevant risk is saturation of shared connections, available tasks or downstream payment workers. The bulkhead protects those resources by limiting the number of slow calls. A clear overload response is preferable to growing queues, timeouts or memory consumption.



**---**



**## 7. Observability and metric interpretation**



**### 7.1 Summary of Prometheus series**



\| Series | Labels | Practical question answered |

\|---|---|---|

\| \`gateway_requests_total\` | \`method,path,status\` | How many requests returned each HTTP status? |

\| \`gateway_request_duration_seconds\` | \`method,path\` | How long does each gateway endpoint take? |

\| \`gateway_retry_total\` | \`target,result\` | Are retries being attempted, recovered or exhausted? |

\| \`gateway_circuit_breaker_transitions_total\` | \`to\` | How often did the breaker enter each state? |

\| \`gateway_rate_limit_rejections_total\` | \`path\` | Which endpoints exceed the incoming rate allowance? |

\| \`gateway_bulkhead_in_flight\` | \`target\` | How much payment concurrency is occupied? |

\| \`gateway_bulkhead_rejections_total\` | \`target\` | Is the payment concurrency cap binding? |

\| \`notifications_notify_total\` | \`result\` | What fraction of notification attempts succeeded? |

\| \`notifications_request_duration_seconds\` | \`method,path\` | How much time do notification requests spend in the handler? |



**### 7.2 Counter versus gauge versus histogram**



A **\*\*Counter\*\*** represents events accumulated since a process started. \`gateway_retry_total\` and \`notifications_notify_total\` are counters. Restarting the pod resets process-local counters, and Prometheus \`rate()\` / \`increase()\` account for restarts when computing windowed values.



A **\*\*Gauge\*\*** describes the current value, including decreases. The number of payment slots presently occupied is a gauge; it returns to zero when all calls finish.



A **\*\*Histogram\*\*** tracks observed latencies in buckets. A high request count alone does not reveal whether the slowest customers are waiting too long; a histogram can be used to estimate p50, p95 and p99.



**### 7.3 Percentiles and tail latency**



\- **\*\*p50\*\*** is the median: half of requests complete within the reported time.

\- **\*\*p95\*\*** is the 95th percentile: 95% complete at or below the reported time.

\- **\*\*p99\*\*** is the 99th percentile: only about 1% are slower.



A \`/pay\` p99 of 0.073 seconds corresponds to 73 ms. It is not the maximum observed request duration. Percentiles depend on the observation window and histogram bucket precision.



PromQL for gateway p99:



\`\`\`promql

histogram_quantile(0.99, sum by (le, path) (rate(gateway_request_duration_seconds_bucket[2m])))

\`\`\`



The same pattern supports p50 and p95 by replacing \`0.99\` with \`0.50\` or \`0.95\`.



**### 7.4 Understanding the notification failure ratio**



For notification attempts, the failure share is calculated from the two \`notifications_notify_total\` counters. With 188 successes and 71 failures:



\`\`\`text

failure_share = failed / (success + failed)

failure_share = 71 / (188 + 71)

failure_share = 0.2741 = 27.4%

\`\`\`



This is separate from checkout failure rate because notifications run after checkout and do not affect the final response to the customer.



**### 7.5 Understanding retries and successful recovery**



\`retried\` counts scheduled additional attempts; \`succeeded_after_retry\` counts successful logical requests that needed at least one retry; \`exhausted\` counts logical requests that hit the attempt limit. These numbers must not be added as though they were mutually exclusive groups of user requests: a single successful checkout may contribute two \`retried\` increments and one \`succeeded_after_retry\` increment.



A good interpretation compares the error rate of underlying payment attempts to the final checkout failure rate and examines the latency added by backoff.



**### 7.6 Reading Prometheus values safely**



For counters, use \`increase(series[5m])\` when comparing experiments across a defined time range rather than relying only on raw lifetime totals. If a canary restart occurs during a test, aggregate across pod instances carefully and inspect labels to ensure old pod time series are not confused with the new deployment.



Useful queries:



\`\`\`promql

sum by (target, result) (gateway_retry_total)

sum by (to) (gateway_circuit_breaker_transitions_total)

sum by (path) (gateway_rate_limit_rejections_total)

sum by (path) (gateway_requests_total{status="429"})

sum by (target) (gateway_bulkhead_rejections_total)

sum by (result) (notifications_notify_total)

\`\`\`



**---**



**## 8. Deployment and reproducibility**



**### 8.1 Rebuild the images**



All following shell commands are intended to run **\*\*inside Kali\*\***, from the root of a local \`SRE-Intro\` checkout. GitHub Desktop on Windows is used only to copy the project files, commit, and push the branch; k3d images belong to the Kali virtual machine.



\`\`\`bash

docker build -t quickticket-notifications:lab11-case2 ./app/notifications

docker build -t quickticket-gateway:lab11-case2 ./app/gateway

k3d image import -c quickticket quickticket-notifications:lab11-case2 quickticket-gateway:lab11-case2

kubectl apply -f k8s/notifications.yaml

kubectl apply -f k8s/gateway.yaml

kubectl rollout status deployment/notifications --timeout=120s

kubectl argo rollouts status gateway --timeout=300s

\`\`\`



Check deployment state:



\`\`\`bash

kubectl get pods -o wide

kubectl get service gateway events payments notifications

kubectl get rollout gateway

kubectl describe deployment notifications

\`\`\`



Inspect the runtime environment:



\`\`\`bash

kubectl get rollout gateway -o yaml

kubectl get deployment notifications -o yaml

\`\`\`



**### 8.2 Smoke test the notifications API**



\`\`\`bash

kubectl run notify-smoke --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '

curl -s http\://notifications:8083/health

curl -s -X POST http\://notifications:8083/notify -H "Content-Type: application/json" -d "{\\"event\\":\\"order_confirmed\\",\\"order_id\\":\\"test-res-42\\"}"

curl -s http\://notifications:8083/metrics | grep -E "notifications\_(requests_total|notify_total)"

'

\`\`\`



A healthy endpoint should return HTTP 200, a valid POST should return an ID, and metrics should expose the expected series.



**### 8.3 Notification fault scenario**



Apply the mixedload resource and inject 28% notification failures plus 270 ms of latency:



\`\`\`bash

kubectl apply -f labs/lab8/mixedload.yaml

kubectl rollout status deployment/mixedload --timeout=60s

kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.28 NOTIFY_LATENCY_MS=270

kubectl rollout status deployment/notifications --timeout=120s

\`\`\`



Run 30 sequential checkout chains:



\`\`\`bash

kubectl run checkout-notify-lab11 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '

ok=0; fail=0

for i in $(seq 1 30); do

  res=$(curl -s -X POST http\://gateway:8080/events/3/reserve -H "Content-Type: application/json" -d "{\\"quantity\\":1}")

  rid=$(printf "%s" "$res" | sed -n "s/.\*reservation_id\\":\\"\\([^\\"]\*\\).\*/\1/p")

  if [ -z "$rid" ]; then fail=$((fail+1)); continue; fi

  code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http\://gateway:8080/reserve/$rid/pay")

  if [ "$code" = "200" ]; then ok=$((ok+1)); else fail=$((fail+1)); fi

  sleep 0.12

done

echo "ok=$ok fail=$fail"

'

\`\`\`



After the test, query the notification counters and gateway request duration histogram. A notification fault should create failed \`/notify\` attempts without requiring a corresponding checkout failure.



**### 8.4 Retry scenario**



\`\`\`bash

kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0

kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.28

kubectl rollout status deployment/payments --timeout=120s

\`\`\`



Repeat the 30-checkout probe and query:



\`\`\`promql

sum by (target, result) (gateway_retry_total)

histogram_quantile(0.99, sum by (le, path) (rate(gateway_request_duration_seconds_bucket[2m])))

\`\`\`



Both \`retried\` and \`succeeded_after_retry\` should be nonzero if transient payment errors are being recovered during actual traffic.



**### 8.5 Circuit-breaker scenario**



\`\`\`bash

kubectl set env deployment/payments PAYMENT_FAILURE_RATE=1.0

kubectl rollout status deployment/payments --timeout=120s

\`\`\`



Run a longer burst of checkout chains, record each HTTP status and inspect the circuit transition counter. Then restore payments and probe recovery:



\`\`\`bash

kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0

kubectl rollout status deployment/payments --timeout=120s

sleep 30

kubectl get rollout gateway

\`\`\`



The breaker only attempts a recovery transition on an incoming call after the cooldown. Merely waiting 30 seconds without subsequent traffic does not force it to close.



**### 8.6 Rate-limiter scenario**



\`\`\`bash

kubectl run event-rate-lab11 --image=curlimages/curl:latest --rm -i --restart=Never --quiet --command -- sh -c '

ok=0; limited=0; other=0

for i in $(seq 1 100); do

  status=$(curl -s -o /dev/null -w "%{http_code}" http\://gateway:8080/events)

  case "$status" in

    200) ok=$((ok+1));;

    429) limited=$((limited+1));;

    \*) other=$((other+1));;

  esac

done

echo "200=$ok 429=$limited other=$other"

curl -s -D - -o /dev/null http\://gateway:8080/events | grep -iE "^(HTTP|retry-after)"

'

\`\`\`



This test reports HTTP 200, 429 and other responses. The 429 header check is valid when the request lands on a gateway pod whose per-path window is full.



**### 8.7 Payment bulkhead scenario**



A reliable bulkhead comparison requires sufficient concurrent \`/pay\` requests. Prepare valid reservations before releasing the concurrent payment burst. Temporarily increase the limiter so it does not mask the bulkhead:



\`\`\`bash

kubectl argo rollouts set env gateway RATE_LIMIT_RPS=900

kubectl argo rollouts status gateway --timeout=300s

kubectl set env deployment/payments PAYMENT_LATENCY_MS=2800

kubectl rollout status deployment/payments --timeout=120s

\`\`\`



If \`kubectl argo rollouts set env\` is unavailable in the installed plugin version, edit the Rollout environment through \`kubectl\` or reapply an adjusted manifest. The key comparison is a bounded payment concurrency gauge and an increasing rejection counter during the burst.



The included \`scripts/lab11_burst.py\` automates sequential reservation preparation followed by concurrent payment requests. With \`kubectl port-forward service/gateway 8080:8080\` running in a separate Kali terminal, collect a JSON result by running:



\`\`\`bash

python scripts/lab11_burst.py --requests 100 --parallel 100 --output submissions/lab11_run_100.json

\`\`\`



After the experiment restore the baseline settings:



\`\`\`bash

kubectl set env deployment/payments PAYMENT_FAILURE_RATE=0.0 PAYMENT_LATENCY_MS=0

kubectl set env deployment/notifications NOTIFY_FAILURE_RATE=0.0 NOTIFY_LATENCY_MS=0

kubectl apply -f k8s/gateway.yaml

kubectl argo rollouts status gateway --timeout=300s

kubectl rollout status deployment/payments --timeout=120s

kubectl rollout status deployment/notifications --timeout=120s

\`\`\`



**---**



**## 9. Automated verification**



The project includes \`tests/test_lab11_resilience.py\`. The suite tests Python behavior directly through asynchronous callables and FastAPI's ASGI transport, without requiring a running k3d cluster.



**### 9.1 Test matrix**



\| Test | Validation |

\|---|---|

\| \`test_retry_recovers_from_503\` | Third attempt succeeds following two 503 responses |

\| \`test_retry_does_not_repeat_404\` | Ordinary 404 is not retried |

\| \`test_breaker_opens_and_recovers\` | Breaker opens at threshold 4 and returns to CLOSED |

\| \`test_sliding_window_keeps_keys_separate\` | 11 accepted and 3 rejected within a window, paths isolated |

\| \`test_bulkhead_rejects_when_full\` | 9 active and 4 rejected with 13 simultaneous coroutines |

\| \`test_notifications_health_and_delivery\` | \`/health\`, \`/notify\`, and \`/metrics\` respond correctly |

\| \`test_429_appears_in_gateway_request_metrics\` | Rate-limited response is included in HTTP metrics |

\| \`test_retry_recovers_after_rate_limit\` | An upstream 429 is retryable |

\| \`test_retry_exhaustion_on_transient_error\` | Connection errors stop after the attempt budget |

\| \`test_circuit_fast_reject_when_open\` | OPEN circuit does not call the dependency |

\| \`test_rate_limiter_expires_old_requests\` | Old window entries expire |

\| \`test_invalid_notification_payload\` | Invalid notification data returns 422 |



**### 9.2 Run locally**



\`\`\`bash

python3 -m venv .venv

source .venv/bin/activate

pip install -r app/notifications/requirements.txt httpx pytest

python -m pytest -q tests/test_lab11_resilience.py

\`\`\`



Automated suite result from the provided source tree:



\`\`\`text

............                                                             [100%]

12 passed in 1.19s

\`\`\`



This verifies the listed functions and routes. It does not by itself verify traffic distribution among Kubernetes pods, Argo Rollout analysis, or any cluster-dependent latency metric.



**---**



**## 10. Technical discussion**



**### 10.1 Reliability versus latency**



Retry increases the probability that transient failures recover, but introduces extra downstream calls and backoff sleeps. A smaller user-visible error percentage is therefore often accompanied by a larger p99 latency. These two metrics should be evaluated together rather than optimizing one in isolation.



**### 10.2 Fast-fail is not the same as failure prevention**



An OPEN circuit immediately returns 503, reducing avoidable load on an unavailable payment service. This improves containment and recovery behavior; it does not make an unavailable payment service successful. Clients still need a suitable error message and potentially a retry policy with a meaningful delay.



**### 10.3 Notification delivery guarantee**



\`asyncio.create_task()\` gives best-effort asynchronous delivery. If the gateway process terminates before the task finishes, the notification can be lost. A production system would use a transactional outbox, message queue and dedicated worker with controlled redelivery.



**### 10.4 Payment idempotency**



Retrying a charge is safe only if duplicate submissions do not produce duplicate charges. A real payment API should accept an idempotency key based on the reservation or logical operation and return the original result for repeated attempts.



**### 10.5 Reservation confirmation risks**



Payment success and reservation confirmation are separate network operations. If events returns HTTP 500 after a successful charge, the customer may have paid without obtaining a confirmed ticket. The gateway correctly reports this condition as a failure, but preventing it requires a transactional workflow, reconciliation process or compensating refund action.



**### 10.6 Why route normalization matters**



Both metrics and rate-limiter keys should group logically identical routes. Raw UUIDs or event identifiers would generate high-cardinality metric labels and allow each new reservation ID to consume a fresh local rate-limit key. Normalization keeps label cardinality manageable and makes aggregate route-level statistics meaningful.



**### 10.7 Why five replicas change the interpretation**



Each gateway pod is a separate process. A burst of 100 requests can reach different local queues depending on connection reuse, kube-proxy routing and request timing. Because no centralized limiter or breaker is used, the observed counts can differ from a simple arithmetic split of exactly 20 requests per pod.



**### 10.8 Suggested future improvements**



1\. Add a distributed or edge rate limiter when a true global traffic quota is necessary.

2\. Allow only one HALF_OPEN probe per payment breaker to prevent a recovery stampede.

3\. Persist notifications in a durable queue rather than relying only on an in-process task.

4\. Introduce payment idempotency keys and cross-service reconciliation.

5\. Use per-dependency HTTP client pools and separate timeouts for payments, events and notifications.

6\. Add a dashboard showing retries, breaker transitions, bulkhead saturation, HTTP 429, and p99 checkout latency.



**## Conclusion**



The fourth QuickTicket microservice provides a non-critical notification destination, while the gateway implements bounded retries, circuit-breaker protection, endpoint-specific request limits and a payment concurrency bulkhead. The combination separates temporary downstream failure from sustained dependency failure and local overload.



The most important outcome is that every pattern has a distinct operational signal: retry counters show recovery attempts, circuit transitions show dependency isolation, HTTP 429 identifies ingress throttling, and bulkhead gauges/rejections identify concurrency pressure. The included tests verify the application-level behaviors, and the supplied k3d commands provide a reproducible way to collect cluster-level evidence under configured fault injection.
