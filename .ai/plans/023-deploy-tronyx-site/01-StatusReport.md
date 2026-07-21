# $START_STATUS_REPORT

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|----------|
| **PURPOSE** | Зафиксировать результат деплоя `tronyx-site` через `make deploy`, состояние CI-пайплайна и текущее состояние на VPS |
| **DESCRIPTION** | Выполнена операция `make deploy PROJECT=tronyx-site`. Git push: already up-to-date (SHA 9e45334). CI-пайплайн завершился успешно ещё 2026-07-20. Текущее состояние VPS: деградировано из-за повторного bootstrap (2026-07-21 04:15 UTC) — docker-compose.yml утерян, контейнер не запущен. |
| **RATIONALE** | Стандартная операция деплоя через платформенный механизм |
| **ACCEPTANCE_CRITERIA** | Git push выполнен, SHA зафиксирован, состояние VPS верифицировано |
| **IMPLEMENTS** | deploy-model: git push → CI → forced-command |
| **IMPACTS** | make deploy, CI pipeline (TronyxLab/tronyx-site), VPS /opt/projects/tronyx-site/ |
| **REQUIRES** | Нет |

---

## 1. Diagnostic Summary

| Параметр | Значение |
|----------|----------|
| **Target host** | tronyx-vps (103.88.243.151) |
| **OS** | Ubuntu 24.04.4 LTS |
| **Context** | tronyx-lab |
| **Project** | tronyx-site |
| **Repo** | TronyxLab/tronyx-site |
| **Domain** | www.tronyx.ru |
| **Branch** | main |

### Issues Found

| # | Severity | Description | Status |
|---|----------|-------------|--------|
| I1 | **CRITICAL** | docker-compose.yml отсутствует на VPS (`/opt/projects/tronyx-site/`) | Открыто |
| I2 | **CRITICAL** | Контейнер tronyx-site не запущен | Открыто |
| I3 | **HIGH** | Образ `ghcr.io/tronyxlab/tronyx-site:*` отсутствует на VPS (возможно, удалён при повторном bootstrap) | Открыто |
| I4 | **HIGH** | Bootstrap 2026-07-21 04:15 UTC: converge failed (exit 1), 5 warnings | Открыто |
| I5 | **MEDIUM** | `.env.platform` пустой (0 bytes) — создан converge как stub | Открыто |
| I6 | **MEDIUM** | `ai-platform.yaml` на VPS — GENERATED-STUB (не актуальный манифест из репозитория) | Открыто |
| I7 | **LOW** | CI workflow `platform-deploy.yml` использует старый глагол `platform-deploy` (ожидает docker-compose.yml на месте), а не `platform-deliver` (доставляет файлы через stdin tar.gz) | Наблюдение |

---

## 2. Actions Taken

### 2.1 Preflight

| Check | Result | Detail |
|-------|--------|--------|
| Connection Context Card | PASS | `.ai/server-state.json` прочитан, VPS=tronyx-vps, context=tronyx-lab |
| Рабочее дерево | PASS | Чистое, нет незакоммиченных изменений |
| `save_server_state` | N/A | Поле отсутствует в `ai-instructions.yaml`; состояние VPS не персистится локально |

### 2.2 Mutation: `make deploy`

```bash
make deploy PROJECT=/Users/tronyx/projects/tronyx-lab/tronyx-site
```

**Результат:** Git push — `Everything up-to-date`.

SHA уже был на origin/main:
- Локальный: `9e45334657834056ee2722c107086fb57fd3e64f`
- Remote:   `9e45334657834056ee2722c107086fb57fd3e64f`

CI-пайплайн для этого SHA уже отработал ранее (2026-07-20T16:59:20Z):

| Job | Status |
|-----|--------|
| resolve-node | ✅ success |
| build-image | ✅ success |
| deploy / Deploy → production | ✅ success |
| deploy / Deploy → dev | ⏭️ skipped |

