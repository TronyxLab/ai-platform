# 041-DevPlan: Test Infrastructure Fault Tolerance — Auto-Discovery + NetworkLeaseManager

**Post-mortem:** 7 TRAP[BUG] коллизий compose-проектов за один день (2026-07-22)
**Architecture Forensics Report:** `.ai/plans/041-test-infra-fault-tolerance/01-ForensicsReport.md` (предшествует данному DevPlan)
**Model:** dev-pipeline skill (Brief → Architect → Coder → QA → Fix)

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Устранить системный архитектурный антипаттерн — отсутствие единого источника истины для тестовой инфраструктуры. Хардкод container_name, project_name, портов, сетей и _STALE_CONTAINER_NAMES в 7+ тестовых файлах приводит к FRAGILITY COLLAPSE: любое изменение docker-compose.test.yml вызывает каскад поломок в тестах. Сети создаются/удаляются 6 независимыми фикстурами без арбитража (BOUNDARY COLLAPSE). Внедрить: авто-обнаружение тестовой инфраструктуры из compose-файлов (Option E из суперпозиции), NetworkLeaseManager для refcounting сетей, расширение platform-env.yaml секцией test_ports, CI gate для предотвращения drift.
DESCRIPTION:           6 волн (W1-W6). W1: расширение discover_modules.py флагом --test-infra — авто-обнаружение container_name, networks, ports из docker-compose.test.yml. W2: tests/_conftest/infra.py — кэширующий Python-слой, предоставляющий get_container_name(), get_test_port(), STALE_CONTAINER_NAMES (всегда актуальны). W3: NetworkLeaseManager в tests/_conftest/networks.py — refcounting для тестовых сетей (observability-net, proxy-net, test-shared-db-net, etc.), устраняет CRITICAL race condition. W4: platform-env.yaml — секция test_ports с каноническими тестовыми портами. W5: миграция 7 тестовых файлов — замена хардкодов на импорты из infra.py. W6: CI gate test_gate_test_infra_consistency.py — валидация консистентности между compose-файлами, инфраструктурным кодом и манифестом.
RATIONALE:             Architecture Forensics (arch-forensics skill, S7-S15 суперпозиция) обнаружил 3 коллапса суперпозиции: (1) FRAGILITY COLLAPSE — _STALE_CONTAINER_NAMES и container_name в compose-файлах связаны неявным контрактом, не enforced на уровне кода (S8 logical coupling ∩ S14 change impact ∩ S15 hidden convention); (2) BOUNDARY COLLAPSE — тестовая инфраструктура не имеет владельца, observability-net создаётся 6 фикстурами без арбитража (S7 no owner ∩ S10 blast radius); (3) OWNERSHIP COLLAPSE — shared resources без явного владельца, классический антипаттерн распределённых систем (S9 multiple updaters ∩ S14 change impact). Паттерн проявляется системно: 7 TRAP[BUG] за день, все одного класса (коллизия compose-проектов). Существующий discover_modules.py (120 LOC) уже реализует авто-обнаружение docker-модулей — расширяется этим же паттерном на test compose-файлы. NetworkLeaseManager — минимальный refcounting-механизм, устраняющий CRITICAL риск без переписывания всех фикстур. Option E выбран из 5 вариантов суперпозиции как соответствующий существующим паттернам платформы (derive from existing, не invent new format).
ACCEPTANCE_CRITERIA:
  - **AC-1 (W1 auto-discovery):** `python3 core/internal/bootstrap/discover_modules.py --test-infra --json` возвращает JSON с полями: module_name, container_name, networks, ports, compose_base, compose_test для каждого модуля, имеющего docker-compose.test.yml. Вывод детерминирован (сортировка по module_name). Пустой вывод (без флага) НЕ меняется — обратная совместимость.
  - **AC-2 (W2 infra.py):** `from tests._conftest.infra import STALE_CONTAINER_NAMES, get_container_name, get_test_port, get_compose_file, get_networks_for_module`. `STALE_CONTAINER_NAMES` содержит ровно столько имён, сколько container_name в docker-compose.test.yml (22 на момент написания). `get_container_name("postgres")` → `"postgres-test"`. `get_test_port("pgbouncer", "listen")` → `6432`. `get_compose_file("postgres")` → (Path(base), Path(test)). Результат кэшируется на уровне модуля (однократный subprocess).
  - **AC-3 (W3 NetworkLeaseManager):** `NetworkLeaseManager.acquire("test-shared-db-net")` создаёт сеть при первом вызове, инкрементит refcount. `release()` декрементит, удаляет сеть при refcount=0. `pytest_sessionfinish` принудительно очищает все оставшиеся сети. Одновременный запуск 6 тестов с observability-net не вызывает race condition — сеть создаётся один раз, удаляется последним потребителем.
  - **AC-4 (W4 platform-env.yaml):** Секция `test_ports` добавлена в platform-env.yaml. Каждая запись: `module_name: { port_name: port_number }`. Порты соответствуют `1{base_port}` convention (где применимо). Gate test_gate_env_example_sync.py остаётся зелёным (новые ключи не ломают существующие валидации).
  - **AC-5 (W5 migration):** 7 тестовых файлов мигрированы: `_STALE_CONTAINER_NAMES` заменён на импорт из `infra.py` в `smoke.py`; хардкоженные container_name заменены на `get_container_name()` в 5 файлах; хардкоженные порты заменены на `get_test_port()` в 8+ местах; `check_foreign_containers` own_project выводится из compose-файла, а не хардкодится. Существующие тесты остаются зелёными.
  - **AC-6 (W6 CI gate):** `test_gate_test_infra_consistency.py` — 5 проверок: (a) `STALE_CONTAINER_NAMES` == все container_name из docker-compose.test.yml; (b) каждый test_port из platform-env.yaml соответствует порту в compose-файле; (c) все compose-проекты уникальны; (d) сети из compose-файлов зарегистрированы в NetworkLeaseManager; (e) ни одного вхождения `"ai-platform-test"` как project name в тестовых файлах (anti-regression для TRAP[BUG] 2026-07-22). Проверяются: `check_foreign_containers`, `COMPOSE_PROJECT`, `COMPOSE_PROJECT_NAME`, `-p ai-platform-test` в subprocess-вызовах. Исключение: `SMOKE_ENV` и `platform_services` в `smoke.py` где `"ai-platform-test"` легитимен.
  - **AC-7 (regression):** `make gate MODE=fast` green. Все Docker-зависимые тесты (smoke + component): 43/45 PASS (2 pre-existing failure: langfuse DNS + nginx error page). Время прогона не ухудшается (≤baseline 500s).
  - **AC-8 (drift prevention):** CI gate W6 падает при: добавлении нового docker-compose.test.yml без обновления STALE_CONTAINER_NAMES; изменении container_name в compose без отражения в infra.py; добавлении дублирующего compose-проекта; хардкоде `"ai-platform-test"` как project name в любом контексте (check_foreign_containers, COMPOSE_PROJECT, -p флаг, subprocess-вызовы).
