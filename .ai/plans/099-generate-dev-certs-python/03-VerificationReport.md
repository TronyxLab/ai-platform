$START_VERIFICATION_REPORT
# VerificationReport 099 — Миграция generate-dev-certs.sh → Python

$ARTIFACT_CONTRACT
PURPOSE:               Верификация реализации DevPlan 099: миграция `generate-dev-certs.sh`
                       (295 LOC) → Python-модуль `dev_cert_generator.py` + shell-фасад.
DESCRIPTION:           Проверка всех 7 acceptance criteria (AC1-AC7) DevPlan 099:
                       Python-модуль, shell-фасад, `make dev-certs`, unit/contract тесты,
                       GREP_SUMMARY/STRUCTURE/TRAP сохранение, `make gate MODE=fast`.
RATIONALE:             Статический аудит + кросс-файловый drift-detection + runtime-валидация
                       тестов для подтверждения корректности Strangler-Fig миграции.
ACCEPTANCE_CRITERIA:   AC1-AC7 из DevPlan 099 §10; каждый проверен с evidence (file:line).
IMPLEMENTS:            DevPlan 099 (`.ai/plans/099-generate-dev-certs-python/02-DevPlan.md`)
IMPACTS:               VerificationReport записан в папку 099-generate-dev-certs-python.
REQUIRES:              Доступ к git HEAD (SHA d99a744), Python ≥ 3.10, pytest.
$END_ARTIFACT_CONTRACT

---

🔒 **Верифицировано против SHA:** `d99a744ccd788ab838a76556c23073feb35fa39b`
📅 **Дата:** 2026-07-31
📏 **Размер задачи:** SMALL (5 файлов по DevPlan §9 File Manifest; расширен до STANDARD — затронуты config-файлы: `helpers.mk`, `entrypoint-manifest.yaml`)

---

## Section 1 — Acceptance Criteria Matrix

| AC | Описание | Статус | Evidence |
|----|----------|:------:|----------|
| AC1 | Python-модуль `dev_cert_generator.py` с 6 функциями + main(), GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT, LDD [IMP:7-10] | ✅ PASS | `core/modules/nginx/dev_cert_generator.py`:556 — 7 функций (required_sans:137, get_cert_sans:168, cert_is_current:214, generate_mkcert:291, generate_openssl:372, verify_san:446, main:481) + 3 хелпера (_log:54, _command_exists:75, _ensure_tool:93, _strip_prefix:115, _write_openssl_config:337). GREP_SUMMARY:2, STRUCTURE:3, MODULE_CONTRACT:4-31. 15 `_log(9, ...)` IMP:9 вызовов (lines:105,281,319,324,327,426,431,436,464,469,471,513,535,540,543). |
| AC2 | Shell-фасад `generate-dev-certs.sh` ≤ 50 LOC, сохраняет GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT | ❌ **CRITICAL FAIL** | Файл **УДАЛЁН** (commit `586898d` — 295 deletions). DevPlan §5.2 описывает фасад ~25 строк с GREP_SUMMARY, STRUCTURE, MODULE_CONTRACT. Файл не существует ни в HEAD, ни в working tree. `git glob **/generate-dev-certs.sh` → no files found. |
| AC3 | `make dev-certs` — идентичное поведение (exit codes, идемпотентность, SAN) | ✅ PASS | `makefiles/helpers.mk:38-44` — вызывает `python3 dev_cert_generator.py` напрямую с env-переменными (PLATFORM_DOMAIN, DEV_CERTS_DIR, CERT_BACKEND). TRAP[BUG] PLATFORM_DOMAIN сохранён (line 37). Runtime-валидация: unit-тесты (16/16) + contract-тесты (4/4) проходят — идемпотентность, SAN-дрифт, expiry проверены. |
| AC4 | Unit-тесты на cert_is_current (SAN match/mismatch, missing file, expiry), verify_san (all present/missing), required_sans (default/context) | ✅ PASS | `tests/unit/test_dev_cert_generator.py`:639 — 16/16 tests PASS (1.10s). Покрытие: required_sans(3), get_cert_sans(2), cert_is_current(4), verify_san(2), main(4), integration(1). LDD IMP:9 verified via capsys в каждом business-logic тесте. Native imports (`from core.modules.nginx.dev_cert_generator import ...`). Zero Hardcode (monkeypatch.setenv + tmp_path). TRAP[TEST] аннотации на всех 16 тестах. |
| AC5 | Интеграционный тест: generate → verify → idempotent no-op | ✅ PASS | `test_integration_full_flow:579` — PASS. Мокированный subprocess с side_effect эмулирует openssl req. Первый вызов main() → exit 0, cert+key созданы. Второй вызов main() → exit 0, req_count остаётся 1 (идемпотентность). |
| AC6 | GREP_SUMMARY/STRUCTURE/TRAP сохранены | ⚠️ PARTIAL | **Сохранено:** helpers.mk TRAP[BUG] PLATFORM_DOMAIN:37 ✅, Python-модуль GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT ✅, тесты GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT/TRAP[TEST] ✅, entrypoint-manifest mechanism/delegates_to обновлены ✅. **Нарушено:** shell-фасад удалён — нечего сохранять ❌. **Документационный drift:** `sync_env_defaults.py:174` всё ещё ссылается на `generate-dev-certs.sh` (устаревший комментарий). |
| AC7 | `make gate MODE=fast` зелёный | ⚠️ NOT VERIFIED | Полный `make gate MODE=fast` не запускался (требует Docker, openssl, compose-стек). Косвенные доказательства: 16/16 unit-тестов PASS, 4/4 contract-тестов PASS, `dev-certs` в `allowed_verbs` entrypoint-manifest.yaml:662 ✅. Риск RR1 из DevPlan §12: gate может быть красным по pre-existing причинам от других модулей — не блокирует merge. |

