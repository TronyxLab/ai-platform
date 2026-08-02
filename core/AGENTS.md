<!-- GREP_SUMMARY: AGENTS.md, core, operations-catalog, canonical-targets, layer-structure, verb-dictionary, forbidden -->

# GREP_SUMMARY: AGENTS.md, core, operations-catalog, canonical-targets, layer-structure, verb-dictionary, forbidden
# STRUCTURE: ┌canonical operations table┐ → ◇ core/ dir structure → ◇ cross-layer import rules → ◇ forbidden lists → ⎋ navigation refs
# region MODULE_CONTRACT
## @purpose  Catalog of canonical make targets, core/ directory structure, cross-layer import rules, and forbidden scripts/verbs for the ai-platform core
## @scope    All operations that pass through Makefile; layer isolation rules; deleted/forbidden script inventory. Source of truth for forbidden scripts: core/entrypoint-manifest.yaml §forbidden_scripts
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
| `make bootstrap-node` | Идемпотентный bootstrap ноды | make bootstrap-node NODE=\<name\> | core/entrypoints/bootstrap.sh → core/internal/bootstrap/preflight.py → core/internal/bootstrap/node-lifecycle.sh --mode init → core/internal/bootstrap/lifecycle/cli.py → core/internal/bootstrap/lifecycle/state_machine.py (B9 state machine — 14 фаз — 9 INIT — φ1 system_bootstrap → core/internal/bootstrap/install-docker.sh → core/internal/bootstrap/install-tor-proxy.sh → core/internal/bootstrap/firewall.sh · φ2 user_accounts · φ3 platform_setup → core/internal/bootstrap/docker_registry_auth.py → core/internal/bootstrap/setup-node.sh · φ4 secrets_provision · φ5 node_configuration · φ6 registry_auth · φ7 certificates → core/internal/bootstrap/install-acme.sh · φ8 deploy_services → core/internal/bootstrap/deploy-modules.sh → core/internal/bootstrap/cert_orchestrator.py → core/internal/bootstrap/deploy/context_deployer.py · φ8.5 converge_services — 5 UPDATE — φ9 secrets_update · φ10 node_config_update · φ11 registry_update · φ12 deploy_update · φ13 converge_update) |
| `make deploy-context` | Деплой проектов контекста на ноде | make deploy-context NODE=\<n\> [CONTEXT=\<ctx\>] | core/entrypoints/deploy-context.sh → core/internal/bootstrap/deploy/context_deployer.py |
| `make deploy` | Деплой проекта | make deploy PROJECT=\<dir\> | git push → CI → .github/workflows/deploy-project.yml (receive verb) → orchestrator_cli dispatch receive → core/internal/deploy/orchestrator.py DeployOrchestrator.receive() → core/internal/notify/notify-hook.sh + core/internal/catalog/generate-catalog.sh (post-deploy, D4) → legacy-local-entrypoint → core/entrypoints/deploy.sh (DevPlan 116 B1 T7) |
| `make deploy-project` | Прямой деплой минуя CI (DeployOrchestrator deliver) | make deploy-project PROJECT=\<dir\> NODE=\<node\> | core/internal/deploy/orchestrator_cli.py deliver (ForcedCommandChannel receive \<project\> \<version\>) → orchestrator_cli dispatch receive → DeployOrchestrator.receive() |
| `make context-promote` | Промоут платформы в контекст | make context-promote CONTEXT=\<context\> | core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py |
| `make hermes-build-platform` | Сборка L1 образа | make hermes-build-platform | core/entrypoints/build.sh → core/internal/build/hermes-images.sh build-platform |
| `make hermes-build-context` | Сборка L1→L2 образа | make hermes-build-context CONTEXT=\<context\> | core/entrypoints/build.sh → core/internal/build/hermes-images.sh build-context |
| `make hermes-push-l1` | Push L1 в ghcr.io | make hermes-push-l1 | docker tag + docker push to ghcr.io |
| `make hermes-push-l2` | Push L2 в ghcr.io | make hermes-push-l2 CONTEXT=\<org\> | docker tag + docker push to ghcr.io |
| `make templates-check` | Dry-run проверка шаблонов | make templates-check | core/internal/template_engine.py check --verbose |
| `make templates-render` | Рендер шаблонов | make templates-render | core/internal/template_engine.py render-all |
| `make validate-modules` | Валидация module.yaml | make validate-modules | core/internal/scripts/validate_module_yaml.py --all |
| `make validate` | Schema-валидация | make validate [FILES=...] | core/entrypoints/validate.sh → core/internal/validate/validate.sh → core/internal/validate/validate_orchestrator.py |
| `make lint` | Линтинг | make lint | core/entrypoints/validate.sh --lint → core/internal/validate/validate.sh → core/internal/validate/validate_orchestrator.py |
| `make check-file-lines` | Проверка длины файлов | make check-file-lines [MAX_LINES=500] | core/entrypoints/check-file-lines.sh |
| `make scripts-audit` | Аудит регистрации скриптов | make scripts-audit | core/internal/scripts-audit.sh |
| `make check-dead-code` | Проверка мёртвого кода | make check-dead-code | core/entrypoints/check-dead-code.sh |
| `make doxygen-check` | Doxygen zero-warnings проверка | make doxygen-check | doxygen Doxyfile (zero-warnings invariant, DevPlan 097) |
| `make test` | Запуск тестов | make test [MARKER=...] | make test [MARKER=static|smoke|component|integration|predeploy|contract|e2e|all] |
| `make test-summary` | Запуск тестов (агент-ориентированная обёртка) | make test-summary [MARKER=static_audit|smoke|component|integration|predeploy|contract|e2e|static|all] [TIMEOUT=1800] | core/internal/test_runner.py --marker \<MARKER\> |
| `make test-inventory-sync` | Синхронизация test inventory | make test-inventory-sync | tests/tools/sync_inventory.py |
| `make test-node` | E2E pipeline тесты на test-VPS | make test-node NODE=\<name\> | pytest tests/e2e/ -m requires_node |
| `make gate` | Production gate | make gate [MODE=fast|full] | make gate [MODE=fast|full] |
| `make check-manifests` | Проверка актуальности сгенерированных манифестов | make check-manifests | --check for all 6 generators (G1-G6) — byte-level comparison |
| `make generate-manifests` | Генерация всех манифестов | make generate-manifests | make generate-manifests |
| `make generate-secrets-manifest` | Генерация secrets-manifest.yaml | make generate-secrets-manifest | python3 core/internal/scripts/generate_secrets_manifest.py |
| `make generate-platform-env` | Генерация platform-env.yaml + Python env files | make generate-platform-env | python3 core/internal/scripts/generate_platform_env.py |
| `make generate-env-example` | Генерация .env.example | make generate-env-example | python3 core/internal/scripts/sync_env_defaults.py → .env.example |
| `make generate-entrypoint-manifest` | Генерация entrypoint-manifest.yaml | make generate-entrypoint-manifest | python3 core/internal/scripts/generate_entrypoint_manifest.py |
| `make generate-agents-md` | Генерация core/AGENTS.md | make generate-agents-md | python3 core/internal/scripts/generate_agents_md.py → core/AGENTS.md |
| `make generate-litellm-config` | Генерация litellm-config.yml | make generate-litellm-config | python3 core/internal/llm/config_renderer.py → litellm-config.yml |
| `make generate-manifests-atomic` | Атомарная генерация всех манифестов | make generate-manifests-atomic | mktemp → Chain A+B+C → mv |
| `make sync-env-defaults` | Генерация .env.example из SoT | make sync-env-defaults | core/internal/scripts/sync_env_defaults.py → .env.example |
| `make check-env-defaults` | Проверка актуальности .env.example | make check-env-defaults | core/internal/scripts/sync_env_defaults.py --check |
| `make new-project` | Создание проекта из шаблона | make new-project NAME=\<n\> TEMPLATE=\<t\> | core/entrypoints/scaffold.sh → core/internal/scaffold/add-project.sh → core/internal/scaffold/add-vhost.sh |
| `make new-context` | Создание контекста деплоя | make new-context NODE=\<n\> | core/entrypoints/scaffold.sh → core/internal/scaffold/context-init.sh |
| `make project-sync-env` | Синхронизация .env.platform | make project-sync-env [NAME=\<name\>] | core/entrypoints/scaffold.sh → core/internal/scaffold/gen_env_platform.py |
| `make remove-project` | Удаление проекта из lifecycle | make remove-project NAME=\<name\> | core/entrypoints/scaffold.sh → core/internal/scaffold/remove-project.sh |
| `make adopt-project` | Адаптация существующего проекта | make adopt-project DIR=\<dir\> | core/entrypoints/scaffold.sh → core/internal/scaffold/adopt-project.sh → core/internal/scaffold/gen_env_platform.py |
| `make project-list` | Список проектов | make project-list [NODE=\<node\>] | core/entrypoints/scaffold.sh → core/internal/scaffold/project-list.sh |
| `make project-status` | Статус проекта | make project-status NAME=\<name\> | core/entrypoints/scaffold.sh → core/internal/scaffold/project-list.sh --status |
| `make render-vhosts` | Генерация vhost конфигов | make render-vhosts NODE=\<name\> | core/internal/scaffold/add-vhost.sh --render-all --node \<n\> |
| `make secrets-unlock` | Расшифровка секретов | make secrets-unlock [NODE=...] | core/entrypoints/secrets.sh → core/internal/secrets/decrypt-secrets.sh |
| `make up-safe` | Безопасный compose up | make up-safe [MODULES=...] | core/entrypoints/compose-wrapper.sh → core/internal/bootstrap/deploy/compose_preflight.py → docker compose up |
| `make compose-safe-up` | Deprecated alias for up-safe | make compose-safe-up (deprecated — use up-safe) | up-safe (deprecated alias) |
| `make converge` | Реконсиляция ноды | make converge NODE=\<name\> | core/entrypoints/converge.sh → core/internal/bootstrap/converge.sh |
| `make healthcheck` | Проверка здоровья | make healthcheck [NODE=...] | core/entrypoints/healthcheck.sh → Module healthcheck.sh scripts + core/internal/healthcheck/tor-proxy-healthcheck.sh |
| `make up` | Запуск compose-стека | make up [PROJECT=...] | core/internal/provision-environment.sh → docker compose up |
| `make down` | Остановка compose-стека | make down | docker compose down |
| `make restart` | Мягкий перезапуск compose-стека | make restart | docker compose stop && docker compose start |
| `make status` | Статус compose-стека | make status | docker compose ps |
| `make backup` | Резервное копирование | make backup [NODE=...] | Module backup scripts |
| `make restore` | Восстановление из бэкапа | make restore NODE=\<n\> | Module restore scripts |
| `make node-update` | Обновление provisioned ноды | make node-update NODE=\<name\> | core/entrypoints/node-update.sh → core/internal/bootstrap/node-lifecycle.sh --mode update → core/internal/bootstrap/lifecycle/cli.py → core/internal/bootstrap/lifecycle/state_machine.py (B9 state machine — UPDATE mode — 5 фаз — φ9 secrets_update · φ10 node_config_update · φ11 registry_update → provision · llm-keys · healthcheck · φ12 deploy_update → core/internal/bootstrap/issue-cert.sh → core/internal/bootstrap/deploy-modules.sh · φ13 converge_update) |
| `make verify` | HTTPS-верификация | make verify NODE=\<node\> | core/entrypoints/verify.sh → core/internal/verify/verify-domains.sh |
| `make provision` | Provision окружения | make provision [SCOPE=...] | core/internal/provision-environment.sh → core/internal/provisioner.py |
| `make provision-llm` | Provision LiteLLM virtual keys | make provision-llm | core/entrypoints/provision-llm.sh → core/internal/llm/key_provisioner.py |
| `make discover-modules` | Авто-обнаружение модулей | make discover-modules | core/internal/bootstrap/discover_modules.py |
| `make dev-certs` | Генерация dev SSL-сертификатов | make dev-certs [CERT_BACKEND=...] | core/modules/nginx/dev_cert_generator.py |
| `make _get_all_profiles` | Вывод COMPOSE_PROFILES | make _get_all_profiles | echo |
| `make render-monitoring` | Рендер конфигурации мониторинга после деплоя проекта | make render-monitoring PROJECT_DIR=\<dir\> PROJECT=\<name\> [NODE=\<node\>] | python3 core/internal/monitoring_config_renderer.py |
| `make fix-executable-bit` | Исправление executable bit на .sh файлах | make fix-executable-bit [DRY_RUN=1] | git add --chmod=+x + git update-index --chmod=+x |
| `make fix-ruff` | Форматирование Python файлов через ruff | make fix-ruff [SCOPE=diff|staged|all] [DRY_RUN=1] | ruff check --fix + ruff format |
| `make fix-gate` | Композитное исправление gate-ошибок | make fix-gate [DRY_RUN=1] | fix-executable-bit + fix-ruff + generate-manifests |
| `make preflight` | Параллельный preflight (сбор всех ошибок gate за один проход) | make preflight [WORKERS=6] [JSON=1] [SKIP_FIX=1] [VERBOSE=1] | python3 -m core.internal.preflight [--skip-fix] [--json] [--workers N] |
| `make check-profiles-parity` | Parity-гейт COMPOSE_PROFILES (единый SoT platform-infra.yaml) | make check-profiles-parity | pytest tests/gates/test_gate_profiles_parity.py (COMPOSE_PROFILES SoT parity, DevPlan 116 T9) |
| `make check-domain-parity` | Parity-гейт PLATFORM_DOMAIN (единое определение, 0 legacy-доменов) | make check-domain-parity | pytest tests/gates/test_gate_domain_parity.py (PLATFORM_DOMAIN SoT parity, DevPlan 116 T9) |
| `make templates-check` | Проверка покрытия и разрешимости шаблонов | make templates-check | core/internal/template_engine.py check (template-manifest coverage) |
<!-- GENERATED:END:canon_table -->

