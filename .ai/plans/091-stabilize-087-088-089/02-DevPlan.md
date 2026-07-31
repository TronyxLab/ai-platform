$START_DEVPLAN

# DevPlan 091 — Stabilize PARTIAL DevPlans 087 / 088 / 089

$ARTIFACT_CONTRACT
PURPOSE:               Закрыть незакрытые AC планов 087 (Bootstrap Phase Consolidation), 088 (NodeYaml Facade Completion), 089 (Deploy Orchestrator Unification) и разрулить их перекрёстную блокировку. После 091 все три плана = STABLE.
DESCRIPTION:           Завершение (не новая миграция) трёх фундаментальных планов в строго dependency-driven порядке: Wave A (089 cleanup) → Wave B (087 backward-compat removal) → Wave C (088 project_registry migration). Каждая Wave — минимальный targeted fix с верификацией перед следующей.
RATIONALE:             Каскадная блокировка 087↔088↔089 делает каждый план отдельно нерешаемым: 088 gate заблокирован 089-зависимостью (orchestrator_cli.py), 087 не может удалить backward-compat пока 089 держит fallback-паттерн `_ORCHESTRATOR_AVAILABLE`, 088 project_registry блокирует AC2. Решаем в порядке зависимостей. User Constraint (тестовая фаза): backward-compat удаляется полностью, миграция state.json НЕ пишется — чистый cold start.
ACCEPTANCE_CRITERIA:   §5 — все 14 AC (5 общих + 4+3+2 per-Wave). Финальные VR 087/088/089 → все STABLE. `make gate MODE=fast` зелёный.
IMPLEMENTS:            Brief 091 (.ai/plans/091-stabilize-087-088-089/01-Brief.md). Завершение DevPlans 087, 088, 089 (не новый план).
IMPACTS:               core/internal/bootstrap/deploy/context_deployer.py, core/internal/deploy/orchestrator.py, core/internal/deploy/orchestrator_cli.py, core/internal/deploy/reconciler_projects.py, core/internal/bootstrap/lifecycle/state_machine.py, core/internal/bootstrap/lifecycle/state_migration.py, core/lib/checkpoint.sh, core/internal/shared/project_registry.py, core/internal/shared/node_yaml.py, makefiles/deploy.mk, core/entrypoint-manifest.yaml, core/internal/bootstrap/AGENTS.md, tests/.
REQUIRES:              Выполнение в порядке Wave A → Wave B → Wave C. AGENTS.md инвариант 9 (тестовая фаза, можно ронять). `make generate-manifests` + `make check-manifests` доступны (план 090 re-enabled).
$END_ARTIFACT_CONTRACT

---

## §0. Audit 2026-07-30 — фактическое состояние vs Brief

Бриф 091 написан на основе финальных VR (087-02, 088-03, 089-03). Фактический аудит кода на старте 091 показал, что **часть работы уже выполнена** между VR и сейчас. DevPlan отражает РЕАЛЬНЫЙ остаток.

### Что УЖЕ сделано (НЕ дублировать)

| План | Найденный в брифе остаток | Фактический статус |
|------|---------------------------|--------------------|
| 087 | Dispatch не переключён на 14-фазный path | ✅ DONE — `_run_init_mode()`/`_run_update_mode()` итерируют `BootstrapPhase.INIT_PHASE_ORDER`/`UPDATE_PHASE_ORDER` |
| 087 | 30 elif-веток `_execute_init_step`/`_execute_update_step` | ✅ DONE — удалены (`rg "elif step_name ==" state_machine.py` = пусто) |
| 087 | Старые `_step_N_*` функции в steps.py | ✅ DONE — удалены |
| 087 | `setup_state()` пишет 23 старых ключа | ✅ DONE — использует `BootstrapPhase` enum |
| 087 | `dry_run_plan()` показывает старые steps | ✅ DONE — показывает фазы |
| 087 | `checkpoint_migration.py` существует | ✅ DONE — удалён |
| 089 | `deliver_payload()`/`deploy_project()` в reconciler_projects.py (AC14) | ✅ DONE — удалены |
| 089 | `audit_logging.sh` references в core/internal/deploy/ (AC7) | ✅ DONE — 0 совпадений |
| 089 | `orchestrator_cli.py` GREP_SUMMARY (блокировал 088 gate) | ✅ DONE — GREP_SUMMARY + STRUCTURE на L2-3 |
| 088 | `overlay_deliverer.py` broad except (STA-2) | ✅ DONE — 0 совпадений |

