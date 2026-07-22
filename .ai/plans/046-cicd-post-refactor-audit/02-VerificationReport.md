# 046-VerificationReport: CI/CD Post-Refactor Audit DevPlan QA

**SHA:** `ee5c3268b4503e8a969868b1015ff1b28c4ee118`
**Date:** 2026-07-22
**QA Scope:** STANDARD (19 files in manifest + CI config expansion)
**Source:** StatusReport 046 §5 (Implementation Plan / DevPlan)

$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Pre-implementation QA верификация DevPlan (StatusReport §5). Проверить корректность диагноза, полноту плана, отсутствие дрейфа между планом и реальным состоянием кодовой базы. Выявить критические пропуски в плане до начала реализации.
DESCRIPTION:           Кросс-файловая верификация всех утверждений StatusReport (13 inline python3, stale manifest, sha-resolve outputs, pre-commit scope, yaml_query capabilities). Выявление дублирования с существующим кодом, неполноты предлагаемых изменений, устаревших метрик. Runtime validation (pytest 671/672 PASS).
RATIONALE:             StatusReport объединяет диагностику и DevPlan в одном документе. Перед запуском Coder-волны необходимо убедиться, что план не создаст новых проблем (дублирование, неполные фиксы, конфликты с существующим кодом).
ACCEPTANCE_CRITERIA:
  1. Все утверждения StatusReport о baseline (13 inline, count=8, sha-resolve outputs) верифицированы против реального кода
  2. Все предлагаемые CREATE-файлы проверены на коллизии с существующими артефактами
  3. Все MODIFY-изменения проверены на полноту (hook script, не только pre-commit config)
  4. Инварианты AGENTS.md проверены на предмет нарушения предлагаемыми изменениями
  5. Runtime test suite пройден — baseline pass rate зафиксирован
IMPLEMENTS:            Принцип 9 (Read before Act) — прочитаны все workflow-файлы, composite actions, pre-commit config, hook script, discover_modules.py, yaml_query.py, entrypoint-manifest.yaml, AGENTS.md инварианты. Принцип 5 (Fail-Fast) — отклонения найдены до реализации.
IMPACTS:               Настоящий файл. Рекомендации к доработке DevPlan перед запуском Coder.
REQUIRES:              StatusReport 046 (01-StatusReport.md), AGENTS.md, core/AGENTS.md, 9 workflow-файлов, sha-resolve/action.yml, discover_modules.py, yaml_query.py, pre-commit-config.yaml, check-no-new-inline-python3.sh, entrypoint-manifest.yaml
$END_ARTIFACT_CONTRACT

---

## 1. Static Audit (Phase 1)

### Compliance Matrix — Files Proposed in Manifest

