# DevPlan 059 — META: Status Page Enhancement (суперпозиционный синтез)

<!-- $ARTIFACT_CONTRACT
  PURPOSE:               МЕТА-ДЕВПЛАН, замещающий 046-DevPlan-status-page-enhancement.md.
                          Инкорпорирует 20+ улучшений от 4 независимых ревью-моделей, результаты опроса оператора
                          и канонические архитектурные ограничения платформы.
  DESCRIPTION:           Модульный Python-экспортёр метрик (cron на хосте) → единый status-metrics.json (TTL-кэш)
                          → read-only mount → Jinja2 status-page. cryptography.x509 вместо openssl, docker stats
                          вместо docker inspect для usage-метрик, атомарная запись JSON, schema_version, graceful
                          degradation, cron flock, batch docker-вызовы, timeout на все subprocess.
  RATIONALE:             Исходный DevPlan 046 получил 4 независимых ревью (модели оценили на 8.0–9.0/10).
                          Вскрыто 4 блокера (docker inspect≠usage, race condition записи, du -sh human-parse,
                          отсутствие schema_version) и 16 важных улучшений. МЕТА-план коллапсирует суперпозицию
                          в единый непротиворечивый дизайн.
  ACCEPTANCE_CRITERIA:
    AC1-M:  Таблица Domains: issuer, expiry (ISO 8601), SAN (первые 5 + tooltip), code_size, image_size
    AC2-M:  Таблица Containers: name, domain(s), status, CPU%, memory (used/limit, цвет >90%), image + size
    AC3-M:  /health 200 PASS / 503 FAIL — контракт неизменен
    AC4-M:  /status.json включает certs, projects, host (+ schema_version: 2)
    AC5-M:  make gate MODE=fast зелёный (без регрессий)
    AC6-M:  platform_export_metrics.py корректно обрабатывает wildcard-сертификаты (cryptography.x509 SAN match)
    AC7-M:  Экспортёр не падает при отсутствии docker/certs — возвращает partial JSON с errors[]
    AC8-M:  Цветовая индикация: красный <7 дней (сертификат)/>90% (диск), жёлтый <30 дней/>80%
    AC9-M:  Атомарная запись JSON (tmpfile + os.replace) — status-page никогда не видит полусформированный файл
    AC10-M: Экспортёр завершается <15s (host-side), включая batch docker inspect + docker stats
    AC11-M: Экспортёр не накладывается сам на себя (flock на lockfile, timeout 50s)
    AC12-M: Поля container_name → name: контракт сломан осознанно, grep-аудит всех потребителей выполнен
    AC13-M: schema_version: 2 в корне JSON, status-page проверяет версию при чтении
    AC14-M: Jinja2 autoescape включён (select_autoescape), XSS-векторы закрыты
    AC15-M: du -sb + mtime-кэш: размеры проектов пересчитываются раз в час или при изменении mtime
  IMPLEMENTS:            User request "Доработка статус страницы" (2026-07-23) + 4-model review synthesis
  SUPERSEDES:             046-DevPlan-status-page-enhancement.md
  IMPACTS:
    CREATE:
      - core/internal/healthcheck/metrics/__init__.py                 (package init)
      - core/internal/healthcheck/metrics/docker_collector.py         (containers + images + resources)
      - core/internal/healthcheck/metrics/cert_collector.py           (SSL certificates via cryptography.x509)
      - core/internal/healthcheck/metrics/project_collector.py        (code sizes via du -sb + mtime cache)
      - core/internal/healthcheck/metrics/host_collector.py           (disk usage via shutil)
      - core/internal/healthcheck/metrics/json_writer.py              (atomic write + schema_version)
      - core/internal/healthcheck/platform_export_metrics.py          (coordinator: сбор + TTL-кэш + main)
      - core/internal/healthcheck/platform-export-metrics.sh          (bash-обёртка <25 строк)
      - core/modules/status-page/templates/status.html                 (Jinja2-шаблон, 3 таблицы + кнопка обновления)
    DELETE:
      - core/internal/healthcheck/docker-healthcheck.sh               (заменён, см. §Миграция)
    MODIFY:
      - core/modules/status-page/app.py                                (удаление inline HTML, Jinja2 render, metrics loading, schema check)
      - core/modules/status-page/Dockerfile                            (Jinja2 + cryptography + COPY templates/)
      - core/modules/status-page/docker-compose.base.yml               (mount status-metrics.json)
      - core/modules/status-page/docker-compose.test.yml               (mount test status-metrics.json)
      - core/modules/status-page/module.yaml                           (описание, env_requires — без изменений)
      - core/modules/backup-cron/scripts/crontab                       (строка 49: замена скрипта + flock)
      - tests/test_status_page.py                                      (адаптация: новый JSON, новые поля)
      - tests/test_platform_export_metrics.py (NEW)                    (юнит-тесты экспортёра с моками)
      - tests/_conftest/smoke.py                                       (замена docker-health.json → status-metrics.json)
  REQUIRES:              Python ≥3.10, cryptography ≥41.0 (pip), Jinja2 ≥3.1 (pip), PyYAML (уже), docker CLI (host), flock (host)
  TASK_SIZE:             LARGE (12 файлов CREATE+DELETE, 9 файлов MODIFY, ~10 новых тестов)
-->

$START_DEVPLAN

---

## Overview

