$START_DEVPLAN

# DevPlan 036E — Wave 5e: Strangler-Fig миграция deploy-project.sh → deploy_engine.py + payload_deliverer.py

$ARTIFACT_CONTRACT
- **PURPOSE:** Декомпозиция критического VPS-компонента `deploy-project.sh` (1183 LOC, forced-command entrypoint) по методологии Strangler-Fig: Python-модули `deploy_engine.py` (~600 LOC) и `payload_deliverer.py` (~150 LOC) получают бизнес-логику, shell-фасад `deploy-project.sh` (~200 LOC) сохраняет trap-обработчики (ERR→rollback, EXIT→finalize) и оркестрацию lib-вызовов.
- **DESCRIPTION:** `deploy-project.sh` — крупнейший shell-монолит платформы после Wave 4 (top-3). Это VPS-side forced-command, выполняющий атомарный деплой с healthcheck-based rollback, remove/status/deliver verbs, и хуковую инвокацию. Миграция разделяет скрипт на: (1) `deploy_engine.py` — атомарный deploy/rollback/remove/status engine с mocked-Docker unit-тестами; (2) `payload_deliverer.py` — валидация и атомарное извлечение tar.gz payload; (3) shell-фасад ≤200 LOC с trap-обработчиками, lib sourcing, и verb dispatch.
- **RATIONALE:** Выполнение языковой политики (AGENTS.md: новый код — Python), устранение 3 inline python3-блоков в одном скрипте, дедупликация с `ssh_command_parser.py` (DevPlan 081) и `platform_deliver.py` (DevPlan 081), повышение тестируемости критического production-пути. deploy-project.sh содержит 11 автономных TRAP-аннотаций (grep-able) + 4 неформализованных design decisions — богатейший knowledge artifact проекта, который должен быть сохранён при миграции.
- **ACCEPTANCE_CRITERIA:**
  - AC-1: `deploy-project.sh` shell-фасад ≤200 LOC, 0 inline `python3 -c` / `<<PYEOF`
  - AC-2: `deploy_engine.py` — unit-тесты с mocked Docker (≥10 тестов, ≥80% coverage)
  - AC-3: `payload_deliverer.py` — unit-тесты с tmp_path fixtures (≥5 тестов, ≥80% coverage)
  - AC-4: Все 11 автономных TRAP-аннотаций перенесены в Python-модули как docstring-комментарии; 4 design decisions формализованы как новые TRAP[DECISION]
  - AC-5: Shell-фасад сохраняет trap handlers (ERR→rollback, EXIT→finalize), lib sourcing (logging, docker, healthcheck, paths, yaml_read), notify_hook wrapper
  - AC-6: `make test` и `make gate MODE=fast` — зелёные
  - AC-7: Staging-тест на реальной VPS: `make deploy-project PROJECT=<test> NODE=<test>` — деплой success, rollback работает, healthcheck проходит
  - AC-8: `make project-status NAME=<p> NODE=<test>` — идентичный JSON-вывод до и после миграции
  - AC-9: `make remove-project NAME=<p>` — idempotent remove, данные сохранены (без `-v`)
- **IMPLEMENTS:** Wave 5e Strangler-Fig декомпозиции deploy-project.sh (VPS forced-command)
- **IMPACTS:**
  - `core/internal/deploy/deploy-project.sh` — 1183→~200 LOC (shell facade, verb dispatch + trap handlers)
  - `core/internal/deploy/deploy_engine.py` — NEW ~600 LOC (DeployEngine: deploy/rollback/remove/status/snapshot/prune)
  - `core/internal/deploy/payload_deliverer.py` — NEW ~150 LOC (PayloadDeliverer: validate + atomic extract)
  - `tests/unit/test_deploy_engine.py` — NEW ~400 LOC (mocked Docker operations)
  - `tests/unit/test_project_registry.py` — NEW ~80 LOC (validate_project_name tests)
  - `tests/unit/test_payload_deliverer.py` — NEW ~150 LOC (tmp_path payload fixtures)
- **REQUIRES:**
  - Python ≥3.10, `pytest`, `pyyaml` (уже в проекте)
  - `core/internal/shared/ssh_command_parser.py` (DevPlan 081 — уже существует, verb classification)
  - `core/internal/shared/platform_deliver.py` (DevPlan 081 — уже существует, build/parse deliver args)
  - `core/internal/shared/deploy_paths.py` (DevPlan 081 — уже существует, canonical path registry)
  - DevPlan 036A (`domain_verifier.py` — для post-deploy verify verb, используется shell-фасадом через `exec verify.sh`)
  - DevPlan 036D (`overlay_deliverer.py` — cross-wave awareness only: overlay_deliverer используется remote-cmd.sh/node-update.sh, НЕ deploy-project.sh. Независимый execution path — runtime coupling отсутствует.)
  - Shell-библиотеки (НЕ мигрируются): `core/lib/logging.sh`, `core/lib/docker.sh`, `core/lib/healthcheck.sh`, `core/lib/paths.sh`, `core/lib/yaml_read.sh`, `core/lib/audit_logging.sh`, `core/lib/module-interface.sh`

> **Note:** DevPlan 081 planning artifact (.ai/plans/081-*) не найден на файловой системе. Модули `ssh_command_parser.py`, `platform_deliver.py`, `deploy_paths.py` верифицированы как существующие и функциональные через filesystem audit. Provenance gap не влияет на runtime deployment.
$END_ARTIFACT_CONTRACT

---

## Debt Intake

### TRAP-аудит deploy-project.sh (11 автономных TRAP + 4 design decisions, все IN_SCOPE)

Это richest TRAP-файл проекта. 11 автономных TRAP (grep-able annotations в shell) и 4 неформализованных design decisions — все переносятся в соответствующие Python-модули. Формализованные TRAP-аннотации переносятся как docstring-комментарии; design decisions становятся новыми TRAP[DECISION] при миграции.

#### Autonomous TRAP (grep-able, 11)

| # | Строка | Тип | Краткое описание | Перенос в |
|---|--------|-----|------------------|-----------|
| T1 | L28 | TRAP[DECISION] | Rollback on-node, not in CI/CD — instant recovery без network roundtrip | `deploy_engine.py` §MODULE_CONTRACT |
| T2 | L31 | TRAP[DECISION] | SSH forced-command вместо shell — security boundary, одно разрешённое действие | `deploy-project.sh` (shell-фасад, остаётся) |
| T3 | L42 | TRAP[BUG] B1 | DEPLOY_STATUS="success" после non-fatal шагов → exit 1 despite success | `deploy_engine.py` §deploy() docstring |
| T4 | L81 | TRAP[DECISION] | audit_log() replaces audit_write() — canonical from lib/audit_logging.sh | `deploy_engine.py` §_write_result() |
| T5 | L168 | TRAP[BUG] | platform-deliver exit 1 despite success — first deploy без .deploy-snapshots/ | `payload_deliverer.py` §deliver() |
| T6 | L413 | TRAP[BUG] | env var prefix в SSH_ORIGINAL_COMMAND → PROJECT=PLATFORM_DEPLOY_DIRECT=1 | `ssh_command_parser.py` (уже там) |
| T7 | L433 | TRAP[BUG] | deploy.sh path prefix not stripped → PROJECT=deploy.sh | `ssh_command_parser.py` (уже там) |
| T8 | L460 | TRAP[DECISION] | Deliver via stdin tar.gz, not sftp/git-pull — zero new channels | `payload_deliverer.py` §MODULE_CONTRACT |
| T9 | L465 | TRAP[DECISION] | platform-deliver backward compat via argument count (1=old, 2=new) | `payload_deliverer.py` §deliver() |
| T10 | L510 | TRAP[BUG] | REF="<sha> production" — env suffix leaks into image tag | `deploy_engine.py` §_parse_ref() |
| T11 | L903 | TRAP[BUSINESS] | remove = disconnect, данные не удаляются (O7/DD10) | `deploy_engine.py` §remove() |

**Итого:** 11 автономных TRAP — 5 в deploy_engine.py, 3 в payload_deliverer.py, 2 уже в ssh_command_parser.py, 1 остаётся в shell-фасаде.

#### Design decisions (to be formalized as TRAP[DECISION] в Python, 4)

Следующие 4 design decisions НЕ имеют формальных TRAP-аннотаций в shell-скрипте. Они фиксируются как новые TRAP[DECISION] в Python-модулях при миграции:

| # | Обоснование | Тип при миграции | Перенос в |
|---|------------|------------------|-----------|
| T13 | PLATFORM_DEPLOY_DIRECT detection via env prefix в SSH_ORIGINAL_COMMAND | TRAP[DECISION] | `deploy-project.sh` (shell-фасад, parse_ssh_command) |
| T14 | FQDN uniqueness check via validate.sh subprocess — pre-deploy gate | TRAP[DECISION] | `deploy_engine.py` §_preflight_checks() |
| T15 | Port conflict detection via ss -tlnp — pre-deploy gate | TRAP[DECISION] | `deploy_engine.py` §_preflight_checks() |
| T16 | STUB_AWARE_STATUS flag для stub-detection в handle_status | TRAP[DECISION] | `deploy_engine.py` §status() |

**Примечание:** T12 (L1145, inline комментарий о B1 fix) — не является самостоятельной TRAP-аннотацией. Это ссылка на T3 (TRAP[BUG] B1). Игнорируется при подсчёте TRAP для миграции.

**DEBT из других источников (cross-wave):**

