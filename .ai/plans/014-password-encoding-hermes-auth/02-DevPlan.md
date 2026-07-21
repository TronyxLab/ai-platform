<!-- $START_DEVPLAN -->
<!-- $ARTIFACT_CONTRACT
  PURPOSE:       DevPlan для Wave 014 — реализация charset constraint на все пароли + unified auth credential propagation (HERMES_DASHBOARD_PASSWORD).
                  Два бага из STRESS_TEST_REPORT (2026-07-20 tronyx-vps): (1) спецсимволы в POSTGRES_PASSWORD ломают pgbouncer entrypoint,
                  (2) hermes-agent unhealthy из-за пустого HERMES_DASHBOARD_PASSWORD.
  DESCRIPTION:   Реализация опций, выбранных в Brief:
                  Часть 1 (Option A): charset constraint ^[A-Za-z0-9._-]+$ на все пароли, встраиваемые в URL.
                  Часть 2 (Explicit Assignment): HERMES_DASHBOARD_PASSWORD = PLATFORM_MASTER_PASSWORD явно в secrets.env,
                  compose-файлы без fallback-цепочек. TRAP-аннотация о политике unified auth.
  RATIONALE:     Блокирует production bootstrap. Charset constraint — preventive, проще чем URL-encode pipeline.
                  Explicit assignment через secrets-init.sh — одна точка управления creds вместо compose-fallbacks.
  ACCEPTANCE_CRITERIA:
    - POSTGRES_PASSWORD со спецсимволами отвергается на этапе validation/deploy
    - Hermes-agent healthcheck проходит (healthy) — HERMES_DASHBOARD_PASSWORD = PLATFORM_MASTER_PASSWORD
    - Все пароли в secrets.env соответствуют ^[A-Za-z0-9._-]+$
    - Gate MODE=fast зелёный (включая новый test_gate_password_charset.py)
    - Существующие тесты не сломаны
  IMPLEMENTS: Brief.md Wave 014 Tx1 (charset constraint), Tx2 (hermes auth), Tx3 (tests/gate)
  IMPACTS:
    - core/secrets-manifest.yaml — поле charset для password-секретов
    - core/internal/bootstrap/deploy-modules.sh — валидация charset
    - .env.example — унифицированный CONSTRAINT-комментарий
    - core/modules/hermes-agent/docker-compose.base.yml — убрать fallback
    - core/internal/bootstrap/secrets-init.sh — новый файл
    - core/internal/bootstrap/node-lifecycle.sh — интеграция secrets-init.sh
    - core/internal/scaffold/context-init.sh — TRAP-комментарий
    - tests/gates/test_gate_password_charset.py — новый gate-тест
    - tests/test_pgbouncer_static.py — обновление теста
    - core/entrypoint-manifest.yaml — регистрация gate
  REQUIRES:
    - НЕ требует Python urllib.parse
    - НЕ требует изменений в edoburu/pgbouncer образе
    - python3+yaml (уже используется)
-->
<!-- GREP_SUMMARY: DevPlan wave-014 password-charset-constraint hermes-dashboard-auth unified-auth secrets-init deploy-modules gate-test entrypoint-manifest -->
<!-- STRUCTURE: ┌Requirements Analysis┐ → ┌Architecture Overview┐ → ┌Data Flow┐ → ┌$TASKS (10)┐ → ┌$PARALLEL_GROUPS (3 waves)┐ → ┌$TEST_SPEC┐ → ┌Design Decisions┐ → ┌File Manifest┐ -->

# DevPlan — Wave 014: Password Charset Constraint & Unified Auth Credential Propagation

**Plan:** 02-DevPlan.md
**Parent:** 01-Brief.md
**Created:** 2026-07-21
**Task size:** STANDARD (11 files, business logic, no new architectural decisions)

---

## 1. Requirements Analysis

### 1.1 Key Success Criteria

