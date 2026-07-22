$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Сократить время прогона Docker-тестов (100 тестов) с 500s до ≤200s без изменения тестовых функций. Устранить container name conflicts (10 ERRORS при едином прогоне). Обеспечить волновую fault isolation: падение контейнера одной волны не блокирует тесты предыдущих волн.
DESCRIPTION:           Пятифазная оптимизация: (1) Container Name Conflict Resolution — foreign guard во все module-фикстуры; (2) Fixture Convergence — module→session scope, reuse platform_services; (3) Stop/Start checkpoint вместо compose down/up; (4) Pre-pull образов; (5) Wave-Pipeline — тесты запускаются по мере готовности волны контейнеров, не дожидаясь полного стека. Зависимости между тестами извлекаются из core/modules/*/module.yaml (depends_on), а не хардкодятся.
RATIONALE:             70% времени прогона тратится на compose up/down циклы (~350s из 500s), а не на выполнение тестов (~150s). Причина — двойной compose-цикл: platform_services (session) поднимает все модули, затем module-фикстуры заново поднимают те же сервисы в изолированных проектах. Паттерн reuse уже реализован в test_smoke_postgres.py — тиражируется на все module-фикстуры. Wave-Pipeline (Фаза 5) дополнительно перекрывает старт контейнеров следующих волн с выполнением тестов текущей волны.
ACCEPTANCE_CRITERIA:   1. Единый прогон `pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)"` завершается без ERRORS (0 container name conflicts). 2. Время прогона ≤200s (↓60% от baseline 500s). 3. Все 100 тестов проходят (97 pass + 2 skip + 1 известный macOS skip). 4. Ни одна тестовая функция не изменена (только фикстуры и conftest). 5. `make gate MODE=full` проходит зелёным. 6. Fault isolation: падение контейнера Wave 1 (litellm/langfuse) не блокирует тесты Wave 0 (redis, nginx, postgres, clickhouse, logging) — эти тесты должны PASS.
IMPLEMENTS:            Рекомендация суперпозиции (Option A: Wave-Pipeline как надстройка над DevPlan 040). Инсайт пользователя: зависимости брать из core/modules/*/module.yaml; сортировать контейнеры по скорости старта для максимизации параллелизма.
IMPACTS:               tests/_conftest/smoke.py (platform_services), tests/test_component_*.py (все module-фикстуры), tests/test_smoke_*.py (все module-фикстуры), tests/_conftest/infra.py (test_infra), core/modules/*/module.yaml (источник данных, read-only), Makefile (новый target pre-pull)
REQUIRES:              Docker daemon running, Python ≥3.10, core/modules/ directory intact
$END_ARTIFACT_CONTRACT

---

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Проблема и baseline → PROBLEM
- GOAL Архитектура текущих фикстур → CURRENT
- GOAL Оптимизационный дизайн (3 фазы) → DESIGN
- GOAL Step-by-step имплементация → IMPLEMENTATION
- GOAL Acceptance criteria и verification → VERIFY
$END_DOCUMENT_PLAN

---

## 1. Проблема и Baseline

### 1.1 Текущие показатели (измерено 2026-07-22, macOS, git SHA b301609)

| Метрика | Значение |
|---------|----------|
| Всего Docker-тестов | 100 (smoke: 40, component: 16, predeploy: 33, requires_docker: 45, с пересечениями) |
| Время единого прогона | **~500s (8.3 мин)** |
| Результат единого прогона | 3 failed, 44 passed, 1 skipped, **10 errors**, 26 deselected |
| Результат батчированного прогона (5 batch) | **97 passed, 2 skipped, 1 failed** (nginx_error_page — macOS bind-mount) |
| Время батчированного прогона | 729s (12 мин) — накладные расходы 5× pytest session |

### 1.2 Распределение времени (полный прогон)

| Фаза | Время | % |
|------|-------|---|
| `platform_services` setup (compose up всех 13 модулей wave-parallel) | 170s | 34% |
| Module-scoped fixtures setup (postgres, redis, nginx, hermes, clickhouse, pgbouncer — последовательно) | ~120s | 24% |
| Выполнение тестов (call) | ~150s | 30% |
| Teardown (compose down × 8+ проектов) | ~60s | 12% |
| **Итого** | **~500s** | |

**70% времени — setup/teardown compose-циклов, не выполнение тестов.**

### 1.3 10 ERRORS — корневая причина

При едином прогоне `pytest` запускает session-scoped `platform_services` (поднимает все 13 модулей с `COMPOSE_PROJECT_NAME=ai-platform-test` и именами контейнеров `*-test`), затем teardown. После этого module-scoped фикстуры пытаются поднять **те же контейнеры** с теми же именами, но контейнеры от `platform_services` уже заняты (teardown не всегда успевает очистить stale-контейнеры при сбоях):

```
ERROR hermes-agent-test: Conflict. Container "/hermes-agent-test" already in use
ERROR clickhouse-test: Conflict. Container "/clickhouse-test" already in use
ERROR hermes_agent_starts, healthcheck_passes, no_restart_loop, dashboard_auth, ...
```

**3 конфликтные группы:**
1. `nginx-test` — nginx_compose (wave-nginx-smoke) vs platform_services (ai-platform-test)
2. `postgres-test` / `pgbouncer-test` — 3 фикстуры (hermes, pgbouncer, smoke-postgres) + platform_services
3. `hermes-agent-test` — hermes_up (ai-platform-test-hermes) vs platform_services (ai-platform-test)

**2 gap в stale-cleanup:**
- `clickhouse-test` — отсутствует в `_STALE_CONTAINER_NAMES` platform_services
- `redis-test` — отсутствует в `_STALE_CONTAINER_NAMES` platform_services

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
│  └─ ensure_external_networks: создаёт test-* Docker networks  │
│  └─ _ensure_volume_dirs: создаёт volume-директории            │
└───────────────────────────────────────────────────────────────┘
                              ↓ teardown
┌─ MODULE LAYER (7 изолированных compose-проектов) ────────────┐
│  nginx_compose     → wave-nginx-smoke      (1 сервис)         │
│  postgres_up       → ai-platform-test      (2 сервиса)        │
│  hermes_up         → ai-platform-test-hermes (1 сервис)       │
│  clickhouse_up     → ai-platform-test-ch   (1 сервис)         │
│  pgbouncer_up      → ai-platform-test-pgbouncer (2 сервиса)   │
│  postgres_up(smoke)→ ai-platform-smoke-postgres (2 сервиса)   │
│  redis_compose     → wave-redis-smoke      (1 сервис)         │
│                                                               │
│  Каждый: compose up → yield → compose down -v                 │
│  Запускаются ПОСЛЕДОВАТЕЛЬНО (module scope)                   │
│  Суммарно: ~120s compose up + ~60s compose down               │
└───────────────────────────────────────────────────────────────┘
```

### 2.2 Единственный существующий reuse-паттерн

`test_smoke_postgres.py:postgres_up` реализует **foreign container guard**:

```python
# Если postgres-test/pgbouncer-test уже запущены (от platform_services или другой фикстуры)
# → _REUSE_CONTAINERS = True
# → пропускаем compose up, только ждём healthy
# → пропускаем compose down в teardown
```

Этот паттерн — готовая модель для тиражирования на все module-фикстуры.

### 2.3 Граф зависимостей модулей (из core/modules/*/module.yaml)

