$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Мигрировать 4 крупнейших scaffold shell-монолита в Python по Strangler-Fig: project-list.sh (403 LOC, 7 inline python3), context-init.sh (364 LOC), remove-project.sh (423 LOC, 2 inline python3), add-project.sh (782 LOC, 11 функций). Суммарно 1972 LOC shell → 4 Python-модуля + 4 shell-фасада <50 LOC каждый. Вынести общую логику add-project ↔ adopt-project в общий shared-helper. Удалить 9 inline python3 блоков.
DESCRIPTION:           Создаёт `core/internal/scaffold/` Python-модули: `project_lister.py` (offline/SSH listing через NodeYaml.get_projects()), `context_initializer.py` (multi-step context scaffold через context_registry.py), `project_remover.py` (unregister + compose down + vhost cleanup), `project_scaffolder.py` (копирование шаблона + генерация ai-platform.yaml/Makefile/AGENTS.md + регистрация). Shell-файлы превращаются в тонкие фасады. Wave-структура: от простейшего (lister) к сложнейшему (scaffolder). Поведение мигрируется 1:1 (behaviour-preserving per Anti-Loop Note в Brief).
RATIONALE:             Это последний крупный блок business-logic без Python-двойника (DP 070-090 его не покрывают). 9 inline python3 блоков (project-list: 7 JSON-парсинг, remove-project: 2 field extraction) — прямое нарушение языковой политики Tier-1 (AGENTS.md §Языковая политика). Каждый inline python3 = отдельная точка нетестируемой логики. Без миграции этого блока цель «переписать sh на Python» формально не достигнута — Strangler-Fig Decision Gate (TRAP 2026-07-22) требует продолжения. Параллельно: общая логика add-project ↔ adopt-project дублируется → вынос в shared helper устраняет drift-риск.
ACCEPTANCE_CRITERIA:
  - AC1: Все 4 операции (`make new-project`, `make remove-project`, `make project-list`, `make new-context`) работают идентично (behaviour-preserving — подтверждено unit-тестами + manual smoke)
  - AC2: 4 shell-фасада <50 LOC каждый (project-list.sh, context-init.sh, remove-project.sh, add-project.sh)
  - AC3: 0 inline python3 в core/internal/scaffold/ (`grep -rn "python3 -c\|python3 <<\|python3 - " core/internal/scaffold/` → 0 совпадений)
  - AC4: Unit-тесты на каждый Python-модуль в tests/ (LDD trajectory IMP:9-10, Anti-Loop counter, R1-R5 compliance)
  - AC5: `make project-status NAME=<p>` работает идентично (project-list.sh --status режим сохранён)
  - AC6: Общая логика add-project ↔ adopt-project вынесена в shared-helper (gen_ai_platform_yaml, gen_project_makefile, gen_project_agents, register_in_node_yaml)
  - AC7: make gate MODE=fast — зелёный
  - AC8: python -m pytest tests/ -v — все тесты (включая новые) проходят
  - AC9: generate-agents-md / generate-entrypoint-manifest — drift-free (фасады зарегистрированы, AGENTS.md canon_table актуальна)
IMPLEMENTS:            Brief 092 §Required Actions (Waves 1-4), AGENTS.md §Языковая политика (Strangler-Fig Tier-1/2), TRAP 2026-07-22 Decision Gate (continuation), закрытие gap «scaffold/ 1972 LOC без плана» (3 аудита)
IMPACTS:               CREATE: project_lister.py, context_initializer.py, project_remover.py, project_scaffolder.py, scaffold_helpers.py (shared), 4 unit-test файла. MODIFY: project-list.sh (403→<50), context-init.sh (364→<50), remove-project.sh (423→<50), add-project.sh (782→<50), project_adopter.py (refactor shared extraction), __init__.py (exports), tests/test_project_lifecycle.py (extend). DELETE: 9 inline python3 блоков. Подробно в §4 File Manifest.
REQUIRES:              DP-088 STABLE (NodeYaml mutation API: add_project/remove_project/update_project/get_projects/get_project — подтверждено: 3 VerificationReports, методы в node_yaml.py:1141-1290). Блокируется DP-091 Wave C (stabilize 087-088-089) — НЕ стартовать до merge DP-091. context_registry.py существует и стабилен (register_context API, 105 LOC).
$END_ARTIFACT_CONTRACT