### Что ОСТАЛОСЬ (scope 091)

| Wave | План | Задача | Severity |
|------|------|--------|----------|
| A | 089 | `context_deployer.py` — собственный deploy path `_deploy_single_project()` (L463-552) bypasses orchestrator | CRITICAL (AC4) |
| A | 089 | `context_deployer.py` — `_ORCHESTRATOR_AVAILABLE=False` fallback (L43-50) | CRITICAL |
| A | 089 | `reconciler_projects.py` — `_ORCHESTRATOR_AVAILABLE=True` vestigial flag (L43) | LOW |
| A | 089 | `makefiles/deploy.mk:54,71` — вызовы удалённого `deploy-project.sh` | HIGH |
| A | 089 | AC10 — dry-run в orchestrator.py + orchestrator_cli.py | MEDIUM |
| A | 089 | AC13/T17 — unit-тест для orchestrator path (gate test существует, unit отсутствует) | MEDIUM |
| B | 087 | `state_migration.py` целиком (198 LOC) — backward-compat migration | HIGH (User Constraint) |
| B | 087 | Migration-блок в `main()` (L1354-1371) | HIGH |
| B | 087 | `INIT_STEPS`/`UPDATE_STEPS`/`*_COUNT` dead constants (L234-275) | HIGH |
| B | 087 | `from_dict(step_list=...)` backward-compat fallback (L574-581) | HIGH |
| B | 087 | `checkpoint.sh` — deprecated shell facade, поддержка двух форматов | MEDIUM (анализ) |
| B | 087 | `test_phase_key_misalignment_prevented` Scenario B (L965-986) — backward-compat test | MEDIUM |
| B | 087 | `core/internal/bootstrap/AGENTS.md` — doc bug (L15: "14 INIT фаз" → "9 INIT фаз") + упоминания state_migration | LOW |
| C | 088 | `project_registry.py` — 3 функции на yaml.safe_load (register/deregister/list, L99/163/220) | HIGH (AC2/DRIFT-088-7) |

### Вне scope 091 → Debt (Anti-Loop Note брифа)

- `vhost_renderer.py:161,233` — yaml.safe_load для node.yaml (не в File Manifest 088)
- `project_adapter.py:623,634,898` — yaml.safe_load для node.yaml (не в File Manifest 088)
- `tests/unit/test_deploy_single_orchestrator.py` — опциональный unit-test (gate test T17 покрывает 3 слоя)
- `PROJECTS_BASE`/`PLATFORM_DEPLOY_TIMEOUT` не в .env.example (LOW)

Эти находки → `.ai/debt/091-residual-Debt.md`, НЕ чинятся в рамках 091.

---

## §1. Draft Code Graph (XML)

