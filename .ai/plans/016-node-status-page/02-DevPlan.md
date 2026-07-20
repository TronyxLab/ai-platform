<!--
$START_DEVPLAN
$ARTIFACT_CONTRACT
  PURPOSE:      Детальный план реализации фичи «Node Status Page» на базе Brief 016.
                Создание live-сервиса status-page (Docker-модуль) с HTTP Basic Auth + stealth 444,
                унификация мастер-кредов платформы, интеграция CI-gate /health, обновление
                nginx-конфигурации и верификации доменов.
  DESCRIPTION:  Разбивка на 12 атомарных задач в 5 параллельных волн. Ключевые архитектурные
                решения: (1) docker-healthcheck.sh-гибрид для доступа к статусам контейнеров
                (cron-файл + live-чтение), (2) python3-stdlib для сервера статусов,
                (3) единая _install_htpasswd_platform() с fallback-совместимостью,
                (4) мастер-креды как default для всех сервисов с per-service override.
  RATIONALE:    Brief 016 коллапсировал суперпозицию в Option A (live-сервис) и определил
                security-модель v2 (Basic Auth + stealth 444 + мастер-креды). DevPlan
                разрешает оставшуюся суб-суперпозицию (docker-доступ), формализует
                контракты, определяет атомарные задачи и wave-группировку без конфликтов файлов.
  ACCEPTANCE_CRITERIA:
    AC1:  https://<main-domain>/login/ — 401 + WWW-Authenticate; / → 444 без авторизации
    AC2:  HTML-таблица всех vhosts из node.yaml + всех модулей ноды, live-статус, ссылки
    AC3:  /status.json — машиночитаемый агрегат {status, generated_at, duration_ms, checks[]}
    AC4:  /health — 200 PASS / 503 FAIL; curl -f -u email:pass работает в CI
    AC5:  Полный каскад проверок ≤30s; per-check timeout ≤5s
    AC6:  PLATFORM_MASTER_EMAIL/PASSWORD в secrets (SOPS/age), не в git
    AC7:  htpasswd-platform используется всеми vhost-конфигами (nginx, Prometheus, Loki)
    AC8:  Модуль соответствует core/modules/AGENTS.md; discover-modules находит его
    AC9:  make gate MODE=fast — зелёный (все существующие гейты + новые)
    AC10: CI post-deploy: curl -f -u .../health fail → rollback; негативный тест (R5)
  IMPLEMENTS:   Brief 016-node-status-page (01-Brief.md)
  IMPACTS:      NEW: core/modules/status-page/ (модуль, ~10 файлов)
                NEW: tests/test_status_page.py, tests/gates/test_gate_status_page.py
                MODIFY: core/modules/nginx/config/ (platform-default.conf.template, nginx.conf, prometheus-vhost.conf, loki-vhost.conf)
                MODIFY: core/modules/nginx/docker-compose.base.yml (htpasswd mount + status-page proxy)
                MODIFY: core/secrets-manifest.yaml (+PLATFORM_MASTER_EMAIL, +PLATFORM_MASTER_PASSWORD)
                MODIFY: core/lib/secrets.sh (+_ensure_htpasswd_generated)
                MODIFY: core/internal/verify/verify-domains.sh (+status-page health check)
                MODIFY: core/internal/healthcheck/docker-healthcheck.sh (+status.json export)
                MODIFY: .env.example (+PLATFORM_MASTER_*)
                MODIFY: core/entrypoint-manifest.yaml (+verify update)
  REQUIRES:     AGENTS.md (root + core), core/modules/AGENTS.md, core/secrets-manifest.yaml,
                core/internal/verify/verify-domains.sh, core/modules/nginx/install.sh (deprecated, install.sh not used for docker nginx),
                docker-healthcheck.sh (crontab), python3.10+ (stdlib: http.server, json, subprocess, yaml)
$END_DEVPLAN
-->

# DevPlan: 016-node-status-page

## 0. Классификация задачи

| Параметр | Значение |
|----------|----------|
| **Размер** | **LARGE** — >20 файлов, новый модуль, архитектурные изменения (контракт секретов, nginx auth-модель) |
| **Волн** | 5 |
| **Задач** | 12 |
| **Критический путь** | TASK-2 → TASK-3 → TASK-8 → TASK-10 (секреты → htpasswd → nginx → интеграция) |

## 0.1 Debt Intake

Аудит существующих TRAP и Debt-артефактов в зоне изменений:

| Источник | Содержание | Решение |
|----------|-----------|---------|
| `core/modules/nginx/install.sh` L34 — TRAP[DEBT] legacy system-nginx | install.sh — мёртвый код для docker-модуля nginx | **DEFER** — не в скоупе. Деплой nginx идёт через docker compose, htpasswd-генерация будет в новом скрипте, не в install.sh. |
| `core/modules/nginx/Makefile` L19 — TRAP[DECISION] Minimal module Makefile | module.mk без overrides | **ПРИНЯТО** — статус-страница не требует кастомных таргетов. |

Новых TRAP[DEBT] при анализе не обнаружено.

## 1. Requirements Analysis — Key Success Criteria

