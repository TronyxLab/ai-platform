$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Восстановить отображение реальных метрик на platform.tronyx.ru — страница статуса показывает нулевые данные (0 projects, 0 containers, 0 GB disk) и возвращает 503 на /health и /status.json
DESCRIPTION:           Диагностика и исправление 4 взаимосвязанных багов: (1) status-metrics.json создаётся как директория Docker bind-mount'ом вместо файла, (2) cron экспорта метрик не работает на хосте (неверный формат crontab в контейнере + отсутствие PYTHONPATH/NODE_NAME), (3) cert_collector падает с TypeError при вычитании naive datetime из aware datetime, (4) vhost curl-проверки из status-page контейнера резолвят домены в localhost
RATIONALE:             Страница статуса — критический мониторинговый инструмент оператора. Все 4 проблемы проявились одновременно после мета-миграции (docker-health.json → status-metrics.json, schema v1→v2), но имеют разные корневые причины и фиксятся независимо
ACCEPTANCE_CRITERIA:
  AC-1: https://platform.tronyx.ru/ отображает реальные данные: ≥3 проекта, ≥20 контейнеров, disk total/free >0
  AC-2: GET /health возвращает 200 (PASS) при всех healthy контейнерах и не-stale метриках
  AC-3: GET /status.json возвращает 200 с полными данными (containers, certs, projects, host)
  AC-4: Метрики обновляются каждую минуту через host cron (generated_at свежий)
  AC-5: cert_collector не падает — certs[] содержит данные сертификатов
  AC-6: vhost curl-проверки проходят для expose:true проектов (или имеют корректный FAIL status с объяснением)
  AC-7: make gate MODE=fast зелёный
  AC-8: При пересоздании контейнера (docker compose down/up) status-metrics.json остаётся файлом, а не директорией
IMPLEMENTS:            Hotfix VPS 2026-07-23 (выполнен вручную), StatusReport 045 (projects not deployed)
IMPACTS:
  - core/modules/status-page/docker-compose.base.yml (volume mount contract)
  - core/internal/healthcheck/platform-export-metrics.sh (wrapper — PYTHONPATH + NODE_NAME)
  - core/internal/healthcheck/platform_export_metrics.py (coordinator — NODE_NAME detection)
  - core/internal/healthcheck/metrics/cert_collector.py (timezone fix)
  - core/modules/backup-cron/scripts/crontab (remove metrics export line)
  - core/internal/bootstrap/node-lifecycle.sh (bootstrap step: pre-create status-metrics.json file)
  - core/modules/status-page/app.py (protective code — handle directory-at-mount-path)
REQUIRES:
  - SSH доступ к tronyx-vps для верификации
  - Docker daemon running на VPS
  - Python cryptography library (any version, но фикс обрабатывает обе версии API)
$END_ARTIFACT_CONTRACT

---

# DevPlan 066 — Status Page Metrics Recovery

## Problem Matrix

| # | Severity | Symptom | Root Cause | Impact | Fix Type |
|---|----------|---------|------------|--------|----------|
| P1 | **CRITICAL** | `[Errno 21] Is a directory: '/run/platform/status-metrics.json'` | Docker bind mount создаёт source path как директорию, если файл не существует на момент старта контейнера. Cron ещё не создал файл → Docker создал директорию | 0 projects, 0 containers, 0 GB disk, /health=503 | Code + Bootstrap |
| P2 | **CRITICAL** | Метрики не экспортируются: `No module named 'core'`, `node.yaml not found` | (a) Cron размещён в backup-cron контейнере (не на хосте), (b) формат crontab с полем `root` (cron.d формат в user crontab), (c) отсутствует `PYTHONPATH=/opt/platform`, (d) отсутствует `NODE_NAME` | Статус-страница показывает данные только после ручного запуска | Code (wrapper) + Cron |
| P3 | **HIGH** | `cert_collector: can't subtract offset-naive and offset-aware datetimes` | `cryptography < 41.0.0` возвращает naive datetime из `cert.not_valid_after`, строка 114 вычитает aware `datetime.now(timezone.utc)` из naive | certs[] пустой на странице | Code |
| P4 | **MEDIUM** | Vhost curl: `Failed to connect to botanika.tronyx.ru:443` | Docker embedded DNS (127.0.0.11) резолвит `*.tronyx.ru` → `localhost` (CNAME), контейнер на proxy-net не может достучаться до nginx по внешнему домену | /health=FAIL (3 failed vhost checks) | Code |

---

## Fix Strategy

### Wave 1: P1 — File-vs-Directory Race Condition (bootstrap + protective code)

**Сценарий:** Docker compose up → bind mount `/run/platform/status-metrics.json` (ro) → source не существует → Docker создаёт директорию → `open()` падает с IsADirectoryError.

