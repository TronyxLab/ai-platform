# Grafana API Evidence — сессия 141 (ночная, tronyx-vps recovery)

> Трек evidence: /Users/tronyx/projects/ai-platform/.ai/plans/141-server-recovery/evidence/
> Хост: https://grafana.tronyx.ru → 103.88.243.151 (DNS через 8.8.8.8 подтверждён 01:52 MSK)
> Клиент: /var/folders/.../141-secrets/grafana_client.py (вне репо, секреты не печатает)
> Ожидания из репозитория: datasources.yml (Prometheus uid=prometheus isDefault + Loki uid=loki),
> 8 dashboards в папке "AI Platform", 8 alert-rules 140 W2 в группе "AI Platform Alerts",
> contact-points "Telegram Critical"/"Telegram Warning", routing-политики severity-based.

## Статус

- [ ] Grafana доступен (health)
- [ ] login (admin) — /api/user
- [ ] datasources: prometheus, loki (+clickhouse если провижинится)
- [ ] alert rules 140 W2 загружены без ошибок
- [ ] dashboards ≥4 (ожидается 8)
- [ ] contact-points (Telegram Critical/Warning)
- [ ] notification policies (routing)

## Пробы доступности

| # | UTC время | Результат | Замер (timings.tsv) |
|---|-----------|-----------|---------------------|
| 1 | 01:53 MSK | HTTP 000 (curl, соединение не установлено) | probe/grafana-https-1 |
| 2 | 01:55 MSK | HTTP -1 URLError Errno 54 Connection reset by peer (клиент ОК) | probe/grafana-health-1 |
| 3 | 01:58 MSK | HTTP -1 Errno 54 Connection reset by peer | probe/grafana-health-2 |
| 4 | 01:59 MSK | HTTP -1 Errno 54 Connection reset by peer | probe/grafana-health-3 |

**BLOCKED (сервис ещё не поднят)** — серия проб 01:53–01:59 MSK, порт 443 сбрасывает соединение.
Продолжаю пробы с интервалом ~5 мин, параллельно готовлю шаблоны PromQL/Loki.

## Хронология сетевого состояния

| Время (MSK) | Состояние | Детали |
|-------------|-----------|--------|
| 02:00 | nginx 502 | веб-слой поднят, upstream grafana ещё стартует (probe health-5..8) |
| 02:03–02:06 | Connection reset | nginx перезапущен во время deploy_services (context_deployer reload) |
| 02:19 | e2e-тест завершён: 3 failed / 7 passed | context_deploy botanika/legacy/roadmap FAILED (деплой проектов) — дорожка оператора |
| 02:20 | Порты 80/443/3000/9090/3100 OPEN (nc) | стек поднят, но TLS ClientHello → Connection reset (curl err 35); HTTP на 80/3000/9090 тоже reset |
| 02:20 | DNS всех platform-поддоменов → 103.88.243.151 | OK (через 8.8.8.8) |

**Гипотеза (не диагноз):** nginx reload после деплоя контекстов оставил веб-слой в состоянии reset — вероятная причина отсутствие cert'ов platform-поддоменов (в S3 кеше их не было, фаза 0 StatusReport) или сломанный reload. Дорожка оператора — фиксирую факты.

## Фаза 2 — оператор починил веб-слой (03:08–03:18 MSK)

