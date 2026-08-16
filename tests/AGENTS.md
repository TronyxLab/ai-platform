# GREP_SUMMARY: AGENTS.md, tests, invariants, repair-contract, gates, registration, reuse, quarantine, wave-pipeline
# STRUCTURE: ┌invariants + repair-contract┐ → ◇ taxonomy → ◇ critical contracts → ⊕ cross-refs
# AGENTS.md — tests/

# region MODULE_CONTRACT
## @purpose  Test architecture: invariants, Repair Contract, gate registration, critical contracts.
## @scope    Everything under tests/ — invariants, reuse, wave-pipeline, gate registration, repair, quarantine.
## @invariants
##   1. tests/conftest.py — thin facade (<200 LOC).
##   2. tests/helpers/gate_helpers.py — canonical source for YAML loading, project root, LDD assertions.
##   3. Container names/ports NEVER hardcoded — derive from _conftest/infra.py::infra.
##   4. pytest.skip ТОЛЬКО для инфраструктурной недоступности. Skip-as-bug-masking запрещён.
##   5. Docker-dependent: @pytest.mark.requires_docker. Статические — без него.
##   6. Test Honesty R4 enforced на CI. Полные правила: ../.kilo/rules/testing.md.
##   7. Wave-Pipeline: тесты сортируются по волнам из module.yaml#depends_on (T5).
##   8. Gate Trinity: файл tests/gates/ + @pytest.mark.gate + entrypoint-manifest.yaml (T11).
##   9. Repair Contract: gates в manifest получают repair-поля. L1-ошибки → make fix-gate.
##   10. xdist-безопасность: `-n auto` — стандарт для статических тестов; docker-тесты —
##       single-process по построению (один стек на машину) — это не исключение, а свойство
##       домена (см. §Параллельный запуск).
##   11. Quarantine-протокол: карантин — ТОЛЬКО docker/сетевые слои
##       (маркеры requires_docker/smoke/component/integration); детерминированные слои
##       (static/unit/gates) — «флак = баг», карантин запрещён; запись без Rev-даты = RED.
## @rationale Единый source of truth. Предотвращает повторение: hardcoded контейнеры, skip-as-fail,
##            дублирование хелперов. Repair Contract = основа AI-self-healing gate-ошибок.
##            Полные правила тестирования (R1-R5, LDD, Anti-Loop) — в ../.kilo/rules/testing.md.
# endregion MODULE_CONTRACT

---

## sys.path policy

**Canonical roots** (добавляются `tests/conftest.py` через `site.addsitedir`, доступны всем тестам):
- `<repo_root>/` — import core, tests, etc.
- `<repo_root>/core/` — import internal, modules, lib, entrypoints
- `<repo_root>/core/internal/` — import bootstrap, deploy, scripts, shared

**Module-specific paths** (добавляются точечно в тестовом файле):
- `core/modules/<name>/` — для тестов, импортирующих модульный код (app.py, etc.)
- `core/internal/scripts/` — для тестов generate-скриптов
- Шаблон: `sys.path.insert(0, str(Path(__file__).parent.parent / "core" / "modules" / "<name>"))`

**Правила:**
1. Новые тесты НЕ добавляют пути, уже покрытые conftest-хуком (`<repo_root>/`, `core/`, `core/internal/`).
2. Module-specific пути — только когда импортируется код из `core/modules/` или `core/internal/scripts/`.
3. Запрещено: `sys.path.append`, относительные импорты за пределами tests/, манипуляции с `__path__`.
4. Существующие `sys.path.insert` для путей, теперь покрытых conftest-хуком, ИЗБЫТОЧНЫ — их удаление
   опционально (не блокирует gate, site.addsitedir идемпотентен).

**Обоснование:** гибрид — общие корневые пути через conftest один раз, module-specific точечно по
шаблону. Нулевая стоимость для новых тестов, явный контроль для специфичных импортов.

