# 141-server-recovery — 05-TelegramSummary.md

$START_TELEGRAM_SUMMARY

## Карта точек отправки Telegram

| Точка | Механизм | Что уже было | Что настроено/изменено в сессии | Файлы |
|-------|----------|--------------|--------------------------------|-------|
| **Grafana alertmanager** (алерты) | contact-points.yml + env | контакт-пойнты были, но grafana crash-loop (#69950) | chatid block-scalar workaround; TELEGRAM_PROXY_URL host-gateway; NO_PROXY; короткий шаблон + escape; сообщение: `🚨 {{ range .Alerts }}...` | contact-points.yml, monitoring docker-compose.base.yml |
| **notify-hook (CLI)** | notify-hook.sh → telegram_notifier.notify() | был, но сломан (B1/B2: кавычки secrets.env + token kwarg) | фиксы B1/B2 + R5-тесты; канонический secrets_env_parser | telegram_notifier.py |
| **Lifecycle bootstrap** (helpers/reporting.py send_telegram) | send_telegram() | был | не менялся (работал через ту же функцию) | helpers/reporting.py |
| **Милстоуны сессии** (tg.sh) | notify-hook.sh + dedup 30 мин | — (новое) | evidence/tg.sh: dedup, лог evidence/telegram-sent.log | evidence/tg.sh |
| **Ручные тесты** | curl sendMessage через tor | — | 5 сообщений (470-475) доставлены оператору — первые примеры в телефоне | — |

## Что и куда добавил

1. **enc.yaml**: +TELEGRAM_CHAT_ID_CRITICAL/WARNING (= TELEGRAM_CHAT_ID, решение оператора «один чат»). Значение sops set парсилось как число (float) — перевыставлено строкой.
2. **contact-points.yml**: chatid через block-scalar (Grafana #69950 — числовой env всегда number); message — короткий шаблон с range + HTML-escape (400 Bad Request на аннотациях с HTML).
3. **monitoring compose**: TELEGRAM_PROXY_URL/http(s)_proxy = host.docker.internal:8118 + extra_hosts host-gateway + NO_PROXY (внутренние сервисы + .tronyx.ru).
4. **privoxy на ноде**: listen 172.17.0.1:8118 (docker-gateway) + 127.0.0.1.
5. **tg.sh**: локальные милстоуны сессии с дедупликацией 30 мин.

## Примеры реальных сообщений (когда ушли)

| Когда (MSK) | Сообщение | Канал |
|-------------|-----------|-------|
| 02:26 | Локально зелёно, push ушёл, CI пошёл | tg.sh |
| 03:06 | 🔄 Бутстрап попытка 1 FAILED (B4) — попытка 2 | tg.sh |
| 08:37 | ✅ Стек ПОДНЯТ (21 контейнер) | tg.sh |
| 08:40 | ✅ Деплой 4 проектов | tg.sh |
| ~09:30 | 5 ручных sendMessage (470-475): «Тест канала 141», «Тест 141 delivery», HTML-тесты | curl через tor |

## Ревью alert-правил (что сработало за ночь)

| Правило | Severity | Сработало | Шумное | Рекомендация |
|---------|----------|-----------|--------|--------------|
| Service Down (Short) | warning | ✅ (после фикса B17: cadvisor+node-exporter down) | нет (реальные падения) | keep |
| Service Down | critical | ✅ (то же, for 1m) | нет | keep |
| Backup Freshness | critical | pending (for 30m, бутстрап-эпоха) | нет | keep |
| DatasourceError (внутренние) | critical | ✅ шторм (B15: прокси ломал datasource) | ДА (ложные!) | keep — корень устранён (NO_PROXY) |
| LLM API Error Rate / Memory / Disk / WAL / Backup Upload | warning/critical | нет | — | keep |

## Рекомендации оператору

1. **Утром сверить телефон**: 5 тестовых сообщений (470-475) + милстоуны — канал живой.
2. **R1 (MED)**: alertmanager-доставка реальных алертов даёт «webhook response status 400» — ручные sendMessage работают; вероятно fallback-шаблон notifier при ошибке кастомного шаблона. Кандидат: выключить HTML в notifier (parse_mode) или использовать шаблон без range.
3. **Чат warning == critical** — по решению оператора; при желании разделить — задать TELEGRAM_CHAT_ID_WARNING отдельно в enc.yaml (sops set, строка!).
4. **Шум DatasourceError** — корень устранён (NO_PROXY), но правило можно retune (исключить внутренние 5xx).

$END_TELEGRAM_SUMMARY
