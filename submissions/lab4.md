# Лабораторная 4 — Kubernetes

Выполнены Task 1, Task 2 и бонус Helm. Проверено 21 сентября 2026 года на
Docker Desktop (Apple Silicon), k3d 5.9.0, k3s и kubectl 1.33.3, Helm 4.3.0.
Времена экспериментов ниже — UTC; `helm list` выводит местное время UTC+3.
Использован отдельный локальный кластер `quickticket`. Код приложения взят из
`main` (cf2ad63); незавершенные изменения лабораторной 2 не требуются.

Манифесты: [k8s](../k8s/), инструкции воспроизведения: [README](../k8s/README.md),
бонус: [Helm chart](../k8s/chart/). Полные доказательства: [evidence/lab4](evidence/lab4/).

## Task 1 — Развертывание и самовосстановление

### Кластер и образы

```bash
k3d cluster create quickticket --image rancher/k3s:v1.33.3-k3s1
kubectl --context k3d-quickticket get nodes
```
```text
NAME                       STATUS   ROLES                  AGE    VERSION
k3d-quickticket-server-0   Ready    control-plane,master   4m6s   v1.33.3+k3s1
```

Образы собраны из Dockerfile каждой службы:

```bash
docker build -t quickticket-gateway:v1 app/gateway
docker build -t quickticket-events:v1 app/events
docker build -t quickticket-payments:v1 app/payments
```