| # | File | T | Exists? | Semantic Markup | Notes |
|---|------|---|---------|-----------------|-------|
| C1 | `core/internal/scripts/module_discovery.py` | T2-CREATE | ❌ | — | **⚠️ GAP-H1**: сходный функционал уже в `core/internal/bootstrap/discover_modules.py` |
| C2 | `core/internal/scripts/validate_dora_dashboard.py` | T3-CREATE | ❌ | — | OK, чистая экстракция |
| C3 | `core/internal/scripts/vps_status_check.py` | T5-CREATE | ❌ | — | **⚠️ GAP-H3**: не покрывает print-only use-case (deploy-project.yml:107) |
| C4 | `.github/actions/discover-modules/action.yml` | T2-CREATE | ❌ | — | OK |
| C5 | `tests/unit/test_module_discovery.py` | T2-CREATE | ❌ | — | OK |
| C6 | `tests/unit/test_validate_dora_dashboard.py` | T3-CREATE | ❌ | — | OK |
| C7 | `tests/unit/test_vps_status_check.py` | T5-CREATE | ❌ | — | OK |
| C8 | `reports/ci-gate-timing-post-wave5.csv` | T8-CREATE | ❌ | — | OK |
| M1 | `core/entrypoint-manifest.yaml` | T1-MODIFY | ✅ L434 | ✅ | Count=8 → 9, верно |
| M2 | `.github/workflows/platform-test.yml` | T2,T3-MODIFY | ✅ | ✅ | 5 inline → экстракция |
| M3 | `.github/workflows/nightly-gate.yml` | T2-MODIFY | ✅ | ✅ | 2 inline → экстракция |
| M4 | `.github/workflows/platform-deploy.yml` | T4-MODIFY | ✅ | ✅ | 1 inline → yaml_query |
| M5 | `.github/workflows/deploy-project.yml` | T4,T5-MODIFY | ✅ | ✅ | 5 inline → экстракция |
| M6 | `.github/actions/sha-resolve/action.yml` | T7-MODIFY | ✅ | ✅ | Добавление `skip` output |
| M7 | `.github/workflows/core-deploy.yml` | T7-MODIFY | ✅ | ✅ (TRAP[DEBT] L55) | Замена inline SHA → composite |
| M8 | `.pre-commit-config.yaml` | T6-MODIFY | ✅ L270 | ✅ | **⚠️ GAP-H2**: только config, hook script тоже нужно менять |
| M9 | `core/internal/hooks/check-no-new-inline-python3.sh` | T6-MODIFY | ✅ | ✅ | **⚠️ GAP-H2**: L22 `core/*.sh` → нужно расширить на `.github/**/*.yml` |
| M10 | `core/internal/scripts/yaml_query.py` | T5-MODIFY | ✅ (201 LOC) | ✅ | Добавление `--stdin` |
| M11 | `core/entrypoint-manifest.yaml` (gates) | T1,T8-MODIFY | ✅ | ✅ | ci-coverage description update |

### Findings

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| SA-01 | INFO | — | 19 files в манифесте — STANDARD scope, корректно |

---

## 2. Drift Analysis (Phase 2)

### Verified StatusReport Claims

| Claim | Status | Evidence |
|-------|--------|----------|
| "13 inline python3 в .github/workflows/" | ✅ CONFIRMED | grep: 13 matches в 4 файлах (platform-test:5, deploy-project:5, nightly-gate:2, platform-deploy:1) |
| "entrypoint-manifest.yaml:434 count=8" | ✅ CONFIRMED | L434: `Workflow count=8` — должен быть 9 |
| "9 workflow-файлов" | ✅ CONFIRMED | `ls .github/workflows/*.yml` = 9 файлов |
| "sha-resolve composite имеет только sha output" | ✅ CONFIRMED | `.github/actions/sha-resolve/action.yml` L29-32: только `outputs.sha`, нет `skip` |
| "core-deploy.yml inline SHA verification" | ✅ CONFIRMED | L55-95: inline steps 1-2, TRAP[DEBT] зафиксирован |
| "pre-commit hook files: '^core/.*\.sh$'" | ✅ CONFIRMED | `.pre-commit-config.yaml` L270 |
| "yaml_query.py существует, нет --stdin" | ✅ CONFIRMED | 201 LOC, grep `--stdin` = 0 matches |
| "test_gate_workflow_consistency ожидает 9" | ✅ CONFIRMED | `_EXPECTED_WORKFLOW_COUNT: int = 9` (L58) |
| "1 system-модуль (platform-secrets)" | ✅ CONFIRMED | Только `platform-secrets/module.yaml` содержит `install_type: system` |

### Drift Register (NEW findings — GAPS in the DevPlan)

| DRIFT-ID | Severity | Description |
|----------|----------|-------------|
| **DRIFT-046-1** | 🔴 **HIGH** | **GAP-H1**: Plan creates `module_discovery.py` ignoring existing `discover_modules.py` |
| **DRIFT-046-2** | 🔴 **HIGH** | **GAP-H2**: T6 hook fix incomplete — pre-commit `files:` changed, but hook script `git diff --cached -- 'core/*.sh'` NOT changed |
| **DRIFT-046-3** | 🟠 **MED** | **GAP-H3**: `vps_status_check.py` doesn't output status — can't replace deploy-project.yml:107 print-only inline |
| **DRIFT-046-4** | 🟠 **MED** | **GAP-M3**: CICD-04 metric stale — 204/210 (97.1%) is outdated, current is 671/672 (99.85%) |
| **DRIFT-046-5** | 🟡 **LOW** | **GAP-M2**: deploy-project.yml:69 inline uses `import yaml` — T4 заменяет на `yaml_query.py` (тоже требует PyYAML). Замена синтаксическая, не архитектурная |
| **DRIFT-046-6** | 🟡 **LOW** | **GAP-L1**: File Manifest не включает `tests/gates/test_gate_workflow_consistency.py` — файл затронут верификацией T1 |

