$START_DEVPLAN
# DevPlan 099 — Миграция generate-dev-certs.sh → Python

$ARTIFACT_CONTRACT
PURPOSE:               Миграция `core/modules/nginx/generate-dev-certs.sh` (295 LOC) →
                       Python-модуль `dev_cert_generator.py` (~250 LOC) + тонкий shell-фасад
                       (~40 LOC) по Strangler-Fig паттерну. Без изменения поведения `make dev-certs`.
DESCRIPTION:           Классификация: SMALL (5 файлов, без архитектурных/API/схемных изменений).
                       Чистая миграция 1:1: 6 доменных функций (required_sans, get_cert_sans,
                       cert_is_current, generate_mkcert, generate_openssl, verify_san) + main()
                       переносятся в Python-модуль с сохранением LDD-логов [IMP:7-10], контракта
                       идемпотентности и exit-code семантики (0 = success/cert-current, 1 = failure).
                       Shell-фасад оставляет: env-var defaults, вызов python3, проброс exit code.
                       Makefile-таргет обновляет команду с `bash script.sh` на `python3 module.py`.
                       entrypoint-manifest.yaml обновляет mechanism: shell-script → python-script.
RATIONALE:             - Максимальный эффект на минимальном объёме (295→40 LOC, −86%)
                       - Полностью самодостаточный домен — нет зависимостей от lib/ (только
                         openssl/mkcert как external dependencies)
                       - Закрывает последний «толстый» скрипт в core/modules/
                       - Нулевой риск регрессии: behaviour-preserving миграция (AC3)
ACCEPTANCE_CRITERIA:   AC1: `core/modules/nginx/dev_cert_generator.py` — Python-модуль с 6
                            функциями + main(), GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT,
                            LDD [IMP:7-10] логи
                        AC2: `generate-dev-certs.sh` ≤ 50 LOC (env vars + вызов python3),
                            сохраняет GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT
                        AC3: `make dev-certs` — поведение идентично (те же exit codes, та же
                            идемпотентность, те же SAN)
                        AC4: Unit-тесты на cert_is_current (SAN match/mismatch, missing file,
                            expiry), verify_san (all present/missing), required_sans (default/context)
                        AC5: Интеграционный тест: generate → verify → idempotent no-op
                            (mocked subprocess, tmp_path)
                        AC6: Все существующие GREP_SUMMARY/STRUCTURE/TRAP сохранены
                            (скрипт, helpers.mk TRAP[BUG] PLATFORM_DOMAIN)
                        AC7: `make gate MODE=fast` зелёный — `make up` зависит от dev-certs,
                            gate не должен сломаться
IMPLEMENTS:            Brief 099 (`.ai/plans/099-generate-dev-certs-python/01-Brief.md`)
IMPACTS:
                       - `core/modules/nginx/dev_cert_generator.py` (NEW — ~250 LOC)
                       - `core/modules/nginx/generate-dev-certs.sh` (MODIFY — 295→~40 LOC)
                       - `makefiles/helpers.mk` (MODIFY — dev-certs target line 42)
                       - `core/entrypoint-manifest.yaml` (MODIFY — mechanism + delegates_to)
                       - `tests/unit/test_dev_cert_generator.py` (NEW — ~250 LOC)
REQUIRES:              Python ≥ 3.10, openssl (external), mkcert (external, optional).
                       Самодостаточный домен — нет зависимостей от lib/ или internal/.
$END_ARTIFACT_CONTRACT

---

## 1. Фактические поправки к брифу

| # | Утверждение брифа | Фактическое состояние | Вердикт |
|---|------------------|----------------------|---------|
| F1 | 295 LOC | Файл `generate-dev-certs.sh` — ровно 295 строк (строки 1-295) | ✅ Подтверждено |
| F2 | 6 функций + main() | 6 функций: `required_sans()`, `get_cert_sans()`, `cert_is_current()`, `generate_mkcert()`, `generate_openssl()`, `verify_san()` + `main()` | ✅ Подтверждено |
| F3 | Самодостаточный, без зависимостей от lib/ | Нет `source lib/*.sh`; только bash builtins + openssl + mkcert + mkdir | ✅ Подтверждено |
| F4 | `make dev-certs [CERT_BACKEND=...]` | Таргет в `makefiles/helpers.mk:38-43`; бриф не упоминает `helpers.mk` как файл для изменения | ⚠️ Поправка: helpers.mk добавлен в IMPACTS |
| F5 | `core/entrypoint-manifest.yaml` в IMPACTS | Запись `dev-certs` на строках 499-504: `mechanism: shell-script`, `delegates_to: core/modules/nginx/generate-dev-certs.sh` | ⚠️ Поправка: mechanism → `python-script` (не `python-module` — так в manifest vocabulary) |
| F6 | Shell-фасад ≤ 50 LOC | Достижимо: ~40 строк (env defaults + doc header + python3 call) | ✅ Подтверждено |
| F7 | Python-модуль в `core/modules/nginx/` | Cross-layer правила разрешают: modules/ может содержать Python (ограничение только на импорт из internal/ — модулю это не нужно) | ✅ Подтверждено |

