<!-- GREP_SUMMARY: Brief, status-page, deploy-verification, basic-auth, ci-gate, rollback, healthcheck, live-service, collapsed-option-A, rate-limit, docker-sock, stealth-444, master-credentials -->
<!-- STRUCTURE: ┌ARTIFACT_CONTRACT┐ → ◇ Background → ◇ Problem → ◇ Superposition (COLLAPSED → A) → ◇ Collapse Result v2 (пересмотр 2026-07-20: auth + creds + stealth) → ◇ Auth & stealth design → ◇ Master credential model → ◇ CI-gate contract → ◇ Extra ideas → ◇ Acceptance Criteria → ◇ Non-scope → ⎋ Design Notes for Architect -->

# $ARTIFACT_CONTRACT
- **PURPOSE:** Бриф фичи «Node Status Page» — единый URL на главном домене ноды с HTTP Basic Auth (мастер-креды из secrets ноды), который отдаёт агрегированный статус всех сайтов и сервисов ноды: человеку — HTML со ссылками для ручной проверки после деплоя, CI — машиночитаемый эндпоинт как финальный gate работоспособности (fail → rollback). Без реализации — только план.
- **DESCRIPTION:** Фиксирует существующие примитивы платформы, разрыв между ними, суперпозицию из 5 вариантов реализации (КОЛЛАПС: Option A — live-сервис, shallow+deep в v1), пересмотр security-модели v2 (токен в path → HTTP Basic Auth + stealth 444 + мастер-креды), контракт CI-gate, критерии приёмки.
- **RATIONALE:** После деплоя оператору нужна одна страница: кликнуть → увидеть статус всех vhosts и сервисов → перейти по ссылкам для ручной проверки. CI нужен тот же источник истины как финальный smoke. Сейчас данные размазаны по `make verify`, `make healthcheck`, cron-healthcheck и Grafana. Пересмотр security-модели мотивирован: (1) унификация кредов всех сервисов платформы под мастер-пару email+password, (2) более сильный stealth (444 вместо 404/401) — домен недетектируем снаружи без знания кредов, (3) чище для CI: `curl -u email:pass` вместо токена в path.
- **ACCEPTANCE_CRITERIA:** (1) один URL на главном домене ноды, доступ только через HTTP Basic Auth с мастер-кредов из SOPS/age; (2) `/login/` — единственный путь, возвращающий 401 + WWW-Authenticate (браузерный диалог); любой другой путь без авторизации → 444 (connection close, ноль байт, access_log off) — домен неотличим от несуществующего; (3) после авторизации HTML: таблица всех vhosts + сервисов, live-статус, кликабельные ссылки; (4) JSON-вид + бинарный вердикт `/health` (200 PASS / 503 FAIL), один `curl -f -u email:pass`; (5) rate-limit + timeout-бюджет ≤30s, per-check ≤5s; (6) status-page не зависит от проверяемых сервисов; (7) креды не в git (SOPS/age → SCP); (8) мастер-креды — default для Prometheus, Loki, Grafana, Langfuse, Hermes (переопределяются per-сервис); (9) все операции через Makefile-фасад.
- **IMPLEMENTS:** skill `superposition` (FULL mode, collapsed 2026-07-18 → Option A; auth-пересмотр 2026-07-20), протокол `dev-pipeline` (Brief → Architect → Coder → QA)
- **IMPACTS:** `core/modules/status-page/` (НОВЫЙ модуль), `core/modules/nginx/` (location /login/ + stealth 444 + htpasswd), `core/secrets-manifest.yaml` (PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD), `core/lib/secrets.sh` (генерация htpasswd из мастер-кредов), `core/modules/nginx/install.sh` (унификация htpasswd для Prometheus/Loki), `core/internal/verify/verify-domains.sh` (обновление CI-проверки), `node.yaml` schema, `core/internal/bootstrap/discover_modules.py`
- **REQUIRES:** `AGENTS.md`, `core/AGENTS.md`, `core/secrets-manifest.yaml`, `core/internal/verify/verify-domains.sh`, `core/modules/nginx/install.sh`, node.yaml

$START_BRIEF

# Brief: Node Status Page — deploy verification URL (v2: Basic Auth + stealth 444 + master credentials)

## Background: что уже существует