---

## R5 ANTI-SURVIVORSHIP Protocol

### Правило (Test Honesty R5, `../.kilo/rules/testing.md`)

Для каждого gate-теста, ссылающегося на bug/issue ID, ДОЛЖЕН существовать negative-тест с точным
входом, поймавшим оригинальный баг. Negative не может существовать без detector: он ломается, если
детектор перестаёт ловить регрессию.

### Формат negative-теста

```python
# 🧪 TRAP[TEST] · NEGATIVE (R5) · <детектор> — <bug-id>
# · Last fail: <исходный вход, поймавший bug U-XX>
# · Remove if: <условие устаревания детектора>
def test_<detector>_negative_<bug_id>(self) -> None:
    """R5 negative: исходный вход, поймавший bug U-XX, детектируется."""
    violations = _scan_function(bug_trigger_input)
    assert len(violations) >= 1, f"R5 FAIL: detector missed original bug U-XX trigger"
```

### Проверка покрытия (структурная)

R5-покрытие проверяется структурно: негативный тест обязан существовать в том же файле, что и
детектор (см. probe-конвенцию в `tests/gates/AGENTS.md` §R5), и падать при отключении детектора.
Полный формат — `../.kilo/rules/testing.md` §R5.

---

## Quarantine-протокол

Политика работы с флак **docker/сетевых** слоёв (smoke/component/integration/requires_docker).
Реестр: `tests/_conftest/quarantine.py::QUARANTINE` (пуст по умолчанию); механизм —
`pytest_collection_modifyitems` (подключается из `tests/conftest.py`, 1 строка).

| Правило | Описание |
|---------|----------|
| **Что можно карантинить** | Только docker/сетевые тесты (маркеры `requires_docker`/`smoke`/`component`/`integration`) |
| **Детерминированные слои** | `static`/`unit`/`gates` — «флак = баг», карантин **ЗАПРЕЩЁН**; запись в реестре для item'а без docker-маркера не применяется (механизм защищает сам) |
| **Процедура** | Флак docker-теста → запись в `QUARANTINE` (`nodeid → {until, reason, debt_ref}`) + Debt-артефакт с Rev-датой (`.ai/plans/*/*-Debt.md`) |
| **Rev-дата обязательна** | Запись без `until` (YYYY-MM-DD) или с невалидной датой = **RED** — `validate_quarantine()` в хуке поднимает `RuntimeError` при сборе коллекции |
| **Автоматика** | Для nodeid из реестра с docker/network-маркером хук делает `pytest.skip` с reason: `[QUARANTINE] <nodeid> — <reason> — Rev: <until> (Debt: <debt_ref>)` |

**Skip-as-bug-masking запрещён:** карантин — временная мера с обязательным сроком пересмотра
(Rev-дата). Детерминированный тест (unit/static/gates), падающий флаки, — баг теста, а не кандидат
в карантин. R4-честность (`../.kilo/rules/testing.md`) сохраняется: карантинный skip виден в отчёте
как `[QUARANTINE]` с Rev-датой, а не как «служебный» пропуск.

---

## Repair Contract Infrastructure

Каждый gate в `entrypoint-manifest.yaml` получает repair-контракт — поля, описывающие исправимость ошибки:

```yaml
gates:
  - id: executable-bit
    repairable: true
    repair_id: "executable-bit"           # Стабильный API-ключ для AI-self-healing
    repair_command: "make fix-gate"        # Команда исправления
    repair_description: "Sets +x on .sh files outside core/lib/"
    repair_safe: true                      # Не меняет семантику
    repair_idempotent: true                # Повторный запуск = no-op
    repair_class: L1                       # L1=auto, L2=confirm, L3=never
```

**Трёхуровневая защита от детерминированных gate-ошибок:**

