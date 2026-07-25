$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Унификация deploy pipeline: (1) документировать 7 путей доставки кода с gate-тестом, (2) добавить retry+rollback в Python deploy-пути, (3) унифицировать парсер SSH_ORIGINAL_COMMAND, (4) вынести platform-deliver builder в shared, (5) унифицировать формат audit-логов.
DESCRIPTION:           Закрывает DRIFT-D1, DRIFT-D3, DRIFT-D4, DRIFT-D5, DRIFT-D6 из Brief 077. Создаёт shared модули для ssh_command_parser, platform_deliver, audit_logger. Добавляет retry+rollback в context_deployer.py и docker_orchestrator.py на основе общей docker_compose.py библиотеки из DevPlan 079. Документирует канонические deploy-пути и добавляет CI gate.
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
  - AC9: Все существуующие тесты проходят; новые unit-тесты для shared модулей
  - AC10: `make gate MODE=fast` — green
IMPLEMENTS:            Brief 077 — Wave D (Deploy Pipeline Unification): DRIFT-D1, DRIFT-D3, DRIFT-D4, DRIFT-D5, DRIFT-D6
IMPACTS:
  - NEW: core/internal/shared/ssh_command_parser.py
  - NEW: core/internal/shared/platform_deliver.py
  - NEW: core/internal/shared/audit_logger.py
  - NEW: tests/unit/test_shared_ssh_command_parser.py
  - NEW: tests/unit/test_shared_platform_deliver.py
  - NEW: tests/unit/test_shared_audit_logger.py
  - NEW: tests/gates/test_gate_deploy_paths.py
  - MODIFIED: core/entrypoints/deploy.sh (импорт ssh_command_parser)
  - MODIFIED: core/internal/deploy/deploy-project.sh (импорт ssh_command_parser + platform_deliver)
  - MODIFIED: core/entrypoints/deploy-project.sh (импорт platform_deliver)
  - MODIFIED: core/internal/deploy/reconcile-projects.sh (импорт platform_deliver)
  - MODIFIED: core/internal/bootstrap/deploy/context_deployer.py (retry_pull + audit_logger)
  - MODIFIED: core/internal/bootstrap/deploy/docker_orchestrator.py (audit_logger)
  - MODIFIED: core/lib/audit_logging.sh (адаптация к JSON-lines формату)
REQUIRES:              DevPlan 079 (shared/docker_compose.py с retry_pull). DevPlan 076 (уже покрывает reconcile-projects.sh platform-deliver — D5 partial).

---

## Requirements Analysis

### Success Criteria
1. **SC1: Единый парсер SSH_ORIGINAL_COMMAND.** Оба parser'а (deploy.sh:29-123, deploy-project.sh:430-481) заменены на вызов общего `parse_ssh_command()`.
2. **SC2: Единый platform-deliver builder.** Три места сборки (entrypoints/deploy-project.sh:230-236, internal/deploy-project.sh:456-481, reconcile-projects.sh:192) используют `build_deliver_command()`.
3. **SC3: Единый audit-формат.** JSON-lines формат в Python и shell. Обратная совместимость с существующим форматом через миграцию.
4. **SC4: Retry+rollback в Python-путях.** context_deployer.py получает retry_pull (3 попытки, backoff 5/10/20s) через shared/docker_compose.py.
5. **SC5: Gate test для deploy-путей.** CI блокирует добавление новых deploy-путей без регистрации.

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
│  DEPRECATED: ["Bootstrap compose stub (generated, temporary)"]        │
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
A: Gate test (pytest) позволяет проверить runtime-регистрацию: парсит entrypoint-manifest.yaml, проверяет что все зарегистрированные deploy-пути соответствуют каноническому списку. Блокирует merge если появляется новый незарегистрированный путь. Lint rule был бы статическим и не видел бы indirect вызовы.

### ## @rationale D4: platform-deliver как отдельный shared модуль
Q: Почему не часть ssh_command_parser?
A: Разная ответственность: ssh_command_parser парсит входящие команды, platform_deliver строит исходящие. Разные callers (парсинг — на VPS, build — на dev-машине и в reconcile). Разделение по Single Responsibility.

