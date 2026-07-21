# Brief — Password Charset Constraint & Unified Auth Credential Propagation

<!-- $ARTIFACT_CONTRACT
  PURPOSE:       Fix PgBouncer/Langfuse/LiteLLM crash-loop from special chars in POSTGRES_PASSWORD and hermes-agent unhealthy from missing dashboard auth.
                 Strategy: charset constraint on ALL password variables (Option A) + explicit credential assignment from PLATFORM_MASTER_PASSWORD (no compose fallbacks).
  DESCRIPTION:   Two bugs from STRESS_TEST_REPORT (2026-07-20 tronyx-vps):
                  1. POSTGRES_PASSWORD=SkyNet!!%) — спецсимволы ломают shell-based URL-парсинг в pgbouncer entrypoint → auth_user=postgres: → cascade crash-loop
                     Fix: charset constraint ^[A-Za-z0-9._-]+$ на все пароли, встраиваемые в URL. Унифицировано с существующим CLICKHOUSE_PASSWORD constraint.
                  2. Hermes-agent unhealthy — HERMES_DASHBOARD_PASSWORD="" (empty) → dashboard auth failure → restart-loop (99.73% CPU)
                     Fix: явное присвоение HERMES_DASHBOARD_PASSWORD = PLATFORM_MASTER_PASSWORD в secrets.env. TRAP-аннотация о политике unified auth.
  RATIONALE:     Блокирует production bootstrap. Charset constraint — preventive: предотвращает повторение бага на любых переменных.
  ACCEPTANCE_CRITERIA:
    - POSTGRES_PASSWORD со спецсимволами отвергается на этапе validation/deploy (не доходит до pgbouncer)
    - Hermes-agent healthcheck проходит (healthy) — HERMES_DASHBOARD_PASSWORD = PLATFORM_MASTER_PASSWORD
    - Все пароли в secrets.env соответствуют ^[A-Za-z0-9._-]+$
    - Gate MODE=fast зелёный
    - Существующие тесты не сломаны, негативные тесты для charset violation добавлены
  IMPLEMENTS:
    - core/secrets-manifest.yaml — поле charset для всех password-секретов
    - core/internal/bootstrap/deploy-modules.sh — валидация charset в _check_env_requires()
    - core/internal/secrets/decrypt-secrets.sh — валидация после расшифровки SOPS
    - core/internal/scaffold/gen-env-platform.sh — генерация паролей через openssl rand -hex 32
    - core/modules/hermes-agent/docker-compose.base.yml — прямой env (без compose fallback)
    - core/internal/bootstrap/secrets-init.sh — инициализация сервис-паролей из PLATFORM_MASTER_PASSWORD + TRAP
    - tests/gates/test_gate_password_charset.py — gate-тест charset validation
    - tests/test_pgbouncer_static.py — обновлён: проверка charset constraint вместо URL-encode
    - .env.example — унифицированный CONSTRAINT-комментарий
  IMPACTS:
    - secrets-manifest.yaml — +1 поле (charset) на password-секреты
    - CI gate — новый тест test_gate_password_charset.py
    - .env.example — унифицированный CONSTRAINT для всех паролей
  REQUIRES:
    - НЕ требует Python urllib.parse (charset constraint реализуется через bash regex)
    - НЕ требует изменений в edoburu/pgbouncer образе
    - Доступ к tronyx-vps для тестирования
-->

<!-- GREP_SUMMARY: Brief, password-charset-constraint, hermes-dashboard-auth, STRESS_TEST_REPORT, pgbouncer-crashloop, unified-auth, explicit-assignment, TRAP, secrets-init -->
<!-- STRUCTURE: ┌Tx1: charset constraint all passwords┐ → ┌Tx2: Hermes explicit auth init + TRAP┐ → ┌Tx3: gate/tests┐ → ┌rollout plan┐ -->
<!--
  ⚠️ TRAP[DECISION] · 2026-07-21 · HI · Collapse: Option A (charset constraint) + Explicit Assignment (no compose fallbacks)
  · Collapsed by user:
    Часть 1: Option A — charset constraint ^[A-Za-z0-9._-]+$ на все пароли (не только POSTGRES_PASSWORD)
    Часть 2: HERMES_DASHBOARD_PASSWORD = PLATFORM_MASTER_PASSWORD явно в secrets.env. TRAP о unified auth при генерации контекста.
  · Rejected: Option B (URL-encode pipeline) — больше кода, сложнее аудит. Charset constraint проще и надёжнее.
  · Rejected: Compose fallback ${HERMES_DASHBOARD_PASSWORD:-${PLATFORM_MASTER_PASSWORD}} — скрытая логика, размазанная по compose-файлам.
  · Rationale: Explicit beats implicit. Один файл (secrets.env) = одна точка управления кредами.
