$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               QA-верификация DevPlan 089 (Deploy Orchestrator Unification) — семантический аудит self-consistency, полноты, измеримости AC
DESCRIPTION:           Проверка $ARTIFACT_CONTRACT, File Manifest vs задачи, корректность волн, архитектурная целостность цепочки forced-command после удаления deploy-project.sh, H7 risk analysis
RATIONALE:             6 BLOCKER-находок не позволяют Coder выполнить DevPlan без доработки Архитектором
ACCEPTANCE_CRITERIA:   Все BLOCKER-находки устранены до запуска Coder
IMPLEMENTS:            QA Phase 1 + Phase 2 (Standing) — static audit + cross-file drift
IMPACTS:               .ai/plans/089-deploy-orchestrator-unification/02-VerificationReport.md (создание)
REQUIRES:              DevPlan 089 (089-deploy-orchestrator-unification/DevPlan.md), доступ к filesystem для cross-file верификации
$END_ARTIFACT_CONTRACT

---

# VerificationReport 089: Deploy Orchestrator Unification

**🔒 Verified against SHA:** `5a31ef2bafd10b6bbe59345d35625e3b1c108953`
**⚠️ Dirty tree:** 8 files modified (не связаны с DevPlan 089 — infra-metrics изменения)
**Date:** 2026-07-28
**Scope:** STANDARD+ (18+ файлов, архитектурные изменения, config/compose затронуты косвенно через CI chain)

---

## Semantic Verdict: BROKEN (6 BLOCKER)

DevPlan содержит 6 BLOCKER-находок, которые делают его невыполнимым для Coder без доработки Архитектором:
- 3 файла в File Manifest имеют неверные пути (файлы не будут найдены при редактировании)
- Путь замены deploy-project.sh в authorized_keys не специфицирован (T13.0 невыполнима)
- VPS-side forced-command receiver не определён после удаления deploy-project.sh (архитектурный разрыв)
- state_machine.py содержит 3-й хардкод deploy-project.sh, не покрытый ни одной задачей
- AC15 grep-паттерн не сматчит реальное содержимое файла (критерий неприёмки неверифицируем)
- DeployOrchestrator CLI (entrypoint) не создаётся ни одной задачей CREATE

**Рекомендация:** вернуть DevPlan Архитектору для устранения BLOCKER-находок перед запуском Coder.

---

## §1. Static Audit (Phase 1)

### 1.1 $ARTIFACT_CONTRACT completeness

| Поле | Статус | Примечание |
|------|--------|-----------|
| PURPOSE | ✅ PASS | Чётко сформулирована цель унификации |
| DESCRIPTION | ✅ PASS | Детальное описание текущего состояния и проблем |
| RATIONALE | ✅ PASS | Обоснование с отсылкой к DP-081 |
| ACCEPTANCE_CRITERIA | ⚠️ WARNING | AC1-AC9 в контракте, AC10-AC17 в §5 — несоответствие количества (9 vs 17) |
| IMPLEMENTS | ✅ PASS | Указан источник (Superposition Analysis) |
| IMPACTS | ❌ FAIL | «18 файлов (6 CREATE, 10 MODIFY, 2 DELETE)» — не включает 5 тестовых CREATE-файлов из T16/T17/T19 |
| REQUIRES | ✅ PASS | DP-081, DP-088 указаны |

### 1.2 File Manifest self-consistency

#### CREATE (6) — task coverage

| Файл в манифесте | Задача | Статус |
|------------------|--------|--------|
| `core/internal/deploy/orchestrator.py` | T6 | ✅ |
| `core/internal/deploy/channels.py` | T1+T2+T3 | ✅ |
| `core/internal/deploy/audit_logger.py` | T4 | ✅ |
| `core/internal/deploy/deploy_history.py` | T6.5 | ✅ |
| `core/internal/deploy/healthcheck_poller.py` | T5 | ✅ |
| `tests/unit/test_orchestrator.py` | T16 | ✅ |

#### CREATE (в задачах, НЕ в манифесте)

| Файл (предполагаемый) | Задача | Статус |
|----------------------|--------|--------|
| `tests/unit/test_channels.py` | T16 | ❌ MISSING from manifest |
| `tests/unit/test_audit_logger.py` | T16 | ❌ MISSING from manifest |
| `tests/unit/test_deploy_history.py` | T16 | ❌ MISSING from manifest |
| `tests/gates/test_deploy_single_orchestrator.py` | T17 | ❌ MISSING from manifest |
| `tests/integration/test_deploy_cycle.py` (approx) | T19 | ❌ MISSING from manifest |