---

## 2. Problem/Design Matrix

| # | Задача | Текущее состояние | Решение |
|---|--------|------------------|---------|
| P1 | 295 LOC shell-скрипт с бизнес-логикой | 6 доменных функций на bash | Миграция 1:1 в Python с сохранением сигнатур и LDD-логов |
| P2 | Shell-фасад должен оставаться тонким | Makefile вызывает `bash generate-dev-certs.sh` | Фасад ≤40 LOC: env defaults + `python3 dev_cert_generator.py` |
| P3 | entrypoint-manifest.yaml синхронизация | `mechanism: shell-script` → нужно `python-script` | Обновить mechanism + delegates_to (S1-раздел, не generated) |
| P4 | Unit-тесты для Python-модуля | Тестов нет; smoke-тест вызывает shell напрямую | 10+ unit-тестов + 1 интеграционный (mocked subprocess) |
| P5 | Сохранение GREP_SUMMARY/STRUCTURE/TRAP | Скрипт и helpers.mk содержат маркерные комментарии | Перенести в Python-модуль; сохранить в фасаде и helpers.mk |

---

## 3. Draft Code Graph

```xml
<code_graph>
  <entity id="dev_cert_generator_py" type="MODULE" keywords="dev-certs ssl mkcert openssl san idempotent cert-generator">
    <annotation>core/modules/nginx/dev_cert_generator.py — Python-модуль с 7 функциями</annotation>
    <functions>
      <call name="required_sans" returns="list[str]">
        <arg name="platform_domain" type="str"/>
        <annotation>Build sorted required SAN set: *.ai-platform.local + optional context wildcard + localhost + 127.0.0.1</annotation>
      </call>
      <call name="get_cert_sans" returns="list[str]">
        <arg name="cert_file" type="Path"/>
        <annotation>Extract literal SAN entries from PEM via subprocess.run(['openssl', 'x509', ...])</annotation>
      </call>
      <call name="cert_is_current" returns="bool">
        <arg name="cert_file" type="Path"/>
        <arg name="key_file" type="Path"/>
        <arg name="platform_domain" type="str"/>
        <annotation>Idempotency check: (cert+key exist) ∧ (required SANs ⊆ cert SANs) ∧ (not expiring in 30d)</annotation>
      </call>
      <call name="generate_mkcert" returns="Path">
        <arg name="dev_certs_dir" type="Path"/>
        <arg name="sans" type="list[str]"/>
        <annotation>Generate via subprocess.run(['mkcert', ...]); returns cert_file path</annotation>
      </call>
      <call name="generate_openssl" returns="Path">
        <arg name="dev_certs_dir" type="Path"/>
        <arg name="platform_domain" type="str"/>
        <arg name="sans" type="list[str]"/>
        <arg name="expiry_days" type="int" default="825"/>
        <annotation>Generate self-signed via tempfile config + subprocess.run(['openssl', 'req', ...])</annotation>
      </call>
      <call name="verify_san" returns="bool">
        <arg name="cert_file" type="Path"/>
        <arg name="required_sans" type="list[str]"/>
        <annotation>Post-generation SAN verification: every required entry must be in generated cert</annotation>
      </call>
      <call name="main" returns="None (sys.exit)">
        <annotation>Orchestration: check idempotency → select backend (auto/mkcert/openssl) → generate → verify → exit 0/1</annotation>
      </call>
    </functions>
    <crossLinks>
      <link target="shell_facade" relation="called_by"/>
      <link target="helpers_mk" relation="delegated_from"/>
      <link target="entrypoint_manifest" relation="registered_in"/>
    </crossLinks>
  </entity>

  <entity id="shell_facade" type="SCRIPT" keywords="generate-dev-certs facade thin-wrapper">
    <annotation>core/modules/nginx/generate-dev-certs.sh — ≤40 LOC: env defaults + python3 call</annotation>
    <crossLinks>
      <link target="dev_cert_generator_py" relation="delegates_to"/>
    </crossLinks>
  </entity>

  <entity id="helpers_mk" type="MAKEFILE" keywords="helpers.mk dev-certs target">
    <annotation>makefiles/helpers.mk:38-43 — обновлённая команда: python3 вместо bash</annotation>
    <crossLinks>
      <link target="dev_cert_generator_py" relation="invokes"/>
    </crossLinks>
  </entity>

  <entity id="entrypoint_manifest" type="CONFIG" keywords="entrypoint-manifest mechanism delegates_to">
    <annotation>core/entrypoint-manifest.yaml:499-504 — mechanism: python-script, delegates_to: dev_cert_generator.py</annotation>
    <crossLinks>
      <link target="dev_cert_generator_py" relation="references"/>
    </crossLinks>
  </entity>

  <entity id="test_module" type="TEST" keywords="unit-test dev-cert-generator pytest tmp_path caplog">
    <annotation>tests/unit/test_dev_cert_generator.py — 10+ unit tests + 1 integration test</annotation>
    <crossLinks>
      <link target="dev_cert_generator_py" relation="tests"/>
    </crossLinks>
  </entity>
</code_graph>
```

