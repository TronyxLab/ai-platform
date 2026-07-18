<!-- GREP_SUMMARY: Brief, data-flow, shellcheck, extended-variable-registry, gate-blindness, _looks_like_path, bash-parsing, unit-tests, static-analysis-ceiling -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Current Ceiling → ◇ Root Cause → ◇ Dual Solution (ShellCheck B + Extended Registry A) → ◇ Implementation → ◇ Gate Design → ◇ Acceptance Criteria → ◇ Non-scope -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** БРИФ улучшения статического анализа cross-layer imports — интеграция ShellCheck для data-flow анализа + расширение реестра переменных в `_looks_like_path`/`resolve_import`.
- **DESCRIPTION:** Двухслойное улучшение Gate #8: (1) ShellCheck как источник structured data-flow информации для обнаружения вызовов через переменные; (2) автоматическое сканирование `paths.sh` для расширения реестра известных переменных. Добавление unit-тестов для функций детекции.
- **RATIONALE:** Текущий `_looks_like_path` имеет жёсткий потолок: требует `/` в литерале, знает только 9 хардкоженных переменных, не отслеживает присвоения. ShellCheck уже делает data-flow анализ (SC2154 — unreferenced variables), но его вывод не используется для cross-layer проверки. Комбинация ShellCheck (structured data) + Extended Registry (coverage gap, который ShellCheck не закрывает) даёт максимальный coverage без написания своего bash-парсера.
- **ACCEPTANCE_CRITERIA:** `_looks_like_path` расширен — `$variable` считается path-bearing; `resolve_import` подставляет переменные из автоматически собранного реестра (paths.sh); ShellCheck integration обнаруживает `bash "$var"` где var присвоена из path-литерала; unit-тесты покрывают ≥10 edge cases; Gate #8 ловит ≥80% ранее невидимых вызовов.
- **IMPLEMENTS:** Superposition 3 вар. B+A (ShellCheck + Extended Registry), skill `arch-patterns` (fail-fast validation)
- **IMPACTS:** `tests/test_cross_layer_imports.py` (_looks_like_path, resolve_import, scan_sh_file), `tests/_conftest/shellcheck.py` (NEW — ShellCheck integration), `tests/test_cross_layer_imports.py` (unit-тесты — NEW секция), `tests/gates/test_gate_cross_layer.py` (обновление expected)
- **REQUIRES:** `Brief.md` W3 (Gate Hardening), `Brief-CallSites.md` (Typed Contract — определяет что ловить), `tests/test_cross_layer_imports.py` (текущая реализация)

$START_BRIEF

# Brief: ShellCheck Integration + Extended Variable Registry

## Current Ceiling

### Что `_looks_like_path` НЕ видит

| Паттерн | Пример | Видит? | Почему нет |
|---------|--------|--------|-----------|
| Переменная без `/` | `bash "$hc_script"` | ❌ | `"$hc_script"` не содержит `/` — `_looks_like_path` → False |
| Присвоение + использование | `local x="modules/foo/bar.sh"; bash "$x"` | ❌ | Нет трекинга присвоений |
| Кросс-файловое присвоение | `source lib/paths.sh; bash "$PATHS_MODULES_DIR/foo.sh"` | ❌ | `resolve_import` не подставляет неизвестные переменные |
| `make -C modules/postgres` | Make-таргет в модуле | ❌ | Нет regex для `make -C` |
| `docker compose -f modules/...` | Compose-файл модуля | ❌ | Нет regex для `docker compose -f` |
| Bare execution | `$cmd arg1 arg2` | ❌ | Нет regex для `^\$\w+` |

### Что `resolve_import` НЕ подставляет

Только 9 хардкоженных переменных: `${_EP_DIR}`, `${SCRIPT_DIR}`, `${MODULE_DIR}`, `${_HEALTHCHECK_LIB_DIR}`, `${_TIMING_LIB_DIR}`, `${_NODE_RESOLVER_LIB_DIR}`, `${CORE_DIR}`, `${PATHS_INTERNAL_DIR}`, `${PLATFORM_ROOT}`.

Переменные из `paths.sh`, не попавшие в список: `PATHS_LIB_DIR`, `PATHS_MODULES_DIR`, `PATHS_TEMPLATES_DIR` — не подставляются.

### Что НЕ тестируется

- **Zero unit-тестов** для `_looks_like_path`, `resolve_import`, `scan_sh_file`
- Нет тестов на edge cases: переменные, многострочные команды, вложенные `${}`, source в subshell

## Root Cause

**Попытка решить compiler-level задачу linter-level инструментом.** Полноценный data-flow анализ bash требует AST-парсер, scope tracking, SSA-форму. Но для практических нужд платформы достаточно coverage ~80% — и ShellCheck уже делает значительную часть этой работы.

## Solution: Dual Layer (B + A)

### Слой B — ShellCheck Integration

**Идея:** ShellCheck уже выполняет data-flow анализ для диагностики SC2154 (referencing undefined variable). Его structured output (`-f json`) содержит информацию о том, какие переменные где используются и откуда берутся. Мы извлекаем из этого вывода все команды вида `bash <path>` где `<path>` — переменная, и проверяем cross-layer правила.

