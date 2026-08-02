# 07-DevPlan — Бриф F: тесты — чистка и дыры

<!-- $ARTIFACT_CONTRACT
PURPOSE:          Почистить тестовый набор после волны 117: закрыть R1-дыру (pass-тесты), удалить дубли, разбить монстры, исправить хрупкие импорты.
DESCRIPTION:      7 задач: F1 pass-тесты, F2 watchdog-дубли, F3 monitoring_renderer, F4 smoke_bootstrap_dry_run orphan, F5 sys.path.insert,
                  F6 test_reconciler монолит, F7 restart-гейты консолидация (D13).
RATIONALE:        Test Honesty R1: 3 pass-теста в гейтах не фальсифицируемы; дубли (watchdog, monitoring_renderer) раздувают набор и дают
                  ложное покрытие; хрупкий импорт ломается на VPS-контекстах. Чистка перед ручным тестированием = честные сигналы.
ACCEPTANCE_CRITERIA:
  - AC-F1: 3 pass-теста получили реальные assert (файл-сканирование R1-гейта не заменяет функцию — проверить/расширить детектор R1 по-функции).
  - AC-F2: test_agent_watchdog.py — 4 дубль-теста CircuitBreaker удалены (канон — test_watchdog_circuit_breaker.py); 0 потерь покрытия.
  - AC-F3: test_monitoring_config_renderer.py — разделён: config-loading остаётся, генератор-тесты удалены/перенесены (дубли 7 новых файлов).
  - AC-F4: test_smoke_bootstrap_dry_run.sh — зарегистрирован в inventory/CI ИЛИ удалён (Python-эквивалент покрывает больше).
  - AC-F5: test_on_project_deploy.py — пакетный импорт вместо sys.path.insert; работает из любого CWD.
  - AC-F6: test_reconciler.py — разбит по converge-подмодулям; 0 теряемого покрытия.
  - AC-F7: restart-проверки консолидированы в test_gate_make_contract; test_restart_consistency.py удалён.
  - AC-F8: gate MODE=fast зелёный; суммарный LOC тестов сокращён.
IMPLEMENTS:       118 01-Brief задачи F1-F7.
IMPACTS:          tests/gates/{test_gate_compose_base_contract,test_gate_ci_env_vars,test_gate_workflow_consistency,test_restart_consistency}.py,
                  tests/unit/{test_agent_watchdog,test_monitoring_config_renderer,test_reconciler}.py, tests/test_smoke_bootstrap_dry_run.sh,
                  tests/unit/test_on_project_deploy.py, tests/gates/AGENTS.md (R1-детектор), test_inventory.
REQUIRES:         118 01-Brief; F7 пересекается с G1 (регистрация invisible-гейтов — restart_consistency удаляется, не регистрируется).
-->

---

## 1. Технический анализ и решения

### F1 (HIGH) — 3 pass-теста в гейтах

**Факты (аудит):**
- `test_gate_compose_base_contract.py::test_container_name_matches_module_name` — только `logger.info("IMP:9 PASS")`, 0 assert.
- `test_gate_ci_env_vars.py::test_ci_env_vars_match_platform_env` — warning + PASS.
- `test_gate_workflow_consistency.py::test_make_targets_exist` (:297) — warning + PASS.

**Корень:** гейт `r1_no_pass_tests` сканирует **по-файловому** (файл содержит assert где-то — проходит), а не по-функции → pass-функции проскакивают.

**Решение:**
1. Реализовать реальные assert в 3 тестах (проверить фактические инварианты: container_name == module_name в каждом base-yml; совпадение env-переменных; существование make-таргетов).
2. Расширить детектор R1 (tests/gates/AGENTS.md + test_r1_no_pass_tests.py): сканирование по-функции (AST: assert в теле функции, не только в файле).

**Тест:** negative-тест R1: функция без assert → RED.

**Риск:** LOW-MED (детектор по-функции может дать ложные срабатывания на fixture/helpers — исключения через декораторы).

### F2 (MED) — test_agent_watchdog.py дубли

**Факты (верифицированы):** `test_agent_watchdog.py:210,243,...` — 4 теста `test_circuit_breaker_*`, продублированы в `test_watchdog_circuit_breaker.py` (12 тестов, канон после T52).

