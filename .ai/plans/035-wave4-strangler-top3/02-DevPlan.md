# 035-DevPlan: Wave 4 — Strangler Fig на топ-3 скриптах

**Wave:** 4 (Strangler Fig топ-3) программы `027-architecture-modernization-program`
**Source brief:** `.ai/plans/027-architecture-modernization-program/01-Brief.md` §6 (Wave 4)
**Source analysis:** `reports/architecture-analysis-2026-07-21.md` §4.1 (Option B — Strangler-Fig) + §4.2 (Makefile include-split)
**Prior waves:** Wave 1 (`.ai/plans/028-wave1-immediate/`) — IMPLEMENTED; Wave 2 (`.ai/plans/029-wave2-dangerous/`) — IMPLEMENTED; Wave 3 (`.ai/plans/033-wave3-contract-d5/`) — IMPLEMENTED
**Pre-flight verification (principle 9 — Read before Act):** выполнена 2026-07-21, см. §1.4
**Operator decisions (2026-07-21):** локация новых Python-модулей → `core/internal/bootstrap/deploy/` (и аналогичные подпапки под converge/lifecycle); порядок миграции → сохранён бриф §6.3: deploy-modules → converge → node-lifecycle.

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Декомпозировать top-3 shell-монолита (deploy-modules.sh=1664 строк, node-lifecycle.sh=1301 строк, converge.sh=1149 строк = 4114 суммарно) по принципу Strangler-Fig: бизнес-логика → типизированные Python-модули с unit-тестами, shell остаётся тонким фасадом <100-200 строк. Разбить root Makefile (747 строк) через `include`-механизм на тематические `.mk` файлы. Завершить консолидацию inline `python3 -c` (старт Wave 1 W1-E7): все inline-вызовы в топ-3 скриптах мигрированы. Закрыть проблемы P03 (top-3 monolith), P09 (deploy-modules SRP), P12 (inline python3 завершение) матрицы.
DESCRIPTION:           Шесть эпиков, выполняемых строго последовательно (W4-E1 → W4-E3 → W4-E2), с параллельным W4-E4 (Makefile split) и завершающим W4-E6 (inline python3 sweep):
                       (W4-E5) Regression baseline — ПЕРВЫМ: расширить существующие тесты (test_deploy_modules.py=10, test_bootstrap_auto.py=12, test_converge_exit.py=5) edge-case покрытием (parallel deploy failure, orphan reconciliation, checkpoint resume, state-machine transitions) ДО extraction. Это страховка R-RISK-5.
                       (W4-E1) deploy-modules.sh декомпозиция — 1664 строк → 5 Python-модулей в `core/internal/bootstrap/deploy/`: `docker_orchestrator.py`, `sudoers_generator.py`, `context_overlay.py`, `secrets_validator.py`, `orphan_reconciler.py`. Shell-фасад <100 строк.
                       (W4-E3) converge.sh reconcile-loop → Python — 1149 строк → `core/internal/bootstrap/converge/reconciler.py`. Shell-фасад <150 строк.
                       (W4-E2) node-lifecycle.sh state-machine → Python — 1301 строк → `core/internal/bootstrap/lifecycle/state_machine.py` (17 step_* transitions). Shell-фасад <200 строк.
                       (W4-E4) Makefile include-split — 747 строк → `makefiles/{bootstrap,deploy,scaffold,modules,ci,helpers}.mk`. Root Makefile <150 строк.
                       (W4-E6) Inline python3 завершение — оставшиеся 14 `python3 -c` + 9 `<<PYEOF` в топ-3 скриптах мигрируются в Python-модули в ходе W4-E1/E2/E3 (без отдельной работы).
                       Принципы Strangler (бриф §6.3): (1) сначала тесты, потом extraction; (2) тонкий shell-фасад сохраняет совместимость с Makefile/CI; (3) один скрипт за раз.
