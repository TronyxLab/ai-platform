# DevPlan 042 — Адаптация тестов после Wave 4 Strangler-Fig декомпозиции

<!-- $ARTIFACT_CONTRACT
  PURPOSE: Адаптировать 14 obsolete shell-grep тестов под новую архитектуру (shell-фасад + Python-модули), исправить 5 Python-багов в docker_orchestrator.py
  DESCRIPTION: Wave 4 Strangler-Fig (035) мигрировал бизнес-логику deploy-modules.sh (1664 LOC) в 5 Python-модулей. 14 shell-grep тестов проверяют функции/переменные, которых больше нет в shell-фасаде. 5 Python-тестов падают из-за багов в docker_orchestrator.py. Подход: Option D «Structural Contract» — 6 структурных контракт-тестов для shell-фасада + 2 интеграционных smoke-теста + фикс 5 багов. 14 obsolete → 8 новых (сокращение 43%) при росте покрытия shell facade с 0% до ~80% structural.
  RATIONALE: Адаптировать тесты к новой архитектуре без дублирования существующих 104 Python unit-тестов. Обеспечить покрытие shell-фасада как архитектурного контракта shell→Python delegation.
  ACCEPTANCE_CRITERIA:
    1. Все 13+5 failing тестов проходят (0 failures)
    2. 14 obsolete shell-grep тестов заменены на 6 структурных контракт-тестов + 2 smoke-теста
    3. 5 Python-багов в docker_orchestrator.py исправлены
    4. Shell-фасад имеет ≥80% structural coverage (arg parsing, provisioner, Python delegation, severity exit)
    5. 104 существующих Python unit-теста проходят без регрессии
  IMPLEMENTS: superposition Option D из session 2026-07-22
  IMPACTS:
    - tests/test_deploy_gates_static.py — переписать test_env_requires_gate_present
    - tests/test_deploy_module_env.py — переписать 2 теста на docker_orchestrator.py
    - tests/test_unit_deploy_modules_provisioner.py — заменить 9 тестов на 4 структурных
    - tests/unit/test_spool_dir.py — удалить 2 shell-grep проверки
    - core/internal/bootstrap/deploy/docker_orchestrator.py — фикс 5 багов
    - tests/unit/test_shell_facade_contract.py — НОВЫЙ: 6 структурных контракт-тестов
    - tests/test_deploy_smoke.py — НОВЫЙ: 2 интеграционных smoke-теста
  REQUIRES:
    - Python 3.10+
    - Доступ к core/internal/bootstrap/deploy-modules.sh
    - Существующие 104 unit-теста должны быть зелёными (кроме 5 известных багов)
-->

$START

## Overview

**Status:** Planned
**Wave:** 042
**Superposition decision:** Option D — Structural Contract (score 9/10)
**Session:** 2026-07-22

### Problem

Wave 4 Strangler-Fig (035-wave4-strangler-top3) мигрировал `deploy-modules.sh` 1664→91 LOC. Бизнес-логика вынесена в 5 Python-модулей:

| Python модуль | LOC | Unit-тесты |
|--------------|-----|-----------|
| `docker_orchestrator.py` | 1155 | 32 (5 fail) |
| `secrets_validator.py` | 589 | 31 |
| `sudoers_generator.py` | 648 | 26 |
| `context_overlay.py` | 369 | 8 |
| `orphan_reconciler.py` | 555 | 7 |

**14 shell-grep тестов** проверяют функции/переменные, которых больше нет в 91-LOC shell-фасаде. **5 Python-тестов** падают из-за реальных багов в `docker_orchestrator.py`.

### Failure Matrix