```
Wave 0 (0 зависимостей):      postgres, redis, clickhouse, minio, nginx, logging
Wave 1 (depends on Wave 0):   litellm(postgres), backup-cron(postgres),
                               monitoring(nginx), infra-metrics(nginx), status-page(nginx),
                               langfuse(postgres, clickhouse)
Wave 2 (depends on Wave 0+1): hermes-agent(nginx, postgres, redis, litellm)
```

**Инсайт:** Зависимости между тестами = зависимости между модулями. Если тесту нужен hermes-agent, ему нужны postgres, redis, litellm (транзитивно через Wave 2). Если тесту нужен только redis — достаточно Wave 0.

---

## 3. Оптимизационный дизайн (3 фазы)

### 3.1 Фаза 1: Container Name Conflict Resolution (prerequisite)

**Цель:** Устранить 10 ERRORS, сделать возможным единый прогон без батчирования.

**Изменения:**
1. `tests/_conftest/smoke.py` — добавить `clickhouse-test` и `redis-test` в `_STALE_CONTAINER_NAMES`
2. Все module-фикстуры — внедрить foreign container guard (по модели `test_smoke_postgres.py`):
   - Перед compose up: проверить `docker ps --filter name=<container>` — если контейнер уже running от `platform_services` → `_REUSE_CONTAINERS = True`
   - В режиме reuse: ожидать healthy (без compose up), в teardown пропустить compose down
   - В режиме fresh: текущее поведение (compose up → down)
3. `platform_services` — гарантировать cleanup stale-контейнеров перед стартом (расширить список)

**Ожидаемый результат:** 0 ERRORS в едином прогоне. Все 100 тестов проходят (97 pass + 2 skip + 1 macOS skip).

**Время:** не меняется (те же 500s), но тесты корректно работают в одном прогоне.

### 3.2 Фаза 2: Fixture Convergence (основная оптимизация)

**Цель:** Устранить дублирующие compose up/down циклы. Module-фикстуры переиспользуют контейнеры из `platform_services`.

**Стратегия:** Strangler-Fig — пошагово, а не big-bang.

#### Phase 2a: Universal reuse helper

Создать `tests/_conftest/reuse.py` — единый модуль с reusable-логикой:

```python
# @purpose — Unified container reuse logic, extracted from test_smoke_postgres.py
#           and generalized for all module-scoped fixtures.

def check_foreign_containers(container_names: list[str]) -> dict[str, bool]:
    """Check if containers are already running under a different project.
    Returns {name: is_foreign} dict."""

def reuse_or_start(
    container_names: list[str],
    compose_config: dict,  # {project_name, files, profile, env}
    healthcheck: Callable[[], bool],
) -> bool:
    """If containers already running → reuse (skip compose up/down).
    Otherwise → compose up → wait healthy → return False (caller handles teardown).
    Returns True if reusing (caller skips teardown)."""
```

Убрать дублирующуюся логику из 7 module-фикстур, заменив вызовом `reuse_or_start()`.

#### Phase 2b: Convert module-scoped fixtures to session-scoped with reuse

Для каждой module-фикстуры:

| Фикстура | Текущий scope | Новый scope | Что меняется |
|----------|--------------|-------------|-------------|
| `nginx_compose` | module | session (depends on platform_services) | reuse platform_services nginx-test; если нужен чистый nginx → restart |
| `postgres_up` (hermes) | module | session (depends on platform_services) | reuse platform_services postgres-test/pgbouncer-test |
| `hermes_up` | module | session (depends on platform_services) | reuse platform_services hermes-agent-test |
| `clickhouse_up` | module | session (depends on platform_services) | reuse platform_services clickhouse-test |
| `pgbouncer_up` | module | session (depends on platform_services) | reuse platform_services pgbouncer-test |
| `postgres_up` (smoke) | module | session (depends on platform_services) | УЖЕ делает reuse — только подтвердить |
| `redis_compose` | module | session (depends on platform_services) | reuse platform_services redis-test |

**Критически важно:** `platform_services` должен оставаться активным (yield) пока ВСЕ module-тесты не завершатся. Поскольку все фикстуры становятся session-scoped и зависят от `platform_services`, pytest гарантирует правильный порядок teardown: module-фикстуры → `platform_services` teardown.

#### Phase 2c: State reset для тестов, требующих чистого состояния

Некоторые тесты ожидают пустую БД или clean состояние сервиса. Для них — индикация через `@pytest.mark`:

```python
@pytest.mark.requires_fresh_state("postgres")  # перезапустить postgres перед этим тестом
def test_something():
    ...
```

Реализация: `autouse` fixture на function-scope, которая перед тестом с маркером делает `docker compose restart <service>` (секунды вместо минут на compose down/up).

**Ожидаемый результат по времени:** 500s → ~320s (-36%)

### 3.3 Фаза 3: Stop/Start Checkpoint (дополнительная оптимизация)

**Цель:** Ускорить teardown и повторный startup между группами тестов.

**Механика:**
- Вместо `docker compose down` → `docker compose stop` (контейнеры остаются, состояние на диске сохраняется)
- Перед следующей группой тестов: `docker compose start` (мгновенно, без пересоздания контейнеров, сетей, волюмов)
- Только тесты с `requires_fresh_state` → полный restart

**Реализация:**
- `platform_services` teardown: заменить `compose down --remove-orphans` на `compose stop`
- Добавить финальный cleanup в `pytest_sessionfinish`: если все тесты прошли → `compose down`
- Тесты с `requires_fresh_state` → `compose restart <service>` перед тестом

**Ожидаемый результат по времени:** 320s → ~250s (-22% от Фазы 2, -50% от baseline)

### 3.4 Фаза 4: Pre-pull (гигиена)

**Цель:** Уменьшить время compose up за счёт предварительной загрузки образов.

**Реализация:**
- `make docker-pull-all` — вызывает `docker compose pull` для всех 13 модулей + `docker compose build` для hermes
- В CI: вызов перед `make gate MODE=full`
- В macOS: необязательно (образы уже в кеше после первого прогона)

**Ожидаемый результат:** -10-30s от времени compose up.

### 3.5 Фаза 5: Wave-Pipeline — тесты запускаются по готовности волны

**Цель:** Запускать тесты как только их контейнерные зависимости удовлетворены, не дожидаясь полного стека. Обеспечить fault isolation: падение контейнера одной волны не блокирует тесты предыдущих волн.

#### Принцип работы

Вместо модели «запусти все контейнеры → запусти все тесты»:

```
platform_services: Wave0↑ → Wave1↑ → Wave2↑ → YIELD → [все 100 тестов]
```

Переходим к модели «запусти Wave 0 → тесты Wave 0 (пока стартует Wave 1) → тесты Wave 1 (пока стартует Wave 2) → ...»:

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

**Pipeline gain:** ~120s экономии за счёт перекрытия старта контейнеров с выполнением тестов.

#### Fault isolation

| Сценарий | Без Wave-Pipeline | С Wave-Pipeline |
|----------|-------------------|-----------------|
| postgres (Wave 0) не стартует | ❌ Все 100 тестов — error | ❌ Wave 0+1+2 тесты, ✅ ~5 тестов (read-only static) |
| litellm (Wave 1) не стартует | ❌ Все 100 тестов — error | ✅ ~35 Wave 0 тестов, ❌ ~65 Wave 1+2+3 тестов |
| hermes-agent (Wave 2) не стартует | ❌ Все 100 тестов — error | ✅ ~65 Wave 0+1 тестов, ❌ ~35 Wave 2+3 тестов |

**Диагностическая ценность:** вместо «всё сломано» получаем «litellm healthcheck failed → 24 теста затронуто, 76 прошли». Root cause виден сразу.

#### Распределение тестов по волнам

Волна теста = максимальный номер волны среди всех контейнеров, от которых он зависит (транзитивно через `module.yaml`):

| Wave | Контейнеры | Тестовые файлы | Тестов | Время тестов |
|------|-----------|----------------|--------|-------------|
| 0 | redis, nginx, clickhouse, postgres, minio, logging, infra-metrics | test_smoke_redis(5), test_smoke_nginx(5), test_component_clickhouse(3), test_smoke_postgres(2), test_smoke_logging(2), test_smoke_infra_metrics(3), test_smoke_provision(2), test_unit_provision(2) | 24 | ~30s |
| 1 | + pgbouncer, litellm, langfuse, monitoring, grafana, prometheus, backup-cron, status-page | test_component_pgbouncer(6), test_smoke_litellm(2), test_smoke_langfuse(3), test_smoke_monitoring(3) | 14 | ~20s |
| 2 | + hermes-agent | test_component_hermes(7), test_smoke_hermes(3) | 10 | ~10s |
| 3 | ALL (проверка полного стека) | test_smoke_platform(5), test_platform_endpoints(4) | 9 | ~40s |

