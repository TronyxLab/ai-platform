$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Консолидация bootstrap pipeline — 32+ шагов → 14 фаз. Удаление dual state machine (state_machine.py vs steps.py дубликаты). Добавление hard dependency graph с precondition checks. Миграция state.json (23→14 ключей). Полная переработка shell-фасада (удаление индексной адресации step_1_*..step_18_*).
DESCRIPTION:           Bootstrap pipeline эволюционировал из линейного shell-скрипта (node-lifecycle.sh, 1301→241 LOC) в Python state machine. В процессе сохранились: дублирующиеся реализации шагов (state_machine.py + steps.py), два механизма чекпоинтов (shell .done → state.json через checkpoint_migration.py bridge), избыточное количество фаз (17 init + 9 update = 32+, не 23 как заявлено в DP-079), и 8 точек silent failure propagation где сбой фазы N не блокирует фазу N+1. DP-079 (95% complete) закрыл content_hash и docker_compose дублирование, но _step_deploy_context остался в steps.py (L751), state.json не мигрирован, shell-фасад всё ещё использует индексную адресацию (step_1_*..step_18_*) и checkpoint_step вызовы через checkpoint_migration.py bridge. Этот DevPlan завершает консолидацию: слияние фаз, миграция state, удаление дубликатов, precondition checks, полная переработка shell-фасада.
RATIONALE:             Пользователь наблюдает recurring drift в bootstrap: «секреты/бутстрап/токены-доступа — я же их уже унифицировал». Причина: после DP-078 и DP-079 bootstrap pipeline всё ещё содержит dual state machine, 8 silent failure точек, и state.json с неконсистентными ключами. Изменение в одном шаге не отражается в дубликате → «вернулся тот же баг». Сокращение с 32 до 14 фаз уменьшает complexity surface на 56% и устраняет все crossing points silent failure. Миграция state.json предотвращает даунтайм при обновлении production нод.
ACCEPTANCE_CRITERIA:
  - AC1: 32+ шага консолидированы в 14 фаз — 9 init + 5 update включая converge как отдельную фазу
  - AC2: _step_deploy_context удалён из steps.py (бизнес-логика только в state_machine.py)
  - AC3: checkpoint_migration.py удалён — все чекпоинты через state.json напрямую
  - AC4: Hard dependency graph: каждая фаза проверяет precondition перед запуском (pre_check метод)
  - AC5: 0 grep по "_step_" функциям в steps.py — все реализации в state_machine.py
  - AC6: 0 shell .done-файлов — все чекпоинты через state.json
  - AC7: 0 grep SHELL_TO_PYTHON_STEP — mapping удалён (заменён новыми 14 ключами)
  - AC8: migrate_state_to_phases() реализована — 23→14 ключей с composite hash
  - AC9: node-lifecycle.sh НЕ содержит step_1_*..step_18_* функций и checkpoint_step вызовов
  - AC10: make gate MODE=fast — зелёный
  - AC11: python -m pytest tests/ -v — все тесты проходят
  - AC12: Bootstrap dry-run на тестовой ноде — 14 фаз успешно
  - AC13: grouped-фазы поддерживают sub-checkpoint-ы (массив sub_steps в state.json)
  - AC14: Интеграционный тест покрывает сценарий частичного отказа φ4 (decrypt OK, ensure-passwords FAIL)
IMPLEMENTS:            Superposition Analysis 2026-07-28 — Проблема 2 (Bootstrap: 23 фазы, dual state machine) + Agent 4 S3 findings + DP-079 residual (95% → 100%) + state.json divergence audit + shell-facade audit
IMPACTS:               14+ файлов (3 CREATE, 9+ MODIFY, 2 DELETE). Подробно в §5 File Manifest.
REQUIRES:              DP-078 (done), DP-086 (Secrets parser — secrets_manager.py изменения могут конфликтовать с миграцией _init_service_passwords). Рекомендуется merge DP-086 перед стартом DP-087.
$END_ARTIFACT_CONTRACT

---

# DevPlan 087: Bootstrap Phase Consolidation (32→14)

**Severity:** HIGH — архитектурный дрейф (dual state machine), надёжность (8 silent failure точек), state.json divergence (23 keys → 14), shell-facade index-addressing (step_1_*..step_18_*)
**Created:** 2026-07-28
**Author:** Kilo (architect agent)
**Source:** Superposition S3, DP-079 residual analysis, Agent 4 S3 findings, state migration audit, shell-facade regression audit
**Sequenced:** AFTER DP-086 (Secrets), BEFORE DP-088 (NodeYaml)

---

## §1. Current State

### 32+ текущие фазы (N+1 discovery — pipeline содержит больше шагов чем 23, заявленных в DP-079)

