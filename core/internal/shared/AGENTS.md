# GREP_SUMMARY: AGENTS.md, shared, inventory, node-yaml, docker-compose, audit-logger, ssh-parser, telegram, docker-auth, age-key, node-detect, vps-readiness, crypto, content-hash, secrets-env, secrets-manifest-reader, deploy-paths, verbs, project-registry, exceptions, timeouts, ssh-opts, contracts, env-requires
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
##           2026-08-02 | DevPlan 118 C3/C5/C6/C10 — +compose_profiles.py (27-й), +module_interface.py (28-й),
##                      +llm_paths.py (29-й), +subprocess_io.py (30-й); deploy_paths +letsencrypt_live/
##                      node_configs_remote/platform_remote_base (C7); ssl_certs +cert_is_valid (C9)
##           2026-08-02 | DevPlan 118 D3 — −age_key.py (compat-шим УДАЛЁН, decrypt_secrets → node_detect);
##                      −ssh_command_parser.py (перенесён в core/internal/deploy/, потребитель orchestrator_cli)
##           2026-08-02 | DevPlan 118 D4 — +env_requires.py (31-й); единый env-requires чекер
##           2026-08-02 | DevPlan 118 E11 — +project_yaml.py (32-й); общий читатель ai-platform.yaml
##           2026-08-02 | DevPlan 119 B1 — project_yaml: ЕДИНСТВЕННЫЙ парсер ai-platform.yaml (8 потребителей мигрированы, AC-B1.1);
##                      B2/B3 — deploy_paths каноны (DEFAULT_PROJECTS_BASE/platform_remote_base/node_configs_remote) во всех /opt/* литералах;
##                      B4 — subprocess_io +fatal_rc, lifecycle/helpers/subprocess_io.py удалён
##           2026-08-02 | DevPlan 119 C3 — verbs.validate_not_verb удалён (0 ссылок)
##           2026-08-02 | DevPlan 119 E5 — +atomic_writer.py (33-й); канон атомарной записи
##           2026-08-04 | DevPlan 127 W2 — +node_resolver.py (34-й); Python-резолв node.yaml
##                      (миграция core/lib/node-resolver.sh, S8/P2-1)
##           2026-08-04 | DevPlan 128 W1 — +docker_ops.py (35-й); единый слой docker-операций
##                      (P2-5/D6; ps/inspect/exec/stop/rm/tag/image/network/volume/stats/info/manifest/pull;
##                      гейт docker_sole_path allowlist пуст; lib/docker.sh — --shell фасад)
# endregion MODULE_CONTRACT

# core/internal/shared/ — инвентарь модулей

