# 141-server-recovery — loki-r2.md (2-й цикл)

$START_LOKI_R2

> Проверка: 2026-08-06 10:55-10:58Z через grafana-прокси (Loki datasource uid=loki). Запросы — evidence/loki-sweep-r2.txt.

## Вердикт: ✅ ЛОГИ ОТ ВСЕХ КОНТЕЙНЕРОВ (docker_sd + json-file работают); 2 замечания (см. §2)

| Стрим | Стримов | Сэмплы | Статус |
|-------|---------|--------|--------|
| {compose_service="nginx"} | 4 | свежие (10:54Z, мои API-вызовы видны) | ✅ |
| {compose_service="postgres"} | 1 | checkpoint, ready | ✅ |
| {compose_service="redis"} | 2 | ready, WARN overcommit | ✅ |
| {compose_service="clickhouse"} | 2 | startup | ✅ |
| {compose_service="minio"} | 1 | startup (9001/9000) | ✅ |
| {compose_service="litellm"} | 1 | /health/liveliness 200, /metrics | ✅ |
| {compose_service="langfuse"} | 1 | startup, MCP features | ✅ |
| {compose_service="promtail"} | 2 | targets added (docker_sd!) | ✅ |
| {compose_service="hermes-agent"} | 3 | **Telegram connect TimedOut ×3** (см. §2) | ⚠️ |
| {compose_service="loki"} | 2 | query logs, таблицы | ✅ |
| {compose_service="prometheus"} | 1 | **"too old or too far into the future"** (см. §2) | ⚠️ |
| {compose_project="tronyx-site"} | 1 | access-logs 200 (10:44-10:48Z) | ✅ |
| {compose_project="dance-site"} | 1 | access-logs 200 (10:45-10:52Z, real-user) | ✅ |
| {compose_project="botanika"} | 1 | access-logs 200 (10:51Z, Windows-browser real-user) | ✅ |
| {compose_project="roadmap"} | 1 | /health 200 каждые 10s (wget) | ✅ |
| {host="tronyx-vps"} (journald) | 1 | UFW BLOCK, cron pam | ✅ (лейбл host, не job — как в 1-м цикле) |

## §2. Замечания (общий корень — наследие chaos)

1. **promtail→loki 400** (`final error sending batch status=400`, 10:22-10:34Z) — часть батчей отклонена (таймстампы из окна clock-skew T4). После 10:36Z ошибок нет (targets added — docker_sd перечитан).
2. **prometheus** `Error on ingesting samples that are too old or are too far into the future` (10:55Z, живой) — TSDB-головка содержит future-сэмплы из окна skew; реальные сэмплы отклоняются → метрики пусты (см. grafana-api-r2.md §3).
3. **hermes-agent**: `[Telegram] Connect attempt 1-3/8 failed: Timed out` — агент не может достучаться до api.telegram.org (канал через tor/privoxy ноды down — см. telegram-alerts-r2.md).

## §3. Выводы

- Пустых стримов НЕТ (все 16 стримов живы; journald через host-лейбл — документированное отличие, не баг).
- Реальный пользовательский трафик виден в Loki (botanika 10:51Z, dance-site 10:52Z) — цепочка nginx→promtail→loki работает.
- R-остаток 1-го цикла по Loki не выявлен; новые замечания — следствие chaos T4/T5 (server-ops восстанавливает).

$END_LOKI_R2