RATIONALE:             Анализ архитектуры (`reports/architecture-analysis-2026-07-21.md` §3.1, §4.1), верифицированный против кодовой базы 2026-07-21, показал: top-3 shell-scripts = 4114 строк (1664+1301+1149, дрейф +35 от брифа 3979), каждый смешивает 3-5 ответственностей; 14 inline `python3 -c` + 9 `<<PYEOF`-блоков в топ-3 = ~23% всех оставшихся inline-вызовов в платформе. Оператор выбрал Strangler-Fig (Option B §4.1, score 8/10) с двухуровневым триггером (бриф §1.1). Wave 1 W1-E7 дал `core/internal/scripts/yaml_query.py` + `core/lib/yaml_read.sh` фасад — доказательство паттерна. `_topo_sort.py` (120 строк, Kahn's algorithm) уже используется deploy-modules.sh → референс для нового поколения Python-модулей. Тестовая база для regression существует (27 тестов по смежным темам, см. §1.4), но требует расширения edge-case'ами ДО extraction (R-RISK-5). Локация `core/internal/bootstrap/deploy/` (а не брифовский `core/internal/deploy/`, который занят project-delivery подсистемой) — решение оператора 2026-07-21. Big-bang rewrite отклонён (Option A §4.1); параллельная миграция отклонена (PGM-R2: каждый скрипт отдельно — staging-тест перед merge).
ACCEPTANCE_CRITERIA:
  AC-1 (W4-E5 Regression baseline, ПЕРВЫМ):
    a. `tests/test_deploy_modules.py` расширен edge-case тестами: parallel deploy failure (1 контейнер из группы падает — поведение остальных), orphan reconciliation (висящие контейнеры без module.yaml), checkpoint resume (.done-маркеры + content-hash), batch sudoers generation (детерминированность). Минимум +6 новых тестов (total ≥16).
    b. `tests/test_node_lifecycle_static.py` расширен: state-machine step-transitions (init vs update mode), step-skip logic при unchanged content-hash, step-warn/error collection, TOR-conditional branch. Минимум +5 новых тестов (total ≥17).
    c. `tests/test_converge_exit.py` расширен: drift detection (perms, audit_log, networks, projects), reconcile idempotency (повторный converge = no-op), stub-detection edge cases. Минимум +4 новых тестов (total ≥9).
    d. Все новые тесты遵循 Test Honesty Rules R1-R5: имеют assert (не `assert True`), падают на конструируемом нарушении, используют tmp_path, эмитят IMP:9-10 логи (LDD Telemetry).
    e. Baseline-замер: `reports/wave4-baseline-2026-07.csv` — время выполнения `pytest tests/test_deploy_modules.py tests/test_node_lifecycle_static.py tests/test_converge_exit.py` до extraction (3 повтора).

  AC-2 (W4-E1 deploy-modules.sh декомпозиция):
    a. `core/internal/bootstrap/deploy/` создан с 5 Python-модулями:
       - `docker_orchestrator.py` — функции deploy_docker_group, deploy_docker_module, _pre_pull_images, wait_for_readiness, run_healthcheck, _check_image_exists (~450-550 строк)
       - `sudoers_generator.py` — generate_module_sudoers, _render_sudoers_rules, _batch_generate_sudoers (~200-300 строк)
       - `context_overlay.py` — ensure_context_repo (git clone/pull с кешированием) (~150-200 строк)
       - `secrets_validator.py` — _check_env_requires, _validate_secret_charsets, _get_module_severity, _batch_module_metadata (~250-350 строк)
       - `orphan_reconciler.py` — _batch_orphan_reconciliation (~150-200 строк)
    b. Каждый Python-модуль имеет MODULE_CONTRACT region с @purpose, @scope, @invariants, @rationale; GREP_SUMMARY + STRUCTURE; LDD-логи [IMP:7-10] в каждой не-trivial функции.
    c. `core/internal/bootstrap/deploy-modules.sh` урезан до <100 строк: arg parsing (через lib/args.sh из Wave 1), делегирование к `python3 core/internal/bootstrap/deploy/<module>.py`, прокидывание exit code. Сохранена бинарная совместимость: Makefile/CI вызывают тот же путь.
    d. `tests/test_deploy_modules.py` green ПОСЛЕ extraction (regression). Новые unit-тесты для каждого Python-модуля: `tests/unit/test_docker_orchestrator.py`, `tests/unit/test_sudoers_generator.py`, `tests/unit/test_context_overlay.py`, `tests/unit/test_secrets_validator.py`, `tests/unit/test_orphan_reconciler.py`.
    e. `wc -l core/internal/bootstrap/deploy-modules.sh` < 100.
    f. В deploy-modules.sh НЕТ inline `python3 -c` и `<<PYEOF` (rg → 0 matches).

  AC-3 (W4-E3 converge.sh reconcile-loop → Python):
    a. `core/internal/bootstrap/converge/` создан. `reconciler.py` содержит: reconcile_perms, reconcile_audit_log, reconcile_projects, reconcile_networks, detect_hosts_drift, verify_vhosts, report_init/add/emit (~500-700 строк).
    b. `_is_stub()` helper тоже мигрирован (используется reconcile_projects).
    c. `core/internal/bootstrap/converge.sh` урезан до <150 строк: setup_environment, acquire_lock, делегирование к `python3 core/internal/bootstrap/converge/reconciler.py --node-yaml <path> --report-file <path>`, печать report, exit code mapping (0=clean, 1=warnings, 2=errors — сохранена семантика test_converge_exit.py).
    d. `tests/test_converge_exit.py` green ПОСЛЕ extraction. `tests/unit/test_reconciler.py` покрывает каждый reconcile_* метод.
    e. `wc -l core/internal/bootstrap/converge.sh` < 150.
    f. В converge.sh НЕТ inline `python3 -c` и `<<PYEOF` (rg → 0 matches).

  AC-4 (W4-E2 node-lifecycle.sh state-machine → Python):
    a. `core/internal/bootstrap/lifecycle/` создан. `state_machine.py` содержит: явная state-machine (JSON state-file `/var/lib/platform/.bootstrap/state.json`), 17 step_* transitions с pre/post-условиями, checkpoint-resume logic, TOR-conditional, update vs init mode dispatch (~600-800 строк).
    b. Вспомогательные helpers `validate_bootstrap_env`, `_step_hash`, `_step_install_acme`, `_step_secrets_init` — тоже мигрированы (если содержат логику, не чистый subprocess).
    c. `core/internal/bootstrap/node-lifecycle.sh` урезан до <200 строк: arg parsing (--mode init/update), state.json load, вызов `python3 core/internal/bootstrap/lifecycle/state_machine.py`, обработка результата.
    d. `tests/test_node_lifecycle_static.py` + `tests/test_bootstrap_auto.py` green ПОСЛЕ extraction. `tests/unit/test_state_machine.py` покрывает: init vs update flow, step-skip на unchanged hash, step-warn/error propagation, checkpoint resume.
    e. `wc -l core/internal/bootstrap/node-lifecycle.sh` < 200.
    f. В node-lifecycle.sh НЕТ inline `python3 -c` и `<<PYEOF` (rg → 0 matches).

  AC-5 (W4-E4 Makefile include-split):
    a. `makefiles/` создан с 6 файлами:
       - `makefiles/bootstrap.mk` — bootstrap-node, node-update, converge, render-vhosts
       - `makefiles/deploy.mk` — deploy, deploy-project, context-promote, hermes-build-*, hermes-push-l1
       - `makefiles/scaffold.mk` — new-project, new-context, adopt-project, remove-project, project-sync-env, project-list, project-status
       - `makefiles/modules.mk` — up, down, restart, status, healthcheck, backup, restore, discover-modules, validate-modules
       - `makefiles/ci.mk` — test, gate, validate, lint, check-file-lines, pre-commit-*, scripts-audit, audit, secrets-unlock
       - `makefiles/helpers.mk` — venv, templates-check/render, dev-certs, provision, test-inventory-sync, help
    b. Root `Makefile` содержит только: переменные (VENV, ROOT, etc.), `include makefiles/*.mk`, общие macros. `wc -l Makefile` < 150.
    c. `make -n <target>` работает для КАЖДОГО `.PHONY` target'а (CI gate: `tests/gates/test_gate_makefile_targets.py` — новый, запускает `make -n` для всех 43 target'ов).
    d. Tab-sensitive parsing сохранён: все recipe-строки используют TAB (CI gate проверяет).

  AC-6 (W4-E6 Inline python3 завершение, НЕЯВНЫЙ — выполняется в ходе E1/E2/E3):
    a. `rg "python3 -c" core/internal/bootstrap/deploy-modules.sh core/internal/bootstrap/converge.sh core/internal/bootstrap/node-lifecycle.sh` → 0 matches.
    b. `rg "<<PYEOF|<<'PYEOF'" core/internal/bootstrap/deploy-modules.sh core/internal/bootstrap/converge.sh core/internal/bootstrap/node-lifecycle.sh` → 0 matches.
    c. Inline-вызовы мигрированы в Python-модули из AC-2/3/4 (значения инлайн-логики стали методами с тестами).
    d. `reports/inline-python3-map-2026-07-21.csv` обновлён: строки для топ-3 скриптов помечены `consolidation_wave=W4-done`.

  AC-7 (Cross-cutting):
    a. `make gate MODE=fast` — зелёный после ВСЕХ изменений.
    b. `make gate MODE=full` — зелёный (с учётом известных macOS-overlay failures).
    c. Все новые Python-файлы проходят `ruff check` + `ruff format --check` без ошибок.
    d. `.pre-commit-config.yaml` hook `no-new-inline-python3` (из Wave 1) НЕ ломается на новых shell-фасадах.
    e. `core/entrypoint-manifest.yaml` обновлён: новые Python-скрипты зарегистрированы, если вызываются из Makefile/CI напрямую.
    f. `core/internal/bootstrap/AGENTS.md` обновлён: новый раздел "Python-модули декомпозиции" с картой shell-фасад → Python-модуль.
    g. TRAP[DECISION] в root `AGENTS.md` (после §Языковая политика): фиксация Strangler-Fig паттерна как canonical для будущих декомпозиций (референс — топ-3).

  AC-8 (Production-релиз Wave 4):
    a. Staging-деплой на тестовой ноде: `make bootstrap-node NODE=<test> --mode init` (fresh) + `make node-update NODE=<test>` (incremental) + `make converge NODE=<test>` — все 3 проходят без hang.
    b. Audit-trail (из Wave 2 W2-E3) фиксирует выполнение entrypoints.
    c. Замер post-Wave 4 метрик: `reports/wave4-results-2026-XX.csv` — Python LOC (цель: +2-3K), shell LOC в топ-3 (цель: 4114 → ~450), inline python3 count в топ-3 (цель: 23 → 0), `make gate MODE=fast` time (сравнение с baseline Wave 1 W1-E8).