| Время (MSK) | Состояние | Детали |
|-------------|-----------|--------|
| 03:08 | 502 (снова) | nginx поднялся (cert'ы/конфиг починены оператором) — upstream grafana не готов |
| 03:08–03:15 | 502 × 8 проб | grafana:3000 недоступен стабильно |
| 03:06–03:15 | bootstrap-node повторный | converge_services success, Bootstrap complete — но grafana всё равно 502 |
| 03:14 | bootstrap-node лог | Healthcheck PASS: 0 (контейнеры платформы не healthy) |

**Наблюдение:** контейнеры платформы (grafana и др.) не в состоянии serve — nginx даёт 502. Возможные причины (не диагноз): crash-loop контейнеров после переустановки (volume/конфиг), или provisioning alert-rules 140 W2 блокирует старт grafana. Проверю `/api/health` графаны напрямую через 3000, когда nginx отдаст запрос — пока 502.

## Порядок действий при первом успешном ответе (health 200)

1. `health` — database/version
2. `user` — login/role (admin-авторизация)
3. `datasources` — Prometheus/Loki/ClickHouse
4. `alert-rules` — 8 правил 140 W2 (group "AI Platform Alerts")
5. `contact-points` — Telegram Critical/Warning
6. `policies` — routing severity-based
7. `dashboards` — ≥4 (ожидается 8, папка "AI Platform")
8. `promql up` — все targets
9. `loki_sweep.sh` — логи всех контейнеров (в loki-queries.md)

## Пробы после 03:08 (все health)

| # | Время | Результат |
|---|-------|-----------|
| 23 | 03:08 | 502 |
| 24 | 03:09 | 502 |
| 25 | 03:10 | 502 |
| 26 | 03:11 | 502 |
| 27 | 03:14 | 502 |
| 28 | 03:17 | 502 |
| 29 | 03:21 | 502 |
| 30 | 03:26 | 502 |
| 31 | 03:31 | 502 |
| 32 | 03:36 | 502 |

**Наблюдение (03:36):** grafana недоступна ~30 мин после повторного bootstrap-node (converge success, но контейнер не отвечает). Стабильный 502 = upstream (grafana:3000) не слушает — контейнер не жив/не в сети. Продолжаю пробы; фикс pull-retry (commit 2665a866) уже в дереве — вероятно, будет повторный деплой контекстов, стек поднимется после.

## Фаза 3 — crash-loop платформенного стека (04:10–04:20 MSK)

| Время (MSK) | Событие |
|-------------|---------|
| 04:17 | **Живое окно:** /api/health HTTP 200 (db=ok, ver=11.6.16) — единственный успех за сессию |
| 04:17 | /api/user + /api/datasources → TimeoutError (20s) — grafana зависла на БД-запросах |
| 04:18 | Все эндпоинты → 502 (включая /api/health): grafana упала |
| 04:15–04:19 | 3× grafana_sweep — все 10 проверок 502 |
| 04:19 | Прямые порты 3000/9090/3100 OPEN (docker-proxy), но HTTP 000 — контейнеры не отвечают |

**Диагноз-факт (для оператора):** grafana в crash-loop — короткие окна жизни (~1 мин), падает на БД-зависимых запросах. Весь платформенный стек (prometheus, loki тоже) не отвечает на прямых портах. Жив только nginx (502). Вероятная первопричина — общая для стека (postgres/БД-бэкенд или окружение), точный диагноз — на сервере (эксклюзив оператора).

## Фаза 5 — ПОЛНЫЙ СБОР ДАННЫХ В ЖИВОМ ОКНЕ (08:36–08:37 MSK, window-catcher)

Окно 05:36:27Z–05:36:44Z (08:36:27–08:36:44 MSK) — данные с HTTP 200:

### login / user ✅
- `login=tronyx@mail.ru`, `email=admin@localhost`, `isGrafanaAdmin=true`, orgId=1, создан 2026-08-05T23:08:18Z

### datasources ✅ (2 шт., ClickHouse НЕ провижинится — ожидаемо, dashboard читает из loki/prometheus)
| name | type | uid | url | default | readOnly |
|------|------|-----|-----|---------|----------|
| Prometheus | prometheus | prometheus | http://prometheus:9090 | ✅ | ✅ |
| Loki | loki | loki | http://loki:3100 | ❌ | ✅ |

### contact-points ✅ (3 шт., provisioning file)
| name | uid | type | disable_notifications |
|------|-----|------|----------------------|
| Telegram Critical | telegram-critical | telegram | false (immediate) |
| Telegram Warning | telegram-warning | telegram | true (grouped) |
| email receiver | (пусто) | email | example@email.com (дефолтный) |

### notification policies ✅ (routing severity-based)
- default receiver: **Telegram Warning**, group_by [alertname, severity]
- route severity=critical → **Telegram Critical** (group_wait 1s, group_interval 1s, repeat 5m)
- route severity=warning → **Telegram Warning** (group_wait 5m, group_interval 5m, repeat 1h)
- provenance: file

### alert-rules ✅ (140 W2 загружены!)
- ruleGroup: **"AI Platform Alerts"**, folderUID=dfuc3djcnp8u8b, provenance=file
- `service_down` — "Service Down": expr `up == 0`, for 1m, severity=critical, datasourceUid=prometheus
- остальные правила в окне не успели собраться (обрезано) — дособрать в следующем окне

### dashboards (частично)
- "AI Overview" (uid=ai-overview, uri db/ai-overview) — список обрезан, дособрать

### Дополнительные окна 08:41–08:42 MSK (dashboards частичные)
- Папка: **AI Platform** (folderUid=dfuc3djcnp8u8b)
- Видны: AI Overview, ClickHouse, DORA CI/CD Metrics, Infrastructure (tags: ai-platform/llm/overview, clickhouse, ci-cd/dora/github-actions)
- Ожидается 8 (включая LLM Usage Breakdown, Logs & Incident Inspector, Redis, $PROJECT template)
- Окна участились (каждые ~30s–5 мин) — grafana стабилизируется; catcher v3 с полными ответами (лимит 8000) перезапущен.

**chat_id контакт-пойнтов замаскирован в этом файле** (в сыром логе catcher присутствует — не секрет, но политика evidence).

## Фаза 6 — СТАБИЛИЗАЦИЯ (09:08–09:15 MSK) ✅

- Grafana стабильно отвечает 200 (3×200 за 90s; sweep 10/10 проверок 200).
- **health**: db=ok, ver=11.6.16
- **user**: tronyx@mail.ru, GrafanaAdmin
- **datasources**: Prometheus (default) + Loki — 2 шт. (ClickHouse не провижинится — по дизайну)
- **folders**: AI Platform (dfuc3djcnp8u8b)
- **dashboards**: 7 шт. — AI Overview, ClickHouse, DORA CI/CD Metrics, Infrastructure, LLM Usage Breakdown, Logs & Incident Inspector, Redis (cache). Project-шаблон "$PROJECT — $TYPE Overview" НЕ появился после деплоя контекстов (наблюдение: возможно, рендерится отдельным каналом render-monitoring)
- **contact-points**: Telegram Critical, Telegram Warning, email receiver
- **alert-rules**: **8/8 правил 140 W2** — Service Down, Service Down (Short), High Memory Usage, Disk Space Low, LLM API Error Rate High, Backup Freshness, Backup Upload Failure, WAL Sync Failure (группа "AI Platform Alerts", provenance=file)
- **policies**: default=Telegram Warning; severity=critical→Critical (1s/5m), severity=warning→Warning (5m/1h)
- **promql `up`**: 8 targets — 7 UP, **cadvisor=0 (DOWN)** ⚠️
- **`container_memory_usage_bytes{compose_project="platform"}`: 0 series** ⚠️ — HighMemory/HighCPU alert-правила не смогут срабатывать, пока cadvisor не поднят
- **node_filesystem_avail_bytes{mountpoint="/"}**: 66 GB свободно ✅ (DiskSpace rule ok)
- **Loki**: все контейнеры + проекты пишут (см. loki-queries.md)

## Финальный статус

- [x] login (admin) — ✅ tronyx@mail.ru, GrafanaAdmin
- [x] datasources — ✅ Prometheus (default) + Loki
- [x] alert rules 140 W2 — ✅ 8/8 загружены без ошибок
- [x] contact-points — ✅ Telegram Critical/Warning + email
- [x] notification policies — ✅ severity-routing
- [x] dashboards — ✅ 7 шт. (ожидаемые 8 минус per-project шаблон)
- [x] Grafana стабильна — ✅ (crash-loop преодолён оператором: #69950 chatid workaround)

## Главная находка для оператора

**cadvisor DOWN (up=0)** — метрики `container_*` (container_memory_usage_bytes и др.) отсутствуют в Prometheus → alert-правила HighMemoryUsage/HighCPUUsage (140 W2) и контейнерные панели Infrastructure не работают. Требуется поднять/починить cadvisor-контейнер.

## Фаза 4 — длительное мёртвое состояние (04:20–06:55 MSK)

- Единственное живое окно: 04:17 MSK (health 200). После — стабильный 502.
- Окна жизни редки (~1/час) и короткие; в окнах ответы медленные (запросы виснут >60s — nginx ждёт медленный upstream, proxy_read_timeout).
- Прямые порты 3000/9090/3100 OPEN (docker-proxy), HTTP 000.
- Оператор: 2 коммита fix(141) (pull retry; certificates dep-gate B4/TOR_ENABLED B5), незакоммиченные правки context_deployer (stub compose). Активность логов: до 06:14 MSK; ci_poller тикает до 06:51 MSK.
- Запущен фоновый window_catcher (каждые 20s health → при 200 полный sweep в window-catcher.log).
- Проверки verify-sweep.json: отсутствует (e2e-verify ещё не запускался).

## Замеры API (когда стек поднимется)

(будут заполнены)

## Шаблоны PromQL (через /api/datasources/proxy/uid/prometheus/api/v1/query)

| Проверка | Запрос |
|----------|--------|
| Все targets | `up` |
| Контейнеры платформы | `container_memory_usage_bytes{compose_project="platform"}` |
| CPU контейнеров | `rate(container_cpu_usage_seconds_total{compose_project="platform"}[5m])` |
| Стек/хост | `node_boot_time_seconds`, `node_filesystem_avail_bytes` |
| Loki healthy | `loki_request_duration_seconds_count` (если экспортёр настроен) |
| ClickHouse | `clickhouse_*` (если метрики экспортируются) |

## Выводы

(будут заполнены)
