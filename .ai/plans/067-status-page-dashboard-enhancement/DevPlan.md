$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Превратить platform.tronyx.ru из пассивного мониторинга в активный dashboard — единый hub для всех сервисов платформы с упреждающими алертами (cert expiry, backup staleness), диагностическими данными (load average, container uptime) и прямыми ссылками на все платформенные сервисы
DESCRIPTION:           3 волны улучшений статус-страницы: (W1) template-only изменения — Platform Services Table + SSL Summary Banner + Container uptime/domains/restart_policy в существующих таблицах; (W2) новые метрики в коллекторах — host load/uptime, container started_at, backup last-run status; (W3) module healthchecks — live-curl 6 платформенных сервисов (grafana, prometheus, loki, hermes, langfuse, litellm) с отображением в Platform Services Table
RATIONALE:             Все данные для W1 уже собраны коллекторами, но не отображаются (exit_code, restart_policy, healthy) или отображаются неполно (domains=[] хардкод). W2 добавляет 4 поля за 2 file-read системных вызова (load average, uptime) + 1 поле из уже получаемого docker inspect JSON (started_at) + 1 mtime-проверку лог-файла бэкапов. W3 добавляет HTTP healthchecks сервисов — тяжелее (6 curl subprocess), но параллелится через ThreadPoolExecutor (как vhost checks). Суммарная стоимость <200 LOC, CPU overhead <2% от текущего cron-экспорта
ACCEPTANCE_CRITERIA:
  AC-1:  Platform Services Table отображает ≥5 платформенных сервисов с прямыми ссылками и live-статусом (PASS/FAIL/WARN)
  AC-2:  SSL Summary Banner показывает "⚠ Earliest cert expires in N days" при days_remaining < 30 для любого сертификата
  AC-3:  Containers table: колонка "Uptime" показывает человеко-читаемое время работы (3h 15m), колонка "Domain(s)" не пустая для сервисов с маппингом, колонка "Restart" показывает restart policy
  AC-4:  Host Resources table: добавлены Load Average (1m/5m/15m) и System Uptime
  AC-5:  Backup Status показывает timestamp последнего успешного бэкапа (postgres) или "No recent backup" если >25h
  AC-6:  /health возвращает 200 PASS при всех healthy checks (без деградации контракта)
  AC-7:  /status.json включает все новые поля в ответе (backward-compatible — поля добавляются, не удаляются)
  AC-8:  make gate MODE=fast зелёный
  AC-9:  Общее время экспорта метрик не превышает 20s (текущий baseline ~5-8s, запас на module healthchecks)
  AC-10: Template рендерится без ошибок Jinja2 на production данных (протестировано на test fixtures из status-metrics.json)
IMPLEMENTS:            Superposition analysis 2026-07-24 — 5 гипотез улучшения статус-страницы
IMPACTS:
  - core/modules/status-page/templates/status.html (W1: template changes — новые таблицы + колонки)
  - core/modules/status-page/app.py (W1: _enrich_containers — маппинг доменов; W1: _render_html — новые данные для шаблона; W3: platform service curl checks)
  - core/internal/healthcheck/metrics/docker_collector.py (W2: +started_at поле)
  - core/internal/healthcheck/metrics/host_collector.py (W2: +get_host_uptime функция)
  - core/internal/healthcheck/platform_export_metrics.py (W2: вызов get_host_uptime + get_backup_status; W3: вызов get_module_healthchecks)
  - core/internal/healthcheck/metrics/__init__.py (W2: новый модуль backup_collector)
  - tests/test_platform_export_metrics.py (W2: тесты новых коллекторов)
  - tests/test_status_page_app.py (W1+W3: тесты template rendering + healthcheck endpoints)
REQUIRES:
  - Python ≥ 3.10 (cryptography, jinja2 — уже в зависимостях)
  - Docker daemon running (для module healthchecks через внутреннюю сеть)
  - /proc/uptime и /proc/loadavg доступны на хосте (Linux VPS)
  - /var/log/platform/backup/postgres.log существует (создаётся backup-cron модулем)
$END_ARTIFACT_CONTRACT

---

# DevPlan 067 — Status Page Dashboard Enhancement

## Краткое содержание (TL;DR)