-->

# Brief — Password Charset Constraint & Unified Auth Credential Propagation

**Wave:** 014
**Status:** Draft
**Created:** 2026-07-21
**Updated:** 2026-07-21 (Option A collapse + explicit assignment decision)
**Source:** STRESS_TEST_REPORT.md (2026-07-20, tronyx-vps)

---

## Проблема 1: PgBouncer/Langfuse/LiteLLM crash-loop

### Symptoms
```
pgbouncer   → auth_user=postgres: (trailing colon — пароль не извлечён из DATABASE_URLS)
langfuse    → restarting (cannot connect through pgbouncer)
litellm     → health: starting → restarting (cannot connect through pgbouncer)
```

### Root Cause
`POSTGRES_PASSWORD=SkyNet!!%)` содержит спецсимволы, которые ломают shell-based URL-парсинг в entrypoint'е edoburu/pgbouncer. Entrypoint разбирает `DATABASE_URLS` (через `cut`/`awk`/shell parameter expansion) и генерирует `userlist.txt` с `auth_user=postgres:` — пароль не извлечён из-за спецсимволов `!`, `%`, `)`.

### Почему ломается только PgBouncer
- **postgres container** (initdb): получает `POSTGRES_PASSWORD` как прямой env — не ломается
- **backup-cron**: использует `PGPASSWORD` как shell env — не ломается
- **langfuse/litellm/infra-metrics**: их драйверы БД (Prisma, SQLAlchemy, Go pg_exporter) корректно парсят URL с любыми символами — **но они не могут подключиться, потому что pgbouncer сломан**
- **pgbouncer entrypoint**: единственный, кто делает наивный shell-парсинг URL — **ЛОМАЕТСЯ**

### Решение: Charset Constraint (Option A)

**Правило:** все пароли, встраиваемые в URL (connection strings, S3 URLs), должны соответствовать `^[A-Za-z0-9._-]+$`.

**Почему не URL-encoding (Option B):**
- URL-encoding требует Python-зависимости и разделения переменных (raw vs encoded)
- Charset constraint проще: одна regex-проверка на входе, никаких преобразований
- `openssl rand -hex 32` генерирует 256-битный пароль без единого спецсимвола — энтропия не страдает
- Прецедент уже существует: `CLICKHOUSE_PASSWORD` constraint задокументирован в `.env.example:55-57`

### Scope: все пароли, не только POSTGRES_PASSWORD

| Переменная | URL-контекст | Файл |
|---|---|---|
| `POSTGRES_PASSWORD` | `DATABASE_URLS` (pgbouncer), `DATABASE_URL` (langfuse, litellm), `DATA_SOURCE_NAME` (infra-metrics) | 4 compose-файла |
| `CLICKHOUSE_PASSWORD` | `CLICKHOUSE_MIGRATION_URL` (langfuse) | 1 compose-файл |
| `MINIO_ROOT_USER` | S3 endpoint URL `user@host` (langfuse) | 1 compose-файл |
| `MINIO_ROOT_PASSWORD` | S3 endpoint URL `user:pass@host` (langfuse) | 1 compose-файл |
| `S3_ACCESS_KEY` | S3 URL query/auth (langfuse) | 1 compose-файл |
| `S3_SECRET_KEY` | S3 URL query/auth (langfuse) | 1 compose-файл |

**Плюс превентивно** (могут стать URL в будущем):
| Переменная | Риск |
|---|---|
| `PLATFORM_MASTER_PASSWORD` | Basic Auth → может стать URL |
| `HERMES_DASHBOARD_PASSWORD` | Basic Auth → может стать URL |
| `GF_SECURITY_ADMIN_PASSWORD` | Grafana env → может стать URL |
| `LITELLM_MASTER_KEY` | HTTP header → низкий, но constraint бесплатен |
| `OPENAI_API_KEY` | HTTP header → низкий |

