$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Унификация 6+ независимых путей деплоя в единый Python DeployOrchestrator. Удаление shell-фасада deploy-project.sh. Введение абстракции delivery channel (SCP/rsync vs ForcedCommand).
DESCRIPTION:           Текущий деплой имеет 6+ параллельных реализаций: DeployEngine (Python, Docker lifecycle), PayloadDeliverer (Python, tar+SSH forced-command), context_deployer.py (Python, оркестрация проектов), deploy-project.sh (Shell, частично мигрирован), docker_orchestrator.py (Python, групповой compose), deploy-modules.sh (Shell, bootstrap deploy). Три Python-модуля не имеют общего фасада. Два канала доставки (SCP vs forced-command) реализованы независимо. DP-081 документировал 6 канонических путей, но не унифицировал Python-движки. Этот DevPlan создаёт DeployOrchestrator — единый typed фасад, инкапсулирующий все deploy-операции.
RATIONALE:             6+ путей деплоя создают ситуацию, где багфикс в одном пути не применяется к другим. DP-081 задокументировал проблему (deploy-paths.md + gate test), но не решил её архитектурно. DeployOrchestrator устраняет дублирование: один вызов deploy() работает через любой delivery channel, с единым аудит-логом, rollback'ом и healthcheck-поллом.
ACCEPTANCE_CRITERIA:
  - AC1: DeployOrchestrator — единый Python класс с deploy()/rollback()/status()/remove()
  - AC2: DeployEngine + PayloadDeliverer → модули, используемые DeployOrchestrator (не самостоятельные entry points)
  - AC3: deploy-project.sh → удалён (бизнес-логика в DeployOrchestrator)
  - AC4: context_deployer.py → делегирует DeployOrchestrator (не свою deploy-логику)
  - AC5: Абстракция DeliveryChannel: SCPChannel, ForcedCommandChannel — общий интерфейс deliver(payload)
  - AC6: Аудит-логирование унифицировано: audit_logging.sh + deploy_engine.py audit_write → AuditLogger Python
  - AC7: make gate MODE=fast — зелёный
  - AC8: python -m pytest tests/ -v — все тесты проходят
  - AC9: Deploy dry-run на тестовой ноде — успешный деплой через новый orchestrator
IMPLEMENTS:            Superposition Analysis 2026-07-28 — Проблема 4 (Деплой: 6+ путей) + Agent 3 Parallel Branches Report (Deploy domain)
IMPACTS:               24 файла (11 CREATE, 10 MODIFY, 3 DELETE). Подробно в File Manifest.
REQUIRES:              DP-081 (deploy-paths.md документирован, ssh_command_parser.py существует), DP-088 (NodeYaml — DeployOrchestrator будет использовать NodeYaml.load()). Рекомендуется merge DP-088 перед стартом DP-089.
$END_ARTIFACT_CONTRACT

---

# DevPlan 089: Deploy Orchestrator Unification

**Severity:** HIGH — 6+ параллельных путей деплоя, дублирование бизнес-логики
**Created:** 2026-07-28
**Author:** Kilo (architect agent)
**Source:** Superposition Analysis, Parallel Branches Report (Agent 3 — Deploy domain)
**Sequenced:** AFTER DP-088 (NodeYaml), BEFORE DP-090 (Manifest)

---

## §1. Current State

### 6+ deploy paths (детальный инвентарь из Agent 3)