```xml
<graph version="091-stabilize">
  <!-- Wave A: 089 cleanup -->
  <entity id="context_deployer_py" type="FILE"
    keywords="deploy-context, parallel-deploy, bypass-orchestrator"
    annotation="CRITICAL: _deploy_single_project() L463-552 — full parallel deploy (pull→build→up→healthcheck), bypasses DeployOrchestrator. AC4 violation."/>
  <entity id="context_deployer_FUNC_deploy_context_projects" type="METHOD"
    keywords="dispatch, _ORCHESTRATOR_AVAILABLE"
    annotation="L358-395: переключается по vestigial flag. После Wave A — прямой вызов orchestrator."/>
  <entity id="reconciler_projects_py" type="FILE"
    keywords="reconciler, deploy-via-orchestrator, vestigial-flag"
    annotation="L43: _ORCHESTRATOR_AVAILABLE=True (always True). L265: deploy_via_orchestrator() sole path."/>
  <entity id="makefiles_deploy_mk" type="FILE"
    keywords="makefile, deploy-project-sh, stale-ref"
    annotation="L54,71: вызывают удалённый core/entrypoints/deploy-project.sh. STALE."/>
  <entity id="orchestrator_py" type="FILE"
    keywords="deployorchestrator, dry-run-missing"
    annotation="AC10: нет --dry-run в deploy()."/>
  <entity id="orchestrator_cli_py" type="FILE"
    keywords="cli, dry-run-flag-missing"
    annotation="AC10: нет --dry-run CLI флага. GREP_SUMMARY присутствует (L2)."/>

  <!-- Wave B: 087 backward-compat removal -->
  <entity id="state_migration_py" type="FILE"
    keywords="migrate-state, 23-to-14, backward-compat, DELETE"
    annotation="198 LOC. User Constraint: УДАЛИТЬ целиком. n() — backward-compat migration."/>
  <entity id="state_machine_FUNC_main" type="METHOD"
    keywords="migration-block, delete"
    annotation="L1354-1371: блок one-shot миграции state.json. User Constraint: удалить."/>
  <entity id="state_machine_CONST_INIT_STEPS" type="CONST"
    keywords="dead-constant, 23-steps, delete"
    annotation="L234-263: INIT_STEPS list (23 items). L234: INIT_STEP_COUNT=23. Используется только L574 (backward-compat load)."/>
  <entity id="state_machine_CONST_UPDATE_STEPS" type="CONST"
    keywords="dead-constant, 9-steps, delete"
    annotation="L265-275: UPDATE_STEPS list. L235: UPDATE_STEP_COUNT=8."/>
  <entity id="state_machine_INIT_L574" type="ANCHOR"
    keywords="from_dict-fallback, backward-compat-load"
    annotation="L574-581: step_list=INIT_STEPS if... + phase key fallback. Упростить: загружать state.json напрямую без old-step fallback."/>
  <entity id="checkpoint_sh" type="FILE"
    keywords="deprecated-shell-facade, dual-format"
    annotation="203 LOC. data['steps'] old-format + phase-key fallback. @deprecated DevPlan 087. Wave B: анализ потребителей → удалить или сократить."/>
  <entity id="test_state_machine_py" type="FILE"
    keywords="scenario-b, backward-compat-test, delete-scenario"
    annotation="test_phase_key_misalignment_prevented L965-986: Scenario B тестирует numeric-key migration. Удалить Scenario B, оставить Scenario A."/>
  <entity id="bootstrap_AGENTS_md" type="FILE"
    keywords="doc-bug, 14-init-phases, 9-init-phases"
    annotation="L15: '14 INIT фаз' → '9 INIT фаз'. Обновить упоминания state_migration после удаления."/>
  <entity id="bootstrap_lifecycle_phases_py" type="FILE"
    keywords="phase-implementations, 14-phases, no-change"
    annotation="1043 LOC, 14 phase_*() функций. Не изменяется в 091 (уже DONE)."/>

  <!-- Wave C: 088 project_registry migration -->
  <entity id="project_registry_py" type="FILE"
    keywords="yaml-safe-load, register, deregister, list, migrate-to-nodeyaml"
    annotation="L99/163/220: 3 yaml.safe_load для node.yaml. Функции: register_project/deregister_project/list_projects/validate_project_name. Semantics: soft-idempotency (skip), tuple[bool,str] return, sys.exit in CLI."/>
  <entity id="node_yaml_FUNC_add_project" type="METHOD"
    keywords="mutation-api, hard-error, projectentry"
    annotation="L1141: add_project(project: ProjectEntry) -> None. RAISES ConfigValidationError on duplicate (hard error vs register's soft skip)."/>
  <entity id="node_yaml_FUNC_remove_project" type="METHOD"
    keywords="mutation-api, bool-return"
    annotation="L1194: remove_project(name: str) -> bool. True=removed, False=not found."/>
  <entity id="node_yaml_FUNC_get_projects" type="METHOD"
    keywords="read-api, list-dict"
    annotation="L551: get_projects() -> list[dict]. RAISES ConfigValidationError if not list."/>
  <entity id="node_yaml_CLASS_ProjectEntry" type="CLASS"
    keywords="dataclass, project-fields"
    annotation="L204-224: name, repo, type, domain, database, context."/>
  <entity id="project_adopter_py" type="FILE"
    keywords="consumer, register-project-import"
    annotation="L735: from core.internal.shared.project_registry import register_project. L778: NodeYaml CLI fallback уже есть. Wave C: обновить импорт после миграции."/>
  <entity id="test_project_registry_py" type="FILE"
    keywords="unit-tests, register, deregister, list"
    annotation="Тесты через subprocess CLI. Wave C: обновить под NodeYaml-backed реализацию."/>

  <!-- CrossLinks -->
  <link from="context_deployer_py" to="orchestrator_py" rel="MUST-DELEGATE"/>
  <link from="makefiles_deploy_mk" to="orchestrator_cli_py" rel="MUST-CALL-DEPLOY-MANY"/>
  <link from="state_machine_FUNC_main" to="state_migration_py" rel="DELETE-CALL"/>
  <link from="state_machine_INIT_L574" to="state_machine_CONST_INIT_STEPS" rel="DELETE-FALLBACK"/>
  <link from="project_registry_py" to="node_yaml_FUNC_add_project" rel="MIGRATE-TO"/>
  <link from="project_adopter_py" to="project_registry_py" rel="CONSUMER-UPDATE"/>
</graph>
```