```
INIT (известные индексы, ~17+):
  1. system-packages       2. docker-install        3. tor-proxy
  4. ssh-access             5. user-platform        6. user-ci-deploy
  7. projects-base          8. platform-dirs        9. docker-config
  9i. firewall             10. decrypt-secrets     11. read-node-yaml
  12. ghcr-auth             12b. ensure-secrets    13. install-acme
  14. deploy-modules       15. ssl-provision       16. verify-core
  17. verify-node-configs  17i. sudoers

UPDATE (9, не 6 как заявлено ранее):
  18. decrypt-secrets      19. read-node-yaml      20. ghcr-auth
  21. deploy-modules       22. ssl-provision       23. verify-core
  24. provision            25. deliver-overlays     26. provision_llm_keys
  27. healthcheck          28. converge             29. deploy_context

Не включены в группировку §2 (нуждаются в явном решении):
  - firewall (index 9 INIT) — не описан в target state
  - sudoers (index 17 INIT) — не описан
  - node_update (19 UPDATE) — отдельный трек, не бутстрап
  - converge (8 UPDATE) — критическая фаза, НЕ выделена отдельно; **должна быть явной фазой** в обоих режимах
  - audit_log (21 UPDATE) — отдельный трек
  - telegram (22 UPDATE) — отдельный трек

**Реальный pipeline (source: node-lifecycle.sh L59-L92):**
| Режим | Shell-шаги (checkpoint_step) | Python-шаги (_delegate --mode) |
|-------|------------------------------|-------------------------------|
| init  | ssh-access, apt-deps, tor-proxy, install-docker, docker-auth, user-platform, user-ci-deploy, projects-base, firewall, verify-core, verify-node-configs, decrypt-secrets, read-node-yaml, ghcr-auth, sudoers, metrics-cron (16 steps) | node_update, converge, audit_log, telegram, deploy_context (5 steps) |
| update| verify-core, provision, deliver-overlays, ssl-provision, deploy-modules, provision-llm-keys, healthcheck-all, converge, deploy-context (9 steps) | — (все через --run-step) |

**Итого**: Реальный pipeline содержит 32+ шага (16 init shell + 5 init python + 9 update shell + 2 дополнительных), не 17 init + 6 update = 23 как указано в DP-079. DP-087 консолидирует до 14 фаз с учётом converge и firewall.

### Dual state machine: who implements what
| Шаг | state_machine.py | steps.py | Статус |
|-----|:---:|:---:|--------|
| system-packages | ✅ _step_system_packages() | — | OK |
| docker-install | ✅ _step_install_docker() | — | OK |
| tor-proxy | ✅ _step_tor_proxy() | — | OK |
| ssh-access | ✅ _step_user_accounts() | — | OK (merged) |
| user-platform | ✅ _step_user_accounts() | — | OK (merged) |
| user-ci-deploy | ✅ _step_user_accounts() | — | OK (merged) |
| projects-base | ✅ _step_project_dirs() | — | OK (merged) |
| platform-dirs | ✅ _step_platform_dirs() | — | OK |
| docker-config | ✅ _step_docker_config() | — | OK |
| firewall | ❌ (shell script) | — | ⚠️ **NOT in Python** — needs migration (T1, T2) |
| decrypt-secrets | ✅ _decrypt_secrets() | — | OK |
| read-node-yaml | ✅ _read_node_yaml() | — | OK |
| ghcr-auth | ✅ _docker_registry_auth() | — | OK |
| ensure-secrets | ✅ _step_secrets_init() | — | ⚠️ **DUPLICATE candidate** — T19 |
| install-acme | ✅ _step_install_acme() | — | OK |
| deploy-modules | ✅ _step_deploy_modules() | ✅ _step_deploy_context() | ⚠️ **DUPLICATE** (steps.py:751) |
| ssl-provision | ✅ _ssl_cert_provision() | — | OK |
| verify-core | ✅ _verify_core() | — | OK (merge candidate) |
| verify-node-configs | ✅ _verify_node_configs() | — | OK (merge candidate) |
| sudoers | ❌ (shell script) | — | ⚠️ **NOT in Python** — low priority |
| converge | ❌ (shell + make + _delegate) | — | ⚠️ **NOT в явной фазе** — T4, T14 |
| provision, deliver_overlays, provision_llm_keys, healthcheck, deploy_context | ❌ (shell/CI) | — | ⚠️ **UPDATE-only steps** — некоторые вне state machine |

**Ключевая находка**: _step_deploy_context() существует в steps.py:751 — это дубликат логики из state_machine.py._step_deploy_modules(). DP-079 TASK-10 требовал удаления этой функции, но она осталась.
**Доп. находка**: _step_secrets_init() может быть дублирован — требуется верификация (T19).
**Доп. находка**: converge и firewall не имеют Python-реализаций — shell-логика в node-lifecycle.sh.

### 8 Silent Failure Propagation Points
1. decrypt-secrets fails → deploy-modules запускает контейнеры с пустыми секретами → crash-loop
2. ghcr-auth fails → docker pull падает на Docker Hub → rate-limit через 100 pulls/6h (не diagnosed)
3. tor-proxy fails → DPI-bypass silently degraded для российских серверов
4. read-node-yaml fails → все последующие фазы используют дефолтные/пустые значения
5. ensure-secrets fails → autogen пароли не сгенерированы → сервисы не могут стартовать
6. install-acme fails → ssl-provision пытается выпустить сертификаты без acme.sh → fail
7. verify-core fails → нет блокировки продолжения (warning-only)
8. verify-node-configs fails → нет блокировки (тот же паттерн)

### Shell facade: текущая архитектура (проблемная)

`node-lifecycle.sh` (241 LOC) содержит:
- **step_1_*..step_18_* shell-функции** — 18 функций с индексной адресацией (step_1_ssh_access, step_4_5_docker_auth, step_18_deploy_context)
- **update_step_1_*..update_step_9_* shell-функции** — 9 функций с индексной адресацией
- **checkpoint_step вызовы** — явные вызовы checkpoint_step "ssh-access" step_1_ssh_access в main()
- **checkpoint_migration.py bridge** — SHELL_TO_PYTHON_STEP mapping (16 записей) для конвертации имён
- **Двойное делегирование**: часть шагов через checkpoint_step (shell), часть через _delegate --mode init (Python bulk)

**Проблема**: Индексная адресация (step_1_*, step_4_5_*) НЕ соответствует новому BootstrapPhase enum. checkpoints через checkpoint_migration.py создают дополнительный слой косвенности. После консолидации в 14 фаз shell-фасад должен использовать BootstrapPhase enum напрямую, без индексов и без bridge.

---

## §2. Target State: 14 фаз

### INIT MODE (9 фаз)

```
ГРУППА 1: system (было 1-3 + firewall, merged)
  φ1. system-bootstrap
      ├── packages (was: 1)
      ├── docker-install (was: 2)
      ├── tor-proxy (was: 3)
      └── firewall (was: 9i)
      Precondition: root access
      ⚠️ Sub-checkpoint support: каждый подшаг имеет свой done-статус в state.json

ГРУППА 2: accounts (было 4-7, merged)
  φ2. user-accounts
      ├── ssh-access (was: 4)
      ├── platform-user (was: 5)
      ├── ci-deploy-user (was: 6)
      └── projects-base (was: 7)
      Precondition: φ1 OK
      Sub-checkpoint support

ГРУППА 3: platform (было 8-9, merged)
  φ3. platform-setup
      ├── platform-dirs (was: 8)
      └── docker-config (was: 9)
      Precondition: φ2 OK
      Sub-checkpoint support

ГРУППА 4: secrets (было 10, 12b + secrets-init, merged)
  φ4. secrets-provision
      ├── decrypt-secrets (was: 10)
      └── ensure-service-passwords (was: 12b + secrets-init.sh)
      Precondition: age-key exists, φ3 OK
      ⚠️ BLOCKS φ6 if fails (no secrets → containers crash-loop)
      ⚠️ Sub-checkpoints критичны: decrypt OK + ensure-passwords FAIL → restart только ensure

ГРУППА 5: node-config (было 11, 16, 17, merged)
  φ5. node-configuration
      ├── read-node-yaml (was: 11)
      ├── verify-core (was: 16)
      └── verify-node-configs (was: 17)
      Precondition: φ3 OK
      Sub-checkpoint support

ГРУППА 6: registry (было 12)
  φ6. registry-auth
      └── ghcr-auth (was: 12)
      Precondition: φ4 OK (needs secrets for docker login)
      ⚠️ BLOCKS φ8 if fails (no docker pull)

