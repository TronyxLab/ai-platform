$START_STATUS_REPORT
# 01-StatusReport.md — Stack Startup Report (tronyx-lab context)

## Section 1 — Diagnostic Summary

### Environment Fingerprint
- **Host:** localhost (macOS Darwin, Apple Silicon)
- **Shell:** /bin/zsh
- **Docker:** Docker Engine v28.4.0, Compose v2.x
- **Platform root:** `/Users/tronyx/projects/ai-platform`
- **Context (из .env):** `tronyxlab` (CONTEXT=tronyxlab)
- **Context image:** `ghcr.io/tronyxlab/hermes-agent-context:v2026.7.1`
- **Dev override:** L1 (hermes-agent-base:latest), CONTEXT_IMAGE=""
- **Compose profiles:** Все 12 модулей

### Overall Verdict
**PARTIAL** — стек запущен, все 22 контейнера здоровы, но выявлены ошибки в процессе запуска. Необходим фикс provision-пайплайна для idempotent startup.

---

### Issues

| # | Severity | Component | Issue |
|---|----------|-----------|-------|
| 1 | **CRITICAL** | `core/internal/scripts/yaml_query.py` (L184) | `yaml_get_field()` возвращает Python `repr()` (в одинарных кавычках) для dict/list типов вместо валидного JSON — ломает `_provision_networks()` |
| 2 | **HIGH** | `litellm` | `prisma migrate deploy` блокирует startup proxy на ~40-60с, healthcheck флапает, license.verification 404 |
| 3 | **MEDIUM** | `grafana` | Dashboard `project-template.json` — UID содержит illegal characters |
| 4 | **MEDIUM** | `grafana` | Файлы с неподдерживаемыми суффиксами: `.disabled`, `.telegram` |
| 5 | **LOW** | `grafana` | SQLite database locked (норма для startup, self-resolving) |

---

## Section 2 — Actions Taken

### Preflight
- Docker info: ✅
- docker compose ps --all: empty (clean state)