---

## §2. Step-by-Step Data Flow

### Wave A: 089 cleanup (разблокирует 088 gate, закрывает AC4/AC10/AC13)

```
A1. context_deployer.py:
    DELETE _deploy_single_project() [L463-552] (90 LOC parallel deploy)
    DELETE _ORCHESTRATOR_AVAILABLE block [L43-50]
    MODIFY deploy_context_projects() [L358-395] → прямой вызов _deploy_single_project_via_orchestrator() [L255-343]
    → verify: rg "_ORCHESTRATOR_AVAILABLE|_deploy_single_project\b" context_deployer.py = пусто

A2. reconciler_projects.py:
    DELETE _ORCHESTRATOR_AVAILABLE flag [L43]
    DELETE `if not _ORCHESTRATOR_AVAILABLE:` guard [~L277]
    → verify: rg "_ORCHESTRATOR_AVAILABLE" reconciler_projects.py = пусто

A3. makefiles/deploy.mk:
    MODIFY L54: bash deploy-project.sh → python3 -m core.internal.deploy.orchestrator_cli deploy-many (SCPChannel)
    MODIFY L71 (target deploy-project:): @...deploy-project.sh → @orchestrator_cli deploy-many
    → verify: rg "deploy-project\.sh" makefiles/ = пусто

A4. entrypoint-manifest.yaml:
    RUN make generate-entrypoint-manifest (regenerate from Makefile .PHONY)
    → verify: rg "deploy-project\.sh" core/entrypoint-manifest.yaml = 0 STALE (L595 историческое описание OK)

A5. orchestrator.py + orchestrator_cli.py (AC10 dry-run):
    ADD DeployOrchestrator.deploy(dry_run: bool = False) — на этапе планирования выводит действия без выполнения
    ADD orchestrator_cli.py --dry-run флаг → deploy/deploy-many subcommands
    → verify: rg "dry.run|dry_run" orchestrator.py orchestrator_cli.py > 0

A6. tests/unit/test_deploy_single_orchestrator.py (AC13 — unit layer):
    CREATE unit-тест (gate test T17 = integration layer, уже существует)
    Проверки: deploy_via_orchestrator() вызывается, fallback-флаг отсутствует
    → verify: pytest tests/unit/test_deploy_single_orchestrator.py PASS

A7. Wave A verification gate:
    make fix-gate && make gate MODE=fast → зелёный
    pytest tests/unit/ tests/integration/ -v → зелёный
    Финальный VR 089 → STABLE
```

### Wave B: 087 backward-compat removal (закрывает User Constraint violations)