---

## 4. Step-by-Step Data Flow

```
Brief 099 → DevPlan 099 (этот документ)
  │
  ├─► Wave 1: Python-модуль + тесты (независимы, нет общих файлов)
  │   ├─► TASK-1: Создать core/modules/nginx/dev_cert_generator.py
  │   │   ├─► required_sans(platform_domain) → list[str]: sorted SAN entries
  │   │   ├─► get_cert_sans(cert_file: Path) → list[str]: openssl x509 parsing
  │   │   ├─► cert_is_current(cert_file, key_file, platform_domain) → bool:
  │   │   │       (files exist) ∧ (SAN ⊇ required) ∧ (-checkend 30d)
  │   │   ├─► generate_mkcert(dev_certs_dir, sans) → Path: subprocess mkcert
  │   │   ├─► generate_openssl(dev_certs_dir, platform_domain, sans, expiry_days) → Path:
  │   │   │       tempfile config → subprocess openssl req
  │   │   ├─► verify_san(cert_file, required_sans) → bool: set comparison
  │   │   └─► main(): оркестрация с sys.exit(0/1)
  │   │
  │   └─► TASK-5: Создать tests/unit/test_dev_cert_generator.py
  │       ├─► test_required_sans_default, test_required_sans_context,
  │       │   test_required_sans_sorted
  │       ├─► test_cert_is_current_exists, test_cert_is_current_missing_san,
  │       │   test_cert_is_current_missing_file, test_cert_is_current_expiring
  │       ├─► test_verify_san_all_present, test_verify_san_missing
  │       ├─► test_get_cert_sans_parse, test_get_cert_sans_no_file
  │       ├─► test_main_idempotent_noop, test_main_generate_missing,
  │       │   test_main_unknown_backend, test_main_missing_backend_tool
  │       └─► test_integration_full_flow (generate → verify → no-op)
  │
  ├─► Wave 2: Интеграционные точки (все зависят от TASK-1, независимы друг от друга)
  │   ├─► TASK-2: Shrink shell facade generate-dev-certs.sh (295→~40 LOC)
  │   ├─► TASK-3: Update makefiles/helpers.mk dev-certs target (bash → python3)
  │   └─► TASK-4: Update core/entrypoint-manifest.yaml (mechanism + delegates_to)
  │
  └─► Wave 3: Верификация
      ├─► make dev-certs → verify exit 0, idempotent no-op
      ├─► make gate MODE=fast → verify green (AC7)
      └─► pytest tests/unit/test_dev_cert_generator.py -v → 100% pass
```

---

## 5. Detailed Design

### 5.1 Python-модуль `dev_cert_generator.py`

**Стиль:** Следует шаблону `core/internal/shared/docker_auth.py` и `secrets_env_parser.py`:
- `#!/usr/bin/env python3` + GREP_SUMMARY + STRUCTURE + MODULE_CONTRACT
- `# region FUNC_xxx` / `# endregion FUNC_xxx` маркеры
- Doxygen-контракты (@purpose, @io, @complexity, @invariants, @rationale)
- LDD-логи через `print(f"[IMP:{level}][{func_name}] message", file=sys.stderr)`
- Импорты: `argparse`, `logging`, `os`, `subprocess`, `sys`, `tempfile`, `pathlib.Path`

**Сигнатуры функций (отображение shell → Python):**

| Shell | Python | Изменения |
|-------|--------|-----------|
| `required_sans()` — глобальные переменные | `required_sans(platform_domain: str) -> list[str]` | Параметризация: PLATFORM_DOMAIN как явный аргумент |
| `get_cert_sans(cert_file)` | `get_cert_sans(cert_file: Path) -> list[str]` | Path вместо строки |
| `cert_is_current()` — глобальные переменные | `cert_is_current(cert_file: Path, key_file: Path, platform_domain: str) -> bool` | Параметризация: все входы явные |
| `generate_mkcert()` — глобальные переменные | `generate_mkcert(dev_certs_dir: Path, sans: list[str]) -> Path` | Параметризация; возвращает Path |
| `generate_openssl()` — глобальные переменные | `generate_openssl(dev_certs_dir: Path, platform_domain: str, sans: list[str], expiry_days: int = 825) -> Path` | Параметризация; возвращает Path |
| `verify_san()` — глобальные переменные | `verify_san(cert_file: Path, required_sans: list[str]) -> bool` | Параметризация: required_sans как явный аргумент |
| `main()` | `main() -> None` | sys.exit(0/1); читает env vars через os.environ |

**Константы модуля:**
```python
DEFAULT_PLATFORM_DOMAIN = "ai-platform.local"
DEFAULT_DEV_CERTS_DIR = str(Path(__file__).resolve().parent / "dev-certs")
DEFAULT_CERT_BACKEND = "auto"
EXPIRY_DAYS = 825
EXPIRY_CHECK_DAYS = 30  # -checkend window
```