| Модуль | Язык | Роль | LOC | Entry point |
|--------|------|------|-----|-------------|
| `DeployEngine` (deploy_engine.py) | Python | Docker compose lifecycle на VPS | ~900 | forced-command `platform-deliver` |
| `PayloadDeliverer` (payload_deliverer.py) | Python | tar доставка через forced-command | ~500 | forced-command |
| `context_deployer.py` | Python | Оркестрация проектов контекста | ~700 | bootstrap step / `make deploy-context` |
| `deploy-project.sh` | Shell | Shell-фасад (частично мигрирован) | ~400 | CI / emergency direct SSH |
| `docker_orchestrator.py` | Python | Групповой docker compose | ~300 | state_machine.py._step_deploy_modules() |
| `deploy-modules.sh` | Shell | Деплой модулей в bootstrap pipeline | ~220 | bootstrap step |
| `overlay_deliverer.py` | Python | Vhost overlay + rsync core sync | ~250 | bootstrap step |
| `reconcile-projects.sh` | Shell | Сверка проектов | ~80 | `make converge` |
| `reconciler_projects.py` | Python | Дублирует deploy + deliver логику | ~700 | `make converge` / internal |

### Текущие проблемы

1. **Три Python-движка без общего фасада**: DeployEngine, PayloadDeliverer, context_deployer — все делают deploy, но не через общий интерфейс
2. **Два канала доставки без абстракции**: SCP/rsync (bootstrap) и forced-command (CI) — разная обработка ошибок, разное логирование
3. **Shell-фасад deploy-project.sh всё ещё жив**: содержит хук-триггеры, тегирование образов, вызов invoke_module_interface
4. **Двойной healthcheck**: context_deployer делает healthcheck после DeployEngine, который уже сделал свой healthcheck внутри
5. **Аудит размазан**: audit_logging.sh (shell) + deploy_engine.py.audit_write() (Python) — два формата, две системы
6. **reconciler_projects.py (700+ LOC) — 9-й путь деплоя**: имеет собственные `deliver_payload()` (L346) и `deploy_project()` (L480) — полные дубликаты PayloadDeliverer + DeployEngine. `make converge` использует этот модуль, а не DeployOrchestrator.
7. **setup-node.sh хардкодит deploy-project.sh**: строки 94, 112 в authorized_keys — `command="deploy-project.sh"`. При удалении deploy-project.sh bootstrap сломается.
8. **5 функций deploy-project.sh не мигрированы**: notify_hook, finalize_deploy (trap EXIT), tag_current + prune_old_images, PLATFORM_DEPLOY_DIRECT env var, MAX_WAIT_SEC/KEEP_IMAGES конфигурация. Все ещё живут в shell.
9. **Rollback неработоспособен без DeployHistory**: `rollback()` нигде не берёт предыдущее состояние — нет снепшотов до деплоя.

---

## §2. Target Architecture