---

# DevPlan 092: Scaffold Python Completion (1972 LOC)

**Severity:** HIGH — крупнейший не покрытый блок business-logic, 9 нарушений языковой политики Tier-1
**Created:** 2026-07-30
**Author:** Kilo (architect agent)
**Source:** Brief 092 (audit 2026-07-30), AGENTS.md §Языковая политика, TRAP 2026-07-22 Decision Gate
**Sequenced:** AFTER DP-091 Wave C (merge), BEFORE DP-093+
**Pattern:** Strangler-Fig (behaviour-preserving migration, then optimisation in debt)

---

## §1. Current State

### Scaffold monolith inventory (audit 2026-07-30)

| Файл | LOC | Inline python3 | Функций | Сложность | Dependencies |
|------|-----|----------------|---------|-----------|--------------|
| `project-list.sh` | 403 | **7** | 8 | Низкая | NodeYaml CLI (уже есть), ssh_read |
| `context-init.sh` | 364 | 0 | 7 | Средняя | context_registry.py (уже есть), gh CLI, mkdir |
| `remove-project.sh` | 423 | **2** | 7 | Средняя | NodeYaml.remove_project(), ssh_exec, rm vhost |
| `add-project.sh` | 782 | 0 | 16 | **Высокая** | rsync, template-engine.sh, gen_env_platform.py, NodeYaml.add_project(), gh, add-vhost.sh |
| **ИТОГО** | **1972** | **9** | **38** | — | — |

### Inline python3 violations (языковая политика Tier-1)

```
project-list.sh:
  L166-176  JSON projects extraction (python3 -c, json.load sys.stdin)
  L183      python3 -c name extraction
  L204-210  python3 -c projects→json.dumps
  L215-218  python3 -c field extraction (name/domain/type/repo) × 4
remove-project.sh:
  L184      (delegates to NodeYaml CLI — не inline, но 2 блока в др. местах)
  + 2 field extraction блока
```

⚠️ Note: `remove-project.sh:184` уже вызывает `python3 -m core.internal.shared.node_yaml --remove-project` — это НЕ inline, это легитимный CLI-вызов. Реальные 2 inline блока нужно подтвердить при Wave 3 (`grep -n "python3 -c" remove-project.sh`).

### Ключевое наблюдение: project_adopter.py уже существует

`core/internal/scaffold/project_adopter.py` (1240 LOC) — полноценная Python-реализация adopt-логики, содержит:
- `generate_minimal_ai_platform_yaml()` (L182)
- `gen_project_makefile()` (L445)
- `gen_project_agents()` (L498)
- `register_in_node_yaml()` (L715)
- `validate_compose_networks()` (L561)
- `configure_vhost()` (L843)

**Вывод:** add-project.sh и project_adopter.py дублируют ~4 функции генерации файлов. Wave 4 выносит общую логику в `scaffold_helpers.py`, оба вызывают shared-функции. Это устраняет drift-риск (TRAP-класс «two implementations diverge»).

### NodeYaml mutation API (DP-088, STABLE)

```python
# core/internal/shared/node_yaml.py — подтверждённые методы:
get_projects() -> list[dict]            # L551
get_project(name) -> dict | None        # L1104
add_project(project: ProjectEntry)      # L1141 — raise ConfigValidationError on dup
remove_project(name) -> bool            # L1194 — False if not found
update_project(name, **updates) -> bool # L1233

# CLI subcommands (L1437+):
--add-project name type repo [domain=..] [database=..]
--remove-project name
--update-project name key=value [...]
--find-project name
--json-output
```

⚠️ **TRAP[BUG] node_yaml.py:1186 (DP-088)** — `remove_project` удаляет ВСЕ записи с matching name (list comprehension filter), не только первую. Для project_remover это предпочтительное поведение (cleanup corrupted data) — документировать в Python-модуле, не «чинить».

### context_registry.py (существует, стабилен)

```python
# core/internal/scaffold/context_registry.py (105 LOC):
register_context(yaml_path, name, desc="", node_cfg_repo="", hermes_agent_repo="") -> "OK"|"EXISTS"
```