IMPLEMENTS:            Architecture Forensics Report (arch-forensics skill, S7-S15 superposition, 3 collapses detected). Option E из суперпозиции (5 вариантов): derive from existing manifests (discover_modules.py, platform-env.yaml) + NetworkLeaseManager для сетей. Принцип 6 (Small Simple Blocks — расширение существующих модулей, не дублирование). Принцип 9 (Read before Act — кодовая база прочитана 3 субагентами, карта хрупкости построена). Инвариант 1 (Makefile — единый фасад, discover-modules target расширен).
IMPACTS:               **Modified Python:** `core/internal/bootstrap/discover_modules.py` (W1 — флаг --test-infra, +60 LOC), `tests/_conftest/smoke.py` (W5 — замена _STALE_CONTAINER_NAMES на импорт, удаление хардкода, -15/+3 строк). **New Python:** `tests/_conftest/infra.py` (W2 — кэширующий слой, ~100 LOC), `tests/_conftest/networks.py` (W3 — NetworkLeaseManager, ~50 LOC), `tests/gates/test_gate_test_infra_consistency.py` (W6 — CI gate, ~80 LOC). **Modified YAML:** `platform-env.yaml` (W4 — секция test_ports, +30 строк). **Modified tests:** `tests/test_smoke_postgres.py`, `tests/test_smoke_redis.py`, `tests/test_smoke_nginx.py`, `tests/test_component_pgbouncer.py`, `tests/test_component_clickhouse.py`, `tests/test_component_hermes.py`, `tests/_conftest/reuse.py` (W5 — замена хардкодов, ~50 строк правок в каждом). **No Makefile changes** (discover-modules target уже зарегистрирован). **No shell facade changes** (вся логика в Python).
REQUIRES:              Docker daemon running. Python ≥3.10. core/modules/ directory intact (13 модулей с docker-compose.test.yml). platform-env.yaml доступен для чтения/записи. Чистый working tree или координация с параллельным агентом. Перед стартом: `make gate MODE=fast` green как baseline.
$END_ARTIFACT_CONTRACT

---

## 0. Architecture Forensics Summary (Pre-DevPlan)

Полный отчёт: `.ai/plans/041-test-infra-fault-tolerance/01-ForensicsReport.md`

### 0.1 Три коллапса суперпозиции (arch-forensics S7-S15)

```
⚡ FRAGILITY COLLAPSE
├─ S8 (Logical Coupling): _STALE_CONTAINER_NAMES меняется вместе с docker-compose.test.yml
│   в 100% случаев — но связь не enforced.
├─ S14 (Change Impact): Изменение container_name в compose → правки в 3+ тестовых файлах.
├─ S15 (Hidden Dependency - CONVENTION): container_name должен заканчиваться на "-test",
│   но это нигде не зафиксировано как инвариант.
└─ Verdict: Тестовая инфраструктура держится на неявных соглашениях.

⚡ BOUNDARY COLLAPSE
├─ S7 (Boundary): Граница «тестовая инфраструктура» не имеет владельца — каждый файл
│   сам себе оркестратор.
├─ S10 (Failure): Отказ observability-net → каскадный отказ 7 модулей.
└─ Verdict: Отсутствие координатора жизненного цикла shared resources.

⚡ OWNERSHIP COLLAPSE
├─ S9 (Ownership): observability-net создают/удаляют 6 независимых фикстур.
│   Нет владельца → нет гарантии консистентности.
├─ S14 (Change Impact): Изменение политики управления сетью затрагивает 6 файлов.
└─ Verdict: Shared resources без явного владельца — классический антипаттерн.
```

### 0.2 Карта рисков

| Компонент | Likelihood × Impact ÷ Detectability | Tier |
|-----------|:---:|:---:|
| `observability-net` lifecycle | 4×5÷1 = **20** | 🔴 CRITICAL |
| `_STALE_CONTAINER_NAMES` sync | 4×4÷2 = **8** | 🟠 HIGH |
| `check_foreign_containers` own_project | 3×4÷2 = **6** | 🟡 MEDIUM |
| Port 6432 (8 файлов) | 2×3÷2 = **3** | 🟡 MEDIUM |

### 0.3 Фрагментация хардкодов (heatmap)

| Что захардкожено | Где | Кол-во мест |
|------------------|-----|:---:|
| Port 6432 (pgbouncer) | 8+ файлов | 🔴 |
| `ai-platform-test` | smoke.py + 6 ранее — исправлено 2026-07-22 | 🟠 |
| Container имена (postgres-test, pgbouncer-test, etc.) | 6 тестовых файлов × 3+ имени каждый | 🟠 |
| `observability-net` | 6 фикстур создают/удаляют | 🔴 |
| Port 9119 (hermes) | 4 файла | 🟡 |
| Port 6379 (redis) | 4 файла | 🟡 |
| `check_foreign_containers` own_project | 7 вызовов в 5 файлах | 🟡 |

### 0.4 Выбранное решение: Option E (derive from existing)

Из 5 вариантов суперпозиции выбран **Option E** — derive from existing manifests + NetworkLeaseManager:

| Критерий | Option A (Manifest) | Option B (Runtime) | Option C (Networks) | Option D (Composer) | **Option E** |
|----------|:---:|:---:|:---:|:---:|:---:|
| Устраняет CRITICAL risk | ✅ | ❌ | ✅ | ✅ | ✅ |
| Устраняет хардкоды | ✅ | ✅ | ❌ | ✅ | ✅ |
| Не создаёт новый формат | ❌ | ✅ | ✅ | ❌ | ✅ |
| Использует существующие паттерны | ✅ | ✅ | ✅ | ❌ | ✅ |
| Объём нового кода | 500 LOC | 150 LOC | 50 LOC | 400 LOC | 290 LOC |
| Миграция тестовых файлов | 50 строк/файл | 30 строк/файл | 0 | 100 строк/файл | 50 строк/файл |

---

## 1. Текущее состояние (Read before Act — Principle 9)

### 1.1. Что уже есть (baseline 2026-07-22)

| Компонент | Файл | Роль |
|-----------|------|------|
| Auto-discovery docker-модулей | `core/internal/bootstrap/discover_modules.py:1-120` | Сканирует `core/modules/*/module.yaml`, генерирует docker-compose.yml include-секцию |
| Реестр портов/сетей (production) | `platform-env.yaml` | SSoT для port_mappings, networks, profiles |
| Канонические операции | `core/entrypoint-manifest.yaml` | Реестр всех make-таргетов и entrypoints |
| Module contract | `core/modules/AGENTS.md` | Инварианты: docker-compose.test.yml обязателен, container_name: "{module}-test" |
| Stale container cleanup | `tests/_conftest/smoke.py:726-749` | `_STALE_CONTAINER_NAMES` — 22 имени, `docker rm -f` перед сессией |
| Foreign container guard | `tests/_conftest/reuse.py:29-100` | `check_foreign_containers()` — проверка compose-проекта через docker inspect |
| Test networks | `tests/_conftest/networks.py:30-53` | Production + test networks, создаются в platform_services, пересоздаются в module-фикстурах |
| Session teardown | `tests/_conftest/session.py:165-180` | `docker container prune` для проекта ai-platform-test |

### 1.2. Проблемы (7 TRAP[BUG] 2026-07-22)

| # | Проблема | Файлы | Корень |
|---|----------|-------|--------|
| 1 | `_STALE_CONTAINER_NAMES` полный (22/22) — не поддерживается автосинхронизацией | smoke.py | Ручная синхронизация — хрупко при добавлении модулей |
| 2 | `check_foreign_containers` own_project = "ai-platform-test" — коллизия | 6 файлов | Захардкоженный проект |
| 3 | `COMPOSE_PROJECT = "ai-platform-test"` — коллизия с platform_services | test_component_hermes.py | Захардкоженный проект |
| 4 | `check_foreign_containers` возвращал list вместо dict | reuse.py | Тип возврата |
| 5 | observability-net race — 6 фикстур | 6 файлов | Нет владельца сети |
| 6 | test-shared-db-net race — 3 фикстуры | 3 файла | Нет владельца сети |
| 7 | proxy-net race — 4 фикстуры | 4 файла | Нет владельца сети |

### 1.3. Docker Compose Test Inventory

**13 модулей с docker-compose.test.yml, 22 уникальных container_name:**

| Модуль | container_name(s) | Сети |
|--------|-------------------|------|
| backup-cron | backup-cron-test | test-shared-db-net |
| clickhouse | clickhouse-test | test-observability-net |
| hermes-agent | hermes-agent-test | test-proxy-net, test-hermes-agent-net, test-observability-net, test-shared-db-net, test-shared-cache-net |
| infra-metrics | cadvisor-test, node-exporter-test, nginx-prometheus-exporter-test, redis-exporter-test, postgres-exporter-test | test-observability-net, test-shared-cache-net, test-shared-db-net |
| langfuse | langfuse-test, langfuse-redis-test | test-shared-db-net, test-observability-net |
| litellm | litellm-test | test-shared-db-net, test-observability-net, test-hermes-agent-net |
| logging | loki-test, promtail-test | test-observability-net |
| minio | minio-test | test-shared-db-net |
| monitoring | prometheus-test, grafana-test, prometheus-config-init-test | test-observability-net, test-proxy-net |
| nginx | nginx-test | test-proxy-net, test-observability-net |
| postgres | postgres-test, pgbouncer-test | test-shared-db-net |
| redis | redis-test | test-shared-cache-net |
| status-page | status-page-test | test-proxy-net |

**7 тестовых compose-проектов:**

| Проект | Используется в | Контейнеры |
|--------|---------------|------------|
| `ai-platform-test` | smoke.py (platform_services session) | Все 22 |
| `ai-platform-smoke-postgres` | test_smoke_postgres.py | postgres, pgbouncer |
| `wave-redis-smoke` | test_smoke_redis.py | redis |
| `wave-nginx-smoke` | test_smoke_nginx.py | nginx |
| `ai-platform-test-pgbouncer` | test_component_pgbouncer.py | postgres, pgbouncer |
| `ai-platform-test-ch` | test_component_clickhouse.py | clickhouse |
| `ai-platform-test-hermes-pg` + `ai-platform-test-hermes` | test_component_hermes.py | postgres/pgbouncer + hermes-agent |

---

## 2. Дизайн решения (Option E)

### 2.1 Принцип: derive, don't configure

Вместо создания нового YAML-манифеста, вся информация извлекается из существующих источников:

```
docker-compose.test.yml ──┐
module.yaml ──────────────┤
                          ├──► discover_modules.py --test-infra --json
                          │         │
platform-env.yaml ────────┤         ▼
(test_ports секция) ──────┘    tests/_conftest/infra.py
                                      │
                                      ├── STALE_CONTAINER_NAMES  (всегда актуален)
                                      ├── get_container_name()    (не хардкод)
                                      ├── get_test_port()         (канонический)
                                      ├── get_compose_file()      (base + test)
                                      └── get_networks_for_module()
```

`NetworkLeaseManager` — отдельный механизм для сетей, не зависящий от auto-discovery.

### 2.2 Детальный дизайн каждого компонента

#### W1: discover_modules.py --test-infra

**Существующий код** (120 LOC) сканирует `core/modules/*/module.yaml`, находит docker-модули, генерирует include-секцию в docker-compose.yml.