```
DeployOrchestrator (core/internal/deploy/orchestrator.py)  [CREATE]
    ├── deploy(project_name, channel: DeliveryChannel) → DeployResult
    ├── deploy_many(project_names: List[str], channel) → List[DeployResult]
    ├── rollback(project_name, snapshot_id: str | None = None) → DeployResult
    ├── status(project_name) → ProjectStatus
    └── remove(project_name, purge: bool = False) → DeployResult
         purge=True — удаляет compose volumes (docker compose down -v)

    DeployResult: Union[[DEPLOYED, FAILED, PARTIAL, SKIPPED], error_info, duration_s]
    Node specification: { name: str, host: str, user: str, port: int, platform_dir: str }
    Concurrent guard: file lock /var/lock/platform-deploy-{project}.lock

    Использует:
    ├── DeliveryChannel (ABC — абстрактный канал доставки)
    │   ├── Payload dataclass: { tar_path: Path, project_name: str, version: str, metadata: dict }
    │   ├── DeliveryResult dataclass: { success: bool, stdout: str, stderr: str, exit_code: int, duration_s: float }
    │   ├── timeout: 600s (configurable via PLATFORM_DEPLOY_TIMEOUT env var)
    │   ├── retry: 2 retries + exponential backoff (initial backoff 5s, factor 2×)
    │   ├── auth: SSH key-based (no password auth); SCPChannel uses SSH agent forwarding
    │   ├── SCPChannel: deliver(payload) через scp/rsync + remote-cmd.sh unpack
    │   └── ForcedCommandChannel: deliver(payload) через SSH forced-command
    ├── DeployEngine (Docker lifecycle) — существующий, рефакторинг под modular API
    ├── PayloadDeliverer (tar assembly) — существующий, рефакторинг: assemble_payload() → Payload
    ├── AuditLogger (унифицированный Python-логгер)
    │   ├── Формат: JSON lines (ndjson)
    │   ├── Путь: /var/log/platform/audit.log
    │   ├── Permissions: chmod 640, chown :adm (совместимость с группой adm)
    │   ├── Поля: timestamp, level, operation, project, channel, result, duration, snapshot_id
    │   ├── Output: файл + syslog (facility LOCAL6)
    │   └── Объединяет audit_logging.sh + deploy_engine.py.audit_write()
    ├── HealthcheckPoller (общий healthcheck)
    │   ├── Протокол: HTTP GET /health → 200 (web-сервисы); docker inspect → Running (workers)
    │   ├── timeout: 30s per check
    │   ├── retry interval: 10s
    │   ├── max retries: 6 (total ~60s polling window)
    │   └── Извлекает логику из context_deployer._shared_healthcheck_poll() + docker_compose.py
    └── DeployHistory (новый модуль, T6.5)
        ├── Хранилище: /opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json
        ├── Snapshot format: { project, version, timestamp, compose_state, health_status, payload_hash }
        ├── Retention: keep last 10 snapshots (prune_old_images on deploy)
        ├── Используется: rollback() восстанавливает compose_state из snapshot
        └── File lock: /var/lock/platform-deploy-{project}.lock (fcntl.flock)

### VPS-side forced-command receiver

После удаления deploy-project.sh VPS-side forced-command entrypoint заменяется на `DeployOrchestrator.receive()`:
  - `setup-node.sh` authorized_keys → `command="python3 -m core.internal.deploy.orchestrator_cli receive"` (строки 94, 112; T13.0)
  - `state_machine.py:1116` → `forced_command = f'command="python3 -m core.internal.deploy.orchestrator_cli receive {node_name}",restrict'` (T13.0)
  - `deploy.sh` (VPS-side) → вызывает `DeployOrchestrator.receive()` и пробрасывает exit code (T10)

`DeployOrchestrator.receive()` — статический метод, принимающий Payload через stdin (tar):
  1. Читает tar-поток из stdin → распаковывает в staging-директорию
  2. Парсит Payload (project_name, version, metadata)
  3. Вызывает `self.deploy()` для проекта
  4. Возвращает `DeployResult` (JSON) в stdout
  5. Exit code: 0 = SUCCESS, 1 = PARTIAL/FAILED

Этот entrypoint заменяет deploy-project.sh во всех трёх точках forced-command chain:
  - setup-node.sh:94,112 (authorized_keys provisioning)
  - state_machine.py:1116 (converge — рекреация authorized_keys)
  - deploy.sh:78,83,95 (exec deploy-project.sh)

### Клиентские потребители (client-side, вызывают DeployOrchestrator.deploy() через DeliveryChannel):

    ├── deploy.sh (client-side) → DeployOrchestrator.deploy() через ForcedCommandChannel (T10)
    ├── context_deployer.py → DeployOrchestrator.deploy() через SCPChannel (T11)
    ├── state_machine.py._step_deploy_modules() → DeployOrchestrator.deploy_many() через SCPChannel (T12)
    └── reconciler_projects.py → МИГРИРУЕТ на DeployOrchestrator.deploy() (T11.5)
```

---

## §3. Wave Structure

### Wave 1: Foundation — Orchestrator + Channels + Audit

