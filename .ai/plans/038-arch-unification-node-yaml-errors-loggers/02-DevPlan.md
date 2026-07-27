$START_DEVPLAN

## ⚠️ SUPERSEDED — 2026-07-26

This parent DevPlan has been **decomposed** into three child plans:
- **[038a-DevPlan.md](038a-DevPlan.md)** — Wave 1: Unified NodeYaml Facade + Typed Exceptions
- **[038b-DevPlan.md](038b-DevPlan.md)** — Waves 2+3+4: sys.exit removal + loggers + typed exceptions
- **[038c-DevPlan.md](038c-DevPlan.md)** — Wave 5: Inline python3 cleanup

This parent document is preserved as an **architectural reference** (Superposition Analysis S6-S10, API Design examples, Before/After contracts) and **MUST NOT be used for implementation**. Use the child plans instead.

All CRITICAL path drift issues from VerificationReport 02 have been resolved in the child plans.

# DevPlan 038 — Архитектурная унификация: node.yaml, exceptions, loggers, inline python3

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Унификация пяти архитектурных проблем: (1) 7 паттернов чтения node.yaml → единый фасад, (2) `sys.exit()` из библиотечных функций project_registry.py → return codes/exceptions, (3) 16 hardcoded имён логгеров → `__name__`, (4) 4 стратегии обработки ошибок → типизированные исключения, (5) ~20 оставшихся inline python3 → вызов Python-модулей |
| **DESCRIPTION** | Провести superposition-анализ стратегий унификации, выбрать поэтапный подход (Option B), спроектировать API единого фасада `node_yaml.py`, иерархию исключений, и пошаговый план замены всех 77+ точек чтения YAML, 16 логгеров, 14 `sys.exit()`, 94 `except Exception` блоков и 20 inline python3 |
| **RATIONALE** | Фрагментация чтения node.yaml (7 паттернов в 60+ файлах) создаёт maintenance burden и drift risk. `sys.exit()` в библиотечных функциях блокирует переиспользование. Hardcoded имена логгеров нарушают конвенцию Python и усложняют отладку. Четыре стратегии обработки ошибок создают inconsistency — нельзя предсказать поведение модуля при сбое. Inline python3 нарушают языковую политику и не дают LDD-телеметрии. Единая архитектурная унификация устраняет все 5 проблем как взаимосвязанные (DRY-first, contracts, fail-fast) |
| **ACCEPTANCE_CRITERIA** | 1. Все 77 `yaml.safe_load` для node.yaml проходят через единый фасад `node_yaml.py`. 2. `project_registry.py` не содержит `sys.exit()` в библиотечных функциях. 3. Все 16 логгеров используют `__name__`. 4. Определена иерархия из 5 типизированных исключений, заменяющих `RuntimeError` и `except Exception`. 5. 0 активных inline `python3 -c "import yaml"` в shell-скриптах (кроме `python_deps.sh` — легитимная проверка). 6. `make gate MODE=fast` passes. 7. CI `check-no-new-inline-python3` hook не регрессирует |
| **IMPLEMENTS** | Brief 038 — Архитектурная унификация (расширенный) |
| **IMPACTS** | `core/internal/shared/` (новый unified node_yaml.py, exceptions.py), `core/internal/` (30+ Python файлов), `core/lib/` (yaml_read.sh, node-resolver.sh), `core/entrypoints/` (3-5 shell entrypoints), `core/internal/scaffold/` (3 shell-скрипта), `core/internal/validate/`, `core/internal/verify/`, `core/modules/` (status-page, postgres-hooks), `tests/` (новые тесты фасада) |
| **REQUIRES** | DevPlan 070 (shared libs extraction) — COMPLETED (node_yaml.py, content_hash.py существуют), DevPlan 079 (bootstrap pipeline) — COMPLETED (структура lifecycle/deploy/ существует), языковая политика AGENTS.md, Python 3.10+, PyYAML, pytest |

---

## Debt Intake

Перед проектированием проведён аудит существующих TRAP и DEBT в зоне изменений:

| Источник | TRAP/DEBT | Решение |
|----------|-----------|---------|
| `yaml_read.sh:38` | `## @rationale` — 13 inline python3 calls | **IN_SCOPE**: замена на вызов фасада node_yaml.py |
| `yaml_query.py:157` | TRAP[DESIGN] — `--stdin` добавляет второй режим ввода | **DEFER**: решается в рамках унификации фасада (фасад будет единой точкой входа) |
| `yaml_query.py:214` | TRAP[BUG] — Python repr вместо JSON для dict/list | **IN_SCOPE**: фасад должен гарантировать JSON-сериализацию |
| `project_registry.py:12` | `## @invariants` — `sys.exit` задекларирован как фича | **IN_SCOPE**: замена на return codes + исключения |
| `bootstrap/yaml_helpers.py:11` | `## @invariants` — «Never raises: returns "" on any parse error» | **IN_SCOPE**: унификация с фасадом — выбрасывать типизированные исключения |
| `shared/docker_compose.py:17` | «Non-fatal: failures return False/empty» | **DEFER**: остаётся как есть (инфраструктурный слой, решение за caller) |
| `state_machine.py:1034` | `raise RuntimeError("node-lifecycle must run as root")` | **IN_SCOPE**: замена на `PlatformFatalError` |
| `steps.py:187` | `raise RuntimeError(f"apt-get install failed")` | **IN_SCOPE**: замена на `PlatformFatalError` |
| `check-no-new-inline-python3.sh` | Pre-commit hook для inline python3 | **IN_SCOPE**: обновить whitelist после миграции |
| `deploy.sh:111` | TRAP[DECISION] · `--format lines` vs inline python3 | **DEFER**: уже мигрировано в DevPlan 081 |

---

## Superposition Analysis

### Option A: Big Bang — все 5 проблем в одной волне

**Approach:** Один большой PR: создать unified `node_yaml.py` + `exceptions.py`, заменить все 77 точек чтения, 16 логгеров, 14 sys.exit, 94 except Exception, 20 inline python3 одновременно.

| Критерий | Оценка |
|----------|--------|
| Atomicity | ★★★★★ — единый коммит, нет промежуточных состояний |
| Risk | ★☆☆☆☆ — 60+ файлов в одном PR, невозможно протестировать изолированно |
| Rollback | ★☆☆☆☆ — `git revert` ломает все 5 подсистем одновременно |
| Reviewability | ★☆☆☆☆ — diff >5000 строк, review невозможен |
| Testability | ★★☆☆☆ — тесты нужно писать на финальное состояние, нет промежуточной валидации |
| Velocity | ★★☆☆☆ — блокирует всю работу на время рефакторинга |

**Verdict:** REJECTED — нарушает принцип Small Simple Blocks, невозможно сделать качественный code review.

---

### Option B: Поэтапно — 5 независимых волн (RECOMMENDED)

**Approach:** Каждая проблема решается в своей волне с независимым PR, тестами и верификацией.

| Wave | Проблема | Файлов | Риск |
|------|----------|--------|------|
| W1 | Фасад `node_yaml.py` | ~35 Python + ~10 shell | HIGH (breaking API) |
| W2 | `sys.exit()` → return codes | 1 (project_registry.py) + 3 callers | MEDIUM |
| W3 | Hardcoded loggers → `__name__` | 16 | LOW (search-replace) |
| W4 | Error handling → typed exceptions | ~30 (все `except Exception`) | MEDIUM |
| W5 | Inline python3 cleanup | ~13 shell-файлов | LOW |

| Критерий | Оценка |
|----------|--------|
| Atomicity | ★★★★☆ — каждая волна атомарна для своей проблемы |
| Risk | ★★★★☆ — изолированные PR, независимый rollback |
| Rollback | ★★★★★ — `git revert` одного PR не затрагивает остальные |
| Reviewability | ★★★★☆ — каждый PR <500 строк diff |
| Testability | ★★★★★ — тесты добавляются инкрементально на каждой волне |
| Velocity | ★★★★☆ — волны можно делать параллельно (W2+W3+W4, W5 зависит от W1) |

**Verdict:** SELECTED — см. §Design Decisions.

---

### Option C: Минимально — только фасад node.yaml (Problem 1)

**Approach:** Только W1 из Option B. Problems 2-5 не решаются.

| Критерий | Оценка |
|----------|--------|
| Scope | ★★★☆☆ — решает главную проблему, но оставляет inconsistency |
| Risk | ★★★★☆ — минимальный risk surface |
| Technical debt | ★☆☆☆☆ — 4 проблемы остаются, нужны ещё DevPlans |

**Verdict:** REJECTED — оставляет 4 architectural smell'а. Неэффективно: новый фасад требует консистентной стратегии ошибок, которую даёт W4.

---

### Option D: Двухволновой — W1 (фасад) + W2-5 (всё остальное вместе)

**Approach:** Первая волна — фасад node.yaml. Вторая волна — всё остальное одним PR.

| Критерий | Оценка |
|----------|--------|
| Баланс | ★★★☆☆ — компромисс между Option A и Option B |
| Risk W2 | ★★☆☆☆ — W2 всё ещё большой PR (4 проблемы одновременно) |

**Verdict:** REJECTED — W2 слишком большой. Разделение на 4 подволны (Option B) безопаснее.

---

### Option E: Risk-prioritized — не-breaking изменения сначала

**Approach:** W1: loggers (W3, non-breaking) + inline python3 (W5) → W2: exceptions (W4) → W3: фасад node.yaml (W1) + sys.exit (W2).

| Критерий | Оценка |
|----------|--------|
| Risk | ★★★★☆ — не-breaking сначала, breaking потом |
| Dependency | ★★☆☆☆ — W3 фасада не может использовать новые исключения из W2 |

**Verdict:** REJECTED — W3 (фасад) логически зависит от W2 (типизированные исключения). Инвертированный порядок создаёт rework.

---

### S6: Гранулярность волн

Анализ: почему именно 5 волн (не 3, не 7) и какие проблемы НЕЛЬЗЯ решать в одной волне.

**Граф зависимостей между волнами:**
- **W1 (фасад)** — обязательный фундамент для W4 и W5 (typed exceptions импортируются фасадом, CLI фасада вызывается shell)
- **W2 (sys.exit removal)** — полностью независим от всех
- **W3 (logger names)** — полностью независим от всех
- **W4 (typed exceptions)** — зависит от W1 (фасад использует PlatformError subtypes), зависит от W2 (логика project_registry выбрасывает исключения вместо sys.exit)
- **W5 (inline python3)** — зависит от W1 (shell-скрипты вызывают CLI фасада)

**Почему не 3 волны:**

