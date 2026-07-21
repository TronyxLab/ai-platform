# $START — DevPlan 031: YAML JSON Output Fix

<!--
$ARTIFACT_CONTRACT
  PURPOSE: Исправить системный дефект вывода yaml_query.py — Python repr() вместо JSON для dict/list значений
  DESCRIPTION: Однофайловый фикс в core/internal/scripts/yaml_query.py — добавление `elif isinstance(value, (dict, list)): print(json.dumps(value))` перед fallback `else: print(value)`. Восстанавливает контракт yaml_get_field: «Dict/list values serialized as JSON».
  RATIONALE: Drift между контрактом yaml_read.sh («Dict/list values serialized as JSON») и реализацией yaml_query.py (print(value) для dict/list выводит Python repr с одиночными кавычками). Все consumer-ы provision-environment.sh используют json.load() на выходе yaml_get_field → падение pipeline через set -euo pipefail.
  ACCEPTANCE_CRITERIA:
    AC-1: make gate MODE=fast — все тесты test_unit_provision_environment.py::TestProvisionerDryRun и TestProvisionerLDDLogging проходят (9/9, exit 0)
    AC-2: yaml_get_field для списка (напр. networks) возвращает валидный JSON: [{"name": "proxy-net", ...}], не [{'name': 'proxy-net', ...}]
    AC-3: yaml_get_field для скаляров (строка, число) возвращает неизменённое значение (обратная совместимость)
    AC-4: make provision --scope networks --dry-run — exit 0, выводит все 8 имён сетей
  IMPLEMENTS: Fix for post-DevPlan-028 W1-E7 regression (yaml_query.py создан в DevPlan 028 W1-E7, заменил 13+ inline python3 -c "import yaml" вызовов)
  IMPACTS:
    - core/internal/scripts/yaml_query.py — функция _cli(), блок вывода (1 строка кода + TRAP[BUG] комментарий)
    - tests/test_unit_provision_environment.py — без изменений (исправление восстанавливает 9 упавших тестов)
  REQUIRES: Python 3.10+, PyYAML, json (stdlib)
  TASK_SIZE: TRIVIAL (1 файл, 1 строка кода, zero-risk rollback)
-->

# DevPlan 031: YAML JSON Output Fix

## Problem Statement

По результатам `make gate MODE=fast` получено 9 failures в `test_unit_provision_environment.py`.

### Цепочка отказа

```
yaml_query.py --get env_defaults → {'KEY': 'val'}  (Python repr, exit 0)
    ↓
_provision_env: echo "$env_json" | python3 -c "json.load(sys.stdin)"
    ↓  json.JSONDecodeError (single quotes ≠ valid JSON)
    ↓
set -euo pipefail + pipefail → pipeline exit ≠0 → silent script kill (exit 1)
```

### Корневая причина

В `yaml_query.py:_cli()`, строки 181–185 (до фикса):

```python
# Было (до фикса):
if args.json_output:
    print(json.dumps(value))
else:
    print(value)            # ← Для dict/list выводит Python repr: {'key': 'val'}
                            #   вместо JSON: {"key": "val"}
```

Для скаляров (строка, число, bool) `print(value)` идентичен `print(json.dumps(value))` — обратная совместимость сохраняется. Но для dict/list `print(value)` вызывает `__str__()` который использует repr-формат с одиночными кавычками — невалидный JSON.

**Системная ошибка**: `_format_item()` для `--items` корректно использует `json.dumps()` (строка 126), но основной вывод (строка 195 до фикса) — нет. Это drift между двумя ветками вывода в одном файле.

### Affected scope

Все 4 scope provision-environment.sh используют `yaml_get_field` → `yaml_query.py --get` → `json.load()`:

| Scope | Consumer | Механизм отказа |
|-------|----------|----------------|
| `networks` | `_provision_networks` → `while IFS= read` + `json.load(sys.stdin)` | Список сетей парсится как 0 элементов → 0 created, 0 skipped |
| `volumes` | `_provision_volumes` → `while IFS= read` + `json.load(sys.stdin)` | Список томов парсится как 0 элементов → 0 created, 0 skipped |
| `env` | `_provision_env` → `json.load(sys.stdin)` | `json.JSONDecodeError` → `set -e` убивает с exit 1 |
| `profiles` | `_provision_profiles` → `json.load(sys.stdin)` | `json.JSONDecodeError` → `set -e` убивает с exit 1 |

### Затронутые тесты (9 failures)

```
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_scope_networks_dry_run
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_scope_volumes_dry_run
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_scope_env_dry_run
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_scope_all_dry_run
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_scope_profiles_dry_run
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_multi_scope_networks_and_volumes
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_multi_scope_env_and_networks
tests/test_unit_provision_environment.py::TestProvisionerDryRun::test_multi_scope_all_equivalent_to_four_scopes
tests/test_unit_provision_environment.py::TestProvisionerLDDLogging::test_ldd_logs_present
```

Все тесты `TestProvisionerWithDocker` (требуют Docker daemon) также затронуты, но при dry-run = true не проходят по той же причине.