| Task | Описание | Effort |
|------|----------|--------|
| **T1** | Создать DeliveryChannel ABC: deliver(payload: Payload) → DeliveryResult | 2 |
| **T2** | SCPChannel: impl DeliveryChannel через scp/rsync. Использует ssh_command_parser из DP-081. | 2 |
| **T3** | ForcedCommandChannel: impl DeliveryChannel через SSH forced-command. Использует ssh_command_parser. | 2 |
| **T4** | Создать AuditLogger: унифицированный Python-логгер аудита. Объединяет форматы audit_logging.sh + deploy_engine.py.audit_write(). | 2 |
| **T5** | Создать HealthcheckPoller: общий healthcheck-полл. Извлекает логику из context_deployer._shared_healthcheck_poll() + docker_compose.py.healthcheck_poll(). | 2 |
| **T6** | Создать DeployOrchestrator: deploy()/deploy_many()/rollback()/status()/remove(). Unit-тесты. | 4 |
| **T6.5** | Создать DeployHistory storage: снепшоты в `/opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json`. Формат: { project, version, timestamp, compose_state, health_status, payload_hash }. Retention: keep last 10. rollback() читает snapshot. File lock: `/var/lock/platform-deploy-{project}.lock`. | 2 |
| **T6.6** | Создать orchestrator_cli.py: CLI entrypoint для `python3 -m core.internal.deploy.orchestrator_cli receive/deploy-many`. Команда `receive` — читает Payload из stdin (tar), вызывает DeployOrchestrator.receive(). Команда `deploy-many` — аргументы project_names + channel, вызывает DeployOrchestrator.deploy_many(). | 1 |

### Wave 2: Refactor — адаптация существующих движков

| Task | Описание | Effort |
|------|----------|--------|
| **T7** | DeployEngine → адаптировать: вызывается из DeployOrchestrator.deploy(), не как standalone. API: deploy_compose(project_path) → DeployResult | 3 |
| **T8** | PayloadDeliverer → адаптировать: assemble_payload() как метод, вызываемый DeployOrchestrator. API: assemble_payload(project_path) → Payload | 2 |
| **T9** | docker_orchestrator.py → адаптировать: deploy_docker_group() → DeployOrchestrator.deploy_many() | 2 |

### Wave 3: Consumer Migration + Shell Removal

| Task | Описание | Effort |
|------|----------|--------|
| **T10** | deploy.sh → DeployOrchestrator.deploy() через ForcedCommandChannel | 2 |
| **T11** | context_deployer.py → DeployOrchestrator.deploy() через SCPChannel. Удалить внутреннюю deploy-логику. | 2 |
| **T11.5** | reconciler_projects.py → мигрировать deliver_payload() (L346) и deploy_project() (L480) на DeployOrchestrator.deploy(). Удалить дубликаты. | 2 |
| **T12** | state_machine.py._step_deploy_modules() → DeployOrchestrator.deploy_many() | 2 |
| **T13.0** | setup-node.sh → обновить хардкод `command="deploy-project.sh"` в authorized_keys (строки 94,112) на новый путь DeployOrchestrator CLI. | 1 |
| **T13** | deploy-project.sh → УДАЛИТЬ. Бизнес-логика перенесена в DeployOrchestrator. 8 не-мигрированных функций: (1) notify_hook → DeployOrchestrator.post_deploy_hook(); (2) finalize_deploy (trap EXIT) → try/finally в DeployOrchestrator.deploy(); (3) tag_current + prune_old_images → DeployOrchestrator._tag_deployed()/_prune_images(); (4) PLATFORM_DEPLOY_DIRECT env var → config в DeployOrchestrator.__init__(); (5) MAX_WAIT_SEC/KEEP_IMAGES → конфигурация DeployOrchestrator; (6) `_rollback_on_error()` (trap ERR) → try/finally + rollback в DeployOrchestrator.deploy(); (7) `_trigger_deploy_hooks()` (deploy-hook + remove-hook для всех модулей) → DeployOrchestrator._run_hooks(); (8) `parse_ssh_command()` (парсинг SSH_ORIGINAL_COMMAND) → DeployOrchestrator.receive(). | 4 |
| **T14** | deploy-modules.sh → фасад (<20 LOC): python3 -m core.internal.deploy.orchestrator_cli deploy-many | 1 |
| **T15** | overlay_deliverer.py → DeployOrchestrator через SCPChannel для overlay delivery | 2 |

