$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Сократить время прогона Docker-тестов (100 тестов) с 500s до ≤200s без изменения тестовых функций. Устранить container name conflicts (10 ERRORS при едином прогоне). Обеспечить волновую fault isolation: падение контейнера одной волны не блокирует тесты предыдущих волн.
DESCRIPTION:           Четырёхфазная оптимизация: (1) Container Name Conflict Resolution — foreign guard во все module-фикстуры; (2) Fixture Convergence — module→session scope, reuse platform_services; (3) Stop/Start checkpoint вместо compose down/up; (4) Wave-Pipeline — тесты запускаются по мере готовности волны контейнеров, не дожидаясь полного стека. Зависимости между тестами извлекаются из core/modules/*/module.yaml (depends_on) динамически, без хардкода.
RATIONALE:             70% времени прогона тратится на compose up/down циклы (~350s из 500s), а не на выполнение тестов (~150s). Причина — двойной compose-цикл: platform_services (session) поднимает все модули, затем module-фикстуры заново поднимают те же сервисы в изолированных проектах. Паттерн reuse уже реализован в test_smoke_postgres.py — тиражируется на все module-фикстуры. Wave-Pipeline (Фаза 4) дополнительно перекрывает старт контейнеров следующих волн с выполнением тестов текущей волны.
ACCEPTANCE_CRITERIA:   1. Единый прогон `pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)"` завершается без ERRORS (0 container name conflicts). 2. Время прогона ≤200s (↓60% от baseline 500s). 3. Все 100 тестов проходят (97 pass + 2 skip + 1 известный macOS skip). 4. Ни одна тестовая функция не изменена (только фикстуры и conftest). 5. `make gate MODE=full` проходит зелёным. 6. Fault isolation: падение контейнера Wave 1 (litellm/langfuse) не блокирует тесты Wave 0 (redis, nginx, postgres, clickhouse, logging) — эти тесты должны PASS.
IMPLEMENTS:            Рекомендация суперпозиции (Option A: Wave-Pipeline как надстройка над DevPlan 040). Инсайт пользователя: зависимости брать из core/modules/*/module.yaml динамически.
IMPACTS:               tests/_conftest/smoke.py (platform_services), tests/test_component_*.py (все module-фикстуры), tests/test_smoke_*.py (все module-фикстуры), tests/_conftest/infra.py (test_infra), tests/conftest.py (pytest_collection_modifyitems), core/modules/*/module.yaml (источник данных, read-only)
REQUIRES:              Docker daemon running, Python ≥3.10, core/modules/ directory intact
$END_ARTIFACT_CONTRACT

---

## 1. Проблема и Baseline

### 1.1 Текущие показатели (измерено 2026-07-22, macOS, git SHA b301609)

| Метрика | Значение |
|---------|----------|
| Всего Docker-тестов | 100 (smoke: 40, component: 16, predeploy: 33, requires_docker: 45, с пересечениями) |
| Время единого прогона | **~500s (8.3 мин)** |
| Результат единого прогона | 3 failed, 44 passed, 1 skipped, **10 errors**, 26 deselected |
| Результат батчированного прогона (5 batch) | **97 passed, 2 skipped, 1 failed** (nginx_error_page — macOS bind-mount) |

### 1.2 Распределение времени

| Фаза | Время | % |
|------|-------|---|
| `platform_services` setup (compose up всех 13 модулей wave-parallel) | 170s | 34% |
| Module-scoped fixtures setup (postgres, redis, nginx, hermes, clickhouse, pgbouncer — последовательно) | ~120s | 24% |
| Выполнение тестов (call) | ~150s | 30% |
| Teardown (compose down × 8+ проектов) | ~60s | 12% |

**70% времени — setup/teardown compose-циклов, не выполнение тестов.**

### 1.3 10 ERRORS — корневая причина

При едином прогоне session-scoped `platform_services` поднимает все 13 модулей, затем teardown. После этого module-scoped фикстуры пытаются поднять те же контейнеры с теми же именами, но stale-контейнеры от `platform_services` уже заняты:

```
ERROR hermes-agent-test: Conflict. Container "/hermes-agent-test" already in use
ERROR clickhouse-test: Conflict. Container "/clickhouse-test" already in use
```

**2 gap в stale-cleanup:** `clickhouse-test` и `redis-test` отсутствуют в `_STALE_CONTAINER_NAMES` platform_services.

---

## 2. Архитектура текущих фикстур

### 2.1 Два слоя compose-фикстур

```
┌─ SESSION LAYER ──────────────────────────────────────────────┐
│  platform_services (session)                                  │
│  └─ _build_waves(module_graph) → 3 волны                     │
│     Wave 0: postgres, redis, clickhouse, minio, nginx,        │
│             logging, platform-secrets (systemd, skip)          │
│     Wave 1: litellm, backup-cron, monitoring, infra-metrics,   │
│             status-page, langfuse                              │
│     Wave 2: hermes-agent                                      │
│  └─ project: ai-platform-test                                 │
│  └─ container_name: <service>-test                            │
│  └─ lifecycle: up (wave-parallel) → yield → down (sequential)  │
├───────────────────────────────────────────────────────────────┤
│  test_infra (session, autouse)                                │
│  └─ ensure_external_networks + _ensure_volume_dirs            │
└───────────────────────────────────────────────────────────────┘
                               ↓ teardown
┌─ MODULE LAYER (7 изолированных compose-проектов) ────────────┐
│  nginx_compose / postgres_up / hermes_up / clickhouse_up /    │
│  pgbouncer_up / postgres_up(smoke) / redis_compose            │
│  Каждый: compose up → yield → compose down -v                 │
│  Запускаются ПОСЛЕДОВАТЕЛЬНО (module scope)                   │
│  Суммарно: ~120s compose up + ~60s compose down               │
└───────────────────────────────────────────────────────────────┘
```

### 2.2 Единственный существующий reuse-паттерн

`test_smoke_postgres.py:postgres_up` реализует **foreign container guard**: если postgres-test/pgbouncer-test уже запущены от `platform_services` → reuse, пропуская compose up/down. Этот паттерн — готовая модель для тиражирования.

### 2.3 Граф зависимостей модулей (из core/modules/*/module.yaml)

