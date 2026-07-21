# 035-DevPlan-Final: Wave 4 — декомпозиция топ-3 скриптов (W4-E1/E3/E2/E6)

**Wave:** 4 (Strangler Fig топ-3) программы `027-architecture-modernization-program`
**Source plan:** `.ai/plans/035-wave4-strangler-top3/02-DevPlan.md` — полный DevPlan (6 эпиков)
**Completed (2026-07-22):** W4-E5 (Regression baseline) + W4-E4 (Makefile include-split) — закоммичены
**This plan:** оставшиеся 4 эпика — декомпозиция трёх shell-монолитов + inline python3 sweep
**Operator decisions:** локация Python-модулей → `core/internal/bootstrap/{deploy,converge,lifecycle}/`; порядок → deploy-modules → converge → node-lifecycle (бриф §6.3)

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Декомпозировать top-3 shell-монолита (deploy-modules.sh=1664 строк, node-lifecycle.sh=1301 строк, converge.sh=1149 строк = 4114 суммарно) по принципу Strangler-Fig: бизнес-логика → типизированные Python-модули с unit-тестами, shell остаётся тонким фасадом <100-200 строк. Завершить консолидацию inline `python3 -c` (31 блока в топ-3 скриптах на 2026-07-22). Закрыть проблемы P03 (top-3 monolith), P09 (deploy-modules SRP), P12 (inline python3 завершение) матрицы.
DESCRIPTION:           Четыре эпика, выполняемых строго последовательно. W4-E5 (regression baseline) и W4-E4 (Makefile split) УЖЕ ВЫПОЛНЕНЫ и закоммичены 2026-07-22:
                       (DONE) W4-E5 — Regression baseline: 27→51 тест, baseline-замер в reports/wave4-baseline-2026-07.csv.
                       (DONE) W4-E4 — Makefile include-split: root Makefile 747→41 строк, 6 тематических .mk в makefiles/, CI gate test_gate_makefile_targets.py.
                       ОСТАЛОСЬ:
                       (W4-E1) deploy-modules.sh декомпозиция — 1664 строк → 5 Python-модулей в `core/internal/bootstrap/deploy/`: `docker_orchestrator.py`, `sudoers_generator.py`, `context_overlay.py`, `secrets_validator.py`, `orphan_reconciler.py`. Shell-фасад <100 строк.
                       (W4-E3) converge.sh reconcile-loop → Python — 1149 строк → `core/internal/bootstrap/converge/reconciler.py`. Shell-фасад <150 строк.
                       (W4-E2) node-lifecycle.sh state-machine → Python — 1301 строк → `core/internal/bootstrap/lifecycle/state_machine.py` (17+6 step-transitions). Shell-фасад <200 строк.
                       (W4-E6) Inline python3 завершение — 31 оставшийся блок в топ-3 мигрируются в Python-модули в ходе W4-E1/E2/E3 (побочный эффект, без отдельной работы).