| # | Критерий | Измерение |
|---|----------|-----------|
| SC1 | `/login/` отдаёт 401 + `WWW-Authenticate`; все остальные пути без авторизации → 444 | `curl -v https://<domain>/login/` (401), `curl -v https://<domain>/` (connection reset) |
| SC2 | После Basic Auth: HTML-таблица с vhosts (из node.yaml) + сервисами (из docker), live-статус, кликабельные ссылки | Открыть браузер, ввести креды, увидеть таблицу |
| SC3 | `/health` возвращает 200 `PASS` когда все сервисы healthy; 503 `FAIL` при проблемах | `curl -f -u email:pass https://<domain>/health` → exit 0 или 22 |
| SC4 | `/status.json` — полный агрегат с `status`, `generated_at`, `duration_ms`, `checks[]` | `curl -u email:pass https://<domain>/status.json \| jq .status` |
| SC5 | CI post-deploy gate: `curl -f -u .../health` fail → rollback; негативный тест (R5) | Негативный тест: симулировать FAIL, проверить exit code ≠ 0 |

## 2. Architecture Overview

### 2.1 Суб-суперпозиция: доступ к docker-статусам — РАЗРЕШЕНИЕ

**## APPROACH: (b) cron `docker-healthcheck.sh` гибрид — live shallow + published deep**

**Анализ вариантов:**

| Вариант | Security surface | Realtime | Сложность | Переиспользование |
|---------|-----------------|----------|-----------|-------------------|
| (a) docker-socket-proxy | medium (HTTP API, no filesystem/exec) | full | +1 контейнер | нет |
| **(b) cron гибрид** | **low** (файл в tmpfs, no socket) | **near-realtime** (≤60s freshness) | **минимальная** | **docker-healthcheck.sh уже существует** |
| (c) docker.sock ro | high (полный доступ к docker API) | full | минимальная | нет |

**## @rationale**
Q: Почему (b), а не (a) docker-socket-proxy?
A: (1) `docker-healthcheck.sh` УЖЕ работает каждую минуту через crontab на VPS — достаточно расширить его записью status.json в `/run/platform/`. (2) `/run/platform/` — tmpfs, стирается при ребуте — безопасно. (3) Не добавляем новый контейнер (экономия ресурсов, меньше moving parts). (4) Freshness-контракт: status-page читает файл (≤60s freshness) для docker-статусов + live-curl для HTTP-проверок vhosts.

**Контракт freshness:**
- `docker-healthcheck.sh` пишет `/run/platform/docker-health.json` каждую минуту
- `status-page` читает этот файл для docker-статусов (shallow: container running/healthy)
- Для deep-проверок (HTTP vhosts) — live-curl из контейнера status-page
- Заголовок `X-Data-Freshness: <timestamp>` в ответе

**Also considered:** (a) docker-socket-proxy (rejected: +1 контейнер, лишняя сложность при существующем cron-механизме), (c) docker.sock read-only (rejected: unacceptable security surface per brief).

### 2.2 Draft Code Graph

```
                        ┌─────────────────────────────────┐
                        │       node.yaml (SSoT)            │
                        │  projects[].domain, expose:true   │
                        │  modules[]                        │
                        └──────┬──────────────────────────┘
                               │
              ┌────────────────┼────────────────────┐
              ▼                ▼                     ▼
   ┌──────────────────┐ ┌──────────────┐ ┌──────────────────────┐
   │ status-page (NEW) │ │ docker-      │ │ nginx (MODIFIED)     │
   │ python3-stdlib    │ │ healthcheck  │ │                      │
   │ :8080 internal    │ │ .sh (MOD)    │ │ /login/ → auth_basic │
   │                   │ │              │ │ / → @stealth_drop    │
   │ GET / → HTML      │ │ writes       │ │ / → proxy_pass      │
   │ GET /health → JSON│ │ /run/plat-   │ │   http://status-     │
   │ GET /status.json  │ │ form/docker- │ │   page:8080/         │
   │                   │ │ health.json  │ │                      │
   └──────┬────────────┘ └──────┬───────┘ └──────────┬───────────┘
          │                     │                     │
          │ reads node.yaml     │ cron every 1m       │ auth_basic_user_file
          │ reads docker-       │                     │ /etc/nginx/conf.d/
          │   health.json       │                     │ .htpasswd-platform
          │ live-curl vhosts    │                     │
          ▼                     ▼                     ▼
   ┌──────────────────────────────────────────────────────────┐
   │                  /run/platform/                           │
   │  secrets.env (SOPS/age → PLATFORM_MASTER_EMAIL/PASSWORD)  │
   │  docker-health.json (cron output)                         │
   └──────────────────────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────────┐
              ▼                ▼                     ▼
   ┌──────────────────┐ ┌──────────────┐ ┌──────────────────────┐
   │ secrets-manifest  │ │ secrets.sh   │ │ CI-gate              │
   │ .yaml (MODIFIED)  │ │ (MODIFIED)   │ │ verify-domains.sh    │
   │                   │ │              │ │ (MODIFIED)           │
   │ +PLATFORM_MASTER_ │ │ +_ensure_    │ │                      │
   │  EMAIL            │ │  htpasswd_   │ │ +status-page health  │
   │ +PLATFORM_MASTER_ │ │  generated() │ │ check via curl -u    │
   │  PASSWORD         │ │              │ │                      │
   └──────────────────┘ └──────────────┘ └──────────────────────┘
```

