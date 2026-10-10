# SRE Intro — Lab 4: Kubernetes, Probes, Resource Limits and Helm

## 1. Цель работы

Развернуть приложение QuickTicket в локальном Kubernetes-кластере k3d, настроить Kubernetes Deployments и Services, проверить self-healing и readiness/liveness probes, задать CPU/memory requests и limits, а также выполнить дополнительное задание с Helm.

## 2. Окружение

- OS: macOS
- Docker: 29.8.1
- k3d: v5.9.0
- Kubernetes/k3s: v1.35.5+k3s1
- Helm: v4.3.0
- Cluster: `quickticket`
- Kubernetes context: `k3d-quickticket`

Кластер создан командой:

```bash
k3d cluster create quickticket
```

Проверка:

```bash
kubectl cluster-info
kubectl get nodes
```

Node `k3d-quickticket-server-0` находится в состоянии `Ready`.

---

# Task 1 — Kubernetes Deployment и Services

## 3. Сборка Docker-образов

Были собраны три образа приложения:

```bash
docker build -t quickticket-gateway:v1 ./gateway
docker build -t quickticket-events:v1 ./events
docker build -t quickticket-payments:v1 ./payments
```

Образы импортированы в k3d:

```bash
k3d image import quickticket-gateway:v1 -c quickticket
k3d image import quickticket-events:v1 -c quickticket
k3d image import quickticket-payments:v1 -c quickticket
```

Проверка образов выполнялась через containerd:

```bash
docker exec k3d-quickticket-server-0 crictl images
```

Все три application images присутствуют в кластере.

## 4. Kubernetes-манифесты

Созданы следующие файлы:

- `k8s/postgres.yaml`
- `k8s/redis.yaml`
- `k8s/gateway.yaml`
- `k8s/events.yaml`
- `k8s/payments.yaml`

Развёртывание:

```bash
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/gateway.yaml
kubectl apply -f k8s/events.yaml
kubectl apply -f k8s/payments.yaml
```

В результате созданы 5 Deployments:

- `gateway`
- `events`
- `payments`
- `postgres`
- `redis`

и 5 соответствующих ClusterIP Services.

## 5. Инициализация PostgreSQL

При первом запуске Events обнаружилась ошибка:

```text
psycopg2.errors.UndefinedTable: relation "events" does not exist
```

Был найден `app/seed.sql`, содержащий создание таблиц `events` и `orders`, а также тестовые данные.

Seed был применён:

```bash
kubectl exec -i postgres-7c7ffc4b-ldr58 -- psql -U quickticket -d quickticket < SRE-Intro/app/seed.sql
```

Результат:

```text
CREATE TABLE
CREATE TABLE
INSERT 0 5
```

## 6. Проверка Gateway

Для проверки использовался port-forward:

```bash
kubectl port-forward svc/gateway 3080:8080
```

Health endpoint:

```bash
curl http://localhost:3080/health
```

Результат:

```json
{
  "status": "healthy",
  "checks": {
    "events": "ok",
    "payments": "ok",
    "circuit_payments": "CLOSED"
  }
}
```

Проверка списка событий:

```bash
curl http://localhost:3080/events
```

Вернулись тестовые события:

- Go Conference 2026
- Python Workshop
- SRE Meetup
- Kubernetes Deep Dive
- Cloud Native Summit

## 7. Self-healing

Для проверки автоматического восстановления был удалён Gateway pod:

```bash
kubectl delete pod -l app=gateway
```

Kubernetes автоматически создал новый pod благодаря Deployment.

После восстановления новый Gateway снова успешно отвечал на `/health` и `/events`.

## 8. Итог Task 1

Проверка:

```bash
kubectl get deployments
kubectl get services
kubectl get pods
```

Все основные компоненты работают в Kubernetes.

---

# Task 2 — Probes и Resource Limits

## 9. Readiness и Liveness probes

Для `gateway`, `events` и `payments` настроены HTTP probes на `/health`.

### Gateway

- readiness: `/health`, port `8080`
- liveness: `/health`, port `8080`

### Events

- readiness: `/health`, port `8081`
- liveness: `/health`, port `8081`

### Payments

- readiness: `/health`, port `8082`
- liveness: `/health`, port `8082`

Настройки:

```yaml
readinessProbe:
  httpGet:
    path: /health
    port: <PORT>
  initialDelaySeconds: 5
  periodSeconds: 5

livenessProbe:
  httpGet:
    path: /health
    port: <PORT>
  initialDelaySeconds: 10
  periodSeconds: 10
```

### Разница между probes

**Readiness probe** определяет, готов ли pod принимать трафик. Если readiness probe не проходит, pod остаётся запущенным, но Kubernetes исключает его из Service endpoints.