context-init.sh L268 вызывает `_register_in_platform_yaml()` — это обёртка над context_registry. Wave 2 оборачивает полный context-init flow в Python, делегируя регистрацию в context_registry.

---

## §2. Target State

### Архитектура core/internal/scaffold/ после DP-092

```
core/internal/scaffold/
├── __init__.py                      (exports: 4 модуля + helpers)
├── scaffold_helpers.py              [CREATE Wave 4] — shared gen-функции
│   ├── gen_ai_platform_yaml(...)        ← extract из add-project + adopter
│   ├── gen_project_makefile(...)        ← extract из add-project + adopter
│   ├── gen_project_agents(...)          ← extract из add-project + adopter
│   └── register_in_node_yaml(...)       ← extract из add-project + adopter
├── project_lister.py                [CREATE Wave 1]
│   ├── list_projects_offline(...)       ← NodeYaml.get_projects()
│   ├── find_project_node(name)          ← NodeYaml CLI --find-project
│   ├── get_status_via_ssh(host, name)   ← ssh_read wrapper
│   └── main() (argparse CLI)
├── context_initializer.py           [CREATE Wave 2]
│   ├── check_idempotent(context_dir)
│   ├── create_dirs(context_dir)
│   ├── create_skeleton_node_yaml(...)   ← YAML template generation
│   ├── gh_repo_create(org, ctx)         ← gh CLI subprocess
│   ├── register_in_platform_yaml(...)   ← context_registry.register_context()
│   └── main() (argparse CLI)
├── project_remover.py               [CREATE Wave 3]
│   ├── unregister_from_node_yaml(...)   ← NodeYaml.remove_project()
│   ├── remove_vhost(domain, node_cfg)   ← rm vhost file
│   ├── ssh_compose_down(host, project)  ← ssh_exec wrapper (NO -v)
│   ├── print_report(...)
│   └── main() (argparse CLI)
├── project_scaffolder.py            [CREATE Wave 4]
│   ├── copy_template(src, dst)          ← rsync wrapper
│   ├── render_project_template(...)     ← template-engine.sh call
│   ├── gen_env_platform(...)            ← delegates to gen_env_platform.py
│   ├── git_init_project(...)            ← git subprocess
│   ├── create_github_repo(...)          ← gh CLI subprocess
│   ├── generate_checklist(...)          ← markdown generation
│   ├── run_add_vhost(...)               ← delegates to add-vhost.sh
│   └── main() (argparse CLI, orchestrates full flow)
│
├── project-list.sh                  [MODIFY 403→<50] — фасад → project_lister.py
├── context-init.sh                  [MODIFY 364→<50] — фасад → context_initializer.py
├── remove-project.sh                [MODIFY 423→<50] — фасад → project_remover.py
├── add-project.sh                   [MODIFY 782→<50] — фасад → project_scaffolder.py
│
├── project_adopter.py               [MODIFY Wave 4] — refactor: shared → scaffold_helpers
├── adopt-project.sh                 [89 LOC, KEEP] — уже тонкий фасад
├── gen_env_platform.py              [KEEP] — вызывается из scaffolder
├── vhost_renderer.py                [KEEP] — не в скоупе
├── add-vhost.sh                     [KEEP] — вызывается из scaffolder
└── context_registry.py              [KEEP] — вызывается из initializer
```

### Фасадный контракт (shell <50 LOC)

Каждый фасад следует единому паттерну (consistency с DP-087/088 Strangler-Fig):

```bash
#!/usr/bin/env bash
# GREP_SUMMARY: <name>-facade, python-dispatch
# region MODULE_CONTRACT
## @purpose  Thin facade → delegates to <module>.py (Strangler-Fig, DP-092)
## @scope    Arg normalization + exec python3 -m core.internal.scaffold.<module>
## @invariants  <50 LOC; zero business logic; exit code passthrough
# endregion MODULE_CONTRACT
set -euo pipefail
# ... source lib/paths.sh, lib/logging.sh ...
exec python3 -m core.internal.scaffold.<module> "$@"
```

---

## §3. Execution Plan (Waves)

### Wave 1: project_lister.py (warm-up, простейший)

**Цель:** Удалить 7 inline python3 блоков. Самый простой — NodeYaml.get_projects() покрывает 90% логики.