**main() псевдокод:**
```python
def main() -> None:
    dev_certs_dir = Path(os.environ.get("DEV_CERTS_DIR", DEFAULT_DEV_CERTS_DIR))
    platform_domain = os.environ.get("PLATFORM_DOMAIN", DEFAULT_PLATFORM_DOMAIN)
    cert_backend = os.environ.get("CERT_BACKEND", DEFAULT_CERT_BACKEND)
    cert_file = dev_certs_dir / "fullchain.pem"
    key_file = dev_certs_dir / "privkey.pem"

    _log(7, "main", f"DEV_CERTS_DIR={dev_certs_dir}")
    _log(8, "main", f"PLATFORM_DOMAIN={platform_domain}")
    _log(8, "main", f"CERT_BACKEND={cert_backend}")

    if cert_is_current(cert_file, key_file, platform_domain):
        _log(9, "main", "Cert up-to-date — no action needed")
        sys.exit(0)

    # Auto-select backend
    if cert_backend == "auto":
        cert_backend = "mkcert" if _command_exists("mkcert") else "openssl"

    # Generate
    sans = required_sans(platform_domain)
    if cert_backend == "mkcert":
        _ensure_mkcert()
        generate_mkcert(dev_certs_dir, sans)
    elif cert_backend == "openssl":
        _ensure_openssl()
        generate_openssl(dev_certs_dir, platform_domain, sans, EXPIRY_DAYS)
    else:
        _log(9, "main", f"ERROR: Unknown CERT_BACKEND='{cert_backend}'")
        sys.exit(1)

    # Verify
    if not verify_san(cert_file, sans):
        _log(9, "main", "FAILED: generated cert SAN verification failed")
        sys.exit(1)

    _log(9, "main", "Certificate generated successfully")
    sys.exit(0)
```

### 5.2 Shell-фасад `generate-dev-certs.sh`

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: generate-dev-certs facade thin-wrapper python3 dev-cert-generator
# STRUCTURE: ┌env defaults┐ → ◇ export → ⊕ python3 dev_cert_generator.py → ⎋ exit $?
# region MODULE_CONTRACT
## @purpose  Thin shell facade for dev_cert_generator.py — preserves Makefile contract.
## @scope    Env var defaults (DEV_CERTS_DIR, PLATFORM_DOMAIN, CERT_BACKEND) + python3 call.
## @invariants
##   - BUSINESS LOGIC LIVES IN dev_cert_generator.py — this file is a facade only
##   - Same env var interface as before migration
##   - Exit code passthrough from Python module
## @rationale Strangler-Fig: shell сохраняет контракт для Makefile и smoke-тестов,
##            вся бизнес-логика в Python (языковая политика AGENTS.md)
# endregion MODULE_CONTRACT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${DEV_CERTS_DIR:="${SCRIPT_DIR}/dev-certs"}"
: "${PLATFORM_DOMAIN:="ai-platform.local"}"
: "${CERT_BACKEND:="auto"}"

export DEV_CERTS_DIR PLATFORM_DOMAIN CERT_BACKEND
exec python3 "${SCRIPT_DIR}/dev_cert_generator.py"
```

~25 строк (без MODULE_CONTRACT — ~17 строк с кодом). GREP_SUMMARY/STRUCTURE сохранены.

### 5.3 Makefile target `helpers.mk`

Обновлённая команда (строка 42):
```makefile
dev-certs:
	@echo "[IMP:7][make][dev-certs] Ensuring dev SSL certificates..."
	@_env_pd="$$(grep -E '^PLATFORM_DOMAIN=' "$(_platform_root)/.env" 2>/dev/null | tail -n1 | cut -d= -f2-)"; \
	PLATFORM_DOMAIN="$${PLATFORM_DOMAIN:-$${_env_pd:-ai-platform.local}}" \
	DEV_CERTS_DIR="$${DEV_CERTS_DIR:-$(_platform_root)/core/modules/nginx/dev-certs}" \
	python3 $(_platform_root)/core/modules/nginx/dev_cert_generator.py
	@echo "[IMP:9][make][dev-certs] Dev certificates check complete"
```

Изменение: строка 42 — `bash ... generate-dev-certs.sh` → `python3 ... dev_cert_generator.py`.
Также добавлен `DEV_CERTS_DIR` (со значением по умолчанию `core/modules/nginx/dev-certs`).
Python-модуль имеет fallback: `Path(__file__).resolve().parent / "dev-certs"` — если
DEV_CERTS_DIR не установлен, используется путь относительно расположения модуля.
TRAP[BUG] (строка 37) сохранён без изменений.

**@rationale (Makefile вызывает Python напрямую, фасад — для обратной совместимости):**
Makefile уже управляет env vars (PLATFORM_DOMAIN, DEV_CERTS_DIR) — добавлять лишний
shell-процесс (фасад) не нужно. Shell-фасад сохраняется для прямых вызовов
(`bash generate-dev-certs.sh`) из существующих тестов и ручного использования.
Это классический Strangler-Fig: Makefile → Python напрямую, старые потребители → фасад.

### 5.4 entrypoint-manifest.yaml

Обновлённая запись (строки 499-505):
```yaml
- make_target: dev-certs
  mechanism: python-script
  delegates_to: core/modules/nginx/dev_cert_generator.py
  signature: make dev-certs [CERT_BACKEND=...]
  operation_ru: Генерация dev SSL-сертификатов
  description: Idempotent generation of self-signed dev SSL certificates for nginx
    (hybrid mkcert→openssl)
