$START_VERIFICATION_REPORT
# VerificationReport — DevPlan 106/108 Combined QA (Post-Coder)

$ARTIFACT_CONTRACT
PURPOSE:               Сводная верификация реализаций DevPlan 106 (lint-headers-consolidation)
                       и DevPlan 108 (scp-deliver-python) после работы кодеров. Cross-plan
                       анализ: выявление дефектов, конфликтов с legacy-тестами, gate-регрессий.
DESCRIPTION:           Phase 1-6 QA-аудит: статический аудит (Phase 1), cross-file drift
                       (Phase 2), верификация инвариантов (Phase 3), тест-качество (Phase 4),
                       рантайм-валидация (Phase 5), config sync audit (Phase 6).
                       Зафиксированы 4 FLAG от кодера 108 + 2 GATE-регрессии + 7 дефектов.
RATIONALE:             Cross-plan проверка необходима: 106 и 108 затрагивают одни и те же
                       gate-тесты (test_check_doc_headers_equivalent, test_linter_parity)
                       через разные механизмы — 106 через удаление shell-функций,
                       108 через изменение scp-deliver.sh facade.
ACCEPTANCE_CRITERIA:   AC106:1-10 (grepsummary_validator, doc_header_validator, фасады ≤40 LOC,
                       grepsummary НЕ дублируется, manifest delegates_to, pre-commit hooks,
                       исключения сохранены). AC108:1-8 (core_deliverer.py, фасад ≤60 LOC,
                       deliver_all последовательность, overlay delegation, dry-run,
                       audit trail, exclude-паттерны, gate).
IMPLEMENTS:            DevPlan 106 (`.ai/plans/106-lint-headers-consolidation/02-DevPlan.md`),
                       DevPlan 108 (`.ai/plans/108-scp-deliver-python/02-DevPlan.md`)
IMPACTS:               - Регистрация 13 дефектов (3 BLOCKER, 5 MAJOR, 5 MINOR)
                       - 6 legacy-тестов нуждаются в адаптации
                       - 2 gate-теста падают (check_doc_headers_equivalent, linter_parity)
REQUIRES:              Python ≥3.10, PyYAML, pytest, git, доступ к реальному репозиторию
$END_ARTIFACT_CONTRACT

---

🔒 Verified against SHA `fbe306d4284d9105193605378be28eb64b3c6795`
⚠️  Working tree: NO uncommitted changes detected (`git diff --name-only` empty)

---

## Section 1 — Static Audit (Phase 1)

### Compliance Matrix

| File | MODULE_CONTRACT | GREP_SUMMARY | STRUCTURE | #region Balance | Doxygen @tags | TRAPs | LDD IMP:7-9 | No bare except |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `core/internal/lint/__init__.py` | ✅ | ✅ | ✅ | ✅ (0 regions) | N/A (package) | N/A | N/A | N/A |
| `core/internal/lint/grepsummary_validator.py` (321 LOC) | ✅ | ✅ | ✅ | ✅ (6 open/close) | ✅ @purpose/@io/@complexity | ✅ 3 TRAPs | ✅ IMP:7-9 | ✅ |
| `core/internal/lint/doc_header_validator.py` (566 LOC) | ✅ | ✅ | ✅ | ✅ (12 open/close) | ✅ @purpose/@io/@complexity | ✅ 5 TRAPs | ✅ IMP:7-9 | ✅ |
| `core/entrypoints/lint.sh` (40 LOC) | ✅ | ✅ | ✅ | ✅ (2 regions) | ✅ @purpose/@invariants | N/A (facade) | ✅ IMP:7-8 | ✅ |
| `core/entrypoints/check-doc-headers.sh` (17 LOC) | ✅ | ✅ | ✅ | ✅ (1 region) | ✅ @purpose/@invariants | N/A (facade) | ✅ IMP:7 | ✅ |
| `core/internal/bootstrap/core_deliverer.py` (450 LOC) | ✅ | ✅ | ✅ | ✅ (18 open/close) | ✅ @purpose/@io/@complexity | ✅ 4 TRAPs | ✅ IMP:8-10 | ✅ |
| `core/internal/bootstrap/scp-deliver.sh` (59 LOC) | ✅ | ✅ | ✅ | ✅ (3 regions) | ✅ @purpose/@param | ✅ 3 TRAPs | ✅ IMP:7-8 | ✅ |
| `tests/unit/test_grepsummary_validator.py` | ✅ | ✅ | ✅ | ✅ (6 regions) | ✅ @purpose/@io | ✅ TRAP[TEST] ×6 | ✅ caplog IMP:9 | ✅ |
| `tests/unit/test_doc_header_validator.py` | ✅ | ✅ | ✅ | ✅ (9 regions) | ✅ @purpose/@io | ✅ TRAP[TEST] ×9 | ✅ caplog IMP:9 | ✅ |
| `tests/unit/test_core_deliverer.py` | ✅ | ✅ | ✅ | ✅ (14 regions) | ✅ @purpose/@io | ✅ TRAP[TEST] ×14 | ✅ caplog IMP:9 | ✅ |