**Текущее состояние:** `platform.tronyx.ru` — статическая страница с 3 таблицами (Domains, Containers, Host Resources), пассивный мониторинг.

**Целевое состояние:** Активный dashboard — единый hub для всех платформенных сервисов с упреждающими алертами, диагностическими данными и прямыми ссылками.

**Стратегия:** 3 волны по возрастанию стоимости → W1 (template-only, ~40 LOC) → W2 (collector additions, ~80 LOC) → W3 (module healthchecks, ~70 LOC).

---

## Problem Matrix (возможности для улучшения)

| # | Severity | Symptom | Root Cause | Impact | Fix Type |
|---|----------|---------|------------|--------|----------|
| P1 | **MEDIUM** | Колонка "Domain(s)" в таблице Containers всегда пустая | `_enrich_containers()` хардкодит `domains: []`, маппинг контейнер→домены не реализован | Оператор не видит связи контейнер-проект | Code (app.py) |
| P2 | **LOW** | Платформенные сервисы (Grafana, Prometheus, Loki, Hermes, Langfuse, LiteLLM) не видны на status-page | Нет таблицы platform services, нет live-curl проверок | Оператор должен помнить URL или искать в закладках | Template + Code |
| P3 | **LOW** | Нет информации о времени работы контейнеров | `docker inspect` возвращает `State.StartedAt`, но поле не пробрасывается в `docker_collector.get_containers()` | При инциденте оператор не знает, какие контейнеры недавно перезапускались | Code (docker_collector) |
| P4 | **LOW** | Нет информации о загрузке CPU (load average) и системном uptime | `host_collector` собирает только disk usage | Оператор вынужден заходить по SSH для `uptime`/`top` | Code (host_collector) |
| P5 | **LOW** | Нет упреждающего предупреждения об истекающих сертификатах | `cert_collector` вычисляет `days_remaining`, но страница показывает expiry в таблице без сводного баннера | Оператор может пропустить истекающий сертификат | Template |
| P6 | **LOW** | Нет информации о статусе резервного копирования | Модуль backup-cron пишет логи в `/var/log/platform/backup/`, но ни один коллектор их не читает | Silent failure — бэкапы могут молча перестать работать | New collector |
| P7 | **LOW** | Поля `exit_code`, `healthy`, `restart_policy` контейнера собираются, но не отображаются | Шаблон показывает упрощённый статус (running/exited) без деталей | Потеря диагностической информации | Template |

---

## Design Decisions

### D1: Почему host_collector, а не встроенные Python-модули?
`os.getloadavg()` (Unix only) и чтение `/proc/uptime` — оба системные вызовы без subprocess. `psutil` не используется для избежания внешней зависимости. Решение: stdlib-only для host-метрик.

### D2: Почему не `docker system df` для disk usage Docker?
`docker system df` требует Docker daemon и медленный (~2-5s). Альтернатива: агрегировать `Size` из `docker image inspect` (уже вызывается в `get_image_sizes`). Решение: добавить вычисление total image size в `platform_export_metrics.py` из существующего `image_sizes` dict — zero-cost.

### D3: Почему маппинг контейнер→домены через `_enrich_containers`, а не через `docker_collector`?
Контейнеры не знают о доменах — домены привязаны к проектам через `node.yaml`. Маппинг должен происходить на уровне `app.py` (как `_enrich_projects` обогащает проекты данными сертификатов). Решение: в `_enrich_containers()` передавать список проектов, сопоставлять `container.name` → `project.name` (эвристика: имя контейнера часто совпадает с именем проекта).

### D4: Почему backup status через новый collector, а не в host_collector?
Backup status — отдельная ответственность (не host-метрика). Выделение в `backup_collector.py` сохраняет single-responsibility и упрощает тестирование. Решение: новый файл `core/internal/healthcheck/metrics/backup_collector.py`.

### D5: Module healthchecks — curl из status-page контейнера или из cron-экспорта?
Cron-экспорт на хосте имеет доступ к Docker DNS (через docker network). Status-page контейнер также имеет доступ через proxy-net для vhost-проверок. Решение: module healthchecks добавляются в `app.py` (как vhost checks) — контейнер на proxy-net может достучаться до `grafana:3000`, `prometheus:9090` и т.д. через Docker DNS. Это даёт live-статус при каждом открытии страницы.

