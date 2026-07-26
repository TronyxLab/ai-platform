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

<!-- GENERATED:START:canon_table -->
| `make bootstrap-node` | Идемпотентный bootstrap ноды | make bootstrap-node NODE=<name> | core/entrypoints/bootstrap.sh → core/internal/bootstrap/preflight.py → core/internal/bootstrap/node-lifecycle.sh --mode init → core/internal/bootstrap/docker_registry_auth.py + core/internal/bootstrap/firewall.sh + core/internal/bootstrap/install-docker.sh + core/internal/bootstrap/install-tor-proxy.sh + core/internal/bootstrap/setup-node.sh + core/internal/bootstrap/install-acme.sh + core/internal/bootstrap/secrets-init.sh + core/internal/bootstrap/deploy-modules.sh + core/internal/bootstrap/cert_orchestrator.py + core/internal/bootstrap/deploy/context_deployer.py |
| `make deploy-context` | Деплой проектов контекста на ноде | make deploy-context NODE=<n> [CONTEXT=<ctx>] | core/entrypoints/deploy-context.sh → core/internal/bootstrap/deploy/context_deployer.py |
| `make deploy` | Деплой проекта | make deploy PROJECT=<dir> | git push → CI → core/entrypoints/deploy.sh (VPS forced-command) → core/internal/deploy/deploy-project.sh → core/internal/notify/notify-hook.sh + core/internal/catalog/generate-catalog.sh |
| `make deploy-project` | Прямой деплой минуя CI | make deploy-project PROJECT=<dir> NODE=<node> | core/entrypoints/deploy-project.sh → ssh platform-deliver + ssh deploy.sh → core/internal/deploy/deploy-project.sh |
| `make context-promote` | Промоут платформы в контекст | make context-promote CONTEXT=<context> | core/entrypoints/context-promote.sh → copy to <context>/ai-platform → CI |
| `make hermes-build-platform` | Сборка L1 образа | make hermes-build-platform | core/entrypoints/build.sh → core/internal/build/hermes-images.sh build-platform |
| `make hermes-build-context` | Сборка L1→L2 образа | make hermes-build-context CONTEXT=<context> | core/entrypoints/build.sh → core/internal/build/hermes-images.sh build-context |
| `make hermes-push-l1` | Push L1 в ghcr.io | make hermes-push-l1 | docker tag + docker push to ghcr.io |
| `make hermes-push-l2` | Push L2 в ghcr.io | make hermes-push-l2 CONTEXT=<org> | docker tag + docker push to ghcr.io |
| `make templates-check` | Dry-run проверка шаблонов | make templates-check | core/internal/template-engine.sh check --verbose |
| `make templates-render` | Рендер шаблонов | make templates-render | core/internal/template-engine.sh render-all |
| `make validate-modules` | Валидация module.yaml | make validate-modules | core/internal/scripts/validate_module_yaml.py --all |
| `make validate` | Schema-валидация | make validate [FILES=...] | core/entrypoints/validate.sh → core/internal/validate/validate.sh |
| `make lint` | Линтинг | make lint | core/entrypoints/validate.sh --lint → core/internal/validate/validate.sh |
| `make audit` | Системный аудит | make audit [NODE=...] | core/entrypoints/audit.sh → core/internal/audit/audit.sh |
| `make check-file-lines` | Проверка длины файлов | make check-file-lines [MAX_LINES=500] | core/entrypoints/check-file-lines.sh |
| `make scripts-audit` | Аудит регистрации скриптов | make scripts-audit | core/internal/scripts-audit.sh |
| `make check-dead-code` | Проверка мёртвого кода | make check-dead-code | core/entrypoints/check-dead-code.sh |
| `make test` | Запуск тестов | make test [MARKER=...] | make test [MARKER=static|smoke|component|integration|predeploy|contract|e2e|all] |
| `make test-inventory-sync` | Синхронизация test inventory | make test-inventory-sync | tests/tools/sync_inventory.py |
| `make gate` | Production gate | make gate [MODE=fast|full] | make gate [MODE=fast|full] |
| `make check-manifests` | Проверка актуальности сгенерированных манифестов | make check-manifests | git diff --exit-code |
| `make generate-manifests` | Генерация всех манифестов | make generate-manifests | make generate-manifests |
| `make sync-env-defaults` | Генерация .env.example из SoT | make sync-env-defaults | core/internal/scripts/sync_env_defaults.py → .env.example |
| `make check-env-defaults` | Проверка актуальности .env.example | make check-env-defaults | core/internal/scripts/sync_env_defaults.py --check |
| `make new-project` | Создание проекта из шаблона | make new-project NAME=<n> TEMPLATE=<t> | core/entrypoints/scaffold.sh → core/internal/scaffold/add-project.sh → core/internal/scaffold/add-vhost.sh |
| `make new-context` | Создание контекста деплоя | make new-context NODE=<n> | core/entrypoints/scaffold.sh → core/internal/scaffold/context-init.sh |
| `make project-sync-env` | Синхронизация .env.platform | make project-sync-env [NAME=<name>] | core/entrypoints/scaffold.sh → core/internal/scaffold/gen-env-platform.sh |
| `make remove-project` | Удаление проекта из lifecycle | make remove-project NAME=<name> | core/entrypoints/scaffold.sh → core/internal/scaffold/remove-project.sh |
| `make adopt-project` | Адаптация существующего проекта | make adopt-project DIR=<dir> | core/entrypoints/scaffold.sh → core/internal/scaffold/adopt-project.sh → core/internal/scaffold/gen-env-platform.sh |
| `make project-list` | Список проектов | make project-list [NODE=<node>] | core/entrypoints/scaffold.sh → core/internal/scaffold/project-list.sh |
| `make project-status` | Статус проекта | make project-status NAME=<name> | core/entrypoints/scaffold.sh → core/internal/scaffold/project-list.sh --status |
| `make render-vhosts` | Генерация vhost конфигов | make render-vhosts NODE=<name> | core/internal/scaffold/add-vhost.sh --render-all --node <n> |
| `make secrets-unlock` | Расшифровка секретов | make secrets-unlock [NODE=...] | core/entrypoints/secrets.sh → core/internal/secrets/decrypt-secrets.sh |
| `make up-safe` | Безопасный compose up | make up-safe [MODULES=...] | core/entrypoints/compose-wrapper.sh → core/internal/bootstrap/deploy/compose_preflight.py → docker compose up |
| `make compose-safe-up` | Deprecated alias for up-safe | make compose-safe-up (deprecated — use up-safe) | up-safe (deprecated alias) |
| `make converge` | Реконсиляция ноды | make converge NODE=<name> | core/entrypoints/converge.sh → core/internal/bootstrap/converge.sh |
| `make healthcheck` | Проверка здоровья | make healthcheck [NODE=...] | core/entrypoints/healthcheck.sh → Module healthcheck.sh scripts + core/internal/healthcheck/tor-proxy-healthcheck.sh |
| `make up` | Запуск compose-стека | make up [PROJECT=...] | core/internal/provision-environment.sh → docker compose up |
| `make down` | Остановка compose-стека | make down | docker compose down |
| `make restart` | Мягкий перезапуск compose-стека | make restart | docker compose stop && docker compose start |
| `make status` | Статус compose-стека | make status | docker compose ps |
| `make backup` | Резервное копирование | make backup [NODE=...] | Module backup scripts |
| `make restore` | Восстановление из бэкапа | make restore NODE=<n> | Module restore scripts |
| `make node-update` | Обновление provisioned ноды | make node-update NODE=<name> | core/entrypoints/node-update.sh → core/internal/bootstrap/node-lifecycle.sh --mode update → core/internal/bootstrap/issue-cert.sh + provision + deploy-modules + healthcheck |
| `make verify` | HTTPS-верификация | make verify NODE=<node> | core/entrypoints/verify.sh → core/internal/verify/verify-domains.sh |
| `make provision` | Provision окружения | make provision [SCOPE=...] | core/internal/provision-environment.sh → core/internal/provisioner.py |
| `make provision-llm` | Provision LiteLLM virtual keys | make provision-llm | core/entrypoints/provision-llm.sh → core/internal/llm/key_provisioner.py |
| `make discover-modules` | Авто-обнаружение модулей | make discover-modules | core/internal/bootstrap/discover_modules.py |
| `make dev-certs` | Генерация dev SSL-сертификатов | make dev-certs [CERT_BACKEND=...] | core/modules/nginx/generate-dev-certs.sh |
| `make _get_all_profiles` | Вывод COMPOSE_PROFILES | make _get_all_profiles | echo |
| `make fix-executable-bit` | Исправление executable bit на .sh файлах | make fix-executable-bit [DRY_RUN=1] | git add --chmod=+x + git update-index --chmod=+x |
| `make fix-ruff` | Форматирование Python файлов через ruff | make fix-ruff [SCOPE=diff|staged|all] [DRY_RUN=1] | ruff check --fix + ruff format |
| `make fix-gate` | Композитное исправление gate-ошибок | make fix-gate [DRY_RUN=1] | fix-executable-bit + fix-ruff + generate-manifests |
<!-- GENERATED:END:canon_table -->

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
│   ├── check_commit_msg.py
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

