$START_BRIEF

# Brief 038b — sys.exit removal + loggers + typed exceptions (Waves W2+W3+W4)

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Выделить три взаимонезависимые архитектурные волны из DevPlan 038 (W2: sys.exit removal, W3: консистентное логирование, W4: типизированные исключения) в отдельный декомпозитный подплан, реализуемый одним PR |
| **DESCRIPTION** | DevPlan 038 — LARGE-рефакторинг (~90 файлов, 5 волн). Волны W2, W3, W4 суммарно затрагивают ~50 файлов, независимы друг от друга, и W2+W3 полностью независимы от W1. W4 зависит от W1 только через `exceptions.py` (создаётся в 038a). Scope: замена 14 `sys.exit()` → `return (bool, str)` в `project_registry.py`, механическая замена 15 `getLogger("literal")` → `getLogger(__name__)`, замена ~20 `raise RuntimeError` → типизированные исключения + сужение ~80 `except Exception` блоков в ~30 файлах + новый CI gate `check-exception-patterns` |
| **RATIONALE** | Три волны можно реализовать в одном PR суммарным diff <800 строк. Волны не конфликтуют друг с другом (разные файлы). W2 и W3 — полностью механические замены, low-risk. W4 — средний риск, но семантически ограничена (исключения определены в 038a). Единственная зависимость от 038a — файл `exceptions.py` с 5 классами исключений. |
| **ACCEPTANCE_CRITERIA** | 1. AC2: `grep 'sys.exit' project_registry.py` → только в `if __name__`. 2. AC3: `grep 'getLogger("[a-z]' core/internal/` → 0. 3. AC4: `grep 'class.*Error.*PlatformError' exceptions.py` → 4 subclass. 4. AC5: `grep 'raise RuntimeError' core/internal/bootstrap/` → 0. 5. AC6: `grep 'except Exception' core/internal/` → только в `__main__`. 6. Новый CI gate `make check-exception-patterns` проходит. 7. `make gate MODE=fast` passes. 8. Все существующие тесты проходят без регрессий. |
| **IMPLEMENTS** | DevPlan 038 — Архитектурная унификация (Waves 2, 3, 4) |
| **IMPACTS** | `core/internal/shared/project_registry.py` (W2), 15 Python-файлов с hardcoded логгерами (W3), ~30 Python-файлов с `except Exception` / `raise RuntimeError` (W4), shell-callers: `add-project.sh`, `adopt-project.sh`, `remove-project.sh` (W2), `Makefile` (новый gate), `core/entrypoint-manifest.yaml` (новый gate), `tests/unit/test_project_registry.py` (новый), `tests/unit/test_exceptions.py` (дополнение) |
| **REQUIRES** | DevPlan 038a (W1: `core/internal/shared/exceptions.py` с 5 классами: `PlatformError`, `ConfigNotFoundError`, `ConfigParseError`, `ConfigValidationError`, `PlatformFatalError`). Без `exceptions.py` W4 не может быть реализована. W2 и W3 от 038a не зависят. |

---

## $DOCUMENT_PLAN

### 1. Problem Statement

DevPlan 038 идентифицирует 5 архитектурных проблем платформы. Три из них (P2, P3, P4) взаимосвязаны через exception hierarchy, но независимы от единого фасада `node_yaml.py` (P1) и cleanup inline python3 (P5):

| # | Проблема | Объём | Severity | Зависимость от W1 (038a) |
|---|----------|-------|----------|---------------------------|
| P2 | `sys.exit()` в библиотечных функциях `project_registry.py` | 14 вызовов | HIGH | **Нет** — независима |
| P3 | Hardcoded имена логгеров: `getLogger("literal")` | 15 файлов | MEDIUM | **Нет** — независима |
| P4 | 4 стратегии обработки ошибок: `RuntimeError`, `except Exception`, silent swallow, return None/False | ~30 файлов, ~100 блоков | HIGH | **Да** — требует `exceptions.py` из 038a |

### 2. Why Decouple

**Причина декомпозиции DevPlan 038 → 038a + 038b:**

- DevPlan 038 — LARGE (5 волн, ~90 файлов). Реализация одной кодовой сессией невозможна.
- W1 (единый фасад `node_yaml.py`) — breaking change, требует extended review и staging-test.
- W2+W3+W4 — не-breaking (W2, W3) или low-risk (W4 использует новые классы исключений из 038a), могут быть объединены в один PR.
- W2 и W3 можно делать параллельно с W1 (разные файлы, нет конфликтов). W4 ждёт `exceptions.py` из 038a.
- 038b суммарно затрагивает ~50 файлов diff <800 строк — review возможен за один проход.

### 3. Scope

#### Scope In (W2 — sys.exit removal, 5 files)
- `core/internal/shared/project_registry.py`: 14 `sys.exit()` → `return (bool, str)`
- CLI (`__main__`): маппинг `(bool, str)` → `sys.exit(0/1)`
- Shell-callers: `add-project.sh`, `adopt-project.sh`, `remove-project.sh` — проверить exit code handling
- Unit-тесты: `tests/unit/test_project_registry.py` (новый)