### Findings

| # | Severity | File:Line | Issue |
|---|:--------:|-----------|-------|
| S1 | WARNING | `core/internal/lint/doc_header_validator.py` → 566 LOC | Целевой размер DevPlan: ~380 LOC. Фактический: 566 LOC (+49%). Основной вклад: expand-пути в check_md_sh_refs (строки 231-310, ~80 LOC поиска .sh-файлов). Не нарушает AC, но сигнализирует о раздутии функции check_md_sh_refs. |
| S2 | WARNING | `core/internal/bootstrap/core_deliverer.py` → 450 LOC | Целевой размер DevPlan: ~230 LOC. Фактический: 450 LOC (+96%). Документированный отклон (кодер 108). Основной вклад: argparse CLI (строки 376-450, ~75 LOC) + детальная документация TRAP-аннотаций + расширенные docstrings. |
| S3 | INFO | `core/entrypoints/lint.sh:40` | AC3: ровно 40 LOC (предел ≤40) — минимальный запас. |
| S4 | INFO | `core/entrypoints/check-doc-headers.sh:17` | AC4: 17 LOC — значительный запас (≤40). |
| S5 | INFO | `core/internal/bootstrap/scp-deliver.sh:59` | AC2: 59 LOC (предел ≤60) — минимальный запас в 1 строку. |

---

## Section 2 — Drift Analysis (Phase 2)

### Drift Register

| DRIFT-ID | Severity | Files | Expected | Actual | Fix |
|----------|:--------:|-------|----------|--------|-----|
| DRIFT-GATE-1 | **BLOCKER** | `tests/gates/test_gate_ci_coverage.py:588-600` vs `core/entrypoints/check-doc-headers.sh` | Gate test expects shell function names (`check_grep_summary`, `check_module_contract`, `check_structure`, `check_regions_balanced`, `check_yaml_purpose`) in check-doc-headers.sh | Shell facade = 17 LOC, делегирует в `doc_header_validator.py`. Функции переехали в Python. | Обновить gate: либо проверять Python-модуль, либо валидировать наличие `python3 -m core.internal.lint.doc_header_validator doc-headers` в фасаде и эквивалентные функции в Python-модуле. |
| DRIFT-GATE-2 | **BLOCKER** | `tests/gates/test_gate_lint_quality.py:114-119` vs `core/entrypoints/lint.sh` | Gate test запускает bash linter (`lint.sh namelint`) и Python linter — ожидает идентичные результаты | Bash linter exec'ит ТОТ ЖЕ Python (`python3 -m ... doc_header_validator namelint`) — кодовая база едина. Но temp-путь игнорируется (Python использует `Path(__file__).resolve().parents[4]` = реальный repo root, не temp). | Обновить тест: (a) запускать Python-модуль НАПРЯМУЮ вместо bash-фасада, либо (b) monkeypatch `_default_repo_root()` для temp-изоляции. |
| DRIFT-LEGACY-1 | **BLOCKER** | `tests/test_deploy_delivery_static.py:46-56` vs `core/internal/bootstrap/scp-deliver.sh` | Статически проверяет `--exclude='default-user.xml'` в scp-deliver.sh | Исключения теперь в `core_deliverer.py:53-59` (RSYNC_EXCLUDES_CORE). Shell-фасад 59 LOC без rsync. | Расширить проверку: scp-deliver.sh ИЛИ core_deliverer.py. Или перенести тест на core_deliverer.py. |
| DRIFT-LEGACY-2 | **BLOCKER** | `tests/test_contract_deploy_ssh.py` (4 теста) vs scp-deliver.sh | Мокают shell-функции (ssh_exec, rsync, scp_to_server) в scp-deliver.sh через subprocess bash | Shell-фасад вызывает `python3 -m core.internal.bootstrap.core_deliverer deliver`. Python логика не мокается shell-моками. Все 4 теста: exit code=1, логи IMP:9 не совпадают. | Полный пересмотр: заменить shell-моки на mock `subprocess.run` в core_deliverer.py ИЛИ удалить тесты (логика покрыта test_core_deliverer.py). |
| DRIFT-LEGACY-3 | **BLOCKER** | `tests/test_bootstrap_auto.py::test_rsync_command_generation` | Ищет rsync-паттерны в scp-deliver.sh, ожидает 5 rsync вызовов | 0 rsync вызовов в фасаде (всё в Python). | Перенаправить на core_deliverer.py: grep deliver_core/deliver_node_configs/deliver_secrets. |
| DRIFT-LEGACY-4 | **MAJOR** | `tests/gates/test_gate_context_overlay_git.py::test_core_rsync_excludes_git` | Проверяет scp-deliver.sh на наличие git exclude в rsync-командах | VACUOUS PASS: 0 rsync команд = 0 нарушений. Потеря покрытия. | Перенаправить на core_deliverer.py: RSYNC_EXCLUDES_CORE содержит `--exclude=.git`. |
| DRIFT-TRAP | INFO | `core/internal/bootstrap/scp-deliver.sh:45-47` | TRAP[DECISION] документирует DRY_RUN guard | Guard `[[ "${DRY_RUN:-}" == "true" ]]` КОРРЕКТЕН: bootstrap.sh:54 инициализирует `DRY_RUN=false` (строка), `${DRY_RUN:+--dry-run}` всегда true. | Без изменений. Кодер 108 прав. |

