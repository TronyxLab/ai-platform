# Compose Mounts Mapping (DevPlan 123 T10, FL2)

<!-- GREP_SUMMARY: compose-mounts-mapping, FL2, bind-mounts, macos-compat, env-parametrization, driver_opts, volumes -->
<!-- STRUCTURE: ┌root compose bind-volumes┐ → ◇ module base.yml env-параметризованные маунты → ◇ module relative bind-mounts → ⊕ macOS-статус → ⎋ правила -->

## $ARTIFACT_CONTRACT (payload-документ, не канонический артефакт)

| Поле | Значение |
|------|----------|
| PURPOSE | Полная таблица compose-маунтов платформы + статус macOS-совместимости (FL2, DevPlan 123 T10) |
| DESCRIPTION | Все bind-маунты root docker-compose.yml и модульных docker-compose.base.yml: файл, source, target, механизм резолюции, macOS-статус, dev-оверрайд |
| RATIONALE | Верификация RC-121: 9/13 false-lead рекомендаций не выполнены; FL2 (mapping маунтов) закрывает класс «жёсткие дефолты /var/lib/platform/*, /opt/* для macOS» |
| ACCEPTANCE_CRITERIA | Каждый маунт имеет строку с резолюцией и macOS-статусом; новые модули следуют контракту env-параметризации (core/modules/AGENTS.md «Compose-include контракт») |

---

## 1. Root docker-compose.yml — bind-тома (driver_opts, DevPlan 116 B3 T4 U-49)

Единственный SoT томов (гейт volumes_sot). Bind через `driver_opts: {type: none, o: bind, device: <host-path>}`.

| Volume | device (host source) | Target (в контейнере) | Резолюция | macOS-статус | Dev-оверрайд |
|--------|----------------------|----------------------|-----------|--------------|--------------|
| `postgres-data` | `/var/lib/platform/postgres-data` | pg data dir | литерал driver_opts | ⚠️ host-путь отсутствует; Docker Desktop создаёт в VM (данные в VM, не в хост-ФС) | нет (данные в Docker Desktop VM) |
| `wal-archive` | `/var/lib/platform/wal-archive` | pg WAL | литерал driver_opts | ⚠️ то же | нет |
| `backup-spool` | `/var/lib/platform/backup-spool` | spool | литерал driver_opts | ⚠️ то же | нет |
| `backup-logs` | `/var/log/platform/backup` | backup logs | литерал driver_opts | ⚠️ то же | нет |
| `hermes-data` | `/var/lib/platform/hermes-agent/data` | hermes state | литерал driver_opts | ⚠️ то же | нет |
| `grafana-data` | docker-managed volume | — | driver:local | ✅ | нет |
| `prometheus-data` | docker-managed volume | — | driver:local | ✅ | нет |
| `loki-data` | docker-managed volume | — | driver:local | ✅ | нет |
| `clickhouse-data` | docker-managed volume | — | driver:local | ✅ | нет |
| `minio-data` | docker-managed volume | — | driver:local | ✅ | нет |
| `langfuse-redis-data` | docker-managed volume | — | driver:local | ✅ | нет |
| `prometheus-config-gen` | docker-managed volume | — | driver:local | ✅ | нет |

**macOS-примечание:** bind-тома `/var/lib/platform/*` на macOS резолвятся ВНУТРИ Docker Desktop VM (bind на VM-путь, которого нет в хост-ФС). Для локальной разработки это работает (данные переживают контейнеры, теряются при сбросе Docker Desktop). Для хост-доступа нужен env-параметризация по образцу п.2 — НЕ выполнена по U-49 контракту (root SoT) + локальный 21/21 healthy.

## 2. Модульные base.yml — env-параметризованные хост-маунты (канон `${VAR:-default}`)

| Файл | Source (env-выражение) | Target | Дефолт (прод) | macOS-статус | Dev-оверрайд (.env) |
|------|------------------------|--------|---------------|--------------|---------------------|
| `core/modules/status-page/docker-compose.base.yml` | `${STATUS_METRICS_JSON:-/run/platform/status-metrics.json}` | `/run/platform/status-metrics.json:ro` | `/run/platform/status-metrics.json` (tmpfs) | ✅ параметризован (прецедент RC-121) | `.env` — локальный путь |
| `core/modules/status-page/docker-compose.base.yml` | `${NODE_CONFIGS_DIR:-/opt/node-configs}/${NODE_NAME:-unknown}/node.yaml` | `/opt/node-configs/.../node.yaml:ro` | `/opt/node-configs` | ⚠️ дефолт /opt отсутствует | `.env:18 NODE_CONFIGS_DIR=<repo>/node-configs` ✅ |
| `core/modules/monitoring/docker-compose.base.yml` | `${PROMETHEUS_TARGETS_DIR:-/opt/platform/prometheus-targets}` | `/prometheus-targets:ro` | `/opt/platform/prometheus-targets` | ⚠️ дефолт /opt отсутствует | `.env:145 /tmp/prometheus-targets` ✅ |
| `core/modules/monitoring/docker-compose.base.yml` | `${PROMETHEUS_RULES_DIR:-/opt/platform/prometheus-rules}` | `/opt/platform/prometheus-rules:ro` | `/opt/platform/prometheus-rules` | ⚠️ дефолт /opt отсутствует | `.env:146 /tmp/prometheus-rules` ✅ |

## 3. Модульные base.yml — относительные bind-маунты (колокализация, FL1)

| Файл | Source | Target | Резолюция | macOS-статус |
|------|--------|--------|-----------|--------------|
| `core/modules/status-page/docker-compose.base.yml` | `./app.py` | `/app/app.py:ro` | от include-файла (директория модуля) | ✅ колокализован |
| `core/modules/status-page/docker-compose.base.yml` | `./templates/` | `/app/templates/:ro` | от include-файла (директория модуля) | ✅ колокализован |

## 4. Правила (обязательные для новых модулей)

1. Литералы `/var/lib/platform/*`, `/opt/*`, `/var/log/platform/*` в bind-источниках нового модульного base.yml — **запрещены** (нарушение Compose-include контракта, core/modules/AGENTS.md).
2. Хост-пути вне директории модуля — **только** `${VAR:-default}` + запись в `.env.example` (generated из platform-infra.yaml env_defaults) и platform-env.yaml `env_defaults` при прод-необходимости.
3. Колокализуемые bind-источники (`./app.py`) остаются относительными — резолюция от include-файла.
4. Root docker-compose.yml — единственный SoT томов (volumes_sot gate); модульные top-level volumes — только `-test` оверрайды (канон volume-rename U-62).