**Правило:** все секреты с `tier: required` или `tier: generated` в `secrets-manifest.yaml`, тип значения которых — пароль/ключ, получают `charset: "^[A-Za-z0-9._-]+$"`.

**Исключения (без constraint):**
- `AGE_SECRET_KEY` — age-ключ в формате `AGE-SECRET-KEY-...`, имеет свою структуру
- `GHCR_PULL_TOKEN` — GitHub PAT формата `github_pat_...`, constraint `^[A-Za-z0-9_]+$`
- `TELEGRAM_BOT_TOKEN` — формат `12345:ABC-DEF...`, constraint `^[0-9]+:[A-Za-z0-9_-]+$`
- `WEBNAMES_API_KEY` — формат провайдера, constraint неизвестен
- `CI_DEPLOY_KEY`, `VPS_SSH_KEY`, `SSH_KEY` — SSH private keys, многострочные PEM

---

## Проблема 2: Hermes-agent unhealthy

### Symptoms
```
hermes-agent → unhealthy → 99.73% CPU → 209.7MiB / 1GiB RAM → restart-loop
```

### Root Cause
`HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: "${HERMES_DASHBOARD_PASSWORD:-}"` — пустая строка по умолчанию. Hermes Dashboard требует non-empty пароль для Basic Auth. Без пароля dashboard падает → healthcheck получает 500/503 → unhealthy → Docker перезапускает → restart-loop.

### Решение: Explicit Assignment + TRAP (Option A суперпозиции)

**Политика unified auth:** при генерации нового контекста (`make new-context`) и при инициализации секретов (`make bootstrap-node` → `secrets.sh`) **все сервис-пароли явно инициализируются значением `PLATFORM_MASTER_PASSWORD`**. Никаких compose-fallback'ов — управление кредами ТОЛЬКО через secrets.env.

**Почему не compose fallback (Option B):**
- Fallback-логика размазана по compose-файлам — невозможно auditiровать через `grep secrets.env`
- «Забыть написать fallback» в новом модуле = silent misconfiguration
- Explicit assignment: один файл (`secrets.env`) = одна точка управления. Оператор видит все сервис-пароли.

**Как это работает:**

1. `secrets.env` (расшифрованный SOPS):
```bash
PLATFORM_MASTER_PASSWORD="supersecret"
HERMES_DASHBOARD_PASSWORD="supersecret"    # ← то же значение, явно присвоено
GF_SECURITY_ADMIN_PASSWORD="supersecret"   # ← то же значение, явно присвоено
# и т.д. для всех сервис-паролей
```

2. Compose — без fallback'ов:
```yaml
# core/modules/hermes-agent/docker-compose.base.yml
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: "${HERMES_DASHBOARD_PASSWORD}"
# НЕ "${HERMES_DASHBOARD_PASSWORD:-${PLATFORM_MASTER_PASSWORD:-}}" — без fallback!
```

3. TRAP-аннотация в `core/internal/bootstrap/secrets-init.sh` (новый файл) или `core/internal/secrets/decrypt-secrets.sh`:
```bash
# ⚠️ TRAP[POLICY] · 2026-07-21 · HI · Unified Auth: все сервис-пароли = PLATFORM_MASTER_PASSWORD
# · Правило: при создании нового контекста (make new-context) и инициализации секретов
# ·   все *_PASSWORD переменные в secrets.env инициализируются значением PLATFORM_MASTER_PASSWORD.
# ·   Оператор может переопределить конкретный сервис-пароль позже — явно в secrets.env.
# ·   Compose-файлы НЕ используют fallback-цепочки — строго ${VAR_NAME}.
# ·   Это гарантирует: (1) один файл управления creds, (2) аудит через grep secrets.env,
# ·   (3) явную ошибку если пароль не задан (а не silent fallback).
# · Rev: если количество сервис-паролей превысит 20 — рассмотреть авто-генерацию из шаблона.
```

---

## Tx1: Charset Constraint на все пароли

### Tx1.1: `core/secrets-manifest.yaml` — поле `charset`

Добавить `charset` поле для всех password-секретов:

```yaml
# Пример для POSTGRES_PASSWORD:
  - name: POSTGRES_PASSWORD
    tier: required
    consumers: [postgres, litellm, backup-cron, infra-metrics]
    source: sops
    charset: "^[A-Za-z0-9._-]+$"
    note: "openssl rand -hex 32. CONSTRAINT: только [A-Za-z0-9._-] — пароль встраивается в URL без encoding."

# Пример для CLICKHOUSE_PASSWORD:
  - name: CLICKHOUSE_PASSWORD
    tier: required
    consumers: [clickhouse, langfuse]
    source: sops
    charset: "^[A-Za-z0-9._-]+$"

# Исключение — AGE_SECRET_KEY (свой формат):
  - name: AGE_SECRET_KEY
    tier: required
    consumers: [platform-secrets]
    source: sops
    charset: "^AGE-SECRET-KEY-[A-Za-z0-9]+$"
```

### Tx1.2: `deploy-modules.sh` — `_check_env_requires()` валидация charset

Добавить шаг после загрузки secrets.env:

```bash
# region FUNC_validate_secret_charsets
_validate_secret_charsets() {
    local manifest="${PLATFORM_ROOT}/core/secrets-manifest.yaml"
    local failed=0

    # Читаем charset'ы из манифеста (python3+yaml или yq)
    # Для каждого секрета с charset: проверяем значение из env
    # Пример: POSTGRES_PASSWORD должен match ^[A-Za-z0-9._-]+$

    python3 -c "
import yaml, os, sys, re
with open('${manifest}') as f:
    data = yaml.safe_load(f)
failed = 0
for s in data.get('secrets', []):
    charset = s.get('charset', '')
    if not charset:
        continue
    name = s['name']
    val = os.environ.get(name, '')
    if not val:
        continue  # пропускаем незаданные (проверяются отдельно)
    if not re.match(charset, val):
        print(f'[IMP:9][charset] FAIL: {name} does not match charset {charset}', file=sys.stderr)
        failed += 1
    else:
        print(f'[IMP:8][charset] OK: {name} matches {charset}', file=sys.stderr)
sys.exit(failed)
"
    return $?
}
# endregion FUNC_validate_secret_charsets
```

### Tx1.3: `secrets.sh step_12b` — генерация паролей без спецсимволов

Все autogen-пароли (`LITELLM_MASTER_KEY`, `NEXTAUTH_SECRET`, `SALT`, `LANGFUSE_*`) уже генерируются через `openssl rand -hex`, что гарантирует charset `[0-9a-f]+` — изменений не требуется.

### Tx1.4: `.env.example` — унифицированный CONSTRAINT

```bash
# ── Postgres (shared-db) ───────────────────────────────────────────────────
POSTGRES_USER=postgres
# ⚠️ CONSTRAINT: POSTGRES_PASSWORD must match ^[A-Za-z0-9._-]+$
#   Пароль встраивается в DATABASE_URL/DATABASE_URLS без URL-encoding.
#   Генерация: openssl rand -hex 32
POSTGRES_PASSWORD=
POSTGRES_DB=platform

# ── ClickHouse (Analytical DB) ─────────────────────────────────────────────
# ⚠️ CONSTRAINT: CLICKHOUSE_PASSWORD must match ^[A-Za-z0-9._-]+$
#   Пароль встраивается в CLICKHOUSE_MIGRATION_URL без URL-encoding.
#   Генерация: openssl rand -hex 32
CLICKHOUSE_PASSWORD=

# ── MinIO (S3-compatible storage) ──────────────────────────────────────────
# ⚠️ CONSTRAINT: MINIO_ROOT_USER, MINIO_ROOT_PASSWORD must match ^[A-Za-z0-9._-]+$
#   Встраиваются в S3 endpoint URL без URL-encoding.
MINIO_ROOT_USER=
MINIO_ROOT_PASSWORD=

# ── Master credentials (unified auth) ──────────────────────────────────────
# ⚠️ CONSTRAINT: PLATFORM_MASTER_PASSWORD must match ^[A-Za-z0-9._-]+$
#   Используется как значение по умолчанию для всех сервис-паролей.
#   Все сервис-пароли (HERMES_DASHBOARD_PASSWORD, GF_SECURITY_ADMIN_PASSWORD, ...)
#   имеют тот же constraint.
PLATFORM_MASTER_PASSWORD=
```