**Логи CI (deploy → production):**
```
[IMP:9][platform-deploy][main] === platform-deploy START ===
Parsed: PROJECT=tronyx-site REF=9e45334...
Previous image saved: f8b3e9a... TAG=ghcr.io/tronyxlab/tronyx-site:latest
Pull complete: tronyx-site:9e45334...
Atomic deploy: docker compose up -d tronyx-site
Container tronyx-site Recreate → Recreated → Starting → Started
tronyx-site is healthy (check passed)
Deploy SUCCESS: tronyx-site → 9e45334...
=== platform-deploy DONE (success) ===
```

### 2.3 VPS Health Check (post-deploy)

| Check | Result | Detail |
|-------|--------|--------|
| Docker containers | **FAIL** | `docker ps --filter name=tronyx` — нет контейнеров (ни running, ни exited) |
| Docker images | **FAIL** | `docker images ghcr.io/tronyxlab/tronyx-site` — образов нет |
| Project directory | **DEGRADED** | `/opt/projects/tronyx-site/`: только `ai-platform.yaml` (GENERATED-STUB) и `.env.platform` (пустой). `docker-compose.yml` отсутствует. |
| Nginx | PASS | nginx running (healthy), www.tronyx.ru → 301 (nginx отвечает, бэкенда нет) |
| Platform modules | PASS | 20 контейнеров healthy, платформа функционирует |
| `.deploy-snapshots/` | **MISSING** | Директория отсутствует (возможно, удалена при converge) |

### 2.4 Root Cause Analysis

Временная шкала:

```
2026-07-20 16:59 UTC  CI deploy SUCCESS — контейнер запущен, health check пройден
                       docker-compose.yml ПРИСУТСТВОВАЛ на VPS
                              ↓
                [НЕИЗВЕСТНЫЙ ПЕРИОД — 14 часов]
                              ↓
2026-07-21 04:15 UTC  Bootstrap re-run:
                       - Step 3:  tor-proxy WARN
                       - Step 13: secrets-init WARN
                       - Step 14: read-node-yaml WARN
                       - Step 18: node-update had failures
                       - Step 19: converge FAILED (exit 1)

                       converge.sh reconcile_projects():
                       - ai-platform.yaml отсутствовал → создан GENERATED-STUB (04:15)
                       - .env.platform отсутствовал → создан пустой (04:15)
                       - docker-compose.yml — НЕ управляется reconcile_projects,
                         удалён кем-то/чем-то ДО converge
                              ↓
2026-07-21 04:44 UTC  (текущий момент)
                       docker-compose.yml отсутствует
                       Образы удалены (docker system prune?)
                       Контейнер не запущен
```

**Ключевой вывод:** CI-деплой от 2026-07-20 был успешным. Деградация произошла позже — при повторном bootstrap'е (2026-07-21 04:15 UTC), который пересоздал project directory со stub-файлами. Причина удаления docker-compose.yml между CI-деплоем и bootstrap'ом не установлена (аудит-лог за 20 июля отсутствует/ротирован).

### 2.5 TRAP Annotations

```yaml
# 🧐 TRAP[DECISION] · 2026-07-21 · — · CI workflow использует platform-deploy (старый глагол), не platform-deliver
# · Rejected: platform-deliver (доставка docker-compose.yml + ai-platform.yaml через stdin tar.gz)
# · Reason: CI workflow был написан до появления D2-глагола platform-deliver (T2, 2026-07-17)
# · Rev: после миграции CI на platform-deliver, docker-compose.yml будет доставляться при каждом деплое —
#   проблема «потерянного docker-compose.yml» исчезнет
```

---

## 3. Audit Trail

