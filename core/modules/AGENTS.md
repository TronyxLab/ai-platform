<!-- GREP_SUMMARY: AGENTS.md, core, modules, module-contract, healthcheck, profiles -->

# GREP_SUMMARY: AGENTS.md, core, modules, module-contract, healthcheck, profiles
# STRUCTURE: ┌module template┐ → ◇ module.yaml D5 → ◇ healthcheck (liveness|deep) → ◇ profiles pluggability → ⎋ navigation
# region MODULE_CONTRACT
## @purpose  Module contract: directory template, module.yaml D5 schema, healthcheck/Makefile contracts, profiles pluggability
## @scope    All docker/systemd service modules under core/modules/
## @invariants
##   - module.yaml — единственный source of truth метаданных модуля (не Docker-specific)
##   - Docker-модули (install_type: docker): healthcheck.sh = docker inspect (default), Makefile = include module.mk
##   - System-модули (install_type: system): Makefile = include module-system.mk; NO docker-compose, NO healthcheck.sh, NO .dockerignore
##   - Docker-модули: .dockerignore → symlink ../../templates/.dockerignore
##   - Docker-модули: docker-compose.base.yml: profiles: [module-name] на каждом сервисе, x-logging: &default-logging
##   - System-модули: lifecycle через systemctl/journalctl, таргеты: install/status/restart/logs
## @rationale Стандартизированный контракт обеспечивает pluggability через profiles, единый healthcheck и zero-touch деплой; DD1/DD3 — архитектурные стандарты (см. core/AGENTS.md §Pluggability). System-контракт добавлен для модулей вне Docker.
# endregion MODULE_CONTRACT

# AGENTS.md — core/modules/

---

## Структура модуля (Docker)

```
core/modules/{module}/
├── module.yaml                 # D5-контракт метаданных
├── docker-compose.base.yml     # profiles: [module-name], HEALTHCHECK, x-logging
├── healthcheck.sh              # uses lib/healthcheck.sh
├── Makefile                    # include ../../templates/module.mk
├── .dockerignore               # symlink → ../../templates/.dockerignore
├── sudo-whitelist.conf         # (опционально) symlink → ../../templates/sudo-whitelist.template
│                               #   если модулю нужны sudo-операции (все 6 файлов — symlink'и,
│                               #   dedup-контракт test_dedup_contract; sudoers_generator
│                               #   рендерит template, НЕ читает conf)
├── Dockerfile                  # (опционально) модуль с собственным образом
│                               #   (backup-cron, postgres, status-page, hermes-agent —
│                               #   единый multi-stage, L1→L2 коллапс DevPlan 002)
├── config/                     # (опционально)
└── {build,context}/            # (только hermes-agent) payload-деревья единого Dockerfile
                                #   (build/ = base-артефакты, context/ = overlay final-стадии)
```

## Структура модуля (System)

```
core/modules/{module}/
├── module.yaml                 # D5-контракт метаданных (install_type: system)
├── *.service                   # systemd unit file(s)
├── Makefile                    # include ../../templates/module-system.mk
├── install.sh                  # (опционально) ТОНКИЙ фасад → installer.py
│                               #   (platform-secrets: install.sh 25 LOC exec python3 installer.py —
│                               #   завершённый Strangler)
└── config/                     # (опционально)
```

**System-модули НЕ содержат:** `docker-compose.base.yml`, `healthcheck.sh`, `.dockerignore`.

Шаблоны: [`core/templates/`](../templates/) — `module.mk` (Docker), `module-system.mk` (systemd), `.dockerignore`, `sudo-whitelist.template`.

---

## module.yaml — D5 контракт