| Файл | TRAP | Статус |
|------|------|--------|
| `reconciler.py:701` | `_validate_project_name()` — дубликат shell-версии из deploy-project.sh:207 | IN_SCOPE: извлечь в `core/internal/shared/project_registry.py` или использовать существующий `project_registry.py` |

### Существующий `project_registry.py` — DRY-аудит

Файл `core/internal/shared/project_registry.py` уже существует. Проверить, содержит ли он `validate_project_name()`. Если нет — добавить как shared-функцию; `deploy_engine.py` и `payload_deliverer.py` импортируют её оттуда (DRY: 3 вызова → 1 реализация).

---

## Requirements Analysis

### Ключевые критерии успеха

1. **Production safety:** deploy/rollback/remove/status работают идентично shell-версии на staging VPS
2. **Trap preservation:** все 11 автономных TRAP-аннотаций задокументированы в Python-модулях; 4 design decisions формализованы как новые TRAP[DECISION]
3. **Shell facade integrity:** trap handlers (ERR→rollback, EXIT→finalize) остаются в shell — это гарантирует recovery даже при сбое Python
4. **Testability:** Docker-операции заmocked, бизнес-логика покрыта unit-тестами
5. **DRY compliance:** `_validate_project_name()` унифицирована с `reconciler.py`, verb dispatch использует `ssh_command_parser.py`, deliver args используют `platform_deliver.py`

### Текущее состояние (baseline)

| Метрика | Значение |
|---------|----------|
| `deploy-project.sh` LOC | 1183 |
| Inline `python3 -c` / heredoc блоков | 3 (parse_ssh_command JSON extraction, platform_deliver parse, --format lines fallback) |
| Функций бизнес-логики | 14 |
| TRAP-аннотаций | 11 (grep-able) + 4 design decisions |
| Shell lib dependencies | 7 (logging, docker, healthcheck, paths, yaml_read, audit_logging, module-interface) |
| VPS-side executability | Да (forced-command в authorized_keys) |

---

## Superposition Analysis

### CRITICAL component — 6+ options required

deploy-project.sh — VPS forced-command, атомарный деплой с rollback. Ошибка = production outage. Анализ должен быть глубже, чем для других DevPlan.

---

### Option A: Full Strangler-Fig — Python engine + shell facade [score: 9/10] ⭐

**Подход:** `deploy_engine.py` (~600 LOC) получает ВСЮ бизнес-логику (deploy, rollback, remove, status, snapshot, prune, health poll). `payload_deliverer.py` (~150 LOC) — валидация и атомарное извлечение tar.gz. Shell-фасад (~200 LOC) — trap handlers, lib sourcing, verb dispatch, notify_hook wrapper.

**Trade-offs:**
- ➕ Полное устранение inline python3 (3 блока), максимальная тестируемость
- ➕ Trap handlers остаются в shell — гарантирует ERR→rollback даже при падении Python
- ➕ DRY с `ssh_command_parser.py`, `platform_deliver.py`, `project_registry.py`
- ➡️ Docker-операции нужно mock'ать в тестах (не тестируют реальный Docker)
- ➖ Требует staging-тестирования на реальной VPS перед merge

**Best when:** команда имеет staging-окружение и готова к пошаговой верификации.

---

### Option B: Reverse Strangler — Python Orchestrator, Shell Plugins [score: 7/10]

**Подход:** Python `DeployOrchestrator` — центральный engine, управляющий flow control (последовательность шагов, error handling, state machine). Shell-функции (`save_previous_image`, `pull_image_with_retry`, `atomic_up`, `perform_rollback`) вызываются через `subprocess.run()` как плагины.

**Trade-offs:**
- ➕ Чистая архитектурная граница: Python = flow control, Shell = системные вызовы
- ➕ Быстрее имплементация (меньше кода мигрировать)
- ➖ Shell-функции остаются нететированными (subprocess mocking сложнее)
- ➖ Два языка в одном execution path = debugging complexity
- ➖ Риск: subprocess overhead для каждой Docker-операции (latency до 100ms на вызов)

**Best when:** нужна быстрая миграция без полного переписывания Docker-операций.

---

### Option C: Feature-Flag Dual-Path [score: 6/10]

**Подход:** Переменная `DEPLOY_V2_ENGINE=true` в shell-фасаде. При false — старая shell-реализация. При true — Python engine. A/B сравнение на staging: деплой 50% проектов через Python, 50% через shell.

**Trade-offs:**
- ➕ Максимальная rollback safety — флаг отключается одним коммитом
- ➕ Объективные метрики сравнения (latency, error rate)
- ➖ Feature flag overhead: shell facade поддерживает обе ветки → >200 LOC (нарушает AC-1)
- ➖ Legacy shell-путь остаётся нететированным
- ➖ Дублирование maintenance work (исправление багов в двух реализациях)

**Best when:** production-стабильность критичнее скорости миграции, есть observability-инфраструктура.

---

### Option D: Inline Extraction Only — минимальная миграция [score: 5/10]

**Подход:** Извлечь только inline python3 блоки (parse_ssh_command JSON parsing, platform_deliver parse) в Python-модули. Вся остальная логика остаётся в shell.

**Trade-offs:**
- ➕ Минимальный risk для production
- ➕ Быстрая имплементация (<4 часа)
- ➖ Не решает проблему: 1183 LOC shell монолита остаётся
- ➖ Не повышает тестируемость бизнес-логики
- ➖ Не соответствует языковой политике (AGENTS.md)
- ➖ Отложенная проблема: следующий баг-фикс потребует правки shell

**Best when:** нет времени на полную миграцию, нужен quick win.

---

### Option E: Extract to Shared Docker Library [score: 7/10]

**Подход:** Общие Docker-операции (`save_previous_image`, `pull_image_with_retry`, `prune_old_images`, `capture_deploy_snapshot`) выносятся в `core/internal/shared/docker_ops.py` — общую библиотеку для deploy-project.sh, context_deployer.py, и reconciler.py. deploy-project.sh использует эту библиотеку, но остаётся shell-фасадом для trap handler'ов.

**Trade-offs:**
- ➕ Дедупликация Docker-операций между 3+ скриптами
- ➕ Постепенная миграция: сначала shared lib, потом deploy_engine
- ➖ Увеличивает scope задачи (затрагивает context_deployer.py и reconciler.py)
- ➖ Shared lib должна поддерживать оба режима: вызов из shell и из Python
- ➖ Усложняет dependency graph

**Best when:** нужна долгосрочная стратегия дедупликации Docker-операций.

---

### Option F: Leave as-is — документирование + debt registration [score: 3/10]

**Подход:** deploy-project.sh НЕ трогаем. Регистрируем как DEBT с плановой датой миграции. Добавляем TRAP-комментарий о решении.

**Trade-offs:**
- ➕ Нулевой risk для production
- ➖ Не выполняет языковую политику
- ➖ 1183 LOC shell монолит — крупнейший в проекте
- ➖ 3 inline python3 блока остаются
- ➖ Не повышает тестируемость
- ➖ Debt, который никогда не будет выплачен (исторический паттерн)

**Best when:** руководство явно запрещает трогать VPS-компонент.

---

### Option G: Full Rewrite — Python-native deploy pipeline [score: 5/10]

**Подход:** Полное переписывание deploy pipeline на Python без shell-фасада. Python-скрипт напрямую вызывается из authorized_keys forced-command.

**Trade-offs:**
- ➕ Максимальная тестируемость и maintainability
- ➕ Соответствие языковой политике на 100%
- ➖ Потеря shell trap-механизма (ERR→rollback, EXIT→finalize) — нужно реализовывать в Python (atexit + signal handlers)
- ➖ Python signal handling ≠ shell trap semantics — риск непредсказуемого поведения при SIGTERM/SIGKILL
- ➖ Риск: Python process может быть убит до вызова atexit handler → rollback не выполнится
- ➖ Полная перестройка контракта authorized_keys

**Best when:** есть полная confidence в Python signal handling и atexit guarantees.

---

### Multi-Dimensional Scoring Matrix

| Dimension | A (Full SF) | B (Reverse) | C (Flagged) | D (Inline) | E (Shared Lib) | F (Leave) | G (Rewrite) |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Production safety | 8 | 7 | 9 | 9 | 7 | 10 | 3 |
| Testability gain | 9 | 6 | 7 | 4 | 8 | 0 | 10 |
| Lang policy compliance | 9 | 7 | 7 | 4 | 7 | 0 | 10 |
| Implementation speed | 6 | 7 | 5 | 9 | 5 | 10 | 4 |
| Rollback safety | 7 | 6 | 10 | 9 | 6 | 10 | 2 |
| Shell facade size | 9 | 6 | 4 | 8 | 7 | 0 | 10 |
| DRY gain (shared code) | 8 | 5 | 5 | 3 | 9 | 0 | 8 |
| Debuggability | 7 | 5 | 6 | 8 | 6 | 9 | 7 |
| **Composite** | **7.9** | **6.1** | **6.6** | **6.8** | **6.9** | **4.9** | **6.8** |

### ## @rationale: Recommendation — Option A (Full Strangler-Fig, score: 7.9 composite)

**Q:** Почему Option A, а не более безопасный Option F (leave as-is) или более быстрый Option D (inline only)?

