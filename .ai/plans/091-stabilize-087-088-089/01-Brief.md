$START_BRIEF
# Brief 091 — Stabilize PARTIAL DevPlans 087 / 088 / 089

## $ARTIFACT_CONTRACT
- **PURPOSE:** Закрыть незакрытые AC планов 087 (Bootstrap Phase Consolidation, 65/100), 088 (NodeYaml Facade Completion, 79/100), 089 (Deploy Orchestrator Unification, 65/100) и разрулить их перекрёстную блокировку. После 091 все три плана = STABLE.
- **DESCRIPTION:** Три плана заблокировали друг друга: 088 gate заблокирован `orchestrator_cli.py` из 089; 087 dispatch не переключён на 14-фазный путь; 089 имеет dead code в `reconciler_projects.py`. Решаем в строгом порядке: 089 → 087 → 088 (dependency-driven).
- **RATIONALE:** Нельзя начинать новые миграции (092-094) поверх незакрытых фундаментальных планов. Каскадная блокировка 087↔088↔089 делает каждый отдельно нерешаемым.
- **ACCEPTANCE_CRITERIA:** Из DevPlan.md каждого плана (финальный VR = STABLE).
- **IMPLEMENTS:** Доработка DevPlans 087, 088, 089 (не новый план, а завершение существующих).
- **IMPACTS:** `core/internal/bootstrap/lifecycle/phases.py`, `core/internal/bootstrap/lifecycle/steps.py`, `core/internal/deploy/reconciler_projects.py`, `core/entrypoints/bootstrap.sh`, `core/internal/project_registry.py`.
- **REQUIRES:** Выполнение в порядке 089 → 087 → 088.

## User Constraint (CRITICAL)
Проект в **тестовой фазе (пет-проекты), всё можно ронять и пересоздавать** (AGENTS.md инвариант 9). Поэтому:
- ❌ **НЕ писать миграцию state.json** (`migrate_state_to_phases()` для старого 23-key формата).
- ❌ **НЕ писать backward-compat пути** — старый 23-step dispatch **удаляется полностью**, остаётся только новый 14-phase.
- ❌ **НЕ писать migration code для CI forced-command** (setup-node.sh уже переписан в 089).

## Current Status (Audit 2026-07-30)
- **087:** 🟡 PARTIAL 65/100 — инфраструктура 14 фаз построена, но `default dispatch (--mode init)` всё ещё вызывает старый 23-step path. `migrate_state_to_phases()` существует, но не вызывается (и не должен — см. User Constraint).
- **088:** 🟡 PARTIAL 79/100 — 6/8 MAJOR находок исправлены. Остаток: gate блокирован orchestrator_cli.py (это 089-зависимость), `project_registry.py` не мигрирован на mutation API NodeYaml.
- **089:** 🟡 PARTIAL 65/100 — фундамент готов (orchestrator.py 842 LOC, 57 тестов PASS). Остаток: AC10 (dry-run), AC13 (gate T17), AC14 (dead code в reconciler), DRIFT-MANIFEST (7 stale refs на deploy-project.sh).

## Key Findings (verificated)
- **087 BLOCKER:** `bootstrap.sh` default path → старый 23-step. Новый `phases.py` (1043 LOC) не подключён к dispatch. Нужно: переключить dispatch + **удалить** старые step_N_* функции + **удалить** `checkpoint_migration.py` + удалить `migrate_state_to_phases()` (не нужен — см. Constraint).
- **089 BLOCKER:** `reconciler_projects.py` L419 `deliver_payload()` + L553 `deploy_project()` — dead code, дубликаты DeployOrchestrator. `_ORCHESTRATOR_AVAILABLE = False` fallback — удалить.
- **088 BLOCKER:** `project_registry.py` использует прямой `yaml.safe_load` вместо `NodeYaml.add_project()/remove_project()/update_project()`.
- **089 DRIFT-MANIFEST:** 7 stale ссылок на `deploy-project.sh` в `core/entrypoint-manifest.yaml` — файл удалён, ссылки висят.

## Required Actions (in order)

### Wave A: 089 cleanup (разблокирует 088 gate)
1. Удалить `deliver_payload()` и `deploy_project()` из `reconciler_projects.py` (AC14).
2. Удалить `_ORCHESTRATOR_AVAILABLE` fallback — orchestrator теперь обязателен.
3. Очистить 7 stale refs на `deploy-project.sh` в `entrypoint-manifest.yaml` (запустить `make generate-manifests`).
4. AC13: создать gate test T17 (3-слойный: Python unit + shell facade + SSH stub).
5. AC10: dry-run на тестовой ноде — описать как manual verification в VR (или авто-тест если возможно).

### Wave B: 087 dispatch switch
6. В `bootstrap.sh` / dispatch: переключить `--mode init` на новый 14-phase path (`phases.py`).
7. **Удалить** старые `step_N_*` функции из `steps.py` (которые заменены 14 фазами).
8. **Удалить** `checkpoint_migration.py` (bridge больше не нужен).
9. **Удалить** `migrate_state_to_phases()` и любой код совместимости со старым state.json.
10. AC: dry-run `make bootstrap-node --mode init` на чистой ноде — все 14 фаз проходят.

### Wave C: 088 project_registry migration
11. `project_registry.py`: заменить прямой `yaml.safe_load` + dict-mutation на `NodeYaml.add_project()/remove_project()/update_project()`.
12. Убедиться, что gate разблокирован (orchestrator_cli.py больше не блокирует после Wave A).
13. Финальный VR 088 → STABLE.

## Verification
- `make gate MODE=fast` — зелёный (включая check-manifests, который после 090 re-enabled).
- Финальные VR для 087, 088, 089 → все STABLE.
- Smoke: `make bootstrap-node --mode init` на пересоздаваемой тестовой ноде (без migration — чистый cold start).

## Anti-Loop Note
Этот план — **завершение**, а не новая миграция. Не расширять scope. Если всплывает новая находка — записать в `.ai/debt/091-residual-Debt.md`, не чинить в рамках 091.

$END_BRIEF