| # | Файл | Тест | Причина | Категория |
|---|------|------|---------|-----------|
| 1 | `test_deploy_gates_static.py` | `test_env_requires_gate_present` | `_check_env_requires()` → `secrets_validator.py` | Функция мигрирована |
| 2 | `test_deploy_module_env.py` | `test_compose_args_has_platform_env` | `("--env-file" "$platform_env")` → `docker_orchestrator.py` | Функция мигрирована |
| 3 | `test_deploy_module_env.py` | `test_prepull_skips_local_build` | `build:`, `grep -q`, `SKIP` → `docker_orchestrator.py` | Функция мигрирована |
| 4 | `test_unit_deploy_modules_provisioner.py` | `test_networks_delegated_to_provisioner` | `_extract_main_body()` regex не матчит | Баг test helper |
| 5 | `test_unit_deploy_modules_provisioner.py` | `test_volumes_delegated_to_provisioner` | `_extract_main_body()` regex не матчит | Баг test helper |
| 6 | `test_unit_deploy_modules_provisioner.py` | `test_legacy_fallback_preserved` | `ensure_docker_network()` удалён | Функция удалена |
| 7 | `test_unit_deploy_modules_provisioner.py` | `test_modules_filter_flag_present` | `_extract_main_body()` regex не матчит | Баг test helper |
| 8 | `test_unit_deploy_modules_provisioner.py` | `test_modules_filter_unknown_module_error` | `_expand_transitive_deps` → Python | Функция мигрирована |
| 9 | `test_unit_deploy_modules_provisioner.py` | `test_get_module_severity_function` | `_get_module_severity()` → Python | Функция мигрирована |
| 10 | `test_unit_deploy_modules_provisioner.py` | `test_severity_exit_codes` | `FAILED_MODULE_NAMES` → `FAILED` array | Переменная переименована |
| 11 | `test_unit_deploy_modules_provisioner.py` | `test_expand_transitive_deps_function` | BFS → `secrets_validator.py` | Функция мигрирована |
| 12 | `test_unit_deploy_modules_provisioner.py` | `test_failed_module_names_tracking` | `FAILED_MODULE_NAMES+=` → `FAILED+=` | Переменная переименована |
| 13 | `test_spool_dir.py` | `test_spool_dir_none_no_warn` | `ensure_spool_dirs` удалён | Функция удалена |
| 14 | `test_spool_dir.py` | `test_spool_dir_missing_still_warns` | `ENSURE_SPOOL_DIRS` region удалён | Функция удалена |
| 15 | `test_docker_orchestrator.py` | `test_cleanup_legacy_container_found` | docker stop не вызывается | Python bug |
| 16 | `test_docker_orchestrator.py` | `test_cleanup_legacy_container_not_found` | docker stop не вызывается | Python bug |
| 17 | `test_docker_orchestrator.py` | `test_deploy_docker_module_hermes_agent` | Hermes stop не вызывается | Python bug |
| 18 | `test_docker_orchestrator.py` | `test_pre_pull_images_single` | `sys.exit(0)` вместо return | Python bug |
| 19 | `test_docker_orchestrator.py` | `test_reconcile_orphan_containers_with_orphan` | Orphan stop не вызывается | Python bug |

## Approach: Option D — Structural Contract

### Phase 1: Fix 5 Python bugs in docker_orchestrator.py

| # | Bug | Fix |
|---|-----|-----|
| 15-16 | `_cleanup_legacy_container` не вызывает docker stop | Добавить `subprocess.run(["docker", "stop", container_name])` |
| 17 | `deploy_docker_module` для hermes-agent не вызывает docker stop перед rebuild | Добавить docker stop в `_handle_hermes_agent` перед docker compose up |
| 18 | `_pre_pull_images` вызывает `sys.exit(0)` вместо return | Заменить `sys.exit(0 if success else 1)` на `return (success, fail_count)` |
| 19 | `_reconcile_orphan_containers` не вызывает docker stop для orphan | Добавить docker stop в цикл обработки orphan |

### Phase 2: Создать 6 структурных контракт-тестов (новый файл)

**Новый файл:** `tests/unit/test_shell_facade_contract.py`

| # | Тест | Что проверяет |
|---|------|--------------|
| S1 | `test_shell_has_arg_parsing` | `--modules`, `--skip-provision` флаги в while/case |
| S2 | `test_shell_has_provisioner_delegation` | `provision-environment.sh --scope networks\|volumes` + legacy `docker network create` fallback |
| S3 | `test_shell_has_python_delegation` | Все 5 вызовов `python3 deploy/*.py` присутствуют: context_overlay, secrets_validator (validate-charsets, parse-node-yaml, check-env, detect-type, module-metadata), docker_orchestrator, sudoers_generator, orphan_reconciler |
| S4 | `test_shell_has_severity_exit` | `FAILED` array, severity loop с `module-metadata`, exit 2 (critical), exit 1 (warn), exit 0 (all ok) |
| S5 | `test_shell_has_context_overlay` | `context_overlay.py --action ensure --node-yaml` вызов |
| S6 | `test_shell_has_sudoers_orphan_post_deploy` | `sudoers_generator.py --action batch-generate` + `orphan_reconciler.py` в post-deploy секции |

### Phase 3: Интеграционные smoke-тесты (новый файл)

**Новый файл:** `tests/test_deploy_smoke.py`

| # | Тест | Что проверяет |
|---|------|--------------|
| I1 | `test_deploy_modules_no_node_yaml` | Запуск без `NODE_YAML` → exit 1, stderr содержит "ERROR" |
| I2 | `test_deploy_modules_missing_node_yaml_file` | `NODE_YAML=/nonexistent` → exit 1 |

### Phase 4: Переписать/удалить 14 obsolete тестов