RATIONALE:             Анализ архитектуры (`reports/architecture-analysis-2026-07-21.md` §3.1, §4.1). Top-3 shell-scripts = 4114 строк, каждый смешивает 3-5 ответственностей; 31 inline python3-блок в топ-3 требует миграции. W4-E5 (regression baseline из 18 edge-case тестов) уже создаёт страховочную сетку ДО extraction. W4-E4 (Makefile split) уже обеспечивает навигацию и снижает риск конфликтов. Оставшиеся 4 эпика — собственно extraction бизнес-логики в Python со Strangler-Fig паттерном (Option B §2.1 оригинального DevPlan, score 8/10).
ACCEPTANCE_CRITERIA:
  AC-1 (W4-E1 deploy-modules.sh декомпозиция):
    a. `core/internal/bootstrap/deploy/` создан с 5 Python-модулями:
       - `docker_orchestrator.py` — deploy_docker_module, deploy_docker_group, _pre_pull_images, wait_for_readiness, run_healthcheck, _check_image_exists (~450-550 строк)
       - `sudoers_generator.py` — generate_module_sudoers, _render_sudoers_rules, _batch_generate_sudoers (~200-300 строк)
       - `context_overlay.py` — ensure_context_repo (git clone/pull с кешированием) (~150-200 строк)
       - `secrets_validator.py` — _check_env_requires, _validate_secret_charsets, _get_module_severity, _batch_module_metadata (~250-350 строк)
       - `orphan_reconciler.py` — _batch_orphan_reconciliation (~150-200 строк)
    b. Каждый Python-модуль имеет MODULE_CONTRACT region с @purpose, @scope, @invariants, @rationale; GREP_SUMMARY + STRUCTURE; LDD-логи [IMP:7-10].
    c. `core/internal/bootstrap/deploy-modules.sh` урезан до <100 строк: arg parsing (через lib/args.sh), делегирование к Python-модулям, exit code.
    d. `tests/test_deploy_modules.py` green ПОСЛЕ extraction (regression). Новые unit-тесты: `tests/unit/test_{docker_orchestrator,sudoers_generator,context_overlay,secrets_validator,orphan_reconciler}.py`.
    e. `wc -l core/internal/bootstrap/deploy-modules.sh` < 100.
    f. В deploy-modules.sh НЕТ inline `python3 -c` и `<<PYEOF` → 0 matches.
    g. Staging-тест: `make bootstrap-node NODE=<test> --mode init` + `make node-update NODE=<test>`.

  AC-2 (W4-E3 converge.sh → reconciler.py):
    a. `core/internal/bootstrap/converge/` создан. `reconciler.py` содержит: reconcile_perms, reconcile_audit_log, reconcile_projects, reconcile_networks, detect_hosts_drift, verify_vhosts, report_init/add/emit, _is_stub (~500-700 строк).
    b. `core/internal/bootstrap/converge.sh` урезан до <150 строк: setup_environment, acquire_lock, делегирование к python3 reconciler.py, exit code mapping (0=clean, 1=warnings, 2=errors).
    c. `tests/test_converge_exit.py` green ПОСЛЕ extraction. `tests/unit/test_reconciler.py` покрывает каждый reconcile_* метод.
    d. `wc -l core/internal/bootstrap/converge.sh` < 150.
    e. В converge.sh НЕТ inline `python3 -c` и `<<PYEOF` → 0 matches.
    f. Staging-тест: `make converge NODE=<test>` на тестовой ноде с искусственным drift.

  AC-3 (W4-E2 node-lifecycle.sh → state_machine.py):
    a. `core/internal/bootstrap/lifecycle/` создан. `state_machine.py` содержит: явная state-machine (JSON state-file `/var/lib/platform/.bootstrap/state.json`), 17 step_* transitions + 6 update_step_*, pre/post-условия, checkpoint-resume, TOR-conditional, step-skip на content-hash (~600-800 строк).
    b. `steps.py` (опционально): каждый step_* как функция с pre/post + subprocess (~300-400 строк).
    c. `core/internal/bootstrap/node-lifecycle.sh` урезан до <200 строк: arg parsing (--mode init/update), state.json load, вызов python3 state_machine.py.
    d. `tests/test_node_lifecycle_static.py` + `tests/test_bootstrap_auto.py` green ПОСЛЕ extraction. `tests/unit/test_state_machine.py` покрывает: init vs update flow, step-skip, step-warn/error, checkpoint resume.
    e. `wc -l core/internal/bootstrap/node-lifecycle.sh` < 200.
    f. В node-lifecycle.sh НЕТ inline `python3 -c` и `<<PYEOF` → 0 matches.
    g. Staging-тест: `make bootstrap-node NODE=<test> --mode init` (fresh VPS).

  AC-4 (W4-E6 Inline python3 завершение — неявный, выполняется в ходе E1/E2/E3):
    a. `python3 -c` count в топ-3: 31 → 0.
    b. `<<PYEOF` count в топ-3: 9 → 0.
    c. `reports/inline-python3-map-2026-07-21.csv` обновлён: строки для топ-3 скриптов → `consolidation_wave=W4-done`.

  AC-5 (Cross-cutting):
    a. `make gate MODE=fast` — зелёный.
    b. `make gate MODE=full` — зелёный (с учётом macOS-overlay).
    c. Все новые Python-файлы проходят `ruff check` + `ruff format --check`.
    d. `.pre-commit-config.yaml` hook `no-new-inline-python3` не ломается.
    e. `core/entrypoint-manifest.yaml` обновлён: новые Python-скрипты зарегистрированы.
    f. `core/internal/bootstrap/AGENTS.md` обновлён: раздел "Python-модули декомпозиции".
    g. Root `AGENTS.md`: TRAP[DECISION] Strangler-Fig canonical.

  AC-6 (Production-релиз):
    a. Staging-gate: bootstrap init → node-update → converge — все 3 проходят без hang.
    b. Audit-trail фиксирует выполнение.
    c. Замер post-Wave 4: `reports/wave4-results-2026-XX.csv` — Python LOC (+2-3K), shell LOC (4114 → ~450), inline python3 (31 → 0).