```
B1. DELETE core/internal/bootstrap/lifecycle/state_migration.py (198 LOC целиком)
    → verify: ls state_migration.py = No such file

B2. state_machine.py main() [L1354-1371]:
    DELETE migration-блок (has_old_keys/has_new_keys check + migrate_state_to_phases call)
    → verify: rg "state_migration|migrate_state_to_phases|has_old_keys|has_new_keys" state_machine.py = пусто

B3. state_machine.py __init__ [L574-581]:
    УПРОСТИТЬ загрузку state.json — убрать `step_list = INIT_STEPS if...` fallback
    BootstrapState.from_dict() загружает только phase-ключи
    → verify: rg "INIT_STEPS|UPDATE_STEPS" state_machine.py = 0 (только в комментариях-истории OK)

B4. state_machine.py [L234-275]:
    DELETE INIT_STEP_COUNT, UPDATE_STEP_COUNT, INIT_STEPS, UPDATE_STEPS (dead constants)
    → verify: rg "INIT_STEPS|UPDATE_STEPS|INIT_STEP_COUNT|UPDATE_STEP_COUNT" state_machine.py = пусто

B5. tests/unit/test_state_machine.py test_phase_key_misalignment_prevented [L965-986]:
    DELETE Scenario B (numeric-key backward-compat migration test)
    Оставить Scenario A (phase-based key alignment)
    → verify: rg "numeric|INIT_STEPS still exists|backward.compat" test_state_machine.py = пусто в этом тесте

B6. core/lib/checkpoint.sh (203 LOC):
    АНАЛИЗ: rg "checkpoint_step|checkpoint_done|checkpoint_mark" core/ --type sh
    ЕСЛИ 0 активных потребителей → DELETE файл целиком
    ЕСЛИ есть → СОКРАТИТЬ до тонкой обёртки над state_machine.py (phase-key only, убрать old-step support)
    → verify: решение зафиксировано в VR 087

B7. core/internal/bootstrap/AGENTS.md:
    FIX L15: "14 INIT фаз" → "9 INIT фаз"
    REMOVE упоминания state_migration.py / migrate_state_to_phases() (инвариант 4, секция "Миграция")
    → verify: rg "14 INIT фаз" AGENTS.md = пусто; rg "state_migration" AGENTS.md = пусто

B8. Wave B verification gate:
    make fix-gate && make gate MODE=fast → зелёный
    pytest tests/unit/test_state_machine.py tests/integration/test_bootstrap_dry_run.py -v → зелёный
    Smoke: make bootstrap-node --mode init на чистой тестовой ноде → 14 фаз проходят (cold start, без migration)
    Финальный VR 087 → STABLE
```

### Wave C: 088 project_registry migration (закрывает AC2/DRIFT-088-7)

```
C1. Семантический мост soft↔hard idempotency:
    NodeYaml.add_project() RAISES ConfigValidationError на дубликат (hard error)
    project_registry.register_project() возвращает (True, "Idempotent SKIP") (soft skip)
    РЕШЕНИЕ: register_project() становится ТОНКОЙ ОБЁРТКОЙ над NodeYaml:
        try:
            ny.add_project(ProjectEntry(...))
            return (True, "Registered")
        except ConfigValidationError as e:
            if "duplicate" in str(e).lower(): return (True, "Idempotent SKIP — already exists")
            return (False, str(e))
    Сигнатура register_project() НЕ меняется (consumer project_adopter.py не трогается)

C2. project_registry.py register_project() [L74-125]:
    REPLACE yaml.safe_load [L99] → NodeYaml(node_yaml_path)
    REPLACE dict-mutation + yaml.dump [L121] → ny.add_project(ProjectEntry(name, repo, type, domain, database))
    PRESERVE tuple[bool,str] return + log_prefix + [IMP:9] logging
    → verify: rg "yaml.safe_load|yaml.dump" project_registry.py в register = пусто

C3. project_registry.py deregister_project() [L144-179]:
    REPLACE yaml.safe_load [L163] → NodeYaml(node_yaml_path)
    REPLACE list-comprehension + yaml.dump [L175] → ny.remove_project(name)
    PRESERVE tuple[bool,str] return (remove_project возвращает bool → обернуть в tuple)
    → verify: rg "yaml.safe_load|yaml.dump" project_registry.py в deregister = пусто

C4. project_registry.py list_projects() [L201-236]:
    REPLACE yaml.safe_load [L220] → ny.get_projects()
    PRESERVE stdout output format (name repo type domain, shell consumer) + tuple[bool,str] return
    → verify: rg "yaml.safe_load" project_registry.py = пусто

C5. validate_project_name() [L43]: БЕЗ ИЗМЕНЕНИЙ (не читает node.yaml)

C6. tests/unit/test_project_registry.py:
    UPDATE: тесты через subprocess CLI продолжают работать (CLI __main__ сохранён)
    ADD: assertion что NodeYaml mutation вызывается (опционально, через caplog IMP:9)
    → verify: pytest tests/unit/test_project_registry.py -v PASS

C7. Wave C verification gate:
    rg "yaml.safe_load" project_registry.py = пусто (AC2 для этого файла)
    make fix-gate && make gate MODE=fast → зелёный (088 gate разблокирован после Wave A)
    pytest tests/unit/test_node_yaml*.py tests/unit/test_project_registry.py -v → зелёный
    Финальный VR 088 → STABLE
```