IMPLEMENTS:            Brief 027 §6 (Wave 4 эпики W4-E1…W4-E6). Report 2026-07-21 §3.1 (top-3 monolith), §4.1 (Option B Strangler-Fig), §4.2 (Makefile include-split). AGENTS.md invariants 1 (Makefile-фасад — сохранён после split), 4 (канонические AGENTS.md — обновляется core/internal/bootstrap/AGENTS.md). Principles 6 (Small Simple Blocks через Strangler), 8 (AI-First Architecture — модульные границы по бизнес-ответственности), 9 (Read before Act — pre-flight §1.4 + regression-first W4-E5). Skills: doc-protocols (этот DevPlan), arch-patterns (lazy import, typed contracts), arch-forensics (исходный анализ).
IMPACTS:               **New Python (~2-3K LOC):** `core/internal/bootstrap/deploy/{docker_orchestrator,sudoers_generator,context_overlay,secrets_validator,orphan_reconciler}.py`, `core/internal/bootstrap/converge/reconciler.py`, `core/internal/bootstrap/lifecycle/state_machine.py`. **Modified shell (top-3 → thin facades, ~4114 → ~450 LOC):** `core/internal/bootstrap/deploy-modules.sh` (<100), `core/internal/bootstrap/converge.sh` (<150), `core/internal/bootstrap/node-lifecycle.sh` (<200). **Makefile split:** root `Makefile` (747 → <150), `makefiles/{bootstrap,deploy,scaffold,modules,ci,helpers}.mk` (новые). **New tests (~15-20 unit + regression):** `tests/unit/test_{docker_orchestrator,sudoers_generator,context_overlay,secrets_validator,orphan_reconciler,reconciler,state_machine}.py`, расширение `tests/test_{deploy_modules,node_lifecycle_static,converge_exit,bootstrap_auto}.py`, `tests/gates/test_gate_makefile_targets.py`. **Docs:** `core/internal/bootstrap/AGENTS.md` (раздел "Python-модули декомпозиции"), root `AGENTS.md` (TRAP[DECISION] Strangler-Fig canonical), `reports/wave4-{baseline,results}-2026-XX.csv`. **Registry:** `core/entrypoint-manifest.yaml` (+новые Python entry points если вызваны напрямую). **Tracking:** `reports/inline-python3-map-2026-07-21.csv` обновлён.
REQUIRES:              Чистый working tree (проверить `git status`). Прочитанные артефакты: `reports/architecture-analysis-2026-07-21.md` §3.1+§4.1+§4.2, `.ai/plans/027-architecture-modernization-program/01-Brief.md` §6, `core/internal/bootstrap/AGENTS.md`, существующие `core/internal/bootstrap/_topo_sort.py` (референс Python-паттерна в bootstrap), `core/internal/scripts/yaml_query.py` (референс Wave 1 консолидации), `core/lib/{ssh.sh,args.sh,audit_logging.sh,logging.sh}` (Wave 1/2 фасады). Зависимости: Wave 1 (yaml_query.py, gate_helpers.py, args.sh, honest tests), Wave 2 (ssh.sh для staging-теста, audit_logging.sh для TRAP), Wave 3 (validate_module_yaml.py — используется deploy-modules). Python 3.10+, pyyaml, jsonschema (в deps). Bash >= 4.0. Перед стартом: архитектор ОБЯЗАН пройти pre-flight §1.4 (verify actual state vs бриф). Staging-ноде доступна для W4-E1/E2/E3 production-релиза. Operator decisions (2026-07-21): локация `core/internal/bootstrap/{deploy,converge,lifecycle}/`, порядок миграции deploy→converge→lifecycle (бриф §6.3).
$END_ARTIFACT_CONTRACT

---

## $DOCUMENT_PLAN

```
$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL Зафиксировать pre-flight verification против фактического состояния кода => GOAL_PREFLIGHT
- GOAL Описать регрессионную_baseline (W4-E5 первым) и страхующий регресс-сет => GOAL_REGRESS
- GOAL Описать декомпозицию deploy-modules.sh по 5 ответственностям с Draft Code Graph => GOAL_DEPLOY
- GOAL Описать декомпозицию converge.sh (reconciler.py) с сохранением exit-code семантики => GOAL_CONVERGE
- GOAL Описать декомпозицию node-lifecycle.sh (state-machine) с 17 step-transitions => GOAL_LIFECYCLE
- GOAL Описать Makefile include-split с CI-gate на `make -n` => GOAL_MAKEFILE
- GOAL Зафиксировать File Manifest (все CREATE/MODIFY) => GOAL_MANIFEST
- GOAL Определить Acceptance Criteria + verifiable commands => GOAL_AC
- GOAL Зафиксировать risks (R-RISK-4/5, PGM-R2) и mitigation => GOAL_RISK
- GOAL Оценить effort по эпикам и последовательность выполнения => GOAL_EFFORT
**SECTION_USE_CASES:**
- USE_CASE Разработчик добавляет новый docker-модуль → docker_orchestrator.deploy_docker_module() — типизированный контракт, unit-тестируемый => UC_NEW_MODULE
- USE_CASE CI запускает `make gate MODE=fast` → makefile split не ломает существующие target'ы, `make -n` валиден для всех .PHONY => UC_CI_GATE
- USE_CASE Staging-деплой: bootstrap-node init → node-update → converge — все 3 проходят (regression W4-E1/E2/E3) => UC_STAGING
- USE_CASE Баг в orphan-reconciliation → unit-тест test_orphan_reconciler локализует, без subprocess.run(["bash", ...]) => UC_DEBUG
- USE_CASE Новый step в lifecycle (например, step_18_backup) → state_machine.register_step() с pre/post-условиями, JSON state-file => UC_NEW_STEP
$END_DOCUMENT_PLAN
```

---

## 1. Контекст и pre-flight (Read before Act)

### 1.1. Цель Wave 4

Превратить top-3 shell-монолита (4114 строк) в тонкие shell-фасады, делегирующие к типизированным Python-модулям с unit-тестами. Закрыть 3 проблемы матрицы:

| ID  | Категория        | Sev     | Текущее состояние (verified 2026-07-21)                                                                                                                              |
|-----|------------------|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| P03 | ARCHITECTURE     | 🔴 CRIT | top-3 = 4114 строк (deploy-modules=1664, node-lifecycle=1301, converge=1149). Дрейф +35 от брифа (3979). Каждый смешивает 3-5 ответственностей.                       |
| P09 | ARCHITECTURE     | 🟠 HIGH | deploy-modules.sh содержит: docker-orchestration + sudoers-generation + context-overlay + secrets-validation + orphan-reconciliation (5 ответственностей в одном файле). |
| P12 | EXTENSIBILITY    | 🟡 MED  | В топ-3 скриптах: 14 `python3 -c` + 9 `<<PYEOF` = 23 inline-блока (deploy-modules=10+1, converge=11+5, node-lifecycle=5+3). Wave 1 W1-E7 дал yaml_query.py, остальное — Wave 4. |