---

## Design Decisions (Superposition Collapse)

| Проблема | Выбранное решение | Отклонённые альтернативы |
|----------|-------------------|------------------------|
| Вывод dict/list | `elif isinstance(value, (dict, list)): print(json.dumps(value))` перед `else: print(value)` | 1. Всегда `json.dumps()` — ломает обратную совместимость для скаляров (напр. вывод строки `"hello"` вместо `hello`) |
| | | 2. Всегда `--json-output` в yaml_read.sh — требует менять 2 consumer-а (`yaml_get_field`, `yaml_get_list`) + все их caller-ы, избыточно |
| | | 3. `json.dumps` с `default=str` — маскирует несериализуемые типы, silent degradation |
| TRAP[BUG] маркер | Добавить в код с датой 2026-07-21 и уровнем HIGH | Пропустить маркер — нарушает принцип Zero-Context Survival (следующий агент не увидит историю бага) |
| Prevention (тест) | Новый unit-тест в test_unit_yaml_query.py: проверка что `yaml_get_field` для списка возвращает валидный JSON | Без теста — регрессия возможна при будущем рефакторинге yaml_query.py |

---

## $TASKS

### Wave 1: Fix yaml_query.py output format — единственная задача

**W1-E1 — Добавить `isinstance(value, (dict, list))` проверку в блок вывода `_cli()`**

Файл: `core/internal/scripts/yaml_query.py`, функция `_cli()`, строки 181–195

**Текущий код** (строка 190–195):
```python
    if args.json_output:
        print(json.dumps(value))
    else:
        print(value)
    return 0
```

**Целевой код** (строка 190–196):
```python
    # ⚠️ TRAP[BUG] · 2026-07-21 · HIGH · yaml_get_field возвращает Python repr вместо JSON для dict/list
    # · Symptom: provision-environment.sh _provision_networks() получает невалидный JSON
    # ·   `[{'name': 'proxy-net', ...}]` вместо `[{"name": "proxy-net", ...}]`
    # ·   → while loop итерирует 0 сетей → docker compose up падает с "network X declared as external"
    # · Root: print(value) выводит Python str() для dict/list, а не json.dumps()
    # · Fix: заменить print(value) на json.dumps(value) при выводе dict/list
    # ·   _format_item() для --items уже правильно использует json.dumps()
    # ·   Проблема только в режиме --get без --items для не-scalar значений
    # · Prevention: unit-тест, проверяющий что yaml_get_field для списка возвращает валидный JSON
    if args.json_output:
        print(json.dumps(value))
    elif isinstance(value, (dict, list)):
        print(json.dumps(value))
    else:
        print(value)
    return 0
```

**Логика**:
- `args.json_output` — явный флаг, поведение не меняется
- `isinstance(value, (dict, list))` — для комплексных типов всегда JSON, даже без флага `--json-output`. Это восстанавливает контракт `yaml_get_field` («Dict/list values serialized as JSON»)
- `else: print(value)` — для скаляров (str, int, float, bool, None) поведение не меняется, обратная совместимость

**W1-E2 — Добавить unit-тест на JSON-валидность вывода yaml_query.py**

Файл: `tests/test_unit_yaml_query.py` (новый)