<!-- GENERATED:START:canon_table-forbidden -->
### Запрещённые директории

Директории, в которых **НЕ ДОЛЖНЫ** находиться исполняемые скрипты:

- `core/scripts/e2e`
- `core/scripts`

### Запрещённые скрипты (имена)

Следующие имена скриптов не должны существовать нигде в проекте:

- dev.sh
- platform-push.sh
- apply.sh
- bare-metal-reset.sh
- prepare-bare-server.sh
- stage-manager.sh
- image-prewarm.sh

### Запрещённые глаголы (make-таргеты)

Следующие глаголы **ЗАПРЕЩЕНЫ** к использованию в качестве имён таргетов:

- `push-core`
- `deploy-node`
- `build-local`
- `bootstrap-core`
- `hermes-deploy-vps`
<!-- GENERATED:END:canon_table-forbidden -->

### Разрешённые глаголы

Полный список разрешённых глаголов см. в таблице «Канонические операции» выше и в [`entrypoint-manifest.yaml`](entrypoint-manifest.yaml) (`allowed_verbs`).

---

## Архитектурные инварианты

11 архитектурных инвариантов платформы определены **только** в [`AGENTS.md`](../AGENTS.md#region-MODULE_CONTRACT) (root). Настоящий файл не дублирует их — все расхождения между копиями устранены в пользу root как единственного Source of Truth.

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
