# GREP_SUMMARY: AGENTS.md, tests, invariants, repair-contract, gates, registration, reuse, anti-tamper, wave-pipeline
# STRUCTURE: ┌invariants + repair-contract┐ → ◇ taxonomy → ◇ critical contracts → ⊕ cross-refs
# AGENTS.md — tests/

# region MODULE_CONTRACT
## @purpose  Test architecture: invariants, Repair Contract (DevPlan 060), gate registration, critical contracts.
## @scope    Everything under tests/ — invariants, reuse, wave-pipeline, gate registration, repair, anti-tamper.
## @invariants
##   1. tests/conftest.py — thin facade (<200 LOC).
##   2. tests/helpers/gate_helpers.py — canonical source for YAML loading, project root, LDD assertions.
##   3. Container names/ports NEVER hardcoded — derive from _conftest/infra.py::infra.
##   4. pytest.skip ТОЛЬКО для инфраструктурной недоступности. Skip-as-bug-masking запрещён.
##   5. Docker-dependent: @pytest.mark.requires_docker. Статические — без него.
##   6. Test Honesty R4 enforced на CI. Полные правила: .kilo/rules/testing.md.
##   7. Wave-Pipeline: тесты сортируются по волнам из module.yaml#depends_on (T5).
##   8. Gate Trinity: файл tests/gates/ + @pytest.mark.gate + entrypoint-manifest.yaml (T11).
##   9. Repair Contract: gates в manifest получают repair-поля (DevPlan 060). L1-ошибки → make fix-gate.
## @rationale Единый source of truth. Предотвращает повторение: hardcoded контейнеры, skip-as-fail,
##            дублирование хелперов. Repair Contract = основа AI-self-healing gate-ошибок.
##            Полные правила тестирования (R1-R5, LDD, Anti-Loop) — в .kilo/rules/testing.md.
# endregion MODULE_CONTRACT

---

## Repair Contract Infrastructure (DevPlan 060)

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
| (2) `make fix-gate` | `fix-executable-bit` + `fix-ruff` + `generate-manifests` | После падения CI, одна команда |
| (3) M-ADE Envelope | `[GATE:FAIL][id:X][class:L1]` `>>> REPAIR_RECIPE_START >>>` … `<<< REPAIR_RECIPE_END <<<` | В выводе gate-ошибки, machine-parsable |

**Ключевые контракты:**
- `repair_id` — первичный API-ключ. AI-агент читает manifest → repair_id → repair_command. Команда может меняться, repair_id стабилен.
- `make fix-gate` — ТОЛЬКО gate-blocking L1 ошибки (executable-bit + ruff + manifest drift). НЕ расширять без ревью.
- `make fix-gate DRY_RUN=1` — вывод "would fix" без мутации.
- Структурированный stdout: `[REPAIR:FIXED]`, `[REPAIR:NOOP]`, `[REPAIR:ERROR]`.
- Pre-flight CI правило: `make fix-gate && git add -u && make gate MODE=fast` (см. `.kilo/rules/_project.md`).
- `repair.mk` экспортирует `REPAIR_TARGETS` — machine-readable реестр для CI-валидации (`test_repair_contract_integrity`).

---

## Directory Taxonomy

| Директория | Назначение | Признак |
|-----------|-----------|---------|
| `tests/gates/` | CI gate-тесты (`make gate MODE=fast`) | `@pytest.mark.gate` |
| `tests/contracts/` | Контрактные тесты entrypoints | `make contracts` |
| `tests/unit/` | Unit-тесты Python-модулей (без Docker) | Нет `requires_docker` |
| `tests/e2e/` | E2E pipeline тесты на test-VPS (DevPlan 095) | `@pytest.mark.requires_node`, `make test-node NODE=<name>`. **НЕ** в `make test MARKER=all` и `make gate` (фильтр `not requires_node`). Нужны NODE env, SSH, AGE-ключ; без test-VPS → FAIL (Rule R4) |
| `tests/_conftest/` | Внутренние фикстуры/хелперы. **НЕ содержит тестов.** | Пакет, `__init__.py` |
| `tests/helpers/` | Shared helpers (YAML, LDD, repo_root) | `gate_helpers.py` |
| `tests/test_data/` | Статические test fixtures | node.yaml auto-validated |
| `tests/tools/` | Инструменты (sync_inventory.py). **Не тесты.** | — |