#### Scope In (W3 — hardcoded loggers → `__name__`, 15 files)
- Механическая замена `getLogger("literal")` → `getLogger(__name__)` в 15 Python-файлах
- Полный список: см. DevPlan 038 §Problem 3 (строки 744-763)

#### Scope In (W4 — typed exception hierarchy, ~30 files)
- Замена ~20 `raise RuntimeError` → `PlatformFatalError` / `ConfigNotFoundError` в `state_machine.py`, `steps.py`
- Сужение `except Exception` → ожидаемые типы в `reconciler_projects.py`, `s3_ssl_cache.py`, `cert_orchestrator.py`
- Batch update оставшихся ~80 блоков `except Exception` в 21 файле (полный список: DevPlan 038 §P4.5, строки 813-837)
- Обновление верхнеуровневых обработчиков: `except PlatformError`, маппинг `exit_code`
- Новый CI gate: `make check-exception-patterns`
- Дополнение `tests/unit/test_exceptions.py` (создан в 038a) тестами на маппинг `exit_code` → `sys.exit`

#### Scope Out
- W1 (единый фасад `node_yaml.py`) — в 038a
- W5 (inline python3 cleanup) — в 038c (или отдельным PR)
- `docker_compose.py` — deferred (инфраструктурный слой)
- `python_deps.sh` / `install-docker.sh` — легитимные inline python3

### 4. Stakeholders

| Стейкхолдер | Интересы |
|-------------|----------|
| Разработчики платформы | `project_registry` можно использовать из Python-кода без `sys.exit()` |
| CI/CD система | Консистентные exit codes через типизированные исключения |
| Операторы | Понятные лог-префиксы (qualified module name) |
| QA | Тестируемые функции с `Tuple[bool, str]` вместо process termination |

### 5. Success Criteria

Из родительского DevPlan 038, отфильтрованные только для W2+W3+W4:

| AC | Критерий | Проверка | Target Wave |
|----|----------|----------|-------------|
| AC2 | `project_registry.py` без `sys.exit()` в библиотечных функциях | `grep 'sys.exit' core/internal/shared/project_registry.py` → только в `if __name__` | W2 |
| AC3 | Все логгеры используют `__name__` | `grep 'getLogger("[a-z]' core/internal/ core/modules/` → 0 | W3 |
| AC4 | Иерархия исключений определена (4 subclass от PlatformError) | `grep 'class.*Error.*PlatformError' core/internal/shared/exceptions.py` → 4 matches | W4 (зависит от 038a) |
| AC5 | 0 `raise RuntimeError` для платформенных ошибок | `grep 'raise RuntimeError' core/internal/bootstrap/` → 0 | W4 |
| AC6 | `except Exception` сужены до ожидаемых типов | `grep 'except Exception' core/internal/` → только в `__main__` / CLI верхнего уровня | W4 |
| AC8 | `make gate MODE=fast` passes | CI green | All |
| AC10 | Все существующие тесты проходят (no regression) | `python -m pytest tests/ -s -v` → all pass | All |

### 6. Risks

| # | Риск | Severity | Mitigation |
|---|------|----------|------------|
| R1 | **W4: неполное сужение `except Exception`** — часть блоков остаётся с голым `except Exception`, silent swallow продолжается | MEDIUM | CI gate `make check-exception-patterns` блокирует merge, если найден `except Exception` в non-`__main__` коде |
| R2 | **W2: shell-callers ломаются** — `add-project.sh` ожидает `sys.exit(1)`, но получает `sys.exit(0)` при ошибке | MEDIUM | CLI `__main__` маппит `return (False, msg)` → `sys.exit(1)`. Идентичное поведение. Проверить shell-callers через grep `|| log_warn` / `|| exit`. |
| R3 | **W3: log parsers ломаются** — замена `"docker_compose"` → `"core.internal.shared.docker_compose"` меняет префикс модуля | LOW | LDD формат `[IMP:X][func_name]` не меняется. Меняется только модульный префикс — grep по `[IMP:` продолжает работать. |
| R4 | **W4: `exit_code` mismatch** — `except PlatformError` в верхнем уровне маппит не тот exit code | MEDIUM | Каждый subclass PlatformError имеет атрибут `exit_code`. Верхнеуровневый handler: `sys.exit(e.exit_code)`. Unit-тесты `test_exceptions.py` проверяют маппинг. |
| R5 | **W4: не все `RuntimeError` заменены** — легитимные `RuntimeError` (Python runtime, не платформенные) заменены ошибочно | LOW | Только `RuntimeError` с платформенными сообщениями («must run as root», «node.yaml not found», «apt-get failed», «secrets.env not found») заменяются. Прочие `RuntimeError` не трогаются. Grep audit перед PR. |

$END_BRIEF