**Status:** Architect Review
**DevPlan:** 059 (META — замещает 046)
**Session:** 2026-07-23
**Priority:** NORMAL
**Size:** LARGE
**Source:** 4-model review synthesis (M1-прагматик 8.8/10, M2-хирург 9.0/10, M3-менеджер 8.0/10, M4-архитектор 8.8/10) + operator survey (5 answers)

### Что изменилось относительно 046

| # | Изменение | Источник | Категория |
|---|-----------|----------|-----------|
| Δ1 | `docker inspect` → `docker stats --no-stream` для memory_usage/cpu_percent | M2#1, M4#2 | **БЛОКЕР** |
| Δ2 | Атомарная запись: tempfile + os.replace() | M1#5, M2#3, M4 | **БЛОКЕР** |
| Δ3 | `du -sh` → `du -sb` (байты, машинный парсинг) | M1#4, M2#2, M3#3, M4#3 | **БЛОКЕР** |
| Δ4 | Добавлен `schema_version: 2` в JSON | M1#6, M2#11, M3#12, M4 | **БЛОКЕР** |
| Δ5 | Модульная декомпозиция: docker/cert/project/host коллекторы | M4#1 + operator Q1 | Архитектура |
| Δ6 | TTL-кэш: runtime каждую минуту, inventory раз в час + mtime | M1#4, M4#5 + operator Q2/Q5 | Архитектура |
| Δ7 | `openssl x509` → `cryptography.x509` (чистый Python) | M4#4 + operator Q3 | Техническое |
| Δ8 | `container_name` → `name` — осознанный разрыв контракта | M2#6 + operator Q4 | Контракт |
| Δ9 | Batch `docker inspect $(docker ps -aq)` (один вызов) | M1#9, M4#2 | Производительность |
| Δ10 | Timeout на каждый subprocess (15s default) | M3, M4, M2#5 | Надёжность |
| Δ11 | Cron flock для предотвращения наложения запусков | M2#5 | Надёжность |
| Δ12 | Jinja2 autoescape=select_autoescape(['html']) | M1#14, M2#4 | Безопасность |
| Δ13 | Graceful degradation: partial JSON + errors[] | M1#8, M2#10, M3#4 | Надёжность |
| Δ14 | Тесты для экспортёра (test_platform_export_metrics.py) | M2#9, M3#6 | Тестирование |
| Δ15 | Image size по sha256 (docker inspect .Image), не по тегу | M1#3, M2#8 | Корректность |
| Δ16 | Cert parsing: cryptography.x509 → ISO 8601 даты | M2#7 | Корректность |
| Δ17 | `os.makedirs(exist_ok=True)` для /run/platform | M2#14 | Надёжность |
| Δ18 | Wildcard: SAN-матч (точное + wildcard), не угадывание | M2#7 | Корректность |
| Δ19 | `FileSystemLoader` с абсолютным путём (`Path(__file__).parent`) | M2#12 | Баг |
| Δ20 | `du` mtime-кэш: пересчёт только при изменении mtime или >1 часа | operator Q5 | Производительность |
| Δ21 | Кнопка ручного обновления метрик на status-page | operator Q1 | UX |
| Δ22 | `node.yaml` структура формально описана в плане | M3#1 | Документация |

### Что НЕ включено (осознанно)

| Отклонённое предложение | Причина |
|--------------------------|---------|
| Два JSON (runtime + inventory) | Operator Q2: единый JSON + TTL-кэш. S3-collapse сохранён. |
| `container_name` alias для backward compat | Operator Q4: сломать контракт. Grep-аудит подтвердит отсутствие скрытых потребителей. |
| Docker SDK for Python (`pip install docker`) | Экспортёр работает на хосте — docker CLI уже доступен. SDK добавил бы зависимость без gains. |
| `openssl` subprocess | Operator Q3: cryptography.x509. Чистый Python, тестируемо без моков subprocess. |
| `networks` поле в схеме контейнера | YAGNI — не покрывается ни одним AC. Удалено из схемы. |
| Mount docker.sock в status-page контейнер | TRAP: read-only docker.sock рассмотрен, но экспортёр остаётся на хосте (см. §TRAP). |

---

## Superposition Collapse (решения приняты + новые)

| # | Dimension | Options | Collapsed |
|---|-----------|---------|-----------|
| S1 | Data delivery | A: Cron-экспорт JSON, B: Prometheus API, C: Docker socket | **A** (cron → tmpfs JSON) |
| S2 | Template engine | A: Jinja2, B: Inline HTML | **A** (Jinja2 + autoescape) |
| S3 | Backward compat | A: Единый status-metrics.json, B: Два файла | **A** (единый JSON + TTL-кэш) |
| S4 | Export language | A: Python, B: Bash+inline python3 | **A** (Python — языковая политика) |
| S5 | Файловая структура | A: Монолит platform_export_metrics.py, B: Модульные коллекторы | **B** (docker/cert/project/host + coordinator) |
| S6 | Cert parsing | A: subprocess openssl, B: Python cryptography.x509, C: ssl stdlib | **B** (cryptography.x509 — точность + тестируемость) |
| S7 | container_name → name | A: alias для совместимости, B: сломать контракт | **B** (grep-аудит → сломать) |
| S8 | du периодичность | A: каждую минуту, B: раз в час, C: по mtime | **C** (раз в час ИЛИ при изменении mtime) |

---

## Architecture Overview

### Target (v2 — с учётом МЕТА-синтеза)