### 2.3 Step-by-Step Data Flow — Статус-страница

```
1. Оператор/CI открывает https://<main-domain>/
2. nginx проверяет Authorization-заголовок:
   ├── Нет заголовка → return 444 (stealth)
   └── Есть заголовок → auth_basic против .htpasswd-platform
       ├── Неверные креды → error_page 401 = @stealth_drop → 444
       └── Валидные креды → proxy_pass http://status-page:8080/
3. status-page (python3 stdlib http.server):
   ├── Читает node.yaml → список vhosts (expose:true) + модули
   ├── Читает /run/platform/docker-health.json → статусы контейнеров
   ├── Для каждого vhost: live-curl (timeout 5s) → HTTP статус
   ├── Для каждого модуля: статус из docker-health.json
   ├── Рендерит HTML (text/html) или JSON (Accept: application/json)
   └── Возвращает ответ с заголовками:
       ├── X-Robots-Tag: noindex, nofollow
       ├── Referrer-Policy: no-referrer
       └── X-Data-Freshness: <timestamp>
4. /health → бинарный вердикт:
   ├── Все проверки PASS → 200 "PASS"
   └── Любая FAIL → 503 "FAIL" (с details в теле)
5. /status.json → полный машиночитаемый агрегат
```

### 2.4 Data Flow — Генерация htpasswd

```
1. Bootstrap: node-lifecycle.sh --mode init
2. step_10_decrypt_secrets() → sops decrypt → source secrets.env
   → PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD в env
3. [NEW] _ensure_htpasswd_generated() в secrets.sh:
   ├── Проверяет наличие PLATFORM_MASTER_EMAIL и PLATFORM_MASTER_PASSWORD
   ├── Генерирует /run/platform/.htpasswd-platform через openssl passwd -apr1
   ├── Идемпотентно (проверяет существующий файл и хеш)
   └── Копирует в /etc/nginx/conf.d/.htpasswd-platform (или mount)
4. nginx container: bind-mount .htpasswd-platform → auth_basic_user_file
5. deploy-modules.sh: перед docker compose up — проверяет наличие htpasswd
```

### 2.5 Data Flow — CI Post-Deploy Gate

```
CI post-deploy step:
1. sops decrypt → PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD
2. curl -fsS --max-time 60 --retry 3 --retry-delay 10 \
     -u "${PLATFORM_MASTER_EMAIL}:${PLATFORM_MASTER_PASSWORD}" \
     https://<main-domain>/health
   ├── HTTP 200 body=PASS → деплой подтверждён
   └── HTTP 503 / non-200 / timeout → rollback
3. (опц.) curl .../status.json → приложить к CI job summary
```

## 3. Design Decisions

### 3.1 Docker-доступ: cron-гибрид (b)

**## @rationale**
Q: Почему не docker-socket-proxy?
A: docker-healthcheck.sh уже существует и работает каждую минуту. Расширение его до записи JSON-файла в /run/platform/ (tmpfs) — минимальное изменение. Не добавляем новый контейнер, не открываем docker API наружу. Freshness ≤60s — приемлемо для post-deploy верификации (оператор жмёт F5 после деплоя, проверки происходят в пределах минуты).

### 3.2 Технология: python3-stdlib

**## @rationale**
Q: Почему python3-stdlib, а не Flask/FastAPI/Go?
A: Python3 уже есть на всех VPS (используется discover_modules.py, template_engine.py, verify-domains.sh). Стандартная библиотека (`http.server`, `json`, `subprocess`, `yaml`) покрывает все требования без дополнительных зависимостей. Никакого pip install — контейнер на основе python:3.12-alpine с PyYAML (единственная внешняя зависимость, уже используется платформой). Компактный образ (<50MB).

### 3.3 htpasswd: единая функция `_ensure_htpasswd_generated()`

**## @rationale**
Q: Почему не в nginx/install.sh?
A: nginx/install.sh — deprecated (nginx теперь docker-модуль). Логика htpasswd-генерации относится к secrets.sh (уже отвечает за `_ensure_secret`). Новая функция `_ensure_htpasswd_generated()` в `secrets.sh`:
- Читает мастер-креды из env
- Генерирует htpasswd-файл через `openssl passwd -apr1`
- Идемпотентна
- Экспортирует `HTPASSWD_FILE` path для nginx mount

### 3.4 Мастер-креды: default с per-service override

**## @rationale**
Q: Как разрешать конфликт мастер-кредов с существующими сервисными креденшелами?
A: Правило разрешения (Bash parameter expansion):
```
SERVICE_USER="${SERVICE_SPECIFIC_USER:-$PLATFORM_MASTER_EMAIL}"
SERVICE_PASS="${SERVICE_SPECIFIC_PASS:-$PLATFORM_MASTER_PASSWORD}"
```
Существующие `MONITORING_AUTH_*` становятся специфичными переменными. Если они заданы — используются. Если нет — fallback на мастер-креды. Это сохраняет обратную совместимость.

