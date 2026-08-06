# 141-server-recovery — grafana-api-r2.md (2-й цикл)

$START_GRAFANA_API_R2

> Проверка: 2026-08-06 10:45-10:58Z. Клиент: grafana_client.py (basic-auth, без вывода секретов). Версия: 11.6.16.

## 1. Базовое ✅

- /api/health → HTTP 200, database=ok, version=11.6.16.
- /api/user (basic-auth) → HTTP 200 (R7 1-го цикла: basic-auth работает; login-form 401 — не регрессия, проверено: форма доступна через браузер, см. auth-matrix-r2.md).

## 2. Datasources / Alert rules / Contact-points / Policies ✅

| Проверка | Результат |
|----------|-----------|
| datasources | 2: Loki (uid=loki), Prometheus (uid=prometheus, default). Clickhouse datasource НЕ провижинится (как в 1-м цикле — не регрессия) |
| alert rules | **8 правил** загружены (Service Down, Service Down (Short), High Memory Usage, Disk Space Low, LLM API Error Rate High, Backup Freshness, Backup Upload Failure, WAL Sync Failure) — B17-фикс на месте (expr `up == bool 0` виден в provisioning) |
| contact-points | 3: Telegram Critical, Telegram Warning, grafana-default-email |
| policies | default=Telegram Warning; routes severity: critical→Telegram Critical (1s/5m), warning→Telegram Warning (5m/1h) |

## 3. Состояние правил и данных (10:55Z)

| Правило | Состояние | health |
|---------|-----------|--------|
| Service Down | pending | **nodata** |
| Service Down (Short) | **FIRING** | **nodata** |
| High Memory Usage | inactive | ok |
| Disk Space Low | inactive | ok |
| LLM API Error Rate High | inactive | ok |
| Backup Freshness | **FIRING** (реальное: backup-cron мёртв, B18b) | ok |
| Backup Upload Failure | inactive | ok |
| WAL Sync Failure | inactive | ok |

**⚠️ ПРОБЛЕМА (P5-блокер): метрики Prometheus пусты с ~09:58:56Z.**
- `up[10m]`, `count(up)`, `container_memory_usage_bytes[10m]` → series=0; `up[2h]` node-exporter: последний сэмпл 09:58:56Z.
- Targets: 8 активных, 7 up / 1 down (cadvisor — `lookup cadvisor on 127.0.0.1` — DNS-фейл внутри prometheus).
- `time()` в prometheus корректен (совпадает с реальным временем).
- Логи prometheus (через Loki): `Error on ingesting samples that are too old or are too far into the future` — **TSDB отклоняет сэмплы**.
- Диагноз: наследие chaos T4 (clock-skew +24h): head-блок TSDB содержит future-сэмплы (maxTime ≈ Aug 7 10:00Z по tsdb-status) → после восстановления часов реальные сэмплы отклоняются (out-of-order/future). Лечение: рестарт prometheus-контейнера (сброс head) или чистка TSDB. Запрошено у server-ops (REQ_EVIDENCE 10:55Z).
- Следствие: Service Down FIRING с health=nodata (cadvisor down реально, но остальные правила «ослепли»); дашборды без данных до рестарта prometheus.

## 4. Alertmanager (11.6.16, внутренний)

- GET /api/alertmanager/grafana/api/v2/alerts → **2 активных алерта**: DatasourceNoData (warning: 15s no data; critical: 1m no data) — datasource_uid=prometheus. Правило-сигнал реально сработало на пропажу данных.
- Получатели: Telegram Warning / Telegram Critical — попытки доставки ЕСТЬ (lastNotifyAttempt), но **timeout**: `failed to send telegram message: ... context deadline exceeded` (30s/10s) — канал через tor/privoxy на ноде лежит (см. telegram-alerts-r2.md).
- **POST /api/alertmanager/grafana/api/v2/alerts (создание тест-алерта) в Grafana 11.6.16 НЕ поддерживается для внутреннего AM** — маршрут зарегистрирован только для внешних AM-datasource ({DatasourceUID}); «grafana»-префикс POST → 404 "data source not found". Тело в формате `{"PostableAlerts":[...]}` (не голый массив — иначе 400 "bad request data"). Test-контакт-пойнт через /api/v1/provisioning/contact-points/{uid}/test → 404 (фича за feature-toggle, выключена). Закрытие R1 — через живые DatasourceNoData-алерты + receivers-статус.

## 5. Сводка

| Критерий | 1-й цикл | 2-й цикл |
|----------|----------|----------|
| login basic-auth | ✅ | ✅ |
| datasources | Loki+Prometheus | Loki+Prometheus (без clickhouse) |
| alert rules | 8 | 8 (B17 ок) |
| контакт-пойнты | 3 | 3 |
| доставка telegram | 400 (R1) | **timeout (tor/privoxy down)** — формат-проблема устранена |
| данные prometheus | DatasourceError-шторм (B15) | **пусто с 09:58Z (clock-skew наследие)** — нужен рестарт |

$END_GRAFANA_API_R2
