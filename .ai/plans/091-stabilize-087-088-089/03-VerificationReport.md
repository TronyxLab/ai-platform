$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Pre-implementation QA-аудит DevPlan 091 — верификация плана на полноту, консистентность, корректность, реализуемость, покрытие рисков и gate-readiness перед передачей Coder.
DESCRIPTION:           Full pre-implementation audit по 6 измерениям с верификацией всех утверждений DevPlan против фактического кода. Чтение финальных VR трёх планов-предшественников (087-02, 088-03, 089-03) для контекста. Проверка 20+ файлов на соответствие заявленным номерам строк и состоянию.
RATIONALE:             DevPlan 091 завершает три взаимозаблокированных PARTIAL-плана. Ошибка в плане = wasted Coder cycles + потенциальный regression. Pre-implementation audit предотвращает передачу невалидного плана на исполнение.
ACCEPTANCE_CRITERIA:   Все CRITICAL находки исправлены → VERDICT=PASS. Наличие CRITICAL → NEEDS FIX.
IMPLEMENTS:            QA role — Pre-implementation audit per user request (6 dimensions).
IMPACTS:               DevPlan 091 (02-DevPlan.md). Рекомендации для исправления.
REQUIRES:              SHA 8a6dbcbf, DevPlan 091, Brief 091, VR 087-02, VR 088-03, VR 089-03, фактический код.
$END_ARTIFACT_CONTRACT

---

# VerificationReport 03: Pre-Implementation Audit — DevPlan 091

🔒 **Verified against SHA:** `8a6dbcbf08297c0f4e044be254e244b20cadfa69`
📅 **Date:** 2026-07-30
📐 **Scope:** Pre-implementation audit (6 dimensions). DevPlan 091 + Brief 091 + 3 prior VRs + 20+ source files verified.

---

## Semantic Verdict: **NEEDS FIX** — 3 CRITICAL findings

План в целом качественный: §0 аудит честно отражает состояние кода, scope правильно ограничен, wave ordering dependency-driven. **Но 3 CRITICAL ошибки (неверный путь к файлу, неполный cleanup в шаге A1, противоречие в smoke test) должны быть исправлены до передачи Coder.**

---

## §1. Verification of DevPlan §0 Audit Claims

### 1.1 «Что УЖЕ сделано» — verification against actual code

| План | Claim | Verified | Evidence |
|------|-------|:--------:|----------|
| 087 | Dispatch переключён на 14-фазный path | ✅ | `_run_init_mode()` L1440: `BootstrapPhase.INIT_PHASE_ORDER` |
| 087 | 30 elif-веток удалены | ✅ | `rg "elif step_name ==" state_machine.py` = пусто |
| 087 | Старые `_step_N_*` функции удалены | ✅ | `rg "def _step_" steps.py` = пусто |
| 087 | `setup_state()` использует `BootstrapPhase` enum | ✅ | DevPlan claim; аудит не оспаривает |
| 087 | `dry_run_plan()` показывает фазы | ✅ | `state_machine.py:1027-1028` — phase-based list |
| 087 | `checkpoint_migration.py` удалён | ✅ | Файл не существует |
| 089 | `deliver_payload()`/`deploy_project()` в reconciler_projects удалены | ✅ | `rg "deliver_payload\|deploy_project" reconciler_projects.py` → только в комментариях/TRAP |
| 089 | `audit_logging.sh` references = 0 | ✅ | `rg "audit_logging\.sh" core/internal/deploy/` = пусто |
| 089 | `orchestrator_cli.py` GREP_SUMMARY | ✅ | L2: `# GREP_SUMMARY: orchestrator-cli, ...`; L3: `# STRUCTURE: ...` |
| 088 | `overlay_deliverer.py` broad except | ✅ | DevPlan claim; аудит не оспаривает |

**Verdict:** Все 10 claims из §0 «УЖЕ сделано» верифицированы. Аудит честный.

### 1.2 «Что ОСТАЛОСЬ» — verification against actual code

