<!-- GREP_SUMMARY: AGENTS.md, core, modules, module-contract, healthcheck, profiles, forbidden -->

# GREP_SUMMARY: AGENTS.md, core, modules, module-contract, healthcheck, profiles, forbidden
# STRUCTURE: ┌module template┐ → ◇ module.yaml D4 → ◇ healthcheck (liveness|deep) → ◇ profiles pluggability → ◇ forbidden → ⎋ navigation
# region MODULE_CONTRACT
## @purpose  Module contract: directory template, module.yaml D4 schema, healthcheck/Makefile contracts, profiles pluggability
## @scope    All docker/systemd service modules under core/modules/
## @invariants
##   - module.yaml — единственный source of truth метаданных модуля (не Docker-specific)
##   - Docker-модули (install_type: docker): healthcheck.sh = docker inspect (default), Makefile = include module.mk
##   - System-модули (install_type: system): Makefile = include module-system.mk; NO docker-compose, NO healthcheck.sh, NO .dockerignore
##   - Docker-модули: .dockerignore → symlink ../../templates/.dockerignore
##   - Docker-модули: docker-compose.base.yml: profiles: [module-name] на каждом сервисе, x-logging: &default-logging
##   - System-модули: lifecycle через systemctl/journalctl, таргеты: install/status/restart/logs
## @rationale Стандартизированный контракт обеспечивает pluggability через profiles, единый healthcheck и zero-touch деплой; DD1/DD3 — архитектурные стандарты (см. core/AGENTS.md §Pluggability). System-контракт добавлен для модулей вне Docker (D3).
# endregion MODULE_CONTRACT

# AGENTS.md — core/modules/

---

## Структура модуля (Docker)

```
core/modules/{module}/
├── module.yaml                 # D4-контракт метаданных
├── docker-compose.base.yml     # profiles: [module-name], HEALTHCHECK, x-logging
├── healthcheck.sh              # source ../../lib/healthcheck.sh
├── Makefile                    # include ../../templates/module.mk
├── .dockerignore               # symlink → ../../templates/.dockerignore
├── config/                     # (опционально)
└── {build,context}/            # (только hermes-agent) Dockerfile L1/L2
```

## Структура модуля (System)

```
core/modules/{module}/
├── module.yaml                 # D4-контракт метаданных (install_type: system)
├── *.service                   # systemd unit file(s)
├── Makefile                    # include ../../templates/module-system.mk
├── install.sh                  # (опционально) скрипт установки для CI
└── config/                     # (опционально)
```

**System-модули НЕ содержат:** `docker-compose.base.yml`, `healthcheck.sh`, `.dockerignore`.

Шаблоны: [`core/templates/`](../templates/) — `module.mk` (Docker), `module-system.mk` (systemd), `.dockerignore`, `docker-compose.test.template`.

---

## module.yaml — D4 контракт

```yaml
name: postgres                    # без -shared суффикса
install_type: docker              # docker | system
description: "..."
depends_on: [redis]               # для _topo_sort.py
interfaces:                       # массив строк — typed contract для cross-layer вызовов
  - healthcheck                  # из internal/bootstrap (healthcheck liveness/readiness)
  - install                      # из internal/bootstrap (system-модули)
  - deploy-hook                  # из internal/deploy (on_project_deploy)
  - remove-hook                  # из internal/deploy (on_project_remove)
  # Пустой [] валиден — модуль не вызывается из internal/
  # Отсутствующее поле = [] (backward compat)
spool_dir: /var/lib/platform/...  # абсолютный путь (deploy-modules.sh)
spool_volume: volume-name
resources:                        # опционально, синхронизировано с base.yml
  limits:    { memory: 1G }
  reservations: { memory: 512M }
env_shared:                       # shared env vars injected into module container(s)
  # ONLY hermes-agent may declare proxy vars (HTTP_PROXY/HTTPS_PROXY/NO_PROXY)
  # See §Proxy opt-in rule below
  SHARED_EXAMPLE: "${SHARED_EXAMPLE}"
env_requires:                     # обязательные в .env/secrets
  - VAR_NAME
```