---

## $TASKS

### TASK-1: Создать `core/internal/shared/ssh_command_parser.py`
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
**Dependencies:** None
**Complexity:** 3

### TASK-2: Создать `tests/unit/test_shared_ssh_command_parser.py`
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
**Dependencies:** TASK-1
**Complexity:** 3

### TASK-3: Создать `core/internal/shared/platform_deliver.py`
**Owner:** Coder
**Output:** `core/internal/shared/platform_deliver.py` (~60 LOC)
**Acceptance Criteria:**
- `build_deliver_command(org: str = "", project: str = "") -> str`
  - С org: `"platform-deliver {org} {project}"`
  - Без org: `"platform-deliver {project}"`
- `parse_deliver_args(args: str) -> tuple[str, str]`: парсит строку после "platform-deliver " → (org, project)
  - Два токена: org + project
  - Один токен: project (org="")
- CLI: `python3 -m core.internal.shared.platform_deliver build --org "myorg" --project "myproj"`
- CLI: `python3 -m core.internal.shared.platform_deliver parse "myorg myproj"`
- Module contract с GREP_SUMMARY, STRUCTURE, @purpose, @invariants
**Dependencies:** None
**Complexity:** 2

### TASK-4: Создать `tests/unit/test_shared_platform_deliver.py`
**Owner:** Coder
**Output:** `tests/unit/test_shared_platform_deliver.py` (~60 LOC)
**Acceptance Criteria:**
- test_build_with_org: org="myorg", project="myproj" → "platform-deliver myorg myproj"
- test_build_without_org: org="", project="myproj" → "platform-deliver myproj"
- test_parse_two_tokens: "myorg myproj" → ("myorg", "myproj")
- test_parse_one_token: "myproj" → ("", "myproj")
- test_parse_with_spaces: "  myorg   myproj  " → ("myorg", "myproj") (xargs trimming)
- LDD: минимум один IMP:9 лог
**Dependencies:** TASK-3
**Complexity:** 2

### TASK-5: Создать `core/internal/shared/audit_logger.py`
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
**Dependencies:** None
**Complexity:** 3

### TASK-6: Создать `tests/unit/test_shared_audit_logger.py`
**Owner:** Coder
**Output:** `tests/unit/test_shared_audit_logger.py` (~80 LOC)
**Acceptance Criteria:**
- test_write_entry_creates_file: первый вызов создаёт файл
- test_write_entry_json_valid: запись → валидный JSON
- test_read_entries: 3 записи → read(limit=2) → 2 записи
- test_read_empty_log: пустой/отсутствующий файл → []
- test_concurrent_writes: множественные вызовы → все записи сохраняются (атомарность)
- LDD: минимум один IMP:9 лог
**Dependencies:** TASK-5
**Complexity:** 3

### TASK-7: Рефакторить deploy.sh — импорт ssh_command_parser
**Owner:** Coder
**Output:** `core/entrypoints/deploy.sh` (~80 LOC, было 126)
**Acceptance Criteria:**
- `parse_verb()` (строка 43-123) заменён на вызов `python3 -m core.internal.shared.ssh_command_parser parse "$raw"` или аналогичный
- Shell получает JSON stdout → извлекает verb, args
- dispatch логика сохранена (ping/exit/remove/status/verify/deploy)
- Stripping path prefix и platform-deploy делегированы Python-модулю
**Dependencies:** TASK-1
**Complexity:** 3

### TASK-8: Рефакторить deploy-project.sh (internal) — импорт ssh_command_parser + platform_deliver
**Owner:** Coder
**Output:** `core/internal/deploy/deploy-project.sh` (~50 LOC изменено)
**Acceptance Criteria:**
- Stripping path prefix (строка 436-440) делегирован `parse_ssh_command()` из shared
- Platform-deliver verb detection (строка 456-481) использует `classify_verb()` и `parse_deliver_args()` из shared
- `handle_deliver()` получает (org, project) через shared вместо inline парсинга
- Локальный парсинг platform-deliver args удалён
**Dependencies:** TASK-1, TASK-3
**Complexity:** 3

