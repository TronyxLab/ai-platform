# 141-server-recovery — timings-hourly-r2.md (2-й цикл)

$START_TIMINGS_HOURLY_R2

> Сводки по часам (UTC) из evidence/timings.tsv. Формат: шаг — длительность / результат.

## Окно 10:40-11:40Z (13:40-14:40 MSK) — Фаза 4-5 evidence

| Время (Z) | Шаг | Длит. | rc | Примечание |
|-----------|-----|-------|----|------------|
| 10:43-10:44 | e2e-verify MODE=remote | ~2s | 2 | R6: remote-collect «unknown verb» (forced-command dispatch не пускает сырой cat) → FAIL, не skip (R4 соблюдён) |
| 10:44 | e2e-verify MODE=local | 4s | 0 | **GREEN: HTTP 4/4, TLS 4/4 (depth=4, san ok)** |
| 10:45 | certs (openssl ×4) | ~20s | 0 | Все LE, не self-signed; 86/75/86/86 дней; «из кеша» подтверждено логом бутстрапа (4× restored from S3, 0 acme-выпусков) |
| 10:45 | auth-matrix (8 хостов) | 5s | 1* | *rc=1 — 2 FAIL (litellm/minio 000, нет vhost'ов); остальные 10/12 pass; R7 (login form) закрыт |
| 10:47 | provision-llm (локальный) | ~4s | 0 | 1 ключ hermes-agent; лителлим-модели deepseek на месте |
| 10:50 | grafana-api (ds/rules/cp/policies) | 1s | 0 | 8 правил, 2 datasource, 3 CP, routing OK |
| 10:50-10:58 | prometheus-диагностика | ~8 мин | — | Скрейпы встали 09:58:56Z; TSDB отклоняет сэмплы (clock-skew наследие T4); targets 7/8 (cadvisor DNS) — запрос server-ops |
| 10:51-10:55 | alertmanager + R1-диагноз | ~4 мин | — | 2 DatasourceNoData FIRING; доставка timeout (tor/privoxy down) — не 400; POST /api/v2/alerts не поддерживается (разбор исходников 11.6.16) |
| 10:55 | loki-sweep (16 стримов) | 6s | 0 | Все контейнеры + проекты + journald; promtail 400 (skew-окно), prometheus ingest-отказы — зафиксированы |
| 10:58 | langfuse API (health/traces) | ~3s | 0 | v3.212.0, traces=0 (LLM-вызовов ещё не было) |

## Прогресс цикла (полный)

| Фаза | 1-й цикл | 2-й цикл | Дельта |
|------|----------|----------|--------|
| E2E cold start | 27 мин, 3 failed/7 passed | 28:42, **8 passed/2 failed** | бутстрап с 1-й попытки; фейлы test_02/03 (B18-окружение) |
| Chaos | — | 43:19, 3 passed/8 failed | T4/T5/T6 PASSED; T1-3/T7-11 failed (B18b/B21/B22/окружение) |
| e2e-verify | HTTP 4/4 TLS 4/4 (depth 1-4) | HTTP 4/4 TLS 4/4 (depth=4) | botanika/roadmap теперь с полной цепочкой |
| deploy-project ×4 | 11-12s | 12-13s | стабильно |
| R1 telegram | 400 (формат) | timeout (транспорт, tor/privoxy) | формат-фикс держится; остался транспорт |

## Окно 11:40-12:40Z (14:40-15:40 MSK) — ожидание закрытия P5-зависимостей

| Время (Z) | Событие |
|-----------|---------|
| 10:55-11:30 | REQ_EVIDENCE ×3 (LLM-проба, tor/privoxy+prometheus) — ответа нет; сигналы P4_VERIFY_DONE/P5_TELEGRAM_DONE/P5_OBSERVABILITY_DONE отправлены |
| 10:58-11:25 | локальная LLM-цепочка: provision ✅, POST deepseek ✅ (200, 142 токена), Loki ✅, langfuse-trace ⚠️ (dev-таймауты 422) |
| 11:28-11:29 | alertmanager продолжает ретраи доставки — по-прежнему timeout (tor/privoxy down) |
| 11:30-12:40 | все дорожки в паузе: server-ops (последнее 10:50Z), local-validation (TREE_CLEAN 08:53Z, поллинг FIXES_NEEDED), ci-ops (0 новых ранов с 07:43Z) |

## Блокеры к закрытию (повтор)

1. tor/privoxy на ноде (telegram-канал alertmanager + hermes) — REQ_EVIDENCE 10:55:30Z.
2. prometheus TSDB (рестарт контейнера) — метрики/дашборды/алерты по данным.
3. LLM-проба на ноде (litellm 127.0.0.1:4000) — REQ_EVIDENCE 10:55:00Z.

$END_TIMINGS_HOURLY_R2
