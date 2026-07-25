$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Унификация deploy pipeline: (1) документировать 7 путей доставки кода с gate-тестом, (2) добавить retry+rollback в Python deploy-пути, (3) унифицировать парсер SSH_ORIGINAL_COMMAND, (4) вынести platform-deliver builder в shared, (5) унифицировать формат audit-логов.
DESCRIPTION:           Закрывает DRIFT-D1, DRIFT-D3, DRIFT-D4, DRIFT-D5, DRIFT-D6 из Brief 077. Создаёт shared модули для ssh_command_parser, platform_deliver, audit_logger. Добавляет retry+rollback в context_deployer.py и docker_orchestrator.py на основе общей docker_compose.py библиотеки из DevPlan 079. Документирует канонические deploy-пути и добавляет CI gate. План реструктурирован по фазам A (независимая), B (требует shared/ от 070), C (требует docker_compose.py от 079). Добавлены unit-тесты для retry_pull и audit_logger интеграции. Добавлен явный план удаления Bootstrap compose stub.
RATIONALE:             Deploy pipeline — самый критичный домен с точки зрения production reliability. DRIFT-D3 (нет rollback в Python-путях) — прямой production risk. DRIFT-D4 (два парсера SSH_ORIGINAL_COMMAND) — хрупкая цепочка с документированными багами. DRIFT-D6 (разные форматы audit) — усложняет forensics. Унификация через shared модули + документирование канонических путей + gate тест.
ACCEPTANCE_CRITERIA:
  - AC1: `core/internal/shared/ssh_command_parser.py` содержит `parse_ssh_command(raw: str) -> dict`
  - AC2: `core/internal/shared/platform_deliver.py` содержит `build_deliver_command(org: str, project: str) -> str`
  - AC3: `core/internal/shared/audit_logger.py` содержит `write_audit_entry(tag, status, message)` с JSON-lines форматом
  - AC4: context_deployer.py использует retry_pull из shared/docker_compose.py (DevPlan 079)
  - AC5: deploy.sh и deploy-project.sh (internal) импортируют parse_ssh_command из shared (через shell wrapper)
  - AC6: entrypoints/deploy-project.sh и reconcile-projects.sh используют build_deliver_command из shared
  - AC7: context_deployer.py и docker_orchestrator.py используют write_audit_entry из shared
  - AC8: Gate тест блокирует добавление новых deploy-путей без регистрации в registry
  - AC9: Все существующие тесты проходят; новые unit-тесты для shared модулей, retry_pull интеграции и audit_logger интеграции
  - AC10: DEPRECATED_DEPLOY_PATHS содержит явный план удаления для 'Bootstrap compose stub'
  - AC11: `make gate MODE=fast` — green
IMPLEMENTS:            Brief 077 — Wave D (Deploy Pipeline Unification): DRIFT-D1, DRIFT-D3, DRIFT-D4, DRIFT-D5, DRIFT-D6
IMPACTS:
  - NEW: core/internal/shared/ssh_command_parser.py
  - NEW: core/internal/shared/platform_deliver.py
  - NEW: core/internal/shared/audit_logger.py
  - NEW: core/internal/shared/deploy_paths.py
  - NEW: tests/unit/test_shared_ssh_command_parser.py
  - NEW: tests/unit/test_shared_platform_deliver.py
  - NEW: tests/unit/test_shared_audit_logger.py
  - NEW: tests/unit/test_context_deployer_retry_pull.py
  - NEW: tests/unit/test_context_deployer_audit_integration.py
  - NEW: tests/gates/test_gate_deploy_paths.py
  - MODIFIED: core/entrypoints/deploy.sh (импорт ssh_command_parser)
  - MODIFIED: core/internal/deploy/deploy-project.sh (импорт ssh_command_parser + platform_deliver)
  - MODIFIED: core/entrypoints/deploy-project.sh (импорт platform_deliver)
  - MODIFIED: core/internal/deploy/reconcile-projects.sh (импорт platform_deliver)
  - MODIFIED: core/internal/bootstrap/deploy/context_deployer.py (retry_pull + audit_logger)
  - MODIFIED: core/internal/bootstrap/deploy/docker_orchestrator.py (audit_logger)
  - MODIFIED: core/lib/audit_logging.sh (адаптация к JSON-lines формату)
REQUIRES:              DevPlan 070 (shared/ directory + __init__.py) → DevPlan 079 Wave 1 (TASK-6: shared/docker_compose.py с retry_pull) → DevPlan 081 implementation. Полная цепочка: 070 → 079 → 081. Альтернативный путь: опция (B) из DevPlan 079 — inline bootstrap shared/ без 070 (создание __init__.py в TASK-1/TASK-6 079).

---

## Prerequisites & Dependency Chain

### Explicit Dependency Graph

```
DevPlan 070 (extract-shared-libs)
  └─► core/internal/shared/__init__.py  ← FOUNDATION
        │
        ▼
DevPlan 079 (bootstrap-pipeline-unification) Wave 1
  ├─► TASK-1: shared/content_hash.py
  └─► TASK-6: shared/docker_compose.py  ← retry_pull() lives here
              │
              ▼
DevPlan 081 (deploy-pipeline-unification) ← THIS PLAN
  ├─ Phase A (INDEPENDENT)
  │   └─ No shared/ modules needed — can run NOW
  ├─ Phase B (DEPENDENT on shared/ from 070)
  │   └─ Requires core/internal/shared/ directory to exist
  └─ Phase C (DEPENDENT on docker_compose.py from 079)
      └─ Requires retry_pull() from shared/docker_compose.py
```