```yaml
name: postgres                    # без -shared суффикса
install_type: docker              # docker | system
description: "..."
depends_on: [redis]               # для topo_sort.py
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


**Ключевые поля D5** (валидатор: `make validate-modules`; schema: `core/schemas/module.schema.json`,
backward-compat через `oneOf`; гейт `test_gate_module_yaml_contract.py`):
- `interfaces` — typed contract для cross-layer вызовов `internal/` → `modules/`
  (`core/lib/module-interface.sh`).
- `env_requires` — bare string (D4 compat = {type: secret, required: true}) или объект
  `{name, type: string|secret|int|bool, required: bool}`.
- `restart` — модуль-уровневая restart-политика; cross-check с per-service restart в base.yml.
  Carve-out: `severity: critical` + `restart: always` принимается даже при `unless-stopped` в compose.
- `severity` — `critical|normal`; critical блокирует node-update при ошибке деплоя (exit 2).
- Docker-specific поля (ports, config.endpoints и пр.) удалены из D4 — живут в `docker-compose.base.yml`.

**Proxy opt-in rule:** Прокси-переменные (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) декларирует в `env_shared` ТОЛЬКО модуль `hermes-agent` — единственный реальный потребитель прокси-канала (Telegram → Tor → Privoxy). Любой другой модуль, которому требуется прокси, должен: (1) добавить переменные в свой `env_shared`, (2) добавить имя модуля в `platform-env.yaml proxy.consumers`. Гейт T8.5 валидирует opt-in контракт — добавление без обоих шагов = красный гейт.

---

## Healthcheck-контракт

Два режима, один entrypoint — `healthcheck.sh`. Deep mode ALWAYS runs `check_docker_health` FIRST (same as liveness), THEN adds service-specific diagnostics. This ensures deep is a strict superset of liveness, not a parallel alternative.

### 3 canonical primitives

| Примитив | Функция | Назначение |
|----------|---------|------------|
| `check_docker_health` | `core/lib/healthcheck.sh` | Liveness: Docker HEALTHCHECK status via `docker inspect State.Health.Status` |
| `check_http` | `core/lib/healthcheck.sh` | HTTP endpoint verification via curl (with configurable timeout) |
| `exec_check` | `core/lib/healthcheck.sh` | Docker exec + service tool (replaces copy-paste `docker exec` pattern) |

| Режим | Механизм | Вызов |
|-------|----------|-------|
| **Liveness** (default) | `check_docker_health "$CONTAINER"` | uses lib/healthcheck.sh → `check_docker_health` |
| **Deep diagnostics** | `check_docker_health` + service check | `MODE=deep` → `check_docker_health` THEN `check_http`/`exec_check` |

### Unified contract template

Шаблон: source ../../lib/healthcheck.sh → liveness: `check_docker_health "$CONTAINER"`;
deep: `check_docker_health` СНАЧАЛА (strict superset of liveness), затем
`exec_check`/`check_http` + `log_imp 9`. `exit 0` — healthy, `exit 1` — unhealthy
(включая недоступность зависимостей).

### Orchestrator contract

`core/internal/healthcheck/modules_healthcheck.py` uses `invoke_module_interface` for ALL modules (docker + system) in ALL modes (liveness + deep). Raw `docker inspect` eliminated — healthcheck идёт только через module-интерфейсы.

---

## Makefile-контракт (Docker-модули)

```makefile
MODULE_NAME := example
CONTAINER   := example
include ../../templates/module.mk
```

Канонические таргеты (определены в `module.mk`; `restart` — в `Makefile.common`): `start`, `stop`, `down`, `restart`, `restart-hard`, `status`, `logs`, `build`, `up`.

**Точная семантика:**
- `stop` = `compose stop --timeout $(STOP_TIMEOUT)` (default 30) — контейнеры **СОХРАНЯЮТСЯ**;
- `down` = `compose down` — реальное удаление контейнеров (отдельный таргет, не алиас stop);
- `restart` = `stop + start` — **soft restart БЕЗ пересоздания** (сохраняет сеть, монтирования и состояние);
- `restart-hard` = `down && up -d --force-recreate` — hard restart с пересозданием (для подхвата новых конфигов/образов);
- `up` = `up -d --force-recreate`.

**Backup/restore — опциональные таргеты stateful-модулей.** Только 3 модуля объявляют их:

| Модуль | BACKUP_MODE | Механизм |
|--------|-------------|----------|
| `postgres` | `custom` | `pg_dumpall` via docker exec / `psql` restore (DUMP_FILE) |
| `backup-cron` | `custom` | `docker exec` `/usr/local/bin/backup-postgres.sh` / делегация в postgres restore |
| `hermes-agent` | `file` | `docker cp` — BACKUP_SOURCE_FILE=`/app/state.json` (generic-контракт module.mk) |

Остальные модули (nginx, status-page, infra-metrics, litellm, langfuse, logging, monitoring, redis, minio, clickhouse) **НЕ объявляют** backup/restore. `make restore` на stateless-модуле = «No rule to make target» — это ожидаемое поведение, не тихий no-op.

**Канон volume-rename (test-оверрайды):** test-оверрайды (`docker-compose.test.yml`) НЕ переопределяют volume in-place — compose deep-merge не умеет удалять ключи (driver_opts/bind-mount сохраняются). Вместо этого объявляется НОВЫЙ volume с суффиксом `-test` и сервис перепривязывается к нему. Примеры канона: `postgres-data-test` (postgres), `backup-spool-test`/`backup-logs-test` (backup-cron), `clickhouse-data-test` (clickhouse), `hermes-data-test` (hermes-agent).

# ⚠️ TRAP[DECISION] · — · Volume-rename канон для test-оверрайдов: compose deep-merge НЕ может удалить ключ volume — объявляется НОВЫЙ volume с суффиксом -test и сервис перепривязывается к нему
· Rev: если docker compose добавит возможность null-override volume-ключа при deep-merge — вернуться к in-place переопределению без суффикса -test

Запрещено: переопределять канонические имена, добавлять свои `build`/`deploy`, healthcheck-логика в Makefile.

---

## Restart-policies и healthcheck-контракт

**Правило restart:**

| Политика | Применение |
|----------|------------|
| `unless-stopped` | **Default для всех long-running** (stateful и stateless) |
| `always` | **Allowlist с обоснованием:** postgres (module.yaml severity=critical — stateful-ядро платформы), redis (no-volume cache-only, потеря = пересоздание), backup-cron (cron-демон должен пережить crash) |
| `"no"` | Init-контейнеры (minio-createbuckets, prometheus-config-init) с `condition: service_completed_successfully` у зависимых |

Другие политики (`on-failure` и т.д.) — запрещены без архитектурного ревью. Gate:
`tests/gates/test_gate_compose_restart_policies.py`.

**Правило healthcheck (канон параметров):**

| Параметр | Канон | Документированные исключения |
|----------|-------|------------------------------|
| interval | 15-30s | backup-cron 60s (cron-демон, частый проб неинформативен) |
| timeout | 5-10s | — |
| retries | 3 | — |
| start_period | 15s | nginx 5s (быстрый старт), minio 30s (server+heals), litellm 60s (Prisma migrate) |

- **Liveness-only** (loki/exporters/cadvisor/alloy/backup-cron — проб бинарного `--version`/`pgrep`):
  допустимы с комментарием «readiness внешний через Prometheus up{} + alert-rules».
- **0 long-running сервисов без healthcheck** — обязательный инвариант.
- **`localhost` в healthcheck** — запрещён: curl/wget резолвит localhost → ::1 (IPv6),
  приложения слушают IPv4 → healthcheck фейлится. Использовать `127.0.0.1`.

**Шаблоны проектов:** `restart: unless-stopped` (не `always` — шаблоны = пример без
оверхеда); healthcheck interval 15s; backend `127.0.0.1` (не localhost).

---

## Compose-include контракт

**Правило:** относительные bind-пути в compose-файле резолвятся от **файла, где объявлены** (include-файла), а НЕ от корня стека. `docker compose include:` мёржит файлы, но каждый файл сохраняет собственную базу резолюции относительных путей.

**Следствия (обязательны для модульных `docker-compose.base.yml`):**

1. **Колокализация обязательна:** bind-источники, на которые ссылается модульный base-файл (`./app.py`, `./templates/`), ДОЛЖНЫ лежать в директории модуля — иначе резолюция зависит от того, кто включает файл (дрейф).
2. **Хост-пути вне директории модуля — только через env-параметризацию** `${VAR:-default}` (НЕ литералы `/var/lib/platform/*`, `/opt/*`): dev-машина (macOS) не имеет этих директорий, прод-нода не имеет локальных dev-путей. Прецеденты канона: `STATUS_METRICS_JSON` (status-page), `NODE_CONFIGS_DIR` (status-page), `PROMETHEUS_TARGETS_DIR`/`PROMETHEUS_RULES_DIR` (monitoring), `NGINX_CERT_DIR` (nginx).
3. **dev-оверрайды:** `.env` (локальный, не коммитится в payload) или `docker-compose.macos.yml` — единственное место dev-переопределений путей.
   (`docker-compose.platform-dev.yml` удалён DevPlan 002 — L1 dev-оверрайд hermes мёртв; dev = единый образ hermes-agent-context с CONTEXT=test.)
4. **Контракт локального стека:** локальный `make up` работает healthy с dev-оверрайдами `.env`; новые модули обязаны следовать п.2 (env-параметризация) — литерал `/var/lib/platform/*` в новом модульном base-файле = нарушение.

# ⚠️ TRAP[DECISION] · — · Compose-include резолюция путей: относительные bind-пути резолвятся от файла, где объявлены (include-файла), НЕ от корня стека; колокализация обязательна; хост-пути вне директории модуля — только через env-параметризацию · Rev: если docker compose изменит резолюцию относительных путей — обновить правило

---

## Makefile-контракт (System-модули)

Для модулей с `install_type: system` (например, `platform-secrets` — systemd oneshot service) используется альтернативный шаблон `module-system.mk`:

```makefile
SERVICE_NAME := my-systemd-service
include ../../templates/module-system.mk
```

Канонические таргеты (определены в `module-system.mk`): **`install`, `status`, `restart`, `logs`** — все через systemctl/journalctl.

**Запрещённые таргеты** (Docker-семантика, не применима к systemd-модулям): `build`, `up`, `backup`, `down`, `stop`, `start`.

**Контракт:** `install` = cp unit + daemon-reload + enable + restart; `status` = systemctl
status; `restart` = systemctl restart; `logs` = journalctl -u [unit] -n 50. Нет start/stop
(systemd управляет жизненным циклом), нет build (образы не собираются), нет backup/restore.

**Важно:** System-модули НЕ имеют `docker-compose.base.yml`, `.dockerignore`, `healthcheck.sh` (healthcheck — через systemd unit-статус). Метрики и мониторинг — через journald.

---

## Pluggability через profiles

Каждый сервис в `docker-compose.base.yml` имеет `profiles: [module-name]`. Root compose использует `include:` — без инлайн-сервисов.

```
make up MODULES=postgres,redis → COMPOSE_PROFILES=postgres,redis → только указанные модули
make up                        → все модули (profiles не фильтруют)
```

**DD1:** `x-logging: &default-logging` дублируется в каждом `base.yml` — YAML anchors из root compose недоступны в include'd файлах.
**DD3 (reversed):** `${VAR:?error}` теперь РАЗРЕШЁН в `docker-compose.base.yml`. Для совместимости CI вызовы `docker compose config` используют `COMPOSE_PROFILES=<all-profiles>`. См. TRAP[DECISION] ниже.
# ⚠️ TRAP[DECISION] · — · DD3 reversed: `${VAR:?error}` в docker-compose.base.yml — runtime fail-fast (не static-валидатор); CI экспортирует COMPOSE_PROFILES="<all-profiles>" перед каждым `docker compose config`; `--skip-check-profiles` НЕ существует в Docker Compose · Rev: если появится CI compose-несовместимость — fallback на static-валидатор

---

## docker-compose.override.yml

- `docker-compose.override.yml` — per-project кастомизация (autodiscovery, в `.gitignore`)
- Платформенные overrides (`docker-compose.macos.yml`) — явные через `-f`
  (`docker-compose.platform-dev.yml` удалён DevPlan 002 — L1 dev-оверрайд hermes мёртв)

---

## Запреты

| # | Запрет | Причина |
|---|--------|---------|
| 1 | Переопределять канонические таргеты | Ломает `make gate` |
| 2 | Импортировать из `internal/` | Cross-layer violation (см. `core/AGENTS.md`) |
| 3 | Свои `build`/`deploy` таргеты | Только корневой Makefile |
| 4 | `.dockerignore` не symlink | Дедупликация через шаблон |
| 5 | Healthcheck-логика в Makefile | Только `healthcheck.sh` |
| ~~6~~ | ~~`${VAR:?error}` в `docker-compose.base.yml`~~ | **REVERSED (TRAP[DECISION])** — `${VAR:?error}` разрешён; COMPOSE_PROFILES export в CI workflows |
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
- test_gate_compose_restart_policies: restart-политика
- test_gate_structural_consistency: container_name registry + depends_on резолвимость
- test_gate_healthcheck_contract: healthcheck контракты

---

## Навигация

| Файл | Назначение |
|------|-----------|
| `core/AGENTS.md` | Канонические операции, структура слоёв |
| `AGENTS.md` (root) | Архитектурные инварианты, модель деплоя, глоссарий глаголов |
| [`core/templates/`](../templates/) | Шаблоны module.mk, module-system.mk, .dockerignore |
| [`core/lib/`](../lib/) | Библиотеки healthcheck, logging |