```
Wave 0 (0 зависимостей):      postgres, redis, clickhouse, minio, nginx, logging
Wave 1 (depends on Wave 0):   litellm(postgres), backup-cron(postgres),
                               monitoring(nginx), infra-metrics(nginx), status-page(nginx),
                               langfuse(postgres, clickhouse)
Wave 2 (depends on Wave 0+1): hermes-agent(nginx, postgres, redis, litellm)
```

**Инсайт:** Зависимости между тестами = зависимости между модулями. Если тесту нужен hermes-agent, ему транзитивно нужны postgres, redis, litellm (Wave 2). Если тесту нужен только redis — достаточно Wave 0.

---

## 3. Оптимизационный дизайн (4 фазы)

### 3.1 Фаза 1: Container Name Conflict Resolution (prerequisite)

**Цель:** Устранить 10 ERRORS, сделать возможным единый прогон без батчирования.

**Изменения:**
1. `tests/_conftest/smoke.py` — добавить `clickhouse-test` и `redis-test` в `_STALE_CONTAINER_NAMES`
2. Все module-фикстуры — внедрить foreign container guard (по модели `test_smoke_postgres.py`): перед compose up проверить `docker ps --filter name=<container>`, если уже running → `_REUSE_CONTAINERS = True`, ожидать healthy, пропустить compose down в teardown
3. Создать `tests/_conftest/reuse.py` — унифицированный модуль `check_foreign_containers()` + `reuse_or_start()`, извлечённый из `test_smoke_postgres.py`

**Ожидаемый результат:** 0 ERRORS в едином прогоне. Время не меняется.

### 3.2 Фаза 2: Fixture Convergence (основная оптимизация)

**Цель:** Устранить дублирующие compose up/down циклы. Module-фикстуры переиспользуют контейнеры из `platform_services`.

**Phase 2a — Universal reuse helper:** `tests/_conftest/reuse.py` (создан в Фазе 1). Все 7 module-фикстур заменяют inline foreign guard на вызов `reuse_or_start()`.