IMPLEMENTS:            Brief 027 §6 (Wave 4 эпики W4-E1…W4-E6). DevPlan 02 §3-5, §7-8. AGENTS.md invariants 1, 4. Principles 6, 8, 9.
IMPACTS:               **New Python (~2-3K LOC):** `core/internal/bootstrap/deploy/{docker_orchestrator,sudoers_generator,context_overlay,secrets_validator,orphan_reconciler}.py`, `core/internal/bootstrap/converge/reconciler.py`, `core/internal/bootstrap/lifecycle/state_machine.py`. **Modified shell (~4114 → ~450 LOC):** `deploy-modules.sh` (<100), `converge.sh` (<150), `node-lifecycle.sh` (<200). **New tests:** `tests/unit/test_{docker_orchestrator,sudoers_generator,context_overlay,secrets_validator,orphan_reconciler,reconciler,state_machine}.py`. **Docs:** `core/internal/bootstrap/AGENTS.md`, root `AGENTS.md`. **Registry:** `core/entrypoint-manifest.yaml`. **Tracking:** `reports/inline-python3-map-2026-07-21.csv`, `reports/wave4-results-2026-XX.csv`.
REQUIRES:              Выполненные W4-E5 (regression baseline) и W4-E4 (Makefile split). Чистый working tree. Python 3.10+, pyyaml, jsonschema. Staging-нода для production-релиза.
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Зафиксировать текущее состояние (W4-E5 + W4-E4 done) => GOAL_STATE
- GOAL Описать декомпозицию deploy-modules.sh с verified inline-подсчётами => GOAL_DEPLOY
- GOAL Описать декомпозицию converge.sh → reconciler.py => GOAL_CONVERGE
- GOAL Описать декомпозицию node-lifecycle.sh → state_machine.py => GOAL_LIFECYCLE
- GOAL Описать inline python3 sweep (W4-E6) с verified текущими подсчётами => GOAL_INLINE
- GOAL Зафиксировать risks и mitigation => GOAL_RISK
- GOAL Оценить effort и последовательность => GOAL_EFFORT
**SECTION_USE_CASES:**
- USE_CASE Разработчик добавляет docker-модуль → docker_orchestrator.deploy_docker_module() — типизированный контракт, unit-тест => UC_NEW_MODULE
- USE_CASE Баг в orphan-reconciliation → unit-тест test_orphan_reconciler локализует => UC_DEBUG
- USE_CASE Staging-деплой: bootstrap init → node-update → converge — regression после каждого эпика => UC_STAGING
$END_DOCUMENT_PLAN
```

---

## 1. Текущее состояние (2026-07-22)

### 1.1. Что уже выполнено (закоммичено)

| Эпик       | Описание                                           | Статус    | Доказательство                                                                                    |
|------------|----------------------------------------------------|-----------|---------------------------------------------------------------------------------------------------|
| **W4-E5**  | Regression baseline — edge-case тесты              | ✅ DONE   | 27→51 тест, 18 новых edge-case. baseline в `reports/wave4-baseline-2026-07.csv`.                  |
| **W4-E4**  | Makefile include-split                             | ✅ DONE   | Root Makefile 747→41 строк, 6 `.mk` файлов (763 строк), gate `test_gate_makefile_targets.py`.     |

### 1.2. Что осталось (4 эпика)

| Эпик       | Описание                                           | Текущее состояние (verified 2026-07-22)                                                          |
|------------|----------------------------------------------------|---------------------------------------------------------------------------------------------------|
| **W4-E1**  | deploy-modules.sh → 5 Python-модулей               | 1664 строк, 14 `python3 -c` + 1 `<<PYEOF` = 15 inline-блоков                                      |
| **W4-E3**  | converge.sh → reconciler.py                        | 1149 строк, 6 `python3 -c` + 5 `<<PYEOF` = 11 inline-блоков                                       |
| **W4-E2**  | node-lifecycle.sh → state_machine.py               | 1301 строк, 2 `python3 -c` + 3 `<<PYEOF` = 5 inline-блоков                                        |
| **W4-E6**  | Inline python3 sweep                               | 31 блок суммарно (22 `python3 -c` + 9 `<<PYEOF`) — побочный эффект W4-E1/E2/E3                   |

### 1.3. Файлы, созданные в W4-E4 (Makefile split)

```
makefiles/bootstrap.mk    ( 74 строки)  — bootstrap-node, node-update, converge, render-vhosts
makefiles/deploy.mk       (128 строк)   — deploy, deploy-project, context-promote, hermes-*, verify
makefiles/scaffold.mk     ( 95 строк)   — new-project, new-context, adopt-project, remove-project, project-*
makefiles/modules.mk      (104 строки)  — up, down, restart, status, healthcheck, backup, restore, discover-modules
makefiles/ci.mk           (283 строки)  — test, gate, validate, lint, pre-commit, scripts-audit, audit
makefiles/helpers.mk      ( 79 строк)   — venv, templates-*, dev-certs, provision, test-inventory-sync, help
```

### 1.4. Файлы, модифицированные в W4-E5 (regression baseline)

```
tests/test_deploy_modules.py        (+570 строк, 10→16 тестов)
tests/test_node_lifecycle_static.py (+342 строки, 0→11 тестов)
tests/test_converge_exit.py         (+344 строки, 5→9 тестов)
tests/test_bootstrap_auto.py        (+138 строк, 12→15 тестов)
```

---

## 2. W4-E1: deploy-modules.sh декомпозиция (ПЕРВЫЙ ЭПИК)

### 2.1. Verified inline-подсчёт (2026-07-22)

```
deploy-modules.sh (1664 строки):
  python3 -c: 14
  <<PYEOF:    1
  Total:      15 inline-блоков
