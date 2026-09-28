# Lab 5 и Lab 6: запуск и проверка в Windows PowerShell

Все команды ниже выполняются из `D:\moon\coding\SRE-Intro`, если явно не указан другой каталог. Git-команды не выполнялись помощником: по вашей просьбе вы запускаете их сами.

## Что уже сделано

- Подготовлены `.github/workflows/ci.yml`, три registry Deployment и `argocd/quickticket.yaml`.
- ArgoCD v3.3.2 установлен в существующий кластер `k3d-quickticket`; все компоненты готовы.
- Запущен Compose-стек QuickTicket + Prometheus + Grafana и локальный webhook.
- Настроены оба alert rule, contact point и notification policy lab6.
- Проведён реальный инцидент lab6; результаты и ссылки на исходные JSON находятся в `lab6.md`.

Lab5 нельзя считать полностью проверенной до ваших commit/push: Actions и ArgoCD должны прочитать файлы из GitHub. Текущие SHA в локальных manifests — стартовые значения, а не опубликованные образы. Не применяйте их до первого успешного CI и автоматического обновления тегов.

## 1. Проверить инструменты

```powershell
Set-Location D:\moon\coding\SRE-Intro
docker info
kubectl config current-context
kubectl get pods
kubectl get pods -n argocd
```

Контекст должен быть `k3d-quickticket`. Для следующих команд установите GitHub CLI и ArgoCD CLI, если их нет:

```powershell
winget install --id GitHub.cli --exact
```