### Detailed Drift Analysis

#### DRIFT-046-1: 🔴 HIGH — Дублирование с существующим discover_modules.py

**Файлы:**
- Существующий: `core/internal/bootstrap/discover_modules.py` (234 LOC, функции `discover_modules()`, `discover_test_infra()`, `update_compose_include()`)
- Предлагаемый: `core/internal/scripts/module_discovery.py` (~60 LOC, функция `discover_docker_modules()`)

**Функциональное пересечение:** Оба модуля решают задачу «найти docker-модули, исключая system-модули»:
- `discover_modules.py::discover_modules()` — использует `yaml.safe_load()` → проверяет `install_type: system`
- `module_discovery.py::discover_docker_modules()` — использует `'install_type: system' not in m.read_text()` (текстовый поиск)

**Почему Plan не замечает существующий код:**
- `discover_modules.py` зарегистрирован в `entrypoint-manifest.yaml` (L225-228) как `make discover-modules`
- `discover_modules.py` читает `module.yaml` через YAML (требует PyYAML)
- CI inline НЕ использует `yaml` (потому что CI-раннер может не иметь PyYAML)
- Plan предлагает текстовый подход как «более лёгкий» для CI

**В чём проблема:** Plan никак не упоминает существующий `discover_modules.py`. Это создаёт:
1. Два модуля с пересекающейся ответственностью — drift risk
2. Разные подходы к фильтрации (YAML vs text grep) — inconsistency risk
3. Путаницу в naming: `discover_modules.py` vs `module_discovery.py`

**Рекомендация:**
- **Вариант A (рекомендуемый):** Добавить `--ci-list --format json` флаг в существующий `discover_modules.py` с fallback на текстовый режим (если PyYAML недоступен). Не создавать новый файл.
- **Вариант B:** Создать `module_discovery.py`, добавить `## @see core/internal/bootstrap/discover_modules.py` и `## @rationale Separate CI-only module — text-based (no YAML dep) vs bootstrap YAML-parsing version`.
- **Вариант C:** Установить PyYAML в CI (pip install pyyaml) и использовать существующий `discover_modules.py` с флагом `--ci-list`.

#### DRIFT-046-2: 🔴 HIGH — T6 hook fix incomplete

**Файлы:**
- `.pre-commit-config.yaml:270` — `files: '^core/.*\.sh$'` → Plan меняет на `'^(core/.*\.sh|\.github/.*\.yml)$'`
- `core/internal/hooks/check-no-new-inline-python3.sh:22` — `git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh'` — Plan НЕ меняет

**Проблема:** Pre-commit `files:` фильтр определяет, на каких файлах ЗАПУСКАТЬ hook. Hook script сам выбирает, какие файлы ПРОВЕРЯТЬ через `git diff --cached -- 'core/*.sh'`. Изменение только `files:` в `.pre-commit-config.yaml` приведёт к тому, что:
1. Pre-commit ВЫЗОВЕТ hook для `.github/**/*.yml`
2. Hook script прочитает `git diff --cached -- 'core/*.sh'` → вернёт пустой список
3. Hook завершится с exit 0 (нет staged .sh файлов) — НИЧЕГО НЕ ПРОВЕРИТ

**Исправление:** Нужно изменить строку 22 в `check-no-new-inline-python3.sh`:
```bash
# Было:
staged_files=$(git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh' || true)
# Стало:
staged_files=$(git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh' '.github/workflows/*.yml' '.github/actions/*/action.yml' || true)
```

**Дополнительно:** Требуется whitelist-доработка (упомянута в Plan как TRAP[DESIGN]) — hook должен различать `python3 -c "print('hello')"` (OK) и `python3 -c "import json; ..."` (BLOCK).

#### DRIFT-046-3: 🟠 MED — vps_status_check.py не покрывает print-only use-case

