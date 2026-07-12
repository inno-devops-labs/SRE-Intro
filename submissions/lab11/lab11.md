# Lab 11 — Advanced Microservice Patterns

## Task 1 — Notifications Service + Retries

### 11.1: Notifications service

Создан сервис `app/notifications` на порту 8083. `POST /notify` учитывает
`NOTIFY_FAILURE_RATE` и `NOTIFY_LATENCY_MS`, логирует событие и публикует
метрики Prometheus. Также реализованы `GET /health` и `GET /metrics`.

Ключевая часть `main.py`:

```python
@app.post("/notify")
def notify(body: dict | None = None):
    payload = body or {}
    event = payload.get("event", "unknown")
    order_id = payload.get("order_id", "unknown")

    if NOTIFY_LATENCY_MS > 0:
        log.info(f"Injecting {NOTIFY_LATENCY_MS}ms latency for order={order_id}")
        time.sleep(NOTIFY_LATENCY_MS / 1000)

    if random.random() < NOTIFY_FAILURE_RATE:
        NOTIFY_TOTAL.labels("failed").inc()
        log.warning(f"Notification failed (injected): event={event} order={order_id}")
        raise HTTPException(500, "Notification delivery failed")

    NOTIFY_TOTAL.labels("success").inc()
    log.info(f"Notification sent: event={event} order={order_id}")
    return {"status": "sent", "event": event, "order_id": order_id}
```

`requirements.txt`:

```text
fastapi==0.136.0
uvicorn==0.44.0
prometheus-client==0.25.0
```

Образ `quickticket-notifications:v1` успешно собран. Локальная smoke-проверка
контейнера дала:

```text
GET /health
{"status":"healthy","failure_rate":0.0,"latency_ms":0}

POST /notify
{"status":"sent","event":"order_confirmed","order_id":"test-11"}

notifications_requests_total{method="GET",path="/health",status="200"} 1.0
notifications_requests_total{method="POST",path="/notify",status="200"} 1.0
notifications_request_duration_seconds_count{method="POST",path="/notify"} 1.0
notifications_notify_total{result="success"} 1.0
```

### 11.2: Kubernetes Deployment and Service

Создан `k8s/notifications.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notifications
spec:
  replicas: 1
  selector:
    matchLabels:
      app: notifications
  template:
    metadata:
      labels:
        app: notifications
    spec:
      containers:
        - name: notifications
          image: quickticket-notifications:v1
          imagePullPolicy: Never
          ports:
            - containerPort: 8083
          env:
            - name: NOTIFY_FAILURE_RATE
              value: "0.0"
            - name: NOTIFY_LATENCY_MS
              value: "0"
---
apiVersion: v1
kind: Service
metadata:
  name: notifications
spec:
  type: ClusterIP
  selector:
    app: notifications
  ports:
    - port: 8083
      targetPort: 8083
```

В `k8s/gateway.yaml` добавлен `NOTIFICATIONS_URL=http://notifications:8083`, а
также переменные настройки retry, circuit breaker, rate limiter и bulkhead.

### 11.4: Retry with exponential backoff and jitter

```python
async def call_with_retry(func, target: str, max_retries: int = RETRY_MAX):
    base_delay = RETRY_BASE_DELAY_MS / 1000

    for attempt in range(max_retries):
        try:
            result = await func()
            if attempt > 0:
                RETRY_TOTAL.labels(target, "succeeded_after_retry").inc()
            return result
        except Exception as exc:
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                retryable = status >= 500 or status in (408, 429)

            if not retryable:
                RETRY_TOTAL.labels(target, "non_retryable").inc()
                raise
            if attempt == max_retries - 1:
                RETRY_TOTAL.labels(target, "exhausted").inc()
                raise

            delay = base_delay * (2**attempt) + random.uniform(0, base_delay)
            RETRY_TOTAL.labels(target, "retried").inc()
            await asyncio.sleep(delay)
```

Повторяются только временные ошибки: timeout, connect error, HTTP 5xx, 408 и
429. Остальные 4xx немедленно возвращаются вызывающей стороне.

### Ответы на вопросы Task 1

- Notifications должны быть fire-and-forget, потому что доставка уведомления
  не является частью критического checkout path. Её задержка или отказ не
  должны увеличивать latency оплаты, отменять уже успешную оплату или создавать
  ложный отказ для пользователя. Для production-варианта надёжнее использовать
  очередь сообщений и отдельный worker.