---

## Section 2 — Drift Analysis (Phase 2)

| DRIFT-ID | Severity | Описание | Evidence | Fix |
|----------|:--------:|----------|----------|-----|
| DRIFT-AC2-SHELL-DELETED | **CRITICAL** | Shell-фасад `generate-dev-certs.sh` удалён (295 LOC remove) вместо сокращения до ≤50 LOC фасада, как предписано DevPlan §5.2 | Commit `586898d`: `core/modules/nginx/generate-dev-certs.sh \| 295 -------------------------------`; `git show HEAD:core/modules/nginx/generate-dev-certs.sh` → `fatal: path does not exist in HEAD` | Создать shell-фасад из DevPlan §5.2 (~26 строк: env defaults + `exec python3 dev_cert_generator.py`). Без фасада нарушается AC2, но **нет runtime-поломки**: все потребители (`make dev-certs`, `test_nginx_dev_certs.py`, `test_smoke_nginx.py`) обновлены на вызов Python-модуля напрямую. |
| DRIFT-SYNC-ENV-COMMENT | **LOW** | `sync_env_defaults.py:174` ссылается на несуществующий `generate-dev-certs.sh` | `core/internal/scripts/sync_env_defaults.py:174`: `"# Сертификаты генерируются автоматически через generate-dev-certs.sh (make dev-certs)."` | Заменить `generate-dev-certs.sh` на `dev_cert_generator.py` в комментарии. |

**Сводка:** 1 CRITICAL (AC2 violation — shell facade deleted), 1 LOW (documentation drift).

---

## Section 3 — Static Audit (Phase 1)

### Compliance Matrix (file × check)

| File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region/#endregion paired | Doxygen @tags на функциях | LDD [IMP:7-10] | TRAP сохранён | Secrets exposed |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `core/modules/nginx/dev_cert_generator.py` (556 LOC) | ✅:2 | ✅:3 | ✅:4-31 | ✅ 11 пар | ✅ @purpose/@io/@complexity/@invariants/@rationale на каждой из 10 функций | ✅ 15 IMP:9 + 12 IMP:8 + 8 IMP:7 | N/A (новый) | ✅ Нет |
| `tests/unit/test_dev_cert_generator.py` (639 LOC) | ✅:2 | ✅:3 | ✅:4-20 | ✅ 17 пар | ✅ @purpose/@io/@complexity на каждой test-функции | ✅ capsys capture в 11/16 тестах | ✅ 16× TRAP[TEST] | ✅ Нет |
| `tests/test_nginx_dev_certs.py` (316 LOC) | ✅:1 | ✅:2 | ✅:3-17 | ✅ | ✅ | ✅ IMP:9 gate | ✅ | ✅ Нет |
| `makefiles/helpers.mk:37-44` | ✅:1 | ✅:2 | ✅:4-11 | N/A (Makefile) | N/A | ✅ IMP:7+IMP:9 | ✅ TRAP[BUG]:37 | ✅ Нет |
| `core/entrypoint-manifest.yaml:501-507` | N/A | N/A | N/A | N/A | N/A | N/A | N/A | ✅ Нет |
| `core/modules/nginx/generate-dev-certs.sh` | ❌ **ФАЙЛ НЕ СУЩЕСТВУЕТ** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | N/A |

