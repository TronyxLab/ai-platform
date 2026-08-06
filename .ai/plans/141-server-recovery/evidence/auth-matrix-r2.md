# 141-server-recovery — auth-matrix-r2.md (2-й цикл)

$START_AUTH_MATRIX_R2

> Проверка: 2026-08-06 10:45Z. Метод: cookie-jar curl / urllib (basic-auth для grafana; анонимные GET для сайтов). Секреты НЕ выводятся.

## Результаты

| Хост | Проверка | HTTP | Вердикт | Примечание |
|------|----------|------|---------|------------|
| https://tronyx.ru | GET / | 200 | ✅ | tronyx-site |
| https://sexydancerostov.ru | GET / | 200 | ✅ | dance-site |
| https://botanika.tronyx.ru | GET / | 200 | ✅ | botanika |
| https://roadmap.tronyx.ru | GET / | 200 | ✅ | roadmap |
| https://grafana.tronyx.ru | /api/health | 200 | ✅ | |
| https://grafana.tronyx.ru | /api/user (basic) | 200 | ✅ | R7: basic-auth работает |
| https://grafana.tronyx.ru | /login (form) | 200 | ✅ | login-form отдаётся (в 1-м цикле было 401) — R7 закрыт |
| https://langfuse.tronyx.ru | GET / | 200 | ✅ | login-page |
| https://litellm.tronyx.ru | GET /health | 000 | ❌ FAIL | нет vhost'а/upstream (см. §2) |
| https://minio.tronyx.ru | /minio/health/live | 000 | ❌ FAIL | нет vhost'а/upstream (см. §2) |
| https://platform.tronyx.ru | GET / | 401 | ✅ | status-page за Basic Auth (жив) |
| https://hermes.tronyx.ru | / | 302 → /auth/login?provider=basic | ✅ | upstream-квирк: /auth/login?provider=basic → 500 (как в 1-м цикле, v2026.7.7.2); логин-форма /auth/password-login → **200** |

## §2. litellm/minio — HTTP 000 (не регрессия контейнеров)

- Контейнеры litellm и minio в списке 25/25 healthy (server-ops), логи в Loki живые (litellm /health/liveliness 200, minio startup).
- Но: **в конфиге nginx-модуля НЕТ vhost'ов litellm/minio** (core/modules/nginx/config/: только grafana, prometheus, loki, langfuse, hermes, platform) — внешний HTTP-доступ к litellm/minio не предусмотрен конфигом; litellm публикует только 127.0.0.1:4000 (loopback ноды).
- Вердикт: не баг восстановления, а отсутствующая фича (нет публичного vhost'а). Для LLM-цепочки используется 127.0.0.1:4000 на ноде (запрос server-ops, REQ_EVIDENCE 10:55Z).
- Рекомендация: если нужен веб-UI litellm/minio снаружи — добавить vhost'ы (отдельный тикет, вне сессии 141).

## §3. Итог

- Авторизованный доступ: ✅ grafana (basic), ✅ langfuse, ✅ platform (401-gated), ✅ hermes (password-login 200), ✅ 4 сайта анонимно.
- ❌ litellm/minio — только внутренние (по дизайну конфига).
- R7 (1-й цикл: login form 401): **закрыт** — форма отвечает 302/200, basic-auth 200.

$END_AUTH_MATRIX_R2