### Tx1.5: `gen-env-platform.sh` — constraint-совместимые defaults

В `platform-env.yaml` — defaults уже charset-safe (`test-pg-pwd`, `test-clickhouse-pwd`). Дополнительных изменений не требуется.

---

## Tx2: Hermes Dashboard Auth — Explicit Assignment

### Tx2.1: `core/modules/hermes-agent/docker-compose.base.yml`

```yaml
# Было (строка ~108):
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: "${HERMES_DASHBOARD_PASSWORD:-}"

# Стало:
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: "${HERMES_DASHBOARD_PASSWORD}"
```

**Без fallback.** Если `HERMES_DASHBOARD_PASSWORD` не задан → docker compose up упадёт с ошибкой — явный сигнал оператору.

### Tx2.2: `core/internal/bootstrap/secrets-init.sh` — новый файл

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: secrets-init context generation master-password unified-auth service-passwords explicit-assignment
# STRUCTURE: ▶ detect context → read PLATFORM_MASTER_PASSWORD → init all service passwords → write SOPS → TRAP annotation
# region MODULE_CONTRACT
## @purpose  Инициализация сервис-паролей из PLATFORM_MASTER_PASSWORD при создании нового контекста
## @scope    Вызывается из make new-context и make bootstrap-node --mode init
## @invariants
##   - Все *_PASSWORD переменные инициализируются значением PLATFORM_MASTER_PASSWORD
##   - Если сервис-пароль уже задан — не перезаписывается (оператор мог изменить)
##   - TRAP[POLICY] аннотация документирует правило unified auth
## @rationale Явное присвоение всех паролей в одном файле (secrets.env) — одна точка управления;
##            compose-файлы не используют fallback-цепочек.
## @changes  2026-07-21 — Initial implementation (Wave 014)
# endregion MODULE_CONTRACT

set -euo pipefail

# ⚠️ TRAP[POLICY] · 2026-07-21 · HI · Unified Auth: все сервис-пароли = PLATFORM_MASTER_PASSWORD
# · Правило: при создании нового контекста все *_PASSWORD переменные
# ·   инициализируются значением PLATFORM_MASTER_PASSWORD.
# ·   Оператор может переопределить конкретный сервис-пароль позже — явно в secrets.env.
# ·   Compose-файлы НЕ используют fallback-цепочки — строго ${VAR_NAME}.
# · Rev: если количество сервис-паролей превысит 20 — рассмотреть авто-генерацию из шаблона.

# Читаем мастер-пароль
if [[ -z "${PLATFORM_MASTER_PASSWORD:-}" ]]; then
    echo "[IMP:9][secrets-init] FAIL: PLATFORM_MASTER_PASSWORD not set — cannot init service passwords" >&2
    exit 1
fi

# Список сервис-паролей, инициализируемых из PLATFORM_MASTER_PASSWORD
SERVICE_PASSWORDS=(
    "HERMES_DASHBOARD_PASSWORD"
    "GF_SECURITY_ADMIN_PASSWORD"
    "LANGFUSE_INIT_USER_PASSWORD"
    # Добавлять новые сервис-пароли сюда при расширении платформы
)

for pw_var in "${SERVICE_PASSWORDS[@]}"; do
    if [[ -z "${!pw_var:-}" ]]; then
        export "${pw_var}=${PLATFORM_MASTER_PASSWORD}"
        echo "[IMP:8][secrets-init] ${pw_var} ← PLATFORM_MASTER_PASSWORD (initialized)"
    else
        echo "[IMP:8][secrets-init] ${pw_var} already set — keeping operator-defined value"
    fi
done

echo "[IMP:9][secrets-init] Service passwords initialized from PLATFORM_MASTER_PASSWORD"
```

### Tx2.3: Интеграция в `make new-context` / `make bootstrap-node`

В `core/internal/scaffold/context-init.sh` и `core/internal/bootstrap/node-lifecycle.sh` добавить вызов `secrets-init.sh` после расшифровки SOPS, перед `docker compose up`.

### Tx2.4: `core/secrets-manifest.yaml` — обновить `note`

```yaml
  - name: HERMES_DASHBOARD_PASSWORD
    tier: required
    consumers: [hermes-agent]
    source: sops
    charset: "^[A-Za-z0-9._-]+$"
    note: "Password для Hermes dashboard. Инициализируется из PLATFORM_MASTER_PASSWORD при создании контекста. Может быть переопределён оператором."