### P0: shared/ directory (required for Phase B)
**Source:** DevPlan 070 — creates `core/internal/shared/__init__.py`, `node_yaml.py`, `project_registry.py`
**Check:** `[ -d core/internal/shared/ ] && [ -f core/internal/shared/__init__.py ]`
**Status:** ❌ NOT IMPLEMENTED — `core/internal/shared/` does not exist (verified 2026-07-25)
**Blocked tasks:** Phase B (TASK-B1 through TASK-B10), Phase C (TASK-C1 through TASK-C4)
**Resolution paths:**
- **(A) Implement DevPlan 070 first** — canonical path, creates proper shared/ infrastructure
- **(B) Bootstrap shared/ inline** — create `__init__.py` in Phase B Wave 1 (TASK-B1). Remove dependency on DevPlan 070.
  This is the approach recommended by DevPlan 079 VerificationReport for its own plan.
- **Recommendation for 081:** Path (B) — simpler. DevPlan 081 only needs `shared/__init__.py` to exist; it does not depend on `node_yaml.py` or `project_registry.py` from 070.

### P1: docker_compose.py with retry_pull() (required for Phase C)
**Source:** DevPlan 079 — TASK-6 creates `shared/docker_compose.py` with `retry_pull(compose_dir, max_attempts=3, backoff_seconds=[5,10,20]) -> bool`
**Check:** `python3 -c "from core.internal.shared.docker_compose import retry_pull; print('OK')"`
**Status:** ❌ NOT IMPLEMENTED — `shared/docker_compose.py` does not exist (verified 2026-07-25)
**Blocked tasks:** Phase C (TASK-C1 through TASK-C4)
**Resolution path:** Implement DevPlan 079 Wave 1 (minimum TASK-6) before Phase C.
**Minimum viable prerequisite:** TASK-6 only (`docker_compose.py`). TASK-1 (`content_hash.py`) is NOT required by DevPlan 081.

### P2: All source files exist (verified)
14/14 source files referenced in File Manifest verified on disk. Pre-refactoring code intact.

---

## Requirements Analysis

### Success Criteria
1. **SC1: Единый парсер SSH_ORIGINAL_COMMAND.** Оба parser'а (deploy.sh:29-123, deploy-project.sh:430-481) заменены на вызов общего `parse_ssh_command()`.
2. **SC2: Единый platform-deliver builder.** Три места сборки используют `build_deliver_command()`.
3. **SC3: Единый audit-формат.** JSON-lines формат в Python и shell.
4. **SC4: Retry+rollback в Python-путях.** context_deployer.py получает retry_pull (3 попытки, backoff 5/10/20s) через shared/docker_compose.py.
5. **SC5: Gate test для deploy-путей.** CI блокирует добавление новых deploy-путей без регистрации.
6. **SC6: Явный план удаления Bootstrap compose stub.** DEPRECATED_DEPLOY_PATHS содержит target date и критерии верификации.
7. **SC7: Unit-тесты для retry_pull и audit_logger интеграций.** Покрытие для TASK-C1 и TASK-C3.

---

## Architecture Overview

### Draft Code Graph (AFTER unification)

```
┌─ entrypoints/deploy.sh ─────────────────────────────────────────────┐
│  from core.internal.shared.ssh_command_parser import parse_ssh_command │
│  verb, args = parse_ssh_command(raw)                                   │
│  → dispatch(verb, args)                                                │
└──────────────────────────────────────────────────────────────────────┘

┌─ internal/deploy/deploy-project.sh ─────────────────────────────────┐
│  python3 -m core.internal.shared.ssh_command_parser parse "$raw"    │
│  → JSON stdout {verb, args}                                           │
│  python3 -m core.internal.shared.platform_deliver build \            │
│    --org "$org" --project "$project"                                  │
└──────────────────────────────────────────────────────────────────────┘

┌─ entrypoints/deploy-project.sh ─────────────────────────────────────┐
│  deliver_verb=$(python3 -m core.internal.shared.platform_deliver \  │
│    build --org "$ORG" --project "$PROJECT_NAME")                     │
└──────────────────────────────────────────────────────────────────────┘

┌─ context_deployer.py ───────────────────────────────────────────────┐
│  from core.internal.shared.docker_compose import retry_pull           │
│  from core.internal.shared.audit_logger import write_audit_entry      │
│  # retry_pull() before fallback build                                 │
│  # write_audit_entry() instead of direct file.write                   │
└──────────────────────────────────────────────────────────────────────┘

┌─ docker_orchestrator.py ────────────────────────────────────────────┐
│  from core.internal.shared.audit_logger import write_audit_entry      │
│  # Стандартизированный audit формат                                   │
└──────────────────────────────────────────────────────────────────────┘

┌─ Deploy Path Registry (core/internal/shared/deploy_paths.py) ────────┐
│  CANONICAL: [                                                         │
│    "CI → platform-deliver + deploy.sh",                               │
│    "make deploy-project (direct)",                                    │
│    "context_deployer.py (Python)",                                    │
│    "deploy-modules.sh (system modules)",                              │
│    "Core SCP/rsync",                                                  │
│    "Context-overlay git",                                             │
│  ]                                                                    │
│  DEPRECATED: [                                                        │
│    "Bootstrap compose stub — temporal: replaces by first project      │
│     deploy via context_deployer.py. Target: 2026-08-15.",             │
│  ]                                                                    │
└──────────────────────────────────────────────────────────────────────┘
```

### Data Flow

**DRIFT-D4 (SSH_ORIGINAL_COMMAND unification):**
```
SSH forced-command → parse_ssh_command(raw) → {verb, args}
  ├── deploy.sh entrypoint: verb=ping|exit|remove|status|verify|deploy
  └── deploy-project.sh internal: verb=platform-deliver|platform-deploy|verify|deploy
```

**DRIFT-D5 (platform-deliver unification):**
```
build_deliver_command(org, project) → str
  ├── org present: "platform-deliver {org} {project}"
  └── org absent:  "platform-deliver {project}"
Callers:
  ├── entrypoints/deploy-project.sh:230-236
  ├── internal/deploy/deploy-project.sh:456-481 (парсинг, не build)
  └── reconcile-projects.sh:192
```

**DRIFT-D6 (audit unification):**
```
write_audit_entry(tag, status, message)
  → JSON-lines: {"ts":"2026-07-25T12:00:00Z","tag":"platform-deploy:project","status":"START","msg":"..."}
  → Bash: python3 -m core.internal.shared.audit_logger write --tag "..." --status "..." --msg "..."
  → Python: from core.internal.shared.audit_logger import write_audit_entry
```