| Уровень | Что | Когда |
|---------|-----|-------|
| (1) Pre-commit hook | `fix-executable-bit` — +x для staged `.sh` | До коммита, прозрачно |
| (2) `make fix-gate` | `fix-executable-bit` + `fix-ruff` + `fix-pycache` + `generate-manifests` | После падения CI, одна команда |
| (3) M-ADE Envelope | `[GATE:FAIL][id:X][class:L1]` `>>> REPAIR_RECIPE_START >>>` … `<<< REPAIR_RECIPE_END <<<` | В выводе gate-ошибки, machine-parsable |

**Ключевые контракты:**
- `repair_id` — первичный API-ключ. AI-агент читает manifest → repair_id → repair_command. Команда может меняться, repair_id стабилен.
- `make fix-gate` — ТОЛЬКО gate-blocking L1 ошибки (executable-bit + ruff + pycache + manifest drift). НЕ расширять без ревью.
- `make fix-gate DRY_RUN=1` — вывод "would fix" без мутации.
- Структурированный stdout: `[REPAIR:FIXED]`, `[REPAIR:NOOP]`, `[REPAIR:ERROR]`.
- Pre-flight CI правило: `make fix-gate && git add -u && make check` (см. `../.kilo/rules/_project.md`).
- `repair.mk` экспортирует `REPAIR_TARGETS` — machine-readable реестр для CI-валидации (`test_repair_contract_integrity`).
- Диагностический цикл кодера: `make check` (SoT-манифест `core/check-suite.yaml`) → фикс-цикл →
  финальная верификация `make check` до чистоты; `make gate MODE=fast` — ТОЛЬКО pre-push hook. Узкий таргет — `make check-diff`.

---

## Directory Taxonomy

| Директория | Назначение | Признак |
|-----------|-----------|---------|
| `tests/gates/` | CI gate-тесты | `@pytest.mark.gate` + регистрация в manifest (тринити) |
| `tests/contracts/` | Контрактные тесты entrypoints | `@pytest.mark.contract` — `make check MARKER=contract` |
| `tests/unit/` | Unit-тесты Python-модулей (без Docker) | Нет `requires_docker` |
| `tests/e2e/` | E2E pipeline тесты на test-VPS | `@pytest.mark.requires_node`, `make test-node NODE=<name>`. **НЕ** в `make check` и `make gate` (фильтр `not requires_node`). Нужны NODE env, SSH, AGE-ключ; без test-VPS → FAIL (Rule R4) |
| `tests/_conftest/` | Внутренние фикстуры/хелперы. **НЕ содержит тестов.** | Пакет, `__init__.py` |
| `tests/helpers/` | Shared helpers (YAML, LDD, repo_root) | `gate_helpers.py` |
| `tests/test_data/` | Статические test fixtures | node.yaml auto-validated |
| `tests/tools/` | Инструменты. **Не тесты.** | — |

### Куда класть тест

- **Требует Docker** → `tests/test_*.py` в корне (`@pytest.mark.requires_docker` / `component` / `smoke`)
- **НЕ требует Docker, тестирует Python-модуль** → `tests/unit/test_*.py`
- **CI gate** → `tests/gates/test_gate_*.py` + регистрация в manifest
- **Контракт entrypoint** → `tests/contracts/test_make_target_contracts.py`

**Таксономия корня:** корень `tests/` содержит ТОЛЬКО Docker-зависимые тесты (component/smoke/
requires_docker). Component-раннер в core/check-suite.yaml: `pytest tests/ -m "component"` (маркер
только у `test_component_*.py`). Новые unit-тесты кладутся ТОЛЬКО в `tests/unit/`; файл в корне без
docker-маркера — нарушение таксономии.

---

## Critical Contracts

### Container Reuse (T3)

Module-scoped фикстуры **переиспользуют** контейнеры platform_services:
1. `check_foreign_containers()` — `docker inspect` проверяет `com.docker.compose.project` label
2. Чужой проект → reuse mode: skip compose up/down
3. `wait_for_containers_healthy()` — поллинг health status перед yield