---

## §3. Architecture & Decisions

### D1: Wave ordering — 089 → 087 → 088 (dependency-driven)

**Decision:** Строгий порядок волн, каждая верифицируется перед следующей.

**Rationale:**
- Wave A (089) разблокирует 088 gate (orchestrator_cli.py GREP_SUMMARY уже DONE, но manifest cleanup нужен для `check-manifests`)
- Wave B (087) удаляет `_ORCHESTRATOR_AVAILABLE`-подобный паттерн backward-compat — тот же антипаттерн что в 089, решаем подряд для консистентности
- Wave C (088) зависит от зелёного gate (после A) и от устранения fallback-паттерна (после B)

**Rejected:** Параллельные волны (риск: конфликт в общих тестах/gate).

### D2: project_registry.py — обёртка, не миграция consumers

**Decision:** `register_project()`/`deregister_project()`/`list_projects()` остаются как ТОНКИЕ ОБЁРТКИ над NodeYaml API. Сигнатуры НЕ меняются.

**Rationale:**
- Consumer `project_adopter.py:735` импортирует `register_project` напрямую — смена сигнатуры = каскадный рефакторинг (нарушает Anti-Loop Note: "не расширять scope")
- Shell CLI `__main__` сохраняет exit-code контракт для shell wrappers
- Soft-idempotency (skip, не error) — валидное бизнес-поведение для idempotent registration; bridge в обёртке сохраняет его поверх hard-error NodeYaml

**Rejected:**
- (a) Удалить project_registry.py, мигрировать consumers на NodeYaml напрямую — нарушение scope, каскад
- (b) Изменить NodeYaml.add_project() на soft-idempotency — ломает 62 существующих теста, нарушает инкапсуляцию фасада

### D3: state_migration.py — DELETE целиком (User Constraint)

**Decision:** Файл `state_migration.py` (198 LOC) удаляется полностью. Migration-блок в main() удаляется. Никакой backward-compat.

**Rationale:** Brief 091 User Constraint (CRITICAL): тестовая фаза, можно ронять. НЕ писать миграцию state.json. Старый 23-step dispatch удаляется полностью. state.json создаётся с нуля при cold start bootstrap.

**Rejected:** Сохранить migration как deprecated path (нарушает явный User Constraint).

### D4: checkpoint.sh — анализ перед действием (B6)

**Decision:** B6 — это АНАЛИЗ, не предзапланированный DELETE. Сначала `rg "checkpoint_step|checkpoint_done|checkpoint_mark" core/`, затем решение.

**Rationale:** checkpoint.sh может ещё вызываться из shell-скриптов bootstrap. Слепое удаление = regression. Решение фиксируется в VR 087 на основе факта вызовов.

### D5: context_deployer.py — found in audit, not in original brief

**Decision:** Включить `_deploy_single_project()` cleanup (L463-552) в Wave A, хотя бриф явно не упоминал его.

**Rationale:** Это прямое нарушение AC4 DevPlan 089 ("context_deployer → делегирует DeployOrchestrator, не свою deploy-логику"). 90 LOC параллельного deploy path с собственным `docker compose up` (L531) — ровно тот класс багов, который DeployOrchestrator был создан устранить. Исключение из scope 091 оставило бы 089 навсегда PARTIAL.

**Risk mitigation:** Аудит подтвердил, что `_deploy_single_project_via_orchestrator()` (L255-343) уже реализован и функционален — переключение на него не новая функциональность, а удаление bypass.

---

## §4. File Manifest

### Wave A: 089 cleanup

| Action | File | LOC Δ | AC |
|--------|------|-------|----|
| MODIFY | `core/internal/bootstrap/deploy/context_deployer.py` | −~100 (delete _deploy_single_project + flag) | AC4 |
| MODIFY | `core/internal/deploy/reconciler_projects.py` | −~5 (delete vestigial flag) | AC14 |
| MODIFY | `makefiles/deploy.mk` | ~4 (deploy-project.sh → orchestrator_cli) | DRIFT-MANIFEST |
| MODIFY (generated) | `core/entrypoint-manifest.yaml` | auto (make generate-entrypoint-manifest) | Invariant 11 |
| MODIFY | `core/internal/deploy/orchestrator.py` | +~30 (dry_run param) | AC10 |
| MODIFY | `core/internal/deploy/orchestrator_cli.py` | +~10 (--dry-run flag) | AC10 |
| CREATE | `tests/unit/test_deploy_single_orchestrator.py` | +~80 | AC13 |

