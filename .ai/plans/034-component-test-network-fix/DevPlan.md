# 034-DevPlan: Critical Test Infrastructure + PgBouncer Config Fixes

**Source:** User-reported bugs (5 unique, 4 actionable)
**Verified against codebase:** 2026-07-21

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Исправить 5 багов: регистрация _get_all_profiles в manifest, восстановление DATABASE_URLS в pgbouncer test.yml, создание test-shared-db-net для component-тестов, удаление observability integration теста.
DESCRIPTION:           5 багов разного severity (1×HIGH, 3×MEDIUM, 1×LOW).
                       HIGH: Docker-сеть test-shared-db-net не создаётся для component-тестов → 13 fixture setup ошибок (hermes + pgbouncer).
                       MEDIUM #1: _get_all_profiles не зарегистрирован в entrypoint-manifest.yaml → 2 gate/contract теста.
                       MEDIUM #2: Loki restart: "no" — корректно, не баг (документировать).
                       MEDIUM #3: pgbouncer test.yml environment: перезаписывает DATABASE_URLS из base.yml — pgbouncer-test стартует без баз данных.
                       LOW: test_integration_hermes_llm.py ссылается на core/modules/observability/docker-compose.base.yml (модуль удалён) → всегда skip. Удалить тест.
RATIONALE:             Bug #4 (HIGH) блокирует все component-тесты — CI красный. Bug #3 (MEDIUM) скрыто ломает pgbouncer component-тесты — pgbouncer стартует без сконфигурированных баз данных. Bug #1 (MEDIUM) — gate/contract regression. Bug #5 (LOW) — мёртвый код.
ACCEPTANCE_CRITERIA:
  **B1 (_get_all_profiles registration):**
     1. `_get_all_profiles` зарегистрирован в секции `dev` манифеста как make_target.
     2. `_get_all_profiles` присутствует в `allowed_verbs` списке.
     3. `make gate MODE=fast` → gate/contract тесты green.
  **B2 (Loki restart — no fix):**
     4. Документировано: `restart: "no"` в docker-compose.test.yml — корректное поведение для тестовых контейнеров.
  **B3 (Pgbouncer DATABASE_URLS restore):**
     5. `core/modules/postgres/docker-compose.test.yml` pgbouncer `environment:` содержит `DATABASE_URLS` с `${POSTGRES_PASSWORD:-test-pg-pwd}`.
     6. `DB_USER`, `DB_PASSWORD` присутствуют в test.yml pgbouncer environment.
     7. `POOL_MODE`, `MAX_CLIENT_CONN`, `DEFAULT_POOL_SIZE` — опционально, наследуются из base.yml (не в test.yml environment → base values).
     8. Pgbouncer component-тесты green.
  **B4 (test-shared-db-net creation):**
     9. `test_component_hermes.py::_EXTERNAL_NETWORKS` включает `test-shared-db-net`.
     10. `test_component_pgbouncer.py::_EXTERNAL_NETWORKS` включает `test-shared-db-net` (замена `pgbouncer-component-db-net`).
     11. 13 fixture setup ошибок устранены. Component-тесты hermes + pgbouncer green.
  **B5 (observability integration test removal):**
     12. `tests/test_integration_hermes_llm.py` удалён.
     13. Запись в `core/entrypoint-manifest.yaml` секции `gates` удалена (если существует).
     14. `tests/__pycache__/test_integration_hermes_llm.*.pyc` удалена.
IMPLEMENTS:            Fixes for 4 actionable bugs (B1, B3, B4, B5). B2 — documentation-only.
IMPACTS:               **Modified:**
                         - `core/entrypoint-manifest.yaml` (B1: регистрация _get_all_profiles)
                         - `core/modules/postgres/docker-compose.test.yml` (B3: pgbouncer environment vars)
                         - `tests/test_component_hermes.py` (B4: _EXTERNAL_NETWORKS)
                         - `tests/test_component_pgbouncer.py` (B4: _EXTERNAL_NETWORKS + _DB_NET_NAME)
                       **Deleted:**
                         - `tests/test_integration_hermes_llm.py` (B5)