**Итого:** 6 CREATE в манифесте, 11 фактических CREATE (6 core + 5 test). IMPACTS должен быть «23 файла (11 CREATE, 10 MODIFY, 2 DELETE)».

#### MODIFY (10) — path verification against actual filesystem

| Путь в манифесте | Реальный путь | Статус |
|-----------------|--------------|--------|
| `core/internal/deploy/deploy_engine.py` | ✅ `core/internal/deploy/deploy_engine.py` | ✅ |
| `core/internal/deploy/payload_deliverer.py` | ✅ `core/internal/deploy/payload_deliverer.py` | ✅ |
| `core/internal/deploy/context_deployer.py` | ❌ `core/internal/bootstrap/deploy/context_deployer.py` | ❌ **DRIFT** |
| `core/internal/deploy/reconciler_projects.py` | ❌ `core/internal/reconciler_projects.py` | ❌ **DRIFT** |
| `core/internal/bootstrap/docker_orchestrator.py` | ✅ `core/internal/bootstrap/deploy/docker_orchestrator.py` | ⚠️ неточный путь |
| `core/entrypoints/deploy.sh` | ✅ `core/entrypoints/deploy.sh` | ✅ |
| `core/internal/bootstrap/lifecycle/state_machine.py` | ✅ `core/internal/bootstrap/lifecycle/state_machine.py` | ✅ |
| `core/internal/bootstrap/overlay_deliverer.py` | ✅ `core/internal/bootstrap/overlay_deliverer.py` | ✅ |
| `core/internal/bootstrap/deploy-modules.sh` | ✅ `core/internal/bootstrap/deploy-modules.sh` | ✅ |
| `core/bootstrap/setup-node.sh` | ❌ `core/internal/bootstrap/setup-node.sh` | ❌ **DRIFT** |

#### DELETE (2)

| Файл | Реальный путь | Статус |
|------|--------------|--------|
| `core/internal/deploy/deploy-project.sh` | ✅ `core/internal/deploy/deploy-project.sh` | ✅ |
| `core/lib/audit_logging.sh` | ✅ `core/lib/audit_logging.sh` | ✅ |

---

## §2. Drift Analysis (Phase 2)

### DRIFT-1 [BLOCKER] File Manifest PATH DRIFT — 3 файла с неверными путями

| DRIFT-ID | Severity | Ожидаемый путь (манифест) | Реальный путь | Файл |
|----------|----------|--------------------------|---------------|------|
| DRIFT-PATH-1 | **BLOCKER** | `core/bootstrap/setup-node.sh` | `core/internal/bootstrap/setup-node.sh` | §4 MODIFY #10 |
| DRIFT-PATH-2 | **BLOCKER** | `core/internal/deploy/context_deployer.py` | `core/internal/bootstrap/deploy/context_deployer.py` | §4 MODIFY #3 |
| DRIFT-PATH-3 | **BLOCKER** | `core/internal/deploy/reconciler_projects.py` | `core/internal/reconciler_projects.py` | §4 MODIFY #4 |

**Impact:** Coder будет редактировать несуществующие файлы. `edit` tool упадёт с «file not found».

**Fix:** Исправить пути в §4 File Manifest:
- `core/bootstrap/setup-node.sh` → `core/internal/bootstrap/setup-node.sh`
- `core/internal/deploy/context_deployer.py` → `core/internal/bootstrap/deploy/context_deployer.py`
- `core/internal/deploy/reconciler_projects.py` → `core/internal/reconciler_projects.py`

Также исправить AC15: `grep ... core/bootstrap/setup-node.sh` → `core/internal/bootstrap/setup-node.sh`.

---

### DRIFT-2 [BLOCKER] T13.0 replacement path for setup-node.sh undefined

T13.0: «обновить хардкод `command="deploy-project.sh"` в authorized_keys (строки 94,112) на новый путь DeployOrchestrator CLI.»

**Проблема:** Новый путь НЕ СПЕЦИФИЦИРОВАН. Что должно быть в authorized_keys после удаления deploy-project.sh?

