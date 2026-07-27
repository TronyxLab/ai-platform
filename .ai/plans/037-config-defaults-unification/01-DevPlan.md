$START_DEVPLAN

# DevPlan 037 — Унификация default-значений и конфигурационных источников

$ARTIFACT_CONTRACT
PURPOSE: Унификация дрейфа default-значений S3_REGION, S3_PREFIX, S3_BUCKET, CONTEXT между platform-infra.yaml (SoT), Python-кодом и docker-compose файлами. Создание Python-фасада для централизованного чтения default-значений.
DESCRIPTION: Провести superposition-анализ архитектуры конфигурационных файлов (platform-infra.yaml vs platform-env.yaml), создать Python-модуль `core/internal/config/platform_config.py` как единый фасад для чтения default-значений, исправить все места дрейфа в Python-коде и compose-файлах.
RATIONALE: Текущий дрейф создаёт риск: изменение default в SoT не доходит до consumers, которые используют хардкоженные значения. При смене S3-провайдера или деплой-контекста часть системы будет использовать старые default'ы, часть — новые. Единый фасад + выравнивание всех consumers устраняет этот класс проблем.
ACCEPTANCE_CRITERIA:
  - AC1: Все 4 переменные (S3_REGION, S3_PREFIX, S3_BUCKET, CONTEXT) имеют единый SoT (platform-infra.yaml)
  - AC2: Python-фасад `platform_config.py` предоставляет типизированный доступ ко всем default-значениям
  - AC3: Все Python-consumers (backup_config, s3_ssl_cache, cert_orchestrator, preflight, docker_orchestrator, agent_watchdog, context_deployer) импортируют default'ы из фасада, а не хардкодят
  - AC4: Все compose-файлы (backup-cron, hermes-agent, minio, langfuse) используют `${VAR:-default}` где default совпадает с SoT
  - AC5: CI gate `make check-env-defaults` проходит без ошибок после всех изменений
  - AC6: Все существующие тесты проходят (0 регрессий)
IMPLEMENTS: Унификация конфигурационных default-значений, устранение дрейфа S3/CONTEXT
IMPACTS:
  - `core/internal/config/platform_config.py` (NEW) — Python-фасад для default-значений
  - `core/modules/backup-cron/scripts/backup_config.py` — замена хардкоженных констант на импорт из фасада
  - `core/internal/bootstrap/s3_ssl_cache.py` — замена DEFAULT_S3_REGION, прояснение DEFAULT_S3_PREFIX
  - `core/internal/bootstrap/cert_orchestrator.py` — выравнивание S3_BUCKET default
  - `core/internal/bootstrap/preflight.py` — выравнивание S3_BUCKET default
  - `core/internal/bootstrap/deploy/docker_orchestrator.py` — CONTEXT default
  - `core/internal/bootstrap/deploy/context_deployer.py` — CONTEXT default
  - `core/modules/hermes-agent/watchdog/agent_watchdog.py` — CONTEXT default
  - `core/modules/backup-cron/docker-compose.base.yml` — выравнивание S3_BUCKET
  - `core/modules/hermes-agent/docker-compose.base.yml` — CONTEXT default
  - `core/modules/minio/docker-compose.base.yml` — S3_BUCKET default
  - `core/modules/langfuse/docker-compose.base.yml` — S3_BUCKET nested default
  - `core/internal/scripts/sync_env_defaults.py` — проверка консистентности
REQUIRES:
  - `core/platform-infra.yaml` (SoT, существует)
  - `platform-env.yaml` (генерируется из platform-infra.yaml)
  - `core/internal/scripts/sync_env_defaults.py` (существует)

---

## Source

Задача от пользователя: провести superposition-анализ и написать DevPlan для унификации дрейфа default-значений и конфигурационных файлов.

Дрейф S3/CONTEXT между platform-infra.yaml (SoT), Python-кодом и docker-compose файлами.

---

## Debt Intake

Перед началом анализа проверены существующие TRAP-ы и DEBT-регистры в зоне изменений:

| Файл | TRAP | Вердикт |
|------|------|---------|
| `core/internal/bootstrap/s3_ssl_cache.py` | Нет TRAP[DEBT] по дрейфу | IN_SCOPE — добавляем в задачи |
| `core/modules/backup-cron/scripts/backup_config.py` | TRAP[BUSINESS] AR8 (единственное S3-хранилище) | DEFER — бизнес-акцент не затрагивает default'ы |
| `core/modules/hermes-agent/docker-compose.base.yml` | TRAP[DECISION] по platform pin | DEFER — не относится к дрейфу default'ов |

---

## Requirements Analysis — Key Success Criteria