```

- `mechanism: shell-script` → `python-script` (соответствует словарю manifest: `discover-modules` имеет `python-script`)
- `delegates_to`: путь к Python-модулю
- Это S1-раздел (structural) — НЕ generated; редактируется вручную. `allowed_verbs` не меняется (dev-certs уже в списке).

---

## 6. $TASKS

| # | Задача | Владелец | Артефакт | Приоритет | Зависимости | Сложность |
|---|--------|----------|----------|:---------:|-------------|:---------:|
| TASK-1 | Создать `core/modules/nginx/dev_cert_generator.py` | Coder | Python-модуль ~250 LOC | HIGH | — | 6 |
| TASK-2 | Shrink `generate-dev-certs.sh` до ≤50 LOC фасада | Coder | Shell-фасад ~40 LOC | HIGH | TASK-1 | 2 |
| TASK-3 | Обновить `makefiles/helpers.mk` dev-certs target | Coder | Makefile target (1 строка) | HIGH | TASK-1 | 1 |
| TASK-4 | Обновить `core/entrypoint-manifest.yaml` | Coder | YAML запись (2 поля) | MEDIUM | TASK-1 | 1 |
| TASK-5 | Создать `tests/unit/test_dev_cert_generator.py` | Coder | Тестовый файл ~250 LOC | HIGH | TASK-1 | 5 |

---

## 7. $PARALLEL_GROUPS

### Wave 1 (независимые, нет общих файлов)
- **Tasks:** TASK-1, TASK-5
- **Files:** `core/modules/nginx/dev_cert_generator.py`, `tests/unit/test_dev_cert_generator.py`
- **Strategy:** TASK-5 может создаваться параллельно с TASK-1 — тесты определяют контракт API до имплементации. Coder создаёт оба файла в одной сессии или запускает тесты после имплементации.
- **Command:** `coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-5`

### Wave 2 (все зависят от TASK-1, независимы друг от друга)
- **Tasks:** TASK-2, TASK-3, TASK-4
- **Files:** `core/modules/nginx/generate-dev-certs.sh`, `makefiles/helpers.mk`, `core/entrypoint-manifest.yaml`
- **Strategy:** Три независимых изменения в трёх разных файлах — можно делать параллельно.
- **Command:** `coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4`

### Wave 3 (верификация)
- **Tasks:** Проверка AC3, AC7
- **Commands:**
  - `make dev-certs` — должен выйти с exit 0 (новый или текущий сертификат)
  - `make dev-certs` (повторно) — идемпотентный no-op
  - `make gate MODE=fast` — зелёный
  - `pytest tests/unit/test_dev_cert_generator.py -v` — 100% pass

---

## 8. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_dev_cert_generator.py` | `test_required_sans_default` | PLATFORM_DOMAIN=ai-platform.local → 4 entries (no context wildcard) | `required_sans()` |
| `tests/unit/test_dev_cert_generator.py` | `test_required_sans_context` | PLATFORM_DOMAIN=custom.local → +DNS:*.custom.local | `required_sans()` |
| `tests/unit/test_dev_cert_generator.py` | `test_required_sans_sorted` | Verify output is sorted deterministically | `required_sans()` |
| `tests/unit/test_dev_cert_generator.py` | `test_get_cert_sans_parse` | Mock openssl x509 output → parse SAN entries | `get_cert_sans()` |
| `tests/unit/test_dev_cert_generator.py` | `test_get_cert_sans_no_file` | Non-existent cert file → empty list | `get_cert_sans()` |
| `tests/unit/test_dev_cert_generator.py` | `test_cert_is_current_exists` | Mock: cert+key exist, SANs match, not expiring → True | `cert_is_current()` |
| `tests/unit/test_dev_cert_generator.py` | `test_cert_is_current_missing_san` | Mock: cert exists but SAN entry missing → False | `cert_is_current()` |
| `tests/unit/test_dev_cert_generator.py` | `test_cert_is_current_missing_file` | No cert file → False | `cert_is_current()` |
| `tests/unit/test_dev_cert_generator.py` | `test_cert_is_current_expiring` | Mock: -checkend fails (expiring <30d) → False | `cert_is_current()` |
| `tests/unit/test_dev_cert_generator.py` | `test_verify_san_all_present` | All required SANs in cert → True | `verify_san()` |
| `tests/unit/test_dev_cert_generator.py` | `test_verify_san_missing` | One SAN missing → False, IMP:9 log | `verify_san()` |
| `tests/unit/test_dev_cert_generator.py` | `test_main_idempotent_noop` | cert_is_current=True → exit 0, no generation | `main()` |
| `tests/unit/test_dev_cert_generator.py` | `test_main_generate_missing` | No cert → generate openssl → verify → exit 0 | `main()` |
| `tests/unit/test_dev_cert_generator.py` | `test_main_unknown_backend` | CERT_BACKEND=invalid → exit 1, IMP:9 error | `main()` |
| `tests/unit/test_dev_cert_generator.py` | `test_main_missing_backend_tool` | CERT_BACKEND=mkcert, mkcert not in PATH → exit 1 | `main()` |
| `tests/unit/test_dev_cert_generator.py` | `test_integration_full_flow` | Full flow with mocked subprocess: generate → verify → idempotent no-op | all |