**A:**
1. **Wave 4 precedent:** Option A успешно применён к топ-3 скриптам (4114→392 LOC, 204/210 tests). Процесс отлажен, риски известны.
2. **Trap handlers в shell:** В отличие от Option G (full rewrite), Option A сохраняет ERR→rollback trap в shell. Это КЛЮЧЕВОЕ преимущество — даже если Python engine упадёт с segfault, shell trap сработает и инициирует rollback. Python `atexit` + `signal` handlers не дают такой гарантии.
3. **Option D (inline only):** Отклонён — не решает проблему, откладывает неизбежное. 1183 LOC shell будет расти с каждым новым требованием.
4. **Option E (shared lib first):** Хорошая долгосрочная стратегия, но увеличивает scope. Может быть выполнена как FOLLOW-UP после Wave 5e.
5. **Option C (feature flags):** Отклонён — нарушает AC-1 (shell facade >200 LOC). Feature flag overhead для VPS forced-command неоправдан: каждая дополнительная строка в `authorized_keys command="..."` — риск.
6. **Option B (Reverse Strangler):** Отклонён — subprocess overhead для каждой Docker-операции добавляет latency; shell-функции остаются нететированными.

**Decision:** Option A — Full Strangler-Fig с shell-фасадом ≤200 LOC, trap handlers в shell, бизнес-логика в Python.

---

## Step-by-Step Data Flow

### Before (deploy-project.sh, 1183 LOC)

```
SSH forced-command
    │
    ▼
deploy-project.sh (main)
    ├── parse_ssh_command() ───────────────────────────────────────────
    │   ├── read SSH_ORIGINAL_COMMAND
    │   ├── strip env var prefixes (PLATFORM_DEPLOY_DIRECT=1)
    │   ├── delegate to ssh_command_parser.py (verb classification)
    │   │   └── ⚠️ inline python3 -c для JSON extraction (TASK-081B8)
    │   ├── verb=platform-deliver → dispatch handle_deliver()
    │   ├── verb=verify → exec verify.sh
    │   └── verb=deploy → set PROJECT, REF, PROJECT_DIR, SERVICE_NAME
    │
    ├── main() deploy path ────────────────────────────────────────────
    │   ├── audit_log START
    │   ├── _do_deploy():
    │   │   ├── save_previous_image()      # Docker inspect
    │   │   ├── capture_deploy_snapshot()  # Docker ps/images
    │   │   ├── FQDN uniqueness check      # validate.sh subprocess
    │   │   ├── docker_login()             # lib/docker.sh
    │   │   ├── pull_image_with_retry()    # 3 attempts, backoff [5,10,20]s
    │   │   ├── port conflict check        # ss -tlnp
    │   │   ├── atomic_up()                # docker compose up -d
    │   │   ├── poll_until_healthy()        # healthcheck.sh, ≤60s
    │   │   ├── [SUCCESS]
    │   │   │   ├── DEPLOY_STATUS="success"
    │   │   │   ├── trap - ERR             # disable rollback trap
    │   │   │   ├── tag_current()          # Docker tag :current
    │   │   │   ├── prune_old_images()     # Keep N=3
    │   │   │   ├── _trigger_deploy_hooks()
    │   │   │   ├── audit_log DONE
    │   │   │   └── notify_hook ✅
    │   │   └── [FAIL]
    │   │       ├── FIRST_DEPLOY → handle_first_deploy() → exit 1
    │   │       ├── DEPLOY_STATUS="degraded"
    │   │       ├── perform_rollback()     # re-tag + compose up --force-recreate
    │   │       ├── audit_log ROLLBACK
    │   │       └── notify_hook ⚠️
    │   └── audit_step wrapper (START/DONE/FAIL)
    │
    ├── handle_deliver() ──────────────────────────────────────────────
    │   ├── audit_log DELIVER-START
    │   ├── _validate_project_name()
    │   ├── read stdin with 1 MiB cap (head -c)
    │   ├── extract tar.gz to mktemp -d
    │   ├── validate content:
    │   │   ├── no subdirectories (path traversal)
    │   │   ├── no symlinks
    │   │   ├── no non-regular files
    │   │   ├── no hardlinks
    │   │   └── whitelist check (compose, yaml, env)
    │   ├── atomic mv to PROJECTS_BASE/<org>/<project>
    │   └── audit_log DELIVER-SUCCESS
    │
    ├── handle_remove() ───────────────────────────────────────────────
    │   ├── validate project dir exists
    │   ├── docker compose down --timeout 30 (БЕЗ -v)
    │   ├── _trigger_remove_hooks()
    │   └── audit_log DONE
    │
    ├── handle_status() ───────────────────────────────────────────────
    │   ├── not_found → JSON {status:"not_found"}
    │   ├── stub detection → JSON {status:"stub"}
    │   └── found → docker ps JSON + deploy-result.json
    │
    └── Trap handlers ──────────────────────────────────────────────────
        ├── ERR trap → _rollback_on_error() → _restore_from_snapshot() → exit 1
        └── EXIT trap → _finalize_deploy() → _write_deploy_result()
```

### After (deploy-project.sh ≤200 LOC + deploy_engine.py + payload_deliverer.py)

```
SSH forced-command
    │
    ▼
deploy-project.sh (~200 LOC, shell facade)
    ├── source libs (logging, docker, healthcheck, paths, yaml_read, audit_logging)
    ├── trap handlers (ERR → rollback, EXIT → finalize)  ← ОСТАЮТСЯ В SHELL
    ├── notify_hook() wrapper (тонкая обёртка)
    │
    ├── parse_ssh_command()
    │   ├── strip env var prefixes (shell)
    │   ├── delegate to ssh_command_parser.py for verb classification
    │   │   └── python3 -m core.internal.shared.ssh_command_parser --format lines parse "$raw"
    │   │       (уже TASK-081B8 — 0 inline python3)
    │   └── dispatch:
    │       ├── verb=platform-deliver:
    │       │   └── python3 -m core.internal.shared.platform_deliver parse "$args"
    │       │       → python3 -m core.internal.deploy.payload_deliverer deliver <project> [<org>]
    │       │
    │       ├── verb=deploy:
    │       │   ├── set PROJECT, REF, PROJECT_DIR, SERVICE_NAME (shell)
    │       │   ├── audit_log START (shell)
    │       │   ├── docker_login (shell → lib/docker.sh)
    │       │   ├── FQDN check (shell → validate.sh subprocess)
    │       │   ├── python3 -m core.internal.deploy.deploy_engine deploy \
    │       │   │       --project $PROJECT --ref $REF --service $SERVICE_NAME \
    │       │   │       --project-dir $PROJECT_DIR --node $NODE_NAME \
    │       │   │       --max-wait $MAX_WAIT_SEC --keep-images $KEEP_IMAGES
    │       │   │   └── Returns DeployResult(success, rollback_performed, previous_image)
    │       │   ├── [success] → tag_current, prune, hooks, audit_log, notify_hook (shell)
    │       │   └── [failure] → ERR trap fires → _rollback_on_error() → exit 1
    │       │
    │       ├── verb=remove:
    │       │   └── python3 -m core.internal.deploy.deploy_engine remove \
    │       │           --project $PROJECT --project-dir $PROJECT_DIR
    │       │
    │       └── verb=status:
    │           └── python3 -m core.internal.deploy.deploy_engine status \
    │                   --project $PROJECT --project-dir $PROJECT_DIR [--stub-aware]
    │
    └── Exit → EXIT trap → _finalize_deploy() → _write_deploy_result()

───────────────────────────────────────────────────────────────────────
core/internal/deploy/deploy_engine.py (~600 LOC, Python)
───────────────────────────────────────────────────────────────────────
class DeployEngine:
    ├── deploy(project, ref, service, project_dir, node, max_wait, keep_images) → DeployResult
    │   ├── _save_previous_image(project_dir, service) → Optional[ImageInfo]
    │   ├── _capture_deploy_snapshot(project_dir) → SnapshotInfo
    │   ├── _preflight_checks(project_dir, service) → void (raise on fail)
    │   │   ├── FQDN uniqueness (Subprocess: validate.sh --check-fqdn)
    │   │   └── port conflict (Subprocess: ss -tlnp)
    │   ├── _pull_image_with_retry(project_dir, service, ref, max_attempts=3) → bool
    │   │   └── backoff: [5, 10, 20]s; detect rate-limit pattern
    │   ├── _atomic_up(project_dir, service, ref) → bool
    │   ├── _poll_health(project_dir, service, timeout, interval) → bool
    │   │   └── delegate to lib/healthcheck.sh check_docker_health
    │   ├── [health OK] → DeployResult(success=True, ...)
    │   └── [health FAIL]
    │       ├── first_deploy → DeployResult(success=False, first_deploy_failed=True)
    │       └── not first → _perform_rollback(project_dir, service, previous_image)
    │           → DeployResult(success=False, rollback_performed=True)
    │
    ├── remove(project, project_dir) → RemoveResult
    │   ├── _validate_project_exists(project_dir)
    │   ├── docker compose down --timeout 30 (БЕЗ -v / TRAP[BUSINESS] O7)
    │   └── RemoveResult(success, project, already_removed)
    │
    ├── status(project, project_dir, stub_aware=False) → StatusResult
    │   ├── not_found → StatusResult(status="not_found")
    │   ├── stub detection → StatusResult(status="stub")
    │   └── found → docker ps JSON + deploy-result.json
    │       → StatusResult(status="found", containers=[...], last_deploy={...})
    │
    └── _validate_project_name(name) → bool  (импорт из project_registry.py)

───────────────────────────────────────────────────────────────────────
core/internal/deploy/payload_deliverer.py (~150 LOC, Python)
───────────────────────────────────────────────────────────────────────
class PayloadDeliverer:
    └── deliver(project, org, projects_base, stdin=sys.stdin.buffer) → DeliverResult
        ├── _read_payload(stdin, max_size=1_MiB) → bytes
        │   └── 🧐 TRAP[DECISION] T8: stdin is the ONLY delivery channel
        ├── _validate_and_extract(tar_bytes, tmp_dir) → list[Path]
        │   ├── no subdirectories (path traversal defense)
        │   ├── no symlinks / hardlinks / non-regular files
        │   └── whitelist: docker-compose.yml | compose.yaml | ai-platform.yaml | .env.platform
        ├── _atomic_move(files, target_dir) → void
        └── DeliverResult(success, project, org, files_delivered)

```