| # | Criterion | Measurable by |
|---|-----------|---------------|
| SC-1 | POSTGRES_PASSWORD cо спецсимволами (`SkyNet!!%)`) отвергается на этапе deploy (до pgbouncer) | `_validate_secret_charsets()` → exit 1 + gate test |
| SC-2 | Hermes-agent healthcheck healthy после устранения fallback | `make gate MODE=fast` + `make test MARKER=static` зелёные |
| SC-3 | Все секреты с `charset` в manifest проходят валидацию | `_validate_secret_charsets()` в deploy-modules.sh |
| SC-4 | Существующие тесты не сломаны (регрессия) | `make test MARKER=static` зелёный |
| SC-5 | Gate-тесты покрывают негативные сценарии (charset violation) | `test_gate_password_charset.py` — 7 parametrized cases |

### 1.2 Scope

**In scope:**
- Charset constraint на все password-секреты в `secrets-manifest.yaml`
- Bash-валидация charset в `deploy-modules.sh`
- Устранение compose-fallback для `HERMES_DASHBOARD_PASSWORD`
- Новый `secrets-init.sh` с TRAP-аннотацией unified auth
- Gate-тест charset validation + обновление test_pgbouncer_static.py
- Регистрация gate-теста в `entrypoint-manifest.yaml`

**Out of scope (из Brief):**
- PostgreSQL password rotation — отдельная задача
- Hermes-agent `/auth/login` 422 — уже исправлен nginx intercept
- Ротация паролей ClickHouse, MinIO — пароли уже charset-safe
- Миграция существующих VPS-паролей — ручная операция

---

## 2. Architecture Overview

### 2.1 Draft Code Graph

```
┌─ Tx1: Charset Constraint ─────────────────────────────────────────────┐
│                                                                        │
│  secrets-manifest.yaml (SSoT)                                          │
│  ├── secrets[]                                                         │
│  │   ├── name: POSTGRES_PASSWORD                                       │
│  │   │   charset: "^[A-Za-z0-9._-]+$"          ← NEW FIELD             │
│  │   ├── name: CLICKHOUSE_PASSWORD                                     │
│  │   │   charset: "^[A-Za-z0-9._-]+$"          ← NEW                   │
│  │   ├── ... (все password-секреты)                                    │
│  │   └── name: AGE_SECRET_KEY                                          │
│  │       charset: "^AGE-SECRET-KEY-[A-Za-z0-9]+$"  ← свой формат       │
│  │                                                                     │
│  deploy-modules.sh                                                     │
│  └── _validate_secret_charsets()            ← NEW FUNCTION              │
│      · python3: читает manifest → charset → re.match(env_val)          │
│      · Вызывается из main() после загрузки secrets.env                 │
│                                                                        │
│  .env.example                                                          │
│  └── CONSTRAINT: комментарии для POSTGRES_PASSWORD,                     │
│      CLICKHOUSE_PASSWORD, MINIO_ROOT_*, PLATFORM_MASTER_PASSWORD +     │
│      унифицированный блок для всех сервис-паролей                       │
└────────────────────────────────────────────────────────────────────────┘

┌─ Tx2: Hermes Dashboard Auth — Explicit Assignment ────────────────────┐
│                                                                        │
│  secrets-init.sh (NEW)                                                 │
│  · PLATFORM_MASTER_PASSWORD → HERMES_DASHBOARD_PASSWORD                │
│  · PLATFORM_MASTER_PASSWORD → GF_SECURITY_ADMIN_PASSWORD               │
│  · PLATFORM_MASTER_PASSWORD → LANGFUSE_INIT_USER_PASSWORD              │
│  · TRAP[POLICY]: unified auth annotation                               │
│  · Idempotent: если сервис-пароль уже задан → не перезаписывается       │
│                                                                        │
│  hermes-agent/docker-compose.base.yml                                  │
│  └── HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: "${HERMES_DASHBOARD_PASSWORD}" │
│      (было: "${HERMES_DASHBOARD_PASSWORD:-}" — fallback убран)          │
│                                                                        │
│  node-lifecycle.sh                                                     │
│  └── init mode: after step_12b_ensure_secrets → call secrets-init.sh   │
│                                                                        │
│  context-init.sh                                                       │
│  └── TRAP comment: secrets-init.sh вызывается при bootstrap             │
└────────────────────────────────────────────────────────────────────────┘

┌─ Tx3: Gate & Tests ───────────────────────────────────────────────────┐
│                                                                        │
│  tests/gates/test_gate_password_charset.py (NEW)                       │
│  ├── test_secrets_manifest_charset_defined_for_url_passwords            │
│  ├── test_password_charset_validation (parametrized: 7 cases)           │
│  ├── test_no_db_url_contains_raw_postgres_password_without_encoded      │
│  └── test_hermes_compose_has_no_fallback                                │
│                                                                        │
│  tests/test_pgbouncer_static.py (MODIFY)                               │
│  └── test_pgbouncer_password_charset_constraint (NEW test function)     │
│                                                                        │
│  core/entrypoint-manifest.yaml (MODIFY)                                │
│  └── gates: +test_gate_password_charset                                │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Step-by-Step Data Flow

```
1. Operator генерирует пароль: openssl rand -hex 32 → POSTGRES_PASSWORD="abc123..."
2. Пароль записывается в SOPS secrets → шифруется age
3. make bootstrap-node NODE=tronyx-vps
   └── node-lifecycle.sh --mode init
       ├── step_10_decrypt_secrets → SOPS decrypt → secrets.env
       ├── step_12b_ensure_secrets → autogen secrets (LITELLM_MASTER_KEY, etc.)
       ├── [NEW] secrets-init.sh → HERMES_DASHBOARD_PASSWORD ← PLATFORM_MASTER_PASSWORD
       └── step_14_node_update → node-lifecycle.sh --mode update
           └── deploy-modules.sh
               ├── main(): загрузка secrets.env
               ├── [NEW] _validate_secret_charsets()
               │   · python3: parse manifest → for each secret with charset:
               │     · re.match(charset, env_value)
               │     · FAIL if mismatch → exit 1 (блокирует deploy)
               └── deploy_docker_module() → docker compose up