ГРУППА 7: certs (было 13, 15, merged)
  φ7. certificates
      ├── install-acme (was: 13)
      └── ssl-provision (was: 15)
      Precondition: φ5 OK (needs node.yaml domains)
      Sub-checkpoint support

ГРУППА 8: deploy (было 14)
  φ8. deploy-services
      └── deploy-modules (was: 14)
      Precondition: φ4 OK, φ6 OK, φ7 OK

ГРУППА 9: converge (было не в группировке)
  φ8.5 converge-services
      └── converge
      Precondition: φ8 OK
      ⚠️ Выделен в отдельную фазу — converge не был включён в группировку §2 ранее
```

### UPDATE MODE (5 фаз)

```
ГРУППА 10 (update): secrets (было 18)
  φ9. secrets-update
      └── decrypt-secrets
      Precondition: age-key exists

ГРУППА 11 (update): node-config (было 19)
  φ10. node-config-update
      └── read-node-yaml

ГРУППА 12 (update): registry (было 20)
  φ11. registry-update
      └── ghcr-auth

ГРУППА 13 (update): deploy (было 21-23, merged)
  φ12. deploy-update
      ├── deploy-modules (was: 21)
      ├── ssl-provision (was: 22)
      └── verify (was: 23)
      Precondition: φ9 OK, φ11 OK

ГРУППА 14 (update): converge
  φ13. converge-update
      └── converge
      Precondition: φ12 OK