**Test infrastructure:**
- `tmp_path` — временная директория для сертификатов
- `monkeypatch` — `os.environ` для env vars, `shutil.which` для command detection
- `mocker` (pytest-mock) — `mocker.patch('subprocess.run')` для openssl/mkcert
- `capsys` / `capfd` — захват stderr для LDD IMP:9 проверок (модуль пишет логи через
  `print(..., file=sys.stderr)`, а не через `logging` — caplog НЕ захватит такие сообщения)
- Все тесты: нативные импорты, `assert` с осмысленными проверками (Test Honesty R1/R2)
- Интеграционный тест: `from core.modules.nginx.dev_cert_generator import main` + `monkeypatch` + `tmp_path` + mocked subprocess

**LDD Telemetry в тестах:**
Модуль пишет LDD-логи через `print(..., file=sys.stderr)`, поэтому тесты используют
`capsys` (или `capfd`) для захвата stderr, а НЕ `caplog` (который перехватывает только `logging`).

```python
def test_verify_san_missing(capsys, tmp_path):
    """Verify IMP:9 log on SAN verification failure."""
    # ... setup ...
    result = verify_san(cert_file, required_sans)
    assert result is False
    # Check IMP:9 failure log in stderr
    captured = capsys.readouterr()
    assert "[IMP:9]" in captured.err, "No IMP:9 log on SAN verification failure"
    assert any(token in captured.err for token in ("SAN MISSING", "FAILED"))
```

---

## 9. File Manifest

| # | Файл | Действие | Тип | Описание |
|---|------|:--------:|-----|----------|
| F1 | `core/modules/nginx/dev_cert_generator.py` | CREATE | PYTHON | Python-модуль с 7 функциями (~250 LOC), GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT |
| F2 | `core/modules/nginx/generate-dev-certs.sh` | MODIFY | SHELL | Shrink до ~40 LOC фасада (env defaults + python3 call) |
| F3 | `makefiles/helpers.mk` | MODIFY | MAKEFILE | Строка 42: `bash script.sh` → `python3 module.py` |
| F4 | `core/entrypoint-manifest.yaml` | MODIFY | YAML | Строки 499-501 (mechanism + delegates_to) + описание на 504-505 |
| F5 | `tests/unit/test_dev_cert_generator.py` | CREATE | PYTHON | Unit-тесты (~250 LOC, 16 test functions) |

### Affected but unchanged (shell facade preserves contract)

| # | Файл | Причина |
|---|------|---------|
| A1 | `tests/test_nginx_dev_certs.py` (4 contract теста) | Вызывает `bash generate-dev-certs.sh` — фасад сохраняет контракт |
| A2 | `tests/test_smoke_nginx.py:218` (smoke-тест nginx) | Вызывает `bash generate-dev-certs.sh` — фасад сохраняет контракт |

---

## 10. Acceptance Criteria Mapping

| AC | Описание | Верификация | TASK |
|----|----------|-------------|:----:|
| AC1 | Python-модуль с 6 функциями + main() | Файл существует, содержит все 7 функций с MODULE_CONTRACT | TASK-1 |
| AC2 | Shell-фасад ≤ 50 LOC | `wc -l generate-dev-certs.sh` ≤ 50, GREP_SUMMARY/STRUCTURE присутствуют | TASK-2 |
| AC3 | `make dev-certs` идентичное поведение | Exit 0 (новый/текущий cert), exit 0 повторно (no-op), exit 1 при ошибке | Wave 3 |
| AC4 | Unit-тесты на cert_is_current, verify_san, required_sans | 16 тестов, 100% pass, LDD IMP:9 проверки | TASK-5 |
| AC5 | Интеграционный тест: generate → verify → no-op | `test_integration_full_flow` проходит | TASK-5 |
| AC6 | GREP_SUMMARY/STRUCTURE/TRAP сохранены | grep по всем изменённым файлам — все маркеры на месте | Wave 3 |
| AC7 | `make gate MODE=fast` зелёный | gate проходит (за исключением pre-existing failures от других модулей) | Wave 3 |

---

## 11. Design Decisions

### @rationale (размещение в core/modules/nginx/)
**Q:** Почему Python-модуль в `modules/nginx/`, а не в `internal/shared/`?
**A:** Модуль используется только nginx-модулем — это не shared-утилита (нарушило бы инвариант shared/: «минимум 2 потребителя»). Cross-layer правила разрешают Python в modules/ при условии отсутствия импортов из internal/. Модуль импортирует только stdlib + subprocess для openssl/mkcert.

### @rationale (subprocess, не Docker SDK)
**Q:** Почему subprocess.run а не cryptography/pyOpenSSL?
**A:** Поведение-preserving миграция. mkcert и openssl — единственные supported backends. Замена на Python crypto library изменила бы формат сертификатов и сломала контракт с nginx. subprocess.run сохраняет побайтовую совместимость вывода.

