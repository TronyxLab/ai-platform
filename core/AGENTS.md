<!-- GREP_SUMMARY: AGENTS.md, core, operations-catalog, canonical-targets, layer-structure, verb-dictionary -->

# GREP_SUMMARY: AGENTS.md, core, operations-catalog, canonical-targets, layer-structure, verb-dictionary
# STRUCTURE: ┌canonical operations table┐ → ◇ core/ dir structure → ◇ cross-layer import rules → ⎋ navigation refs
# region MODULE_CONTRACT
## @purpose  Catalog of canonical make targets, core/ directory structure, cross-layer import rules for the ai-platform core
## @scope    All operations that pass through Makefile; layer isolation rules.
## @invariants
##   - Every Makefile .PHONY target maps to a row in the canonical operations table
##   - Entrypoints only call internal/ or lib/ — never modules/
##   - Таргет вне глоссария (allowed_verbs) = запрещён — namelint
## @rationale Machine-readable operations catalog enables CI gates to validate Makefile/AGENTS.md/filesystem triad
# endregion MODULE_CONTRACT

# AGENTS.md — core/

---

## Канонические операции

<!-- GENERATED:START:canon_table -->
| `make bootstrap-node` | Идемпотентный bootstrap ноды | make bootstrap-node NODE=\<name\> | core/entrypoints/bootstrap.sh → core/internal/bootstrap/preflight.py → core/internal/bootstrap/node-lifecycle.sh --mode init → core/internal/bootstrap/lifecycle/cli.py → core/internal/bootstrap/lifecycle/state_machine.py (B9 state machine — 14 фаз — 9 INIT — φ1 system_bootstrap → core/internal/bootstrap/install-docker.sh → core/internal/bootstrap/install-tor-proxy.sh → core/internal/bootstrap/firewall.sh · φ2 user_accounts · φ3 platform_setup → core/internal/bootstrap/docker_registry_auth.py → core/internal/bootstrap/setup-node.sh · φ4 secrets_provision · φ5 node_configuration · φ6 registry_auth · φ7 certificates → core/internal/bootstrap/install-acme.sh · φ8 deploy_services → core/internal/bootstrap/deploy-modules.sh → core/internal/bootstrap/cert_orchestrator.py → core/internal/bootstrap/deploy/context_deployer.py · φ8.5 converge_services — 5 UPDATE — φ9 secrets_update · φ10 node_config_update · φ11 registry_update · φ12 deploy_update · φ13 converge_update) |
| `make deploy-context` | Деплой проектов контекста на ноде | make deploy-context NODE=\<n\> [CONTEXT=\<ctx\>] | core/entrypoints/deploy-context.sh → core/internal/bootstrap/deploy/deploy_context_cli.py → core/internal/bootstrap/deploy/context_deployer.py |
| `make core-deliver` | DR-канал локального оператора (тот же core_deliverer.py) | make core-deliver NODE=\<n\> [AGE_SECRET_KEY_FILE=\<f\>] [DRY_RUN=1] | core/entrypoints/core-deliver.sh → rsync core/+scripts/+makefiles/+platform-env.yaml → ssh make provision SCOPE=networks,volumes → ssh make node-update NODE=\<n\> |
| `make deploy` | Деплой проекта | make deploy PROJECT=\<dir\> [NODE=\<node\>] [LAUNCH=1] | git push → CI → .github/workflows/deploy-project.yml (receive verb) → orchestrator_cli dispatch receive → core/internal/deploy/orchestrator.py DeployOrchestrator.receive() → core/internal/notify/notify-hook.sh + core/internal/catalog/generate-catalog.sh (post-deploy, D4) |
| `make deploy-project` | Прямой деплой минуя CI (DeployOrchestrator deliver) | make deploy-project PROJECT=\<dir\> NODE=\<node\> | core/internal/deploy/orchestrator_cli.py deliver (ForcedCommandChannel receive \<project\> \<version\>) → orchestrator_cli dispatch receive → DeployOrchestrator.receive() |
| `make context-promote` | Промоут платформы в контекст | make context-promote CONTEXT=\<context\> | core/entrypoints/context-promote.sh → core/internal/deploy/context_promoter.py |
| `make hermes-build-platform` | Сборка L1 образа | make hermes-build-platform | core/entrypoints/build.sh → core/internal/build/hermes_images.py build-platform |
| `make hermes-build-context` | Сборка L1→L2 образа | make hermes-build-context CONTEXT=\<context\> | core/entrypoints/build.sh → core/internal/build/hermes_images.py build-context |
| `make hermes-push-l1` | Push L1 в ghcr.io | make hermes-push-l1 | docker tag + docker push to ghcr.io |
| `make hermes-push-l2` | Push L2 в ghcr.io | make hermes-push-l2 CONTEXT=\<org\> | docker tag + docker push to ghcr.io |
| `make templates-render` | Рендер шаблонов (internal) | make templates-render | core/internal/template_engine.py render-all |
| `make validate-modules` | Валидация module.yaml (internal) | make validate-modules | core/internal/scripts/validate_module_yaml.py --all |
| `make scripts-audit` | Аудит регистрации скриптов | make scripts-audit | core/internal/scripts-audit.sh |
| `make test-node` | E2E pipeline тесты на test-VPS | make test-node NODE=\<name\> | pytest tests/e2e/ -m requires_node |
| `make e2e-verify` | HTTP+TLS sweep-верификация всех endpoints ноды | make e2e-verify NODE=\<name\> [MODE=local|remote] [JSON=1] | python3 -m core.internal.verify_sweep sweep |
| `make gate` | Production gate | make gate [MODE=fast|full|ci-docker] | make gate [MODE=fast|full|ci-docker] |
| `make generate-manifests` | Генерация всех манифестов | make generate-manifests | make generate-manifests |
| `make generate-secrets-manifest` | Генерация secrets-manifest.yaml (internal) | make generate-secrets-manifest | python3 core/internal/scripts/generate_secrets_manifest.py |
| `make generate-platform-env` | Генерация platform-env.yaml + Python env files (internal) | make generate-platform-env | python3 core/internal/scripts/generate_platform_env.py |
| `make generate-env-example` | Генерация .env.example (internal) | make generate-env-example | python3 core/internal/scripts/sync_env_defaults.py → .env.example |
| `make generate-entrypoint-manifest` | Генерация entrypoint-manifest.yaml (internal) | make generate-entrypoint-manifest | python3 core/internal/scripts/generate_entrypoint_manifest.py |
| `make generate-agents-md` | Генерация core/AGENTS.md (internal) | make generate-agents-md | python3 core/internal/scripts/generate_agents_md.py → core/AGENTS.md |
| `make generate-litellm-config` | Генерация litellm-config.yml (internal) | make generate-litellm-config | python3 core/internal/llm/config_renderer.py → litellm-config.yml |
| `make generate-requirements` | Генерация requirements.txt из pyproject.toml (internal) | make generate-requirements | core/internal/scripts/sync_requirements.py → core/requirements.txt |
| `make new-project` | Создание проекта из шаблона | make new-project NAME=\<n\> TEMPLATE=\<t\> | core/entrypoints/scaffold.sh → core/internal/scaffold/add-project.sh → core/internal/scaffold/add-vhost.sh |
| `make new-context` | Создание контекста деплоя | make new-context NODE=\<n\> | core/entrypoints/scaffold.sh → core/internal/scaffold/context-init.sh |
| `make project-sync-env` | Синхронизация .env.platform и AI-PLATFORM.md | make project-sync-env [NAME=\<name\>] [DOMAIN=\<domain\>] [PROJECT_DIR=\<dir\>] | core/entrypoints/scaffold.sh → core/internal/scaffold/gen_env_platform.py → core/internal/scaffold/gen_project_platform_md.py |
| `make remove-project` | Удаление проекта из lifecycle | make remove-project NAME=\<name\> | core/entrypoints/scaffold.sh → core/internal/scaffold/remove-project.sh |
| `make adopt-project` | Адаптация существующего проекта | make adopt-project DIR=\<dir\> | core/entrypoints/scaffold.sh → core/internal/scaffold/adopt-project.sh → core/internal/scaffold/gen_env_platform.py |
| `make agent-check` | L1-статический сигнал агента (DevPlan 163 W-E) | make agent-check [JSON=1] | python3 -m core.internal.agent_check (ruff + advisory SLF/FBT/ARG/C90 + basedpyright + static check --changed + bespoke doc-headers) |
| `make project-list` | Список проектов | make project-list [NODE=\<node\>] | core/entrypoints/scaffold.sh → core/internal/scaffold/project-list.sh |
| `make project-status` | Статус проекта | make project-status NAME=\<name\> | core/entrypoints/scaffold.sh → core/internal/scaffold/project-list.sh --status |
| `make render-vhosts` | Генерация vhost конфигов | make render-vhosts NODE=\<name\> | core/internal/scaffold/add-vhost.sh → core/internal/scaffold/vhost_renderer.py render-all |
| `make project-check` | Проверка практик проекта (K1) | make project-check PROJECT=\<dir\> [LEVEL=\<level\>] | python3 -m core.internal.practices.check_project --project-dir \<p\> [--level \<l\>] [--fix] |
| `make project-sync-practices` | Перегенерация GENERATED-файлов практик до канона | make project-sync-practices PROJECT=\<dir\> | python3 -m core.internal.practices.sync_practices --project-dir \<p\> |
| `make project-set-practices` | Установка уровня практик (baseline|full|auto) | make project-set-practices PROJECT=\<dir\> LEVEL=\<level\> | python3 -m core.internal.practices.set_practices --project-dir \<p\> --level \<l\> |
| `make secrets-unlock` | Расшифровка секретов | make secrets-unlock [NODE=...] | core/entrypoints/secrets.sh → core/internal/secrets/decrypt_secrets.py |
| `make converge` | Реконсиляция ноды | make converge NODE=\<name\> | core/entrypoints/converge.sh → core/internal/bootstrap/converge.sh |
| `make check-security` | Проверка security-постурa ноды | make check-security NODE=\<name\> | core/entrypoints/check-security.sh → core/internal/bootstrap/check_security_cli.py → remote_executor.py execute-check-security → core/internal/bootstrap/security_posture.py (S1-S9, DevPlan 134 L2 + 136 W10) |
| `make healthcheck` | Проверка здоровья | make healthcheck [NODE=...] | core/entrypoints/healthcheck.sh → core/internal/healthcheck/modules_healthcheck.py → Module healthcheck.sh scripts (via shared/module_interface) |
| `make up` | Запуск compose-стека | make up [MODULES=\<comma-list\>] [SKIP_PREFLIGHT=1] | core/internal/bootstrap/deploy/compose_preflight.py → core/internal/provision-environment.sh → docker compose up (MODULES filter) |
| `make down` | Остановка compose-стека | make down | docker compose down |
| `make down-volumes` | Остановка compose-стека и удаление volumes | make down-volumes | docker compose down -v |
| `make restart` | Мягкий перезапуск compose-стека | make restart | docker compose stop && docker compose start |
| `make status` | Статус compose-стека | make status | docker compose ps |
| `make backup` | Резервное копирование | make backup | backup-cron module make backup |
| `make restore` | Восстановление из бэкапа | make restore DUMP_FILE=\<path\> | backup-cron module make restore DUMP_FILE=\<path\> |
| `make node-update` | Обновление provisioned ноды | make node-update NODE=\<name\> | core/entrypoints/node-update.sh → core/internal/bootstrap/node-lifecycle.sh --mode update → core/internal/bootstrap/lifecycle/cli.py → core/internal/bootstrap/lifecycle/state_machine.py (B9 state machine — UPDATE mode — 5 фаз — φ9 secrets_update · φ10 node_config_update · φ11 registry_update → provision · llm-keys · healthcheck · φ12 deploy_update → core/internal/bootstrap/issue_cert.py → core/internal/bootstrap/deploy-modules.sh · φ13 converge_update) |
| `make verify-domains` | HTTPS-верификация доменов | make verify-domains NODE=\<node\> [PROJECT=\<name\>] | core/entrypoints/verify.sh → core/internal/verify/domain_verifier.py |
| `make provision` | Provision окружения | make provision [SCOPE=...] | core/internal/provision-environment.sh → core/internal/provisioner.py |
| `make provision-llm` | Provision LiteLLM virtual keys | make provision-llm | core/entrypoints/provision-llm.sh → core/internal/llm/key_provisioner.py |
| `make discover-modules` | Авто-обнаружение модулей (internal) | make discover-modules | core/internal/bootstrap/discover_modules.py |
| `make dev-certs` | Генерация dev SSL-сертификатов | make dev-certs [CERT_BACKEND=...] | core/modules/nginx/dev_cert_generator.py |
| `make dev-metrics` | Генерация dev status-metrics.json + htpasswd | make dev-metrics | core/internal/healthcheck/platform_export_metrics.py + core/internal/bootstrap/lifecycle/secrets_manager.py (htpasswd CLI) |
| `make dev-hosts` | Управление /etc/hosts dev-блоком | make dev-hosts [APPLY=1] | core/internal/dev_hosts.py |
| `make render-monitoring` | Рендер конфигурации мониторинга после деплоя проекта | make render-monitoring PROJECT_DIR=\<dir\> PROJECT=\<name\> [NODE=\<node\>] | python3 core/internal/monitoring/config_renderer.py |
| `make age-key-backup` | Off-node encrypted backup AGE мастер-ключа (DR, секция «DR мастер-ключа AGE» core/AGENTS.md) | make age-key-backup [AGE_RECIPIENT=\<pubkey\>] [DRY_RUN=1|NO_UPLOAD=1|OUTPUT_ENC=\<path\>|S3_KEY=\<key\>] | python3 -m core.internal.deploy.age_key_backup (node_detect AGE-ключ → sops encrypt --age AGE_RECIPIENT → S3 private ACL → sha256 verify; секция «DR мастер-ключа AGE» core/AGENTS.md) |
| `make fix-executable-bit` | Исправление executable bit на .sh файлах (internal) | make fix-executable-bit [DRY_RUN=1] | git add --chmod=+x + git update-index --chmod=+x |
| `make fix-ruff` | Форматирование Python файлов через ruff (internal) | make fix-ruff [SCOPE=diff|staged|all] [DRY_RUN=1] | ruff check --fix + ruff format |
| `make fix-pycache` | Очистка __pycache__ рабочего дерева | make fix-pycache [DRY_RUN=1] | find core tests -type d -name __pycache__ -exec rm -rf (build-каталоги исключены) |
| `make fix-gate` | Композитное исправление gate-ошибок | make fix-gate [DRY_RUN=1] | fix-executable-bit + fix-ruff + fix-pycache + generate-manifests |
| `make check` | Диагностика — все проверки из core/check-suite.yaml | make check [WORKERS=6] [JSON=1] [SKIP_FIX=1] [VERBOSE=1] [CHECK_CACHE=0] [MARKER=\<suite\>] [TEST_FILE=\<path\>] | python3 -m core.internal.check_suite run (SoT core/check-suite.yaml) |
| `make check-diff` | Узкая диагностика по изменённым файлам | make check-diff | python3 -m core.internal.check_suite run --mode diff |
| `make templates-check` | Проверка покрытия и разрешимости шаблонов (internal) | make templates-check | core/internal/template_engine.py check (template-manifest coverage) |
| `make load-test` | Запуск нагрузочного теста (locust-генератор + PromQL-отчёт + baseline) | make load-test [SCENARIO=\<s\>] [NODE=\<n\>] [MODE=smoke|regression|capacity] [LOAD_RUNNER=local|node] [LOAD_RPS=...] [LOAD_DURATION=...] | python3 -m core.internal.loadtest.runner_cli --scenario \<s\> --node \<n\> --mode \<m\> (locust headless --max-rps + PromQL saturation + baseline) |
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