**Fix 1a — Bootstrap step: pre-create file before container start**

В `node-lifecycle.sh` (или `deploy-modules.sh`) добавить шаг после `mkdir -p /run/platform`:

```bash
# Pre-create status-metrics.json as empty valid JSON file
# Prevents Docker from creating it as a directory during bind mount
if [ ! -f /run/platform/status-metrics.json ]; then
    echo '{"schema_version":2,"generated_at":null,"containers":[],"certs":[],"projects":[],"host":{}}' \
        > /run/platform/status-metrics.json
fi
```

**Fix 1b — Protective code in app.py `_load_status_metrics()`**

Добавить проверку, что path — это файл, а не директория:

```python
def _load_status_metrics(path: str) -> dict:
    # Protective: Docker bind mount может создать path как директорию
    if not os.path.isfile(path):
        print(f"[IMP:8][status-page][load-metrics] Path is not a file: {path}", file=sys.stderr)
        return {
            "generated_at": None,
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {},
            "errors": [f"status-metrics.json not found or is a directory at {path}"],
        }
    try:
        with open(path) as f:
            ...
```

### Wave 2: P2 — Cron Environment Fix

**Fix 2a — Wrapper script: auto-detect PYTHONPATH and NODE_NAME**

`core/internal/healthcheck/platform-export-metrics.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-detect platform root: /opt/platform (canonical) or 3 levels up from script
if [ -z "${PLATFORM_ROOT:-}" ]; then
    PLATFORM_ROOT="/opt/platform"
    [ -d "$PLATFORM_ROOT" ] || PLATFORM_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

# Auto-detect NODE_NAME from node-configs directory (fallback: "unknown")
if [ -z "${NODE_NAME:-}" ]; then
    NODE_NAME=$(ls /opt/node-configs/ 2>/dev/null | grep -v secrets | head -1)
    [ -z "$NODE_NAME" ] && NODE_NAME="unknown"
fi

# Set PYTHONPATH so 'from core.internal.healthcheck.metrics...' works
export PYTHONPATH="${PLATFORM_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export NODE_NAME

# Ensure cache + output directories exist on tmpfs (empty after reboot)
mkdir -p /run/platform /var/cache/platform/metrics

# Protective: ensure status-metrics.json is a file, not a directory (P1 safeguard)
if [ -d /run/platform/status-metrics.json ]; then
    rmdir /run/platform/status-metrics.json 2>/dev/null || true
fi

exec python3 "${SCRIPT_DIR}/platform_export_metrics.py" "$@"
```

**Fix 2b — Host crontab (already applied via hotfix, document for bootstrap)**

Добавить в node-lifecycle.sh или setup-node.sh шаг, устанавливающий host cron:

```bash
# Install metrics export cron on HOST (not in backup-cron container)
METRICS_CRON_LINE='* * * * * /opt/platform/core/internal/healthcheck/platform-export-metrics.sh >> /var/log/platform/backup/metrics-export.log 2>&1'
if ! crontab -l 2>/dev/null | grep -q 'platform-export-metrics'; then
    (crontab -l 2>/dev/null; echo "$METRICS_CRON_LINE") | crontab -
fi
```

**Fix 2c — Remove metrics export from backup-cron container crontab**

Удалить строку 49 из `core/modules/backup-cron/scripts/crontab`:

```diff
- # [IMP:8][crontab] * * * * * — Platform metrics export (status page data, replaces docker-healthcheck.sh)
- * * * * *   root flock -n /run/lock/platform-metrics.lock timeout 50s /opt/platform/core/internal/healthcheck/platform-export-metrics.sh >> /var/log/platform/backup/metrics-export.log 2>&1
```

Метрики должны экспортироваться **только** через host cron, не через контейнер.

### Wave 3: P3 — cert_collector Timezone Fix

`core/internal/healthcheck/metrics/cert_collector.py`, строки 111–117:

```python
# Not After (ISO 8601)
not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after
if isinstance(not_after, datetime):
    # Normalise offset-naive → offset-aware UTC
    # cryptography < 41.0.0 returns naive datetime (always UTC, just without tzinfo)
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    not_after_iso = not_after.strftime("%Y-%m-%dT%H:%M:%SZ")
    days_remaining = (not_after - datetime.now(timezone.utc)).days
else:
    not_after_iso = str(not_after)
    days_remaining = 0
```

### Wave 4: P4 — Vhost Curl Internal Resolution

**Вариант A (быстрый):** Добавить `extra_hosts` в docker-compose.base.yml для резолва production-доменов:

```yaml
# platform-vhost.conf.template proxies all vhosts through nginx
# Internal curl must reach nginx by external domain
extra_hosts:
  - "botanika.tronyx.ru:172.18.0.2"
  - "www.tronyx.ru:172.18.0.2"
  - "sexydancerostov.ru:172.18.0.2"
```