```

**Ключевая точка отказа:** если `POSTGRES_PASSWORD=SkyNet!!%)` → `_validate_secret_charsets()` возвращает FAIL → `main()` exit 1 → deploy заблокирован ДО docker compose up. Это защищает pgbouncer от crash-loop.

---

## 3. $TASKS

### TASK-1: secrets-manifest.yaml — charset field
**Priority:** HIGH
**Dependencies:** None
**Complexity:** 3
**Output:** `core/secrets-manifest.yaml` — все password-секреты получают поле `charset`

**Acceptance Criteria:**
- POSTGRES_PASSWORD, CLICKHOUSE_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, S3_ACCESS_KEY, S3_SECRET_KEY имеют `charset: "^[A-Za-z0-9._-]+$"`
- PLATFORM_MASTER_PASSWORD, HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD, LITELLM_MASTER_KEY, OPENAI_API_KEY — превентивно тот же charset
- AGE_SECRET_KEY имеет `charset: "^AGE-SECRET-KEY-[A-Za-z0-9]+$"` (свой формат)
- GHCR_PULL_TOKEN имеет `charset: "^[A-Za-z0-9_]+$"` (PAT формат)
- TELEGRAM_BOT_TOKEN имеет `charset: "^[0-9]+:[A-Za-z0-9_-]+$"` (формат токена)
- LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LITELLM_MASTER_KEY — charset не добавляется (генерируются через `openssl rand -hex`, всегда валидны)
- `step_12b_ensure_secrets()` в lib/secrets.sh продолжает работать без изменений (manifest-совместим)

**File:** `core/secrets-manifest.yaml`

---

### TASK-2: deploy-modules.sh — charset validation
**Priority:** HIGH
**Dependencies:** TASK-1 (manifest должен иметь charset поля)
**Complexity:** 5
**Output:** `core/internal/bootstrap/deploy-modules.sh` — новая функция `_validate_secret_charsets()`

**Acceptance Criteria:**
- Функция `_validate_secret_charsets()` определена в deploy-modules.sh
- Использует python3 + yaml для чтения manifest + re.match для валидации
- Вызывается из `main()` ПОСЛЕ загрузки secrets.env (экспорта SECRETS_ENV_FILE)
- FAIL для любого секрета, не прошедшего charset → exit 1 с сообщением о нарушении
- OK для секретов без charset (пропускаются)
- OK для незаданных секретов (пропускаются — проверяются отдельно `_check_env_requires`)
- LDD логи: IMP:8 для OK, IMP:9 для FAIL
- Graceful degradation: если manifest не найден → WARN + return 0 (не блокирует деплой)

**File:** `core/internal/bootstrap/deploy-modules.sh`

---

### TASK-3: .env.example — CONSTRAINT comments
**Priority:** MEDIUM
**Dependencies:** None
**Complexity:** 2
**Output:** `.env.example` — унифицированные CONSTRAINT-комментарии

**Acceptance Criteria:**
- POSTGRES_PASSWORD: комментарий `# ⚠️ CONSTRAINT: POSTGRES_PASSWORD must match ^[A-Za-z0-9._-]+$`
- CLICKHOUSE_PASSWORD: такой же CONSTRAINT (уже есть, обновить формат для унификации)
- MINIO_ROOT_USER, MINIO_ROOT_PASSWORD: CONSTRAINT добавлен (ранее отсутствовал)
- PLATFORM_MASTER_PASSWORD: CONSTRAINT добавлен
- HERMES_DASHBOARD_PASSWORD: CONSTRAINT + note "Инициализируется из PLATFORM_MASTER_PASSWORD"
- Существующий CLICKHOUSE_PASSWORD constraint (строки 55-58) обновлён для единообразия формата
- Все CONSTRAINT-комментарии используют одинаковый формат: `# ⚠️ CONSTRAINT: <VAR> must match <regex>`

