# DevPlan 049 — Secrets Centralization & Drift Fix

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранить дрейф между secrets-manifest.yaml, docker-compose и кодом bootstrap, обеспечить единый механизм инжекции секретов в контейнеры, и добавить pre-up валидацию секретов.
DESCRIPTION:           6 задач: (1) синхронизация consumers в манифесте с реальным compose-использованием, (2) удаление неиспользуемых LLM-ключей из compose, (3) добавление docker compose wrapper для pre-up валидации, (4) sops --set для персистенции autogen-секретов (уже реализовано — аудит), (5) очистка cross-chain S3/AWS в backup-cron, (6) удаление OPENAI_API_KEY из hermes-agent.
RATIONALE:             Аудит 2026-07-22 вскрыл: 4 consumers missing, 1 spurious, 7 compose-переменных не зарегистрированы в манифесте, dead schema (node.yaml#secrets), единственная хрупкая точка инжекции (docker_orchestrator.py), отсутствие валидации при ручном docker compose up. Пользователь принял решения: secrets-manifest.yaml как SSoT, env vars (не Docker secrets), autogen → sops persistence, pre-up wrapper.
ACCEPTANCE_CRITERIA:
  AC-1: secrets-manifest.yaml consumers точно соответствуют compose-файлам (0 missing, 0 spurious)
  AC-2: Все неиспользуемые LLM API keys удалены из litellm + hermes-agent compose (ANTHROPIC, OPENROUTER, GLM, LITELLM_LICENSE, OPENAI_API_KEY в hermes-agent)
  AC-3: pre-up wrapper (compose-plugin или shell-wrapper) блокирует docker compose up при отсутствии required секретов
  AC-4: S3/AWS cross-chain в backup-cron заменён на канонические S3_ACCESS_KEY/S3_SECRET_KEY
  AC-5: secrets-manifest.yaml consumers обновлены: +minio для S3_BUCKET, +litellm для LANGFUSE_PUBLIC_KEY/SECRET_KEY, -backup-cron для TELEGRAM_BOT_TOKEN
  AC-6: gate-тесты проходят (test_gate_secrets_manifest.py), lint зелёный, make gate MODE=fast green
  AC-7: HERMES_DASHBOARD_PASSWORD и LANGFUSE_INIT_USER_PASSWORD генерируются из PLATFORM_MASTER_PASSWORD (уже работает — подтверждено secrets-init.sh:51-55)
IMPLEMENTS:            AGENTS.md инвариант 2 (Makefile — единый фасад), языковая политика (новый код = Python для wrapper)
IMPACTS:
  - core/secrets-manifest.yaml — исправление consumers (5 записей)
  - core/modules/litellm/docker-compose.base.yml — удаление ANTHROPIC_API_KEY, OPENROUTER_API_KEY, LITELLM_LICENSE
  - core/modules/hermes-agent/docker-compose.base.yml — удаление ANTHROPIC_API_KEY, OPENROUTER_API_KEY, GLM_API_KEY, OPENAI_API_KEY
  - core/modules/backup-cron/docker-compose.base.yml — удаление AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY cross-chain
  - core/internal/bootstrap/deploy/ — docker compose wrapper (новый Python-модуль)
  - core/entrypoints/ — entrypoint для wrapper (новый shell-фасад)
  - Makefile (root) — новый таргет или интеграция в существующий docker compose flow
  - tests/gates/test_gate_secrets_manifest.py — может потребовать обновления после правок манифеста
  - tests/test_secrets_validation.py — predeploy тест, может требовать обновления
REQUIRES:
  - Python ≥3.10
  - secrets-manifest.yaml (существующий)
  - docker compose (v2)
$END_ARTIFACT_CONTRACT

---

## Problem Statement (аудит 2026-07-22)

Полный cross-reference аудит: `secrets-manifest.yaml` (32 записи) ↔ 13 `docker-compose.base.yml` ↔ код bootstrap pipeline.

### Найденные проблемы

| ID | Severity | Проблема | Детали |
|----|----------|----------|--------|
| P1 | MED | Dead schema — `secrets` в node.yaml не читается кодом | Схема определена, но ни один Python/bash не читает `secrets.enc_file` или `secrets.required[]`. Реальный flow идёт через `/run/platform/secrets.env`. **Решено:** удалить dead schema из node.schema.json. |
| P2 | HIGH | Manifest drift — consumers не совпадают с compose | 4 missing, 1 spurious. Валидатор `secrets_validator.py` использует манифест → false confidence. |
| P3 | MED | 7 compose-переменных не зарегистрированы в манифесте | Из них 5 — LLM API keys, которые пользователь решил удалить из compose. Оставшиеся 2 (LITELLM_METRICS_TOKEN, API_SERVER_KEY) — зарегистрировать как optional. |
| P4 | HIGH | Единственная точка инжекции (`docker_orchestrator.py:_build_compose_args`) | Ручной `docker compose up` → секреты недоступны → fail. Нужен pre-up wrapper. |
| P5 | MED | S3/AWS credential cross-chain в backup-cron | `AWS_ACCESS_KEY_ID` ↔ `S3_ACCESS_KEY` циклическая ссылка. **Решено:** удалить AWS_* из compose, оставить канонические S3_*. |
| P6 | LOW | 5 переменных без fallback и без fail-fast | NEXTAUTH_SECRET, SALT — уже autogen (OK). HERMES_DASHBOARD_PASSWORD, LANGFUSE_INIT_USER_PASSWORD — уже генерируются из PLATFORM_MASTER_PASSWORD в secrets-init.sh (OK). OPENAI_API_KEY в hermes-agent — удаляется. |
| P7 | LOW | Нет Docker secrets | **Решено:** env vars достаточно. Не внедряем. |
| P8 | INFO | autogen persistence | sops --set уже реализован в `step_12b_ensure_secrets()` (secrets.sh:361-378). Подтверждён audit-ом. |

---

## Superposition Analysis (с decisions)

| Decision | Option | Verdict |
|----------|--------|---------|
| **SSoT** | secrets-manifest.yaml | ✅ Выбрано |
| | node.yaml как SSoT | ❌ Dead schema, удалить |
| | .env.platform как SSoT | ❌ Не выбрано |
| **Docker secrets** | Не внедрять, env vars достаточно | ✅ Выбрано |
| | Docker secrets для критичных | ❌ |
| **Autogen persistence** | sops --set (уже реализовано) | ✅ Подтверждено |
| | Docker secrets | ❌ |
| **Pre-up validation** | Docker compose wrapper/plugin | ✅ Выбрано |
| | Только CI/bootstrap | ❌ |
| **LLM API keys** | Удалить из compose | ✅ Выбрано |
| | Зарегистрировать в манифесте | ❌ |

---

## Tasks

### TASK-1: Fix secrets-manifest.yaml consumers

**Что:** Исправить 4 missing + 1 spurious consumer + dead schema cleanup.

**Изменения в `core/secrets-manifest.yaml`:**

| Запись | Было | Стало | Причина |
|--------|------|-------|---------|
| `S3_BUCKET` consumers | `[backup-cron]` | `[backup-cron, minio]` | minio-createbuckets использует S3_BUCKET |
| `LANGFUSE_PUBLIC_KEY` consumers | `[langfuse]` | `[langfuse, litellm]` | litellm использует для Langfuse tracing |
| `LANGFUSE_SECRET_KEY` consumers | `[langfuse]` | `[langfuse, litellm]` | litellm использует для Langfuse tracing |
| `TELEGRAM_BOT_TOKEN` consumers | `[hermes-agent, monitoring, backup-cron]` | `[hermes-agent, monitoring]` | backup-cron не использует TELEGRAM_BOT_TOKEN в compose |
| `LITELLM_METRICS_TOKEN` | отсутствует | `tier: required, consumers: [monitoring], source: sops` | Используется prometheus→litellm в monitoring compose |
| `API_SERVER_KEY` | отсутствует | `tier: optional, consumers: [hermes-agent], source: autogen` | Используется hermes-agent |

**Изменения в `core/schemas/node.schema.json`:**
- Удалить секцию `secrets` (строки 117-155) — dead schema

**Validation:**
```bash
make gate MODE=fast  # test_gate_secrets_manifest.py должен пройти
```

---

### TASK-2: Remove unused LLM API keys from compose files

**Что:** Удалить переменные, которые пользователь явно указал к удалению.

**`core/modules/litellm/docker-compose.base.yml`:**
- Удалить: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `LITELLM_LICENSE`

**`core/modules/hermes-agent/docker-compose.base.yml`:**
- Удалить: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GLM_API_KEY`, `OPENAI_API_KEY`

**Примечание:** `DEEPSEEK_API_KEY` в litellm НЕ удаляется (пользователь не упоминал). Остаётся как optional.

---

### TASK-3: Clean S3/AWS cross-chain in backup-cron

**Что:** Упростить до канонических переменных.

**`core/modules/backup-cron/docker-compose.base.yml`:**
```yaml
# Было:
S3_ACCESS_KEY: "${S3_ACCESS_KEY:-${AWS_ACCESS_KEY_ID}}"
AWS_ACCESS_KEY_ID: "${AWS_ACCESS_KEY_ID:-${S3_ACCESS_KEY}}"
S3_SECRET_KEY: "${S3_SECRET_KEY:-${AWS_SECRET_ACCESS_KEY}}"
AWS_SECRET_ACCESS_KEY: "${AWS_SECRET_ACCESS_KEY:-${S3_SECRET_KEY}}"

# Стало:
S3_ACCESS_KEY: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
S3_SECRET_KEY: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"
```

Удалить строки с `AWS_ACCESS_KEY_ID` и `AWS_SECRET_ACCESS_KEY`.

---

### TASK-4: Docker compose pre-up wrapper

**Что:** Создать `docker-compose` wrapper, который перед `up` проверяет наличие всех required секретов из `secrets-manifest.yaml` для целевых модулей.

**Архитектура:**

```
make up  (или docker compose up)
  └── core/entrypoints/compose-wrapper.sh
        └── python3 core/internal/bootstrap/deploy/compose_preflight.py
              ├── читает secrets-manifest.yaml
              ├── читает /run/platform/secrets.env + os.environ
              ├── определяет модули из docker-compose аргументов
              ├── проверяет все required секреты для этих модулей
              ├── валидирует charset constraints
              └── exit 0 → передаёт управление docker compose
                  exit 1 → блокирует, выводит missing secrets
```

**Новый файл:** `core/internal/bootstrap/deploy/compose_preflight.py`
- Python-модуль (~200 LOC)
- Функции: `parse_compose_args()`, `resolve_modules()`, `check_secrets()`, `main()`
- Переиспользует `secrets_validator.py` для charset-валидации

**Новый файл:** `core/entrypoints/compose-wrapper.sh`
- Shell-фасад (~30 LOC)
- Вызывает `python3 compose_preflight.py "$@"` → `exec docker compose "$@"`

**Интеграция в Makefile:**
- Не подменяем глобальный `docker compose` (риск: сломать CI)
- Добавляем алиас: `make compose-up MODULES=postgres,litellm` → wrapper → docker compose
- Либо: создаём `/usr/local/bin/docker-compose-platform` на VPS через install.sh

**Решение по scope:** Создаём `make compose-safe-up MODULES=<list>`. Не подменяем системный docker compose. Оператор сам решает, использовать wrapper или нет.

---

### TASK-5: Verify autogen persistence (sops --set)

**Что:** Подтвердить, что sops --set работает корректно. Уже реализован в `step_12b_ensure_secrets()` (secrets.sh:361-378).

**Проверка:**
1. `step_12b_ensure_secrets()` читает generated-секреты из манифеста
2. Если секрет отсутствует — генерирует через `openssl rand`
3. Если существует SOPS encrypted file — вызывает `sops --set` для персистенции
4. Функция `secrets-init.sh` дополнительно инициализирует HERMES_DASHBOARD_PASSWORD, LANGFUSE_INIT_USER_PASSWORD из PLATFORM_MASTER_PASSWORD

**Результат аудита:** Механизм уже реализован. Изменений не требуется.

---

### TASK-6: Tests & gate

**Что:** Актуализировать тесты после изменений.

1. `tests/gates/test_gate_secrets_manifest.py` — проверить, что тест проходит после правок манифеста (новые consumers, удалённые spurious)
2. `tests/test_secrets_validation.py` — проверить, что predeploy тест проходит (удалённые LLM-ключи не считаются missing)
3. Новый тест: `tests/unit/test_compose_preflight.py` — unit-тесты для compose_preflight.py
4. `make gate MODE=fast` — зелёный перед push
5. `ruff format . && ruff check --fix .` — форматирование

---

## Rollback Plan

| Change | Rollback |
|--------|----------|
| secrets-manifest.yaml consumers | `git revert` коммит |
| docker-compose.base.yml удаление LLM ключей | `git revert` коммит |
| compose_preflight.py | `git revert` коммит — wrapper не подменяет системный docker compose, только новый make target |
| node.schema.json удаление secrets | `git revert` коммит |
| backup-cron cross-chain fix | `git revert` коммит — но S3_ACCESS_KEY/S3_SECRET_KEY канонические, миграция обратно маловероятна |

---

## After Completion

- [ ] `make gate MODE=fast` green
- [ ] `ruff format . && ruff check --fix .` чисто
- [ ] `make bootstrap-node NODE=<test>` на test-ноде — secrets flow не сломан
- [ ] Ручной `docker compose up` с wrapper: блокирует при отсутствии секретов, пропускает при наличии
- [ ] Gate test `test_gate_secrets_manifest.py` — 0 failures после правок consumers

$END_DEVPLAN
