# 141-server-recovery — telegram-alerts-r2.md (2-й цикл) — закрытие R1

$START_TELEGRAM_ALERTS_R2

> Проверка: 2026-08-06 10:45-11:00Z. Каналы: alertmanager (grafana), hermes-agent, tg.sh (dev-машина).

## 1. Контакт-пойнты (через API) ✅

- 2 telegram-пойнта (Critical/Warning) + email; routing severity-based подтверждён (см. grafana-api-r2.md §2). TELEGRAM_* env в контейнере — docker inspect за server-ops (REQ 10:55Z, см. §4).

## 2. Тест-алерты (warning + critical)

| Способ | Результат |
|--------|-----------|
| POST /api/alertmanager/grafana/api/v2/alerts (голый массив) | 400 "bad request data" |
| POST ... (обёртка {"PostableAlerts":[...]}) | 404 "data source not found" |
| POST /api/v1/provisioning/contact-points/{uid}/test | 404 (feature-toggle off) |

**Вывод:** в Grafana 11.6.16 внутренний alertmanager НЕ принимает внешний POST /api/v2/alerts (маршрут только для внешних AM-datasource; разобрано по исходникам v11.6.16, pkg/services/ngalert/api/generated_base_api_alertmanager.go:437). Это НЕ регрессия — фича отсутствует в версии. Тест доставки закрыт живыми алертами (см. §3).

## 3. Живая доставка (DatasourceNoData FIRING) — R1: СТАТУС ИЗМЕНИЛСЯ

- Alertmanager обрабатывает 2 активных алерта: DatasourceNoData severity=warning (15s no-data) и critical (1m no-data), datasource_uid=prometheus.
- Попытки доставки фиксируются: Telegram Warning last=10:48:27Z dur=30s; Telegram Critical last=10:51:34Z dur=10s.
- Ошибка: **`failed to send telegram message: Post "<url>": proxyconnect tcp: dial tcp 172.17.0.1:8118: i/o timeout (Client.Timeout exceeded while awaiting headers)`** (Telegram Warning, 11:58Z) — **прокси-слушатель на 172.17.0.1:8118 (privoxy) на ноде НЕ ОТВЕЧАЕТ** (dial timeout). Critical: `context deadline exceeded` (10s). НЕ 400.
- **Сравнение с 1-м циклом (R1):**
  - 1-й цикл: «webhook response status 400» — формат сообщения (HTML-шаблон ломался) → фикс B15 (короткий plain-шаблон с escape) на месте.
  - 2-й цикл: формат-проблемы НЕТ (сообщение уходит в HTTP-вызов), блокер — **транспорт**: tor/privoxy-прокси на ноде недоступен (context deadline exceeded).
- Подтверждение канала: hermes-agent (тот же tor-прокси) — `[Telegram] Connect attempt 1-3/8 failed: Timed out` (10:55Z, Loki). dev-машина (без прокси) — **tg.sh милстоун доставлен rc=0** (10:53Z, «P4 evidence-r2...»). Токен/чат валидны; лежит прокси-цепочка на ноде.

## 4. Необходимо от server-ops (для полного закрытия R1)

1. `systemctl status tor@default privoxy` на ноде (гипотеза: down после chaos T5); поднять; alertmanager повторит доставку автоматически (repeat_interval 5m/1h).
2. `docker inspect grafana` env TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID_CRITICAL/WARNING/TELEGRAM_PROXY_URL — подтвердить непустоту (формат-фикс B14/B15).
3. После подъёма канала — проверить lastNotifyAttemptError (станет пустой) и сверить телефон оператора (сообщения от alertmanager придут в чат).

## 5. Милстоун (tg.sh)

- 10:53Z warning: «📊 P4 evidence-r2: e2e-verify GREEN...» — **доставлено rc=0** (канал dev-машины жив; лог evidence/telegram-sent.log).

$END_TELEGRAM_ALERTS_R2