- Композиция `cb.call(retry(_charge))` правильна: circuit breaker наблюдает один
  итоговый исход пользовательской операции после внутренних повторов. При
  `retry(lambda: cb.call(_charge))` retry может повторять `CircuitOpenError`,
  обходя смысл fast-fail и создавая лишнюю работу.

## Task 2 — Circuit Breaker + Rate Limiter

### 11.7: Circuit breaker

```python
async def call(self, func):
    if self.state == self.OPEN:
        if time.time() - self.opened_at >= self.cooldown:
            self._transition(self.HALF_OPEN)
        else:
            raise CircuitOpenError(f"circuit[{self.name}] OPEN")

    try:
        result = await func()
    except Exception:
        self.failures += 1
        self.opened_at = time.time()
        if self.state == self.HALF_OPEN or self.failures >= self.threshold:
            self._transition(self.OPEN)
        raise

    self.failures = 0
    self._transition(self.CLOSED)
    return result
```

Состояния реализованы как `CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN`. Пока
cooldown не закончился, вызовы завершаются быстрым `CircuitOpenError`, который
gateway преобразует в HTTP 503.

### 11.8: Sliding-window rate limiter

```python
def allow(self, key: str) -> bool:
    now = time.time()
    q = self.hits[key]
    cutoff = now - self.window_s
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= self.rps:
        return False
    q.append(now)
    return True
```

Лимит ведётся отдельно для нормализованного endpoint path. Ответ 429 содержит
`Retry-After: 1`, а отказ учитывается в
`gateway_rate_limit_rejections_total{path}`. Поскольку состояние находится в
памяти процесса, суммарный предел равен `RATE_LIMIT_RPS × число gateway pods`.

## Bonus Task — Bulkhead Isolation

### 11.9: Bounded payment pool

```python
class BulkheadFullError(Exception):
    pass


class Bulkhead:
    def __init__(self, name: str, max_concurrent: int, acquire_timeout_s: float):
        self.name = name
        self.acquire_timeout_s = acquire_timeout_s
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def call(self, func):
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(), timeout=self.acquire_timeout_s
            )
        except asyncio.TimeoutError as exc:
            BULKHEAD_REJECTIONS.labels(self.name).inc()
            raise BulkheadFullError(f"bulkhead[{self.name}] full") from exc

        BULKHEAD_IN_FLIGHT.labels(self.name).inc()
        try:
            return await func()
        finally:
            BULKHEAD_IN_FLIGHT.labels(self.name).dec()
            self.semaphore.release()
```

В payment path используется композиция:

```python
pay_resp = await payments_bulkhead.call(
    lambda: payments_cb.call(
        lambda: call_with_retry(_charge, target="payments")
    )
)
```

Ошибки `BulkheadFullError` и `CircuitOpenError` преобразуются в понятный HTTP
503. Экспортируются Gauge `gateway_bulkhead_in_flight{target}` и Counter
`gateway_bulkhead_rejections_total{target}`.

### Ответы на вопросы Bonus

- Bulkhead находится снаружи CB и retry, чтобы одна checkout-операция занимала
  ровно один слот на всё время своих повторов. Если захватывать слот внутри
  retry, каждая попытка конкурирует отдельно и предел перестаёт описывать число
  активных пользовательских операций. Fast-fail CB удерживает слот лишь на
  очень короткое время.
- Rate limiter ограничивает скорость входящих запросов за временное окно и
  защищает сервис от слишком высокого RPS. Bulkhead ограничивает одновременно
  выполняемые обращения к конкретной зависимости и не даёт медленному payments
  исчерпать ресурсы, нужные events и другим маршрутам.

## Проверка fault injection в k3d

Для проверки поднят кластер `quickticket` на k3d v5.9.0. Развёрнуты PostgreSQL,
Redis, events, payments, notifications, пять реплик gateway и внутрикластерный
Prometheus. Перед каждым тестом fault-injection параметры возвращались в
исходное состояние.

### Test #1 — fire-and-forget notifications

При `NOTIFY_FAILURE_RATE=0.3` и `NOTIFY_LATENCY_MS=300`:

```text
result: ok=30 fail=0

notifications_notify_total{result="success"} 23.0
notifications_notify_total{result="failed"} 7.0
```

Фактическая доля отказов notifications: `7 / 30 = 23.3%`. Все пользовательские
checkout завершились успешно, несмотря на эти отказы.

Prometheus-запрос p99 дал для payment endpoint:

```text
path="/reserve/{id}/pay"  0.024686956746510426
```