#### Реализация

**Механизм:** Три компонента, работающие вместе:

1. **`@pytest.mark.wave(N)`** — маркер на тестовых файлах (не на функциях), указывает волну зависимости. Добавляется через `pytest_collection_modifyitems` или явно в коде.

2. **Background thread в `platform_services`** — после синхронного старта Wave 0, запускает Wave 1+2 в фоновом потоке. `threading.Event` сигнализирует готовность каждой волны.

3. **`_ensure_wave_ready` (autouse, function scope)** — перед каждым тестом проверяет `_wave_ready[wave].is_set()`. Если нет — ждёт (блокирует тест, но не блокирует setup других фикстур, т.к. function-scope выполняется непосредственно перед тестом).

```python
# tests/_conftest/wave_pipeline.py (новый модуль)
import threading
import pytest

# Wave readiness events — устанавливаются platform_services при готовности волны
_wave_ready: dict[int, threading.Event] = {}

def _init_wave_events(num_waves: int) -> None:
    """Called by platform_services setup to initialise events."""
    for w in range(num_waves + 1):
        _wave_ready[w] = threading.Event()

def signal_wave_ready(wave: int) -> None:
    """Called by platform_services background thread when wave N containers are healthy."""
    _wave_ready[wave].set()

@pytest.fixture(scope="function", autouse=True)
def _ensure_wave_ready(request) -> None:
    """Block test execution until its wave's containers are ready.
    
    Function-scoped → executes just before the test, NOT during session setup.
    This means Wave 0 tests run immediately (Wave 0 is ready),
    while Wave 1 tests block only when they're about to run.
    """
    marker = request.node.get_closest_marker("wave")
    if marker is None:
        return  # static tests, no Docker dependency
    wave = marker.args[0]
    if wave == 0:
        return  # Wave 0 is always ready before any test runs
    # Block until background thread signals Wave N containers are healthy
    event = _wave_ready.get(wave)
    if event is not None:
        event.wait(timeout=600)  # 10 min max wait
```

**Модификация `platform_services`:**

```python
# tests/_conftest/smoke.py — platform_services fixture
@pytest.fixture(scope="session")
def platform_services(request):
    # ... existing setup: ensure_volumes, ensure_networks, pre-cleanup ...
    
    # Инициализируем wave events
    num_waves = len(_build_waves(module_graph))
    _init_wave_events(num_waves)
    
    # Phase 1: Wave 0 — синхронно (критический путь, блокирует fixture setup)
    _start_wave_sync(0)
    signal_wave_ready(0)
    
    # Phase 2: Wave 1+2 — фоновый поток
    # Пока pytest выполняет Wave 0 тесты, контейнеры Wave 1 стартуют параллельно
    def _start_remaining():
        for wave in range(1, num_waves):
            _start_wave_sync(wave)
            signal_wave_ready(wave)
    
    bg_thread = threading.Thread(target=_start_remaining, daemon=True)
    bg_thread.start()
    
    yield  # pytest начинает тесты (Wave 0 — сразу, Wave 1+ — после signal)
    
    # Дождаться завершения фонового потока (на случай если тесты кончились раньше)
    bg_thread.join(timeout=600)
    
    # Teardown: compose stop (из Фазы 3)
    _teardown_all()
```

#### Тегирование тестов волнами

Волна назначается автоматически через `pytest_collection_modifyitems` на основе fixture-зависимостей теста:

```python
# conftest.py
def pytest_collection_modifyitems(items):
    """Tag tests with wave number based on container dependencies."""
    # Mapping: fixture_name → wave_number
    FIXTURE_WAVE = {
        "redis_compose": 0, "nginx_compose": 0, "clickhouse_up": 0,
        "postgres_up": 0, "logging_compose": 0, "infra_metrics_compose": 0,
        "pgbouncer_up": 1, "litellm_up": 1, "langfuse_up": 1, "monitoring_compose": 1,
        "hermes_up": 2,
        "platform_services": 3,  # tests that need ALL containers → always wave 3
    }
    
    for item in items:
        max_wave = 0
        # Check fixture names used by this test
        if hasattr(item, "fixturenames"):
            for fname in item.fixturenames:
                if fname in FIXTURE_WAVE:
                    max_wave = max(max_wave, FIXTURE_WAVE[fname])
        
        if max_wave > 0:
            item.add_marker(pytest.mark.wave(max_wave))
    
    # Sort: Wave 0 first, then Wave 1, then Wave 2, then Wave 3
    items.sort(key=lambda item: (
        item.get_closest_marker("wave").args[0] if item.get_closest_marker("wave") else 0,
        item.nodeid  # stable secondary sort
    ))
```

**Ключевое свойство:** `pytest_collection_modifyitems` только переупорядочивает тесты. `_ensure_wave_ready` (function scope) выполняет фактическую блокировку непосредственно перед тестом, а не во время session setup. Это позволяет Wave 0 тестам выполняться пока фоновый поток стартует Wave 1.