| Файл | Действие | Детали |
|------|----------|--------|
| `test_deploy_gates_static.py` | **Переписать** `test_env_requires_gate_present` | Заменить shell-grep на проверку вызова `secrets_validator.py --action check-env` в shell |
| `test_deploy_module_env.py` | **Переписать** оба теста | `test_compose_args_has_platform_env` → тест `docker_orchestrator._build_compose_args`; `test_prepull_skips_local_build` → тест `docker_orchestrator._pre_pull_images` |
| `test_unit_deploy_modules_provisioner.py` | **Заменить** 9 тестов на 4 | `test_networks_delegated` + `test_volumes_delegated` → объединить в контракт S2; `test_modules_filter` + `test_modules_filter_unknown` → удалить (покрыто контрактом S1 + S3); `test_get_module_severity` + `test_severity_exit_codes` + `test_expand_transitive_deps` + `test_failed_module_names` → удалить (покрыто контрактами S3, S4); `test_legacy_fallback` → удалить (fallback удалён из архитектуры); `test_postgres_module_severity_critical` → **сохранить** (проверяет module.yaml, не shell) |
| `test_spool_dir.py` | **Удалить** 2 shell-grep проверки из тестов | Из `test_spool_dir_none_no_warn`: удалить Check 3 (grep shell). Из `test_spool_dir_missing_still_warns`: удалить grep `ENSURE_SPOOL_DIRS` region. Check 1-2 в обоих тестах проверяют module.yaml — сохранить. |

## Task Breakdown

### Task 1: Fix Python bugs [~30 min]
- [ ] T1.1: Fix `_cleanup_legacy_container` — add docker stop (lines ~690-720)
- [ ] T1.2: Fix `_handle_hermes_agent` — add docker stop before rebuild (lines ~290-370)
- [ ] T1.3: Fix `_pre_pull_images` — replace `sys.exit()` with `return` (line ~703)
- [ ] T1.4: Fix `_reconcile_orphan_containers` — add docker stop for orphans (lines ~590-680)

### Task 2: Structural contract tests [~45 min]
- [ ] T2.1: Create `tests/unit/test_shell_facade_contract.py` with 6 tests
- [ ] T2.2: Implement helper: `_read_deploy_modules_shell()` — читает shell-фасад
- [ ] T2.3: Implement S1: `test_shell_has_arg_parsing`
- [ ] T2.4: Implement S2: `test_shell_has_provisioner_delegation`
- [ ] T2.5: Implement S3: `test_shell_has_python_delegation`
- [ ] T2.6: Implement S4: `test_shell_has_severity_exit`
- [ ] T2.7: Implement S5: `test_shell_has_context_overlay`
- [ ] T2.8: Implement S6: `test_shell_has_sudoers_orphan_post_deploy`

### Task 3: Integration smoke tests [~15 min]
- [ ] T3.1: Create `tests/test_deploy_smoke.py` with 2 tests

### Task 4: Rewrite/remove obsolete tests [~30 min]
- [ ] T4.1: `test_deploy_gates_static.py` — rewrite `test_env_requires_gate_present`
- [ ] T4.2: `test_deploy_module_env.py` — rewrite both tests as docker_orchestrator tests
- [ ] T4.3: `test_unit_deploy_modules_provisioner.py` — replace 9→4 tests, keep postgres severity
- [ ] T4.4: `test_spool_dir.py` — remove Check 3 from both tests

### Task 5: Verification [~15 min]
- [ ] T5.1: `make gate MODE=fast` — зеленый
- [ ] T5.2: `python -m pytest tests/unit/ -v` — 0 failures
- [ ] T5.3: `python -m pytest tests/ -k "deploy" -v` — 0 failures

## Risk Analysis

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Структурные тесты хрупки при изменении shell | Medium | Low | Тесты проверяют контракт (вызовы python3), не реализацию |
| Баги в docker_orchestrator.py глубже чем кажется | Low | High | Каждый баг-фикс изолирован, тесты покрывают конкретный сценарий |
| ensure_spool_dirs удалён — потеря spool-валидации | Medium | Medium | Создать issue на реимплементацию spool-проверки в Python |
| Новые тесты дублируют существующие 104 unit-теста | Low | Low | Контракт-тесты проверяют shell wiring — ортогональны Python unit-тестам |

## Architecture Decision: ensure_spool_dirs removal

`ensure_spool_dirs()` была в старом deploy-modules.sh (1664 LOC) и проверяла, что все docker-модули имеют `spool_dir` или `spool_volume`. После миграции в 91-LOC фасад эта проверка была удалена — НЕ перенесена в Python.

**Решение:** создать отдельную задачу (не в скоупе этого DevPlan) на реимплементацию `ensure_spool_dirs` как Python-валидатора в `secrets_validator.py` или отдельного скрипта. Пока что spool-проверка обеспечивается gate-тестом `test_all_docker_modules_have_spool_dir` в `test_spool_dir.py` (статический анализ module.yaml).

## Files Changed

| File | Action | LOC change |
|------|--------|-----------|
| `core/internal/bootstrap/deploy/docker_orchestrator.py` | Fix 5 bugs | ~20 |
| `tests/unit/test_shell_facade_contract.py` | NEW | ~120 |
| `tests/test_deploy_smoke.py` | NEW | ~40 |
| `tests/test_deploy_gates_static.py` | Rewrite 1 test | ~10 |
| `tests/test_deploy_module_env.py` | Rewrite 2 tests | ~40 |
| `tests/test_unit_deploy_modules_provisioner.py` | Replace 9→4 tests | ~-80 |
| `tests/unit/test_spool_dir.py` | Trim 2 shell checks | ~-20 |
| **Total** | | **~+130 net** |

$END