### Куда класть тест

- **Требует Docker** → `tests/test_*.py` в корне (`@pytest.mark.requires_docker`)
- **НЕ требует Docker, тестирует Python-модуль** → `tests/unit/test_*.py`
- **CI gate** → `tests/gates/test_gate_*.py` + регистрация в manifest
- **Контракт entrypoint** → `tests/contracts/test_make_target_contracts.py`

---

## Critical Contracts

### Container Reuse (T3)

Module-scoped фикстуры **переиспользуют** контейнеры platform_services (~350s экономии):
1. `check_foreign_containers()` — `docker inspect` проверяет `com.docker.compose.project` label
2. Чужой проект → reuse mode: skip compose up/down
3. `wait_for_containers_healthy()` — поллинг health status перед yield

⚠️ TRAP[DECISION] · Return type: `dict[str, str]` (container_name → project), не `list[str]`

### Wave-Pipeline (T5)

Тесты сортируются по wave number из `module.yaml#depends_on`. Overlap (не параллельность): Wave 0 стартуют пока Wave 1 контейнеры запускаются (~100s gain). Механизм: `threading.Event`, timeout 600s.

### Anti-Tamper Replication (T18)

`tools/sync_inventory.py::collect_tests()` и `gates/test_gate_test_inventory.py::_collect_tests()` — **намеренно идентичные копии** XML-парсера `pytest --collect-only -q`. НЕ импортировать, НЕ рефакторить в shared library — изменение общей библиотеки делает gate всегда зелёным.

⚠️ TRAP[DECISION] · Intentional code duplication as security boundary

### infra Singleton Import Protocol (T21)

`from _conftest.infra import infra` — OK. `from _conftest import infra` — **не сработает**. infra не re-экспортируется через `__init__.py` (предотвращает subprocess при import, T16).

**Lazy-инициализация (T5, DevPlan 116 B10):** `infra` — `_LazyTestInfraProxy` (PEP 562-стиль). Импорт `_conftest.infra` НЕ запускает subprocess — `discover_modules.py --test-infra` выполняется при ПЕРВОМ обращении к accessor-методу (`get_container_name`, `get_test_port`, ...), затем кэшируется (`@lru_cache` — 1 subprocess на сессию). Статические сессии без Docker не запускают discover_modules. Косвенный доступ к `_data`/`_index`/`_delegate` — внутренний контракт, НЕ использовать из тестов.

### LDD Trajectory (T6)

`@ldd_trajectory` декоратор (`tests/_conftest/ldd.py`) — автоматическая проверка `[IMP:9]` лога. Для тестов без декоратора: `gate_helpers.assert_ldd_imp9(caplog)`.

---

## Gate Registration

Gate-тест ДОЛЖЕН быть зарегистрирован в **трёх местах** (подробнее: `tests/gates/AGENTS.md`):

1. Файл `tests/gates/test_gate_*.py`
2. Декоратор `@pytest.mark.gate`
3. Запись в `core/entrypoint-manifest.yaml` (секция `gates`) — с repair-полями если L1

Пропуск любого → gate не запускается в `make gate`. Trinity enforcement: `test_gate_manifest_integrity.py`.

---

## Cross-References

| Файл | Назначение |
|------|-----------|
| [`tests/gates/AGENTS.md`](gates/AGENTS.md) | Gate taxonomy, registration protocol |
| `../AGENTS.md` (root) | Архитектурные инварианты платформы |
| [`../core/AGENTS.md`](../core/AGENTS.md) | Канонические операции, слои |
| [`../core/entrypoint-manifest.yaml`](../core/entrypoint-manifest.yaml) | YAML-реестр gates + repair-поля |
| [`../makefiles/repair.mk`](../makefiles/repair.mk) | Repair targets: fix-executable-bit, fix-ruff, fix-gate |
| `.ai/plans/060-self-healing-gates/DevPlan.md` | Полный DevPlan Repair Contract Infrastructure |
| `.kilo/rules/testing.md` | **Канонический источник:** R1-R5, LDD telemetry, Anti-Loop protocol |
| `.kilo/rules/markup.md` | Semantic markup standard |