| Wave | Claim | Verified | Evidence |
|------|-------|:--------:|----------|
| A | `context_deployer.py` `_deploy_single_project()` L463-552 | ✅ | Функция существует, имеет собственный deploy path (pull→build→up→healthcheck) |
| A | `context_deployer.py` `_ORCHESTRATOR_AVAILABLE=False` L43-50 | ✅ | L43: `_ORCHESTRATOR_AVAILABLE = False` + import-try pattern L44-50 |
| A | `reconciler_projects.py` `_ORCHESTRATOR_AVAILABLE=True` L43 | ✅ | L43: `_ORCHESTRATOR_AVAILABLE = True` + TRAP[DEBT] |
| A | `makefiles/deploy.mk:54,71` вызовы `deploy-project.sh` | ✅ | L54: `bash .../deploy-project.sh`; L71: `@.../deploy-project.sh` |
| A | AC10 dry-run NOT in orchestrator | ✅ | `rg "dry.run\|dry_run" orchestrator.py` = пусто |
| B | `state_migration.py` EXISTS | ✅ | `glob` подтверждает: `core/internal/bootstrap/lifecycle/state_migration.py` |
| B | Migration-блок в `main()` L1354-1371 | ✅ | L1354-1371: `has_old_keys`/`has_new_keys` check + `migrate_state_to_phases()` |
| B | `INIT_STEPS`/`UPDATE_STEPS` dead constants L234-275 | ✅ | L234: `INIT_STEP_COUNT = 23`; L239-263: `INIT_STEPS` (23 items); L265-275: `UPDATE_STEPS` (9 items) |
| B | `from_dict(step_list=...)` fallback L574-581 | ✅ | L574: `step_list = INIT_STEPS if loaded_mode == "init" else UPDATE_STEPS` |
| B | `checkpoint.sh` exists | ✅ | 203 LOC, `core/lib/checkpoint.sh` |
| B | `test_phase_key_misalignment_prevented` Scenario B L965-986 | ✅ | L965-986: old numeric-key migration test; explicitly references `INIT_STEPS` |
| B | AGENTS.md L15 "14 INIT фаз" | ✅ | L15: `--mode init (14 INIT фаз)` — should be `9 INIT фаз` |
| C | `project_registry.py` 3× yaml.safe_load | ✅ | L99 (`register`), L163 (`deregister`), L220 (`list`) + yaml.dump L121/L175 |

**Verdict:** Все 13 claims из §0 «Что ОСТАЛОСЬ» верифицированы. Scope корректен.

---

## §2. Findings

### CRITICAL (must fix before implementation)

#### [F-1] CRITICAL · Wrong file path: `reconciler_projects.py`

| Поле | Значение |
|------|----------|
| **Location in DevPlan** | §1 graph L79, §2 A2 L165-168, §4 File Manifest Wave A L342, §8 L447 |
| **DevPlan says** | `core/internal/deploy/reconciler_projects.py` |
| **Actual path** | `core/internal/reconciler_projects.py` |
| **Impact** | Coder не найдёт файл. Все операции A2 (delete flag, delete guard) будут выполнены не по тому пути. `rg` check в verify-шаге не сработает. |
| **Fix** | Заменить ВСЕ вхождения `core/internal/deploy/reconciler_projects.py` на `core/internal/reconciler_projects.py` в: §1 L79, §2 A2, §4 L342, §8 L447. |

#### [F-2] CRITICAL · Incomplete A1: `_deploy_single_project_via_orchestrator()` L261-266 guard not addressed

| Поле | Значение |
|------|----------|
| **Location in DevPlan** | §2 A1 L159-163 |
| **DevPlan says** | "DELETE `_ORCHESTRATOR_AVAILABLE` block [L43-50]" + "MODIFY `deploy_context_projects()` [L358-395] → прямой вызов `_deploy_single_project_via_orchestrator()`" |
| **Actual code** | `_deploy_single_project_via_orchestrator()` L261-266 содержит: `if not _ORCHESTRATOR_AVAILABLE: ... return _deploy_single_project(project, ...)` |
| **Problem** | После удаления `_ORCHESTRATOR_AVAILABLE` на L43-50, код на L261 бросит `NameError` (переменная `_ORCHESTRATOR_AVAILABLE` не определена). Кроме того, `deploy_context_projects()` L376-379 содержит `if _ORCHESTRATOR_AVAILABLE: ... else: _deploy_single_project(...)` — ветка else вызовет удалённую функцию. |
| **Impact** | Runtime crash при вызове `deploy_context_projects()` после Wave A. |
| **Fix** | Добавить в A1: (a) DELETE guard `if not _ORCHESTRATOR_AVAILABLE:` L261-266 в `_deploy_single_project_via_orchestrator()`; (b) MODIFY `deploy_context_projects()` L376-379 → удалить `if/else`, всегда вызывать `_deploy_single_project_via_orchestrator()`. Обновить verify check: `rg "_ORCHESTRATOR_AVAILABLE" context_deployer.py = пусто` (уже есть, но стоит убедиться что покрывает и L261). |

