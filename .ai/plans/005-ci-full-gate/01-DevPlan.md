# GREP_SUMMARY: devplan ci-full-gate green platform-test compose container-resolution hermes-registry runner-provision smoke component
$START_DEVPLAN

# DevPlan — Green CI (full gate: smoke + component)

## $ARTIFACT_CONTRACT
- **PURPOSE:** Починить platform-test full gate (smoke + component стадии), который красный исторически — ни одного зелёного прогона на main за 40+ runs — и блокирует автоматический core-deploy на VPS.
- **DESCRIPTION:** 3 диагностико-ремонтные задачи: (T1) smoke — compose-стек не резолвит container names на CI-раннере; (T2) component — hermes-agent падает пуллом образа (${CONTEXT_IMAGE:-} не резолвится в CI без .env); (T3) backfill — убрать оставшиеся красные тесты (ssl-provision line-limit, gate_skip_enforcement, code-quality issues). Каждая задача начинается с диагностики через CI-log analysis, затем минимальный fix.
- **RATIONALE:** Q: почему отдельный план? A: full gate — самостоятельный класс проблем «CI-инфраструктура раннера», не связанный с кодом платформы (DevPlan 001/004 закрыли продуктовые баги). Q: почему диагностика перед фиксом? A: smoke-тесты падают без внятного первопричины в логах («No container names resolved» c 7 started модулей) — нужен целенаправленный diagnostic-run в CI прежде фикса.
- **ACCEPTANCE_CRITERIA:** См. §Acceptance Criteria — 4 измеримых критерия.
- **IMPLEMENTS:** Запрос владельца «Оба: деплой + план по CI» (2026-07-17), вторая часть.
- **IMPACTS:** tests/_conftest/smoke.py, tests/test_smoke_platform.py, tests/test_component_hermes.py, .github/workflows/platform-test.yml, core/modules/hermes-agent/docker-compose.test.yml, core/internal/bootstrap/ssl-provision.sh.
- **REQUIRES:** Доступ к GitHub Actions CI (run logs, rerun), локальный Docker для smoke-тестов (опционально).

---

## 1. Requirements Analysis — карта провалов full gate (на 2026-07-17 14:42 MSK)

| Слой | Статус | Что падает |
|------|--------|-----------|
| pre-commit | ✅ green (после DevPlan 004-CI-fix) | — |
| fast gate (syntax + static + predeploy) | ✅ green | — |
| **smoke** (Docker compose up + health) | ❌ 8 failed, 5 errors | 7 модулей стартуют, но `docker compose ps` не возвращает container_names → все smoke-тесты FAIL |
| **component** (hermes-agent) | ❌ 7 errors | `docker compose up` — pull `${CONTEXT_IMAGE:-ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1}` → registry error; в CI нет `.env` с CONTEXT_IMAGE |
| gate_skip_enforcement | ❌ 1 failed | `test_executed_tests_greater_than_zero` — side-effect от падения smoke/component (JUnit report без executed тестов) |
| line-limit (ssl-provision.sh) | ⚠️ WARN | 551 строк > max 500 — не блокирует, но шумит |

**Success criteria (ключевые):**
1. `docker compose ps` возвращает container_names для каждого запущенного модуля в CI-раннере.
2. hermes-agent component-тест green (образ доступен или тест корректно skipped).
3. Полный `make gate MODE=full` зелёный на CI (workflow_run → core-deploy может сработать).

## 2. Design Decisions

### D1 — Diagnostic-first: CI diagnostic run с расширенным логированием
## @rationale Q: почему не предполагаем fix без диагностики? A: smoke container resolution failure не воспроизводится локально (локально `make test MARKER=smoke` зеленый). Первопричина в CI-раннере: либо `docker compose ps` возвращает пустой вывод из-за project-name дрифта, либо compose up падает молча (exit 0 но контейнеры не стартуют), либо `container_name` в compose-файлах не матчится с фактическими именами. Без diagnostic-run с `docker compose ps --all` и `docker ps -a` после compose up — гадание. Rejected: предположить fix без диагностики (риск wasted CI cycles).

### D2 — hermes-agent: skip в CI без registry auth, не pull
## @rationale Q: почему skip а не auth? A: `${CONTEXT_IMAGE}` — контекстный образ, билдится локально (`make hermes-build-context`), отсутствует в ghcr.io/tronyxlab. Добавление CI-secrets для pull'а чужого registry — неоправданное расширение security surface для теста. Component-тест должен SKIP'аться при отсутствии образа (маркер `needs_docker_image`) или использовать `hermes-agent-base` (собирается CI-билдом!). Rejected: (a) pull-specific registry credentials в CI secrets; (b) хардкодить ghcr.io/tronyx161 вместо ${CONTEXT_IMAGE} — нарушает DRY.