**Ожидаемый результат по времени:** 270s → ~200s (-26% от Фазы 4, -60% от baseline)

---

## 4. Step-by-Step Имплементация

### Wave 0: Preparation (исследование завершено ✅)

- [x] Собраны все имена контейнеров (production + test)
- [x] Построена матрица конфликтов
- [x] Найден reuse-паттерн в `test_smoke_postgres.py`
- [x] Определены 2 gap в `_STALE_CONTAINER_NAMES` (clickhouse-test, redis-test)
- [x] Подтверждён граф зависимостей из `core/modules/*/module.yaml`

### Wave 1: Container Name Conflict Resolution (Фаза 1)

**Файлы:**
| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 1 | `tests/_conftest/smoke.py` | Modify | Добавить `clickhouse-test`, `redis-test` в `_STALE_CONTAINER_NAMES` |
| 2 | `tests/_conftest/reuse.py` | **Create** | Новый модуль: `check_foreign_containers()`, `reuse_or_start()` — извлечённая и обобщённая логика из `test_smoke_postgres.py` |
| 3 | `tests/test_smoke_postgres.py` | Modify | Заменить inline foreign guard на вызов `reuse_or_start()` из `_conftest/reuse.py` |
| 4 | `tests/test_component_hermes.py` | Modify | Внедрить foreign guard в `postgres_up` и `hermes_up` |
| 5 | `tests/test_component_pgbouncer.py` | Modify | Внедрить foreign guard в `pgbouncer_up` |
| 6 | `tests/test_component_clickhouse.py` | Modify | Внедрить foreign guard в `clickhouse_up` |
| 7 | `tests/test_smoke_nginx.py` | Modify | Внедрить foreign guard в `nginx_compose` |
| 8 | `tests/test_smoke_redis.py` | Modify | Внедрить foreign guard в `redis_compose` |

**Верификация Wave 1:**
```bash
# Единый прогон ВСЕХ docker-тестов — должен дать 0 ERRORS
python -m pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" -v --tb=line
# Ожидается: 0 errors, только известные nginx_error_page (macOS) и langfuse_ingestion (HTTP 500)
```

### Wave 2: Fixture Scope Migration (Фаза 2)

**Файлы:**
| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 9 | `tests/_conftest/smoke.py` | Modify | `platform_services` — добавить параметр `_markers` для передачи markers в тесты; убедиться что teardown после ВСЕХ session-тестов |
| 10 | `tests/test_component_hermes.py` | Modify | `postgres_up`, `hermes_up`: module→session scope, depends on `platform_services` |
| 11 | `tests/test_component_pgbouncer.py` | Modify | `pgbouncer_up`: module→session scope, depends on `platform_services` |
| 12 | `tests/test_component_clickhouse.py` | Modify | `clickhouse_up`: module→session scope, depends on `platform_services` |
| 13 | `tests/test_smoke_nginx.py` | Modify | `nginx_compose`: module→session scope, depends on `platform_services` |
| 14 | `tests/test_smoke_redis.py` | Modify | `redis_compose`: module→session scope, depends on `platform_services` |
| 15 | `tests/test_smoke_postgres.py` | Modify | `postgres_up`: module→session scope, depends on `platform_services` |
| 16 | `tests/_conftest/__init__.py` | Modify | Реэкспорт новых session-фикстур для обратной совместимости |

**Ключевой момент scope-миграции:** все 7 фикстур становятся `scope="session"` и добавляют `platform_services` в параметры. Pytest выполняет setup фикстур в порядке зависимости: сначала `platform_services` (поднимает все контейнеры), потом module-фикстуры (обнаруживают running контейнеры через foreign guard → reuse). Teardown в обратном порядке: module-фикстуры (пропускают down, т.к. reuse=True) → `platform_services` (делает compose stop).

**Верификация Wave 2:**
```bash
python -m pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --durations=10
# Ожидается: setup times резко сокращены (module fixtures < 5s каждая вместо 15-25s)
```

### Wave 3: Fresh State Marker + Stop/Start (Фаза 3)

**Файлы:**
| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 17 | `tests/_conftest/state_reset.py` | **Create** | Новый модуль: autouse fixture `_reset_fresh_state()`, marker `requires_fresh_state`, функция `restart_service(name)` |
| 18 | `pyproject.toml` | Modify | Зарегистрировать marker `requires_fresh_state` |
| 19 | `tests/_conftest/smoke.py` | Modify | `platform_services` teardown: `compose down` → `compose stop`; финальный cleanup в `pytest_sessionfinish` |
| 20 | `tests/test_component_pgbouncer.py` | Modify | Добавить `@pytest.mark.requires_fresh_state("postgres")` на тесты, регистрирующие БД в pgbouncer |
| 21 | `tests/test_component_hermes.py` | Modify | Добавить `@pytest.mark.requires_fresh_state("hermes-agent")` если нужно |

**Верификация Wave 3:**
```bash
python -m pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --durations=10
# Ожидается: teardown < 10s (вместо ~60s), общее время ≤250s
```

### Wave 4: Pre-pull Target (Фаза 4)

**Файлы:**
| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 22 | `Makefile` | Modify | Добавить target `docker-pull-all`: `docker compose pull` для всех 13 модулей |
| 23 | `.github/workflows/platform-test.yml` | Modify | Добавить шаг `make docker-pull-all` перед `make gate MODE=full` |

