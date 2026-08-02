# GREP_SUMMARY: AGENTS.md, shared, inventory, node-yaml, docker-compose, audit-logger, ssh-parser, telegram, docker-auth, age-key, node-detect, vps-readiness, crypto, content-hash, secrets-env, secrets-manifest-reader, deploy-paths, verbs, project-registry, exceptions, timeouts, ssh-opts, contracts
# STRUCTURE: ┌контракт области┐ → ◇ инвентарь 21 модуль (таблица) → ◇ правила добавления → ◇ запреты → ⎋ cross-refs
# region MODULE_CONTRACT
## @purpose  Архитектурный контракт области core/internal/shared/ — инвентарь модулей и правила добавления.
## @scope    Все модули под core/internal/shared/. Закрывает остаток RC3 C1: «no canonical architecture document
##           for shared/ — each module was added ad-hoc» (085-rc3-verification, 2026-07-31).
## @invariants
##   1. shared/ — единственное место для переиспользуемой бизнес-логики уровня internal (НЕ infra-фасадов).
##   2. Каждый модуль имеет MODULE_CONTRACT с @purpose/@scope/@invariants (doxygen-python standard).
##   3. Новый модуль в shared/ требует: (а) минимум 2 потребителей ИЛИ дедупликацию ≥2 существующих реализаций,
##      (б) unit-тесты в tests/unit/, (в) запись в таблицу ниже.
##   4. Facade-паттерн: shell-скрипты НЕ дублируют логику — вызывают python3 -m core.internal.shared.MODULE.
##   5. НИКОГДА не импортировать core/internal/bootstrap/deploy/* из shared/ (слой зависимостей — только вниз).
## @rationale Единый инвентарь предотвращает ad-hoc добавление модулей и дублирование реализаций
##            (RC3 C1: каждый модуль добавлялся точечно без контракта области).
## @changes 2026-07-31 | Создан (debt C1) — инвентарь 15 модулей, правила добавления, запреты
##           2026-07-31 | DevPlan 104 — +node_detect.py (16-й модуль); age_key.py → compat-шим
##           2026-07-31 | DevPlan 105 — +vps_readiness.py (17-й модуль); фасад vps-readiness.sh
##           2026-08-01 | DevPlan 116 B6 — +schema_validator.py (18-й модуль); inventory update
##           2026-08-01 | DevPlan 116 B5 — +timeouts.py (19-й), +ssh_opts.py (20-й); инвентарь
##           2026-08-01 | DevPlan 116 B4 — +contracts.py (21-й); инвентарь
##           2026-08-01 | DevPlan 116 B9 T4 — +stub_detection.py (22-й); инвентарь
##           2026-08-01 | DevPlan 116 B1 T1/T7 — +verbs.py (23-й); −platform_deliver.py (verb УДАЛЁН, D1)
##           2026-08-01 | DevPlan 117 C — +ssl_certs.py (24-й), +s3_client.py (25-й); project_registry +discover_llm_projects
##           2026-08-02 | DevPlan 118 A2 — +compose_files.py (26-й); deploy_paths.py +projects_base (A3)
# endregion MODULE_CONTRACT

# core/internal/shared/ — инвентарь модулей

