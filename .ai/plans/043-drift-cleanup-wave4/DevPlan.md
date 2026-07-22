# 043-DevPlan: Drift Cleanup — Wave 4 Post-Implementation Fixes

**Trigger:** VerificationReport 041/04 — 3 MEDIUM drift findings (DRIFT-DP-3, DRIFT-DP-5, DRIFT-DP-6) + 1 LOW (DRIFT-IMP-3)
**Parent plan:** DevPlan 041 (test-infra-fault-tolerance)
**Model:** dev-pipeline skill (Brief → Architect → Coder → QA)
**Size:** SMALL — 5 files modified, 1 file removed, ~40 LOC net change

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Завершить неполную миграцию DevPlan 041 — устранить 3 MEDIUM drift-находки и 1 LOW, обнаруженные в post-implementation verification audit (04-VerificationReport.md).
DESCRIPTION:           Три атомарные задачи: (1) DRIFT-DP-3 — миграция 4 module-фикстур с ensure_external_networks() на NetworkLeaseManager.acquire()/release(); (2) DRIFT-DP-5 — удаление неиспользуемого check_foreign_containers_adapter() как dead code; (3) DRIFT-DP-6 + DRIFT-IMP-3 — добавление hermes-agent.desktop: 18642 в test_ports platform-env.yaml и синхронизация двух источников тестовых портов.
RATIONALE:             DevPlan 041 W3 спроектировал NetworkLeaseManager с явным намерением заменить ensure_external_networks() (DevPlan 041:410-414). W5 мигрировал platform_services, но module-фикстуры остались на старом механизме — incomplete migration. check_foreign_containers_adapter() был convenience-обёрткой, которая не подошла ни одному вызывающему (smoke-тесты используют отличные от конвенции project names) — dead code. SMOKE_ENV и test_ports — два независимых источника тестовых портов, HERMES_DESKTOP_TEST_PORT присутствует только в одном из них.
ACCEPTANCE_CRITERIA:
  - **AC-1 (DRIFT-DP-3):** Ни одного вызова `ensure_external_networks()` в test_smoke_postgres.py, test_component_pgbouncer.py, test_component_clickhouse.py, test_component_hermes.py. Вместо этого — `nm = get_network_manager(); nm.acquire(...)` в setup и `nm.release(...)` в teardown.
  - **AC-2 (DRIFT-DP-5):** `check_foreign_containers_adapter()` удалён из `tests/_conftest/reuse.py`. Ни одного оставшегося reference на него (кроме исторического упоминания в gate error message — допустимо).
  - **AC-3 (DRIFT-DP-6 + DRIFT-IMP-3):** `hermes-agent.desktop: 18642` добавлен в секцию `test_ports` platform-env.yaml. Gate AC-6b (test_test_ports_match_compose_ports) остаётся зелёным.
  - **AC-4 (regression):** `make gate MODE=fast` green. Все существующие Docker-тесты (smoke + component) проходят без деградации.
  - **AC-5 (LDD):** Каждый изменённый файл сохраняет IMP:9 логи в бизнес-логике.
IMPLEMENTS:            VerificationReport 041/04 §Section 8 — Required Post-Implementation Fixes. DevPlan 041 §W3 design decision (NetworkLeaseManager replaces ensure_external_networks). DevPlan 041 §W4 test_ports design.
IMPACTS:               **Modified:** `tests/test_smoke_postgres.py` (DRIFT-DP-3), `tests/test_component_pgbouncer.py` (DRIFT-DP-3), `tests/test_component_clickhouse.py` (DRIFT-DP-3), `tests/test_component_hermes.py` (DRIFT-DP-3), `tests/_conftest/reuse.py` (DRIFT-DP-5 — removal), `platform-env.yaml` (DRIFT-DP-6 + DRIFT-IMP-3). **Gate tests:** `tests/gates/test_gate_test_infra_consistency.py` — удалить reference на check_foreign_containers_adapter из error message.
REQUIRES:              Docker daemon running (для верификации smoke/component тестов). Python ≥3.10. Чистый working tree.
$END_ARTIFACT_CONTRACT

---

## 1. Current State (Read before Act — Principle 9)

### 1.1 Drift Inventory (from VerificationReport 041/04)