**Liveness probe** определяет, работает ли контейнер корректно. Если liveness probe продолжительно не проходит, Kubernetes перезапускает контейнер.

## 10. Проверка readiness при отказе Redis

Сначала Redis был масштабирован до нуля:

```bash
kubectl scale deployment/redis --replicas=0
```

После отключения Redis:

```text
events     0/1 Running
gateway    0/1 Running
payments   1/1 Running
postgres   1/1 Running
```

Таким образом, `events` перестал считаться Ready из-за недоступной зависимости Redis. Gateway также перестал считаться Ready, поскольку его health check зависит от Events.

Redis был восстановлен:

```bash
kubectl scale deployment/redis --replicas=1
```

После восстановления зависимости:

```text
events     1/1 Running
gateway    1/1 Running
payments   1/1 Running
postgres   1/1 Running
redis      1/1 Running
```

Это подтверждает корректную работу readiness checks.

## 11. Resource Requests и Limits

Для `gateway`, `events` и `payments` добавлены следующие ограничения:

```yaml
resources:
  requests:
    cpu: "100m"
    memory: "128Mi"
  limits:
    cpu: "500m"
    memory: "256Mi"
```

Проверка:

```bash
kubectl describe node k3d-quickticket-server-0
```

Для каждого из трёх application pods отображается:

| Pod | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---|---:|---:|---:|---:|
| gateway | 100m | 500m | 128Mi | 256Mi |
| events | 100m | 500m | 128Mi | 256Mi |
| payments | 100m | 500m | 128Mi | 256Mi |

В секции `Allocated resources`:

```text
cpu     500m (5%)    1500m (15%)
memory  524Mi (6%)   938Mi (11%)
```

## 12. Финальная проверка Task 2

```bash
kubectl get deployments
```

Результат:

```text
NAME       READY   UP-TO-DATE   AVAILABLE
events     1/1     1            1
gateway    1/1     1            1
payments   1/1     1            1
postgres   1/1     1            1
redis      1/1     1            1
```

Task 2 выполнен.

---

# Bonus — Helm

## 13. Установка Helm

Helm был установлен через Homebrew.

Проверка:

```bash
helm version
```

Результат:

```text
Version: v4.3.0
```

## 14. Создание Helm chart

Создан chart:

```bash
helm create quickticket
```

В Helm chart были помещены существующие Kubernetes-манифесты:

- `events.yaml`
- `gateway.yaml`
- `payments.yaml`
- `postgres.yaml`
- `redis.yaml`

Стандартные шаблоны Helm, не используемые в проекте, были удалены.

## 15. Проверка Helm chart

Lint:

```bash
helm lint quickticket
```

Результат:

```text
1 chart(s) linted, 0 chart(s) failed
```

Предупреждение об отсутствии `icon` не является ошибкой.

Рендеринг шаблонов:

```bash
helm template quickticket quickticket
```

Helm успешно сгенерировал все необходимые Services и Deployments, включая probes и resource requests/limits.

## 16. Установка Helm release

Существующие Kubernetes-ресурсы были подготовлены для управления Helm с помощью стандартных Helm ownership labels и annotations.

После этого выполнена установка:

```bash
helm install quickticket ./quickticket
```

Результат:

```text
NAME: quickticket
NAMESPACE: default
STATUS: deployed
REVISION: 1
DESCRIPTION: Install complete
```

Проверка:

```bash
helm list
```

Результат:

```text
NAME        NAMESPACE   REVISION   STATUS
quickticket default     1          deployed
```

Chart:

```text
quickticket-0.1.0
```

## 17. Итог Bonus

Helm chart успешно создан, проверен с помощью `helm lint` и `helm template`, после чего QuickTicket успешно установлен как Helm release `quickticket`.

---

# Итог работы

В ходе лабораторной работы:

1. Создан локальный Kubernetes-кластер на базе k3d.
2. Собраны и импортированы Docker-образы QuickTicket.
3. Созданы Kubernetes Deployments и Services для Gateway, Events, Payments, PostgreSQL и Redis.
4. Инициализирована база данных тестовыми данными.
5. Проверена работа Gateway API.
6. Проверен Kubernetes self-healing после удаления pod.
7. Настроены readiness и liveness probes.
8. Проверено поведение приложения при отказе Redis.
9. Настроены CPU/memory requests и limits.
10. Проверено распределение ресурсов на node.
11. Создан Helm chart.
12. Helm chart прошёл `helm lint`.
13. Выполнен `helm template`.
14. QuickTicket успешно установлен через Helm как release `quickticket`.

**Все основные задания и Bonus выполнены.**