| Модуль | Назначение | Ключевой API | Потребители |
|--------|-----------|--------------|-------------|
| `age_key.py` | Compat-re-export шим — детекция AGE-ключа делегирована в node_detect.py (DevPlan 104) | `detect_age_key()` (re-export), CLI | decrypt-secrets, bootstrap, node-update |
| `audit_logger.py` | Единый JSON-lines audit логгер — ЕДИНСТВЕННЫЙ writer (D1, DevPlan 116 B11 T2: deploy/audit_logger.py удалён, reporting pipe мигрирован). Расширенная схема ts/tag/status/msg + extra (operation/project/channel/result/duration_s/snapshot_id) | `write_audit_entry(tag, status, msg, **extra)`, `read_audit_log()`, CLI `write/read --log-file` | context_deployer, deploy_orchestrator (DeployAuditLogger adapter), lifecycle/helpers/reporting, scaffold/vhost_renderer, lib/audit.sh |
| `content_hash.py` | SHA256 content-hash для идемпотентности bootstrap (state.json sub-steps) | `compute_step_hash()`, `step_hash_changed()` | state_machine |
| `compose_files.py` | Единый SoT списков compose-файлов и резолва (DevPlan 118 A2 — заменяет 6 локальных кортежей: docker_orchestrator, converge/runtime, converge/volumes, orphan_reconciler, payload_deliverer, project_adopter) | `COMPOSE_FILENAMES`, `PROJECT_COMPOSE_FILENAMES`, `resolve_compose_file()`, `requires_compose_project()` | docker_orchestrator, converge/runtime, converge/volumes, orphan_reconciler, payload_deliverer, project_adopter, gate compose_files_sole_path |
| `contracts.py` | Контракт операционных политик (DevPlan 116 B4 T1, U-39) — DEPLOY_BEST_EFFORT (legacy parity) + machine-readable exit-коды | `DEPLOY_BEST_EFFORT`, `EXIT_OK/GENERIC/CONFIG_NOT_FOUND/CONFIG_PARSE/CONFIG_VALIDATION/FATAL` | deploy_orchestrator, гейты B4 (broad-except-allowlist, exit-codes-documented) |
| `crypto.py` | APR1/htpasswd хэширование (openssl passwd -apr1, детерминизм через salt) | `hash_apr1()`, `generate_htpasswd_entry()`, CLI `hash/entry [--salt]` | lib/secrets.sh, secrets_manager |
| `deploy_paths.py` | Канонический реестр путей доставки кода (SoT для удаления deprecated путей) | `get_canonical_paths()`, `DEPRECATED_DEPLOY_PATHS` | core-deploy CI, deploy |
| `docker_auth.py` | Единый Docker registry auth (заменяет 5 дублирующихся точек) | `docker_login()`, `ghcr_login()`, `configure_docker_auth()` | bootstrap registry-auth, phases.py |
| `docker_compose.py` | Shared compose-операции: pull/build/up/healthcheck_poll | `docker_compose_pull()`, `docker_compose_build()`, `docker_compose_up()`, `healthcheck_poll()` | context_deployer, docker_orchestrator, DeployEngine |
| `exceptions.py` | Типизированная иерархия ошибок платформы | `PlatformError`, `ConfigValidationError`, `ConfigNotFoundError`, `ConfigParseError`, ... | все Python-модули |
| `node_detect.py` | Детекция AGE-ключа (env-цепочка) + авто-детекция имени ноды из node-configs (DevPlan 104 — дедупликация bootstrap/converge/node-update) | `detect_age_key()`, `auto_detect_node_name()`, CLI `--detect-age-key` / `--detect-node-name` | bootstrap, converge, node-update |
| `node_yaml.py` | Единый фасад чтения node.yaml (мутации с TRAP[BUG] 2026-07-30) | `NodeYaml(path).get(...)`, CLI `--get/--set` | vhost_renderer, reconciler, converge, scaffold |
| `project_registry.py` | Реестр проектов: регистрация/дерегистрация/список поверх NodeYaml | `validate_project_name()`, `register/unregister/list`, `discover_llm_projects()` (DevPlan 117 D24 — LLM-проекты по ai-platform.yaml llm.enabled=true) | DeployEngine, scaffold, lifecycle, key_provisioner (discover_projects shim → делегирование) |
| `s3_client.py` | Единая boto3 S3-фабрика платформенного домена (DevPlan 117 D26 — дедупликация s3_ssl_cache._get_s3_client + preflight инлайн; backup-cron upload/retention вне скоупа) | `get_s3_client(endpoint=None, access_key=None, secret_key=None, max_attempts=3, region=None)` | s3_ssl_cache, preflight |
| `schema_validator.py` | Единый schema-валидатор YAML↔JSON-Schema (draft-07) — единственная Draft7Validator-точка (DevPlan 116 B6 T5, дедупликация jsonschema_validate.py + node_yaml.validate) | `validate_yaml_against_schema()`, `validate_dict_against_schema()` | jsonschema_validate, node_yaml.validate |
| `secrets_env_parser.py` | Единый парсер secrets.env (заменяет 7 inline-парсеров) | `parse()`, `write()`, `merge()`, `export_shell()` | decrypt-secrets, secrets-init, bootstrap |
| `secrets_manifest_reader.py` | Строгий ридер secrets-manifest.yaml (заменяет 3 парсера с разными graceful-degradation семантиками; отсутствие = громкий fail, не silent `[]`) — DevPlan 116 T4, U-33/U-43 | `iter_secrets()`, `tier()`, `consumers()`, `charset()`, `gen_command()` | secrets_manager, secrets_validator |
| `ssh_command_parser.py` | Парсер SSH_ORIGINAL_COMMAND (заменяет 2 дублирующихся парсера) | `parse_ssh_command()`, `classify_verb()` | deploy forced-command, deploy.sh |
| `ssh_opts.py` | Единый SoT SSH-флагов (DevPlan 116 B5 T2, D1 — заменяет 5 Python-копий «SSH_OPTS» + shell lib/ssh.sh фасад) | `SSH_OPTS`, `build_rsync_ssh_opts()`, CLI `--shell`/`--rsync-e` | core_deliverer, overlay_deliverer, remote_executor, channels ×2, lib/ssh.sh (python3 -m) |
| `ssl_certs.py` | Единый SoT openssl x509-примитивов (DevPlan 117 D21 — дедупликация s3_ssl_cache._validate_cert + cert_orchestrator._is_cert_valid/_is_le_issuer) | `cert_is_parseable()`, `cert_check_expiry()`, `cert_get_issuer()`, `cert_is_le_issuer()`, `DEFAULT_OPENSSL_TIMEOUT=10`, `DEFAULT_EXPIRY_THRESHOLD=2592000` | s3_ssl_cache, cert_orchestrator |
| `stub_detection.py` | Единая is_stub-детекция ai-platform.yaml (DevPlan 116 B9 T4, U-28 — консолидирует дубль reconciler_projects + converge/reconciler) | `is_stub_ai_platform_yaml(path)` | reconciler_projects (wrapper is_stub_project), converge/projects (R3) |
| `telegram_notifier.py` | Единый Telegram-клиент (заменяет 6 реализаций: 3 shell + 3 Python) | `send_telegram()` | notify-hook, hermes-agent, deploy |
| `timeouts.py` | Единый реестр таймаутов операционных политик (DevPlan 116 B5 T1, U-11 — единственный источник числовых timeout= в docker/ssh/healthcheck-домене) | `COMPOSE_UP_TIMEOUT`, `PULL_TIMEOUT`, `BUILD_TIMEOUT`, `HEALTHCHECK_POLL_TIMEOUT`, `SSH_CONNECT_TIMEOUT`, `DEPLOY_TIMEOUT`, `SSH_READ_TIMEOUT`, `RETRY_BACKOFF_SECONDS`, `IMAGE_CHECK_TIMEOUT`, `DOCKER_CMD_TIMEOUT`, `DOCKER_STOP_TIMEOUT`, `RSYNC_TIMEOUT`, `RETRY_COUNT` | docker_compose, ssh_opts, channels, docker_orchestrator, deploy_engine, reconciler, context_deployer, remote_executor, overlay_deliverer, context_promoter, orphan_reconciler, deploy_orchestrator |
| `vps_readiness.py` | VPS pre-flight проверки (SSH, forced-command ping, /opt/projects/, Docker) — Strangler-миграция vps-readiness.sh (DevPlan 105, дедупликация deploy.mk/CI pre-flight) | `check_vps_ready()`, CLI `NODE [--json|--quick]` | deploy.mk pre-flight, deploy-project.yml (через фасад core/lib/vps-readiness.sh) |
| `verbs.py` | Канонический verb-словарь forced-command диспетчера (DevPlan 116 B1 T1, U-56) — единый источник CANONICAL_VERBS + reserve-имен для проектов | `CANONICAL_VERBS`, `VERB_RESERVE`, `is_verb()`, `validate_not_verb()` | ssh_command_parser (classify_verb), project_registry (validate_project_name), gate канала (T10) |
| `__init__.py` | Пакетный контракт shared-области | — | — |