1. **KSC1 — Единый SoT:** platform-infra.yaml `env_defaults` — единственное место, где определены default-значения для S3_REGION, S3_PREFIX, S3_BUCKET, CONTEXT, PLATFORM_CONTEXT
2. **KSC2 — Python-фасад:** Все Python-consumers получают default'ы через `core/internal/config/platform_config.py`, не через хардкоженные константы
3. **KSC3 — Compose-выравнивание:** Все `${VAR:-default}` в docker-compose файлах совпадают с SoT
4. **KSC4 — Нулевой дрейф:** После изменений grep по `us-east-1`, `platform/ssl-certs` (в контексте S3_PREFIX), `platform-backups` (в контексте S3_BUCKET), `"personal"` (в контексте CONTEXT) не находит расхождений с SoT
5. **KSC5 — Обратная совместимость:** Ни один существующий тест не ломается; поведение системы не меняется (только default'ы выравниваются)

---

## Superposition Analysis — Архитектура конфигурационных файлов

### Текущее состояние

```
platform-infra.yaml (SoT, AUTHORITATIVE)
  ├── networks, volumes, proxy, provides (статическая инфраструктура)
  └── env_defaults (140 строк, включая S3_*, CONTEXT, PLATFORM_CONTEXT)
       │
       ▼ generate_platform_env.py
platform-env.yaml (GENERATED — DO NOT EDIT)
  ├── networks, volumes, proxy, provides (копия из platform-infra)
  ├── env_defaults (копия из platform-infra)
  ├── port_mappings, test_ports (авто-обнаружение)
  └── profiles (авто-обнаружение)
       │
       ▼ sync_env_defaults.py
.env.example (GENERATED — шаблон для разработчиков)
```

**Проблема:** Python-код и compose-файлы НЕ читают ни platform-infra.yaml, ни platform-env.yaml. Они хардкодят свои default'ы, создавая дрейф.

### Option A: Единый `platform-env.yaml` (объединить infra + env) [score: 5/10]

**Approach:** Объединить platform-infra.yaml и platform-env.yaml в один файл. Удалить разделение на «статическую инфраструктуру» и «генерируемые секции».

**Trade-offs:**
- (+) Один файл = один SoT, невозможно расхождение между infra и env
- (+) Проще для понимания: один конфигурационный файл платформы
- (−) Ломает существующую архитектуру генерации: `generate_platform_env.py` становится ненужным или требует переписывания
- (−) Смешивает статическую инфраструктуру (networks, volumes — редко меняются) с динамическими секциями (profiles, port_mappings — авто-обнаружение)
- (−) Высокий риск регрессии: затронуты provision, CI, тесты — все consumers platform-env.yaml
- (−) Нарушает инвариант 11 (Manifest Generation Contract): platform-infra.yaml — авторитативный источник

**Best when:** нужна радикальная перестройка конфигурационной архитектуры (не сейчас)

### Option B: Чёткое разделение + Python-фасад [score: 9/10]

**Approach:** Сохранить текущую архитектуру (platform-infra.yaml → platform-env.yaml), но добавить Python-фасад `core/internal/config/platform_config.py`, который читает platform-env.yaml и предоставляет типизированные default'ы. Все Python-consumers импортируют default'ы из фасада. Compose-файлы выравнивают `${VAR:-default}` с SoT.

**Trade-offs:**
- (+) Минимальные изменения: архитектура generation остаётся нетронутой
- (+) Python-фасад — естественный слой абстракции, соответствующий языковой политике (Python-first)
- (+) Легко тестировать: фасад можно замокать в тестах
- (+) Постепенное внедрение: можно мигрировать consumers по одному
- (+) Не нарушает инвариант 11
- (−) Два физических файла (platform-infra.yaml + platform-env.yaml) остаются — но это архитектурное решение, не баг

**Best when:** нужно устранить дрейф с минимальным риском и сохранить существующую архитектуру generation

### Option C: `platform-infra.yaml` как SoT, удалить `env_defaults` из `platform-env.yaml` [score: 6/10]

**Approach:** Оставить platform-infra.yaml единственным источником env_defaults. Убрать секцию env_defaults из platform-env.yaml (или оставить как reference). Python-фасад читает platform-infra.yaml напрямую.

**Trade-offs:**
- (+) platform-infra.yaml — настоящий «единственный SoT», без дублирования в platform-env.yaml
- (+) Меньше путаницы: env_defaults определены только в одном месте
- (−) Ломает `sync_env_defaults.py`, который читает platform-env.yaml
- (−) Нарушает инвариант: platform-env.yaml позиционируется как «canonical environment descriptor — consumed by provision-environment.sh, CI workflows»
- (−) Высокий риск: CI, provision, и тесты читают platform-env.yaml — изменение структуры сломает их

### Option D: `platform_config.py` как единственный SoT (Python-first) [score: 4/10]

**Approach:** Вынести ВСЕ default'ы в Python-модуль `core/internal/config/platform_config.py`. YAML-файлы становятся generated/deprecated. Все consumers (Python, compose, shell) читают из Python-фасада.

**Trade-offs:**
- (+) Максимальная типобезопасность (Python dataclasses с валидацией)
- (+) Автоматический IDE-autocomplete для всех default'ов
- (−) Радикальное изменение: compose-файлы не могут читать Python
- (−) Ломает generation pipeline (YAML → YAML)
- (−) Shell-скрипты не могут импортировать Python (нужен adapter)

### Recommendation: **Option B** — Чёткое разделение + Python-фасад

**Justification:**
1. Сохраняет инвариант 11 (Manifest Generation Contract)
2. Минимальный риск регрессии — architecture generation не меняется
3. Соответствует языковой политике (Python-first)
4. Постепенное внедрение — можно деплоить инкрементально
5. Создаёт переиспользуемый Python-фасад, который можно расширять для будущих default'ов

**Also considered:** Option A (rejected: ломает generation pipeline, высокий риск), Option C (rejected: ломает consumers platform-env.yaml), Option D (rejected: compose не читает Python).

---

## Architecture Overview — Draft Code Graph

```
┌─ platform-infra.yaml ────────────────────────────────────────┐
│  env_defaults:                                                │
│    S3_REGION: "ru-1"          ← AUTHORITATIVE SoT             │
│    S3_PREFIX: "platform/backups"                              │
│    S3_BUCKET: "test-bucket"                                   │
│    CONTEXT: "test"                                            │
│    PLATFORM_CONTEXT: "personal"                               │
└──────────────────────┬───────────────────────────────────────┘
                       │ generate_platform_env.py
                       ▼
┌─ platform-env.yaml (GENERATED) ──────────────────────────────┐
│  env_defaults: (identical copy)                               │
└──────────────────────┬───────────────────────────────────────┘
                       │
         ┌─────────────┼─────────────────┐
         ▼             ▼                  ▼
   sync_env_defaults  NEW:                 compose files
   → .env.example     platform_config.py  ${VAR:-default}
                      │
                      ├──▶ backup_config.py    (remove _DEFAULT_S3_REGION, etc.)
                      ├──▶ s3_ssl_cache.py     (replace DEFAULT_S3_REGION)
                      ├──▶ cert_orchestrator.py (S3_BUCKET default)
                      ├──▶ preflight.py        (S3_BUCKET default)
                      ├──▶ docker_orchestrator.py (CONTEXT default)
                      ├──▶ agent_watchdog.py   (CONTEXT default)
                      └──▶ context_deployer.py (CONTEXT default)
```

### Новый модуль: `core/internal/config/platform_config.py`

```python
# GREP_SUMMARY: platform_config, config-facade, defaults, SoT, S3_REGION, S3_PREFIX, S3_BUCKET, CONTEXT, PLATFORM_CONTEXT
# STRUCTURE: ▶ load platform-env.yaml → env_defaults dict → ◇ typed accessors → ⎋ get_default(key, fallback)
# region MODULE_CONTRACT
## @purpose  Единый Python-фасад для чтения default-значений из platform-env.yaml.
##           Все consumers платформы получают default'ы только через этот модуль.
## @scope    Импортируется backup_config.py, s3_ssl_cache.py, cert_orchestrator.py,
##           preflight.py, docker_orchestrator.py, agent_watchdog.py, context_deployer.py
## @invariants
##   - Единственный Source of Truth для default-значений в Python-коде
##   - Загружает platform-env.yaml при первом импорте (lazy-load с кэшированием)
##   - Все accessors возвращают str; числовые значения — ответственность вызывающего
##   - Если platform-env.yaml недоступен — использует жёстко закодированные fallback'и,
##     идентичные значениям в platform-infra.yaml (defence-in-depth)
## @rationale Устраняет класс дрейфа «SoT обновлён, consumers — нет».
##            Централизованный фасад делает default'ы grepable и тестируемыми.
# endregion MODULE_CONTRACT

import os
from pathlib import Path
from typing import Optional
import yaml

# ... lazy-load platform-env.yaml ...

def get_default(key: str, fallback: str = "") -> str: ...

# Typed accessors
def default_s3_region() -> str: ...
def default_s3_prefix() -> str: ...
def default_s3_bucket() -> str: ...
def default_context() -> str: ...
def default_platform_context() -> str: ...
```

---

## Data Flow — Step by Step

1. **Загрузка:** `platform_config.py` при первом импорте читает `platform-env.yaml` → извлекает `env_defaults` → кэширует в `_defaults: dict[str, str]`
2. **Python-consumer:** `from core.internal.config.platform_config import default_s3_region` → вызывает `default_s3_region()` → возвращает `_defaults.get("S3_REGION", "ru-1")`
3. **Compose-consumer:** `${S3_REGION:-ru-1}` — compose engine сам резолвит default; значение `ru-1` должно совпадать с SoT
4. **Shell-consumer:** `${S3_REGION:-ru-1}` — shell резолвит; значение должно совпадать с SoT
5. **CI gate:** `make check-env-defaults` → `sync_env_defaults.py --check` → сравнивает `.env.example` с generated → exit 2 при расхождении

---

## Полный список мест дрейфа (файл:строка:текущее→правильное)

### S3_REGION (SoT: `ru-1`)

| # | Файл | Строка | Текущее | Правильное | Статус |
|---|------|--------|---------|------------|--------|
| R1 | `core/modules/backup-cron/scripts/backup_config.py` | 65 | `_DEFAULT_S3_REGION = "us-east-1"` | `ru-1` | 🔴 DRIFT |
| R2 | `core/internal/bootstrap/s3_ssl_cache.py` | 53 | `DEFAULT_S3_REGION = "us-east-1"` | `ru-1` | 🔴 DRIFT |
| R3 | `core/modules/backup-cron/docker-compose.base.yml` | 70 | `${S3_REGION:-ru-1}` | — | ✅ CORRECT |
| R4 | `core/modules/langfuse/docker-compose.base.yml` | 89 | `${S3_REGION:-ru-1}` | — | ✅ CORRECT |
| R5 | `core/modules/backup-cron/scripts/upload-s3.sh` | 66 | `${S3_REGION:-ru-1}` | — | ✅ CORRECT |
| R6 | `core/internal/scripts/sync_env_defaults.py` | 274 | `get_val("S3_REGION", "ru-1")` | — | ✅ CORRECT |

### S3_PREFIX (SoT: `platform/backups`)

| # | Файл | Строка | Текущее | Правильное | Статус |
|---|------|--------|---------|------------|--------|
| P1 | `core/internal/bootstrap/s3_ssl_cache.py` | 51 | `DEFAULT_S3_PREFIX = "platform/ssl-certs"` | N/A — см. анализ | 🟡 NAMING |
| P2 | `core/modules/backup-cron/scripts/backup_config.py` | 66 | `_DEFAULT_S3_PREFIX = "platform/backups"` | — | ✅ CORRECT |
| P3 | `core/modules/backup-cron/docker-compose.base.yml` | 71 | `${S3_PREFIX:-platform/backups}` | — | ✅ CORRECT |
| P4 | `core/modules/backup-cron/scripts/upload-s3.sh` | 67 | `${S3_PREFIX:-platform/backups}` | — | ✅ CORRECT |
| P5 | `core/internal/scripts/sync_env_defaults.py` | 275 | `get_val("S3_PREFIX", "platform/backups")` | — | ✅ CORRECT |

**Анализ P1:** `DEFAULT_S3_PREFIX = "platform/ssl-certs"` в s3_ssl_cache.py — это НЕ дрейф значения S3_PREFIX. Это отдельная константа для SSL-сертификатов, которая используется как параметр по умолчанию в функциях `upload_cert()`, `download_cert()` и др. Она относится к домену SSL-кеширования (путь в S3: `s3://bucket/platform/ssl-certs/<domain>/fullchain.pem`), а не к домену бэкапов (путь: `s3://bucket/platform/backups/...`). **Решение:** переименовать `DEFAULT_S3_PREFIX` → `DEFAULT_SSL_CACHE_PREFIX` во избежание путаницы с SoT `S3_PREFIX`.

### S3_BUCKET (SoT: `test-bucket`)

| # | Файл | Строка | Текущее | Правильное | Статус |
|---|------|--------|---------|------------|--------|
| B1 | `core/modules/backup-cron/scripts/backup_config.py` | 98 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B2 | `core/modules/backup-cron/scripts/backup_config.py` | 172 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B3 | `core/internal/bootstrap/s3_ssl_cache.py` | 233 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B4 | `core/internal/bootstrap/s3_ssl_cache.py` | 274 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B5 | `core/internal/bootstrap/s3_ssl_cache.py` | 383 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B6 | `core/internal/bootstrap/s3_ssl_cache.py` | 500 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B7 | `core/internal/bootstrap/s3_ssl_cache.py` | 625 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B8 | `core/internal/bootstrap/s3_ssl_cache.py` | 679 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B9 | `core/internal/bootstrap/s3_ssl_cache.py` | 793 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B10 | `core/internal/bootstrap/cert_orchestrator.py` | 346 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B11 | `core/internal/bootstrap/cert_orchestrator.py` | 404 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B12 | `core/internal/bootstrap/preflight.py` | 425 | `os.environ.get("S3_BUCKET", "")` | `""` — sentinel, OK | 🟡 SENTINEL |
| B13 | `core/modules/minio/docker-compose.base.yml` | 86 | `${S3_BUCKET:-platform-backups}` | `${S3_BUCKET:-test-bucket}` | 🔴 DRIFT |
| B14 | `core/modules/langfuse/docker-compose.base.yml` | 85 | `${LANGFUSE_S3_BUCKET:-${S3_BUCKET:-local-dev}}` | `${LANGFUSE_S3_BUCKET:-${S3_BUCKET:-test-bucket}}` | 🔴 DRIFT |
| B15 | `core/modules/backup-cron/docker-compose.base.yml` | 69 | `${S3_BUCKET}` (no default) | — | ✅ CORRECT |

**Анализ B1-B12:** Python-код использует `""` как sentinel «S3 не сконфигурирован». Это ПРАВИЛЬНОЕ поведение для production: если S3_BUCKET не задан, операции S3 должны деградировать gracefully (пропустить upload/download), а не пытаться писать в `test-bucket`. Значение `test-bucket` из SoT — это CI/test default, а не production fallback. **Решение:** оставить `""` в Python-коде, но заменить хардкоженные `os.environ.get("S3_BUCKET", "")` на вызов `platform_config.default_s3_bucket_sentinel()` который документирует семантику sentinel.

**Анализ B13-B14:** Compose-файлы используют `platform-backups` и `local-dev` как значения по умолчанию. Это dev-convenience: minio создаёт бакет с этим именем при старте. **Решение:** выровнять с SoT (`test-bucket`) или явно задокументировать, что minio dev-значения отличаются от production SoT.

### CONTEXT (SoT: `test`)

| # | Файл | Строка | Текущее | Правильное | Статус |
|---|------|--------|---------|------------|--------|
| C1 | `core/internal/bootstrap/deploy/docker_orchestrator.py` | 390 | `os.environ.get('CONTEXT', 'personal')` | `test` | 🔴 DRIFT |
| C2 | `core/modules/hermes-agent/watchdog/agent_watchdog.py` | 441 | `os.environ.get("CONTEXT", "unknown")` | `test` | 🔴 DRIFT |
| C3 | `core/modules/hermes-agent/watchdog/agent_watchdog.py` | 939 | `os.environ.get("CONTEXT", "unknown")` | `test` | 🔴 DRIFT |
| C4 | `core/internal/bootstrap/deploy/context_deployer.py` | 707 | `os.environ.get("CONTEXT", "")` | `""` — валидационный sentinel, OK | 🟡 SENTINEL |
| C5 | `core/internal/bootstrap/deploy/context_deployer.py` | 853 | `os.environ.get("CONTEXT", "")` | `""` — валидационный sentinel, OK | 🟡 SENTINEL |
| C6 | `core/modules/hermes-agent/docker-compose.base.yml` | 90 | `CONTEXT: ${CONTEXT:-personal}` | `test` | 🔴 DRIFT |
| C7 | `core/modules/hermes-agent/docker-compose.base.yml` | 154 | `CONTEXT: "${CONTEXT:-personal}"` | `test` | 🔴 DRIFT |
| C8 | `core/modules/backup-cron/scripts/backup_config.py` | 67 | `_DEFAULT_CONTEXT = "personal"` | N/A — это PLATFORM_CONTEXT, не CONTEXT | 🟡 NAMING |
| C9 | `core/modules/backup-cron/docker-compose.base.yml` | 72 | `${PLATFORM_CONTEXT:-personal}` | — (SoT PLATFORM_CONTEXT = personal) | ✅ CORRECT |
| C10 | `core/internal/scripts/sync_env_defaults.py` | 282 | `get_val("PLATFORM_CONTEXT", "personal")` | — | ✅ CORRECT |

**Анализ C8:** `_DEFAULT_CONTEXT` в backup_config.py — это default для PLATFORM_CONTEXT (значение `personal` совпадает с SoT). Но имя переменной вводит в заблуждение — выглядит как default для CONTEXT. **Решение:** переименовать `_DEFAULT_CONTEXT` → `_DEFAULT_PLATFORM_CONTEXT`.

**Анализ C4-C5:** context_deployer.py использует `""` как sentinel «CONTEXT не задан — требуется явное указание». Это валидационный паттерн, не дрейф.

**Анализ C1-C3, C6-C7:** Реальный дрейф — CONTEXT default не совпадает с SoT (`personal`/`unknown` vs `test`).

### Дополнительно: PLATFORM_CONTEXT (SoT: `personal`) — без дрейфа

| # | Файл | Строка | Текущее | Статус |
|---|------|--------|---------|--------|
| PC1 | `core/modules/backup-cron/scripts/backup_config.py` | 67 | `_DEFAULT_CONTEXT = "personal"` | ✅ CORRECT value, 🟡 NAMING |
| PC2 | `core/modules/backup-cron/docker-compose.base.yml` | 72 | `${PLATFORM_CONTEXT:-personal}` | ✅ CORRECT |
| PC3 | `core/modules/backup-cron/scripts/upload-s3.sh` | 68 | `${PLATFORM_CONTEXT:-personal}` | ✅ CORRECT |
| PC4 | `core/internal/scripts/sync_env_defaults.py` | 282 | `get_val("PLATFORM_CONTEXT", "personal")` | ✅ CORRECT |

---

## $TASKS

### TASK-1: Создать Python-фасад `platform_config.py`
- **Owner:** Coder
- **Output:** `core/internal/config/__init__.py` + `core/internal/config/platform_config.py` + `tests/unit/test_platform_config.py`
- **Acceptance:** 
  - Модуль загружает `platform-env.yaml` при импорте
  - Предоставляет типизированные accessors: `default_s3_region()`, `default_s3_prefix()`, `default_s3_bucket_sentinel()`, `default_context()`, `default_platform_context()`
  - `default_s3_bucket_sentinel()` возвращает `""` с документированной семантикой sentinel
  - Fallback-значения (при отсутствии platform-env.yaml) совпадают с platform-infra.yaml
  - Модуль имеет GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT
  - Unit-тест проверяет: загрузку из YAML, fallback-значения, все accessors
  - Импорт `from core.internal.config.platform_config import default_s3_region` работает в контексте bootstrap (корень проекта в PYTHONPATH)
- **Dependencies:** None
- **Complexity:** 4

### TASK-2: Переименовать константы в `s3_ssl_cache.py`
- **Owner:** Coder
- **Output:** Изменённый `s3_ssl_cache.py`
- **Acceptance:**
  - `s3_ssl_cache.py`: `DEFAULT_S3_PREFIX` → `DEFAULT_SSL_CACHE_PREFIX`, `DEFAULT_S3_REGION` → замена на импорт из `platform_config.default_s3_region()`
  - Сигнатуры функций обновлены: `upload_cert()` (L367), `download_cert()` (L486), `check_cert()` (L616), `bulk_restore()` (L670) — параметр `s3_cache_prefix: str = DEFAULT_S3_PREFIX` → `s3_cache_prefix: str = DEFAULT_SSL_CACHE_PREFIX`
  - Все тесты, использующие эти константы, проходят (прямые импорты обновлены)
- **Dependencies:** TASK-1
- **Complexity:** 3

### TASK-3: Исправить S3_* и CONTEXT/PLATFORM_CONTEXT default'ы в backup_config.py, cert_orchestrator.py, preflight.py
- **Owner:** Coder
- **Output:** Изменённые `backup_config.py`, `cert_orchestrator.py`, `preflight.py`
- **Acceptance:**
  - `backup_config.py`: `_DEFAULT_CONTEXT` → `_DEFAULT_PLATFORM_CONTEXT`, `_DEFAULT_S3_REGION` → замена на импорт из `platform_config`, S3_BUCKET sentinel из фасада
  - `cert_orchestrator.py`: заменить `os.environ.get("S3_BUCKET", "")` на `platform_config.default_s3_bucket_sentinel()`
  - `preflight.py`: аналогично
  - Тесты `tests/test_backup_config.py` проходят после изменений (константы переименованы, импорты обновлены)
  - Все существующие тесты проходят (0 регрессий)
- **Dependencies:** TASK-1
- **Complexity:** 2

### TASK-4: Исправить CONTEXT default'ы в Python-consumers
- **Owner:** Coder
- **Output:** Изменённые `docker_orchestrator.py`, `agent_watchdog.py`, `context_deployer.py`
- **Acceptance:**
  - `docker_orchestrator.py:390`: `"personal"` → `platform_config.default_context()`
  - `agent_watchdog.py:441,939`: `"unknown"` → `platform_config.default_context()`
  - `context_deployer.py:707,853`: `""` → `platform_config.default_context_sentinel()` (sentinel-семантика сохранена)
  - Все тесты проходят
- **Dependencies:** TASK-1
- **Complexity:** 2

### TASK-5: Выровнять compose-файлы с SoT
- **Owner:** Coder
- **Output:** Изменённые `hermes-agent/docker-compose.base.yml`, `minio/docker-compose.base.yml`, `langfuse/docker-compose.base.yml`
- **Acceptance:**
  - `hermes-agent/docker-compose.base.yml:90,154`: `personal` → `test`
  - `minio/docker-compose.base.yml:86`: `platform-backups` → `test-bucket`
  - `langfuse/docker-compose.base.yml:85`: `local-dev` → `test-bucket`
  - `docker compose config` (с COMPOSE_PROFILES) не выдаёт ошибок
- **Dependencies:** None
- **Complexity:** 2

### TASK-6: Обновить `sync_env_defaults.py` для верификации консистентности
- **Owner:** Coder
- **Output:** Изменённый `sync_env_defaults.py` (опционально)
- **Acceptance:**
  - `make sync-env-defaults` генерирует `.env.example` с правильными default'ами (без изменений, если SoT не менялся)
  - `sync_env_defaults.py` дополнен строкой `lines.append("CONTEXT=" + get_val("CONTEXT", "test"))` — CONTEXT теперь в `.env.example`
  - `make check-env-defaults` проходит (exit 0)
  - При несовпадении `.env.example` с generated — exit 2 с diff
- **Dependencies:** TASK-1..TASK-5
- **Complexity:** 1

### TASK-7: Прогнать полный тестовый набор и CI gate
- **Owner:** Coder
- **Output:** Результаты `make test && make gate MODE=fast`
- **Acceptance:**
  - `make test` — все тесты зелёные (0 failures)
  - `make gate MODE=fast` — зелёный (0 violations)
  - `make check-env-defaults` — exit 0
  - `make check-manifests` — exit 0
- **Dependencies:** TASK-1..TASK-6
- **Complexity:** 1

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- **Tasks:** TASK-1
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1`

### Wave 2 (depend on TASK-1, no shared files among themselves)
- **Tasks:** TASK-2, TASK-3, TASK-4, TASK-5
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4, TASK-5`

### Wave 3 (depends on Wave 2)
- **Tasks:** TASK-6, TASK-7
- **Command:** `coder Read DevPlan.md, implement Wave 3: TASK-6, TASK-7`

---

## Acceptance Criteria Summary

| AC | Критерий | Верификация |
|----|----------|-------------|
| AC1 | Единый SoT для всех 4 переменных | platform-infra.yaml `env_defaults` — единственное место определения |
| AC2 | Python-фасад предоставляет типизированный доступ | `platform_config.default_s3_region()` возвращает `"ru-1"` |
| AC3 | Все Python-consumers используют фасад | `grep "_DEFAULT_S3_REGION\|DEFAULT_S3_REGION\|DEFAULT_S3_PREFIX" core/internal/ core/modules/` → 0 хардкоженных констант (кроме переименованной DEFAULT_SSL_CACHE_PREFIX) |
| AC4 | Compose-файлы выровнены с SoT | `grep "CONTEXT:-personal"` → 0 совпадений; `grep "S3_BUCKET:-platform-backups"` → 0 совпадений; `grep "S3_BUCKET:-local-dev"` → 0 совпадений |
| AC5 | CI gate проходит | `make check-env-defaults && make check-manifests` → exit 0 |
| AC6 | Нет регрессий | `make test` → все тесты зелёные |

---

## File Manifest

| Файл | Действие | TASK |
|------|----------|------|
| `core/internal/config/__init__.py` | **CREATE** (пустой) | TASK-1 |
| `core/internal/config/platform_config.py` | **CREATE** | TASK-1 |
| `tests/unit/test_platform_config.py` | **CREATE** | TASK-1 |
| `core/internal/bootstrap/s3_ssl_cache.py` | MODIFY (rename DEFAULT_S3_PREFIX→DEFAULT_SSL_CACHE_PREFIX, import DEFAULT_S3_REGION from facade) | TASK-2 |
| `core/modules/backup-cron/scripts/backup_config.py` | MODIFY (rename _DEFAULT_CONTEXT→_DEFAULT_PLATFORM_CONTEXT, import S3_REGION from facade, S3_BUCKET sentinel) | TASK-3 |
| `core/internal/bootstrap/cert_orchestrator.py` | MODIFY (S3_BUCKET sentinel from facade) | TASK-3 |
| `core/internal/bootstrap/preflight.py` | MODIFY (S3_BUCKET sentinel from facade) | TASK-3 |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | MODIFY (CONTEXT default from facade) | TASK-4 |
| `core/modules/hermes-agent/watchdog/agent_watchdog.py` | MODIFY (CONTEXT default from facade) | TASK-4 |
| `core/internal/bootstrap/deploy/context_deployer.py` | MODIFY (CONTEXT sentinel from facade) | TASK-4 |
| `core/modules/hermes-agent/docker-compose.base.yml` | MODIFY (CONTEXT:-personal → CONTEXT:-test) | TASK-5 |
| `core/modules/minio/docker-compose.base.yml` | MODIFY (S3_BUCKET:-platform-backups → S3_BUCKET:-test-bucket) | TASK-5 |
| `core/modules/langfuse/docker-compose.base.yml` | MODIFY (S3_BUCKET:-local-dev → S3_BUCKET:-test-bucket) | TASK-5 |
| `core/internal/scripts/sync_env_defaults.py` | MODIFY (добавить CONTEXT в генерацию .env.example) | TASK-6 |
| `core/platform-infra.yaml` | NO CHANGE (уже правильные значения) | — |
| `platform-env.yaml` | NO CHANGE (генерируется) | — |

---

## Design Decisions

### ## @rationale (D1): `""` как sentinel для S3_BUCKET в Python-коде
**Q:** Почему Python-код не использует `"test-bucket"` из SoT как default для S3_BUCKET?
**A:** `""` — это sentinel «S3 не сконфигурирован». В production S3_BUCKET всегда задаётся через secrets. Использование `"test-bucket"` как fallback создаст риск: если secrets не загружены, система начнёт писать в несуществующий test-bucket вместо graceful degradation. Паттерн: `if not bucket: logger.warning("S3 not configured"); return False`. SoT-значение `test-bucket` предназначено для CI/test окружений, где platform-env.yaml предоставляет все переменные.

### ## @rationale (D2): Переименование `DEFAULT_S3_PREFIX` → `DEFAULT_SSL_CACHE_PREFIX`
**Q:** Почему не привести `DEFAULT_S3_PREFIX = "platform/ssl-certs"` к SoT `"platform/backups"`?
**A:** Это разные домены. `S3_PREFIX` (SoT) = префикс для бэкапов (`s3://bucket/platform/backups/...`). `DEFAULT_SSL_CACHE_PREFIX` = префикс для SSL-сертификатов (`s3://bucket/platform/ssl-certs/...`). Они не должны совпадать — сертификаты и бэкапы лежат в разных S3-путях. Переименование устраняет путаницу, не меняя поведение.

### ## @rationale (D3): `platform-env.yaml` остаётся generated, `platform-infra.yaml` — SoT
**Q:** Почему не консолидировать в один файл (Option A)?
**A:** Существующая архитектура generation (platform-infra.yaml → platform-env.yaml) обслуживает два разных набора consumers: platform-env.yaml читается provision, CI, тестами; platform-infra.yaml содержит статическую инфраструктуру. Консолидация сломает инвариант 11 (Manifest Generation Contract) и создаст риск регрессии для CI/CD пайплайнов. Python-фасад (Option B) добавляет слой абстракции без изменения generation pipeline.

### ## @rationale (D4): Compose S3_BUCKET default меняется на `test-bucket`
**Q:** Почему `platform-backups` и `local-dev` заменяются на `test-bucket`?
**A:** `test-bucket` — каноническое значение из SoT. `platform-backups` в minio — это dev-convenience (имя бакета, которое создаёт minio при старте). `local-dev` в langfuse — ещё одно dev-значение. После унификации все compose-файлы ссылаются на одно каноническое значение. Если для minio нужно другое имя бакета — оно должно быть явно задано в `.env`, а не хардкожено в compose.

### ## @rationale (D5): CONTEXT добавлен в sync_env_defaults.py
**Q:** Почему раньше CONTEXT не было в .env.example?
**A:** Исторически CONTEXT считался strictly per-node параметром, но платформа использует CONTEXT и как глобальный default ("test"). Отсутствие CONTEXT в .env.example означает, что gate `make check-env-defaults` не ловит дрейф CONTEXT. Добавление CONTEXT в генерацию закрывает этот gap и согласуется с KSC4 (нулевой дрейф).

### ## @rationale (D6): Паттерн миграции os.environ.get → facade
**Q:** Как именно заменять хардкоженные константы на вызовы фасада?
**A:** Стандартный паттерн: `os.environ.get("VAR", HARDCODED_CONSTANT)` → `os.environ.get("VAR", platform_config.default_var())`. Это сохраняет семантику «env var переопределяет default» и делает default централизованным. Исключение — sentinel-значения (`""`), которые заменяются на `platform_config.default_*_sentinel()` и сохраняют валидационную семантику.

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_platform_config.py` | `test_load_from_yaml` | Загрузка default'ов из platform-env.yaml | `platform_config.py` |
| `tests/unit/test_platform_config.py` | `test_fallback_values` | Fallback-значения при отсутствии YAML | `platform_config.py` |
| `tests/unit/test_platform_config.py` | `test_default_s3_region` | `default_s3_region()` → `"ru-1"` | `platform_config.py` |
| `tests/unit/test_platform_config.py` | `test_default_s3_prefix` | `default_s3_prefix()` → `"platform/backups"` | `platform_config.py` |
| `tests/unit/test_platform_config.py` | `test_default_s3_bucket_sentinel` | `default_s3_bucket_sentinel()` → `""` | `platform_config.py` |
| `tests/unit/test_platform_config.py` | `test_default_context` | `default_context()` → `"test"` | `platform_config.py` |
| `tests/unit/test_platform_config.py` | `test_default_platform_context` | `default_platform_context()` → `"personal"` | `platform_config.py` |
| `tests/unit/test_s3_ssl_cache.py` | `test_ssl_cache_prefix_constant` | `DEFAULT_SSL_CACHE_PREFIX == "platform/ssl-certs"` | `s3_ssl_cache.py` |
| `tests/test_backup_config.py` | Existing tests | Регрессия: все тесты проходят после переименования констант | `backup_config.py` |
| `tests/gates/test_gate_*.py` | All gate tests | `make gate MODE=fast` — 0 violations | gates |

---

## Risk Register

| Риск | Вероятность | Влияние | Mitigation |
|------|-------------|---------|------------|
| Переименование `DEFAULT_S3_PREFIX` → `DEFAULT_SSL_CACHE_PREFIX` ломает внешних consumers | LOW | MEDIUM | grep по всем файлам проекта перед переименованием; `DEFAULT_S3_PREFIX` используется только внутри s3_ssl_cache.py |
| Изменение CONTEXT default с `personal` на `test` ломает локальный docker compose up | MEDIUM | LOW | Локальный `.env` уже переопределяет CONTEXT; тест `make up MODULES=hermes-agent` после изменений |
| Изменение S3_BUCKET default в minio ломает создание бакета | LOW | MEDIUM | Minio entrypoint создаёт бакет из `$${S3_BUCKET}` — значение подставляется из env; в CI platform-env.yaml задаёт `S3_BUCKET=test-bucket` |
| Platform-infra.yaml и platform-env.yaml расходятся после изменений | LOW | HIGH | `make generate-manifests && make check-manifests` после всех правок |

---

## Next Steps

### Wave 1
Use coder role and read `file:///Users/tronyx/projects/ai-platform/.ai/plans/037-config-defaults-unification/01-DevPlan.md`, implement Wave 1: TASK-1

### Wave 2
Use coder role and read `file:///Users/tronyx/projects/ai-platform/.ai/plans/037-config-defaults-unification/01-DevPlan.md`, implement Wave 2: TASK-2, TASK-3, TASK-4, TASK-5

### Wave 3
Use coder role and read `file:///Users/tronyx/projects/ai-platform/.ai/plans/037-config-defaults-unification/01-DevPlan.md`, implement Wave 3: TASK-6, TASK-7

$END_DEVPLAN