### Findings

| # | Severity | File:Line | Issue | Fix |
|---|:--------:|-----------|-------|-----|
| F1 | CRITICAL | `core/modules/nginx/generate-dev-certs.sh` (MISSING) | Shell-фасад не существует — AC2 не выполнен | Создать фасад из DevPlan §5.2 (~26 строк) |
| F2 | LOW | `core/internal/scripts/sync_env_defaults.py:174` | Документационный drift — ссылка на `generate-dev-certs.sh` | Обновить на `dev_cert_generator.py` |

---

## Section 4 — LDD Telemetry Analysis

### IMP:9 Coverage (business logic assertions)

Модуль использует хелпер `_log(9, func_name, message)` → `print(f"[IMP:9][{func_name}] {message}", file=sys.stderr)`. Всего 15 точек IMP:9:

| # | Function | Line | Message | Type |
|---|----------|------|---------|------|
| 1 | `_ensure_tool` | 105 | `ERROR: CERT_BACKEND={name} but {name} not in PATH` | Error gate |
| 2 | `cert_is_current` | 281 | `Cert is current (SAN matches, >30d until expiry)` | Idempotency success |
| 3 | `generate_mkcert` | 319 | `ERROR: mkcert not found on PATH` | Error gate |
| 4 | `generate_mkcert` | 324 | `ERROR: mkcert failed (exit {rc})` | Error gate |
| 5 | `generate_mkcert` | 327 | `mkcert generated: {cert_file}` | Generation success |
| 6 | `generate_openssl` | 426 | `ERROR: openssl not found on PATH` | Error gate |
| 7 | `generate_openssl` | 431 | `ERROR: openssl req failed (exit {rc})` | Error gate |
| 8 | `generate_openssl` | 436 | `OpenSSL generated: {cert_file}` | Generation success |
| 9 | `verify_san` | 464 | `SAN MISSING: {entry}` | Per-entry failure |
| 10 | `verify_san` | 469 | `SAN verification FAILED — missing entries above` | Summary failure |
| 11 | `verify_san` | 471 | `All required SAN entries present` | Verification success |
| 12 | `main` | 513 | `Cert up-to-date — no action needed` | Idempotent no-op |
| 13 | `main` | 535 | `ERROR: Unknown CERT_BACKEND={backend}` | Error gate |
| 14 | `main` | 540 | `FAILED: generated cert SAN verification failed` | Error gate |
| 15 | `main` | 543 | `Certificate generated successfully` | Generation success |

**IMP:9 покрытие:** Все критические пути покрыты — идемпотентность (1 success), генерация (2 success + 4 error), верификация (3), main-оркестрация (4), tool gate (1).

**Anti-Illusion Verdict:** ✅ PASS — 15 IMP:9 точек на 10 функций. Каждый бизнес-путь (success + error) имеет IMP:9 лог. Тесты проверяют IMP:9 presence через capsys (`_assert_imp9` в 8 тестах).

---

## Section 5 — Runtime Validation (Phase 5)

### Unit Tests

```
tests/unit/test_dev_cert_generator.py — 16/16 PASSED (1.10s)
```

