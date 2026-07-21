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
## @rationale Стандартизированный контракт обеспечивает pluggability через profiles, единый healthcheck и zero-touch деплой; DD1/DD3 — архитектурные стандарты (см. core/AGENTS.md §Pluggability). DD3 superseded 2026-07-21 (DevPlan 033 Option A). System-контракт добавлен для модулей вне Docker (D3).
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

## module.yaml — D5 контракт

```yaml
name: postgres                    # без -shared суффикса
install_type: docker              # docker | system
description: "..."
depends_on: [redis]               # для _topo_sort.py
severity: normal                  # D5: critical | normal (critical — deploy failure blocks node-update)
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
env_requires:                     # D5: typed env_requires — bare string (D4 compat) OR object {name, type, required}
  - VAR_NAME                      # bare string = {type: secret, required: true} (D4 backward-compat)
  - name: SECRET_VAR              # D5 typed: type=secret (default), required=true (default)
    type: secret
    required: true
  - name: OPTIONAL_STRING         # D5 typed: type=string, required=false
    type: string
    required: false
restart: unless-stopped           # D5: модуль-уровневая restart-политика для cross-check с base.yml
                                  # enum: [always, unless-stopped, no, on-failure]
                                  # severity:critical + restart: always/unstop-stopped = carve-out (принимается)
```


**Удалены из D4:** `version` (моно-версия), `config.network`, `config.readiness_endpoint`, `config.liveness_endpoint`, `config.stop_grace_period`, `ports`. Docker-specific поля — в `docker-compose.base.yml`.

**Добавлено в D4:** `interfaces` (2026-07-18) — typed contract для cross-layer вызовов из `internal/` в `modules/`. См. `core/lib/module-interface.sh` и `core/AGENTS.md` cross-layer правила.

**Добавлено в D5 (DevPlan 033 Wave 3, 2026-07-21):**
- `env_requires`: typed entries — bare string (D4 compat, treated as `{type: secret, required: true}`) OR object `{name, type: string|secret|int|bool, required: bool}`. Валидатор: `core/internal/scripts/validate_module_yaml.py --all`.
- `restart`: модуль-уровневая restart-политика (`always|unless-stopped|no|on-failure`) — cross-check с per-service restart в `docker-compose.base.yml` (drift detection). Carve-out: `severity: critical` + `restart: always` в module.yaml принимается даже если compose говорит `unless-stopped` (W3-R7).
- `severity`: enum `critical|normal` — critical модули блокируют node-update при ошибке деплоя (exit 2). Carve-out для restart-drift.
- Schema: `core/schemas/module.schema.json` расширена до D5 (backward-compat через `oneOf`).
- CI enforcement: gate `test_gate_module_yaml_contract.py` + `make validate-modules` (W3-E5).

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
**DD3 (SUPERSEDED 2026-07-21):** `${VAR:?error}` теперь РАЗРЕШЁН в `docker-compose.base.yml` (DevPlan 033 Option A). Для совместимости CI вызовы `docker compose config` используют `COMPOSE_PROFILES=<all-profiles>`. См. TRAP[DECISION] ниже.
# ⚠️ TRAP[DECISION] · 2026-07-21 · HI · DD3 reversed — `${VAR:?error}` now enforced in base.yml (DevPlan 033 W3-E3)
# · Rejected: static-validator-only (Option B, score 8/10) — operator chose runtime-fail-fast per W3-E3 brief letter
# · Reason: P07 closed by compose-runtime enforcement, not CI-gate-only. Acceptable ripple-cost: CI invocation update.
# · Implementation: 4 critical secrets use ${VAR:?...} in 7 base.yml files;
#   CI exports COMPOSE_PROFILES="<all-profiles>" before every `docker compose config` call.
#   ⚠️ --skip-check-profiles does NOT exist in Docker Compose (verified v5.3.0).
# · Revert-path: git revert <merge-commit> + restore raw ${VAR} + restore запрет #6.
# · Rev: if CI compose-v2 incompatibility discovered → fall back to Option B (static validator).

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
| ~~6~~ | ~~`${VAR:?error}` в `docker-compose.base.yml`~~ | **REVERSED 2026-07-21 (TRAP[DECISION])** — Option A collapse Wave 3 DevPlan 033; COMPOSE_PROFILES export in CI workflows |
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