---

## Design Decisions

### ## @rationale D1: JSON-lines для audit вместо pipe-delimited
Q: Почему JSON-lines вместо существующего `ts | step | status | msg`?
A: JSON-lines — machine-parseable, расширяемый (добавление полей без ломания парсеров). Существующий формат остаётся через shell `audit_log()` для обратной совместимости — Python пишет в JSON-lines, shell может мигрировать постепенно. Двойная запись (старый + новый формат) в течение переходного периода.

### ## @rationale D2: parse_ssh_command возвращает dict, не tuple
Q: Почему dict, а не tuple (verb, args)?
A: Разные entrypoint'ы имеют разные verb-словари. deploy.sh: ping/exit/remove/status/verify/deploy. deploy-project.sh: platform-deliver/platform-deploy/verify/deploy. Dict позволяет вернуть `{verb, args, raw, cleaned}` — оба caller'а извлекают нужные поля. Расширяемость без изменения сигнатуры.

### ## @rationale D3: Gate test для deploy-путей
Q: Почему gate, а не lint rule?
A: Gate test (pytest) позволяет проверить runtime-регистрацию: парсит entrypoint-manifest.yaml, проверяет что все зарегистрированные deploy-пути соответствуют каноническому списку. Блокирует merge если появляется новый незарегистрированный путь.

### ## @rationale D4: platform-deliver как отдельный shared модуль
Q: Почему не часть ssh_command_parser?
A: Разная ответственность: ssh_command_parser парсит входящие команды, platform_deliver строит исходящие. Разные callers. Разделение по Single Responsibility.

### ## @rationale D5: Bootstrap Compose Stub Removal Plan
Q: Почему нужен явный план удаления?
A: Bootstrap compose stub (nginx:alpine) — временная заглушка, генерируемая при bootstrap ноды до первого реального деплоя проекта. Без явного плана удаления она может остаться в production, создавая ложное впечатление работающего сервиса (HTTP 200 от nginx при фактически отсутствующем проекте). Gate test (TASK-081A1) проверяет наличие removal plan для каждого deprecated пути.

**Removal Plan:**
- **What:** Temporary nginx:alpine Docker Compose stub generated during node bootstrap, replaced by first real project deployment
- **Mechanism:** `context_deployer._deploy_single_project()` runs `docker compose up -d` for the real project → replaces the stub container automatically (same compose project name)
- **Verification:** `docker compose ps --format '{{.Image}}' | grep -c 'nginx:alpine'` returns 0 after first project deploy
- **Fallback:** If stub persists after first project deploy → manual: `docker compose down nginx-stub && docker rm nginx-stub`
- **Target:** Post-DevPlan 079 completion, estimated 2026-08-15
- **Rev:** 2026-09-01 — re-evaluate if Bootstrap compose stub present in any production node

### ## @rationale D6: Three-phase decomposition for dependency clarity
Q: Почему три фазы вместо исходных 4 волн?
A: Исходные 4 волны предполагали, что shared/ уже существует. VerificationReport обнаружил, что DevPlan 079 не реализован, и shared/ отсутствует. Трёхфазная декомпозиция разделяет задачи по реальной доступности зависимостей: Phase A можно запустить немедленно, Phase B — после создания shared/ (070), Phase C — после реализации retry_pull (079 Wave 1). Это позволяет вести параллельную работу: Phase A не блокируется отсутствием shared/.

---

## $TASKS

### Phase A — INDEPENDENT (no shared/ dependency, can run NOW)

Эти задачи не требуют `core/internal/shared/` директории или любых shared-модулей. Могут выполняться параллельно с DevPlan 070/079.

---

#### TASK-081A1: Создать Deploy Path Registry и gate test (бывший TASK-13)

**Owner:** Coder
**Output:** `core/internal/shared/deploy_paths.py` (~50 LOC) + `tests/gates/test_gate_deploy_paths.py` (~80 LOC)
**Acceptance Criteria:**
- `deploy_paths.py` содержит константу `CANONICAL_DEPLOY_PATHS` с 6 документированными путями:
  1. "CI → platform-deliver + deploy.sh" — git push → CI → tar via SSH forced-command
  2. "make deploy-project (direct)" — tar + SSH, bypass CI
  3. "context_deployer.py (Python)" — ghcr.io pull + build fallback
  4. "deploy-modules.sh (system modules)" — docker compose up (system: install.sh)
  5. "Core SCP/rsync" — CI workflow core-deploy
  6. "Context-overlay git" — git clone/pull via ensure_context_repo()
- Константа `DEPRECATED_DEPLOY_PATHS` с явным планом удаления:
  ```
  {
    "Bootstrap compose stub": {
      "description": "Temporary nginx:alpine container generated during node bootstrap, replaced by first real project deployment via context_deployer._deploy_single_project()",
      "removal_mechanism": "docker compose up -d на реальный проект заменяет заглушку автоматически",
      "verification": "docker compose ps --format '{{.Image}}' | grep -c 'nginx:alpine' returns 0",
      "target_date": "2026-08-15",
      "fallback": "docker compose down nginx-stub && docker rm nginx-stub",
      "rev_date": "2026-09-01"
    }
  }
  ```
- Gate test `tests/gates/test_gate_deploy_paths.py`:
  - `test_canonical_paths_registered`: парсит entrypoint-manifest.yaml → извлекает deploy-related таргеты → проверяет наличие в CANONICAL_DEPLOY_PATHS
  - `test_no_unregistered_paths`: проверяет отсутствие незарегистрированных deploy-путей
  - `test_deprecated_have_removal_plan`: каждый DEPRECATED путь имеет `target_date` и `removal_mechanism`
- Module contract с GREP_SUMMARY, STRUCTURE, @purpose, @invariants
**Dependencies:** None (deploy_paths.py не импортирует другие shared-модули; создаёт `shared/` директорию как side effect если её нет)
**Complexity:** 3
**Phase:** A — INDEPENDENT