```python
# GREP_SUMMARY: yaml-query, unit-test, json-output, dict-list, python-repr-regression
# STRUCTURE: test_yaml_get_list_returns_valid_json → test_yaml_get_dict_returns_valid_json → test_yaml_get_scalar_unchanged
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/yaml_query.py JSON output regression prevention
## @scope    Verify that yaml_get for dict/list returns valid JSON, not Python repr
## @invariants
##   - yaml_get for list → valid JSON array
##   - yaml_get for dict → valid JSON object
##   - yaml_get for scalar → unchanged value (no JSON wrapping)
## @rationale  TRAP[BUG] 2026-07-21: print(value) for dict/list output Python repr
##             (single quotes) instead of JSON (double quotes) → broke all
##             provision-environment.sh consumers. Prevention test.
# endregion MODULE_CONTRACT

import json
import pathlib
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

YAML_QUERY_PATH = Path(__file__).resolve().parents[1] / "core" / "internal" / "scripts" / "yaml_query.py"


def _query(file_path: Path, key: str) -> subprocess.CompletedProcess:
    """Run yaml_query.py --get and return subprocess result."""
    return subprocess.run(
        ["python3", str(YAML_QUERY_PATH), "--file", str(file_path), "--get", key],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_yaml_get_list_returns_valid_json(tmp_path: pathlib.Path) -> None:
    """yaml_get for a list must output valid JSON (not Python repr).

    ## @purpose — Prevention for TRAP[BUG] 2026-07-21.
    ## @rationale — Before fix: output was [{'name': 'test-net'}] (Python repr).
    ##              After fix: output must be [{"name": "test-net"}] (JSON).
    """
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
networks:
  - name: proxy-net
    driver: bridge
  - name: shared-db-net
    driver: bridge
""")

    result = _query(yaml_file, "networks")

    assert result.returncode == 0, f"Exit {result.returncode}: stderr={result.stderr[:500]}"

    output = result.stdout.strip()
    # Must be valid JSON
    parsed = json.loads(output)
    assert isinstance(parsed, list), f"Expected JSON array, got {type(parsed).__name__}"
    assert len(parsed) == 2, f"Expected 2 networks, got {len(parsed)}"
    assert parsed[0]["name"] == "proxy-net"
    assert parsed[1]["name"] == "shared-db-net"

    # Must NOT contain Python repr syntax (single quotes around keys/values)
    assert "'" not in output, f"Python repr detected in output (single quotes): {output[:100]}"
    assert '"' in output, f"Expected JSON double quotes: {output[:100]}"


def test_yaml_get_dict_returns_valid_json(tmp_path: pathlib.Path) -> None:
    """yaml_get for a dict must output valid JSON object."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
env_defaults:
  POSTGRES_PASSWORD: test-pg-pwd
  LITELLM_MASTER_KEY: sk-ci-test
""")

    result = _query(yaml_file, "env_defaults")

    assert result.returncode == 0, f"Exit {result.returncode}: stderr={result.stderr[:500]}"

    output = result.stdout.strip()
    parsed = json.loads(output)
    assert isinstance(parsed, dict), f"Expected JSON object, got {type(parsed).__name__}"
    assert parsed["POSTGRES_PASSWORD"] == "test-pg-pwd"

    # Must NOT contain Python repr syntax
    assert "'" not in output, f"Python repr detected in output: {output[:100]}"


def test_yaml_get_scalar_unchanged(tmp_path: pathlib.Path) -> None:
    """yaml_get for a scalar must NOT be JSON-wrapped (backward compat)."""
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
node:
  host: 127.0.0.1
  port: 8080
  enabled: true
""")

    # String scalar — should be bare, not JSON-quoted
    result = _query(yaml_file, "node.host")
    assert result.returncode == 0
    assert result.stdout.strip() == "127.0.0.1", (
        f"Expected bare scalar '127.0.0.1', got: {result.stdout.strip()}"
    )

    # Integer scalar
    result = _query(yaml_file, "node.port")
    assert result.returncode == 0
    assert result.stdout.strip() == "8080", (
        f"Expected bare scalar '8080', got: {result.stdout.strip()}"
    )

    # Boolean scalar
    result = _query(yaml_file, "node.enabled")
    assert result.returncode == 0
    assert result.stdout.strip() == "True", (
        f"Expected bare scalar 'True', got: {result.stdout.strip()}"
    )


def test_yaml_get_list_no_python_repr_single_quotes(tmp_path: pathlib.Path) -> None:
    """Explicit regression test: output must not contain Python repr single quotes.

    ## @purpose — TRAP[BUG] 2026-07-21 specific regression: single quotes in output
    ##            break json.load() consumers.
    ## @rationale — Python repr uses single quotes for strings inside dicts,
    ##              which is not valid JSON. This test catches the exact symptom.
    """
    # Minimal reproduction of the exact platform-env.yaml networks structure
    yaml_file = tmp_path / "test.yaml"
    yaml_file.write_text("""
networks:
  - name: proxy-net
    driver: bridge
    internal: false
""")

    result = _query(yaml_file, "networks")
    assert result.returncode == 0

    output = result.stdout.strip()
    # Attempt json.load — must succeed
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as e:
        pytest.fail(
            f"json.JSONDecodeError on yaml_query.py output: {e}\n"
            f"Output was: {output[:200]}\n"
            f"Likely cause: Python repr instead of JSON (single quotes)."
        )

    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["name"] == "proxy-net"
```

---

## Verification

### Pre-merge

```bash
# 1. Unit-тесты yaml_query.py (новые + существующие)
python3 -m pytest tests/test_unit_yaml_query.py -s -v

# 2. Provisioner dry-run (все 4 scope)
make provision SCOPE=all DRY_RUN=true

# 3. Production gate
make gate MODE=fast
```

**Ожидаемый результат**: все 9 тестов `test_unit_provision_environment.py` — зеленые.

### Ручная проверка цепочки

```bash
# Проверка что yaml_get_field возвращает валидный JSON для списка
source core/lib/yaml_read.sh
yaml_get_field platform-env.yaml networks | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
# Должно вывести: 8 (количество сетей)
```

---

## Rollback Plan

Изменение — аддитивное (добавление `elif`-ветки, не удаление логики):

- `yaml_query.py`: добавлена 1 строка `elif isinstance(value, (dict, list)): print(json.dumps(value))` + TRAP[BUG] комментарий
- `test_unit_yaml_query.py`: новый файл (можно удалить или заскипать)

Откат: `git revert <merge-commit>`. Все изменения в одном коммите на feature-ветке.

При откате:
- Восстанавливается старое поведение `print(value)` для dict/list
- 9 тестов provision-environment.sh снова падают
- Никакие другие системы не затрагиваются (изменение изолировано в одном блоке вывода)