## Правила добавления нового модуля

1. **Обоснование:** минимум 2 потребителя ИЛИ дедупликация ≥2 существующих реализаций (критерий из RC3 C1).
2. **Контракт:** MODULE_CONTRACT с @purpose/@scope/@invariants + GREP_SUMMARY/STRUCTURE (doxygen-python).
3. **Тесты:** unit-тесты в `tests/unit/test_shared_MODULE.py` (нативные импорты, tmp_path, LDD).
4. **Реестр:** строка в таблице выше + упоминание в root AGENTS.md §New shared modules (DevPlan 086).
5. **Запрет ad-hoc:** одноразовая утилита для одного потребителя → живёт рядом с потребителем, НЕ в shared/.

## Запреты

| # | Запрет | Причина |
|---|--------|---------|
| 1 | Импорт из `bootstrap/deploy/` или `bootstrap/lifecycle/` | Layer violation — shared ниже по зависимостям (инвариант 5) |
| 2 | Прямой YAML-парсинг node.yaml вне NodeYaml фасада | DRIFT-088-7: единая точка чтения node.yaml |
| 3 | Дублирование логики модуля в shell (inline python3 -c) | Языковая политика — facade вызывает `python3 -m core.internal.shared.MODULE` |
| 4 | Новый модуль без unit-тестов | R1-R5 Test Honesty (tests/AGENTS.md) |

## Cross-references

| Файл | Назначение |
|------|-----------|
| root AGENTS.md | Архитектурные инварианты, языковая политика, New shared modules (086) |
| core/AGENTS.md | Канонические операции core, cross-layer import rules |
| tests/unit/test_shared_*.py | Unit-тесты shared-модулей |
| .ai/debt/096-Residual-Debt.md §C1 | Источник долга (shared/AGENTS.md отсутствовал) |