---

#### TASK-081A2: Структурный рефакторинг deploy.sh (бывший TASK-7 — Phase A lite)

**Owner:** Coder
**Output:** `core/entrypoints/deploy.sh` (~100 LOC, было 126)
**Acceptance Criteria:**
- Code cleanup: улучшить структуру dispatch-логики, добавить комментарии к verb-диспетчеризации
- Выделить `parse_verb()` в отдельную хорошо документированную shell-функцию (без импорта shared)
- Сохранить существующую stripping-логику (path prefix, platform-deploy) — она будет заменена на shared в Phase B (TASK-081B7)
- Добавить # STRUCTURE и # GREP_SUMMARY комментарии
- **В этой фазе НЕ импортирует shared модули** — только структурная подготовка
- Все существующие тесты проходят
**Dependencies:** None
**Complexity:** 2
**Phase:** A — INDEPENDENT

---

#### TASK-081A3: Структурный рефакторинг entrypoints/deploy-project.sh (бывший TASK-9 — Phase A lite)

**Owner:** Coder
**Output:** `core/entrypoints/deploy-project.sh` (~5 LOC изменено)
**Acceptance Criteria:**
- Выделить `deliver_verb` построение в отдельную shell-функцию `build_deliver_verb()` (без импорта shared)
- Добавить комментарии с планом миграции на shared/platform_deliver (Phase B, TASK-081B9)
- **В этой фазе НЕ импортирует shared модули** — только структурная подготовка
**Dependencies:** None
**Complexity:** 1
**Phase:** A — INDEPENDENT

---

### Phase B — DEPENDENT on shared/ (requires core/internal/shared/ from DevPlan 070)

Эти задачи требуют существования `core/internal/shared/__init__.py`. Если DevPlan 070 не реализован, создать `__init__.py` в первом задании фазы (TASK-081B1).

---

#### TASK-081B1: Создать `core/internal/shared/ssh_command_parser.py` (бывший TASK-1)

**Owner:** Coder
**Output:** `core/internal/shared/ssh_command_parser.py` (~100 LOC)
**Acceptance Criteria:**
- `parse_ssh_command(raw: str) -> dict` с полями: `verb` (str), `args` (str | None), `raw` (str), `cleaned` (str)
- Логика stripping (агрегирует из deploy.sh:59-70 и deploy-project.sh:436-440):
  - Strip path prefix (`/opt/platform/core/entrypoints/deploy.sh `)
  - Strip legacy `platform-deploy ` prefix
  - Strip `platform-deploy` (без пробела)
  - Trim whitespace
- `classify_verb(cleaned: str) -> str`: возвращает `ping|exit|remove|status|verify|platform-deliver|platform-deploy|deploy`
- CLI: `python3 -m core.internal.shared.ssh_command_parser parse "raw command"`
- Module contract с GREP_SUMMARY, STRUCTURE, @purpose, @invariants
- Если `core/internal/shared/__init__.py` отсутствует — создать его (пустой файл)
**Dependencies:** shared/ directory (DevPlan 070 OR inline bootstrap)
**Complexity:** 3
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B2: Создать `tests/unit/test_shared_ssh_command_parser.py` (бывший TASK-2)

**Owner:** Coder
**Output:** `tests/unit/test_shared_ssh_command_parser.py` (~80 LOC)
**Acceptance Criteria:**
- test_parse_ping: "ping" → verb=ping, args=None
- test_parse_remove: "remove myproject" → verb=remove, args="myproject"
- test_parse_platform_deliver_org: "platform-deliver org project" → verb=platform-deliver
- test_parse_platform_deliver_legacy: "platform-deliver project" → verb=platform-deliver
- test_parse_deploy_legacy: "project sha env" → verb=deploy
- test_strip_path_prefix: "/opt/platform/core/entrypoints/deploy.sh project sha" → cleaned="project sha"
- test_strip_platform_deploy: "platform-deploy project sha" → cleaned="project sha"
- test_empty_command: "" → raises ValueError
- LDD: минимум один IMP:9 лог
**Dependencies:** TASK-081B1
**Complexity:** 3
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B3: Создать `core/internal/shared/platform_deliver.py` (бывший TASK-3)

**Owner:** Coder
**Output:** `core/internal/shared/platform_deliver.py` (~60 LOC)
**Acceptance Criteria:**
- `build_deliver_command(org: str = "", project: str = "") -> str`
  - С org: `"platform-deliver {org} {project}"`
  - Без org: `"platform-deliver {project}"`
- `parse_deliver_args(args: str) -> tuple[str, str]`: парсит строку после "platform-deliver " → (org, project)
- CLI: `python3 -m core.internal.shared.platform_deliver build --org "myorg" --project "myproj"`
- CLI: `python3 -m core.internal.shared.platform_deliver parse "myorg myproj"`
- Module contract с GREP_SUMMARY, STRUCTURE, @purpose, @invariants
**Dependencies:** shared/ directory (DevPlan 070 OR inline bootstrap)
**Complexity:** 2
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B4: Создать `tests/unit/test_shared_platform_deliver.py` (бывший TASK-4)

**Owner:** Coder
**Output:** `tests/unit/test_shared_platform_deliver.py` (~60 LOC)
**Acceptance Criteria:**
- test_build_with_org: org="myorg", project="myproj" → "platform-deliver myorg myproj"
- test_build_without_org: org="", project="myproj" → "platform-deliver myproj"
- test_parse_two_tokens: "myorg myproj" → ("myorg", "myproj")
- test_parse_one_token: "myproj" → ("", "myproj")
- test_parse_with_spaces: "  myorg   myproj  " → ("myorg", "myproj")
- LDD: минимум один IMP:9 лог
**Dependencies:** TASK-081B3
**Complexity:** 2
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B5: Создать `core/internal/shared/audit_logger.py` (бывший TASK-5)