### Wave 3.5: Shell → Python migration (H-tasks)

| Task | Описание | Effort |
|------|----------|--------|
| **H7** | Мигрировать invoke_module_interface вызовы из shell-скриптов в Python. Заменить shell-вызовы `invoke_module_interface` на прямой Python-вызов через module-interface.py API. | 2 |

### Wave 4: Tests + Gate

| Task | Описание | Effort |
|------|----------|--------|
| **T16** | Unit-тесты: test_orchestrator.py (deploy, rollback, status, remove), test_channels.py (SCP, ForcedCommand), test_audit_logger.py, test_deploy_history.py | 4 |
| **T17** | Gate test (многослойный): test_deploy_single_orchestrator.py — 3 проверки: (1) Python — fail если `docker compose up` вне DeployOrchestrator/deploy_compose(); (2) Shell — fail если `scp`/`rsync` вне channels.py; (3) Shell — fail если ssh forced-command вызов вне разрешённых каналов | 2 |
| **T18** | make fix-gate + make gate MODE=fast + pytest tests/ -v | 1 |
| **T19** | Интеграционный тест полного deploy-цикла: assemble_payload → channel deliver → deploy_compose → healthcheck_poll → audit_log. Docker-in-Docker или mock VPS. | 3 |
| **T20** | Адаптировать существующие тесты: test_deploy_engine.py, test_payload_deliverer.py, test_context_deployer.py, test_docker_orchestrator.py — убедиться, что они проходят после рефакторинга API | 2 |

---

## §4. File Manifest

### CREATE (11)
| Файл | Назначение |
|------|-----------|
| `core/internal/deploy/orchestrator.py` | DeployOrchestrator — единый фасад деплоя |
| `core/internal/deploy/orchestrator_cli.py` | CLI entrypoint: `python3 -m core.internal.deploy.orchestrator_cli receive/deploy-many` (T6.6) |
| `core/internal/deploy/channels.py` | SCPChannel, ForcedCommandChannel |
| `core/internal/deploy/audit_logger.py` | Унифицированный аудит-логгер |
| `core/internal/deploy/deploy_history.py` | DeployHistory — снепшоты состояния деплоя (T6.5) |
| `core/internal/deploy/healthcheck_poller.py` | HealthcheckPoller — общий полл здоровья |
| `tests/unit/test_orchestrator.py` | Unit-тесты DeployOrchestrator |
| `tests/unit/test_channels.py` | Unit-тесты SCPChannel + ForcedCommandChannel |
| `tests/unit/test_audit_logger.py` | Unit-тесты AuditLogger |
| `tests/unit/test_deploy_history.py` | Unit-тесты DeployHistory |
| `tests/integration/test_deploy_e2e.py` | Интеграционный тест полного deploy-цикла (T19) |

### MODIFY (10)
| Файл | Изменение |
|------|----------|
| `core/internal/deploy/deploy_engine.py` | → модуль, API: deploy_compose() |
| `core/internal/deploy/payload_deliverer.py` | → модуль, API: assemble_payload() |
| `core/internal/bootstrap/deploy/context_deployer.py` | → делегирует DeployOrchestrator |
| `core/internal/reconciler_projects.py` | → мигрировать deploy_project()/deliver_payload() на DeployOrchestrator (T11.5) |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | → DeployOrchestrator.deploy_many() |
| `core/entrypoints/deploy.sh` | → DeployOrchestrator через ForcedCommandChannel |
| `core/internal/bootstrap/lifecycle/state_machine.py` | → DeployOrchestrator.deploy_many() |
| `core/internal/bootstrap/overlay_deliverer.py` | → DeployOrchestrator через SCPChannel |
| `core/internal/bootstrap/deploy-modules.sh` | → фасад <20 LOC |
| `core/internal/bootstrap/setup-node.sh` | → обновить путь deploy-project.sh → DeployOrchestrator CLI (T13.0) |