### Shell facade — verb dispatch detail

```bash
# deploy-project.sh (~200 LOC)

# 1. Source libs (REQUIRED — НЕ мигрируются)
source core/lib/logging.sh
source core/lib/docker.sh        # docker_login()
source core/lib/healthcheck.sh   # check_docker_health(), poll_until_healthy()
source core/lib/paths.sh
source core/lib/yaml_read.sh
source core/lib/audit_logging.sh # audit_log(), audit_step()
source core/lib/module-interface.sh  # invoke_module_interface() for hooks

# 2. Trap handlers (ОСТАЮТСЯ В SHELL — critical safety net)
trap '_rollback_on_error' ERR
trap '_finalize_deploy' EXIT

# 3. notify_hook wrapper (тонкая обёртка)
notify_hook() { ... }  # остаётся shell, вызывает notify-hook.sh

# 4. parse_ssh_command()
#    → ssh_command_parser.py (уже Python, 0 inline)
#    → platform_deliver.py parse (уже Python, 0 inline)

# 5. Verb dispatch (все ветки → python3 -m ...)
case "$verb" in
    platform-deliver)
        # PayloadDeliverer: stdin tar.gz → validate → atomic extract
        python3 -m core.internal.deploy.payload_deliverer deliver "$project" ${org:+"$org"}
        exit $?
        ;;
    deploy)
        # DeployEngine: atomic deploy + healthcheck + rollback
        python3 -m core.internal.deploy.deploy_engine deploy \
            --project "$PROJECT" --ref "$REF" --service "$SERVICE_NAME" \
            --project-dir "$PROJECT_DIR" --node "$NODE_NAME" \
            --max-wait "$MAX_WAIT_SEC" --keep-images "$KEEP_IMAGES"
        # ... post-deploy non-fatal steps in shell (tag, prune, hooks, audit, notify)
        ;;
    remove)
        python3 -m core.internal.deploy.deploy_engine remove \
            --project "$PROJECT" --project-dir "$PROJECT_DIR"
        exit $?
        ;;
    status)
        python3 -m core.internal.deploy.deploy_engine status \
            --project "$PROJECT" --project-dir "$PROJECT_DIR" ${STUB_AWARE:+--stub-aware}
        exit $?
        ;;
esac
```

---

## Draft Code Graph

```
core/internal/
├── deploy/
│   ├── deploy-project.sh         # MODIFIED: 1183 → ~200 LOC
│   │   ├── trap handlers (ERR→rollback, EXIT→finalize)  # ОСТАЮТСЯ
│   │   ├── lib sourcing (7 libs)                        # ОСТАЮТСЯ
│   │   ├── notify_hook() wrapper                        # ОСТАЁТСЯ
│   │   ├── parse_ssh_command() → ssh_command_parser.py  # УЖЕ Python
│   │   ├── verb dispatch (4 verbs → python3 -m ...)      # НОВЫЙ
│   │   └── post-deploy non-fatal (tag, prune, hooks, audit, notify) # ОСТАЮТСЯ
│   │
│   ├── deploy_engine.py          # NEW ~600 LOC
│   │   └── class DeployEngine:
│   │       ├── deploy() → DeployResult
│   │       ├── remove() → RemoveResult
│   │       ├── status() → StatusResult
│   │       ├── _save_previous_image() → Optional[ImageInfo]
│   │       ├── _capture_deploy_snapshot() → SnapshotInfo
│   │       ├── _preflight_checks() → void
│   │       ├── _pull_image_with_retry() → bool
│   │       ├── _atomic_up() → bool
│   │       ├── _poll_health() → bool
│   │       ├── _perform_rollback() → bool
│   │       ├── _handle_first_deploy() → NoReturn
│   │       ├── _validate_project_name() → bool (→ project_registry)
│   │       └── CLI: __main__ with argparse (deploy/remove/status subcommands)
│   │
│   └── payload_deliverer.py      # NEW ~150 LOC
│       └── class PayloadDeliverer:
│           ├── deliver() → DeliverResult
│           ├── _read_payload() → bytes
│           ├── _validate_and_extract() → list[Path]
│           ├── _validate_entry() → void
│           ├── _atomic_move() → void
│           └── CLI: __main__ with argparse (deliver subcommand)
│
├── shared/
│   ├── ssh_command_parser.py     # EXISTS (DevPlan 081) — verb classification
│   ├── platform_deliver.py       # EXISTS (DevPlan 081) — build/parse deliver args
│   ├── deploy_paths.py           # EXISTS (DevPlan 081) — canonical path registry
│   └── project_registry.py       # EXISTS — добавить validate_project_name()

tests/unit/
├── test_deploy_engine.py         # NEW ~400 LOC (mocked Docker)
├── test_project_registry.py      # NEW ~80 LOC (validate_project_name tests)
└── test_payload_deliverer.py     # NEW ~150 LOC (tmp_path fixtures)
```

### Data classes (dataclass-driven DeployResult)

```python
@dataclass
class DeployResult:
    success: bool
    project: str
    ref: str
    service: str
    previous_image: Optional[str] = None
    rollback_performed: bool = False
    first_deploy_failed: bool = False
    error_message: Optional[str] = None

@dataclass
class RemoveResult:
    success: bool
    project: str
    already_removed: bool = False
    error_message: Optional[str] = None

@dataclass
class StatusResult:
    project: str
    node: str
    status: str  # "found" | "not_found" | "stub"
    containers: list[dict] = field(default_factory=list)
    last_deploy: Optional[dict] = None

@dataclass
class DeliverResult:
    success: bool
    project: str
    org: Optional[str] = None
    files_delivered: int = 0
    error_message: Optional[str] = None

@dataclass
class ImageInfo:
    id: str
    tag: Optional[str] = None

@dataclass
class SnapshotInfo:
    timestamp: int
    ps_file: Optional[str] = None
    images_file: Optional[str] = None
```

---

## Design Decisions

### ## @rationale D1: deploy_engine + payload_deliverer — TWO separate modules, not one

**Q:** Почему два модуля, а не один `deploy_orchestrator.py`?

**A:** `deploy_engine.py` и `payload_deliverer.py` обслуживают РАЗНЫЕ verbs:
- `platform-deliver` → `payload_deliverer.py` — payload extraction, validation, atomic file move. Zero Docker dependency. Чистый I/O + файловая система + tar.
- `deploy/remove/status` → `deploy_engine.py` — Docker-операции, healthcheck, rollback, state management. Требует Docker.

По AI-First Architecture (DDD): один модуль = одна бизнес-ответственность. Payload delivery и deploy orchestration — разные домены. `payload_deliverer.py` может быть переиспользован другими entrypoints (например, `reconcile-projects.sh` для bulk project delivery). `deploy_engine.py` может быть переиспользован `context_deployer.py` для деплоя контекстных проектов.

Объединение в один модуль создало бы coupling между разнородными подсистемами. **Rejected:** единый `deploy_orchestrator.py` — нарушает DDD, создаёт God Class >800 LOC.

### ## @rationale D2: Trap handlers (ERR→rollback, EXIT→finalize) остаются в shell

**Q:** Почему trap handlers не мигрируют в Python (atexit + signal handlers)?

**A:** Shell trap handlers имеют более сильные гарантии:
1. **ERR trap** срабатывает при ЛЮБОМ ненулевом exit code подоболочки — включая segfault Python-процесса. Python `atexit` не гарантирует выполнение при SIGSEGV.
2. **EXIT trap** гарантированно вызывается при завершении shell-процесса (нормальном или аварийном). Python `atexit` не вызывается при `os._exit()` или SIGKILL.
3. **Double EXIT-trap** — bash гарантирует однократное выполнение (защита от двойного вызова).
4. Shell trap — это последний рубеж обороны. Если Python engine упал — shell всё равно вызовет `_finalize_deploy()` и запишет `deploy-result.json` со статусом `failed`.

**Rejected:** Python-only error handling — теряет гарантии shell trap, требует реализации signal handlers для SIGTERM/SIGINT, не покрывает SIGSEGV.

### ## @rationale D3: Non-fatal steps (tag_current, prune, hooks, audit, notify) остаются в shell

**Q:** Почему tag_current(), prune_old_images(), _trigger_deploy_hooks(), audit_log(), notify_hook() не мигрируют в Python?

**A:** Это **non-fatal housekeeping** операции (TRAP[BUG] B1: после health-gate `trap - ERR` — ни одна не должна ронять success). Они:
1. Не содержат бизнес-логики — чистые Docker/shell вызовы
2. Выполняются ПОСЛЕ того, как Python engine вернул `DeployResult(success=True)`
3. Их failure должен быть logged, но не должен менять DEPLOY_STATUS="success"