**Owner:** Coder
**Output:** `core/internal/shared/audit_logger.py` (~80 LOC)
**Acceptance Criteria:**
- `write_audit_entry(tag: str, status: str, message: str, log_file: str = "/var/log/platform/audit.jsonl") -> None`
- JSON-lines формат: `{"ts":"2026-07-25T12:00:00Z","tag":"...","status":"...","msg":"..."}`
- Создаёт директорию лога если отсутствует (mkdir -p)
- Thread-safe через append mode (O_APPEND на уровне ОС атомарен для строк < PIPE_BUF)
- `read_audit_log(log_file, limit=100) -> list[dict]`: чтение последних N записей
- CLI: `python3 -m core.internal.shared.audit_logger write --tag "test" --status "OK" --msg "test message"`
- CLI: `python3 -m core.internal.shared.audit_logger read --limit 10`
- Module contract с GREP_SUMMARY, STRUCTURE, @purpose, @invariants
**Dependencies:** shared/ directory (DevPlan 070 OR inline bootstrap)
**Complexity:** 3
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B6: Создать `tests/unit/test_shared_audit_logger.py` (бывший TASK-6)

**Owner:** Coder
**Output:** `tests/unit/test_shared_audit_logger.py` (~80 LOC)
**Acceptance Criteria:**
- test_write_entry_creates_file: первый вызов создаёт файл
- test_write_entry_json_valid: запись → валидный JSON
- test_read_entries: 3 записи → read(limit=2) → 2 записи
- test_read_empty_log: пустой/отсутствующий файл → []
- test_concurrent_writes: множественные вызовы → все записи сохраняются (атомарность)
- LDD: минимум один IMP:9 лог
**Dependencies:** TASK-081B5
**Complexity:** 3
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B7: Полный рефакторинг deploy.sh — импорт ssh_command_parser (бывший TASK-7 — full)

**Owner:** Coder
**Output:** `core/entrypoints/deploy.sh` (~80 LOC, было 100 после TASK-081A2)
**Acceptance Criteria:**
- `parse_verb()` заменён на вызов `python3 -m core.internal.shared.ssh_command_parser parse "$raw"`
- Shell получает JSON stdout → извлекает verb, args
- Dispatch логика сохранена (ping/exit/remove/status/verify/deploy)
- Stripping path prefix и platform-deploy делегированы Python-модулю
- Интегрируется с результатами TASK-081A2 (структурный рефакторинг)
**Dependencies:** TASK-081B1 (ssh_command_parser.py), TASK-081A2 (structural prep)
**Complexity:** 3
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B8: Рефакторинг deploy-project.sh (internal) — импорт ssh_command_parser + platform_deliver (бывший TASK-8)

**Owner:** Coder
**Output:** `core/internal/deploy/deploy-project.sh` (~50 LOC изменено)
**Acceptance Criteria:**
- Stripping path prefix делегирован `parse_ssh_command()` из shared
- Platform-deliver verb detection использует `classify_verb()` и `parse_deliver_args()` из shared
- `handle_deliver()` получает (org, project) через shared вместо inline парсинга
- Локальный парсинг platform-deliver args удалён
**Dependencies:** TASK-081B1, TASK-081B3
**Complexity:** 3
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B9: Полный рефакторинг entrypoints/deploy-project.sh — импорт platform_deliver (бывший TASK-9 — full)

**Owner:** Coder
**Output:** `core/entrypoints/deploy-project.sh` (~10 LOC изменено)
**Acceptance Criteria:**
- `build_deliver_verb()` (созданная в TASK-081A3) заменена на `$(python3 -m core.internal.shared.platform_deliver build --org "$ORG" --project "$PROJECT_NAME")`
- Локальное построение строки `platform-deliver ${ORG} ${PROJECT_NAME}` удалено
**Dependencies:** TASK-081B3, TASK-081A3 (structural prep)
**Complexity:** 1
**Phase:** B — DEPENDENT on shared/

---

#### TASK-081B10: Рефакторинг reconcile-projects.sh — импорт platform_deliver (бывший TASK-10)

**Owner:** Coder
**Output:** `core/internal/deploy/reconcile-projects.sh` (~5 LOC изменено)
**Acceptance Criteria:**
- `deliver_verb` заменён на `$(python3 -m core.internal.shared.platform_deliver build --org "${proj_org}" --project "${proj_name}")`
- Локальное построение строки удалено
**Dependencies:** TASK-081B3
**Complexity:** 1
**Phase:** B — DEPENDENT on shared/

---

### Phase C — DEPENDENT on docker_compose.py (requires retry_pull() from DevPlan 079 Wave 1, TASK-6)

Эти задачи требуют `from core.internal.shared.docker_compose import retry_pull` для работы. DevPlan 079 Wave 1 (TASK-6) должен быть реализован до начала Phase C.

---

#### TASK-081C1: Интегрировать retry_pull в context_deployer.py (бывший TASK-11)

**Owner:** Coder
**Output:** `core/internal/bootstrap/deploy/context_deployer.py` (~25 LOC изменено)
**Acceptance Criteria:**
- `_deploy_single_project()`: перед fallback build вызывается `retry_pull()` из shared/docker_compose.py
- 3 попытки с backoff 5/10/20 секунд (параметры по умолчанию из retry_pull)
- Если retry_pull успешен → channel="ghcr" (без fallback build), запись audit: status="DEPLOYED", channel="ghcr"
- Если все попытки неуспешны → существующая логика fallback build, запись audit: status="FALLBACK_BUILD"
- Импорт: `from core.internal.shared.docker_compose import retry_pull`
- PRECONDITION: DevPlan 079 TASK-6 выполнен (shared/docker_compose.py с retry_pull существует)
**Dependencies:** DevPlan 079 Wave 1, TASK-6 (shared/docker_compose.py with retry_pull)
**Complexity:** 2
**Phase:** C — DEPENDENT on docker_compose.py

---

#### TASK-081C2: Создать `tests/unit/test_context_deployer_retry_pull.py` (NEW)

