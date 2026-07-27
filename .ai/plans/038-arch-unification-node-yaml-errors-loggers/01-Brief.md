$START_BRIEF

# Brief 038 — Архитектурная унификация: node.yaml, exceptions, loggers, inline python3

## $ARTIFACT_CONTRACT

| Поле | Значение |
|------|---------|
| **PURPOSE** | Устранить фрагментацию 5 архитектурных подсистем платформы — единый фасад YAML, типизированные исключения, консистентное логирование, чистая библиотечная архитектура — в рамках одного LARGE-рефакторинга |
| **DESCRIPTION** | Проблема: 7 разных паттернов чтения node.yaml в 60+ файлах, `sys.exit()` в библиотечных функциях, 16 hardcoded логгеров, 4 стратегии обработки ошибок, ~20 inline python3. Brief фиксирует границы задачи, scope in/out и критерии успеха — до superposition и проектирования DevPlan |
| **RATIONALE** | После DevPlans 070-085 платформа прошла unification-волну, кодовая база стабилизировалась. Это окно для архитектурной унификации без риска конфликтов. Проблемы 1-5 взаимосвязаны: единый фасад требует типизированных исключений, типизированные исключения требуют `sys.exit()` → return codes. |
| **ACCEPTANCE_CRITERIA** | 1. Единый фасад `NodeYaml` — все 77+ yaml.safe_load для node.yaml проходят через него. 2. `project_registry.py` — 0 `sys.exit()` в библиотечных функциях. 3. Все 16 логгеров используют `__name__`. 4. Иерархия типизированных исключений (5 классов). 5. 0 inline `python3 -c "import yaml"` в shell-скриптах. Требования AC1-AC7 из VerificationReport. |
| **IMPLEMENTS** | Архитектурная унификация платформы — фаза 1 (стабилизация после DevPlans 070-085) |
| **IMPACTS** | `core/internal/shared/` (новый unified node_yaml.py, exceptions.py), `core/internal/bootstrap/` (30+ Python-файлов в lifecycle/, deploy/, converge/), `core/lib/` (yaml_read.sh, node-resolver.sh), `core/entrypoints/`, `core/modules/` (status-page, postgres-hooks), ~20 shell-файлов, `tests/` |
| **REQUIRES** | DevPlan 070 (shared libs extraction) — COMPLETED, DevPlan 079 (bootstrap pipeline restructuring) — COMPLETED, AGENTS.md языковая политика, Python 3.10+, PyYAML, pytest |

---

## $DOCUMENT_PLAN

### 1. Problem Statement

Пять архитектурных проблем, обнаруженных при аудите после DevPlans 070-085:

| # | Проблема | Объём | Severity |
|---|----------|-------|----------|
| P1 | 7 разных паттернов чтения node.yaml | 60+ файлов, 77+ yaml.safe_load, 39 файлов прямого чтения | CRITICAL — отсутствие единого API ведёт к drift и дублированию |
| P2 | `sys.exit()` в библиотечных функциях | 14 вызовов в `project_registry.py` | HIGH — блокирует переиспользование из Python-кода |
| P3 | Hardcoded имена логгеров | 16 файлов (`getLogger("literal")`) | MEDIUM — нарушает Python-конвенцию, усложняет отладку |
| P4 | 4 стратегии обработки ошибок | RuntimeError, silent swallow, return None, return False — 94 except Exception | HIGH — непредсказуемое поведение при сбое |
| P5 | Inline python3 с import yaml | ~20 вызовов в shell-скриптах | MEDIUM — нарушение языковой политики, нет LDD-телеметрии |

### 2. Why Now

- **Кодовая база стабилизирована** — после DevPlans 070-085 (shared libs extraction, bootstrap pipeline restructuring) платформа прошла unification-волну. Изменения в этом окне имеют минимальный конфликт с параллельной работой.
- **Проблемы взаимосвязаны** — P1 (единый фасад) требует P4 (типизированные исключения), P4 требует P2 (sys.exit removal). Разделение на 5 независимых DevPlans создаст dependency hell.
- **Maintenance cost растёт** — каждый новый модуль копирует один из 7 паттернов чтения node.yaml, увеличивая drift.

### 3. Scope

#### Scope In
- Единый фасад `NodeYaml` — `core/internal/shared/node_yaml.py` (расширение существующего)
- Иерархия типизированных исключений — `core/internal/shared/exceptions.py` (новый модуль)
- Замена `sys.exit()` на return codes в `project_registry.py`
- Замена hardcoded `getLogger("literal")` на `__name__` в 16 файлах
- Сужение `except Exception` до ожидаемых типов (~94 блока)
- Миграция inline `python3 -c "import yaml"` на CLI фасада
- Unit-тесты для фасада, исключений, project_registry
- CI gates: `check-exception-patterns`, обновление `check-no-new-inline-python3`

#### Scope Out
- `docker_compose.py` — инфраструктурный слой, deferred
- jsonschema валидация — уже существует в `validate.sh`
- `python_deps.sh` — легитимная проверка наличия модуля
- `install-docker.sh` — platform detection однострочник
- Streaming/lazy section load для YAML (не требуется для <50KB файлов)
- Atomic write / partial update node.yaml (читай != пиши)

### 4. Stakeholders

| Стейкхолдер | Интересы |
|-------------|----------|
| Разработчики платформы | Единый API для YAML — меньше когнитивной нагрузки |
| CI/CD система | Консистентные exit codes, предсказуемое поведение при ошибках |
| Будущие модули | Typed contracts для YAML-чтения |
| Операторы | Понятные лог-префиксы (qualified name) |
| QA | Тестируемый фасад с покрытием ≥90% |

### 5. Success Criteria

Критерии из VerificationReport 02 — AC1-AC7 (измеримые, grep-based):

| AC | Критерий | Проверка |
|----|----------|----------|
| AC1 | Все yaml.safe_load через NodeYaml | `grep 'yaml.safe_load' core/internal/` → только в `yaml_query.py:_load_yaml` и `NodeYaml.load` |
| AC2 | project_registry без sys.exit() | `grep 'sys.exit' project_registry.py` → только в `if __name__` |
| AC3 | Логгеры через __name__ | `grep 'getLogger("[a-z]' core/internal/ core/modules/` → 0 |
| AC4 | Иерархия исключений определена | `grep 'class.*Error.*PlatformError'` → 4 subclass |
| AC5 | 0 raise RuntimeError | `grep 'raise RuntimeError' core/internal/bootstrap/` → 0 |
| AC6 | except Exception сужены | `grep 'except Exception' core/internal/` → только в `__main__` |
| AC7 | 0 inline import yaml | `grep -rn 'python3 -c.*import yaml' core/ --include='*.sh'` → 0 |

### 6. Risks (Pre-Design)

| # | Риск | Severity |
|---|------|----------|
| R1 | Конфликт с параллельными DevPlans (079/081/082) — те же файлы | HIGH |
| R2 | Breaking change в API — 60+ consumers | HIGH |
| R3 | Неполная миграция — часть consumers остаётся на старом API | MEDIUM |
| R4 | Regression в shell exit codes | MEDIUM |

$END_BRIEF