```

**Итого**: 32→14 фаз (−56%). 8 silent failure точек → 5 explicit precondition BLOCKS + 4 с sub-checkpoints.

### Precondition model: разграничение двух механизмов

В консолидированном pipeline используется два различных механизма проверки:

1. **`precondition_check(phase)`** — проверяет ВНУТРИ-фазные условия:
   - `os.geteuid() == 0` (root access для system-level операций)
   - Файл существует (age-key, node.yaml)
   - Директория смонтирована
   - Сеть доступна (DNS resolution)
   Вызывается внутри `_execute_phase()` перед запуском бизнес-логики фазы.
   При failure → `PhasePreconditionError` с человеко-читаемым сообщением.

2. **`_phase_dependency_graph`** — проверяет МЕЖДУ-фазные зависимости:
   ```python
   _phase_dependency_graph: dict[Phase, set[Phase]] = {
       Phase.USER_ACCOUNTS:      {Phase.SYSTEM_BOOTSTRAP},   # φ2 ← φ1
       Phase.PLATFORM_SETUP:     {Phase.USER_ACCOUNTS},      # φ3 ← φ2
       Phase.SECRETS_PROVISION:  {Phase.PLATFORM_SETUP},     # φ4 ← φ3
       Phase.NODE_CONFIGURATION: {Phase.PLATFORM_SETUP},     # φ5 ← φ3
       Phase.REGISTRY_AUTH:      {Phase.SECRETS_PROVISION},  # φ6 ← φ4
       Phase.CERTIFICATES:       {Phase.NODE_CONFIGURATION},  # φ7 ← φ5
       Phase.DEPLOY_SERVICES:    {Phase.SECRETS_PROVISION,
                                  Phase.REGISTRY_AUTH,
                                  Phase.CERTIFICATES},       # φ8 ← φ4,φ6,φ7
       Phase.CONVERGE_SERVICES:  {Phase.DEPLOY_SERVICES},   # φ8.5 ← φ8
       Phase.SECRETS_UPDATE:     set(),                      # φ9 (no deps — update entry)
       Phase.NODE_CONFIG_UPDATE: set(),                      # φ10 (no deps)
       Phase.REGISTRY_UPDATE:    set(),                      # φ11 (no deps)
       Phase.DEPLOY_UPDATE:      {Phase.SECRETS_UPDATE,
                                  Phase.REGISTRY_UPDATE},    # φ12 ← φ9,φ11
       Phase.CONVERGE_UPDATE:    {Phase.DEPLOY_UPDATE},     # φ13 ← φ12
   }
   ```
   Проверяется в `_execute_phase()` ДО вызова `precondition_check()`.
   При violation → `PhaseDependencyError(non_satisfied_deps)`.

**Почему два механизма**: внутри-фазные условия (precondition_check) могут быть временными (сеть недоступна сейчас, но будет через минуту). Меж-фазные зависимости (dependency graph) структурны — φ6 registry-auth никогда не сможет выполниться до φ4 secrets-provision, независимо от времени. Разделение позволяет оператору видеть разные типы блокировок: «retry precondition» vs «execute missing dependency».

### Converge: явное решение по группировке

Converge НЕ был включён в §2 группировку исходного DevPlan, хотя существовал в обоих режимах (init и update). Причина: исторически converge запускается как отдельная make-команда после bootstrap. После консолидации converge становится полноценной фазой state machine:

| Режим | Было | Стало | Обоснование |
|-------|------|-------|-------------|
| INIT | converge через _delegate --mode init (шаг 20) | φ8.5 converge-services | converge — идемпотентный reconcile, явная фаза с precondition φ8 |
| UPDATE | converge через checkpoint_step (шаг 8) | φ13 converge-update | То же самое в update-режиме |

---

## §2.5 State.json Migration Strategy

### Проблема
Production-ноды имеют state.json с ~23 старыми ключами (по одному на каждый шаг до консолидации). Текущий SHELL_TO_PYTHON_STEP mapping в checkpoint_migration.py (16 записей) + Python-ключи (дополнительные из state_machine.py) образуют ~23 ключа:

INIT_STEPS (23): `system_packages`, `docker_install`, `tor_proxy`, `ssh_access`, `user_platform`, `user_ci_deploy`, `projects_base`, `platform_dirs`, `docker_config`, `firewall`, `decrypt_secrets`, `read_node_yaml`, `ghcr_auth`, `ensure_secrets`, `secrets_init`, `install_acme`, `sudoers`, `ssl_provision`, `node_update`, `converge`, `audit_log`, `telegram`, `deploy_modules`

UPDATE_STEPS (9): `decrypt_secrets`, `read_node_yaml`, `verify_core`, `provision`, `deliver_overlays`, `ssl_provision`, `deploy_modules`, `provision_llm_keys`, `healthcheck`, `converge`, `deploy_context`

**Примечание:** Shell-level имена `platform_dirs`, `docker_config`, `ssh_access`, `apt_deps` и др. в state.json представлены Python-ключами `platform`, `docker`, `ssh_access`, `system_packages` (нормализованы). Полный маппинг — в MIGRATION_MAP ниже.

После консолидации ключи меняются на 14 новых (по одной на фазу):
`system_bootstrap`, `user_accounts`, `platform_setup`, `secrets_provision`, `node_configuration`, `registry_auth`, `certificates`, `deploy_services`, `converge_services`, `secrets_update`, `node_config_update`, `registry_update`, `deploy_update`, `converge_update`

**Риск**: БЕЗ миграции повторный bootstrap перезапустит ВСЕ фазы на production → даунтайм.
state.json содержит done=true на старых ключах, но Python state machine ищет новые ключи → done=false → полный перезапуск.

### Решение: `migrate_state_to_phases()`

| Аспект | Решение |
|--------|---------|
| **Расположение** | `core/internal/checkpoint_migration.py` (расширить существующий модуль новым entrypoint) ИЛИ новый `core/internal/bootstrap/lifecycle/state_migration.py` |
| **Вызов** | Один раз при первом запуске bootstrap после обновления, перед чтением state.json. После миграции checkpoint_migration.py удаляется. |
| **Алгоритм** | Читает старый state.json → маппинг старых ключей на новые → композитный хеш → запись нового state.json |
| **Идемпотентность** | Если state.json уже содержит новые ключи — миграция не запускается (no-op) |
| **Пустые ключи** | Старый ключ отсутствует → новая фаза = pending (будет выполнена при первом bootstrap) |

### Composite hash rules

| Старые ключи (INIT + UPDATE) | Новая фаза | Composite logic |
|-------------|-----------|----------------|
| system_packages, docker_install, tor_proxy, firewall | φ1 system_bootstrap | ALL done → done; ANY failed/pending → pending; missing → pending |
| ssh_access, user_platform, user_ci_deploy, projects_base | φ2 user_accounts | ALL done → done |
| platform, docker, metrics_cron | φ3 platform_setup | ALL done → done; metrics_cron non-critical |
| decrypt_secrets, ensure_secrets, secrets_init | φ4 secrets_provision | ALL done → done (ensure_secrets/secrets_init могут отсутствовать — legacy) |
| read_node_yaml, init.verify_core, verify_node_configs | φ5 node_configuration | ALL done → done |
| ghcr_auth, docker_auth | φ6 registry_auth | ALL done → done; docker_auth migrated from φ1 |
| install_acme, ssl_provision | φ7 certificates | ALL done → done |
| deploy_modules, init.deploy_context | φ8 deploy_services | ALL done → done |
| init.converge | φ8.5 converge_services | Direct mapping |
| update.decrypt_secrets | φ9 secrets_update | Direct mapping (update-контекст) |
| update.read_node_yaml, update.verify_core | φ10 node_config_update | ALL done → done |
| update.ghcr_auth, provision, deliver_overlays, provision_llm_keys, healthcheck | φ11 registry_update | ALL done → done; UPDATE-only keys могут отсутствовать |
| update.deploy_modules, update.ssl_provision, update.verify_core, update.deploy_context | φ12 deploy_update | ALL done → done |
| update.converge | φ13 converge_update | Direct mapping |

### Функция migrate_state_to_phases() — сигнатура

```python
def migrate_state_to_phases(state: dict) -> dict:
    """
    Миграция state.json: ~23 старых ключа → 14 новых фазовых ключей.

    - Composite hash: все подшаги done → фаза done
    - Хотя бы один failed/pending → фаза pending
    - Пустой/отсутствующий ключ → фаза pending
    - Идемпотентна: если state уже содержит новые ключи → no-op
    - Вызывается ОДИН раз при первом запуске после обновления

    @invariants:
      - Не мутирует старые ключи (сохраняет для rollback)
      - Добавляет только новые ключи (state[phase.value] = {...})
      - composite_hash: all(sub_states) == done → phase.done = True
    """
    MIGRATION_MAP: dict[str, list[str]] = {
        # ── INIT phases (φ1-φ8.5) ──
        "system_bootstrap":   ["system_packages", "docker_install", "tor_proxy", "firewall"],
        # ↑ docker_auth moved to φ6 (DRIFT-MIG-006)
        "user_accounts":      ["ssh_access", "user_platform", "user_ci_deploy", "projects_base"],
        "platform_setup":     ["platform", "docker", "metrics_cron"],
        # ↑ platform_dirs→platform, docker_config→docker (DRIFT-MIG-003)
        "secrets_provision":  ["decrypt_secrets", "ensure_secrets", "secrets_init"],
        # ↑ secrets_init added (BLOCKER DRIFT)
        "node_configuration": ["read_node_yaml", "verify_core", "verify_node_configs"],
        # ↑ init.verify_core — см. дубликат в UPDATE
        "registry_auth":      ["ghcr_auth", "docker_auth"],
        # ↑ docker_auth moved from φ1 (DRIFT-MIG-006)
        "certificates":       ["install_acme", "ssl_provision"],
        "deploy_services":    ["deploy_modules", "deploy_context"],
        # ↑ init.deploy_context — см. update.deploy_context в φ12
        "converge_services":  ["converge"],
        # ↑ init.converge — см. update.converge в φ13

        # ── UPDATE phases (φ9-φ13) ──
        "secrets_update":     ["decrypt_secrets"],
        # ↑ update.decrypt_secrets — отдельный ключ в UPDATE контексте
        "node_config_update": ["read_node_yaml", "verify_core"],
        # ↑ update.verify_core — отдельный от init.verify_core
        "registry_update":    ["ghcr_auth", "provision", "deliver_overlays",
                               "provision_llm_keys", "healthcheck"],
        # ↑ provision/deliver_overlays/provision_llm_keys/healthcheck — UPDATE-only
        "deploy_update":      ["deploy_modules", "ssl_provision", "verify_core",
                               "deploy_context"],
        # ↑ update.deploy_context — отдельный от init.deploy_context
        #   update.verify_core дублирован в φ10 для совместимости
        "converge_update":    ["converge"],
        # ↑ update.converge — отдельный от init.converge
    }
    # ... composite logic