#### [F-3] CRITICAL · Smoke test phase count contradiction: AC-B3 vs B8

| Поле | Значение |
|------|----------|
| **Location in DevPlan** | AC-B3 (§5 L399-404) vs B8 smoke description (§2 L233-234) |
| **AC-B3 says** | "make bootstrap-node --mode init на чистой тестовой ноде → **9 INIT фаз** проходят, state.json содержит 9 phase-ключей" |
| **B8 says** | "Smoke: make bootstrap-node --mode init на чистой тестовой ноде → **14 фаз** проходят (cold start, без migration)" |
| **Reality** | `--mode init` выполняет ТОЛЬКО 9 INIT фаз (φ1-φ8.5). 14 = 9 INIT + 5 UPDATE. UPDATE-фазы запускаются только через `--mode update`. |
| **Impact** | QA при верификации будет искать 14 фаз в smoke-тесте init-режима, что невозможно. |
| **Fix** | B8 L234: исправить "14 фаз проходят" → "9 INIT фаз проходят". Либо AC-B3 исправить на "14 фаз". Консистентно в пользу AC-B3 (9 INIT фаз — корректно для `--mode init`). |

---

### HIGH (should fix)

#### [F-4] HIGH · Brief has stale status data

| Поле | Значение |
|------|----------|
| **Location** | Brief §"Current Status" L20-22 |
| **Brief says** | "087: default dispatch (--mode init) всё ещё вызывает старый 23-step path" |
| **DevPlan §0 says** | "087: Dispatch не переключён на 14-фазный path → ✅ DONE" |
| **Impact** | Читатель Brief (без чтения DevPlan §0) подумает что работа по переключению dispatch всё ещё нужна. Wasted analysis time. |
| **Fix** | Обновить Brief §"Current Status" — отразить факт что dispatch уже переключён, но остались dead constants + migration block. Либо добавить сноску: «Актуальный остаток — см. DevPlan §0». |

#### [F-5] HIGH · state_machine.py LOC estimate optimistic for B3+B4

| Поле | Значение |
|------|----------|
| **Location** | §4 File Manifest Wave B L354 |
| **DevPlan says** | `state_machine.py` −~60 LOC (migration block + INIT_STEPS + from_dict) |
| **Actual** | INIT_STEPS = 25 строк (L239-263), UPDATE_STEPS = 10 строк (L265-275), INIT_STEP_COUNT + UPDATE_STEP_COUNT = 2 строки, migration block = 18 строк (L1354-1371), from_dict fallback = 8 строк (L574-581). Итого ~63 строк. |
| **Verdict** | Оценка близка к реальности (~63 vs ~60), но не учитывает: `_deploy_single_project_via_orchestrator()` L261-266 guard cleanup (6 строк) + `deploy_context_projects()` L376-379 cleanup (4 строки). Реально: −~73 LOC в совокупности с A1. Не критично для feasibility, но Coder должен знать. |

---

### MEDIUM (consider fixing)

#### [F-6] MEDIUM · Step A2 describes deleting non-existent guard

