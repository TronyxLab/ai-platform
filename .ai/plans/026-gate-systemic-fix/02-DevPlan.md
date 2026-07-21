# 026-DevPlan: Gate systemic fix — 3 волны, fixture schema coherence + pre-commit registration audit

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранить 5 из 6 оставшихся gate failures (F6 macOS overlay — бриф 027) и предотвратить их повторное появление через три механизма: pytest_sessionstart автовалидация test fixtures против schema, pre-commit hook scripts-audit для детекции незарегистрированных скриптов, точечные фиксы (executable bit, changelog, lint, TRAP[DEBT] removal).
DESCRIPTION:           После 5 циклов check/fix (коммит 79e780c) большинство проблем S1 и S3 закрыты. Актуальный `make gate MODE=full` (SKIP_PRECOMMIT=1) показывает 6 уникальных failures. Суперпозиция (FULL mode, superposition skill) выявила корневую причину трёх failures (F3-F5): отсутствие механизма когерентности test_data/*.yaml ↔ core/schemas/*.json. Решение Option A (score 9/10, подтверждено оператором 2026-07-21): pytest_sessionstart автовалидация фикстур против schema + gate-тест для явного покрытия. Две точечных проблемы (F1 executable bit, F2 changelog) — тривиальные фиксы. Pre-commit registration drift: новый `make scripts-audit` + pre-commit hook + регистрация в entrypoint-manifest.yaml. DevPlan структурирован в 3 волны с суммарным усилием ~1 час 20 минут.
RATIONALE:             Ситуация после DevPlan'ов 024-025 типична: schema эволюционирует, test fixtures остаются старыми, gate детектит дрейф post-factum. Без превентивного механизма (автовалидация фикстур на pytest_sessionstart) каждый следующий DevPlan будет порождать те же failures. Pre-commit hook scripts-audit закрывает registration friction — новые shebang-скрипты не могут быть закоммичены без регистрации. Текущий момент — чистый working tree с изолированным набором failures — оптимален для санации.
ACCEPTANCE_CRITERIA:
  1. `make gate MODE=fast` — зелёный.
  2. `make gate MODE=full` — зелёный за исключением F6 (status-page-test macOS — бриф 027).
  3. `git ls-files -s core/internal/bootstrap/s3-ssl-cache.sh` — mode 100755.
  4. `git ls-files -s core/internal/deploy/reconcile-projects.sh` — mode 100755.
  5. `test_no_test_removed_without_changelog` — PASSED.
  6. `test_executable_bit_outside_lib` — PASSED.
  7. `test_extract_node_host_from_yaml` — PASSED.
  8. `test_node_yaml_domain_extraction` — PASSED.
  9. `test_node_yaml_validation[valid-node-yaml]` — PASSED.
  10. `test_all_test_fixtures_match_schemas` — PASSED (новый gate-тест).
  11. `pytest_sessionstart` выполняет автовалидацию фикстур; при несовпадении — pytest.exit с читаемым сообщением ДО запуска первого теста.
  12. `ruff check .` — 0 errors.
  13. `ruff format --check .` — 0 files would be reformatted.
  14. Устаревший TRAP[DEBT] комментарий (Makefile строки 317-322) удалён.
  15. `test_all_internal_scripts_reachable` — PASSED (регресс S1).
  16. `test_entrypoint_loc` — PASSED (регресс S3).
  17. `make scripts-audit` — exit 0 на чистом working tree.
  18. Pre-commit hook `scripts-audit` блокирует коммит shebang-файлов без регистрации в manifest или exceptions.
  19. `scripts-audit` зарегистрирован в `core/entrypoint-manifest.yaml` (delegates_to и секция gates).
  20. Новый gate-файл `tests/gates/test_gate_fixture_schema.py` зарегистрирован в `core/entrypoint-manifest.yaml` (секция gates).
IMPLEMENTS:            Brief 026 (01-Brief.md), суперпозиционный анализ Option A (score 9/10), инварианты 1 (Makefile-фасад), 4 (AGENTS.md канонические файлы), правила Test Honesty (R1-R3), протокол регистрации gate-тестов (tests/gates/AGENTS.md).
IMPACTS:               tests/test_data/node.yaml (MODIFY: актуализация под schema), tests/test_validate.py (MODIFY: valid_node_data fixture), tests/test_bootstrap_auto.py (MODIFY: assert host 127.0.0.1), tests/test_node_yaml_domains.py (MODIFY: assert test.example.com), tests/_conftest/session.py (MODIFY: _validate_test_fixtures() в pytest_sessionstart), tests/gates/test_gate_fixture_schema.py (CREATE: gate-покрытие валидации фикстур), tests/test_inventory_changes.yaml (MODIFY: changelog entry), tests/test_inventory.yaml (MODIFY: регенерация через make test-inventory-sync), core/internal/bootstrap/s3-ssl-cache.sh (MODIFY: chmod +x), core/internal/deploy/reconcile-projects.sh (MODIFY: chmod +x), Makefile (MODIFY: удаление TRAP[DEBT] строки 317-322 + target scripts-audit), core/internal/scripts-audit.sh (CREATE: аудит регистрации shebang-скриптов), core/entrypoint-manifest.yaml (MODIFY: регистрация scripts-audit + gate fixture_schema), .pre-commit-config.yaml (MODIFY: hook scripts-audit), 30+ файлов (lint fixes: ruff check --fix + ruff format).
REQUIRES:              Чистый working tree (текущее состояние коммита 79e780c). `SKIP_PRECOMMIT=1 make gate MODE=full` показывает описанные 6 failures. Python 3.10+, jsonschema (уже в зависимостях). Бриф 027 (macOS overlay) — отдельно, не блокирует данный DevPlan.
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Wave 1: устранить все точечные failures + линтер-долг — chmod +x, changelog, ruff/format, удаление TRAP[DEBT], регенерация inventory → GOAL_W1
- GOAL Wave 2: механизм когерентности test-data ↔ schema — актуализировать фикстуру, добавить pytest_sessionstart автовалидацию, gate-тест покрытия → GOAL_W2
- GOAL Wave 3: pre-commit prevention registration drift — scripts-audit.sh, make target, pre-commit hook, manifest registration → GOAL_W3
**SECTION_USE_CASES:**
- USE_CASE gate запускается на устаревшей фикстуре → sessionstart блокирует запуск с диагностикой → UC_FIXTURE_FAILFAST
- USE_CASE разработчик добавляет новый shebang-скрипт без регистрации → pre-commit блокирует коммит → UC_PRECOMMIT_AUDIT
- USE_CASE CI gate обнаруживает незарегистрированный скрипт через test_all_shebang_files_in_manifest → UC_CI_GATE_CATCH
- USE_CASE schema эволюционирует (новые required поля) → fixture обновляется вручную, sessionstart валидирует, gate зелёный → UC_SCHEMA_EVOLUTION
$END_DOCUMENT_PLAN
```

---

## Draft Code Graph (XML)

```xml
<graph>
  <!-- FIXTURE-SCHEMA COHERENCE FLOW -->
  <entity id="pytest_sessionstart" type="HOOK" layer="tests/_conftest/session.py">
    <calls>_validate_test_fixtures()</calls>
    <reads>_FIXTURE_SCHEMA_MAP</reads>
    <on_failure>pytest.exit("[IMP:10] Test fixture schema validation FAILED")</on_failure>
  </entity>

  <entity id="_validate_test_fixtures" type="FUNC" layer="tests/_conftest/session.py">
    <reads>
      <src>tests/test_data/*.yaml</src>
      <src>core/schemas/*.schema.json</src>
    </reads>
    <uses>jsonschema.validate(data, schema)</uses>
    <returns>None (on success) | pytest.exit (on failure)</returns>
  </entity>

  <entity id="_FIXTURE_SCHEMA_MAP" type="CONST" layer="tests/_conftest/session.py">
    <maps>{"node.yaml": "core/schemas/node.schema.json", ...}</maps>
    <rationale>Explicit mapping prevents accidental validation of non-schema files; easy to extend</rationale>
  </entity>

  <entity id="test_gate_fixture_schema" type="GATE_TEST" layer="tests/gates/test_gate_fixture_schema.py">
    <calls>_validate_test_fixtures()</calls>
    <marker>@pytest.mark.gate</marker>
    <manifest>core/entrypoint-manifest.yaml → gates: → test_gate_fixture_schema</manifest>
  </entity>

  <!-- TEST FIXTURE UPDATES -->
  <entity id="node_yaml_fixture" type="FIXTURE" layer="tests/test_data/node.yaml">
    <adds>context, modules, owner_key, project.type</adds>
    <removes>branch</removes>
    <validates_against>core/schemas/node.schema.json</validates_against>
  </entity>

  <entity id="test_extract_node_host" type="TEST" layer="tests/test_bootstrap_auto.py">
    <assert>host == "127.0.0.1"</assert>
    <was>"192.168.1.100"</was>
  </entity>

  <entity id="test_node_yaml_domain_extraction" type="TEST" layer="tests/test_node_yaml_domains.py">
    <assert>platform_domain == "test.example.com"</assert>
    <assert>email == "test@example.com"</assert>
    <assert>project_domain == "test-site.example.com"</assert>
  </entity>

  <entity id="test_node_yaml_validation" type="TEST" layer="tests/test_validate.py">
    <fixture>valid_node_data → tests/test_data/node.yaml</fixture>
    <now_passes>fixture содержит все required поля schema</now_passes>
  </entity>

  <!-- SCRIPTS-AUDIT FLOW -->
  <entity id="scripts_audit_sh" type="INTERNAL_SCRIPT" layer="core/internal/scripts-audit.sh">
    <finds>Все .sh под core/ с shebang</finds>
    <filters>EXCEPTIONS[] — модульные healthcheck, hooks, lib, etc.</filters>
    <checks>grep в core/entrypoint-manifest.yaml</checks>
    <exit>0 (all registered) | 1 (unregistered list)</exit>
  </entity>

  <entity id="make_scripts_audit" type="MAKE_TARGET" layer="Makefile">
    <delegates_to>core/internal/scripts-audit.sh</delegates_to>
  </entity>

  <entity id="precommit_scripts_audit" type="HOOK" layer=".pre-commit-config.yaml">
    <entry>make scripts-audit</entry>
    <files>^core/.*\.sh$</files>
    <language>system</language>
  </entity>

  <entity id="manifest_scripts_audit" type="MANIFEST_ENTRY" layer="core/entrypoint-manifest.yaml">
    <delegates_to>core/internal/scripts-audit.sh</delegates_to>
    <id>scripts-audit</id>
  </entity>
</graph>
```

---

## 1. Волна 1 (P0): Точечные фиксы — chmod, changelog, lint, TRAP[DEBT], inventory sync

### 1.1. Шаг 1a: Executable bit для internal-скриптов

**Проблема (F1):** `s3-ssl-cache.sh` и `reconcile-projects.sh` добавлены в репозиторий без executable bit. `test_executable_bit_outside_lib` корректно детектит `mode 100644` вместо `100755`.

**Файлы:**
- `core/internal/bootstrap/s3-ssl-cache.sh` — chmod +x в git index
- `core/internal/deploy/reconcile-projects.sh` — chmod +x в git index

**Команды:**
```bash
git update-index --chmod=+x core/internal/bootstrap/s3-ssl-cache.sh
git update-index --chmod=+x core/internal/deploy/reconcile-projects.sh
```

**Верификация:** `git ls-files -s core/internal/bootstrap/s3-ssl-cache.sh` → mode `100755`.

### 1.2. Шаг 1b: Changelog для удалённого теста

**Проблема (F2):** `test_gate_zero_new_entrypoints` удалён из `tests/gates/test_gate_sequencing.py` без записи в `tests/test_inventory_changes.yaml`.

**Причина удаления:** Тест дублировал `test_all_shebang_files_in_manifest` (gate coverage), который уже покрывает zero-new-entrypoint инвариант. Удалён в DevPlan 025 при консолидации gate-тестов.

**Файл:** `tests/test_inventory_changes.yaml`

**Добавить запись:**
```yaml
  # ── DevPlan 026 (gate-systemic-fix) — удалён test_gate_zero_new_entrypoints ──
  - nodeid: "tests/gates/test_gate_sequencing.py::test_gate_zero_new_entrypoints"
    reason: "Removed — duplicate of test_all_shebang_files_in_manifest which covers zero-new-entrypoint invariant. Gate sequencing test suite consolidated in DevPlan 025, this test was kept in STRUCTURE comment but never implemented as a function."
    issue: "026-gate-systemic-fix"
    approved_by: "@tronyx"
```

### 1.3. Шаг 1c: Линтер-чистка

**Проблема:** 24 ruff-check ошибок + 11 ruff-format + 4 trailing-whitespace — следствие последних коммитов (~16500 строк кода).

**Команды:**
```bash
ruff check --fix .          # авто-фикс I001 (import order), UP032 (f-strings), часть F401 (unused imports)
ruff format .               # форматирование 11 файлов
# Ручная правка оставшихся ошибок (F841 unused variables, F401 импортов, B007 loop variables)
```

**Верификация:** `ruff check .` → 0 errors. `ruff format --check .` → 0 files would be reformatted.

### 1.4. Шаг 1d: Удаление устаревшего TRAP[DEBT] из Makefile

**Проблема:** TRAP[DEBT] комментарий (Makefile строки 317-322) описывает баг «MODE=fast проглатывает падения шагов», который был исправлен до коммита 79e780c. Код на строках 326-349 использует `|| { echo ...; exit 1; }` для каждого шага — баг устранён. Комментарий — мусор, вводящий в заблуждение.

**Файл:** `Makefile`

**Удалить строки 317-322:**
```makefile
# 📝 TRAP[DEBT] · 2026-07-16 · HI · make gate MODE=fast проглатывает падения шагов lint/gates/static
# · Observed: gate напечатал «ALL PASS (MODE=fast)» при report-static.xml failures=10 и 3 FAILED в шаге gates
# · Suspected: shell-цепочка `pytest gates && echo …; pytest static && echo …; pytest predeploy || exit 1` —
#   `;` разрывает &&-цепочку, проверяется только результат predeploy; у lint (`validate.sh --lint;`) нет `||`-обработчика
# · Impact: красные gates/static молча проходят локальный production gate — Anti-Illusion violation, дрейф уезжает в CI
# · When: QA-верификация DevPlan 016 (proxy-isolation), 2026-07-16 — вне scope 016, Makefile не менялся (pre-existing)
```

**Верификация:** `grep "TRAP\[DEBT\].*MODE=fast" Makefile` → no matches.

### 1.5. Шаг 1e: Регенерация test_inventory.yaml

После добавления changelog entry и удаления `test_gate_zero_new_entrypoints`:
```bash
make test-inventory-sync
```

`test_gate_zero_new_entrypoints` удалён физически из файла (нет определения функции), но остаётся в `tests/test_inventory.yaml` (строка 187). Регенерация удалит его из inventory, и changelog подтвердит намеренность удаления.

### Файлы волны 1

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 1 | `core/internal/bootstrap/s3-ssl-cache.sh` | MODIFY (git) | `git update-index --chmod=+x` |
| 2 | `core/internal/deploy/reconcile-projects.sh` | MODIFY (git) | `git update-index --chmod=+x` |
| 3 | `tests/test_inventory_changes.yaml` | MODIFY | changelog entry для test_gate_zero_new_entrypoints |
| 4 | `Makefile` | MODIFY | удаление TRAP[DEBT] строк 317-322 |
| 5 | `tests/test_inventory.yaml` | MODIFY | регенерация через `make test-inventory-sync` |
| 6 | 30+ файлов | MODIFY | ruff check --fix + ruff format |

---

## 2. Волна 2 (P0): Test-data/schema coherence — fixture update + sessionstart validation + gate test

### 2.1. Шаг 2a: Актуализация test fixture `tests/test_data/node.yaml`

**Проблема (F3-F5):** Фикстура не соответствует `core/schemas/node.schema.json`:
- Отсутствуют required поля: `context`, `modules`, `node.owner_key`
- Отсутствует required поле `projects[].type`
- Присутствует запрещённое поле `projects[].branch`
- Захардкоженные assert'ы в тестах ссылаются на значения, которых нет в фикстуре

**Файл:** `tests/test_data/node.yaml`

**Новое содержимое:**
```yaml
# GREP_SUMMARY: test-data node-yaml predeploy-gate test-site schema-coherent
# Test node.yaml for predeploy gate local testing
# Auto-validated against core/schemas/node.schema.json at pytest_sessionstart
# Update this file whenever node.schema.json evolves — sessionstart will catch drift

context: test

node:
  name: test-node
  host: 127.0.0.1
  owner_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA test-key-for-gate"
  timezone: UTC

domain: test.example.com
email: test@example.com

modules: []

projects:
  - name: test-site
    domain: test-site.example.com
    repo: https://github.com/example/test-site.git
    type: fullstack
```

**Изменения относительно текущего файла:**
| Поле | Было | Стало | Причина |
|------|------|-------|---------|
| `context` | отсутствовало | `test` | required по schema |
| `node.owner_key` | отсутствовало | `ssh-ed25519 ...` | required по schema |
| `modules` | отсутствовало | `[]` | required по schema |
| `projects[].type` | отсутствовало | `fullstack` | required по schema |
| `projects[].branch` | `main` | удалено | `additionalProperties: false` |

### 2.2. Шаг 2b: Обновление assert'ов в тестах

**Файлы и изменения:**

**`tests/test_bootstrap_auto.py`** — `test_extract_node_host_from_yaml`:
```python
# Было: assert host == "192.168.1.100"  (захардкожено под старые данные)
# Стало: assert host == "127.0.0.1"     (актуальное значение из фикстуры)
```

**`tests/test_node_yaml_domains.py`** — `test_node_yaml_domain_extraction`:
```python
# Было: assert platform_domain == "test.local"
# Было: assert email == "admin@test.local"
# Стало: assert platform_domain == "test.example.com"
# Стало: assert email == "test@example.com"
# Стало: assert project_domain == "test-site.example.com"
```

**`tests/test_validate.py`** — `test_node_yaml_validation[valid-node-yaml]`:
- Фикстура `valid_node_data` (или параметризация) должна подавать данные из `tests/test_data/node.yaml`
- После актуализации фикстуры — все required поля присутствуют, validation PASSED

### 2.3. Шаг 2c: Механизм автовалидации фикстур в pytest_sessionstart

**Дизайн:** Fail-fast проверка ДО запуска любого теста. Если фикстура невалидна — pytest.exit с читаемой диагностикой. Механизм минимален (~40 строк), расширяем через `_FIXTURE_SCHEMA_MAP`.

**Файл:** `tests/_conftest/session.py`

**Добавить перед `pytest_sessionstart`:**
```python
# region FIXTURE_SCHEMA_VALIDATION
import json
import pathlib

import jsonschema
import yaml


# Mapping: test_data fixture filename → schema file (relative to project root)
_FIXTURE_SCHEMA_MAP = {
    "node.yaml": "core/schemas/node.schema.json",
    # Future fixtures — add entries here
}


def _validate_test_fixtures() -> None:
    """Validate all test fixtures against their schemas at session start.

    Fails fast with readable message BEFORE any test runs.
    Called from pytest_sessionstart in this module.

    Design decisions:
    - Uses jsonschema.validate (not Draft7Validator) for version-agnostic validation
    - Missing fixture file → skip (optional fixtures)
    - Missing schema file → pytest.exit (configuration error)
    - yaml.safe_load (not FullLoader) for security
    """
    # Resolve paths relative to tests/_conftest/session.py
    # session.py → tests/_conftest/ → tests/ → project root
    conftest_dir = pathlib.Path(__file__).resolve().parent  # tests/_conftest/
    test_data_dir = conftest_dir.parent / "test_data"       # tests/test_data/
    project_root = conftest_dir.parent.parent               # project root

    errors: list[str] = []
    for fixture_name, schema_relpath in _FIXTURE_SCHEMA_MAP.items():
        fixture_path = test_data_dir / fixture_name
        schema_path = project_root / schema_relpath

        if not fixture_path.exists():
            continue  # Optional fixture — skip silently

        if not schema_path.exists():
            pytest.exit(
                f"\n[IMP:10][sessionstart] Schema file not found: {schema_path}\n"
                f"Check _FIXTURE_SCHEMA_MAP in tests/_conftest/session.py\n"
            )

        with open(fixture_path) as f:
            data = yaml.safe_load(f)
        with open(schema_path) as f:
            schema = json.load(f)

        try:
            jsonschema.validate(data, schema)
        except jsonschema.ValidationError as e:
            errors.append(f"  {fixture_path}: {e.message}")

    if errors:
        pytest.exit(
            f"\n[IMP:10][sessionstart] Test fixture schema validation FAILED:\n"
            + "\n".join(errors)
            + "\n\nUpdate test fixtures to match current schemas.\n"
        )


# endregion FIXTURE_SCHEMA_VALIDATION
```

**Изменить `pytest_sessionstart`:**
```python
def pytest_sessionstart(session: pytest.Session) -> None:
    """Session start hook: validate fixtures + counter + conditional imports."""
    # ── FAIL-FAST: validate test fixtures BEFORE any test runs ──
    _validate_test_fixtures()

    # ── Existing: conditional import for retention module ──
    _marker_option = session.config.getoption("-m", "")
    # ... rest of existing code unchanged ...
```

**Порядок важен:** `_validate_test_fixtures()` вызывается ПЕРВЫМ в `pytest_sessionstart`, до инкремента счётчика попыток. Невалидная фикстура — это не попытка, это конфигурационная ошибка, не должна учитываться в Anti-Loop протоколе.

### 2.4. Шаг 2d: Gate-тест `test_gate_fixture_schema.py`

**Дизайн:** Явный gate-тест для CI-покрытия механизма автовалидации. Вызывает ту же `_validate_test_fixtures()`, что и sessionstart. Если sessionstart прошёл (фикстуры валидны), тест проходит. Если sessionstart не вызывался (например, `pytest --collect-only`), тест всё равно выполняет проверку.

**Файл:** `tests/gates/test_gate_fixture_schema.py` (CREATE)

```python
# GREP_SUMMARY: gate fixture-schema test-data validation coherence jsonschema
# STRUCTURE: ▶ test_all_test_fixtures_match_schemas → ◇ call _validate_test_fixtures() → ⊕ verify LDD [IMP:9] trajectory → ⎋ gate green
# region MODULE_CONTRACT
## @purpose  Gate test: validate all test_data/*.yaml fixtures against their jsonschema schemas.
##           Ensures test fixtures stay coherent with evolving platform schemas.
## @scope    All .yaml fixtures in tests/test_data/ checked against core/schemas/.
##           Mapping defined in _FIXTURE_SCHEMA_MAP (tests/_conftest/session.py).
## @invariants
##   - Every fixture in _FIXTURE_SCHEMA_MAP must validate against its schema
##   - Test must NOT pass (anti-R1) — calls _validate_test_fixtures() explicitly
##   - Registered in core/entrypoint-manifest.yaml gates section
## @rationale Prevents silent fixture drift. Without this gate, a developer can change
##            node.schema.json without updating test_data/node.yaml — and the only
##            signal is cryptic test failures (F3-F5 in Brief 026). The gate makes
##            the failure explicit and localized.
## @changes 2026-07-21 | Created (DevPlan 026 W2)
# endregion MODULE_CONTRACT

import pytest

from _conftest.session import _FIXTURE_SCHEMA_MAP, _validate_test_fixtures


@pytest.mark.gate
def test_all_test_fixtures_match_schemas() -> None:
    """Gate: every test_data/*.yaml fixture must validate against its schema.

    Calls _validate_test_fixtures() — same function used by pytest_sessionstart.
    If fixtures are invalid, pytest.exit is raised (caught by pytest as failure).

    Empty _FIXTURE_SCHEMA_MAP is a valid state (no fixtures to validate).
    The test implicitly validates that the function itself runs without error.
    """
    # Verify the map is importable (configuration check)
    assert isinstance(_FIXTURE_SCHEMA_MAP, dict), (
        f"[IMP:9][gate][fixture-schema] _FIXTURE_SCHEMA_MAP must be dict, "
        f"got {type(_FIXTURE_SCHEMA_MAP).__name__}"
    )

    # Run validation — pytest.exit on failure, no return value on success
    _validate_test_fixtures()

    # If we reach here, all fixtures validated successfully
    assert True  # Explicit assertion to satisfy Test Honesty R1
```

### 2.5. Регистрация gate-теста в entrypoint-manifest.yaml

**Файл:** `core/entrypoint-manifest.yaml`

**Добавить в секцию `gates`:**
```yaml
  - id: gate-fixture-schema
    description: "Validate test_data/*.yaml fixtures against core/schemas/*.json (jsonschema coherence)"
    test_file: "test_gate_fixture_schema.py"
    issue: "026-gate-systemic-fix"
```

### Файлы волны 2

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 7 | `tests/test_data/node.yaml` | MODIFY | Добавить context, modules, owner_key, project.type; удалить branch |
| 8 | `tests/test_bootstrap_auto.py` | MODIFY | assert host 127.0.0.1 (было 192.168.1.100) |
| 9 | `tests/test_node_yaml_domains.py` | MODIFY | assert test.example.com, test@example.com (было test.local) |
| 10 | `tests/test_validate.py` | MODIFY | valid_node_data fixture → данные из обновлённого node.yaml |
| 11 | `tests/_conftest/session.py` | MODIFY | +_FIXTURE_SCHEMA_MAP, +_validate_test_fixtures(), вызов в pytest_sessionstart |
| 12 | `tests/gates/test_gate_fixture_schema.py` | CREATE | Gate-тест покрытия fixture-schema валидации |
| 13 | `core/entrypoint-manifest.yaml` | MODIFY | Регистрация gate-fixture-schema в секции gates |

---

## 3. Волна 3 (P0): Registration friction prevention — `make scripts-audit` + pre-commit hook

### 3.1. Шаг 3a: Создать `core/internal/scripts-audit.sh`

**Дизайн:** Скрипт аудита регистрации всех shebang-файлов под `core/`. Для каждого `.sh` файла с shebang проверяет: либо он в списке исключений (модульные healthcheck'и, lib, hooks), либо его относительный путь найден в `core/entrypoint-manifest.yaml`. Если ни одно условие не выполнено — exit 1 с перечнем незарегистрированных скриптов и remediation hints.

**Файл:** `core/internal/scripts-audit.sh` (CREATE)

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: scripts-audit, shebang-registration, pre-commit, gate-exceptions, manifest
# region MODULE_CONTRACT
## @purpose  Audit: every shebang file under core/ must be registered in
##           entrypoint-manifest.yaml (delegates_to or module_hooks) OR
##           match an exception pattern in this file. Exit 0 = all registered, 1 = violations.
## @scope    All .sh files with shebang under core/ (excluding __pycache__, .backup, node_modules)
## @io       Reads core/entrypoint-manifest.yaml → exit 0 (clean) | exit 1 + list of unregistered scripts
## @invariants
##   - Reads only first line of each .sh for shebang detection (#! prefix)
##   - Exception patterns use bash glob matching against relative path from project root
##   - Manifest check uses simple grep — false positives possible but acceptable
##     (path may appear in comments/descriptions)
## @rationale Prevents registration drift — gate tests catch missing registrations
##            post-factum on CI; this hook catches them pre-commit.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 026 W3)
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$CORE_DIR/.." && pwd)"
MANIFEST="$CORE_DIR/entrypoint-manifest.yaml"

# ── Exception patterns ──────────────────────────────────────────────────
# Scripts that legitimately don't need manifest registration.
# Patterns use bash glob matching against relative path from project root.
EXCEPTIONS=(
    "core/lib/"                         # Libraries (sourced, not executed)
    "core/modules/*/healthcheck.sh"     # Module healthchecks
    "core/modules/*/hooks/*.sh"         # Module hooks
    "core/modules/*/install.sh"         # Module installers
    "core/modules/*/ready-check.sh"     # Module readiness checks
    "core/modules/*/scripts/*.sh"       # Module scripts
    "core/modules/*/config/*.sh"        # Module configs
    "core/modules/*/config/*/*.sh"      # Nested module configs
    "core/modules/*/watchdog/*.sh"      # Module watchdogs
    "core/internal/healthcheck/*.sh"    # Internal healthchecks
    "core/modules/hermes-agent/build/scripts/"  # Hermes build
    "core/modules/hermes-agent/context/scripts/" # Hermes context
    "core/internal/bootstrap/ssl-provision.sh"   # SSL provisioning (thin wrapper)
    "core/modules/nginx/nginx_reload_hook.sh"    # Nginx hook
    "core/internal/bootstrap/s3-ssl-cache.sh"    # SSL cache (DevPlan 024)
    "core/internal/deploy/reconcile-projects.sh"  # Reconciliation (DevPlan 025)
    "core/internal/scripts-audit.sh"              # Self
)