| Вариант слияния | Проблема |
|----------------|----------|
| W1 + (W2+W3) + (W4+W5) | W4+W5 = ~40 файлов — PR слишком большой |
| (W1+W4) + W2 + W3 + W5 | W1+W4 вместе = breaking change + новая exception hierarchy = diff >2000 строк |
| W1 + W2 + W3 + (W4+W5) | W4 зависит от W1 (импорт PlatformError), W5 зависит от W1 (CLI), но W4 и W5 независимы друг от друга — их можно было бы объединить, но это 40+ файлов = высокий риск конфликтов при ревью |

**Почему не 7 волн:**
- Разделение W1 (фасад) на подволны (Python API → CLI → shell migration) дало бы 3 зависимых PR: каждый не проходит gate без предыдущего, review overhead растёт, выгода нулевая.
- Разделение W4 (исключения) на подволны (RuntimeError → typed, затем except Exception → typed) создаёт неконсистентное промежуточное состояние: часть кода выбрасывает typed exceptions, часть ловит RuntimeError.

**Вывод:** 5 волн — оптимум между granularity (каждая волна <500 строк diff) и dependency overhead (минимальные cross-PR ожидания). W2+W3 можно параллелить.

---

### S7: Rollback surface analysis

Для каждого Option подсчитано количество файлов, подлежащих откату при обнаружении regression после мёржа:

| Option | Files to revert | Consumers affected | Rollback cost |
|--------|----------------|-------------------|---------------|
| A (Big Bang) | 60+ | Все 5 подсистем, 30+ Python + 20 shell | ★☆☆☆☆ — полная блокировка платформы на время revert |
| B (5 waves) | W1: ~35, W2: ~4, W3: 16, W4: ~30, W5: ~12 | Wave-specific. При откате W1 страдают W4,W5 (depends) | ★★★★★ — независимый revert каждой волны |
| C (только W1) | ~35 | Только W1 (остальные проблемы не решаются) | ★★★★☆ — 1 revert, но 4 проблемы остаются |
| D (2 waves) | W1: ~35, W2: 60+ | W2 затрагивает все 4 оставшиеся проблемы | ★★☆☆☆ — W2 revert затрагивает 60+ файлов |
| E (risk-prioritized) | W1: ~16, W2: ~30, W3: ~35 | W3 (фасад) зависит от W2 (исключения) | ★★★☆☆ — каскадный revert при проблеме в W2 |

**Matrix: кто затронут при откате каждой волны (Option B):**

| Откатываемая волна | W1 consumers | W2 consumers | W3 consumers | W4 consumers | W5 consumers |
|-------------------|-------------|-------------|-------------|-------------|-------------|
| W1 (фасад) | ALL (26 py + 8 sh) | 0 | 0 | ALL (depends: typed exceptions) | ALL (depends: CLI facade) |
| W2 (sys.exit) | 0 | 1 py + 3 sh | 0 | 0 | 0 |
| W3 (logger) | 0 | 0 | 16 py | 0 | 0 |
| W4 (exceptions) | 0 | 0 | 0 | ALL (~30 files) | 0 |
| W5 (inline py3) | 0 | 0 | 0 | 0 | ~13 shell |

**Вывод:** Option B минимизирует rollback surface. Единственная не-независимая волна — W1: её откат ломает W4 и W5, что ожидаемо (W4 и W5 — потребители W1). Стратегия: W1 должен пройти extended review и staging-test перед мержем.

---

### S8: Coverage gap analysis

Сценарии использования node.yaml, НЕ покрываемые предложенным API фасада:

| Сценарий | Покрытие | Вердикт |
|----------|----------|---------|
| Чтение (get, get_list, get_context, get_projects, get_modules, get_domain_config, get_node_info) | ✅ Полное | — |
| Валидация структуры (validate) | ✅ Полное | — |
| Кэширование + lazy load | ✅ | — |
| CLI для shell consumers | ✅ | — |
| **Partial update** (изменение одного поля без перезаписи всего файла) | ❌ | **Осознанный out-of-scope** — фасад read-only. Запись — отдельная ответственность (register/deregister project). |
| **Atomic write** (read→modify→write как транзакция) | ❌ | **Out-of-scope** — текущие операции записи перезаписывают весь файл, что уже работает. Транзакционный write — отдельная задача. |
| **Multi-file queries** (чтение node.yaml + другого YAML как один объект) | ❌ | **Out-of-scope** — задача orchestrator'а высокого уровня, не фасада. |
| **Schema validation** (jsonschema против node.yaml) | ❌ | **Out-of-scope** — уже существует в `validate.sh`. |
| **Streaming / partial load** (чтение только нужной секции без полного парсинга) | ❌ | **Упущение (low severity)** — фасад всегда читает весь файл. Для node.yaml (1-50KB) overhead ничтожен. Если появятся файлы >1MB — нужен lazy section loading. |
| **Type coercion** | ✅ | Через `yaml.safe_load` по умолчанию |
| **Environment variable interpolation** (`${VAR}` в значениях) | ❌ | **Осознанный out-of-scope** — env-интерполяция обрабатывается на уровне compose. При необходимости — отдельный метод `load_with_env()`. |

**Вывод:** Критических упущений нет. Единственный потенциальный gap — streaming/lazy section load для файлов >1MB — пока не актуален.

---

### S9: Migration safety

Для каждой волны — риск «частичной миграции» (когда часть consumers обновлена, а часть продолжает использовать старый паттерн) и механизм обнаружения:

| Wave | Partial migration risk | Описание | Detection mechanism |
|------|----------------------|----------|---------------------|
| **W1** (фасад) | **HIGH** | Два параллельных API: прямые `yaml.safe_load` и `NodeYaml`. Новый код использует фасад, старый — прямой вызов. | `grep-gate`: `grep 'yaml.safe_load' core/internal/` после W1 → FAIL если не 0 (кроме `yaml_query.py:_load_yaml` и `node_yaml.py`). `warnings.warn()` deprecation в старых функциях. |
| **W2** (sys.exit) | **MEDIUM** | Старый код с `sys.exit()` удалён. Shell-callers не сломаны (CLI-обёртка маппит return → exit code). | `grep 'sys.exit' project_registry.py` → только в `if __name__`. Проверка exit codes через тесты. |
| **W3** (логгеры) | **LOW** | Замена `"literal"` → `__name__` не меняет поведение. Mixed префиксы не ломают функциональность. | `grep 'getLogger("[a-z]'` → 0. |
| **W4** (исключения) | **MEDIUM** | Если не все `except Exception` обновлены — silent swallow продолжает маскировать ошибки. | `make check-exception-patterns` — новый gate: `grep -P 'except\s+Exception' core/internal/ --include="*.py"` → только в `__main__`. |
| **W5** (inline) | **LOW** | Замена inline на CLI фасада не ломает shell (stdout-совместимость). | `check-no-new-inline-python3.sh` pre-commit hook + `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0. |

**Runtime warning для incomplete migration (W1):**
```python
# В старых функциях (extract_context_from_node_yaml, yaml_get_field):
import warnings
warnings.warn(
    "extract_context_from_node_yaml() is deprecated. Use NodeYaml(path).get_context() instead.",
    DeprecationWarning, stacklevel=2
)
```

**Вывод:** Основной риск — W1 (dual API в переходный период). `grep-gate` + `warnings.warn()` дают достаточную защиту. W2/W3/W5 — низкий риск. W4 — средний, покрывается новым gate.

---

### S10: Performance regression

Оценка overhead `NodeYaml` фасада для worst-case сценария: чтение node.yaml на каждый healthcheck-запрос (каждые 30 сек).

**Overhead на один вызов `.get()`:**

| Компонент | Прямой yaml.safe_load | NodeYaml фасад | Разница |
|-----------|----------------------|----------------|---------|
| `open()` + `yaml.safe_load()` | 1× (каждый caller читает сам) | 1× (lazy, всего один раз) | **−N× для N callers** |
| Dict lookup (top-level key) | 1× `data.get("key")` | 1× `self._data.get("key")` | 0 |
| Dict lookup (dotted key `a.b.c`) | 3× sequential | 3× sequential `_traverse()` | 0 |
| Class instantiation | 0 | 1× `NodeYaml(path)` | **+~5µs** (однократно) |
| Cache check (hasattr) | 0 | 1× `hasattr(self, '_data')` | **+~0.1µs** |
| Validation | 0 | 1× (только при явном вызове validate()) | 0 (lazy) |
| Exception handling | bare `except Exception` | typed `except PlatformError` | 0 (одинаковый try/catch cost) |
| CLI subprocess overhead | 0 (inline python3) | 0 (Python callers напрямую) | 0 |

**Worst-case: healthcheck каждые 30 сек (96 раз/день)**
- **Без фасада:** каждый healthcheck читает node.yaml с диска = ~2ms read + ~5ms parse = 7ms × 96 = **~672ms/day**
- **С фасадом:** lazy load при первом healthcheck (7ms), кэш на все последующие (96×0.5µs). **~7.05ms/day**
- **Экономия: ~665ms/day (99% reduction)**

**Дополнительные сценарии:**
- `reload()` — полный reread при register/deregister (единицы раз в день) — те же 7ms
- Shell consumers через CLI: subprocess overhead ~5ms на запуск Python-module. Заменяет inline python3 (~3ms). **Overhead +2ms per call** — приемлемо для CI-операций (не real-time)
- Количество dict lookups на `.get(a.b.c)`: ровно 3 — столько же, сколько прямой `data["a"]["b"]["c"]`. **0 дополнительных lookup'ов**

**Вывод:** Overhead фасада ≈ 0 в реальных условиях. Lazy load + cache дают net-positive performance gain (99% reduction в worst-case). CLI subprocess overhead (+2ms) ничтожен для CI-сценариев.

---

### Выбранная стратегия: **Option B — 5 Waves**

| Wave | Название | Priority | Зависимости |
|------|----------|----------|-------------|
| **W1** | Unified `node_yaml.py` Facade | P0 | None |
| **W2** | `project_registry.py` — remove `sys.exit()` | P1 | None (независима) |
| **W3** | Hardcoded loggers → `__name__` | P2 | None (независима) |
| **W4** | Typed exception hierarchy | P1 | W1 (фасад использует новые исключения) |
| **W5** | Inline python3 cleanup | P2 | W1 (shell-скрипты вызывают фасад) |

**Параллелизм:**
- W1 → блокирует W4, W5
- W2 || W3 (независимы, можно параллельно)
- W4 → зависит от W1
- W5 → зависит от W1

**Рекомендуемая последовательность:**
1. W1 (фасад) — первый, потому что это breaking change
2. W2 + W3 параллельно (не-breaking, легко откатить)
3. W4 (typed exceptions) — использует фасад из W1
4. W5 (inline python3 cleanup) — использует фасад из W1

---

## §Design Decisions

### DD1: Почему Option B, а не Big Bang?

## @rationale
**Q:** Почему не сделать всё сразу одним PR?
**A:** 60+ файлов в одном PR невозможно качественно проревьювить. Diff >5000 строк создаёт риск пропуска regression. Каждая волна в Option B атомарна, тестируема изолированно, и может быть откачена независимо. Время на review 5×400 строк < время на review 1×5000 строк. Small Simple Blocks principle.

### DD2: Почему новый unified facade, а не расширение существующего `shared/node_yaml.py`?

## @rationale
**Q:** `core/internal/shared/node_yaml.py` уже существует — почему не расширить его?
**A:** Текущий `shared/node_yaml.py` содержит только `extract_context_from_node_yaml()` — одну функцию (67 строк). Unified facade будет содержать ~15 методов: `load()`, `get()`, `get_list()`, `get_projects()`, `get_modules()`, `get_domain_config()`, `get_context()`, `validate()`, `reload()`. Расширение до такого объёма — это практически новый модуль. Имя файла сохраняется (`shared/node_yaml.py`), существующая функция остаётся как `get_context()` alias для обратной совместимости.

### DD3: Почему типизированные исключения, а не `RuntimeError`?

## @rationale
**Q:** `RuntimeError` уже используется в `state_machine.py` и `steps.py` — зачем новые типы?
**A:** `RuntimeError` не несёт семантики. `except RuntimeError` ловит ВСЁ — и ошибку парсинга YAML, и ошибку сети, и precondition violation. Caller не может различить recoverable error от fatal error. 5 типизированных исключений решают это:
- `PlatformFatalError` — невосстановимая ошибка (root required, file not found)
- `ConfigParseError` — ошибка парсинга YAML/JSON (можно перечитать файл)
- `ConfigValidationError` — структурная ошибка (missing required key)
- `ConfigNotFoundError` — файл не найден (можно создать)
- `PlatformError` (base) — для generic случаев

### DD4: Почему `sys.exit()` в `project_registry.py` — проблема именно библиотечного кода?

## @rationale
**Q:** `sys.exit()` задекларирован в `@invariants` как фича для shell-совместимости. Почему это проблема?
**A:** `sys.exit()` прерывает процесс. Если `project_registry.register_project()` вызван из Python-кода (не CLI), он убивает весь процесс без возможности обработать ошибку. Правильный паттерн: функция возвращает `(success: bool, message: str)`, CLI-обёртка (`if __name__ == "__main__"`) вызывает `sys.exit()` на основе возврата. Shell callers проверяют exit code через `|| log_warn`, Python callers проверяют `Tuple[bool, str]`. Обратная совместимость: CLI сохраняет идентичные exit codes (0/1).

### DD5: Почему `except Exception` без re-raise — это проблема?

## @rationale
**Q:** `except Exception as e: return []` выглядит безопасно. Почему это architectural smell?
**A:** Silent swallow маскирует: (1) `KeyboardInterrupt` не caught (это `BaseException`, не `Exception`), но (2) `ImportError`, `AttributeError` (опечатка), `TypeError` (неправильный тип аргумента) — всё swallowed. Баги становятся невидимыми. Правильно: `except (yaml.YAMLError, FileNotFoundError, PermissionError) as e:` — ловить только ожидаемые типы, остальные пробрасывать. Типизированные исключения из W4 решают эту проблему системно.

---

## Unified `node_yaml.py` API Design

### Module location
`core/internal/shared/node_yaml.py` (расширение существующего)

### Module contract

```
# GREP_SUMMARY: node_yaml, unified-facade, yaml-reader, single-source-of-truth, caching, validation
# STRUCTURE: ▶ NodeYaml(path) → ◇ _load() → ⊕ cache → ◇ get(key) / get_list(key) / get_context() / get_projects() / get_modules() / get_domain_config() → ⎋ typed result | raise PlatformError subclass
```

### API Methods

| Метод | Возвращает | Описание | Заменяет |
|-------|-----------|----------|----------|
| `NodeYaml(path: str)` | `NodeYaml` | Конструктор, не читает файл (lazy) | Все прямые `open() + yaml.safe_load()` |
| `.load() → dict` | `dict` | Загружает и кэширует YAML. Выбрасывает `ConfigNotFoundError` / `ConfigParseError` | Все `yaml.safe_load(f)` |
| `.reload() → dict` | `dict` | Инвалидирует кэш и перечитывает | Для операций, изменяющих node.yaml (register/deregister) |
| `.get(key: str, default=None) → Any` | `Any` | Dotted-key доступ к полю. `None` → `ConfigValidationError` если default не задан | `yaml_get_field()`, `extract_yaml_field()`, `yaml_get()` |
| `.get_list(key: str) → list` | `list` | Dotted-key доступ к списку. Не-list → `ConfigValidationError` | `yaml_get_list()` |
| `.get_context() → str` | `str` | Извлекает context (string или contexts[0].name). Пусто → `""` | `extract_context_from_node_yaml()`, `_extract_domain_from_node_yaml()` |
| `.get_projects() → list[dict]` | `list[dict]` | Список проектов. Нет ключа → `[]` | `_parse_projects_yaml()`, все inline-чтения projects |
| `.get_modules() → list[dict]` | `list[dict]` | Список модулей. Нет ключа → `[]` | `parse_modules_from_node_yaml()`, `_parse_node_modules_yaml()` |
| `.get_domain_config() → DomainConfig` | `DomainConfig` | NamedTuple: domain, email, acme_dns_plugin, project_domains | `yaml_read_domain_config()`, `_extract_domains_from_yaml()` |
| `.get_repo_url() → str` | `str` | URL репозитория из `repos.core` | `_read_repo_url()` |
| `.get_node_info() → NodeInfo` | `NodeInfo` | NamedTuple: fqdn, owner_key, docker_mirror | `extract_yaml_field(node, owner_key)` + др |
| `.validate() → list[str]` | `list[str]` | Валидация структуры. Возвращает список ошибок (пустой = valid) | Ad-hoc проверки в `preflight.py` |
| `.raw() → dict` | `dict` | Доступ к сырому словарю (для обратной совместимости) | Прямой доступ к `data["key"]` после `yaml.safe_load` |

### NamedTuples

```python
class DomainConfig(NamedTuple):
    platform_domain: str
    email: str
    acme_dns_plugin: str
    project_domains: list[str]

class NodeInfo(NamedTuple):
    fqdn: str = ""
    owner_key: str = ""
    docker_mirror: str = ""
```

### Caching strategy

- Lazy load: первый вызов любого `.get*()` или `.load()` → чтение и парсинг YAML
- Кэш инвалидируется только через `.reload()` (для операций записи: register/deregister project)
- Потокобезопасность не требуется (однопоточный bootstrap pipeline)

### Backward compatibility aliases

```python
# Старые вызовы → новые
extract_context_from_node_yaml(path, log_tag) → NodeYaml(path).get_context()
extract_yaml_field(path, *field_path)          → NodeYaml(path).get(".".join(field_path))
yaml_get(path, key, default)                   → NodeYaml(str(path)).get(key, default)
yaml_get_field(yaml_path, dotted_key)          → via CLI: python3 node_yaml.py --file X --get Y
yaml_read_domain_config(node_yaml)             → NodeYaml(node_yaml).get_domain_config()
parse_modules_from_node_yaml(path)             → NodeYaml(path).get_modules()
```

### CLI Interface

```bash
# Прямые замены yaml_query.py и yaml_helpers.py CLI
python3 -m core.internal.shared.node_yaml --file node.yaml --get node.host
python3 -m core.internal.shared.node_yaml --file node.yaml --get projects --items
python3 -m core.internal.shared.node_yaml --file node.yaml --domain-config
python3 -m core.internal.shared.node_yaml --file node.yaml --context
python3 -m core.internal.shared.node_yaml --file node.yaml --validate
```

---

## API Contract Examples

### Python API: Before vs After

**1. Чтение простого поля**
```python
# До:
with open("/etc/platform/node.yaml") as f:
    data = yaml.safe_load(f)
host = data.get("node", {}).get("host", "")

# После:
node = NodeYaml("/etc/platform/node.yaml")
host = node.get("node.host", default="")
```

**2. Чтение контекста (самый частый паттерн)**
```python
# До (3 разных паттерна в коде):
# Вариант A: прямой yaml.safe_load + dict access
with open(path) as f:
    data = yaml.safe_load(f)
ctx = data.get("context", "")

# Вариант B: extract_context_from_node_yaml()
ctx = extract_context_from_node_yaml(path, log_tag="deploy")

# Вариант C: contexts[0].name fallback
with open(path) as f:
    data = yaml.safe_load(f)
ctx = data.get("context", "")
if not ctx and "contexts" in data:
    ctx = data["contexts"][0].get("name", "")

# После (единый):
node = NodeYaml(path)
ctx = node.get_context()  # string + array fallback встроен
```

**3. Чтение списка проектов с валидацией**
```python
# До:
try:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
except (FileNotFoundError, yaml.YAMLError) as e:
    logger.error(f"[IMP:9][deploy] Failed to read: {e}")
    return []

projects = data.get("projects", [])
if not isinstance(projects, list):
    logger.error(f"[IMP:9][deploy] projects is not a list")
    return []

# После:
try:
    node = NodeYaml(path)
    projects = node.get_projects()  # list[dict], пусто = []
except (ConfigNotFoundError, ConfigParseError) as e:
    logger.error(f"[IMP:9][deploy] Failed to read: {e}")
    return []
```

**4. Dotted-key доступ к вложенным полям**
```python
# До:
d = yaml.safe_load(f)
domain = d.get("domain", {})
platform_domain = domain.get("platform", "") if isinstance(domain, dict) else ""

# После:
node = NodeYaml(path)
platform_domain = node.get("domain.platform", default="")
```

**5. Domain config (составной объект)**
```python
# До:
domains = yaml_read_domain_config(node_yaml_path)
# или boilerplate:
data = yaml.safe_load(f)
platform_domain = data.get("domain", {}).get("platform", "")
email = data.get("domain", {}).get("email", "")
# ... 5+ строк

# После:
domains = NodeYaml(node_yaml_path).get_domain_config()
# DomainConfig(platform_domain="example.com", email="admin@...", ...)
```

### Shell API: Before vs After

**1. Чтение поля из node.yaml**
```bash
# До:
host=$(python3 -c "import yaml; print(yaml.safe_load(open('$NODE_YAML'))['node']['host'])")