**Что даёт ShellCheck:**
- Знает, что `$hc_script` присвоена из `${CORE_DIR}/modules/${mod_name}/healthcheck.sh`
- Знает, что `${CORE_DIR}` определена в `paths.sh`
- Может отследить цепочку: `paths.sh:CORE_DIR → node-lifecycle.sh:hc_script → bash "$hc_script"`
- Structured output (JSON) — не нужно парсить shell вручную

**Что ShellCheck НЕ даёт:**
- Не отслеживает кросс-файловые вызовы через `source` (SC2154 работает в пределах одного файла, но source'd переменные — known limitation)
- Не классифицирует слои (не знает что такое `internal/` vs `modules/`)
- Не проверяет `make -C`, `docker compose -f`, bare execution

**Интеграция:**
```python
# tests/_conftest/shellcheck.py (NEW)
def get_shellcheck_bash_calls(file_path: Path) -> list[BashCall]:
    """Run shellcheck -f json and extract all bash/sh commands with resolved paths."""
    result = subprocess.run(
        ["shellcheck", "-f", "json", str(file_path)],
        capture_output=True, text=True
    )
    # ShellCheck не отдаёт эту информацию напрямую в JSON.
    # Альтернативный подход: использовать shellcheck -f json с code=SC2154
    # и вручную резолвить переменные через grep присвоений.
    ...
```

**Важное уточнение:** ShellCheck JSON output в стандартном режиме НЕ содержит data-flow граф. Он содержит diagnostic messages (ошибки/предупреждения). Для извлечения информации о присвоениях переменных нужно либо:
- Использовать `shellcheck -f json` + парсить `SC2154` предупреждения для построения карты undefined-переменных (от обратного — переменная используется но не определена в скоупе → значит пришла из source)
- Или использовать `shellcheck -f diff` / custom format
- Или парсить `declare -p` / `export` вывод (runtime)

**Прагматичный подход:** ShellCheck используется НЕ как единственный источник data-flow, а как дополнительный детектор для паттерна «переменная присвоена из path-литерала, затем использована в `bash`». Комбинируется с расширенным реестром (слой A).

### Слой A — Extended Variable Registry

**Идея:** Вместо хардкоженных 9 переменных — автоматически собирать ВСЕ переменные из `core/lib/paths.sh` и других lib-файлов.

**Реализация:**
```python
# tests/test_cross_layer_imports.py

def _collect_path_variables() -> dict[str, str]:
    """Parse core/lib/paths.sh and extract all VAR=value assignments."""
    paths_file = PROJECT_ROOT / "core" / "lib" / "paths.sh"
    variables: dict[str, str] = {}

    for line in paths_file.read_text().splitlines():
        # PATHS_CORE_DIR="${PATHS_LIB_DIR}/.."
        if match := re.match(r'^(\w+)=["\']?(.+?)["\']?\s*(?:#.*)?$', line):
            name, value = match.groups()
            variables[name] = value

    return variables

_KNOWN_PATH_VARIABLES = _collect_path_variables()
```

**Расширение `_looks_like_path`:**
```python
def _looks_like_path(text: str) -> bool:
    t = text.strip().strip("'\"")

    # Existing checks
    has_separator = "/" in t
    has_var_prefix = t.startswith("${") and "/" in t
    has_relative = t.startswith("..")
    has_absolute = t.startswith("/") and t != "/"

    # NEW: bare variable reference — potentially a path
    # We'll resolve it later in resolve_import
    is_variable = t.startswith("$") and not t.startswith("${") and t not in _NON_IMPORT_ARGS

    return has_separator or has_var_prefix or has_relative or has_absolute or is_variable
```

**Расширение `resolve_import`:**
```python
def resolve_import(source_file: Path, import_path: str, source_layer: str) -> Path | None:
    # Step 1: substitute known variables from auto-collected registry
    resolved = import_path
    for var_name, var_value in _KNOWN_PATH_VARIABLES.items():
        resolved = resolved.replace(f"${{{var_name}}}", var_value)
        resolved = resolved.replace(f"${var_name}", var_value)

    # Step 2: if still a bare variable, try to trace its assignment in the current file
    if resolved.startswith("$") and "/" not in resolved:
        var_name = resolved.lstrip("$").strip("{}")
        traced = _trace_variable_assignment(source_file, var_name)
        if traced:
            resolved = traced

    # Continue with existing resolution logic...
```

**Локальный трекинг присвоений (в пределах одного файла):**
```python
def _trace_variable_assignment(file_path: Path, var_name: str) -> str | None:
    """Trace a variable to its last assignment in the same file."""
    content = file_path.read_text()
    # local hc_script="${CORE_DIR}/modules/${mod_name}/healthcheck.sh"
    pattern = rf'(?:local\s+|export\s+)?{re.escape(var_name)}=["\']?([^"\'\n]+)'
    for match in re.finditer(pattern, content):
        value = match.group(1)
        # Resolve nested variables
        for nested in re.finditer(r'\$\{?(\w+)\}?', value):
            nested_name = nested.group(1)
            if nested_name in _KNOWN_PATH_VARIABLES:
                value = value.replace(nested.group(0), _KNOWN_PATH_VARIABLES[nested_name])
        if "/" in value:
            return value
    return None
```

### Обработка новых паттернов

| Паттерн | Старый gate | Новый gate | Механизм |
|---------|------------|------------|----------|
| `bash "$hc_script"` | ❌ | ✅ | Extended registry: `$var` → path-bearing; resolve_import: trace assignment |
| `source "${PATHS_MODULES_DIR}/foo.sh"` | ❌ (переменная неизвестна) | ✅ | Auto-collected registry from paths.sh |
| `make -C modules/postgres start` | ❌ | ⚠️ Частично | Новый regex для `make -C`; нужен ручной audit модульных Makefile |
| `docker compose -f modules/...` | ❌ | ⚠️ Частично | Новый regex; но compose-файлы могут быть в разных местах |
| `$cmd arg1 arg2` | ❌ | ❌ | Не покрывается — bare execution слишком неоднозначен |

### Unit-тесты

```python
# tests/test_cross_layer_imports.py — NEW section

class TestLooksLikePath:
    def test_literal_path(self): ...
    def test_variable_with_path(self): ...
    def test_bare_variable(self): ...
    def test_flag_minus_c(self): ...
    def test_bare_variable_known(self): ...
    def test_empty_string(self): ...

class TestResolveImport:
    def test_known_variable_substitution(self): ...
    def test_local_assignment_trace(self): ...
    def test_cross_file_unresolved(self): ...
    def test_nested_variable(self): ...

class TestCollectPathVariables:
    def test_paths_sh_parsed(self): ...
    def test_all_known_vars_present(self): ...
```

## Implementation Steps

### Фаза 1: Auto-collect path variables

- `tests/test_cross_layer_imports.py` — `_collect_path_variables()`: парсит `core/lib/paths.sh` → `_KNOWN_PATH_VARIABLES`
- Заменить 9 хардкоженных переменных на auto-collected dict

### Фаза 2: Расширить `_looks_like_path`

- Bare variable: `$var` (не `${var}`) считается path-bearing
- Интегрировать с `_trace_variable_assignment`

### Фаза 3: Локальный трекинг присвоений

- `_trace_variable_assignment(file_path, var_name)` — поиск `local var=...` в том же файле
- Подстановка известных переменных внутри присвоения

### Фаза 4: ShellCheck integration (дополнительный слой)

- `tests/_conftest/shellcheck.py` — запуск `shellcheck` на проблемных файлах
- Извлечение `bash <variable>` вызовов + резолвинг переменных через grep
- Использовать как дополнительный детектор, не как основной механизм

### Фаза 5: Новые regex-паттерны

- `make -C core/modules/<name>` — обнаруживать Make-вызовы в модули
- `docker compose -f modules/...` — обнаруживать compose-вызовы

### Фаза 6: Unit-тесты

- Минимум 10 тестов: `_looks_like_path` (5), `resolve_import` (3), `_collect_path_variables` (2)
- Edge cases: переменные, вложенные `${}`, source в subshell

## Acceptance Criteria

1. `_KNOWN_PATH_VARIABLES` содержит ≥15 переменных из `paths.sh` (не 9 хардкоженных)
2. `_looks_like_path("$hc_script")` → `True` (bare variable = potentially path)
3. `_looks_like_path("-c")` → `False` (flag, не переменная)
4. `resolve_import(file, "$hc_script", "internal")` → резолвится в путь внутри `core/modules/`
5. `_trace_variable_assignment(node_lifecycle_sh, "hc_script")` → возвращает путь с `modules/`
6. Unit-тесты: ≥10 тестов, включая edge cases, все green
7. Gate #8 находит ≥4 из 6 runtime-вызовов (без Typed Contract) или валидирует `invoke_module_interface` вызовы (с Typed Contract)
8. `make gate MODE=fast` выполняется за то же время (±10%) — ShellCheck не должен замедлять gate

## Non-scope

- **Полноценный bash-парсер** — AST, scope tracking, SSA форма. Это compiler-level задача, не для этого БРИФа
- **Кросс-файловый data-flow** — отслеживание переменных через `source`. ShellCheck имеет ограничения, мы их принимаем
- **Анализ runtime-конфигов** — crontab, systemd units. Это покрывается `verify-node-paths.sh` (W5) и path-consistency gate (W3)
- **`make -C` и `docker compose -f`** — только базовое обнаружение; полный аудит всех Make/Compose вызовов — отдельный БРИФ

## Dependencies

| Зависимость | Статус |
|-------------|--------|
| `Brief.md` W3 (Gate Hardening) | Этот БРИФ = детализация W3 (static analysis часть) |
| `Brief-CallSites.md` (Typed Contract) | С Typed Contract — Gate #8 ищет `invoke_module_interface` + валидирует interfaces. Без Typed Contract — Gate #8 ловит прямые вызовы через переменные. Оба режима поддерживаются. |
| `shellcheck` в CI | Уже установлен (часть `make lint`). Нужна версия ≥0.9.0 для structured output. |

$END_BRIEF
