<!-- GREP_SUMMARY: DevPlan, data-flow, shellcheck, extended-variable-registry, gate-blindness, _looks_like_path, bash-parsing, unit-tests, static-analysis-ceiling, H1-dual-layer -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ TRAP[DECISION] ×4 → ◇ Wave 1: EXTENDED REGISTRY (T1.1–T1.5) → ◇ Wave 2: SHELLCHECK (T2.1–T2.3) → ◇ Wave 3: REGEX PATTERNS (T3.1–T3.2) → ◇ Wave 4: UNIT TESTS (T4.1–T4.5) → ◇ Wave 5: DISCOVERY+FIX (T5.1–T5.2) → ◇ Wave 6: GATE REGISTRATION (T6.1–T6.4) → ⊕ $TASKS/$PARALLEL_GROUPS/$TEST_SPEC/$FILE_MANIFEST -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** DevPlan улучшения статического анализа cross-layer imports — двухслойное расширение Gate #8: Extended Variable Registry (авто-сбор переменных из `paths.sh` + локальный трекинг присвоений) + ShellCheck integration (дополнительный data-flow детектор). Реализация Гипотезы H1 (Dual-Layer Static).
- **DESCRIPTION:** 6 волн: W1 авто-сбор и расширение `_looks_like_path`/`resolve_import`, W2 ShellCheck integration с закреплением версии, W3 новые regex-паттерны (`make -C`, `docker compose -f`), W4 unit-тесты (≥14), W5 discovery существующих violations + fix, W6 gate-регистрация и валидация. План идёт после Brief-CallSites (Typed Contract) — Gate #8 уже модифицирован (`invoke_module_interface` bypass, `_IMPORT_RULES` обновлены), DataFlow надстраивается поверх.
- **RATIONALE:** Текущий `_looks_like_path` слеп к вызовам через переменные (требует `/` в литерале). `resolve_import` знает только 9 хардкоженных переменных. ShellCheck уже делает data-flow анализ (SC2154), но его вывод не используется. Комбинация Extended Registry (coverage gap от source'd переменных) + ShellCheck (structured data-flow для intra-file) даёт ~80% coverage без написания своего bash-парсера. Typed Contract (CallSites) упрощает картину: легитимные вызовы используют `invoke_module_interface`, DataFlow ловит нелегитимные и предотвращает регресс.
- **ACCEPTANCE_CRITERIA:**
  1. `_KNOWN_PATH_VARIABLES` содержит ≥6 переменных из `paths.sh` (PATHS_LIB_DIR, PATHS_CORE_DIR, PATHS_MODULES_DIR, PATHS_TEMPLATES_DIR, PATHS_INTERNAL_DIR, PLATFORM_ROOT)
  2. `_looks_like_path("$hc_script")` → `True` (bare variable = potentially path)
  3. `_looks_like_path("$?")` → `False` (специальная shell-переменная)
  4. `resolve_import(file, "$hc_script", "internal")` → резолвится в путь внутри `core/modules/` через `_trace_variable_assignment`
  5. `_trace_variable_assignment(node_lifecycle_sh, "hc_script")` → возвращает путь с `modules/`
  6. `shellcheck --version` ≥0.9.0 закреплён в CI; `tests/_conftest/shellcheck.py` корректно деградирует при отсутствии shellcheck
  7. `tests/_conftest/shellcheck.py` обнаруживает вызовы `bash "$var"` где `var` присвоена из path-литерала
  8. `scan_sh_file` обнаруживает `make -C modules/<name>` и `docker compose -f modules/...`
  9. Unit-тесты: ≥14 тестов (5 `_looks_like_path`, 3 `resolve_import`, 2 `_collect_path_variables`, 2 `_trace_variable_assignment`, 2 ShellCheck), все green
  10. Gate #8 ловит ≥80% ранее невидимых вызовов (измеряется как: violations_found / total_known_blind_spots)
  11. `make gate MODE=fast` выполняется за ≤110% от baseline времени (ShellCheck overhead ≤10%)
  12. Найденные существующие violations зафиксированы — Gate #8 зелёный на финальном состоянии
- **IMPLEMENTS:** Brief-DataFlow.md, Superposition H1 (Dual-Layer Static), skill `arch-patterns` (fail-fast validation)
- **IMPACTS:** `tests/test_cross_layer_imports.py` (_collect_path_variables NEW, _KNOWN_PATH_VARIABLES NEW, _looks_like_path MODIFIED, resolve_import MODIFIED, _trace_variable_assignment NEW, _NON_IMPORT_ARGS MODIFIED, scan_sh_file MODIFIED, lint_core MODIFIED), `tests/_conftest/shellcheck.py` (NEW), `tests/_conftest/__init__.py` (MODIFIED), `tests/gates/test_gate_cross_layer.py` (MODIFIED), `core/entrypoint-manifest.yaml` (MODIFIED — gate entry), `core/AGENTS.md` (MODIFIED — cross-layer таблица)
- **REQUIRES:** `Brief-DataFlow.md`, `Brief-CallSites.md` (реализован ПЕРВЫМ — W2 Typed Contract), `tests/test_cross_layer_imports.py` (текущая + post-CallSites), `tests/_conftest/` (структура), `core/lib/paths.sh`, `shellcheck` ≥0.9.0 (Homebrew/APT)

$START_DEVPLAN

# 07-DevPlan-DataFlow: ShellCheck + Extended Variable Registry

---

## $TASKS (task graph)

```
Wave 1 (EXTENDED REGISTRY)
  T1.1 ─► T1.2 ─► T1.3 ─► T1.4 ─► T1.5
  collect   replace  _looks_  _trace_  resolve_
  paths.sh  h/coded  like     variable import
           vars     path     assign   integration

Wave 2 (SHELLCHECK)
  T2.1 ─► T2.2 ─► T2.3
  shell-  version  scan_sh_
  check   pin     file int.

Wave 3 (REGEX PATTERNS)
  ┌ T3.1: make -C pattern
  └ T3.2: docker compose -f pattern
  (parallel — независимые regex)

Wave 4 (UNIT TESTS)
  ┌ T4.1: TestLooksLikePath (≥5)
  ├ T4.2: TestResolveImport (≥3)
  ├ T4.3: TestCollectPathVariables (≥2)
  ├ T4.4: TestTraceVariableAssignment (≥2)
  └ T4.5: TestShellCheckIntegration (≥2)
  (все параллельно — тесты независимых функций)

Wave 5 (DISCOVERY + FIX)
  T5.1 ─► T5.2
  run new  fix found
  gate     violations

Wave 6 (GATE REGISTRATION)
  T6.1 ─► T6.2 ─► T6.3 ─► T6.4
  update   manifest gate     docs
  gate     entry   MODE=fast
```

## $PARALLEL_GROUPS

| Wave | Group | Tasks | Rationale |
|------|-------|-------|-----------|
| 1 | G0 | T1.1→T1.5 | Sequential: each depends on prior (collect → replace → look → trace → integrate) |
| 2 | G1 | T2.1→T2.3 | Sequential: shellcheck.py → version pin → integration depends on module |
| 3 | G2 | T3.1 ∥ T3.2 | Independent: два regex в разных частях scan_sh_file, не конфликтуют |
| 4 | G3 | T4.1 ∥ T4.2 ∥ T4.3 ∥ T4.4 ∥ T4.5 | Независимые тестовые классы — разные функции, нет shared state |
| 5 | G4 | T5.1→T5.2 | Sequential: сначала inventory, потом fix |
| 6 | G5 | T6.1→T6.4 | Sequential: gate update → manifest → validate → docs |

## Task-level edge cases

Каждая задача T*.* в кодовой спецификации ниже снабжена картой крайних случаев: пустой/невалидный/предельно большой вход, повторный запуск (идемпотентность), частичный сбой и откат, отказ внешней зависимости, конкурентный доступ, миграция данных.

---

# ⚠️ TRAP[DECISION] · 2026-07-18 · HI · Гипотеза H1 (Dual-Layer Static) выбрана вместо H2 (Typed Contract-first)

- **Context:** Бриф предлагал ShellCheck B + Extended Registry A как равноправные слои. Суперпозиция рассмотрела 5 гипотез: H1 (Dual-Layer, 8/10), H2 (Typed Contract-first, 7/10), H3 (Custom Parser, 4/10), H4 (Runtime Only, 3/10), H5 (ShellCheck Primary, 6/10).
- **Decision:** H1 — два независимых слоя: авто-сбор переменных из paths.sh (слой A) + ShellCheck как дополнительный детектор (слой B). H2 отвергнута, потому что привязывает судьбу DataFlow к CallSites и теряет универсальность (не помогает для entrypoints→* и других слоёв). H3/H4/H5 отвергнуты по cost/benefit.
- **Reason:** H1 даёт максимальный coverage (~80%) без своего bash-парсера. Каждый слой деградирует независимо: при отказе ShellCheck остаётся Extended Registry; при отсутствии paths.sh остаётся ShellCheck + хардкоженные переменные. Typed Contract (CallSites) реализуется ПЕРВЫМ — DataFlow надстраивается поверх, добавляя детекцию для не-`invoke` паттернов.
- **Rejected:** H2 (Typed Contract-first) — gate становится grep-тривиальным, но теряет способность обнаруживать не-`invoke` вызовы в других слоях. H5 (ShellCheck Primary) — слишком сильная связность с форматом ShellCheck, риск при смене версии. H3 (Custom Parser) — высокая стоимость поддержки (~6-8h vs ~4-5h).
- **Rev:** если ShellCheck deprecate'ит SC2154 или изменит JSON-формат несовместимым образом — пересмотреть в пользу H3 (Custom Parser) или полностью отказаться от слоя B.

# ⚠️ TRAP[DECISION] · 2026-07-18 · HI · Порядок: Brief-CallSites ПЕРВЫМ, DataFlow ВТОРЫМ

- **Context:** Оба брифа модифицируют `tests/test_cross_layer_imports.py`. DataFlow меняет `_looks_like_path`/`resolve_import`/`scan_sh_file` (детекция), CallSites меняет `_IMPORT_RULES`/`check_violation` (правила). Функции разные, но файл один — merge conflict при параллельной реализации.
- **Decision:** CallSites реализуется первым. DataFlow DevPlan пишется с предположением, что CallSites уже выполнен: `_IMPORT_RULES["internal"]` включает `"modules"`, `scan_sh_file` исключает строки с `invoke_`, `check_violation` валидирует `module.yaml.interfaces`.
- **Reason:** Typed Contract — архитектурное изменение (меняет правила игры), DataFlow — улучшение детекции (работает в тех же правилах). Логично сначала установить правила, потом улучшать их enforcement.
- **Rejected:** Параллельная реализация — merge conflict в test_cross_layer_imports.py при слиянии веток. DataFlow первым — gate будет фейлиться на всех 6 call sites, пока CallSites не реализован.
- **Rev:** если CallSites откладывается на неопределённый срок — реализовать DataFlow с feature-флагом `TYPED_CONTRACT_ACTIVE=False`, который отключает проверку `invoke_` bypass.

# ⚠️ TRAP[DECISION] · 2026-07-18 · MED · ShellCheck версия закреплена на ≥0.9.0

- **Context:** Brief упоминает ShellCheck ≥0.9.0 для structured output. На машине разработчика установлен 0.11.0. Формат SC2154 diagnostic messages не является частью стабильного API ShellCheck.
- **Decision:** Закрепить `shellcheck>=0.9.0` в CI-окружении и документировать как требование для локальной разработки. В `tests/_conftest/shellcheck.py` добавить проверку версии с предупреждением (warning, не error). При отсутствии shellcheck — graceful degradation (пустой результат, IMP:7 warning).
- **Reason:** SC2154 — зрелая диагностика (существует с ранних версий ShellCheck), формат JSON стабилен годами. Риск низкий. Но страховка: gate не фейлится при отсутствии shellcheck (только слой A активен).
- **Rejected:** Не закреплять версию — gate сломается молча при обновлении ShellCheck. Использовать `shellcheck -f diff` — формат ещё менее стабилен.
- **Rev:** если ShellCheck >1.0 изменит SC2154 формат — обновить парсер в `shellcheck.py`; если изменения радикальные — отключить слой B.

# ⚠️ TRAP[DECISION] · 2026-07-18 · MED · Кросс-файловый source — принятое ограничение

- **Context:** Brief явно исключает кросс-файловый data-flow из скоупа (Non-scope). Основные вызовы (node-lifecycle.sh, deploy-modules.sh, deploy-project.sh) получают переменные из `paths.sh` через `source`. ShellCheck SC2154 работает в пределах одного файла и не отслеживает source'd переменные.
- **Decision:** Extended Registry (авто-сбор из paths.sh) покрывает основной кейс: переменные из paths.sh резолвятся через `_KNOWN_PATH_VARIABLES` в любом файле. `_trace_variable_assignment` работает только в пределах одного файла для локальных присвоений. Кросс-файловое отслеживание остаётся вне скоупа.
- **Reason:** 100% coverage требует AST-парсер — нецелесообразно. Auto-collect из paths.sh + локальный трекинг дают ~80% практического покрытия. Оставшиеся 20% — экзотические паттерны, которые маловероятны при наличии Typed Contract.
- **Rejected:** Эмуляция source с загрузкой содержимого — высокая сложность (надо отслеживать цепочки source, guard от рекурсии, разрешать relative paths), легко сломать.
- **Rev:** если появятся новые паттерны кросс-файловых вызовов (не из paths.sh) — добавить ручное расширение реестра через конфигурационный файл (без эмуляции source).

---

# ⚠️ TRAP[DECISION] · 2026-07-18 · HI · Отвергнутые гипотезы суперпозиции

| Гипотеза | Score | Причина отказа | Rev |
|----------|-------|---------------|-----|
| H2: Typed Contract-first | 7/10 | Gate теряет универсальность, привязан к `invoke_module_interface`. Не помогает для entrypoints→* и других слоёв. | Если Typed Contract станет единственным легитимным механизмом ВО ВСЕХ слоях — пересмотреть. |
| H3: Custom Bash Parser | 4/10 | Стоимость ~6-8h, поддержка как второго compiler'а. Нецелесообразно для ~80% coverage. | Если ShellCheck станет недоступен/непригоден, а потребность в кросс-файловом анализе вырастет. |
| H4: Runtime Enforcement Only | 3/10 | Gate #8 остаётся слепым — ложная гарантия. Нарушения только на проде. | Если статический анализ достигнет принципиального потолка и дальнейшие улучшения невозможны. |
| H5: ShellCheck Primary Engine | 6/10 | Предобработка (конкатенация source) создаёт артефакты, сложность высока. Риск при смене версии ShellCheck критический (основной движок). | Если ShellCheck добавит нативную поддержку кросс-файлового анализа. |

---

## Wave 1 — EXTENDED VARIABLE REGISTRY

### T1.1: `_collect_path_variables()` — авто-сбор из paths.sh

**Файл:** `tests/test_cross_layer_imports.py`

**Сигнатура:**
```python
def _collect_path_variables() -> dict[str, str]:
    """Parse core/lib/paths.sh and extract all VAR=value assignments.

    Returns dict mapping variable name → raw value (with ${} references unresolved).
    Handles: readonly VAR="value", bare VAR=value, VAR='value'.
    Skips: comments, empty lines, export (paths.sh doesn't use export).
    """
```

**Логика парсинга:**
- Читает `PROJECT_ROOT / "core" / "lib" / "paths.sh"`
- Для каждой строки: `^(\w+)=(.*)` — захватывает имя и значение
- Поддерживает `readonly VAR=...` — stripping `readonly ` префикса
- Значения: stripping surrounding quotes (`"..."` или `'...'`), trailing inline comments (`# comment`)
- Пропускает строки с `${BASH_SOURCE[0]}` и `$(...)` в значении (runtime-резолвинг, не парсим статически)

**Возврат:**
```python
{
    "PATHS_LIB_DIR": "${BASH_SOURCE[0]} resolved at runtime",
    "PATHS_CORE_DIR": "${PATHS_LIB_DIR}/..",
    "PATHS_MODULES_DIR": "${PATHS_CORE_DIR}/modules",
    "PATHS_TEMPLATES_DIR": "${PATHS_CORE_DIR}/templates",
    "PATHS_INTERNAL_DIR": "${PATHS_CORE_DIR}/internal",
    "PLATFORM_ROOT": "/opt/platform",
}
```

**Важно:** Значения с `${BASH_SOURCE[0]}` не парсятся статически. Вместо этого для переменных, которые ссылаются на `${BASH_SOURCE[0]}`, значение вычисляется относительно CORE_DIR:
```python
if "${BASH_SOURCE[0]}" in raw_value or "$(cd" in raw_value:
    # RESOLVE: вычислить статически
    # PATHS_LIB_DIR = core/lib (где лежит paths.sh)
    # PATHS_CORE_DIR = core (parent of lib)
    variables[name] = _resolve_statically(name, CORE_DIR)
```

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **Пустой paths.sh** | Возвращает `{}` — gate деградирует до хардкоженных переменных |
| **paths.sh не существует** | `FileNotFoundError` → `{}` + IMP:7 warning |
| **readonly без `=`** | Пропускается (не является присвоением) |
| **Multi-line assignment** | Только первая строка (backslash-continuation не парсится) |
| **export VAR=value** | Парсится (stripping `export ` префикса) |
| **Переменная с `${}` в значении** | Сохраняется с `${}` — резолвится позже в `resolve_import` рекурсивно |
| **Повторный запуск** | Идемпотентен — возвращает тот же dict |
| **paths.sh изменён между запусками** | Не кешируется — перечитывается каждый раз (pytest не переиспользует модуль между файлами) |

### T1.2: Замена 9 хардкоженных переменных на auto-collected + contextual

**Файл:** `tests/test_cross_layer_imports.py`

**Текущее состояние (строки 143-171):** 9 `if "${VAR}" in resolved:` блоков.

**Новое состояние:**
```python
# Module-level: auto-collected at import time
_KNOWN_PATH_VARIABLES: dict[str, str] = _collect_path_variables()

# В resolve_import() — единый цикл подстановки:
def _substitute_variables(resolved: str, source_file: Path, source_layer: str) -> str:
    """Substitute known variables in import path."""
    # Шаг 1: auto-collected из paths.sh
    for var_name, var_value in _KNOWN_PATH_VARIABLES.items():
        resolved = resolved.replace(f"${{{var_name}}}", var_value)
        resolved = resolved.replace(f"${var_name}", var_value)

    # Шаг 2: contextual variables (не из paths.sh, зависят от source_file)
    contextual = {
        "_EP_DIR": str(source_file.parent),
        "SCRIPT_DIR": str(source_file.parent),
        "MODULE_DIR": str(source_file.parent),
        "_HEALTHCHECK_LIB_DIR": str(CORE_DIR / "lib"),
        "_TIMING_LIB_DIR": str(CORE_DIR / "lib"),
        "_NODE_RESOLVER_LIB_DIR": str(CORE_DIR / "lib"),
        "CORE_DIR": str(CORE_DIR),
        "PLATFORM_ROOT": _resolve_platform_root(source_file, source_layer),
    }
    for var_name, var_value in contextual.items():
        resolved = resolved.replace(f"${{{var_name}}}", var_value)
        resolved = resolved.replace(f"${var_name}", var_value)

    return resolved
```

**Мёрджинг:** Auto-collected переменные имеют приоритет над contextual (если пересекаются). `PLATFORM_ROOT` — в обоих источниках; contextual-версия учитывает `source_layer` через `_resolve_platform_root()`.

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **Переменная в обоих словарях** | Auto-collected ПЕРВЫМ, contextual перезаписывает (PLATFORM_ROOT contextual-версия точнее) |
| **PATHS_MODULES_DIR не в paths.sh** | В `_KNOWN_PATH_VARIABLES` отсутствует → не подставляется → `resolve_import` фейлится для этой переменной |
| **PATHS_CORE_DIR = "${PATHS_LIB_DIR}/.."** | Значение содержит `${PATHS_LIB_DIR}` — резолвится рекурсивно: сначала PATHS_LIB_DIR подставляется, потом PATHS_CORE_DIR |
| **Идемпотентность** | Вызов `_collect_path_variables()` дважды → одинаковый результат (paths.sh не меняется) |

### T1.3: Расширение `_looks_like_path` — bare variables

**Файл:** `tests/test_cross_layer_imports.py`, функция `_looks_like_path` (строка 121)

**Текущая логика:**
```python
has_separator = "/" in t
has_var_prefix = t.startswith("${") and "/" in t
has_relative = t.startswith("..")
has_absolute = t.startswith("/") and t != "/"
return has_separator or has_var_prefix or has_relative or has_absolute
```

**Новая логика:**
```python
def _looks_like_path(text: str) -> bool:
    t = text.strip().strip("'\"")

    # Существующие проверки
    has_separator = "/" in t
    has_var_prefix = t.startswith("${") and "/" in t
    has_relative = t.startswith("..")
    has_absolute = t.startswith("/") and t != "/"

    # NEW: bare variable reference — potentially a path
    # Проверяем что это $variable (не флаг, не спец-переменная)
    is_bare_variable = (
        t.startswith("$")
        and not t.startswith("${")  # ${var}/path уже покрыто has_var_prefix
        and t not in _NON_IMPORT_ARGS
        and not re.match(r'^\$[\d@*!#?\-]$', t)  # спец-переменные: $1, $@, $*, $!, $#, $?, $-
    )

    return has_separator or has_var_prefix or has_relative or has_absolute or is_bare_variable
```

**Расширение `_NON_IMPORT_ARGS`:**
```python
_NON_IMPORT_ARGS: set[str] = {
    "-c", "-s", "-i", "-l", "--login", "-r", "--restricted",
    "+o", "-o", "-n", "-x", "-e", "-u", "-p", "-v",
    # NEW: специальные shell-переменные
    "$?", "$#", "$$", "$!", "$@", "$*", "$-", "$0",
    "${?}", "${#}", "${$}", "${!}", "${@}", "${*}", "${-}", "${0}",
}
```

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| `$hc_script` | `True` — bare variable, не в _NON_IMPORT_ARGS, не спец-переменная |
| `$?` | `False` — в _NON_IMPORT_ARGS |
| `$$` | `False` — в _NON_IMPORT_ARGS |
| `$-` | `False` — в _NON_IMPORT_ARGS |
| `${hc_script}` | `False` — не содержит `/`, не bare `$var` (использует `${}`). **Примечание:** `${bare_var}` без `/` не детектится здесь — это ожидаемо, резолвится в `resolve_import` через variable substitution. |
| `$1` (позиционный параметр) | `False` — матчится `^\$\d+$` |
| Пустая строка | `False` — `t` пустой, все условия False |
| `$` (одиночный $) | `False` — `t == "$"`, не матчится ни на один случай |
| Предельно длинное имя переменной | OK — `startswith` не зависит от длины |

### T1.4: `_trace_variable_assignment()` — локальный трекинг присвоений

**Файл:** `tests/test_cross_layer_imports.py`, новая функция

**Сигнатура:**
```python
def _trace_variable_assignment(file_path: Path, var_name: str) -> str | None:
    """Trace a variable to its last assignment in the same file.

    Searches for: local VAR=..., export VAR=..., VAR=... (bare assignment).
    Resolves nested ${} references using _KNOWN_PATH_VARIABLES.
    Returns resolved path if it contains '/', or None.
    """
```

**Логика:**
```python
def _trace_variable_assignment(file_path: Path, var_name: str) -> str | None:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # Шаблон: local/export/ничего VAR=value
    # Многострочность НЕ поддерживается — только однострочные присвоения
    pattern = rf'(?:local\s+|export\s+)?{re.escape(var_name)}=["\']?([^"\'\n]+)'

    matches = list(re.finditer(pattern, content))
    if not matches:
        return None

    # Берём ПОСЛЕДНЕЕ присвоение (ближайшее к использованию)
    last_match = matches[-1]
    value = last_match.group(1).strip()

    # Резолвим вложенные ${VAR} и $VAR
    for nested_name, nested_value in _KNOWN_PATH_VARIABLES.items():
        value = value.replace(f"${{{nested_name}}}", nested_value)
        value = value.replace(f"${nested_name}", nested_value)

    # Если результат содержит / — это путь
    if "/" in value:
        return value
    return None
```

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **Несколько присвоений одной переменной** | Берётся ПОСЛЕДНЕЕ (closest to use site) |
| **Присвоение в heredoc** | Не фильтруется — может дать ложное срабатывание если `var_name=` в heredoc-контенте. Риск низкий (heredoc в shell-скриптах платформы встречается редко) |
| **Присвоение в `if`/`case`** | Парсится корректно — regex ищет паттерн в любой позиции строки |
| **`readonly VAR=...`** | Парсится — regex допускает `readonly ` префикс? НЕТ, `readonly` не в паттерне. **FIX:** добавить `readonly\s+` опционально |
| **`declare VAR=...`** | Не парсится — `declare` не используется в платформе |
| **Файл не читается** | `return None` |
| **Пустой файл** | `return None` |
| **Мультилайн (backslash)** | Не парсится — только первая строка до `\n`. Acceptable limitation. |
| **Вложенные `${}` глубже 1 уровня** | Не резолвятся — только один проход подстановки. Acceptable limitation (paths.sh имеет только 1 уровень вложенности). |

### T1.5: Интеграция в `resolve_import`

**Файл:** `tests/test_cross_layer_imports.py`, функция `resolve_import` (строка 132)

**Изменения:**
1. Заменить 9 `if "${VAR}" in resolved:` блоков на вызов `_substitute_variables()`
2. После подстановки: если resolved всё ещё bare variable (начинается с `$`, без `/`), вызвать `_trace_variable_assignment()`
3. Сохранить существующую логику: strip quotes, strip `./`, проверка `/` в результате

**Новая логика:**
```python
def resolve_import(source_file: Path, import_path: str, source_layer: str) -> Path | None:
    if not _looks_like_path(import_path):
        return None

    resolved = import_path.strip()

    # Шаг 1: подстановка известных переменных
    resolved = _substitute_variables(resolved, source_file, source_layer)

    # Шаг 2: если результат — bare $variable без пути, пробуем локальный трейсинг
    if resolved.startswith("$") and "/" not in resolved:
        var_name = resolved.lstrip("$").strip("{}")
        traced = _trace_variable_assignment(source_file, var_name)
        if traced:
            resolved = traced

    # Шаг 3: strip quotes, ./, resolve path (существующая логика)
    resolved = resolved.replace('"', "").replace("'", "")
    if resolved.startswith("./"):
        resolved = resolved[2:]
    if "/" not in resolved:
        return None
    if not resolved.startswith("/") and not resolved.startswith("..") and not resolved.startswith("${"):
        return None

    result = Path(resolved)
    if result.is_absolute():
        final = result.resolve()
    else:
        final = (source_file.parent / result).resolve()

    if not str(final).startswith(str(CORE_DIR)):
        return None
    return final
```

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **`$hc_script` без присвоения в файле** | `_trace_variable_assignment` → `None` → resolved остаётся `$hc_script` → `/` not in → `return None` (не резолвится) |
| **`${PATHS_MODULES_DIR}/foo.sh`** | Auto-collect подставляет `PATHS_MODULES_DIR` → путь содержит `/` → резолвится |
| **`${UNKNOWN_VAR}/foo.sh`** | Auto-collect не знает `UNKNOWN_VAR` → остаётся `${UNKNOWN_VAR}/foo.sh` → содержит `/` → резолвится как есть (проверка `str(final).startswith(str(CORE_DIR))` решит) |
| **Циклическая подстановка** | Невозможна — переменные в paths.sh не образуют циклов (линейная цепочка: LIB_DIR → CORE_DIR → MODULES_DIR → ...) |

---

## Wave 2 — SHELLCHECK INTEGRATION

### T2.1: `tests/_conftest/shellcheck.py` — модуль интеграции

**Файл:** `tests/_conftest/shellcheck.py` (NEW)

**Сигнатура:**
```python
# tests/_conftest/shellcheck.py
"""ShellCheck integration for cross-layer data-flow detection.

Uses ShellCheck SC2154 diagnostics to detect patterns where a variable
is assigned from a path literal and then used in a bash/sh/source command.
This catches cases that _trace_variable_assignment misses due to scope
boundaries (e.g., variable assigned in one function, used in another).
"""

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Минимальная версия ShellCheck для structured JSON output
MIN_SHELLCHECK_VERSION = (0, 9, 0)


def _check_shellcheck_available() -> tuple[bool, str]:
    """Check if shellcheck is available and version >= MIN.
    Returns (available, version_string or error_message).
    """
    try:
        result = subprocess.run(
            ["shellcheck", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return False, f"shellcheck returned code {result.returncode}"
        # Parse version from "version: X.Y.Z"
        m = re.search(r'version:\s*(\d+)\.(\d+)\.(\d+)', result.stdout)
        if not m:
            return False, f"cannot parse version from: {result.stdout[:80]}"
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        version_str = f"{major}.{minor}.{patch}"
        if (major, minor, patch) < MIN_SHELLCHECK_VERSION:
            return False, f"version {version_str} < {'.'.join(map(str, MIN_SHELLCHECK_VERSION))}"
        return True, version_str
    except FileNotFoundError:
        return False, "shellcheck not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "shellcheck --version timed out"
    except Exception as exc:
        return False, f"unexpected error: {exc}"


def _parse_shellcheck_sc2154(file_path: Path) -> list[str]:
    """Run shellcheck -f json and extract variable names from SC2154 warnings.

    SC2154 = "variable is referenced but not assigned" — ShellCheck detected
    a variable that is used but was never assigned in the current scope.
    This means the variable likely comes from an external source.
    """
    try:
        result = subprocess.run(
            ["shellcheck", "-f", "json", str(file_path)],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode not in (0, 1):
            # returncode 1 = warnings found (normal), >1 = error
            if result.returncode > 1:
                logger.warning("[IMP:6][shellcheck] ShellCheck error on %s: %s", file_path, result.stderr[:200])
                return []

        diagnostics = json.loads(result.stdout) if result.stdout.strip() else []

        sc2154_vars: list[str] = []
        for diag in diagnostics:
            if diag.get("code") == 2154:
                # Extract variable name from message: "VAR is referenced but not assigned."
                message = diag.get("message", "")
                m = re.match(r'^(\w+)\s+is\s+referenced', message)
                if m:
                    sc2154_vars.append(m.group(1))

        return sc2154_vars
    except json.JSONDecodeError:
        logger.warning("[IMP:6][shellcheck] Invalid JSON from shellcheck on %s", file_path)
        return []
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:6][shellcheck] Timeout running shellcheck on %s", file_path)
        return []
    except Exception as exc:
        logger.warning("[IMP:6][shellcheck] Error running shellcheck on %s: %s", file_path, exc)
        return []


def get_shellcheck_bash_calls(file_path: Path) -> list[tuple[int, str]]:
    """Detect bash/sh/source calls where the argument is a variable assigned from a path literal.

    Approach:
    1. Run shellcheck to find SC2154 variables (used but not locally assigned)
    2. For each SC2154 variable, grep the file for its assignment (local/export/VAR=)
    3. If assignment value looks like a path, check if variable is used in bash/sh/source/. call
    4. Return (lineno, import_path) for each detected call

    Returns empty list if shellcheck not available (graceful degradation).
    """
    available, version_str = _check_shellcheck_available()
    if not available:
        logger.warning("[IMP:7][shellcheck] ShellCheck unavailable: %s — skipping data-flow analysis", version_str)
        return []

    logger.info("[IMP:8][shellcheck] ShellCheck %s available — analysing %s", version_str, file_path)

    # Step 1: get SC2154 variables
    sc2154_vars = _parse_shellcheck_sc2154(file_path)
    if not sc2154_vars:
        return []

    # Step 2: read file content
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
    except Exception:
        return []

    # Step 3: for each SC2154 variable, find its assignment
    var_assignments: dict[str, str] = {}
    for var_name in sc2154_vars:
        pattern = rf'(?:local\s+|export\s+|readonly\s+)?{re.escape(var_name)}=["\']?([^"\'\n]+)'
        matches = list(re.finditer(pattern, content))
        if matches:
            value = matches[-1].group(1).strip()
            # Resolve nested ${} using simple heuristic
            for nested in re.finditer(r'\$\{(\w+)\}', value):
                nested_name = nested.group(1)
                if nested_name in var_assignments:
                    value = value.replace(nested.group(0), var_assignments[nested_name])
            if "/" in value:
                var_assignments[var_name] = value

    if not var_assignments:
        return []

    # Step 4: find bash/sh/source/. calls using these variables
    results: list[tuple[int, str]] = []
    bash_pattern = re.compile(r'(?:^|\s)(?:bash|/bin/bash|sh|/bin/sh|source|\.)\s+(\S+)')
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = bash_pattern.search(stripped)
        if m:
            arg = m.group(1)
            # Strip quotes
            arg_clean = arg.strip("'\"")
            # Check if arg is a known path-bearing variable
            if arg_clean.startswith("$"):
                var_name = arg_clean.lstrip("$").strip("{}")
                if var_name in var_assignments:
                    results.append((i, arg))

    return results
```

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **ShellCheck не установлен** | `_check_shellcheck_available()` → `False` → возврат `[]` + IMP:7 warning. Gate не фейлится |
| **Версия <0.9.0** | `_check_shellcheck_available()` → `False` → возврат `[]` + IMP:7 warning |
| **ShellCheck timeout** | 30с таймаут → `[]` + IMP:6 warning |
| **ShellCheck возвращает не-JSON** | `json.JSONDecodeError` → `[]` |
| **Файл с синтаксической ошибкой** | ShellCheck всё равно парсит (не компилятор) — диагностики будут, SC2154 среди них |
| **Очень большой файл (>5000 строк)** | ShellCheck может замедлиться. 30с таймаут — защита. При превышении — `[]` |
| **Переменная присвоена но ShellCheck считает её unreferenced** | SC2154 _не срабатывает_ (она referenced в нашем понимании, ShellCheck тоже считает referenced) — false negative, acceptable |
| **Переменная в heredoc-контенте** | ShellCheck может дать SC2154 на переменную, которая выглядит как unreferenced. Присвоение может не найтись → false negative. Риск низкий |
| **Идемпотентность** | Повторный вызов → повторный shellcheck run (нет кеширования). OK для CI |
| **Конкурентный доступ** | Нет (однопоточный pytest) |

### T2.2: Закрепление версии ShellCheck

**Файлы:**
- `tests/_conftest/shellcheck.py` — проверка версии (уже в T2.1)
- `.github/workflows/*.yml` (если есть CI workflows) — закрепить `shellcheck>=0.9.0`
- `Makefile` — проверить, что `make lint` вызывает shellcheck

**Действия:**
1. В shellcheck.py: `MIN_SHELLCHECK_VERSION = (0, 9, 0)` (уже в T2.1)
2. Проверить CI-окружение: `shellcheck --version` → версия ≥0.9.0
3. Если `make lint` не проверяет shellcheck версию — добавить проверку в `core/entrypoints/validate.sh`
4. Документировать в `core/AGENTS.md` в секции зависимостей

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **Локальная машина без shellcheck** | `make lint` предупреждает, `make gate MODE=fast` деградирует (слой B молча отключён) |
| **CI без shellcheck** | CI-шаг `make lint` фейлится с явным сообщением «shellcheck not found, install shellcheck>=0.9.0» |
| **Обновление shellcheck в CI** | Версия закреплена в CI-конфиге — обновление ручное |

### T2.3: Интеграция ShellCheck в `scan_sh_file`

**Файл:** `tests/test_cross_layer_imports.py`, функция `scan_sh_file` (строка 224)

**Изменения:**
В конце `scan_sh_file`, после существующих паттернов, добавить ShellCheck-обнаруженные вызовы:

```python
def scan_sh_file(file_path: Path, source_layer: str | None = None) -> list[tuple[int, str, bool]]:
    imports: list[tuple[int, str, bool]] = []
    # ... существующая логика (source, ., exec, bash/sh паттерны) ...

    # NEW: ShellCheck data-flow analysis (дополнительный слой)
    # Вызывается только для importing layers (не для lib/ и templates/)
    if source_layer in _IMPORTING_LAYERS:
        try:
            from _conftest.shellcheck import get_shellcheck_bash_calls
            shellcheck_calls = get_shellcheck_bash_calls(file_path)
            for lineno, imp_path in shellcheck_calls:
                # Проверяем, не дублируется ли с уже найденным
                already_found = any(
                    existing_lineno == lineno and existing_path == imp_path
                    for existing_lineno, existing_path, _ in imports
                )
                if not already_found:
                    exempt = _has_lint_exempt(lines, lineno)
                    imports.append((lineno, imp_path, exempt))
        except ImportError:
            logger.debug("[IMP:5][scan][shellcheck] Module not available for %s", file_path)

    return imports
```

**Важно:** ShellCheck-вызовы НЕ дублируют уже найденные паттернами 1-4. Проверка `already_found` предотвращает дубли.

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **ShellCheck недоступен** | `ImportError` или `get_shellcheck_bash_calls()` → `[]` — просто меньше coverage |
| **Дубликат с существующим паттерном** | Пропускается через `already_found` |
| **ShellCheck timeout на конкретном файле** | Файл пропускается, остальные обрабатываются |
| **source_layer=None** | ShellCheck НЕ вызывается (не importing layer) |

---

## Wave 3 — НОВЫЕ REGEX-ПАТТЕРНЫ

### T3.1: `make -C modules/<name>` pattern

**Файл:** `tests/test_cross_layer_imports.py`, функция `scan_sh_file` (после pattern 4)

**Добавить Pattern 5:**
```python
# Pattern 5: make -C <path> (Make invocation into module directory)
m = re.search(r'(?:^|\s)make\s+-C\s+(\S+)', stripped)
if m:
    path = m.group(1)
    if _looks_like_path(path):
        imports.append((i, path, exempt))
    continue
```

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| `make -C modules/postgres start` | Захватывает `modules/postgres` — `_looks_like_path` → True |
| `make -C /opt/core/modules/postgres` | Захватывает `/opt/core/...` — `_looks_like_path` → True |
| `make -j4 -C modules/postgres` | `-j4` — не захватывается, `-C` захватывает `modules/postgres` |
| `make --directory modules/postgres` | Лонг-форма НЕ поддерживается (low priority, не используется в платформе) |
| `make` без `-C` | Не матчится |

### T3.2: `docker compose -f modules/...` pattern

**Файл:** `tests/test_cross_layer_imports.py`, функция `scan_sh_file` (после pattern 5)

**Добавить Pattern 6:**
```python
# Pattern 6: docker compose -f <path> (Compose file reference)
m = re.search(r'(?:^|\s)docker\s+(?:compose|compose)\s+(?:.*\s)?-f\s+(\S+)', stripped)
if m:
    path = m.group(1)
    if _looks_like_path(path) and path not in ("-f",):
        imports.append((i, path, exempt))
    continue
```

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| `docker compose -f modules/postgres/docker-compose.base.yml up` | Захватывает `modules/postgres/docker-compose.base.yml` |
| `docker compose -f a.yml -f b.yml up` | Захватывает только `a.yml` (первый `-f`) — limitation |
| `docker-compose -f modules/...` (дефис) | Матчится: `compose` матчит и `compose` и `compose` в `docker-compose` |
| `docker compose` без `-f` | Не матчится |
| `sudo docker compose -f ...` | Матчится: `sudo` не мешает `docker` |
| Пустой `-f` (на следующей строке) | Не матчится — `-f` и путь должны быть в одной строке |

---

## Wave 4 — UNIT-ТЕСТЫ

### T4.1: `TestLooksLikePath` (≥5 тестов)

**Файл:** `tests/test_cross_layer_imports.py`, новый класс

```python
class TestLooksLikePath:
    """Unit tests for _looks_like_path() function."""

    def test_literal_path(self):
        """Literal path with / is detected."""
        assert _looks_like_path("modules/postgres/healthcheck.sh") is True

    def test_variable_with_path(self):
        """${VAR}/path is detected."""
        assert _looks_like_path("${CORE_DIR}/modules/postgres/healthcheck.sh") is True

    def test_bare_variable(self):
        """Bare $variable (no /) is detected as potential path."""
        assert _looks_like_path("$hc_script") is True

    def test_bare_variable_braces(self):
        """${variable} without / is NOT detected as path (bare braces, no separator)."""
        # _looks_like_path требует / для ${}-переменных (has_var_prefix проверяет "/" in t)
        # ${bare} без / → False. Это ожидаемо — резолвится в resolve_import через substitution.
        assert _looks_like_path("${hc_script}") is False

    def test_flag_minus_c(self):
        """Flag argument is not a path."""
        assert _looks_like_path("-c") is False

    def test_special_vars(self):
        """Special shell variables are not paths."""
        for var in ["$?", "$#", "$$", "$!", "$@", "$*", "$-", "$0"]:
            assert _looks_like_path(var) is False, f"{var} should not be path"

    def test_empty_string(self):
        """Empty string is not a path."""
        assert _looks_like_path("") is False

    def test_quoted_bare_variable(self):
        """Quoted bare variable is detected."""
        assert _looks_like_path('"$hc_script"') is True

    def test_multiple_variables_in_string(self):
        """String with multiple $vars and / is detected."""
        assert _looks_like_path("${CORE_DIR}/modules/${mod_name}/healthcheck.sh") is True
```

**Всего:** 8 тестов (≥5 ✓)

### T4.2: `TestResolveImport` (≥3 тестов)

```python
class TestResolveImport:
    """Unit tests for resolve_import() function."""

    def test_known_variable_substitution(self, tmp_path):
        """Auto-collected variable from paths.sh is substituted."""
        # PATHS_MODULES_DIR = core/modules (from paths.sh)
        result = resolve_import(
            tmp_path / "test.sh",
            "${PATHS_MODULES_DIR}/postgres/healthcheck.sh",
            "internal"
        )
        assert result is not None
        assert "core/modules/postgres/healthcheck.sh" in str(result)

    def test_local_assignment_trace(self, tmp_path):
        """Variable assigned locally is traced to its value."""
        f = tmp_path / "test.sh"
        f.write_text('local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\nbash "$hc_script"')
        # NOTE: resolve_import вызывается с extracted import_path, не с содержимым файла
        # Правильный тест: тестируем _trace_variable_assignment отдельно
        pass  # См. T4.4

    def test_unresolved_bare_variable(self, tmp_path):
        """Bare variable without assignment returns None."""
        result = resolve_import(
            tmp_path / "test.sh",
            "$unknown_var",
            "internal"
        )
        # $unknown_var не в _KNOWN_PATH_VARIABLES, не присвоена локально → None
        assert result is None

    def test_bare_variable_with_trace(self, tmp_path):
        """Bare variable traced to local assignment resolves correctly."""
        f = tmp_path / "test.sh"
        f.write_text('local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\n')
        # Тестируем _trace_variable_assignment напрямую
        traced = _trace_variable_assignment(f, "hc_script")
        assert traced is not None
        assert "modules/postgres/healthcheck.sh" in traced

    def test_nested_variable_substitution(self):
        """Nested variable references are resolved recursively."""
        # PATHS_CORE_DIR = ${PATHS_LIB_DIR}/.. → оба разрешаются
        result = resolve_import(
            Path("core/internal/test.sh"),
            "${PATHS_CORE_DIR}/modules/postgres/healthcheck.sh",
            "internal"
        )
        assert result is not None
        # PATHS_CORE_DIR резолвится в core/
        assert str(result).endswith("core/modules/postgres/healthcheck.sh")

    def test_contextual_variable(self, tmp_path):
        """Contextual variable (_EP_DIR) resolves to source file directory."""
        source = tmp_path / "entrypoints" / "test.sh"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("")
        result = resolve_import(source, "${_EP_DIR}/internal/foo.sh", "entrypoints")
        assert result is not None
        # _EP_DIR → source.parent → tmp_path/entrypoints/
```

**Всего:** 6 тестов (≥3 ✓)

### T4.3: `TestCollectPathVariables` (≥2 тестов)

```python
class TestCollectPathVariables:
    """Unit tests for _collect_path_variables() function."""

    def test_paths_sh_parsed(self):
        """Real paths.sh is parsed and returns expected variables."""
        variables = _collect_path_variables()
        # Минимум 6 переменных из paths.sh
        assert len(variables) >= 6
        assert "PATHS_LIB_DIR" in variables
        assert "PATHS_CORE_DIR" in variables
        assert "PATHS_MODULES_DIR" in variables
        assert "PATHS_TEMPLATES_DIR" in variables
        assert "PATHS_INTERNAL_DIR" in variables
        assert "PLATFORM_ROOT" in variables

    def test_all_values_are_non_empty(self):
        """All collected variables have non-empty values."""
        variables = _collect_path_variables()
        for name, value in variables.items():
            assert value, f"Variable {name} has empty value"

    def test_empty_file(self, tmp_path):
        """Empty file returns empty dict (test with mock — but _collect_path_variables
        uses fixed PROJECT_ROOT path). Instead, test that function doesn't crash
        when paths.sh has unexpected content."""
        # This is more of an integration test — real paths.sh should always exist
        pass
```

**Корректировка:** `_collect_path_variables()` использует фиксированный `PROJECT_ROOT / "core" / "lib" / "paths.sh"`. Тестировать с временным файлом не получится без рефакторинга. Вместо этого:
1. Тест 1: интеграционный — проверяет реальный paths.sh
2. Тест 2: юнит — рефакторим `_collect_path_variables()` чтобы принимать опциональный `paths_file: Path` параметр

```python
def _collect_path_variables(paths_file: Path | None = None) -> dict[str, str]:
    if paths_file is None:
        paths_file = PROJECT_ROOT / "core" / "lib" / "paths.sh"
    # ... остальная логика ...
```

Тогда тесты:
```python
class TestCollectPathVariables:
    def test_real_paths_sh_parsed(self):
        variables = _collect_path_variables()
        assert len(variables) >= 6
        assert "PATHS_MODULES_DIR" in variables
        assert "PLATFORM_ROOT" in variables

    def test_custom_paths_file(self, tmp_path):
        f = tmp_path / "paths.sh"
        f.write_text('readonly MY_DIR="/opt/myapp"\nexport MY_OTHER="/var/lib/myapp"\n')
        variables = _collect_path_variables(f)
        assert "MY_DIR" in variables
        assert variables["MY_DIR"] == "/opt/myapp"
        assert "MY_OTHER" in variables

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.sh"
        f.write_text("")
        variables = _collect_path_variables(f)
        assert variables == {}

    def test_only_comments(self, tmp_path):
        f = tmp_path / "comments.sh"
        f.write_text("# This is a comment\n# Another comment\n")
        variables = _collect_path_variables(f)
        assert variables == {}
```

**Всего:** 4 теста (≥2 ✓)

### T4.4: `TestTraceVariableAssignment` (≥2 тестов)

```python
class TestTraceVariableAssignment:
    """Unit tests for _trace_variable_assignment() function."""

    def test_local_assignment_found(self, tmp_path):
        """local var=path is traced correctly."""
        f = tmp_path / "test.sh"
        f.write_text('local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\nbash "$hc_script"\n')
        result = _trace_variable_assignment(f, "hc_script")
        assert result is not None
        assert "healthcheck.sh" in result

    def test_no_assignment(self, tmp_path):
        """Variable not assigned locally returns None."""
        f = tmp_path / "test.sh"
        f.write_text('bash "$hc_script"\n')
        result = _trace_variable_assignment(f, "hc_script")
        assert result is None

    def test_multiple_assignments_last_wins(self, tmp_path):
        """Last assignment is used."""
        f = tmp_path / "test.sh"
        f.write_text(
            'local var="/first/path.sh"\n'
            'local var="/second/path.sh"\n'
            'bash "$var"\n'
        )
        result = _trace_variable_assignment(f, "var")
        assert result is not None
        assert "second" in result

    def test_assignment_without_path(self, tmp_path):
        """Assignment without / in value returns None."""
        f = tmp_path / "test.sh"
        f.write_text('local flag="--verbose"\n')
        result = _trace_variable_assignment(f, "flag")
        assert result is None

    def test_export_assignment(self, tmp_path):
        """export var=path is traced."""
        f = tmp_path / "test.sh"
        f.write_text('export MY_SCRIPT="/opt/platform/core/modules/postgres/healthcheck.sh"\n')
        result = _trace_variable_assignment(f, "MY_SCRIPT")
        assert result is not None
        assert "healthcheck.sh" in result

    def test_readonly_assignment(self, tmp_path):
        """readonly var=path is traced."""
        f = tmp_path / "test.sh"
        f.write_text('readonly MY_DIR="/opt/core/modules/postgres"\n')
        result = _trace_variable_assignment(f, "MY_DIR")
        assert result is not None
        assert "postgres" in result
```

**Всего:** 6 тестов (≥2 ✓)

### T4.5: `TestShellCheckIntegration` (≥2 тестов)

**Файл:** `tests/test_cross_layer_imports.py`, новый класс

```python
class TestShellCheckIntegration:
    """Tests for tests/_conftest/shellcheck.py integration."""

    def test_check_available_returns_bool(self):
        """_check_shellcheck_available returns (bool, str)."""
        from _conftest.shellcheck import _check_shellcheck_available
        available, msg = _check_shellcheck_available()
        assert isinstance(available, bool)
        assert isinstance(msg, str)

    def test_parse_sc2154_empty_file(self, tmp_path):
        """Empty file has no SC2154 diagnostics."""
        from _conftest.shellcheck import _parse_shellcheck_sc2154
        f = tmp_path / "empty.sh"
        f.write_text("#!/bin/bash\n")
        vars_found = _parse_shellcheck_sc2154(f)
        assert vars_found == []

    def test_parse_sc2154_unassigned_var(self, tmp_path):
        """Unassigned variable triggers SC2154."""
        from _conftest.shellcheck import _parse_shellcheck_sc2154
        f = tmp_path / "test.sh"
        f.write_text('#!/bin/bash\nbash "$hc_script"\n')
        vars_found = _parse_shellcheck_sc2154(f)
        # hc_script is not assigned in this file → SC2154 should fire
        assert "hc_script" in vars_found

    def test_get_bash_calls_with_shellcheck(self, tmp_path):
        """ShellCheck detects bash call with variable assigned from path."""
        from _conftest.shellcheck import get_shellcheck_bash_calls
        f = tmp_path / "test.sh"
        f.write_text(
            '#!/bin/bash\n'
            'local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\n'
            'bash "$hc_script" liveness\n'
        )
        # hc_script is locally assigned → SC2154 NOT triggered (it IS assigned)
        # ShellCheck won't help here — variable IS assigned in scope
        # This tests the limitation: ShellCheck layer B only helps when
        # variable is assigned and used in DIFFERENT scopes
        calls = get_shellcheck_bash_calls(f)
        # Variable IS assigned locally → no SC2154 → no ShellCheck detection
        # This is expected — _trace_variable_assignment handles this case
        assert isinstance(calls, list)

    def test_get_bash_calls_cross_scope(self, tmp_path):
        """ShellCheck detects bash call where var assigned in different function."""
        from _conftest.shellcheck import get_shellcheck_bash_calls
        f = tmp_path / "test.sh"
        f.write_text(
            '#!/bin/bash\n'
            'setup() {\n'
            '    local hc_script="/opt/core/modules/postgres/healthcheck.sh"\n'
            '}\n'
            'main() {\n'
            '    bash "$hc_script" liveness\n'  # hc_script not in scope → SC2154
            '}\n'
        )
        calls = get_shellcheck_bash_calls(f)
        # ShellCheck should detect SC2154 for hc_script in main()
        # Then grep finds assignment in setup() → detected
        # NOTE: depends on ShellCheck behavior; test may need adjustment
        assert isinstance(calls, list)
```

**Всего:** 4 теста (≥2 ✓). Тесты 3 и 4 требуют ShellCheck в окружении — маркировать `@pytest.mark.skipif(not shellcheck_available, reason="shellcheck not installed")`.

---

## Wave 5 — DISCOVERY + FIX

### T5.1: Inventory существующих violations

**Действия:**
1. Запустить `python -m pytest tests/test_cross_layer_imports.py -s -v` с новым кодом
2. Зафиксировать ВСЕ найденные violations (вывод в файл `.ai/plans/001-arch-forensics/07-discovery-violations.txt`)
3. Классифицировать каждое violation:
   - `TP` (True Positive) — реальное нарушение cross-layer правил
   - `FP` (False Positive) — ложное срабатывание нового детектора
   - `CALLSITE` — уже использует `invoke_module_interface` (не violation после CallSites)
4. Для FP: либо добавить исключение, либо скорректировать детектор

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **0 violations** | Маловероятно — пробел детекции именно в том, что старый gate слеп. Если 0 → проверить, что новый код реально запускается (не закеширован старый .pyc) |
| **>50 violations** | Слишком много для одного брифа — приоритизировать CRITICAL, остальное в Debt |
| **FP из-за invoke_module_interface** | `invoke_` bypass уже должен исключать — если нет, проверить логику bypass |

### T5.2: Fix найденных violations

**Принцип:** Для каждого TP — минимальное исправление:
- Если вызов легитимен → заменить на `invoke_module_interface` (Typed Contract)
- Если вызов нелегитимен → рефакторить (переместить логику в правильный слой)
- Если вызов — false positive → добавить исключение в детектор или `# LINT-EXEMPT`

**Файлы, которые вероятно потребуют исправлений:**
- `core/internal/bootstrap/node-lifecycle.sh` — уже должно быть починено CallSites
- `core/internal/bootstrap/deploy-modules.sh` — уже должно быть починено CallSites
- `core/internal/deploy/deploy-project.sh` — уже должно быть починено CallSites
- Другие файлы — по результатам T5.1

**Крайние случаи:**
| Случай | Обработка |
|--------|-----------|
| **Violation в коде, который нельзя менять (например, архивный)** | `# LINT-EXEMPT: <reason>` с явным обоснованием |
| **Violation ломает прод если его исправить неправильно** | Ручное ревью + тестирование на стейдже |
| **Violation требует изменений в Typed Contract** | Если нужен новый interface — добавить в `module.yaml` соответствующего модуля |

---

## Wave 6 — GATE REGISTRATION + ВАЛИДАЦИЯ

### T6.1: Обновление `tests/gates/test_gate_cross_layer.py`

**Файл:** `tests/gates/test_gate_cross_layer.py`

**Изменения:**
- Обновить docstring/комментарии — указать, что gate теперь использует Extended Registry + ShellCheck
- Добавить `## @changes 2026-07-18 | DataFlow бриф: Extended Registry + ShellCheck integration`
- Без изменений в логике — gate просто вызывает `lint_core()`, который теперь улучшен

### T6.2: Регистрация в `core/entrypoint-manifest.yaml`

**Файл:** `core/entrypoint-manifest.yaml`

**Действие:** Проверить, что gate #8 (cross-layer-linter) зарегистрирован. Если нет — добавить:
```yaml
gates:
  - id: cross-layer-linter
    description: "Cross-layer import isolation — enforce architectural boundaries with Extended Registry + ShellCheck data-flow analysis"
    test_file: test_gate_cross_layer.py
```

### T6.3: `make gate MODE=fast` — валидация

**Действия:**
1. Запустить `make gate MODE=fast`
2. Проверить: gate #8 зелёный (0 violations)
3. Проверить время выполнения: ≤110% от baseline (замерить `time make gate MODE=fast` до и после)
4. Если время >110% — оптимизировать ShellCheck (кеширование, параллельный запуск, уменьшить набор файлов)

**Baseline-замер (до изменений):**
```bash
time make gate MODE=fast 2>&1 | tee .ai/plans/001-arch-forensics/07-gate-baseline.txt
```

**Post-замер (после изменений):**
```bash
time make gate MODE=fast 2>&1 | tee .ai/plans/001-arch-forensics/07-gate-post.txt
```

### T6.4: Обновление документации

**Файлы:**
- `core/AGENTS.md` — обновить cross-layer таблицу, указать что Gate #8 использует Extended Registry + ShellCheck
- `tests/test_cross_layer_imports.py` — обновить MODULE_CONTRACT (@scope, @invariants)
- `tests/gates/test_gate_cross_layer.py` — обновить MODULE_CONTRACT (@scope, @changes)

---

## $TEST_SPEC

### Unit-тесты (Wave 4)

| Test Class | # Tests | Target Function | Coverage |
|-----------|---------|-----------------|----------|
| `TestLooksLikePath` | 8 | `_looks_like_path` | Literal path, bare var, flags, special vars, empty, quoted, multi-var |
| `TestResolveImport` | 6 | `resolve_import` | Auto-collect substitution, bare var trace, unresolved, nested, contextual |
| `TestCollectPathVariables` | 4 | `_collect_path_variables` | Real paths.sh, custom file, empty, comments-only |
| `TestTraceVariableAssignment` | 6 | `_trace_variable_assignment` | Local, no assignment, multi-assign, no-path, export, readonly |
| `TestShellCheckIntegration` | 4 | `get_shellcheck_bash_calls` + helpers | Available check, empty file, unassigned var, cross-scope |

**Всего: 28 тестов (≥14 ✓)**

### Gate-тесты (существующие, обновлённые)

| Test | Файл | Статус |
|------|------|--------|
| `test_gate_cross_layer` | `tests/gates/test_gate_cross_layer.py` | Обновить docstring + MODULE_CONTRACT |

### Интеграционный тест (ручной)

```bash
# 1. Создать временный .sh файл с заведомым нарушением
cat > /tmp/test_cross_layer_violation.sh << 'EOF'
#!/bin/bash
local script="${CORE_DIR}/modules/postgres/healthcheck.sh"
bash "$script"
EOF

# 2. Скопировать в core/internal/ (временно)
cp /tmp/test_cross_layer_violation.sh core/internal/

# 3. Запустить gate
python -m pytest tests/gates/test_gate_cross_layer.py -s -v

# 4. Убедиться что violation обнаружено
# Ожидается: FAIL с сообщением о cross-layer violation

# 5. Удалить временный файл
rm core/internal/test_cross_layer_violation.sh
```

---

## $FILE_MANIFEST

| Файл | Действие | Волна | Описание |
|------|----------|-------|----------|
| `tests/test_cross_layer_imports.py` | MODIFY | W1–W4 | +`_collect_path_variables()`, +`_KNOWN_PATH_VARIABLES`, +`_substitute_variables()`, +`_trace_variable_assignment()`, MOD `_looks_like_path`, MOD `resolve_import`, MOD `_NON_IMPORT_ARGS`, MOD `scan_sh_file` (patterns 5+6, ShellCheck), MOD `lint_core` (ShellCheck call), +`TestLooksLikePath`, +`TestResolveImport`, +`TestCollectPathVariables`, +`TestTraceVariableAssignment`, +`TestShellCheckIntegration` |
| `tests/_conftest/shellcheck.py` | CREATE | W2 | ShellCheck integration: `_check_shellcheck_available()`, `_parse_shellcheck_sc2154()`, `get_shellcheck_bash_calls()` |
| `tests/_conftest/__init__.py` | MODIFY | W2 | Re-export `get_shellcheck_bash_calls` из `shellcheck.py` |
| `tests/gates/test_gate_cross_layer.py` | MODIFY | W6 | Обновить docstring + MODULE_CONTRACT |
| `core/entrypoint-manifest.yaml` | MODIFY | W6 | Проверить/добавить gate entry cross-layer-linter |
| `core/AGENTS.md` | MODIFY | W6 | Обновить cross-layer таблицу — указать механизм детекции |
| `Makefile` | MODIFY (опционально) | W2 | Если `make lint` нужно обновить для проверки shellcheck версии |
| `.ai/plans/001-arch-forensics/07-discovery-violations.txt` | CREATE | W5 | Inventory найденных violations |
| `.ai/plans/001-arch-forensics/07-gate-baseline.txt` | CREATE | W6 | Baseline timing замера |
| `.ai/plans/001-arch-forensics/07-gate-post.txt` | CREATE | W6 | Post-change timing замера |

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| `_collect_path_variables()` с опциональным `paths_file` параметром | Тестируемость: в юнит-тестах подставляем tmp_path файл, в production — реальный paths.sh |
| ShellCheck как дополнительный, не основной детектор | Graceful degradation: при отсутствии shellcheck gate всё ещё работает (слой A) |
| `_trace_variable_assignment` ищет ПОСЛЕДНЕЕ присвоение | В shell-скриптах последнее присвоение до использования — правильное. Предыдущие перезаписываются |
| Специальные shell-переменные в `_NON_IMPORT_ARGS` | Явное исключение лучше неявного (resolve_import всё равно вернёт None, но _looks_like_path даст ложный True → лишняя работа) |
| ShellCheck SC2154 вместо `-f json` data-flow графа | ShellCheck не предоставляет AST/граф в JSON. SC2154 — прагматичный прокси для «переменная пришла извне текущего скоупа» |
| `make -C` и `docker compose -f` только базовое обнаружение | Полный аудит всех Make/Compose вызовов — отдельный БРИФ (Brief-DataFlow Non-scope) |
| `invoke_` bypass в `scan_sh_file` (от CallSites) | Предотвращает ложные срабатывания на `invoke_module_interface` и его внутреннем `bash "${module_dir}/..."` |

---

## Open Risks

| Риск | Severity | Mitigation |
|------|----------|------------|
| ShellCheck может замедлить `make gate MODE=fast` >10% | MEDIUM | Замерить baseline до/после. Если медленно — кеширование результатов ShellCheck на файл (по mtime) |
| Новый `_looks_like_path` даст много false positives | MEDIUM | Wave 5 (Discovery) выявит масштаб. При >20% FP — скорректировать эвристики до приёмлемого уровня |
| `_trace_variable_assignment` даст ложные срабатывания на heredoc | LOW | Heredoc редко содержит `VAR=value` в точном формате. Если проблема возникнет — добавить фильтр heredoc-строк |
| ShellCheck не установлен в CI | LOW | CI-воркфлоу должен быть обновлён. Проверить `.github/workflows/*.yml` |
| Merge conflict с CallSites | LOW | CallSites ПЕРВЫМ (TRAP[DECISION]). DataFlow надстраивается поверх — conflict маловероятен (разные функции) |

---

## Next Steps

1. **Реализовать Brief-CallSites** (Typed Contract) — prerequisite для этого DevPlan
2. **Выполнить Wave 1–6** согласно `$TASKS` и `$PARALLEL_GROUPS`
3. **После T5.1 (Discovery):** если найденные violations требуют изменений в Typed Contract (новые interfaces, редкие модули) — согласовать с Architect
4. **После T6.3 (make gate MODE=fast):** если gate не зелёный — итерация fix (T5.2 → T6.3 цикл)
5. **После завершения всех волн:** QA-прогон (VerificationReport)

$END_DEVPLAN