### D3 — ssl-provision.sh: разбить, не поднимать лимит
## @rationale Q: почему split а не поднять MAX_LINES? A: 551 строка — нарушение контракта «Small Simple Blocks»; скрипт уже содержит две логические части (acme-install + cert-issue). Разбиение на `install-acme.sh` + `issue-cert.sh` снижает coupling и даёт независимую переиспользуемость (install-acme нужен один раз при bootstrap, issue-cert — при каждом renew). Rejected: поднять лимит (маскирует проблему размера, противоречит принципам).

### D4 — gate_skip_enforcement: зависит от D1/D2, отдельный fix не требуется
## @rationale Тест `test_executed_tests_greater_than_zero` читает JUnit XML. При зелёном smoke+component — зелёный автоматически. Отдельного fix не требует.

## 3. Data Flow (после фиксов)

```
CI push → platform-test.yml
  ├─ pre-commit ✅
  ├─ fast gate ✅
  ├─ full gate:
  │    ├─ provision (networks + volumes) → Docker → Build hermes-agent-base
  │    ├─ smoke: docker compose up (per module, COMPOSE_PROFILES) → ⚡ diagnostic
  │    │    └─ ps --all + inspect → container_names резолвятся → health PASS  (T1)
  │    └─ component: hermes-agent
  │         ├─ образ доступен (hermes-agent-base из CI build) → test PASS   (T2)
  │         └─ образ недоступен → SKIP с обоснованием
  └─ все стадии green → workflow_run core-deploy → rsync + node-update
```

## 4. $TASKS

| ID | Задача | Acceptance | Deps | Cx |
|----|--------|-----------|------|----|
| T1 | **Smoke diagnostic:** создать diagnostic CI-run (PR/ветка с echo-отладкой): после compose up каждого модуля — `docker compose -p <project> ps --all`, `docker ps -a --filter name=<project>`, `docker compose -p <project> logs --tail 20`. По результатам diagnostic-логов определить root cause (project-name mismatch? compose up failed silently? container_name pattern mismatch?) и применить минимальный fix. | diagnostic-run выдаёт логи; root cause идентифицирован; fix делает smoke green на CI | — | 6 |
| T2 | **hermes-agent component fix:** (a) проверить, доступен ли `hermes-agent-base` образ после CI-билда (сейчас билдится); (b) если доступен — переключить component-тест на `hermes-agent-base` (маркер `needs_docker_image` уже есть); (c) если нет — сделать SKIP с `@pytest.mark.skipif` (образ не в registry) + informative reason; (d) исправить `${PLATFORM_VERSION:-dev}` в Dockerfile (W7 fix, добавить ARG с дефолтом). | `pytest tests/test_component_hermes.py` на CI green или skipped; Dockerfile warning чист | T1 (для контекста CI-раннера) | 4 |
| T3 | **Backfill:** (a) ssl-provision.sh: разбить на `install-acme.sh` (~250 строк) + `issue-cert.sh` (~300 строк); node-lifecycle.sh вызывает install-acme при init, issue-cert при update; (b) запустить `make test-inventory-sync`. | `make check-file-lines` без WARN на ssl-provision.sh; `make lint` чист | — | 4 |

Merge-rule: T3 — самостоятельная задача без зависимостей; выполняется отдельной сессией параллельно с T1.

## 5. Acceptance Criteria

| # | Критерий | Проверка |
|---|----------|---------|
| A1 | CI diagnostic-run выдаёт первопричину smoke container resolution failure | diagnostic CI log analysis |
| A2 | CI full gate: smoke стадия зелёная (все запущенные модули проходят healthcheck) | CI platform-test run |
| A3 | CI full gate: component стадия зелёная или skipped (hermes-agent) | CI platform-test run |
| A4 | `make check-file-lines` зелёный (ssl-provision.sh ≤500 строк) | локальный прогон |

## 6. File Manifest