| Test | Status | IMP:9 verified |
|------|:------:|:--------------:|
| test_required_sans_default | ✅ | N/A (pure function) |
| test_required_sans_context | ✅ | IMP:8 check |
| test_required_sans_sorted | ✅ | N/A (pure function) |
| test_get_cert_sans_parse | ✅ | N/A (mock) |
| test_get_cert_sans_no_file | ✅ | IMP:7 check |
| test_cert_is_current_exists | ✅ | ✅ `Cert is current` |
| test_cert_is_current_missing_san | ✅ | IMP:7 drift |
| test_cert_is_current_missing_file | ✅ | IMP:8 check |
| test_cert_is_current_expiring | ✅ | IMP:7 expiry |
| test_verify_san_all_present | ✅ | ✅ `All required SAN entries present` |
| test_verify_san_missing | ✅ | ✅ `SAN MISSING` + `FAILED` |
| test_main_idempotent_noop | ✅ | ✅ `no action needed` |
| test_main_generate_missing | ✅ | ✅ `Certificate generated successfully` |
| test_main_unknown_backend | ✅ | ✅ `Unknown CERT_BACKEND` |
| test_main_missing_backend_tool | ✅ | ✅ `mkcert not in PATH` |
| test_integration_full_flow | ✅ | ✅ `Certificate generated successfully` + `no action needed` |

### Contract Tests

```
tests/test_nginx_dev_certs.py — 4/4 PASSED (2.29s)
```

| Test | Status |
|------|:------:|
| test_generate_certs_openssl_backend | ✅ PASS |
| test_context_domain_in_san | ✅ PASS |
| test_second_run_is_noop | ✅ PASS |
| test_regenerates_on_san_drift | ✅ PASS |

**Итого:** 20/20 тестов PASS (0 failures, 0 skips).

---

## Section 6 — Test Quality Audit (Phase 4)

### Test Honesty Rules

| Rule | Status | Evidence |
|------|:------:|----------|
| R1: No pass-tests | ✅ | Все 16 тестов имеют `assert`. Минимум 1 осмысленный assert на тест. |
| R2: No unfalsifiable asserts | ✅ | Семантические проверки: SAN set comparison, exit codes, IMP:9 presence, file existence, req_count idempotency. Нет `assert True` или `assert isinstance(x, object)`. |
| R3: Stale skip | ✅ | Нет skip-маркеров в тестовом файле. |
| R4: NO_SERVICE = FAIL | ✅ | Не применимо (тесты не зависят от сервисов). |
| R5: Anti-survivorship | ⚠️ N/A | Нет bug ID в тестах — правило не применимо. |

### Test Health Score: 95/100

- −5 за отсутствие shell-фасада (AC2) — косвенно влияет на test coverage контракта фасада

### Native Imports

```
from core.modules.nginx.dev_cert_generator import (
    DEFAULT_PLATFORM_DOMAIN, EXPIRY_CHECK_DAYS,
    cert_is_current, get_cert_sans, main, required_sans, verify_san,
)
```
✅ Все импорты нативные — никаких `subprocess.run` для вызова бизнес-логики.

### Mock Strategy

- `unittest.mock.patch` — subprocess.run, shutil.which, cert_is_current, generate_openssl/mkcert, verify_san, get_cert_sans
- `monkeypatch.setenv` — DEV_CERTS_DIR, PLATFORM_DOMAIN, CERT_BACKEND
- ✅ pytest-mock **НЕ используется** (не в зависимостях проекта — верно)
- ✅ capsys для захвата stderr (не caplog — модуль пишет через print, не logging)

---

## Section 7 — Config Sync Audit (Phase 6)

### Env Variable Propagation Chain

| Variable | .env | helpers.mk | dev_cert_generator.py | Status |
|----------|:----:|:----------:|:---------------------:|:------:|
| `PLATFORM_DOMAIN` | ✅ grep `.env` | ✅:41 (with fallback `ai-platform.local`) | ✅:501 `os.environ.get("PLATFORM_DOMAIN", DEFAULT)` | ✅ Consistent |
| `DEV_CERTS_DIR` | N/A | ✅:42 (with fallback) | ✅:500 `os.environ.get("DEV_CERTS_DIR", DEFAULT)` | ✅ Consistent |
| `CERT_BACKEND` | N/A | N/A | ✅:502 `os.environ.get("CERT_BACKEND", "auto")` | ✅ Consistent |

### entrypoint-manifest.yaml Consistency

```yaml
- make_target: dev-certs         # ✅ Соответствует helpers.mk .PHONY:18
  mechanism: python-script       # ✅ Соответствует vocabulary manifest
  delegates_to: core/modules/nginx/dev_cert_generator.py  # ✅ Файл существует
  signature: make dev-certs [CERT_BACKEND=...]            # ✅ Соответствует фактическому использованию
```