```

---

## §Rollback Plan

### Назначение
Восстановление работоспособности ноды после неудачной миграции state.json с 23→14 ключей. Миграция однократна и идемпотентна, но в случае бага в `migrate_state_to_phases()` (неверный composite hash, потеря ключей, логическая ошибка) требуется откат.

### Команда отката
```bash
git revert <merge-commit> && make bootstrap-node --force NODE=<name>
```

- `git revert <merge-commit>` — откатывает код до pre-migration состояния (старый state_machine.py, старый state.json формат, checkpoint_migration.py восстановлен)
- `make bootstrap-node --force` — запускает bootstrap с принудительным перезапуском всех фаз. Старые ключи в state.json используются как прежде
- После revert все чекпоинты читаются в старом формате. `.done`-файлы (если не были удалены) восстанавливаются через checkpoint_migration.py bridge

### Условия применения
| Симптом | Действие |
|---------|----------|
| После обновления bootstrap перезапускает все 14 фаз (done=false на всех новых ключах) | Проверить migrate_state_to_phases(): неверный composite hash → фикс + повторный bootstrap |
| После обновления bootstrap пропускает фазы (done=true на новых ключах при невыполненных старых) | **НЕМЕДЛЕННЫЙ REVERT** — риск silent пропуска фаз → даунтайм |
| migrate_state_to_phases() упал с KeyError | Фикс в коде + перезапуск (миграция идемпотентна — no-op если уже выполнена) |
| state.json повреждён (невалидный JSON) | Восстановить из бэкапа `/opt/platform/state.json.bak` → повторный bootstrap |

### Migration audit log
`migrate_state_to_phases()` логирует:
```
[IMP:9][MIGRATE] Mapping 23→14 keys: old_count=23, new_count=14, changed_keys=[...]
[IMP:8][MIGRATE] Composite hash: φ1=true (5/5 sub-steps done), φ2=false (3/4 done)
[IMP:10][MIGRATE] Migration complete — new state.json written to /opt/platform/state.json
```

При откате оператор проверяет audit log для确认 причину неверного маппинга.

---

## §3. Draft Code Graph

```
core/internal/bootstrap/lifecycle/state_machine.py   [MODIFY] — основной файл изменений
    ├── BootstrapPhase (enum) — 14 значений вместо 23
    ├── BootstrapState — новый класс с precondition_check()
    ├── _execute_phase(phase) — precondition → execute → checkpoint
    ├── _execute_grouped_phase(phase, sub_steps) — sub-checkpoint support
    ├── _resume_phase(phase) — partial failure recovery
    └── _phase_dependency_graph — dict[phase, set[prerequisite_phases]]

core/internal/bootstrap/lifecycle/phases.py           [CREATE] — извлечённая логика фаз
    ├── phase_system_bootstrap()   — φ1
    ├── phase_user_accounts()      — φ2
    ├── phase_platform_setup()     — φ3
    ├── phase_secrets_provision()  — φ4
    ├── phase_node_configuration() — φ5
    ├── phase_registry_auth()      — φ6
    ├── phase_certificates()       — φ7
    ├── phase_deploy_services()    — φ8
    ├── phase_converge_services()  — φ8.5
    ├── phase_secrets_update()     — φ9
    ├── phase_node_config_update() — φ10
    ├── phase_registry_update()    — φ11
    ├── phase_deploy_update()      — φ12
    └── phase_converge_update()    — φ13

core/internal/bootstrap/lifecycle/steps.py            [MODIFY → DELETE _step_deploy_context]
    └── _step_deploy_context() → УДАЛЁН
    └── _step_secrets_init() → верификация дубликата (T19)

core/internal/bootstrap/lifecycle/state_migration.py  [CREATE] — миграция state.json 23→14
    └── migrate_state_to_phases() — composite hash, idempotent, однократный вызов

core/internal/checkpoint_migration.py                 [DELETE]
    └── SHELL_TO_PYTHON_STEP mapping → удалён
    └── migrate_state_to_phases() перенесена в state_migration.py перед удалением

core/internal/bootstrap/node-lifecycle.sh            [MODIFY — ПОЛНАЯ ПЕРЕРАБОТКА]
    └── Удаление step_1_*..step_18_* функций (18 init + 9 update)
    └── Удаление checkpoint_step вызовов
    └── Новый фасад: вызов BootstrapPhase enum напрямую (не индексы)
    └── Удаление checkpoint_migrate_legacy() и checkpoint_reset_all()
    └── Целевой размер: <80 LOC (тонкий фасад)

core/lib/checkpoint.sh                                [MODIFY — замена вызовов]
    └── checkpoints → напрямую state.json (без checkpoint_migration.py)

core/internal/bootstrap/AGENTS.md                    [MODIFY — новая фазовая структура]

core/entrypoints/bootstrap.sh                        [MODIFY — обновить phase names]

core/entrypoints/node-update.sh                      [MODIFY — обновить update-фазы]

tests/unit/test_bootstrap_phases.py                   [CREATE] — unit-тесты precondition logic

tests/unit/test_state_machine.py                      [MODIFY] — рефакторинг 1193 LOC под 14 фаз