```
┌─ HOST cron (* * * * *) ──────────────────────────────────────────┐
│  flock -n /run/lock/platform-metrics.lock timeout 50s             │
│    platform-export-metrics.sh → platform_export_metrics.py         │
│                                                                   │
│  ┌─ Коллекторы (модульные) ────────────────────────────────────┐ │
│  │ docker_collector.py                                          │ │
│  │   docker ps -aq → docker inspect $(all) [batch, 1 вызов]    │ │
│  │   docker stats --no-stream --format '{{json .}}' [1 вызов]  │ │
│  │   docker image inspect $(docker ps -aq --format '{{.Image}}')│ │
│  │   → containers[], images map[sha256]size_bytes              │ │
│  │                                                              │ │
│  │ cert_collector.py                                            │ │
│  │   node.yaml → domains[] → /etc/letsencrypt/live/<d>/         │ │
│  │   cryptography.x509.load_pem_x509_certificate()              │ │
│  │   SAN match: exact + wildcard (*.domain)                     │ │
│  │   → certs[{cert_id, domains[], issuer, not_after_iso, ...}] │ │
│  │                                                              │ │
│  │ project_collector.py                                         │ │
│  │   node.yaml → projects[] → mtime check (TTL: 1h or changed) │ │
│  │   du -sb /opt/projects/<name>/ [только при промахе кэша]    │ │
│  │   docker image inspect <image_sha> → Size [из кэша образов] │ │
│  │   → projects[{name, domain, code_size_bytes, image_size}]   │ │
│  │                                                              │ │
│  │ host_collector.py                                            │ │
│  │   shutil.disk_usage('/opt') → total_gb, free_gb, used_pct   │ │
│  │   → host{disk_total_gb, disk_free_gb, disk_used_percent}    │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  ┌─ json_writer.py (атомарная запись) ──────────────────────────┐ │
│  │ json.dumps(data) → tempfile.mkstemp(dir=output_dir)          │ │
│  │ → os.fsync(fd) → os.replace(tmp, target)                     │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  → /run/platform/status-metrics.json                              │
│       ↓                                                           │
│  {schema_version: 2, generated_at, node,                          │
│   containers: [{name, running, healthy, exit_code,                │
│     status_line, image, image_size_bytes,                         │
│     memory_usage_bytes, memory_limit_bytes, cpu_percent,          │
│     restart_policy}],                                             │
│   certs: [{cert_id, domains[], issuer, subject,                   │
│     not_after_iso, days_remaining, san[],                         │
│     source_path}],                                                │
│   projects: [{name, domain, code_size_bytes,                      │
│     docker_image, docker_image_size_bytes}],                      │
│   host: {disk_total_gb, disk_free_gb, disk_used_percent},         │
│   errors: []  // partial failures, если есть                      │
│  }                                                                │
└──────────────┬────────────────────────────────────────────────────┘
               │ mount ro
┌─ status-page (Docker) ────────────────────────────────────────────┐
│  app.py (stdlib http.server + Jinja2 autoescape)                  │
│  templates/status.html (Jinja2)                                   │
│  GET /        → Jinja2 render (certs + containers + host)         │
│                 + кнопка «Обновить метрики»                       │
│  GET /health  → JSON PASS/FAIL (unchanged contract)               │
│  GET /status.json → full JSON (extended, schema_version: 2)       │
│  GET /refresh → POST-редирект на ручной запуск экспортёра (TODO) │
└──────────────────────────────────────────────────────────────────┘
```

### TTL-кэш модель

```
┌─ platform_export_metrics.py (каждую минуту) ─────────────────┐
│                                                               │
│  docker ps + docker inspect + docker stats                    │
│    → ВСЕГДА свежие (runtime, дешёвые вызовы)                  │
│                                                               │
│  docker image inspect (sizes)                                 │
│    → КЭШ: /var/cache/platform/metrics/image_sizes.json        │
│    → TTL: 1 час (образы не меняются чаще)                     │
│    → Инвалидация: по image ID (sha256)                        │
│                                                               │
│  certs (cryptography.x509)                                    │
│    → КЭШ: /var/cache/platform/metrics/certs.json              │
│    → TTL: 1 час (сертификаты меняются раз в 2-3 месяца)       │
│    → Инвалидация: по mtime fullchain.pem                       │
│                                                               │
│  du -sb /opt/projects/*                                       │
│    → КЭШ: /var/cache/platform/metrics/project_sizes.json      │
│    → TTL: 1 час ИЛИ mtime директории изменился                 │
│    → Первый запуск: холодный кэш → полный сбор                 │
│                                                               │
│  shutil.disk_usage('/opt')                                    │
│    → ВСЕГДА свежий (один syscall)                             │
│                                                               │
│  Мёрж: свежие runtime + кэшированный inventory → JSON         │
└───────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Модульный экспортёр метрик (host-side)

### P1.0 Структура модуля

```
core/internal/healthcheck/metrics/
├── __init__.py              # package init, реэкспорт публичных символов
├── docker_collector.py      # get_containers(), get_image_sizes()
├── cert_collector.py        # get_certs(node_yaml_path)
├── project_collector.py     # get_projects(node_yaml_path, image_cache)
├── host_collector.py        # get_host_disk()
├── json_writer.py           # atomic_write(data, path), schema_version
└── cache.py                 # CacheManager: load/save TTL cache, mtime check