### 3.5 Anti-recursion

**## @rationale**
Q: Как status-page избегает проверки самой себя?
A: Контейнер `status-page` исключается из списка проверяемых сервисов по container_name. Фильтр: `if container_name == "status-page": skip`. Дополнительно: `/health` эндпоинт самого status-page не проверяется через nginx (проверка на уровне контейнера через docker-healthcheck.sh).

### 3.6 node.yaml — Single Source of Truth

**## @rationale**
Q: Откуда status-page берёт список проверок?
A: Из node.yaml — projects[].domain (expose:true) + modules[] (все задеплоенные модули). Других источников нет. `docker-health.json` — runtime-данные (статус), не конфигурация. SSoT соблюдён.

## 4. $TASKS

### TASK-1: secrets-manifest.yaml — добавить мастер-креды
- **Роль**: Coder
- **Файлы**: `core/secrets-manifest.yaml` (MODIFY), `.env.example` (MODIFY)
- **Описание**: Добавить `PLATFORM_MASTER_EMAIL` (tier: required, source: sops, consumers: [nginx, status-page, prometheus, loki, grafana, langfuse, hermes]) и `PLATFORM_MASTER_PASSWORD` (tier: required, source: sops, gen_command: `openssl rand -base64 32`). Обновить `.env.example` — добавить обе переменные в секцию platform secrets.
- **Приёмка**: `grep PLATFORM_MASTER_EMAIL core/secrets-manifest.yaml` находит запись с consumers. `.env.example` содержит `PLATFORM_MASTER_EMAIL=` и `PLATFORM_MASTER_PASSWORD=`. `make gate MODE=fast` — зелёный.
- **Сложность**: 2
- **Зависимости**: нет

### TASK-2: secrets.sh — `_ensure_htpasswd_generated()`
- **Роль**: Coder
- **Файлы**: `core/lib/secrets.sh` (MODIFY)
- **Описание**: Добавить функцию `_ensure_htpasswd_generated()` в secrets.sh: читает `PLATFORM_MASTER_EMAIL` и `PLATFORM_MASTER_PASSWORD` из env, генерирует `/run/platform/.htpasswd-platform` через `openssl passwd -apr1`, идемпотентна (проверяет существующий файл и соответствие хеша), экспортирует `HTPASSWD_FILE` path. Вызвать из `step_12b_ensure_secrets()` после авто-генерации секретов.
- **Приёмка**: `bash -c 'source core/lib/secrets.sh; PLATFORM_MASTER_EMAIL=admin@test.com PLATFORM_MASTER_PASSWORD=test123 _ensure_htpasswd_generated; cat /run/platform/.htpasswd-platform'` содержит `admin@test.com:$apr1$...`. Повторный вызов — no-op.
- **Сложность**: 3
- **Зависимости**: TASK-1 (нужны определения переменных в manifest)

### TASK-3: nginx config — /login/ + stealth 444 + htpasswd mount
- **Роль**: Coder
- **Файлы**: `core/modules/nginx/config/platform-default.conf.template` (MODIFY), `core/modules/nginx/config/nginx.conf` (MODIFY), `core/modules/nginx/docker-compose.base.yml` (MODIFY)
- **Описание**:
  - `platform-default.conf.template`: добавить `location = /login/` с `auth_basic` + `satisfy any; return 302 /;`, основной `location /` с проверкой `$http_authorization` (пустой → 444), `auth_basic` + `error_page 401 = @stealth_drop`, `proxy_pass http://status-page:8080/`. `location @stealth_drop` → `access_log off; return 444;`.
  - `nginx.conf`: убедиться что `server_names_hash_bucket_size` достаточен.
  - `docker-compose.base.yml`: добавить mount `/run/platform/.htpasswd-platform:/etc/nginx/conf.d/.htpasswd-platform:ro`, добавить `status-page` в `proxy-net` aliases (или убедиться что proxy_pass работает по container_name).
- **Приёмка**: nginx -t проходит. `curl -v http://localhost/login/` → 401 + WWW-Authenticate. `curl -v http://localhost/` → connection reset (444). `curl -v -u admin@test.com:test123 http://localhost/` → proxy_pass (200 или 502 если status-page не запущен).
- **Сложность**: 5
- **Зависимости**: TASK-2 (htpasswd-файл должен существовать)

### TASK-4: nginx config — унификация htpasswd для Prometheus/Loki vhosts
- **Роль**: Coder
- **Файлы**: `core/modules/nginx/config/prometheus-vhost.conf` (MODIFY), `core/modules/nginx/config/loki-vhost.conf` (MODIFY)
- **Описание**: Заменить `auth_basic_user_file` с `.htpasswd-monitoring` на `.htpasswd-platform` для Prometheus и Loki vhost configs. Добавить `satisfy any;` если ещё нет.
- **Приёмка**: `grep '.htpasswd-platform' core/modules/nginx/config/prometheus-vhost.conf` находит запись. `grep '.htpasswd-monitoring' core/modules/nginx/config/*.conf` не находит (полная миграция).
- **Сложность**: 2
- **Зависимости**: TASK-3 (nginx config уже модифицирован)

