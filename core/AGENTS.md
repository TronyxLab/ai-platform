<!-- GREP_SUMMARY: AGENTS.md, core, operations-catalog, canonical-targets, layer-structure, verb-dictionary, forbidden -->

# GREP_SUMMARY: AGENTS.md, core, operations-catalog, canonical-targets, layer-structure, verb-dictionary, forbidden
# STRUCTURE: ┌canonical operations table┐ → ◇ core/ dir structure → ◇ cross-layer import rules → ◇ forbidden lists → ⎋ navigation refs
# region MODULE_CONTRACT
## @purpose  Catalog of canonical make targets, core/ directory structure, cross-layer import rules, and forbidden scripts/verbs for the ai-platform core
## @scope    All operations that pass through Makefile; layer isolation rules; deleted/forbidden script inventory
## @invariants
##   - Every Makefile .PHONY target maps to a row in the canonical operations table
##   - Entrypoints only call internal/ or lib/ — never modules/
##   - Forbidden lists are explicit deny — no additions without Architect approval
## @rationale Machine-readable operations catalog enables CI gates to validate Makefile/AGENTS.md/filesystem triad
# endregion MODULE_CONTRACT

# AGENTS.md — core/

---

## Канонические операции

| Канонический таргет | Операция | Сигнатура | Делегирует в (internal) |
|---|---|---|---|
| `make deploy` | Деплой проекта через git push → CI | `make deploy PROJECT=<dir>` | git push → CI → `core/internal/deploy/deploy-project.sh` |
| `make deploy-project` | Прямой деплой минуя CI (emergency) | `make deploy-project PROJECT=<dir> NODE=<node>` | `core/entrypoints/deploy-project.sh` → SSH platform-deliver + deploy.sh |
| `make bootstrap-node` | Идемпотентный bootstrap ноды | `make bootstrap-node NODE=<name>` | `core/entrypoints/bootstrap.sh` → `core/internal/bootstrap/node-lifecycle.sh --mode init` |
| `make context-promote` | Промоут платформы в контекст | `make context-promote CONTEXT=<context>` | `core/entrypoints/context-promote.sh` → копирование кода в `<context>/ai-platform` |
| `make hermes-build-platform` | Сборка L1 локально | `make hermes-build-platform` | `core/entrypoints/build.sh` → `core/internal/build/hermes-images.sh build-platform` |
| `make hermes-build-context` | Сборка L1→L2, опционально push | `make hermes-build-context CONTEXT=<context>` | `core/entrypoints/build.sh` → `core/internal/build/hermes-images.sh build-context` |
| `make hermes-push-l1` | Push L1 в ghcr.io (backup) | `make hermes-push-l1` | docker tag + docker push |
| `make test` | Тесты с MARKER-фильтром. MARKER=all (default): validate→lint→gates→contract→static→predeploy→smoke→component→integration | `make test [MARKER=static|smoke|component|integration|predeploy|contract|e2e|all]` | pytest с MARKER-диспетчеризацией + validate.sh + lint |
| `make test-inventory-sync` | Регенерация test_inventory.yaml из pytest --collect-only | `make test-inventory-sync` | `tests/tools/sync_inventory.py` |
| `make gate` | Production gate (MODE=fast/full). MODE=full: validate→lint→gates→contract→static→predeploy→smoke→component. MODE=fast: validate→lint→gates→static→predeploy. | `make gate [MODE=fast|full]` | validate.sh + линтеры + pytest + MODE-диспетчеризация |
| `make new-project` | Создать проект из шаблона | `make new-project NAME=<n> TEMPLATE=<t>` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/add-project.sh` |
| `make new-context` | Создать контекст деплоя | `make new-context NODE=<n>` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/context-init.sh` |
| `make project-sync-env` | Синхронизация .env.platform из platform-env.yaml | `make project-sync-env [NAME=<name>] [DOMAIN=<domain>]` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/gen-env-platform.sh` |
| `make remove-project` | Удаление проекта из lifecycle (safe — без потери данных) | `make remove-project NAME=<name> [NODE=<node>]` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/remove-project.sh` |
| `make adopt-project` | Адаптация существующего проекта в lifecycle | `make adopt-project DIR=<dir> [NAME=<name>] [DOMAIN=<domain>]` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/adopt-project.sh` |
| `make project-list` | Список зарегистрированных проектов (offline) | `make project-list [NODE=<node>]` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/project-list.sh` |
| `make project-status` | Статус проектов на target node (SSH) | `make project-status NAME=<name> [NODE=<node>]` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/project-list.sh --status` |
| `make templates-check` | Dry-run проверка разрешимости всех шаблонов | `make templates-check` | `core/internal/template-engine.sh check --verbose` |
| `make templates-render` | Рендер всех шаблонов по манифесту | `make templates-render` | `core/internal/template-engine.sh render-all` |
| `make validate` | Schema-валидация | `make validate [FILES=...]` | `core/entrypoints/validate.sh` |
| `make lint` | shellcheck + yamllint + pytest-lint | `make lint` | `core/entrypoints/validate.sh --lint` |
| `make audit` | Системный аудит платформы | `make audit [NODE=...]` | `core/entrypoints/audit.sh` |
| `make check-file-lines` | Проверка длины файлов | `make check-file-lines [MAX_LINES=500]` | `core/entrypoints/check-file-lines.sh` |
| `make scripts-audit` | Аудит регистрации shebang-скриптов в manifest или exceptions | `make scripts-audit` | `core/internal/scripts-audit.sh` |
| `make dev-certs` | Генерация dev SSL-сертификатов (idempotent, hybrid mkcert→openssl) | `make dev-certs [CERT_BACKEND=auto|mkcert|openssl]` | `core/modules/nginx/generate-dev-certs.sh` |
| `make provision` | Provision окружения | `make provision [SCOPE=all|networks|volumes|env]` | `core/internal/provision-environment.sh` |
| `make discover-modules` | Авто-обнаружение модулей и обновление docker-compose.yml | `make discover-modules` | `core/internal/bootstrap/discover_modules.py` |
| `make secrets-unlock` | Расшифровка SOPS/age секретов | `make secrets-unlock [NODE=...]` | `core/entrypoints/secrets.sh` → `core/internal/secrets/decrypt-secrets.sh` |
| `make healthcheck` | Проверка здоровья платформы | `make healthcheck [NODE=...]` | `core/entrypoints/healthcheck.sh` → `core/internal/healthcheck/modules-healthcheck.sh` |
| `make node-update` | Update provisioned node | `make node-update NODE=<name>` | `core/entrypoints/node-update.sh` → `core/internal/bootstrap/node-lifecycle.sh --mode update` |
| `make converge` | Реконсиляция ноды с desired state | `make converge NODE=<name>` | `core/entrypoints/converge.sh` → `core/internal/bootstrap/converge.sh` |
| `make render-vhosts` | Генерация vhost конфигов из node.yaml | `make render-vhosts NODE=<name>` | `core/entrypoints/scaffold.sh` → `core/internal/scaffold/add-vhost.sh --render-all` |
| `~~make project-sync-secrets~~` | ~~Раскатка repo-secrets~~ (DISABLED — требуется T3.6) | `~~make project-sync-secrets NAME=<name>~~` | `~~core/internal/scaffold/sync-repo-secrets.sh~~` |
| `make verify` | Пост-деплойная HTTPS-верификация | `make verify NODE=<node>` | `core/entrypoints/verify.sh` → `core/internal/verify/verify-domains.sh` |
| `make up` / `make down` | Локальный compose-lifecycle | `make up [PROJECT=...]` | docker compose |
| `make status` | Статус compose-стека | `make status [PROJECT=...]` | docker compose ps |
| `make restart` | Мягкий перезапуск compose-стека | `make restart [PROJECT=...]` | docker compose stop && docker compose start |
| `make backup` | Резервное копирование стека | `make backup [NODE=...]` | Модульные healthcheck.sh + snapshot |
| `make restore` | Восстановление из бэкапа | `make restore NODE=<n> DUMP_FILE=<f>` | Модульные restore-скрипты |
| `make build` | Сборка Docker-образа модуля | `make build (в модуле)` | docker compose build |
| `make logs` | Логи модуля | `make logs (в модуле)` | docker compose logs -f |
| `make start` | Старт модуля | `make start (в модуле)` | docker compose up -d |
| `make stop` | Стоп модуля | `make stop (в модуле)` | docker compose down |

**По умолчанию:** `make healthcheck` и `make audit` без NODE проверяют локальный docker compose. С NODE — удалённую ноду через SSH.

---

## Структура core/

```
core/
├── entrypoints/                # Internal-обёртки — только из Makefile
│   ├── deploy.sh
│   ├── deploy-project.sh
│   ├── bootstrap.sh
│   ├── context-promote.sh
│   ├── build.sh
│   ├── scaffold.sh
│   ├── validate.sh
│   ├── audit.sh
│   ├── secrets.sh
│   ├── healthcheck.sh
│   ├── check-file-lines.sh
│   ├── lint.sh
│   ├── check-commit-msg.sh
│   ├── check-doc-headers.sh
│   └── pre-push-gate.sh
├── internal/                   # Внутренние скрипты — не вызывать напрямую
│   ├── deploy/deploy-project.sh
│   ├── healthcheck/
│   │   ├── docker-healthcheck.sh
│   │   ├── modules-healthcheck.sh
│   │   └── tor-proxy-healthcheck.sh
│   ├── bootstrap/node-lifecycle.sh        (объединяет orchestrator.sh + node-update.sh, mode dispatch)
│   ├── bootstrap/deploy-modules.sh
│   ├── bootstrap/install-tor-proxy.sh
│   ├── bootstrap/setup-node.sh
│   ├── bootstrap/install-docker.sh
│   ├── bootstrap/firewall.sh
│   ├── build/hermes-images.sh             (бывший build-hermes-images.sh)
│   ├── template_engine.py                (NEW — Python template engine)
│   ├── template-engine.sh                (NEW — Bash CLI wrapper)
│   ├── scaffold/add-project.sh
│   ├── scaffold/context-init.sh
│   ├── scaffold/add-vhost.sh
│   ├── scaffold/gen-env-platform.sh
│   ├── scaffold/remove-project.sh
│   ├── scaffold/adopt-project.sh
│   └── scaffold/project-list.sh
│   ├── notify/notify-hook.sh
│   ├── catalog/generate-catalog.sh
│   ├── provision-environment.sh
│   ├── secrets/decrypt-secrets.sh
│   ├── validate/validate.sh
│   └── audit/audit.sh
├── lib/                        # Библиотеки (logging, healthcheck, node-resolver, paths)
├── modules/{module}/           # Docker-сервисы
│   ├── module.yaml, docker-compose.base.yml
│   ├── */healthcheck.sh       → source ../../lib/healthcheck.sh
│   ├── Makefile              → include ../../templates/module.mk
│   ├── .dockerignore         → symlink ../../templates/.dockerignore
│   └── {build,context}/      # (только hermes-agent) Dockerfile L1/L2
├── templates/                  # module.mk, sudo-whitelist.template, template-manifest.yaml, .dockerignore
├── entrypoint-manifest.yaml
└── VERSION
```

### Cross-layer import rules

| Слой | Может импортировать | Запрещено |
|------|--------------------|-----------|
| `entrypoints/` | `internal/`, `lib/` | Всё остальное |
| `internal/` | `internal/`, `lib/`, `modules/` (через `invoke_module_interface` + `interfaces`) | Прямые вызовы `modules/` без регистрации |
| `modules/` | `lib/`, `templates/` | `internal/` |

**Typed contract:** `internal/` вызывает `modules/` **только** через `invoke_module_interface()` из `core/lib/module-interface.sh`. Модуль должен регистрировать интерфейсы в `module.yaml#interfaces`. Gate #8 v2 валидирует оба условия — прямой вызов `bash modules/<name>/...` без `invoke_module_interface` = violation, вызов незарегистрированного интерфейса = violation.

