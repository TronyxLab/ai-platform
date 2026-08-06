# 141-server-recovery — 03-browser-checklist.md

$START_BROWSER_CHECKLIST

## Как пользоваться
Открыть в браузере, пройти каждый пункт. Пароли — из `/var/folders/14/vtgwv6lj4g70fldm667f33lc0000gn/T/kilo/141-secrets/secrets.env` (формат KEY='value'; НЕ коммитить, НЕ в чат). Проверено агентно (статус) — браузерная проверка за оператором.

## Основные сайты

| URL | Логин | Что ожидается | Агентный статус |
|-----|-------|---------------|-----------------|
| https://tronyx.ru | — | Сайт tronyx-site (nginx) | ✅ HTTP 200, TLS ok |
| https://sexydancerostov.ru | — | dance-site | ✅ HTTP 200, TLS ok |
| https://botanika.tronyx.ru | — | botanika | ✅ HTTP 200, TLS ok (новый серт 06.08) |
| https://roadmap.tronyx.ru | — | roadmap | ✅ HTTP 200, TLS ok (новый серт 06.08) |

## Платформенные сервисы

| URL | Логин | Что ожидается | Агентный статус |
|-----|-------|---------------|-----------------|
| https://grafana.tronyx.ru | GF_SECURITY_ADMIN_USER / GF_SECURITY_ADMIN_PASSWORD (из secrets.env; если login-form 401 — basic-auth работает, см. R7) | Дашборды, datasources Prometheus+Loki, 8 alert-правил, контакт-пойнты Telegram | ✅ API-проверено |
| https://langfuse.tronyx.ru | LANGFUSE_INIT_USER_EMAIL / LANGFUSE_INIT_USER_PASSWORD | Вход, проекты | ✅ контейнер healthy (UI не проверен агентом) |
| https://litellm.tronyx.ru | admin / LITELLM_MASTER_KEY | Admin UI, модели | ✅ контейнер healthy |
| https://minio.tronyx.ru | MINIO_ROOT_USER / MINIO_ROOT_PASSWORD | Buckets (backups) | ✅ контейнер healthy |
| https://status.tronyx.ru (или /status) | — | Status page | ✅ контейнер healthy (URL уточнить по nginx overlay) |
| https://hermes.tronyx.ru (dashboard) | HERMES_DASHBOARD_USERNAME / PASSWORD | Hermes dashboard | ✅ контейнер healthy |

## Проверки утром (после браузера)

1. Телефон: 5 тестовых telegram (470-475) + милстоуны — сверить с 05-TelegramSummary.md.
2. Telegram-алерт: в Grafana → Alerting → Rules — ServiceDown в firing при остановленном сервисе; контакт-пойнты — Test.
3. Сроки сертификатов: все 4 домена — LE, ~90 дней (бот. — 89, roadmap — 89).
4. CI: platform-gate-fast последних пушей — зелёные; core-deploy при следующем push — должен пройти (ci-deploy ключ на месте).
5. R1 (MED): alertmanager 400 — см. 02-VerificationReport §6.

$END_BROWSER_CHECKLIST