### Contract Violations

| # | Severity | Module | Issue |
|---|:--------:|--------|-------|
| CV1 | HIGH | `core/internal/lint/doc_header_validator.py` → `check_md_sh_refs` | Функция нарушает Small Simple Blocks: ~150 LOC (строки 231-380) — expand-path логика, find-рекурсия, lib/ strip. Рекомендация: извлечь `_resolve_sh_ref_path` и `_find_sh_file_recursive` в отдельные функции. |
| CV2 | INFO | `core/internal/bootstrap/core_deliverer.py` → `cli()` | CLI-функция 75 LOC с детальным argparse — соответствует SRP, но выходит за пределы целевого ~230 LOC. |

---

## Section 3 — Invariant Status (Phase 3)

| # | Инвариант (из root AGENTS.md) | Статус | Evidence |
|---|-------------------------------|:------:|----------|
| I1 | Makefile — единый фасад | HELD | `make lint` вызывает `core/entrypoints/validate.sh --lint` → `core/internal/validate/validate.sh` (API не менялось, AC6). |
| I3 | org = context | HELD | Не затронуто. |
| I4 | AGENTS.md — 3 канонических файла | HELD | `core/internal/bootstrap/AGENTS.md` обновлён (TASK-5 merged), @scope +core_deliverer.py. |
| I6 | make bootstrap-node — идемпотентный | HELD | `scp_to_server()` сохраняет exit code passthrough. DRY_RUN guard корректен. |
| I8 | LiteLLM — PostgreSQL | HELD | Не затронуто. |
| I11 | Manifest Generation Contract | AT_RISK | `test_linter_parity` gate сравнивает bash vs Python linter — теперь оба пути вызывают один Python-модуль. Gate нуждается в пересмотре: сравнение Python (inline в тесте) vs Python (через фасад). |
| Языковая политика | Новый код = Python | HELD | 4 новых Python-модуля, 2 shell-фасада ≤40 LOC. 0 inline python3/heredoc. |

---

## Section 4 — Test Quality (Phase 4)

### Coverage Gaps

| Gap | Detail |
|-----|--------|
| GAP-1 | `test_core_rsync_excludes_git` — VACUOUS PASS. Gate потерял реальное покрытие: исключения rsync переехали в Python, shell-фасад не содержит rsync-команд. Нужен тест, проверяющий RSYNC_EXCLUDES_CORE в core_deliverer.py. |
| GAP-2 | `test_check_doc_headers_equivalent` — FALSE NEGATIVE. Gate валидирует shell-функции, которые удалены. Нужен тест, проверяющий эквивалентные функции в doc_header_validator.py. |
| GAP-3 | `test_linter_parity` — BROKEN. Bash-linter теперь = Python-linter → сравнение Python с Python. Тест не изолирует temp-окружение для Python-модуля. |