| Модуль | Назначение | Ключевой API | Потребители |
|--------|-----------|--------------|-------------|
| `atomic_writer.py` | Канонический атомарный writer (DevPlan 119 E5 — tempfile+fsync+os.replace+optional validator). Заменяет 12+ локальных копий os.replace/NamedTemporaryFile с разной семантикой. Исключение: json_writer.py (Docker bind mount TRAP) | `atomic_write(path, content, mode, validator)`, `atomic_write_json()`, `atomic_write_text()` | secrets_env_parser, docker_registry_auth, docker_daemon, s3_ssl_cache, sudoers_generator, lifecycle/helpers/system (cron), metrics/cache, sync_env_defaults, template_engine, node_yaml._write_back |
| `audit_logger.py` | Единый JSON-lines audit логгер — ЕДИНСТВЕННЫЙ writer (D1, DevPlan 116 B11 T2: deploy/audit_logger.py удалён, reporting pipe мигрирован). Расширенная схема ts/tag/status/msg + extra (operation/project/channel/result/duration_s/snapshot_id) | `write_audit_entry(tag, status, msg, **extra)`, `read_audit_log()`, CLI `write/read --log-file` | context_deployer, deploy_orchestrator (DeployAuditLogger adapter), lifecycle/helpers/reporting, scaffold/vhost_renderer, lib/audit.sh |
| `content_hash.py` | SHA256 content-hash для идемпотентности bootstrap (state.json sub-steps) | `compute_step_hash()`, `step_hash_changed()` | state_machine |
| `compose_files.py` | Единый SoT списков compose-файлов и резолва (DevPlan 118 A2 — заменяет 6 локальных кортежей: docker_orchestrator, converge/runtime, converge/volumes, orphan_reconciler, payload_deliverer, project_adopter) | `COMPOSE_FILENAMES`, `PROJECT_COMPOSE_FILENAMES`, `resolve_compose_file()`, `requires_compose_project()` | docker_orchestrator, converge/runtime, converge/volumes, orphan_reconciler, payload_deliverer, project_adopter, gate compose_files_sole_path |
| `compose_profiles.py` | Единый loader COMPOSE_PROFILES (DevPlan 118 C3 — заменяет чтение platform-env.yaml в scaffold_helpers + platform_config в docker_orchestrator; SoT platform-infra.yaml) | `load_profiles()` | scaffold_helpers, docker_orchestrator, gate profiles_parity |
| `contracts.py` | Контракт операционных политик (DevPlan 116 B4 T1, U-39) — DEPLOY_BEST_EFFORT (legacy parity) + machine-readable exit-коды | `DEPLOY_BEST_EFFORT`, `EXIT_OK/GENERIC/CONFIG_NOT_FOUND/CONFIG_PARSE/CONFIG_VALIDATION/FATAL` | deploy_orchestrator, гейты B4 (broad-except-allowlist, exit-codes-documented) |
| `crypto.py` | APR1/htpasswd хэширование (openssl passwd -apr1, детерминизм через salt) | `hash_apr1()`, `generate_htpasswd_entry()`, CLI `hash/entry [--salt]` | lib/secrets.sh, secrets_manager |
| `deploy_paths.py` | Канонический реестр путей доставки кода (SoT для удаления deprecated путей) + резолверы /etc/letsencrypt/live, /opt/node-configs, /opt/platform, /opt/projects (DevPlan 118 C7 — топ-5 потребителей делегируют) | `get_canonical_paths()`, `DEPRECATED_DEPLOY_PATHS`, `projects_base()`, `letsencrypt_live()`, `node_configs_remote()`, `platform_remote_base()` | core-deploy CI, deploy, s3_ssl_cache, cert_orchestrator, cert_collector, core_deliverer, overlay_deliverer |
| `docker_auth.py` | Единый Docker registry auth (заменяет 5 дублирующихся точек) | `docker_login()`, `ghcr_login()`, `configure_docker_auth()` | bootstrap registry-auth, phases.py |
| `docker_compose.py` | Shared compose-операции: pull/build/up/healthcheck_poll | `docker_compose_pull()`, `docker_compose_build()`, `docker_compose_up()`, `healthcheck_poll()` | context_deployer, docker_orchestrator, DeployEngine |
| `docker_ops.py` | Единый слой docker-операций (DevPlan 128 W1, P2-5/D6) — ps/inspect/exec/stop/rm/tag/image/network/volume/stats/info/manifest/pull + CLI `--shell` для shell-фасадов | `docker_ps()`, `ps_container_names()`, `docker_inspect()`, `docker_exec()`, `docker_stop()`, `docker_rm()`, `docker_tag()`, `docker_image_inspect(_exists/_many)()`, `docker_manifest_inspect(_raw)()`, `docker_pull()`, `docker_network_inspect(_raw/_create)()`, `docker_volume_inspect()`, `docker_info()`, `docker_stats()` | deploy_engine, docker_orchestrator, observability, orphan_reconciler, converge/{vhosts,networks,volumes,runtime}, modules_healthcheck, docker_collector, deploy/orchestrator, provisioner, reconciler_projects, preflight, hermes_workflow, phases/docker, docker_registry_auth, state_store, security_posture, docker_compose (примитивы), lib/docker.sh (--shell фасад), gate docker_sole_path |
| `env_requires.py` | Единый env-requires чекер (DevPlan 118 D4 — объединяет module.yaml-driven presence и manifest-driven runtime; устраняет расхождение вердиктов validate_module_yaml vs secrets_validator) | `check_requires_presence()`, `check_runtime_env()`, `check_env_requires()`, `env_var_in_dotenv()`, `env_var_in_secrets_manifest()` | validate_module_yaml (фасад), secrets_validator (фасад) |
| `project_yaml.py` | Общий читатель ai-platform.yaml (DevPlan 118 E11 + 119 B1: ЕДИНСТВЕННЫЙ парсер — 8 потребителей прямого YAML-парсинга мигрированы: vhost_renderer/vhost_configurator/conflict_checks/monitoring_config_renderer/project_registry/deploy_engine/generate_catalog/orchestrator) + auto-detect (org-from-path, casing vs node.yaml) | `load_project_yaml()`, `read_project_yaml()`, `get_expose()/get_domain()/get_target_node()/get_needs()/get_llm()/get_monitoring()/get_name()/get_project_type()/get_expose_config()`, `derive_org_from_path()`, `detect_project_config()` | vhost_renderer, vhost_configurator, conflict_checks, monitoring_config_renderer, project_registry, deploy_engine, generate_catalog, orchestrator, project_adopter (detect_project_config re-export) |
| `exceptions.py` | Типизированная иерархия ошибок платформы | `PlatformError`, `ConfigValidationError`, `ConfigNotFoundError`, `ConfigParseError`, ... | все Python-модули |
| `llm_paths.py` | Единый источник пути litellm-config.yml (DevPlan 118 C6 — заменяет 4 копии вывода + 1 шаблон: context_deployer, deploy_orchestrator, llm_provision, phases, config_renderer) | `litellm_config_path(core_dir)`, `litellm_template_path(core_dir)` | context_deployer, deploy_orchestrator, llm_provision, phases, config_renderer |
| `module_interface.py` | Единая bash-обёртка invoke_module_interface (DevPlan 118 C5 — дедупликация docker_orchestrator._invoke_healthcheck_full + deploy_orchestrator._invoke_module_interface; **вход для B8 wire module-hooks**) | `invoke(module, interface, *args, timeout=...) → (bool, output)` | docker_orchestrator, deploy_orchestrator |
| `node_detect.py` | Детекция AGE-ключа (env-цепочка + default key file ~/.config/age/keys.txt — age CLI локация, на dev-машине symlink на ~/.ssh/age-key-personal.txt; только при пустой env, E2E auto-detect 2026-08-02, единый default-путь 2026-08-03) + авто-детекция имени ноды из node-configs (DevPlan 104 — дедупликация bootstrap/converge/node-update) | `detect_age_key()`, `auto_detect_node_name()`, CLI `--detect-age-key` / `--detect-node-name` | bootstrap, converge, node-update |
| `node_yaml/` | Единый фасад чтения node.yaml (DevPlan 119 H1: монолит node_yaml.py 1164 LOC → пакет node_yaml/ — агрегатор + миксины domains/projects/modules/node/validation/resolve; мутации с TRAP[BUG] 2026-07-30) | `NodeYaml(path).get(...)`, CLI `--get/--set` | vhost_renderer, reconciler, converge, scaffold, context_deployer, preflight |
| `node_resolver.py` | Python-резолв node.yaml (DevPlan 127 W2 — миграция core/lib/node-resolver.sh, S8/P2-1): 3-path search через NodeYaml.resolve + host-извлечение; CLI resolve/host с exit-контрактом 0/1; shell-фасад node-resolver.sh <100 LOC | `resolve_node_yaml()`, `extract_node_host()`, CLI `resolve --node X` / `host --file F` | node-resolver.sh (фасад: bootstrap.sh, node-update.sh, node-lifecycle.sh, converge.sh, deploy-context.sh, deploy.mk) |
| `project_registry.py` | Реестр проектов: регистрация/дерегистрация/список поверх NodeYaml | `validate_project_name()`, `register/unregister/list`, `discover_llm_projects()` (DevPlan 117 D24 — LLM-проекты по ai-platform.yaml llm.enabled=true) | DeployEngine, scaffold, lifecycle, key_provisioner (discover_projects shim → делегирование) |
| `s3_client.py` | Единая boto3 S3-фабрика платформенного домена (DevPlan 117 D26 — дедупликация s3_ssl_cache._get_s3_client + preflight инлайн; backup-cron upload/retention вне скоупа) | `get_s3_client(endpoint=None, access_key=None, secret_key=None, max_attempts=3, region=None)` | s3_ssl_cache, preflight |
| `schema_validator.py` | Единый schema-валидатор YAML↔JSON-Schema (draft-07) — единственная Draft7Validator-точка (DevPlan 116 B6 T5, дедупликация jsonschema_validate.py + node_yaml.validate) | `validate_yaml_against_schema()`, `validate_dict_against_schema()` | jsonschema_validate, node_yaml.validate |
| `secrets_env_parser.py` | Единый парсер secrets.env (заменяет 7 inline-парсеров) | `parse()`, `write()`, `merge()`, `export_shell()` | decrypt-secrets, secrets-init, bootstrap |
| `secrets_manifest_reader.py` | Строгий ридер secrets-manifest.yaml (заменяет 3 парсера с разными graceful-degradation семантиками; отсутствие = громкий fail, не silent `[]`) — DevPlan 116 T4, U-33/U-43 | `iter_secrets()`, `tier()`, `consumers()`, `charset()`, `gen_command()` | secrets_manager, secrets_validator |
| `ssh_opts.py` | Единый SoT SSH-флагов (DevPlan 116 B5 T2, D1 — заменяет 5 Python-копий «SSH_OPTS» + shell lib/ssh.sh фасад) | `SSH_OPTS`, `build_rsync_ssh_opts()`, CLI `--shell`/`--rsync-e` | core_deliverer, overlay_deliverer, remote_executor, channels ×2, lib/ssh.sh (python3 -m) |
| `ssl_certs.py` | Единый SoT openssl x509-примитивов (DevPlan 117 D21 + 118 C9 — дедупликация s3_ssl_cache._validate_cert + cert_orchestrator._is_cert_valid/_is_le_issuer; C9: cert_is_valid() единая комбинация parseable+LE+domain+expiry) | `cert_is_parseable()`, `cert_check_expiry()`, `cert_get_issuer()`, `cert_get_subject()`, `cert_is_le_issuer()`, `cert_subject_matches_domain()`, `cert_is_valid()`, `DEFAULT_OPENSSL_TIMEOUT=10`, `DEFAULT_EXPIRY_THRESHOLD=2592000` | s3_ssl_cache, cert_orchestrator, context_deployer |
| `stub_detection.py` | Единая is_stub-детекция ai-platform.yaml (DevPlan 116 B9 T4, U-28 — консолидирует дубль reconciler_projects + converge/reconciler) | `is_stub_ai_platform_yaml(path)` | reconciler_projects (wrapper is_stub_project), converge/projects (R3) |
| `subprocess_io.py` | Единый канон run_subprocess (DevPlan 118 C10 — дедупликация lifecycle/helpers/subprocess_io.py raise-семантики и converge/infra.py graceful-семантики; обе выражаются параметрами check/non_fatal; DevPlan 119 B4: +fatal_rc — exit=127 всегда fatal, lifecycle/helpers/subprocess_io.py УДАЛЁН) | `run_subprocess(cmd, *, timeout, check, non_fatal, fatal_rc)` | converge/infra (делегирование, check=False), lifecycle/helpers/{system,users,secrets,validation}.py + phases.py (B4) |
| `telegram_notifier.py` | Единый Telegram-клиент (заменяет 6 реализаций: 3 shell + 3 Python) | `send_telegram()` | notify-hook, hermes-agent, deploy |
| `timeouts.py` | Единый реестр таймаутов операционных политик (DevPlan 116 B5 T1, U-11 — единственный источник числовых timeout= в docker/ssh/healthcheck-домене) | `COMPOSE_UP_TIMEOUT`, `PULL_TIMEOUT`, `BUILD_TIMEOUT`, `HEALTHCHECK_POLL_TIMEOUT`, `SSH_CONNECT_TIMEOUT`, `DEPLOY_TIMEOUT`, `SSH_READ_TIMEOUT`, `RETRY_BACKOFF_SECONDS`, `IMAGE_CHECK_TIMEOUT`, `DOCKER_CMD_TIMEOUT`, `DOCKER_STOP_TIMEOUT`, `RSYNC_TIMEOUT`, `RETRY_COUNT` | docker_compose, ssh_opts, channels, docker_orchestrator, deploy_engine, reconciler, context_deployer, remote_executor, overlay_deliverer, context_promoter, orphan_reconciler, deploy_orchestrator |
| `vps_readiness.py` | VPS pre-flight проверки (SSH, forced-command ping, /opt/projects/, Docker) — Strangler-миграция vps-readiness.sh (DevPlan 105, дедупликация deploy.mk/CI pre-flight) | `check_vps_ready()`, CLI `NODE [--json|--quick]` | deploy.mk pre-flight, deploy-project.yml (через фасад core/lib/vps-readiness.sh) |
| `verbs.py` | Канонический verb-словарь forced-command диспетчера (DevPlan 116 B1 T1, U-56) — единый источник CANONICAL_VERBS + reserve-имен для проектов | `CANONICAL_VERBS`, `VERB_RESERVE`, `is_verb()` (validate_not_verb удалён — 0 ссылок, DevPlan 119 C3) | deploy/ssh_command_parser (classify_verb, DevPlan 118 D3), project_registry (validate_project_name), gate канала (T10) |
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