### Wave B: 087 backward-compat removal

| Action | File | LOC Δ | AC |
|--------|------|-------|----|
| DELETE | `core/internal/bootstrap/lifecycle/state_migration.py` | −198 | User Constraint |
| MODIFY | `core/internal/bootstrap/lifecycle/state_machine.py` | −~60 (migration block L1354-1371 + INIT_STEPS L234-275 + from_dict L574-581) | AC8 |
| MODIFY | `tests/unit/test_state_machine.py` | −~22 (Scenario B L965-986) | Test honesty |
| ANALYZE→(DELETE or MODIFY) | `core/lib/checkpoint.sh` | TBD (B6 analysis) | DRIFT-CHECKPOINT-004 |
| MODIFY | `core/internal/bootstrap/AGENTS.md` | ~3 (doc fix L15 + remove state_migration refs) | DRIFT-AGENTS-005 |

### Wave C: 088 project_registry migration

| Action | File | LOC Δ | AC |
|--------|------|-------|----|
| MODIFY | `core/internal/shared/project_registry.py` | ~0 net (yaml.safe_load → NodeYaml calls) | AC2/DRIFT-088-7 |
| MODIFY | `tests/unit/test_project_registry.py` | +~20 (NodeYaml-backed assertions) | Test coverage |

### Generated artifacts (VR — after each Wave)

| Action | File |
|--------|------|
| CREATE | `.ai/plans/089-deploy-orchestrator-unification/04-VerificationReport.md` |
| CREATE | `.ai/plans/087-bootstrap-phase-consolidation/03-VerificationReport.md` |
| CREATE | `.ai/plans/088-node-yaml-facade-completion/04-VerificationReport.md` |
| CREATE | `.ai/debt/091-residual-Debt.md` (findings outside scope) |

---

## §5. Acceptance Criteria

### Общие (5 AC — все волны)

| AC | Описание | Метод верификации |
|----|----------|-------------------|
| AC-G1 | `make gate MODE=fast` — зелёный после всех трёх волн | Выполнить gate, проверить exit 0 |
| AC-G2 | `make check-manifests` — зелёный (Invariant 11) | `make check-manifests` exit 0 |
| AC-G3 | Все существующие тесты PASS (нет regressions) | `pytest tests/ -v` — 0 failed |
| AC-G4 | Финальные VR 087, 088, 089 → все STABLE | VR verdict = STABLE в каждом |
| AC-G5 | Debt registry создан для out-of-scope находок | `.ai/debt/091-residual-Debt.md` существует |

### Wave A: 089 (4 AC)

| AC | Описание | Метод верификации |
|----|----------|-------------------|
| AC-A1 | `rg "_deploy_single_project\b|_ORCHESTRATOR_AVAILABLE" core/internal/bootstrap/deploy/context_deployer.py` = пусто | grep |
| AC-A2 | `rg "deploy-project\.sh" makefiles/ core/entrypoint-manifest.yaml` = 0 STALE refs | grep + make generate-entrypoint-manifest |
| AC-A3 | `rg "dry.run\|dry_run" core/internal/deploy/orchestrator.py core/internal/deploy/orchestrator_cli.py` > 0 | grep (AC10) |
| AC-A4 | `pytest tests/unit/test_deploy_single_orchestrator.py tests/gates/test_gate_single_orchestrator.py -v` PASS | pytest (AC13) |

### Wave B: 087 (3 AC)

| AC | Описание | Метод верификации |
|----|----------|-------------------|
| AC-B1 | `ls core/internal/bootstrap/lifecycle/state_migration.py` = No such file + `rg "state_migration\|migrate_state_to_phases\|has_old_keys" state_machine.py` = пусто | ls + grep |
| AC-B2 | `rg "INIT_STEPS\|UPDATE_STEPS\|INIT_STEP_COUNT\|UPDATE_STEP_COUNT" core/internal/bootstrap/lifecycle/state_machine.py` = пусто | grep |
| AC-B3 | Smoke: `make bootstrap-node --mode init` на чистой тестовой ноде → 9 INIT фаз проходят, state.json содержит 9 phase-ключей (cold start, без migration) | manual dry-run / fresh node |