### @rationale (mechanism: python-script в manifest)
**Q:** Почему `python-script` а не `python-module`?
**A:** Словарь manifest уже содержит `python-script` (discover-modules, line 494). Значение `python-module` не используется в manifest. Консистентность с существующим vocabulary.

### @rationale (exec python3 в фасаде)
**Q:** Почему `exec python3` а не просто `python3`?
**A:** `exec` заменяет shell-процесс на Python — пробрасывает exit code напрямую без промежуточного shell-процесса. Экономия одного PID и гарантия точного проброса сигналов.

### @rationale (print(stderr) для LDD, не logging)
**Q:** Почему модуль использует `print(..., file=sys.stderr)` а не `logging`?
**A:** Сохранение обратной совместимости с shell-скриптом: `>&2 echo "[IMP:X]..."`. 
`print(stderr)` — минимальная зависимость (нет импорта logging), консистентный формат
с существующими shell-логами, видим в docker logs/stderr без настройки log levels.
Тесты используют `capsys`/`capfd` (не `caplog` — он ловит только logging, не stderr).

---

## 12. Risks & Mitigations

| Риск | Вероятность | Mitigation |
|------|:-----------:|------------|
| R1: Regression в smoke-тесте (`test_smoke_nginx.py:218` вызывает `generate-dev-certs.sh`) | LOW | Shell-фасад сохраняет контракт: те же env vars, тот же exit code. Smoke-тест вызывает `bash script.sh` — фасад работает идентично |
| R1a: Regression в contract-тестах (`test_nginx_dev_certs.py` — 4 теста вызывают `generate-dev-certs.sh`) | LOW | Тесты используют `subprocess.run(["bash", _SCRIPT_PATH], ...)` — shell-фасад сохраняет обратную совместимость. Файл НЕ требует изменений |
| R2: `make gate MODE=fast` красный из-за других причин | MEDIUM | Gate зависит от `make up` → `discover-modules dev-certs`. Если dev-certs работает, gate не сломается. Если gate красный по другим причинам (pre-existing) — зафиксировать в VR |
| R2: `make gate MODE=fast` красный из-за других причин | MEDIUM | Gate зависит от `make up` → `discover-modules dev-certs`. Если dev-certs работает, gate не сломается. Если gate красный по другим причинам (pre-existing) — зафиксировать в VR |
| R3: mkcert/openssl недоступны на CI | LOW | Модуль проверяет `command -v` перед вызовом (как и shell-скрипт). CI использует openssl (mkcert — macOS only) |
| R4: Нарушение Invariant 11 (entrypoint-manifest редактируется вручную) | NONE | S1-раздел (структурные записи операций) — НЕ generated. Generated только `allowed_verbs` и `gates` (G3 cycle break). `mechanism` и `delegates_to` — ручные поля |
| R5: Конфликт с параллельными changes в тех же файлах | LOW | 5 файлов — маловероятно пересечение с другими активными планами |

---

## 13. TRAP Preservation Checklist (AC6)

| Расположение | TRAP/Маркер | Статус |
|-------------|-------------|:------:|
| `generate-dev-certs.sh:2-3` | GREP_SUMMARY + STRUCTURE | → TASK-2 сохранит в фасаде |
| `generate-dev-certs.sh:4-18` | MODULE_CONTRACT | → TASK-2 обновит для фасада |
| `dev_cert_generator.py:1-3` | GREP_SUMMARY + STRUCTURE (NEW) | → TASK-1 создаст |
| `dev_cert_generator.py` | MODULE_CONTRACT (NEW) | → TASK-1 создаст |
| `helpers.mk:37` | TRAP[BUG] PLATFORM_DOMAIN | → TASK-3 НЕ меняет строку 37 |
| `helpers.mk:1-2` | GREP_SUMMARY + STRUCTURE | → TASK-3 не затрагивает |
| `entrypoint-manifest.yaml:1-13` | GREP_SUMMARY + MODULE_CONTRACT | → TASK-4 не затрагивает |

---

## 14. Next Steps

### Wave 1
```text
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/099-generate-dev-certs-python/02-DevPlan.md, implement Wave 1: TASK-1, TASK-5
```
- TASK-1: Create `core/modules/nginx/dev_cert_generator.py` (~250 LOC Python module)
- TASK-5: Create `tests/unit/test_dev_cert_generator.py` (~250 LOC, 16 tests)

### Wave 2
```text
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/099-generate-dev-certs-python/02-DevPlan.md, implement Wave 2: TASK-2, TASK-3, TASK-4
```
- TASK-2: Shrink `core/modules/nginx/generate-dev-certs.sh` to ≤50 LOC facade
- TASK-3: Update `makefiles/helpers.mk:42` command
- TASK-4: Update `core/entrypoint-manifest.yaml:499-501` mechanism + delegates_to

### Wave 3 (Verification)
```text
make dev-certs && make dev-certs && make gate MODE=fast && pytest tests/unit/test_dev_cert_generator.py -v
```