```

---

## Tx3: Тесты и Gate

### Tx3.1: Gate-тест — charset validation всех секретов

```python
# tests/gates/test_gate_password_charset.py
"""Gate-тест: все секреты из secrets-manifest.yaml с полем charset проходят валидацию."""

import yaml
import re
import os
from pathlib import Path

PLATFORM_ROOT = Path(__file__).parent.parent.parent

def test_secrets_manifest_charset_defined_for_url_passwords():
    """Все пароли, встраиваемые в URL, должны иметь charset constraint."""
    manifest_path = PLATFORM_ROOT / "core" / "secrets-manifest.yaml"
    with open(manifest_path) as f:
        data = yaml.safe_load(f)

    # Пароли, которые точно в URL
    url_passwords = {
        "POSTGRES_PASSWORD",
        "CLICKHOUSE_PASSWORD",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
    }

    for s in data["secrets"]:
        if s["name"] in url_passwords:
            assert "charset" in s, \
                f"SECURITY: {s['name']} embedded in URL but has no charset field in secrets-manifest.yaml"
            assert s["charset"] == "^[A-Za-z0-9._-]+$", \
                f"SECURITY: {s['name']} charset mismatch: expected ^[A-Za-z0-9._-]+$, got {s.get('charset')}"


@pytest.mark.parametrize("special_password,should_fail", [
    ("SkyNet!!%)", True),          # оригинальный проблемный пароль
    ("pass#hash", True),           # # в пароле
    ("pwd with space", True),      # пробел
    ("pass/with/slash", True),     # слэш
    ("valid-pass_123.abc", False), # валидный пароль
    ("openssl_rand_hex_32", False),# типичный hex
    ("simple", False),             # только буквы
])
def test_password_charset_validation(special_password, should_fail):
    """Charset ^[A-Za-z0-9._-]+$ должен отвергать спецсимволы."""
    pattern = re.compile(r"^[A-Za-z0-9._-]+$")
    matches = bool(pattern.match(special_password))
    if should_fail:
        assert not matches, f"SECURITY BUG: '{special_password}' should be REJECTED by charset constraint"
    else:
        assert matches, f"BUG: '{special_password}' should be ACCEPTED by charset constraint"


def test_no_db_url_contains_raw_postgres_password_without_encoded():
    """DATABASE_URL/DATABASE_URLS в compose НЕ должны использовать
    raw ${POSTGRES_PASSWORD} без fallback на ENCODED — deprecated Option B,
    теперь пароль гарантированно charset-safe через constraint."""
    # Проверяем что нигде нет POSTGRES_PASSWORD_ENCODED (артефакт Option B)
    compose_files = [
        "core/modules/postgres/docker-compose.base.yml",
        "core/modules/langfuse/docker-compose.base.yml",
        "core/modules/litellm/docker-compose.base.yml",
        "core/modules/infra-metrics/docker-compose.base.yml",
    ]
    for f in compose_files:
        content = (PLATFORM_ROOT / f).read_text()
        assert "POSTGRES_PASSWORD_ENCODED" not in content, \
            f"CLEANUP: {f} references POSTGRES_PASSWORD_ENCODED (Option B artifact). Remove — charset constraint is sufficient."


def test_hermes_compose_has_no_fallback():
    """HERMES_DASHBOARD_BASIC_AUTH_PASSWORD не должен иметь compose fallback на PLATFORM_MASTER_PASSWORD."""
    compose = (PLATFORM_ROOT / "core/modules/hermes-agent/docker-compose.base.yml").read_text()
    # Проверяем ОТСУТСТВИЕ fallback-паттерна
    assert ":-${PLATFORM_MASTER_PASSWORD}" not in compose, \
        "POLICY VIOLATION: HERMES_DASHBOARD_BASIC_AUTH_PASSWORD has fallback to PLATFORM_MASTER_PASSWORD. Use explicit assignment in secrets.env."
    # Проверяем НАЛИЧИЕ прямого использования
    assert "HERMES_DASHBOARD_PASSWORD}" in compose, \
        "MISSING: HERMES_DASHBOARD_BASIC_AUTH_PASSWORD must reference HERMES_DASHBOARD_PASSWORD directly."