**File:** `.env.example`

---

### TASK-4: hermes-agent compose — remove fallback
**Priority:** HIGH
**Dependencies:** None
**Complexity:** 1
**Output:** `core/modules/hermes-agent/docker-compose.base.yml` — строка 110 без fallback

**Acceptance Criteria:**
- Строка 110: `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: "${HERMES_DASHBOARD_PASSWORD}"` (без `:-`)
- Нет fallback на PLATFORM_MASTER_PASSWORD в compose
- Если HERMES_DASHBOARD_PASSWORD не задан → docker compose up упадёт с ошибкой (by design)
- Gate-тест test_hermes_compose_has_no_fallback проверяет отсутствие fallback

**File:** `core/modules/hermes-agent/docker-compose.base.yml`

---

### TASK-5: secrets-init.sh — new file
**Priority:** HIGH
**Dependencies:** None
**Complexity:** 4
**Output:** `core/internal/bootstrap/secrets-init.sh` — новый файл

**Acceptance Criteria:**
- Скрипт имеет shebang `#!/usr/bin/env bash`, `set -euo pipefail`
- MODULE_CONTRACT с @purpose, @scope, @invariants, @rationale
- TRAP[POLICY] аннотация о unified auth (как в Brief §Tx2.2)
- Читает PLATFORM_MASTER_PASSWORD из env; если не задан → exit 1
- SERVICE_PASSWORDS массив: HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD, LANGFUSE_INIT_USER_PASSWORD
- Для каждой переменной: если не задана → export = PLATFORM_MASTER_PASSWORD; если задана → сохранить (idempotent)
- LDD логи: IMP:9 для FAIL (нет PLATFORM_MASTER_PASSWORD), IMP:8 для каждого initialized/kept, IMP:9 для summary
- Выполняется после `step_12b_ensure_secrets` (secrets уже в env)
- Поддерживает `source` (может быть sourced из другого скрипта) И прямой вызов

**File:** `core/internal/bootstrap/secrets-init.sh` (NEW)

---

### TASK-6: node-lifecycle.sh — integrate secrets-init.sh
**Priority:** HIGH
**Dependencies:** TASK-5 (secrets-init.sh должен существовать)
**Complexity:** 2
**Output:** `core/internal/bootstrap/node-lifecycle.sh` — вызов secrets-init.sh после ensure-secrets

**Acceptance Criteria:**
- В init-режиме: после `step_12b_ensure_secrets` (строка ~1064) добавлен checkpoint_step для secrets-init.sh
- Используется `_step_hash` для content-hash tracking
- Вызов: `bash "${CORE_DIR}/internal/bootstrap/secrets-init.sh"` или аналогичный source-based
- В update-режиме: secrets-init.sh НЕ вызывается повторно (пароли уже инициализированы при init)
- Если secrets-init.sh падает (exit 1) → bootstrap продолжается (WARN, не CRITICAL — пароли могут быть заданы в SOPS)

**File:** `core/internal/bootstrap/node-lifecycle.sh`

---