core/internal/healthcheck/platform_export_metrics.py  # coordinator + main()
core/internal/healthcheck/platform-export-metrics.sh  # bash wrapper
```

### P1.1 `docker_collector.py`

```python
# region MODULE_CONTRACT
## @purpose  Docker collector — container status, resource usage, image sizes
## @strategy Batch-first: docker inspect (все контейнеры один вызов) + docker stats --no-stream
# endregion MODULE_CONTRACT

def get_containers() -> list[dict]:
    """
    1. docker ps -aq → список всех container ID
    2. docker inspect $(all_ids) → ОДИН вызов: State, Config.Image, HostConfig (limits), NetworkSettings
    3. docker stats --no-stream --format '{{json .}}' → ОДИН вызов: CPUPerc, MemUsage, MemLimit
    4. Мёрж inspect + stats по container ID/name
    5. subprocess.run(timeout=15) на каждый вызов
    Returns: list[container_dict]
    """
    ...

def get_image_sizes(image_ids: set[str]) -> dict[str, int]:
    """
    docker image inspect $(image_ids) --format '{{json .}}'
    → {sha256: Size}
    subprocess.run(timeout=15)
    """
    ...
```

**Ключевое отличие от 046:** `docker stats --no-stream` для runtime-метрик (CPU%, memory usage). Без этого поля `memory_usage_bytes` и `cpu_percent` всегда пустые.

### P1.2 `cert_collector.py`

```python
# region MODULE_CONTRACT
## @purpose  SSL certificate collector via cryptography.x509 — NO subprocess openssl
## @strategy Read node.yaml → domains[] → /etc/letsencrypt/live/<domain>/fullchain.pem
##           Wildcard: если точный домен не найден → search all live/*/fullchain.pem,
##           для каждого домена из node.yaml найти покрывающий сертификат (exact или wildcard SAN match)
# endregion MODULE_CONTRACT

from cryptography import x509
from cryptography.hazmat.primitives import hashes

def _san_match(cert_san: list[str], domain: str) -> bool:
    """Exact match OR wildcard match (*.domain → sub.domain)."""
    ...

def _load_cert(path: str) -> dict:
    """cryptography.x509.load_pem_x509_certificate → issuer, subject, not_after (ISO 8601), SAN."""
    ...

def get_certs(node_yaml_path: str) -> list[dict]:
    """
    1. node.yaml → domains (expose:true)
    2. Для каждого домена: /etc/letsencrypt/live/<domain>/fullchain.pem
    3. Если не найден: поиск по всем /etc/letsencrypt/live/*/fullchain.pem, SAN match
    4. Дедупликация: один физический сертификат → cert_id (sha256 от пути)
       → certs[{cert_id, domains[], issuer, subject, not_after_iso, days_remaining, san[], source_path}]
    5. Graceful: отсутствующий файл → warning в errors[], не crash
    """
    ...
```

**Ключевое отличие от 046:**
- `cryptography.x509` вместо `openssl x509` — чистый Python, без парсинга locale-зависимых дат
- Даты в ISO 8601 (`not_after_iso`), не `notAfter=Jul 23 12:00:00 2026 GMT`
- Wildcard через SAN match, не угадывание по PLATFORM_DOMAIN

### P1.3 `project_collector.py`

```python
# region MODULE_CONTRACT
## @purpose  Project code size via du -sb with mtime-based cache
## @strategy du -sb (bytes, machine-parseable). Cache: /var/cache/platform/metrics/project_sizes.json
##           Refresh: only if mtime changed OR >1 hour since last check
# endregion MODULE_CONTRACT

def get_projects(node_yaml_path: str, image_cache: dict[str, int], cache_mgr) -> list[dict]:
    """
    1. node.yaml → projects[{name, domain, docker_image, code_path}]
    2. Для каждого проекта: проверить mtime code_path
       - Не изменился И <1 час → использовать кэш
       - Изменился ИЛИ >1 час → du -sb <code_path> (timeout=30s)
    3. docker_image_size: из image_cache по sha256 (batch-загружен в docker_collector)
    4. Возвращает [{name, domain, code_size_bytes, docker_image, docker_image_size_bytes}]
    """
    ...
```

**Ключевое отличие от 046:**
- `du -sb` (байты) вместо `du -sh` (human-readable: "1.4G", "900M")
- mtime-кэш: ду выполняется раз в час или при изменении директории, а не каждую минуту
- Размер образа по sha256 из docker image inspect, не по имени тега

### P1.4 `host_collector.py`

```python
# region MODULE_CONTRACT
## @purpose  Host disk usage via shutil.disk_usage (stdlib, один syscall)
# endregion MODULE_CONTRACT

import shutil

def get_host_disk(path: str = "/opt") -> dict:
    """shutil.disk_usage → total_gb, free_gb, used_percent (округление до 1 знака)."""
    usage = shutil.disk_usage(path)
    total_gb = round(usage.total / (1024**3), 1)
    free_gb = round(usage.free / (1024**3), 1)
    used_percent = round((1 - usage.free / usage.total) * 100, 1)
    return {"disk_total_gb": total_gb, "disk_free_gb": free_gb, "disk_used_percent": used_percent}
```

Без изменений относительно 046 (простейший коллектор).

### P1.5 `json_writer.py` — атомарная запись

```python
# region MODULE_CONTRACT
## @purpose  Atomic JSON writer: tempfile + fsync + os.replace — читатель никогда не видит половину файла
# endregion MODULE_CONTRACT