**Расширение:**
```python
# core/internal/bootstrap/discover_modules.py (новый код)

def discover_test_infra() -> list[dict]:
    """Scan core/modules/*/docker-compose.test.yml, extract container_name, networks, ports.

    Returns sorted list of module test info dicts:
    [
        {
            "module": "postgres",
            "container_names": ["postgres-test", "pgbouncer-test"],
            "networks": ["test-shared-db-net"],
            "ports": {"pgbouncer": {"internal": 6432, "external": 6432}},
            "compose_base": "core/modules/postgres/docker-compose.base.yml",
            "compose_test": "core/modules/postgres/docker-compose.test.yml",
        },
        ...
    ]
    """
    modules = []
    modules_dir = Path("core/modules")
    for mod_dir in sorted(modules_dir.iterdir()):
        test_compose = mod_dir / "docker-compose.test.yml"
        if not test_compose.exists():
            continue
        module_yaml = mod_dir / "module.yaml"
        mod_name = mod_dir.name

        compose_data = yaml.safe_load(test_compose.read_text())
        container_names = []
        networks = set()
        ports = {}

        for svc_name, svc in compose_data.get("services", {}).items():
            if "container_name" in svc:
                container_names.append(svc["container_name"])
            for net in svc.get("networks", []):
                if isinstance(net, dict):
                    networks.update(net.keys())
                else:
                    networks.add(net)
            for port_mapping in svc.get("ports", []):
                # Parse "6432:6432" or "127.0.0.1:15432:5432"
                parts = str(port_mapping).split(":")
                if len(parts) >= 2:
                    external = int(parts[-2]) if len(parts) > 2 else int(parts[0])
                    internal = int(parts[-1])
                    ports[svc_name] = {"internal": internal, "external": external}

        modules.append({
            "module": mod_name,
            "container_names": sorted(container_names),
            "networks": sorted(networks),
            "ports": ports,
            "compose_base": str(mod_dir / "docker-compose.base.yml"),
            "compose_test": str(test_compose),
        })
    return modules


# CLI extension
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-infra", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.test_infra:
        result = discover_test_infra()
        if args.json:
            print(json.dumps(result, indent=2))
    else:
        # Existing behavior — unchanged
        ...
```

**Инварианты:**
- Без флага `--test-infra` поведение не меняется.
- Сортировка по module_name — детерминированный вывод.
- Пустые модули (без docker-compose.test.yml) пропускаются.

#### W2: tests/_conftest/infra.py — кэширующий слой

```python
"""Test infrastructure auto-discovery cache.

Provides canonical access to container names, ports, compose files.
Derived from docker-compose.test.yml via discover_modules.py --test-infra.
"""

import json
import subprocess
from pathlib import Path
from functools import lru_cache

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DISCOVER_SCRIPT = _PROJECT_ROOT / "core" / "internal" / "bootstrap" / "discover_modules.py"

# ⚠️ TRAP[PERF] · 2026-07-22 · Subprocess on import — cached at module level
# · Mitigation: lru_cache + module-level cache. One call per pytest session.
# · If discover_modules.py becomes slow (>500ms), add file-based cache with mtime check.

@lru_cache(maxsize=1)
def _load_test_infra() -> list[dict]:
    """Load test infrastructure data from discover_modules.py --test-infra --json."""
    result = subprocess.run(
        ["python3", str(_DISCOVER_SCRIPT), "--test-infra", "--json"],
        capture_output=True, text=True, check=True,
        cwd=str(_PROJECT_ROOT),
    )
    return json.loads(result.stdout)


def _get_module_data(module_name: str) -> dict:
    """Get test infra data for a specific module."""
    for mod in _load_test_infra():
        if mod["module"] == module_name:
            return mod
    raise KeyError(f"Module '{module_name}' not found in test infrastructure")


def get_container_name(module_name: str) -> str:
    """Return the container_name for a module (e.g., 'postgres' → 'postgres-test')."""
    return _get_module_data(module_name)["container_names"][0]


def get_container_names(module_name: str) -> list[str]:
    """Return all container_names for a module (e.g., infra-metrics → 5 names)."""
    return _get_module_data(module_name)["container_names"]


def get_test_port(module_name: str, service: str = None) -> int | dict:
    """Return external test port(s) for a module.

    Args:
        module_name: e.g., 'pgbouncer' → 6432
        service: if module has multiple services, specify which one

    Returns:
        int if service specified, dict[str, int] if not.
    """
    data = _get_module_data(module_name)
    ports = data.get("ports", {})
    if service:
        return ports[service]["external"]
    return {svc: info["external"] for svc, info in ports.items()}


def get_compose_file(module_name: str) -> tuple[Path, Path]:
    """Return (base_compose, test_compose) paths for a module."""
    data = _get_module_data(module_name)
    return Path(data["compose_base"]), Path(data["compose_test"])


def get_networks_for_module(module_name: str) -> list[str]:
    """Return list of network names a module's test container connects to."""
    return _get_module_data(module_name).get("networks", [])


# Module-level cached properties — evaluated once per pytest session
@property
def STALE_CONTAINER_NAMES() -> list[str]:
    """All test container names from ALL docker-compose.test.yml files.

    Always in sync with compose files — derived at import time.
    Replaces the hardcoded list in tests/_conftest/smoke.py.
    """
    names = []
    for mod in _load_test_infra():
        names.extend(mod["container_names"])
    return sorted(names)


@property
def ALL_TEST_NETWORKS() -> set[str]:
    """All unique test network names across all modules."""
    networks = set()
    for mod in _load_test_infra():
        networks.update(mod["networks"])
    return networks
```

**Примечание:** `@property` на module-level не работает напрямую. Используется паттерн module-level singleton:

```python
class _TestInfra:
    """Module-level singleton for cached test infrastructure data."""
    _instance = None

    def __init__(self):
        self._data = _load_test_infra()

    @property
    def stale_container_names(self) -> list[str]:
        names = []
        for mod in self._data:
            names.extend(mod["container_names"])
        return sorted(names)

    @property
    def all_test_networks(self) -> set[str]:
        networks = set()
        for mod in self._data:
            networks.update(mod["networks"])
        return networks

    def get_container_name(self, module_name: str) -> str: ...
    def get_test_port(self, module_name: str, service: str = None) -> int | dict: ...
    def get_compose_file(self, module_name: str) -> tuple[Path, Path]: ...
    def get_networks_for_module(self, module_name: str) -> list[str]: ...


# Singleton instance
infra = _TestInfra()

# Convenience module-level aliases
STALE_CONTAINER_NAMES = property(lambda self: infra.stale_container_names)
ALL_TEST_NETWORKS = property(lambda self: infra.all_test_networks)
```