### TASK-7: context-init.sh — TRAP note
**Priority:** LOW
**Dependencies:** None
**Complexity:** 1
**Output:** `core/internal/scaffold/context-init.sh` — TRAP-комментарий о secrets-init

**Acceptance Criteria:**
- TRAP[DECISION] комментарий в context-init.sh:
  - При создании нового контекста сервис-пароли инициализируются через `secrets-init.sh` при первом bootstrap
  - context-init.sh НЕ вызывает secrets-init.sh (нет PLATFORM_MASTER_PASSWORD на этапе scaffold)
  - Оператор должен задать PLATFORM_MASTER_PASSWORD в SOPS secrets до bootstrap
- Комментарий размещён в логическом месте (рядом с _create_dirs или _report_summary)

**File:** `core/internal/scaffold/context-init.sh`

---

### TASK-8: test_gate_password_charset.py — new gate test
**Priority:** HIGH
**Dependencies:** TASK-1 (manifest charset), TASK-4 (compose без fallback)
**Complexity:** 5
**Output:** `tests/gates/test_gate_password_charset.py` — 4 тестовые функции

**Acceptance Criteria:**
- Файл имеет GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT
- `@pytest.mark.gate` на всех тестах
- `test_secrets_manifest_charset_defined_for_url_passwords()`:
  - Проверяет что POSTGRES_PASSWORD, CLICKHOUSE_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, S3_ACCESS_KEY, S3_SECRET_KEY имеют `charset: "^[A-Za-z0-9._-]+$"` в manifest
- `test_password_charset_validation(special_password, should_fail)` — parametrized (7 cases из Brief):
  - "SkyNet!!%)" → should_fail=True
  - "pass#hash" → should_fail=True
  - "pwd with space" → should_fail=True
  - "pass/with/slash" → should_fail=True
  - "valid-pass_123.abc" → should_fail=False
  - "openssl_rand_hex_32" → should_fail=False
  - "simple" → should_fail=False
- `test_no_db_url_contains_raw_postgres_password_without_encoded()`:
  - Проверяет отсутствие `POSTGRES_PASSWORD_ENCODED` в 4 compose-файлах (postgres, langfuse, litellm, infra-metrics)
- `test_hermes_compose_has_no_fallback()`:
  - Проверяет отсутствие `:-${PLATFORM_MASTER_PASSWORD}` в hermes-agent compose
  - Проверяет наличие `${HERMES_DASHBOARD_PASSWORD}` в compose
- Использует `ldd_trajectory` декоратор (если применимо) или caplog для IMP:9

**File:** `tests/gates/test_gate_password_charset.py` (NEW)

---

### TASK-9: test_pgbouncer_static.py — charset constraint awareness
**Priority:** MEDIUM
**Dependencies:** TASK-4 (compose changes)
**Complexity:** 2
**Output:** `tests/test_pgbouncer_static.py` — новый тест

**Acceptance Criteria:**
- Новый тест `test_pgbouncer_password_charset_constraint()`:
  - Проверяет что DATABASE_URLS содержит `${POSTGRES_PASSWORD}` (не ENCODED)
  - Проверяет отсутствие `POSTGRES_PASSWORD_ENCODED` в postgres compose
- Тест использует существующий fixture `postgres_fixtures`
- Имеет `@pytest.mark.static_audit` и `@ldd_trajectory`
- IMP:9 лог при ASSERT

**File:** `tests/test_pgbouncer_static.py`

---

### TASK-10: entrypoint-manifest.yaml — register gate test
**Priority:** HIGH
**Dependencies:** TASK-8 (gate test file must exist)
**Complexity:** 1
**Output:** `core/entrypoint-manifest.yaml` — запись в секции `gates`

**Acceptance Criteria:**
- Новая запись в секции `gates`:
  - id: `password-charset`
  - description: краткое описание
  - test_file: `test_gate_password_charset.py`
- Запись соответствует формату существующих gate-записей
- После регистрации: `make gate MODE=fast` запускает новый тест

**File:** `core/entrypoint-manifest.yaml`

---

## 4. $PARALLEL_GROUPS

### Wave 1 — Independent, no shared files
**Tasks:** TASK-1, TASK-3, TASK-4, TASK-5, TASK-7
**Rationale:** Все задачи затрагивают разные файлы, нет зависимостей друг от друга.