import json
import os
import tempfile

SCHEMA_VERSION = 2

def atomic_write(data: dict, target_path: str, dir_mode: int = 0o755) -> None:
    """
    1. data['schema_version'] = SCHEMA_VERSION
    2. tempfile.mkstemp(dir=os.path.dirname(target_path), suffix='.tmp')
    3. json.dump → fd
    4. os.fsync(fd)  # гарантия записи на диск
    5. os.close(fd)
    6. os.replace(tmp_path, target_path)  # атомарный rename в той же ФС
    """
    ...
```

**Ключевое отличие от 046:** В 046 запись была «пишет status-metrics.json» без атомарности — race condition с читателем.

### P1.6 `cache.py` — TTL Cache Manager

```python
# region MODULE_CONTRACT
## @purpose  Generic TTL cache for inventory data (certs, image sizes, project sizes)
## @strategy JSON on disk (/var/cache/platform/metrics/). mtime-based invalidation for file sources.
# endregion MODULE_CONTRACT

class CacheManager:
    def __init__(self, cache_dir: str = "/var/cache/platform/metrics"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def get(self, key: str, ttl_seconds: int = 3600, source_mtime: float | None = None) -> dict | None:
        """Возвращает кэшированные данные если свежие. None при промахе."""
        ...

    def set(self, key: str, data: dict) -> None:
        """Сохраняет кэш с текущим timestamp."""
        ...
```

### P1.7 `platform_export_metrics.py` — координатор

```python
# region MODULE_CONTRACT
## @purpose  Metrics export coordinator — собирает данные от коллекторов, применяет TTL-кэш,
##           мёржит, пишет атомарно в status-metrics.json
# endregion MODULE_CONTRACT

def main():
    # 1. node.yaml
    node_data = load_node_yaml(NODE_YAML_PATH)

    # 2. Runtime (всегда свежее)
    containers = docker_collector.get_containers()
    image_ids = {c['image_id'] for c in containers}
    host_disk = host_collector.get_host_disk()

    # 3. Inventory (TTL-кэш)
    image_sizes = _get_image_sizes_cached(image_ids, cache_mgr)
    certs = _get_certs_cached(node_data, cache_mgr)
    projects = project_collector.get_projects(node_data, image_sizes, cache_mgr)

    # 4. Errors aggregation
    errors = _collect_errors(containers, certs, projects)

    # 5. Build + atomic write
    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "node": NODE_NAME,
        "containers": containers,
        "certs": certs,
        "projects": projects,
        "host": host_disk,
        "errors": errors,
    }
    json_writer.atomic_write(data, STATUS_METRICS_JSON)
```

### P1.8 Bash-обёртка `platform-export-metrics.sh`

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: platform-export-metrics wrapper → platform_export_metrics.py
# STRUCTURE: ▶ set vars → ○ ensure dirs → ○ exec python3 coordinator → ⎋ exit
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
METRICS_DIR="${SCRIPT_DIR}/metrics"

# Ensure cache + output directories exist on tmpfs (empty after reboot)
mkdir -p /run/platform /var/cache/platform/metrics

exec python3 "${SCRIPT_DIR}/platform_export_metrics.py" "$@"
```

### P1.9 Обновление crontab (строка 49)

```cron
# Было:
* * * * *   root /opt/platform/core/internal/healthcheck/docker-healthcheck.sh >> /var/log/platform/backup/docker-healthcheck.log 2>&1

# Стало:
* * * * *   root flock -n /run/lock/platform-metrics.lock timeout 50s /opt/platform/core/internal/healthcheck/platform-export-metrics.sh >> /var/log/platform/backup/metrics-export.log 2>&1
```

**Добавлено:**
- `flock -n` — предотвращает наложение запусков (если предыдущий подвис на docker stats)
- `timeout 50s` — аппаратный лимит на выполнение (cron интервал 60s, запас 10s)

---

## Phase 2: Обновление status-page (container-side)

### P2.1 Dockerfile

```dockerfile
FROM python:3.12-alpine

RUN apk add --no-cache curl && \
    pip install --no-cache-dir pyyaml==6.0.2 jinja2==3.1.4 cryptography==41.0.7

COPY app.py /app/app.py
COPY templates/ /app/templates/

WORKDIR /app

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=5s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

EXPOSE 8080

CMD ["python3", "/app/app.py"]
```

**Изменения:** добавлены `jinja2==3.1.4`, `cryptography==41.0.7`, `COPY templates/`.

### P2.2 `templates/status.html` (Jinja2, 3 таблицы)

```jinja2
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="120">  <!-- автообновление каждые 2 минуты -->
  <title>Node Status — {{ node_name }}</title>
  <style>/* ... цветовая индикация ... */</style>
</head>
<body>
  <!-- Overall status banner -->
  <!-- Staleness warning: если generated_at > 5 минут → баннер "данные устарели" -->

  <!-- Таблица 1: Domains -->
  <h2>Domains</h2>
  <table>
    <thead><tr><th>Domain</th><th>Project</th><th>Cert Issuer</th><th>Expiry</th><th>SAN</th><th>Code Size</th><th>Image Size</th></tr></thead>
    <tbody>
    {% for p in projects %}
    <tr>
      <td><a href="https://{{ p.domain }}">{{ p.domain }}</a></td>
      <td>{{ p.name }}</td>
      <td>{{ p.cert_issuer }}</td>
      <td class="{{ p.expiry_class }}">{{ p.cert_expiry }} ({{ p.days_remaining }}d)</td>
      <td><span title="{{ p.san_full }}">{{ p.san_truncated }}</span></td>  <!-- первые 5 + tooltip -->
      <td>{{ p.code_size_gb }} GB</td>
      <td>{{ p.image_size_gb }} GB</td>
    </tr>
    {% endfor %}
    </tbody>
  </table>

  <!-- Таблица 2: Containers -->
  <!-- Таблица 3: Host -->

  <!-- Кнопка ручного обновления -->
  <form action="/refresh" method="post">
    <button type="submit">Refresh Metrics</button>
  </form>
</body>
</html>
```