---

## File Manifest

| # | File | Action | Wave | LOC Δ |
|---|------|--------|------|-------|
| 1 | `core/modules/status-page/templates/status.html` | MODIFY — +Platform Services Table, +SSL banner, +Uptime колонка, +Restart колонка, +Host load/uptime строки, +Backup status строка | W1 | +80 |
| 2 | `core/modules/status-page/app.py` | MODIFY — `_enrich_containers()` маппинг доменов, `_render_html()` новые поля, `get_all_checks()` platform service checks | W1+W3 | +60 |
| 3 | `core/internal/healthcheck/metrics/docker_collector.py` | MODIFY — добавить `started_at` в возвращаемый dict контейнера | W2 | +5 |
| 4 | `core/internal/healthcheck/metrics/host_collector.py` | MODIFY — добавить `get_host_uptime()` функцию | W2 | +30 |
| 5 | `core/internal/healthcheck/metrics/backup_collector.py` | CREATE — `get_backup_status()` проверка mtime postgres.log | W2 | +35 |
| 6 | `core/internal/healthcheck/platform_export_metrics.py` | MODIFY — вызов `get_host_uptime()` + `get_backup_status()` | W2 | +15 |
| 7 | `core/modules/status-page/templates/status.html` | MODIFY — +Platform Services Table (W3: live-curl статусы) | W3 | +40 |
| 8 | `core/modules/status-page/app.py` | MODIFY — `_curl_platform_service()` + интеграция в `get_all_checks()` | W3 | +35 |
| 9 | `tests/test_platform_export_metrics.py` | MODIFY — тесты для новых полей docker_collector + host_collector | W2 | +40 |
| 10 | `tests/test_status_page_app.py` | MODIFY — тесты template rendering с новыми данными | W1+W3 | +50 |

**Total LOC:** ~390 (80 template + 160 Python + 150 tests)

---

## Configuration DRY — cascade check

Новые поля в `status-metrics.json` требуют обновления:
1. `docker_collector.py` — добавляет поле `started_at`
2. `host_collector.py` — добавляет `get_host_uptime()` 
3. `backup_collector.py` — новый модуль
4. `platform_export_metrics.py` — вызывает новые коллекторы, расширяет `data` dict
5. `platform-export-metrics.sh` — без изменений (только PYTHONPATH/NODE_NAME, уже настроены в 066)

Все consumer'ы `status-metrics.json` (status-page app.py + тесты) backward-совместимы — новые поля опциональны, старые не удаляются.

---

## Contracts

### Contract C1: status-metrics.json schema v2.1 (backward-compatible extension)

```yaml
# Новые поля (все опциональны — consumer должен обрабатывать отсутствие):
containers[i].started_at: str | null       # ISO 8601 timestamp (из docker inspect State.StartedAt)
host.uptime_seconds: int | null            # из /proc/uptime
host.load_1m: float | null                 # из /proc/loadavg
host.load_5m: float | null
host.load_15m: float | null
host.docker_images_size_gb: float | null   # агрегировано из image_sizes dict
backup.last_postgres_at: str | null        # ISO 8601 — mtime postgres.log
backup.last_app_data_at: str | null        # ISO 8601 — mtime app-data.log
backup.status: "ok" | "stale" | "unknown"  # ok: <25h, stale: >25h, unknown: log missing
```

### Contract C2: Platform Services Table — статический список

```python
PLATFORM_SERVICES = [
    {"name": "Grafana",    "url": f"https://grafana.{PLATFORM_DOMAIN}",    "internal": "grafana:3000",     "health_path": "/api/health"},
    {"name": "Prometheus", "url": f"https://prometheus.{PLATFORM_DOMAIN}", "internal": "prometheus:9090",  "health_path": "/-/healthy"},
    {"name": "Loki",       "url": f"https://loki.{PLATFORM_DOMAIN}",       "internal": "loki:3100",        "health_path": "/ready"},
    {"name": "Hermes",     "url": f"https://hermes.{PLATFORM_DOMAIN}",     "internal": "hermes-agent:9119","health_path": "/"},
    {"name": "Langfuse",   "url": f"https://langfuse.{PLATFORM_DOMAIN}",   "internal": "langfuse:3000",    "health_path": "/api/public/health"},
    {"name": "LiteLLM",    "url": None,                                     "internal": "litellm:4000",     "health_path": "/health/readiness"},
]
```

