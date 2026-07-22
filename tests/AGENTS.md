<![CDATA[<!-- GREP_SUMMARY: AGENTS.md, tests, invariants, decisions, T1-T21, skip-gate, reuse, wave-pipeline, auto-discovery, import-contract, anti-tamper -->
<!-- STRUCTURE: ┌invariants + decision-tree┐ → ◇ T1-T15 non-obvious decisions → ◇ T16-T21 infrastructure internals → ⊕ cross-refs -->

# AGENTS.md — tests/

# region MODULE_CONTRACT
## @purpose  Test architecture, agent-critical invariants, and non-obvious design decisions.
## @scope    Everything under tests/ — invariants, reuse, wave-pipeline, auto-discovery, import contracts, anti-tamper.
## @invariants
##   1. tests/conftest.py — thin facade (<200 LOC). Wave logic in conftest.py (_compute_module_waves) — исключение (duplicated algorithm, T17).
##   2. tests/helpers/gate_helpers.py — canonical source for YAML loading, project root, LDD assertions. Не дублировать yaml.safe_load (DRIFT-LITE в 2 gate-файлах).
##   3. Container names and ports NEVER hardcoded — derive from docker-compose.test.yml via _conftest/infra.py::infra.
##      Исключения: test_infra_discovery.py (тестирует сам infra), docker rm -f в cleanup-коде (T14 mitigation).
##   4. pytest.skip ТОЛЬКО для инфраструктурной недоступности (no Docker, no env, no network). Skip-as-bug-masking запрещён.
##   5. Docker-dependent тесты: @pytest.mark.requires_docker. Статические — без него.
##   6. Test Honesty R4 enforced на CI через honesty.py. R1, R3 — code review (planned CI gate). Полные правила — .kilo/rules/testing.md.
##   7. Wave-Pipeline: тесты сортируются по волнам зависимостей из module.yaml#depends_on.
##   8. Container reuse: module-фикстуры переиспользуют контейнеры platform_services, а не поднимают свои.
## @rationale Единый source of truth о неочевидных из кода решениях (TRAP[DECISION], TRAP[DEBT], architectural gates).
##            Предотвращает повторение ошибок: hardcoded контейнеры, skip-as-fail, дублирование хелперов.
##            Полные правила тестирования (R1-R5, LDD telemetry, Anti-Loop protocol) — в .kilo/rules/testing.md.
##            T-номера в коде (T10, T12, T13, T18-T21) — ссылки на DevPlan-задачи, не на секции этого документа.
## @changes 2026-07-22 v2: +T16-T21 (infrastructure internals audit), drift fixes (invariants 1,3,6; T9).
# endregion MODULE_CONTRACT

---

## Directory Taxonomy

| Директория | Назначение | Признак |
|-----------|-----------|---------|
| `tests/gates/` | CI gate-тесты (`make gate MODE=fast`) | `@pytest.mark.gate` |
| `tests/contracts/` | Контрактные тесты entrypoints | `make contracts` |
| `tests/unit/` | Unit-тесты Python-модулей (без Docker) | Нет `requires_docker` |
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

## Non-Obvious Decisions

### Test Honesty & Skip Policy

#### T1 — R4 Honesty Implementation

`require_docker_or_fail()` / `require_script_or_fail()` / `require_env_or_fail()` — канонический способ проверки доступности сервиса. Режим: `REQUIRE_HONESTY_MODE`:

- `marker` (default) → `pytest.skip` с `[honesty:marker]`
- `xfail` → `pytest.xfail(strict=False)`
- `fail` → `pytest.fail` (честный)

⚠️ TRAP[DECISION] · 2026-07-21 · Honesty transition: поэтапный marker → xfail → fail
· Rejected: прямой skip→fail (ломает CI без Docker)
· Current: marker mode. Wave 2 переключит на fail (operator decision)

#### T2 — pytest.skip Architectural Gate

**pytest.skip допустим ТОЛЬКО для инфраструктурной недоступности** (Docker отсутствует, нет env vars, нет сети). Каждый skip логируется `[IMP:8][automatic_skip_gate]` через pytest hook `pytest_runtest_makereport`. Autouse фикстура `automatic_skip_gate` — no-op маркер (реальная логика в hook, не в фикстуре).

⚠️ TRAP[DECISION] · 2026-07-07 · AUTOMATIC_SKIP_GATE — пассивный логгинг (не блокирует, но виден в LDD trajectory)

---

### Infrastructure & Performance

#### T3 — Container Reuse: Foreign Detection

Module-scoped фикстуры **переиспользуют** контейнеры platform_services вместо поднятия своих (~350s экономии). Механизм:

1. `check_foreign_containers()` через `docker inspect` проверяет `com.docker.compose.project` label
2. Если контейнер чужого проекта → reuse mode: skip compose up/down
3. `wait_for_containers_healthy()` — поллинг health status перед yield

⚠️ TRAP[DECISION] · 2026-07-22 · Return type: `dict[str, str]` (container_name → project), не `list[str]`

#### T4 — Test Infrastructure Auto-Discovery

Container names, ports, compose paths **NEVER hardcoded**. Источник: `discover_modules.py --test-infra --json` через `_conftest/infra.py::_TestInfra` (singleton, один subprocess за сессию):

```python
from _conftest.infra import infra
name = infra.get_container_name("postgres")          # → "postgres-test"
port = infra.get_test_port("pgbouncer", "pgbouncer") # → 6432
all_names = infra.stale_container_names               # всегда в sync с compose
```

⚠️ TRAP[PERF] · 2026-07-22 · Subprocess on import. Mitigation: `@lru_cache(maxsize=1)`. Если >500ms → file-based cache.
⚠️ TRAP[DECISION] · 2026-07-22 · `stale_container_names` derived from auto-discovery, не hardcoded list.

#### T5 — Wave-Pipeline (Test Ordering)

Тесты сортируются по wave number из `core/modules/*/module.yaml#depends_on`. Это **overlap** (не параллельность): Wave 0 тесты стартуют через ~20s, пока Wave 1 контейнеры ещё запускаются. Gain: ~100s.

Компоненты: `pytest_collection_modifyitems` (теггинг + сортировка), `_ensure_wave_ready` (autouse фикстура), `signal_wave_ready` (smoke.py platform_services).

⚠️ TRAP[DECISION] · 2026-07-22 · threading.Event, не pytest-ordering плагин
· Rejected: pytest-order/pytest-dependency (хрупкая зависимость между файлами)
· Timeout: 600s на event.wait()

#### T14 — Container Name Isolation

Тесты используют изолированные `-test` суффиксы и отдельные test-сети:

- `PLATFORM_NETWORKS` — production сети (proxy-net, shared-db-net, ...)
- `TEST_NETWORKS` — тестовые эквиваленты (proxy-net-test, ...)
- `EXEMPT_CREATED_NETWORKS` — сети модулей (не pre-created)

⚠️ TRAP[DEBT] · 2026-07-15 · MED · Parallel teardown destroys shared external networks
· Race condition: модульные фикстуры с `docker compose down` удаляют общие external сети
· Mitigation: container reuse (T3) — фикстуры не делают compose down если контейнеры чужие

---

### Infrastructure Internals

#### T16 — Subprocess-on-Import Constraint

`_conftest/infra.py` инстанциирует `_TestInfra()` на уровне модуля (`infra = _TestInfra()`). **Любой `import` модуля, транзитивно импортирующего `_conftest.infra`, запускает `discover_modules.py --test-infra --json` ДО старта pytest.** Даже `pytest --collect-only`.

`@lru_cache` предотвращает повторный subprocess, но не сам вызов. Цепочка заражения: `test_component_*.py` → `from _conftest.infra import infra` → subprocess (~50-200ms overhead на import).

**Контракт:** не добавлять `from _conftest.infra import infra` в модули, загружаемые при import других `_conftest/` helpers.

⚠️ TRAP[PERF] · 2026-07-22 · Subprocess at import time, не at runtime

#### T17 — Duplicated Wave Algorithm

Два НЕЗАВИСИМЫХ вычисления волнового графа (вопреки комментарию «Same algorithm»):

| Аспект | `conftest.py::_compute_module_waves()` | `smoke.py::_build_waves()` |
|--------|---------------------------------------|---------------------------|
| Алгоритм | Multi-pass (while changed) | Single-pass (insertion order) |
| Unknown deps | → wave 0 | → wave -1 |
| Результат | `{module: wave}` | `[[wave0], [wave1], ...]` |

**Контракт:** при изменении алгоритма волн ОБЕ функции должны быть обновлены синхронно. При циклических зависимостях результаты расходятся.

⚠️ TRAP[DRIFT] · 2026-07-22 · Ручная синхронизация. Комментарий «Same algorithm» в коде НЕВЕРЕН.

#### T18 — Anti-Tamper Replication Contract

`tools/sync_inventory.py::collect_tests()` и `gates/test_gate_test_inventory.py::_collect_tests()` — **намеренно идентичные копии** XML-парсера вывода `pytest --collect-only -q`.

**Rationale:** парсер скопирован (не импортирован через shared library), чтобы gate-тест нельзя было обойти, изменив общую библиотеку. Если оба используют один импорт → изменение в shared library делает gate всегда зелёным.

**Контракт:** НЕ импортировать. НЕ рефакторить в shared library. При изменении формата вывода pytest — обновить обе копии.

⚠️ TRAP[DECISION] · 2026-07-22 · Intentional code duplication as security boundary

#### T19 — Autouse Fixture Ordering (Alphabetical)

Function-scoped autouse фикстуры упорядочены pytest'ом **по алфавиту имени** (внутри одного scope):

1. `_ensure_wave_ready` (e) — ждёт готовности волны
2. `_reset_fresh_state` (r) — ресетит сервисы

**Контракт:** новая autouse фикстура с именем на `a` будет ПЕРВОЙ. Если она должна быть после `_ensure_wave_ready` — порядок silently broken. При добавлении autouse фикстур проверять алфавитную позицию.

⚠️ TRAP[DECISION] · 2026-07-22 · Порядок не очевиден из имён. Не менять имена существующих фикстур на более ранние по алфавиту.

#### T20 — Session Fixture Background Thread

`platform_services` (session-scoped) запускает Wave 1+ контейнеры в background thread (`daemon=True`). Teardown: `bg_thread.join(timeout=600)`.

**Контракты:**
- `yield` фикстуры НЕ сигнализирует готовность всех контейнеров — для этого `_ensure_wave_ready` (T5, T19).
- При таймауте join (600s): daemon thread убивается при завершении Python, cleanup (release networks, stop containers) не выполняется → утечка контейнеров/сетей.
- Failed-модули могут быть ложноположительными — `_module_container_running()` для верификации.

⚠️ TRAP[DEBT] · 2026-07-22 · MED · Cleanup не гарантирован при таймауте daemon thread.

#### T21 — infra Singleton Import Protocol

`_conftest/infra.py::infra` — singleton, НЕ re-экспортируется через `_conftest/__init__.py`. Это **единственный** module-level объект в `_conftest/`, нарушающий правило «public names re-exported through `__init__`».

**Контракт:** только `from _conftest.infra import infra`. `from _conftest import infra` — **не сработает**. Причина исключения: предотвратить subprocess при import через `__init__.py` (T16).

⚠️ TRAP[DECISION] · 2026-07-22 · Исключение из правила re-export, связанное с T16.

---

### LDD & Code Quality

#### T6 — LDD Trajectory Enforcement

`@ldd_trajectory` декоратор (`tests/_conftest/ldd.py`) — автоматически проверяет наличие `[IMP:9]` лога после теста. Заменил ~750 строк boilerplate (250 тестов × 3 строки).

`gate_helpers.assert_ldd_imp9(caplog)` — для тестов без декоратора. `_print_ldd_trajectory(caplog)` — печатает IMP:7-10 логи перед ассертами.

⚠️ TRAP[DECISION] · 2026-07-21 · Совместим с @pytest.mark.parametrize и всеми маркерами

#### T7 — Anti-Loop Protocol

Предотвращает бесконечные ретраи агентов. Counter в `.test_counter.json`, сбрасывается только при 100% PASS. FORBIDDEN: вызывать counter management внутри test files.

⚠️ TRAP[DECISION] · 2026-07-21 · Реализация: tests/_conftest/counter.py. Эскалация: см. `.kilo/rules/testing.md#Anti-Loop Protocol`

#### T8 — Unit Tests для Strangler-Fig Python-модулей

После декомпозиции shell-монолитов каждый Python-модуль получил unit-тесты в `tests/unit/`: `deploy/`, `converge/`, `lifecycle/`.

⚠️ TRAP[DEBT] · 2026-07-22 · P2 · 5 test-side failures в test_docker_orchestrator.py (str/bytes type safety в моках subprocess.run)
⚠️ TRAP[DECISION] · 2026-07-22 · Strangler-Fig migration — unit-тесты ПОСЛЕ Python-модуля
· Rejected: TDD (test-first) для shell→Python миграции
· Reason: shell-монолит уже работает, поведение известно. Тесты на известное поведение, не на spec.

---

### Data & Registration

#### T9 — Test Data Auto-Validation

`tests/test_data/node.yaml` **auto-validated** при `pytest_sessionstart` против `core/schemas/node.schema.json`. `config_*.yaml` (3 файла) — planned (schema ещё не зарегистрированы в `_FIXTURE_SCHEMA_MAP`).

⚠️ TRAP[DECISION] · 2026-07-22 · Sessionstart validation — дрейф фикстур ловится немедленно. Не в CI gate (schema-only изменения не должны блокироваться отсутствием test data).

#### T11 — Gate Test Trinity Registration

Gate-тест должен быть зарегистрирован в **трёх местах** (подробнее: `tests/gates/AGENTS.md`):

1. Файл `tests/gates/test_gate_*.py`
2. Декоратор `@pytest.mark.gate`
3. Запись в `core/entrypoint-manifest.yaml` (секция `gates`)

Пропуск любого → gate не запускается в `make gate`. Всего 56 gate-файлов (на 2026-07-22).

⚠️ TRAP[DECISION] · 2026-07-21 · Trinity enforcement через test_gate_manifest_integrity.py

---

### Error Handling

#### T15 — E2E Error Handling Contract

`_handle_e2e_error()` (`tests/_conftest/ldd.py`) — канонический обработчик HTTP-ошибок в E2E тестах:

| Exception | Действие |
|-----------|---------|
| `SSLError` | `pytest.fail` — сертификаты не настроены |
| `ConnectionError` | CI → `pytest.fail`; local → `pytest.skip` (auto-detect `E2E_OFFLINE`) |
| `ProxyError` | `pytest.fail` — проверить HTTPS_PROXY/HTTP_PROXY |
| `Timeout` | `pytest.skip` — transient network issue |

⚠️ TRAP[DEBT] · 2026-07-08 · LO · Не все E2E тесты делегируют в `_handle_e2e_error`. CHECKLIST item в `_conftest/checklist.py`.

---

## Cross-References

| Файл | Назначение |
|------|-----------|
| [`tests/gates/AGENTS.md`](gates/AGENTS.md) | Gate taxonomy, registration protocol |
| [`../AGENTS.md`](../AGENTS.md) (root) | Архитектурные инварианты платформы |
| [`../core/AGENTS.md`](../core/AGENTS.md) | Канонические операции, слои |
| [`../core/modules/AGENTS.md`](../core/modules/AGENTS.md) | Шаблон модуля |
| [`../core/internal/bootstrap/AGENTS.md`](../core/internal/bootstrap/AGENTS.md) | Bootstrap pipeline, Python-модули |
| [`../core/entrypoint-manifest.yaml`](../core/entrypoint-manifest.yaml) | YAML-реестр gates |
| [`.kilo/rules/testing.md`](../.kilo/rules/testing.md) | **Канонический источник:** R1-R5, LDD telemetry, Anti-Loop protocol |
| [`.kilo/rules/markup.md`](../.kilo/rules/markup.md) | Semantic markup standard |
]]>