Для ArgoCD CLI используйте Windows-бинарник той же версии, что и сервер, по [официальной инструкции](https://argo-cd.readthedocs.io/en/stable/cli_installation/):

```powershell
$argoTools = Join-Path $env:TEMP 'quickticket-tools'
New-Item -ItemType Directory -Force $argoTools | Out-Null
curl.exe -fL https://github.com/argoproj/argo-cd/releases/download/v3.3.2/argocd-windows-amd64.exe -o (Join-Path $argoTools 'argocd.exe')
$env:PATH = "$argoTools;$env:PATH"
```

После установки GitHub CLI откройте новое окно PowerShell, если `gh` ещё не найден. Строку с `$env:PATH` для ArgoCD нужно повторять в новом окне. Выполните:

```powershell
Set-Location D:\moon\coding\SRE-Intro
gh auth login
gh auth status
argocd version --client
```

Для `gh api user/packages` у токена должен быть доступ `read:packages`. При необходимости для OAuth-входа CLI выполните `gh auth refresh -s read:packages`.

## 2. Lab5: отправить подготовленные файлы и запустить CI

Посмотрите `git status` и не добавляйте `.idea/`. Следующие команды рассчитаны на исходную ветку `feature/lab4`, которая была открыта при подготовке работы. Если `feature/lab5` уже создана, используйте `git switch feature/lab5` вместо `switch -c`.

```powershell
git status --short
git switch -c feature/lab5
git add .github/workflows/ci.yml argocd/quickticket.yaml k8s/gateway.yaml k8s/events.yaml k8s/payments.yaml submissions/lab5.md submissions/evidence/lab5 submissions/lab5-lab6-run-guide.md
git commit -m "feat(lab5): add CI pipeline and automated GitOps deployment"
git push -u origin feature/lab5
git push origin feature/lab5:main
```

Последняя команда нужна именно для живой проверки: workflow слушает `main`. Это обычный push без `--force`. Если Git отклонит его из-за чужих новых коммитов, сначала выполните `git fetch origin` и разберите расхождение; не используйте force push.

Откройте [Actions вашего fork](https://github.com/NurKhab-ib/SRE-Intro/actions). Дождитесь зелёного CI. Сохраните ссылку на конкретный run в 5.7 отчёта. Можно проверить через CLI:

```powershell
gh run list --workflow ci.yml --branch main --limit 5
```

Возьмите ID нужного run из списка:

```powershell
$runId = Read-Host 'ID CI run'
gh run watch $runId --exit-status
gh run view $runId --json url,conclusion,headSha
gh api 'user/packages?package_type=container' --jq '.[].name'
```

Ожидаются три пакета `quickticket-gateway`, `quickticket-events`, `quickticket-payments`. Если push CI-коммита запрещён политикой репозитория, разрешите workflow запись в Contents в Settings → Actions → General и проверьте branch protection. Токен имеет `contents: write` в самом workflow.

## 3. Получить автоматически обновлённые manifests

```powershell
git fetch origin
git switch main
git merge --ff-only origin/main
git log --oneline -4
Select-String -Path k8s/gateway.yaml,k8s/events.yaml,k8s/payments.yaml -Pattern 'image:'
```

Должен появиться `ci: update image tags to <SHA>`, а все три Deployment должны ссылаться на один SHA из зелёного run. Изменения lab6 пока остаются незакоммиченными: не добавляйте их в коммит lab5.

## 4. Создать pull secret

Создайте classic PAT с `read:packages` в [GitHub token settings](https://github.com/settings/tokens/new?scopes=read:packages). Следующий код спрашивает токен скрытым вводом, передаёт Secret через stdin и не сохраняет токен в файле или истории команд:

```powershell
$securePat = Read-Host 'Classic PAT с read:packages' -AsSecureString
$plainPat = [System.Net.NetworkCredential]::new('', $securePat).Password
$authBytes = [Text.Encoding]::UTF8.GetBytes("NurKhab-ib:$plainPat")
$dockerConfig = @{ auths = @{ 'ghcr.io' = @{ auth = [Convert]::ToBase64String($authBytes) } } } | ConvertTo-Json -Depth 5 -Compress
$pullSecret = @{
  apiVersion = 'v1'
  kind = 'Secret'
  metadata = @{ name = 'ghcr-secret'; namespace = 'default' }
  type = 'kubernetes.io/dockerconfigjson'
  data = @{ '.dockerconfigjson' = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dockerConfig)) }
}
$pullSecret | ConvertTo-Json -Depth 6 -Compress | kubectl apply -f -
Remove-Variable plainPat,securePat,authBytes,dockerConfig,pullSecret
```

Проверьте только наличие, не печатайте содержимое:

```powershell
kubectl get secret ghcr-secret
```

## 5. Открыть ArgoCD и создать Application

ArgoCD уже установлен. Для новой машины команды установки есть в 5.4 отчёта. В отдельном окне оставьте работающим:

```powershell
kubectl port-forward svc/argocd-server -n argocd 8443:443
```

В основном окне получите начальный пароль для собственного входа; не включайте его в отчёт:

```powershell
$encodedPassword = kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}'
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedPassword))
argocd login localhost:8443 --insecure --username admin
kubectl apply -f argocd/quickticket.yaml
argocd app wait quickticket --sync --health --timeout 600
argocd app get quickticket
```

Откройте [ArgoCD](https://localhost:8443); для локального сертификата потребуется исключение браузера. Должны быть `Synced` и `Healthy`. Сохраните вывод в отчёт. Если репозиторий приватный, добавьте его в ArgoCD с отдельными credentials через Settings → Repositories.

## 6. Проверить видимое изменение из Git

В `k8s/gateway.yaml` добавьте метку только к Deployment:

```yaml
metadata:
  name: gateway
  labels:
    version: "v2"
```

Затем:

```powershell
git add k8s/gateway.yaml
git commit -m "feat: add version label to gateway"
git push origin main
argocd app sync quickticket
argocd app wait quickticket --sync --health --timeout 600
kubectl get deployment gateway -o jsonpath='{.metadata.labels.version}'
```

Ожидается `v2`. Сохраните реальный вывод для 5.6–5.7. Manual sync здесь разрешён условиями основной части.

## 7. Task 2: сломанный tag и rollback через Git

Сначала сохраните текущий рабочий image:

```powershell
$goodImage = kubectl get deployment gateway -o jsonpath='{.spec.template.spec.containers[0].image}'
$gatewayText = Get-Content k8s/gateway.yaml -Raw
$gatewayText = $gatewayText -replace '(image: ghcr\.io/nurkhab-ib/quickticket-gateway:)[^\s]+', '${1}does-not-exist'
[IO.File]::WriteAllText((Join-Path $PWD 'k8s/gateway.yaml'), $gatewayText)
git add k8s/gateway.yaml
git commit -m "feat: deploy bad gateway tag for rollback test"
$badCommit = git rev-parse HEAD
git push origin main
```

Не меняйте application code или workflow на этом этапе. Тогда manifest-only push не запустит CI и не затрёт плохой tag.

Через несколько минут:

```powershell
argocd app get quickticket --refresh
kubectl get pods -l app=gateway
kubectl describe deployment gateway
```

Новый pod должен показать `ErrImagePull`/`ImagePullBackOff`. Сохраните вывод. Сначала ArgoCD может показывать `Progressing`; для `Degraded` нужно дождаться progress deadline Deployment (обычно 600 секунд после начала rollout). Старый исправный pod может остаться `Running` — это нормальное поведение rolling update. Не удаляйте его.

После фиксации **Degraded + ImagePullBackOff** выполните:

```powershell
$rollbackStart = Get-Date
git revert $badCommit --no-edit
git push origin main
$revertSha = git rev-parse HEAD
do {
  Start-Sleep -Seconds 5
  $appState = kubectl get application quickticket -n argocd -o json | ConvertFrom-Json
} until ($appState.status.sync.revision -eq $revertSha -and $appState.status.sync.status -eq 'Synced' -and $appState.status.health.status -eq 'Healthy')
kubectl rollout status deployment/gateway --timeout=300s
$rollbackSeconds = [math]::Round(((Get-Date) - $rollbackStart).TotalSeconds, 1)
"Rollback recovery: $rollbackSeconds seconds"
git log --oneline -3
argocd app get quickticket
kubectl get pods -l app=gateway
```

Цикл ожидает именно SHA revert, чтобы случайно не принять старый Healthy за успешный rollback. Если прошло более 10 минут, прервите ожидание Ctrl+C и проверьте ошибки Application; не записывайте такое ожидание как успешный результат.

Добавьте оба состояния ArgoCD, pods, git log и измеренное время в 5.8–5.9.

## 8. Bonus lab5: полностью автоматическое обновление tag

Добавьте безвредный комментарий в исходник, затем отправьте изменение:

```powershell
Add-Content app/gateway/main.py "`n# Lab 5: verify the automatic GitOps image update."
git add app/gateway/main.py
git commit -m "feat: verify automated image update"
$sourceSha = git rev-parse HEAD
git push origin main
```

Дождитесь зелёного CI и автоматического tag-update commit. **Не запускайте `argocd app sync`:** эта проверка должна подтвердить автоматическую синхронизацию.

```powershell
gh run list --workflow ci.yml --branch main --limit 3
git pull --ff-only origin main
git log --oneline -3
argocd app get quickticket
kubectl get deployment gateway -o jsonpath='{.spec.template.spec.containers[0].image}'
```

ArgoCD опрашивает Git примерно раз в несколько минут. Повторите последние две команды позже, пока приложение не станет Synced/Healthy с образом `ghcr.io/nurkhab-ib/quickticket-gateway:<sourceSha>`. Проверяйте SHA, а не только зелёный статус старой версии. Добавьте git log и фактический вывод в bonus-раздел отчёта.

## 9. Отправить отчёт lab5 отдельным PR

После всех тестов перенесите реальные результаты в `submissions/lab5.md`, заменив статусы Pending. Файлы runtime уже должны быть на `main`, а итоговый отчёт даёт отдельную разницу для PR:

```powershell
git fetch origin
git switch feature/lab5
git merge --ff-only origin/main
git add submissions/lab5.md submissions/evidence/lab5
git commit -m "docs(lab5): record CI GitOps and rollback evidence"
git push origin feature/lab5
gh pr create --base main --head feature/lab5 --title "Lab 5: CI/CD and GitOps" --body "CI pipeline, GitOps checks, rollback evidence, and automated image tag update."
```

Если merge переключения ветки мешают незакоммиченные изменения, сначала сохраните нужные файлы отдельным коммитом на их учебной ветке. Не используйте `reset --hard` или `clean`.

## 10. Lab6: открыть и проверить уже работающий результат

```powershell
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml -f docker-compose.lab6.yaml ps
curl.exe -s http://localhost:3080/health
curl.exe -s http://localhost:8082/health
curl.exe -s http://localhost:9090/api/v1/targets
curl.exe -s http://localhost:8090
```

- [Grafana](http://localhost:3000): `admin` / `admin`, Alerting → Alert rules. Должны быть два правила QuickTicket.
- Contact points → `quickticket-alerts`: Webhook.
- Notification policies: группировка `alertname`, ожидание 30s, повтор 5m.
- [Webhook JSON](http://localhost:8090): реальные `firing` и `resolved` сообщения.
- [Prometheus targets](http://localhost:9090/targets): gateway/events/payments — UP.

Исторические состояния Firing сохранены в `submissions/evidence/lab6/`, поэтому после восстановления текущая Grafana правильно показывает Normal. Не нужно повторно ломать сервис только ради проверки отчёта.

## 11. Lab6: повторить эксперимент при необходимости

Перед повтором сохраните старую папку evidence отдельно: скрипт дописывает timeline, а снимки состояний заменяет. Для чистого нового запуска задайте в `scripts/lab6_incident.py` другой путь `OUT` и такой же путь в `scripts/test_lab6_contact.py`.

```powershell
$env:PAYMENT_FAILURE_RATE = '0.0'
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml -f docker-compose.lab6.yaml up -d --build
python scripts/provision_lab6.py
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml restart grafana
python scripts/test_lab6_contact.py
```

Для новой БД создайте отдельное учебное событие и прочитайте его ID:

```powershell
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml exec -T postgres psql -U quickticket -d quickticket -c "INSERT INTO events (name,venue,event_date,total_tickets,price_cents) SELECT 'Lab 6 load test','Local lab',now(),100000,100 WHERE NOT EXISTS (SELECT 1 FROM events WHERE name='Lab 6 load test'); SELECT id FROM events WHERE name='Lab 6 load test';"
```

В другом PowerShell запустите нагрузку, заменив ID при необходимости:

```powershell
python scripts/lab6_load.py --seconds 4200 --event 6
```

Дайте Prometheus собрать хотя бы несколько минут нормальной нагрузки. Для полностью заполненного 30-минутного окна соберите 30 минут baseline и продлите нагрузку, если нужно. Не начинайте новый инцидент, пока старые алерты не вернулись в Normal.

Запустить автоматический эксперимент:

```powershell
python scripts/lab6_incident.py
```

Он включает `PAYMENT_FAILURE_RATE=0.5`, записывает переходы состояний, читает health/logs/env по runbook, возвращает `0.0` и ждёт восстановления обоих правил и уведомлений. В этой учебной проверке исправление специально ждёт срабатывания обоих правил. В реальном инциденте исправляйте причину сразу после диагностики.

Если выполнение прервано или терминал закрыт, вручную восстановите платежи:

```powershell
$env:PAYMENT_FAILURE_RATE = '0.0'
docker compose -f app/docker-compose.yaml -f docker-compose.monitoring.yaml up -d payments
curl.exe -s http://localhost:8082/health
```

High Error Rate обычно возвращается в Normal раньше burn rate. Старые ошибки остаются в окне `[30m]`; это не означает, что новые платежи продолжают падать. Не меняйте окна/пороги и не очищайте Prometheus ради быстрого зелёного статуса.

## 12. Отправить lab6

После отправки lab5 создайте новую ветку от актуального `main` и добавьте только файлы lab6:

```powershell
git switch main
git pull --ff-only origin main
git switch -c feature/lab6
git add submissions/lab6.md submissions/evidence/lab6 docker-compose.lab6.yaml monitoring/grafana/provisioning/alerting/quickticket.json monitoring/webhook/receiver.py scripts/lab6_load.py scripts/lab6_incident.py scripts/provision_lab6.py scripts/test_lab6_contact.py
git commit -m "feat(lab6): add alerting runbook and measured postmortem"
git push -u origin feature/lab6
gh pr create --base main --head feature/lab6 --title "Lab 6: Alerting and Incident Response" --body "Two Grafana alerts, webhook evidence, a runbook, and a blameless postmortem."
```

Ссылки на PR отправьте в Moodle самостоятельно. Перед сдачей убедитесь, что lab5 уже содержит реальные результаты ваших GitHub-проверок, а не Pending.