```

**Отклонение от оригинального DevPlan (02):** DevPlan §1.4 указывал 10 `python3 -c` + 1 `<<PYEOF` = 11. Фактически 14 + 1 = 15. Дрейф +4 `python3 -c` — часть из них относится к `_expand_transitive_deps` и `detect_install_type`, которые добавились после первоначального анализа.

### 2.2. 5 Python-модулей (детализация)

| Модуль                     | Строки  | Shell-функции                                                                                   | Inline python3 |
|----------------------------|---------|-------------------------------------------------------------------------------------------------|----------------|
| `docker_orchestrator.py`   | 450-550 | deploy_docker_module, deploy_docker_group, _pre_pull_images, _check_image_exists, wait_for_readiness, run_healthcheck | 1 PYEOF |
| `sudoers_generator.py`     | 200-300 | generate_module_sudoers, _render_sudoers_rules, _batch_generate_sudoers                         | —              |
| `context_overlay.py`       | 150-200 | ensure_context_repo                                                                             | 1 `python3 -c` |
| `secrets_validator.py`     | 250-350 | _check_env_requires, _validate_secret_charsets, _get_module_severity, _batch_module_metadata, _expand_transitive_deps, parse_modules_from_node_yaml, detect_install_type | 12 `python3 -c` |
| `orphan_reconciler.py`     | 150-200 | _batch_orphan_reconciliation                                                                    | 1 `python3 -c` |

**Остаются в shell-фасаде:** `_load_platform_networks`, `ensure_docker_network`, `ensure_spool_dirs`, `main` + arg parsing = ~80-100 строк.

### 2.3. Порядок extraction (Strangler micro-steps)

```
1. Создать core/internal/bootstrap/deploy/__init__.py
2. Создать docker_orchestrator.py с deploy_docker_module (docker CLI через subprocess.run)
3. Unit-тест tests/unit/test_docker_orchestrator.py — mock subprocess, verify args
4. В deploy-modules.sh: заменить inline-реализацию на python3 docker_orchestrator.py --action deploy ...
5. Regression: tests/test_deploy_modules.py green (16 тестов)
6. Повторить для остальных 4 модулей (sudoers → context_overlay → secrets_validator → orphan)
7. Финальный pass: урезать deploy-modules.sh до <100 строк
8. Staging-тест: make bootstrap-node NODE=<test> --mode init + node-update
```

### 2.4. Acceptance

(см. AC-1 в $ARTIFACT_CONTRACT выше)

---

## 3. W4-E3: converge.sh → reconciler.py (ВТОРОЙ ЭПИК)

### 3.1. Verified inline-подсчёт (2026-07-22)

```
converge.sh (1149 строк):
  python3 -c: 6
  <<PYEOF:    5
  Total:      11 inline-блоков