**Ключевые моменты:**
- `<meta http-equiv="refresh" content="120">` — автообновление страницы
- SAN: первые 5 записей + `title` атрибут с полным списком (tooltip при наведении)
- Цветовая индикация через CSS-классы (`expiry-critical`, `expiry-warning`, `mem-warning`, `disk-critical`)
- Кнопка ручного обновления (POST /refresh — future: дергает cron на хосте)
- Jinja2 autoescape включён (конфигурация в app.py)

### P2.3 Рефакторинг `app.py`

**Удалить:**
- `_handle_html()` inline HTML (строки 356-441)
- Константа `DOCKER_HEALTH_JSON`

**Добавить:**
- `STATUS_METRICS_JSON` (env var с fallback: `/run/platform/status-metrics.json`)
- `_load_status_metrics()` — замена `load_docker_health()`, читает status-metrics.json, проверяет `schema_version`
- `_render_html(data)` — Jinja2 с autoescape:

```python
from jinja2 import Environment, FileSystemLoader, select_autoescape

_jinja_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(['html']),
)

def _render_html(data: dict) -> str:
    template = _jinja_env.get_template("status.html")
    return template.render(**data)
```

- Интеграция в `get_all_checks()` — данные из metrics.json включаются в `/status.json`
- Staleness check: если `generated_at` старше 5 минут → баннер на HTML + WARN в `/health`

**Ключевые отличия от 046:**
- `FileSystemLoader` с абсолютным путём (`Path(__file__).parent / "templates"`)
- `autoescape=select_autoescape(['html'])` — XSS protection
- `Environment` создаётся ОДИН раз при старте, не на каждый запрос
- Staleness check (age > 5 мин → предупреждение)

### P2.4 `docker-compose.base.yml`

```yaml
volumes:
  # Было:
  - /run/platform/docker-health.json:/run/platform/docker-health.json:ro
  # Стало:
  - /run/platform/status-metrics.json:/run/platform/status-metrics.json:ro
```

### P2.5 `module.yaml`

Добавить `jinja2`, `cryptography` в описание (не в `env_requires`, т.к. это pip-пакеты, не env-переменные). Поле `env_requires` без изменений.

---

## Phase 3: Тесты

### P3.1 Новый: `tests/test_platform_export_metrics.py` (юнит-тесты экспортёра)

```python
# GREP_SUMMARY: test-platform-export-metrics docker cert project host collector json-writer cache
# STRUCTURE: ▶ test_docker_collector_containers → ◇ mock subprocess.run (docker inspect + stats) → assert fields
#            ▶ test_docker_collector_batch → ◇ один subprocess вызов для inspect all → assert
#            ▶ test_cert_collector_wildcard → ◇ mock cryptography.x509 + SAN *.domain → assert domains[]
#            ▶ test_cert_collector_expiry_dates → ◇ ISO 8601 not_after → assert days_remaining
#            ▶ test_project_collector_mtime_cache → ◇ mock mtime unchanged → assert no du call
#            ▶ test_project_collector_du_sb → ◇ du -sb output → assert code_size_bytes int
#            ▶ test_host_collector → ◇ mock shutil.disk_usage → assert total/free/used
#            ▶ test_json_writer_atomic → ◇ tmp file exists before rename → assert final file complete
#            ▶ test_json_writer_schema_version → ◇ assert schema_version: 2 in output
#            ▶ test_coordinator_partial_failure → ◇ certs fail, docker OK → assert errors[] + partial data
#            ▶ test_coordinator_empty_state → ◇ no docker, no certs → assert empty arrays + no crash
#            ▶ test_cache_ttl_hit → ◇ cache fresh → assert no recompute
#            ▶ test_cache_ttl_miss → ◇ cache expired → assert recompute
#            ▶ test_cache_mtime_invalidation → ◇ source mtime newer → assert cache miss

# Все тесты используют tmp_path, mock subprocess.run / cryptography.x509 / shutil.disk_usage
```

**Минимальный набор:** 14 тестов, покрывающих все коллекторы + writer + cache + coordinator + edge cases.

### P3.2 Обновление `tests/test_status_page.py`

- Заменить `mock_docker_health_json_*` на `mock_status_metrics_json_*` с новыми полями
- `container_name` → `name` во всех фикстурах
- Добавить тесты: `test_status_page_schema_version_check`, `test_status_page_staleness_warning`, `test_status_page_jinja2_autoescape`
- Сохранить существующие тесты: health, json, anti-recursion, timeout, x-headers

### P3.3 Обновление `tests/_conftest/smoke.py`

- Строки 652-670: заменить генерацию `docker-health.json` на `status-metrics.json` с новыми полями

### P3.4 `make test-inventory-sync`