Перенос в Python создал бы риск: Python-функция может raise exception → exception не будет пойман shell trap (т.к. trap - ERR уже отключен) → deploy_result.json не запишется. Оставляя эти шаги в shell, мы сохраняем контракт B1 fix: DEPLOY_STATUS="success" уже установлен, все дальнейшие шаги обёрнуты в `|| log_imp`.

**Rejected:** Python housekeeping — добавляет complexity без прироста тестируемости (эти операции не содержат business rules).

### ## @rationale D4: Docker-операции в Python → subprocess.run, не docker-py SDK

**Q:** Почему для Docker-операций используется `subprocess.run(["docker", "compose", ...])`, а не `docker-py` SDK?

**A:**
1. **Zero new dependencies:** `docker-py` — внешняя зависимость с собственной цепочкой обновлений и CVE. `subprocess.run` — stdlib.
2. **Identical behavior:** `docker compose` CLI — это то, что администратор использует вручную. docker-py SDK может иметь subtle differences в поведении (особенно compose v2).
3. **Mockability:** `subprocess.run` легче mock'ать в unit-тестах (патчинг `subprocess.run` с проверкой аргументов), чем сложный SDK с множеством цепочек вызовов.
4. **Precedent:** `context_deployer.py` и `docker_orchestrator.py` уже используют `subprocess.run` для Docker — консистентность с существующим кодом.

**Rejected:** docker-py SDK — добавляет dependency, усложняет mocking, может иметь subtle behavior differences.

### ## @rationale D5: Rollback logic мигрирует как есть, с полным сохранением семантики

**Q:** Atomic rollback — как гарантировать идентичность поведения?

**A:** Логика rollback тестируется через unit-тесты с mocked `subprocess.run`. Каждый Docker-вызов в `_perform_rollback()` проверяется:
1. `docker tag <prev_id> <prev_tag>` — вызывается с правильными аргументами
2. `docker compose up -d --force-recreate <service>` — вызывается с `--force-recreate`
3. При failure: возвращает `DeployResult(success=False, rollback_performed=False, error_message=...)`
4. Shell facade на основе результата вызывает `notify_hook` с деталями

Семантика shell-версии `perform_rollback()` сохранена один-в-один: re-tag → compose up --force-recreate → audit ROLLBACK → notify ⚠️ → exit 1. Отличие: audit и notify остаются в shell-фасаде (см. D3).

Интеграционный тест rollback требует staging VPS (см. Integration Test Plan).

**Rejected:** изменение rollback-стратегии (например, Docker swarm rollback) — out of scope, не предусмотрено архитектурой.

### ## @rationale D6: PayloadDeliverer использует tmp_path для тестов, НЕ реальный stdin

**Q:** Как тестировать `payload_deliverer.deliver()`, если он читает stdin?

**A:** Сигнатура метода принимает `stdin: BinaryIO = sys.stdin.buffer`. В production — sys.stdin.buffer. В тестах — `io.BytesIO(tar_bytes)`. Это позволяет:
1. Генерировать валидные/невалидные tar.gz через `tarfile` + `io.BytesIO`
2. Проверять edge cases: пустой payload, превышение размера, symlinks, path traversal
3. Тестировать без реального stdin (pytest не поддерживает stdin injection)

`_atomic_move()` тестируется через `tmp_path` fixture (временная директория → целевая директория).

**Rejected:** тестирование через subprocess.run (реальный stdin) — ненадёжно, не изолировано.

### ## @rationale D7: _validate_project_name() — DRY extraction в project_registry.py

**Q:** Почему `_validate_project_name()` извлекается в `project_registry.py`, а не остаётся локальной?

**A:** `_validate_project_name()` дублируется в трёх местах:
1. `deploy-project.sh:207` (shell — мигрирует)
2. `reconciler.py:701` (Python — уже существует)
3. `payload_deliverer.py` (Python — новая)

DRY principle: единая каноническая реализация в `core/internal/shared/project_registry.py`. Все три consumer'а импортируют `from core.internal.shared.project_registry import validate_project_name`. При изменении правил валидации (допустимые символы, длина) — правка в одном месте.

**Примечание:** `reconciler.py` использует regex `^[a-zA-Z0-9_-]+$`, shell-версия проверяет только `/` и `..`. После унификации — regex (строже, покрывает оба случая).

> **ℹ️ Test location note:** Unit-тесты для `validate_project_name()` пишутся в `tests/unit/test_project_registry.py` (тестируют `project_registry.validate_project_name()` напрямую). `test_deploy_engine.py` получает ровно 1 integration call-through test (`test_deploy_calls_validate_project_name`), который верифицирует, что `DeployEngine.deploy()` вызывает shared-функцию. Это предотвращает DRY-нарушение, когда тесты shared-функции живут в тестовом файле consumer'а.

### ## @rationale D8: CLI интерфейс — argparse subcommands, НЕ единый verb dispatch

**Q:** Почему Python-модули используют argparse с subcommands, а не единый entrypoint с verb-диспетчеризацией как в shell?

**A:** Shell использует verb dispatch потому что forced-command контракт: `command="deploy-project.sh $SSH_ORIGINAL_COMMAND"`. Python CLI вызывается из shell-фасада, который уже выполнил verb classification (через `ssh_command_parser.py`). Поэтому Python-модули получают конкретный verb:
- `python3 -m core.internal.deploy.deploy_engine deploy --project ... --ref ...`
- `python3 -m core.internal.deploy.payload_deliverer deliver <project> [<org>]`

Это улучшает:
1. **Debuggability:** subcommand + named args читаются в логах
2. **Testability:** можно тестировать конкретный subcommand без verb dispatch
3. **Composability:** другие entrypoints могут вызывать `deploy_engine.deploy()` напрямую (Python import), без CLI

**Rejected:** единый verb dispatch в Python — дублирует логику `ssh_command_parser.classify_verb()`, усложняет тестирование.

---

## $TASKS

### TASK-036E1: deploy_engine.py — DeployEngine + unit tests
- **Owner:** Coder
- **Output:** `core/internal/deploy/deploy_engine.py` (~600 LOC), `tests/unit/test_deploy_engine.py` (~400 LOC)
- **Acceptance:**
  - Все методы DeployEngine покрыты unit-тестами (≥10 тестов)
  - Docker-операции заmocked через `unittest.mock.patch("subprocess.run")`
  - Shell-фасад `deploy-project.sh` обновлён: deploy/remove/status ветки вызывают `python3 -m core.internal.deploy.deploy_engine`
  - `make test` зелёный (unit tests)
  - Все 11 автономных TRAP перенесены + 4 design decisions формализованы в docstring deploy_engine.py
- **Dependencies:** DevPlan 036A (domain_verifier для verify verb — используется shell-фасадом); DevPlan 036D (overlay_deliverer — cross-wave awareness only, не runtime dependency)
- **Complexity:** 9/10 (CRITICAL — VPS component)
- **Checkpoint:** `make test` зелёный, ≥10 unit tests pass с mocked Docker

### TASK-036E2: payload_deliverer.py — PayloadDeliverer + unit tests
- **Owner:** Coder
- **Output:** `core/internal/deploy/payload_deliverer.py` (~150 LOC), `tests/unit/test_payload_deliverer.py` (~150 LOC)
- **Acceptance:**
  - Все методы PayloadDeliverer покрыты unit-тестами (≥5 тестов)
  - Payload validation тестируется через BytesIO (tar.gz in memory)
  - Atomic extraction тестируется через tmp_path
  - Shell-фасад `deploy-project.sh` обновлён: platform-deliver ветка вызывает `python3 -m core.internal.deploy.payload_deliverer`
  - TRAP T5, T8, T9 перенесены в docstring payload_deliverer.py
- **Dependencies:** None (независим от DeployEngine — разные модули)
- **Complexity:** 5/10
- **Checkpoint:** `make test` зелёный, ≥5 unit tests pass с tmp_path fixtures

### TASK-036E3: Shell facade — deploy-project.sh reduction + verb dispatch
- **Owner:** Coder
- **Output:** `core/internal/deploy/deploy-project.sh` (1183→~200 LOC)
- **Acceptance:**
  - Shell facade ≤200 LOC
  - 0 inline `python3 -c` / `<<PYEOF`
  - Trap handlers сохранены (ERR→rollback, EXIT→finalize)
  - Все 7 lib sourcing сохранены
  - Verb dispatch: deploy → deploy_engine.py, remove → deploy_engine.py, status → deploy_engine.py, platform-deliver → payload_deliverer.py
  - Non-fatal post-deploy steps (tag_current, prune, hooks, audit, notify) остаются в shell
- **Dependencies:** TASK-036E1, TASK-036E2 (shell facade вызывает оба Python-модуля)
- **Complexity:** 4/10
- **Checkpoint:** `wc -l deploy-project.sh` ≤200, `grep "python3 -c\|<<PYEOF"` → 0 matches

### TASK-036E4: project_registry.py — DRY: extract validate_project_name
- **Owner:** Coder
- **Output:** `core/internal/shared/project_registry.py` (MODIFIED — добавлена `validate_project_name()`)
- **Acceptance:**
  - `validate_project_name()` в `project_registry.py` использует regex `^[a-zA-Z0-9_-]+$`
  - `reconciler.py`, `deploy_engine.py`, `payload_deliverer.py` импортируют из `project_registry`
  - Shell `_validate_project_name()` удалена (используется Python-версия)