LiteLLM не имеет внешнего URL (нет nginx vhost) — отображается с пометкой "internal only".

### Contract C3: Container→Domain mapping (best-effort эвристика)

```python
def _map_container_to_project(container_name: str, projects: list[dict]) -> str | None:
    """Map container name to project domain using best-effort heuristics."""
    for p in projects:
        pname = p.get("name", "")
        if container_name == pname or container_name.startswith(f"{pname}-"):
            return p.get("domain")
    return None
```

Это эвристика, не строгий контракт. Некоторые контейнеры (инфраструктурные: nginx, postgres, redis) не будут иметь домена — это ожидаемо.

---

## Draft Code Graph

```
┌─ W1: Template + app.py enrich ─────────────────────────────────────────────────┐
│                                                                                 │
│  status.html                                                                     │
│  ├── +SSL Summary Banner (вычисляется из projects[].days_remaining)              │
│  ├── +Platform Services Table (статический список PLATFORM_SERVICES)             │
│  ├── Containers table:                                                           │
│  │   ├── +Uptime колонка (из container.started_at)                               │
│  │   ├── +Restart колонка (из container.restart_policy)                          │
│  │   ├── Domain(s) колонка — теперь НЕ пустая (маппинг через _enrich_containers) │
│  │   └── Status колонка — показывать exit_code для exited контейнеров            │
│  └── Host Resources table:                                                       │
│      └── (W2 поля добавляются позже)                                             │
│                                                                                  │
│  app.py::_enrich_containers(containers, projects)                                 │
│  ├── ⊕ started_at → uptime_human (разница с now)                                 │
│  ├── ⊕ restart_policy → строка для шаблона                                       │
│  ├── ⊕ маппинг container.name → project.domain                                   │
│  └── ⊕ exit_code → human-readable для exited                                     │
│                                                                                  │
│  app.py::_render_html(data)                                                       │
│  ├── ⊕ ssl_min_days: int (минимальное days_remaining)                            │
│  ├── ⊕ platform_services: list[dict] (статический список)                        │
│  └── ⊕ host.uptime, host.load_* (W2 поля)                                        │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ W2: Collector additions ──────────────────────────────────────────────────────┐
│                                                                                 │
│  docker_collector.py::get_containers()                                           │
│  └── +started_at: c.get("State", {}).get("StartedAt", "")                        │
│      (уже в inspect JSON, бесплатно)                                             │
│                                                                                  │
│  host_collector.py::get_host_uptime()           [NEW FUNCTION]                    │
│  ├── /proc/uptime → uptime_seconds: float                                        │
│  └── /proc/loadavg → load_1m, load_5m, load_15m: float                           │
│                                                                                  │
│  backup_collector.py::get_backup_status()        [NEW MODULE]                     │
│  ├── /var/log/platform/backup/postgres.log → mtime                               │
│  ├── /var/log/platform/backup/app-data.log → mtime                               │
│  └── age < 25h → "ok", else "stale", missing → "unknown"                         │
│                                                                                  │
│  platform_export_metrics.py::main()                                               │
│  ├── +host.update(get_host_uptime())  (после get_host_disk)                      │
│  ├── +data["backup"] = get_backup_status()                                       │
│  ├── +host["docker_images_size_gb"] = sum(image_sizes.values()) / (1024**3)      │
│  └── +data["platform_services"] = []  (placeholder, заполняется в W3)            │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘

┌─ W3: Module healthchecks ──────────────────────────────────────────────────────┐
│                                                                                 │
│  app.py::_curl_platform_service(internal_url, health_path)   [NEW FUNCTION]      │
│  ├── subprocess.run curl (аналог _curl_vhost, но без --resolve)                  │
│  ├── internal Docker DNS: grafana:3000, prometheus:9090, etc.                    │
│  └── timeout: 5s per check                                                       │
│                                                                                  │
│  app.py::get_all_checks()                                                         │
│  └── +platform_checks: параллельный ThreadPoolExecutor (как vhost checks)        │
│                                                                                  │
│  status.html                                                                     │
│  └── Platform Services Table — каждая строка с live-статусом из curl             │
│      (PASS=зелёный, FAIL=красный, без проверки=серый)                            │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow

### W1: Template-only changes (без изменения коллекторов)

```
cron (каждую минуту)
  └→ platform_export_metrics.py
       └→ атомарная запись /run/platform/status-metrics.json  (без изменений)