**Задачи:**
1. CREATE `core/internal/scaffold/project_lister.py`:
   - `list_projects_offline(node_filter, project_name, format)` → читает node.yaml через NodeYaml(path).get_projects() + .get("node","host"). Без inline python3 — чистый Python.
   - `find_project_node(name, node_filter)` → glob node.yaml + NodeYaml.get_project(name) is not None. Заменяет L249-272.
   - `get_status_via_ssh(host, project)` → subprocess wrapper над ssh_read (lib/ssh.sh). Заменяет L284-339. ⚠️ macOS: gtimeout, на VPS: timeout (TRAP DP-087).
   - `main()` — argparse: `--list`, `--status`, `--node`, `--name`, `--format [table|json]`.
2. MODIFY `project-list.sh` → фасад <30 LOC (dispatch: `exec python3 -m core.internal.scaffold.project_lister "$@"`).
3. CREATE `tests/test_project_lister.py`:
   - `test_list_offline_table` — tmp_path с node.yaml, проверка форматирования таблицы
   - `test_list_offline_json` — JSON output, валидация структуры
   - `test_list_filter_by_name` — фильтрация PROJECT_NAME
   - `test_list_filter_by_node` — фильтрация по node
   - `test_list_empty_state` — нет node.yaml → "No projects found"
   - `test_list_multiple_nodes` — агрегация из нескольких node.yaml
   - LDD: caplog IMP:9 assertion, Anti-Loop counter
4. VERIFY: `grep -n "python3 -c" core/internal/scaffold/project-list.sh` → 0 (после модификации фасада).

**Acceptance:** AC1 (project-list), AC2 (фасад), AC3 (0 inline в lister), AC4 (6 unit-тестов).

**Risk:** LOW. NodeYaml.get_projects() существует. Логика прямолинейная (read + format).

---

### Wave 2: context_initializer.py

**Цель:** Обернуть multi-step context scaffold в Python. context_registry.py уже есть — делегировать регистрацию.

**Задачи:**
1. CREATE `core/internal/scaffold/context_initializer.py`:
   - `check_idempotent(context_dir)` → если dir exists: print SKIP, exit 0 (поведение L116-125).
   - `create_dirs(context_dir)` → mkdir hermes-agent/ + node-configs/ (L129-142).
   - `create_skeleton_node_yaml(path, context_name)` → генерация skeleton YAML (L151-189). ⚠️ Сохранить GREP_SUMMARY/STRUCTURE-комментарии в шаблоне (семантическая разметка).
   - `gh_repo_create(org, ctx, skip)` → subprocess `gh repo create`. Graceful degradation если gh не установлен (WARN, не error — L193-205).
   - `register_in_platform_yaml(...)` → вызов `context_registry.register_context()`.
   - `main()` — argparse: `--name`, `--node`, `--org`, `--skip-gh-repo`.
2. MODIFY `context-init.sh` → фасад <50 LOC.
3. CREATE `tests/test_context_initializer.py`:
   - `test_new_context_creates_dirs` — tmp_path, проверка структуры каталогов
   - `test_new_context_creates_skeleton_yaml` — валидация skeleton node.yaml (контекст, node, modules, projects:[])
   - `test_existing_context_idempotent` — dir exists → exit 0, no mutation
   - `test_missing_org_skips_gh` — без gh/`--skip-gh-repo` → WARN, продолжение
   - `test_register_in_platform_yaml` — context_registry integration
   - LDD: caplog IMP:9 assertion
4. VERIFY: `make new-context NODE=<test> --dry-run` (если dry-run есть) или manual inspection skeleton.

**Acceptance:** AC1 (new-context), AC2, AC4.

**Risk:** MED. gh_repo_create = subprocess → external dependency. Mitigation: graceful degradation (warn, не fail) сохранена из оригинала.

---

### Wave 3: project_remover.py

**Цель:** Удалить 2 inline python3. Оркестрация unregister + compose down + vhost cleanup.