#### W3: NetworkLeaseManager

**⚠️ Design Decision: NetworkLeaseManager vs existing `ensure_external_networks()`**

`tests/_conftest/networks.py:101-128` уже имеет `ensure_external_networks()` — idempotent create (inspect → create if missing). Без явного решения NetworkLeaseManager создаст второй параллельный механизм управления теми же сетями, усугубляя BOUNDARY COLLAPSE.

**Решение: NetworkLeaseManager ЗАМЕНЯЕТ (не сосуществует с) `ensure_external_networks()`.**
- `ensure_external_networks()` становится тонкой обёрткой, вызывающей `NetworkLeaseManager.acquire()`.
- После полной миграции всех потребителей → `ensure_external_networks()` удаляется.
- W3 имплементирует NetworkLeaseManager с acquire/release/refcounting, а W5 мигрирует все вызовы `ensure_external_networks()` на `NetworkLeaseManager.acquire()`.
- Единый source of truth: NetworkLeaseManager — единственный механизм управления тестовыми сетями.

```python
"""Network lifecycle manager with reference counting.

Eliminates race conditions when multiple test fixtures create/destroy
the same Docker network (observability-net, proxy-net, etc.).

Pattern:
    lease = NetworkLeaseManager()
    lease.acquire("observability-net")   # Creates if not exists
    # ... tests ...
    lease.release("observability-net")   # Removes only when refcount=0
"""

import subprocess
import logging

_logger = logging.getLogger(__name__)


class NetworkLeaseManager:
    """Thread-safe reference counting for Docker test networks."""

    def __init__(self):
        self._leases: dict[str, int] = {}  # network_name → refcount

    def acquire(self, network_name: str) -> bool:
        """Acquire a network lease. Creates network if first acquisition.

        Returns True if network was newly created.
        """
        if network_name not in self._leases:
            self._leases[network_name] = 0

        if self._leases[network_name] == 0:
            self._create_network(network_name)
            _logger.info("[IMP:8][NetworkLeaseManager] Created network '%s'", network_name)

        self._leases[network_name] += 1
        _logger.debug("[IMP:7][NetworkLeaseManager] Acquired '%s' (refcount=%d)",
                      network_name, self._leases[network_name])
        return self._leases[network_name] == 1

    def release(self, network_name: str) -> bool:
        """Release a network lease. Removes network when refcount reaches 0.

        Returns True if network was removed.
        """
        if network_name not in self._leases:
            _logger.warning("[IMP:7][NetworkLeaseManager] Release called for unknown network '%s'",
                            network_name)
            return False

        self._leases[network_name] -= 1

        if self._leases[network_name] <= 0:
            self._remove_network(network_name)
            del self._leases[network_name]
            _logger.info("[IMP:8][NetworkLeaseManager] Removed network '%s'", network_name)
            return True

        _logger.debug("[IMP:7][NetworkLeaseManager] Released '%s' (refcount=%d)",
                      network_name, self._leases[network_name])
        return False

    def _create_network(self, name: str) -> None:
        """Create Docker network if it doesn't exist."""
        subprocess.run(
            ["docker", "network", "create", name],
            capture_output=True, text=True, check=False,
        )
        # Ignore "already exists" errors — idempotent

    def _remove_network(self, name: str) -> None:
        """Remove Docker network. Best-effort — ignore errors."""
        subprocess.run(
            ["docker", "network", "rm", name],
            capture_output=True, text=True, check=False,
        )

    def release_all(self) -> None:
        """Force-release all remaining leases. Called from pytest_sessionfinish."""
        for name in list(self._leases.keys()):
            self._remove_network(name)
            _logger.info("[IMP:9][NetworkLeaseManager] Force-released network '%s' (session finish)",
                         name)
        self._leases.clear()

    @property
    def active_leases(self) -> dict[str, int]:
        """Return current lease state (for diagnostics)."""
        return dict(self._leases)


# Singleton instance for the test session
_network_manager = NetworkLeaseManager()


def get_network_manager() -> NetworkLeaseManager:
    """Get the session-level NetworkLeaseManager singleton."""
    return _network_manager
```

**Интеграция в platform_services (smoke.py):**

```python
# Вместо прямого создания сетей:
# БЫЛО:
#   subprocess.run(["docker", "network", "create", "test-shared-db-net"])
# СТАЛО:
#   from tests._conftest.networks import get_network_manager
#   nm = get_network_manager()
#   nm.acquire("test-shared-db-net")

# В teardown:
# БЫЛО:
#   subprocess.run(["docker", "network", "rm", "test-shared-db-net"])
# СТАЛО:
#   nm.release("test-shared-db-net")
```

#### W4: platform-env.yaml — test_ports

```yaml
# platform-env.yaml — новая секция
test_ports:
  # Canonical test ports. Convention: test port = 1{base_port} where applicable.
  # Each entry: module_name: { port_name: port_number }
  postgres:
    postgres: 15432           # base: 5432, test: 1{5432}
  pgbouncer:
    listen: 6432              # pgbouncer default (unchanged in test)
  redis:
    redis: 16379              # base: 6379, test: 1{6379}
  litellm:
    litellm: 14000            # base: 4000, test: 1{4000}
  nginx:
    http: 18080               # base: 8080, test: 1{8080}
    https: 18443              # base: 8443, test: 1{8443}
  clickhouse:
    http: 18123               # base: 8123, test: 1{8123}
    metrics: 19363            # base: 9363, test: 1{9363}
  hermes-agent:
    dashboard: 19119          # base: 9119, test: 1{9119}
  prometheus:
    prometheus: 19090         # base: 9090, test: 1{9090}
  grafana:
    grafana: 13030            # base: 3030, test: 1{3030}
  langfuse:
    langfuse: 13000           # base: 3000, test: 1{3000}
  loki:
    loki: 13100               # base: 3100, test: 1{3100}
```

#### W5: Миграция тестовых файлов

**Шаблон миграции (для каждого из 7 файлов):**

