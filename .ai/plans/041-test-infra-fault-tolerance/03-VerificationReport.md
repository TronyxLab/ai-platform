# 03-VerificationReport: DevPlan 041 Pre-Implementation Audit

🔒 **Verified against SHA:** `0c8b8d16cb0b497e08af71480e85a34bb77054eb`
**Verification date:** 2026-07-22
**Scope:** STANDARD (≈14 files, config/compose/CI/env changes)
**Mode:** Pre-implementation — no code changes exist yet

$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Pre-implementation semantic verification of DevPlan 041 — проверка DevPlan на корректность относительно реальной кодовой базы, обнаружение drift между DevPlan и codebase, валидация architectural invariants.
DESCRIPTION:           Static audit (Phase 1) существующих файлов в скоупе + Cross-File Drift Detection (Phase 2) между DevPlan и реальными файлами. Phase 5 (runtime) не применим — код ещё не написан. Phase 6 (config sync) — частично, для обнаружения конфликтов в platform-env.yaml.
RATIONALE:             DevPlan был создан на основе Forensics Report (arch-forensics skill) с использованием Option E superposition. QA верифицирует, что DevPlan непротиворечив относительно актуального состояния кодовой базы и не вносит новых drift при реализации.
ACCEPTANCE_CRITERIA:   DevPlan не содержит ссылок на несуществующие файлы; pseudo-code консистентен с существующими паттернами; network topology в DevPlan соответствует реальной; все CRITICAL drift задокументированы.
IMPLEMENTS:            QA role — pre-implementation gate per dev-pipeline skill
IMPACTS:               Блокирует реализацию до исправления BLOCKER-находок
REQUIRES:              Чистый working tree (подтверждено: git rev-parse 0c8b8d)
$END_ARTIFACT_CONTRACT

---

## Semantic Verdict

**DRIFTED (CRITICAL)** — DevPlan содержит BLOCKER: ссылки на несуществующий путь `core/internal/scripts/discover_modules.py`. Без исправления W1 не может быть реализована.

**Health Score:** 55/100 (до исправления) → ожидается 85+/100 после исправления

---

## Section 1 — Static Audit (Phase 1)

### 1.1 Compliance Matrix (существующие файлы в скоупе)