### 1.2. Operator decisions (2026-07-21)

| Решение                                           | Выбор                                                                                       | Обоснование                                                                                  |
|---------------------------------------------------|---------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------|
| Локация новых Python-модулей декомпозиции         | `core/internal/bootstrap/{deploy,converge,lifecycle}/`                                      | Физически рядом с shell-фасадами. Не конфликтует с занятым `core/internal/deploy/` (project delivery). |
| Порядок миграции топ-3                            | deploy-modules → converge → node-lifecycle (бриф §6.3)                                      | Самый сложный (deploy, 5 модулей) первым на свежую голову; state-machine (lifecycle) последним — чёткие seam'ы (17 step_*). |
| Параллелизация                                    | Один скрипт за раз (бриф §6.3.3)                                                            | PGM-R2 mitigation: staging-тест перед merge каждого.                                          |
| Makefile split timing                             | Параллельно W4-E1 (независимый эпик)                                                        | Не зависит от Python-декомпозиции, не блокирует.                                              |

### 1.3. Референсные Python-паттерны (уже в продакшене)

Эти файлы — canonical reference для новых модулей декомпозиции (структура, MODULE_CONTRACT, LDD-логи):

| Файл                                           | LOC | Паттерн                                                                            |
|------------------------------------------------|-----|------------------------------------------------------------------------------------|
| `core/internal/bootstrap/_topo_sort.py`        | 120 | Python-вызов из shell (deploy-modules.sh), argparse → JSON, MODULE_CONTRACT full   |
| `core/internal/bootstrap/discover_modules.py`  | 295 | Make target → Python, regex-based compose update, idempotent                       |
| `core/internal/scripts/yaml_query.py`          | 201 | Typed API (yaml_get/yaml_query/json_get), unit-тесты в tests/test_yaml_query.py    |
| `core/internal/scripts/validate_module_yaml.py`| 664 | jsonschema-валидатор, CI gate через Makefile, D5-контракт                           |
| `core/internal/template_engine.py`             | 716 | Самый крупный production-Python, MULTIPLE_CONTRACTS                                |

### 1.4. Pre-flight verification (выполнено 2026-07-21)

| Проверка                                                                                          | Результат                                                                                                                  |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Wave 1 implemented (yaml_query.py, args.sh, gate_helpers.py, honesty.py, _negative tests)         | ✅ verified: все файлы существуют. `tests/helpers/gate_helpers.py`=78 LOC, `core/internal/scripts/yaml_query.py`=201 LOC.   |
| Wave 2 implemented (ssh.sh, setup-platform composite, audit_logging.sh)                           | ✅ verified: `core/lib/ssh.sh`=199 LOC, `.github/actions/setup-platform/action.yml` существует, `core/lib/audit_logging.sh`=134 LOC. |
| Wave 3 implemented (validate_module_yaml.py, validate-modules target, ${VAR:?} enforcement)       | ✅ verified: `core/internal/scripts/validate_module_yaml.py`=664 LOC, `Makefile` имеет `validate-modules`, git log подтверждает (`eb55c13 feat(wave3): ...`). |
| deploy-modules.sh line count                                                                      | ✅ 1664 (бриф: 1633, дрейф +31)                                                                                            |
| node-lifecycle.sh line count                                                                      | ✅ 1301 (бриф: 1297, дрейф +4)                                                                                             |
| converge.sh line count                                                                            | ✅ 1149 (совпадает с брифом)                                                                                                |
| Makefile line count                                                                               | ✅ 747 (бриф: не указан явно; цель <150 после split)                                                                       |
| Inline python3 в топ-3                                                                            | ✅ deploy-modules: 10 `python3 -c` + 1 `<<PYEOF`; converge: 11 `python3 -c` + 5 `<<PYEOF`; node-lifecycle: 5 `python3 -c` + 3 `<<PYEOF` = 23 суммарно. |
| Существующий regression-сет для топ-3                                                             | ✅ `tests/test_deploy_modules.py`=10 тестов, `tests/test_bootstrap_auto.py`=12, `tests/test_converge_exit.py`=5, `tests/test_node_lifecycle_static.py` (статический анализ). |
| Функциональная карта deploy-modules.sh                                                            | ✅ 23 функции (см. §3.1). 5 ответственностей идентифицированы: docker, sudoers, context-overlay, secrets, orphan.            |
| Функциональная карта converge.sh                                                                  | ✅ 13 функций: reconcile_{perms,audit_log,projects,networks}, detect_hosts_drift, verify_vhosts, report_{init,add,emit}, _is_stub. |
| Функциональная карта node-lifecycle.sh                                                            | ✅ 35 функций: 17 step_*, update_step_*, helpers (_step_hash, _step_install_acme, _step_secrets_init), main.                  |
| `_topo_sort.py` — референс Python-in-bootstrap                                                    | ✅ используется deploy-modules.sh (subprocess), MODULE_CONTRACT, 120 LOC.                                                   |
| `core/internal/deploy/` занят                                                                     | ⚠️ confirmed: содержит deploy-project.sh, reconcile-projects.sh (project delivery). Решение оператора: использовать `core/internal/bootstrap/deploy/`. |
| `core/internal/converge/`, `core/internal/bootstrap/lifecycle/`                                   | ✅ не существуют, готовы к созданию.                                                                                        |
| `.pre-commit-config.yaml` hook `no-new-inline-python3` (Wave 1)                                   | ✅ существует, проверяет `core/`. Shell-фасады после декомпозиции не должны добавлять новые inline — hook остаётся gate'ом. |
| Staging-нода доступна для production-релиза                                                       | ⏳ требует подтверждения оператора перед стартом W4-E1 staging-фазы.                                                        |

---

## 2. SUPERPOSITION: развилки, требующие фиксации

### 2.1. Стратегия extraction (in-place vs branch-per-script)

```
## SUPERPOSITION: extraction strategy

### Option A: "In-place, sequential merge" [score: 7/10]
Approach: Каждый эпик (W4-E1, W4-E3, W4-E2) — отдельная feature-ветка от main,
  merge в main после staging-теста. Промежуточные коммиты в ветке.
Trade-offs: +Audit-trail чистый (один merge-commit на эпик). +Возможность revert
  эпика независимо. −Дольше по времени (staging между эпиками). −Main остаётся
  stable, но 3 merge-commit'а утяжеляют history.
Best when: Команда хочет чистый audit-trail и независимый revert.

### Option B: "In-place, continuous commits" [score: 8/10] ★ RECOMMENDED
Approach: Одна ветка `wave4-strangler` на всю волну. Коммиты по подзадачам
  (extract docker_orchestrator, extract sudoers, ...). Staging-тест после каждого
  эпика (merge в main только после зелёного staging). Merge в main = один PR
  на всю волну ИЛИ 3 PR (по одному на топ-3 скрипт).
Trade-offs: +Низкий overhead по git-операциям. +Code review по всему изменению
  (видна целостность декомпозиции). −Один revert откатывает всю волну (mitigation:
  PR разбит на 3 — по одному на топ-3 скрипт).
Best when: Доверие к regression-тестам (W4-E5 первым), быстрая обратная связь.

### Option C: "Big-bang в одну ветку, один PR" [score: 3/10]
Approach: Вся Wave 4 в один PR, merge после полного завершения.
Trade-offs: +Простой git-flow. −Огромный PR (4114 → 450 строк shell + 2-3K Python),
  неревьюабельный. −Высокий PGM-R2 риск (один revert = вся волна). −Длинная ветка
  → конфликты с main. ОТКЛОНЯЕТСЯ (соответствует anti-goal §12 брифа).
```