tests/test_node_lifecycle_static.py                   [MODIFY] — enum-based проверки shell-фасада
```

---

## §4. Wave Structure

### Wave 1: Foundation — извлечение фаз + precondition framework

| Task | Описание | Effort |
|------|----------|--------|
| **T1** | Создать BootstrapPhase enum: 14 значений (φ1-φ13 + φ8.5). Заменить все references к старым 23 именам в state_machine.py и steps.py. | 2 |
| **T2** | Создать phases.py: извлечь бизнес-логику каждой группы из state_machine.py._step_* методов в отдельные функции phase_*(). Включить firewall в φ1 и converge как φ8.5/φ13. | 4 |
| **T3** | Создать BootstrapState.precondition_check(): для каждой группы проверяет prerequisites. BLOCK если precondition не satisfied. | 2 |
| **T4** | Создать _phase_dependency_graph: dict с явными зависимостями для всех 14 фаз. _execute_phase() проверяет граф перед запуском. Включить φ8.5→φ8 и φ13→φ12. | 2 |

### Wave 2: Cleanup — удаление дубликатов + state migration + shell refactoring

| Task | Описание | Effort |
|------|----------|--------|
| **T5** | Удалить _step_deploy_context() из steps.py (L751). Проверить: все call sites уже используют state_machine.py версию. | 1 |
| **T6** | Удалить checkpoint_migration.py. Перенести migrate_state_to_phases() в state_migration.py. Удалить SHELL_TO_PYTHON_STEP mapping. | 1 |
| **T7** | **Полная переработка node-lifecycle.sh:** удаление step_1_*..step_18_* функций, удаление update_step_1_*..update_step_9_* функций, удаление checkpoint_step вызовов, удаление checkpoint_migrate_legacy(), новый фасад с прямым вызовом BootstrapPhase enum. Shell остаётся тонким фасадом <80 LOC. | 4 |
| **T8** | Удалить shell .done-файлы логику: grep по ".done" и "touch.*step" в core/internal/bootstrap/ и core/lib/checkpoint.sh | 1 |
| **T12** | Реализовать `migrate_state_to_phases()` в lifecycle/state_migration.py: чтение state.json, маппинг 23→14 ключей, composite hash, однократный вызов при обновлении. Полная таблица маппинга (см. §2.5). | 3 |
| **T15** | Финальное удаление индексной адресации в node-lifecycle.sh: верификация grep "step_1_\|step_18_\|checkpoint_step" → empty. Проверка что все call sites используют BootstrapPhase enum. | 2 |
| **T16** | Обновить core/lib/checkpoint.sh: замена вызовов checkpoint_migration на прямые state.json операции (read/write без bridge). | 2 |
| **T17** | Обновить _compute_step_hash() path_map под 14-фазную структуру: пути к новым phase_*() функциям в phases.py | 1 |
| **T19** | Удалить _step_secrets_init() дубликат: проверить ensure-secrets реализован и в state_machine.py, и в steps.py; удалить дублирующуюся имплементацию. | 1 |

### Wave 3: Documentation + Test update

| Task | Описание | Effort |
|------|----------|--------|
| **T13** | Обновить tests/unit/test_state_machine.py (1193 LOC): отрефакторить тесты под 14-фазную модель, удалить тесты для удалённых фаз (старые 23), добавить тесты для _phase_dependency_graph и _execute_grouped_phase() | 3 |
| **T14** | Интеграционный тест 14-фазного потока (dry-run): симуляция прохождения всех 14 фаз в INIT и UPDATE режимах. Проверка precondition BLOCK при failure. Проверка skip для уже done. Проверка _resume_phase() для φ4 partial failure. | 3 |
| **T18** | Обновить core/internal/bootstrap/AGENTS.md: новая фазовая структура (14 фаз), новые файлы (state_migration.py, phases.py), диаграмма зависимостей, схема sub-checkpoints | 1 |
| **T20** | Обновить tests/test_node_lifecycle_static.py: проверить соответствие shell-фасада новому BootstrapPhase enum. Тесты на то, что shell не вызывает step_1_* функции. | 2 |

### Wave 4: Unit tests + Gate

| Task | Описание | Effort |
|------|----------|--------|
| **T9** | tests/unit/test_bootstrap_phases.py: unit-тесты для precondition_check() каждого φ — успех, провал каждого precondition изолированно, проверка блокировки зависимых фаз. Покрытие: 14 success + 14×precondition failure paths. | 3 |
| **T10** | Gate test: tests/unit/test_bootstrap_no_duplicate_steps.py — fail если grep находит две _step_* функции с одинаковой ответственностью или _step_secrets_init() дубликат. | 1 |
| **T11** | make fix-gate + make gate MODE=fast + pytest tests/ -v | 1 |

---

### §4.1 Grouped-Phase Idempotency (Sub-Checkpoints)

grouped-фаза (φ1-φ5, φ7, φ12) объединяет несколько подшагов. Без sub-checkpoint-ов изменение одного скрипта перезапускает ВСЮ группу — даже если 3 из 4 подшагов не изменились.

**Требование**: Каждая grouped-фаза поддерживает массив `sub_steps` в state.json:

```json
{
  "system_bootstrap": {
    "done": true,
    "sub_steps": {
      "packages":      {"done": true, "hash": "abc123"},
      "docker_install": {"done": true, "hash": "def456"},
      "tor_proxy":     {"done": true, "hash": "ghi789"},
      "firewall":      {"done": true, "hash": "jkl012"}
    }
  }
}
```

**Логика выполнения**:
| Состояние sub_step | hash совпадает | Действие |
|-------------------|---------------|----------|
| done=true + hash совпадает | ✅ | SKIP — execution не вызывается |
| done=true + hash изменился | ❌ | EXECUTE — скрипт обновился |
| done=false | — | EXECUTE — не был выполнен/hash не валиден |
| sub_step отсутствует | — | EXECUTE — новый подшаг |

**Групповая логика**:
| Условие | phase.done |
|---------|-----------|
| Все sub_steps done, хеши совпадают | true |
| Все sub_steps done, но хеши изменились | false (+перезапуск изменившихся) |
| Хотя бы один sub_step failed/pending | false (+phase.failed_sub_step = имя) |

**Реализация**:
- `_execute_grouped_phase(phase, sub_steps: dict)` — базовый метод в state_machine.py
- `_compute_sub_step_hash(phase, sub_step_name)` — хеш для конкретного подшага
- `_skip_unchanged_sub_steps(phase, sub_steps)` — сравнение хешей

### §4.2 Error Recovery: Partial Failure Scenarios

**Сценарий: Частичный отказ φ4 (secrets-provision)**

```
φ4.secrets-provision:
  sub_step: decrypt-secrets → OK (done=true, hash=abc)
  sub_step: ensure-service-passwords → FAIL (exit code 1, done=false)
→ Результат: phase.done = false, phase.failed_sub_step = "ensure-service-passwords"
```

**Что происходит при перезапуске**:
1. pre φ5 check: phase.done=false → BLOCK (precondition not satisfied)
2. Оператор исправляет причину (например, age-ключ повреждён в ensure-service-passwords)
3. Повторный вызов bootstrap → `_execute_phase(φ4)`:
   - decrypt-secrets: sub_step.done=true + hash unchanged → SKIP (не требует ввода passphrase)
   - ensure-service-passwords: sub_step.done=false → EXECUTE
4. После успеха → phase.done=true → φ5 может запуститься

**Механизм `_resume_phase()`**:
```python
def _resume_phase(phase: Phase, state: dict) -> None:
    """Resume execution of a partially-failed grouped phase.
    
    - Анализирует sub_steps из state.json
    - Запускает только failed/pending подшаги
    - Не трогает успешные подшаги (done=true + hash unchanged = skip)
    - Не требует ручного сброса done-флагов
    """
    phase_state = state.get(phase.value, {})
    sub_steps = phase_state.get("sub_steps", {})
    for sub_name, sub_state in sub_steps.items():
        if sub_state.get("done") and not _hash_changed(phase, sub_name, sub_state.get("hash")):
            logger.info("[IMP:8][_resume_phase][%s] SKIP sub_step %s (unchanged)", phase.value, sub_name)
            continue
        _execute_sub_step(phase, sub_name)