$END_DEVPLAN

---

## QA Review (2026-07-31)

🔒 **Verified against SHA:** `fbe306d4284d9105193605378be28eb64b3c6795`

### Verdict: APPROVED-WITH-CORRECTIONS

Все 7 AC брифа покрыты задачами DevPlan. Инварианты платформы соблюдены.
Выявлено 7 неточностей/пробелов, исправленных в этом документе:

### Внесённые поправки

| # | Severity | Finding | Fix |
|---|:--------:|---------|-----|
| Q1 | **HIGH** | `DEFAULT_DEV_CERTS_DIR` не определён в псевдокоде main() — ссылка на несуществующую константу | Добавлена константа: `Path(__file__).resolve().parent / "dev-certs"` |
| Q2 | **HIGH** | Тест-спецификация использует `caplog` для LDD, но модуль пишет логи через `print(stderr)`. `caplog` перехватывает только `logging` — тесты будут молча проходить без проверки IMP:9 | Тест-инфраструктура: `caplog` → `capsys`/`capfd`. LDD-пример обновлён. Добавлен @rationale (print vs logging) |
| Q3 | **MEDIUM** | Makefile target вызывает Python напрямую, но не экспортирует `DEV_CERTS_DIR` — Python-модуль должен уметь вычислить путь самостоятельно | Добавлен `DEV_CERTS_DIR` в Makefile target (с fallback на `$(_platform_root)/core/modules/nginx/dev-certs`). Python: `__file__`-based default. Добавлен @rationale (Makefile → Python напрямую, фасад — для обратной совместимости) |
| Q4 | **MEDIUM** | Существующие тесты (`test_nginx_dev_certs.py` — 4 contract теста, `test_smoke_nginx.py:218`) вызывают `bash generate-dev-certs.sh` — не упомянуты в File Manifest или Impacts | Добавлена секция «Affected but unchanged» в File Manifest + риск R1a |
| Q5 | **LOW** | Section 4 (Step-by-Step Data Flow) перечисляет 13 тестов, Section 8 ($TEST_SPEC) — 16. Пропущены: `test_required_sans_sorted`, `test_main_unknown_backend`, `test_main_missing_backend_tool` | Section 4 дополнена до 16 тестов (консистентно с Section 8) |
| Q6 | **LOW** | TASK-4 reference: `entrypoint-manifest.yaml:499-504` — delegates_to на строке 501, mechanism на 500; описание до 505 | Исправлено на `499-501` (места фактических изменений) |
| Q7 | **INFO** | Нет планов 100-105 — кросс-зависимости отсутствуют ✅ | Без изменений |

### Оставшиеся риски

| # | Severity | Risk |
|---|:--------:|------|
| RR1 | **LOW** | `make gate MODE=fast` зависит от `make up` → `discover-modules dev-certs`. Если gate красный по pre-existing причинам (другие модули) — зафиксировать в VR, не блокировать merge |
| RR2 | **LOW** | Интеграционный тест `test_integration_full_flow` требует `mocker.patch('subprocess.run')` — если API pytest-mock изменится, тест может потребовать обновления |

### Проверка инвариантов

| Инвариант | Статус | Evidence |
|-----------|:------:|----------|
| Makefile — единый фасад | ✅ HELD | `make dev-certs` → Python module (через helpers.mk) |
| Python-first (фасад ≤50 LOC) | ✅ HELD | Shell-фасад ~26 LOC, бизнес-логика в Python |
| Manifest Generation Contract | ✅ HELD | `dev-certs` запись в S1-секции (`dev:`), НЕ generated. `load_structural_sections()` исключает только `allowed_verbs` и `gates` |
| Идемпотентность (`make dev-certs`) | ✅ HELD | `cert_is_current()` → no-op если сертификат валиден |
| Запрещённые глаголы | ✅ HELD | `dev-certs` присутствует в `allowed_verbs` (manifest:661) |
| org = context | ✅ N/A | Не затрагивается |
| LiteLLM — PostgreSQL | ✅ N/A | Не затрагивается |

### Test Spec Quality (pre-implementation)

| Критерий | Статус |
|----------|:------:|
| Native imports (не subprocess для бизнес-логики) | ✅ `from core.modules.nginx.dev_cert_generator import main` |
| Zero Hardcode (tmp_path) | ✅ Все тесты используют tmp_path |
| LDD IMP:9 проверки | ✅ capsys/capfd для захвата stderr |
| Test Honesty R1 (нет pass-тестов) | ✅ Все 16 тестов имеют assert |
| Test Honesty R2 (нет unfalsifiable asserts) | ✅ Семантические проверки (SAN match, exit code) |
| Test Honesty R5 (anti-survivorship) | ⚠️ Не применимо — нет bug ID в тестах |
| Subprocess для бизнес-логики | ✅ Только для external tools (openssl/mkcert) — заmockано |
| 16 тестов — правдоподобно | ✅ Покрытие: 3×required_sans + 2×get_cert_sans + 4×cert_is_current + 2×verify_san + 4×main + 1×integration = 16 |