**Выбор:** Option B. Три PR (по одному на топ-3 скрипт: deploy-modules → converge → node-lifecycle). W4-E4 (Makefile) — отдельный 4-й PR, может идти параллельно.

### 2.2. Степень тонкости shell-фасада (pure delegation vs retain orchestration)

```
## SUPERPOSITION: shell-facade thickness

### Option A: "Ultra-thin: arg parsing + 1 python call" [score: 6/10]
Approach: Shell-фасад делает ТОЛЬКО: source lib/args.sh, parse_args,
  exec python3 <module>.py "$@". Вся логика (включая setup_environment,
  acquire_lock для converge) — в Python.
Trade-offs: +Минимальный shell. −Перенос lock-логики в Python (fcntl) —
  неидиоматично для platform (locking历来 in shell). −Высокий риск regress
  в edge-cases.

### Option B: "Thin: orchestration + delegation" [score: 9/10] ★ RECOMMENDED
Approach: Shell сохраняет: arg parsing, environment setup (paths, exports),
  lock acquisition (flock), высокоуровневый flow control (init vs update mode
  dispatch). Python получает: бизнес-логику, вычисления, валидацию, генерацию.
Trade-offs: +Соответствует AGENTS.md §Языковая политика п.2 (bash для
  orchestration, Python для logic). +Locking и env-setup — идиоматичный shell.
  +Меньше regression-риск. −Shell-фасад чуть больше (100-200 строк vs 50).

### Option C: "Thick: retain most logic in shell" [score: 2/10]
Approach: Shell сохраняет всю логику, Python — только утилиты.
Trade-offs: +Минимальные изменения. −НЕ достигает цели Wave 4 (P03 не закрыт).
  ОТКЛОНЯЕТСЯ.
```

**Выбор:** Option B. Shell-фасад = orchestration + delegation. Это соответствует AGENTS.md §Языковая политика п.2: bash для entrypoints/orchestration, Python для logic.

---

## 3. W4-E1: deploy-modules.sh декомпозиция (ПЕРВЫЙ ЭПИК)

### 3.1. Функциональная карта (verified 2026-07-21)

deploy-modules.sh (1664 строки) содержит 23 функции, сгруппированные по 5 ответственностям:

| Ответственность       | Python-модуль              | Shell-функции                                                                                                                       | Inline python3 / PYEOF                              |
|-----------------------|----------------------------|-------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------|
| docker-orchestration  | `docker_orchestrator.py`   | deploy_docker_module, deploy_docker_group, _pre_pull_images, _check_image_exists, wait_for_readiness, run_healthcheck              | 1 PYEOF (parse modules from node.yaml, строка 651)  |
| sudoers-generation    | `sudoers_generator.py`     | generate_module_sudoers, _render_sudoers_rules, _batch_generate_sudoers                                                             | —                                                   |
| context-overlay       | `context_overlay.py`       | ensure_context_repo                                                                                                                 | 1 `python3 -c` (context_repo_url, строка 250)       |
| secrets-validation    | `secrets_validator.py`     | _check_env_requires, _validate_secret_charsets, _get_module_severity, _batch_module_metadata, _expand_transitive_deps, parse_modules_from_node_yaml, detect_install_type | 8 `python3 -c` (строки 48, 687, 707, 723, 763, 821, 862) |
| orphan-reconciliation | `orphan_reconciler.py`     | _batch_orphan_reconciliation                                                                                                        | 1 `python3 -c` (_orphan_lines, строка 533)          |
| (остаются в shell)    | —                          | _load_platform_networks, ensure_docker_network, ensure_spool_dirs, main                                                             | —                                                   |

**Итого в Python переезжает:** 18 функций + 10 inline-блоков = ~1400 строк бизнес-логики.
**Остаётся в shell-фасаде:** 5 функций (orchestration: networks, spool, main) + arg parsing = ~80-100 строк.

### 3.2. Draft Code Graph для docker_orchestrator.py

```xml
<code_graph module="core/internal/bootstrap/deploy/docker_orchestrator.py">
  <contract>
    @purpose Orchestrate docker module deployment via docker compose with parallel groups
    @scope Called by deploy-modules.sh; reads module.yaml + node.yaml; invokes docker CLI
    @invariants
      - deploy_docker_group preserves parallelism within group (W5-E1 transactional — Wave 5 scope)
      - wait_for_readiness respects module.yaml healthcheck.timeout
      - _check_image_exists short-circuits pull if image already present (idempotency)
    @rationale Q: Why not docker SDK? A: docker CLI is already on all nodes, subprocess.run with
      typed args is safer than SDK version drift. Python gives typing + unit-testability.
  </contract>

  <function name="deploy_docker_module">
    @purpose Deploy single docker module via docker compose up -d
    @input module_name: str, module_dir: Path, skip_pull: bool = False
    @output dict: {deployed: bool, health: str, error: Optional[str]}
    @complexity 4 — subprocess + healthcheck polling
    @delegates _check_image_exists, _pre_pull_images, wait_for_readiness
  </function>

  <function name="deploy_docker_group">
    @purpose Deploy group of modules in parallel (from _topo_sort.py groups)
    @input group: list[str], modules_dir: Path, node_yaml: dict
    @output dict: {deployed: list, failed: list, errors: dict}
    @complexity 5 — concurrent.futures or subprocess parallelism
    @note W5-E1 will add transactional rollback; Wave 4 keeps best-effort
  </function>

  <function name="wait_for_readiness">
    @purpose Poll container health until healthy or timeout
    @input module_name: str, timeout_s: int, mode: str = "liveness"
    @output dict: {ready: bool, status: str, attempts: int}
    @complexity 3 — polling loop with subprocess
  </function>
</code_graph>
```

### 3.3. Порядок extraction (Strangler micro-steps)

```
1. Создать core/internal/bootstrap/deploy/__init__.py (empty package marker)
2. Создать docker_orchestrator.py с deploy_docker_module (пока вызывает те же docker CLI)
3. Unit-тест tests/unit/test_docker_orchestrator.py — mock subprocess, verify args
4. В deploy-modules.sh: заменить inline-реализацию deploy_docker_module на
   `python3 core/internal/bootstrap/deploy/docker_orchestrator.py --action deploy ...`
5. Regression: tests/test_deploy_modules.py green
6. Повторить для остальных 4 модулей (sudoers → context_overlay → secrets_validator → orphan)
7. Финальный pass: урезать deploy-modules.sh до <100 строк, удалить мигрировавшие функции
8. Staging-тест: make bootstrap-node NODE=<test> --mode init + node-update
```