### TASK-9: Рефакторить entrypoints/deploy-project.sh — импорт platform_deliver
**Owner:** Coder
**Output:** `core/entrypoints/deploy-project.sh` (~10 LOC изменено)
**Acceptance Criteria:**
- `deliver_verb` (строка 231-236) заменён на `$(python3 -m core.internal.shared.platform_deliver build --org "$ORG" --project "$PROJECT_NAME")`
- Локальное построение строки `platform-deliver ${ORG} ${PROJECT_NAME}` удалено
**Dependencies:** TASK-3
**Complexity:** 1

### TASK-10: Рефакторить reconcile-projects.sh — импорт platform_deliver
**Owner:** Coder
**Output:** `core/internal/deploy/reconcile-projects.sh` (~5 LOC изменено)
**Acceptance Criteria:**
- `deliver_verb` (строка 192) заменён на `$(python3 -m core.internal.shared.platform_deliver build --org "${proj_org}" --project "${proj_name}")`
- Локальное построение строки удалено
**Dependencies:** TASK-3
**Complexity:** 1

### TASK-11: Добавить retry_pull в context_deployer.py
**Owner:** Coder
**Output:** `core/internal/bootstrap/deploy/context_deployer.py` (~20 LOC изменено)
**Acceptance Criteria:**
- `_deploy_single_project()` (строка 376-452): перед fallback build вызывается `retry_pull()` из shared/docker_compose.py
- 3 попытки с backoff 5/10/20 секунд
- Если retry_pull успешен → channel="ghcr" (без fallback build)
- Если все попытки неуспешны → существующая логика fallback build
- PRECONDITION: TASK-6 и TASK-8 из DevPlan 079 выполнены (shared/docker_compose.py с retry_pull существует)
**Dependencies:** DevPlan 079 (shared/docker_compose.py)
**Complexity:** 2

### TASK-12: Мигрировать context_deployer.py и docker_orchestrator.py на audit_logger
**Owner:** Coder
**Output:** `context_deployer.py` + `docker_orchestrator.py` (~30 LOC изменено)
**Acceptance Criteria:**
- `context_deployer._write_audit()` (строка 613-624) заменён на `write_audit_entry()` из shared/audit_logger.py
- `docker_orchestrator.py`: ключевые операции (deploy, healthcheck fail) пишут audit через shared/audit_logger.py
- Формат: JSON-lines
- Старый формат audit_log в shell НЕ трогаем (обратная совместимость)
**Dependencies:** TASK-5
**Complexity:** 2

### TASK-13: Создать Deploy Path Registry и gate test
**Owner:** Coder
**Output:** `core/internal/shared/deploy_paths.py` + `tests/gates/test_gate_deploy_paths.py`
**Acceptance Criteria:**
- `deploy_paths.py` содержит константу `CANONICAL_DEPLOY_PATHS` с 6 документированными путями:
  1. "CI → platform-deliver + deploy.sh" — git push → CI → tar via SSH forced-command
  2. "make deploy-project (direct)" — tar + SSH, bypass CI
  3. "context_deployer.py (Python)" — ghcr.io pull + build fallback
  4. "deploy-modules.sh (system modules)" — docker compose up (system: install.sh)
  5. "Core SCP/rsync" — CI workflow core-deploy
  6. "Context-overlay git" — git clone/pull via ensure_context_repo()
- Константа `DEPRECATED_DEPLOY_PATHS`:
  1. "Bootstrap compose stub" — временная заглушка nginx:alpine, заменяется CI delivery
- Gate test `tests/gates/test_gate_deploy_paths.py`:
  - Парсит `entrypoint-manifest.yaml` → извлекает все deploy-related таргеты
  - Проверяет: каждый make-таргет с deploy-семантикой имеет запись в CANONICAL_DEPLOY_PATHS
  - Проверяет: нет незарегистрированных путей
  - Проверяет: DEPRECATED пути имеют явный план удаления