HTTP GET platform.tronyx.ru/
  └→ app.py::get_all_checks()
       ├→ _load_status_metrics(STATUS_METRICS_JSON)  — существующие данные
       ├→ _load_node_yaml(NODE_YAML_PATH)             — существующие данные
       └→ vhost checks (existing)                     — существующие
  └→ app.py::_render_html(data)
       ├→ вычисление ssl_min_days из projects[].days_remaining  [NEW]
       ├→ PLATFORM_SERVICES статический список                   [NEW]
       ├→ _enrich_containers() с маппингом доменов              [MODIFIED]
       └→ Jinja2 render → status.html с новыми секциями          [MODIFIED]
```

### W2: Collector additions

```
cron (каждую минуту)
  └→ platform_export_metrics.py
       ├→ docker_collector.get_containers()         [+started_at]
       ├→ host_collector.get_host_disk()            (без изменений)
       ├→ host_collector.get_host_uptime()           [NEW — /proc/uptime + /proc/loadavg]
       ├→ backup_collector.get_backup_status()       [NEW — mtime логов]
       ├→ image_sizes aggregation → docker_images_size_gb [NEW]
       └→ атомарная запись с расширенной схемой      [MODIFIED]
```

### W3: Module healthchecks (live, при каждом запросе)

```
HTTP GET platform.tronyx.ru/
  └→ app.py::get_all_checks()
       └→ parallel curl platform services  [NEW]
            ├→ grafana:3000/api/health
            ├→ prometheus:9090/-/healthy
            ├→ loki:3100/ready
            ├→ hermes-agent:9119/
            ├→ langfuse:3000/api/public/health
            └→ litellm:4000/health/readiness
       └→ результаты → status.html Platform Services Table
```

---

## Tasks

### $TASKS

| Task ID | Description | Owner | Wave | Depends On | Output |
|---------|-------------|-------|------|------------|--------|
| TASK-1 | Template: SSL Summary Banner — Jinja2 баннер при days_remaining < 30 | Coder | W1 | — | status.html |
| TASK-2 | Template: Containers table — +Uptime, +Restart, +Domains колонки | Coder | W1 | — | status.html |
| TASK-3 | app.py: `_enrich_containers()` — маппинг container→domain, uptime_human, exit_code human-readable | Coder | W1 | — | app.py |
| TASK-4 | Template: Platform Services Table — статический список со ссылками | Coder | W1 | — | status.html |
| TASK-5 | docker_collector.py: добавить `started_at` в контейнерный dict | Coder | W2 | — | docker_collector.py |
| TASK-6 | host_collector.py: функция `get_host_uptime()` — load average + uptime | Coder | W2 | — | host_collector.py |
| TASK-7 | backup_collector.py: новый модуль `get_backup_status()` — mtime логов | Coder | W2 | — | backup_collector.py |
| TASK-8 | platform_export_metrics.py: интеграция новых коллекторов (host_uptime, backup, docker_images_size) | Coder | W2 | TASK-5, TASK-6, TASK-7 | platform_export_metrics.py |
| TASK-9 | Template: Host Resources — +Load Average, +Uptime, +Docker Images Size строки | Coder | W2 | TASK-8 | status.html |
| TASK-10 | app.py: `_render_html()` — передача новых полей host в шаблон | Coder | W2 | TASK-8 | app.py |
| TASK-11 | app.py: `_curl_platform_service()` + интеграция в `get_all_checks()` | Coder | W3 | TASK-4 | app.py |
| TASK-12 | Template: Platform Services Table — live-curl статусы (PASS/FAIL) | Coder | W3 | TASK-11 | status.html |
| TASK-13 | Tests: docker_collector + host_collector новые поля | QA | W2 | TASK-5, TASK-6 | test_platform_export_metrics.py |
| TASK-14 | Tests: backup_collector unit test | QA | W2 | TASK-7 | test_platform_export_metrics.py |
| TASK-15 | Tests: template rendering с новыми данными (Jinja2 output snapshot) | QA | W1+W2 | TASK-4, TASK-9 | test_status_page_app.py |
| TASK-16 | Tests: platform service healthcheck curls (mock subprocess) | QA | W3 | TASK-11 | test_status_page_app.py |
| TASK-17 | Gate: `make gate MODE=fast` — зелёный | QA | All | TASK-1…TASK-16 | CI |

### $PARALLEL_GROUPS

```
Wave 1 (parallel, no dependencies):
  TASK-1 ─┬─ TASK-2 ─┬─ TASK-3 ─┬─ TASK-4
          │           │           │
          └───────────┴───────────┘  (все независимы, можно в 1 PR)