**Typed contract:** `internal/` вызывает `modules/` **только** через `invoke_module_interface()` из `core/lib/module-interface.sh`. Модуль должен регистрировать интерфейсы в `module.yaml#interfaces`. Gate #8 v2 валидирует оба условия — прямой вызов `bash modules/\<name\>/...` без `invoke_module_interface` = violation, вызов незарегистрированного интерфейса = violation.

**DataFlow (Gate #8 v3):** Extended Variable Registry (`core/lib/paths.sh`) + Local Variable
Tracking (`local/export/readonly VAR=path`) + ShellCheck Data-Flow (SC2154; graceful
degradation при отсутствии ShellCheck).

**Bootstrap→deploy import direction:** `bootstrap/` может импортировать `deploy/`
(bootstrap оркестрирует деплой, φ8); обратное (`deploy/` → `bootstrap/`) — запрещено
(gate cross_layer_imports). Рациональность: deploy — нижележащий транспортный слой;
инверсия создала бы цикл «transport vs orchestration».

**Remote-команды никогда не получают локальные пути:** ни одна строка passthrough / `build_*_ssh_cmd` / `execute_remote_*` НЕ должна форвардить переменные-пути с локальными значениями (`AGE_SECRET_KEY_FILE`, `PLATFORM_ROOT`, `NODE_CONFIGS_DIR`, `PROJECTS_BASE`, `NODE_YAML` — в форме `$VAR`/`${VAR}`) или флаг `--age-secret-key-file` в remote-аргументы. Ключи/секреты читаются ЛОКАЛЬНО (node_detect-цепочка), в remote уходит только КОНТЕНТ (`--age-secret-key` / `AGE_SECRET_KEY` env). Remote-сторона (`node-lifecycle.sh`) НЕ принимает пути. Enforcement: `tests/gates/test_gate_local_path_in_remote.py` (allowlist пуст).

---
### Разрешённые глаголы

Полный список разрешённых глаголов см. в таблице «Канонические операции» выше и в [`entrypoint-manifest.yaml`](entrypoint-manifest.yaml) (`allowed_verbs`).

### Системные исключения .PHONY (категорийное правило)

`.PHONY`-таргеты **намеренно НЕ входят** в глоссарий глаголов и в `allowed_verbs`, если попадают в одну из категорий (перечень имён упразднён):

| Категория | Примеры | Почему вне глоссария |
|-----------|---------|----------------------|
| Стандартные служебные таргеты make | `help`, `venv` | Интерактивная помощь разработчику / dev-инфраструктура, не операции на VPS/CI-ноде |
| Префиксы `test-`/`gate-`/`pre-commit-` | `pre-commit-install`, `pre-commit-run` | Setup/локальные инструменты, дублируются CI gate'ами |
| `_`-префиксные имена | `_get_all_profiles` | Технические помощники гейтов, не канонические операции |

**Инвариант:** эти таргеты не имеют `delegates_to`-цепочек и исключаются генератором манифеста
(`SYSTEM_EXCEPTIONS` + `SYSTEM_PREFIXES` в `generate_entrypoint_manifest.py`) из `allowed_verbs`.
Валидатор — `core/internal/lint/doc_header_validator.py` (`STANDARD_MAKE_SERVICE_TARGETS`).
Новый служебный таргет не требует правок — достаточно попасть в категорию.

---

## DR мастер-ключа AGE

Каноническая DR-стратегия AGE мастер-ключа. Цель —
пережить полную потерю ноды (reprovision, hoster ban, key-meltdown) без потери расшифровки
`secrets.env` и проектных секретов. Реальные значения ключей в репозитории НЕ фиксируются.

**Инварианты:**
1. НИКАКИХ реальных значений ключей в секции/отчётах — только имена и процедуры.
2. Мастер-ключ в покое НИКОГДА не хранится в открытом виде вне ноды; off-node backup —
   ТОЛЬКО зашифрованный (sops/KMS) и в защищённом хранилище.
3. Восстановление — «restore-first»: новая нода бутстрапится, ключ доставляется зашифрованным
   и расшифровывается НА ноде; plaintext-ключ не пересекает сеть.
4. Temp-ключ при дешифровке — на tmpfs (`/dev/shm`), 0600, dd-wipe после (`decrypt_secrets.py`).
5. sops stderr санитизируется (truncate + redact temp-key path).

### 1. Где хранится мастер-ключ (node_detect цепочка)

Единая точка детекции — `core/internal/shared/node_detect.py::detect_age_key()`. Цепочка
(первый непустой источник побеждает):

| Приоритет | Источник | Контекст |
|-----------|----------|----------|
| 1 | `AGE_SECRET_KEY` (env) | CI (node-update, GitHub Secrets), bootstrap (ключ передаётся как env-контент, не файл) — канон |
| 2 | `SOPS_AGE_KEY` (env) | sops-совместимость |
| 3 | `AGE_SECRET_KEY_FILE` (env) | путь к файлу-ключу (bootstrap оператора) |
| 4 | `~/.config/age/keys.txt` (default key file) | dev-машина оператора; на dev-машине — symlink на `~/.ssh/age-key-personal.txt` (age CLI default-локация) |
| 5 | `/etc/age/key.txt` (Check 5) | **restore-first fallback — non-canonical**: ручной перенос ключа оператором при восстановлении ноды; читается только если env-цепочка пуста и default key file не найден. φ4 ключ НЕ персистит |

**На ноде:** bootstrap (φ4 secrets-provision) НЕ записывает ключ на диск — persist-блок
(`phases/secrets.py`) удалён; ключ приходит env (`AGE_SECRET_KEY`/`AGE_SECRET_KEY_FILE`)
и используется ТОЛЬКО для расшифровки через tmpfs decrypt-only (`decrypt_secrets.py`: temp-key
на `/dev/shm` 0600 + dd-wipe). Мастер-копия живёт в защищённом месте вне репозитория
(секреты оператора / GitHub Secrets / password manager) — НЕ на файловой системе ноды в plaintext.
`/etc/age/key.txt` на ноде допустим исключительно как restore-first fallback (ручной перенос;
0600 root — M6, security hardening: plaintext-ключ только root-readable, канон — env/tmpfs).

### 2. Off-node encrypted backup (sops/KMS)

**Инвариант:** plaintext мастер-ключ за пределы ноды НЕ выходит — backup только зашифрованный
(sops age-реципиент или KMS; хранилище — S3 timeweb.cloud, отдельный bucket, private ACL;
резервный слой — второй KMS-регион/печатная копия; периодичность — при каждой ротации).

**Процедура:** прочитать ключ → `sops encrypt --age <recipient>` → выгрузить .enc в приватный
bucket → sha256-сверка (целостность до удаления локального plaintext).

### 3. Процедура восстановления (DR-drill)

1. Новая нода бутстрапится до φ4 (остановка на secrets-provision — ожидаемо).
2. Оператор доставляет `age-master-key.enc` по защищённому каналу (SCP).
3. На ноде: `sops --decrypt` → temp на tmpfs `/dev/shm` (0600) → `AGE_SECRET_KEY` env → повтор φ4.
4. Верификация: `make secrets-unlock NODE=<new>` + сверка известного значения.
5. Персист в password manager / GitHub Secrets; temp dd-wipe (автоматика `decrypt_secrets.py`).

### 4. Threat-model (кратко)

Потеря ноды → off-node encrypted backup (остаточный риск низкий); утечка из env-логов →
masked-логи + sops stderr sanitize; plaintext на диске → tmpfs + dd-wipe (малое окно);
KMS-компрометация → отдельный KMS-ключ + ротация; backup в облаке → шифрование ДО выгрузки.

### 5. Completion status (операционные долги)

- `/etc/age/key.txt` plaintext — закрыто: persist удалён из φ4, канон env → tmpfs decrypt-only.
- Off-node encrypted backup — **Debt** (`DR-offnode-backup`, Rev 2026-08-31).
- DR-drill на test-VPS — **Debt** (`DR-drill`, Rev 2026-08-31).
- `make age-key-backup` — отложена до drill'а.

---

## Безопасность данных: RTO/RPO и fix-forward (C5/C6, security hardening)

**Single-node модель (осознанное решение):** одна нода, failover отсутствует. Честные
операционные границы зафиксированы (не рекламные):

| Метрика | Значение | Обоснование |
|---------|----------|-------------|
| **RTO** | **часы** | ручной bootstrap (~30 мин) + restore postgres + redeploy проектов; автоматизирован restore-таргетом postgres (`make restore`) и каноном runbook §Runbook |
| **RPO** | **24ч** (логический) | nightly `pg_dumpall` в S3; PITR-граница — в пределах WAL-retention (weekly `pg_basebackup`; до его внедрения физический PITR ограничен) |

**Fix-forward политика rollback (C5):** откат деплоя откатывает ТОЛЬКО образ (docker tag)
+ healthcheck-rollback; миграции БД нового кода НЕ откатываются — старый код против новой
схемы недопустим, поэтому rollback = fix-forward (новый коммит, не откат схемы). Страховка
от неудачной миграции — nightly-дампы (RPO 24ч) + pre-restore снэпшот в restore-таргете.

---

## TLS/DNS-01 и ClickHouse-сеть (M31/LOW, security hardening)

**TLS/DNS-01 (acme.sh):** wildcard-сертификаты выдаются только через DNS-01. Реестр
провайдеров — `certs-providers.yaml` (SoT: name/plugin/mode/creds) + `provider_registry.py`
(longest-suffix per-domain resolve). Канал renewal — cron `--renew-hook` → S3-синхронизация
(`s3_ssl_cache.py`). **Правило fallback:** HTTP-01 — только как fallback и БЕЗ wildcard
(HTTP-01 не умеет wildcard); при `zone_manager_unavailable` от webnames сначала проверять
add/delete TXT (listing-эндпоинт ≠ DNS-01 сломан — TRAP[BUG] в bootstrap/AGENTS.md).
Хранение кредов: webnames — inject+shred, regru — account.conf root:600 (env-passthrough).

**ClickHouse listen_host 0.0.0.0 (LOW):** осознанное решение — контейнер слушает на всех
интерфейсах, НО снаружи закрыт ufw (default-deny + DOCKER-USER), публичной точки нет
(единственная публичная точка — nginx 80/443). Не менять на 127.0.0.1 без миграции
межконтейнерных consumer'ов (shared-сети).

---

## Ротация SSH/CI-ключей

Канонический runbook ротации CI-ключей/секретов.
Авторитетный инвентарь секретов — `core/secret-definitions.yaml` (SSoT); реальные значения —
только в GitHub Secrets / node secrets.env (sops).

**Инварианты:** (1) никаких реальных значений в доках; (2) ротация — двухключевой переход
(add new → verify → remove old), НЕ одномоментная замена; (3) окно отката 30 дней (старый
ключ — в защищённом месте); (4) `GITHUB_TOKEN` — авто-провижинится, ручная ротация не нужна.

**Матрица ключей (полный список):** `VPS_SSH_KEY` (root-rsync core/), `CI_DEPLOY_KEY`
(forced-command `receive`, repo-level deploy key ×N проектов), `MIRROR_SSH_KEY`
(mirror push, pub — `.github/mirror-deploy-key.pub`), `GITHUB_TOKEN` (auto),
`GIT_MIRROR_TOKEN` (PAT, отозван: HTTPS fallback удалён, mirror SSH-only),
`DOCKER_HUB_USERNAME/TOKEN`, `GHCR_PULL_TOKEN` (node sops), `GHCR_PUSH_TOKEN` (CI),
`GHCR_OWNER` (derived, не ключ), `TELEGRAM_*` (BOT_TOKEN/CHAT_ID*_WARNING/_CRITICAL/
PROXY_URL/API_BASE/ALLOWED_USERS/GETME_URL), `AGE_SECRET_KEY` (мастер-ключ — DR-секция),
`VPS_HOST`/`NODE_HOST_MAP`.

**Чек-листы ротации (суть):** generate `ssh-keygen -t ed25519` → добавить pub (node.yaml /
repo deploy keys ×N / GitHub-аккаунт / BotFather) → обновить Secrets/sops → проверить канал
(`make converge` / `make deploy-project` / mirror dispatch / тестовая нотификация) → удалить
старый ключ → старый приватный ключ в защищённое место на 30 дней → аудит
(`write_audit_entry(tag="ci-secret:rotate")`). `AGE_SECRET_KEY`: новый ключ → `sops update-keys`
ВСЕХ файлов → SCP на ноду (НЕ git) → `make secrets-unlock` проверка → shred старого; потеря
мастер-ключа = потеря секретов (восстановление только из DR-бэкапа).

**Сценарии:** новая VPS — генерация ключей ДО bootstrap; утечка лога CI — отозвать PAT;
потеря AGE-ключа — DR-секция.

**grep-гейт:** новый CI-секрет в `.github/`/`makefiles/`/`core/` обязан попасть в матрицу
`core/secret-definitions.yaml` — секрет «в голове» = RED.

---
## Нагрузочное тестирование

Система load-тестирования: Locust-генератор, 3 режима, PromQL-анализ насыщения из
существующего Prometheus, baseline-сравнение. Таргет — тонкий фасад:
`python3 -m core.internal.loadtest.runner_cli --scenario <s> --node <n> --mode <m>`
(реализация: `core/internal/loadtest/*.py`; SoT: `core/loadtest/scenarios.yaml`).

**Ключевые инварианты (детали — код):**
- **users ≠ rps** — точный RPS задаёт `constant_throughput` через env `LT_TARGET_RPS`/`LT_USERS`
  (единый helper `core/loadtest/scenarios/__init__.py`; users = rps × 2).
- **Длительности ≥ scrape_interval Prometheus**: smoke ≥ 90s; rate-окна ≤ run_time/2; <2 сэмплов → WARN.
- **Ноль новой мониторинговой инфраструктуры** — только post-run PromQL pull (порт 9090).
- **LLM-детерминизм** — сценарии llm гоняются только против mock-модели `mock-echo`; без mock — ранний FAIL.
- Сценарии: `web`, `llm`, `llm_stream`, `langfuse_ingest` (включены); `db` (PG wire protocol
  через stdlib, только `LOAD_RUNNER=node` + `LOAD_NETWORK=shared-db-net`), `s3` (SigV4, без boto3) — optional.
- Режимы: `smoke` (90s, 0 errors AND p95<max → PASS), `regression` (300s, p95 ≤1.5×prev,
  error ≤prev+2pp), `capacity` (шаг 60s, max_steps=8, автостоп error>5% | p99>3s).
- Capacity на production без `LOAD_ALLOW_PROD=1` → exit 10. Baseline: `core/loadtest/history/`
  (коммитится); смена host тестовой VPS → `baseline_reset` (PASS с пометкой), не FAIL.
- Remote-режим (`LOAD_RUNNER=node`): rsync core/loadtest/ → docker run locust на ноде
  (вне стека, `--cpus 2`); PromQL-pull и отчёт — локально.
- Отчёт: `load-results/<node>/<scenario>/<mode>/<ts>/` (gitignored) — report.json/md/junit;
  `history.json` — источник сводной статистики.
- Юнит-тесты: `tests/unit/test_loadtest_*.py`; e2e: `tests/e2e/test_load_test.py` (requires_node).

---
## Архитектурные инварианты

12 архитектурных инвариантов платформы определены **только** в `AGENTS.md` (root) — `#region MODULE_CONTRACT`. Настоящий файл не дублирует их — все расхождения между копиями устранены в пользу root как единственного Source of Truth.

**Правило:** если новый инвариант затрагивает общую архитектуру платформы — добавляй в root AGENTS.md. В core/AGENTS.md описываются только core-специфичные контракты (операции, структура).

## Логгер-канон (keep-TRAP)

⚠️ TRAP[DECISION] · — · Единая конфигурация stdlib logging (LDD-формат) НЕ вводится:
60+ файлов используют самопаттерн `logging.getLogger(__name__)` + [IMP:n][FUNC]-префиксы —
функционально однороден; централизация даст churn без поведенческой выгоды. · Rev: если
появится требование структурированных логов (JSON-линтер/парсер в мониторинге) —
ввести единый конфигуратор в shared/ и мигрировать волнами.

---

## Hook-окружение (dev-машина, поведение 2026-08-15 v1.0.1)

| Событие | Поведение | Следствие для агента |
|---------|-----------|----------------------|
| `git push` | pre-push → quick check (pre-commit run --all-files + ruff check . + make check-diff): ~1-2 мин, все ветки. ПОЛНЫЙ fast-gate — только CI push-gate.yml (OOM-политика 0.8: hook не гоняет 12 параллельных xdist-прогонов) | таймаут bash-тула ≥300s; CI — финальный арбитр |
| push-отказ БЕЗ remote-сообщения | exit hook'а ≠ 0 (вывод hook'а в stderr, git его не показывает) | причина — FAIL-строки stderr hook-лога, НЕ rulesets/auth |
| `git commit` | pre-commit правит whitespace/ruff | перед commit: `make fix-gate && git add -u` |
| `make check` | static_audit timeout 900s, вывод в конце шага | «10 мин без вывода» — норма, не зависание; тайминги шагов печатает executor |
| `make gate` (ручной) | pytest-xdist воркеры = min(cpu, free_GB), CHECK_XDIST_MAX_WORKERS — жёсткий потолок (memory-guard 0.8) | при дефиците памяти gate замедляется, но НЕ убивает машину |