**Решение:** удалить 4 дубль-теста из test_agent_watchdog.py. Проверить, что оставшиеся тесты файла не дублируют другие (full-scan).

**Тест:** coverage-сравнение до/после (нет потерь).

**Риск:** LOW.

### F3 (MED) — test_monitoring_config_renderer.py (943 LOC)

**Факты:** генераторы (alert_rules, prometheus, grafana, loki) вынесены в `monitoring/` волной 117 → 7 новых тест-файлов дублируют старые генератор-тесты. Осталась ценность: config-loading (deep_merge, L1/L2/L3).

**Решение:** удалить генератор-тесты, оставить config-loading; файл сокращается до ~300 LOC. При полном дублировании — удалить файл.

**Тест:** coverage по monitoring_config_renderer после чистки.

**Риск:** LOW (дубли уже покрыты 7 новыми файлами).

### F4 (MED) — орфан test_smoke_bootstrap_dry_run.sh

**Факты:** 108 LOC bash-прогон bootstrap.sh/node-lifecycle.sh/provision-environment.sh (только --help/--dry-run); не упоминается в make/CI/test_inventory.yaml. Python-эквивалент — `tests/integration/test_bootstrap_dry_run.py` (14 фаз state_machine с mock subprocess, покрывает больше).

**Решение:** удалить (Python-эквивалент покрывает больше и не прогоняет реальный bash). При желании сохранить — зарегистрировать в inventory + test_runner static-сьют.

**Тест:** test_bootstrap_dry_run.py остаётся зелёным (замена).

**Риск:** LOW.

### F5 (MED) — test_on_project_deploy.py хрупкий импорт

**Факты (верифицированы):** `sys.path.insert(0, str(_HOOKS_DIR))` (:33) в директорию без `__init__.py` — работает только из определённого CWD; на VPS (watchdog PYTHONPATH) может сломаться.

**Решение:** пакетная структура `core/modules/postgres/hooks/__init__.py` + импорт `core.modules.postgres.hooks.on_project_deploy`, либо перемещение логики в `internal/`. Убрать sys.path.insert.

**Тест:** запуск pytest из любого CWD.

**Риск:** LOW (проверить, не используется ли модуль как standalone скрипт на VPS).

### F6 (LOW) — test_reconciler.py монолит

**Факты:** 1292 LOC, 0 классов, тестирует 6+ модулей (reconciler, converge/{audit,infra,networks,projects,vhosts}, stub_detection).

**Решение:** разбить на `tests/unit/test_reconciler.py` + `tests/unit/test_converge_{audit,infra,networks,projects,vhosts}.py` (по подмодулям). Без смены assertions.

**Тест:** полный набор после разбиения идентичен.

**Риск:** LOW (механическое разбиение).

### F7 (MED) — restart-гейты консолидация [D13]

**Факты:** `test_gate_make_contract.py::test_restart_soft_semantics` (зарегистрирован) ∩ `tests/gates/test_restart_consistency.py` (257 LOC, НЕ зарегистрирован — invisible, см. G1) — оба проверяют restart = soft stop+start. Также `test_gate_compose_restart_consistency` (compose-слой).

**Решение:** перенести проверки restart-hard/module.mk из test_restart_consistency.py в test_gate_make_contract.py; удалить test_restart_consistency.py. **Важно:** НЕ регистрировать его в G1 (он удаляется, а не активируется).

**Тест:** все assertions перенесены (сравнение списков до/после).

**Риск:** LOW-MED (перенос без потерь).

---

## 2. Порядок выполнения

```
F1 (pass-тесты + R1-детектор)  ← первым: честность гейтов
   │
F2 → F3 → F4 → F5 → F6         ← чистка дублей/монстров (независимы)
   │
F7 (restart-гейты)             ← вместе с G1 (не регистрировать test_restart_consistency)
```

## 3. Оценки

| Метрика | Значение |
|---------|----------|
| Задач | 7 |
| LOC-сокращение тестов | ~1500 (F3 −600, F6 −0 нетто, F2 −150, F4 −108, F7 −257) |
| Рискованных | F1 (AST-детектор — ложные срабатывания) |

## $END

Открытые вопросы:
1. **F1** — объём ложных срабатываний нового по-функции R1-детектора (helpers/fixtures в gate-файлах).
2. **F5** — используется ли on_project_deploy.py как standalone на VPS (тогда пакетная структура, не перемещение).