### 3.4. Acceptance для W4-E1

(см. AC-2 в $ARTIFACT_CONTRACT)

---

## 4. W4-E3: converge.sh → reconciler.py (ВТОРОЙ ЭПИК)

### 4.1. Функциональная карта (verified 2026-07-21)

converge.sh (1149 строк), 13 функций:

| Python-модуль            | Shell-функции                                                              | Inline python3 / PYEOF                                             |
|--------------------------|----------------------------------------------------------------------------|--------------------------------------------------------------------|
| `reconciler.py`          | reconcile_perms, reconcile_audit_log, reconcile_projects, reconcile_networks, detect_hosts_drift, verify_vhosts, _is_stub | 11 `python3 -c` + 5 `<<PYEOF` (node.yaml parsing в строках 223, 241, 502, 521, 736, 803, 872, 891, 932, 934) |
| (остаются в shell)       | setup_environment, acquire_lock, report_init/add/emit, main, usage         | —                                                                  |

**Exit-code семантика (КРИТИЧНО сохранить):**
- `exit 0` — clean (no drifts)
- `exit 1` — warnings (drifts detected, auto-reconciled)
- `exit 2` — errors (unrecoverable drifts)

Эта семантика проверяется `tests/test_converge_exit.py` (5 тестов) — regression gate.

### 4.2. Порядок extraction

```
1. Создать core/internal/bootstrap/converge/__init__.py
2. Создать reconciler.py с CLI: --node-yaml <path> --report-file <path> --mode <check|reconcile>
3. Реализовать reconcile_* методы (перенос логики + замена inline python3 на yaml_query.py API)
4. Output: JSON report {drifts: [...], reconciled: [...], errors: [...], exit_code: int}
5. Unit-тест tests/unit/test_reconciler.py: каждый reconcile_* метод с tmp_path
6. В converge.sh: setup_environment + acquire_lock + python3 reconciler.py + read JSON + report_emit + exit
7. Regression: tests/test_converge_exit.py green (exit-code mapping сохранён)
8. Staging-тест: make converge NODE=<test> на тестовой ноде с искусственным drift
```

### 4.3. Acceptance для W4-E3

(см. AC-3 в $ARTIFACT_CONTRACT)

---

## 5. W4-E2: node-lifecycle.sh → state_machine.py (ТРЕТИЙ ЭПИК)

### 5.1. Функциональная карта (verified 2026-07-21)

node-lifecycle.sh (1301 строк), 35 функций:

| Python-модуль            | Shell-функции (17 step_*) + helpers                                                                                                                                  | Inline python3 / PYEOF                                              |
|--------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------|
| `state_machine.py`       | step_1_ssh_access … step_17_telegram (17 init steps), update_step_1_verify_core … update_step_6_healthcheck (6 update steps), step_start/done/skip/warn, _step_hash, validate_bootstrap_env, helpers (_step_install_acme, _step_secrets_init) | 5 `python3 -c` + 3 `<<PYEOF` (node.yaml schema validate, modules_raw, TOR_ENABLED) |
| (остаются в shell)       | main, mode-dispatch (init vs update), subprocess-вызовы (systemctl, apt, ssh)                                                                                        | —                                                                   |

**Важный нюанс:** state-machine содержит много subprocess-вызовов (systemctl, apt-get, ssh). Решение: Python-модуль содержит **transition logic** (pre/post-условия, checkpoint-resume, step-skip на content-hash), а subprocess-вызовы остаются в shell-step'ах или выносятся в thin Python-wrapper'ы вокруг subprocess.run.

### 5.2. State-machine дизайн

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
- `start_step(n)` → проверка pre-условий, hash-compare с предыдущим run, update state.json
- `complete_step(n)` → update hash, status=done
- `skip_step(n, reason)` → status=skipped (TOR_DISABLED, content unchanged)
- `fail_step(n, error)` → status=failed, collect error, decide abort vs continue

### 5.3. Порядок extraction

```
1. Создать core/internal/bootstrap/lifecycle/__init__.py
2. Создать state_machine.py: state dataclass, StateMachine class, transitions
3. Создать steps.py (или step_helpers.py): каждый step_* как функция с pre/post + subprocess
4. Unit-тест tests/unit/test_state_machine.py: transitions, checkpoint-resume, skip-logic
5. В node-lifecycle.sh: mode-dispatch + state.json load + python3 state_machine.py run
6. Regression: tests/test_node_lifecycle_static.py + test_bootstrap_auto.py green
7. Staging-тест: make bootstrap-node NODE=<test> --mode init (fresh VPS)
```

### 5.4. Acceptance для W4-E2

(см. AC-4 в $ARTIFACT_CONTRACT)

---

## 6. W4-E4: Makefile include-split (ПАРАЛЛЕЛЬНЫЙ ЭПИК)

### 6.1. Текущее состояние (verified 2026-07-21)

- `Makefile`: 747 строк, 43 `.PHONY` target'а, 0 `include` директив.
- Все target'ы в одном файле → низкая навигируемость, высокий риск конфликтов при редактировании.

### 6.2. Маппинг target → .mk файл

| .mk файл              | Target'ы                                                                                                                       |
|-----------------------|--------------------------------------------------------------------------------------------------------------------------------|
| `makefiles/bootstrap.mk` | bootstrap-node, node-update, converge, render-vhosts                                                                          |
| `makefiles/deploy.mk`    | deploy, deploy-project, context-promote, hermes-build-platform, hermes-build-context, hermes-push-l1                          |
| `makefiles/scaffold.mk`  | new-project, new-context, adopt-project, remove-project, project-sync-env, project-list, project-status                       |
| `makefiles/modules.mk`   | up, down, restart, status, healthcheck, backup, restore, discover-modules, validate-modules                                   |
| `makefiles/ci.mk`        | test, gate, validate, lint, check-file-lines, pre-commit-install, pre-commit-run, scripts-audit, audit, secrets-unlock        |
| `makefiles/helpers.mk`   | venv, templates-check, templates-render, dev-certs, provision, test-inventory-sync, help                                      |

### 6.3. Root Makefile после split

```makefile
# Makefile — root facade (include-only)
# See makefiles/*.mk for target implementations

# === Variables ===
VENV ?= .venv
ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
# ... (общие macros, ~30 строк)

# === Includes ===
include makefiles/bootstrap.mk
include makefiles/deploy.mk
include makefiles/scaffold.mk
include makefiles/modules.mk
include makefiles/ci.mk
include makefiles/helpers.mk

# === Default ===
.DEFAULT_GOAL := help
.PHONY: help
help:  # defined in helpers.mk
```

### 6.4. CI gate (R-RISK-4 mitigation)

Новый gate `tests/gates/test_gate_makefile_targets.py`:
```python
# Для каждого .PHONY target'а:
#   result = subprocess.run(["make", "-n", target], capture_output=True)
#   assert result.returncode == 0, f"make -n {target} failed"
# Дополнительно: check что каждая recipe-строка начинается с TAB (не space)
```

### 6.5. Acceptance для W4-E4

(см. AC-5 в $ARTIFACT_CONTRACT)

---