### DELETE (3)
| Файл | Причина |
|------|---------|
| `core/internal/deploy/deploy-project.sh` | Бизнес-логика → DeployOrchestrator; хуки → orchestrator hooks |
| `core/entrypoints/deploy-project.sh` | Entrypoint (411 LOC) → DeployOrchestrator CLI (T13) |
| `core/lib/audit_logging.sh` | → audit_logger.py (Python) |

---

## §5. Acceptance Criteria (Detailed)

- [ ] AC1: DeployOrchestrator.deploy() — принимает project_name + channel, возвращает DeployResult (stdout, stderr, exit_code, deployed_at, healthcheck_status)
- [ ] AC2: DeployEngine.deploy_compose() — не standalone; вызывается только из DeployOrchestrator
- [ ] AC3: PayloadDeliverer.assemble_payload() — не standalone; вызывается только из DeployOrchestrator
- [ ] AC4: `ls core/internal/deploy/deploy-project.sh` → file not found (удалён); `ls core/entrypoints/deploy-project.sh` → file not found (удалён)
- [ ] AC5: `grep "def deploy\|def deliver\|def deploy_project" core/internal/deploy/ --include="*.py" | grep -v orchestrator` → только внутри классов-модулей (DeployEngine, PayloadDeliverer)
- [ ] AC6: DeliveryChannel ABC с двумя реализациями: SCPChannel, ForcedCommandChannel
- [ ] AC7: AuditLogger — единый формат; 0 grep "audit_logging.sh" core/internal/deploy/ (вне deprecated)
- [ ] AC8: `make gate MODE=fast` — зелёный, test_deploy_single_orchestrator.py PASS
- [ ] AC9: `python -m pytest tests/unit/test_orchestrator.py -v` — все тесты PASS
- [ ] AC10: Deploy dry-run на тестовой ноде через новый orchestrator
- [ ] AC11: DeployHistory — `/opt/projects/<name>/.deploy-snapshots/` содержит корректные JSON-снепшоты; rollback() восстанавливает compose_state
- [ ] AC12: File lock `/var/lock/platform-deploy-{project}.lock` предотвращает конкурентный деплой одного проекта
- [ ] AC13: Gate test T17 — 3 слоя проверок (Python docker compose, Shell scp/rsync, Shell forced-command) — все проходят
- [ ] AC14: `grep "deliver_payload\|deploy_project" core/internal/reconciler_projects.py` → пусто (мигрировано)
- [ ] AC15: `grep -rn "deploy-project\.sh" core/internal/bootstrap/setup-node.sh` → пусто (обновлён путь)
- [ ] AC16: Интеграционный тест T19 — полный цикл deploy PASS
- [ ] AC17: Существующие тесты (test_deploy_engine.py, test_payload_deliverer.py, test_context_deployer.py, test_docker_orchestrator.py) — все PASS после рефакторинга

---

## §6. Design Decisions

### DD1: Почему DeliveryChannel — ABC, а не if/else?
Два канала доставки (SCP и forced-command) имеют разный lifecycle:
- SCP: assemble tar → scp → remote-cmd.sh unpack
- ForcedCommand: assemble tar → ssh forced-command → VPS side handle
ABC позволяет добавить третий канал (например, HTTP push для serverless) без изменения DeployOrchestrator.

### DD2: Почему deploy-project.sh удаляется, а не остаётся фасадом?
deploy-project.sh (400 LOC) содержит бизнес-логику, которая ДОЛЖНА быть в Python согласно языковой политике. DP-081 оставил его как «частично мигрированный». После создания DeployOrchestrator shell-фасад становится избыточным:
- Хуки → DeployOrchestrator.pre_deploy_hook() / post_deploy_hook()
- Тегирование → DeployOrchestrator._tag_deployed()
- invoke_module_interface → Python-вызов через module-interface.py