| Drift ID | Severity | Description | Status |
|----------|:--------:|-------------|--------|
| DRIFT-DP-3 | MEDIUM | `ensure_external_networks()` используется напрямую в 4 module-фикстурах — миграция на NetworkLeaseManager неполная | ❌ |
| DRIFT-DP-5 | MEDIUM | `check_foreign_containers_adapter()` определён но не используется ни одним тестом | ❌ |
| DRIFT-DP-6 | MEDIUM | `SMOKE_ENV` (жёстко заданные порты) и `platform-env.yaml` `test_ports` — два независимых источника истины для тестовых портов | ❌ |
| DRIFT-IMP-3 | LOW | `HERMES_DESKTOP_TEST_PORT: 18642` в SMOKE_ENV, но отсутствует в `test_ports` | ❌ |

### 1.2 Affected Call Sites — DRIFT-DP-3

4 файла вызывают `ensure_external_networks()` напрямую:

| Файл | Строка | Сети |
|------|--------|------|
| `test_smoke_postgres.py:195` | `ensure_external_networks(_EXTERNAL_NETWORKS)` | `["test-shared-db-net"]` |
| `test_component_pgbouncer.py:217` | `ensure_external_networks(_EXTERNAL_NETWORKS)` | `["test-shared-db-net"]` |
| `test_component_clickhouse.py:198` | `ensure_external_networks(_EXTERNAL_NETWORKS)` | `["observability-net"]` |
| `test_component_hermes.py:149` | `ensure_external_networks(_EXTERNAL_NETWORKS)` | `["shared-db-net", "proxy-net", "hermes-agent-net", "observability-net", "test-shared-db-net"]` |

**Примечание:** `test_smoke_nginx.py` имеет собственный inline-механизм создания сетей (не через `ensure_external_networks()`) — outside scope данного DevPlan, требует отдельного анализа (inline logic → NetworkLeaseManager migration is a separate task). `test_smoke_redis.py` не создаёт сети самостоятельно (полагается на platform_services).

### 1.3 Dead Code — DRIFT-DP-5

`check_foreign_containers_adapter()` (`tests/_conftest/reuse.py:103-124`):
- Определён, но **ни разу не вызван** в тестовых файлах
- Все 6 тестовых файлов продолжают вызывать `check_foreign_containers()` напрямую
- Адаптер использует конвенцию `own_project = f"ai-platform-test-{module_name}"`, которая подходит только для component-тестов, но не для smoke-тестов (где project names отличаются: `ai-platform-smoke-postgres`, `wave-redis-smoke`, etc.)
- Единственное упоминание вне определения — gate error message в `test_gate_test_infra_consistency.py:367`

### 1.4 Dual Truth — DRIFT-DP-6 + DRIFT-IMP-3

**SMOKE_ENV** (`tests/_conftest/smoke.py:93-136`):
```
LITELLM_TEST_PORT: "14000"
HERMES_DASHBOARD_TEST_PORT: "19119"
HERMES_DESKTOP_TEST_PORT: "18642"    ← отсутствует в test_ports
LANGFUSE_TEST_PORT: "13000"
PROMETHEUS_TEST_PORT: "19090"
GRAFANA_TEST_PORT: "13030"
```

**test_ports** (`platform-env.yaml:125-148`):
```yaml
litellm.litellm: 14000
nginx.http: 18080          ← отсутствует в SMOKE_ENV
nginx.https: 18443          ← отсутствует в SMOKE_ENV
clickhouse.http: 18123      ← отсутствует в SMOKE_ENV
clickhouse.metrics: 19363   ← отсутствует в SMOKE_ENV
hermes-agent.dashboard: 19119
# hermes-agent.desktop ОТСУТСТВУЕТ ← DRIFT-IMP-3
monitoring.prometheus: 19090
monitoring.grafana: 13030
langfuse.langfuse: 13000
logging.loki: 13100          ← отсутствует в SMOKE_ENV
```

**Анализ:** Полная консолидация SMOKE_ENV и test_ports была бы крупным рефакторингом (SMOKE_ENV используется platform_services fixture для compose env vars; test_ports — YAML-секция для gate-валидации). Минимальное необходимое действие: обеспечить, чтобы все порты из SMOKE_ENV присутствовали в test_ports (test_ports — канонический реестр). Отсутствующие в SMOKE_ENV порты (nginx, clickhouse, loki) не являются проблемой — SMOKE_ENV содержит только те переменные, которые реально передаются в compose.

---

## 2. Design Decisions

### 2.1 DRIFT-DP-3: NetworkLeaseManager Migration Pattern

**DevPlan 041 design (стр. 410-414):**
> `ensure_external_networks()` становится тонкой обёрткой, вызывающей `NetworkLeaseManager.acquire()`. После полной миграции всех потребителей → `ensure_external_networks()` удаляется.