**Удалены из D4:** `version` (моно-версия), `config.network`, `config.readiness_endpoint`, `config.liveness_endpoint`, `config.stop_grace_period`, `ports`. Docker-specific поля — в `docker-compose.base.yml`.

**Добавлено в D4:** `interfaces` (2026-07-18) — typed contract для cross-layer вызовов из `internal/` в `modules/`. См. `core/lib/module-interface.sh` и `core/AGENTS.md` cross-layer правила.

**Proxy opt-in rule:** Прокси-переменные (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) декларирует в `env_shared` ТОЛЬКО модуль `hermes-agent` — единственный реальный потребитель прокси-канала (Telegram → Tor → Privoxy). Любой другой модуль, которому требуется прокси, должен: (1) добавить переменные в свой `env_shared`, (2) добавить имя модуля в `platform-env.yaml proxy.consumers`. Гейт T8.5 валидирует opt-in контракт — добавление без обоих шагов = красный гейт.

---

## Healthcheck-контракт

Два режима, один entrypoint — `healthcheck.sh`:

| Режим | Механизм | Вызов |
|-------|----------|-------|
| **Liveness** (default) | `docker inspect` → `State.Health.Status` | `check_docker_health "$CONTAINER"` |
| **Deep diagnostics** | Специфичные модулю проверки | `MODE=deep` → `check_http`, `redis-cli ping`, etc. |

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"
CONTAINER="module-name"
MODE="${1:-}"
[ "$MODE" = "deep" ] && { check_http "http://127.0.0.1:8080/health" "200" || exit 1; }
check_docker_health "$CONTAINER" || exit 1
```

`exit 0` — healthy, `exit 1` — unhealthy (включая недоступность зависимостей).

---

## Makefile-контракт (Docker-модули)

```makefile
MODULE_NAME := example
CONTAINER   := example
include ../../templates/module.mk
```

Канонические таргеты (определены в `module.mk`): `start`, `stop`, `restart`, `status`, `logs`, `build`, `up`, `backup`, `restore`.
`restart` использует **soft restart** (`docker compose stop && docker compose start`) — остановка и запуск без пересоздания контейнеров (сохраняет сеть, монтирования и состояние). Для **hard restart** с пересозданием (down + up -d --force-recreate) используйте таргет `restart-hard`, определённый в `module.mk`.
Запрещено: переопределять канонические имена, добавлять свои `build`/`deploy`, healthcheck-логика в Makefile.

---

## Makefile-контракт (System-модули)

Для модулей с `install_type: system` (например, `platform-secrets` — systemd oneshot service) используется альтернативный шаблон `module-system.mk`:

```makefile
SERVICE_NAME := my-systemd-service
include ../../templates/module-system.mk
```

Канонические таргеты (определены в `module-system.mk`): **`install`, `status`, `restart`, `logs`** — все через systemctl/journalctl.

**Запрещённые таргеты** (Docker-семантика, не применима к systemd-модулям): `build`, `up`, `backup`, `down`, `stop`, `start`.

**Контракт:**
| Таргет | Операция | Команда |
|--------|----------|---------|
| `install` | Установка/обновление systemd unit + enable + restart | `cp *.service → /etc/systemd/system/; systemctl daemon-reload; systemctl enable; systemctl restart` |
| `status` | Статус сервиса | `systemctl status --no-pager` |
| `restart` | Перезапуск через systemd | `systemctl restart` |
| `logs` | Логи сервиса | `journalctl -u <unit> --no-pager -n 50` |

**Отличия от Docker-контракта:**
1. Нет `start`/`stop` — systemd управляет жизненным циклом через `install`/`restart`
2. Нет `build` — образы не собираются (systemd-модули не контейнеризированы)
3. Нет `backup`/`restore` — stateful persistence не предусмотрена (tmpfs-based)
4. `install` — первичная установка (копирование unit-файла, включение, запуск)
5. `restart` — перезапуск через systemd (аналог `systemctl restart`)

**Важно:** System-модули НЕ имеют `docker-compose.base.yml`, `.dockerignore`, `healthcheck.sh` (healthcheck — через systemd unit-статус). Метрики и мониторинг — через journald.

---

## Pluggability через profiles

Каждый сервис в `docker-compose.base.yml` имеет `profiles: [module-name]`. Root compose использует `include:` — без инлайн-сервисов.

```
make up MODULES=postgres,redis → COMPOSE_PROFILES=postgres,redis → только указанные модули
make up                        → все модули (profiles не фильтруют)
```

**DD1:** `x-logging: &default-logging` дублируется в каждом `base.yml` — YAML anchors из root compose недоступны в include'd файлах.
**DD3:** Все `${VAR:?error}` заменены на `${VAR:-}` — `docker compose config` валидирует все include'd файлы, даже неактивные profiles.

---

## docker-compose.override.yml

- `docker-compose.override.yml` — per-project кастомизация (autodiscovery, в `.gitignore`)
- Платформенные overrides (`docker-compose.platform-dev.yml`, `docker-compose.macos.yml`) — явные через `-f`

---

## Запреты

| # | Запрет | Причина |
|---|--------|---------|
| 1 | Переопределять канонические таргеты | Ломает `make gate` |
| 2 | Импортировать из `internal/` | Cross-layer violation (см. `core/AGENTS.md`) |
| 3 | Свои `build`/`deploy` таргеты | Только корневой Makefile |
| 4 | `.dockerignore` не symlink | Дедупликация через шаблон |
| 5 | Healthcheck-логика в Makefile | Только `healthcheck.sh` |
| 6 | `${VAR:?error}` в `docker-compose.base.yml` | Блокирует валидацию неактивных profiles (DD3) |
| 7 | Инлайн-сервисы в root `docker-compose.yml` | Только `include:` модулей |
| 8 | Docker-таргеты (build/up/backup/down) в system-модулях | System-модули используют module-system.mk, не module.mk |

---

## docker-compose.test.yml contract

Каждый Docker-модуль предоставляет `docker-compose.test.yml` — test-overlay,
параллельный production-конфигурации через container_name суффикс `-test`.

### Инварианты
- `container_name: <container>-test` для ВСЕХ контейнеров модуля — предотвращает конфликты с production
- `restart: "no"` — тестовые контейнеры не авто-перезапускаются
- Volumes: Docker-managed (не bind-mount)
- Port mappings: смещённые по правилу `1{port}` (e.g., 80→18080, 5432→15432) на 127.0.0.1
- Healthcheck: ускоренный (start_period=10s, interval=10s) для CI

### Collision policy
При коллизии `1{port}` с production портом — разработчик модуля выбирает:
(a) префикс `2` (e.g., 8000→28000)
(b) сдвиг разрядности (e.g., 3XXXX)
(c) явный свободный порт с TRAP[DECISION]

### Gate coverage
- test_gate_compose_no_base_image: нет L1-образа в production compose
- test_restart_consistency: restart-политика
- test_gate_container_name_consistency: container_name консистентность
- test_gate_healthcheck_contract: healthcheck контракты

---

## Навигация

| Файл | Назначение |
|------|-----------|
| [`core/AGENTS.md`](../AGENTS.md) | Канонические операции, структура слоёв, forbidden-списки |
| [`AGENTS.md`](../../AGENTS.md) (root) | Архитектурные инварианты, модель деплоя, глоссарий глаголов |
| [`core/templates/`](../templates/) | Шаблоны module.mk, module-system.mk, .dockerignore |
| [`core/lib/`](../lib/) | Библиотеки healthcheck, logging |
