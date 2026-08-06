# Verify Sweep Notes — сессия 141

> Файл: evidence/verify-sweep.json (09:10 MSK, оператор: `make e2e-verify NODE=tronyx-vps MODE=local JSON=1`)

## Статус

- [x] verify-sweep.json получен — **✅ e2e-verify PASS: 4 endpoint(s) all green**
- [x] Разбор выполнен

## Таблица результатов

| fqdn | HTTP code | verdict | TLS chain | SAN | days_left | TLS verdict |
|------|-----------|---------|-----------|-----|-----------|-------------|
| tronyx.ru | 200 | pass | 4 | ✅ (SAN *.tronyx.ru + tronyx.ru) | 87 | ok |
| sexydancerostov.ru | 200 | pass | 4 | ✅ | 75 | ok |
| botanika.tronyx.ru | 200 | pass | 1 | ✅ | 89 | ok |
| roadmap.tronyx.ru | 200 | pass | 1 | ✅ | 89 | ok |

- `collect_errors: []` — пусто
- `legacy` — пропущен: "Project legacy has no domain — skip" (ожидаемо, домен не задан в node.yaml)
- HTTP 200 "by-design OK" (classify_http_code), TLS: LE-сертификаты (le=True), cross_check=True

## Хронология

1. 09:07 MSK — первая попытка MODE=remote → FAIL: SSH ci-deploy@103.88.243.151 rc=4 (authorized_keys не настроен после переустановки)
2. 09:10 MSK — MODE=local (после фикса) → **PASS**, файл с логом+JSON оставлен в evidence

## Выводы

1. **Все 4 сайта зелёные** — HTTP 200 + TLS валиден (SAN совпадает, LE, 75-89 дней до expiry, cross_check ok).
2. Сертификаты: tronyx.ru/sexydancerostov.ru (chain 4, restored из S3), botanika/roadmap (chain 1, issued acme) — все валидны.
3. Платформенные поддомены (grafana.tronyx.ru и др.) в sweep не входят (это vhost'ы conf.d, local mode покрывает node.yaml projects) — но их работоспособность подтверждена HTTPS-sweep дорожки (см. grafana-api.md).
4. SSH ci-deploy не настроен — блокирует remote-режим e2e-verify и будущие деплои через CI (NODE_HOST_MAP fallback). Рекомендация: bootstrap φ2 helpers/users.py add_ssh_key (оператор).