### Wave-Pipeline (T5)

Сортировка по wave number из `module.yaml#depends_on`; overlap (не параллельность):
Wave 0 стартуют пока Wave 1 контейнеры запускаются; `threading.Event`, timeout 600s.

### infra Singleton Import Protocol (T21)

`from _conftest.infra import infra` — OK. `from _conftest import infra` — **не сработает**. infra не
re-экспортируется через `__init__.py` (предотвращает subprocess при import).

**Lazy-инициализация:** `infra` — `_LazyTestInfraProxy` (PEP 562-стиль). Импорт `_conftest.infra`
НЕ запускает subprocess — `discover_modules.py --test-infra` выполняется при ПЕРВОМ обращении к
accessor-методу (`get_container_name`, `get_test_port`, ...), затем кэшируется (1 subprocess на
сессию). Статические сессии без Docker не запускают discover_modules. Косвенный доступ к
`_data`/`_index`/`_delegate` — внутренний контракт, НЕ использовать из тестов.

### LDD Trajectory (T6)

`@ldd_trajectory` декоратор (`tests/_conftest/ldd.py`) — автоматическая проверка `[IMP:9]` лога.
Для тестов без декоратора: `gate_helpers.assert_ldd_imp9(caplog)`.

---

## Параллельный запуск (pytest-xdist)

Запуск через `-n auto` — стандарт (test_runner/check-suite); флак параллельного прогона = баг теста.
Обязательные правила (неочевидные; очевидная pytest-гигиена — monkeypatch/cwd/wait-фикстуры — опущена):

1. **Docker — только канонические фикстуры** (`platform_services`, модульные из `_conftest/smoke.py`).
   Прямой `docker compose up` запрещён: воркеры конкурентно поднимают один стек.
2. **`xdist_group("serial")` НЕ работает** при `-n auto` — не использовать.
3. **Общие ресурсы не мутируются:** файлы — `tmp_path`; рабочее репо
   (git add/commit/checkout, tracked-файлы) — read-only; docker/счётчик — через flock +
   master-семантику (`_conftest/counter.py`; session-хуки — только master, `PYTEST_XDIST_WORKER`).
   Остальное — стандартная pytest-гигиена.

---

## Gate Registration

Gate-тест ДОЛЖЕН быть зарегистрирован в **трёх местах** (подробнее: `tests/gates/AGENTS.md`):

1. Файл `tests/gates/test_gate_*.py`
2. Декоратор `@pytest.mark.gate`
3. Запись в `core/entrypoint-manifest.yaml` (секция `gates`) — с repair-полями если L1

Пропуск любого → gate не запускается в `make gate`. Trinity enforcement: `test_gate_manifest_integrity.py`.

---

## Удаление тестов

Удаление теста — обычная операция PR. Качество тестов защищают структурные гейты:
R1 no-pass-tests, R3 stale-skip, R4 no-service-fail, test_gate_test_naming (test_-префикс) —
списки-базлайны не ведутся.

---

## Cross-References

| Файл | Назначение |
|------|-----------|
| [`tests/gates/AGENTS.md`](gates/AGENTS.md) | Gate taxonomy, registration protocol |
| `../AGENTS.md` (root) | Архитектурные инварианты платформы |
| [`../core/AGENTS.md`](../core/AGENTS.md) | Канонические операции, слои |
| [`../core/entrypoint-manifest.yaml`](../core/entrypoint-manifest.yaml) | YAML-реестр gates + repair-поля |
| [`../makefiles/repair.mk`](../makefiles/repair.mk) | Repair targets: fix-executable-bit, fix-ruff, fix-pycache, fix-gate |
| `../.kilo/rules/testing.md` | **Канонический источник:** R1-R5, LDD telemetry, Anti-Loop protocol |
| `../.kilo/rules/markup.md` | Semantic markup standard |