**Phase 2b — Scope migration:** Все module-фикстуры: `scope="module"` → `scope="session"`, добавляют `platform_services` в параметры. Pytest гарантирует порядок: `platform_services` setup → module-фикстуры setup (обнаруживают running контейнеры → reuse) → тесты → module-фикстуры teardown (пропускают down, reuse=True) → `platform_services` teardown.

| Фикстура | Текущий scope | Новый scope | Что меняется |
|----------|--------------|-------------|-------------|
| `nginx_compose` | module | session (depends on platform_services) | reuse nginx-test |
| `postgres_up` (hermes) | module | session (depends on platform_services) | reuse postgres-test/pgbouncer-test |
| `hermes_up` | module | session (depends on platform_services) | reuse hermes-agent-test |
| `clickhouse_up` | module | session (depends on platform_services) | reuse clickhouse-test |
| `pgbouncer_up` | module | session (depends on platform_services) | reuse pgbouncer-test |
| `postgres_up` (smoke) | module | session (depends on platform_services) | уже делает reuse — подтвердить |
| `redis_compose` | module | session (depends on platform_services) | reuse redis-test |

**Phase 2c — State reset для тестов, требующих чистого состояния:** `@pytest.mark.requires_fresh_state("postgres")` — autouse fixture на function-scope делает `docker compose restart <service>` (секунды вместо минут).

**Phase 2d — Оптимизация test_platform_starts_all_containers:**
Тест уже использует `platform_services` fixture (контейнеры уже running). 122s — время поллинга (24 retries × 5s), ожидающего что контейнеры станут running. После Phase 2b `platform_services` дожидается healthy ВСЕХ контейнеров до `yield` → поллинг не нужен. Оптимизация: `max_retries: 24→1` (единственная проверка `docker ps`), 122s → ~5s.

**Ожидаемый результат по времени:** 500s → ~280s (-44%)

### 3.3 Фаза 3: Stop/Start Checkpoint

**Цель:** Ускорить teardown — `compose down` (60s) → `compose stop` (~10s).

**Механика:**
- `platform_services` teardown: `compose down --remove-orphans` → `compose stop`
- Финальный cleanup в `pytest_sessionfinish`: `compose down` для всех проектов (однократно, после всех тестов)
- Тесты с `@requires_fresh_state` → `compose restart <service>` перед тестом

**Ожидаемый результат по времени:** 280s → ~230s (-18% от Фазы 2)

### 3.4 Фаза 4: Wave-Pipeline — тесты запускаются по готовности волны

**Цель:** Запускать тесты как только их контейнерные зависимости удовлетворены, не дожидаясь полного стека. Обеспечить fault isolation.

#### Принцип работы

Вместо «запусти все контейнеры → запусти все тесты»:

```
Wave 0 containers↑(20s) → [Wave 0 tests | Wave 1 containers↑ in background]
  t=20: Wave 0 ready, Wave 1 start
  t=50: Wave 0 tests done (30s), Wave 1 at 30/70s → wait 40s
  t=90: Wave 1 ready → [Wave 1 tests | Wave 2 containers↑ in background]
  t=110: Wave 1 tests done (20s), Wave 2 at 20/60s → wait 40s
  t=150: Wave 2 ready → Wave 2 tests (10s)
  t=160: Wave 2 done → Wave 3 tests (platform endpoints + smoke, 40s)
  t=200: All tests done → teardown (10s)
```

**Pipeline gain:** ~100s экономии за счёт перекрытия старта контейнеров с выполнением тестов.

#### Fault isolation

| Сценарий | Без Wave-Pipeline | С Wave-Pipeline |
|----------|-------------------|-----------------|
| postgres (Wave 0) не стартует | ❌ Все 100 тестов — error | ❌ Wave 0+1+2, ✅ ~5 static тестов |
| litellm (Wave 1) не стартует | ❌ Все 100 тестов — error | ✅ ~35 Wave 0 тестов, ❌ ~65 Wave 1+2+3 |
| hermes-agent (Wave 2) не стартует | ❌ Все 100 тестов — error | ✅ ~65 Wave 0+1 тестов, ❌ ~35 Wave 2+3 |

#### Распределение тестов по волнам