Wave 2 (TASK-5,6,7 параллельны; TASK-8 зависит от них; TASK-9,10 от TASK-8):
  TASK-5 ─┐
  TASK-6 ─┼─ TASK-8 ─┬─ TASK-9
  TASK-7 ─┘           └─ TASK-10

Wave 3 (зависит от W1 TASK-4 + W2 всех):
  TASK-4 (done) ─┐
  TASK-8 (done) ─┼─ TASK-11 ─ TASK-12
                 │

Tests (после всех волн):
  TASK-13, TASK-14, TASK-15, TASK-16  (параллельно)
  └─ TASK-17 (gate)
```

---

## $TEST_SPEC

### Unit Tests (pytest native)

**TASK-13: test_docker_collector_started_at**
- Фикстура: мок `subprocess.run` для `docker inspect` с тестовым JSON (контейнер с `State.StartedAt: "2026-07-24T00:00:00.000000000Z"`)
- Assert: `container["started_at"]` содержит ISO timestamp
- Assert: контейнер без State.StartedAt → `started_at: None`

**TASK-13: test_host_collector_uptime**
- Фикстура: tmp_path с фейковыми `/proc/uptime` (12345.67) и `/proc/loadavg` (0.5 0.3 0.1 ...)
- Monkeypatch `open()` для чтения из tmp_path
- Assert: `uptime_seconds == 12345.67`, `load_1m == 0.5`, `load_5m == 0.3`, `load_15m == 0.1`
- Assert: graceful degradation → все null при отсутствии файлов

**TASK-14: test_backup_collector**
- Фикстура: tmp_path с лог-файлом (mtime = now)
- Assert: `status == "ok"`, `last_postgres_at` — валидный ISO timestamp
- Фикстура: лог-файл старше 25h → `status == "stale"`
- Фикстура: лог-файл отсутствует → `status == "unknown"`

### Integration Tests (Jinja2 template rendering)

**TASK-15: test_status_page_template_rendering**
- Фикстура: status-metrics.json с 3 проектами, 5 контейнерами, certs (1 истекающий через 5 дней)
- Вызов `_render_html()` напрямую
- Assert: HTML содержит `class="staleness-banner"` с текстом "Earliest cert expires in 5 days"
- Assert: HTML содержит Platform Services Table с 6 строками
- Assert: Containers table содержит колонку "Uptime" (заголовок)
- Assert: Containers table содержит колонку "Restart" (заголовок)
- Assert: Host Resources содержит строку "Load Average"
- Assert: Host Resources содержит строку "System Uptime"
- Assert: Backup Status строка присутствует

### Module Healthcheck Tests

**TASK-16: test_platform_service_healthchecks**
- Mock `subprocess.run` для curl → returncode=0, stdout="200"
- Assert: `_curl_platform_service("grafana:3000", "/api/health")` → `status: "PASS"`
- Mock `subprocess.run` для curl → returncode=7 (connection refused)
- Assert: → `status: "FAIL"`, `error: "curl exit 7: ..."`
- Интеграция: `get_all_checks()` включает platform service checks в общий список

### Gate Test

**TASK-17: Контракт /health не нарушен**
- `make gate MODE=fast` — все существующие тесты проходят
- Новые поля в status-metrics.json не ломают парсинг
- /health контракт без изменений: 200 PASS, 503 FAIL

---

## Execution

### Wave 1: Template-only Dashboard Enhancement

```bash
# Coder prompt:
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/067-status-page-dashboard-enhancement/DevPlan.md,
implement Wave 1: TASK-1, TASK-2, TASK-3, TASK-4

