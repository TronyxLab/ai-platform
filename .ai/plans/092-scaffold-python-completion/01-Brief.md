$START_BRIEF
# Brief 092 — Scaffold Python Completion (1972 LOC)

## $ARTIFACT_CONTRACT
- **PURPOSE:** Мигрировать 4 scaffold-монолита shell→Python: add-project.sh (782), remove-project.sh (423), project-list.sh (403), context-init.sh (364). Это крупнейший не покрытый блок бизнес-логики (~1972 LOC shell).
- **DESCRIPTION:** Создать `core/internal/scaffold/` Python-модули: `project_scaffolder.py`, `project_remover.py`, `project_lister.py`, `context_initializer.py`. Shell-файлы превращаются в тонкие фасады (<50 LOC) или удаляются. Использует готовый NodeYaml mutation API (из 088).
- **RATIONALE:** Эти 4 скрипта содержат генерацию YAML, multi-branch case-логику, 9 inline python3 блоков (project-list: 7, remove-project: 2). Ни один DevPlan 070-090 их не покрывает. Без их миграции цель «переписать sh на Python» формально не достигнута.
- **ACCEPTANCE_CRITERIA:** Все 4 операции (`make new-project`, `make remove-project`, `make project-list`, `make context-init`) работают идентично; shell-фасады <50 LOC; 0 inline python3; unit-тесты на каждый Python-модуль.
- **IMPLEMENTS:** Закрытие gap «scaffold/ 1972 LOC без плана» (выявлено 3-мя аудитами).
- **IMPACTS:** `core/internal/scaffold/add-project.sh`, `remove-project.sh`, `project-list.sh`, `context-init.sh` → Python. Зависит от `core/internal/shared/node_yaml.py` (mutation API из 088).
- **REQUIRES:** **DevPlan 088 STABLE** (NodeYaml mutation API: add_project/remove_project/update_project). Блокируется 091 Wave C.

## Current Status (Audit 2026-07-30)
- **Coverage:** 0% — ни один из 4 файлов не имеет Python-двойника (частично: `gen_env_platform.py` заменяет часть add-project логики, `NodeYaml` CLI заменяет часть project-list).
- **Inline python3:** 9 блоков суммарно (project-list: 7 JSON-парсинг, remove-project: 2 field extraction).
- **Шаблоны:** `templates/template-{backend,frontend,fullstack}/` — payload для `make new-project`. Логика копирования шаблона сейчас в add-project.sh.

## Key Findings (verificated)
- `add-project.sh` (782 LOC) — генерирует `ai-platform.yaml`, `Makefile`, `AGENTS.md`, вызывает `template-engine.sh render-dir`. 11 функций. Самый сложный.
- `remove-project.sh` (423 LOC) — оркестрация: unregister + compose down (без -v) + cleanup. SSH + node search.
- `project-list.sh` (403 LOC) — 7 inline python3 для JSON. Весь JSON-парсинг уже есть в `NodeYaml` CLI — простейшая цель.
- `context-init.sh` (364 LOC) — multi-step scaffold контекста: регистрация org, генерация overlay.
- **Cross-link:** `make adopt-project` (из 036-wave5c) имеет общую логику с add-project — вынести в общий `project_scaffolder.py`.

## Required Actions

### Wave 1: project-lister (простейший, warm-up)
1. Создать `core/internal/scaffold/project_lister.py` — использует `NodeYaml.get_projects()`.
2. `project-list.sh` → фасад <30 LOC (dispatch на Python).
3. Удалить 7 inline python3 блоков.
4. Unit-тесты: offline-list, filter-by-context, empty-state.

### Wave 2: context-initializer
5. Создать `core/internal/scaffold/context_initializer.py` — использует `context_registry.py` (существует).
6. `context-init.sh` → фасад.
7. Unit-тесты: new-context, existing-context-idempotent, missing-org.

### Wave 3: project-remover
8. Создать `core/internal/scaffold/project_remover.py` — unregister + docker compose down + cleanup. Использует `NodeYaml.remove_project()`.
9. `remove-project.sh` → фасад. Удалить 2 inline python3.
10. Unit-тесты: remove-existing, remove-missing (idempotent), compose-down-error.

### Wave 4: project-scaffolder (самый сложный)
11. Создать `core/internal/scaffold/project_scaffolder.py` — копирование шаблона + генерация `ai-platform.yaml`/`Makefile`/`AGENTS.md` + регистрация через `NodeYaml.add_project()`.
12. Вынести общую логику с `adopt-project` в shared helper.
13. `add-project.sh` → фасад.
14. Unit-тесты: new-backend, new-frontend, new-fullstack, existing-project-conflict, template-missing.

## Verification
- `make new-project TYPE=backend NAME=test-proj` → проект создан корректно.
- `make project-list` → показывает test-proj.
- `make remove-project NAME=test-proj` → проект удалён.
- `make gate MODE=fast` зелёный.
- `grep -rn "python3 -c\|python3 <<" core/internal/scaffold/` → 0 совпадений.

## Anti-Loop Note
Не пытаться сделать «идеальный» scaffold engine. Мигрировать логику 1:1 (behaviour-preserving), оптимизация — в долг. Strangler-Fig: сначала перенос, потом улучшения.

$END_BRIEF