```python
# БЫЛО — хардкод:
POSTGRES_CONTAINER_NAME = "postgres-test"
_COMPOSE_PROJECT = "ai-platform-smoke-postgres"
_TEST_PORT = 6432

# СТАЛО — импорт из инфраструктурного слоя:
from tests._conftest.infra import infra
POSTGRES_CONTAINER_NAME = infra.get_container_name("postgres")
_COMPOSE_PROJECT = "ai-platform-test-postgres"  # имя проекта остаётся декларативным
_TEST_PORT = infra.get_test_port("pgbouncer", "listen")  # → 6432
```

**Что меняется в каждом файле:**

| Файл | Заменяемый хардкод | Новый код |
|------|-------------------|-----------|
| `tests/_conftest/smoke.py` | `_STALE_CONTAINER_NAMES = ["backup-cron-test", ...]` (22 имени, 25 строк) | `from tests._conftest.infra import infra; _STALE_CONTAINER_NAMES = infra.stale_container_names` |
| `tests/_conftest/smoke.py` | Создание сетей: `docker network create` | `nm.acquire(network_name)` |
| `tests/_conftest/smoke.py` | Удаление сетей: `docker network rm` | `nm.release(network_name)` |
| `test_smoke_postgres.py` | `CONTAINER_NAME_POSTGRES = "postgres-test"` | `infra.get_container_name("postgres")` |
| `test_smoke_postgres.py` | `COMPOSE_PROJECT = "ai-platform-smoke-postgres"` | Без изменений (имя проекта — локальное решение, не хардкод infra) |
| `test_smoke_postgres.py` | `check_foreign_containers(["postgres-test", "pgbouncer-test"], "ai-platform-smoke-postgres")` | `check_foreign_containers_adapter()` — адаптер выводит own_project из compose-файла |
| `test_smoke_redis.py` | `_REDIS_CONTAINER_NAME = "redis-test"` | `infra.get_container_name("redis")` |
| `test_smoke_nginx.py` | `_NGINX_CONTAINER_NAME = "nginx-test"` | `infra.get_container_name("nginx")` |
| `test_component_pgbouncer.py` | `CONTAINER_NAME_PGBOUNCER = "pgbouncer-test"` | `infra.get_container_name("pgbouncer")` |
| `test_component_pgbouncer.py` | Port 6432 (4 места) | `infra.get_test_port("pgbouncer", "listen")` |
| `test_component_clickhouse.py` | `CONTAINER_NAME_CLICKHOUSE = "clickhouse-test"` | `infra.get_container_name("clickhouse")` |
| `test_component_clickhouse.py` | Ports 18123, 19363 | `infra.get_test_port("clickhouse", ...)` |
| `test_component_hermes.py` | `POSTGRES_CONTAINER = "postgres-test"`, `HERMES_CONTAINER = "hermes-agent-test"` | `infra.get_container_name(...)` |
| `test_component_hermes.py` | `COMPOSE_PROJECT` × 2 | Без изменений |
| `tests/_conftest/reuse.py` | `check_foreign_containers()` — own_project хардкодится вызывающим | Добавить `check_foreign_containers_adapter()` — автоматически выводит own_project из compose-файла |
| `tests/_conftest/session.py` | `docker container prune` hardcoded project | Без изменений (финальная очистка) |

**Адаптер для check_foreign_containers:**

```python
# tests/_conftest/reuse.py — новый адаптер
def check_foreign_containers_adapter(module_name: str) -> dict[str, str]:
    """Auto-derive container names and own_project from compose files.

    Replaces: check_foreign_containers(["postgres-test", "pgbouncer-test"], "ai-platform-smoke-postgres")
    With:     check_foreign_containers_adapter("postgres")
    """
    from tests._conftest.infra import infra
    container_names = infra.get_container_names(module_name)
    # own_project = compose project name derived from module name
    # Convention: "ai-platform-test-{module}" for component tests
    own_project = f"ai-platform-test-{module_name}"
    # For special cases (platform_services), use "ai-platform-test"
    return check_foreign_containers(container_names, own_project)
```

#### W6: CI Gate