### Test Health Score

| Метрика | Значение |
|---------|----------|
| Всего новых unit-тестов (106+108) | 29 (15 grepsummary/doc_header + 14 core_deliverer) |
| Все проходят (unit) | 29/29 ✅ |
| Legacy-тесты требуют адаптации | 6 (4 coder flags + 2 gate failures) |
| VACUOUS PASS | 1 (test_core_rsync_excludes_git) |
| Skip rate (в gate) | 15/272 = 5.5% (все легитимные — no Docker/env) |
| IMP:9 assertion в тестах | ✅ Все 29 unit-тестов имеют caplog IMP:9 траекторию |

**Health Score: 72/100**
- −10: 2 BLOCKER gate failures
- −3: 1 VACUOUS PASS (потеря покрытия)
- −5: 1 AT_RISK invariant (I11)
- −5: 4 legacy теста требуют адаптации
- −5: 2 gate теста деградировали (ложные срабатывания)

---

## Section 5 — Runtime Validation (Phase 5)

### Test Results

| Suite | Command | Result |
|-------|---------|:------:|
| Plan 106 unit | `pytest tests/unit/test_grepsummary_validator.py tests/unit/test_doc_header_validator.py -v` | **15/15 PASS** ✅ |
| Plan 108 unit | `pytest tests/unit/test_core_deliverer.py tests/unit/test_overlay_deliverer.py tests/unit/test_remote_executor.py -q` | **36/36 PASS** ✅ |
| Flag 1 (rsync excludes) | `pytest tests/test_deploy_delivery_static.py::test_rsync_excludes_runtime_artifacts -v` | **FAIL** ❌ — `--exclude='default-user.xml'` not found in scp-deliver.sh |
| Flag 2 (deploy SSH ×4) | `pytest tests/test_contract_deploy_ssh.py -v` | **4/4 FAIL** ❌ — Все exit_code=1, IMP:9 логи не совпадают |
| Flag 3 (rsync command gen) | `pytest tests/test_bootstrap_auto.py::test_rsync_command_generation -v` | **FAIL** ❌ — 0 rsync found (expected 5) |
| Flag 4 (gate rsync git) | `pytest tests/gates/test_gate_context_overlay_git.py::test_core_rsync_excludes_git -v` | **PASS** ⚠️ (VACUOUS — 0 rsync = 0 violations) |
| Gate (static, -m "gate and not requires_docker") | `pytest tests/gates/ -m "gate and not requires_docker" -v` | **255/272 PASS, 2 FAIL, 15 skip** |

### Gate Failures Detail

**FAIL #1 — `test_check_doc_headers_equivalent`** (tests/gates/test_gate_ci_coverage.py:661)
```
check-doc-headers.sh missing 5 check(s):
  - GREP_SUMMARY validation (function check_grep_summary not found)
  - MODULE_CONTRACT validation (function check_module_contract not found)
  - STRUCTURE validation (function check_structure not found)
  - Region balance check (function check_regions_balanced not found)
  - YAML @purpose check (function check_yaml_purpose not found)
```
**Root cause:** Gate статически инспектирует check-doc-headers.sh (shell) на имена функций. После Plan 106 shell = 17 LOC фасад с единственной строкой `exec python3 -m core.internal.lint.doc_header_validator doc-headers "$@"`. Функции переехали в `doc_header_validator.py`.

**FAIL #2 — `test_linter_parity`** (tests/gates/test_gate_lint_quality.py:226)
```
Python linter flagged targets that Bash did not: ['deploy-node', 'foobar', 'push-core', 'random-task']
Bash linter missed expected FAIL targets: ['deploy-node', 'foobar', 'push-core', 'random-task']
```
**Root cause:** `_run_bash_linter` создаёт temp Makefile и запускает `bash core/entrypoints/lint.sh namelint` → `exec python3 -m core.internal.lint.doc_header_validator namelint`. Python-модуль использует `Path(__file__).resolve().parents[4]` = реальный repo root — игнорирует temp Makefile. Реальные .PHONY targets не содержат forbidden-имён → 0 FAIL. Python-linter в тесте (`_run_python_linter`) использует захардкоженный `_TEST_TARGETS` dict → 4 FAIL.

### Coder Flag 4 — DRY_RUN Analysis