После добавления `test_platform_export_metrics.py` — обновить test inventory.

---

## Phase 4: Gate

`make gate MODE=fast` зелёный после всех изменений.

Дополнительные проверки:
- `make check-file-lines` — `platform_export_metrics.py` (координатор) ≤ 250 строк, каждый коллектор ≤ 200 строк
- `make lint` — ruff check всех новых файлов
- `make validate` — schema-валидация

---

## Файловый манифест

| # | Файл | Действие | Назначение |
|---|------|----------|------------|
| 1 | `core/internal/healthcheck/metrics/__init__.py` | **CREATE** | Package init |
| 2 | `core/internal/healthcheck/metrics/docker_collector.py` | **CREATE** | Docker container + image collector |
| 3 | `core/internal/healthcheck/metrics/cert_collector.py` | **CREATE** | SSL certificate collector (cryptography.x509) |
| 4 | `core/internal/healthcheck/metrics/project_collector.py` | **CREATE** | Project code size collector (du -sb + cache) |
| 5 | `core/internal/healthcheck/metrics/host_collector.py` | **CREATE** | Host disk collector (shutil) |
| 6 | `core/internal/healthcheck/metrics/json_writer.py` | **CREATE** | Atomic JSON writer + schema_version |
| 7 | `core/internal/healthcheck/metrics/cache.py` | **CREATE** | TTL cache manager |
| 8 | `core/internal/healthcheck/platform_export_metrics.py` | **CREATE** | Coordinator: сбор + мёрж + main |
| 9 | `core/internal/healthcheck/platform-export-metrics.sh` | **CREATE** | Bash-обёртка для cron |
| 10 | `core/modules/status-page/templates/status.html` | **CREATE** | Jinja2 HTML шаблон |
| 11 | `core/internal/healthcheck/docker-healthcheck.sh` | **DELETE** | Заменён на platform-export-metrics.sh |
| 12 | `core/modules/status-page/app.py` | **MODIFY** | Удаление inline HTML, Jinja2 render, metrics loading, staleness check |
| 13 | `core/modules/status-page/Dockerfile` | **MODIFY** | +jinja2, +cryptography, COPY templates/ |
| 14 | `core/modules/status-page/docker-compose.base.yml` | **MODIFY** | mount: docker-health.json → status-metrics.json |
| 15 | `core/modules/status-page/docker-compose.test.yml` | **MODIFY** | mount: test status-metrics.json |
| 16 | `core/modules/status-page/module.yaml` | **MODIFY** | Обновить description |
| 17 | `core/modules/backup-cron/scripts/crontab` | **MODIFY** | Строка 49: +flock +timeout +новый скрипт |
| 18 | `tests/test_platform_export_metrics.py` | **CREATE** | Юнит-тесты экспортёра (14 тестов) |
| 19 | `tests/test_status_page.py` | **MODIFY** | Адаптация: новый JSON, новые тесты |
| 20 | `tests/_conftest/smoke.py` | **MODIFY** | docker-health.json → status-metrics.json |

---

## Acceptance Criteria (полный список)

| # | Критерий | Проверка | Источник |
|---|----------|----------|----------|
| AC1-M | Таблица Domains: issuer, expiry (ISO 8601), SAN (5 + tooltip), code_size, image_size | Визуально + тест | AC1 |
| AC2-M | Таблица Containers: name, domain(s), status, CPU%, memory (used/limit, цвет >90%), image + size | Визуально + тест | AC2 |
| AC3-M | `/health` 200 PASS / 503 FAIL — контракт неизменен | Существующий тест | AC3 |
| AC4-M | `/status.json` включает certs, projects, host, schema_version: 2 | Тест | AC4 |
| AC5-M | `make gate MODE=fast` зелёный | CI | AC5 |
| AC6-M | Wildcard-сертификаты: SAN exact + wildcard match | test_cert_collector_wildcard | AC6 |
| AC7-M | Экспортёр не падает при отсутствии docker/certs → partial JSON + errors[] | test_coordinator_partial_failure | AC7 |
| AC8-M | Цветовая индикация: красный <7d (cert)/>90% (disk), жёлтый <30d/>80% | Визуально + Jinja2 class test | AC8 |
| AC9-M | Атомарная запись: tmpfile + os.fsync + os.replace | test_json_writer_atomic | Δ2 (META) |
| AC10-M | Экспортёр ≤15s (batch inspect + stats) | test_coordinator_performance | Δ (META) |
| AC11-M | Экспортёр не накладывается (flock, timeout 50s) | crontab entry verification | Δ (META) |
| AC12-M | `container_name` → `name`: grep-аудит подтверждает отсутствие потребителей | grep audit report | Δ (META) |
| AC13-M | `schema_version: 2` в JSON, status-page проверяет при чтении | test_json_writer_schema_version | Δ (META) |
| AC14-M | Jinja2 autoescape включён, XSS-векторы закрыты | test_status_page_jinja2_autoescape | Δ (META) |
| AC15-M | `du -sb` + mtime-кэш: пересчёт только при изменении | test_project_collector_mtime_cache | Δ (META) |

---

## Миграция и откат

### Порядок развёртывания