```

### Tx3.2: Обновить `test_pgbouncer_static.py`

```python
def test_pgbouncer_password_charset_constraint():
    """PgBouncer DATABASE_URLS использует пароль, который должен соответствовать charset constraint."""
    # Проверить что DATABASE_URLS содержит ${POSTGRES_PASSWORD}
    # (не ENCODED — теперь charset constraint гарантирует безопасность)
    compose = (PLATFORM_ROOT / "core/modules/postgres/docker-compose.base.yml").read_text()
    assert '${POSTGRES_PASSWORD}' in compose
    assert 'POSTGRES_PASSWORD_ENCODED' not in compose
```

---

## Rollout Plan

| Шаг | Операция | Риски | Откат |
|-----|----------|-------|-------|
| 1 | Tx1.1: secrets-manifest.yaml — charset поле | Нулевые — аддитивно | Удалить поле |
| 2 | Tx1.2: charset валидация в deploy-modules.sh | Может заблокировать деплой с существующим `SkyNet!!%)` | Заменить пароль на charset-safe до деплоя |
| 3 | Tx1.4: обновить .env.example комментарии | — | — |
| 4 | Tx2.2: secrets-init.sh + TRAP | — | — |
| 5 | Tx2.1: убрать fallback в hermes compose | Если `HERMES_DASHBOARD_PASSWORD` не задан — контейнер упадёт (by design) | Вернуть fallback временно |
| 6 | Tx3: gate-тесты | — | — |
| 7 | `make gate MODE=fast` локально | — | — |
| 8 | `make gate MODE=full` локально | Может упасть на существующих тестах, ожидающих fallback | Исправить тесты |
| 9 | Push → CI green | — | — |
| 10 | **Сгенерировать новый `POSTGRES_PASSWORD`** через `openssl rand -hex 32` для tronyx-vps | Потеря доступа к существующим БД — **нужно сменить пароль в postgres через ALTER USER** | Старый пароль (если без спецсимволов) продолжит работать |
| 11 | Явно задать `HERMES_DASHBOARD_PASSWORD` = `PLATFORM_MASTER_PASSWORD` в secrets.env на VPS | — | — |
| 12 | `make bootstrap-node NODE=tronyx-vps` | — | `docker compose down && docker compose up -d` с предыдущей версией |

---

## ⚠️ Критический шаг: смена POSTGRES_PASSWORD на сервере

Существующий `POSTGRES_PASSWORD=SkyNet!!%)` на tronyx-vps **не пройдёт** charset-валидацию. Недостаточно просто сменить переменную в secrets.env — PostgreSQL хранит пароль в `pg_authid` (initdb). Процедура:

```bash
# 1. Сгенерировать новый пароль
NEW_PASSWORD=$(openssl rand -hex 32)

# 2. Подключиться к postgres (с текущим паролем) и сменить
PGPASSWORD="SkyNet!!%)" psql -h 127.0.0.1 -U postgres -d platform \
  -c "ALTER USER postgres WITH PASSWORD '${NEW_PASSWORD}';"

# 3. Обновить secrets.env
# POSTGRES_PASSWORD=<новый пароль>

# 4. Перезапустить pgbouncer (перечитает userlist.txt)
docker compose restart pgbouncer
```

---

## Dependencies

- НЕ требует Python `urllib.parse`
- НЕ требует изменений в edoburu/pgbouncer образе
- НЕ требует миграции данных (pgbouncer stateless, данные в postgres)
- НЕ требует `POSTGRES_PASSWORD_ENCODED` переменной — charset constraint исключает необходимость encoding
- Требует `python3+yaml` (уже используется в gen-env-platform.sh и gate-тестах)

---

## Out of Scope

- PostgreSQL password rotation (TRAP[DEBT] в docker-compose.base.yml:50-56) — отдельная задача
- Hermes-agent `/auth/login` 422 без `?provider=basic` — уже исправлен nginx intercept
- Ротация паролей для других сервисов (ClickHouse, MinIO) — их пароли уже charset-safe
- Миграция существующих VPS-паролей на charset constraint — ручная операция, документирована выше