- `dev-certs` присутствует в `allowed_verbs`:662 ✅
- Mechanism `python-script` консистентен с другими Python-скриптами (`discover-modules`:496) ✅

### Makefile → entrypoint → filesystem Triad

| Makefile .PHONY | entrypoint-manifest | Файл на диске | Status |
|:---:|:---:|:---:|:---:|
| `dev-certs` (helpers.mk:18) | `dev-certs` (manifest:501) | `core/modules/nginx/dev_cert_generator.py` ✅ | ✅ Triad consistent |
| — | — | `core/modules/nginx/generate-dev-certs.sh` | ❌ **Файл отсутствует**, но больше не регистрируется в triad (ни в .PHONY, ни в delegates_to) |

---

## Section 8 — TRAP Preservation Checklist (AC6 per DevPlan §13)

| Расположение | TRAP/Маркер | Статус | Evidence |
|-------------|-------------|:------:|----------|
| `helpers.mk:37` | TRAP[BUG] PLATFORM_DOMAIN | ✅ Сохранён | `helpers.mk:37`: `# ⚠️ TRAP[BUG] · 2026-07-16 · HIGH · PLATFORM_DOMAIN from .env` |
| `helpers.mk:1-2` | GREP_SUMMARY + STRUCTURE | ✅ Сохранён | `helpers.mk:1-2` без изменений |
| `dev_cert_generator.py:1-3` | GREP_SUMMARY + STRUCTURE | ✅ Создан | `dev_cert_generator.py:2-3` |
| `dev_cert_generator.py:4-31` | MODULE_CONTRACT | ✅ Создан | `dev_cert_generator.py:4-31` |
| `entrypoint-manifest.yaml` | GREP_SUMMARY + MODULE_CONTRACT | ✅ Не затронут | Секция dev-certs (501-507) добавлена, остальное без изменений |
| `generate-dev-certs.sh` | GREP_SUMMARY + STRUCTURE + MODULE_CONTRACT | ❌ **Утеряны** | Файл удалён; маркеры не перенесены в фасад (фасад не создан) |

---

## Semantic Verdict

**DRIFTED (HIGH)** — соответствует пользовательскому вердикту **PARTIAL**

### Обоснование

| Критерий | Статус |
|----------|:------:|
| Python-модуль (AC1) | ✅ Полностью выполнен — 7 функций, 15 IMP:9, полный MODULE_CONTRACT |
| Shell-фасад (AC2) | ❌ **CRITICAL** — файл удалён вместо сокращения до ≤50 LOC |
| `make dev-certs` (AC3) | ✅ Функционально идентичен — helpers.mk вызывает Python напрямую |
| Unit-тесты (AC4) | ✅ 16/16 PASS, полное покрытие, IMP:9 verification |
| Интеграционный тест (AC5) | ✅ PASS — generate → verify → idempotent no-op |
| TRAP сохранение (AC6) | ⚠️ PARTIAL — helpers.mk TRAP сохранён, фасад отсутствует |
| `make gate MODE=fast` (AC7) | ⚠️ NOT VERIFIED — косвенные доказательства положительные |
| Тесты | ✅ 20/20 PASS (0 failures, 0 skips) |

### Рекомендация

Создать shell-фасад `core/modules/nginx/generate-dev-certs.sh` из шаблона DevPlan §5.2 (~26 строк) — это восстановит AC2 без изменения какого-либо поведения. Фасад НЕ требуется для функционирования системы (все потребители обновлены), но ТРЕБУЕТСЯ для соответствия контракту DevPlan.

Делегирование: `task(subagent_type="Plan", description="Fix AC2 shell facade", prompt="Review VerificationReport 099 at .ai/plans/099-generate-dev-certs-python/03-VerificationReport.md. Create shell facade core/modules/nginx/generate-dev-certs.sh from DevPlan §5.2 template (~26 LOC).")`

---

## Findings Summary

| # | Severity | ID | Issue |
|---|:--------:|-----|-------|
| F1 | **CRITICAL** | DRIFT-AC2-SHELL-DELETED | Shell-фасад не создан — AC2 DevPlan 099 нарушен |
| F2 | LOW | DRIFT-SYNC-ENV-COMMENT | Устаревшая ссылка на `generate-dev-certs.sh` в `sync_env_defaults.py:174` |

$END_VERIFICATION_REPORT