Волна теста = максимальный номер волны среди всех контейнеров, от которых он зависит (транзитивно через `module.yaml#depends_on`):

| Wave | Контейнеры | Тестовые файлы | Тестов | Время тестов |
|------|-----------|----------------|--------|-------------|
| 0 | redis, nginx, clickhouse, postgres, minio, logging, infra-metrics | test_smoke_redis(5), test_smoke_nginx(5), test_component_clickhouse(3), test_smoke_postgres(2), test_smoke_logging(2), test_smoke_infra_metrics(3), test_smoke_provision(2), test_unit_provision(2) | 24 | ~30s |
| 1 | + pgbouncer, litellm, langfuse, monitoring, grafana, prometheus, backup-cron, status-page | test_component_pgbouncer(6), test_smoke_litellm(2), test_smoke_langfuse(3), test_smoke_monitoring(3) | 14 | ~20s |
| 2 | + hermes-agent | test_component_hermes(7), test_smoke_hermes(3) | 10 | ~10s |
| 3 | ALL (проверка полного стека) | test_smoke_platform(5), test_platform_endpoints(4) | 9 | ~40s |

#### Реализация — три компонента

**1. `tests/_conftest/wave_pipeline.py` (новый модуль):**

```python
import threading
import pytest

# Wave readiness events — устанавливаются platform_services при готовности волны
_wave_ready: dict[int, threading.Event] = {}

def _init_wave_events(num_waves: int) -> None:
    for w in range(num_waves + 1):
        _wave_ready[w] = threading.Event()

def signal_wave_ready(wave: int) -> None:
    _wave_ready[wave].set()

@pytest.fixture(scope="function", autouse=True)
def _ensure_wave_ready(request) -> None:
    """Block test execution until its wave's containers are ready.
    Function-scoped → executes just before the test, NOT during session setup."""
    marker = request.node.get_closest_marker("wave")
    if marker is None:
        return
    wave = marker.args[0]
    if wave == 0:
        return
    event = _wave_ready.get(wave)
    if event is not None:
        event.wait(timeout=600)
```

**2. `tests/conftest.py::pytest_collection_modifyitems` — динамическое тегирование волнами:**

Волна теста вычисляется из `module.yaml#depends_on`, а не хардкодится. Алгоритм:

```python
def _compute_module_waves() -> dict[str, int]:
    """Read core/modules/*/module.yaml, compute wave numbers from depends_on.
    Same algorithm as _build_waves() in smoke.py."""
    import yaml, os

    platform_root = os.path.dirname(__file__)
    modules_dir = os.path.join(platform_root, "..", "core", "modules")

    mod_deps: dict[str, list[str]] = {}
    for entry in sorted(os.listdir(modules_dir)):
        mod_path = os.path.join(modules_dir, entry)
        yaml_path = os.path.join(mod_path, "module.yaml")
        if os.path.isdir(mod_path) and os.path.isfile(yaml_path):
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            mod_deps[entry] = data.get("depends_on") or []

    assigned: dict[str, int] = {}
    for mod, deps in mod_deps.items():
        if not deps:
            assigned[mod] = 0
        else:
            assigned[mod] = max((assigned.get(d, -1) for d in deps), default=-1) + 1

    return assigned  # {module_name: wave_number}


def pytest_collection_modifyitems(items):
    """Tag tests with wave number based on fixture→module→depends_on chain."""
    module_waves = _compute_module_waves()
    max_wave = max(module_waves.values()) if module_waves else 0

    # Stable mapping: fixture name → module name.
    # This is mechanical (fixture exists in test code), not dependency-driven.
    FIXTURE_TO_MODULE = {
        "redis_compose": "redis",
        "nginx_compose": "nginx",
        "clickhouse_up": "clickhouse",
        "postgres_up": "postgres",
        "pgbouncer_up": "postgres",
        "logging_compose": "logging",
        "infra_metrics_compose": "infra-metrics",
        "minio_compose": "minio",
        "litellm_up": "litellm",
        "langfuse_up": "langfuse",
        "monitoring_compose": "monitoring",
        "backup_cron_compose": "backup-cron",
        "status_page_compose": "status-page",
        "hermes_up": "hermes-agent",
        "platform_services": None,  # special: always max_wave + 1
    }

    for item in items:
        test_wave = 0
        if hasattr(item, "fixturenames"):
            for fname in item.fixturenames:
                if fname == "platform_services":
                    test_wave = max(test_wave, max_wave + 1)
                elif fname in FIXTURE_TO_MODULE and FIXTURE_TO_MODULE[fname] is not None:
                    mod = FIXTURE_TO_MODULE[fname]
                    test_wave = max(test_wave, module_waves.get(mod, 0))

        if test_wave > 0:
            item.add_marker(pytest.mark.wave(test_wave))

    items.sort(key=lambda item: (
        item.get_closest_marker("wave").args[0] if item.get_closest_marker("wave") else 0,
        item.nodeid,
    ))
```