| Примитив | Что делает | Ограничение |
|---|---|---|
| `make verify NODE=<n>` | curl всех `expose:true` доменов из node.yaml с машины оператора/CI | Внешний pull; нет HTML; нет ссылок; не видит внутренние сервисы |
| `make healthcheck [NODE=]` | module healthcheck.sh всех модулей, локально или по SSH | Требует SSH; не HTTP-эндпоинт; не кликабельно |
| `core/internal/healthcheck/docker-healthcheck.sh` | Запускается crontab'ом каждую минуту на VPS | Результат не публикуется наружу |
| Модуль `monitoring` (Prometheus + Grafana) | TSDB + дашборды | Не deploy-gate; не «одна страница после деплоя» |
| `make render-vhosts NODE=<n>` | Генерация nginx vhosts из node.yaml | Паттерн конфиг-генерации, переиспользуем |

**Разрыв:** нет одного URL на ноде, который одновременно (a) человеко-кликабелен со ссылками, (b) машиночитаем как CI-gate.

## Problem statement

После деплоя нужно:
1. **Человеку:** открыть один URL → увидеть статус всех vhosts и сервисов ноды → перейти по ссылкам для ручной проверки.
2. **CI:** дёрнуть тот же источник истины как финальный smoke-тест; fail → rollback.
3. **Безопасность:** страница раскрывает топологию ноды → доступ только по мастер-креду; креды не в git (SOPS/age + SCP).

## SUPERPOSITION: варианты реализации (COLLAPSED → Option A, 2026-07-18)

### Option A: Микро-модуль `status-page` (live-агрегатор) ✅ CHOSEN
- **Approach:** Новый контейнер `core/modules/status-page/`: по HTTP-запросу выполняет проверки (curl vhosts изнутри, статус health контейнеров), рендерит HTML + JSON.
- **Trade-offs:** Realtime; но +1 рантайм-сервис, обязательный rate-limit, доступ к docker.
- **Chosen because:** оператору важен realtime-статус в момент клика после деплоя.

### Option B: Static artifact — cron публикует status.json + HTML — NOT CHOSEN
- Лаг cron неприемлем для post-deploy. Fallback-вариант при деградации A.

### Option C: Gatus — NOT CHOSEN (эволюционный путь)
### Option D: Pull-only — NOT CHOSEN (нет мобильного сценария)
### Option E: Client-side JS — REJECTED (CORS, CI без JS)

### Collapse Result v1 (2026-07-18)

| Вопрос | Решение |
|---|---|
| Q1 Архитектура | **Option A** — live-сервис |
| Q2 Защита URL | ~~Токен в path~~ → **ПЕРЕСМОТРЕНО v2** |
| Q3 Глубина проверок | **Shallow + deep сразу в v1** |
| Q4 История прогонов | Не в v1 |

## Collapse Result v2: пересмотр security-модели (2026-07-20)

**Причина пересмотра:** унификация кредов платформы, усиление stealth, чище для CI.

| Что | Было (v1) | Стало (v2) |
|---|---|---|
| **Аутентификация** | Токен ≥32 hex в path `/_status/<token>/` | HTTP Basic Auth на `/login/` |
| **Креды** | Один секретный токен | Мастер-email + мастер-пароль из secrets ноды |
| **Stealth** | 404 на неверный токен | `return 444` — connection close без ответа, `access_log off` |
| **CI доступ** | `curl .../_status/<token>/health` | `curl -u email:pass .../health` |
| **Креды сервисов** | Разрозненные (каждый сервис сам) | Мастер-креды — default для всех (переопределяются) |

⚠️ TRAP[DECISION] · 2026-07-20 · HI · Basic Auth выбран поверх token-in-path
· Основание: унификация кредов, stealth 444 сильнее 404, `curl -u` чище для CI.
· Цена: htpasswd-файл на ноде, но секреты и так уже в `/run/platform/secrets.env`.
· Rev: если Basic Auth окажется проблемным для мобильных браузеров — вернуться к token-in-path без смены внешнего контракта (оба механизма могут сосуществовать).

## Auth & stealth design

### Точка входа

Единственный URL: `https://<main-domain>/login/` (location на главном домене ноды).

### nginx-логика

```nginx
# /login/ — единственный путь, показывающий 401 с WWW-Authenticate
location = /login/ {
    auth_basic "Platform";
    auth_basic_user_file /etc/nginx/conf.d/.htpasswd-platform;
    satisfy any;
    return 302 /;   # После успешного входа → редирект на status-page
}

# Всё остальное — stealth 444
location / {
    access_log off;
    if ($http_authorization = "") {
        return 444;                        # Нет Authorization → connection close
    }
    auth_basic "Platform";
    auth_basic_user_file /etc/nginx/conf.d/.htpasswd-platform;
    error_page 401 = @stealth_drop;        # Неверные креды → 444
    proxy_pass http://status-page:8080/;
}

location @stealth_drop {
    access_log off;
    return 444;
}
```

### Почему 444