### TASK-5: Создание модуля status-page — module.yaml + docker-compose.base.yml + Makefile
- **Роль**: Coder
- **Файлы**: `core/modules/status-page/module.yaml` (NEW), `core/modules/status-page/docker-compose.base.yml` (NEW), `core/modules/status-page/Makefile` (NEW), `core/modules/status-page/.dockerignore` (NEW → symlink)
- **Описание**: Создать модуль по контракту `core/modules/AGENTS.md`:
  - `module.yaml`: name=status-page, install_type=docker, description, depends_on=[nginx], interfaces=[healthcheck], env_shared (PLATFORM_DOMAIN), env_requires=[PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD]
  - `docker-compose.base.yml`: image=python:3.12-alpine, container_name=status-page, profiles=[status-page], restart=unless-stopped, proxy-net, volumes (app + node.yaml ro + /run/platform/docker-health.json ro), healthcheck (curl localhost:8080/health), x-logging
  - `Makefile`: MODULE_NAME=status-page, include module.mk
  - `.dockerignore`: symlink → ../../templates/.dockerignore
- **Приёмка**: `make discover-modules` добавляет status-page в docker-compose.yml. `make gate MODE=fast` — зелёный (module.yaml D4 contract validation passes).
- **Сложность**: 4
- **Зависимости**: TASK-1 (secrets-manifest), параллельно с TASK-2,3,4

### TASK-6: Создание модуля status-page — Dockerfile + app.py (python3-stdlib сервер)
- **Роль**: Coder
- **Файлы**: `core/modules/status-page/Dockerfile` (NEW), `core/modules/status-page/app.py` (NEW), `core/modules/status-page/docker-compose.test.yml` (NEW)
- **Описание**:
  - `Dockerfile`: FROM python:3.12-alpine, RUN pip install pyyaml, COPY app.py /app/, WORKDIR /app, CMD ["python3", "app.py"], HEALTHCHECK (curl localhost:8080/health)
  - `app.py`: python3 stdlib HTTP-сервер на порту 8080:
    - Парсит node.yaml (через PyYAML) → список vhosts + модулей
    - Читает `/run/platform/docker-health.json` → статусы контейнеров
    - Для каждого vhost: `subprocess.run(['curl', '-sf', '--max-time', '5', ...])` → HTTP статус
    - Рендерит HTML (text/html) с таблицей: vhost | статус | ссылка; сервис | контейнер | статус
    - `/health` → JSON `{"status": "PASS"|"FAIL", "checks": ...}`
    - `/status.json` → полный агрегат
    - Заголовки: `X-Robots-Tag`, `Referrer-Policy`, `X-Data-Freshness`
    - Timeout-бюджет: total ≤30s, per-check ≤5s (параллельный fan-out через `subprocess.run` + таймауты)
    - Анти-рекурсия: исключает `status-page` из проверок
  - `docker-compose.test.yml`: test overlay (container_name: status-page-test, порт 18080)
- **Приёмка**: `curl http://localhost:8080/health` → 200 `{"status": "PASS"}` (если docker-health.json есть). `curl http://localhost:8080/` → HTML с таблицей. `curl http://localhost:8080/status.json` → JSON с checks[].
- **Сложность**: 7
- **Зависимости**: TASK-5 (модуль создан)

### TASK-7: Создание модуля status-page — healthcheck.sh
- **Роль**: Coder
- **Файлы**: `core/modules/status-page/healthcheck.sh` (NEW)
- **Описание**: Стандартный healthcheck по контракту: liveness=`check_docker_health status-page`, deep=`check_http http://127.0.0.1:8080/health 200`.
- **Приёмка**: `bash healthcheck.sh` → 0 (если контейнер running). `MODE=deep bash healthcheck.sh` → 0.
- **Сложность**: 1
- **Зависимости**: TASK-6 (app.py должен отвечать на /health)

### TASK-8: docker-healthcheck.sh — экспорт status.json
- **Роль**: Coder
- **Файлы**: `core/internal/healthcheck/docker-healthcheck.sh` (MODIFY)
- **Описание**: Расширить crontab-скрипт: после проверки всех контейнеров записывать `/run/platform/docker-health.json` с JSON-агрегатом статусов всех контейнеров: `[{container_name, running, healthy, exit_code, status_line}]`. Идемпотентно (перезапись каждый запуск).
- **Приёмка**: Ручной запуск `docker-healthcheck.sh` создаёт/обновляет `/run/platform/docker-health.json`. `cat /run/platform/docker-health.json | jq '.[0].container_name'` возвращает имя контейнера.
- **Сложность**: 3
- **Зависимости**: нет (независимая задача)