**Вариант B (правильный):** Использовать Docker DNS resolver с явным DNS-сервером. Но это требует знания IP nginx контейнера.

**Вариант C (архитектурный):** Изменить curl-проверку на использование внутреннего Docker хоста:

```python
# curl internal nginx with Host header instead of external domain
subprocess.run(
    ["curl", "-sSk", "-o", "/dev/null", "-w", "%{http_code}",
     "--max-time", str(timeout),
     "--resolve", f"{domain}:443:nginx",  # resolve to nginx container
     f"https://{domain}"],
    ...
)
```

`--resolve` директива curl решает проблему DNS, отправляя запрос на nginx контейнер с правильным SNI/Host заголовком.

**Выбор:** Вариант C (архитектурно чистый — не требует hardcoded IP, использует Docker service discovery).

---

## File Manifest

| # | File | Action | Wave |
|---|------|--------|------|
| 1 | `core/internal/healthcheck/platform-export-metrics.sh` | **Modify** — auto-detect PYTHONPATH + NODE_NAME, protective directory check, <50 lines | W2 |
| 2 | `core/internal/healthcheck/metrics/cert_collector.py` | **Modify** — timezone normalization (3 lines after line 111) | W3 |
| 3 | `core/modules/backup-cron/scripts/crontab` | **Modify** — remove metrics export line (2 lines) | W2 |
| 4 | `core/modules/status-page/app.py` | **Modify** — add `os.path.isfile()` check in `_load_status_metrics()` (5 lines before line 107) | W1 |
| 5 | `core/modules/status-page/docker-compose.base.yml` | **No change needed** — mount contract stays, fixed by bootstrap pre-create + protective code | — |
| 6 | `core/internal/bootstrap/node-lifecycle.sh` | **Modify** — add pre-create step for status-metrics.json + host cron installation | W1+W2 |
| 7 | `core/modules/status-page/app.py` (vhost curl) | **Modify** — use `--resolve` in `_curl_vhost()` (1 line change) | W4 |

---

## Test Plan

**New tests:**
- `tests/test_cert_collector.py::test_not_valid_after_naive_datetime` — verify naive datetime is normalized to aware before subtraction
- `tests/test_status_page.py::test_load_metrics_directory_at_path` — verify `_load_status_metrics()` returns empty data when path is a directory
- `tests/test_platform_export_metrics.py::test_auto_detect_node_name` — verify NODE_NAME auto-detection from /opt/node-configs/

**Modified tests:**
- `tests/gates/test_gate_status_page.py` — update for new crontab contract (no metrics line in backup-cron)

**Gate verification:**
```bash
make fix-gate && make gate MODE=fast
```

---

## Implementation Sequence

1. **Wave 3 (P3):** cert_collector timezone fix — 3 строки, независимый, сразу даёт certs в metrics
2. **Wave 2 (P2):** Wrapper script fix — убирает зависимость от cron env vars, авто-детект
3. **Wave 2 (P2):** Remove metrics from backup-cron crontab — предотвращает spurious cron errors
4. **Wave 2 (P2):** Bootstrap cron installation step — гарантирует установку после пересоздания ноды
5. **Wave 1 (P1):** Bootstrap pre-create step + app.py protective code — предотвращает race condition
6. **Wave 4 (P4):** Vhost curl --resolve fix — восстанавливает live vhost проверки
7. **Тестирование:** `make test` + деплой на VPS через `make bootstrap-node NODE=tronyx-vps` (или целевой scp affected files)
8. **Верификация:** `curl -u admin@tronyx.ru:... https://platform.tronyx.ru/`, `curl .../health`, `curl .../status.json`

---

## VPS Hotfix Already Applied (2026-07-23)

Следующие исправления уже применены вручную на VPS и **должны быть закреплены в коде**:

1. ✅ `/run/platform/status-metrics.json` — директория удалена, создан валидный JSON-файл с placeholder данными
2. ✅ Host crontab — добавлена строка `* * * * * cd /opt/platform && NODE_NAME=tronyx-vps PYTHONPATH=/opt/platform flock -n ... platform-export-metrics.sh`
3. ✅ Status-page контейнер пересоздан (docker rm -f + docker compose up) для перемонтирования файла
4. ✅ Метрики собираются корректно при ручном запуске с правильными env vars

**Оставшиеся проблемы после hotfix:**
- certs[] пустой (P3 — timezone bug)
- /health возвращает 503 из-за 3 failed vhost checks (P4)
- При следующем `docker compose down/up` может снова возникнуть P1 без bootstrap-защиты

$END_DEVPLAN