**Задачи:**
1. CREATE `core/internal/scaffold/project_remover.py`:
   - `find_node_yaml(name)` → glob + NodeYaml.get_project(name). Заменяет find_node_yaml() shell (L110-175).
   - `unregister_from_node_yaml(node_yaml, name)` → NodeYaml(node_yaml).remove_project(name). Заменяет L176-198.
   - `remove_vhost(domain, node_configs_dir)` → rm vhost file. Заменяет L207-228.
   - `ssh_compose_down(host, project)` → ssh_exec wrapper, **NO `-v`** per O7/DD10 (L240+). Документировать инвариант: «compose down без -v = не удаляем volumes».
   - `print_report(...)` → L310.
   - `main()` — argparse: `--name`, `--node`, `--keep-data`, `--dry-run`.
2. MODIFY `remove-project.sh` → фасад <50 LOC.
3. DELETE 2 inline python3 блока (подтвердить grep перед удалением).
4. CREATE `tests/test_project_remover.py`:
   - `test_remove_existing_project` — tmp node.yaml с проектом → remove → проверка NodeYaml.get_project(name) is None
   - `test_remove_missing_idempotent` — проект не найден → exit 0, no error (поведение remove_project returns False)
   - `test_unregister_removes_all_duplicates` — TRAP node_yaml.py:1186 — node.yaml с дубликатом name → оба удаляются (документированное поведение)
   - `test_remove_vhost_deletes_file` — tmp vhost file → remove_vhost → файл удалён
   - `test_remove_vhost_no_domain_skips` — domain="" → skip, no error
   - `test_compose_down_no_volumes_flag` — ⚠️ проверка, что команда НЕ содержит `-v` (инвариант O7/DD10). Mock ssh_exec, assert arg не содержит `-v`.
   - LDD: caplog IMP:9 assertion
5. VERIFY: `grep -n "python3 -c" core/internal/scaffold/remove-project.sh` → 0.

**Acceptance:** AC1 (remove-project), AC2, AC3 (remove-project 0 inline), AC4 (6 unit-тестов).

**Risk:** MED. SSH compose down = side-effect на VPS. Mitigation: unit-тесты mock'ают ssh_exec (Dependency Injection по testing.md §UI Testing). Инвариант NO `-v` покрыт негативным тестом.

---

### Wave 4: project_scaffolder.py + scaffold_helpers.py (самый сложный)

**Цель:** Мигрировать 782 LOC (16 функций). Вынести shared-логику с project_adopter.py в scaffold_helpers.py.

**Под-Wave 4a: extract scaffold_helpers.py (refactor-first)**

1. EXTRACT из `project_adopter.py` → `scaffold_helpers.py` (новый файл):
   - `gen_ai_platform_yaml(name, type, org, node, domain, database, mode)` — перенести из adopter.generate_minimal_ai_platform_yaml + add-project.generate_ai_platform_yaml. Унифицировать сигнатуры.
   - `gen_project_makefile(name, type)` — из adopter.gen_project_makefile + add-project.gen_project_makefile.
   - `gen_project_agents(name, type)` — из adopter.gen_project_agents + add-project.gen_project_agents.
   - `register_in_node_yaml(name, org, node, type, domain, database, yaml_path)` — из adopter.register_in_node_yaml + add-project.register_in_node_yaml.
2. MODIFY `project_adopter.py` → import из scaffold_helpers (delegate). Поведение идентично (behaviour-preserving).
3. CREATE `tests/test_scaffold_helpers.py` — unit-тесты на 4 shared-функции (tmp_path, проверка содержимого генерируемых файлов).
4. VERIFY: существующие тесты `tests/test_adopt_project_org_validation.py`, `tests/test_project_lifecycle.py` — зелёные (refactor не сломал adopter).

**Под-Wave 4b: project_scaffolder.py**