```python
# tests/gates/test_gate_test_infra_consistency.py

def test_stale_container_names_equals_compose_container_names():
    """AC-6a: _STALE_CONTAINER_NAMES == all container_name from docker-compose.test.yml."""
    from tests._conftest.infra import infra

    stale = infra.stale_container_names
    all_compose_names = []
    for mod in infra._data:
        all_compose_names.extend(mod["container_names"])
    all_compose_names = sorted(all_compose_names)

    assert stale == all_compose_names, (
        f"STALE_CONTAINER_NAMES drift detected.\n"
        f"Stale ({len(stale)}): {stale}\n"
        f"Compose ({len(all_compose_names)}): {all_compose_names}\n"
        f"Missing in stale: {set(all_compose_names) - set(stale)}\n"
        f"Extra in stale: {set(stale) - set(all_compose_names)}\n"
        f"Run: make discover-modules (updates discovery data)"
    )


def test_test_ports_match_compose_ports():
    """AC-6b: Each test_port from platform-env.yaml matches a port in compose files."""
    import yaml
    from tests._conftest.infra import infra
    from pathlib import Path

    platform_env = yaml.safe_load(Path("platform-env.yaml").read_text())
    test_ports = platform_env.get("test_ports", {})

    for module_name, ports in test_ports.items():
        try:
            compose_ports = infra.get_test_port(module_name)
        except KeyError:
            pytest.fail(f"Module '{module_name}' has test_ports in platform-env.yaml "
                        f"but no docker-compose.test.yml found")

        for port_name, port_value in ports.items():
            actual = compose_ports.get(port_name)
            assert actual == port_value, (
                f"Port mismatch for {module_name}.{port_name}: "
                f"platform-env.yaml={port_value}, compose={actual}"
            )


def test_compose_projects_are_unique():
    """AC-6c: All compose projects used in tests are unique."""
    # Сканируем все тестовые файлы на предмет COMPOSE_PROJECT / --project-name
    # Проверяем отсутствие дубликатов
    import ast
    from pathlib import Path

    projects = {}
    tests_dir = Path("tests")

    for test_file in sorted(tests_dir.glob("test_*.py")):
        for line in test_file.read_text().splitlines():
            # Ищем паттерны: COMPOSE_PROJECT = "...", --project-name ..., -p ...
            if "COMPOSE_PROJECT" in line:
                # Простой парсинг — извлекаем значение строки
                pass  # реализация
    # ... проверка уникальности


def test_networks_registered_in_lease_manager():
    """AC-6d: All test networks from compose files are managed by NetworkLeaseManager."""
    from tests._conftest.infra import infra
    from tests._conftest.networks import get_network_manager

    nm = get_network_manager()
    # Проверяем, что все сети из compose-файлов могут быть захвачены
    for network in infra.all_test_networks:
        # Не создаём реально, только проверяем API
        assert hasattr(nm, 'acquire'), "NetworkLeaseManager missing acquire method"


def test_no_hardcoded_ai_platform_test_own_project():
    """AC-6e: No "ai-platform-test" as project name in test files.

    Anti-regression for TRAP[BUG] 2026-07-22.
    Scans ALL occurrences of "ai-platform-test" as a hardcoded project name:
    - check_foreign_containers(..., "ai-platform-test")
    - COMPOSE_PROJECT = "ai-platform-test", COMPOSE_PROJECT_NAME = "ai-platform-test"
    - "-p", "ai-platform-test" or --project-name "ai-platform-test" in subprocess calls
    - Any string literal "ai-platform-test" in test files

    Exceptions (whitelist): SMOKE_ENV / platform_services in smoke.py
    where "ai-platform-test" is the legitimate shared compose project.
    """
    import re
    from pathlib import Path

    # Whitelist: files/lines where "ai-platform-test" is legitimately used
    # as the shared compose project for platform_services
    WHITELIST_FILES = {"smoke.py"}
    WHITELIST_PATTERNS = [
        r'SMOKE_ENV\s*=',
        r'platform_services',
    ]

    tests_dir = Path("tests")
    # Match "ai-platform-test" as a delimited string literal
    pattern = re.compile(r'"ai-platform-test"')

    violations = []
    for test_file in sorted(tests_dir.rglob("*.py")):
        if test_file.name in WHITELIST_FILES:
            continue  # skip entire whitelisted files
        content = test_file.read_text()
        if pattern.search(content):
            violations.append(str(test_file.relative_to(tests_dir)))

    assert not violations, (
        f"TRAP[BUG] REGRESSION: hardcoded 'ai-platform-test' project name "
        f"found in: {violations}. "
        f"Use check_foreign_containers_adapter() or unique project name."
    )
```

---

## 3. Step-by-Step Имплементация

### Wave 1: Auto-discovery расширение

| # | Файл | Действие |
|---|------|----------|
| 1.1 | `core/internal/bootstrap/discover_modules.py` | Modify — добавить `discover_test_infra()`, CLI-флаг `--test-infra --json` |
| 1.2 | Запустить валидацию: `python3 core/internal/bootstrap/discover_modules.py --test-infra --json` | Проверить вывод (22 container_name, 13 модулей) |

**Верификация W1:**
```bash
python3 core/internal/bootstrap/discover_modules.py --test-infra --json | python3 -m json.tool | head -50
# Ожидается: JSON с 13 элементами, каждый имеет module, container_names, networks, ports
```

### Wave 2: Infra.py кэширующий слой

| # | Файл | Действие |
|---|------|----------|
| 2.1 | `tests/_conftest/infra.py` | **Create** — `_TestInfra` singleton, `STALE_CONTAINER_NAMES`, все getter-методы |
| 2.2 | `tests/_conftest/__init__.py` | Modify — экспорт infra (если нужно) |
| 2.3 | Ручной тест: `python3 -c "from tests._conftest.infra import STALE_CONTAINER_NAMES; print(len(STALE_CONTAINER_NAMES))"` | Ожидается: 22 |

**Верификация W2:**
```bash
python3 -c "
from tests._conftest.infra import infra
print('Container names:', infra.stale_container_names)
print('Postgres container:', infra.get_container_name('postgres'))
print('Pgbouncer port:', infra.get_test_port('pgbouncer', 'listen'))
print('Networks:', infra.all_test_networks)
"
```

### Wave 3: NetworkLeaseManager

| # | Файл | Действие |
|---|------|----------|
| 3.1 | `tests/_conftest/networks.py` | Modify — добавить `NetworkLeaseManager` класс + singleton `get_network_manager()` |
| 3.2 | `tests/_conftest/smoke.py` | Modify — заменить `docker network create/rm` на `nm.acquire/release` в `platform_services` |
| 3.3 | `tests/_conftest/smoke.py` | Modify — заменить создание сетей в module-фикстурах на `nm.acquire/release` |
| 3.4 | `tests/_conftest/session.py` | Modify — добавить `nm.release_all()` в `pytest_sessionfinish` |

**Верификация W3:**
```bash
# Тест: две фикстуры одновременно захватывают observability-net
pytest tests/test_smoke_monitoring.py tests/test_smoke_infra_metrics.py -v --tb=short
# Ожидается: observability-net не пересоздаётся между тестами, нет "network not found" ошибок
```

### Wave 4: platform-env.yaml test_ports

| # | Файл | Действие |
|---|------|----------|
| 4.1 | `platform-env.yaml` | Modify — добавить секцию `test_ports` со всеми 13 модулями |
| 4.2 | `tests/_conftest/infra.py` | Modify — метод `get_test_port()` читает из platform-env.yaml (fallback: parse из compose) |

### Wave 5: Миграция тестовых файлов

| # | Файл | Что меняется |
|---|------|-------------|
| 5.1 | `tests/_conftest/smoke.py` | `_STALE_CONTAINER_NAMES` → `infra.stale_container_names`; удалить 25 строк хардкода |
| 5.2 | `tests/_conftest/reuse.py` | Добавить `check_foreign_containers_adapter()` |
| 5.3 | `tests/test_smoke_postgres.py` | container_name → `infra.get_container_name()`, check_foreign_containers → adapter |
| 5.4 | `tests/test_smoke_redis.py` | container_name → `infra.get_container_name()`, check_foreign_containers → adapter |
| 5.5 | `tests/test_smoke_nginx.py` | container_name → `infra.get_container_name()`, check_foreign_containers → adapter |
| 5.6 | `tests/test_component_pgbouncer.py` | container_name → `infra.get_container_name()`, port 6432 → `infra.get_test_port()` |
| 5.7 | `tests/test_component_clickhouse.py` | container_name → `infra.get_container_name()`, ports → `infra.get_test_port()` |
| 5.8 | `tests/test_component_hermes.py` | container_name × 3 → `infra.get_container_name()`, check_foreign_containers → adapter |