**Ключевое свойство:** номера волн вычисляются динамически из `module.yaml#depends_on`. При добавлении/удалении модуля или изменении зависимостей волны пересчитываются автоматически — никакого хардкода. `FIXTURE_TO_MODULE` — механический mapping (fixture-имя → module-имя), не меняется с изменением зависимостей.

**3. Модификация `platform_services` в `tests/_conftest/smoke.py`:**

```python
@pytest.fixture(scope="session")
def platform_services(request):
    # ... existing setup ...

    num_waves = len(_build_waves(module_graph))
    _init_wave_events(num_waves)

    # Wave 0 — синхронно (критический путь, блокирует fixture setup)
    _start_wave_sync(0)
    signal_wave_ready(0)

    # Wave 1+2 — фоновый поток
    def _start_remaining():
        for wave in range(1, num_waves):
            _start_wave_sync(wave)
            signal_wave_ready(wave)

    bg_thread = threading.Thread(target=_start_remaining, daemon=True)
    bg_thread.start()

    yield  # pytest начинает тесты

    bg_thread.join(timeout=600)
    # Teardown: compose stop (из Фазы 3)
```

**Ожидаемый результат по времени:** 230s → ~200s (-13% от Фазы 3, -60% от baseline)

---

## 4. Step-by-Step Имплементация

### Wave 0: Preparation ✅

- [x] Собраны все имена контейнеров (production + test)
- [x] Построена матрица конфликтов
- [x] Найден reuse-паттерн в `test_smoke_postgres.py`
- [x] Определены 2 gap в `_STALE_CONTAINER_NAMES` (clickhouse-test, redis-test)
- [x] Подтверждён граф зависимостей из `core/modules/*/module.yaml`

### Wave 1: Container Name Conflict Resolution (Фаза 1)

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 1 | `tests/_conftest/smoke.py` | Modify | Добавить `clickhouse-test`, `redis-test` в `_STALE_CONTAINER_NAMES` |
| 2 | `tests/_conftest/reuse.py` | **Create** | `check_foreign_containers()`, `reuse_or_start()` — унифицированная логика из `test_smoke_postgres.py` |
| 3 | `tests/test_smoke_postgres.py` | Modify | Заменить inline foreign guard на вызов `reuse_or_start()` |
| 4 | `tests/test_component_hermes.py` | Modify | Внедрить foreign guard в `postgres_up` и `hermes_up` |
| 5 | `tests/test_component_pgbouncer.py` | Modify | Внедрить foreign guard в `pgbouncer_up` |
| 6 | `tests/test_component_clickhouse.py` | Modify | Внедрить foreign guard в `clickhouse_up` |
| 7 | `tests/test_smoke_nginx.py` | Modify | Внедрить foreign guard в `nginx_compose` |
| 8 | `tests/test_smoke_redis.py` | Modify | Внедрить foreign guard в `redis_compose` |

**Верификация Wave 1:**
```bash
pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" -v --tb=line
# Ожидается: 0 errors, только известные nginx_error_page (macOS) и langfuse_ingestion (HTTP 500)
```