# После:
host=$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML" --get node.host)
```

**2. Получение списка проектов (items mode)**
```bash
# До:
projects_json=$(python3 -c "
import yaml, json
d = yaml.safe_load(open('$NODE_YAML'))
print(json.dumps(d.get('projects', [])))
")

# После:
projects_json=$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML" --get projects --items)
```

**3. Domain config**
```bash
# До (функция yaml_read.sh):
yaml_read_domain_config() {
    python3 - <<'PYEOF'
import yaml, sys
...
PYEOF
}

# После (alias):
alias yaml_read_domain_config='python3 -m core.internal.shared.node_yaml --domain-config'
```

**4. Контекст**
```bash
# До:
context=$(python3 -c "
import yaml
d = yaml.safe_load(open('$NODE_YAML'))
ctx = d.get('context', '') or (d.get('contexts', [{}])[0].get('name', '') if 'contexts' in d else '')
print(ctx)
")

# После:
context=$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML" --context)
```

**5. Валидация**
```bash
# До:
errors=$(python3 -c "import yaml; ..." 2>&1)

# После:
errors=$(python3 -m core.internal.shared.node_yaml --file "$NODE_YAML" --validate 2>&1)
```

---

## Typed Exception Hierarchy

### Module location
`core/internal/shared/exceptions.py` (новый)

### Class hierarchy

```python
class PlatformError(Exception):
    """Base exception for all platform errors."""
    exit_code: int = 1

class ConfigNotFoundError(PlatformError):
    """Configuration file not found."""
    exit_code: int = 2

class ConfigParseError(PlatformError):
    """Configuration file parse error (YAML syntax, JSON decode)."""
    exit_code: int = 3

class ConfigValidationError(PlatformError):
    """Configuration structure validation error (missing key, wrong type)."""
    exit_code: int = 4

class PlatformFatalError(PlatformError):
    """Non-recoverable platform error (root required, preconditions)."""
    exit_code: int = 10
```

### Migration map

| Старый паттерн | Новый паттерн |
|----------------|---------------|
| `raise RuntimeError("node.yaml not found")` | `raise ConfigNotFoundError(f"node.yaml not found: {path}")` |
| `raise RuntimeError("apt-get failed")` | `raise PlatformFatalError(f"apt-get failed: {stderr}")` |
| `except Exception: return []` | `except (ConfigNotFoundError, ConfigParseError): return []` |
| `except yaml.YAMLError: return ""` | `except ConfigParseError: return ""` (фасад конвертирует) |
| `sys.exit(1)` в библиотеке | `return (False, "error message")` или `raise PlatformError` |

---

## Requirements Analysis & Key Success Criteria

1. **Единый фасад node_yaml.py** — все 77+ `yaml.safe_load` для node.yaml проходят через `NodeYaml` класс. Фасад покрыт unit-тестами (≥90% coverage).
2. **Библиотечная чистота project_registry.py** — 0 `sys.exit()` в функциях `register_project`, `deregister_project`, `list_projects`. CLI-обёртка (`__main__`) маппит return codes → exit codes.
3. **Консистентное логирование** — все 16 файлов используют `logging.getLogger(__name__)`. Лог-сообщения сохраняют прежний формат `[IMP:X][module]`.
4. **Типизированные исключения** — 5 классов исключений. Все `raise RuntimeError` заменены. Все `except Exception` сужены до ожидаемых типов.
5. **0 inline python3 с import yaml** — все shell-скрипты вызывают Python-модули или CLI фасада. CI hook `check-no-new-inline-python3` passes.

---

## Architecture Overview — Draft Code Graph

```
                   ┌─────────────────────────────┐
                   │  core/internal/shared/      │
                   │  ┌─ node_yaml.py (UNIFIED)  │
                   │  │  NodeYaml class          │
                   │  │  + load/reload/cache     │
                   │  │  + get/get_list/context  │
                   │  │  + get_projects/modules  │
                   │  │  + get_domain_config     │
                   │  │  + get_node_info         │
                   │  │  + validate              │
                   │  │  + CLI (--file/--get)    │
                   │  └──────────────────────────┘
                   │  ┌─ exceptions.py (NEW)     │
                   │  │  PlatformError (base)    │
                   │  │  + ConfigNotFoundError   │
                   │  │  + ConfigParseError      │
                   │  │  + ConfigValidationError │
                   │  │  + PlatformFatalError    │
                   │  └──────────────────────────┘
                   └─────────────────────────────┘
                                ▲
            ┌───────────────────┼───────────────────┐
            │                   │                   │
    ┌───────┴───────┐   ┌──────┴──────┐   ┌────────┴────────┐
    │ Python callers│   │ Shell libs  │   │ Shell entrypoints│
    │ (30+ files)   │   │ yaml_read.sh│   │ validate.sh      │
    │ state_machine │   │ node-resolv │   │ verify-domains.sh│
    │ cert_orchestr │   │   .sh       │   │ add-vhost.sh     │
    │ context_depl  │   └─────────────┘   │ remove-project.sh│
    │ reconciler    │                     │ deploy-project.sh│
    │ preflight     │                     └──────────────────┘
    └───────────────┘
```

### Data Flow (чтение node.yaml)

```
┌─ Caller ─┐     ┌─ NodeYaml Facade ───────────────┐     ┌─ File System ─┐
│           │     │                                  │     │                │
│  .get()   │────▶│ 1. Lazy load? → open + parse    │────▶│  node.yaml     │
│  .load()  │     │ 2. Cache in self._data           │     │                │
│  .reload()│     │ 3. Traverse dotted key           │     └────────────────┘
│           │◀────│ 4. Return typed value            │
└───────────┘     │ 5. On error → raise PlatformErr  │
                  └──────────────────────────────────┘
```

---

## Полный список всех мест для исправления

### Problem 1: node.yaml reading (W1 — Unified Facade)

#### P1.1 — Python файлы с прямым `yaml.safe_load` для node.yaml

| # | Файл | Строки | Что менять |
|---|------|--------|------------|
| 1 | `core/internal/shared/project_registry.py` | 64, 127, 180 | `open() + yaml.safe_load()` → `NodeYaml(path).load()` |
| 2 | `core/internal/shared/node_yaml.py` | 42-43 | Самореференс — рефакторинг в методы NodeYaml |
| 3 | `core/internal/bootstrap/yaml_helpers.py` | 72-73 | `open() + yaml.safe_load()` → `NodeYaml(path).load()` |
| 4 | `core/internal/bootstrap/preflight.py` | 465 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 5 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1727, 1740, 1922 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 6 | `core/internal/bootstrap/lifecycle/steps.py` | 439, 463 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 7 | `core/internal/bootstrap/deploy/context_deployer.py` | 192, 641 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 8 | `core/internal/bootstrap/deploy/context_overlay.py` | 129, 289 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 9 | `core/internal/bootstrap/s3_ssl_cache.py` | 314 | `yaml.safe_load(f) or {}` → `NodeYaml(path).load()` |
| 10 | `core/internal/bootstrap/converge/reconciler.py` | 682, 1197, 1313 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 11 | `core/internal/bootstrap/deploy/secrets_validator.py` | 71, 160, 220, 269, 322, 393, 457, 500 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 12 | `core/internal/bootstrap/deploy/compose_preflight.py` | 177 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 13 | `core/internal/bootstrap/deploy/spool_validator.py` | 159 | `yaml.safe_load(f) or {}` → `NodeYaml(path).load()` |
| 14 | `core/internal/bootstrap/lifecycle/secrets_manager.py` | 114 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 15 | `core/internal/reconciler_projects.py` | 132, 270 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 16 | `core/internal/provisioner.py` | 96 | `yaml.safe_load(f) or {}` → `NodeYaml(path).load()` |
| 17 | `core/internal/monitoring_config_renderer.py` | 185 | `yaml.safe_load(raw) or {}` → `NodeYaml(path).load()` |
| 18 | `core/internal/scaffold/gen_env_platform.py` | 40 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 19 | `core/internal/scaffold/vhost_yaml_reader.py` | 37 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 20 | `core/internal/scaffold/context_registry.py` | 45 | `yaml.safe_load(f) or {}` → `NodeYaml(path).load()` |
| 21 | `core/internal/healthcheck/platform_export_metrics.py` | 268 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 22 | `core/internal/healthcheck/metrics/cert_collector.py` | 183 | `yaml.safe_load(f) or {}` → `NodeYaml(path).load()` |
| 23 | `core/internal/healthcheck/metrics/project_collector.py` | 54 | `yaml.safe_load(f) or {}` → `NodeYaml(path).load()` |
| 24 | `core/modules/status-page/app.py` | 126 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 25 | `core/internal/llm/policy_schema.py` | 291 | `yaml.safe_load(f)` → `NodeYaml(path).load()` |
| 26 | `core/internal/scripts/yaml_query.py` | 83 | `yaml.safe_load(f)` — остаётся как low-level `_load_yaml()`, используется фасадом |

#### P1.2 — Shell-файлы с inline python3 + import yaml

| # | Файл | Строки | Что менять |
|---|------|--------|------------|
| 27 | `core/lib/yaml_read.sh` | 133-146 | `yaml_read_domain_config()` → `python3 -m core.internal.shared.node_yaml --domain-config` |
| 28 | `core/lib/node-resolver.sh` | 302-310 | `python3 -c "import yaml..."` → `python3 -m core.internal.shared.node_yaml --get` |
| 29 | `core/internal/scaffold/remove-project.sh` | 162-174 | `python3 -c "import yaml..."` → CLI фасада |
| 30 | `core/internal/scaffold/adopt-project.sh` | 399-427 | `python3 -c` blocks → CLI фасада |
| 31 | `core/internal/verify/verify-domains.sh` | 106-118 | `python3 -c "import yaml..."` → CLI фасада |
| 32 | `core/internal/validate/validate.sh` | 97-107 | `python3 - ... <<'PYEOF'` (schema validation) → остаётся (не node.yaml) |
| 33 | `core/internal/catalog/generate-catalog.sh` | 40-84 | `python3 - ... <<'PYEOF'` → CLI фасада |
| 34 | `core/modules/postgres/hooks/on-project-deploy.sh` | 43-46 | `python3 -c` → CLI фасада |

#### P1.3 — Shell-файлы с использованием `NODE_YAML_PATH` напрямую

| # | Файл | Строки | Что менять |
|---|------|--------|------------|
| 35 | `core/internal/bootstrap/converge.sh` | 18, 49, 57, 59-61, 102, 108, 118 | `NODE_YAML_PATH` переменная → использовать `NodeYaml(path)` в Python |
| 36 | `core/internal/scaffold/add-vhost.sh` | 733-739 | `export NODE_YAML_PATH` → передавать путь как аргумент CLI |
| 37 | `core/internal/healthcheck/platform_export_metrics.py` | 17, 43 | `os.environ.get("NODE_YAML_PATH")` → `NodeYaml(path)` |
| 38 | `core/modules/status-page/app.py` | 71, 649, 1060 | `NODE_YAML_PATH` константа → `NodeYaml(path)` |
| 39 | `core/internal/healthcheck/metrics/cert_collector.py` | 303 | `os.environ.get("NODE_YAML_PATH")` → `NodeYaml(path)` |

#### P1.4 — Shell-файлы: `yaml_read.sh` usage audit

| # | Файл | Вызывает | Что менять |
|---|------|----------|------------|
| 40 | `core/lib/yaml_read.sh` | (source, не вызывается напрямую) | Заменить тело функций на вызов CLI фасада |
| 41 | Все `source yaml_read.sh` + `yaml_get_field` | ~10 файлов | Обновить вызовы → CLI фасада |

---

### Problem 2: `sys.exit()` в `project_registry.py` (W2)

| # | Файл | Строка | Функция | Что менять |
|---|------|--------|---------|------------|
| 1 | `core/internal/shared/project_registry.py` | 54 | `register_project` | `sys.exit(1)` → `return (False, "PyYAML not available")` |
| 2 | `core/internal/shared/project_registry.py` | 61 | `register_project` | `sys.exit(0)` → `return (False, "Missing params")` |
| 3 | `core/internal/shared/project_registry.py` | 73 | `register_project` | `sys.exit(0)` → `return (True, "Idempotent SKIP")` |
| 4 | `core/internal/shared/project_registry.py` | 91 | `register_project` | `sys.exit(0)` → `return (True, "Registered")` |
| 5 | `core/internal/shared/project_registry.py` | 117 | `deregister_project` | `sys.exit(1)` → `return (False, "PyYAML not available")` |
| 6 | `core/internal/shared/project_registry.py` | 124 | `deregister_project` | `sys.exit(0)` → `return (False, "Missing params")` |
| 7 | `core/internal/shared/project_registry.py` | 131 | `deregister_project` | `sys.exit(0)` → `return (True, "No projects section")` |
| 8 | `core/internal/shared/project_registry.py` | 144 | `deregister_project` | `sys.exit(0)` → `return (True, "Removed")` |
| 9 | `core/internal/shared/project_registry.py` | 172 | `list_projects` | `sys.exit(1)` → `return (False, "PyYAML not available")` |
| 10 | `core/internal/shared/project_registry.py` | 176 | `list_projects` | `sys.exit(1)` → `return (False, "Missing path")` |
| 11 | `core/internal/shared/project_registry.py` | 183 | `list_projects` | `sys.exit(1)` → `return (False, "Failed to read")` |
| 12 | `core/internal/shared/project_registry.py` | 194 | `list_projects` | `sys.exit(0)` → `return (True, "Listed N projects")` |
| 13 | `core/internal/shared/project_registry.py` | 231-251 | `__main__` (CLI) | Обновить: вызвать функцию → `sys.exit(0 if success else 1)` |

**Callers requiring update (shell):**
| # | Файл | Строки | Что менять |
|---|------|--------|------------|
| 14 | `core/internal/scaffold/add-project.sh` | ~719 | Вызов `project_registry.py register` — exit code не меняется |
| 15 | `core/internal/scaffold/adopt-project.sh` | ~674 | Вызов `project_registry.py register` — exit code не меняется |
| 16 | `core/internal/scaffold/remove-project.sh` | ~212 | Вызов `project_registry.py deregister` — exit code не меняется |

---

### Problem 3: Hardcoded logger names → `__name__` (W3)

| # | Файл | Строка | Старое имя | Новое (`__name__`) |
|---|------|--------|-----------|-------------------|
| 1 | `core/internal/provisioner.py` | 31 | `"provisioner"` | `__name__` |
| 2 | `core/internal/reconciler_projects.py` | 34 | `"reconcile_projects"` | `__name__` |
| 3 | `core/internal/shared/content_hash.py` | 28 | `"content_hash"` | `__name__` |
| 4 | `core/internal/shared/docker_compose.py` | 33 | `"docker_compose"` | `__name__` |
| 5 | `core/internal/shared/audit_logger.py` | 33 | `"audit_logger"` | `__name__` |
| 6 | `core/internal/shared/ssh_command_parser.py` | 33 | `"ssh_command_parser"` | `__name__` |
| 7 | `core/internal/shared/platform_deliver.py` | 30 | `"platform_deliver"` | `__name__` |
| 8 | `core/internal/bootstrap/_topo_sort.py` | 43 | `"_topo_sort"` | `__name__` |
| 9 | `core/internal/bootstrap/deploy/content_hash.py` | 43 | `"content_hash"` | `__name__` |
| 10 | `core/internal/bootstrap/deploy/sudoers_generator.py` | 38 | `"sudoers_generator"` | `__name__` |
| 11 | `core/internal/bootstrap/deploy/compose_preflight.py` | 39 | `"compose_preflight"` | `__name__` |
| 12 | `core/internal/bootstrap/deploy/docker_orchestrator.py` | 99 | `"docker_orchestrator"` | `__name__` |
| 13 | `core/internal/bootstrap/deploy/spool_validator.py` | 44 | `"spool_validator"` | `__name__` |
| 14 | `core/internal/bootstrap/deploy/secrets_validator.py` | 40 | `"secrets_validator"` | `__name__` |
| 15 | `core/internal/bootstrap/yaml_helpers.py` | 33 | `__name__` ✅ | Уже правильно |
| 16 | `core/internal/shared/node_yaml.py` | 22 | `__name__` ✅ | Уже правильно |
| 17 | `core/modules/hermes-agent/watchdog/agent_watchdog.py` | 39 | `"watchdog"` | `__name__` |

**Note:** Замена `"module_name"` → `__name__` меняет лог-префикс с `[IMP:X][module_name]` на `[IMP:X][core.internal.shared.docker_compose]`. Это ожидаемое поведение — полный qualified name улучшает отладку. Формат сообщений `[IMP:X][func_name]` не меняется (func_name задаётся вручную в каждом вызове `logger.info()`).

---

### Problem 4: Error handling strategies → typed exceptions (W4)

#### P4.1 — Silent swallow → `return []` (reconciler_projects.py)

| # | Файл | Строка | Паттерн | Замена |
|---|------|--------|---------|--------|
| 1 | `core/internal/reconciler_projects.py` | 132-135 | `except Exception as exc: logger.warning(...); return []` | `except (ConfigNotFoundError, ConfigParseError) as exc: ...` |
| 2 | `core/internal/reconciler_projects.py` | 270-279 | `except Exception as exc: logger.warning(...); return None` | `except (ConfigNotFoundError, ConfigParseError) as exc: ...` |
| 3 | `core/internal/reconciler_projects.py` | 457-464 | `except Exception as exc: logger.error(...); return False` | `except (ConfigNotFoundError, ConfigParseError) as exc: ...` |

#### P4.2 — Silent swallow → `return False` (s3_ssl_cache.py)

| # | Файл | Строка | Паттерн | Замена |
|---|------|--------|---------|--------|
| 4 | `core/internal/bootstrap/s3_ssl_cache.py` | 252 | `except Exception as e: return False` | `except (ConfigNotFoundError, ConfigParseError) as e: return False` |
| 5 | `core/internal/bootstrap/s3_ssl_cache.py` | 281 | `except Exception as e: return False` | `except (ConfigNotFoundError, ConfigParseError) as e: ...` |
| 6 | `core/internal/bootstrap/s3_ssl_cache.py` | 315 | `except Exception as e: return {}` | `except (ConfigNotFoundError, ConfigParseError) as e: ...` |
| 7 | `core/internal/bootstrap/s3_ssl_cache.py` | 447, 535, 552, 569, 587, 709 | `except Exception as e: ...` | Сузить до ожидаемых типов |

#### P4.3 — Fail hard → `RuntimeError` (state_machine.py, steps.py, provisioner.py)

| # | Файл | Строка | `RuntimeError` | Замена |
|---|------|--------|---------------|--------|
| 8 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1034 | `raise RuntimeError("must run as root")` | `raise PlatformFatalError(...)` |
| 9 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1106 | `raise RuntimeError("node.yaml not found")` | `raise ConfigNotFoundError(...)` |
| 10 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1408-1434 | `raise RuntimeError(...)` (5 cases) | `raise PlatformFatalError(...)` |
| 11 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1614 | `raise RuntimeError(...)` | `raise PlatformFatalError(...)` |
| 12 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1679 | `raise RuntimeError("secrets.env not found")` | `raise ConfigNotFoundError(...)` |
| 13 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1713 | `raise RuntimeError("node.yaml not found")` | `raise ConfigNotFoundError(...)` |
| 14 | `core/internal/bootstrap/lifecycle/state_machine.py` | 1812 | `raise RuntimeError(...)` | `raise PlatformFatalError(...)` |
| 15 | `core/internal/bootstrap/lifecycle/steps.py` | 187-192 | `raise RuntimeError(...)` (3 cases) | `raise PlatformFatalError(...)` |
| 16 | `core/internal/bootstrap/lifecycle/steps.py` | 259-274 | `raise RuntimeError(...)` (3 cases) | `raise PlatformFatalError(...)` |
| 17 | `core/internal/bootstrap/lifecycle/steps.py` | 315-318 | `raise RuntimeError(...)` (2 cases) | `raise PlatformFatalError(...)` |
| 18 | `core/internal/bootstrap/lifecycle/steps.py` | 384-398 | `raise RuntimeError(...)` (3 cases) | `raise PlatformFatalError(...)` |
| 19 | `core/internal/bootstrap/lifecycle/steps.py` | 560 | `raise RuntimeError(...)` | `raise PlatformFatalError(...)` |
| 20 | `core/internal/bootstrap/lifecycle/steps.py` | 677 | `raise RuntimeError(...)` | `raise PlatformFatalError(...)` |
| 21 | `core/internal/provisioner.py` | 367 | `except Exception as e:` | Сузить до ожидаемых типов |

#### P4.4 — Graceful → DomainCertResult с ошибкой (cert_orchestrator.py)

| # | Файл | Строка | Паттерн | Замена |
|---|------|--------|---------|--------|
| 22 | `core/internal/bootstrap/cert_orchestrator.py` | 382, 409, 547, 611, 688 | `except Exception as e:` → `DomainCertResult(..., error=str(e))` | `except (ConfigNotFoundError, ConfigParseError, PlatformFatalError) as e:` → дифференцировать |

#### P4.5 — Все остальные `except Exception` (batch update)

Оставшиеся ~70 блоков `except Exception` в файлах:

- `core/internal/healthcheck/platform_export_metrics.py`: 13 блоков (строки 112-291) — сузить до `(ConfigParseError, OSError, json.JSONDecodeError)`
- `core/internal/healthcheck/metrics/cert_collector.py`: 5 блоков (строки 88-184) — сузить
- `core/internal/healthcheck/metrics/project_collector.py`: 1 блок (строка 55) — сузить
- `core/internal/bootstrap/preflight.py`: 3 блока (строки 241, 383, 475) — сузить
- `core/internal/bootstrap/discover_modules.py`: 1 блок (строка 137) — сузить
- `core/internal/bootstrap/deploy/context_deployer.py`: 7 блоков (строки 550-783) — сузить
- `core/internal/bootstrap/deploy/context_overlay.py`: 2 блока (строки 133, 294) — сузить
- `core/internal/bootstrap/converge/reconciler.py`: 5 блоков (строки 691-1707) — сузить
- `core/internal/bootstrap/deploy/docker_orchestrator.py`: 4 блока (строки 616-1075) — сузить
- `core/internal/bootstrap/deploy/sudoers_generator.py`: 4 блока (строки 166-457) — сузить
- `core/internal/bootstrap/deploy/orphan_reconciler.py`: 3 блока (строки 152-299) — сузить
- `core/internal/bootstrap/lifecycle/secrets_manager.py`: 1 блок (строка 139) — сузить
- `core/internal/llm/key_provisioner.py`: 3 блока (строки 590-741) — сузить
- `core/internal/scripts/sync_env_defaults.py`: 1 блок (строка 532) — сузить
- `core/internal/scripts/generate_entrypoint_manifest.py`: 1 блок (строка 473) — сузить
- `core/internal/scripts/generate_agents_md.py`: 1 блок (строка 282) — сузить
- `core/internal/scaffold/context_registry.py`: 2 блока (строки 46, 70) — сузить
- `core/internal/scaffold/vhost_yaml_reader.py`: 1 блок (строка 38) — сузить
- `core/internal/shared/ssh_command_parser.py`: 1 блок (строка 264) — сузить
- `core/internal/bootstrap/lifecycle/steps.py`: 3 блока (строки 474, 658, 767) — сузить
- `core/internal/shared/node_yaml.py`: 1 блок (строка 62) — сузить

---

### Problem 5: Inline python3 cleanup (W5)

| # | Файл | Строки | Контекст | Замена |
|---|------|--------|----------|--------|
| 1 | `core/lib/yaml_read.sh` | 133-146 | `yaml_read_domain_config()` — python3 heredoc | CLI фасада: `node_yaml.py --domain-config` |
| 2 | `core/lib/node-resolver.sh` | 255 | `python3 -c` — JSON parsing | CLI: `yaml_query.py --stdin` (уже существует) |
| 3 | `core/lib/node-resolver.sh` | 302-310 | `python3 -c "import yaml"` — YAML host extraction | CLI фасада: `node_yaml.py --get node.host` |
| 4 | `core/lib/vps-readiness.sh` | 74, 78 | `python3 -c "import json"` — JSON из stdin | CLI: `yaml_query.py --stdin` (уже существует) |
| 5 | `core/internal/validate/validate.sh` | 71 | `python3 -c "import yaml"` — проверка структуры | CLI фасада: `node_yaml.py --validate` |
| 6 | `core/internal/validate/validate.sh` | 97-107 | `python3 - ... <<'PYEOF'` — schema validation | ОСТАВИТЬ (не node.yaml, это jsonschema валидатор) |
| 7 | `core/internal/validate/validate.sh` | 276 | `python3 -c` — host:port extraction | CLI фасада |
| 8 | `core/internal/verify/verify-domains.sh` | 106-118 | `python3 -c "import yaml"` — domain extraction | CLI фасада: `node_yaml.py --get` |
| 9 | `core/internal/verify/verify-domains.sh` | 141 | `python3 -c` — project list | CLI фасада |
| 10 | `core/internal/deploy/deploy-project.sh` | 445, 479 | `python3 -c` — JSON parsing | CLI: `yaml_query.py --stdin` |
| 11 | `core/internal/catalog/generate-catalog.sh` | 40-84 | `python3 - <<'PYEOF'` — YAML + catalog logic | Python-модуль (часть exists: `generate-catalog.sh`) |
| 12 | `core/internal/scaffold/add-vhost.sh` | 548 | `python3 -c` — domain parsing | CLI фасада |
| 13 | `core/internal/scaffold/add-vhost.sh` | 779-780 | `python3 -c "import json"` — JSON parsing | CLI: `yaml_query.py --stdin` |
| 14 | `core/internal/scaffold/remove-project.sh` | 162-174 | `python3 -c "import yaml"` — project check | CLI фасада |
| 15 | `core/internal/scaffold/adopt-project.sh` | 399-427 | `python3 -c` — file parsing + analysis | Python-модуль |
| 16 | `core/internal/bootstrap/install-docker.sh` | 116 | `python3 -c` — platform detection | ОСТАВИТЬ (не YAML, легитимный однострочник) |
| 17 | `core/lib/python_deps.sh` | 22 | `python3 -c "import ${module}"` | ОСТАВИТЬ (легитимная проверка наличия модуля) |
| 18 | `core/modules/postgres/hooks/on-project-deploy.sh` | 43-46 | `python3 -c` — db name extraction | CLI фасада |
| 19 | `core/lib/yaml_read.sh` | 75, 102 | `python3 .../yaml_query.py ...` (вызов) | Заменить на вызов CLI фасада |

**Легитимные inline python3 (НЕ трогать):**
- `python_deps.sh` — проверка наличия Python-модуля (`python3 -c "import X"`)
- `install-docker.sh` — platform detection (чистый однострочник без import)
- `validate.sh:97` — jsonschema валидация (не node.yaml)

---

## $TASKS

### Wave 1: Unified `node_yaml.py` Facade (W1)

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T1.1** | Создать `exceptions.py` с 5 классами исключений | 1 (NEW) | 2 | None | Модуль импортируется, все 5 классов созданы, у каждого `exit_code` |
| **T1.2** | Расширить `shared/node_yaml.py`: класс `NodeYaml` с методами `load`, `reload`, `get`, `get_list`, `get_context`, `get_projects`, `get_modules`, `get_domain_config`, `get_node_info`, `validate`, `raw` | 1 | 8 | T1.1 | Все 11 методов реализованы, типизированы, с LDD-логами |
| **T1.3** | Добавить CLI в `shared/node_yaml.py` (`--file`, `--get`, `--domain-config`, `--context`, `--validate`, `--items`) | 1 | 4 | T1.2 | CLI запускается, все флаги работают, exit codes = exit_code из exceptions |
| **T1.4** | Написать unit-тесты для `NodeYaml` (test_node_yaml_facade.py): load, cache, reload, get, get_list, get_context, get_projects, get_modules, get_domain_config, validate, CLI, error cases | 1 (NEW) | 6 | T1.3 | 20+ тестов, покрытие ≥90%, test honesty rules соблюдены |
| **T1.5** | Заменить прямые `yaml.safe_load` для node.yaml в Python-файлах (P1.1: 26 файлов) на `NodeYaml(path)` | ~26 | 10 | T1.4 | Все 26 файлов импортируют `NodeYaml`, старые `import yaml` удалены где возможно |
| **T1.6** | Обновить `yaml_read.sh`: заменить тела функций на вызов CLI фасада (backward-compat обёртка) | 1 | 3 | T1.3 | `yaml_get_field`, `yaml_get_list`, `yaml_read_domain_config` работают через CLI фасада |
| **T1.7** | Обновить shell-файлы: заменить inline python3 + import yaml на вызов CLI фасада (P1.2: 8 файлов) | ~8 | 5 | T1.6 | 0 активных inline `python3 -c "import yaml"` |
| **T1.8** | Обновить `NODE_YAML_PATH` использования: заменить на `NodeYaml(path)` (P1.3: 5 файлов) | ~5 | 4 | T1.5 | `NODE_YAML_PATH` env var больше не используется для чтения YAML |
| **T1.9** | Запустить `make gate MODE=fast` + исправить регрессии | N/A | 3 | T1.5-T1.8 | Gate green |

### Wave 2: `project_registry.py` — remove `sys.exit()` (W2)

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T2.1** | Рефакторинг `register_project`, `deregister_project`, `list_projects`: `sys.exit()` → `return (bool, str)` | 1 | 3 | None | 0 `sys.exit()` в библиотечных функциях |
| **T2.2** | Обновить CLI (`__main__`): маппинг `(success, msg)` → `sys.exit(0/1)` | 1 | 2 | T2.1 | CLI exit codes идентичны старым |
| **T2.3** | Обновить shell-callers: проверить что exit code handling не сломан | 3 | 2 | T2.2 | `add-project.sh`, `adopt-project.sh`, `remove-project.sh` работают |
| **T2.4** | Добавить unit-тесты: проверка return values (не exit codes) | 1 (NEW) | 3 | T2.1 | Тесты вызывают функции напрямую, проверяют `Tuple[bool, str]` |

### Wave 3: Hardcoded loggers → `__name__` (W3)

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T3.1** | Заменить `getLogger("literal")` → `getLogger(__name__)` в 15 Python файлах (P3: строки 1-15) | 15 | 2 | None | Все 16 логгеров (включая уже правильные) используют `__name__` |
| **T3.2** | Верификация: `grep 'getLogger("[a-z]' core/internal/ core/modules/` → 0 результатов | N/A | 1 | T3.1 | No hardcoded logger names |
| **T3.3** | Запустить тесты: убедиться что формат логов не сломан | N/A | 2 | T3.1 | Все тесты проходят, LDD-логи содержат qualified module name |

### Wave 4: Typed exception hierarchy (W4)

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T4.1** | Заменить `raise RuntimeError` на типизированные исключения в state_machine.py, steps.py (P4.3: ~20 замен) | 2 | 4 | T1.1 | 0 `raise RuntimeError` для платформенных ошибок |
| **T4.2** | Сузить `except Exception` до ожидаемых типов в reconciler_projects.py, s3_ssl_cache.py, provisioner.py (P4.1-P4.2: ~10 замен) | 3 | 3 | T1.1 | Каждый `except` ловит только конкретные типы |
| **T4.3** | Обновить cert_orchestrator.py: дифференцировать ошибки в DomainCertResult (P4.4: 5 замен) | 1 | 2 | T1.1 | `DomainCertResult.error` содержит тип ошибки, не только строку |
| **T4.4** | Batch update оставшихся `except Exception` блоков (P4.5: ~70 блоков в 21 файле) | ~21 | 6 | T1.1 | Все `except Exception` сужены до ожидаемых типов |
| **T4.5** | Обновить `except PlatformError` в верхнеуровневых обработчиках (state_machine main loop, CLI entrypoints) | ~5 | 3 | T4.1-T4.4 | Верхний уровень ловит `PlatformError`, логирует IMP:10, маппит exit_code |

### Wave 5: Inline python3 cleanup (W5)

| Task | Description | Files | Complexity | Dependencies | AC |
|------|-------------|-------|------------|-------------|-----|
| **T5.1** | Заменить inline python3 с `import yaml` на вызов CLI фасада (P5: строки 1-3, 5, 7-10, 12-15, 18-19) | ~10 | 5 | T1.3, T1.6 | 0 активных inline `python3 -c "import yaml"` |
| **T5.2** | Заменить inline python3 с `import json` на `yaml_query.py --stdin` (P5: строки 4, 11, 13) | ~3 | 3 | None (yaml_query.py уже существует) | Используется `yaml_query.py --stdin` |
| **T5.3** | Обновить `check-no-new-inline-python3.sh` whitelist | 1 | 1 | T5.1-T5.2 | Pre-commit hook passes на новом коде |
| **T5.4** | Верификация: `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 результатов (кроме комментариев) | N/A | 1 | T5.1 | Clean grep |

---

## $PARALLEL_GROUPS

### Wave 1 (независимые, разные файлы)
- **Tasks:** T1.1
- **Command:** `coder Read DevPlan.md, implement Wave 1: T1.1`

### Wave 2 (T1.2-T1.4: последовательно внутри, независимы от других волн)
- **Tasks:** T1.2, T1.3, T1.4
- **Command:** `coder Read DevPlan.md, implement Wave 2: T1.2, T1.3, T1.4`

### Wave 3 (T1.5-T1.8: миграция consumer'ов; можно частично параллелить)
- **Tasks:** T1.5, T1.6, T1.7, T1.8
- **Note:** T1.5 (Python consumers) и T1.6-T1.8 (shell consumers) не пересекаются по файлам
- **Command:** `coder Read DevPlan.md, implement Wave 3: T1.5, T1.6, T1.7, T1.8`

### Wave 4 (T1.9: финальная верификация W1; W2+W3 параллельно)
- **Tasks:** T1.9, T2.1+T2.2+T2.3+T2.4 (W2), T3.1+T3.2+T3.3 (W3)
- **Note:** W2 и W3 полностью независимы друг от друга и от T1.9 (разные файлы)
- **Command:** `coder Read DevPlan.md, implement Wave 4: T1.9, T2.1-T2.4, T3.1-T3.3`

### Wave 5 (W4: зависит от T1.1)
- **Tasks:** T4.1, T4.2, T4.3, T4.4, T4.5
- **Note:** T4.1-T4.4 можно параллелить (разные файлы), T4.5 зависит от T4.1-T4.4
- **Command:** `coder Read DevPlan.md, implement Wave 5: T4.1, T4.2, T4.3, T4.4, T4.5`

### Wave 6 (W5: зависит от T1.3, T1.6)
- **Tasks:** T5.1, T5.2, T5.3, T5.4
- **Command:** `coder Read DevPlan.md, implement Wave 6: T5.1, T5.2, T5.3, T5.4`

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_node_yaml_facade.py` | `test_load_valid_yaml` | Загрузка валидного node.yaml | `NodeYaml.load()` |
| `tests/unit/test_node_yaml_facade.py` | `test_load_file_not_found` | Файл не найден → `ConfigNotFoundError` | `NodeYaml.load()` |
| `tests/unit/test_node_yaml_facade.py` | `test_load_malformed_yaml` | Битый YAML → `ConfigParseError` | `NodeYaml.load()` |
| `tests/unit/test_node_yaml_facade.py` | `test_cache_hit` | Повторный `.load()` не читает файл | `NodeYaml.load()` cache |
| `tests/unit/test_node_yaml_facade.py` | `test_reload_invalidates_cache` | `.reload()` перечитывает файл | `NodeYaml.reload()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_simple_key` | `.get("domain")` → `"example.com"` | `NodeYaml.get()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_nested_key` | `.get("node.host")` → `"1.2.3.4"` | `NodeYaml.get()` dotted key |
| `tests/unit/test_node_yaml_facade.py` | `test_get_missing_key_no_default` | `.get("nonexistent")` → `ConfigValidationError` | `NodeYaml.get()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_missing_key_with_default` | `.get("nonexistent", default="fallback")` → `"fallback"` | `NodeYaml.get()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_list` | `.get_list("projects")` → `list[dict]` | `NodeYaml.get_list()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_list_not_a_list` | `.get_list("domain")` → `ConfigValidationError` | `NodeYaml.get_list()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_context_string` | `context: "myorg"` → `"myorg"` | `NodeYaml.get_context()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_context_array` | `contexts: [{name: "myorg"}]` → `"myorg"` | `NodeYaml.get_context()` fallback |
| `tests/unit/test_node_yaml_facade.py` | `test_get_projects` | Список проектов из node.yaml | `NodeYaml.get_projects()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_modules` | Список модулей из node.yaml | `NodeYaml.get_modules()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_domain_config` | DomainConfig namedtuple | `NodeYaml.get_domain_config()` |
| `tests/unit/test_node_yaml_facade.py` | `test_get_node_info` | NodeInfo namedtuple | `NodeYaml.get_node_info()` |
| `tests/unit/test_node_yaml_facade.py` | `test_validate_valid` | Валидный node.yaml → `[]` | `NodeYaml.validate()` |
| `tests/unit/test_node_yaml_facade.py` | `test_validate_missing_domain` | Без domain → ошибка валидации | `NodeYaml.validate()` |
| `tests/unit/test_node_yaml_facade.py` | `test_cli_get` | CLI `--file x --get node.host` → stdout value | NodeYaml CLI |
| `tests/unit/test_node_yaml_facade.py` | `test_cli_domain_config` | CLI `--domain-config` → stdout lines | NodeYaml CLI |
| `tests/unit/test_exceptions.py` | `test_platform_error_exit_code` | Каждый subclass имеет правильный exit_code | `exceptions.py` |
| `tests/unit/test_exceptions.py` | `test_exception_inheritance` | `ConfigNotFoundError` is `PlatformError` | `exceptions.py` |
| `tests/unit/test_project_registry.py` | `test_register_returns_tuple` | `register_project()` → `(True, msg)` | `project_registry.py` (W2) |
| `tests/unit/test_project_registry.py` | `test_deregister_returns_tuple` | `deregister_project()` → `(True, msg)` | `project_registry.py` (W2) |
| `tests/unit/test_project_registry.py` | `test_list_returns_tuple` | `list_projects()` → `(True, msg)` | `project_registry.py` (W2) |

---

## Acceptance Criteria Summary

| # | Критерий | Проверка | Target Wave |
|---|----------|----------|-------------|
| AC1 | Все `yaml.safe_load` для node.yaml проходят через `NodeYaml` класс | `grep 'yaml.safe_load' core/internal/` → только в `yaml_query.py:_load_yaml` и `NodeYaml.load` | W1 |
| AC2 | `project_registry.py` не содержит `sys.exit()` в библиотечных функциях | `grep 'sys.exit' core/internal/shared/project_registry.py` → только в `if __name__` | W2 |
| AC3 | Все логгеры используют `__name__` | `grep 'getLogger("[a-z]' core/internal/ core/modules/` → 0 результатов | W3 |
| AC4 | Иерархия исключений определена и используется | `grep 'class.*Error.*PlatformError' core/internal/shared/exceptions.py` → 4 subclass | W4 |
| AC5 | 0 `raise RuntimeError` для платформенных ошибок | `grep 'raise RuntimeError' core/internal/bootstrap/` → 0 | W4 |
| AC6 | Все `except Exception` сужены до ожидаемых типов | `grep 'except Exception' core/internal/` → только в __main__/CLI верхнего уровня | W4 |
| AC7 | 0 активных inline `python3 -c "import yaml"` | `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 (кроме комментариев) | W5 |
| AC8 | `make gate MODE=fast` passes | CI green | All |
| AC9 | Pre-commit hook `check-no-new-inline-python3` passes | Hook exit 0 | W5 |
| AC10 | Все существующие тесты проходят (no regression) | `python -m pytest tests/ -s -v` → all pass | All |

---

## File Count Breakdown

Сводная таблица объёма изменений по каждой волне:

| Wave | Новых Python | Модифицируемых Python | Модифицируемых shell | Всего файлов |
|------|-------------|----------------------|---------------------|-------------|
| **W1** (фасад node_yaml + exceptions) | 2 (exceptions.py, test_node_yaml_facade.py) | 26 | ~10 (yaml_read.sh, node-resolver.sh, add-vhost.sh, remove-project.sh, adopt-project.sh, validate.sh, verify-domains.sh, deploy-project.sh, generate-catalog.sh, on-project-deploy.sh) | **~38** |
| **W2** (sys.exit removal) | 1 (test_project_registry.py) | 1 (project_registry.py) | 3 (add-project.sh, adopt-project.sh, remove-project.sh) | **5** |
| **W3** (logger names → __name__) | 0 | 15 | 0 | **15** |
| **W4** (typed exceptions) | 0 | ~30 | 0 | **~30** |
| **W5** (inline python3 cleanup) | 0 | 0 | ~12 | **~12** |
| **Total** | **3** | **~72** | **~25** | **~100** |

**Примечание:** Файлы, модифицируемые в нескольких волнах (например, `project_registry.py` — в W1 и W2), подсчитаны в каждой волне отдельно. Реальное уникальное количество файлов — ~90.

---

## File Manifest

### Новые файлы
| Файл | Назначение |
|------|-----------|
| `core/internal/shared/exceptions.py` | Иерархия типизированных исключений |
| `tests/unit/test_node_yaml_facade.py` | Unit-тесты для NodeYaml фасада |
| `tests/unit/test_exceptions.py` | Unit-тесты для иерархии исключений |

### Модифицируемые файлы (W1 — фасад)
| Файл | Изменение |
|------|-----------|
| `core/internal/shared/node_yaml.py` | Расширение: класс NodeYaml + CLI |
| `core/internal/shared/project_registry.py` | `yaml.safe_load` → `NodeYaml(path).load()` |
| `core/internal/bootstrap/yaml_helpers.py` | Делегирование к NodeYaml (или deprecation) |
| `core/internal/bootstrap/preflight.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/lifecycle/state_machine.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/lifecycle/steps.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/deploy/context_deployer.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/deploy/context_overlay.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/s3_ssl_cache.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/converge/reconciler.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/deploy/secrets_validator.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/deploy/compose_preflight.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/deploy/spool_validator.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/bootstrap/lifecycle/secrets_manager.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/reconciler_projects.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/provisioner.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/monitoring_config_renderer.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/scaffold/gen_env_platform.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/scaffold/vhost_yaml_reader.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/scaffold/context_registry.py` | `yaml.safe_load` → `NodeYaml` |
| `core/internal/healthcheck/platform_export_metrics.py` | `yaml.safe_load` + `NODE_YAML_PATH` → `NodeYaml` |
| `core/internal/healthcheck/metrics/cert_collector.py` | `yaml.safe_load` + `NODE_YAML_PATH` → `NodeYaml` |
| `core/internal/healthcheck/metrics/project_collector.py` | `yaml.safe_load` → `NodeYaml` |
| `core/modules/status-page/app.py` | `yaml.safe_load` + `NODE_YAML_PATH` → `NodeYaml` |
| `core/lib/yaml_read.sh` | Тела функций → CLI фасада |
| `core/lib/node-resolver.sh` | inline python3 → CLI фасада |

### Модифицируемые файлы (W2 — sys.exit)
| Файл | Изменение |
|------|-----------|
| `core/internal/shared/project_registry.py` | `sys.exit()` → `return (bool, str)` |
| `core/internal/scaffold/add-project.sh` | Проверить exit code handling |
| `core/internal/scaffold/adopt-project.sh` | Проверить exit code handling |
| `core/internal/scaffold/remove-project.sh` | Проверить exit code handling |

### Модифицируемые файлы (W3 — loggers)
| Файл | Изменение |
|------|-----------|
| `core/internal/provisioner.py:31` | `"provisioner"` → `__name__` |
| `core/internal/reconciler_projects.py:34` | `"reconcile_projects"` → `__name__` |
| `core/internal/shared/content_hash.py:28` | `"content_hash"` → `__name__` |
| `core/internal/shared/docker_compose.py:33` | `"docker_compose"` → `__name__` |
| `core/internal/shared/audit_logger.py:33` | `"audit_logger"` → `__name__` |
| `core/internal/shared/ssh_command_parser.py:33` | `"ssh_command_parser"` → `__name__` |
| `core/internal/shared/platform_deliver.py:30` | `"platform_deliver"` → `__name__` |
| `core/internal/bootstrap/_topo_sort.py:43` | `"_topo_sort"` → `__name__` |
| `core/internal/bootstrap/deploy/content_hash.py:43` | `"content_hash"` → `__name__` |
| `core/internal/bootstrap/deploy/sudoers_generator.py:38` | `"sudoers_generator"` → `__name__` |
| `core/internal/bootstrap/deploy/compose_preflight.py:39` | `"compose_preflight"` → `__name__` |
| `core/internal/bootstrap/deploy/docker_orchestrator.py:99` | `"docker_orchestrator"` → `__name__` |
| `core/internal/bootstrap/deploy/spool_validator.py:44` | `"spool_validator"` → `__name__` |
| `core/internal/bootstrap/deploy/secrets_validator.py:40` | `"secrets_validator"` → `__name__` |
| `core/modules/hermes-agent/watchdog/agent_watchdog.py:39` | `"watchdog"` → `__name__` |

### Модифицируемые файлы (W4 — typed exceptions)
| Категория | Файлы |
|-----------|-------|
| P4.1 (silent swallow → []) | `reconciler_projects.py` |
| P4.2 (silent swallow → False) | `s3_ssl_cache.py` |
| P4.3 (RuntimeError → typed) | `state_machine.py`, `steps.py` |
| P4.4 (DomainCertResult) | `cert_orchestrator.py` |
| P4.5 (batch except Exception) | 21 файл (см. полный список в P4.5) |

### Модифицируемые файлы (W5 — inline python3 cleanup)
| Файл | Изменение |
|------|-----------|
| `core/lib/yaml_read.sh` | Замена тела `yaml_read_domain_config()` |
| `core/lib/node-resolver.sh` | Замена inline python3 |
| `core/lib/vps-readiness.sh` | Замена inline python3 на `yaml_query.py --stdin` |
| `core/internal/validate/validate.sh` | Замена inline python3 |
| `core/internal/verify/verify-domains.sh` | Замена inline python3 |
| `core/internal/deploy/deploy-project.sh` | Замена inline python3 на `yaml_query.py --stdin` |
| `core/internal/catalog/generate-catalog.sh` | Python-модуль (или оставить) |
| `core/internal/scaffold/add-vhost.sh` | Замена inline python3 |
| `core/internal/scaffold/remove-project.sh` | Замена inline python3 |
| `core/internal/scaffold/adopt-project.sh` | Замена inline python3 |
| `core/modules/postgres/hooks/on-project-deploy.sh` | Замена inline python3 |
| `core/internal/hooks/check-no-new-inline-python3.sh` | Обновление whitelist |

---

## Риски и Mitigations

| # | Риск | Severity | Mitigation |
|---|------|----------|------------|
| R1 | **Breaking change в API node_yaml** — 60+ consumers сломаются одновременно | HIGH | Обратная совместимость через алиасы (`extract_context_from_node_yaml` остаётся как `NodeYaml.get_context()`). Депрекейшн-ворнинги на 1 релиз. |
| R2 | **Shell exit code change** — замена `yaml_read.sh` функций на CLI фасада может изменить exit code поведение | MEDIUM | CLI фасада использует `exit_code` из `PlatformError` subclass. Маппинг: ConfigNotFoundError→2, ConfigParseError→3, ConfigValidationError→4 — совпадает с существующими кодами `yaml_query.py` |
| R3 | **Logger name change** — замена `"docker_compose"` → `"core.internal.shared.docker_compose"` может сломать log parsers | LOW | LDD формат `[IMP:X][func_name]` не меняется (func_name задаётся в каждом вызове `logger.info()`). Меняется только префикс модуля — grep по `[IMP:` продолжает работать. |
| R4 | **Performance regression** — `NodeYaml` класс с кэшированием добавляет слой абстракции | LOW | Lazy load + in-memory cache. `yaml.safe_load` вызывается ровно столько же раз. Дополнительный overhead — один dict lookup на операцию (ничтожно). |
| R5 | **Incomplete migration** — не все `except Exception` сужены, остаются silent swallow | MEDIUM | CI gate: `make check-exception-patterns` — новый проверочный таргет, который детектирует `except Exception` в non-CLI коде |
| R6 | **yaml_read.sh consumers не обновлены** — shell-скрипты продолжают вызывать старые функции | MEDIUM | `grep 'yaml_get_field\|yaml_get_list\|yaml_read_domain_config' core/ --include='*.sh'` после миграции → должно быть 0 (кроме definitions в yaml_read.sh) |
| R7 | **Тесты используют NODE_YAML_PATH env var** — 5 тестов завязаны на переменную окружения | LOW | Обновить тесты в tests/ на использование tmp_path с тестовым node.yaml. Удалить `os.environ["NODE_YAML_PATH"]` моки. |
| R8 | **Конфликт с параллельными DevPlans** — 079 (bootstrap), 081 (deploy), 082 (config) модифицируют те же файлы | HIGH | Координировать порядок мёржа: сначала 079/081/082 (они ближе к завершению), потом 038. Или мёржить 038 первым как фундаментальный рефакторинг, а 079/081/082 ребейзить. |

---

## CI Gate Impact

### Новые gates

**1. `make check-exception-patterns`** — новый проверочный таргет

**Назначение:** детектировать `except Exception` в non-CLI коде. После W4 все `except Exception` должны быть либо сужены до ожидаемых типов, либо находиться в CLI-обёртке (`__main__`).

**Реализация:**
```makefile
check-exception-patterns:
	@echo "Checking for bare except Exception in non-CLI code..."
	@! grep -rn 'except\s\+Exception' core/internal/ --include='*.py' \
		| grep -v '__main__\|if __name__' \
		|| (echo "FAIL: bare except Exception found" && exit 1)
	@echo "All exception handlers are typed."
```

**Интеграция:** добавить в `make gate MODE=fast` и `core/entrypoint-manifest.yaml`. Риск ложных срабатываний — низкий (исключения только в `__main__`).

**2. `make check-no-new-inline-python3`** — существующий pre-commit hook, обновить whitelist

**Изменения:**
- Добавить `core/internal/shared/node_yaml.py` в whitelist (легитимный Python-модуль, вызываемый через `python3 -m`)
- Удалить из whitelist все файлы, мигрированные в W5
- Добавить проверку на `python3 -c.*import yaml` в новых shell-файлах

### Existing gates to update

**3. `make check-manifests`** — обновить generated files

| Generated file | Изменение |
|----------------|-----------|
| `entrypoint-manifest.yaml` | Добавить entrypoint: `node_yaml.py:CLI` (python3 -m mode) и `exceptions.py:classes` |
| `platform-env.yaml` | Не требует изменений (NodeYaml не добавляет новых env vars) |
| `secrets-manifest.yaml` | Не требует изменений |
| `env_defaults_generated.py` | Не требует изменений |

**4. `make gate MODE=fast`** — добавить новые проверки

```makefile
gate-fast: check-exception-patterns check-no-new-inline-python3 \
           ...existing-gates...
```

**5. Pre-commit hook** (`check-no-new-inline-python3.sh`)

- Проверять staged `.sh` файлы на наличие `python3 -c.*import yaml`
- Если найден — FAIL с сообщением:
  > "Use CLI facade instead: python3 -m core.internal.shared.node_yaml --file ... --get ..."
- Исключение: `python_deps.sh` (легитимная проверка наличия модуля)

---

## Next Steps

### Wave 1 (Unified Facade — Part 1: Core)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/038-arch-unification-node-yaml-errors-loggers/02-DevPlan.md, implement Wave 1 Core: T1.1 (exceptions.py), T1.2 (NodeYaml class), T1.3 (CLI), T1.4 (unit tests)
```

### Wave 2 (Unified Facade — Part 2: Migration)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/038-arch-unification-node-yaml-errors-loggers/02-DevPlan.md, implement Wave 2 Migration: T1.5 (Python consumers), T1.6 (yaml_read.sh), T1.7 (shell consumers), T1.8 (NODE_YAML_PATH), T1.9 (gate check)
```

### Wave 3 (sys.exit + Loggers — parallel)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/038-arch-unification-node-yaml-errors-loggers/02-DevPlan.md, implement Wave 3: T2.1-T2.4 (project_registry return codes), T3.1-T3.3 (logger __name__ fix)
```

### Wave 4 (Typed Exceptions)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/038-arch-unification-node-yaml-errors-loggers/02-DevPlan.md, implement Wave 4: T4.1-T4.5 (typed exception hierarchy)
```

### Wave 5 (Inline python3 cleanup)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/038-arch-unification-node-yaml-errors-loggers/02-DevPlan.md, implement Wave 5: T5.1-T5.4 (inline python3 cleanup)
```

$END_DEVPLAN