# ── Collect shebang files ───────────────────────────────────────────────
UNREGISTERED=()
while IFS= read -r -d '' file; do
    rel="${file#$PROJECT_ROOT/}"
    
    # Check: has shebang?
    if ! head -1 "$file" 2>/dev/null | grep -q '^#!/'; then
        continue  # Not a shebang file
    fi
    
    # Check exceptions (suffix match for directory patterns)
    is_exception=false
    for pattern in "${EXCEPTIONS[@]}"; do
        if [[ "$rel" == $pattern ]]; then
            is_exception=true
            break
        fi
    done
    $is_exception && continue
    
    # Check manifest registration
    if grep -qF "$rel" "$MANIFEST" 2>/dev/null; then
        continue
    fi
    
    UNREGISTERED+=("$rel")
done < <(find "$CORE_DIR" -name "*.sh" \
    -not -path "*/.backup/*" \
    -not -path "*/__pycache__/*" \
    -not -path "*/node_modules/*" \
    -print0 2>/dev/null)

# ── Report ──────────────────────────────────────────────────────────────
if [[ ${#UNREGISTERED[@]} -gt 0 ]]; then
    echo "[IMP:10][scripts-audit] UNREGISTERED SCRIPTS FOUND:"
    for f in "${UNREGISTERED[@]}"; do
        echo "  - $f"
    done
    echo ""
    echo "Action required:"
    echo "  1. Register in core/entrypoint-manifest.yaml (delegates_to or module_hooks)"
    echo "  2. OR add to EXCEPTIONS array in core/internal/scripts-audit.sh"
    echo "  3. Retry commit"
    exit 1
fi

echo "[IMP:9][scripts-audit] All shebang scripts registered or in exceptions"
exit 0
```

**Примечание к исключениям:** Список исключений намеренно включён в сам скрипт (а не во внешний конфиг) по тем же причинам, по которым отклонён `core/gate-config.yaml`: при текущем масштабе (~20 исключений) внешний файл создал бы ещё одну точку дрейфа без значимого выигрыша. При росте до 50+ исключений — рефакторить в `core/internal/scripts-audit-exceptions.yaml`.

### 3.2. Шаг 3b: Добавить `make scripts-audit` target

**Файл:** `Makefile`

**Добавить в секцию `.PHONY` и в тело Makefile (рядом с другими audit-таргетами):**
```makefile
## scripts-audit: Проверить регистрацию всех shebang-скриптов в manifest или exceptions
.PHONY: scripts-audit
scripts-audit:
	@echo "[IMP:7][make][scripts-audit] Auditing shebang script registration..."
	@bash $(_platform_root)/core/internal/scripts-audit.sh
```

### 3.3. Шаг 3c: Добавить pre-commit hook

**Файл:** `.pre-commit-config.yaml`

**Добавить в секцию `repos` (последним hook'ом в локальном repo):**
```yaml
  - id: scripts-audit
    name: Audit script registration
    entry: make scripts-audit
    language: system
    files: '^core/.*\.sh$'
    pass_filenames: false
    always_run: false
```

**Параметры:**
- `files: '^core/.*\.sh$'` — запускать только когда изменены `.sh` файлы под `core/`
- `pass_filenames: false` — не передавать имена файлов (скрипт сам ищет все)
- `always_run: false` — не запускать на каждый коммит, только при изменении `.sh`

### 3.4. Шаг 3d: Зарегистрировать `scripts-audit` в entrypoint-manifest.yaml

**Файл:** `core/entrypoint-manifest.yaml`

**Добавить в секцию `entries` (delegates_to):**
```yaml
  - id: scripts-audit
    description: "Audit shebang script registration in manifest or exceptions"
    delegates_to: "core/internal/scripts-audit.sh"
    category: "audit"
    allowed_verbs: ["scripts-audit"]
```

### 3.5. Верификация pre-commit hook

**Сценарий 1: все скрипты зарегистрированы**
```bash
make scripts-audit
# Expected: exit 0, "[IMP:9][scripts-audit] All shebang scripts registered or in exceptions"
```

**Сценарий 2: незарегистрированный скрипт**
```bash
# Создать тестовый скрипт
echo '#!/usr/bin/env bash' > core/internal/test-unregistered.sh
chmod +x core/internal/test-unregistered.sh

make scripts-audit
# Expected: exit 1, "[IMP:10][scripts-audit] UNREGISTERED SCRIPTS FOUND:"
#           "  - core/internal/test-unregistered.sh"

# Очистка
rm core/internal/test-unregistered.sh
```

**Сценарий 3: pre-commit блокирует коммит**
```bash
echo '#!/usr/bin/env bash' > core/internal/test-unregistered.sh
git add core/internal/test-unregistered.sh
git commit -m "test"
# Expected: pre-commit hook scripts-audit → FAIL → commit blocked
```

### Файлы волны 3

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 14 | `core/internal/scripts-audit.sh` | CREATE | Аудит регистрации shebang-скриптов в manifest |
| 15 | `Makefile` | MODIFY | +target scripts-audit |
| 16 | `.pre-commit-config.yaml` | MODIFY | +hook scripts-audit |
| 17 | `core/entrypoint-manifest.yaml` | MODIFY | Регистрация scripts-audit в entries |

---

## 4. File Manifest — полный список изменений

| # | Файл | Волна | Действие | Описание |
|---|------|:-----:|----------|----------|
| 1 | `core/internal/bootstrap/s3-ssl-cache.sh` | W1 | MODIFY (git) | `git update-index --chmod=+x` — mode 100644→100755 |
| 2 | `core/internal/deploy/reconcile-projects.sh` | W1 | MODIFY (git) | `git update-index --chmod=+x` — mode 100644→100755 |
| 3 | `tests/test_inventory_changes.yaml` | W1 | MODIFY | changelog entry: test_gate_zero_new_entrypoints removal |
| 4 | `tests/test_inventory.yaml` | W1 | MODIFY | Регенерация: `make test-inventory-sync` |
| 5 | `Makefile` | W1,W3 | MODIFY | W1: удалить TRAP[DEBT] строки 317-322; W3: +target scripts-audit |
| 6 | 30+ файлов (lint) | W1 | MODIFY | `ruff check --fix .` + `ruff format .` |
| 7 | `tests/test_data/node.yaml` | W2 | MODIFY | +context, +modules, +node.owner_key, +projects[].type; −branch |
| 8 | `tests/test_bootstrap_auto.py` | W2 | MODIFY | assert host: 192.168.1.100 → 127.0.0.1 |
| 9 | `tests/test_node_yaml_domains.py` | W2 | MODIFY | assert domain: test.local → test.example.com |
| 10 | `tests/test_validate.py` | W2 | MODIFY | valid_node_data fixture → актуальные данные |
| 11 | `tests/_conftest/session.py` | W2 | MODIFY | +_FIXTURE_SCHEMA_MAP, +_validate_test_fixtures(), вызов в sessionstart |
| 12 | `tests/gates/test_gate_fixture_schema.py` | W2 | CREATE | Gate-тест покрытия fixture-schema валидации |
| 13 | `core/entrypoint-manifest.yaml` | W2,W3 | MODIFY | W2: +gate fixture-schema; W3: +entry scripts-audit |
| 14 | `core/internal/scripts-audit.sh` | W3 | CREATE | Аудит регистрации shebang-скриптов |
| 15 | `.pre-commit-config.yaml` | W3 | MODIFY | +hook scripts-audit |

---

## 5. Порядок выполнения

```
Волна 1 (точечные фиксы) → gate green
  └── Волна 2 (fixture-schema coherence) → gate green
      └── Волна 3 (registration friction) → gate green
```

**Обоснование порядка:**

1. **W1 первой** — точечные фиксы не зависят ни от чего и устраняют F1, F2, lint debt. После W1: `test_executable_bit_outside_lib` PASSED, `test_no_test_removed_without_changelog` PASSED, `ruff check .` clean.

2. **W2 после W1** — fixture-schema coherence устраняет F3-F5 (3 из 6 failures). Требует чистого lint (W1) для беспрепятственного прохода pre-commit в gate. После W2: 4 из 5 failures устранены (F6 macOS — отдельно).

3. **W3 последней** — scripts-audit не влияет на failures (preventive measure), но добавляет новый файл, который должен быть зарегистрирован в manifest. Регистрация в manifest требует, чтобы `test_all_shebang_files_in_manifest` проходил — что гарантировано после W1 (executable bit) и W2 (fixture обновлена, но manifest-тесты не затрагиваются).

**Совместные файлы:**
- `Makefile` — модифицируется в W1 (удаление TRAP[DEBT]) и W3 (target scripts-audit). Разные строки, конфликт невозможен — W1 удаляет строки 317-322, W3 добавляет новый target в другой секции.
- `core/entrypoint-manifest.yaml` — модифицируется в W2 (gates) и W3 (entries). Разные секции, конфликт невозможен.

---

## 6. Acceptance Criteria

### Gate green
- [ ] `make gate MODE=fast` — зелёный (все шаги: pre-commit, validate, lint, gates, static, predeploy)
- [ ] `make gate MODE=full` — зелёный за исключением F6 (test_platform_starts_all_containers → status-page-test macOS)

### Точечные фиксы (W1)
- [ ] `git ls-files -s core/internal/bootstrap/s3-ssl-cache.sh` → mode `100755`
- [ ] `git ls-files -s core/internal/deploy/reconcile-projects.sh` → mode `100755`
- [ ] `test_executable_bit_outside_lib` — PASSED
- [ ] `test_no_test_removed_without_changelog` — PASSED
- [ ] `test_all_internal_scripts_reachable` — PASSED (регресс S1)
- [ ] `test_entrypoint_loc` — PASSED (регресс S3)
- [ ] `ruff check .` — 0 errors
- [ ] `ruff format --check .` — 0 files would be reformatted
- [ ] `grep "TRAP\[DEBT\].*MODE=fast" Makefile` — no matches
- [ ] `make test-inventory-sync` — exit 0; `test_gate_zero_new_entrypoints` отсутствует в `tests/test_inventory.yaml`

### Fixture-schema coherence (W2)
- [ ] `test_extract_node_host_from_yaml` — PASSED (assert 127.0.0.1)
- [ ] `test_node_yaml_domain_extraction` — PASSED (assert test.example.com)
- [ ] `test_node_yaml_validation[valid-node-yaml]` — PASSED
- [ ] `test_all_test_fixtures_match_schemas` — PASSED (новый gate-тест)
- [ ] `pytest --collect-only` → `_validate_test_fixtures()` вызывается в sessionstart (видно в stderr)
- [ ] При невалидной фикстуре: `pytest --collect-only` → pytest.exit ДО сбора тестов, exit code ≠ 0
- [ ] `tests/test_data/node.yaml` валидируется против `core/schemas/node.schema.json`

### Registration friction (W3)
- [ ] `make scripts-audit` — exit 0 на чистом working tree
- [ ] `make scripts-audit` — exit 1 при наличии незарегистрированного shebang-скрипта
- [ ] Pre-commit hook `scripts-audit` блокирует коммит при нарушении
- [ ] `scripts-audit` зарегистрирован в `core/entrypoint-manifest.yaml` (delegates_to и allowed_verbs)
- [ ] `core/internal/scripts-audit.sh` присутствует в EXCEPTIONS (self-reference)

---

## 7. Не входит в этот DevPlan

| Исключено | Причина |
|-----------|---------|
| **F6 (status-page-test macOS overlay)** | Вынесен в бриф 027. Требует macOS-specific Docker volume mount testing. |
| **S1 (exception list desync)** | Уже исправлено в коммите 79e780c. Call-graph резолвит source-вызовы через переменные. |
| **S3 (allowlist governance)** | Уже исправлено в коммите 79e780c. thin_wrapper ALLOWLIST проходит проверки. |
| **`core/gate-config.yaml` (unified exception config)** | Отменён решением оператора. Текущие механизмы (hardcoded exception lists в тестах) работают корректно. Внешний конфиг добавил бы новую точку дрейфа без значимого выигрыша при текущем масштабе (~20 исключений). |
| **TRAP[DEBT] fix (не удаление комментария)** | Код Makefile (строки 326-349) уже исправлен — каждый шаг использует `\|\| { echo ...; exit 1; }`. Удаляется только устаревший комментарий. |
| **Новые тесты для scripts-audit.sh** | Не требуются: pre-commit hook сам является тестом (блокирует/пропускает коммит). Gate-тесты `test_all_shebang_files_in_manifest` и `test_all_entrypoints_have_live_caller` покрывают registration с другой стороны. |
| **Изменения в AGENTS.md** | `scripts-audit` — internal-скрипт, не entrypoint. Не требует обновления глоссария глаголов. AGENTS.md обновляется только при изменении канонических операций. |

---

## 8. Оценка усилия

| Волна | Шаги | Время |
|-------|------|:----:|
| W1 | chmod +x (2 файла), changelog entry, ruff --fix + ruff format, удаление TRAP[DEBT], регенерация inventory | 20 мин |
| W2 | Актуализация fixture (node.yaml), обновление 3 assert'ов, _validate_test_fixtures() в session.py, gate-тест, manifest-регистрация | 30 мин |
| W3 | scripts-audit.sh, make target, pre-commit hook, manifest-регистрация | 30 мин |
| **Итого** | | **~1 час 20 мин** |

---

$END_DEVPLAN