**Owner:** Coder
**Output:** `tests/unit/test_context_deployer_retry_pull.py` (~80 LOC)
**Acceptance Criteria:**
- `test_retry_pull_success_first_attempt`: mock retry_pull → True на первой попытке, проверяет channel="ghcr"
- `test_retry_pull_success_third_attempt`: mock retry_pull → первые 2 вызова False, 3-й True, проверяет channel="ghcr"
- `test_retry_pull_all_failed_fallback_to_build`: mock retry_pull → всегда False, проверяет что вызывается fallback build
- `test_retry_pull_backoff_intervals`: проверяет что retry_pull вызывается с правильными аргументами (max_attempts=3, backoff_seconds=[5,10,20])
- `test_retry_pull_audit_logged`: успешный retry_pull → audit запись содержит status="DEPLOYED", channel="ghcr"
- `test_fallback_build_audit_logged`: fallback build → audit запись содержит status="FALLBACK_BUILD"
- LDD: минимум один IMP:9 лог в каждом успешном сценарии
- PRECONDITION: DevPlan 079 TASK-6 выполнен (retry_pull доступен для импорта)
**Dependencies:** TASK-081C1 (context_deployer.py with retry_pull), TASK-081B5 (audit_logger.py)
**Complexity:** 4
**Phase:** C — DEPENDENT on docker_compose.py

---

#### TASK-081C3: Мигрировать context_deployer.py и docker_orchestrator.py на audit_logger (бывший TASK-12)

**Owner:** Coder
**Output:** `context_deployer.py` + `docker_orchestrator.py` (~30 LOC изменено)
**Acceptance Criteria:**
- `context_deployer._write_audit()` (строка 613-624) заменён на `write_audit_entry()` из shared/audit_logger.py
- `docker_orchestrator.py`: ключевые операции (deploy старт, healthcheck fail, deploy завершение) пишут audit через `write_audit_entry()`
- Формат: JSON-lines (стандартный формат audit_logger)
- Старый формат audit_log в shell НЕ трогаем (обратная совместимость)
- Импорт: `from core.internal.shared.audit_logger import write_audit_entry`
**Dependencies:** TASK-081B5 (audit_logger.py)
**Complexity:** 3
**Phase:** C — DEPENDENT on docker_compose.py

---

#### TASK-081C4: Создать `tests/unit/test_context_deployer_audit_integration.py` (NEW)

**Owner:** Coder
**Output:** `tests/unit/test_context_deployer_audit_integration.py` (~80 LOC)
**Acceptance Criteria:**
- `test_context_deployer_writes_audit_on_deploy`: деплой проекта → audit файл содержит JSON-lines запись с tag="context_deploy:deploy"
- `test_audit_entry_contains_required_fields`: запись содержит поля ts, tag, status, msg в JSON-lines формате
- `test_docker_orchestrator_writes_audit_on_healthcheck_fail`: healthcheck fail → audit запись с status="UNHEALTHY"
- `test_docker_orchestrator_writes_audit_on_deploy_complete`: деплой завершён → audit запись с status="DEPLOYED"
- `test_audit_format_is_valid_json_lines`: каждая строка — валидный JSON object
- `test_old_shell_format_unchanged`: shell audit_log() продолжает работать в старом формате (pipe-delimited)
- LDD: минимум один IMP:9 лог в каждом успешном сценарии
**Dependencies:** TASK-081C3 (context_deployer.py + docker_orchestrator.py with audit_logger), TASK-081B5 (audit_logger.py)
**Complexity:** 4
**Phase:** C — DEPENDENT on docker_compose.py

---

### Phase Gate — Final Verification

---

#### TASK-081G1: Gate + интеграционная верификация (бывший TASK-14)

**Owner:** Coder
**Output:** Все тесты зелёные, `make gate MODE=fast` проходит
**Acceptance Criteria:**
- `python3 -m pytest tests/unit/test_shared_ssh_command_parser.py tests/unit/test_shared_platform_deliver.py tests/unit/test_shared_audit_logger.py -v` — все проходят
- `python3 -m pytest tests/unit/test_context_deployer_retry_pull.py tests/unit/test_context_deployer_audit_integration.py -v` — все проходят
- `python3 -m pytest tests/gates/test_gate_deploy_paths.py -v` — проходит (включая test_deprecated_have_removal_plan)
- `python3 -m pytest tests/unit/test_docker_orchestrator.py -v` — без регрессии
- `make fix-gate && git add -u && make gate MODE=fast` — green
**Dependencies:** Phase A (TASK-081A1) + Phase B (all) + Phase C (all)
**Complexity:** 2
**Phase:** Gate

---

### Task-to-Phase Mapping (original → new)

| Original TASK | Description | New ID | Phase | Dependency |
|---------------|-------------|--------|-------|------------|
| TASK-1 | ssh_command_parser.py | TASK-081B1 | B | shared/ (070) |
| TASK-2 | test_shared_ssh_command_parser.py | TASK-081B2 | B | TASK-081B1 |
| TASK-3 | platform_deliver.py | TASK-081B3 | B | shared/ (070) |
| TASK-4 | test_shared_platform_deliver.py | TASK-081B4 | B | TASK-081B3 |
| TASK-5 | audit_logger.py | TASK-081B5 | B | shared/ (070) |
| TASK-6 | test_shared_audit_logger.py | TASK-081B6 | B | TASK-081B5 |
| TASK-7 | deploy.sh refactor (full) | TASK-081A2 + TASK-081B7 | A + B | A=none, B=TASK-081B1 |
| TASK-8 | deploy-project.sh internal | TASK-081B8 | B | TASK-081B1, TASK-081B3 |
| TASK-9 | deploy-project.sh entrypoint (full) | TASK-081A3 + TASK-081B9 | A + B | A=none, B=TASK-081B3 |
| TASK-10 | reconcile-projects.sh | TASK-081B10 | B | TASK-081B3 |
| TASK-11 | retry_pull in context_deployer | TASK-081C1 | C | DevPlan 079 TASK-6 |
| — | — | TASK-081C2 (NEW) | C | TASK-081C1 |
| TASK-12 | audit_logger in deploy modules | TASK-081C3 | C | TASK-081B5 |
| — | — | TASK-081C4 (NEW) | C | TASK-081C3 |
| TASK-13 | deploy_paths.py + gate test | TASK-081A1 | A | None |
| TASK-14 | gate verification | TASK-081G1 | Gate | All phases |