**Решение (данный DevPlan):** Мигрировать 4 оставшихся потребителя на прямой вызов `NetworkLeaseManager.acquire()/release()`. `ensure_external_networks()` пока сохраняется — она используется в `conftest.py` (импортируется другими файлами). Полное удаление `ensure_external_networks()` — вне скоупа данного DevPlan (требует аудита ВСЕХ потребителей, включая `conftest.py`).

**Паттерн миграции:**

```python
# БЫЛО:
from conftest import ensure_external_networks
_EXTERNAL_NETWORKS = ["test-shared-db-net"]

# В fixture setup:
ensure_external_networks(_EXTERNAL_NETWORKS)

# В fixture teardown:
# (ничего — сети не удалялись, полагались на pytest_sessionfinish)

# СТАЛО:
from _conftest.networks import get_network_manager
_EXTERNAL_NETWORKS = ["test-shared-db-net"]

# В fixture setup:
_nm = get_network_manager()
for net in _EXTERNAL_NETWORKS:
    _nm.acquire(net)

# В fixture teardown (yield):
for net in _EXTERNAL_NETWORKS:
    _nm.release(net)
```

### 2.2 DRIFT-DP-5: Remove check_foreign_containers_adapter

**Superposition (3 варианта):**

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| A | Мигрировать все тесты на adapter | Завершает DevPlan 041 замысел | Адаптер не подходит smoke-тестам (project naming convention mismatch); требует переписывания логики адаптера |
| B | Удалить adapter как dead code | Minimal change, устраняет drift | "Потеря" написанного кода |
| C | Оставить как есть | Нет изменений | Dead code — нарушение принципа clean code |

**Решение: Option B** — удалить `check_foreign_containers_adapter()`.

**Обоснование:**
- Адаптер спроектирован под конвенцию `ai-platform-test-{module}`, которая работает только для component-тестов. Smoke-тесты используют другие project names (`ai-platform-smoke-postgres`, `wave-redis-smoke`, `wave-nginx-smoke`).
- Чтобы adapter заработал для всех, потребовалось бы добавить параметр `own_project` — что сводит на нет его convenience (становится не проще, чем прямой вызов `check_foreign_containers`).
- Gate error message в `test_gate_test_infra_consistency.py:367` упоминает adapter — reference обновляется на `check_foreign_containers()`.

### 2.3 DRIFT-DP-6 + DRIFT-IMP-3: Consolidate test_ports

**Решение:** Добавить `hermes-agent.desktop: 18642` в `test_ports` platform-env.yaml.

**Что НЕ делается в этом DevPlan:**
- Полная консолидация SMOKE_ENV и test_ports (требует рефакторинга platform_services fixture для чтения портов из platform-env.yaml вместо хардкода)
- Добавление nginx/clickhouse/loki портов в SMOKE_ENV (SMOKE_ENV содержит только compose env vars, не все порты)

---

## 3. Implementation Tasks

### TASK-1: DRIFT-DP-3 — Migrate test_smoke_postgres.py

**Файл:** `tests/test_smoke_postgres.py`

**Изменения:**
1. Импорт: заменить `from conftest import ... ensure_external_networks ...` → добавить `from _conftest.networks import get_network_manager` (отдельной строкой)
2. В fixture `postgres_up` (строка ~195): заменить `ensure_external_networks(_EXTERNAL_NETWORKS)` на цикл `_nm.acquire()`
3. В fixture teardown (после `yield`): добавить цикл `_nm.release()`

### TASK-2: DRIFT-DP-3 — Migrate test_component_pgbouncer.py

**Файл:** `tests/test_component_pgbouncer.py`

**Изменения:** Аналогично TASK-1.

### TASK-3: DRIFT-DP-3 — Migrate test_component_clickhouse.py

**Файл:** `tests/test_component_clickhouse.py`

**Изменения:** Аналогично TASK-1.

### TASK-4: DRIFT-DP-3 — Migrate test_component_hermes.py

**Файл:** `tests/test_component_hermes.py`

**Изменения:** Аналогично TASK-1. Сетей больше (5), но паттерн тот же.

### TASK-5: DRIFT-DP-5 — Remove check_foreign_containers_adapter

**Файл:** `tests/_conftest/reuse.py`
- Удалить функцию `check_foreign_containers_adapter()` (строки 103-124)

**Файл:** `tests/gates/test_gate_test_infra_consistency.py`
- Обновить error message: заменить `check_foreign_containers_adapter()` на `check_foreign_containers()`