Варианты (ни один не указан в DevPlan):
- (A) `command="${PLATFORM_ROOT}/core/entrypoints/deploy.sh"` — deploy.sh уже существует, но сейчас он exec'ит deploy-project.sh
- (B) `command="python3 -m core.internal.deploy.orchestrator"` — запуск orchestrator напрямую
- (C) Новый CLI-скрипт — не существует ни в CREATE, ни в задачах

**Impact:** Coder не знает, на что заменить. T13.0 невыполнима.

**Fix:** Специфицировать конкретный replacement path в T13.0. Если это deploy.sh — указать, что T10 также должен обновить deploy.sh для прямого вызова orchestrator (а не exec deploy-project.sh). Если новый CLI — добавить CREATE-задачу.

---

### DRIFT-3 [BLOCKER] VPS-side forced-command receiver chain broken after deploy-project.sh deletion

Текущая цепочка VPS-side forced-command:

```
SSH authorized_keys
  └─ command="...deploy-project.sh"     ← setup-node.sh:112
       └─ main()                        ← deploy-project.sh:97
            ├─ parse_ssh_command()       ← парсинг SSH_ORIGINAL_COMMAND
            ├─ deploy → deploy_engine.py
            ├─ remove → deploy_engine.py
            ├─ status → deploy_engine.py
            └─ platform-deliver → payload_deliverer.py
```

Альтернативный entrypoint (deploy.sh → exec):

```
SSH forced-command
  └─ deploy.sh                          ← VPS entrypoint
       └─ exec deploy-project.sh        ← deploy.sh:78,83,95
```

После удаления deploy-project.sh разрываются ОБЕ цепочки:
1. setup-node.sh authorized_keys → `/core/internal/deploy/deploy-project.sh` (удалён)
2. deploy.sh exec → `/core/internal/deploy/deploy-project.sh` (удалён)

**Третий разрыв:** `state_machine.py:1116` — `forced_command = f'command="{core_dir}/internal/deploy/deploy-project.sh {node_name}",restrict'` — Python-эквивалент setup-node.sh.

Ни одна задача не определяет, что заменяет deploy-project.sh как VPS-side receiver. T10 говорит «deploy.sh → DeployOrchestrator.deploy() через ForcedCommandChannel», но:
- ForcedCommandChannel — это КЛИЕНТСКАЯ абстракция (отправка на VPS)
- deploy.sh работает НА VPS — ему не нужен канал для вызова локального orchestrator'а

**Impact:** После удаления deploy-project.sh деплой через CI (make deploy → git push → SSH forced-command) сломается — VPS не сможет обработать входящий forced-command.

**Fix:** Определить VPS-side receiver в архитектуре:
1. Либо deploy.sh напрямую вызывает `DeployOrchestrator.deploy()` (локальный вызов на VPS, без channel)
2. Либо создать новый Python entrypoint (`python3 -m core.internal.deploy.orchestrator`), который парсит SSH_ORIGINAL_COMMAND и вызывает DeployOrchestrator
3. Обновить setup-node.sh И state_machine.py на новый путь

---

### DRIFT-4 [BLOCKER] state_machine.py:1116 — 3-й хардкод deploy-project.sh, не покрыт задачами

`core/internal/bootstrap/lifecycle/state_machine.py:1116`:
```python
forced_command = f'command="{core_dir}/internal/deploy/deploy-project.sh {node_name}",restrict'
```

**Где в DevPlan:** Нигде. File Manifest MODIFY для state_machine.py говорит только о T12 (deploy_many через DeployOrchestrator). T13.0 упоминает только setup-node.sh.

**Impact:** После удаления deploy-project.sh `make converge` сломается — state_machine.py создаст authorized_keys с путём к удалённому файлу.

**Fix:** Добавить в T12 или T13.0 обновление state_machine.py:1116. Либо вынести в отдельную подзадачу T13.1.

---

### DRIFT-5 [BLOCKER] AC15 grep pattern mismatch — критерий приёмки неверифицируем

AC15: `grep "command=deploy-project.sh" core/bootstrap/setup-node.sh → пусто (обновлён путь)`

**Проблема 1 — файл:** путь `core/bootstrap/setup-node.sh` не существует (должен быть `core/internal/bootstrap/setup-node.sh`).