```

**Отклонение от оригинального DevPlan (02):** DevPlan §1.4 указывал 11 `python3 -c` + 5 `<<PYEOF` = 16. Фактически 6 + 5 = 11. Часть `python3 -c` уже мигрирована в `yaml_query.py` в ходе Wave 1. Осталось 11 блоков — в основном node.yaml parsing (строки 223, 241, 502, 521, 736, 803, 872, 891, 932, 934).

### 3.2. Функциональная карта

| Python-модуль     | Shell-функции                                                                              | Inline (осталось)                      |
|-------------------|--------------------------------------------------------------------------------------------|----------------------------------------|
| `reconciler.py`   | reconcile_perms, reconcile_audit_log, reconcile_projects, reconcile_networks, detect_hosts_drift, verify_vhosts, _is_stub | 6 `python3 -c` + 5 `<<PYEOF`          |
| (shell-фасад)     | setup_environment, acquire_lock, report_init/add/emit, main, usage                         | —                                      |

**Exit-code семантика (КРИТИЧНО):**
- `exit 0` — clean (no drifts)
- `exit 1` — warnings (drifts, auto-reconciled)
- `exit 2` — errors (unrecoverable)

Проверяется `tests/test_converge_exit.py` (9 тестов после W4-E5).

### 3.3. Порядок extraction

```
1. Создать core/internal/bootstrap/converge/__init__.py
2. Создать reconciler.py с CLI: --node-yaml <path> --report-file <path> --mode <check|reconcile>
3. Реализовать reconcile_* методы (inline python3 → yaml_query.py API)
4. Output: JSON report {drifts: [...], reconciled: [...], errors: [...], exit_code: int}
5. Unit-тест tests/unit/test_reconciler.py: каждый reconcile_* метод с tmp_path
6. В converge.sh: setup_environment + acquire_lock + python3 reconciler.py + report_emit + exit
7. Regression: tests/test_converge_exit.py green (exit-code mapping сохранён)
8. Staging-тест: make converge NODE=<test>
```

### 3.4. Acceptance

(см. AC-2 в $ARTIFACT_CONTRACT выше)

---

## 4. W4-E2: node-lifecycle.sh → state_machine.py (ТРЕТИЙ ЭПИК)

### 4.1. Verified inline-подсчёт (2026-07-22)

```
node-lifecycle.sh (1301 строка):
  python3 -c: 2
  <<PYEOF:    3
  Total:      5 inline-блоков
