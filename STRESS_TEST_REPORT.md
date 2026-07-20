# Stress Test Report — tronyx-vps

## Дата: 2026-07-20
## Окружение: tronyx-vps (103.88.243.151), context: tronyx-lab

---

### 1. Pre-deploy Gates

| Проверка | Статус | Детали |
|----------|--------|--------|
| `make gate MODE=full` | PARTIAL | 1 failure: smoke test (status-page-test port conflict 18080 с nginx-test — локальное окружение macOS) |
| `make test MARKER=all` | PARTIAL | 1 failure (тот же smoke) в 977 тестах |
| `make test MARKER=e2e` | SKIPPED | E2E не запускались по причине незавершённого gate |

**Исправленные дефекты:**
1. Cross-layer violation в `deploy.sh:99` — вызов `verify.sh` из entrypoints (entrypoint→entrypoint) заменён на прямой вызов `internal/verify/verify-domains.sh`
2. .env drift — добавлены недостающие ключи `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `GLM_API_KEY`
3. Formatting — `ruff format` + trailing whitespace в StatusReport.md
4. SMOKE_ENV — добавлены `NODE_NAME` и `NODE_CONFIGS_DIR` для корректной работы status-page в тестовом окружении macOS

---

### 2. Bootstrap

| Шаг | Статус | Детали |
|-----|--------|--------|
| Dry-run bootstrap | PASS | Все 17 шагов разрешимы |
| SSH access | PASS | `ssh root@103.88.243.151 echo ok` |
| Full `make bootstrap-node` | PARTIAL | Exit 0, но 4 warnings |
| `make healthcheck` | PARTIAL | 17/21 containers healthy |
| `make verify` (HTTPS domains) | PARTIAL | www.tronyx.ru → 301 redirect; sexydancerostov.ru, botanika.tronyx.ru → 502 (проекты не задеплоены) |

**Warnings bootstrap:**
1. Tor circuit failed — Telegram недоступен (не критично, тестовый сервер)
2. node.yaml validation warning (не критично)
3. Docker module deployment — `ensure_context_repo` exit 1 из-за неудачного git clone контекстного репозитория (public repo, нет auth)
4. Converge warnings — legacy vhost markers (ожидаемо после сброса)

---

### 3. Доступ

| Сервис | URL | Статус | Комментарий |
|--------|-----|--------|-------------|
| Grafana | http://status.tronyx.ru:3000 (internal) | ✅ | HTTP 200 healthy |
| Prometheus | http://localhost:9090 | ✅ | 8 active targets |
| Loki | http://localhost:3100 | ✅ | Healthy |
| nginx (HTTPS) | https://www.tronyx.ru | ✅ 301 | Редирект на основной домен |
| nginx (HTTPS) | https://sexydancerostov.ru | ⚠️ 502 | Проект не задеплоен (требуется CI `make deploy`) |
| nginx (HTTPS) | https://botanika.tronyx.ru | ⚠️ 502 | Проект не задеплоен (требуется CI `make deploy`) |
| status.tronyx.ru | n/a | ❌ | DNS не настроен (требуется конфигурация) |

---

### 4. Docker-статистика

| Контейнер | Статус | CPU | Память |
|-----------|--------|-----|--------|
| postgres | healthy | 0.00% | 46.8MiB / 1GiB |
| pgbouncer | healthy | 0.02% | 1.6MiB / 64MiB |
| redis | healthy | — | — |
| clickhouse | healthy | — | — |
| minio | healthy | — | — |
| nginx | healthy | 0.00% | 7.4MiB / 7.8GiB |
| litellm | health: starting | — | — |
| grafana | healthy | 0.10% | 69.8MiB / 256MiB |
| prometheus | healthy | 0.22% | 122.3MiB / 512MiB |
| loki | healthy | — | — |
| hermes-agent | unhealthy | 99.73% | 209.7MiB / 1GiB |
| langfuse | restarting | — | — |
| status-page | healthy | 0.02% | 14.4MiB / 7.8GiB |
| backup-cron | healthy | 0.00% | 816KiB / 128MiB |

---

### 5. Проблемы и инциденты

| # | Проблема | Коренная причина | Статус |
|---|----------|-----------------|--------|
| 1 | PgBouncer `auth_user=postgres:` | `POSTGRES_PASSWORD=SkyNet!!%)` содержит спецсимволы. PgBouncer entrypoint URL-парсит DATABASE_URLS, но не URL-decode'ит пароль при генерации userlist.txt, что приводит к `auth_user=postgres:` (trailing colon) | open |
| 2 | Langfuse crash-loop | Не может подключиться через pgbouncer (проблема #1). ClickHouse migration URL также содержит неверный пароль | open |
| 3 | LiteLLM crash-loop | Не может подключиться к Prisma DB через pgbouncer (проблема #1) | open |
| 4 | Hermes-agent unhealthy | Не настроен dashboard auth provider (ожидаемо — требует конфигурации) | known |
| 5 | Проекты недоступны (502) | Контейнеры tronyx-site, dance-site, botanika удалены при сбросе. Требуется `make deploy` через CI | known |
| 6 | Git clone контекстного репозитория | Public repo `https://github.com/TronyxLab/ai-platform.git` не клонируется (возможно, заблокирован) | open |

---

### 6. Рекомендации

1. **Исправить POSTGRES_PASSWORD** — сменить пароль на не содержащий спецсимволов `!%)` или URL-encode в `secrets/` файле
2. **Добавить URL-encode в pgbouncer entrypoint** — `userlist.txt` должен содержать raw password, а не URL-encoded
3. **Деплой проектов** — после фикса pgbouncer запустить `make deploy PROJECT=<name>` для tronyx-site, dance-site, botanika
4. **Настроить Grafana DNS** — `status.tronyx.ru` должен указывать на сервер
5. **Исправить ensure_context_repo** — добавить `|| true` в `deploy-modules.sh` line 905, чтобы git clone failure не блокировал docker compose up
6. **Настроить dashboard auth для hermes-agent** — это плановая конфигурация, не баг

---

### 7. Вердикт

**PARTIAL** — Bootstrap выполнен, критическая инфраструктура (postgres, redis, nginx, monitoring) работает. 2 контейнера в crash-loop из-за проблемы с URL-encoding пароля. Проекты не задеплоены (требуется CI). Рекомендуется исправить password encoding перед следующим полным сбросом.