### Wave 2: Fixture Scope Migration (Фаза 2)

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 9 | `tests/_conftest/smoke.py` | Modify | `platform_services` — убедиться что teardown после ВСЕХ session-тестов |
| 10 | `tests/test_component_hermes.py` | Modify | `postgres_up`, `hermes_up`: module→session scope, depends on `platform_services` |
| 11 | `tests/test_component_pgbouncer.py` | Modify | `pgbouncer_up`: module→session scope, depends on `platform_services` |
| 12 | `tests/test_component_clickhouse.py` | Modify | `clickhouse_up`: module→session scope, depends on `platform_services` |
| 13 | `tests/test_smoke_nginx.py` | Modify | `nginx_compose`: module→session scope, depends on `platform_services` |
| 14 | `tests/test_smoke_redis.py` | Modify | `redis_compose`: module→session scope, depends on `platform_services` |
| 15 | `tests/test_smoke_postgres.py` | Modify | `postgres_up`: module→session scope, depends on `platform_services` |
| 16 | `tests/_conftest/__init__.py` | Modify | Реэкспорт новых session-фикстур для обратной совместимости |
| 17 | `tests/test_smoke_platform.py` | Modify | `test_platform_starts_all_containers`: `max_retries: 24→1` — контейнеры уже healthy от `platform_services`, поллинг не нужен |

**Ключевой момент:** все 7 фикстур становятся `scope="session"` и добавляют `platform_services` в параметры. Pytest выполняет setup в порядке зависимости: `platform_services` (поднимает все контейнеры) → module-фикстуры (обнаруживают running контейнеры через foreign guard → reuse). Teardown в обратном порядке.

**Верификация Wave 2:**
```bash
pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --durations=10
# Ожидается: setup times module fixtures < 5s каждая (вместо 15-25s)
```

### Wave 3: Fresh State Marker + Stop/Start (Фаза 3)

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 18 | `tests/_conftest/state_reset.py` | **Create** | `_reset_fresh_state` autouse fixture, `restart_service(name)` |
| 19 | `pyproject.toml` | Modify | Зарегистрировать marker `requires_fresh_state` |
| 20 | `tests/_conftest/smoke.py` | Modify | `platform_services` teardown: `compose down` → `compose stop`; финальный `compose down` в `pytest_sessionfinish` |

**Верификация Wave 3:**
```bash
pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --durations=10
# Ожидается: teardown < 10s (вместо ~60s), общее время ≤230s
```

### Wave 4: Wave-Pipeline (Фаза 4)

| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 21 | `tests/_conftest/wave_pipeline.py` | **Create** | `_wave_ready` events, `_init_wave_events()`, `signal_wave_ready()`, `_ensure_wave_ready` autouse fixture |
| 22 | `tests/conftest.py` | Modify | Добавить `_compute_module_waves()` + `pytest_collection_modifyitems` — динамическое тегирование тестов маркером `wave(N)` из `module.yaml#depends_on` + сортировка |
| 23 | `tests/_conftest/smoke.py` | Modify | `platform_services`: Wave 0 синхронно → Wave 1+2 в background thread → `signal_wave_ready` после каждой волны |
| 24 | `pyproject.toml` | Modify | Зарегистрировать marker `wave` |
| 25 | `tests/_conftest/__init__.py` | Modify | Реэкспорт `_ensure_wave_ready` для autouse-обнаружения |

**Верификация Wave 4:**
```bash
# Тесты упорядочены: Wave 0 → Wave 1 → Wave 2 → Wave 3
pytest tests/ --collect-only -q -m "(smoke or component or predeploy or requires_docker) and (not e2e)" 2>&1 | head -30

# Fault isolation: убить litellm после Wave 0 → Wave 0 тесты должны пройти
# (ручной тест: docker stop litellm-test во время прогона)

# Общее время ≤200s
pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --durations=10
```

---

## 5. Прогноз времени (итоговый)

| Этап | Время | Накоплено |
|------|-------|-----------|
| Wave 0 containers up (синхронно) | 20s | 20s |
| Wave 0 tests (Wave 1 стартует в фоне) | 30s | 50s |
| Wait Wave 1 ready (остаток от 70s litellm) | 40s | 90s |
| Wave 1 tests (Wave 2 стартует в фоне) | 20s | 110s |
| Wait Wave 2 ready (остаток от 60s hermes) | 40s | 150s |
| Wave 2 tests | 10s | 160s |
| Wave 3 tests (без ожидания — всё уже ready) | 30s | 190s |
| Teardown (compose stop + sessionfinish cleanup) | 10s | 200s |
| **Итого** | | **~200s** |