| Файл | Действие |
|------|----------|
| tests/_conftest/smoke.py | diagnostic edit (T1) |
| tests/test_smoke_platform.py | possible fix (T1) |
| tests/test_component_hermes.py | edit (T2 — skip или base-образ) |
| core/modules/hermes-agent/docker-compose.test.yml | possible edit (T2) |
| core/modules/hermes-agent/build/Dockerfile | edit (T2 — ARG PLATFORM_VERSION) |
| core/internal/bootstrap/ssl-provision.sh | split (T3 — install-acme.sh + issue-cert.sh) |
| core/internal/bootstrap/install-acme.sh | new (T3) |
| core/internal/bootstrap/issue-cert.sh | new (T3) |
| core/internal/bootstrap/node-lifecycle.sh | edit (T3 — вызов новых скриптов) |
| core/entrypoint-manifest.yaml | edit (T3 — регистрация новых internal-скриптов) |
| .github/workflows/platform-test.yml | possible edit (T1 diagnostic-log capture) |

## 7. Constraints / Out of scope

- **Не фиксим CI-инфраструктуру глобально** — только конкретные провалы full gate. Общий рефакторинг CI (matrix build, caching strategy) — отдельный план.
- **Docker Hub rate limit** — pre-existing, вне scope.
- **hermes-agent context image в registry** — build/push pipeline вне scope; T2 использует base-образ или skip.
- **T1 требует diagnostic CI-run с расширенным логированием** — создаётся diagnostic-ветка, пушится, CI-лог анализируется. Без этого root cause неустановим.

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| — | — | T1 — diagnostic-only (CI log analysis), тесты не добавляются до root cause | — |
| tests/test_component_hermes.py | (existing) | Должен SKIP или PASS в CI без registry-образа | hermes-agent component |
| tests/test_contract_entrypoints.py | (existing) | Новые internal-скрипты (install-acme.sh, issue-cert.sh) зарегистрированы | entrypoint manifest |

## 9. $PARALLEL_GROUPS

### Wave 1 (диагностика smoke — блокирует T2)
- Tasks: T1
- Command: `coder Read .ai/plans/005-ci-full-gate/01-DevPlan.md, implement Wave 1: T1 — diagnostic CI run`

### Wave 2 (после диагностики T1 — параллельно)
- Tasks: T2 (hermes-agent), T3 (ssl-provision split)
- Command: `coder Read .ai/plans/005-ci-full-gate/01-DevPlan.md, implement Wave 2: T2, T3`

## 10. T1 Findings (2026-07-17, Wave 1 completed)

**Diagnostic branch:** `diag/smoke-container-resolution` (PR #7, runs 29582577285 / 29584177010).

**Root cause (A1 ✅):** гипотеза (c)+(b) — на GHA-раннере `docker compose up -d --wait` возвращает exit 0 при истечении `--wait-timeout` БЕЗ создания контейнеров (`docker ps -a` пуст). Fixture `platform_services` считала returncode=0 успехом. Fix применён на diagnostic-ветке (commit `0c5856a`): post-up existence check через `docker compose ps --all --format {{.Name}}` — модуль без контейнеров помечается failed.

**Вторичные провалы, вскрытые диагностикой (расширяют scope Wave 2):**
| Модуль | Причина | Куда относится |
|--------|---------|----------------|
| hermes-agent | `${CONTEXT_IMAGE}` образ не существует ни в одном registry | T2 (как планировалось) |
| nginx | нет dev-сертификатов `/etc/nginx/dev-certs/_local.pem` в CI (make dev-certs не вызывается) | **T4 (new)** |
| minio, langfuse, litellm | `compose up` returncode=1 — image pull / env var issues, требует расследования | **T4 (new)** |
| GHA BuildKit cache | `failed to reserve cache` — `cache-to` временно отключён в docker-build-cache/action.yml | **T4 (new)** — вернуть `mode=min` |

**T4 (new): smoke module startup fixes** — deps: T1 ✅; Cx: 6. Acceptance: A2 (smoke green). Diagnostic-ветка НЕ смержена в main — merge входит в Wave 2.

**T3 статус:** выполнен локально (install-acme.sh 76 строк, issue-cert.sh 497, wrapper 35; contract-тесты 126/126, lint чист). Изменения в working tree, не закоммичены.

## Next Steps
### Wave 1 (diagnostic) — ✅ DONE 2026-07-17
### Wave 2 (fix)
Use coder role and read /Users/tronyx/projects/ai-platform/.ai/plans/005-ci-full-gate/01-DevPlan.md §10, implement T2 (hermes-agent: base-образ или skip + ARG PLATFORM_VERSION) + T4 (nginx dev-certs в CI, minio/langfuse/litellm startup, вернуть BuildKit cache-to mode=min) на ветке diag/smoke-container-resolution; verify smoke+component green на CI, затем merge в main.

$END_DEVPLAN