```

**Другие сценарии частичного отказа**:

| Фаза | Частичный отказ | Recovery |
|------|----------------|---------|
| φ1 system-bootstrap | tor-proxy FAIL (DPI блокировка) | Restart → tor-proxy retry с новым транспортом; packages + docker + firewall skip |
| φ5 node-configuration | verify-core FAIL | Restart → verify-core retry; read-node-yaml skip (done+unchanged) |
| φ7 certificates | ssl-provision FAIL (ACME challenge timeout) | Restart → ssl-provision retry; install-acme skip |
| φ8.5 converge-services | converge FAIL | Restart → converge retry; deploy-modules skip |
| φ12 deploy-update | verify FAIL | Restart → verify retry; deploy-modules + ssl-provision skip |

---

## §5. File Manifest

### CREATE (4)
| Файл | Назначение |
|------|-----------|
| `core/internal/bootstrap/lifecycle/phases.py` | Извлечённая бизнес-логика 9 init + 5 update фаз. 14 функций phase_*(). |
| `core/internal/bootstrap/lifecycle/state_migration.py` | Миграция state.json: 23→14 ключей, composite hash, idempotent. Содержит migrate_state_to_phases(). |
| `tests/unit/test_bootstrap_phases.py` | Unit-тесты precondition logic для 14 фаз. |
| `tests/integration/test_bootstrap_dry_run.py` | Интеграционный тест 14-фазного dry-run: симуляция INIT и UPDATE потоков, проверка precondition BLOCK, skip done, _resume_phase() для частичного отказа φ4. |

### MODIFY (9)
| Файл | Изменение |
|------|----------|
| `core/internal/bootstrap/lifecycle/state_machine.py` | BootstrapPhase enum (14), precondition_check(), _phase_dependency_graph, _execute_grouped_phase(), _resume_phase(). ~50% кода перенесено в phases.py. Новый import. |
| `core/internal/bootstrap/lifecycle/steps.py` | Удалить _step_deploy_context() — единственная оставшаяся дублированная _step_* функция; удалить _step_secrets_init() дубликат |
| `core/internal/bootstrap/node-lifecycle.sh` | **Полная переработка:** удаление step_1_*..step_18_* функций, удаление update_step_1_*..update_step_9_* функций, удаление checkpoint_step вызовов, удаление checkpoint_migrate_legacy(), новый фасад на BootstrapPhase enum. Цель: <80 LOC. |
| `core/lib/checkpoint.sh` | Замена вызовов checkpoint_migration на прямые state.json операции. |
| `core/internal/bootstrap/AGENTS.md` | Обновление документации: новая фазовая структура (14 фаз, диаграмма), sub-checkpoints, error recovery. |
| `core/entrypoints/bootstrap.sh` | Обновить передачу phase names при вызове node-lifecycle.sh |
| `core/entrypoints/node-update.sh` | Обновить имена update-фаз |
| `tests/unit/test_state_machine.py` | Рефакторинг 1193 LOC под 14-фазную модель. Добавить тесты _phase_dependency_graph, _execute_grouped_phase(), _resume_phase(). |
| `tests/test_node_lifecycle_static.py` | Enum-based проверки shell-фасада: verify shell не использует step_1_* функции |

### DELETE (2)
| Файл | Причина |
|------|---------|
| `core/internal/checkpoint_migration.py` | Bridge удалён — все чекпоинты через state.json напрямую. Функциональность migrate_state_to_phases() перенесена в state_migration.py. |
| Shell .done-файлы | Все чекпоинты через state.json (ключи: 14 phase names). |

---

## §6. Acceptance Criteria (Detailed)

- [ ] AC1: 14 значений в BootstrapPhase enum (φ1-φ13 + φ8.5). grep "BootstrapPhase.*=" содержит 14 names.
- [ ] AC2: `grep "_step_deploy_context" core/internal/bootstrap/lifecycle/steps.py` → empty
- [ ] AC3: `grep "SHELL_TO_PYTHON_STEP" core/` → empty (checkpoint_migration.py удалён)
- [ ] AC4: `grep "_step_.*().*:" core/internal/bootstrap/lifecycle/steps.py` → empty (все _step_* в phases.py или state_machine.py)
- [ ] AC5: `grep -rn "\.done" core/internal/bootstrap/ | grep -v "\.done\.log\|\.done\.pid\|#\|//"` → empty (нет shell .done-файлов; только комментарии/документация исключены через grep -v)
- [ ] AC6: precondition_check() реализован для всех 14 фаз — unit-тесты покрывают success + каждый failure path
- [ ] AC7: _phase_dependency_graph содержит все 14 фаз с корректными prerequisite-связями (φ8.5→φ8, φ13→φ12)
- [ ] AC8: `migrate_state_to_phases()` реализована — 23→14 ключей, composite hash, idempotent (no-op на уже мигрированном state.json). `python3 -c "from core.internal.bootstrap.lifecycle.state_migration import migrate_state_to_phases; print('OK')"` — OK
- [ ] AC9: `grep "step_1_\|step_18_\|checkpoint_step\|checkpoint_migrate_legacy\|checkpoint_reset_all" core/internal/bootstrap/node-lifecycle.sh` → empty. Файл <80 LOC.
- [ ] AC10: `make gate MODE=fast` — зелёный
- [ ] AC11: `python -m pytest tests/unit/test_state_machine.py tests/unit/test_bootstrap_phases.py tests/test_node_lifecycle_static.py -v` — все PASS
- [ ] AC12: Bootstrap dry-run на тестовой ноде — 14 фаз (не 23) выполняются корректно
- [ ] AC13: grouped-фазы (φ1-φ5, φ7, φ12) поддерживают sub_checkpoints в state.json — unit-тест проверяет restart после частичного отказа с 3/4 подшагов done
- [ ] AC14: Интеграционный тест (T14) симулирует частичный отказ φ4 (decrypt OK + ensure-passwords FAIL) и проверяет корректный resume через _resume_phase()

---

## §7. Design Decisions

### DD1: Почему 14 фаз, а не меньше?
Каждая из 14 фаз представляет атомарную группу с единым precondition. Дальнейшее слияние (например, φ4+φ6 — secrets+registry) создало бы фазу с разными failure mode'ами (secrets fail ≠ registry fail). 14 — это минимальное число, где каждая фаза имеет ровно один failure mode и ровно один precondition. Добавление converge (φ8.5, φ13) и firewall (в φ1) даёт +2 фазы к изначальным 12, plan-заголовок обновлён с 23→12 на 32→14.

### DD2: Почему phases.py — отдельный файл, а не методы state_machine.py?
state_machine.py уже 2115 LOC. Извлечение бизнес-логики фаз в отдельный модуль:
- Разделяет оркестрацию (state_machine.py: execute_phase, checkpoints, dependency graph) и реализацию (phases.py: конкретные шаги каждой фазы)
- Позволяет unit-тестировать каждую фазу изолированно, без инициализации всей state machine
- Следует Single Responsibility Principle

### DD3: Почему precondition BLOCKS, а не WARN?
Текущее поведение с 8 silent failure propagation точками — это источник production-багов (P0 mkcert certs survived bootstrap, nginx 502 fix). Explicit BLOCK лучше, чем silent degradation. Если precondition не выполнен — стоп с читаемой ошибкой. Оператор может перезапустить после исправления причины. Idempotency гарантирует, что уже выполненные фазы не перезапустятся.

### DD4: Удаление checkpoint_migration.py — безопасно?
SHELL_TO_PYTHON_STEP mapping существует только для обратной совместимости со старыми .done-файлами и bridge для checkpoint_step вызовов. После удаления shell .done-файлов (T8), полной переработки node-lifecycle.sh (T7) и стандартизации на state.json — bridge больше не нужен. Функциональность migrate_state_to_phases() перенесена в state_migration.py и вызывается один раз при обновлении.

### DD5: Converge — отдельная фаза, не скрытая в группировке
Converge (φ8.5 init, φ13 update) — это идемпотентный reconcile ноды с desired state. Он не является «шагом» в линейном смысле — это операция, которая может запускаться многократно и независимо. Выделение в отдельную фазу позволяет:
- Сохранить converge в обоих режимах (INIT и UPDATE) с разными preconditions
- Не блокировать другие фазы при converge (он может работать параллельно)
- Чётко разделить deploy (доставка модулей) и converge (проверка соответствия desired state)

Почему converge не был в исходной группировке §2: исторически converge существовал как скрытый шаг внутри _delegate --mode init (индекс 20) и как checkpoint_step в update (индекс 8). После консолидации converge становится полноценной явной фазой state machine.

### DD6: Sub-checkpoint-ы для grouped-фаз (Idempotency)
Без sub-checkpoint-ов изменение одного скрипта в grouped-фазе (например, tor-proxy конфиг в φ1) перезапускает ВСЕ подшаги группы — включая apt update и docker install. Sub-checkpoint-ы через массив sub_steps в state.json решают эту проблему:
- Каждый подшаг имеет свой done-статус и content hash
- При перезапуске: unchanged + done подшаги SKIP, изменившиеся EXECUTE, failed EXECUTE
- Производительность: типичный bootstrap ускоряется в 3-5 раз после первого прохода

Реализация: `_execute_grouped_phase(phase, sub_steps)` — единый метод для всех grouped-фаз в state_machine.py. Хранение: sub_steps как вложенный dict в state.json.

### DD7: Error Recovery для grouped-фаз
Частичный отказ grouped-фазы (например, φ4: decrypt-secrets OK, ensure-service-passwords FAIL) не требует ручного сброса state.json. Механизм `_resume_phase()`:
- Анализирует sub_steps массив в state.json
- Находит failed/pending подшаги (done=false или hash изменился)
- Запускает только их, минуя успешные
- Не требует ввода age passphrase повторно для decrypt-secrets

Без этого механизма оператор должен вручную очищать state.json → риск ошибиться и перезапустить decrypt-secrets, что потребует повторного ввода age passphrase на production-ноде.

---

## §8. Implementation Commands

```
# === WAVE 1: Foundation ===
coder implement DevPlan 087 Wave 1:
  T1 (BootstrapPhase enum 14 values: φ1-φ13 + φ8.5),
  T2 (phases.py — извлечение 9+5 фаз, включить firewall + converge),
  T3 (precondition_check()),
  T4 (_phase_dependency_graph + _execute_phase)