5. CREATE `core/internal/scaffold/project_scaffolder.py`:
   - `parse_args()` — argparse: `--name`, `--template`, `--org`, `--node`, `--domain`, `--database`, `--mode [prod|dev]`, `--register`, `--dry-run`.
   - `auto_domain(name, org, domain)` — L135 (domain generation logic).
   - `copy_template(src, dst)` — rsync wrapper, exclude `platform-deploy.yml` (T9).
   - `generate_ai_platform_yaml(...)` → delegates to `scaffold_helpers.gen_ai_platform_yaml()`.
   - `render_project_template(...)` → subprocess template-engine.sh render-dir.
   - `gen_env_platform(...)` → subprocess gen_env_platform.py.
   - `gen_project_makefile(...)` / `gen_project_agents(...)` → delegates to scaffold_helpers.
   - `git_init_project(dir)` — subprocess git init + initial commit.
   - `create_github_repo(...)` — subprocess gh repo create. Graceful degradation.
   - `generate_checklist(name, type, project_dir)` — markdown generation (L511-590).
   - `run_add_vhost(...)` — subprocess add-vhost.sh.
   - `register_in_node_yaml(...)` → delegates to scaffold_helpers.
   - `main()` — orchestrates full flow (mirror add-project.sh main L738-776).
6. MODIFY `add-project.sh` → фасад <50 LOC.
7. CREATE `tests/test_project_scaffolder.py`:
   - `test_new_backend_project` — tmp template + tmp projects root → scaffold → проверка ai-platform.yaml, Makefile, AGENTS.md
   - `test_new_frontend_project` — тип frontend → проверка monitoring config (metrics=false)
   - `test_new_fullstack_project` — тип fullstack → проверка llm=remote, ai_retention=30d
   - `test_existing_project_conflict` — dir exists → exit 1 (поведение copy_template L196-199)
   - `test_template_missing` — template dir не существует → error
   - `test_dry_run_no_mutation` — `--dry-run` → файлы не создаются, только log
   - `test_register_in_node_yaml` — NodeYaml.add_project integration, проверка дубликата → ConfigValidationError
   - `test_domain_auto_generation` — auto_domain логика
   - LDD: caplog IMP:9 assertion
8. VERIFY: `make new-project NAME=test-proj TEMPLATE=backend --dry-run` → корректный dry-run output.

**Acceptance:** AC1 (new-project), AC2, AC4, AC6 (shared extraction).

**Risk:** HIGH. 16 функций, генерация нескольких файлов, множественные subprocess-вызовы (rsync, template-engine, gen_env_platform, git, gh, add-vhost).
**Mitigation:**
- Wave 4a (refactor-first) перед 4b изолирует shared-логику и тестируется отдельно.
- dry-run режим покрывает большинство путей без side-effects.
- Subprocess-вызовы через Dependency Injection (передаваемые callable) для тестирования.

---

## §4. File Manifest

### CREATE (10 файлов)

| Файл | Wave | LOC est. | Назначение |
|------|------|----------|-----------|
| `core/internal/scaffold/project_lister.py` | 1 | ~250 | Offline/SSH project listing |
| `core/internal/scaffold/context_initializer.py` | 2 | ~200 | Context scaffold orchestration |
| `core/internal/scaffold/project_remover.py` | 3 | ~220 | Project removal orchestration |
| `core/internal/scaffold/scaffold_helpers.py` | 4a | ~300 | Shared gen-функции (extract) |
| `core/internal/scaffold/project_scaffolder.py` | 4b | ~450 | New-project full scaffold |
| `tests/test_project_lister.py` | 1 | ~200 | 6 unit-тестов |
| `tests/test_context_initializer.py` | 2 | ~180 | 5 unit-тестов |
| `tests/test_project_remover.py` | 3 | ~220 | 6 unit-тестов |
| `tests/test_scaffold_helpers.py` | 4a | ~200 | 4 shared-функции |
| `tests/test_project_scaffolder.py` | 4b | ~280 | 8 unit-тестов |

### MODIFY (8 файлов)

| Файл | Wave | Δ LOC | Назначение |
|------|------|-------|-----------|
| `core/internal/scaffold/project-list.sh` | 1 | 403→<30 | → фасад |
| `core/internal/scaffold/context-init.sh` | 2 | 364→<50 | → фасад |
| `core/internal/scaffold/remove-project.sh` | 3 | 423→<50 | → фасад |
| `core/internal/scaffold/add-project.sh` | 4b | 782→<50 | → фасад |
| `core/internal/scaffold/project_adopter.py` | 4a | -~200 | refactor: delegate to scaffold_helpers |
| `core/internal/scaffold/__init__.py` | 1-4 | +exports | Module exports |
| `tests/test_project_lifecycle.py` | 4 | extend | Расширение lifecycle-тестов |
| `core/entrypoints/scaffold.sh` | 1-4 | verify | Проверка dispatch (без изменений если фасады сохраняют CLI-контракт) |