`return 444` — специальный код nginx: закрывает TCP-соединение без отправки ни одного байта. Ни статуса, ни заголовков, ни тела. Снаружи неотличимо от:
- Несуществующего домена (connection refused/reset)
- Упавшего сервера
- Файрвола с DROP-правилом

В комбинации с `access_log off` — ноль шума от краулеров и сканеров.

### Stealth-матрица поведения

| Сценарий | Ответ |
|---|---|
| `GET /` без Authorization | 444 (connection close, ноль байт, нет лога) |
| `GET /any/path` без Authorization | 444 |
| `GET /login/` без Authorization | 401 + `WWW-Authenticate` → браузерный диалог |
| `GET /login/` с неверными кредами | 401 → диалог повторно |
| `GET /login/` с валидными кредами | 302 → `/` |
| `GET /` с валидными кредами | Status-page HTML |
| `GET /health` с валидными кредами | 200 `PASS` / 503 `FAIL` |
| `GET /` с неверными кредами | 444 (через `@stealth_drop`) |

### User flow

```
Оператор (первый вход):
  1. Открывает https://<main-domain>/login/
  2. Браузер показывает нативный диалог Basic Auth
  3. Вводит мастер-email + мастер-пароль
  4. 302 редирект на /
  5. Видит статус-страницу со всеми vhosts и сервисами

Оператор (повторный вход, та же сессия браузера):
  1. Открывает https://<main-domain>/
  2. Браузер автоматически шлёт сохранённый Authorization-заголовок
  3. Сразу видит статус-страницу
```

## Master credential model

### Источник

```yaml
# core/secrets-manifest.yaml (новые записи)
PLATFORM_MASTER_EMAIL:
  tier: required
  source: sops
  consumers: [nginx, status-page, prometheus, loki, grafana, langfuse, hermes]
  description: "Master email/login for all platform services"

PLATFORM_MASTER_PASSWORD:
  tier: required
  source: sops
  consumers: [nginx, status-page, prometheus, loki, grafana, langfuse, hermes]
  description: "Master password for all platform services; generated via openssl rand 32"
```

### Распространение на сервисы

Мастер-креды — **default**, каждый сервис может переопределить через свою env-переменную:

| Сервис | Env для логина (default) | Env для пароля (default) |
|---|---|---|
| **nginx /login/** | `PLATFORM_MASTER_EMAIL` → htpasswd | `PLATFORM_MASTER_PASSWORD` → htpasswd |
| **Prometheus** | `MONITORING_AUTH_USER` ← `PLATFORM_MASTER_EMAIL` | `MONITORING_AUTH_PASSWORD` ← `PLATFORM_MASTER_PASSWORD` |
| **Loki** | `MONITORING_AUTH_USER` | `MONITORING_AUTH_PASSWORD` |
| **Grafana** | `GF_SECURITY_ADMIN_USER` ← `PLATFORM_MASTER_EMAIL` | `GF_SECURITY_ADMIN_PASSWORD` ← `PLATFORM_MASTER_PASSWORD` |
| **Langfuse** | `LANGFUSE_INIT_USER_EMAIL` ← `PLATFORM_MASTER_EMAIL` | `LANGFUSE_INIT_USER_PASSWORD` ← `PLATFORM_MASTER_PASSWORD` |
| **Hermes Dashboard** | `HERMES_DASHBOARD_USERNAME` ← `PLATFORM_MASTER_EMAIL` | `HERMES_DASHBOARD_PASSWORD` ← `PLATFORM_MASTER_PASSWORD` |

**Правило разрешения (в secrets.sh / install.sh):**
```
SERVICE_PASSWORD = ${SERVICE_SPECIFIC_PASSWORD:-$PLATFORM_MASTER_PASSWORD}
SERVICE_EMAIL    = ${SERVICE_SPECIFIC_EMAIL:-$PLATFORM_MASTER_EMAIL}
```

### htpasswd-генерация

`core/modules/nginx/install.sh` расширяется: вместо отдельных `MONITORING_AUTH_*` → единая функция `_install_htpasswd_platform()`, которая:
1. Читает `PLATFORM_MASTER_EMAIL` / `PLATFORM_MASTER_PASSWORD` из `/run/platform/secrets.env`
2. Генерирует `/etc/nginx/conf.d/.htpasswd-platform` через `openssl passwd -apr1`
3. Идемпотентна (проверяет существующий файл)
4. Используется всеми vhost-конфигами (status-page, Prometheus, Loki)

## CI-gate контракт (обновлённый)

```bash
# v2: Basic Auth вместо токена в path
post-deploy шаг CI:
  1. curl -fsS --max-time 60 --retry 3 --retry-delay 10 \
       -u "${PLATFORM_MASTER_EMAIL}:${PLATFORM_MASTER_PASSWORD}" \
       https://<main-domain>/health
     → HTTP 200 body=PASS  → деплой подтверждён
     → HTTP 503 / non-200 / timeout → rollback
  2. (опц.) curl .../status.json → приложить к job summary
```

Креды в CI: из GitHub Secrets (`PLATFORM_MASTER_EMAIL`, `PLATFORM_MASTER_PASSWORD`), не из git.

## Дополнительные идеи (brainstorm, приоритизация в DevPlan)

1. **Version drift detect** — сервисы отдают git SHA + deploy timestamp; сравнение с ожидаемым.
2. **Prometheus scrape** того же status.json → алерты в Grafana без нового кода.
3. **История последних N прогонов** (ring buffer) — не в v1.
4. **Ссылки на внутренние админки** (Grafana, Langfuse, MinIO console) в HTML-таблице.
5. ⚠️ TRAP[DESIGN] **Anti-recursion:** status-page не проверяет сам себя.
6. **Cache коротких TTL (5–15 сек)** — повторный клик не запускает новый каскад проверок.
7. **Расширения статус-страницы** (TBD в DevPlan) — дополнительные элементы помимо базовой таблицы vhosts/сервисов.

## Acceptance Criteria

1. `https://<main-domain>/login/` — единственный путь с 401 + WWW-Authenticate; нативный браузерный диалог.
2. После авторизации `GET /` → HTML: таблица всех vhosts из node.yaml + всех модулей ноды, live-статус, кликабельные ссылки.
3. `/status.json` — машиночитаемый агрегат: `{status, generated_at, duration_ms, checks[]}`.
4. `/health` — бинарный вердикт: 200 `PASS` / 503 `FAIL`.
5. Любой путь без валидного Authorization → 444 (connection close, `access_log off`). Домен недектируем снаружи.
6. Неверный Authorization на не-`/login/` путях → 444 (через `@stealth_drop`), не 401.
7. `Referrer-Policy: no-referrer` на всех ответах status-page.
8. `X-Robots-Tag: noindex, nofollow`.
9. nginx rate-limit на location: превышение → 429.
10. Полный каскад проверок ≤30s; per-check timeout ≤5s; зависший сервис = FAIL этой проверки, не подвисание всей страницы.
11. CI post-deploy: `curl -f -u email:pass .../health`, fail → rollback; негативный тест обязателен (R5).
12. Падение status-page → CI curl fail (502/connection refused) → rollback. Chicken-egg закрыт.
13. `PLATFORM_MASTER_EMAIL` / `PLATFORM_MASTER_PASSWORD` в secrets ноды (SOPS/age), не в git. CI получает из GitHub Secrets.
14. htpasswd генерируется идемпотентно из мастер-кредов; используется всеми vhost-конфигами.
15. Сервисы по умолчанию используют мастер-креды, переопределяются per-сервис через env.
16. Модуль соответствует `core/modules/AGENTS.md`; обнаруживается `make discover-modules`.
17. Все операции через Makefile-фасад.

## Non-scope

- Alerting/уведомления (существующий monitoring)
- Публичная status page
- Автоматический rollback (только триггер)
- Мультинодная агрегация
- История прогонов / flapping-аналитика (v2+)

## Design Notes for Architect

1. **Доступ к статусам docker** — суб-суперпозиция:
   - (a) docker-socket-proxy (только `GET /containers/*`)
   - (b) cron `docker-healthcheck.sh` (гибрид: live shallow + published deep, freshness-контракт)
   - (c) docker.sock read-only — наихудший, требует обоснования.
2. **Timeout-бюджет:** total ≤30s; параллельный fan-out; per-check ≤5s.
3. **Источник списка проверок:** node.yaml — единственный SSoT.
4. **Технология сервиса:** минимальный рантайм (python3-stdlib или статический бинарь); решение за Architect.
5. **Make-глагол:** новый таргет не требуется; если потребуется — регистрация в `entrypoint-manifest.yaml`.
6. **Деградация на Option B (static):** внешний контракт (URL, /health, /status.json) не меняется.
7. **Унификация htpasswd:** переработка `_install_htpasswd_monitoring()` → `_install_htpasswd_platform()` с единым источником кредов.
8. **Миграция существующих сервисов:** Prometheus/Loki переезжают с `MONITORING_AUTH_*` на мастер-креды с обратной совместимостью (если `MONITORING_AUTH_PASSWORD` задан явно — использовать его, иначе fallback на мастер).
9. **Путь `/login/`:** черновое имя; может быть переименован позже без смены контракта (nginx location alias).

$END_BRIEF