# Verify Wave 1
python3 -m pytest tests/unit/test_bootstrap_phases.py -v

# === WAVE 2: Cleanup + State migration + Shell refactoring ===
coder implement DevPlan 087 Wave 2:
  T5 (удалить _step_deploy_context из steps.py),
  T6 (удалить checkpoint_migration.py; migrate_state_to_phases в state_migration.py),
  T7 (ПОЛНАЯ переработка node-lifecycle.sh: удалить step_1_*..step_18_*, checkpoint_step, фасад на enum),
  T8 (удалить .done логику; очистить checkpoint.sh),
  T12 (migrate_state_to_phases() — 23→14 composite hash),
  T15 (верификация: grep "step_1_\|checkpoint_step" → empty),
  T16 (обновить checkpoint.sh — прямые state.json операции),
  T17 (обновить _compute_step_hash() path_map),
  T19 (удалить _step_secrets_init дубликат)

# Verify Wave 2
echo "=== Verify: no step_1_* functions ==="
grep "step_1_\|step_18_\|checkpoint_step\|checkpoint_migrate_legacy" core/internal/bootstrap/node-lifecycle.sh || echo "PASS"
echo "=== Verify: no SHELL_TO_PYTHON_STEP ==="
grep "SHELL_TO_PYTHON_STEP" core/ || echo "PASS"
echo "=== Verify: no .done files ==="
grep -rn "\.done" core/internal/bootstrap/ | grep -v "\.done\.log\|\.done\.pid\|#\|//" || echo "PASS"
echo "=== Verify: state migration importable ==="
python3 -c "from core.internal.bootstrap.lifecycle.state_migration import migrate_state_to_phases; print('PASS')"

# === WAVE 3: Documentation + Test update ===
coder implement DevPlan 087 Wave 3:
  T13 (test_state_machine.py refactor — 14 фаз),
  T14 (integration test dry-run — 14 фаз, partial failure φ4),
  T18 (AGENTS.md update),
  T20 (test_node_lifecycle_static.py update)

# === WAVE 4: Tests + Gate ===
coder implement DevPlan 087 Wave 4:
  T9 (test_bootstrap_phases.py — precondition 14 фаз),
  T10 (gate test no duplicate steps),
  T11 (fix-gate + gate)

# Verify Waves 3+4
echo "=== Verify: all unit tests ==="
python3 -m pytest tests/unit/test_bootstrap_phases.py \
                   tests/unit/test_state_machine.py \
                   tests/test_node_lifecycle_static.py -v
echo "=== Verify: integration dry-run ==="
python3 -m pytest tests/integration/test_bootstrap_dry_run.py -v
echo "=== Make gate ==="
make fix-gate && make gate MODE=fast

# Final verification
echo "=== Final: 14 BootstrapPhase values ==="
python3 -c "
from core.internal.bootstrap.lifecycle.state_machine import BootstrapPhase
phases = list(BootstrapPhase)
assert len(phases) == 14, f'Expected 14 phases, got {len(phases)}'
print(f'PASS: {len(phases)} phases in enum')
"
echo "=== Final: all phase functions importable ==="
python3 -c "
from core.internal.bootstrap.lifecycle.phases import (
    phase_system_bootstrap, phase_user_accounts, phase_platform_setup,
    phase_secrets_provision, phase_node_configuration, phase_registry_auth,
    phase_certificates, phase_deploy_services, phase_converge_services,
    phase_secrets_update, phase_node_config_update, phase_registry_update,
    phase_deploy_update, phase_converge_update
)
print('PASS: All 14 phase functions importable')
"
echo "=== Final: state migration dry-run ==="
python3 -c "
from core.internal.bootstrap.lifecycle.state_migration import migrate_state_to_phases
test_state = {'ssh_access': {'done': True}, 'apt_deps': {'done': True}}
result = migrate_state_to_phases(test_state)
assert 'system_bootstrap' in result
print(f'PASS: state.json migrated, new keys: {[k for k in result.keys() if k not in test_state]}')
"
```

$END_DEVPLAN