---

## $PARALLEL_GROUPS

### Pre-requisite Wave 0 — DevPlan 079 (must be completed before Phase C)
```
Implement DevPlan 079 Wave 1: TASK-6 (shared/docker_compose.py with retry_pull).
If shared/ directory does not exist: create __init__.py inline (option B from DevPlan 079).
```

### Wave A — Phase A (independent, parallel with Wave 0)
- Tasks: TASK-081A1, TASK-081A2, TASK-081A3
- No shared file intersections — all 3 can run in parallel
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave A: TASK-081A1, TASK-081A2, TASK-081A3`

### Wave B1 — Phase B, shared modules (independent of each other)
- Tasks: TASK-081B1, TASK-081B3, TASK-081B5
- No shared file intersections (create different files in shared/)
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave B1: TASK-081B1, TASK-081B3, TASK-081B5`

### Wave B2 — Phase B, tests for shared modules
- Tasks: TASK-081B2, TASK-081B4, TASK-081B6
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave B2: TASK-081B2, TASK-081B4, TASK-081B6`

### Wave B3 — Phase B, shell refactoring (depends on shared modules)
- Tasks: TASK-081B7, TASK-081B8, TASK-081B9, TASK-081B10
- File intersection: TASK-081B7 writes deploy.sh, TASK-081B9 writes deploy-project.sh — different files → parallel OK
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave B3: TASK-081B7, TASK-081B8, TASK-081B9, TASK-081B10`

### Wave C1 — Phase C, retry_pull integration
- Tasks: TASK-081C1, TASK-081C3 (different files: context_deployer.py and docker_orchestrator.py share no common sections → parallel OK)
- PRECONDITION: DevPlan 079 TASK-6 completed (docker_compose.py with retry_pull exists)
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave C1: TASK-081C1, TASK-081C3`

### Wave C2 — Phase C, integration tests
- Tasks: TASK-081C2, TASK-081C4
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave C2: TASK-081C2, TASK-081C4`