- **Dependencies:** TASK-036E1, TASK-036E2 (оба модуля должны импортировать из project_registry)
- **Complexity:** 2/10
- **Checkpoint:** `grep "_validate_project_name" deploy-project.sh` → 0 matches (вызов через Python CLI)

### TASK-036E5: Staging integration test — deploy on real VPS
- **Owner:** QA
- **Output:** `02-VerificationReport.md`
- **Acceptance:**
  - `make deploy-project PROJECT=<test> NODE=<staging>` — deploy success, healthcheck green
  - `make project-status NAME=<test> NODE=<staging>` — JSON output идентичен pre-migration
  - `make remove-project NAME=<test>` — idempotent remove, данные сохранены
  - Rollback test: deploy broken image → auto-rollback → previous image restored
  - payload deliver test: tar + ssh platform-deliver → файлы на VPS
- **Dependencies:** TASK-036E3 (shell facade complete)
- **Complexity:** 3/10 (ручное тестирование на staging VPS)
- **Checkpoint:** Все 4 сценария пройдены на staging VPS, отчёт приложен

### TASK-036E6: Pre-merge gate + fix-gate
- **Owner:** Coder
- **Output:** Green CI gate
- **Acceptance:**
  - `make fix-gate && make gate MODE=fast` — зелёные
  - `make test` — все тесты зелёные (unit + existing)
  - `make check-file-lines` — deploy-project.sh ≤200 LOC
- **Dependencies:** TASK-036E3, TASK-036E4
- **Complexity:** 2/10
- **Checkpoint:** Все gate checks зелёные

---

## $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
- **Tasks:** TASK-036E1 (deploy_engine.py), TASK-036E2 (payload_deliverer.py)
- **Rationale:** Разные модули, разные тестовые файлы, zero file intersection. Могут разрабатываться параллельно.
- **Command:** `coder Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, implement Wave 1: TASK-036E1, TASK-036E2`

### Wave 2 (depends on Wave 1)
- **Tasks:** TASK-036E3 (shell facade), TASK-036E4 (project_registry DRY)
- **Rationale:** Shell facade вызывает оба Python-модуля. project_registry — shared dependency для обоих модулей.
- **Command:** `coder Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, implement Wave 2: TASK-036E3, TASK-036E4`

### Wave 3 (depends on Waves 1+2)
- **Tasks:** TASK-036E5 (staging integration test)
- **Rationale:** Staging test только после того, как shell facade готов.
- **Command:** `qa Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, run staging integration test: TASK-036E5`

### Wave 4 (depends on Waves 1+2)
- **Tasks:** TASK-036E6 (pre-merge gate)
- **Command:** `coder Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, run pre-merge gate: TASK-036E6`

---

## Task Dependency Graph

```
TASK-036E1 (deploy_engine.py) ──┐
TASK-036E2 (payload_deliverer.py) ┤ (parallel, independent)
                                  │
                                  ▼
                          TASK-036E3 (shell facade)
                          TASK-036E4 (project_registry DRY)
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
            TASK-036E5 (staging test)    TASK-036E6 (gate check)
                                          (может идти параллельно)
```

---

## Acceptance Criteria Summary

| ID | Критерий | Метод проверки |
|----|----------|---------------|
| AC-1 | Shell facade ≤200 LOC, 0 inline python3 | `wc -l deploy-project.sh`; `grep "python3 -c\|<<PYEOF" deploy-project.sh` |
| AC-2 | deploy_engine.py ≥10 unit tests, ≥80% coverage | `pytest tests/unit/test_deploy_engine.py --cov=core.internal.deploy.deploy_engine -v` |
| AC-3 | payload_deliverer.py ≥5 unit tests, ≥80% coverage | `pytest tests/unit/test_payload_deliverer.py --cov=core.internal.deploy.payload_deliverer -v` |
| AC-4 | Все 11 автономных TRAP + 4 design decisions перенесены/формализованы | `grep "TRAP\[" deploy_engine.py payload_deliverer.py` → ≥14 matches (≥11 в deploy_engine.py + ≥3 в payload_deliverer.py) |
| AC-5 | Trap handlers сохранены в shell | `grep "trap.*ERR\|trap.*EXIT" deploy-project.sh` → 2 matches |
| AC-6 | `make test && make gate MODE=fast` зелёные | CI run |
| AC-7 | Staging deploy успешен | Ручное тестирование: deploy + rollback + status + remove |
| AC-8 | Status JSON идентичен pre-migration | diff `status-before.json` `status-after.json` (same project) |
| AC-9 | Remove идемпотентен, данные сохранены | `docker compose down` без `-v`; volumes/DB/images НЕ удалены |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_project_registry.py` | `test_validate_project_name_valid` | Валидное имя `my-project_01` → True | `project_registry.validate_project_name()` |
| `tests/unit/test_project_registry.py` | `test_validate_project_name_traversal` | Имя `../escape` → False | `project_registry.validate_project_name()` |
| `tests/unit/test_project_registry.py` | `test_validate_project_name_slash` | Имя `foo/bar` → False | `project_registry.validate_project_name()` |
| `tests/unit/test_project_registry.py` | `test_validate_project_name_empty` | Пустая строка → False | `project_registry.validate_project_name()` |
| `tests/unit/test_deploy_engine.py` | `test_save_previous_image_exists` | Docker возвращает image ID → ImageInfo returned | `deploy_engine._save_previous_image()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_save_previous_image_first_deploy` | Docker возвращает пустой ID → None (first deploy) | `deploy_engine._save_previous_image()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_pull_image_with_retry_success` | Первая попытка успешна → True | `deploy_engine._pull_image_with_retry()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_pull_image_with_retry_rate_limit` | Rate-limit → retry 2 раза → success | `deploy_engine._pull_image_with_retry()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_pull_image_with_retry_fail_all` | 3 попытки → all fail → raise DeployError | `deploy_engine._pull_image_with_retry()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_atomic_up_success` | docker compose up -d → success (rc=0) | `deploy_engine._atomic_up()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_atomic_up_failure` | docker compose up -d → fail (rc=1) → raise | `deploy_engine._atomic_up()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_poll_health_healthy` | Health check returns True на 3-й попытке → True | `deploy_engine._poll_health()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_poll_health_timeout` | Health check always False → timeout → False | `deploy_engine._poll_health()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_perform_rollback_success` | Re-tag + compose up --force-recreate → success | `deploy_engine._perform_rollback()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_perform_rollback_failure` | Re-tag success, compose up fail → raise | `deploy_engine._perform_rollback()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_deploy_full_success_flow` | Full deploy pipeline → DeployResult(success=True) | `deploy_engine.deploy()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_deploy_health_fail_triggers_rollback` | Health fail → auto-rollback → DeployResult(success=False, rollback=True) | `deploy_engine.deploy()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_deploy_first_deploy_fail` | First deploy, health fail → first_deploy_failed=True | `deploy_engine.deploy()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_remove_idempotent` | Повторный remove → RemoveResult(already_removed=True) | `deploy_engine.remove()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_remove_not_found` | Проект не существует → RemoveResult(already_removed=True) | `deploy_engine.remove()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_status_not_found` | Директория не существует → StatusResult(status="not_found") | `deploy_engine.status()` |
| `tests/unit/test_deploy_engine.py` | `test_status_stub` | ai-platform.yaml = GENERATED-STUB → status="stub" | `deploy_engine.status(stub_aware=True)` |
| `tests/unit/test_deploy_engine.py` | `test_status_found` | Проект существует, контейнеры running → status="found" | `deploy_engine.status()` (mocked docker) |
| `tests/unit/test_deploy_engine.py` | `test_capture_snapshot_creates_files` | Snapshot создаёт ps + images файлы | `deploy_engine._capture_deploy_snapshot()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_prune_old_images_below_limit` | Изображений ≤ KEEP → no-op | `deploy_engine._prune_old_images()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_prune_old_images_above_limit` | Изображений > KEEP → удаление старых | `deploy_engine._prune_old_images()` (mocked) |
| `tests/unit/test_deploy_engine.py` | `test_deploy_calls_validate_project_name` | DeployEngine.deploy() вызывает `project_registry.validate_project_name()` (mocked verify call) | `deploy_engine.deploy()` — integration call-through |
| `tests/unit/test_payload_deliverer.py` | `test_validate_whitelist_ok` | Валидные файлы (compose, yaml, env) → success | `payload_deliverer._validate_and_extract()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_whitelist_reject` | Невалидный файл (script.sh) → raise ValidationError | `payload_deliverer._validate_and_extract()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_symlink_reject` | Symlink в payload → raise ValidationError | `payload_deliverer._validate_and_extract()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_path_traversal_reject` | Поддиректория в payload → raise ValidationError | `payload_deliverer._validate_and_extract()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_size_cap` | Payload > 1 MiB → raise SizeLimitError | `payload_deliverer._read_payload()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_empty_payload` | Payload = 0 bytes → raise ValidationError | `payload_deliverer._read_payload()` |
| `tests/unit/test_payload_deliverer.py` | `test_validate_missing_compose` | Нет docker-compose.yml → raise ValidationError | `payload_deliverer._validate_and_extract()` |
| `tests/unit/test_payload_deliverer.py` | `test_atomic_extract_to_target` | Файлы извлечены в PROJECTS_BASE/<org>/<project> | `payload_deliverer.deliver()` (tmp_path) |
| `tests/unit/test_payload_deliverer.py` | `test_deliver_full_flow` | Полный flow: читаем stdin tar.gz → validate → extract → success | `payload_deliverer.deliver()` (BytesIO + tmp_path) |

$TEST_SPEC: 36 tests specified (23 deploy_engine + 4 project_registry + 9 payload_deliverer), 3 test files

---

## Risk Assessment

| # | Risk | Severity | Probability | Mitigation |
|---|------|----------|:---:|------------|
| R1 | Production outage — deploy engine regression | 🔴 CRITICAL | Medium | Wave 5e — ПОСЛЕДНЯЯ волна после верификации 5a-5d. Staging deploy на реальной VPS ПЕРЕД merge (AC-7). Python-модуль с mocked Docker для unit-тестов. Shell trap handlers гарантируют rollback даже при падении Python. |
| R2 | Shell trap semantics mismatch — Python engine падает, trap не срабатывает | 🔴 CRITICAL | Low | Trap handlers ОСТАЮТСЯ в shell-фасаде. Python engine вызывается как subprocess из shell → если Python падает с exit ≠ 0, ERR trap срабатывает. Протестировано: `python3 -c "import sys; sys.exit(1)"` в shell с `set -e` → ERR trap fires. |
| R3 | Docker command output parsing regression — другой формат вывода на staging vs dev | 🟡 MEDIUM | Medium | Unit-тесты используют REAL docker output samples (записанные в test fixtures). Не полагаемся на формат вывода compose v2 — используем `docker compose ps --format json` (структурированный вывод). |
| R4 | Payload delivery race condition — concurrent deploys на одну VPS | 🟡 MEDIUM | Low | Существующий forced-command канал последовательный (SSH выполняет команды последовательно). Параллельные деплои на один проект невозможны — это существующий инвариант, не изменяемый миграцией. |
| R5 | Staging VPS недоступна в момент merge-проверки | 🟡 MEDIUM | Low | Staging тест может быть выполнен на любой VPS с `deploy-project.sh`. Если staging недоступна — использовать production VPS с `--dry-run` флагом (entrypoint deploy-project.sh поддерживает `--dry-run`). |
| R6 | TRAP миграция — потеря контекста при переносе | 🟢 LOW | Low | Каждый TRAP переносится как docstring-комментарий с полным текстом (Symptom, Root, Fix, Prevention). Скрипт `grep "TRAP\[" deploy_engine.py payload_deliverer.py | wc -l` в CI gate проверяет количество (≥14: ≥11 в deploy_engine.py + ≥3 в payload_deliverer.py). |
| R7 | post-deploy hooks regression — _trigger_deploy_hooks ломается | 🟢 LOW | Low | Hooks остаются в shell-фасаде (D3: non-fatal housekeeping). Никаких изменений в логике вызова `invoke_module_interface()`. |
| R8 | Python startup latency — каждый вызов python3 -m добавляет ~200ms | 🟢 LOW | Low | Абсолютно приемлемо для деплоя (полный цикл ~30-60s). Python startup = 0.3% от общего времени. |

---

## Integration Test Plan

### STAGING DEPLOY ON REAL VPS REQUIRED before merge (AC-7)

**VPS требования:**
- Docker + docker compose v2
- ci-deploy user с forced-command в authorized_keys
- PROJECTS_BASE=/opt/projects (стандартный путь)
- PLATFORM_ROOT=/opt/platform (core доставлен через `make bootstrap-node` или SCP)

**Команды (выполняются с dev-машины):**

```bash
# Pre-flight: убедиться что staging VPS доступна и core обновлён
make bootstrap-node NODE=<staging> 2>&1 | tail -20
# → должно показать "Bootstrap complete" или "no changes"