| # | Timestamp | Action | Rationale | Result |
|---|-----------|--------|-----------|--------|
| 1 | 07:44 | Прочитан `.ai/server-state.json` | Connection Context Card (Step 1 VALIDATE_CTX) | PASS: host=tronyx-vps, context=tronyx-lab |
| 2 | 07:44 | Прочитан `ai-instructions.yaml` | Проверка `save_server_state` | N/A: поле отсутствует |
| 3 | 07:44 | `git log --oneline -3` в tronyx-site | Preflight: проверка HEAD | HEAD=9e45334 |
| 4 | 07:44 | `git status` в tronyx-site | Preflight: чистое дерево | PASS: working tree clean |
| 5 | 07:44 | `make deploy PROJECT=...` | Step 6 EXECUTE_BATCH | SUCCESS: git push "Everything up-to-date" |
| 6 | 07:44 | `git rev-parse HEAD` + `git branch -vv` | Step 7: verify local SHA | 9e45334, tracking origin/main |
| 7 | 07:44 | `git ls-remote origin HEAD` | Step 7: verify remote SHA | 9e45334 (совпадает) |
| 8 | 07:44 | `gh run list --repo TronyxLab/tronyx-site` | Step 7: CI status | Run 29761903809: success (2026-07-20) |
| 9 | 07:44 | `gh run view 29761903809` | Step 7: CI jobs detail | Все jobs success |
| 10 | 07:44 | SSH: `docker ps --filter name=tronyx` | Step 7: container health check | FAIL: нет контейнеров |
| 11 | 07:44 | SSH: `ls /opt/projects/tronyx-site/` | Step 7: project directory audit | DEGRADED: только stub-файлы |
| 12 | 07:44 | SSH: `docker images ghcr.io/tronyxlab/tronyx-site` | Step 7: image audit | FAIL: образов нет |
| 13 | 07:44 | `gh run view ... --log` | Step 5 BATCH_DIAGNOSE: CI-логи | Deploy succeeded yesterday; контейнер был healthy |
| 14 | 07:44 | SSH: audit.log за 20-21 июля | Step 5: диагностика причины | Bootstrap 04:15 UTC, converge fail |

---

## 4. Legalization Tasks

| # | Что изменено | Когда | TRAP | Статус |
|---|-------------|-------|------|--------|
| — | Ручных мутаций VPS не производилось | — | — | N/A |

---

## 5. Overall Verdict

**PARTIAL**

| Компонент | Статус |
|-----------|--------|
| `make deploy` (git push) | ✅ SUCCESS |
| CI pipeline (build + deploy) | ✅ SUCCESS (2026-07-20) |
| VPS — контейнер tronyx-site | ❌ FAIL (не запущен) |
| VPS — docker-compose.yml | ❌ FAIL (отсутствует) |
| VPS — образы | ❌ FAIL (отсутствуют) |
| www.tronyx.ru | ⚠️ DEGRADED (nginx 301, бэкенда нет) |

**Причина PARTIAL:** CI-деплой был успешен, но последующий bootstrap (2026-07-21 04:15 UTC) с упавшим converge деградировал состояние проекта на VPS.

---

## 6. Next Steps

### Immediate fix (восстановление)

Для восстановления необходимо доставить `docker-compose.yml` на VPS и перезапустить деплой:

```bash
# Вариант A: platform-deliver (доставка файлов + docker compose up)
cat docker-compose.yml ai-platform.yaml .env.platform | \
  tar czf - -C /Users/tronyx/projects/tronyx-lab/tronyx-site \
    docker-compose.yml ai-platform.yaml .env.platform | \
  ssh ci-deploy@103.88.243.151 "platform-deliver tronyx-lab/tronyx-site"

# Вариант B: повторный CI-запуск (после доставки docker-compose.yml)
# 1. Доставить docker-compose.yml через platform-deliver
# 2. make deploy PROJECT=/Users/tronyx/projects/tronyx-lab/tronyx-site
```

### Medium-term (миграция CI на platform-deliver)

Обновить `platform-deploy.yml` для использования глагола `platform-deliver` вместо старого `platform-deploy`. Это гарантирует, что `docker-compose.yml` и `ai-platform.yaml` доставляются при каждом деплое, а не полагаются на их присутствие на VPS.

### Investigation (почему пропал docker-compose.yml)

Расследовать, что именно удалило `docker-compose.yml` между CI-деплоем (2026-07-20 17:00 UTC) и bootstrap'ом (2026-07-21 04:15 UTC). Возможные гипотезы:
- `docker system prune` в cron
- Ручное вмешательство
- Баг в converge.sh (удаление директории проекта перед пересозданием)
- Системный сбой (диск, перезагрузка)

```bash
# Агент для расследования:
# @sysadmin investigate project-directory-loss at tronyx-vps for tronyx-site
# timeframe: 2026-07-20T17:00Z to 2026-07-21T04:15Z
```

---

$END_STATUS_REPORT