### DD3: Почему audit_logging.sh тоже удаляется?
audit_logging.sh (shell) и deploy_engine.py.audit_write() (Python) имеют разные форматы аудит-логов. Это создаёт проблемы при поиске по логам (разные timestamp-форматы, разные уровни). AuditLogger Python — единый формат, единый output (файл + syslog).

### DD4: HealthcheckPoller — почему отдельный модуль?
Текущая ситуация: context_deployer делает healthcheck ПОСЛЕ DeployEngine.deploy(), который УЖЕ сделал healthcheck внутри. Это двойная работа. HealthcheckPoller как shared модуль используется DeployOrchestrator ЕДИНОЖДЫ после deploy.

### DD5: DeployHistory — почему не in-memory?
Rollback без хранения предыдущего состояния — холостой выстрел. In-memory история не переживёт рестарт VPS. DeploySnapshot на диск (/opt/projects/<name>/.deploy-snapshots/) даёт: (1) rollback после краша VPS, (2) audit trail для forensics, (3) возможность отката к конкретной версии. Retention 10 снепшотов балансирует между историей и местом (средний snapshot ~5 KB JSON).

### DD6: reconciler_projects.py — почему не оставить как есть?
700+ LOC модуль содержит `deliver_payload()` (L346) и `deploy_project()` (L480) — полные дубликаты PayloadDeliverer + DeployEngine. Оставить значило бы иметь 2 параллельные имплементации одной логики, где багфикс в одной не применяется к другой. DP-089 создаёт единый typed фасад — reconciler_projects.py должен его использовать, а не дублировать.

---

## §7. Implementation Commands

```
# === WAVE 1: Foundation ===
coder implement DevPlan 089 Wave 1:
  T1 (DeliveryChannel ABC), T2 (SCPChannel), T3 (ForcedCommandChannel),
  T4 (AuditLogger), T5 (HealthcheckPoller), T6 (DeployOrchestrator + unit tests),
  T6.5 (DeployHistory storage)

# Verify Wave 1
python3 -m pytest tests/unit/test_orchestrator.py -v

# === WAVE 2: Refactor existing engines ===
coder implement DevPlan 089 Wave 2:
  T7 (DeployEngine → модуль), T8 (PayloadDeliverer → модуль), T9 (docker_orchestrator → many)

# Verify Wave 2 — существующие тесты деплоя
python3 -m pytest tests/ -k "deploy" -v

# === WAVE 3: Consumer Migration ===
coder implement DevPlan 089 Wave 3:
  T10 (deploy.sh), T11 (context_deployer.py), T11.5 (reconciler_projects.py),
  T12 (state_machine.py), T13.0 (setup-node.sh path update),
  T13 (удалить deploy-project.sh — 8 функций мигрированы),
  T14 (deploy-modules.sh фасад), T15 (overlay_deliverer.py)

# Verify Wave 3
ls core/internal/deploy/deploy-project.sh 2>&1
# Expected: No such file or directory
grep -rn "audit_logging" core/internal/deploy/
# Expected: empty
grep -rn "deploy-project\.sh" core/internal/bootstrap/setup-node.sh
# Expected: empty (обновлён путь)
grep "deliver_payload\|deploy_project" core/internal/reconciler_projects.py
# Expected: empty (мигрировано)

# === WAVE 3.5: Shell → Python ===
coder implement DevPlan 089 Wave 3.5:
  H7 (invoke_module_interface calls → Python)

# === WAVE 4: Gate ===
coder implement DevPlan 089 Wave 4:
  T16 (unit tests), T17 (multi-layered gate test),
  T18 (fix-gate + gate), T19 (integration test),
  T20 (adapt existing tests)

# Final verification
make fix-gate && make gate MODE=fast
python3 -m pytest tests/ -v
```

$END_DEVPLAN