**Экономия по фазам:**
| Фаза | Что сокращено | Экономия |
|------|-------------|----------|
| Фаза 2 | Module-фикстуры setup: reuse без compose up | 120s → 5s (-115s) |
| Фаза 2d | test_platform_starts_all_containers: no polling | 122s → 5s (-117s) |
| Фаза 2 | Module-фикстуры teardown: skip compose down | 60s → 2s (-58s) |
| Фаза 3 | platform_services teardown: compose stop | 60s → 10s (-50s) |
| Фаза 4 | Pipeline overlap: тесты + старт контейнеров параллельно | ~100s |
| **Итого экономия** | | **~340s (500s → ~200s, -60%)** |

---

## 6. Acceptance Criteria

| # | Критерий | Проверка | Target |
|---|----------|----------|--------|
| AC1 | 0 ERRORS в едином прогоне | `pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --tb=line` | 0 errors |
| AC2 | Все 100 тестов проходят | То же, `-v` | 97 pass + 2 skip + 1 skip (macOS) |
| AC3 | Время прогона ≤200s | `--durations=0` | ≤200s |
| AC4 | Тестовые функции не изменены | `git diff --stat -- 'tests/test_*.py'` | Только fixture-строки |
| AC5 | `make gate MODE=full` зелёный | `make gate MODE=full` | exit 0 |
| AC6 | Foreign guard на всех 7 фикстурах | `docker ps` до и после прогона | Контейнеры переиспользованы, не пересозданы |
| AC7 | Тесты упорядочены по волнам | `pytest --collect-only -q` | Wave 0 первыми, затем 1, 2, 3 |
| AC8 | Fault isolation: Wave 0 тесты проходят при падении Wave 1 | Ручной тест: `docker stop litellm-test` во время прогона | Wave 0 PASS, Wave 1+2+3 FAIL с диагностикой |
| AC9 | Номера волн вычисляются из module.yaml | Добавление тестового module.yaml → волны пересчитываются без правок кода | Zero hardcode |

---

## 7. Risk Register

| Риск | Вероятность | Impact | Митигация |
|------|------------|--------|-----------|
| Тесты ожидают пустую БД, но БД переиспользуется | MEDIUM | HIGH | `@requires_fresh_state` маркер + autouse restart |
| `compose stop` не очищает анонимные волюмы | LOW | MEDIUM | `compose down` в `pytest_sessionfinish` |
| Pytest ordering: session-фикстуры из разных файлов | LOW | HIGH | Все явно зависят от `platform_services` через параметры |
| `docker compose restart` медленнее ожидаемого | LOW | LOW | Измерить в Wave 3; если >10s — вернуть `compose stop/start` |
| Background thread не завершается до teardown | MEDIUM | HIGH | `bg_thread.join(timeout=600)` + `daemon=True` |
| `_ensure_wave_ready` блокирует тест навсегда | LOW | HIGH | `event.wait(timeout=600)` — fail с диагностикой |
| `_compute_module_waves()` расходится с `_build_waves()` | LOW | MEDIUM | Оба используют идентичный алгоритм; добавить assertion в тесте |
| CI таймаут (сейчас 40 мин = 2400s, тесты ≤200s) | LOW | LOW | Margin достаточный, не требует изменений |

---

## 8. Rollback Plan

Каждая wave независима — при проблеме откатывается только последняя:

```
Wave 1 (Conflict Resolution)  → git revert <wave1-commit>
Wave 2 (Scope Migration)      → git revert <wave2-commit>
Wave 3 (Stop/Start)           → git revert <wave3-commit>
Wave 4 (Wave-Pipeline)        → git revert <wave4-commit>
```

**Специфика Wave 4:** `_ensure_wave_ready` — strictly additive. Удаление `wave_pipeline.py` + `pytest_collection_modifyitems` возвращает к post-Wave-3 поведению: все тесты после полного старта стека, но с ускоренными fixture-циклами.

$END_DEVPLAN