| File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region/#endregion | Doxygen tags | LDD IMP:7-10 | No bare except | No secrets |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `core/internal/bootstrap/discover_modules.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/_conftest/networks.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/_conftest/smoke.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/_conftest/session.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/_conftest/reuse.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `platform-env.yaml` | ✅ | ✅ | ✅ | N/A (YAML) | N/A | N/A | N/A | ✅ |
| `core/entrypoint-manifest.yaml` | ✅ | ✅ | ✅ | N/A (YAML) | N/A | N/A | N/A | ✅ |
| `tests/test_smoke_postgres.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/test_smoke_redis.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/test_smoke_nginx.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/test_component_pgbouncer.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/test_component_clickhouse.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `tests/test_component_hermes.py` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Итого:** 13/13 PASS по всем механическим критериям.

### 1.2 TRAP Audit

| File | Active TRAPs |
|------|-------------|
| `networks.py:91-99` | TRAP[DEBT] · 2026-07-15 · MED · Parallel test teardown destroys shared external networks |
| `smoke.py:696-698` | TRAP[BUG] · 2026-07-17 · HI · per-module `down --remove-orphans` killed previously started modules |
| `smoke.py:712-725` | TRAP[BUG] ×2 · 2026-07-18/22 · stale containers block test run |
| `test_smoke_postgres.py:68-72` | TRAP[BUG] · 2026-07-21 · MED · test compose overrides base network to test-shared-db-net |
| `test_smoke_postgres.py:155-159` | TRAP[BUG] · 2026-07-22 · HI · own_project was "ai-platform-test" |
| `test_smoke_redis.py:84-86` | TRAP[BUG] · 2026-07-22 · HI · own_project was "ai-platform-test" |
| `test_smoke_nginx.py:126-128` | TRAP[BUG] · 2026-07-22 · HI · own_project was "ai-platform-test" |
| `test_component_pgbouncer.py:79-87` | TRAP[BUG] · 2026-07-15 · HI · component-тесты молча скипались |
| `test_component_hermes.py:49-52` | TRAP[BUG] · 2026-07-22 · HI · COMPOSE_PROJECT was "ai-platform-test" |
| `test_component_hermes.py:128-129` | TRAP[BUG] · 2026-07-22 · HI · own_project hardcoded |
| `test_component_hermes.py:286` | TRAP[BUG] · 2026-07-22 · HI · own_project hardcoded |
| `test_component_clickhouse.py:177` | TRAP[BUG] · 2026-07-22 · HI · own_project was "ai-platform-test" |

**Наблюдение:** 7 TRAP[BUG] за 2026-07-22 (все одного класса — `own_project` / `COMPOSE_PROJECT` коллизия) — **уже исправлены в коде**. TRAP-комментарии остались для документации root cause. Это соответствует Forensics Report.

---

## Section 2 — Drift Analysis (Phase 2)

### 2.1 Drift Register

#### [BLOCKER] DRIFT-DP-1 · Path Mismatch: DevPlan vs реальный файл

- **DevPlan:** `core/internal/scripts/discover_modules.py` (строки 23, 99, 181, 227, 280, 727, 731)
- **Факт:** `core/internal/bootstrap/discover_modules.py` (120 LOC)
- **Manifest:** `entrypoint-manifest.yaml:227` → `delegates_to: core/internal/bootstrap/discover_modules.py` ✅
- **Fix:** Заменить все вхождения `core/internal/scripts/discover_modules.py` → `core/internal/bootstrap/discover_modules.py` в DevPlan. Затронуты: IMPACTS (строка 23), дизайн W1 (строка 181+), секция 1.1 (строка 99), секция верификации W1 (строка 727, 731), дизайн W2 (строка 280).

**Последствия без исправления:** Coder создаст файл по неверному пути, или попытается модифицировать несуществующий файл → W1 implementation failure.

---

#### [HIGH] DRIFT-DP-2 · Stale State: `_STALE_CONTAINER_NAMES` описан как неполный

- **DevPlan строка 112:** «`_STALE_CONTAINER_NAMES` неполный — 9 из 22»
- **Факт smoke.py:726-749:** Все 22 имени присутствуют (подтверждено grep по docker-compose.test.yml)
- **Вердикт:** Информация устарела — состояние было исправлено между Forensics Report и текущим состоянием. DevPlan должен описывать ТЕКУЩЕЕ состояние (fixed), а не использовать историческую проблему как обоснование.

**Fix:** Обновить секцию 1.2: указать что список сейчас полный (22/22), но остаётся хрупким — ручная синхронизация при добавлении новых модулей. Это не отменяет необходимость W1-W6, но меняет обоснование с «broken» на «fragile».

---

#### [HIGH] DRIFT-DP-3 · NetworkLeaseManager overlap с существующим `ensure_external_networks()`

- **DevPlan W3:** `NetworkLeaseManager` с acquire/release + refcounting
- **Факт networks.py:101-128:** `ensure_external_networks()` уже реализует idempotent create (inspect → create if missing)
- **Риск:** NetworkLeaseManager создаёт второй механизм управления теми же сетями. `ensure_external_networks()` вызывается 6+ фикстурами для pre-create сетей перед compose up. DevPlan должен явно описать, как NetworkLeaseManager заменяет/дополняет `ensure_external_networks()`, а не создаёт параллельный механизм.

**Fix:** Добавить в W3 дизайн: явное решение о миграции `ensure_external_networks()` → `NetworkLeaseManager.acquire()` или сосуществовании. Без этого решения Coder может реализовать оба механизма, усугубив BOUNDARY COLLAPSE.

---

#### [HIGH] DRIFT-DP-4 · Vacuous Gate: AC-6e anti-regression

- **DevPlan AC-6e:** Gate проверяет `check_foreign_containers(..., "ai-platform-test")` — anti-regression для TRAP[BUG] 2026-07-22
- **Факт:** grep подтверждает — **ни одного** вхождения `check_foreign_containers(..., "ai-platform-test")` в тестовых файлах. Все 9 вызовов используют уникальные project names.
- **Вердикт:** Gate всегда будет зелёным (false-pass). Нулевая ценность как regression test — он уже не ловит bug, который был исправлен. Gate полезен ТОЛЬКО если он детектирует `own_project="ai-platform-test"` в ЛЮБОМ контексте (не только в `check_foreign_containers`), включая COMPOSE_PROJECT_NAME, `-p ai-platform-test` в subprocess-вызовах.

**Fix:** Расширить scope AC-6e: сканировать все вхождения `"ai-platform-test"` как строкового литерала в тестовых файлах (кроме SMOKE_ENV и platform_services где это легитимно), а не только в `check_foreign_containers`.

---

#### [MEDIUM] DRIFT-DP-5 · `check_foreign_containers_adapter` использует хардкод-конвенцию

- **DevPlan строка 611:** `own_project = f"ai-platform-test-{module_name}"`
- **Проблема:** Адаптер хардкодит конвенцию `ai-platform-test-{module_name}`, что противоречит цели «derive from compose files». Реальный own_project должен выводиться из COMPOSE_PROJECT_NAME переменной в compose-файлах или из переменной окружения, а не из конвенции.
- **Fix:** Два варианта: (a) адаптер принимает own_project параметром, а вызывающий передаёт свою константу (более честно); (b) адаптер читает COMPOSE_PROJECT_NAME из env test-файла.

---

#### [MEDIUM] DRIFT-DP-6 · `test_ports` дублирует SMOKE_ENV

- **DevPlan W4:** Секция `test_ports` в platform-env.yaml
- **Факт smoke.py:126-131:** `SMOKE_ENV` уже содержит LITELLM_TEST_PORT=14000, HERMES_DASHBOARD_TEST_PORT=19119, etc.
- **Риск:** Два источника истины для тестовых портов. Если `SMOKE_ENV` и `test_ports` разойдутся — тесты будут использовать одни порты, compose-файлы другие.

**Fix:** Выбрать один source of truth. Варианты: (a) `test_ports` в platform-env.yaml — единственный источник, SMOKE_ENV читает его; (b) SMOKE_ENV остаётся, test_ports не добавляется. DevPlan должен явно описать стратегию консолидации.

---

#### [MEDIUM] DRIFT-DP-7 · Semantic: `POSTGRES_PORT=6432` в port_mappings

- **platform-env.yaml:92:** `POSTGRES_PORT: 6432`
- **Проблема:** 6432 — это порт pgbouncer, не postgres. Postgres слушает 5432. Имя ключа семантически неверно и может ввести в заблуждение при добавлении test_ports.
- **Fix:** Переименовать `POSTGRES_PORT` → `PGBOUNCER_PORT` или добавить комментарий. Не блокирует, но накапливает confusion.

---

#### [WARNING] DRIFT-DP-8 · DevPlan: «No Makefile changes» — не совсем точно

- **DevPlan строка 23:** «No Makefile changes (discover-modules target уже зарегистрирован)»
- **Факт W6.2:** `core/entrypoint-manifest.yaml` нужно обновить для регистрации нового gate.
- **Оценка:** Entrypoint-manifest — не Makefile, но концептуально это «no build system changes». Формулировка приемлема.

---

#### [WARNING] DRIFT-DP-9 · Unit-тесты для новых Python-модулей отсутствуют

- **DevPlan:** W1 (`discover_test_infra()`), W3 (`NetworkLeaseManager`), W2 (`_TestInfra`) — ни одного unit-теста.
- **Риск:** Новый код без тестов противоречит языковой политике (Python-first) и увеличивает fragility.
- **Recommendation:** Добавить unit-тесты для `discover_test_infra()`, `NetworkLeaseManager.acquire/release`, `_TestInfra.get_container_name()`.

### 2.2 Cross-File Verification (фактические данные)

#### 2.2a Container Name Consistency

| Источник | Количество | Статус |
|----------|:---:|--------|
| docker-compose.test.yml (13 модулей) | 22 container_name | ✅ |
| `_STALE_CONTAINER_NAMES` smoke.py:726-749 | 22 entries | ✅ IDENTICAL SET |
| DevPlan Table 1.3 | 23 claimed | ❌ OFF-BY-ONE (см. ниже) |

**OFF-BY-ONE в DevPlan:** Секция 1.3 декларирует «23 уникальных container_name», но grep по всем docker-compose.test.yml показывает ровно 22. Пересчёт:
- backup-cron (1) + clickhouse (1) + hermes-agent (1) + infra-metrics (5) + langfuse (2) + litellm (1) + logging (2) + minio (1) + monitoring (3) + nginx (1) + postgres (2) + redis (1) + status-page (1) = **22**

#### 2.2b Port 6432 Fragmentation (Heatmap Verification)

| Файл | Количество вхождений |
|------|:---:|
| `test_component_pgbouncer.py` | 15 (строки 362, 455-685) |
| `test_smoke_postgres.py` | 7 (строки 404-467) |
| `test_pgbouncer_static.py` | 12 (строки 278-351) |
| `test_project_scaffold.py` | 3 (строки 516-517) |
| `test_scaffold_env_platform.py` | 5 (строки 225-262) |

**Всего:** 42 вхождения порта 6432 в 5 файлах. DevPlan утверждает «8+ мест» — заниженная оценка.

#### 2.2c Compose Project Name Consistency

| Проект | Файлы | Статус |
|--------|-------|--------|
| `ai-platform-test` | smoke.py (platform_services) | ✅ Легитимный — общий проект |
| `ai-platform-smoke-postgres` | test_smoke_postgres.py | ✅ Уникальный |
| `wave-redis-smoke` | test_smoke_redis.py | ✅ Уникальный |
| `wave-nginx-smoke` | test_smoke_nginx.py | ✅ Уникальный |
| `ai-platform-test-pgbouncer` | test_component_pgbouncer.py | ✅ Уникальный |
| `ai-platform-test-ch` | test_component_clickhouse.py | ✅ Уникальный |
| `ai-platform-test-hermes-pg` + `ai-platform-test-hermes` | test_component_hermes.py | ✅ Уникальные |

**Вердикт:** Все 7 compose-проектов уникальны. Ни одного коллидирующего `"ai-platform-test"` вне platform_services.

### 2.3 Module Contract Verification

Все 13 модулей имеют `docker-compose.test.yml` — соответствует `core/modules/AGENTS.md` invariant.
Все test.yml содержат `container_name` с суффиксом `-test` — соответствует convention.

---

## Section 3 — Invariant Status (Phase 3, выборочно)

| # | Invariant (из root AGENTS.md) | Статус | Evidence |
|---|--------|--------|----------|
| 1 | Makefile — единый фасад | HELD | `discover-modules` → `discover_modules.py` через entrypoint-manifest.yaml |
| 2 | Модель деплоя: git push → CI | HELD | Не затрагивается |
| 3 | org = context | HELD | Не затрагивается |
| 6 | make bootstrap-node — строго идемпотентный | HELD | Не затрагивается |
| 7 | Полный локальный стек через docker compose up | HELD | Тестовый compose остаётся валидным |
| 9 | Тестовый сервер может быть пересоздан | AT_RISK | W3 NetworkLeaseManager должен гарантировать cleanup — refcount leak при краше = нарушение |

### 3.1 Специфичные для DevPlan риски инвариантов

| Инвариант | Статус | Риск |
|-----------|--------|------|
| `test_ports` — два источника истины | AT_RISK | SMOKE_ENV + platform-env.yaml test_ports |
| NetworkLeaseManager vs ensure_external_networks | AT_RISK | Два параллельных механизма управления сетями |
| `check_foreign_containers_adapter` hardcodes convention | AT_RISK | Не derive from compose files как заявлено |

---

## Section 4 — Config Sync Audit (Phase 6, выборочно)

### 4.1 Env Variable Propagation

DevPlan не добавляет новых env-переменных в propagation chain. `test_ports` — это YAML-секция, не env variable. Риск propagation conflict отсутствует.

### 4.2 Compose Override Consistency

Test compose files используют паттерн `!override` для:
- `container_name` → test-specific (-test suffix)
- `networks` → test-prefixed networks (test-shared-db-net, etc.)
- `ports` → shifted ports (1XXXX)

**Вердикт:** Паттерн консистентен. DevPlan не нарушает override chain.

### 4.3 Network Consistency

DevPlan предлагает `NetworkLeaseManager` для управления test-сетями (test-shared-db-net, test-observability-net, etc.). Эти сети уже определены в:
- `networks.py:TEST_NETWORKS` (5 сетей)
- `platform-env.yaml:networks` (5 test-* записей)
- 13 docker-compose.test.yml (external: true)

**Вердикт:** Три источника определения test-сетей. DevPlan не консолидирует их — NetworkLeaseManager добавляет четвёртый. Рекомендация: сделать platform-env.yaml единственным source of truth, как заявлено в его MODULE_CONTRACT.

---

## Section 5 — Acceptance Criteria Analysis

| AC | Критерий | Оценка реализуемости | Замечания |
|----|----------|:---:|-----------|
| AC-1 | `discover_modules.py --test-infra --json` | ⚠️ BLOCKED DRIFT-DP-1 | Неверный путь к файлу |
| AC-2 | `STALE_CONTAINER_NAMES` = 22 | ✅ Реализуемо | Текущие 22 имени подтверждены |
| AC-3 | NetworkLeaseManager refcounting | ⚠️ AT_RISK DRIFT-DP-3 | Конфликт с ensure_external_networks |
| AC-4 | platform-env.yaml test_ports | ⚠️ AT_RISK DRIFT-DP-6 | Дублирование с SMOKE_ENV |
| AC-5 | Миграция 7 файлов | ✅ Реализуемо | Явные замены, backward-compatible |
| AC-6 | CI gate 5 проверок | ⚠️ AC-6e vacuous (DRIFT-DP-4) | Требует расширения scope |
| AC-7 | `make gate MODE=fast` green | ✅ Реализуемо | При исправлении DRIFT-DP-1 |
| AC-8 | Gate падает при drift | ✅ Реализуемо | При корректном AC-6e |

---

## Section 6 — Required DevPlan Fixes (Checklist)

### BLOCKER (перед реализацией):

- [ ] **DRIFT-DP-1:** Заменить `core/internal/scripts/discover_modules.py` → `core/internal/bootstrap/discover_modules.py` во ВСЕХ местах DevPlan: строки 23, 99, 181, 227, 280, 727, 731. Проверить `__file__` resolution в pseudo-code W2 (строка 280) — путь `parent.parent.parent` должен соответствовать реальной структуре: `bootstrap/discover_modules.py` → `bootstrap/` → `internal/` → `core/` → project root (4 уровня).

### HIGH (рекомендуется до реализации):

- [ ] **DRIFT-DP-2:** Обновить секцию 1.2 — `_STALE_CONTAINER_NAMES` сейчас полный (22/22), проблема в fragility ручной синхронизации.
- [ ] **DRIFT-DP-3:** Добавить в дизайн W3: решение о сосуществовании/замене `ensure_external_networks()` сетевым менеджером.
- [ ] **DRIFT-DP-4:** Расширить AC-6e scope на ВСЕ вхождения `"ai-platform-test"` как project name в тестовых файлах.
- [ ] **OFF-BY-ONE:** Исправить «23 уникальных container_name» → «22» в секции 1.3.

### MEDIUM (можно в процессе):

- [ ] **DRIFT-DP-5:** Пересмотреть `check_foreign_containers_adapter` — derive own_project из compose-файла, а не из конвенции.
- [ ] **DRIFT-DP-6:** Определить стратегию консолидации test_ports и SMOKE_ENV.
- [ ] **DRIFT-DP-9:** Добавить unit-тесты для W1-W3 новых Python-модулей.

### WARNING:

- [ ] **DRIFT-DP-7:** Уточнить формулировку «No Makefile changes» → «No Makefile changes (entrypoint-manifest registration only)».
- [ ] **DRIFT-DP-8:** Добавить комментарий к `POSTGRES_PORT: 6432` в platform-env.yaml (pgbouncer proxy port).

---

## Delegate

**Рекомендация:** Делегировать Architect для исправления BLOCKER и HIGH находок в DevPlan перед реализацией. CRITICAL drift (DRIFT-DP-1) делает DevPlan нереализуемым в текущем виде.

**Предлагаемое действие:** `task(subagent_type="Architect", description="Fix DevPlan 041 drift findings", prompt="Review VerificationReport.md at .ai/plans/041-test-infra-fault-tolerance/03-VerificationReport.md. Fix all BLOCKER and HIGH findings, then re-submit for QA.")`

$END_VERIFICATION_REPORT