p99 равен примерно **24.7 ms**, то есть существенно меньше 100 ms и не включает
инъецированные 300 ms notifications. Это подтверждает настоящий
fire-and-forget вызов.

### Test #2 — retry при временных отказах payments

При `PAYMENT_FAILURE_RATE=0.3` первый случайный прогон дал `28/30`, а повторный
независимый прогон дал ожидаемый результат:

```text
result: ok=30 fail=0
```

Prometheus после первого прогона:

```text
gateway_retry_total{target="payments",result="retried"} 11
gateway_retry_total{target="payments",result="succeeded_after_retry"} 5
gateway_retry_total{target="payments",result="exhausted"} 2
```

Таким образом, повторы не только настроены, но действительно выполнялись и
успешно восстанавливали часть checkout.

### Circuit breaker

При `PAYMENT_FAILURE_RATE=1.0` выполнено 80 checkout attempts:

```text
500s=0 503s=76 other=3
gateway_circuit_breaker_transitions_total{to="OPEN"} 4
```

Три `other` — это 429 от per-pod rate limiter во время быстрого burst. В данной
реализации gateway и исчерпанные HTTP 5xx, и открытый circuit преобразует в
пользовательский HTTP 503, поэтому разбивка на 500 и fast-fail 503 по одному
только status code невозможна. Само открытие подтверждено отдельной метрикой.
После дополнительных обращений OPEN был достигнут на всех пяти gateway pods.

После восстановления payments и окончания 30-секундного cooldown:

```text
recovery: 200=15 other=0

gateway_circuit_breaker_transitions_total{to="OPEN"} 5
gateway_circuit_breaker_transitions_total{to="HALF_OPEN"} 5
gateway_circuit_breaker_transitions_total{to="CLOSED"} 5
```

Каждая из пяти локальных CB-машин прошла через HALF_OPEN и закрылась.

### Rate limiter

Burst из 100 запросов при пяти gateway pods и `RATE_LIMIT_RPS=10`:

```text
200=50 429=50 other=0

HTTP/1.1 429 Too Many Requests
retry-after: 1
```

Поток ниже лимита:

```text
sustained: 200=30 429=0
```

Prometheus:

```text
gateway_rate_limit_rejections_total{path="/events"} 72
```

Значение включает burst для проверки заголовка после основного теста.

### Bonus — bulkhead isolation

Для точного доказательства cap gateway был временно уменьшен до одной реплики:
bulkhead является per-process, поэтому при пяти pods кластерный cap был бы 50,
а 30 одновременных запросов не обязаны вызвать насыщение. `RATE_LIMIT_RPS` на
время теста повышен до 100, чтобы rate limiter не подменял собой bulkhead.

При `PAYMENT_LATENCY_MS=3000`, `BULKHEAD_PAYMENTS_MAX=10` и 30 заранее созданных
reservations:

```text
10 × pay HTTP 200
20 × pay HTTP 503
reservations=30 EVENTS: ok=30 slow=0

gateway_bulkhead_rejections_total{target="payments"} 20
gateway_bulkhead_in_flight{target="payments"} 10
```

То есть cap действительно связал на 10: следующие 20 операций завершились
fast-fail, а все 30 обращений к `/events` остались быстрее 500 ms. Для надёжного
снятия Gauge latency временно увеличивалась до 10 секунд; Prometheus увидел
одновременную occupancy ровно 10.

Контрольный прогон с практически отключённым cap
(`BULKHEAD_PAYMENTS_MAX=100`) дал:

```text
without-cap reservations=30 EVENTS: ok=30 slow=0
```

В отличие от ожидаемого в тексте лабораторной результата `slow=30`, async
gateway не замедлил `/events`: ожидание `httpx.AsyncClient` освобождает event
loop. Поэтому изоляция доказана cap/rejection/Gauge-метриками и быстрым
fast-fail, но обещанное заданием блокирование event loop без bulkhead на этой
реализации не воспроизводится. Фактический результат приведён без подмены.

После тестов восстановлены `PAYMENT_FAILURE_RATE=0`, `PAYMENT_LATENCY_MS=0`,
`BULKHEAD_PAYMENTS_MAX=10`, `RATE_LIMIT_RPS=10` и пять gateway replicas. Все
Deployment находятся в состоянии Ready.

## Итог

- [x] Task 1: notifications, manifest, fire-and-forget wiring, retry.
- [x] Task 2: circuit breaker и rate limiter.
- [x] Bonus Task: bulkhead, метрики и правильная композиция.
- [x] Кластерные fault-injection замеры выполнены в k3d.