```
coder read 02-DevPlan.md, implement Wave 1: TASK-1, TASK-3, TASK-4, TASK-5, TASK-7
```

### Wave 2 — Depend on Wave 1 completions
**Tasks:** TASK-2, TASK-6, TASK-8, TASK-9
**Rationale:** TASK-2 зависит от TASK-1 (manifest), TASK-6 зависит от TASK-5 (secrets-init.sh), TASK-8 зависит от TASK-1 + TASK-4, TASK-9 зависит от TASK-4.

```
coder read 02-DevPlan.md, implement Wave 2: TASK-2, TASK-6, TASK-8, TASK-9
```

### Wave 3 — Manifest registration (last)
**Tasks:** TASK-10
**Rationale:** Регистрация gate-теста — последний шаг, зависит от TASK-8.

```
coder read 02-DevPlan.md, implement Wave 3: TASK-10
```

### File-intersection matrix (Wave 1)

| File | TASK-1 | TASK-3 | TASK-4 | TASK-5 | TASK-7 |
|------|--------|--------|--------|--------|--------|
| core/secrets-manifest.yaml | ✗ | | | | |
| .env.example | | ✗ | | | |
| hermes-agent/docker-compose.base.yml | | | ✗ | | |
| core/internal/bootstrap/secrets-init.sh | | | | ✗ | |
| core/internal/scaffold/context-init.sh | | | | | ✗ |

No file collisions — all Wave 1 tasks can run in parallel.

### File-intersection matrix (Wave 2)

| File | TASK-2 | TASK-6 | TASK-8 | TASK-9 |
|------|--------|--------|--------|--------|
| core/internal/bootstrap/deploy-modules.sh | ✗ | | | |
| core/internal/bootstrap/node-lifecycle.sh | | ✗ | | |
| tests/gates/test_gate_password_charset.py | | | ✗ | |
| tests/test_pgbouncer_static.py | | | | ✗ |

No file collisions — all Wave 2 tasks can run in parallel.

---

