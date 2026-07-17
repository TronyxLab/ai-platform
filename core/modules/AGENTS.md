<!-- GREP_SUMMARY: AGENTS.md, core, modules, module-contract, healthcheck, profiles, forbidden -->

# GREP_SUMMARY: AGENTS.md, core, modules, module-contract, healthcheck, profiles, forbidden
# STRUCTURE: ┌module template┐ → ◇ module.yaml D4 → ◇ healthcheck (liveness|deep) → ◇ profiles pluggability → ◇ forbidden → ⎋ navigation
# region MODULE_CONTRACT
## @purpose  Module contract: directory template, module.yaml D4 schema, healthcheck/Makefile contracts, profiles pluggability
## @scope    All docker/systemd service modules under core/modules/
## @invariants
##   - module.yaml — единственный source of truth метаданных модуля (не Docker-specific)
##   - healthcheck.sh: liveness = docker inspect (default), diagnostics = shell healthcheck.sh (MODE=deep)
##   - Makefile: include ../../templates/module.mk; переопределять только канонические таргеты
##   - .dockerignore → symlink ../../templates/.dockerignore
##   - docker-compose.base.yml: profiles: [module-name] на каждом сервисе, x-logging: &default-logging
## @rationale Стандартизированный контракт обеспечивает pluggability через profiles, единый healthcheck и zero-touch деплой; DD1/DD3 — архитектурные стандарты (см. core/AGENTS.md §Pluggability)
# endregion MODULE_CONTRACT

# AGENTS.md — core/modules/

---

## Структура модуля

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

Шаблоны: [`core/templates/`](../templates/) — `module.mk`, `.dockerignore`, `docker-compose.test.template`.

---

## module.yaml — D4 контракт

```yaml
name: postgres                    # без -shared суффикса
install_type: docker              # docker | system
description: "..."
depends_on: [redis]               # для _topo_sort.py
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

## Makefile-контракт

```makefile
MODULE_NAME := example
CONTAINER   := example
include ../../templates/module.mk
```

Канонические таргеты (определены в `module.mk`): `start`, `stop`, `restart`, `status`, `logs`, `build`, `up`, `backup`, `restore`.
Все restart-таргеты используют **hard restart** (`docker compose down && docker compose up -d`), не soft restart (`docker compose restart`). Это обеспечивает гарантированную перезагрузку контейнеров с пересозданием сети и монтирований.
Запрещено: переопределять канонические имена, добавлять свои `build`/`deploy`, healthcheck-логика в Makefile.

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

---

## Навигация

| Файл | Назначение |
|------|-----------|
| [`core/AGENTS.md`](../AGENTS.md) | Канонические операции, структура слоёв, forbidden-списки |
| [`AGENTS.md`](../../AGENTS.md) (root) | Архитектурные инварианты, модель деплоя, глоссарий глаголов |
| [`core/templates/`](../templates/) | Шаблоны module.mk, .dockerignore |
| [`core/lib/`](../lib/) | Библиотеки healthcheck, logging |