**Файл:** `.github/workflows/deploy-project.yml:107`
```bash
echo "[IMP:9][preflight] VPS readiness check passed (status: $(echo "${STATUS_JSON}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status'))"))"
```

**Проблема:** Это inline `python3 -c` внутри `$(...)` subshell — используется для ПЕЧАТИ статуса (не валидации). Предлагаемый `vps_status_check.py`:
- Возвращает exit 0/1/2/3
- Пишет `[IMP:9][vps-status] VPS project status: {status}` в stdout
- НЕ возвращает голый статус (`found`, `stub`) для использования в subshell

**Прямая замена невозможна:** `$(python3 core/internal/scripts/vps_status_check.py)` вернёт полную строку лога, а не `found`/`stub`.

**Рекомендация:** Добавить `--output-status-only` флаг в `vps_status_check.py`, который печатает только значение статуса без логов:
```python
parser.add_argument("--output-status-only", action="store_true",
                    help="Print only the status value (for subshell use)")
# В main():
if args.output_status_only:
    data = json.load(sys.stdin)
    print(data.get("status", ""))
    sys.exit(0)
```

#### DRIFT-046-4: 🟠 MED — CICD-04 metric stale

**StatusReport утверждает:** 6 pre-existing test failures (204/210, 97.1%)

**Фактический runtime:** 671 passed, 1 failed, 15 skipped (99.85%)

**Причина расхождения:** 5 fix-коммитов (CICD-06) исправили большинство failures. Единственный оставшийся failure — `test_e2e_health.py::test_service_health[langfuse-/api/public/health-False]` (401 Unauthorized) — конфигурационный, не CI/CD.

**Влияние на Plan:** План корректно помечает CICD-04 как DEFERRED→042, но использует устаревшую метрику 97.1%. Следует обновить baseline до 99.85%.

---

## 3. Invariant Verification (Phase 3)

| # | Invariant (из AGENTS.md) | Status | Evidence | Risk |
|---|--------------------------|--------|----------|------|
| 1 | Makefile — единый фасад | HELD | Plan не добавляет новые entrypoints — только Python-модули и composite actions | — |
| 2 | Модель деплоя: git push → CI | HELD | Plan не меняет модель деплоя | — |
| 3 | org = context | HELD | Plan не затрагивает org-логику | — |
| 4 | 3 канонических AGENTS.md | HELD | Plan не создаёт новых AGENTS.md | — |
| 5 | entrypoint-manifest.yaml | AT_RISK | T1 правит manifest (count: 8→9) — корректно. T8 добавляет gate-fast-time — новый descriptive контент | LOW: изменения в description, не в структуре |
| 7 | Полный локальный стек через docker compose up | HELD | Plan не затрагивает compose | — |
| 8 | LiteLLM — PostgreSQL | HELD | Plan не затрагивает LiteLLM | — |
| 9 | Тестовый сервер может быть пересоздан | HELD | Plan не затрагивает тестовый сервер | — |
| 10 | Сборка образов hermes | HELD | Plan не затрагивает hermes | — |
| — | Языковая политика (Python-first) | AT_RISK | Plan экстрагирует inline python3 из CI в Python-модули — соответствует духу политики. Но сама политика в AGENTS.md говорит о shell-скриптах, не о CI YAML. | LOW: Plan усиливает политику, а не нарушает |

### Invariant: entrypoint-manifest.yaml целостность

**Проверка:** Plan предлагает изменить 2 описания (T1: count=8→9, T8: добавить gate-fast-time). Это допустимые изменения в human-readable полях — структура gates не меняется.

### Invariant: Языковая политика — CI YAML scope

**Анализ:** AGENTS.md §Языковая политика п.3: «Inline Python и heredoc — сигнал к извлечению. Любой `python3 -c "..."` или `python3 - <<PYEOF` в bash-скрипте…». CI workflow `run:` блоки с `shell: bash` — технически bash-скрипты. Политика применима.

**Текущее состояние:** 13 inline в CI — нарушение духа политики (не тестируемо, не grep-able). Plan устраняет все 13 → приведение в соответствие.