# 1. Deploy test project (новый или существующий staging-проект)
make deploy-project PROJECT=<test-project-dir> NODE=<staging>
# Pass criteria:
#   - exit code 0
#   - [IMP:9] Deploy SUCCESS в выводе
#   - [IMP:9] platform-deploy DONE (success)

# 2. Status check — сравнить JSON до и после миграции
make project-status NAME=<test-project> NODE=<staging>
# Pass criteria:
#   - status: "found"
#   - containers[] не пуст
#   - last_deploy.status: "success"

# 3. Rollback test — деплой broken image
# (создать тестовый образ с EXIT 1 в entrypoint)
docker build -t test-broken:latest -f- . <<EOF
FROM alpine:latest
CMD ["sh", "-c", "echo 'FAILING' && exit 1"]
EOF
docker tag test-broken:latest ghcr.io/<org>/<test-project>:broken
docker push ghcr.io/<org>/<test-project>:broken
# Trigger deploy with broken ref:
ssh ci-deploy@<staging-host> "deploy.sh <test-project> broken"
# Pass criteria:
#   - exit code != 0
#   - previous image restored (docker compose ps показывает старый образ)
#   - audit.log содержит ROLLBACK

# 4. Remove test
make remove-project NAME=<test-project>
# Pass criteria:
#   - exit code 0
#   - docker compose ps → контейнеры остановлены
#   - project directory НЕ удалена
#   - docker volumes ls → volumes сохранены

# 5. Payload deliver test
tar czf - docker-compose.yml ai-platform.yaml .env.platform | \
  ssh ci-deploy@<staging-host> "platform-deliver <test-project> <org>"
# Pass criteria:
#   - exit code 0
#   - файлы появились в /opt/projects/<org>/<test-project>/

# 6. B1 non-fatal step isolation test — DEPLOY_STATUS="success" после health-gate
# (после успешного деплоя, симулировать failure post-deploy non-fatal шага)
# Достаточно проверить, что deploy-result.json содержит "status": "success"
# даже если tag_current или prune завершились ошибкой.
# Pass criteria (B1 invariant):
#   - deploy-result.json (в .deploy-snapshots/) содержит "status": "success"
#   - exit code 0 (shell facade не должен падать из-за non-fatal шагов)
#   - docker tag или docker image prune могут упасть — это не роняет success
```

### Dry-run validation (если staging VPS недоступна)

```bash
# Использовать --dry-run флаг entrypoint deploy-project.sh
make deploy-project PROJECT=<test-project-dir> NODE=<staging> --dry-run
# Проверить что команды корректно сформированы (без реального выполнения)
```

---

## Rollback Strategy

### Emergency rollback (<30 min)

При любом regression на production после merge:

```bash
# 1. Revert merge commit
git revert <merge-commit-hash>

# 2. Push revert
git push origin main

# 3. Доставить старый deploy-project.sh на VPS
make bootstrap-node NODE=<production> --force
# SCP/rsync доставит старую версию core/internal/deploy/deploy-project.sh
# ВАЖНО: это доставит ВЕСЬ core/ — убедиться что другие файлы не имеют breaking changes

# 4. Верификация
make project-status NAME=<any-project> NODE=<production>
# → должен вернуть JSON (старый формат)

# Альтернатива (точечная доставка только deploy-project.sh):
scp core/internal/deploy/deploy-project.sh ci-deploy@<vps>:/opt/platform/core/internal/deploy/
```

### Rollback decision tree

```
Production issue detected
    │
    ├── deploy-project.sh regression?
    │   ├── YES → git revert + bootstrap-node --force → verify → DONE
    │   └── NO → standard incident response
    │
    ├── deploy_engine.py bug?
    │   ├── Shell facade fallback? (not implemented — by design)
    │   └── → git revert → fix → re-deploy
    │
    └── payload_deliverer.py bug?
        └── → git revert → fix → re-deploy (deliver — не критично, проекты уже на VPS)
```

---

## TRAP Inventory (post-migration)

### Перенесённые TRAP (11 автономных → docstring в Python-модулях)

```python
# В deploy_engine.py — MODULE_CONTRACT:

# ⚠️ TRAP[BUG] · 2026-07-18 · P1 · Deploy reports 'failed' despite success (B1)
# · Symptom: Deploy SUCCESS in logs, deploy-result.json=status:"failed", exit 1
# · Root: DEPLOY_STATUS="success" assigned AFTER non-fatal steps under set -e
# · Fix: DEPLOY_STATUS="success" immediately after health-gate; trap - ERR; non-fatal guarded
# · Prevention: Any code after health-gate in deploy() must not raise exceptions

# 🧐 TRAP[DECISION] · 2026-07-17 · — · Rollback on-node, not in CI/CD
# · Rejected: CI/CD-driven rollback (re-deploy via GitHub Actions)
# · Reason: instant rollback without CI pipeline wait, eliminates network roundtrip
# · Rev: if deploy latency >5min from CI → reconsider

# 🧐 TRAP[DECISION] · 2026-07-17 · — · audit_log() replaces audit_write()
# · Rejected: keeping audit_write() in deploy-project.sh
# · Reason: canonical from lib/audit_logging.sh — syslog + file append + structured IMP:8

# ⚠️ TRAP[BUG] · 2026-07-20 · REF="<sha> production" — env suffix leaks into image tag
# · Symptom: "invalid reference format" — docker pulls "image:sha production"
# · Root: parse_ssh_command didn't strip optional third token (environment)
# · Fix: REF="${REF%% *}" — strip everything after second space

# 💼 TRAP[BUSINESS] · 2026-07-17 · HI · remove = disconnect, данные не удаляются автоматически
# · Source: owner (O7/DD10)
# · Risk: авто-очистка = невосстановимая потеря БД проекта
# · Safeguard: remove() использует docker compose down БЕЗ -v