**Верификация Wave 4:**
```bash
make docker-pull-all  # должен выполниться без ошибок
# CI: platform-test workflow проходит с pre-pull шагом
```

### Wave 5: Wave-Pipeline (Фаза 5)

**Файлы:**
| # | Файл | Действие | Описание |
|---|------|----------|----------|
| 24 | `tests/_conftest/wave_pipeline.py` | **Create** | Новый модуль: `_wave_ready` events, `_init_wave_events()`, `signal_wave_ready()`, `_ensure_wave_ready` autouse fixture |
| 25 | `tests/conftest.py` | Modify | Добавить `pytest_collection_modifyitems` — авто-тегирование тестов маркером `wave(N)` на основе fixture-зависимостей + сортировка по волне |
| 26 | `tests/_conftest/smoke.py` | Modify | `platform_services`: Wave 0 синхронно → Wave 1+2 в background thread → signal_wave_ready после каждой волны |
| 27 | `pyproject.toml` | Modify | Зарегистрировать marker `wave` |
| 28 | `tests/_conftest/__init__.py` | Modify | Реэкспорт `_ensure_wave_ready` для autouse-обнаружения |

**Верификация Wave 5:**
```bash
# 1. Порядок тестов: Wave 0 → Wave 1 → Wave 2 → Wave 3
python -m pytest tests/ --collect-only -q -m "(smoke or component or predeploy or requires_docker) and (not e2e)" 2>&1 | head -30

# 2. Fault isolation: убить litellm после Wave 0 → Wave 0 тесты должны пройти
# (ручной тест: docker stop litellm-test во время прогона)
python -m pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --durations=10

# 3. Общее время ≤200s
# Ожидается: --durations показывает setup module-фикстур < 5s, общее время ≤200s
```

---

## 5. Draft Code Graph (XML)

```xml
<Knowl_graph>
  <_conftest_reuse_py TYPE="MODULE" keywords="reuse,foreign_guard,container,compose">
    <check_foreign_containers_FUNC TYPE="FUNCTION" annotation="Check if containers running under foreign project"/>
    <reuse_or_start_FUNC TYPE="FUNCTION" annotation="Unified compose-or-reuse lifecycle"/>
  </_conftest_reuse_py>
  <_conftest_state_reset_py TYPE="MODULE" keywords="state,reset,restart,fresh">
    <_reset_fresh_state_FUNC TYPE="FUNCTION" annotation="Autouse fixture: restart services marked requires_fresh_state"/>
    <restart_service_FUNC TYPE="FUNCTION" annotation="docker compose restart single service"/>
  </_conftest_state_reset_py>
  <_conftest_wave_pipeline_py TYPE="MODULE" keywords="wave,pipeline,background,thread,event,fault_isolation">
    <_wave_ready_VAR TYPE="VARIABLE" annotation="dict[int, threading.Event] — сигналы готовности волн"/>
    <_init_wave_events_FUNC TYPE="FUNCTION" annotation="Создаёт Event для каждой волны"/>
    <signal_wave_ready_FUNC TYPE="FUNCTION" annotation="Вызывается bg_thread: wave N containers healthy → Event.set()"/>
    <_ensure_wave_ready_FUNC TYPE="FIXTURE" annotation="Autouse function-scope: блокирует тест до готовности его волны"/>
  </_conftest_wave_pipeline_py>
  <_conftest_smoke_py TYPE="MODULE" keywords="platform_services,stale,cleanup,stop,background,thread">
    <_STALE_CONTAINER_NAMES_VAR TYPE="VARIABLE" annotation="Extended: +clickhouse-test +redis-test"/>
    <platform_services_FUNC TYPE="FIXTURE" annotation="Wave 0 sync → Wave 1+2 bg_thread → Teardown: compose stop"/>
  </_conftest_smoke_py>
  <conftest_py TYPE="MODULE" keywords="collection,ordering,wave,marker">
    <pytest_collection_modifyitems_FUNC TYPE="HOOK" annotation="Auto-tag @pytest.mark.wave(N) + sort by wave"/>
  </conftest_py>
  <CrossLinks>
    <link from="platform_services_FUNC" to="_init_wave_events_FUNC" rel="calls in setup"/>
    <link from="platform_services_FUNC" to="signal_wave_ready_FUNC" rel="calls after each wave"/>
    <link from="_ensure_wave_ready_FUNC" to="_wave_ready_VAR" rel="waits on Event"/>
    <link from="pytest_collection_modifyitems_FUNC" to="platform_services_FUNC" rel="reads fixture dependencies to assign wave"/>
    <link from="check_foreign_containers_FUNC" to="platform_services_FUNC" rel="queries containers started by"/>
    <link from="reuse_or_start_FUNC" to="check_foreign_containers_FUNC" rel="calls"/>
    <link from="_reset_fresh_state_FUNC" to="restart_service_FUNC" rel="calls"/>
  </CrossLinks>
</Knowl_graph>
```

---

## 6. Data Flow: единый прогон после оптимизации (включая Wave-Pipeline)