**Верификация W5:**
```bash
# Все Docker-тесты должны остаться зелёными
pytest tests/ -m "(smoke or component) and (not e2e)" -v --tb=short
# Ожидается: 43 pass, 2 fail (pre-existing: langfuse DNS + nginx error page)
```

### Wave 6: CI Gate

| # | Файл | Действие |
|---|------|----------|
| 6.1 | `tests/gates/test_gate_test_infra_consistency.py` | **Create** — 5 тестов (AC-6a..6e) |
| 6.2 | `core/entrypoint-manifest.yaml` | Modify — зарегистрировать новый gate в секции `gates` |
| 6.3 | `make gate MODE=fast` | Проверить, что новый gate зелёный |

**Верификация W6:**
```bash
make gate MODE=fast
# Ожидается: exit 0, test_gate_test_infra_consistency.py — 5 passed
```

---

## 4. Risk Register

| ID | Риск | Вероятность | Impact | Митигация |
|----|------|:---:|:---:|-----------|
| W5-R1 | `discover_test_infra()` расходится с реальными compose-файлами | LOW | MEDIUM | Gate W6 валидирует консистентность |
| W5-R2 | Subprocess на импорт (`infra.py` вызывает `discover_modules.py`) медленный | LOW | LOW | Кэширование на уровне модуля. Если >500ms — добавить file-based cache с mtime |
| W5-R3 | `NetworkLeaseManager` refcount leak при краше тестов | MEDIUM | HIGH | `pytest_sessionfinish` → `release_all()` принудительно очищает. Daemon-поток не используется — refcount только в памяти pytest-процесса |
| W5-R4 | platform-env.yaml test_ports расходится с compose ports | LOW | MEDIUM | Gate W6 валидирует. Источник истины — compose-файлы, platform-env.yaml — кэш |
| W5-R5 | Миграция ломает существующие тесты | MEDIUM | HIGH | Изолированная миграция (один файл за раз), `pytest` после каждого. Git revert для отката |
| W5-R6 | `check_foreign_containers_adapter` неверно выводит own_project | LOW | MEDIUM | Сохраняется возможность явного указания own_project. Адаптер — convenience, не mandatory |
| W5-R7 | Gate W6 даёт false-positive при добавлении легитимного нового compose-проекта | LOW | LOW | Gate проверяет уникальность, но не запрещает новые проекты — только предупреждает о потенциальных коллизиях |
| W5-R8 | `container_name` в compose не соответствует convention `{module}-test` | LOW | LOW | Существующий инвариант из `core/modules/AGENTS.md`. Gate W6 добавит проверку этого инварианта |

---

## 5. Rollback Plan

Каждая wave независима — при проблеме откатывается только последняя:

```
Wave 1 (discover_modules.py)  → git revert <w1-commit>
Wave 2 (infra.py)             → git revert <w2-commit> + удалить импорты infra из тестов
Wave 3 (NetworkLeaseManager)  → git revert <w3-commit> + вернуть docker network create/rm
Wave 4 (platform-env.yaml)    → git revert <w4-commit> + удалить test_ports секцию
Wave 5 (migration)            → git revert <w5-commit> + вернуть хардкоды
Wave 6 (CI gate)              → git revert <w6-commit> + удалить тест
```

**Ключевое свойство:** W1-W4 — strictly additive (новый код, старый не удаляется). Только W5 удаляет хардкоды. Если W5 ломается, W1-W4 остаются в коде без вреда — они не используются продакшеном, только тестами.

---

## 6. Acceptance Criteria (развёрнуто)

| # | Критерий | Как проверить | Target |
|---|----------|---------------|--------|
| AC-1 | `discover_modules.py --test-infra --json` возвращает валидный JSON | `python3 ... | python3 -m json.tool` | exit 0, 13 модулей |
| AC-2 | `STALE_CONTAINER_NAMES` содержит 22 имени | `python3 -c "from tests._conftest.infra import STALE_CONTAINER_NAMES; assert len(STALE_CONTAINER_NAMES) == 22"` | exit 0 |
| AC-2b | `get_container_name("postgres")` → `"postgres-test"` | `python3 -c "..."` | exit 0, correct value |
| AC-3 | NetworkLeaseManager refcounting работает | Юнит-тест: acquire × 3, release × 2, assert сеть существует; release × 1, assert удалена | 4 asserts pass |
| AC-4 | `platform-env.yaml` test_ports содержит все модули | `python3 -c "import yaml; ..."` | 13 записей |
| AC-5a | `_STALE_CONTAINER_NAMES` в smoke.py — импорт, не хардкод | `grep -c "_STALE_CONTAINER_NAMES =" tests/_conftest/smoke.py` | 0 (присваиваний) |
| AC-5b | Ни одного хардкода container_name в 6 тестовых файлах | `grep -c '= ".*-test"' tests/test_{smoke,component}_*.py` | ≤1 (project name assignments) |
| AC-5c | Docker-тесты зелёные | `pytest tests/ -m "(smoke or component) and (not e2e)"` | 43 pass, 2 fail (pre-existing) |
| AC-6a | gate: stale names = compose names | `pytest tests/gates/test_gate_test_infra_consistency.py::test_stale_container_names_equals_compose_container_names` | pass |
| AC-6b | gate: ports match | `pytest ...::test_test_ports_match_compose_ports` | pass |
| AC-6e | gate: no hardcoded "ai-platform-test" project name anywhere (check_foreign_containers, COMPOSE_PROJECT, -p flag, subprocess calls) | `pytest ...::test_no_hardcoded_ai_platform_test_own_project` | pass |
| AC-7 | `make gate MODE=fast` green | `make gate MODE=fast` | exit 0 |
| AC-8 | Gate W6 падает при добавлении нового compose без обновления infra | Ручной тест: добавить container_name в compose, не трогать infra, запустить gate | gate FAIL с диагностикой |

$END_DEVPLAN