### DELETE

- 9 inline python3 блоков (7 в project-list.sh, 2 в remove-project.sh — подтвердить grep)

### KEEP (без изменений)

- `core/internal/scaffold/adopt-project.sh` (89 LOC — уже фасад)
- `core/internal/scaffold/gen_env_platform.py` (вызывается из scaffolder)
- `core/internal/scaffold/vhost_renderer.py` (вне скоупа)
- `core/internal/scaffold/add-vhost.sh` (вызывается из scaffolder)
- `core/internal/scaffold/context_registry.py` (вызывается из initializer)
- `core/internal/shared/node_yaml.py` (DP-088 stable)

---

## §5. Testing Strategy

### LDD Compliance (testing.md §LDD)

Каждый тест содержит:
```python
found_log = False
print("--- LDD TRAJECTORY (IMP:7-10) ---")
for record in caplog.records:
    if "[IMP:" in record.message:
        imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
        if imp_level >= 7:
            print(record.message)
        if imp_level >= 9:
            found_log = True
print("--- END LDD TRAJECTORY ---")
assert found_log, "Critical LDD Error: No IMP:9 business logic log found"
```

### Test Honesty Rules (R1-R5)

- **R1:** Каждый тест имеет минимум 1 meaningful assertion (не `assert True`).
- **R2:** Запрещены unfalsifiable asserts (`assert isinstance(x, dict)`).
- **R3:** Нет `@pytest.mark.skip` (все тесты исполняемые).
- **R4:** Тесты без сервиса/SSH → mock через DI, не skip.
- **R5:** Для каждого gate-теста (bug-ID) — негативный тест (e.g. `test_unregister_removes_all_duplicates` для TRAP node_yaml.py:1186).

### DI over Mocks (testing.md)

- `ssh_read`/`ssh_exec` передаются как callable (default из lib/ssh.sh). В тестах — fake callable.
- `subprocess.run` для rsync/gh/git — обёрнуты в injectable runner. В тестах — capture args without execution.
- Path-аргументы везде явные (tmp_path), никаких hardcoded paths.

### Anti-Loop Protocol

`tests/conftest.py` session hook + `.test_counter.json`. На failure — CHECKLIST (tmp_path, caplog level, fixture content).

### Test Inventory Sync

После каждого Wave: `make test-inventory-sync` для регенерации `test_inventory.yaml`.

---

## §6. Verification Protocol

### Per-Wave (Coder → QA)

```bash
# Wave N завершение:
grep -rn "python3 -c\|python3 <<\|python3 - " core/internal/scaffold/<target>.sh  # → 0
wc -l core/internal/scaffold/<target>.sh                                          # → <50
make fix-gate
make gate MODE=fast
python -m pytest tests/test_<module>.py -s -v
make test-inventory-sync
make check-manifests  # drift detection: entrypoint-manifest, AGENTS.md canon_table
```

### Final Verification (все AC)

```bash
# AC1: functional equivalence
make new-project NAME=test-092 TEMPLATE=backend NODE=test-node
make project-list                                          # → показывает test-092
make project-status NAME=test-092                          # → status (если нода доступна)
make remove-project NAME=test-092                          # → проект удалён
# (context-init: make new-context NODE=test-092 --skip-gh-repo в tmp)

# AC2: facade LOC
wc -l core/internal/scaffold/{project-list,context-init,remove-project,add-project}.sh  # все <50

# AC3: 0 inline python3
grep -rn "python3 -c\|python3 <<\|python3 - " core/internal/scaffold/  # → 0

# AC4: unit-тесты
python -m pytest tests/test_project_lister.py tests/test_context_initializer.py \
  tests/test_project_remover.py tests/test_scaffold_helpers.py \
  tests/test_project_scaffolder.py -s -v

# AC5: project-status preserved
grep -n "status" core/internal/scaffold/project_lister.py  # → режим сохранён

# AC6: shared extraction
grep -n "from core.internal.scaffold.scaffold_helpers" core/internal/scaffold/project_adopter.py  # → import есть
grep -n "from core.internal.scaffold.scaffold_helpers" core/internal/scaffold/project_scaffolder.py

# AC7-8: gate + tests
make gate MODE=fast
python -m pytest tests/ -v

# AC9: manifests drift-free
make check-manifests  # → no drift
```