```
pytest session start
│
├─ test_infra (session, autouse)
│  └─ ensure_external_networks() → test-* Docker networks
│  └─ _ensure_volume_dirs() → volume directories
│
├─ pytest_collection_modifyitems
│  └─ Авто-тегирование тестов @pytest.mark.wave(N) на основе fixture-зависимостей
│  └─ Сортировка: Wave 0 → Wave 1 → Wave 2 → Wave 3
│
├─ platform_services (session) SETUP
│  └─ _init_wave_events(3) → создаёт threading.Event для каждой волны
│  └─ Phase 1 (синхронно): Wave 0 containers up
│     └─ postgres(20s) ∥ redis(3s) ∥ nginx(3s) ∥ clickhouse(10s) ∥ loki(12s) ∥ minio(20s)
│     └─ signal_wave_ready(0) → _wave_ready[0].set()
│  └─ Phase 2 (background thread): start Wave 1+2
│     └─ bg_thread = Thread(target=_start_remaining_waves)
│     └─ bg_thread.start()  ← Wave 1 стартует пока Wave 0 тесты выполняются
│  └─ YIELD → pytest начинает тесты
│
├─ [Wave 0 тесты — немедленно, т.к. _wave_ready[0] уже set]
│  ├── _ensure_wave_ready: wave=0 → return (no wait)
│  ├── test_redis_smoke_ping, test_redis_config_* (5 tests, ~5s)
│  ├── test_nginx_http_responds, test_nginx_* (5 tests, ~10s)
│  ├── test_clickhouse_ping, test_clickhouse_* (3 tests, ~5s)
│  ├── test_smoke_postgres_* (2 tests, ~8s)
│  ├── test_loki_ready, test_loki_buildinfo (2 tests, ~3s)
│  ├── test_cadvisor_healthz, test_node_exporter_*, test_infra_* (3 tests, ~5s)
│  └── [Wave 0 tests total: ~30s]
│     ╔══════════════════════════════════════════════════════════╗
│     ║  BACKGROUND: Wave 1 containers starting in parallel      ║
│     ║  └─ pgbouncer(5s after postgres) ∥ litellm(70s)          ║
│     ║     ∥ langfuse(45s) ∥ grafana+prometheus(15s)            ║
│     ╚══════════════════════════════════════════════════════════╝
│
├─ [Wave 1 тесты — блокируются до _wave_ready[1].set()]
│  ├── _ensure_wave_ready: wave=1 → _wave_ready[1].wait()
│  │   └─ К этому моменту bg_thread уже мог завершить Wave 1 (30s overlap)
│  │   └─ Если litellm ещё стартует (40s осталось) → ждём
│  ├── test_pgbouncer_container_healthy, test_pgbouncer_* (6 tests, ~5s)
│  ├── test_litellm_readiness, test_litellm_models_api (2 tests, ~5s)
│  ├── test_langfuse_health, test_langfuse_* (3 tests, ~10s)
│  ├── test_prometheus_health, test_grafana_health, test_prometheus_targets (3 tests, ~10s)
│  └── [Wave 1 tests total: ~20s]
│     ╔══════════════════════════════════════════════════════════╗
│     ║  BACKGROUND: Wave 2 (hermes-agent, build ~60s) starting  ║
│     ╚══════════════════════════════════════════════════════════╝
│
├─ [Wave 2 тесты — блокируются до _wave_ready[2].set()]
│  ├── _ensure_wave_ready: wave=2 → _wave_ready[2].wait()
│  ├── test_hermes_compose_up, test_hermes_agent_starts, test_healthcheck_passes,
│  │   test_no_restart_loop, test_hermes_dashboard_auth, test_hermes_gateway_listens,
│  │   test_ready_endpoint_returns_valid_json (7 tests, ~5s)
│  ├── test_hermes_dashboard_health, test_hermes_auth_login, test_hermes_api_completions (3 tests, ~35s)
│  │   └─ test_hermes_api_completions: 60s timeout на LLM-вызов
│  └── [Wave 2 tests total: ~40s]
│
├─ [Wave 3 тесты — полный стек, блокируются до _wave_ready[3].set()]
│  ├── _ensure_wave_ready: wave=3 → _wave_ready[3].wait()
│  ├── test_platform_starts_all_containers (оптимизирован: ~30s)
│  ├── test_critical_services_healthy, test_no_restart_loops,
│  │   test_docker_daemon_available, test_platform_cleanup (4 tests, ~5s)
│  ├── test_hermes_dashboard_endpoint, test_prometheus_healthy_endpoint,
│  │   test_grafana_health_endpoint, test_langfuse_health_endpoint (4 tests, ~5s)
│  └── [Wave 3 tests total: ~40s]
│
├─ [Если тест с @requires_fresh_state("postgres")]
│  └─ _reset_fresh_state: docker compose restart postgres → ~3-5s
│
├─ platform_services TEARDOWN
│  └─ bg_thread.join() — гарантировать завершение фонового потока
│  └─ compose stop (вместо compose down) — контейнеры остановлены, ~10s
│
└─ pytest_sessionfinish
   └─ docker compose down --remove-orphans (финальная очистка)
   └─ docker network rm test-* (очистка сетей)
```

**Тайминг (прогноз):**

| Этап | Время | Накоплено |
|------|-------|-----------|
| Wave 0 containers up (синхронно) | 20s | 20s |
| Wave 0 tests (Wave 1 стартует в фоне) | 30s | 50s |
| Wait Wave 1 ready (остаток от 70s) | 40s | 90s |
| Wave 1 tests (Wave 2 стартует в фоне) | 20s | 110s |
| Wait Wave 2 ready (остаток от 60s) | 40s | 150s |
| Wave 2 tests | 40s | 190s |
| Wave 3 tests (без ожидания — всё уже ready) | 40s | 230s |
| Teardown (compose stop + sessionfinish cleanup) | 10s | 240s |
| **Итого** | | **~240s ≤ 250s** |