**Заключение:** AT_RISK (LOW) — политика расширяется на CI без изменения текста AGENTS.md.

---

## 4. Test Quality (Phase 4) — сокращённая для STANDARD

| Метрика | Значение |
|---------|----------|
| Total tests | 687 |
| Passed | 671 |
| Failed | 1 (e2e health langfuse — 401, конфигурационный) |
| Skipped | 15 |
| Pass rate | 99.85% |
| Gate tests pass | 14/14 (100%) |

**Skip analysis:**
- 1 skip по причине GNU Make limitation (`make -n` с `$(eval ...)`) — легитимный
- 12 skips: модули без hooks — не gate failure (легитимный informational skip)
- 1 skip: нет projects/ директории — dev environment (легитимный)
- 1 skip: extra pytest markers — non-critical

**Вывод:** Test suite здоров. Baseline pass rate = 99.85%. Единственный failure не связан с CI/CD.

---

## 5. Runtime Validation (Phase 5)

### Test Results

```
$ python3 -m pytest tests/ -s -v -x --tb=short
671 passed, 1 failed, 15 skipped in 68.16s
```

**Единственный failure:** `tests/test_e2e_health.py::test_service_health[langfuse-/api/public/health-False]`
- Причина: langfuse возвращает 401 (требуется аутентификация)
- Не связан с изменениями Plan — pre-existing, конфигурационный

### Gate Test Suite

```
$ python3 -m pytest tests/gates/ -v
14 passed in 0.26s  (workflow_consistency — все тесты зелёные)
```

### LDD Trace Analysis

```
[IMP:9][conftest][sessionstart] Attempt #1 — running tests...
[IMP:9][conftest][sessionfinish] 100% PASS — counter reset to 0
[IMP:9][conftest][sessionfinish] FAILURES DETECTED — attempt #0
```

IMP:9 логи присутствуют — anti-illusion check PASS.

### Acceptance Criteria (из StatusReport §1 Diagnostic)

| AC | Status | Evidence |
|----|--------|----------|
| 1. Все workflow-файлы проверены на соответствие языковой политике | ✅ | 13 inline подтверждены grep |
| 2. entrypoint-manifest.yaml gates проверены на stale descriptions | ✅ | count=8 (stale) подтверждён, test expects 9 |
| 3. SHA-aware aggregator pattern consistency проверена | ✅ | sha-resolve composite корректен, core-deploy inline — отклонение (CICD-03) |
| 4. Composite action usage coverage проверена | ⚠️ | AC9 Brief 027 (≤3 checkout): 11 checkout@v7 в 9 файлах — цель не достигнута, но Plan это не фиксит |
| 5. Gate test suite health проверена | ✅ | 14/14 gate tests pass |

---

## 6. Config Sync Audit (Phase 6) — сокращённая для STANDARD

### Pre-commit Hook Propagation Chain

```
.pre-commit-config.yaml:270 (files: '^core/.*\.sh$')
  → check-no-new-inline-python3.sh:22 (git diff --cached -- 'core/*.sh')
    → grep 'python3 -c' (line 36)
```

**Drift:** Оба звена цепи фильтруют только `core/*.sh`. Plan (T6) меняет первое звено (pre-commit `files:`) но не второе (hook script glob). **CHAIN BREAK** на втором звене.

### Workflow Composite Action Chain

```
build-platform.yml → sha-resolve (composite) ✅
mirror.yml → sha-resolve (composite) ✅
core-deploy.yml → inline SHA (TRAP[DEBT]) ❌ → T7 fixes
```

---

## 7. Risk Assessment

| ID | Risk | Severity | Status |
|----|------|----------|--------|
| R-046-1 | sha-resolve backward compat (Plan уже учтён) | MED | ✅ Mitigated |
| R-046-2 | module_discovery порядок модулей (Plan уже учтён) | LOW | ✅ Mitigated |
| R-046-3 | Pre-commit false-positive (Plan TRAP[DESIGN] учтён) | LOW | ✅ Mitigated |
| R-046-4 | --stdin conflict с --file (Plan уже учтён) | LOW | ✅ Mitigated |
| **R-046-5** | **Дублирование discover_modules (DRIFT-046-1)** | **HIGH** | ❌ **NOT in Plan** |
| **R-046-6** | **Hook fix incomplete (DRIFT-046-2)** | **HIGH** | ❌ **NOT in Plan** |
| **R-046-7** | **vps_status_check не покрывает print (DRIFT-046-3)** | **MED** | ❌ **NOT in Plan** |