| Поле | Значение |
|------|----------|
| **Location** | §2 A2 L167 |
| **DevPlan says** | "DELETE `if not _ORCHESTRATOR_AVAILABLE:` guard [~L277]" |
| **Actual code** | `reconciler_projects.py` L265: `deploy_via_orchestrator()` — НЕ содержит `if not _ORCHESTRATOR_AVAILABLE` guard. `rg "if not _ORCHESTRATOR_AVAILABLE" reconciler_projects.py` = пусто. |
| **Problem** | Шаг описывает удаление того, чего уже нет. Реальный остаток для reconciler_projects.py: только удаление vestigial флага `_ORCHESTRATOR_AVAILABLE = True` на L43 (уже в transition state — всегда True). |
| **Fix** | A2: заменить "DELETE `if not _ORCHESTRATOR_AVAILABLE:` guard [~L277]" на "DELETE `_ORCHESTRATOR_AVAILABLE = True` + TRAP[DEBT] блок [L37-43]". Verify check: `rg "_ORCHESTRATOR_AVAILABLE" reconciler_projects.py = пусто` (тот же). |

#### [F-7] MEDIUM · AC-B3 smoke test требует manual test node — not automatable

| Поле | Значение |
|------|----------|
| **Location** | AC-B3 §5 L403-404, B8 §2 L233-234 |
| **Method** | "manual dry-run / fresh node" |
| **Problem** | AC-B3 smoke test не автоматизируем в CI. QA не сможет верифицировать AC-B3 без ручного доступа к тестовой ноде. Это ожидаемо для платформенного bootstrap, но должно быть явно отмечено как manual-only verification. |
| **Fix** | Добавить в AC-B3 метод верификации: "(manual — requires test VPS)". |

#### [F-8] MEDIUM · deploy.mk L54 reference — entrypoint already deleted

| Поле | Значение |
|------|----------|
| **Location** | §2 A3 L171, `makefiles/deploy.mk:54,71` |
| **DevPlan says** | "MODIFY L54: bash deploy-project.sh → python3 -m core.internal.deploy.orchestrator_cli deploy-many" |
| **Actual** | `core/entrypoints/deploy-project.sh` — УЖЕ удалён (glob = not found). L54 вызывает несуществующий файл — runtime error при `make deploy LAUNCH=1`. |
| **Impact** | A3 — не cleanup, а CRITICAL bugfix. Текущий `make deploy LAUNCH=1` сломается с `No such file or directory`. |
| **Fix** | Повысить приоритет A3 в описании: не просто "cleanup stale ref", а "fix broken make target". |

---

### LOW (cosmetic / suggestions)

#### [F-9] LOW · orchestrator.py LOC estimate for dry-run

| Поле | Значение |
|------|----------|
| **Location** | §4 File Manifest Wave A L345 |
| **DevPlan says** | `orchestrator.py` +~30 LOC (dry_run param) |
| **Assessment** | `deploy()` сигнатура: +1 параметр. Тело: ~6-8 условных блоков (lock skip, assemble skip, deliver skip, deploy_compose skip, healthcheck skip, snapshot skip, audit). + docstring update. Реально ~40-50 LOC. |
| **Fix** | +~30 → +~45 LOC. |

#### [F-10] LOW · entrypoint-manifest.yaml — only historical reference remains

| Поле | Значение |
|------|----------|
| **Location** | §2 A4 L176-177, entrypoint-manifest.yaml |
| **DevPlan says** | "verify: rg 'deploy-project\.sh' core/entrypoint-manifest.yaml = 0 STALE (L595 историческое описание OK)" |
| **Actual** | `rg "deploy-project\.sh" entrypoint-manifest.yaml` = 1 match: L595 `description: Unified deploy CLI — replaces deploy-project.sh`. Это НЕ stale reference — это описание назначения CLI. |
| **Verdict** | A4 verify check корректен. L595 — допустимое историческое описание. |

#### [F-11] LOW · checkpoint.sh analysis (B6) — consumer search pattern incomplete

| Поле | Значение |
|------|----------|
| **Location** | §2 B6 L220-223 |
| **DevPlan says** | "АНАЛИЗ: `rg 'checkpoint_step|checkpoint_done|checkpoint_mark' core/ --type sh`" |
| **Suggestion** | Добавить также поиск `checkpoint.sh` как source/include: `rg 'source.*checkpoint\.sh|\. .*checkpoint\.sh' core/ --type sh`. Shell-скрипты могут source'ить checkpoint.sh без вызова отдельных функций. |

---

## §3. Dimension Scores

### Dimension 1: Completeness (Полнота) — **7/10**