Env-override: `AGE_SECRET_KEY` (env сессии) перекрывает файл/цепочку node_detect — при деплое
секретов предпочитать файл (`unset AGE_SECRET_KEY`), иначе core-deliver печатает warning.

---

## node_yaml CLI контракт --get / --get-many

`python3 -m core.internal.shared.node_yaml --get <dotted.key>` / `--get-many alias:key,...` — единая точка
чтения node.yaml для shell-фасадов. **Скалярный вывод нормализован** (единая точка — `_format_cli_value`
в `core/internal/shared/node_yaml/cli.py`):

- **bool** → lowercase `"true"` / `"false"` (НЕ Python `"True"`/`"False"`)
- **числа** (int/float) → десятичные строки (`str(value)`, без кавычек)
- **прочие типы** (str/None/list/dict) → как есть (Python `str()`/repr)
- **JSON-режимы** (`--items`, `--json-output`) → сырые Python-типы (НЕ нормализуются)

**Потребители** сравнивают значения ТОЛЬКО через нормализованные выражения: `[ "$(node_yaml --get ...)" = "true" ]`
(CLI уже отдаёт lowercase) или Python `(x or "").lower() == "true"` / нормализация на входе функции.
Строгие сравнения с булевыми литералами без `.lower()` запрещены гейтом `tests/gates/test_gate_bool_string_literals.py`
(per-line allowlist: deploy_orchestrator.py:314 — вход нормализован в `parse_modules_from_node_yaml`).

---

## Exit-коды (контракт)

Единый контракт exit-кодов на весь core. Машиночитаемые константы — `core/internal/shared/contracts.py`; runtime-классы исключений — `core/internal/shared/exceptions.py`.

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

## Shared-модули и shell-фасады (указатели)

- **Инвентарь shared-модулей** (полный перечень + API + потребители): [`internal/shared/AGENTS.md`](internal/shared/AGENTS.md) — канон области.
- **Shell-исключения** (keep-решения фасадов в `core/lib/`): root `AGENTS.md` §Shell-исключения — единый SoT.

**requirements.txt — GENERATED из pyproject.toml [project].dependencies:**
ручные правки запрещены (инвариант 11); регенерация: `make generate-requirements`;
проверка актуальности: `make check MARKER=check-requirements` (byte-level, exit 1 на divergence).