REQUIRES:              Чистый working tree. Docker daemon running (для component-тестов). Python 3.10+, pytest.
TASK_SIZE:             SMALL (4 файла изменений, 1 файл удаления)
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Суперпозиционный анализ по каждому из 5 багов (3-5 гипотез) => GOAL_SUPERPOSITION
- GOAL B1: регистрация _get_all_profiles в manifest (dev секция + allowed_verbs) => GOAL_B1_MANIFEST
- GOAL B2: документирование Loki restart: "no" как expected => GOAL_B2_NONBUG
- GOAL B3: восстановление DATABASE_URLS в pgbouncer test.yml environment => GOAL_B3_PGBOUNCER_ENV
- GOAL B4: добавление test-shared-db-net в component test fixtures => GOAL_B4_NETWORK
- GOAL B5: удаление observability integration теста => GOAL_B5_DELETE
- GOAL File Manifest + Acceptance Criteria с командами проверки => GOAL_MANIFEST
- GOAL Dive into root cause: Compose override environment merge semantics + test network drift => GOAL_ROOT_CAUSE
**SECTION_USE_CASES:**
- USE_CASE CI runner запускает component-тесты → test-shared-db-net создаётся, все 13 fixture setup ошибок устранены => UC_CI_GREEN
- USE_CASE Разработчик запускает pgbouncer component-тест → pgbouncer-test стартует с DATABASE_URLS → databases сконфигурированы => UC_PGBOUNCER_WORKS
- USE_CASE gate MODE=fast → _get_all_profiles зарегистрирован, manifest parity тесты green => UC_GATE_GREEN
$END_DOCUMENT_PLAN
```

---

## 1. Superposition Analysis

### B1: _get_all_profiles не зарегистрирован в entrypoint-manifest.yaml

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Makefile target добавлен (DevPlan 033 W3-E3) без обновления manifest** | **85%** | `_get_all_profiles` — хелпер, добавленный для COMPOSE_PROFILES. Комментарий в allowed_verbs: «Private helper targets (underscore prefix)». Мог быть зарегистрирован в allowed_verbs, но пропущен в dev-секции make_target. |
| S2 | target есть в `allowed_verbs`, но отсутствует в секции `dev` как `make_target` entry | **70%** | Gate-тесты проверяют parity между секциями. Если target есть только в allowed_verbs без dev-секции — manifest неполный. |
| S3 | target есть в `.PHONY`, но забыт при обновлении manifest обеих секций | **60%** | `test_allowed_verbs_match_makefile` (A.3) сравнивает `.PHONY` targets с `allowed_verbs`. Если в manifest нет — MANIFEST_STALE. |
| S4 | `delegates_to: "echo"` не является файловым путём → gate-тест `test_delegates_to_paths_exist` флажит | **15%** | `_extract_delegate_paths` ищет `core/[\w./-]+\.sh` — `echo` не матчится → 0 paths → тест не флажит. Не причина. |
| S5 | AGENTS.md таблица имеет `_get_all_profiles`, но manifest allowed_verbs — нет | **10%** | AGENTS.md содержит `` `make _get_all_profiles` `` — `_extract_agents_verbs` извлекает. Но в manifest allowed_verbs уже есть (строка 605). Несоответствие маловероятно. |

**Коллапс:** S1+S2+S3 — наиболее вероятная комбинация. `_get_all_profiles` добавлен в `allowed_verbs` (строка 605) как комментарий «Private helper targets», но пропущен в dev-секции как полноценный `make_target` entry. Gate-тесты `test_allowed_verbs_match_makefile` и `test_agents_md_synced_with_manifest` сравнивают Makefile/AGENTS.md с allowed_verbs — если allowed_verbs содержит `_get_all_profiles`, тесты должны проходить. **Если тест падает** → `_get_all_profiles` отсутствует в `allowed_verbs` (состояние до исправления). Фикс: зарегистрировать в обеих секциях.

**Фикс:** Убедиться что `_get_all_profiles` зарегистрирован:
1. В секции `dev` как `make_target` entry с `mechanism: makefile-echo`, `delegates_to: "echo"`
2. В `allowed_verbs` списке

---

### B2: Loki restart: "no" — не баг

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Тестовые контейнеры имеют `restart: "no"` по дизайну** | **100%** ✅ CONFIRMED | `docker-compose.test.yml` contract (core/modules/AGENTS.md): «restart: "no" — тестовые контейнеры не авто-перезапускаются». Loki — часть тестового стека. |
| S2 | Ожидалось `unless-stopped` как в production | 0% | Production base.yml имеет `unless-stopped`. Test override — `restart: "no"`. Правильное поведение. |

**Коллапс:** S1 — документированный контракт. Никаких изменений.

**Фикс:** Документировать. No code changes.

---

### B3: ${POSTGRES_PASSWORD} отсутствует в DATABASE_URLS pgbouncer compose

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Pgbouncer test.yml `environment:` полностью ПЕРЕЗАПИСЫВАЕТ base.yml environment** | **95%** ✅ CONFIRMED | Docker Compose override: `environment:` mapping replaces, not merges. base.yml pgbouncer имеет 8 env vars (DATABASE_URLS, DB_USER, DB_PASSWORD, LISTEN_PORT, POOL_MODE, MAX_CLIENT_CONN, DEFAULT_POOL_SIZE, SERVER_IDLE_TIMEOUT, AUTH_TYPE). test.yml переопределяет `environment:` с 2 vars (AUTH_TYPE, LISTEN_PORT). Результат: только эти 2 vars. DATABASE_URLS потерян. |
| S2 | `POSTGRES_HOST_AUTH_METHOD=trust` в postgres-test отключает пароль → pgbouncer не нужен POSTGRES_PASSWORD | **5%** | Pgbouncer всё равно нужен DATABASE_URLS для конфигурации databases (SHOW DATABASES). Без DATABASE_URLS pgbouncer стартует с пустой конфигурацией. |
| S3 | `${POSTGRES_PASSWORD:?}` fail-fast в test окружении → контейнер падает | **10%** | В test окружении POSTGRES_PASSWORD может быть не установлен (trust auth). `:?` убьёт pgbouncer. Нужно `:-test-pg-pwd` для test. |
| S4 | Compose merge делает deep merge для mappings | **0%** | Docker Compose документация: mappings (dicts) в `environment:` ЗАМЕЩАЮТСЯ, не мержатся. Это не баг Compose, это ожидаемое поведение. |

**Коллапс:** S1 + S3. Docker Compose override environment semantics: test.yml `environment:` REPLACES base.yml `environment:`. DATABASE_URLS, DB_USER, DB_PASSWORD, etc. — все потеряны. Дополнительно: в test окружении POSTGRES_PASSWORD не установлен (trust auth), поэтому `:?` fail-fast убьёт pgbouncer даже если DATABASE_URLS добавить. Фикс: использовать `:-test-pg-pwd` вместо `:?PG_PASSWORD_REQUIRED`.

**Фикс:** Добавить в `core/modules/postgres/docker-compose.test.yml` pgbouncer `environment:` все необходимые переменные из base.yml, с заменой `${POSTGRES_PASSWORD:?...}` на `${POSTGRES_PASSWORD:-test-pg-pwd}`.

Необходимые env vars для pgbouncer-test:
- `DATABASE_URLS` — **критично**, содержит определение баз данных
- `DB_USER` — **критично**, используется для auth
- `DB_PASSWORD` — **критично**, хотя в trust-режиме может не проверяться
- `LISTEN_PORT` — уже есть (6432)
- `AUTH_TYPE` — уже есть (trust)

Опциональные (наследуют дефолты pgbouncer образа):
- `POOL_MODE`, `MAX_CLIENT_CONN`, `DEFAULT_POOL_SIZE`, `SERVER_IDLE_TIMEOUT`

---

### B4: Docker-сеть test-shared-db-net не создана → 13 fixture setup ошибок

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Component-тесты не включают `test-shared-db-net` в свои `_EXTERNAL_NETWORKS`** | **90%** ✅ CONFIRMED | `test_component_hermes.py` _EXTERNAL_NETWORKS: `[shared-db-net, proxy-net, hermes-agent-net, observability-net]` — production сети, нет `test-shared-db-net`. `test_component_pgbouncer.py` _EXTERNAL_NETWORKS: `[pgbouncer-component-db-net]` — изолированная сеть, не `test-shared-db-net`. Но оба теста используют `docker-compose.test.yml` для postgres, который требует `test-shared-db-net` (external: true). |
| S2 | `test_infra` autouse fixture должен создавать test-shared-db-net (из platform-env.yaml), но не создаёт | **40%** | `platform-env.yaml` содержит `test-shared-db-net` (строка 38). `test_infra` fixture читает из platform-env.yaml. Но T2.2 conditional activation проверяет markers — component тесты имеют `@pytest.mark.component` → должно активироваться. |
| S3 | `test_infra` teardown удаляет test-shared-db-net между модулями | **30%** | `test_infra` scope=session → teardown только в конце сессии. Но при parallel execution (xdist) — каждый worker имеет свою сессию → гонка. |
| S4 | `ensure_external_networks()` вызывается, но падает молча | **10%** | Функция использует `subprocess.run(check=False)` — ошибки не выбрасывают исключений. Docker network create может падать если Docker daemon недоступен. |
| S5 | Network naming convention mismatch: test.yml требует `test-shared-db-net`, fixture создаёт `pgbouncer-component-db-net` | **85%** ✅ CONFIRMED | `test_component_pgbouncer.py` строка 80: `_DB_NET_NAME = "pgbouncer-component-db-net"`. Но test.yml hardcodes `test-shared-db-net` (строка 85). ENV `DB_NET_NAME` не используется compose-файлом. Mismatch. |

**Коллапс:** S1 + S5 — основная причина. Component-тесты создают НЕ ТЕ сети, которые требуются compose-файлам.

Механика бага:
1. `test_component_hermes.py` использует `docker-compose.base.yml` + `docker-compose.test.yml` для postgres
2. `docker-compose.test.yml` объявляет `networks.test-shared-db-net: external: true` (строка 85-86)
3. Сервисы postgres/pgbouncer в test.yml форсированы на `test-shared-db-net` через `!override`
4. `test_component_hermes.py::_EXTERNAL_NETWORKS` содержит `shared-db-net` (production), не `test-shared-db-net`
5. Docker Compose пытается найти external сеть `test-shared-db-net` → не находит → setup failure

**Фикс:**
- `test_component_hermes.py`: добавить `test-shared-db-net` в `_EXTERNAL_NETWORKS`
- `test_component_pgbouncer.py`: заменить `pgbouncer-component-db-net` на `test-shared-db-net` в `_DB_NET_NAME` и `_EXTERNAL_NETWORKS` (либо добавить `test-shared-db-net` дополнительно)

---

### B5: core/modules/observability/docker-compose.base.yml отсутствует → 1 integration тест пропущен

| # | Hypothesis | Likelihood | Rationale |
|---|-----------|------------|-----------|
| S1 | **Модуль observability разбит на 5 составляющих (TASK-3)** | **100%** ✅ CONFIRMED | `test_pgbouncer_static.py` строка 23: «observability module split into 5 modules». `test_gate_workflow_consistency.py` строка 67-68: `_OBSERVABILITY_REFERENCE_PATTERN` — верифицирует отсутствие ссылок на observability. |
| S2 | `test_integration_hermes_llm.py` — единственный выживший reference | **100%** ✅ CONFIRMED | Файл ссылается на `core/modules/observability/docker-compose.base.yml` (строка 532). Проверка `os.path.exists` → всегда skip. Тест никогда не выполняется. |
| S3 | Тест нужно мигрировать на новые модули (litellm + langfuse + monitoring + logging) вместо удаления | **20%** | Интеграционный тест покрывает полный стек hermes→litellm→loki+postgres. Модуль observability не существует. Миграция требует рефакторинга compose-путей. Но тест всегда скипался → его бизнес-логика никогда не верифицировалась после split. |
| S4 | Файл нужно сохранить как reference для будущей миграции | **5%** | Git история сохраняет файл. Нет необходимости держать мёртвый код в рабочем дереве. |

**Коллапс:** S1+S2 — тест мёртв после TASK-3 (observability split). Удалить.

**Фикс:** Удалить `tests/test_integration_hermes_llm.py`. Очистить `tests/__pycache__/test_integration_hermes_llm.*.pyc`.

---

## 2. Root Cause Analysis (Systemic)

### B4 — центральный баг, блокирующий 13 тестов

Корневая причина: **drift между component test fixtures и docker-compose test overlay конвенцией**.

DevPlan 017 (Test Network Isolation) ввёл конвенцию: test overlay использует `test-*` префикс для сетей (Option B). `docker-compose.test.yml` для postgres корректно форсирует сервисы на `test-shared-db-net`. Но component-тесты, написанные ДО этой конвенции, продолжают создавать production-сети (`shared-db-net`).

Вторичная причина: `_EXTERNAL_NETWORKS` в component-тестах — ручной список, не синхронизированный с `networks:` секциями compose-файлов. Нет автоматической верификации, что fixture создаёт все сети, требуемые compose-файлами.

### B3 — PgBouncer environment override

Корневая причина: **непонимание Docker Compose merge semantics для `environment:`**.

Compose merge для массивов — replace (документировано в test.yml комментариях). Но для словарей (mappings, как `environment: KEY: value`) поведение LESS очевидно — это ТОЖЕ replace, не deep merge. Разработчик, добавляя `environment:` в test.yml, ожидал additive merge (добавить AUTH_TYPE к существующим vars), но получил полную замену.

Третичная причина: нет автоматического теста, который парсит test.yml и валидирует, что критичные env vars из base.yml присутствуют в test.yml для каждого сервиса.

---

## 3. File Manifest

| # | File | Action | Change |
|---|------|--------|--------|
| B1 | `core/entrypoint-manifest.yaml` | MODIFY | Убедиться: `_get_all_profiles` в секции `dev` как `make_target` и в `allowed_verbs` |
| B3 | `core/modules/postgres/docker-compose.test.yml` | MODIFY | Добавить `DATABASE_URLS`, `DB_USER`, `DB_PASSWORD` в pgbouncer `environment:` |
| B4 | `tests/test_component_hermes.py` | MODIFY | Добавить `test-shared-db-net` в `_EXTERNAL_NETWORKS` |
| B4 | `tests/test_component_pgbouncer.py` | MODIFY | Заменить `_DB_NET_NAME` на `test-shared-db-net`, обновить `_EXTERNAL_NETWORKS` |
| B5 | `tests/test_integration_hermes_llm.py` | DELETE | Удалить файл |
| B5 | `tests/__pycache__/test_integration_hermes_llm.*.pyc` | DELETE | Очистить pycache |

---

## 4. Verification Commands

```bash
# B1: Verify manifest registration
grep -n "_get_all_profiles" core/entrypoint-manifest.yaml
# Expected: entries in dev section AND allowed_verbs list