Обычный `k3d image import` сначала завершился ошибкой `content digest ... not found`.
Исправление для Docker Desktop с containerd: экспорт только нужной платформы
([описание аналогичной проблемы в k3d](https://github.com/k3d-io/k3d/issues/1538)).

```bash
docker save --platform linux/arm64 -o /tmp/quickticket-arm64.tar \
  quickticket-gateway:v1 quickticket-events:v1 quickticket-payments:v1 \
  postgres:17-alpine redis:7-alpine
k3d image import /tmp/quickticket-arm64.tar -c quickticket
kubectl --context k3d-quickticket apply -f k8s/
```

В каждой из пяти служб есть Deployment и ClusterIP Service. У Python-приложений
`imagePullPolicy: Never`, у баз — `IfNotPresent`. Init containers у events ждут
PostgreSQL и Redis; одного порядка применения YAML недостаточно для готовности зависимостей.
PostgreSQL использует `emptyDir`, поэтому это учебная непостоянная БД.

### Все службы и данные

```bash
kubectl --context k3d-quickticket exec -i deployment/postgres -- \
  psql -U quickticket -d quickticket -f /dev/stdin < app/seed.sql
```
```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

```bash
kubectl --context k3d-quickticket get pods,svc
```
```text
NAME                            READY   STATUS    RESTARTS   AGE
pod/events-56ff56dd58-jgtbt     1/1     Running   0          89s
pod/gateway-6764b89544-m9hzj    1/1     Running   0          89s
pod/payments-58f46b478f-srrl7   1/1     Running   0          89s
pod/postgres-56db6d697c-k4pgz   1/1     Running   0          89s
pod/redis-6d695466ff-776qn      1/1     Running   0          89s

NAME                 TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)    AGE
service/events       ClusterIP   10.43.28.138   <none>        8081/TCP   89s
service/gateway      ClusterIP   10.43.7.59     <none>        8080/TCP   89s
service/kubernetes   ClusterIP   10.43.0.1      <none>        443/TCP    4m8s
service/payments     ClusterIP   10.43.21.13    <none>        8082/TCP   89s
service/postgres     ClusterIP   10.43.79.75    <none>        5432/TCP   89s
service/redis        ClusterIP   10.43.132.8    <none>        6379/TCP   89s
```

Использован порт 3084, потому что 3080 занят параллельным Compose-стендом лабораторной 3.

```bash
kubectl --context k3d-quickticket port-forward svc/gateway 3084:8080
curl -s http://localhost:3084/events
```
```json
[{"id":1,"name":"Go Conference 2026","venue":"Main Hall A","date":"2026-09-15T09:00:00+00:00","total_tickets":100,"price_cents":5000,"available":100},{"id":4,"name":"Python Workshop","venue":"Lab 301","date":"2026-09-22T14:00:00+00:00","total_tickets":25,"price_cents":2000,"available":25},{"id":2,"name":"SRE Meetup","venue":"Room 204","date":"2026-10-01T18:00:00+00:00","total_tickets":30,"price_cents":0,"available":30},{"id":5,"name":"Kubernetes Deep Dive","venue":"Auditorium B","date":"2026-10-10T10:00:00+00:00","total_tickets":80,"price_cents":8000,"available":80},{"id":3,"name":"Cloud Native Summit","venue":"Expo Center","date":"2026-11-20T10:00:00+00:00","total_tickets":500,"price_cents":15000,"available":500}]
```

```bash
curl -s http://localhost:3084/health
```
```json
{"status":"healthy","checks":{"events":"ok","payments":"ok","circuit_payments":"CLOSED"}}
```

### Удаление gateway

```bash
kubectl --context k3d-quickticket get pods -l app=gateway -w
# В другом терминале:
kubectl --context k3d-quickticket delete pod -l app=gateway --wait=false
```
```text
Deletion started: 2026-09-21T08:23:33.324759+00:00
NAME                       READY   STATUS    RESTARTS   AGE
gateway-6764b89544-m9hzj   1/1     Running   0          118s
pod "gateway-6764b89544-m9hzj" deleted
gateway-6764b89544-m9hzj   1/1     Terminating   0          118s
gateway-6764b89544-lv8nz   0/1     Pending       0          0s
gateway-6764b89544-lv8nz   0/1     Pending       0          0s
gateway-6764b89544-lv8nz   0/1     ContainerCreating   0          0s
gateway-6764b89544-m9hzj   0/1     Completed           0          118s
gateway-6764b89544-lv8nz   0/1     Running             0          1s
gateway-6764b89544-m9hzj   0/1     Completed           0          119s
gateway-6764b89544-m9hzj   0/1     Completed           0          119s
gateway-6764b89544-lv8nz   0/1     Running             0          4s
gateway-6764b89544-lv8nz   1/1     Running             0          4s
New pod Ready after 4.59s: gateway-6764b89544-lv8nz
```

**Ответ:** новый pod стал Ready через **4,59 с** от начала команды удаления.
Deployment/ReplicaSet автоматически восстановил желаемую реплику; вмешательство оператора
не потребовалось. В лабораторной 1 после явного `docker compose stop` нужно было вручную
выполнить `start`; даже политика restart не отменяет намеренную остановку контейнера.
Одной реплики недостаточно для непрерывной доступности: пока новая не Ready, есть окно отказа.
Port-forward привязан к выбранному pod, поэтому после его удаления туннель нужно создать заново.

## Task 2 — Probes и ресурсы

### Настроенные проверки

```bash
kubectl --context k3d-quickticket describe pod -l app=gateway
```
```text
    Liveness:   tcp-socket :8080 delay=10s timeout=1s period=10s #success=1 #failure=3
    Readiness:  http-get http://:8080/health delay=0s timeout=5s period=5s #success=1 #failure=2
    Startup:    tcp-socket :8080 delay=0s timeout=1s period=2s #success=1 #failure=30
```

Полный вывод: [gateway-describe.txt](evidence/lab4/gateway-describe.txt).
Readiness использует `/health`; liveness — TCP-порт процесса. Это осознанное отличие
от примера с `/health` для обеих проверок: в приложении health gateway/events зависит
от других служб, и такая liveness создавала бы каскад перезапусков при отказе Redis/БД.
TCP проверяет только доступность listener, а не полную работоспособность бизнес-логики.

### Отказ Redis и исключение events из готовых endpoints

Сначала выполнено `kubectl delete pod -l app=redis`: новая реплика стала Ready примерно
за секунду, readiness events не успела зафиксировать отказ. Для воспроизводимого наблюдения
Redis затем масштабирован до нуля, что удаляет pod и удерживает зависимость недоступной:

```bash
kubectl --context k3d-quickticket scale deployment/redis --replicas=0
kubectl --context k3d-quickticket get pods -w
kubectl --context k3d-quickticket get endpointslices \
  -l kubernetes.io/service-name=events -o yaml
kubectl --context k3d-quickticket scale deployment/redis --replicas=1
```

Выдержка из [полной записи](evidence/lab4/redis-readiness.txt):

```text
2026-09-21T08:24:42+00:00 events became NotReady after 13.98s
events-56ff56dd58-jgtbt     0/1     Running   0          3m7s
```

```yaml
conditions:
  ready: false
  serving: false
  terminating: false
```

После восстановления Redis:

```text
2026-09-21T08:24:44+00:00 deployment "events" successfully rolled out
events-56ff56dd58-jgtbt     1/1     Running   0          3m9s
```

Pod events сохранил имя и **0 рестартов**. В [describe events](evidence/lab4/events-unready-describe.txt)
зафиксирован timeout readiness: `/health` не успел ответить за 5 секунд, пока Redis
был недоступен (`context deadline exceeded`). Redis восстановлен до одной реплики.

**Ответ:** readiness failure убирает pod из готовых Service endpoints, но не перезапускает
контейнер; liveness failure после порога ошибок приводит к рестарту контейнера.
Связь с БД нужно проверять readiness: перезапуск приложения не исправит недоступную БД
и может усилить нагрузку при ее восстановлении.

### Запросы и лимиты ресурсов

У каждого контейнера, включая init containers: requests `50m CPU / 64Mi`, limits
`200m CPU / 256Mi`. Вывод узла включает также системные pod k3s:

```bash
kubectl --context k3d-quickticket describe node k3d-quickticket-server-0
```
```text
Allocated resources:
  (Total limits may be over 100 percent, i.e., overcommitted.)
  Resource           Requests    Limits
  --------           --------    ------
  cpu                450m (4%)   1 (10%)
  memory             460Mi (5%)  1450Mi (18%)
  ephemeral-storage  0 (0%)      0 (0%)
  hugepages-1Gi      0 (0%)      0 (0%)
  hugepages-2Mi      0 (0%)      0 (0%)
  hugepages-32Mi     0 (0%)      0 (0%)
  hugepages-64Ki     0 (0%)      0 (0%)
Events:
```

## Бонус — Helm

Chart сохраняет Deployment/Service для всех пяти компонентов и позволяет менять
реплики, образы, env, probes и ресурсы через values. Порты и имена служб соответствуют
raw-манифестам. Raw YAML сохранены для следующих лабораторных.

```bash
helm lint k8s/chart
helm template quickticket k8s/chart
```

```text
1 chart(s) linted, 0 chart(s) failed
PASS: 10 rendered resources; raw/chart container parity; custom replicas and payment latency
```

Дополнительно проверены `--set gateway.replicas=2` и
`--set payments.env.PAYMENT_LATENCY_MS=250` в сгенерированных Deployment.
Raw-манифесты прошли `kubectl apply --dry-run=server -f k8s/`.

### Chart.yaml

```yaml
apiVersion: v2
name: quickticket
description: QuickTicket SRE learning project
type: application
version: 0.1.0
appVersion: "1.0.0"
```

### values.yaml

```yaml
# Local lab defaults, including demo database credentials. One release per namespace.
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 256Mi
events:
  replicas: 1
  image: quickticket-events:v1
  imagePullPolicy: Never
  port: 8081
  env:
    DB_HOST: postgres
    DB_PORT: '5432'
    DB_NAME: quickticket
    DB_USER: quickticket
    DB_PASS: quickticket
    DB_MAX_CONNS: '10'
    REDIS_HOST: redis
    REDIS_PORT: '6379'
    REDIS_TIMEOUT_MS: '1000'
    RESERVATION_TTL: '300'
  startupProbe:
    tcpSocket:
      port: 8081
    periodSeconds: 2
    failureThreshold: 30
  readinessProbe:
    httpGet:
      path: /health
      port: 8081
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 2
  livenessProbe:
    tcpSocket:
      port: 8081
    initialDelaySeconds: 10
    periodSeconds: 10
    failureThreshold: 3
gateway:
  replicas: 1
  image: quickticket-gateway:v1
  imagePullPolicy: Never
  port: 8080
  env:
    EVENTS_URL: http://events:8081
    PAYMENTS_URL: http://payments:8082
    GATEWAY_TIMEOUT_MS: '5000'
  startupProbe:
    tcpSocket:
      port: 8080
    periodSeconds: 2
    failureThreshold: 30
  readinessProbe:
    httpGet:
      path: /health
      port: 8080
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 2
  livenessProbe:
    tcpSocket:
      port: 8080
    initialDelaySeconds: 10
    periodSeconds: 10
    failureThreshold: 3
postgres:
  replicas: 1
  image: postgres:17-alpine
  imagePullPolicy: IfNotPresent
  port: 5432
  env:
    POSTGRES_DB: quickticket
    POSTGRES_USER: quickticket
    POSTGRES_PASSWORD: quickticket
  readinessProbe:
    exec:
      command:
      - pg_isready
      - -U
      - quickticket
      - -d
      - quickticket
    periodSeconds: 5
    timeoutSeconds: 3
  livenessProbe:
    tcpSocket:
      port: 5432
    initialDelaySeconds: 10
    periodSeconds: 10
payments:
  replicas: 1
  image: quickticket-payments:v1
  imagePullPolicy: Never
  port: 8082
  env:
    PAYMENT_FAILURE_RATE: '0.0'
    PAYMENT_LATENCY_MS: '0'
  startupProbe:
    tcpSocket:
      port: 8082
    periodSeconds: 2
    failureThreshold: 30
  readinessProbe:
    httpGet:
      path: /health
      port: 8082
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 2
  livenessProbe:
    tcpSocket:
      port: 8082
    initialDelaySeconds: 10
    periodSeconds: 10
    failureThreshold: 3
redis:
  replicas: 1
  image: redis:7-alpine
  imagePullPolicy: IfNotPresent
  port: 6379
  env: {}
  readinessProbe:
    exec:
      command:
      - redis-cli
      - ping
    periodSeconds: 5
    timeoutSeconds: 3
  livenessProbe:
    tcpSocket:
      port: 6379
    initialDelaySeconds: 10
    periodSeconds: 10
```

### Установка

```bash
kubectl --context k3d-quickticket delete -f k8s/
helm install quickticket k8s/chart --kube-context k3d-quickticket --wait --timeout 3m
# В новой БД снова загружен seed.sql.
helm list --kube-context k3d-quickticket
```
```text
NAME       	NAMESPACE	REVISION	UPDATED                             	STATUS  	CHART            	APP VERSION
quickticket	default  	1       	2026-09-21 11:25:59.605769 +0300 MSK	deployed	quickticket-0.1.0	1.0.0
```

```bash
kubectl --context k3d-quickticket get pods
```
```text
NAME                        READY   STATUS    RESTARTS   AGE
events-7867fcfd55-ktjwd     1/1     Running   0          14s
gateway-74c8b87478-5skbd    1/1     Running   0          14s
payments-7794787857-z7wx2   1/1     Running   0          14s
postgres-8547cb95cc-5wxhm   1/1     Running   0          14s
redis-5dbb6fcbff-lp5zx      1/1     Running   0          14s
```

Опциональный kube-prometheus-stack не устанавливался; для бонуса проверен собственный
Helm chart. Мониторинг лабораторной 3 работает в отдельном Compose-стенде.

После Helm-установки через новый port-forward проверены `/health`, пять событий
и полный checkout (`reserve → pay → confirm`):

```json
{
  "health": {
    "status": "healthy",
    "checks": {
      "events": "ok",
      "payments": "ok",
      "circuit_payments": "CLOSED"
    }
  },
  "event_count": 5,
  "checkout": {
    "order_id": "cc69aeef-7da2-403d-82da-effd2f3b8002",
    "event_id": 3,
    "quantity": 1,
    "total_cents": 15000,
    "status": "confirmed"
  }
}
```
