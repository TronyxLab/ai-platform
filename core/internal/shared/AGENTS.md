# GREP_SUMMARY: AGENTS.md, shared, inventory, node-yaml, docker-compose, audit-logger, ssh-parser, telegram, docker-auth, age-key, node-detect, vps-readiness, crypto, content-hash, secrets-env, deploy-paths, platform-deliver, project-registry, exceptions
# STRUCTURE: ┌контракт области┐ → ◇ инвентарь 17 модулей (таблица) → ◇ правила добавления → ◇ запреты → ⎋ cross-refs
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
# endregion MODULE_CONTRACT

# core/internal/shared/ — инвентарь модулей

| Модуль | Назначение | Ключевой API | Потребители |
|--------|-----------|--------------|-------------|
| `age_key.py` | Compat-re-export шим — детекция AGE-ключа делегирована в node_detect.py (DevPlan 104) | `detect_age_key()` (re-export), CLI | decrypt-secrets, bootstrap, node-update |
| `audit_logger.py` | Единый JSON-lines audit логгер (заменяет прямой file.write и shell audit_logging.sh) | `write_audit_entry()`, `read_audit_log()`, CLI `write/read --log-file` | context_deployer, deploy, lib/audit.sh |
| `content_hash.py` | SHA256 content-hash для идемпотентности bootstrap (state.json sub-steps) | `compute_step_hash()`, `step_hash_changed()` | state_machine, content-hash.sh |
| `crypto.py` | APR1/htpasswd хэширование (openssl passwd -apr1, детерминизм через salt) | `hash_apr1()`, `generate_htpasswd_entry()`, CLI `hash/entry [--salt]` | lib/secrets.sh, secrets_manager |
| `deploy_paths.py` | Канонический реестр путей доставки кода (SoT для удаления deprecated путей) | `get_canonical_paths()`, `DEPRECATED_DEPLOY_PATHS` | core-deploy CI, deploy |
| `docker_auth.py` | Единый Docker registry auth (заменяет 5 дублирующихся точек) | `docker_login()`, `ghcr_login()`, `configure_docker_auth()` | bootstrap registry-auth, phases.py |
| `docker_compose.py` | Shared compose-операции: pull/build/up/healthcheck_poll | `docker_compose_pull()`, `docker_compose_build()`, `docker_compose_up()`, `healthcheck_poll()` | context_deployer, docker_orchestrator, DeployEngine |
| `exceptions.py` | Типизированная иерархия ошибок платформы | `PlatformError`, `ConfigValidationError`, `ConfigNotFoundError`, `ConfigParseError`, ... | все Python-модули |
| `node_detect.py` | Детекция AGE-ключа (env-цепочка) + авто-детекция имени ноды из node-configs (DevPlan 104 — дедупликация bootstrap/converge/node-update) | `detect_age_key()`, `auto_detect_node_name()`, CLI `--detect-age-key` / `--detect-node-name` | bootstrap, converge, node-update |
| `node_yaml.py` | Единый фасад чтения node.yaml (мутации с TRAP[BUG] 2026-07-30) | `NodeYaml(path).get(...)`, CLI `--get/--set` | vhost_renderer, reconciler, converge, scaffold |
| `platform_deliver.py` | Сборка verb-команды forced-command platform-deliver (замена дублирующих строк) | `build_platform_deliver_verb()` | deploy, orchestrator_cli |
| `project_registry.py` | Реестр проектов: регистрация/дерегистрация/список поверх NodeYaml | `validate_project_name()`, `register/unregister/list` | DeployEngine, scaffold, lifecycle |
| `secrets_env_parser.py` | Единый парсер secrets.env (заменяет 7 inline-парсеров) | `parse()`, `write()`, `merge()`, `export_shell()` | decrypt-secrets, secrets-init, bootstrap |
| `ssh_command_parser.py` | Парсер SSH_ORIGINAL_COMMAND (заменяет 2 дублирующихся парсера) | `parse_ssh_command()`, `classify_verb()` | deploy forced-command, deploy.sh |
| `telegram_notifier.py` | Единый Telegram-клиент (заменяет 6 реализаций: 3 shell + 3 Python) | `send_telegram()` | notify-hook, hermes-agent, deploy |
| `vps_readiness.py` | VPS pre-flight проверки (SSH, forced-command ping, /opt/projects/, Docker) — Strangler-миграция vps-readiness.sh (DevPlan 105, дедупликация deploy.mk/CI pre-flight) | `check_vps_ready()`, CLI `NODE [--json|--quick]` | deploy.mk pre-flight, deploy-project.yml (через фасад core/lib/vps-readiness.sh) |
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