# B1: Run gate tests
make gate MODE=fast
# Expected: all gate/contract tests green

# B3: Verify pgbouncer test.yml has DATABASE_URLS
grep -A 10 "pgbouncer:" core/modules/postgres/docker-compose.test.yml | grep DATABASE_URLS
# Expected: DATABASE_URLS line with ${POSTGRES_PASSWORD:-test-pg-pwd}

# B3: Dry-run pgbouncer compose config
cd core/modules/postgres && docker compose -f docker-compose.base.yml -f docker-compose.test.yml config --no-interpolate 2>/dev/null | grep -A 20 "pgbouncer:"
# Expected: environment contains DATABASE_URLS, DB_USER, DB_PASSWORD

# B4: Verify network is created by fixtures
python3 -c "
from tests._conftest.networks import TEST_NETWORKS
assert 'test-shared-db-net' in TEST_NETWORKS
print('OK: test-shared-db-net in TEST_NETWORKS')
"

# B4: Run component tests individually
pytest tests/test_component_hermes.py -v --timeout=300
pytest tests/test_component_pgbouncer.py -v --timeout=300
# Expected: 0 fixture setup errors

# B5: Verify observability test is deleted
ls tests/test_integration_hermes_llm.py 2>&1
# Expected: No such file or directory
```

---

## 5. Rollback Guide

| Bug | Rollback |
|-----|----------|
| B1 | Удалить `_get_all_profiles` из `dev` секции и `allowed_verbs` манифеста |
| B3 | Удалить добавленные env vars из pgbouncer test.yml `environment:` |
| B4 | Вернуть старые значения `_EXTERNAL_NETWORKS` и `_DB_NET_NAME` |
| B5 | `git checkout tests/test_integration_hermes_llm.py` |

---

$END_DEVPLAN