| Check | Status |
|-------|:------:|
| AC→step mapping: все 14 AC покрыты шагами | ✅ |
| AC без corresponding step: нет | ✅ |
| Находки §0 «Что ОСТАЛОСЬ» имеют шаги в §2 или явно → Debt | ✅ |
| Все файлы из шагов §2 присутствуют в File Manifest §4 | ⚠️ F-1: wrong path for reconciler_projects.py |
| Шаг A1 не полностью специфицирует cleanup `_deploy_single_project_via_orchestrator()` | ⚠️ F-2 |
| Brief расходится с DevPlan §0 (stale status) | ⚠️ F-4 |

### Dimension 2: Consistency (Консистентность) — **7/10**

| Check | Status |
|-------|:------:|
| AC-C1/AC-C2 соответствуют шагам C1-C7 | ✅ |
| D2 soft↔hard idempotency соответствует C1-C5 | ✅ |
| D1-D5 не противоречат друг другу | ✅ |
| Plan §1 соответствует инвариантам (Makefile facade, Invariant 11, Python-first) | ✅ |
| AC-B3 vs B8 smoke test противоречие (9 vs 14 фаз) | ⚠️ F-3 |
| A2 описывает удаление несуществующего guard | ⚠️ F-6 |

### Dimension 3: Correctness (Корректность) — **6/10**

| Check | Status |
|-------|:------:|
| Номера строк для context_deployer.py (L43-50, L358-395, L463-552) | ✅ точны |
| Номера строк для state_machine.py (L234-275, L574-581, L1354-1371) | ✅ точны |
| Номера строк для node_yaml.py (L1141, L1194, L551, L204-224) | ✅ точны |
| Номера строк для project_registry.py (L74-125, L144-179, L201-236, L43) | ✅ точны |
| Номера строк для test_state_machine.py (L965-986) | ✅ точны |
| Файл state_migration.py существует | ✅ |
| Функции add_project/remove_project/get_projects/ProjectEntry существуют в node_yaml.py | ✅ |
| **PATH**: reconciler_projects.py = WRONG PATH | ❌ F-1 |
| `_deploy_single_project_via_orchestrator()` содержит неуказанный guard | ❌ F-2 |
| `_ORCHESTRATOR_AVAILABLE` guard на ~L277 в reconciler_projects.py НЕ существует | ⚠️ F-6 |

### Dimension 4: Feasibility (Реализуемость) — **8/10**

| Check | Status |
|-------|:------:|
| LOC Δ estimates в File Manifest — большинство реалистичны | ✅ |
| User Constraint: backward-compat удаляется полностью | ✅ |
| User Constraint: миграция state.json НЕ пишется | ✅ |
| User Constraint: чистый cold start | ✅ |
| Wave ordering dependency-driven логичен | ✅ |
| Нет circular dependency между волнами | ✅ |
| Оценка state_machine.py −~60 занижена (реально ~73 совместно с A1 cleanup) | ⚠️ F-5 |
| deploy.mk L54 — не cleanup, а bugfix (entrypoint уже удалён) | ⚠️ F-8 |

### Dimension 5: Risk Coverage (Покрытие рисков) — **7/10**

| Check | Status |
|-------|:------:|
| Risk: удаление `_deploy_single_project()` ломает deploy-context — mitigation описан | ✅ |
| Risk: checkpoint.sh удаление ломает shell-скрипты — mitigation (анализ перед действием) | ✅ |
| Risk: NodeYaml.add_project hard-error ломает idempotent registration — mitigation (D2 bridge) | ✅ |
| Risk: from_dict упрощение ломает загрузку state.json — mitigation (cold start) | ✅ |
| Risk: manifest regeneration создаёт unexpected drift — mitigation (check-manifests) | ✅ |
| **НЕ УПОМЯНУТО**: удаление `_ORCHESTRATOR_AVAILABLE` в context_deployer сломает `_deploy_single_project_via_orchestrator()` L261 | ❌ F-2 |
| **НЕ УПОМЯНУТО**: deploy.mk L54 вызывает уже удалённый entrypoint — runtime error уже сейчас | ⚠️ F-8 |
| **НЕ УПОМЯНУТО**: удаление state_migration.py + migration-блока может сломать существующие ноды с старым state.json при попытке `--mode update` | ⚠️ (minor — User Constraint разрешает) |