### Mutations
1. **manual `docker network create`** (workaround for issue #1):
   - `proxy-net`, `shared-db-net`, `shared-cache-net`, `hermes-agent-net`, `observability-net`, `backup-net`
   - **Причина:** `make up` упал с `network observability-net declared as external, but could not be found`
   - **Root cause:** `yaml_query.py` возвращает Python repr вместо JSON → provision loop не создаёт сети

2. **`make up`** — успешный запуск 22 контейнеров (включая init-контейнеры)

### Health Check Results (final, after 3 минут)
| Container | Status | Endpoint | HTTP |
|-----------|--------|----------|------|
| postgres | healthy | — | — |
| pgbouncer | healthy | — | — |
| redis | healthy | — | — |
| clickhouse | healthy | — | — |
| minio | healthy | :9001 | 200 |
| litellm | healthy | :4000/health | 401 (требует auth) |
| langfuse | healthy | — | — |
| langfuse-redis | healthy | — | — |
| hermes-agent | healthy | :9119 (dashboard) | — |
| nginx | healthy | :80 | 200 |
| grafana | healthy | :3000/api/health | 200 |
| prometheus | healthy | :9090/-/healthy | 200 |
| loki | healthy | :3100/ready | 200 |
| cadvisor | healthy | :8080 | — |
| node-exporter | healthy | :9100 | — |
| nginx-prometheus-exporter | healthy | — | — |
| postgres-exporter | healthy | — | — |
| redis-exporter | healthy | — | — |
| backup-cron | healthy | — | — |
| promtail | healthy | — | — |
| prometheus-config-init | Exited (0) | init-контейнер | — |
| minio-createbuckets | Exited (0) | init-контейнер | — |

---

## Section 3 — Audit Trail

| Time | Action | Result | Rationale |
|------|--------|--------|-----------|
| 15:27Z | `docker info` | OK | Docker running |
| 15:27Z | `docker compose ps` | Empty | Clean state |
| 15:28Z | `make up` (1st attempt) | FAIL | `network observability-net declared as external, but could not be found` |
| 15:28Z | `docker network create` (×6) | Created | Workaround #1 — provision script не создал сети |
| 15:28Z | `bash -x core/internal/provision-environment.sh --scope networks` | debug output | Установлена причина: `yaml_query.py` возвращает Python repr вместо JSON |
| 15:29Z | `make up` (2nd attempt) | SUCCESS | All containers started |
| 15:33Z | Healthcheck poll | Все healthy | За исключением litellm (флапал ~60с из-за prisma migrate) |
| 15:34Z | litellm recheck | Healthy | prisma migrate завершён |
| 15:35Z | Endpoint checks | All 200 | Grafana 200, Nginx 200, Prometheus 200, Loki 200, Minio 200, Litellm 401 (auth req) |

### Deviations from plan
- No plan existed — ad-hoc diagnostic session.
- Networks created manually instead of via provision script.

---

## Section 4 — Legalization Tasks

| # | What | Why | When | TRAP[DECISION] | Status |
|---|------|-----|------|-----------------|--------|
| 1 | Manual `docker network create` | Workaround для бага `yaml_query.py` | 2026-07-21T15:28Z | — | **PENDING** (требуется фикс provision) |

---

## Root Cause Analysis

### Issue 1 (CRITICAL): `yaml_query.py` Python repr вместо JSON

**Файл:** `core/internal/scripts/yaml_query.py`, функция `_cli()`, строка 184

**Код:**
```python
if args.json_output:
    print(json.dumps(value))
else:
    print(value)  # ← bug: для list/dict выводит Python repr
```

**Проблема:** `print(value)` для списка сетей выводит:
```
[{'name': 'proxy-net', 'driver': 'bridge'}, ...]
```
Вместо валидного JSON:
```
[{"name": "proxy-net", "driver": "bridge"}, ...]
```

**Цепочка отказа:**
1. `_load_platform_env_yaml()` → `yaml_get_field()` → `yaml_query.py --get networks` → Python repr
2. `networks_json` содержит невалидный JSON
3. `echo "$networks_json" | python3 -c "import json; json.load(sys.stdin)"` → fail (single quotes)
4. while loop в `_provision_networks()` итерирует 0 раз
5. "0 created, 0 skipped"
6. `docker compose up -d` падает с `network X declared as external, but could not be found`

**Функция `_format_item()` (строка 123-127) уже правильно использует `json.dumps()` для --items флага. Проблема только в режиме --get без --items.**

**Fix:** заменить `print(value)` на `print(json.dumps(value))` когда `value` — `dict` или `list`.

### Issue 2 (HIGH): litellm prisma migrate долгий startup

**Причина:** LiteLLM proxy запускает `prisma migrate deploy` перед тем, как начать слушать порт. Миграция PostgreSQL занимает 40-60 секунд на пустой БД. Healthcheck (start_period=60s) срабатывает почти на грани.

**Влияние:** non-fatal — после завершения миграции proxy стартует и становится healthy. Однако license verification (404) может указывать на проблемы сети/доступа к external API.

### Issue 3 (MEDIUM): Grafana dashboard UID validation

**Лог:** `error="failed to save dashboard" file=/etc/grafana/provisioning/dashboards/project-template.json error="uid contains illegal characters"`

**Причина:** UID в dashboard JSON содержит символы, не разрешённые Grafana.

**Влияние:** Одна дашборда не загрузилась, остальные работают.

### Issue 4 (MEDIUM): Grafana contact-points file suffixes

**Файлы:** `contact-points.yml.disabled` и `contact-points.yml.telegram` — имеют неподдерживаемые суффиксы. Grafana принимает только `.yaml`, `.yml`, `.json`.

**Влияние:** Файлы игнорируются, контакт-поинты не настроены.

---

## Next Steps

```bash
# 1. Зафиксировать provision:
#    - yaml_query.py: изменить print(value) на условный json.dumps для dict/list
#    - После фикса: make down && docker network rm proxy-net shared-db-net ... && make up
#    - Проверить, что provision создаёт сети автоматически

# 2. Проверить litellm:
#    - Убедиться, что LITELLM_LICENSE не пуст (если есть лицензия)
#    - Проверить логи prisma migrate на ошибки подключения к postgres

# 3. Починить Grafana дашборды:
#    - Проверить core/modules/monitoring/config/grafana/provisioning/dashboards/project-template.json
#    - Исправить UID
#    - Переименовать contact-points.yml.telegram → contact-points.yml (или удалить)

# 4. Для проверки полного цикла:
#    make healthcheck
```
$END_STATUS_REPORT