---

## Semantic Verdict

```
VERDICT: DRIFTED (HIGH)

Justification:
  - 2 HIGH-severity drifts (DRIFT-046-1, DRIFT-046-2) — Plan gaps that would
    create functional problems during implementation:
    1. DRIFT-046-1: Creating module_discovery.py without acknowledging existing
       discover_modules.py → code duplication + naming confusion
    2. DRIFT-046-2: T6 pre-commit hook extension misses hook script changes →
       hook would silently pass on yml files (exit 0 with no files checked)

  - Diagnostics (13 inline, stale manifest, sha-resolve structure) — VERIFIED ACCURATE
  - Task decomposition (T1-T9) — STRUCTURALLY SOUND
  - File manifest — INCOMPLETE (missing hook script fix, missing existing discover_modules.py reference)
  - Test suite — HEALTHY (99.85% pass rate)

Recommendation: DO NOT START implementation until DRIFT-046-1 and DRIFT-046-2 are addressed.
Delegate to Architect for DevPlan revision.
```

---

## Required DevPlan Changes

### Must-Fix (before Coder delegation):

1. **DRIFT-046-1:** Добавить в Plan анализ существующего `discover_modules.py`:
   - Решить: extend existing vs create new vs install PyYAML in CI
   - Добавить секцию «Relationship to existing discover_modules.py» в T2
   - Если создаётся новый файл → добавить `## @see` cross-reference и `## @rationale` почему не extend

2. **DRIFT-046-2:** Расширить T6:
   - Добавить изменение `check-no-new-inline-python3.sh:22` (git diff glob)
   - Верификационная команда после T6 должна проверять hook на `.github/**/*.yml`

3. **DRIFT-046-3:** Расширить T5:
   - Добавить `--output-status-only` флаг в `vps_status_check.py`
   - Показать замену для deploy-project.yml:107 (subshell use-case)

### Should-Fix (до реализации, но не блокирует старт):

4. **DRIFT-046-4:** Обновить baseline pass rate: 97.1% → 99.85%
5. **DRIFT-046-5:** Добавить примечание в T4: замена синтаксическая (yaml_query.py тоже требует PyYAML), архитектурно проблема не меняется
6. **DRIFT-046-6:** Добавить `tests/gates/test_gate_workflow_consistency.py` в File Manifest (для контекста верификации T1)

---

## Audit Trail

| Time (UTC+3) | Action | Result |
|-------------|--------|--------|
| 17:10 | Read StatusReport 046 + git rev-parse HEAD | SHA: ee5c326 |
| 17:11 | grep inline python3 in .github/workflows/ | 13 matches confirmed |
| 17:12 | Read sha-resolve/action.yml | Only `sha` output, no `skip` |
| 17:12 | Read pre-commit-config.yaml L260-273 | `files: '^core/.*\.sh$'` confirmed |
| 17:13 | Read check-no-new-inline-python3.sh | L22: `git diff --cached -- 'core/*.sh'` |
| 17:13 | Read entrypoint-manifest.yaml L434 | `Workflow count=8` (stale) |
| 17:14 | Read discover_modules.py (234 LOC) | Existing module with overlapping functionality |
| 17:14 | Read yaml_query.py (201 LOC) | No `--stdin` flag |
| 17:15 | Read deploy-project.yml L69,79,100,107,136 | All 5 inline confirmed |
| 17:15 | Read platform-deploy.yml L94 | 1 inline (STAGING detection) |
| 17:16 | Read test_gate_workflow_consistency.py L58 | `_EXPECTED_WORKFLOW_COUNT = 9` |
| 17:16 | pytest tests/gates/test_gate_workflow_consistency.py | 14 passed |
| 17:17 | pytest tests/ -x | 671 passed, 1 failed, 15 skipped |

$END_VERIFICATION_REPORT