**При оптимизации test_platform_starts_all_containers (122s → 30s): 240s → ~210s ≤ 200s.**

**Ключевые изменения относительно baseline:**
- Module-фикстуры SETUP: 120s → ~5s (reuse, без compose up) — Фаза 2
- Module-фикстуры TEARDOWN: 60s → ~2s (skip compose down) — Фаза 2
- platform_services TEARDOWN: 60s → ~10s (compose stop) — Фаза 3
- Pipeline overlap: ~120s экономии (тесты + старт контейнеров параллельно) — Фаза 5
- **Итого экономия: ~290s (500s → ~210s, -58%)**

---

## 7. Acceptance Criteria

| # | Критерий | Как проверить | Target |
|---|----------|--------------|--------|
| AC1 | 0 ERRORS в едином прогоне | `pytest tests/ -m "(smoke or component or predeploy or requires_docker) and (not e2e)" --tb=line` | 0 errors |
| AC2 | Все 100 тестов проходят | То же, `-v` | 97 pass + 2 skip + 1 skip (macOS nginx_error_page) |
| AC3 | Время прогона ≤200s | `--durations=0` | ≤200s |
| AC4 | Тестовые функции не изменены | `git diff --stat -- 'tests/test_*.py'` | Только fixture-строки, не def test_* тела |
| AC5 | `make gate MODE=full` зелёный | `make gate MODE=full` | exit 0 |
| AC6 | `make docker-pull-all` работает | `make docker-pull-all` | exit 0, все образы загружены |
| AC7 | Foreign guard работает на всех 7 фикстурах | `docker ps` до и после прогона | Контейнеры от platform_services переиспользованы, не пересозданы |
| AC8 | Тесты упорядочены по волнам | `pytest --collect-only -q` | Wave 0 тесты идут первыми, затем Wave 1, 2, 3 |
| AC9 | Fault isolation: Wave 0 тесты проходят при падении Wave 1 | Ручной тест: `docker stop litellm-test` во время прогона | Wave 0 тесты PASS, Wave 1+2+3 — FAIL с диагностикой «litellm healthcheck» |

---

## 8. Risk Register

| Риск | Вероятность | Impact | Митигация |
|------|------------|--------|-----------|
| Тесты ожидают пустую БД, но БД переиспользуется | MEDIUM | HIGH | `@requires_fresh_state` маркер + autouse restart; тесты, которые такой маркер получат, будут определены в Wave 3 на основе реальных падений |
| `compose stop` не очищает анонимные волюмы | LOW | MEDIUM | `compose down` в `pytest_sessionfinish` выполняет полную очистку |
| Pytest ordering: session-фикстуры из разных файлов | LOW | HIGH | Все фикстуры явно зависят от `platform_services` через параметры — pytest гарантирует правильный порядок |
| `docker compose restart` медленнее чем ожидается | LOW | LOW | Измерить в Wave 3; если >10s — вернуть `compose stop/start` |
| Port conflict при параллельном nginx_compose (ports 18080/18443) | LOW | MEDIUM | nginx_compose использует свои порты, не конфликтующие с platform_services (80/443) |
| Background thread не завершается до teardown | MEDIUM | HIGH | `bg_thread.join(timeout=600)` + `daemon=True`; если поток завис — teardown всё равно выполняется |
| `_ensure_wave_ready` блокирует тест навсегда (event never set) | LOW | HIGH | `event.wait(timeout=600)` — 10 мин максимум; если таймаут → pytest fail с диагностикой «Wave N containers never became ready» |
| `pytest_collection_modifyitems` неправильно определяет волну теста | MEDIUM | MEDIUM | FIXTURE_WAVE mapping валидируется в Wave 5 тестом: `assert all tests have correct wave marker` |
| Wave 0 тесты выполняются дольше чем ожидалось → Wave 1+2 уже готовы → pipeline gain меньше | MEDIUM | LOW | Даже без pipeline gain тесты всё ещё проходят (fallback к модели «все контейнеры готовы до тестов») |

---

## 9. Rollback Plan

Каждая wave независима — при проблеме откатывается только последняя wave:

```
Wave 1 (Conflict Resolution)  → git revert <wave1-commit>
Wave 2 (Scope Migration)      → git revert <wave2-commit>
Wave 3 (Stop/Start)           → git revert <wave3-commit>
Wave 4 (Pre-pull)             → git revert <wave4-commit>
Wave 5 (Wave-Pipeline)        → git revert <wave5-commit>
```

**Специфика Wave 5:** `_ensure_wave_ready` (function-scope autouse) — если вызывает проблемы, удаление этого модуля (`tests/_conftest/wave_pipeline.py`) + удаление `pytest_collection_modifyitems` из `conftest.py` возвращает систему к post-Wave-4 поведению: все тесты выполняются после полного старта стека, но с уже ускоренными fixture-циклами. Pipeline — strictly additive, не блокирует базовую функциональность.

Все изменения в фикстурах, не в тестовых функциях. Откат не ломает тесты.

$END_DEVPLAN