```

**Отклонение от оригинального DevPlan (02):** DevPlan §1.4 указывал 5 `python3 -c` + 3 `<<PYEOF` = 8. Фактически 2 + 3 = 5. Часть `python3 -c` уже мигрирована. Оставшиеся: node.yaml schema validate, modules_raw, TOR_ENABLED.

### 4.2. State-machine дизайн

```python
# Явная state-machine (JSON state-file)
# /var/lib/platform/.bootstrap/state.json
{
  "mode": "init|update",
  "node": "<node-name>",
  "current_step": 5,
  "steps": {
    "1": {"name": "ssh_access", "status": "done", "hash": "abc123", "started_at": "..."},
    "2": {"name": "apt_deps", "status": "done", "hash": "def456"},
    "3": {"name": "tor_proxy", "status": "skipped", "reason": "TOR_DISABLED"},
    "4": {"name": "install_docker", "status": "running", "started_at": "..."},
    ...
  },
  "errors": [],
  "warnings": []
}
```

Transitions:
- `start_step(n)` → pre-условия, hash-compare, update state.json
- `complete_step(n)` → update hash, status=done
- `skip_step(n, reason)` → status=skipped (TOR_DISABLED, content unchanged)
- `fail_step(n, error)` → status=failed, collect error, decide abort vs continue

### 4.3. Функциональная карта

| Python-модуль        | Shell-функции                                                                                      | Inline     |
|----------------------|----------------------------------------------------------------------------------------------------|------------|
| `state_machine.py`   | 17 step_* (init) + 6 update_step_* (update), step_start/done/skip/warn, _step_hash, validate_bootstrap_env | 5 блоков   |
| `steps.py`           | _step_install_acme, _step_secrets_init (опционально, если логика сложная)                          | —          |
| (shell-фасад)        | main, mode-dispatch (init vs update), subprocess-вызовы (systemctl, apt, ssh)                     | —          |

### 4.4. Порядок extraction

```
1. Создать core/internal/bootstrap/lifecycle/__init__.py
2. Создать state_machine.py: state dataclass, StateMachine class, transitions
3. Создать steps.py (опционально): каждый step_* как функция с pre/post
4. Unit-тест tests/unit/test_state_machine.py: transitions, checkpoint-resume, skip-logic
5. В node-lifecycle.sh: mode-dispatch + state.json load + python3 state_machine.py run
6. Regression: tests/test_node_lifecycle_static.py + test_bootstrap_auto.py green
7. Staging-тест: make bootstrap-node NODE=<test> --mode init (fresh VPS)
```

### 4.5. Acceptance

(см. AC-3 в $ARTIFACT_CONTRACT выше)

---

## 5. W4-E6: Inline python3 завершение (НЕЯВНЫЙ)

### 5.1. Что делаем

W4-E6 — побочный эффект W4-E1/E2/E3. В ходе extraction каждый inline `python3 -c` и `<<PYEOF` блок заменяется вызовом соответствующего Python-модуля (использующего `yaml_query.py` API для YAML/JSON parsing).

### 5.2. Verified baseline (2026-07-22)

| Скрипт               | `python3 -c` | `<<PYEOF` | Total |
|----------------------|-------------|----------|-------|
| deploy-modules.sh    | 14          | 1        | 15    |
| converge.sh          | 6           | 5        | 11    |
| node-lifecycle.sh    | 2           | 3        | 5     |
| **Итого**            | **22**      | **9**     | **31** |

После W4-E1/E2/E3: все 31 → 0.

### 5.3. Acceptance

(см. AC-4 в $ARTIFACT_CONTRACT выше)

---

## 6. File Manifest (только новые/модифицируемые в E1/E3/E2/E6)

### 6.1. CREATE

```
# Python-пакеты (W4-E1)
core/internal/bootstrap/deploy/__init__.py
core/internal/bootstrap/deploy/docker_orchestrator.py          # ~450-550 LOC
core/internal/bootstrap/deploy/sudoers_generator.py             # ~200-300 LOC
core/internal/bootstrap/deploy/context_overlay.py               # ~150-200 LOC
core/internal/bootstrap/deploy/secrets_validator.py             # ~250-350 LOC
core/internal/bootstrap/deploy/orphan_reconciler.py             # ~150-200 LOC

# Python-пакет (W4-E3)
core/internal/bootstrap/converge/__init__.py
core/internal/bootstrap/converge/reconciler.py                  # ~500-700 LOC

# Python-пакет (W4-E2)
core/internal/bootstrap/lifecycle/__init__.py
core/internal/bootstrap/lifecycle/state_machine.py              # ~600-800 LOC
core/internal/bootstrap/lifecycle/steps.py                      # ~300-400 LOC (опционально)

# Unit-тесты (W4-E1)
tests/unit/test_docker_orchestrator.py
tests/unit/test_sudoers_generator.py
tests/unit/test_context_overlay.py
tests/unit/test_secrets_validator.py
tests/unit/test_orphan_reconciler.py

# Unit-тесты (W4-E3)
tests/unit/test_reconciler.py