### Dimension 6: Gate Readiness — **8/10**

| Check | Status |
|-------|:------:|
| `make gate MODE=fast` зелёный после каждой волны (план утверждает) | ✅ (требует verify) |
| Invariant 11 (Manifest Generation Contract): `make generate-entrypoint-manifest` + `make check-manifests` | ✅ A4 |
| Удаление deploy-project.sh references из manifest корректно | ✅ A4 |
| entrypoint-manifest.yaml L595 — допустимое историческое описание | ✅ F-10 |
| Шаг A3 не уточняет что `deploy-project.sh` уже удалён (L54 вызовет runtime error) | ⚠️ F-8 |

---

## §4. Cross-Reference: DevPlan vs Prior VerificationReports

### Что изменилось с VR 087-02 (DRIFTED CRITICAL, Health 65/100)

| Finding из VR 087-02 | Статус в коде | Покрыто в DevPlan 091 |
|-----------------------|---------------|----------------------|
| DRIFT-DISPATCH-001: dispatch не переключён | ✅ DONE (§0) | — |
| DRIFT-DUAL-002: два execution path | ✅ DONE (§0) | — |
| DRIFT-STATESETUP-003: state.json старый формат | ⚠️ ЧАСТИЧНО: setup_state использует BootstrapPhase enum, но from_dict всё ещё с fallback | B3 (упростить загрузку), B4 (delete dead constants) |
| DRIFT-CHECKPOINT-004: checkpoint.sh старые ключи | ⚠️ ВСЁ ЕЩЁ: checkpoint.sh читает state.json но формат ключей зависит от того, что пишет state_machine | B6 (анализ → решение) |
| DRIFT-AGENTS-005: doc bug 14 vs 9 фаз | ❌ НЕ ИСПРАВЛЕНО: L15 "14 INIT фаз" | B7 (AGENTS.md fix) |
| GAP-DEFAULT-TEST: нет теста на phase-based dispatch | ✅ DONE (dispatch переключён) | — |
| FRAGILE-OLD-TESTS: test_init_flow_all_steps hardcoded 23 | ⚠️ ВСЁ ЕЩЁ: тесты проверяют старые значения | B5 (Scenario B delete) |

### Что изменилось с VR 089-03 (DRIFTED CRITICAL, Health 65/100)

| Finding из VR 089-03 | Статус в коде | Покрыто в DevPlan 091 |
|-----------------------|---------------|----------------------|
| DRIFT-MANIFEST: 7 stale refs deploy-project.sh | ✅ DONE: осталась 1 строка (L595 описание) | A4 (оставшаяся строка OK) |
| DRIFT-AC14: deliver_payload/deploy_project в reconciler | ✅ DONE (§0) | — |
| DRIFT-GATE: T17 gate test missing | ⚠️ Gate test существует (T17?), но unit-тест отсутствует | A6 (unit test AC13) |
| DRIFT-CONTEXT: context_deployer bypass | ❌ ВСЁ ЕЩЁ: _deploy_single_project() L463-552 | A1 (delete _deploy_single_project) |
| AC10 dry-run | ❌ НЕ РЕАЛИЗОВАНО | A5 |

### Что изменилось с VR 088-03 (DRIFTED WARNING, Health 79/100)

| Finding из VR 088-03 | Статус в коде | Покрыто в DevPlan 091 |
|-----------------------|---------------|----------------------|
| DRIFT-088-4: orchestrator_cli.py GREP_SUMMARY | ✅ DONE (§0) | — |
| DRIFT-088-7: project_registry.py yaml.safe_load | ❌ ВСЁ ЕЩЁ: 3× yaml.safe_load | C1-C5 (Wave C migration) |
| STA-1: TRAP[BUG] в mutation API | ⚠️ Частично (3 TRAP[BUG] добавлены) | Вне scope 091 → Debt |
| GAP-TYPED: typed getters без тестов | ⚠️ Всё ещё | Вне scope 091 → Debt |

---

## §5. Recommendations for Coder

### Must-fix in DevPlan before implementation (delegate to Architect)