## 7. W4-E5: Regression baseline (ПЕРВЫМ, страховка R-RISK-5)

### 7.1. Принцип "сначала тесты, потом extraction"

Бриф §6.3 п.1 фиксирует: каждый Python-модуль получает unit-тесты ДО того, как shell-вызов переключается на `python3 script.py`. W4-E5 расширяет СУЩЕСТВУЮЩИЕ тесты edge-case'ами ДО начала extraction — это страховка, что regression будет пойман.

### 7.2. Edge-cases для расширения

| Файл                              | Текущий # | + Edge-cases                                                                                              | Цель # |
|-----------------------------------|-----------|-----------------------------------------------------------------------------------------------------------|--------|
| `test_deploy_modules.py`          | 10        | parallel deploy failure (1 контейнер из группы падает), orphan reconciliation, checkpoint resume, batch sudoers determinism, _expand_transitive_deps (cycle detection), parse_modules_from_node_yaml edge | ≥16    |
| `test_node_lifecycle_static.py`   | —         | step-transitions init vs update, step-skip unchanged hash, step-warn/error collection, TOR-conditional, mode-dispatch | ≥5     |
| `test_bootstrap_auto.py`          | 12        | (расширение существующих orchestrator-тестов)                                                              | ≥15    |
| `test_converge_exit.py`           | 5         | drift detection (perms, audit, networks, projects), reconcile idempotency, stub-detection edge             | ≥9     |

### 7.3. Baseline-замер

`reports/wave4-baseline-2026-07.csv`:
- `pytest tests/test_deploy_modules.py tests/test_node_lifecycle_static.py tests/test_converge_exit.py tests/test_bootstrap_auto.py` время (3 повтора)
- Текущий shell LOC топ-3 (4114)
- Текущий inline python3 count в топ-3 (23)
- Текущий `make gate MODE=fast` время (для сравнения с post-Wave 4)

---

## 8. W4-E6: Inline python3 завершение (НЕЯВНЫЙ)

### 8.1. Что делаем

W4-E6 не отдельная работа — это **побочный эффект** W4-E1/E2/E3. В ходе extraction каждый inline `python3 -c` и `<<PYEOF` блок в топ-3 скриптах заменяется вызовом соответствующего Python-модуля (который сам использует `yaml_query.py` для YAML/JSON parsing).

### 8.2. Tracking

После завершения W4-E1/E2/E3 обновить `reports/inline-python3-map-2026-07-21.csv`:
- Строки для топ-3 скриптов: `consolidation_wave=W4-done`
- Обновить summary: inline count в топ-3: 23 → 0

---

## 9. File Manifest

### 9.1. CREATE

```
# Пакеты Python
core/internal/bootstrap/deploy/__init__.py
core/internal/bootstrap/deploy/docker_orchestrator.py          # ~450-550 LOC
core/internal/bootstrap/deploy/sudoers_generator.py             # ~200-300 LOC
core/internal/bootstrap/deploy/context_overlay.py               # ~150-200 LOC
core/internal/bootstrap/deploy/secrets_validator.py             # ~250-350 LOC
core/internal/bootstrap/deploy/orphan_reconciler.py             # ~150-200 LOC

core/internal/bootstrap/converge/__init__.py
core/internal/bootstrap/converge/reconciler.py                  # ~500-700 LOC

core/internal/bootstrap/lifecycle/__init__.py
core/internal/bootstrap/lifecycle/state_machine.py              # ~600-800 LOC
core/internal/bootstrap/lifecycle/steps.py                      # ~300-400 LOC (опционально)

# Makefile split
makefiles/bootstrap.mk
makefiles/deploy.mk
makefiles/scaffold.mk
makefiles/modules.mk
makefiles/ci.mk
makefiles/helpers.mk

# Unit-тесты
tests/unit/__init__.py (если не существует)
tests/unit/test_docker_orchestrator.py
tests/unit/test_sudoers_generator.py
tests/unit/test_context_overlay.py
tests/unit/test_secrets_validator.py
tests/unit/test_orphan_reconciler.py
tests/unit/test_reconciler.py
tests/unit/test_state_machine.py

# CI gate (Makefile split)
tests/gates/test_gate_makefile_targets.py

# Reports
reports/wave4-baseline-2026-07.csv
reports/wave4-results-2026-XX.csv
```

### 9.2. MODIFY

```
# Top-3 shell → thin facades
core/internal/bootstrap/deploy-modules.sh                       # 1664 → <100 LOC
core/internal/bootstrap/converge.sh                             # 1149 → <150 LOC
core/internal/bootstrap/node-lifecycle.sh                       # 1301 → <200 LOC

# Makefile split
Makefile                                                        # 747 → <150 LOC

# AGENTS.md updates
core/internal/bootstrap/AGENTS.md                               # +раздел "Python-модули декомпозиции"
AGENTS.md                                                       # +TRAP[DECISION] Strangler-Fig canonical

# Тесты (regression edge-cases, W4-E5)
tests/test_deploy_modules.py                                    # +6 edge-case тестов
tests/test_node_lifecycle_static.py                             # +5 edge-case тестов
tests/test_bootstrap_auto.py                                    # +3 edge-case тестов
tests/test_converge_exit.py                                     # +4 edge-case тестов

# Registry
core/entrypoint-manifest.yaml                                   # +новые Python entry points

# Tracking
reports/inline-python3-map-2026-07-21.csv                       # W4-done маркировка
```

---

## 10. Risk Register (Wave 4-specific)

| ID           | Risk                                                                                         | L  | I  | Mitigation                                                                                                                                                    | Эпик       |
|--------------|----------------------------------------------------------------------------------------------|----|----|---------------------------------------------------------------------------------------------------------------------------------------------------------------|------------|
| **R-RISK-4** | Makefile include-split ломает tab-sensitive parsing                                          | M  | H  | CI gate `tests/gates/test_gate_makefile_targets.py`: `make -n <target>` для каждого `.PHONY` до/после split. Recipe-строки — только TAB.                       | W4-E4      |
| **R-RISK-5** | Strangler-extraction ломает runtime скриптов (deploy/bootstrap/converge)                     | M  | H  | W4-E5 ПЕРВЫМ: edge-case regression-тесты ДО extraction. Staging-тест после каждого эпика. Revert-path: git revert <merge-commit> + shell-версия в git history. | W4-E1/E2/E3 |
| **PGM-R2**   | Strangler-extraction ломает production-deploy                                                | M  | H  | Regression-тесты ДО extraction; staging-деплой перед production; audit-trail (Wave 2) для отката. Один скрипт за раз (бриф §6.3.3).                            | All        |
| **R-RISK-NEW-1** | state_machine.py переносит locking/env-setup в Python неидиоматично                       | L  | M  | Option B §2.2: shell-фасад сохраняет orchestration (flock, env exports). Python — только transition logic.                                                     | W4-E2      |
| **R-RISK-NEW-2** | docker_orchestrator.py ломает parallel deploy (concurrent.futures vs shell `&`)            | M  | M  | Сохранить shell-level parallelism (deploy_docker_group в shell вызывает python3 в цикле с `&`). Python — per-module deploy.                                     | W4-E1      |
| **R-RISK-NEW-3** | reconcile exit-code mapping нарушен (test_converge_exit.py red)                           | L  | H  | Python reconciler.py возвращает exit_code в JSON; shell-фасад маппит JSON→exit code. Unit-тест test_reconciler.py явно проверяет {0,1,2} mapping.              | W4-E3      |
| **R-RISK-NEW-4** | Inline python3 в топ-3 не полностью мигрирован (W4-E6 partial)                            | L  | L  | `rg "python3 -c\|<<PYEOF" core/internal/bootstrap/{deploy-modules,converge,node-lifecycle}.sh` → 0 — явный AC. Pre-commit hook (Wave 1) блокирует новые.        | W4-E1/E2/E3 |