- Module contract для deploy_paths.py
**Dependencies:** None
**Complexity:** 3

### TASK-14: Gate + интеграционная верификация
**Owner:** Coder
**Output:** Все тесты зелёные, `make gate MODE=fast` проходит
**Acceptance Criteria:**
- `python3 -m pytest tests/unit/test_shared_ssh_command_parser.py tests/unit/test_shared_platform_deliver.py tests/unit/test_shared_audit_logger.py -v` — все проходят
- `python3 -m pytest tests/gates/test_gate_deploy_paths.py -v` — проходит
- `python3 -m pytest tests/unit/test_docker_orchestrator.py -v` — без регрессии
- `make fix-gate && git add -u && make gate MODE=fast` — green
**Dependencies:** TASK-1 through TASK-13
**Complexity:** 2

---

## $PARALLEL_GROUPS

### Wave 1 (independent shared modules)
- Tasks: TASK-1, TASK-3, TASK-5, TASK-13
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 1: TASK-1, TASK-3, TASK-5, TASK-13`

### Wave 2 (tests for Wave 1 modules)
- Tasks: TASK-2, TASK-4, TASK-6
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 2: TASK-2, TASK-4, TASK-6`

### Wave 3 (shell refactoring — depends on Wave 1)
- Tasks: TASK-7, TASK-8, TASK-9, TASK-10, TASK-11, TASK-12
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 3: TASK-7, TASK-8, TASK-9, TASK-10, TASK-11, TASK-12`

### Wave 4 (final verification)
- Tasks: TASK-14
- Command: `coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 4: TASK-14`

---

## Acceptance Criteria Summary

| # | Criteria | Verification |
|---|----------|-------------|
| AC1 | `parse_ssh_command(raw)` в shared/ssh_command_parser.py | test_shared_ssh_command_parser.py |
| AC2 | `build_deliver_command(org, project)` в shared/platform_deliver.py | test_shared_platform_deliver.py |
| AC3 | `write_audit_entry(tag, status, msg)` в shared/audit_logger.py | test_shared_audit_logger.py |
| AC4 | context_deployer.py использует retry_pull | код-ревью |
| AC5 | deploy.sh + deploy-project.sh используют parse_ssh_command | код-ревью |
| AC6 | deploy-project.sh + reconcile-projects.sh используют build_deliver_command | код-ревью |
| AC7 | Python deploy-пути используют write_audit_entry | код-ревью |
| AC8 | Gate test блокирует новые незарегистрированные пути | test_gate_deploy_paths.py |
| AC9 | Все тесты проходят | pytest |
| AC10 | `make gate MODE=fast` green | CI |

---

## File Manifest

| File | Action | LOC change |
|------|--------|------------|
| `core/internal/shared/ssh_command_parser.py` | NEW | +100 |
| `core/internal/shared/platform_deliver.py` | NEW | +60 |
| `core/internal/shared/audit_logger.py` | NEW | +80 |
| `core/internal/shared/deploy_paths.py` | NEW | +40 |
| `tests/unit/test_shared_ssh_command_parser.py` | NEW | +80 |
| `tests/unit/test_shared_platform_deliver.py` | NEW | +60 |
| `tests/unit/test_shared_audit_logger.py` | NEW | +80 |
| `tests/gates/test_gate_deploy_paths.py` | NEW | +60 |
| `core/entrypoints/deploy.sh` | MODIFY | ~-40 (126→86) |
| `core/internal/deploy/deploy-project.sh` | MODIFY | ~-20 (парсинг делегирован) |
| `core/entrypoints/deploy-project.sh` | MODIFY | ~-5 (deliver_verb делегирован) |
| `core/internal/deploy/reconcile-projects.sh` | MODIFY | ~-5 (deliver_verb делегирован) |
| `core/internal/bootstrap/deploy/context_deployer.py` | MODIFY | ~+15 (retry_pull) + ~-10 (audit → shared) |
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | MODIFY | ~+10 (audit → shared) |

---

## $TEST_SPEC

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| tests/unit/test_shared_ssh_command_parser.py | test_parse_ping | "ping" → verb=ping | shared/ssh_command_parser.py |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_remove | "remove myproject" → verb=remove | shared/ssh_command_parser.py |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_status | "status myproject" → verb=status | shared/ssh_command_parser.py |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_platform_deliver_org | "platform-deliver org proj" | shared/ssh_command_parser.py |
| tests/unit/test_shared_ssh_command_parser.py | test_parse_platform_deliver_legacy | "platform-deliver proj" (1 arg) | shared/ssh_command_parser.py |
| tests/unit/test_shared_ssh_command_parser.py | test_strip_path_prefix | Префикс пути stripped | shared/ssh_command_parser.py |
| tests/unit/test_shared_ssh_command_parser.py | test_strip_platform_deploy | "platform-deploy" stripped | shared/ssh_command_parser.py |
| tests/unit/test_shared_ssh_command_parser.py | test_empty_command | "" → ValueError | shared/ssh_command_parser.py |
| tests/unit/test_shared_platform_deliver.py | test_build_with_org | С org → "platform-deliver org proj" | shared/platform_deliver.py |
| tests/unit/test_shared_platform_deliver.py | test_build_without_org | Без org → "platform-deliver proj" | shared/platform_deliver.py |
| tests/unit/test_shared_platform_deliver.py | test_parse_two_tokens | parse "org proj" → (org, proj) | shared/platform_deliver.py |
| tests/unit/test_shared_platform_deliver.py | test_parse_one_token | parse "proj" → ("", proj) | shared/platform_deliver.py |
| tests/unit/test_shared_audit_logger.py | test_write_entry_creates_file | Первый вызов создаёт файл | shared/audit_logger.py |
| tests/unit/test_shared_audit_logger.py | test_write_entry_json_valid | JSON-lines формат валиден | shared/audit_logger.py |
| tests/unit/test_shared_audit_logger.py | test_read_entries_limit | read(limit=2) из 3 записей | shared/audit_logger.py |
| tests/unit/test_shared_audit_logger.py | test_read_empty_log | Пустой лог → [] | shared/audit_logger.py |
| tests/gates/test_gate_deploy_paths.py | test_canonical_paths_registered | Все entrypoint-manifest deploy-таргеты зарегистрированы | shared/deploy_paths.py |
| tests/gates/test_gate_deploy_paths.py | test_no_unregistered_paths | Нет новых незарегистрированных путей | shared/deploy_paths.py |
| tests/gates/test_gate_deploy_paths.py | test_deprecated_have_removal_plan | DEPRECATED пути имеют план удаления | shared/deploy_paths.py |

---

## Verification Commands

```bash
# After Wave 1
python3 -c "from core.internal.shared.ssh_command_parser import parse_ssh_command; print(parse_ssh_command('ping'))"
python3 -c "from core.internal.shared.platform_deliver import build_deliver_command; print(build_deliver_command('org', 'proj'))"
python3 -c "from core.internal.shared.audit_logger import write_audit_entry; print('OK')"

# After Wave 2
python3 -m pytest tests/unit/test_shared_ssh_command_parser.py tests/unit/test_shared_platform_deliver.py tests/unit/test_shared_audit_logger.py -v

# After Wave 3
python3 -m pytest tests/gates/test_gate_deploy_paths.py -v

# After Wave 4 (final)
make fix-gate && git add -u && make gate MODE=fast
```

---

## Next Steps

### Wave 1
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 1: TASK-1, TASK-3, TASK-5, TASK-13
```

### Wave 2
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 2: TASK-2, TASK-4, TASK-6
```

### Wave 3
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 3: TASK-7, TASK-8, TASK-9, TASK-10, TASK-11, TASK-12
```

### Wave 4
```
coder Read .ai/plans/081-deploy-pipeline-unification/01-DevPlan.md, implement Wave 4: TASK-14
```

$END_DEVPLAN