# Unit-тесты (W4-E2)
tests/unit/test_state_machine.py

# Reports
reports/wave4-results-2026-XX.csv
```

### 6.2. MODIFY

```
# Top-3 shell → thin facades
core/internal/bootstrap/deploy-modules.sh                       # 1664 → <100 LOC
core/internal/bootstrap/converge.sh                             # 1149 → <150 LOC
core/internal/bootstrap/node-lifecycle.sh                       # 1301 → <200 LOC

# Документация
core/internal/bootstrap/AGENTS.md                               # +раздел "Python-модули декомпозиции"
AGENTS.md                                                       # +TRAP[DECISION] Strangler-Fig canonical

# Registry
core/entrypoint-manifest.yaml                                   # +новые Python entry points

# Tracking
reports/inline-python3-map-2026-07-21.csv                       # W4-done маркировка
```

---

## 7. Risk Register

| ID              | Risk                                                              | L  | I  | Mitigation                                                                                                                                                    | Эпик       |
|-----------------|-------------------------------------------------------------------|----|----|---------------------------------------------------------------------------------------------------------------------------------------------------------------|------------|
| **R-RISK-5**    | Strangler-extraction ломает runtime скриптов                      | M  | H  | W4-E5 (уже выполнена): edge-case regression-тесты. Staging-тест после каждого эпика. Revert: `git revert <merge-commit>`.                                    | W4-E1/E2/E3 |
| **PGM-R2**      | Extraction ломает production-deploy                               | M  | H  | Один скрипт за раз. Regression-тесты ДО extraction (W4-E5 ready). Staging-gate перед каждым PR.                                                              | All        |
| **R-RISK-NEW-1**| state_machine.py переносит locking/env-setup неидиоматично        | L  | M  | Shell-фасад сохраняет orchestration (flock, env exports). Python — только transition logic.                                                                   | W4-E2      |
| **R-RISK-NEW-2**| docker_orchestrator.py ломает parallel deploy                     | M  | M  | Shell-level parallelism сохранён (deploy_docker_group в shell вызывает python3 в цикле с `&`). Python — per-module deploy.                                    | W4-E1      |
| **R-RISK-NEW-3**| reconcile exit-code mapping нарушен                                | L  | H  | Python возвращает exit_code в JSON; shell маппит JSON→exit. Unit-тест явно проверяет {0,1,2}.                                                                | W4-E3      |
| **R-RISK-NEW-4**| Inline python3 не полностью мигрирован                            | L  | L  | `rg "python3 -c\|<<PYEOF"` → 0 — явный AC. Pre-commit hook (Wave 1) блокирует новые.                                                                          | W4-E1/E2/E3 |

**Mitigation, уже на месте:**
- W4-E5: 51 regression-тест (включая 18 edge-case) — страховка R-RISK-5/PGM-R2
- W4-E4: CI gate `test_gate_makefile_targets.py` проверяет `make -n` для всех target'ов
- Wave 1/2: `yaml_query.py`, `ssh.sh`, `audit_logging.sh` — стабильные фасады, не затрагиваются

---

## 8. Метрики успеха

### 8.1. Количественные

| Метрика                                                | Baseline (W4-E5, 2026-07-22) | Цель (конец W4-E2)             |
|--------------------------------------------------------|------------------------------|--------------------------------|
| Shell LOC топ-3 (deploy + converge + lifecycle)        | 4114                         | ~450 (<100 + <150 + <200)      |
| Python LOC (new decomposition modules)                 | 0                            | ~2000-3000                     |
| Inline python3-блоки в топ-3                           | 31 (22 `-c` + 9 PYEOF)       | 0                              |
| Unit-тесты для новых Python-модулей                    | 0                            | 7 файлов                       |
| Regression-тесты (существующие)                        | 51                           | 51 (green, no regress)         |
| `make gate MODE=fast` time                             | baseline ~3.82s (avg)        | ≤ baseline                     |

### 8.2. Качественные

- Каждый топ-3 скрипт — тонкий shell-фасад + типизированный Python с unit-тестами.
- Strangler-Fig паттерн зафиксирован в TRAP[DECISION] как canonical.
- Staging-деплой проходит без hang (bootstrap init + node-update + converge).
- Все 31 inline python3-блока мигрированы в тестируемые Python-функции.

---

## 9. Effort estimation и последовательность

| Эпик       | Описание                              | Effort      | Зависимости                  | Статус     |
|------------|---------------------------------------|-------------|------------------------------|------------|
| **W4-E5**  | Regression baseline (edge-cases)      | ~1 нед      | —                            | ✅ DONE     |
| **W4-E4**  | Makefile include-split                | ~0.5-1 нед  | —                            | ✅ DONE     |
| **W4-E1**  | deploy-modules.sh (5 модулей)         | ~2-3 нед    | W4-E5                        | ⏳ REMAINING |
| **W4-E3**  | converge.sh → reconciler.py           | ~1.5-2 нед  | W4-E1 (опыт Strangler)       | ⏳ REMAINING |
| **W4-E2**  | node-lifecycle.sh → state_machine.py  | ~2-2.5 нед  | W4-E3                        | ⏳ REMAINING |
| **W4-E6**  | Inline python3 завершение             | ~0 (неявный)| Побочный эффект E1/E2/E3     | ⏳ REMAINING |
| **Осталось**|                                      | **~5.5-7.5 нед** |                           |             |

**Последовательность (оставшаяся):**
```
W4-E1 (deploy-modules) ──► W4-E3 (converge) ──► W4-E2 (node-lifecycle)
W4-E6 (inline sweep) ── побочный эффект на каждом этапе
```

**Production-релизы (3 PR):**
1. **PR-1:** W4-E1 (deploy-modules decomposition)
2. **PR-2:** W4-E3 (converge decomposition)
3. **PR-3:** W4-E2 (node-lifecycle decomposition)

Каждый PR проходит staging-gate перед merge.

---

## 10. Anti-goals

- ❌ Big-bang rewrite топ-3 в один PR.
- ❌ Параллельная миграция скриптов (бриф §6.3.3 — один за раз).
- ❌ Перенос locking/env-setup в Python (shell — для orchestration).
- ❌ Миграция стабильных lib (logging.sh, paths.sh, ssh.sh, args.sh, audit_logging.sh).
- ❌ Добавление новых фич в ходе декомпозиции (pure refactor).
- ❌ Пересоздание Makefile split или regression baseline (уже сделаны).
- ❌ Transactional deploy (Wave 5 W5-E1 scope).
- ❌ Converge K8s-parity (Wave 5 W5-E2/E3/E4/E5 scope).

---

## 11. Staging-gate (перед каждым PR merge)

```bash
# На тестовой ноде:
make bootstrap-node NODE=<test> --mode init    # W4-E2 validation
make node-update NODE=<test>                   # W4-E2 incremental
make converge NODE=<test>                      # W4-E3 validation
make project-list NODE=<test>                  # lib/ssh.sh regression
make project-status NAME=<p> NODE=<test>       # lib/ssh.sh regression

# Все 5 проходят без hang
```

---

## 12. Post-Wave 4 замеры

`reports/wave4-results-2026-XX.csv`:
- Shell LOC топ-3 (цель: ~450)
- Python LOC новых модулей (цель: ~2-3K)
- Inline python3 count в топ-3 (цель: 0)
- `make gate MODE=fast` time (сравнение с baseline W4-E5: ~3.82s avg)
- Unit-тест execution time (новые 7 файлов)

---

## 13. Документация (после завершения)

- `core/internal/bootstrap/AGENTS.md` — раздел "Python-модули декомпозиции" с картой shell→Python.
- Root `AGENTS.md` — TRAP[DECISION] Strangler-Fig canonical.
- Бриф 027 §6 — отметить Wave 4 как IMPLEMENTED.

---

## 14. Делегирование

```
035-wave4-strangler-top3/
├── 01-Brief (implicit — бриф 027 §6)
├── 02-DevPlan.md (оригинальный полный DevPlan — reference)
├── 03-DevPlan-final.md (этот файл — оставшиеся 4 эпика)
├── 03-VerificationReport.md (после QA)
└── 04-VerificationReport-fixes.md (если нужны fix-волны)
```

$END_DEVPLAN