**Проблема 2 — паттерн:** `grep "command=deploy-project.sh"` не сматчит реальное содержимое:
```bash
local restrict_opts="command=\"${PLATFORM_ROOT}/core/internal/deploy/deploy-project.sh\",restrict"
```
Строка содержит `${PLATFORM_ROOT}/...` префикс и экранированные кавычки. Паттерн `command=deploy-project.sh` не найдёт эту строку.

**Impact:** AC15 не может быть верифицирован даже после успешной реализации — grep всегда вернёт «empty» независимо от того, обновлён путь или нет.

**Fix:** 
- Исправить путь файла
- Исправить grep-паттерн на `grep "deploy-project.sh" core/internal/bootstrap/setup-node.sh core/internal/bootstrap/lifecycle/state_machine.py`
- После исправления ожидать «empty» во всех трёх локациях (setup-node.sh, deploy.sh, state_machine.py)

---

### DRIFT-6 [BLOCKER] DeployOrchestrator CLI entrypoint не создаётся

T13.0 ссылается на «новый путь DeployOrchestrator CLI», но ни одна задача не создаёт CLI/entrypoint для DeployOrchestrator.

В архитектуре §2 указаны потребители:
```
deploy.sh → DeployOrchestrator.deploy() через ForcedCommandChannel
context_deployer.py → DeployOrchestrator.deploy() через SCPChannel
```

Но:
- `deploy.sh` — существующий VPS entrypoint (MODIFY), не создаётся
- `deploy.sh` вызывает ForcedCommandChannel к САМОМУ СЕБЕ (deploy.sh на VPS) — архитектурная петля
- Отсутствует файл, который становится новым forced-command receiver'ом

**Impact:** T13.0 невыполнима без явного определения нового CLI-пути.

**Fix:** Одно из:
- (A) Добавить в T10 обновление deploy.sh: вместо `exec deploy-project.sh` → прямой вызов `python3 -m core.internal.deploy.orchestrator` (парсинг SSH_ORIGINAL_COMMAND + dispatch)
- (B) Создать новый entrypoint `core/entrypoints/orchestrator-cli.sh` (или `deploy-receiver.sh`) и добавить в CREATE

---

### DRIFT-7 [MAJOR] core/entrypoints/deploy-project.sh (411 LOC) не в File Manifest

`core/entrypoints/deploy-project.sh` — локальный entrypoint для `make deploy-project` (direct deploy bypassing CI). 411 LOC.

**Где в DevPlan:** Нигде. Не в CREATE, не в MODIFY, не в DELETE.

**Текущая роль:** оркестрирует tar+ssh доставку с машины оператора на VPS:
1. `tar czf` → `ssh ci-deploy@VPS "platform-deliver ..."` (доставка payload)
2. `ssh ci-deploy@VPS "deploy.sh <project> <sha>"` (запуск деплоя)

После миграции этот entrypoint должен использовать DeployOrchestrator + SCPChannel (или ForcedCommandChannel) для доставки вместо ручного tar+ssh. Но это не указано в задачах.

**Impact:** `make deploy-project` останется на старой реализации, не использующей DeployOrchestrator. Дупликация логики деплоя сохранится.

**Fix:** Добавить `core/entrypoints/deploy-project.sh` в File Manifest MODIFY и указать задачу миграции на DeployOrchestrator (либо T10-расширение, либо новая T10.5).

---

### DRIFT-8 [MAJOR] deploy.sh architectural role confusion in §2 Target Architecture

§2 Target Architecture:
```
deploy.sh → DeployOrchestrator.deploy() через ForcedCommandChannel
```

**Проблема:** `deploy.sh` — VPS-side forced-command receiver. Он не может «использовать» ForcedCommandChannel для вызова DeployOrchestrator, потому что:
- ForcedCommandChannel — КЛИЕНТСКАЯ абстракция для отправки payload на VPS
- DeployOrchestrator работает ЛОКАЛЬНО на VPS (там же, где deploy.sh)
- deploy.sh должен вызывать orchestrator НАПРЯМУЮ, без канала

Правильная архитектурная модель:
```
# CLIENT SIDE (машина оператора / CI):
DeployOrchestrator (local)
  └─ ForcedCommandChannel.deliver(payload)
       └─ ssh ci-deploy@VPS "deploy.sh <project> <sha>"   ← forced-command

# VPS SIDE:
deploy.sh (forced-command receiver)
  └─ python3 -m core.internal.deploy.orchestrator deploy <project> <sha>
       └─ DeployOrchestrator.deploy()  ← прямой локальный вызов
```