# В payload_deliverer.py — MODULE_CONTRACT:

# 🧐 TRAP[DECISION] · 2026-07-17 · — · Deliver via stdin tar.gz, not sftp/git-pull
# · Rejected: sftp-chroot user, git-pull projects (deploy-keys on node)
# · Reason: zero new channels/keys, restrict preserved
# · Rev: if payload size exceeds 1 MiB regularly → SCP variant

# 🧐 TRAP[DECISION] · 2026-07-21 · — · platform-deliver backward compat via argument count
# · Rejected: --org flag (breaks existing CI calls)
# · Reason: 1-arg = old format, 2-arg = new format. Unambiguous (project names no spaces)

# ⚠️ TRAP[BUG] · 2026-07-20 · platform-deliver exit 1 despite success
# · Symptom: first-time deliver → .deploy-snapshots/ not found → ERR trap → exit 1
# · Root: _write_deploy_result() → cat > .../deploy-result.json fails if dir missing
# · Fix: mkdir -p before writing (idempotent)
```

### Новые TRAP (post-migration)

```python
# В deploy_engine.py:

# 🧐 TRAP[DECISION] · 2026-07-26 · — · Wave 5e: deploy-project.sh Strangler-Fig migrated to Python
# · Rejected: keeping deploy logic in shell (risk: 1183 LOC monolith, 3 inline python3)
# · Reason: языковая политика (AGENTS.md), тестируемость, дедупликация с ssh_command_parser
# · Rev: если Python deploy_engine добавляет >2s latency vs shell → профилировать subprocess overhead

# 🧐 TRAP[DECISION] · 2026-07-26 · — · deploy_engine + payload_deliverer — TWO separate modules
# · Rejected: единый deploy_orchestrator.py (God Class >800 LOC)
# · Reason: разные домены (Docker orchestration vs file delivery), DDD boundary, переиспользование

# 📝 TRAP[DEBT] · 2026-07-26 · MED · Docker operations library — кандидат на shared модуль
# · Observed: save_previous_image, pull_image_with_retry, prune_old_images дублируются в
#   deploy_engine.py, context_deployer.py, docker_orchestrator.py
# · Suspected: дедупликация в core/internal/shared/docker_ops.py сократит ~200 LOC дублирования
# · Impact: при изменении Docker API — правка в 3+ местах вместо одного
# · When: during Wave 5e implementation — deferred to follow-up DevPlan
```

### Формализованные design decisions (4 → новые TRAP[DECISION] при миграции)

Следующие design decisions НЕ имели формальных TRAP-аннотаций в shell-скрипте. Создаются как новые TRAP[DECISION] в Python-модулях:

```python
# В deploy_engine.py §_preflight_checks():

# 🧐 TRAP[DECISION] · 2026-07-26 · — · FQDN uniqueness via validate.sh subprocess
# · Rejected: Python socket/FQDN parsing (duplicates validate.sh logic)
# · Reason: validate.sh is the canonical FQDN check — Python reimplementation would drift
# · Rev: if validate.sh is ever deprecated → inline Python socket.gethostbyname check

# 🧐 TRAP[DECISION] · 2026-07-26 · — · Port conflict via ss -tlnp
# · Rejected: Docker network inspect (only shows mapped ports, not host conflicts)
# · Reason: ss -tlnp shows ALL listening ports — detects conflicts before Docker starts
# · Rev: if Docker adds host-port conflict detection → migrate to Docker-native check

# В deploy_engine.py §status():

# 🧐 TRAP[DECISION] · 2026-07-26 · — · STUB_AWARE_STATUS flag
# · Rejected: always detect stubs (performance overhead on every status call)
# · Reason: stub detection requires yaml_read — optional flag avoids unnecessary I/O
# · Rev: if stub detection overhead <1ms → make it default, remove flag
```

### TRAP inventory summary

| Source | deploy_engine.py | payload_deliverer.py | ssh_command_parser.py | shell facade | Всего |
|--------|:-:|:-:|:-:|:-:|:-:|
| Автономные (11) | 5 | 3 | 2 | 1 | **11** |
| Формализованные (4) | 3 | 0 | 0 | 1 | **4** |
| Новые post-migration (3) | 3 | 0 | 0 | 0 | **3** |
| **Всего** | **11** | **3** | **2** | **2** | **18** |

**CI gate TRAP count** (`grep "TRAP\[" deploy_engine.py payload_deliverer.py`): **≥14** (11 deploy_engine + 3 payload_deliverer)

---

## File Manifest

### Modified files

| Файл | До (LOC) | После (LOC) | Сокращение |
|------|----------|-------------|------------|
| `core/internal/deploy/deploy-project.sh` | 1183 | ~200 | 83% |
| `core/internal/shared/project_registry.py` | существующий | +20 (добавлена validate_project_name) | — |

### New files

| Файл | LOC | Назначение |
|------|-----|-----------|
| `core/internal/deploy/deploy_engine.py` | ~600 | Atomic deploy/rollback/remove/status engine |
| `core/internal/deploy/payload_deliverer.py` | ~150 | Tar.gz payload validation + atomic extraction |
| `tests/unit/test_deploy_engine.py` | ~400 | Unit tests with mocked Docker (23 tests) |
| `tests/unit/test_project_registry.py` | ~80 | Unit tests for validate_project_name (4 tests) |
| `tests/unit/test_payload_deliverer.py` | ~150 | Unit tests with tmp_path fixtures (9 tests) |

### Unchanged (shell фасад НЕ трогает эти файлы)

| Файл | Причина |
|------|---------|
| `core/entrypoints/deploy-project.sh` | Direct deploy entrypoint (dev machine) — НЕ VPS forced-command. Уже использует `platform_deliver.py` для build. Изменения не требуются. Подтверждено: SSH command format `deploy.sh <project> <ref>` совместим с новым verb dispatch — backward compatibility обеспечена. |
| `core/lib/docker.sh` | Canonical shell library — НЕ мигрируется |
| `core/lib/healthcheck.sh` | Canonical shell library — НЕ мигрируется |
| `core/lib/audit_logging.sh` | Canonical shell library — НЕ мигрируется |
| `core/lib/module-interface.sh` | Canonical shell library — НЕ мигрируется |
| `core/internal/shared/ssh_command_parser.py` | Уже Python (DevPlan 081) — используется как есть |

---

## Next Steps

### Wave 1 — Parallel: deploy_engine.py + payload_deliverer.py

```
coder Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, implement Wave 1: TASK-036E1, TASK-036E2
```

**Что делает:**
- Создаёт `core/internal/deploy/deploy_engine.py` — DeployEngine class + CLI
- Создаёт `core/internal/deploy/payload_deliverer.py` — PayloadDeliverer class + CLI
- Создаёт `tests/unit/test_deploy_engine.py` — 26 unit tests (mocked Docker)
- Создаёт `tests/unit/test_payload_deliverer.py` — 9 unit tests (tmp_path)
- Переносит 11 автономных TRAP (docstring) + формализует 4 design decisions как новые TRAP[DECISION] в Python-модулях

**Проверка:** `python -m pytest tests/unit/test_deploy_engine.py tests/unit/test_payload_deliverer.py -s -v` → все 35 тестов зелёные

### Wave 2 — Shell facade + DRY

```
coder Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, implement Wave 2: TASK-036E3, TASK-036E4
```

**Что делает:**
- Редуцирует `deploy-project.sh`: 1183→~200 LOC
- Добавляет verb dispatch (4 verbs → python3 -m ...)
- Сохраняет trap handlers, lib sourcing, notify_hook
- Унифицирует `_validate_project_name()` в `project_registry.py`
- Обновляет импорты в `reconciler.py`, `deploy_engine.py`, `payload_deliverer.py`

**Проверка:** `wc -l deploy-project.sh` ≤200; `grep "python3 -c\|<<PYEOF" deploy-project.sh` → 0; `make test` зелёный

### Wave 3 — Staging integration test

```
qa Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, run staging integration test: TASK-036E5
```

**Что делает:**
- Выполняет staging deploy на реальной VPS (deploy + rollback + status + remove + deliver)
- Сравнивает JSON-вывод status до/после миграции
- Пишет `02-VerificationReport.md`

**Проверка:** Все 5 сценариев пройдены, отчёт приложен

### Wave 4 — Pre-merge gate

```
coder Read .ai/plans/036-wave5e-deploy/01-DevPlan.md, run pre-merge gate: TASK-036E6
```

**Что делает:** `make fix-gate && git add -u && make gate MODE=fast`

**Проверка:** Все gate checks зелёные

### Final merge checklist

- [ ] `make test` — все тесты зелёные (unit + existing)
- [ ] `make gate MODE=fast` — зелёный
- [ ] `wc -l core/internal/deploy/deploy-project.sh` ≤200
- [ ] `grep -c "python3 -c\|<<PYEOF" core/internal/deploy/deploy-project.sh` = 0
- [ ] `grep -c "TRAP\[" core/internal/deploy/deploy_engine.py` ≥11 (5 автономных + 3 формализованных + 3 новых)
- [ ] `grep -c "TRAP\[" core/internal/deploy/payload_deliverer.py` ≥3 (все из автономных)
- [ ] Staging integration test пройден (TASK-036E5 VerificationReport)
- [ ] `git diff --stat origin/main` — только затронутые файлы (см. File Manifest)

$END_DEVPLAN