### TASK-9: verify-domains.sh — интеграция status-page health check
- **Роль**: Coder
- **Файлы**: `core/internal/verify/verify-domains.sh` (MODIFY)
- **Описание**: Добавить в `verify_domains()` после проверки всех expose:true доменов — проверку `/health` эндпоинта главного домена с Basic Auth (используя `PLATFORM_MASTER_EMAIL`/`PLATFORM_MASTER_PASSWORD` из env). `curl -fsS -u "${PLATFORM_MASTER_EMAIL}:${PLATFORM_MASTER_PASSWORD}" https://${main_domain}/health`. Это становится частью CI post-deploy gate.
- **Приёмка**: `make verify NODE=<node>` включает проверку status-page. При недоступности — exit 1.
- **Сложность**: 2
- **Зависимости**: TASK-3 (nginx должен проксировать /health)

### TASK-10: nginx docker-compose.base.yml — status-page интеграция
- **Роль**: Coder
- **Файлы**: `core/modules/nginx/docker-compose.base.yml` (MODIFY)
- **Описание**: Убедиться что nginx и status-page на одной сети (proxy-net), status-page доступен как `status-page:8080` из nginx контейнера. Добавить mount для `.htpasswd-platform`.
- **Приёмка**: `docker compose up nginx status-page`, затем `docker exec nginx curl -sf http://status-page:8080/health` → успешный ответ.
- **Сложность**: 2
- **Зависимости**: TASK-5 (status-page должен существовать) + TASK-3

### TASK-11: Тесты — модульные + интеграционные для status-page
- **Роль**: Coder
- **Файлы**: `tests/test_status_page.py` (NEW)
- **Описание**:
  - `test_status_page_app_health_pass`: мок node.yaml + docker-health.json (все healthy) → `/health` возвращает 200 PASS
  - `test_status_page_app_health_fail`: один контейнер unhealthy → `/health` возвращает 503 FAIL
  - `test_status_page_app_html_contains_vhosts`: `/` возвращает HTML с vhosts из node.yaml
  - `test_status_page_app_status_json_schema`: `/status.json` возвращает валидный JSON с полями status, generated_at, duration_ms, checks[]
  - `test_status_page_app_anti_recursion`: status-page не проверяет сам себя
  - `test_status_page_app_timeout_per_check`: проверка с недоступным vhost → FAIL этой проверки, не весь запрос
  - `test_status_page_app_x_headers`: X-Robots-Tag, Referrer-Policy, X-Data-Freshness присутствуют
- **Приёмка**: `python -m pytest tests/test_status_page.py -s -v` — все тесты зелёные.
- **Сложность**: 5
- **Зависимости**: TASK-6 (app.py готов)

### TASK-12: Gate-тесты — CI gate для status-page
- **Роль**: Coder
- **Файлы**: `tests/gates/test_gate_status_page.py` (NEW), `core/entrypoint-manifest.yaml` (MODIFY — добавить gate в список)
- **Описание**:
  - `test_gate_status_page_module_contract`: module.yaml соответствует D4, docker-compose.base.yml имеет profiles и healthcheck
  - `test_gate_status_page_htpasswd_consistency`: все nginx vhost configs используют `.htpasswd-platform`, нет ссылок на `.htpasswd-monitoring`
  - `test_gate_status_page_nginx_stealth`: nginx config содержит `return 444`, `@stealth_drop`, `access_log off`
  - `test_gate_status_page_secrets_registered`: PLATFORM_MASTER_EMAIL/PASSWORD зарегистрированы в secrets-manifest.yaml
  - `test_gate_status_page_ci_negative`: негативный тест — curl /health без авторизации → не 200 (444, connection reset)
  - `test_gate_status_page_dockerignore_symlink`: .dockerignore в status-page — symlink на templates/.dockerignore
- **Приёмка**: `python -m pytest tests/gates/test_gate_status_page.py -s -v` — все gate-тесты зелёные. `make gate MODE=fast` — зелёный.
- **Сложность**: 4
- **Зависимости**: TASK-3, TASK-4, TASK-5, TASK-6 (все компоненты готовы)