**DataFlow enhancements (Gate #8 v3):** Детекция расширена тремя механизмами:
1. **Extended Variable Registry** — авто-сбор переменных из `core/lib/paths.sh` (`_collect_path_variables`), подстановка в `resolve_import` вместо 9 хардкоженных переменных
2. **Local Variable Tracking** — `_trace_variable_assignment` отслеживает локальные присвоения переменных (`local VAR=path`, `export VAR=path`, `readonly VAR=path`) в пределах одного файла
3. **ShellCheck Data-Flow Analysis** — слой B детекции через SC2154 (переменная «referenced but not assigned»), обнаруживает вызовы `bash "$variable"` где переменная присвоена из path-литерала в другом скоупе. Graceful degradation: при отсутствии ShellCheck слой B отключается без ошибки.

---

## Удалённые / устаревшие скрипты

Следующие скрипты удалены из проекта или помечены к удалению в Фазе 2:

| Файл | Статус | Причина |
|------|--------|---------|
| dev.sh | 💀 УДАЛЁН | Функционал — в Makefile |
| bare-metal-reset.sh | 💀 УДАЛЁН | Сирота, 0 живых вызывателей |
| prepare-bare-server.sh | 💀 УДАЛЁН | Сирота, самоссылающийся кластер |
| stage-manager.sh | 💀 УДАЛЁН | Сирота |
| image-prewarm.sh | 💀 УДАЛЁН | Сирота |
| e2e/setup-vps.sh | 💀 УДАЛЁН | Сирота |
| platform-push.sh | 💀 УДАЛЁН | Rsync-логика → CI-воркфлоу core-deploy.yml |
| apply.sh | 💀 УДАЛЁН | Деплой → make deploy / make context-promote |
| `docker-healthcheck.sh` | ✅ LIVE: core/internal/healthcheck/docker-healthcheck.sh — вызывается crontab'ом каждую минуту |
| `gate-loop SKILL.md` | 💀 УДАЛЁН | Заменён на make gate → make context-promote |

---

## Forbidden

### Запрещённые директории

Директории, в которых **НЕ ДОЛЖНЫ** находиться исполняемые скрипты:

- `core/scripts/e2e/` — удалена
- `core/scripts/` — legacy, заменена на `core/entrypoints/` + `core/internal/`

### Запрещённые скрипты (имена)

Следующие имена скриптов не должны существовать нигде в проекте:

- dev.sh
- platform-push.sh
- bare-metal-reset.sh
- prepare-bare-server.sh
- stage-manager.sh
- image-prewarm.sh
- apply.sh

### Запрещённые глаголы (make-таргеты)

Следующие глаголы **ЗАПРЕЩЕНЫ** к использованию в качестве имён таргетов:

- `push-core`
- `deploy-node`
- `build-local`
- `bootstrap-core`
- `hermes-deploy-vps`

### Разрешённые глаголы

Полный список разрешённых глаголов см. в таблице «Канонические операции» выше и в [`entrypoint-manifest.yaml`](entrypoint-manifest.yaml) (`allowed_verbs`).

---

## Архитектурные инварианты

10 архитектурных инвариантов платформы определены **только** в [`AGENTS.md`](../AGENTS.md#region-MODULE_CONTRACT) (root). Настоящий файл не дублирует их — все расхождения между копиями устранены в пользу root как единственного Source of Truth.

**Правило:** если новый инвариант затрагивает общую архитектуру платформы — добавляй в root AGENTS.md. В core/AGENTS.md описываются только core-специфичные контракты (операции, структура, forbidden-списки).

---

## Навигация

| Файл | Назначение |
|------|-----------|
| [`AGENTS.md`](../AGENTS.md) (root) | **Единственный Source of Truth** — архитектурные инварианты, модель деплоя, глоссарий глаголов |
| [`modules/AGENTS.md`](modules/AGENTS.md) | Шаблон модуля, контракты |
| [`entrypoint-manifest.yaml`](entrypoint-manifest.yaml) | Машиночитаемый YAML-реестр |
| [`templates/`](templates/) | Параметризованные шаблоны |
- [Root AGENTS.md — языковая политика](../AGENTS.md#языковая-политика) — Python-only new code, двухуровневый Strangler-триггер