---

## Структура core/

<!-- Directory structure reflects the actual filesystem. See core/ files. -->

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

11 архитектурных инвариантов платформы определены **только** в `AGENTS.md` (root) — `#region MODULE_CONTRACT`. Настоящий файл не дублирует их — все расхождения между копиями устранены в пользу root как единственного Source of Truth.

**Правило:** если новый инвариант затрагивает общую архитектуру платформы — добавляй в root AGENTS.md. В core/AGENTS.md описываются только core-специфичные контракты (операции, структура, forbidden-списки).

---

## Exit-коды (контракт)

Единый контракт exit-кодов на весь core (DevPlan 116 B4, U-29). Машиночитаемые константы — `core/internal/shared/contracts.py`; runtime-классы исключений — `core/internal/shared/exceptions.py`.

| Код | Семантика | Исключение |
|-----|-----------|------------|
| 0 | ok | — |
| 1 | generic error | PlatformError base |
| 2 | ConfigNotFound | ConfigNotFoundError (файл можно создать) |
| 3 | ConfigParse | ConfigParseError (синтаксис YAML/JSON) |
| 4 | ConfigValidation | ConfigValidationError (структура) |
| 10 | Fatal — ручное вмешательство | PlatformFatalError |

**Инвариант main()-контракта:** business-функции НЕ вызывают `sys.exit`; `sys.exit` — только в `main()` / `if __name__ == "__main__":`. Все `main()` в core/internal имеют сигнатуру `def main() -> int` и паттерн `except PlatformError as e: return e.exit_code`.