# Файлы:
#   - core/modules/status-page/templates/status.html (MODIFY)
#   - core/modules/status-page/app.py (MODIFY — _enrich_containers, _render_html)

# Верификация:
#   - Локально: python3 -c "from core.modules.status_page.app import _render_html, _enrich_containers; ..." 
#   - Шаблон: python3 -c "from jinja2 import Environment, FileSystemLoader; ..." 
```

### Wave 2: Collector Additions

```bash
# Coder prompt:
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/067-status-page-dashboard-enhancement/DevPlan.md,
implement Wave 2: TASK-5, TASK-6, TASK-7, TASK-8, TASK-9, TASK-10

# Файлы:
#   - core/internal/healthcheck/metrics/docker_collector.py (MODIFY)
#   - core/internal/healthcheck/metrics/host_collector.py (MODIFY)
#   - core/internal/healthcheck/metrics/backup_collector.py (CREATE)
#   - core/internal/healthcheck/platform_export_metrics.py (MODIFY)
#   - core/modules/status-page/templates/status.html (MODIFY)
#   - core/modules/status-page/app.py (MODIFY)

# Верификация:
#   - PYTHONPATH=. python3 core/internal/healthcheck/platform_export_metrics.py (dry-run)
#   - Проверить структуру JSON: jq '.host.load_1m, .backup.status' /run/platform/status-metrics.json
```

### Wave 3: Module Healthchecks

```bash
# Coder prompt:
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/067-status-page-dashboard-enhancement/DevPlan.md,
implement Wave 3: TASK-11, TASK-12

# Файлы:
#   - core/modules/status-page/app.py (MODIFY)
#   - core/modules/status-page/templates/status.html (MODIFY)

# Верификация:
#   - В контейнере status-page: curl localhost:8080/status.json | jq '.checks[] | select(.type == "platform_service")'
```

### QA Gate

```bash
# QA prompt:
qa Read /Users/tronyx/projects/ai-platform/.ai/plans/067-status-page-dashboard-enhancement/DevPlan.md,
run TASK-13, TASK-14, TASK-15, TASK-16, TASK-17.
Verify all acceptance criteria AC-1 through AC-10.
Write VerificationReport.md to /Users/tronyx/projects/ai-platform/.ai/plans/067-status-page-dashboard-enhancement/VerificationReport.md
```

---

## Debt Intake

| # | Item | Severity | Defer Reason | Rev Condition |
|---|------|----------|-------------|---------------|
| D1 | Container→domain mapping — эвристика, не 100% надёжна | LOW | Нет формального контракта между docker-compose service name и node.yaml project name. Полноценное решение требует поля `compose_service` в node.yaml | Если появятся проекты с нестандартными именами контейнеров — добавить маппинг в node.yaml |
| D2 | LiteLLM не имеет внешнего URL (нет nginx vhost) | LOW | LiteLLM не предназначен для прямого доступа — только через API | Если понадобится LiteLLM UI — добавить nginx vhost |
| D3 | Module healthchecks добавляют ~3-5s к рендеру страницы (6 параллельных curl с таймаутом 5s) | LOW | Приемлемо для страницы, которая открывается редко (оператором). /health endpoint не делает эти проверки | Если время рендера станет >10s — кэшировать module healthchecks в status-metrics.json (cron) |
| D4 | Docker images total size — агрегация из image_sizes dict (без учёта dangling images) | LOW | `docker system df` медленный и требует Docker daemon. Агрегация достаточна для мониторинга | Если расхождение >20% — добавить периодический `docker system df` в cron |

---

## Next Steps

1. Запустить **Wave 1** (TASK-1…TASK-4) — Coder, параллельно
2. Запустить **Wave 2** (TASK-5…TASK-10) — Coder, после Wave 1
3. Запустить **Wave 3** (TASK-11, TASK-12) — Coder, после Wave 2
4. Запустить **Tests** (TASK-13…TASK-17) — QA, после всех волн
5. `make gate MODE=fast` — финальная проверка

$END_DEVPLAN