## 5. $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/test_status_page.py` | `test_status_page_app_health_pass` | Все сервисы healthy → /health 200 PASS | `status-page/app.py` |
| `tests/test_status_page.py` | `test_status_page_app_health_fail` | Один сервис unhealthy → /health 503 FAIL | `status-page/app.py` |
| `tests/test_status_page.py` | `test_status_page_app_html_contains_vhosts` | HTML содержит vhosts из node.yaml | `status-page/app.py` |
| `tests/test_status_page.py` | `test_status_page_app_status_json_schema` | /status.json — валидный JSON с обязательными полями | `status-page/app.py` |
| `tests/test_status_page.py` | `test_status_page_app_anti_recursion` | status-page исключён из проверок | `status-page/app.py` |
| `tests/test_status_page.py` | `test_status_page_app_timeout_per_check` | Недоступный vhost → FAIL проверки, не 500 | `status-page/app.py` |
| `tests/test_status_page.py` | `test_status_page_app_x_headers` | X-Robots-Tag, Referrer-Policy, X-Data-Freshness | `status-page/app.py` |
| `tests/test_status_page.py` | `test_htpasswd_generation_idempotent` | Повторный вызов _ensure_htpasswd_generated — no-op | `core/lib/secrets.sh` |
| `tests/test_status_page.py` | `test_htpasswd_generation_creates_valid_file` | Файл содержит правильный APR1 хеш | `core/lib/secrets.sh` |
| `tests/test_status_page.py` | `test_master_creds_fallback_resolution` | SERVICE_SPECIFIC_PASS → PLATFORM_MASTER_PASSWORD fallback | `core/lib/secrets.sh` |
| `tests/gates/test_gate_status_page.py` | `test_gate_status_page_module_contract` | D4 контракт module.yaml, profiles, healthcheck | `status-page/module.yaml` |
| `tests/gates/test_gate_status_page.py` | `test_gate_status_page_htpasswd_consistency` | Все vhosts используют .htpasswd-platform | `nginx/config/*.conf` |
| `tests/gates/test_gate_status_page.py` | `test_gate_status_page_nginx_stealth` | nginx config содержит 444/stealth_drop/access_log off | `nginx/config/platform-default.conf.template` |
| `tests/gates/test_gate_status_page.py` | `test_gate_status_page_secrets_registered` | PLATFORM_MASTER_* в secrets-manifest.yaml | `core/secrets-manifest.yaml` |
| `tests/gates/test_gate_status_page.py` | `test_gate_status_page_ci_negative` | curl /health без авторизации → не 200 (R5) | `nginx + status-page` |
| `tests/gates/test_gate_status_page.py` | `test_gate_status_page_dockerignore_symlink` | .dockerignore symlink integrity | `status-page/.dockerignore` |

## 6. $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- **Tasks**: TASK-1, TASK-8
- **Файлы**: `core/secrets-manifest.yaml` + `.env.example`, `core/internal/healthcheck/docker-healthcheck.sh`
- **Команда**: `coder Read 02-DevPlan.md, implement Wave 1: TASK-1, TASK-8`

### Wave 2 (depends on TASK-1)
- **Tasks**: TASK-2, TASK-5
- **Файлы**: `core/lib/secrets.sh`, `core/modules/status-page/module.yaml` + `docker-compose.base.yml` + `Makefile` + `.dockerignore`
- **Команда**: `coder Read 02-DevPlan.md, implement Wave 2: TASK-2, TASK-5`

### Wave 3 (depends on Wave 2 + TASK-8)
- **Tasks**: TASK-3, TASK-4, TASK-6, TASK-7, TASK-10
- **Файлы**: разные файлы в nginx/config/, status-page/, docker-compose.base.yml
- **Parallel safe**: TASK-3 (nginx config + base.yml), TASK-4 (prometheus/loki vhost), TASK-6 (Dockerfile + app.py), TASK-7 (healthcheck.sh), TASK-10 (nginx base.yml mount) — разные файлы
- **Команда**: `coder Read 02-DevPlan.md, implement Wave 3: TASK-3, TASK-4, TASK-6, TASK-7, TASK-10`

### Wave 4 (depends on Wave 3)
- **Tasks**: TASK-9, TASK-11, TASK-12
- **Файлы**: `core/internal/verify/verify-domains.sh`, `tests/test_status_page.py`, `tests/gates/test_gate_status_page.py`
- **Команда**: `coder Read 02-DevPlan.md, implement Wave 4: TASK-9, TASK-11, TASK-12`

### Wave 5 (final verification)
- **Tasks**: запуск `make gate MODE=fast`, `make discover-modules`, проверка интеграции
- **Команда**: `coder Read 02-DevPlan.md, run Wave 5: final gate + integration check`

## 7. Acceptance Criteria (Summary)

| # | Критерий | Верификация |
|---|----------|-------------|
| AC1 | `/login/` → 401 + WWW-Authenticate | `curl -v https://<domain>/login/` |
| AC2 | HTML с vhosts + сервисами, live-статус, ссылки | Открыть браузер |
| AC3 | `/status.json` — валидный агрегат | `curl -u ... \| jq .status` |
| AC4 | `/health` — бинарный вердикт | `curl -f -u ...` → 0 или 22 |
| AC5 | Timeout ≤30s total, ≤5s per-check | Измерить `time curl .../health` |
| AC6 | Мастер-креды в secrets, не в git | `grep -r PLATFORM_MASTER_PASSWORD core/` → только manifest |
| AC7 | htpasswd-platform используется всеми vhosts | Gate-тест |
| AC8 | Модуль соответствует контракту | `make discover-modules` + gate |
| AC9 | `make gate MODE=fast` зелёный | exit code 0 |
| AC10 | CI post-deploy: негативный тест (R5) | Gate-тест `test_gate_status_page_ci_negative` |

## 8. File Manifest