### Regression: существующие тесты

```bash
python -m pytest tests/test_project_lifecycle.py tests/test_adopt_project_org_validation.py \
  tests/test_project_ci_contract.py tests/test_project_schema.py -v
```

---

## §7. Risk Register

| ID | Risk | Severity | Mitigation | TRAP-link |
|----|------|----------|-----------|-----------|
| R1 | Wave 4 HIGH-сложность (16 функций) | HIGH | 4a refactor-first изолирует shared; dry-run покрывает пути; DI для subprocess | — |
| R2 | SSH side-effects (compose down) | MED | Mock ssh_exec через DI; инвариант NO `-v` покрыт негативным тестом | TRAP O7/DD10 |
| R3 | node_yaml.remove_project removes ALL dups | LOW | Документированное поведение; негативный тест | TRAP node_yaml.py:1186 |
| R4 | gh CLI / git external deps | LOW | Graceful degradation сохранена (warn, не fail) | L193-205 original |
| R5 | DRIFT: project_adopter refactor ломает adopter | MED | Wave 4a — отдельная верификация существующих adopter-тестов перед 4b | — |
| R6 | macOS `timeout` vs Linux `gtimeout` | LOW | TRAP DP-087 — использовать ssh_read timeout-параметр, не внешний timeout | TRAP lib/ssh.sh |
| R7 | context-init skeleton YAML semantic markup | LOW | Сохранить GREP_SUMMARY/STRUCTURE-комментарии в template | — |
| R8 | template-engine.sh subprocess contract | LOW | Поведение 1:1 (render-dir вызов сохранён) | Anti-Loop Note |

---

## §8. Sequencing & Dependencies

```
DP-091 Wave C (merge)  ──REQUIRED──→  DP-092 START
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
              Wave 1 (lister)      Wave 2 (context)      Wave 3 (remover)
              [LOW risk]            [MED risk]           [MED risk]
              independent           independent           independent
                    └─────────────────────┴─────────────────────┘
                                          │
                                          ▼
                              Wave 4a (scaffold_helpers extract)
                              [refactor-first, MED risk]
                              VERIFY: adopter tests green
                                          │
                                          ▼
                              Wave 4b (project_scaffolder)
                              [HIGH risk, last]
                                          │
                                          ▼
                              Final Verification (all AC)
```

- Waves 1-3 **независимы** между собой (можно параллелить через subagents).
- Wave 4a **блокирует** 4b (extract перед использованием).
- Wave 4b **последний** (HIGH risk, isolate).

---

## §9. Out of Scope

- **Оптимизация** scaffold-логики (Anti-Loop Note: migrate 1:1, optimize in debt). Н-р: auto_domain эвристика не переписывается.
- `vhost_renderer.py` (54885 bytes) — отдельный монолит, не в этом плане (вне brief scope).
- `gen_env_platform.py` (уже Python) — не мигрируется, вызывается как subprocess.
- `add-vhost.sh` (6059 bytes) — вызывается из scaffolder, не мигрируется здесь.
- Полное удаление shell-фасадов (entrypoints/scaffold.sh dispatch остаётся — он регистрирует subcommands).
- `adopt-project.sh` (89 LOC, уже фасад) — не трогается, только project_adopter.py refactor.

---

## §10. Anti-Loop Safeguards

Per Brief §Anti-Loop Note:
1. **Behaviour-preserving** — каждый Wave мигрирует логику 1:1. Никаких «улучшений» в рамках DP-092.
2. Если Coder находит баг в оригинальной shell-логике → НЕ чинить в этом плане. Открыть отдельный Debt-entry, мигрировать с багом, исправить в следующем плане.
3. Если refactor project_adopter (Wave 4a) выявляет drift между add-project и adopter → выбрать поведение project_adopter.py (новее, Python, тестируемое), задокументировать выбор в TRAP-комментарии.
4. Strangler-Fig discipline: shell-фасад остаётся как backward-compat layer. Удаление фасадов — будущий план (после smoke-тестов на production-ноде).

---

$END_DEVPLAN
