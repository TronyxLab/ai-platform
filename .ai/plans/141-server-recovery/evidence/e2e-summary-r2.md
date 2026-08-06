# 141-server-recovery — e2e-summary-r2.md (2-й цикл)

$START_E2E_SUMMARY

> Дата: 2026-08-06 13:44 MSK (10:44Z), после P4_STACK_UP (25/25 healthy).
> Команда: `make e2e-verify NODE=tronyx-vps MODE=remote JSON=1` → fallback `MODE=local JSON=1` (R6).
> JSON: evidence/e2e-verify-r2.json (MODE=local), лог remote: evidence/e2e-verify-r2.log.

## Вердикт: ✅ GREEN — HTTP 4/4 pass, TLS 4/4 ok

| fqdn | HTTP | TLS chain | SAN | days_left | TLS verdict |
|------|------|-----------|-----|-----------|-------------|
| tronyx.ru | 200 | 4 | ✅ | 86 | ok |
| sexydancerostov.ru | 200 | 4 | ✅ | 75 | ok |
| botanika.tronyx.ru | 200 | 4 | ✅ | 86 | ok |
| roadmap.tronyx.ru | 200 | 4 | ✅ | 86 | ok |

- `collect_errors: []`, exit 0. legacy пропущен (домен не задан — ожидаемо).
- Сравнение с 1-м циклом: **botanika/roadmap chain_depth 1 → 4** (сейчас отдают wildcard tronyx.ru с полной цепочкой, см. certs-r2.md §3).

## R6 (1-й цикл): MODE=remote — СТАТУС: воспроизведён, корень уточнён

- `MODE=remote` падает: `SSH ci-deploy@103.88.243.151 rc=4: unknown verb in SSH command: 'cat /etc/nginx/conf.d/overlay/*.conf'`.
- Механизм: forced-command (orchestrator_cli dispatch) отвечает JSON-ошибкой на сырой `cat` — dispatch НЕ имеет read-only verb'а для сбора nginx-conf. R4 соблюдён: недоступность = FAIL, не skip (exit 1).
- **Рекомендация (закрытие R6):** добавить в orchestrator_cli dispatch read-only verb (например `collect-nginx-conf`) и использовать его в `_collect_remote` verify_sweep. Пока работает MODE=local.

$END_E2E_SUMMARY