---

## Навигация

| Файл | Назначение |
|------|-----------|
| `AGENTS.md` (root) | **Единственный Source of Truth** — архитектурные инварианты, модель деплоя, глоссарий глаголов |
| [`modules/AGENTS.md`](modules/AGENTS.md) | Шаблон модуля, контракты |
| [`entrypoint-manifest.yaml`](entrypoint-manifest.yaml) | Машиночитаемый YAML-реестр |
| [`templates/`](templates/) | Параметризованные шаблоны |
- Root AGENTS.md — языковая политика — Python-only new code, двухуровневый Strangler-триггер

---

## New shared modules (DevPlan 086)

| Module | Path | Purpose |
|--------|------|---------|
| `contracts` | `core/internal/shared/contracts.py` | Контракт операционных политик — DEPLOY_BEST_EFFORT (legacy parity) + константы exit-кодов (0/1/2/3/4/10). Единый machine-readable источник для гейтов и CLI (DevPlan 116 B4 T1, U-39). |
| `secrets_env_parser` | `core/internal/shared/secrets_env_parser.py` | Единый парсер secrets.env — parse()/write()/merge()/export_shell(). Заменяет 7 inline-парсеров. |
| `schema_validator` | `core/internal/shared/schema_validator.py` | Единый schema-валидатор YAML↔JSON-Schema (draft-07) — validate_yaml_against_schema()/validate_dict_against_schema(). Единственная Draft7Validator-точка (DevPlan 116 B6 T5). |
| `ssh_opts` | `core/internal/shared/ssh_opts.py` | Единый SoT SSH-флагов — SSH_OPTS/build_rsync_ssh_opts()/CLI --shell. Заменяет 5 Python-копий + lib/ssh.sh фасад (DevPlan 116 B5 T2, D1). |
| `telegram_notifier` | `core/internal/shared/telegram_notifier.py` | Единый Telegram-клиент — send_telegram(). Заменяет 6 независимых реализаций (3 shell + 3 Python). |
| `timeouts` | `core/internal/shared/timeouts.py` | Единый реестр таймаутов операционных политик — COMPOSE_UP_TIMEOUT/PULL_TIMEOUT/SSH_CONNECT_TIMEOUT/... Единственный источник числовых timeout= в docker/ssh/healthcheck-домене (DevPlan 116 B5 T1, U-11). |
| `docker_auth` | `core/internal/shared/docker_auth.py` | Единый Docker registry auth — docker_login()/ghcr_login()/configure_docker_auth(). Заменяет 5 дублирующихся точек. |
| `verbs` | `core/internal/shared/verbs.py` | Канонический verb-словарь forced-command диспетчера (DevPlan 116 B1 T1, U-56) — CANONICAL_VERBS + reserve-имена для проектов. Потребители: ssh_command_parser, project_registry, gate канала. |
| `compose_files` | `core/internal/shared/compose_files.py` | Единый SoT списков compose-файлов и резолва — COMPOSE_FILENAMES/PROJECT_COMPOSE_FILENAMES/resolve_compose_file()/requires_compose_project(). Заменяет 6 локальных кортежей (docker_orchestrator, converge×2, orphan_reconciler, payload_deliverer, project_adopter, DevPlan 118 A2). |