| Файл | Статус | Задача |
|------|--------|--------|
| `core/modules/status-page/module.yaml` | NEW | TASK-5 |
| `core/modules/status-page/docker-compose.base.yml` | NEW | TASK-5 |
| `core/modules/status-page/docker-compose.test.yml` | NEW | TASK-6 |
| `core/modules/status-page/Makefile` | NEW | TASK-5 |
| `core/modules/status-page/.dockerignore` | NEW (symlink) | TASK-5 |
| `core/modules/status-page/Dockerfile` | NEW | TASK-6 |
| `core/modules/status-page/app.py` | NEW | TASK-6 |
| `core/modules/status-page/healthcheck.sh` | NEW | TASK-7 |
| `core/secrets-manifest.yaml` | MODIFY | TASK-1 |
| `.env.example` | MODIFY | TASK-1 |
| `core/lib/secrets.sh` | MODIFY | TASK-2 |
| `core/modules/nginx/config/platform-default.conf.template` | MODIFY | TASK-3 |
| `core/modules/nginx/config/nginx.conf` | MODIFY | TASK-3 |
| `core/modules/nginx/config/prometheus-vhost.conf` | MODIFY | TASK-4 |
| `core/modules/nginx/config/loki-vhost.conf` | MODIFY | TASK-4 |
| `core/internal/healthcheck/docker-healthcheck.sh` | MODIFY | TASK-8 |
| `core/internal/verify/verify-domains.sh` | MODIFY | TASK-9 |
| `core/modules/nginx/docker-compose.base.yml` | MODIFY | TASK-3, TASK-10 |
| `core/entrypoint-manifest.yaml` | MODIFY | TASK-12 |
| `tests/test_status_page.py` | NEW | TASK-11 |
| `tests/gates/test_gate_status_page.py` | NEW | TASK-12 |

## 9. Контракты (Contract Formalization)

### 9.1 Status-page HTTP API Contract

| Endpoint | Method | Auth | Response | Status codes |
|----------|--------|------|----------|--------------|
| `/` | GET | Basic Auth (через nginx) | text/html — таблица статусов | 200 |
| `/health` | GET | Basic Auth (через nginx) | application/json `{"status":"PASS"\|"FAIL"}` | 200 PASS, 503 FAIL |
| `/status.json` | GET | Basic Auth (через nginx) | application/json — полный агрегат | 200 |

**Заголовки ответа (все эндпоинты):**
- `X-Robots-Tag: noindex, nofollow`
- `Referrer-Policy: no-referrer`
- `X-Data-Freshness: <ISO8601 timestamp>` (время последнего обновления docker-health.json)

### 9.2 docker-health.json Contract

```json
{
  "generated_at": "2026-07-20T10:42:00Z",
  "containers": [
    {
      "container_name": "nginx",
      "running": true,
      "healthy": true,
      "exit_code": 0,
      "status_line": "Up 2 hours (healthy)"
    }
  ]
}
```

### 9.3 htpasswd Contract

- Путь: `/run/platform/.htpasswd-platform` (tmpfs при генерации) → mount в nginx как `/etc/nginx/conf.d/.htpasswd-platform:ro`
- Формат: `email:apr1_hash` (openssl passwd -apr1)
- Владелец: root:root, 644
- Идемпотентность: проверка существующего файла + хеша перед перезаписью

## 10. $IMPACT_MAP

```
NEW MODULE: core/modules/status-page/
  ├── module.yaml              → discover_modules.py (авто-обнаружение)
  ├── docker-compose.base.yml  → docker compose (root include)
  ├── docker-compose.test.yml  → CI test
  ├── Dockerfile               → сборка образа
  ├── app.py                   → бизнес-логика
  ├── healthcheck.sh           → make healthcheck
  └── Makefile                 → module.mk таргеты

MODIFIED: core/modules/nginx/
  ├── config/platform-default.conf.template → /login/ + stealth 444 + proxy_pass status-page
  ├── config/prometheus-vhost.conf          → .htpasswd-platform
  ├── config/loki-vhost.conf                → .htpasswd-platform
  ├── config/nginx.conf                     → server_names_hash (если нужно)
  └── docker-compose.base.yml               → htpasswd mount + status-page network

MODIFIED: core/
  ├── secrets-manifest.yaml     → +PLATFORM_MASTER_EMAIL, +PLATFORM_MASTER_PASSWORD
  ├── lib/secrets.sh            → +_ensure_htpasswd_generated()
  ├── internal/healthcheck/
  │   └── docker-healthcheck.sh → +status.json export
  ├── internal/verify/
  │   └── verify-domains.sh     → +status-page health check
  └── entrypoint-manifest.yaml  → +gate (test_gate_status_page)

MODIFIED: root
  └── .env.example              → +PLATFORM_MASTER_*

NEW TESTS:
  ├── tests/test_status_page.py              → модульные тесты
  └── tests/gates/test_gate_status_page.py   → CI gate-тесты
```

## 11. Next Steps — Implementation Commands

### Wave 1
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/016-node-status-page/02-DevPlan.md, implement Wave 1: TASK-1, TASK-8
```

### Wave 2
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/016-node-status-page/02-DevPlan.md, implement Wave 2: TASK-2, TASK-5
```

### Wave 3
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/016-node-status-page/02-DevPlan.md, implement Wave 3: TASK-3, TASK-4, TASK-6, TASK-7, TASK-10
```

### Wave 4
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/016-node-status-page/02-DevPlan.md, implement Wave 4: TASK-9, TASK-11, TASK-12
```

### Wave 5 (final verification)
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/016-node-status-page/02-DevPlan.md, run Wave 5: make gate MODE=fast, make discover-modules, verify integration
```

$END_DEVPLAN