### TASK-6: DRIFT-DP-6 + DRIFT-IMP-3 — Add missing port to test_ports

**Файл:** `platform-env.yaml`
- В секцию `hermes-agent` добавить: `desktop: 18642  # base: 8642, test: 1{8642}`

---

## 4. File Manifest

| # | File | Action | LOC change |
|---|------|--------|:---:|
| 1 | `tests/test_smoke_postgres.py` | Modify — migrate ensure_external_networks → NetworkLeaseManager | ~+8/-3 |
| 2 | `tests/test_component_pgbouncer.py` | Modify — migrate ensure_external_networks → NetworkLeaseManager | ~+8/-3 |
| 3 | `tests/test_component_clickhouse.py` | Modify — migrate ensure_external_networks → NetworkLeaseManager | ~+8/-3 |
| 4 | `tests/test_component_hermes.py` | Modify — migrate ensure_external_networks → NetworkLeaseManager | ~+8/-3 |
| 5 | `tests/_conftest/reuse.py` | Modify — remove check_foreign_containers_adapter() | -22 |
| 6 | `tests/gates/test_gate_test_infra_consistency.py` | Modify — update error message reference | ~1 |
| 7 | `platform-env.yaml` | Modify — add hermes-agent.desktop: 18642 | +1 |

**Total:** ~+34/-35 LOC, 7 files.

---

## 5. Verification Plan

### 5.1 Static Verification

```bash
# No remaining ensure_external_networks calls in the 4 target files
grep -n "ensure_external_networks" tests/test_smoke_postgres.py tests/test_component_pgbouncer.py tests/test_component_clickhouse.py tests/test_component_hermes.py
# Expected: no output (or only in comments/docstrings)

# No remaining check_foreign_containers_adapter definition
grep -n "check_foreign_containers_adapter" tests/_conftest/reuse.py
# Expected: no output

# Check hermes-agent.desktop in test_ports
python3 -c "import yaml; d=yaml.safe_load(open('platform-env.yaml')); print(d['test_ports']['hermes-agent'])"
# Expected: {'dashboard': 19119, 'desktop': 18642}
```

### 5.2 Gate Verification

```bash
make gate MODE=fast
# Expected: exit 0, all gate tests pass

# Specifically verify AC-6b (test_ports match compose)
pytest tests/gates/test_gate_test_infra_consistency.py::test_test_ports_match_compose_ports -v
# Expected: PASS
```

### 5.3 Runtime Verification (Docker required)

```bash
# Smoke tests
pytest tests/test_smoke_postgres.py -v --tb=short
pytest tests/test_smoke_hermes.py -v --tb=short

# Component tests
pytest tests/test_component_pgbouncer.py -v --tb=short
pytest tests/test_component_clickhouse.py -v --tb=short
pytest tests/test_component_hermes.py -v --tb=short
```

---

## 6. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|:---:|:---:|-----------|
| NetworkLeaseManager.release() в teardown удаляет сеть до того, как compose down отработал | LOW | MEDIUM | `release()` вызывается ПОСЛЕ `docker compose down` (сначала останавливаем контейнеры, потом освобождаем сеть). Порядок операций в teardown: compose down → release networks. |
| Refcount leak при падении теста до release() | LOW | LOW | `pytest_sessionfinish` → `release_all()` принудительно очищает все сети. |
| Gate AC-6b fails после добавления hermes-agent.desktop | LOW | LOW | Проверить, что порт 18642 действительно присутствует в docker-compose.test.yml еёrmes-agent. Если нет — gate правильно сигнализирует о несоответствии, нужна правка в compose. |

---

## 7. Teardown Pattern (DRIFT-DP-3)

Корректный порядок операций в fixture teardown:

```python
# Правильный порядок:
yield  # тесты выполняются здесь
# --- teardown ---
# 1. Останавливаем контейнеры
subprocess.run(["docker", "compose", ..., "down", ...])
# 2. Освобождаем сети (теперь они не используются)
for net in _EXTERNAL_NETWORKS:
    _nm.release(net)
```

**Почему важен порядок:** `docker compose down` отключает контейнеры от external-сетей. Если release() вызвать до compose down, сеть может быть удалена пока контейнеры ещё к ней подключены → ошибка.

---

## 8. Rollback

```bash
# Каждая задача атомарна и независима:
git revert <commit>  # откат любой отдельной задачи без последствий для остальных
```

$END_DEVPLAN