### Wave C: 088 (2 AC)

| AC | Описание | Метод верификации |
|----|----------|-------------------|
| AC-C1 | `rg "yaml.safe_load\|yaml.dump" core/internal/shared/project_registry.py` = пусто (3 функции мигрированы на NodeYaml) | grep |
| AC-C2 | `pytest tests/unit/test_project_registry.py tests/unit/test_node_yaml*.py -v` PASS + `make gate MODE=fast` зелёный (088 gate разблокирован) | pytest + gate |

---

## §6. Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| Удаление `_deploy_single_project()` в context_deployer ломает deploy-context на production-ноде | HIGH | AC-B3/A-verify: deploy-context работает через `_deploy_single_project_via_orchestrator()` (уже реализован L255-343). Smoke-test на тестовой ноде перед STABLE. |
| `checkpoint.sh` удаление ломает shell-скрипты, всё ещё его вызывающие | MEDIUM | B6 = АНАЛИЗ ПЕРЕД действием. `rg "checkpoint_step\|checkpoint_done" core/` определяет потребителей. Если есть → сокращение, не удаление. |
| NodeYaml.add_project hard-error ломает idempotent registration в project_adopter | MEDIUM | D2: обёртка register_project() перехватывает ConfigValidationError на duplicate → soft skip. Сигнатура consumer не меняется. |
| `from_dict(step_list=...)` упрощение ломает загрузку существующего state.json на ноде | LOW | User Constraint: тестовая фаза, нода пересоздаётся. Cold start не требует загрузки старого state.json. |
| `make generate-entrypoint-manifest` создаёт unexpected drift в других секциях | LOW | `make check-manifests` после генерации. Diff-review перед commit. |

---

## §7. Anti-Loop Guard

**Scope lock:** Этот план — завершение 087/088/089. НЕ расширять.

- Если в ходе Wave A/B/C всплывает находка, не указанная в §0 "Что ОСТАЛОСЬ" → записать в `.ai/debt/091-residual-Debt.md`, НЕ чинить.
- Если Wave не проходит verification gate → STOP, диагностика, не переход к следующей Wave.
- Запрещено: новая миграция, новый module, новый gate test (кроме AC-A4 unit-test), refactor вне File Manifest.

**Already-detected out-of-scope (→ Debt):**
- vhost_renderer.py node.yaml consumers
- project_adapter.py node.yaml consumers
- PROJECTS_BASE / PLATFORM_DEPLOY_TIMEOUT в .env.example
- STA-1: TRAP[BUG] coverage в NodeYaml mutation methods

---

## §8. Execution Order (delegation hints)

| Step | Owner | Wave | Task |
|------|-------|------|------|
| A1-A2 | Coder | A | context_deployer.py + reconciler_projects.py cleanup |
| A3-A4 | Coder+Sysadmin | A | makefiles/deploy.mk + manifest regenerate |
| A5 | Coder | A | orchestrator.py + orchestrator_cli.py dry-run (AC10) |
| A6 | Coder | A | test_deploy_single_orchestrator.py unit (AC13) |
| A7 | QA | A | Wave A gate + VR 089 → STABLE |
| B1-B5 | Coder | B | state_migration.py DELETE + state_machine.py + test cleanup |
| B6 | Coder | B | checkpoint.sh анализ (decision → VR) |
| B7 | Coder | B | AGENTS.md doc fix |
| B8 | QA | B | Wave B gate + smoke + VR 087 → STABLE |
| C1-C5 | Coder | C | project_registry.py → NodeYaml bridge |
| C6 | Coder | C | test_project_registry.py update |
| C7 | QA | C | Wave C gate + VR 088 → STABLE |
| Final | Sysadmin | — | Debt registry creation (AC-G5) |

---

## §9. Success Definition

091 = SUCCESS когда:
1. Три финальных VR (087-03, 088-04, 089-04) имеют verdict **STABLE**
2. `make gate MODE=fast` зелёный (AC-G1)
3. `make check-manifests` зелёный (AC-G2, Invariant 11)
4. 0 regressions в существующих тестах (AC-G3)
5. `.ai/debt/091-residual-Debt.md` создан с out-of-scope находками (AC-G5)

$END_DEVPLAN