```
1. [HOST] Заменить crontab строку 49 (старый docker-healthcheck.sh → новый platform-export-metrics.sh)
   → старый docker-healthcheck.sh остаётся на диске, но не вызывается
2. [HOST] Развернуть core/internal/healthcheck/metrics/ + platform_export_metrics.py + обёртку
3. [HOST] Создать /var/cache/platform/metrics/ (если не существует)
4. [HOST] Дождаться первого успешного экспорта (проверить /run/platform/status-metrics.json)
5. [DOCKER] Пересобрать образ status-page (docker compose build status-page)
6. [DOCKER] docker compose up -d status-page
7. [VERIFY] Проверить status-page: /health → 200, / → HTML с новыми таблицами
```

### Откат

```
1. [HOST] Вернуть исходную строку 49 в crontab (docker-healthcheck.sh)
2. [DOCKER] Откатить docker-compose.base.yml (mount docker-health.json)
3. [DOCKER] Пересобрать образ status-page из предыдущего Dockerfile (без jinja2)
4. [DOCKER] docker compose up -d status-page
5. [HOST] Удалить /run/platform/status-metrics.json (опционально)
```

---

## Риски

| # | Риск | Вероятность | Влияние | Митигация |
|---|------|------------|---------|-----------|
| R1 | `docker stats --no-stream` возвращает нестабильный JSON | Низкая | AC10 не выполняется | Graceful degradation: errors[] при сбое stats |
| R2 | `cryptography.x509` не может прочитать некоторые сертификаты | Низкая | Часть сертификатов не отображается | Fallback: subprocess openssl для проблемных сертификатов |
| R3 | mtime-кэш не инвалидируется при деплое (новый код) | Средняя | Размеры кода устарели | Кнопка ручного обновления; deploy hook для сброса кэша |
| R4 | После ребута `/var/cache/platform/metrics/` пуст (tmpfs) | Высокая | Холодный старт каждые 1-2 минуты | Первый запуск после ребута — полный сбор (холодный кэш = полный сбор) |
| R5 | `FileSystemLoader` путь ломается при деплое | Низкая | status-page не стартует | Абсолютный путь через `Path(__file__).parent` |

---

## TRAP-аннотации

<!-- ⚠️ TRAP[DECISION] · 2026-07-23 · HI · Read-only docker.sock — рассмотрен, отклонён для текущей реализации -->
<!-- · Context: Operator Q1 предложил монтировать docker.sock в экспортёр для упрощения сбора данных -->
<!-- · Decision: Экспортёр остаётся на хосте (cron), docker CLI через subprocess. Причины: -->
<!--   (1) Экспортёр на хосте уже имеет полный доступ к docker CLI — docker.sock в контейнере не даёт преимуществ -->
<!--   (2) Контейнеризация экспортёра усложнит мониторинг (docker healthcheck → auto-restart docker daemon) -->
<!--   (3) Если в будущем понадобится контейнеризация: read-only docker.sock mount допустим при условии -->
<!--       НУЛЕВОГО пользовательского ввода на status-page (нет вектора инъекции) -->
<!-- · Rev: если экспортёр будет контейнеризирован — пересмотреть с учётом security-аудита -->

<!-- ⚠️ TRAP[DECISION] · 2026-07-23 · HI · container_name → name: осознанный разрыв контракта -->
<!-- · Decision: Поле container_name переименовано в name БЕЗ обратной совместимости -->
<!-- · Pre-condition: grep-аудит ВСЕХ потребителей docker-health.json (app.py, tests, smoke.py, CI) -->
<!-- · Risk: если есть скрытый потребитель (вне репозитория) — он сломается молча -->
<!-- · Mitigation: grep по всему репозиторию + проверка CI/gate после изменения -->
<!-- · Revert-path: добавить alias container_name в json_writer.py (одна строка) -->

<!-- ⚠️ TRAP[DECISION] · 2026-07-23 · MED · cryptography.x509 как единственный парсер сертификатов -->
<!-- · Decision: Только Python cryptography.x509, без fallback на openssl subprocess -->
<!-- · Risk: x509 может не поддерживать редкие форматы сертификатов (Ed25519, DSA) -->
<!-- · Mitigation: При ошибке загрузки сертификата — errors[] + пропуск, не crash -->
<!-- · Rev: если в проде обнаружатся сертификаты, не читаемые cryptography → добавить openssl fallback -->

<!-- ⚠️ TRAP[DECISION] · 2026-07-23 · MED · mtime-кэш для размеров проектов -->
<!-- · Decision: du -sb пересчитывается только при изменении mtime директории ИЛИ >1 часа -->
<!-- · Risk: если файлы добавились без изменения mtime директории (редкий kernel behaviour) — размеры устареют -->
<!-- · Mitigation: Кнопка ручного обновления на status-page; принудительный пересчёт раз в час -->
<!-- · Rev: если обнаружатся расхождения >10% — уменьшить TTL до 30 минут -->

---

## Grep-аудит (предварительный чеклист перед реализацией)

Перед началом Phase 1 выполнить:

```bash
# 1. Найти всех потребителей docker-health.json
grep -r "docker-health.json" --include="*.py" --include="*.sh" --include="*.yml" --include="*.yaml" .

# 2. Найти всех потребителей container_name
grep -r "container_name" --include="*.py" --include="*.sh" .

# 3. Проверить, что нет скрытых импортов docker-healthcheck.sh
grep -r "docker-healthcheck.sh" --include="*.sh" --include="*.yml" .

# 4. Проверить размер текущего app.py (для сравнения после рефакторинга)
wc -l core/modules/status-page/app.py
```

---

$END_DEVPLAN