### Wave G — Final gate verification
- Tasks: TASK-081G1
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave G: TASK-081G1`

---

## Acceptance Criteria Summary

| # | Criteria | Verification |
|---|----------|-------------|
| AC1 | `parse_ssh_command(raw)` в shared/ssh_command_parser.py | test_shared_ssh_command_parser.py |
| AC2 | `build_deliver_command(org, project)` в shared/platform_deliver.py | test_shared_platform_deliver.py |
| AC3 | `write_audit_entry(tag, status, msg)` в shared/audit_logger.py | test_shared_audit_logger.py |
| AC4 | context_deployer.py использует retry_pull | test_context_deployer_retry_pull.py |
| AC5 | deploy.sh + deploy-project.sh используют parse_ssh_command | код-ревью + существующие тесты |
| AC6 | deploy-project.sh + reconcile-projects.sh используют build_deliver_command | код-ревью |
| AC7 | Python deploy-пути используют write_audit_entry | test_context_deployer_audit_integration.py |
| AC8 | Gate test блокирует новые незарегистрированные пути | test_gate_deploy_paths.py |
| AC9 | Все тесты проходят | pytest (all test files) |
| AC10 | DEPRECATED_DEPLOY_PATHS содержит явный план удаления | test_gate_deploy_paths.py::test_deprecated_have_removal_plan |
| AC11 | `make gate MODE=fast` green | CI |

---

## File Manifest

| File | Action | LOC change | Phase |
|------|--------|------------|-------|
| `core/internal/shared/deploy_paths.py` | NEW | +50 | A |
| `core/internal/shared/ssh_command_parser.py` | NEW | +100 | B |
| `core/internal/shared/platform_deliver.py` | NEW | +60 | B |
| `core/internal/shared/audit_logger.py` | NEW | +80 | B |
| `tests/unit/test_shared_ssh_command_parser.py` | NEW | +80 | B |
| `tests/unit/test_shared_platform_deliver.py` | NEW | +60 | B |
| `tests/unit/test_shared_audit_logger.py` | NEW | +80 | B |
| `tests/unit/test_context_deployer_retry_pull.py` | NEW | +80 | C |
| `tests/unit/test_context_deployer_audit_integration.py` | NEW | +80 | C |
| `tests/gates/test_gate_deploy_paths.py` | NEW | +80 | A |
| `core/entrypoints/deploy.sh` | MODIFY | ~−40 (126→86) | A + B |
| `core/internal/deploy/deploy-project.sh` | MODIFY | ~−20 | B |
| `core/entrypoints/deploy-project.sh` | MODIFY | ~−10 | A + B |
| `core/internal/deploy/reconcile-projects.sh` | MODIFY | ~−5 | B |
| `core/internal/bootstrap/deploy/context_deployer.py` | MODIFY | ~+25 (retry_pull) + ~−10 (audit) | C |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | MODIFY | ~+10 (audit) | C |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test | Phase |
|-----------|---------------|----------|-------------------|-------|
| tests/unit/test_shared_ssh_command_parser.py | test_parse_ping | "ping" → verb=ping | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_remove | "remove myproject" → verb=remove | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_status | "status myproject" → verb=status | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_platform_deliver_org | "platform-deliver org proj" | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_platform_deliver_legacy | "platform-deliver proj" (1 arg) | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_ssh_command_parser.py | test_strip_path_prefix | Префикс пути stripped | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_ssh_command_parser.py | test_strip_platform_deploy | "platform-deploy" stripped | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_ssh_command_parser.py | test_empty_command | "" → ValueError | shared/ssh_command_parser.py | B |
| tests/unit/test_shared_platform_deliver.py | test_build_with_org | С org → "platform-deliver org proj" | shared/platform_deliver.py | B |
| tests/unit/test_shared_platform_deliver.py | test_build_without_org | Без org → "platform-deliver proj" | shared/platform_deliver.py | B |
| tests/unit/test_shared_platform_deliver.py | test_parse_two_tokens | parse "org proj" → (org, proj) | shared/platform_deliver.py | B |
| tests/unit/test_shared_platform_deliver.py | test_parse_one_token | parse "proj" → ("", proj) | shared/platform_deliver.py | B |
| tests/unit/test_shared_audit_logger.py | test_write_entry_creates_file | Первый вызов создаёт файл | shared/audit_logger.py | B |
| tests/unit/test_shared_audit_logger.py | test_write_entry_json_valid | JSON-lines формат валиден | shared/audit_logger.py | B |
| tests/unit/test_shared_audit_logger.py | test_read_entries_limit | read(limit=2) из 3 записей | shared/audit_logger.py | B |
| tests/unit/test_shared_audit_logger.py | test_read_empty_log | Пустой лог → [] | shared/audit_logger.py | B |
| tests/unit/test_context_deployer_retry_pull.py | test_retry_pull_success_first_attempt | retry_pull→True, channel="ghcr" | context_deployer.py | C |
| tests/unit/test_context_deployer_retry_pull.py | test_retry_pull_success_third_attempt | 2 fail + 1 success | context_deployer.py | C |
| tests/unit/test_context_deployer_retry_pull.py | test_retry_pull_all_failed_fallback | Все попытки fail → build | context_deployer.py | C |
| tests/unit/test_context_deployer_retry_pull.py | test_retry_pull_backoff_intervals | max_attempts=3, backoff=[5,10,20] | context_deployer.py | C |
| tests/unit/test_context_deployer_retry_pull.py | test_retry_pull_audit_logged | Audit: status="DEPLOYED", channel="ghcr" | context_deployer.py | C |
| tests/unit/test_context_deployer_retry_pull.py | test_fallback_build_audit_logged | Audit: status="FALLBACK_BUILD" | context_deployer.py | C |
| tests/unit/test_context_deployer_audit_integration.py | test_context_deployer_writes_audit_on_deploy | Deploy → JSON-lines audit запись | context_deployer.py | C |
| tests/unit/test_context_deployer_audit_integration.py | test_audit_entry_contains_required_fields | ts, tag, status, msg поля | shared/audit_logger.py | C |
| tests/unit/test_context_deployer_audit_integration.py | test_docker_orchestrator_writes_audit_on_healthcheck_fail | Healthcheck fail → UNHEALTHY | docker_orchestrator.py | C |
| tests/unit/test_context_deployer_audit_integration.py | test_docker_orchestrator_writes_audit_on_deploy_complete | Deploy done → DEPLOYED | docker_orchestrator.py | C |
| tests/unit/test_context_deployer_audit_integration.py | test_audit_format_is_valid_json_lines | Каждая строка — валидный JSON | shared/audit_logger.py | C |
| tests/unit/test_context_deployer_audit_integration.py | test_old_shell_format_unchanged | Shell pipe-формат не сломан | audit_logging.sh | C |
| tests/gates/test_gate_deploy_paths.py | test_canonical_paths_registered | Все entrypoint-manifest deploy-таргеты зарегистрированы | shared/deploy_paths.py | A |
| tests/gates/test_gate_deploy_paths.py | test_no_unregistered_paths | Нет новых незарегистрированных путей | shared/deploy_paths.py | A |
| tests/gates/test_gate_deploy_paths.py | test_deprecated_have_removal_plan | DEPRECATED пути имеют target_date + removal_mechanism | shared/deploy_paths.py | A |

---

## Verification Commands

```bash
# After Wave A
python3 -m pytest tests/gates/test_gate_deploy_paths.py -v

# After Wave B1
python3 -c "from core.internal.shared.ssh_command_parser import parse_ssh_command; print(parse_ssh_command('ping'))"
python3 -c "from core.internal.shared.platform_deliver import build_deliver_command; print(build_deliver_command('org', 'proj'))"
python3 -c "from core.internal.shared.audit_logger import write_audit_entry; print('OK')"

# After Wave B2
python3 -m pytest tests/unit/test_shared_ssh_command_parser.py tests/unit/test_shared_platform_deliver.py tests/unit/test_shared_audit_logger.py -v

# After Wave C1 (requires DevPlan 079 TASK-6 completed)
python3 -c "from core.internal.shared.docker_compose import retry_pull; print('retry_pull available')"

# After Wave C2
python3 -m pytest tests/unit/test_context_deployer_retry_pull.py tests/unit/test_context_deployer_audit_integration.py -v

# After Wave G (final)
make fix-gate && git add -u && make gate MODE=fast
```

---

## Next Steps

### Prerequisite: DevPlan 079 Wave 1 (shared/docker_compose.py)
```
Ensure core/internal/shared/ exists. If not:
  Option A: Implement DevPlan 070 first
  Option B (recommended): Bootstrap inline — create __init__.py as part of DevPlan 079 Wave 1 TASK-6

Then implement DevPlan 079 Wave 1 minimum: TASK-6 (shared/docker_compose.py with retry_pull).
This unblocks Phase C of DevPlan 081.
```

### Wave A (independent — can run NOW, parallel with prerequisite)
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave A: TASK-081A1, TASK-081A2, TASK-081A3
```

### Wave B1 (requires shared/ directory)
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave B1: TASK-081B1, TASK-081B3, TASK-081B5
```

### Wave B2
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave B2: TASK-081B2, TASK-081B4, TASK-081B6
```

### Wave B3
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave B3: TASK-081B7, TASK-081B8, TASK-081B9, TASK-081B10
```

### Wave C1 (requires DevPlan 079 TASK-6 completed)
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave C1: TASK-081C1, TASK-081C3
```

### Wave C2
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave C2: TASK-081C2, TASK-081C4
```

### Wave G (final gate)
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave G: TASK-081G1
```

$END_DEVPLAN