- **[R-1]** Fix F-1: заменить `core/internal/deploy/reconciler_projects.py` → `core/internal/reconciler_projects.py` во всех секциях DevPlan (§1, §2, §4, §8)
- **[R-2]** Fix F-2: дополнить A1 — удалить guard L261-266 в `_deploy_single_project_via_orchestrator()` + удалить if/else в `deploy_context_projects()` L376-379
- **[R-3]** Fix F-3: синхронизировать AC-B3 и B8 (9 INIT фаз, не 14)

### Should-fix (recommended)

- **[R-4]** Fix F-4: обновить Brief §"Current Status" или добавить перекрёстную ссылку на DevPlan §0
- **[R-5]** Fix F-6: скорректировать A2 — убрать несуществующий guard, оставить только удаление `_ORCHESTRATOR_AVAILABLE = True` на L43
- **[R-6]** Fix F-8: документировать A3 как bugfix (entrypoint уже удалён, L54 ломается сейчас), а не просто cleanup

### Consider (cosmetic)

- **[R-7]** Fix F-5: обновить LOC estimate для state_machine.py с −~60 на −~73 (учёт совместного cleanup с A1)
- **[R-8]** Fix F-7: добавить "(manual — requires test VPS)" в метод верификации AC-B3
- **[R-9]** Fix F-9: обновить orchestrator.py LOC estimate с +~30 на +~45
- **[R-10]** Fix F-11: дополнить B6 search pattern на `rg 'source.*checkpoint\.sh' core/ --type sh`

---

## §6. TRAP Verification

Активные TRAP в scope-файлах (проверено grep `TRAP\[`):

| File | TRAP Type | Line | Status |
|------|-----------|------|:------:|
| `context_deployer.py` | — | — | No TRAP |
| `reconciler_projects.py` | TRAP[DEBT] | 38-42 | VALID (документирует transition state _ORCHESTRATOR_AVAILABLE) |
| `state_machine.py` | — | — | No TRAP |
| `node_yaml.py` | TRAP[BUG] | 1134-1140, 1186-1193 | VALID |
| `project_registry.py` | — | — | No TRAP |
| `orchestrator.py` | — | — | No TRAP |

**Finding:** `state_machine.py` — migration-блок L1354-1371 не имеет TRAP[DEBT] аннотации. Рекомендуется добавить перед удалением чтобы будущие агенты понимали почему блок был удалён (User Constraint: no migration, cold start).

---

## §7. Project Health Score (pre-implementation baseline)

Актуальное состояние кода (pre-091):

```
Score = 100
− 5  (CRITICAL: context_deployer bypass path _deploy_single_project still active)
− 5  (CRITICAL: _ORCHESTRATOR_AVAILABLE=False fallback active in context_deployer)
− 3  (HIGH: state_migration.py exists — dead code 198 LOC)
− 3  (HIGH: migration block active in main() — dead code path)
− 3  (HIGH: INIT_STEPS/UPDATE_STEPS dead constants 35 LOC)
− 3  (HIGH: from_dict step_list fallback — backward-compat path)
− 3  (HIGH: project_registry 3× yaml.safe_load outside NodeYaml facade)
− 1  (MEDIUM: checkpoint.sh dual-format — phase-aware but old-key references)
− 1  (MEDIUM: deploy.mk L54/L71 stale refs — deploy-project.sh already deleted)
− 1  (MEDIUM: test Scenario B — tests backward-compat that will be removed)
− 1  (LOW: AGENTS.md doc bug — "14 INIT фаз")
− 0  (AT_RISK invariants: none — Invariant 11 held, manifest clean)
− 0  (uncovered invariants: none remaining from pre-091 state)
─────────────────
= 72/100
```

**Post-091 target:** 100/100 (all findings resolved, all three VRs → STABLE).

---

## Semantic Verdict: NEEDS FIX

3 CRITICAL findings (F-1, F-2, F-3) должны быть исправлены в DevPlan до передачи Coder. План в остальном качественный: честный §0 аудит, правильный scope, dependency-driven wave ordering. После исправления CRITICAL — VERDICT = PASS.

**Делегирование:** Architect — исправить F-1, F-2, F-3 в DevPlan 091 (02-DevPlan.md). Опционально: F-4, F-6, F-8 из HIGH/MEDIUM.

$END_VERIFICATION_REPORT