---

## 11. Метрики успеха Wave 4

### 11.1. Количественные

| Метрика                                                | Baseline (2026-07-21) | Цель (конец Wave 4)            |
|--------------------------------------------------------|-----------------------|--------------------------------|
| Shell LOC топ-3 (deploy-modules + converge + lifecycle)| 4114                  | ~450 (<100 + <150 + <200)      |
| Python LOC (new decomposition modules)                 | 0                     | ~2000-3000                     |
| Inline `python3 -c` + `<<PYEOF` в топ-3                | 23                    | 0                              |
| Makefile root LOC                                      | 747                   | <150                           |
| Makefile .mk files                                     | 0                     | 6                              |
| Unit-тесты для новых Python-модулей                    | 0                     | 7+ файлов                      |
| Regression edge-case тестов (W4-E5)                    | 0                     | +18 (6+5+3+4)                  |
| `make gate MODE=fast` time                             | baseline (W4-E5)      | ≤ baseline (no regression)     |

### 11.2. Качественные

- Каждый топ-3 скрипт — тонкий shell-фасад + типизированный Python с unit-тестами.
- Makefile навигируем: правки deploy-логики не трогают ci.mk.
- Strangler-Fig паттерн зафиксирован в TRAP[DECISION] как canonical для будущих декомпозиций.
- Staging-деплой проходит без hang (bootstrap init + node-update + converge).

---

## 12. Effort estimation и последовательность

| Эпик       | Описание                              | Effort    | Зависимости                  |
|------------|---------------------------------------|-----------|------------------------------|
| **W4-E5**  | Regression baseline (edge-cases)      | ~1 нед    | — (ПЕРВЫМ)                   |
| **W4-E1**  | deploy-modules.sh декомпозиция (5 модулей) | ~2-3 нед  | W4-E5                        |
| **W4-E4**  | Makefile include-split                | ~0.5-1 нед| — (параллельно W4-E1)        |
| **W4-E3**  | converge.sh → reconciler.py           | ~1.5-2 нед| W4-E1 (опыт Strangler)       |
| **W4-E2**  | node-lifecycle.sh → state_machine.py  | ~2-2.5 нед| W4-E3                        |
| **W4-E6**  | Inline python3 завершение             | ~0 (implicit) | Побочный эффект W4-E1/E2/E3 |
| **Total**  |                                       | **~7-9.5 нед** | (бриф: ~8 нед)              |

**Последовательность:**
```
W4-E5 (regression baseline) ──► W4-E1 (deploy-modules) ──► W4-E3 (converge) ──► W4-E2 (node-lifecycle)
                                 │
                                 └─► W4-E4 (Makefile split, параллельно)
W4-E6 (inline python3 sweep) ── побочный эффект на каждом этапе
```

**Production-релизы (3 PR):**
1. PR-1: W4-E5 + W4-E1 (deploy-modules decomposition) + W4-E4 (Makefile split, если готов)
2. PR-2: W4-E3 (converge decomposition)
3. PR-3: W4-E2 (node-lifecycle decomposition)

---

## 13. Anti-goals (Wave 4 scope)

- ❌ Big-bang rewrite топ-3 в один PR (Option C §2.1, score 3/10).
- ❌ Параллельная миграция deploy-modules + converge + node-lifecycle (бриф §6.3.3 — один за раз).
- ❌ Перенос locking/env-setup/subprocess-orchestration в Python (Option A §2.2 — shell остаётся для orchestration).
- ❌ Миграция стабильных lib (logging.sh, paths.sh, ssh.sh, args.sh, audit_logging.sh) — их API стабилен (AGENTS.md §Языковая политика п.2).
- ❌ Добавление новых фич в ходе декомпозиции (pure refactor + extraction, no behavior change).
- ❌ Transactional deploy_docker_group (это Wave 5 W5-E1 scope).
- ❌ Converge K8s-parity расширение (это Wave 5 W5-E2/E3/E4/E5 scope).

---

## 14. Production-релиз Wave 4

### 14.1. Staging-gate (перед каждым PR merge)

```bash
# На тестовой ноде (fresh VPS или пересозданная):
make bootstrap-node NODE=<test> --mode init    # W4-E2 validation
make node-update NODE=<test>                   # W4-E2 incremental
make converge NODE=<test>                      # W4-E3 validation
make project-list NODE=<test>                  # uses lib/ssh.sh (Wave 2)
make project-status NAME=<p> NODE=<test>       # uses lib/ssh.sh

# Все 5 проходят без hang (TRAP[DECISION] 2026-07-21 в root AGENTS.md, lib/ssh.sh staging-gate)
```

### 14.2. Post-Wave 4 замеры

`reports/wave4-results-2026-XX.csv`:
- Shell LOC топ-3 (цель: ~450)
- Python LOC новых модулей (цель: ~2-3K)
- Inline python3 count в топ-3 (цель: 0)
- `make gate MODE=fast` time (сравнение с baseline Wave 1 W1-E8 и Wave 4 W4-E5)
- Unit-тест execution time (новые 7+ файлов)

### 14.3. Документация

- `core/internal/bootstrap/AGENTS.md` — раздел "Python-модули декомпозиции" с картой shell-фасад → Python-модуль.
- Root `AGENTS.md` — TRAP[DECISION] Strangler-Fig canonical (референс — топ-3, паттерн для будущих декомпозиций).
- Обновить бриф 027 §6: отметить Wave 4 как IMPLEMENTED + ссылка на этот DevPlan и VerificationReport.

---

## 15. Делегирование в dev-pipeline

Wave 4 делегируется через dev-pipeline skill:
```
035-wave4-strangler-top3/
├── 01-Brief (implicit — бриф 027 §6)
├── 02-DevPlan.md (этот файл)
├── 03-VerificationReport.md (после QA)
└── 04-VerificationReport-fixes.md (если нужны fix-волны)
```

**Принципы делегирования (бриф 027 §11):**
1. Одна delivery-волна = один DevPlan = одна сессия dev-pipeline (Brief → Architect → Coder → QA → Fix).
2. Завершается production-релизом (3 PR + staging-gate).
3. Перед стартом: verify problem matrix против текущего состояния (§1.4 — выполнено).
4. После завершения: прикрепить ссылку на DevPlan и VerificationReport к Brief 027.
5. Архитектор ОБЯЗАН прочитать `reports/architecture-analysis-2026-07-21.md` и этот DevPlan.
6. Wave 5 стартует ТОЛЬКО после завершения Wave 4 + production-релиза.

$END_DEVPLAN