**Fix:** Исправить диаграмму в §2: разделить client-side и VPS-side роли. `deploy.sh` → прямой вызов DeployOrchestrator (не через channel).

---

### DRIFT-9 [MAJOR] deploy-project.sh имеет 8 функциональных областей, T13 покрывает 5

Фактический состав `deploy-project.sh` (133 LOC) по функциям:

| # | Функция | Строки | Покрыто T13? | Куда мигрирует |
|---|---------|--------|-------------|----------------|
| 1 | `notify_hook()` | 47-49 | ✅ (#1) | DeployOrchestrator.post_deploy_hook() |
| 2 | `_finalize_deploy()` | 37-44 | ✅ (#2) | try/finally в DeployOrchestrator.deploy() |
| 3 | `tag_current()` | 92 | ✅ (#3) | _tag_deployed() |
| 4 | `prune_old_images()` | 93 | ✅ (#3) | _prune_images() |
| 5 | `PLATFORM_DEPLOY_DIRECT` env | 57 | ✅ (#4) | config в __init__() |
| 6 | `MAX_WAIT_SEC` / `KEEP_IMAGES` | 21-22 | ✅ (#5) | config в __init__() |
| 7 | **`_rollback_on_error()` trap ERR** | 36 | ❌ | НЕ покрыто |
| 8 | **`_trigger_deploy_hooks()`** | 94 | ⚠️ частично | H7 (invoke_module_interface), но логика итерации по module.yaml НЕ покрыта |
| 9 | **`parse_ssh_command()` + main verb dispatch** | 53-89, 97-131 | ❌ | SSH-парсинг + dispatch «deploy/remove/status» — НЕ покрыто |

**Пропущенные функции:**

**#7 `_rollback_on_error()`:** trap ERR handler. В DeployOrchestrator нет соответствующего метода. Rollback в DevPlan описан (DeployOrchestrator.rollback()), но не привязан к trap-механизму. При ошибке деплоя текущий trap вызывает `log_imp 10 "CRITICAL: error" + DEPLOY_STATUS="failed" + exit 1`. Новый orchestrator должен иметь эквивалентный error-handling.

**#8 `_trigger_deploy_hooks()`:** итерация по `modules/*/module.yaml`, вызов `invoke_module_interface deploy-hook`. H7 мигрирует вызовы invoke_module_interface, но логика итерации и фильтрации модулей (for my in modules/*/module.yaml) не указана ни в одной задаче.

**#9 `parse_ssh_command()` + main():** SSH_ORIGINAL_COMMAND парсинг, валидация PROJECT_DIR, определение SERVICE_NAME, диспатч глаголов (deploy/remove/status/platform-deliver). Эта логика частично дублируется в deploy.sh (entrypoint) и НЕ покрыта задачами миграции. Предположительно, останется в deploy.sh, но DevPlan это явно не утверждает.

**Impact:** 3 из 8 функциональных областей не имеют явного пути миграции. Coder будет вынужден принимать архитектурные решения на ходу.

**Fix:** Явно указать для каждой функции:
- `_rollback_on_error()` → DeployOrchestrator._handle_deploy_error() или интегрировать в try/finally
- `_trigger_deploy_hooks()` → DeployOrchestrator._trigger_module_hooks() или оставить в deploy.sh
- `parse_ssh_command()` + verb dispatch → ОСТАЁТСЯ в deploy.sh (не мигрирует) — явно указать это

---

### DRIFT-10 [MAJOR] DeployHistory.compose_state field undefined

T6.5: `Snapshot format: { project, version, timestamp, compose_state, health_status, payload_hash }`

`compose_state` не имеет определения. Возможные интерпретации:
- `docker compose ps --format json` — состояние контейнеров
- `docker inspect <container>` — детальное состояние
- `docker compose config` — конфигурация (для восстановления)
- Кастомный словарь с image digests и container status

**Impact:** Coder не знает, что сохранять в снепшот. Разные интерпретации → разное поведение rollback.

**Fix:** Специфицировать `compose_state` явно, например:
```
compose_state: {
    "services": { "<name>": { "image": "ghcr.io/org/img:sha256:abc", "status": "running" } },
    "config_hash": "sha256:..."
}
```

---

### DRIFT-11 [MAJOR] Конфликт форматов: deploy-result.json vs новый DeployHistory

Текущий `deploy-project.sh:38-41` пишет:
```json
{"status":"success","timestamp":"2026-07-28T13:00:00Z","project":"myapp","ref":"abc123"}
```
в `/opt/projects/<name>/.deploy-snapshots/deploy-result.json`.

Новый DeployHistory (T6.5) пишет:
```json
{"project":"...","version":"...","timestamp":"...","compose_state":{...},"health_status":"...","payload_hash":"..."}
```
в `/opt/projects/<name>/.deploy-snapshots/<snapshot_id>.json`.

**Проблема:** Два разных формата в одной директории. После удаления deploy-project.sh старый `deploy-result.json` перестанет обновляться. Если кто-то (мониторинг, audit) читает deploy-result.json — сломается.

**Impact:** Потребители старого формата (если есть) потеряют данные после миграции.

**Fix:** Явно указать в DevPlan:
1. Есть ли потребители `deploy-result.json` (grep по кодовой базе)?
2. Нужна ли миграция старых снепшотов в новый формат?
3. Удаляется ли `deploy-result.json` после миграции?

---

### DRIFT-12 [MAJOR] File Manifest MODIFY: docker_orchestrator.py неточный путь

Манифест: `core/internal/bootstrap/docker_orchestrator.py`
Реальность: `core/internal/bootstrap/deploy/docker_orchestrator.py`

Пропущен `deploy/` в пути. Менее критично, чем DRIFT-PATH-1 (файл существует в обоих случаях, просто на один уровень глубже), но всё равно вызовет ошибку при точном редактировании.

**Fix:** `core/internal/bootstrap/docker_orchestrator.py` → `core/internal/bootstrap/deploy/docker_orchestrator.py`

---

## §3. Invariant Status (Phase 3 — выборочно)

Выборочная проверка релевантных инвариантов из root AGENTS.md:

| Инвариант | Статус | Evidence |
|-----------|--------|----------|
| **Makefile — единый фасад** | ⚠️ AT_RISK | DevPlan не упоминает обновление Makefile-таргетов после удаления deploy-project.sh. `make deploy` и `make deploy-project` ссылаются на удаляемые/изменяемые файлы. |
| **Языковая политика (Python-first)** | ✅ HELD | Миграция shell→Python соответствует политике |
| **core/entrypoint-manifest.yaml** | ⚠️ AT_RISK | DevPlan не упоминает обновление манифеста: deploy-project.sh удалён из forced-command chain, нужно обновить registered entrypoints |
| **LiteLLM — PostgreSQL** | ✅ HELD | Не затрагивается |
| **Manifest Generation Contract** | ⚠️ AT_RISK | Новые CREATE-файлы (orchestrator.py, channels.py, etc.) должны быть зарегистрированы в check-manifests/generate-manifests |

---

## §4. Detailed Findings by User Concern

### 4.1 File Manifest CREATE = 4+test vs реальность

**Утверждение пользователя:** «CREATE 4 (orchestrator, channels, audit_logger, deploy_history) + test — посчитай точно»

**Факт:** File Manifest CREATE = 6 файлов (5 core + 1 test). Задачи создают 11 файлов (6 core + 5 test). Расхождение: 5 тестовых файлов не в манифесте.

См. DRIFT-MANIFEST в §2 (MAJOR).

### 4.2 DeployHistory storage (T6.5) — формат и retention

**Статус:** Формат специфицирован на высоком уровне, но `compose_state` не определён. Retention специфицирован (keep last 10). Механизм prune не описан (кто и когда чистит старые снепшоты?).

См. DRIFT-10 и DRIFT-11 в §2 (MAJOR).

### 4.3 DeliveryChannel ABC — timeout/retry/Payload/DeliveryResult

**Статус:** ХОРОШО специфицировано. §2 содержит:
- Payload dataclass (4 поля)
- DeliveryResult dataclass (5 полей)
- timeout: 600s configurable
- retry: 2 retries + exponential backoff (5s ×2)
- auth: SSH key-based
- ❗ `Payload.metadata: dict` — нетипизированный (MINOR)

### 4.4 setup-node.sh (T13.0) — новый путь forced-command

**Статус:** НЕ специфицирован. T13.0 говорит «обновить на новый путь DeployOrchestrator CLI» — путь не указан.

См. DRIFT-2 в §2 (BLOCKER).

### 4.5 5 sub-tasks для deploy-project.sh удаления — покрытие

**Статус:** 5 заявленных подзадач покрывают functions #1-#5 (из 8). Functions #7 (_rollback_on_error), #8 (_trigger_deploy_hooks), #9 (parse_ssh_command+dispatch) не покрыты.

См. DRIFT-9 в §2 (MAJOR).

### 4.6 H7 (invoke_module_interface) — risk shell→Python

**Статус:** Risk analysis ОТСУТСТВУЕТ в DevPlan.

Текущее состояние:
- `docker_orchestrator.py` уже вызывает `invoke_module_interface` через bash subprocess (lines 1308-1318)
- `state_machine.py` тоже через bash subprocess (line 1967-1976, с TRAP[BUG])
- `deploy-modules.sh`, `modules-healthcheck.sh` вызывают напрямую (bash→bash)

**Риски при миграции:**
1. **PATH resolution:** `invoke_module_interface` зависит от `PATHS_MODULES_DIR` (из `paths.sh`). Python-враппер должен либо source'ить paths.sh, либо знать MODULES_DIR.
2. **YAML validation:** `module-interface.sh` валидирует interfaces через `yaml_get_list`. Python-враппер должен либо вызывать bash (subprocess), либо ре-имплементировать YAML-валидацию.
3. **Module hook breakage:** deploy-hook/remove-hook диспатчатся через `module.yaml#hooks.on_project_deploy`. Если Python-враппер неправильно резолвит пути — хуки модулей (postgres, nginx, monitoring) молча пропускаются.
4. **Graceful degradation:** текущая реализация возвращает 0 при отсутствии интерфейса/скрипта («graceful skip»). Python-враппер должен сохранить это поведение.

**Рекомендация:** Добавить risk analysis в DevPlan §6 (Design Decisions) или отдельную секцию для H7.

---

## §5. Acceptance Criteria Verification

| AC | Описание | Верифицируемость | Проблема |
|----|----------|-----------------|----------|
| AC1 | DeployOrchestrator.deploy() → DeployResult | ✅ Измеримо | — |
| AC2 | DeployEngine.deploy_compose() не standalone | ✅ Измеримо | — |
| AC3 | PayloadDeliverer.assemble_payload() не standalone | ✅ Измеримо | — |
| AC4 | `ls deploy-project.sh` → file not found | ✅ Измеримо | Но не проверяет entrypoints/deploy-project.sh |
| AC5 | grep def deploy\|deliver\|deploy_project | ⚠️ | Синтаксис grep может дать ложные срабатывания |
| AC6 | DeliveryChannel ABC с 2 impl | ✅ Измеримо | — |
| AC7 | 0 grep audit_logging.sh | ✅ Измеримо | — |
| AC8 | make gate MODE=fast зелёный | ✅ Измеримо | — |
| AC9 | pytest tests/unit/test_orchestrator.py PASS | ✅ Измеримо | — |
| AC10 | Deploy dry-run на тестовой ноде | ❌ | Не автоматизировано, требует ручной проверки |
| AC11 | DeployHistory snowshots + rollback | ⚠️ | «корректные JSON-снепшоты» — субъективно |
| AC12 | File lock предотвращает конкурентный деплой | ⚠️ | Требует race-condition теста |
| AC13 | Gate test T17 3 слоя | ✅ Измеримо | — |
| AC14 | grep deliver_payload\|deploy_project в reconciler_projects.py | ✅ | Но файл в манифесте имеет неверный путь |
| AC15 | grep command=deploy-project.sh в setup-node.sh | ❌ **BROKEN** | См. DRIFT-5 |
| AC16 | Интеграционный тест T19 PASS | ❌ | Не автоматизировано |
| AC17 | Существующие тесты PASS после рефакторинга | ✅ Измеримо | — |

---

## §6. Missing Cross-References

Задачи и файлы, которые затронуты изменениями, но НЕ упомянуты в DevPlan:

| Файл | Проблема | Где должно быть |
|------|---------|-----------------|
| `core/entrypoints/deploy-project.sh` (411 LOC) | Не в File Manifest. Использует tar+ssh для доставки — должно мигрировать на DeployOrchestrator | MODIFY + задача T10.5 |
| `core/internal/bootstrap/lifecycle/state_machine.py:1116` | Хардкод deploy-project.sh — не покрыт задачами | T13.0 расширение или T13.1 |
| `core/entrypoint-manifest.yaml` | deploy-project.sh удалён — нужно обновить registered scripts | T13 дополнение |
| `Makefile` (root) | `make deploy` и `make deploy-project` таргеты ссылаются на изменяемые файлы | T13 дополнение или T18 расширение |
| `tests/test_contract_deploy.py` | Тестирует deploy-project.sh (будет удалён) — нужно удалить/адаптировать | T20 |
| `tests/test_contract_deploy_pruning.py` | Тестирует deploy-project.sh prune | T20 |
| `tests/test_contract_deploy_rollback.py` | Тестирует deploy-project.sh rollback | T20 |
| `tests/test_contract_deploy_ssh.py` | Тестирует deploy-project.sh SSH parsing | T20 |
| `tests/test_contract_deploy_audit.py` | Тестирует deploy-project.sh audit | T20 |
| `tests/test_contract_deploy_deliver.py` | Тестирует deploy-project.sh platform-deliver | T20 |
| `tests/test_deploy_finalization.py` | Интеграционные тесты deploy-project.sh | T20 |
| `tests/test_deploy_direct.py` | Тестирует deploy-project.sh + deploy.sh | T20 |
| `tests/test_char_deploy_parse_save.py` | Golden-тесты deploy-project.sh парсинга | T20 |
| `tests/unit/test_deploy_snapshot.py` | Тестирует deploy-project.sh через subprocess | T20 |
| `tests/test_project_lifecycle.py` | Тестирует remove-hooks в deploy-project.sh | T20 |

**Итого:** 14 дополнительных тестовых файлов ссылаются на deploy-project.sh. T20 говорит «адаптировать существующие тесты» но перечисляет только 4 файла (test_deploy_engine.py, test_payload_deliverer.py, test_context_deployer.py, test_docker_orchestrator.py). Остальные 10+ тестовых файлов не упомянуты.

---

## Summary: Findings by Severity

| Severity | Count | IDs |
|----------|-------|-----|
| **BLOCKER** | 6 | DRIFT-PATH-1, DRIFT-PATH-2, DRIFT-PATH-3, DRIFT-2 (T13.0 undefined), DRIFT-3 (VPS receiver), DRIFT-4 (state_machine.py), DRIFT-5 (AC15 grep), DRIFT-6 (CLI missing) |
| **MAJOR** | 7 | DRIFT-7 (entrypoints/deploy-project.sh), DRIFT-8 (deploy.sh role confusion), DRIFT-9 (5/8 functions covered), DRIFT-10 (compose_state undefined), DRIFT-11 (deploy-result.json conflict), DRIFT-12 (docker_orchestrator path), File Manifest CREATE missing 5 test files |
| **MINOR** | 6 | DeployResult syntax, H7 risk analysis, Payload.metadata:dict, T13.0 incomplete line coverage, 14 test files not in T20 scope, Makefile/entrypoint-manifest не упомянуты |

---

## Health Score

```
score = 100
- 5 × 6 BLOCKER = −30
- 3 × 7 MAJOR  = −21
- 1 × 6 MINOR  = −6
─────────────────
score = 43 / 100  →  BROKEN (0-39 CRITICAL, 40-69 significant drift)
```

**Verdict:** BROKEN. DevPlan требует доработки Архитектором перед запуском Coder.

**Приоритет исправлений:**
1. Исправить 3 пути в File Manifest (DRIFT-PATH-1/2/3)
2. Специфицировать replacement path в T13.0 и создать задачу для CLI (DRIFT-2, DRIFT-6)
3. Определить VPS-side receiver и цепочку forced-command после удаления (DRIFT-3, DRIFT-8)
4. Исправить AC15 grep pattern (DRIFT-5)
5. Добавить state_machine.py:1116 в scope T13.0 (DRIFT-4)
6. Добавить 5 тестовых файлов в File Manifest CREATE
7. Дополнить T13 coverage для _rollback_on_error, _trigger_deploy_hooks, parse_ssh_command (DRIFT-9)
8. Специфицировать DeployHistory.compose_state (DRIFT-10)

$END_VERIFICATION_REPORT