## 5. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/gates/test_gate_password_charset.py | test_secrets_manifest_charset_defined_for_url_passwords | Все URL-пароли в secrets-manifest.yaml имеют charset constraint | core/secrets-manifest.yaml |
| tests/gates/test_gate_password_charset.py | test_password_charset_validation[SkyNet!!%)] | Пароль со спецсимволами !, %, ) отвергается | regex ^[A-Za-z0-9._-]+$ |
| tests/gates/test_gate_password_charset.py | test_password_charset_validation[pass#hash] | Пароль с # отвергается | regex ^[A-Za-z0-9._-]+$ |
| tests/gates/test_gate_password_charset.py | test_password_charset_validation[pwd with space] | Пароль с пробелом отвергается | regex ^[A-Za-z0-9._-]+$ |
| tests/gates/test_gate_password_charset.py | test_password_charset_validation[pass/with/slash] | Пароль со слэшем отвергается | regex ^[A-Za-z0-9._-]+$ |
| tests/gates/test_gate_password_charset.py | test_password_charset_validation[valid-pass_123.abc] | Валидный charset-safe пароль принимается | regex ^[A-Za-z0-9._-]+$ |
| tests/gates/test_gate_password_charset.py | test_password_charset_validation[openssl_rand_hex_32] | Hex-пароль (типичный) принимается | regex ^[A-Za-z0-9._-]+$ |
| tests/gates/test_gate_password_charset.py | test_password_charset_validation[simple] | Простой буквенный пароль принимается | regex ^[A-Za-z0-9._-]+$ |
| tests/gates/test_gate_password_charset.py | test_no_db_url_contains_raw_postgres_password_without_encoded | Нигде нет POSTGRES_PASSWORD_ENCODED (артефакт Option B) | 4 compose files (postgres, langfuse, litellm, infra-metrics) |
| tests/gates/test_gate_password_charset.py | test_hermes_compose_has_no_fallback | Hermes compose не имеет fallback на PLATFORM_MASTER_PASSWORD | core/modules/hermes-agent/docker-compose.base.yml |
| tests/test_pgbouncer_static.py | test_pgbouncer_password_charset_constraint | PgBouncer DATABASE_URLS использует ${POSTGRES_PASSWORD} без ENCODED | core/modules/postgres/docker-compose.base.yml |
| tests/gates/test_gate_secrets_manifest.py | (existing) test_manifest_vs_module_yaml | Регрессия: существующие gate-тесты проходят | core/secrets-manifest.yaml |

**Rationale for test coverage:** 11 тестов (7 parametrized + 4 структурных) покрывают все три аспекта: charset constraint validation, отсутствие Option B артефактов, и hermes compose без fallback.

---

## 6. Acceptance Criteria (Summary)

| # | Criterion | Verification |
|---|-----------|-------------|
| AC-1 | POSTGRES_PASSWORD со спецсимволами отвергается на этапе deploy | `_validate_secret_charsets()` FAIL → `deploy-modules.sh` exit 1 |
| AC-2 | Hermes-agent healthcheck healthy | `docker compose up hermes-agent` → healthy after start_period |
| AC-3 | Все пароли в secrets.env charset-safe | `_validate_secret_charsets()` PASS (все секреты OK) |
| AC-4 | Gate MODE=fast зелёный | `make gate MODE=fast` exit 0 (включая test_gate_password_charset.py) |
| AC-5 | Существующие тесты не сломаны | `make test MARKER=static` зелёный |
| AC-6 | Негативные тесты charset violation проходят | `test_password_charset_validation` — 4 fail cases + 3 pass cases |

---

## 7. File Manifest

| # | File | Action | TASK |
|---|------|--------|------|
| 1 | `core/secrets-manifest.yaml` | MODIFY — добавить charset поле | TASK-1 |
| 2 | `core/internal/bootstrap/deploy-modules.sh` | MODIFY — добавить `_validate_secret_charsets()` + вызов в main() | TASK-2 |
| 3 | `.env.example` | MODIFY — унифицированные CONSTRAINT-комментарии | TASK-3 |
| 4 | `core/modules/hermes-agent/docker-compose.base.yml` | MODIFY — убрать fallback в строке 110 | TASK-4 |
| 5 | `core/internal/bootstrap/secrets-init.sh` | CREATE — инициализация сервис-паролей + TRAP | TASK-5 |
| 6 | `core/internal/bootstrap/node-lifecycle.sh` | MODIFY — добавить checkpoint_step для secrets-init.sh | TASK-6 |
| 7 | `core/internal/scaffold/context-init.sh` | MODIFY — TRAP-комментарий о secrets-init | TASK-7 |
| 8 | `tests/gates/test_gate_password_charset.py` | CREATE — 4 тестовые функции | TASK-8 |
| 9 | `tests/test_pgbouncer_static.py` | MODIFY — новый тест `test_pgbouncer_password_charset_constraint` | TASK-9 |
| 10 | `core/entrypoint-manifest.yaml` | MODIFY — регистрация gate `password-charset` | TASK-10 |

**Total:** 8 modified + 2 created = 10 files.

---

## 8. Design Decisions

### D1: Charset validation in deploy-modules.sh (not decrypt-secrets.sh)
**@rationale:** Валидация charset на этапе deploy (не decrypt), потому что:
- `decrypt-secrets.sh` — pure decryption, не должен знать о бизнес-правилах
- `deploy-modules.sh` — последняя точка перед docker compose up, где все secrets уже в env
- Fail-fast: если charset violation обнаружен при deploy → блокируется до запуска контейнеров
- Graceful degradation: если manifest отсутствует → валидация пропускается (не блокирует)

### D2: secrets-init.sh — idempotent, не перезаписывает operator-defined значения
**@rationale:** Оператор должен иметь возможность переопределить сервис-пароль (например, `HERMES_DASHBOARD_PASSWORD != PLATFORM_MASTER_PASSWORD`). `secrets-init.sh` проверяет: если переменная уже задана → сохраняет, если нет → инициализирует из `PLATFORM_MASTER_PASSWORD`. Это гарантирует:
- При первом bootstrap: все сервис-пароли = PLATFORM_MASTER_PASSWORD
- При повторном bootstrap: операторские значения не перезаписываются

### D3: secrets-init.sh ОТДЕЛЬНО от step_12b_ensure_secrets
**@rationale:** `step_12b_ensure_secrets` генерирует autogen-секреты (LITELLM_MASTER_KEY, LANGFUSE_*) через `openssl rand -hex`. `secrets-init.sh` решает другую задачу — инициализацию сервис-паролей из мастер-пароля. Разделение ответственности:
- `ensure_secrets` → генерация случайных секретов (криптографические ключи)
- `secrets-init` → propagation мастер-пароля в сервис-пароли (политика unified auth)

### D4: context-init.sh — только TRAP, не вызов secrets-init.sh
**@rationale:** `context-init.sh` выполняется на машине разработчика при `make new-context`. На этом этапе:
- Нет PLATFORM_MASTER_PASSWORD (пароль будет задан позже в SOPS)
- Нет secrets.env (SOPS ещё не создан)
- secrets-init.sh будет вызван при первом `make bootstrap-node` на VPS

TRAP-комментарий документирует этот факт для будущих агентов.

### D5: .env.example CONSTRAINT — единый формат
**@rationale:** Существующий CLICKHOUSE_PASSWORD constraint (строки 55-58) имеет свой формат. Унификация всех CONSTRAINT-комментариев под единый формат `# ⚠️ CONSTRAINT: <VAR> must match <regex>` упрощает:
- grep-поиск всех constraint в проекте
- Автоматическую валидацию constraint-покрытия в gate-тестах
- Человекочитаемость

---

## 9. Debt Intake

**Аудит TRAP[DEBT] в affected модулях:** выполнен grep по всем файлам из File Manifest. Найдены существующие TRAP-аннотации:

| File | TRAP | Classification |
|------|------|----------------|
| `hermes-agent/docker-compose.base.yml:22` | TRAP[DECISION] cross-compose depends_on | DEFER — out of scope |
| `hermes-agent/docker-compose.base.yml:43` | TRAP[DECISION] context overlay L2 | DEFER — out of scope |
| `hermes-agent/docker-compose.base.yml:52` | TRAP[DECISION] layer naming L0/L1/L2 | DEFER — out of scope |
| `hermes-agent/docker-compose.base.yml:59` | TRAP[PERF] no digest pin | DEFER — out of scope |
| `deploy-modules.sh:30` | TRAP[DECISION] staging via separate project | DEFER — out of scope |
| `deploy-modules.sh:68` | TRAP[DECISION] ghcr_login moved to lib | DEFER — out of scope |
| `node-lifecycle.sh:117` | TRAP[DECISION] step_warn rename | DEFER — out of scope |
| `node-lifecycle.sh:240` | TRAP[DECISION] shared bridges.txt | DEFER — out of scope |
| `node-lifecycle.sh:379` | TRAP[DECISION] CORE_DEPLOY_DIR dead code | DEFER — out of scope |
| `node-lifecycle.sh:1114` | TRAP[BUG] NODE_YAML not passed | DEFER — out of scope |

**No IN_SCOPE debt items found.** Все существующие TRAP-аннотации относятся к другим аспектам системы, не затрагиваемым Wave 014.

---

## 10. Implementation Commands

### Wave 1 (independent, parallel-safe)
```
coder read /Users/tronyx/projects/ai-platform/.ai/plans/014-password-encoding-hermes-auth/02-DevPlan.md, implement Wave 1: TASK-1 (secrets-manifest.yaml charset), TASK-3 (.env.example CONSTRAINT), TASK-4 (hermes-agent compose remove fallback), TASK-5 (secrets-init.sh new file), TASK-7 (context-init.sh TRAP note)
```

### Wave 2 (depends on Wave 1)
```
coder read /Users/tronyx/projects/ai-platform/.ai/plans/014-password-encoding-hermes-auth/02-DevPlan.md, implement Wave 2: TASK-2 (deploy-modules.sh charset validation), TASK-6 (node-lifecycle.sh secrets-init integration), TASK-8 (test_gate_password_charset.py), TASK-9 (test_pgbouncer_static.py update)
```

### Wave 3 (registration)
```
coder read /Users/tronyx/projects/ai-platform/.ai/plans/014-password-encoding-hermes-auth/02-DevPlan.md, implement Wave 3: TASK-10 (entrypoint-manifest.yaml gate registration)
```

### Verification (after all waves)
```
make gate MODE=fast && make test MARKER=static
```

---

<!-- $END_DEVPLAN -->