**Guard:** `[[ "${DRY_RUN:-}" == "true" ]]` в scp-deliver.sh:50
**Вердикт:** **CORRECT**. `bootstrap.sh:54` инициализирует `DRY_RUN=false` (строка, не boolean). `${DRY_RUN:+--dry-run}` всегда раскрывается (строка "false" непустая). Сравнение `"${DRY_RUN:-}" == "true"` — единственный корректный способ.

### Acceptance Criteria Verification

#### Plan 106

| AC | Status | Evidence |
|----|:------:|----------|
| AC1 | ✅ | `core/internal/lint/grepsummary_validator.py` (321 LOC). CLI `python3 -m ... scan-all`. 6 unit-тестов PASS. |
| AC2 | ✅ | `core/internal/lint/doc_header_validator.py` (566 LOC). CLI `doc-headers` / `namelint`. 9 unit-тестов PASS. |
| AC3 | ✅ | `wc -l core/entrypoints/lint.sh` = 40 (≤40). `exec python3 -m` вызовы. |
| AC4 | ✅ | `wc -l core/entrypoints/check-doc-headers.sh` = 17 (≤40). `exec python3 -m` doc-headers "$@". |
| AC5 | ✅ | `grep -cE 'check_grepsummary\|check_sh_refs_in_md' core/entrypoints/lint.sh` = 0. `grep grepsummary_validator` → 1 match. |
| AC6 | ⚠️ | `make lint` не протестирован (BLOCKED bash). `git diff ... validate.sh` = empty (файлы не тронуты, AC6: поток не меняется). |
| AC7 | ⚠️ | `pre-commit run ...` не протестирован (BLOCKED bash). Фасады сохраняют пути и имена → hook entry-строки не менялись. |
| AC8 | ✅ | Manifest `delegates_to` содержит оба модуля (lines 152-153, 159). Gate `test_delegates_to_paths_exist` PASS. |
| AC9 | ❌ | Gate: 2 FAIL (test_check_doc_headers_equivalent, test_linter_parity) — оба вызваны 106/108. |
| AC10 | ✅ | Все false-positive исключения покрыты тестами: `test_sh_ref_scan_exceptions_preserved` (http, /opt/, проза, ..), `test_validate_file_ext_filter` (.venv/node_modules/__pycache__), `test_md_sh_refs_resolution` (lib/ssh.sh, абсолютные /*). |

#### Plan 108

| AC | Status | Evidence |
|----|:------:|----------|
| AC1 | ✅ | `core_deliverer.py` (450 LOC) — все 7 функций + CLI `deliver`. 14 unit-тестов PASS. |
| AC2 | ✅ | `wc -l core/internal/bootstrap/scp-deliver.sh` = 59 (≤60). `prepare_ssh_opts()` unchanged, `scp_to_server()` → python3. |
| AC3 | ⚠️ | `deliver_all()` последовательность 6 шагов статически верифицирована (ensure_remote_dirs → deliver_core → deliver_platform_env → deliver_makefile → deliver_node_configs → deliver_secrets). Интеграционный тест невозможен без VPS. Unit-тесты покрывают fail-fast и success-путь. |
| AC4 | ✅ | `test_overlay_deliverer.py` — 11/11 PASS без модификации. `sync_core_to_vps` делегирует в `deliver_core()`. |
| AC5 | ✅ | DRY_RUN guard корректен (анализ выше). `test_dry_run_no_execution` PASS (0 subprocess, IMP:8). |
| AC6 | ⚠️ | Аудит-трейл: IMP:8-10 логи в core_deliverer.py совпадают с AC6 таблицей DevPlan по содержанию событий. Формат префикса изменён (`[bootstrap][scp]` → `[<function>][<block>]`) — задокументировано в DevPlan:436. |
| AC7 | ✅ | Exclude-паттерны: `test_deliver_core_excludes_exact` — 5 паттернов; `test_deliver_node_configs_excludes` — 3; `test_deliver_secrets_excludes_and_skip` — 1. Все PASS. |
| AC8 | ❌ | Gate: 2 FAIL (те же, что AC9 Plan 106). Новые unit-тесты в tests/unit/ (не gate, не регистрируются). |

---

## Section 6 — Config Sync (Phase 6)

### Env Variable Propagation

Не затрагивается. API фасадов сохраняется — `prepare_ssh_opts()` и `scp_to_server()` вызываются из тех же entrypoints с теми же параметрами.

### Compose Override Consistency

Не затрагивается. Модули 106 и 108 не меняют docker-compose файлы.

### Manifest Consistency

| Проверка | Результат |
|----------|:---------:|
| `lint.sh` delegated to `grepsummary_validator.py` + `doc_header_validator.py` | ✅ (manifest:152-153) |
| `check-doc-headers.sh` delegated to `doc_header_validator.py` | ✅ (manifest:159) |
| `scp-deliver.sh` consumer `lib/ssh.sh` | ✅ (manifest:527, без изменений) |
| `make check-manifests` | ⚠️ Не протестирован (BLOCKED bash). Gate `test_manifests_up_to_date` PASS. |

---

## Problem Table

| # | Severity | План | Тест/Файл | Проблема | Рекомендуемый фикс |
|---|:--------:|------|-----------|----------|-------------------|
| P1 | **BLOCKER** | 106 | `tests/gates/test_gate_ci_coverage.py::test_check_doc_headers_equivalent` | Ищет shell-функции в check-doc-headers.sh; функции переехали в doc_header_validator.py | Обновить gate: проверять наличие `python3 -m core.internal.lint.doc_header_validator doc-headers` в фасаде + эквивалентные функции в Python-модуле (grep `def check_grep_summary_presence\|def check_module_contract\|def check_structure\|def check_regions_balanced\|def check_yaml_purpose`) |
| P2 | **BLOCKER** | 106 | `tests/gates/test_gate_lint_quality.py::test_linter_parity` | Bash-linter exec'ит Python (тот же код), temp Makefile игнорируется | (a) Удалить bash-linter путь (оба — одно и то же), сравнивать Python-модуль с inline-логикой напрямую; (b) monkeypatch `_default_repo_root()` для temp-изоляции ИЛИ (c) параметризовать repo_root в `validate_make_target_names()` |
| P3 | **BLOCKER** | 108 | `tests/test_deploy_delivery_static.py::test_rsync_excludes_runtime_artifacts` | Ищет `--exclude` в scp-deliver.sh (shell); исключения в core_deliverer.py | Вариант A: Расширить проверку на core_deliverer.py (`RSYNC_EXCLUDES_CORE` + `RSYNC_EXCLUDES_NODE` + `RSYNC_EXCLUDES_SECRETS`). Вариант B: Добавить новый тест в test_core_deliverer.py и замаркировать старый как skip с `reason="rsync excludes moved to core_deliverer.py (DevPlan 108)"` |
| P4 | **BLOCKER** | 108 | `tests/test_contract_deploy_ssh.py` (×4 теста) | Мокают shell-функции в scp-deliver.sh; логика в Python | Заменить shell-моки на mock `subprocess.run` в core_deliverer.py. Альтернатива: удалить тесты (core-логика покрыта 14 unit-тестами test_core_deliverer.py) |
| P5 | **BLOCKER** | 108 | `tests/test_bootstrap_auto.py::test_rsync_command_generation` | Ищет rsync в scp-deliver.sh (0 found); команды в core_deliverer.py | Перенаправить: grep `deliver_core\|deliver_platform_env\|deliver_makefile\|deliver_node_configs\|deliver_secrets` в core_deliverer.py + assert их вызовов |
| P6 | **MAJOR** | 108 | `tests/gates/test_gate_context_overlay_git.py::test_core_rsync_excludes_git` | VACUOUS PASS — 0 rsync в фасаде, потеря покрытия | Перенаправить на RSYNC_EXCLUDES_CORE/NODE/SECRETS в core_deliverer.py. assert `--exclude=.git` присутствует во всех трёх списках |
| P7 | **MAJOR** | 106 | `core/internal/lint/doc_header_validator.py::check_md_sh_refs` | Функция ~150 LOC — нарушает Small Simple Blocks | Извлечь `_resolve_sh_ref_path()` и `_find_sh_file_recursive()` в отдельные функции |
| P8 | **MAJOR** | 106 | `core/internal/lint/doc_header_validator.py` | LOC 566 vs target ~380 (+49%) | (a) Ужать check_md_sh_refs через извлечение подфункций; (b) сократить docstrings где возможно без потери семантики |
| P9 | **MAJOR** | 108 | `core/internal/bootstrap/core_deliverer.py` | LOC 450 vs target ~230 (+96%) | (a) Рассмотреть извлечение argparse CLI в отдельный `cli.py`; (b) сократить docstrings; (c) извлечь `_build_rsync_cmd()` helper |
| P10 | **MAJOR** | 106+108 | Gate suite (make gate MODE=fast) | 2 BLOCKER failures → gate красный. merge невозможен. | Fix P1 + P2 (gate failures). Fix P3-P6 (legacy test adaptation). После фиксов: `make test-inventory-sync && make gate MODE=fast` должен быть зелёным. |
| P11 | MINOR | 106 | `core/entrypoints/lint.sh:40` | AC3: ровно 40 LOC — нулевой запас. Любое добавление (даже 1 строка) нарушит AC3. | При будущих правках — ужимать комментарии или переносить в Python. |
| P12 | MINOR | 108 | `core/internal/bootstrap/scp-deliver.sh:59` | AC2: 59 LOC — запас 1 строка. | При будущих правках — ужимать модульный контракт. |
| P13 | MINOR | 108 | `core_deliverer.py` — timeout rsync=600 | Hardcoded константа. DevPlan D5: «если понадобится — параметризация timeout». | Добавить `--rsync-timeout` CLI-аргумент (не блокирует merge). |

---

## Вердикт

### DevPlan 106 (lint-headers-consolidation): **CHANGES-REQUIRED**

Причина: 2 BLOCKER gate-регрессии (P1, P2). 3 MAJOR замечания по качеству кода (P7, P8, P11).
- Все unit-тесты зелёные (15/15). ✅
- Фасады соответствуют AC3/AC4. ✅
- Manifest delegates_to корректен (AC8). ✅
- Исключения сохранены (AC10). ✅
- Gate красный из-за P1+P2 — блокирует merge. ❌

### DevPlan 108 (scp-deliver-python): **CHANGES-REQUIRED**

Причина: 3 BLOCKER legacy-теста (P3, P4, P5) + 1 MAJOR VACUOUS PASS (P6) + 1 MAJOR LOC overshoot (P9). Gate красный (P1+P2 — cross-plan impact).
- Все unit-тесты зелёные (14/14 core_deliverer + 11/11 overlay). ✅
- Фасад ≤60 LOC (AC2). ✅
- Overlay delegation корректен (AC4). ✅
- Exclude-паттерны точные (AC7). ✅
- 4 legacy-теста сломаны (P3-P6) — блокирует merge. ❌
- Gate красный из-за P1+P2 (cross-plan) + P6 (VACUOUS PASS). ❌

### Общий вердикт: **CHANGES-REQUIRED**

**Merge BLOCKED** до исправления:
1. P1 + P2 (gate-регрессии, оба плана)
2. P3 + P4 + P5 (legacy-тесты, план 108)
3. P6 (VACUOUS PASS — потеря покрытия, план 108)

Рекомендуемый порядок волны фиксов:
- **Wave Fix-1:** P1 (gate check_doc_headers) + P2 (gate linter_parity) — восстановить зелёный gate
- **Wave Fix-2:** P3 + P4 + P5 + P6 — адаптировать/перенаправить legacy-тесты
- **Wave Fix-3:** P7 + P8 + P9 — качество кода (LOC reduction, извлечение функций) — non-blocking
- **Wave Fix-4:** P11 + P12 + P13 — минорные улучшения

---

## Post-Fix Verification Commands

```bash
# 1. Unit tests (must stay green)
pytest tests/unit/test_grepsummary_validator.py tests/unit/test_doc_header_validator.py -v
pytest tests/unit/test_core_deliverer.py tests/unit/test_overlay_deliverer.py tests/unit/test_remote_executor.py -q

# 2. Fixed legacy tests (must become green)
pytest tests/test_deploy_delivery_static.py::test_rsync_excludes_runtime_artifacts -v
pytest tests/test_contract_deploy_ssh.py -v
pytest tests/test_bootstrap_auto.py::test_rsync_command_generation -v

# 3. Fixed gate (must become green)
pytest tests/gates/test_gate_ci_coverage.py::test_check_doc_headers_equivalent -v
pytest tests/gates/test_gate_lint_quality.py::test_linter_parity -v
pytest tests/gates/test_gate_context_overlay_git.py::test_core_rsync_excludes_git -v

# 4. Full gate
pytest tests/gates/ -m "gate and not requires_docker" -v

# 5. Inventory sync
make test-inventory-sync

# 6. Final gate
make gate MODE=fast
```

$END_VERIFICATION_REPORT
