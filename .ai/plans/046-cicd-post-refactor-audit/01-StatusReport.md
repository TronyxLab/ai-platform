# 046-StatusReport: CI/CD Post-Architecture-Modernization Audit

**Program:** 027-architecture-modernization-program (Decision Gate: validated ✅)
**Analysis date:** 2026-07-22
**Source analysis:** Codebase audit — 9 workflow files, 6 composite actions, Makefile, entrypoint-manifest.yaml, gate tests, Decision Gate 043, VerificationReports W1-W5
**Scope:** CI/CD pipeline integrity after Waves 1-5 refactoring (2026-07-21 → 2026-07-22)

$START_STATUS_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Выявить проблемы CI/CD, оставшиеся после большого рефакторинга (Waves 1-5 программы 027). Зафиксировать дрейфы, несоответствия языковой политике, дубликаты, недозамеренные метрики. Не исправлять — только диагностировать.
DESCRIPTION:           Аудит CI/CD по коду: workflow-файлы, composite actions, Makefile gate target, entrypoint-manifest.yaml gates. Сравнение с ожидаемым состоянием из Brief 027 и Decision Gate 043. Каждый найденный дефект классифицирован по severity с привязкой к конкретным строкам кода.
RATIONALE:             После 5 delivery-волн за 2 дня (Jul 21-22) CI/CD мог получить регрессии. Decision Gate (043) сфокусирован на архитектурных метриках, а не на CI/CD hygiene. Данный аудит проверяет CI/CD изолированно.
ACCEPTANCE_CRITERIA:
  1. Все workflow-файлы проверены на соответствие языковой политике (inline python3)
  2. entrypoint-manifest.yaml gates проверить на stale descriptions
  3. SHA-aware aggregator pattern consistency проверена
  4. Composite action usage coverage проверена
  5. Gate test suite health проверена
IMPLEMENTS:            Принцип 9 (Read before Act) — прочитаны AGENTS.md, entrypoint-manifest.yaml, все workflow-файлы, Decision Gate 043, VerificationReport'ы W1-W5. Принцип 4 (Superposition).
IMPACTS:               Настоящий файл. TRAP[DECISION]/TRAP[DEBT] в affected файлы.
REQUIRES:              01-Brief.md (027), 02-DecisionGate.md (043), VerificationReport'ы W1-W5, все workflow-файлы .github/workflows/*.yml, entrypoint-manifest.yaml
$END_ARTIFACT_CONTRACT

---

## 1. Diagnostic Summary

### Environment Fingerprint

| Аспект | Значение |
|--------|----------|
| Platform | macOS (darwin) — dev machine |
| Git branch | `main` (post-Wave 5) |
| Workflow files | 9 `.yml` в `.github/workflows/` |
| Composite actions | 6 `.github/actions/*/action.yml` |
| Gate tests | ~30+ registered in entrypoint-manifest.yaml |
| Last gate pass rate | 671/672 (99.85%) — 1 конфигурационный failure (langfuse 401) |

### Issues Summary

| ID | Severity | Категория | Кратко |
|----|----------|-----------|--------|
| CICD-01 | 🔴 HIGH | Language Policy Violation | 13 inline `python3 -c` вызовов в `.github/workflows/*.yml` — нарушение языковой политики AGENTS.md |
| CICD-02 | 🟠 MED | Drift | `entrypoint-manifest.yaml` gate `ci-coverage` description: "Workflow count=8" — актуально 9 |
| CICD-03 | 🟠 MED | Duplication | `core-deploy.yml` использует inline SHA verification вместо shared `sha-resolve` action (TRAP[DEBT] уже есть) |
| CICD-04 | 🟡 LOW | Incomplete Migration | 1 pre-existing конфигурационный failure (671/672, 99.85%) — langfuse 401 Unauthorized, не связан с CI/CD |
| CICD-05 | 🟡 LOW | Missing Measurement | CI gate execution time post-Wave 5 не замерен (Baseline W1-E8 есть, post нет) |
| CICD-06 | 🟢 INFO | Active CI/CD Churn | 5 fix-коммитов сегодня (22 Jul) — CI/CD в активной доработке |

---

## 2. Detailed Findings

### CICD-01: 🔴 HIGH — 13 inline `python3 -c` в CI workflow-файлах

**Описание:** В `.github/workflows/*.yml` найдено 13 вызовов `python3 -c "..."` — нарушение языковой политики (AGENTS.md §Языковая политика п.3: "Inline Python и heredoc — сигнал к извлечению").

**Pre-commit hook** `no-new-inline-python3` проверяет только `core/.*\.sh$` и не распространяется на `.github/*.yml`.

**Распределение:**

| Файл | Кол-во | Назначение |
|------|--------|-----------|
| `.github/workflows/platform-test.yml` | 5 | DORA JSON validation, module_list generation, cleanup loop |
| `.github/workflows/deploy-project.yml` | 5 | node resolution (2), status check (2), verify deliver (1) |
| `.github/workflows/nightly-gate.yml` | 2 | module_list generation, cleanup loop |
| `.github/workflows/platform-deploy.yml` | 1 | STAGING environment detection |

**Риск:** Те же самые причины, что и в Brief 027 для shell-скриптов: не тестируемо, не grep-able, не типизировано. При изменении YAML-структуры в workflow — сломается runtime.

**Рекомендация:** Вынести общую логику (module_list generation, node resolution) в Python-модули (`core/internal/scripts/`) или composite actions. Расширить pre-commit hook на `.github/**/*.yml`.

---

### CICD-02: 🟠 MED — Stale description в entrypoint-manifest.yaml

**Файл:** `core/entrypoint-manifest.yaml:350`
**Строка:**
```yaml
  - id: ci-coverage
    description: CI skip documentation, SHA-aware aggregator, MODE=fast exclusions, check-doc-headers, main-full-gate trigger, origin/main fetch, MARKER=all contract
    test_file: test_gate_ci_coverage.py
```

**Проблема:** Более специфично — в описании gate `workflow-consistency`:
```yaml
    description: Workflow count=8, main-full-gate deleted, platform-test single job, ...
```

Актуальное количество workflow-файлов: **9** (build-platform, core-deploy, deploy-project, mirror, nightly-gate, platform-deploy, platform-test, push-gate, stage-deploy). Тест ожидает 9 (`_EXPECTED_WORKFLOW_COUNT = 9`), manifest говорит 8.

**Риск:** При добавлении/удалении workflow-файла manifest описание будет вводить в заблуждение.

---

### CICD-03: 🟠 MED — SHA verification duplication

**Файл:** `.github/workflows/core-deploy.yml` (Steps 1-2, строки 63-95)
**TRAP[DEBT] уже существует:** `.github/workflows/core-deploy.yml:55-61`

**Проблема:** `core-deploy.yml` использует inline `gh api` вызовы для SHA verification, в то время как `build-platform.yml` и `mirror.yml` используют shared composite action `.github/actions/sha-resolve`. 3-way duplication — drift risk.

**Риск:** При изменении логики SHA verification (`github.event.workflow_run.head_sha` vs `github.sha`) исправление в одном месте не распространяется на core-deploy.

---

### CICD-04: 🟡 LOW — 1 pre-existing конфигурационный failure

**Источник:** Decision Gate 043, VerificationReport 039-W5, VerificationReport 046 (runtime: 671/672, 99.85%)
**Pass rate:** 671/672 (99.85%) — 5 из 6 ранее зафиксированных failures исправлены коммитами CICD-06 (22 Jul)

**Root cause (единственный оставшийся failure):** `test_e2e_health.py::test_service_health[langfuse-/api/public/health-False]` — langfuse возвращает 401 Unauthorized. Конфигурационная проблема (требуется аутентификация), не связана с CI/CD.

**Риск:** Минимальный — 1 failure из 672, не блокирует CI/CD изменения.

---

### CICD-05: 🟡 LOW — CI gate execution time not post-measured

**Baseline (W1-E8):** Оценка ~3-5 минут для `make gate MODE=fast`
**Post-Wave 5:** Не замерен

**Контекст:** Brief 027 §10.1 KPI: `make gate MODE=fast time: baseline TBD → цель <90 сек`. Decision Gate 043 §DG-2 признаёт: "CI gate execution time не замерян точно".

---

### CICD-06: 🟢 INFO — Active CI/CD churn (today, 22 Jul 2026)

5 fix-коммитов за сегодня, затрагивающих CI/CD:

| Commit | Исправление |
|--------|------------|
| `ee5c326` | `fix(context-promote): ssh auth check — capture output, not exit code` |
| `16e2c5f` | `fix(tests): 3 CI-specific test failures (spool_validator, secrets_validator, converge)` |
| `552a79a` | `fix(gate): make -n timeout for complex targets on GNU Make 4.x` |
| `02b5f5b` | `fix(gate): add missing import os in test_make_n_for_complex_targets` |
| `e8ad2a9` | `fix(ci): setup-platform composite action — checkout must precede local action` |

**Вывод:** CI/CD в активной фазе стабилизации после рефакторинга. Это ожидаемо — Waves 2-4 существенно переработали CI/CD инфраструктуру.

---

### Дополнительно: Compliance Check против Brief 027

#### Wave 2 (Dangerous) — AC верификация

| AC | Статус | Примечание |
|----|--------|-----------|
| **AC9** (CI composite: ≥6 workflows migrated, ≤3 checkout осталось) | ⚠️ PARTIAL | `core-deploy.yml` и `deploy-project.yml` используют checkout@v7 отдельно от composite (это нормально — checkout должен быть перед composite). Но `rg "actions/checkout@v7" .github/workflows/` показывает 7 вхождений в 6 файлах (whitelist target ≤3 не достигнут) |
| **AC11-12** (SHA-aware aggregator) | ✅ | `build-platform.yml` и `mirror.yml` используют `sha-resolve` composite. `core-deploy.yml` — inline (CICD-03) |

#### Wave 4 (Strangler) — Makefile include-split

| AC | Статус | Примечание |
|----|--------|-----------|
| Makefile <150 LOC | ✅ | 41 LOC, 6 includes |
| `make -n <target>` для всех .PHONY | ⚠️ FIXED today | `552a79a` чинит timeout для complex targets |

---

## 3. Audit Trail

| Время | Действие | Результат |
|-------|----------|-----------|
| 16:57 UTC+3 | Read Brief 027 (01-Brief.md) | Creation date: 2026-07-21, commit d726442 |
| 16:57 UTC+3 | Git log since d726442 | 40+ commits, 5 waves + Decision Gate |
| 16:58 UTC+3 | Glob workflow files | 9 `.yml` в `.github/workflows/`, 6 composite actions |
| 16:59 UTC+3 | Read push-gate.yml | ✅ Использует setup-platform composite |
| 16:59 UTC+3 | Read setup-platform action.yml | ✅ Корректная имплементация |
| 17:00 UTC+3 | Read entrypoint-manifest.yaml | 617 строк, gates, forbidden lists, allowed_verbs |
| 17:01 UTC+3 | Read Decision Gate 043 (02-DecisionGate.md) | Стратегия validated, P12 частично открыт |
| 17:02 UTC+3 | Read platform-test.yml | 5 inline python3, DORA validation через python3 -c |
| 17:03 UTC+3 | Read core-deploy.yml | Inline SHA verification (CICD-03) |
| 17:04 UTC+3 | Read deploy-project.yml | 5 inline python3, raw SSH key handling |
| 17:06 UTC+3 | Read platform-deploy.yml | 1 inline python3 |
| 17:07 UTC+3 | Read nightly-gate.yml | 2 inline python3 |
| 17:08 UTC+3 | Read build-platform.yml | ✅ Использует sha-resolve composite |
| 17:09 UTC+3 | Read mirror.yml | ✅ Использует sha-resolve composite |
| 17:10 UTC+3 | Read Makefile | 41 LOC, 6 includes, clean |
| 17:11 UTC+3 | Read makefiles/ci.mk | gate: fast/full/ci-docker modes |
| 17:13 UTC+3 | Read Decision Gate metrics | 14/15 P closed, P12 partial |
| 17:14 UTC+3 | grep inline python3 in .github/ | 13 violations found |
| 17:15 UTC+3 | Check workflow-consistency gate | `_EXPECTED_WORKFLOW_COUNT = 9` (correct), manifest description stale |
| 17:17 UTC+3 | Check TRAP[DEBT] in core-deploy.yml | Already documented |
| 17:18 UTC+3 | Check pre-commit hook scope | `files: '^core/.*\.sh$'` — не покрывает .github/*.yml |
| 17:20 UTC+3 | Check fix commits today | 5 CI/CD-related fixes identified |

---

## 4. Overall Verdict

**VERDICT: PARTIAL**

CI/CD pipeline functional ✅ — gate passes, deploy workflows work, composite actions unified setup, SHA-aware pattern established. Однако:

- **Языковая политика не соблюдена в CI** → 13 inline python3 в workflow-файлах (CICD-01)
- **Stale documentation** → entrypoint-manifest description дрейфует (CICD-02)
- **SHA verification дублируется** → inline vs composite (CICD-03)
- **Метрики не собраны** → CI gate execution time post-Wave 5 (CICD-05)

CI/CD был существенно улучшен в Waves 2-4, но гигиена кода CI-файлов отстаёт от стандартов, установленных для core/ shell-скриптов.

---

## 5. Implementation Plan (DevPlan)

### 5.1 Task Breakdown

| # | ID | Task | Effort | Depends On | Priority |
|---|----|------|--------|-----------|----------|
| T1 | CICD-02 | Fix stale `workflow-consistency` gate description (count=8→9) | 0.1h | — | HIGH |
| T2 | CICD-01a | Extract `module_list` generator into Python module + CI composite | 1.5h | — | HIGH |
| T3 | CICD-01b | Extract DORA dashboard validator into Python module | 0.5h | — | MED |
| T4 | CICD-01c | Replace YAML-reading inline python3 with `yaml_query.py` | 0.3h | — | MED |
| T5 | CICD-01d | Extract JSON-from-stdin validators into `json_query_stdin.py` | 1.0h | — | HIGH |
| T6 | CICD-01e | Extend pre-commit hook to cover `.github/**/*.yml` | 0.5h | T1-T5 | HIGH |
| T7 | CICD-03 | Migrate `core-deploy.yml` SHA verification to `sha-resolve` + `skip` output | 1.5h | — | MED |
| T8 | CICD-05 | Post-Wave 5 CI gate timing measurement | 0.2h | T1-T7 | LOW |
| T9 | CICD-04 | Fix 1 pre-existing конфигурационный failure (langfuse 401) | — | — | DEFERRED→042 |

---

### 5.2 Task Details

#### T1 — CICD-02: Fix stale manifest description (0.1h)

**Файл:** `core/entrypoint-manifest.yaml:434`

**Изменение:**
```yaml
# Было:
    description: Workflow count=8, main-full-gate deleted, platform-test single job, no observability refs, deploy triggers, push filter, NODE=, provisioner usage
# Стало:
    description: Workflow count=9, main-full-gate deleted, platform-test single job, no observability refs, deploy triggers, push filter, NODE=, provisioner usage
```

**Верификация:** `rg "Workflow count=" core/entrypoint-manifest.yaml` → ровно одно вхождение, значение 9.

**Почему 9, а не динамически:** Количество workflow-файлов меняется редко (последнее изменение — добавление `stage-deploy.yml`). Описание в manifest — human-readable документация, а не runtime constraint. Тест `test_gate_ci_coverage.py::test_workflow_count` использует `_EXPECTED_WORKFLOW_COUNT = 9` — runtime проверка синхронизирована.

---

#### T2 — CICD-01a: Extract `module_list` generator (1.5h)

**Проблема:** Один и тот же `python3 -c "import json; from pathlib..."` блок дублируется в:
- `platform-test.yml:167-168` (3 вызова: генерация, подсчёт, итерация)
- `nightly-gate.yml:108` (1 вызов)
- `platform-test.yml:177` + `:367` (итерация по сгенерированному JSON)
- `nightly-gate.yml:122` (итерация)

**Решение:** Двухшаговая экстракция:

**Шаг A — Python-модуль `core/internal/scripts/module_discovery.py`:**

```python
# GREP_SUMMARY: module_discovery, docker-module-list, compose-discovery, CI-helper
# STRUCTURE: ▶ discover_docker_modules() → ◇ glob modules/*/module.yaml → ⊕ filter non-system → ⎋ List[ComposeFile]
# region MODULE_CONTRACT
## @purpose  Typed API + CLI для поиска docker-compose модулей. Заменяет inline `python3 -c` блоки
##           в platform-test.yml и nightly-gate.yml (дублирование ×4).
## @scope    Чтение core/modules/*/module.yaml, фильтрация system-модулей, возврат списка compose-файлов.
## @invariants
##   - Модули с `install_type: system` исключаются из результата
##   - CLI: `--format json` (массив строк) / `--format lines` (одна строка на файл)
##   - API: возвращает `list[Path]`
## @rationale Один и тот же inline-блок в 3 workflow-файлах (platform-test, nightly-gate ×2).
##            Экстракция в typed-модуль даёт тестируемость, единую валидацию, устранение дублирования.
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import List

MODULES_DIR = pathlib.Path("core/modules")
SYSTEM_INSTALL_MARKER = "install_type: system"


def discover_docker_modules(modules_dir: pathlib.Path = MODULES_DIR) -> list[pathlib.Path]:
    """Discover docker-compose modules, excluding system-install modules."""
    modules: list[pathlib.Path] = []
    for module_yaml in sorted(modules_dir.glob("*/module.yaml")):
        content = module_yaml.read_text()
        if SYSTEM_INSTALL_MARKER not in content:
            compose_file = modules_dir / module_yaml.parent.name / "docker-compose.base.yml"
            if compose_file.exists():
                modules.append(compose_file)
    return modules


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover docker-compose modules")
    parser.add_argument("--format", choices=["json", "lines"], default="lines",
                        help="Output format: json array or one file per line")
    parser.add_argument("--modules-dir", default=str(MODULES_DIR),
                        help="Path to modules directory")
    args = parser.parse_args()

    modules = discover_docker_modules(pathlib.Path(args.modules_dir))

    if args.format == "json":
        print(json.dumps([str(m) for m in modules]))
    else:
        for m in modules:
            print(str(m))


if __name__ == "__main__":
    main()
```

**Шаг B — Composite action `.github/actions/discover-modules/action.yml`:**

```yaml
name: 'Discover Docker Modules'
description: 'Generate module_list.json from core/modules/*/module.yaml, excluding system modules'

outputs:
  module-list-json:
    description: 'Path to /tmp/module_list.json'
    value: '/tmp/module_list.json'
  module-count:
    description: 'Number of discovered modules'
    value: ${{ steps.discover.outputs.count }}

runs:
  using: 'composite'
  steps:
    - name: Discover modules
      id: discover
      shell: bash
      run: |
        python3 core/internal/scripts/module_discovery.py --format json > /tmp/module_list.json
        COUNT=$(python3 -c "import json; print(len(json.load(open('/tmp/module_list.json'))))")
        echo "count=$COUNT" >> "$GITHUB_OUTPUT"
        echo "[IMP:8][discover-modules] Discovered $COUNT modules"
```

**Шаг C — Замена в workflow-файлах:**

В `platform-test.yml` (строки 164-170, 177, 367):
```yaml
# Было (строки 164-170):
      - name: Generate module list
        id: module_list
        run: |
          python3 -c "import json; from pathlib ..."

# Стало:
      - name: Discover modules
        id: module_list
        uses: ./.github/actions/discover-modules
```

В `nightly-gate.yml` (строки 103-109, 122):
```yaml
# Аналогичная замена
      - name: Discover modules
        id: module_list
        uses: ./.github/actions/discover-modules
```

**Итерация по module_list (все workflow):**
```yaml
# Было:
          for compose_file in $(python3 -c "import json; [print(f) for f in json.load(open('/tmp/module_list.json'))]"); do

# Стало:
          for compose_file in $(python3 core/internal/scripts/module_discovery.py --format lines); do
```

**Unit-тест:** `tests/unit/test_module_discovery.py` — проверяет фильтрацию system-модулей, JSON/lines формат, пустой modules_dir.

**Верификация:** `rg 'python3 -c.*module_list\|python3 -c.*json.load.*module_list' .github/workflows/` → 0 matches.

**Relationship to existing `core/internal/bootstrap/discover_modules.py`:**

> **DRIFT-046-1 (VerificationReport) · Вариант B — отдельный легковесный CI-модуль**
>
> Существует `core/internal/bootstrap/discover_modules.py` (234 LOC, зарегистрирован как `make discover-modules` в entrypoint-manifest.yaml). Он решает схожую задачу — поиск docker-модулей через `yaml.safe_load()` с фильтрацией `install_type: system`. Однако:
>
> 1. **PyYAML-зависимость:** `discover_modules.py` требует `import yaml` (PyYAML). CI-раннер может не иметь PyYAML. Новый `module_discovery.py` использует текстовый поиск `'install_type: system' not in content` — zero dependencies.
> 2. **Разные подсистемы:** `discover_modules.py` — часть bootstrap-подсистемы (`core/internal/bootstrap/`), отвечает за обновление `docker-compose.yml` include-секции и test infra discovery. Новый `module_discovery.py` — легковесный CI-хелпер (`core/internal/scripts/`), только возвращает список compose-файлов.
> 3. **Разные контракты:** bootstrap-версия мутирует файлы (`update_compose_include`), CI-версия — read-only (stdout). Добавление CI-режима в bootstrap-модуль нарушило бы SRP и добавило бы complexity в критичный bootstrap-код.
>
> **## @rationale** Отдельный модуль выбран потому что:
> - CI не должен зависеть от PyYAML для операции «найти compose-файлы»
> - Bootstrap-модуль решает более широкую задачу (include-генерация, test infra, YAML-толерантность к `!override`)
> - Изоляция: баг в CI-модуле не сломает `make discover-modules` (bootstrap critical path)
>
> **## @see** `core/internal/bootstrap/discover_modules.py` — полный аналог для bootstrap-окружения (YAML-based).
> **Rejected strategy (Variant A):** Extend `discover_modules.py` с флагом `--ci-list --text-fallback`. Отклонено: добавляет conditional complexity (YAML vs text fallback) в критичный bootstrap-модуль. Предпочтена изоляция CI-логики.
> **Rejected strategy (Variant C):** Установить PyYAML в CI. Отклонено: добавляет зависимость в CI-раннер для тривиальной текстовой операции.

---

#### T3 — CICD-01b: Extract DORA dashboard validator (0.5h)

**Проблема:** `platform-test.yml:110` — 9 строк inline python3 для валидации Grafana DORA dashboard JSON.

**Решение:** Python-модуль `core/internal/scripts/validate_dora_dashboard.py`:

```python
# GREP_SUMMARY: validate_dora_dashboard, grafana, CI-validation, DORA-metrics
# STRUCTURE: ▶ validate_dora_dashboard(path) → ◇ load JSON → ⊕ check uid + 4 DORA panels → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  CLI-валидатор структуры DORA CI/CD дашборда Grafana.
## @scope    Проверяет uid='dora-ci-cd' и наличие 4 обязательных DORA-метрик-панелей.
## @invariants
##   - 4 обязательных панели: Deploy Frequency, Lead Time for Changes, MTTR, CFR
##   - Отсутствие панели → exit 1 с diagnostics
##   - Не-JSON файл → exit 2
## @rationale 9 строк inline python3 в platform-test.yml — нет причин для inline.
# endregion MODULE_CONTRACT

import json
import sys
from pathlib import Path

REQUIRED_PANELS = {
    "Deploy Frequency",
    "Lead Time for Changes",
    "Mean Time to Recovery (MTTR)",
    "Change Failure Rate (CFR)",
}

def validate(path: Path) -> bool:
    data = json.loads(path.read_text())
    if data.get("uid") != "dora-ci-cd":
        print(f"[IMP:10][dora] ERROR: Wrong dashboard UID: {data.get('uid')}", file=sys.stderr)
        return False
    panels = data.get("panels", [])
    found = {p.get("title", "") for p in panels}
    missing = REQUIRED_PANELS - found
    if missing:
        print(f"[IMP:10][dora] ERROR: Missing panels: {missing}", file=sys.stderr)
        return False
    print(f"[IMP:9][dora] DORA dashboard OK: {len(panels)} panels, {len(REQUIRED_PANELS)} required present")
    return True

if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("core/modules/monitoring/config/dashboards/dora-ci-cd.json")
    sys.exit(0 if validate(path) else 1)
```

**Замена в `platform-test.yml:110`:**
```yaml
# Было (9 строк):
      - name: Validate DORA dashboard JSON
        run: |
          echo "::group::DORA dashboard JSON validation"
          python3 -c "
          import json
          path = 'core/modules/monitoring/config/dashboards/dora-ci-cd.json'
          ...

# Стало:
      - name: Validate DORA dashboard JSON
        run: |
          echo "::group::DORA dashboard JSON validation"
          python3 core/internal/scripts/validate_dora_dashboard.py
```

**Unit-тест:** `tests/unit/test_validate_dora_dashboard.py` — валидный дашборд, отсутствующие панели, неверный UID, malformed JSON.

---

#### T4 — CICD-01c: Replace YAML-reading inline with yaml_query.py (0.3h)

**Проблема:** `platform-deploy.yml:94` — 5 строк inline `python3 -c "import yaml..."` для чтения `environments.staging`.

**Решение:** Использовать существующий `core/internal/scripts/yaml_query.py` (201 строка, Wave 1 W1-E7).

**Замена в `platform-deploy.yml:94`:**
```yaml
# Было (строки 90-101):
      - name: Read ai-platform.yaml
        id: project
        run: |
          STAGING=$(python3 -c "
          import yaml, sys
          with open('${{ inputs.project_yaml_path }}') as f:
              d = yaml.safe_load(f)
          envs = d.get('environments', {})
          print('true' if envs.get('staging', False) else 'false')
          " 2>/dev/null || echo "false")
          echo "staging=${STAGING}" >> "$GITHUB_OUTPUT"

# Стало:
      - name: Read ai-platform.yaml
        id: project
        run: |
          STAGING=$(python3 core/internal/scripts/yaml_query.py \
            --file "${{ inputs.project_yaml_path }}" \
            --get environments.staging \
            --default false 2>/dev/null || echo "false")
          echo "staging=${STAGING}" >> "$GITHUB_OUTPUT"
```

**Дополнительно:** `deploy-project.yml:69` (resolve target_node) — аналогичная замена:
```yaml
# Было:
  TARGET_NODE=$(python3 -c "import yaml; d=yaml.safe_load(open('ai-platform.yaml')); print(d.get('target_node','tronyx-vps'))" 2>/dev/null || echo "tronyx-vps")
# Стало:
  TARGET_NODE=$(python3 core/internal/scripts/yaml_query.py --file ai-platform.yaml --get target_node --default tronyx-vps 2>/dev/null || echo "tronyx-vps")
```

**Итого:** 2 из 13 inline python3 заменены на `yaml_query.py`.

> **⚠️ TRAP[DESIGN] · 2026-07-22 · LOW · DRIFT-046-5: Замена синтаксическая — yaml_query.py тоже требует PyYAML**
> - `deploy-project.yml:69` и `platform-deploy.yml:94` используют `import yaml` — оба inline зависят от PyYAML.
> - `yaml_query.py` (Wave 1 W1-E7, 201 LOC) также требует PyYAML (`import yaml`).
> - Замена **синтаксическая**: вместо inline `python3 -c "import yaml..."` → вызов typed CLI `yaml_query.py --file ... --get ...`.
> - Архитектурно проблема не меняется: PyYAML должен быть доступен в CI-раннере для этих шагов.
> - Это улучшение читаемости, grep-ability и тестируемости, но не устранение зависимости от PyYAML в CI.
> - Rev: если PyYAML станет проблемой в CI → рассмотреть текстовый fallback.

---

#### T5 — CICD-01d: Extract JSON-from-stdin validators (1.0h)

**Проблема:** `deploy-project.yml` содержит 3 inline python3, читающих JSON из stdin (строки 79, 100, 107, 136). `yaml_query.py` не поддерживает stdin (требует `--file`).

**Строки 79:** JSON query из stdin (NODE_HOST_MAP)
**Строки 100, 107, 136:** VPS status validation из stdin (STATUS_JSON)

**Решение A — Расширить `yaml_query.py` поддержкой stdin (`--stdin` flag):**

Добавить в `yaml_query.py`:
```python
parser.add_argument("--stdin", action="store_true", help="Read JSON from stdin instead of --file")
```

В функции `json_get()` / `main()`:
```python
if args.stdin:
    data = json.load(sys.stdin)
else:
    data = load_json(path)
```

Это позволит заменить `deploy-project.yml:79`:
```yaml
# Было:
  SSH_HOST=$(echo "${NODE_HOST_MAP}" | python3 -c "import json,sys; m=json.load(sys.stdin); n='${TARGET_NODE}'; h=m.get(n,''); print(h if h else (print(f'K5: node {n} not found in NODE_HOST_MAP',file=sys.stderr) or exit(1)))")
# Стало:
  SSH_HOST=$(echo "${NODE_HOST_MAP}" | python3 core/internal/scripts/yaml_query.py --stdin --get "${TARGET_NODE}" --default "")
```

**⚠️ TRAP[DESIGN] · 2026-07-22 · MED · `--stdin` добавляет второй режим ввода в yaml_query.py**
- Риск: нарушение SRP — yaml_query.py становится "YAML file reader" + "stdin JSON reader"
- Mitigation: `--stdin` автоматически определяет формат как JSON (stdin всегда text/plain). YAML из stdin не поддерживается — только JSON.
- Альтернатива: отдельный `json_query_stdin.py`. Отклонено — overhead поддержки двух CLI-модулей для одной задачи. `--stdin` flag — минимальное расширение контракта (≤15 строк кода).
- Rev: если в будущем потребуется YAML из stdin → выделить `stdin_query.py`.

**Решение B — Семантическая валидация VPS status:**

Строки 100, 107, 136 содержат не просто JSON-чтение, а семантическую проверку статуса проекта. Экстракция в `core/internal/scripts/vps_status_check.py`:

```python
# GREP_SUMMARY: vps_status_check, project-status, CI-preflight, verify-deliver
# STRUCTURE: ▶ check_status(stdin_json) → ◇ parse status → ⊕ validate found|stub → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  CLI-валидатор статуса проекта на VPS. Принимает JSON project-status из stdin.
## @scope    Проверяет, что status ∈ {found, stub}. Используется в preflight и verify-deliver шагах CI.
## @invariants
##   - status ∉ {found, stub} → exit 1
##   - malformed JSON → exit 2
##   - пустой stdin → exit 3
# endregion MODULE_CONTRACT

import json
import sys


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print("[IMP:10][vps-status] ERROR: empty stdin", file=sys.stderr)
        sys.exit(3)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[IMP:10][vps-status] ERROR: invalid JSON: {e}", file=sys.stderr)
        sys.exit(2)
    status = data.get("status", "")
    if status in ("found", "stub"):
        print(f"[IMP:9][vps-status] VPS project status: {status}")
        sys.exit(0)
    else:
        print(f"[IMP:10][vps-status] ERROR: unexpected status: {status}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Дополнительно — `--output-status-only` флаг (DRIFT-046-3):**

> **⚠️ DRIFT-046-3 (VerificationReport):** `vps_status_check.py` только валидирует статус (exit 0/1/2/3),
> но не возвращает голое значение статуса для subshell-подстановки.
> `deploy-project.yml:107` использует inline `python3 -c "print(json.load(sys.stdin).get('status'))"`
> внутри `$(...)` — для ПЕЧАТИ статуса в лог-сообщении, а не для валидации.

Добавить в `vps_status_check.py` аргумент `--output-status-only`:
```python
parser.add_argument("--output-status-only", action="store_true",
                    help="Print only the status value (for subshell use)")
```

В `main()`:
```python
if args.output_status_only:
    data = json.load(sys.stdin)
    print(data.get("status", ""))
    sys.exit(0)
```

**Замена в `deploy-project.yml:107` (print-only use-case):**
```yaml
# Было:
echo "[IMP:9][preflight] VPS readiness check passed (status: $(echo "${STATUS_JSON}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status'))"))"

# Стало:
echo "[IMP:9][preflight] VPS readiness check passed (status: $(echo "${STATUS_JSON}" | python3 core/internal/scripts/vps_status_check.py --output-status-only))"
```

**Итого T5 c дополнением:** 5 inline python3 устранены (строки 79, 100, 107 [print], 107 [validate], 136).

**Замена в `deploy-project.yml:100` (preflight check):**
```yaml
# Было:
          echo "${STATUS_JSON}" | python3 -c "
          import json, sys
          d = json.load(sys.stdin)
          status = d.get('status', '')
          if status not in ('found', 'stub'):
              sys.exit(1)
          "
# Стало:
          echo "${STATUS_JSON}" | python3 core/internal/scripts/vps_status_check.py
```

**Замена в `deploy-project.yml:136` (verify-deliver):**
```yaml
# Было (7 строк heredoc):
          echo "${STATUS_JSON}" | python3 -c "
          import json, sys
          d = json.load(sys.stdin)
          ...

# Стало:
          echo "${STATUS_JSON}" | python3 core/internal/scripts/vps_status_check.py
```

**Итого T5 c дополнением:** 5 inline python3 устранены (строки 79, 100, 107 [print + validate — 2 вызова], 136).

---

#### T6 — CICD-01e: Extend pre-commit hook to `.github/**/*.yml` (0.5h)

**Текущее состояние:** `no-new-inline-python3` hook настроен на `files: '^core/.*\\.sh$'` — не защищает CI workflow-файлы.

**Изменение 1 — `.pre-commit-config.yaml:270` (pre-commit `files:` фильтр):**
```yaml
# Было:
        files: '^core/.*\\.sh$'

# Стало:
        files: '^(core/.*\\.sh|\\.github/.*\\.yml)$'
```

**Изменение 2 — `core/internal/hooks/check-no-new-inline-python3.sh:22` (hook script `git diff` glob):**

> **⚠️ DRIFT-046-2 (VerificationReport):** Изменение только `files:` в `.pre-commit-config.yaml` недостаточно.
> Pre-commit ВЫЗОВЕТ hook для `.github/**/*.yml`, но hook script на строке 22 фильтрует
> `git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh'` — вернёт пустой список для yml-файлов,
> hook завершится с exit 0 (нет staged .sh файлов) = НИЧЕГО НЕ ПРОВЕРИТ.

Строка 22 — заменить glob:
```bash
# Было (строка 22):
staged_files=$(git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh' || true)

# Стало:
staged_files=$(git diff --cached --name-only --diff-filter=ACM -- 'core/*.sh' '.github/workflows/*.yml' '.github/actions/*/action.yml' || true)
```

**Почему три glob-а:**
- `'core/*.sh'` — существующее покрытие (shell-скрипты)
- `'.github/workflows/*.yml'` — CI workflow-файлы (основная цель расширения)
- `'.github/actions/*/action.yml'` — composite action definitions (могут содержать inline python3 в `run:` блоках)

**⚠️ TRAP[DESIGN] · 2026-07-22 · MED · Whitelist для легитимного inline в CI**
- Некоторые CI-шаги могут требовать inline python3 для простых операций (например, `python3 -c "print('hello')"`)
- Whitelist pattern: разрешить `python3 -c` с однострочниками без `import` (чистый print/format), блокировать `python3 -c` с `import`
- Реализация: hook проверяет, содержит ли `python3 -c` строку `import` — если да, блокирует
- Rev: если легитимных inline без import станет >5 → пересмотреть whitelist

**Верификация:**
```bash
# Проверка, что hook срабатывает на новых inline python3 в CI:
echo 'python3 -c "import json; print(1)"' >> .github/workflows/test.yml
git add .github/workflows/test.yml && git commit -m "test"  # должен быть заблокирован
git reset HEAD .github/workflows/test.yml && git checkout .github/workflows/test.yml

# Проверка, что hook script читает yml-файлы (chain test):
# 1. Создать .github/workflows/test-inline.yml с python3 -c "import json"
# 2. git add → git commit
# 3. Убедиться что hook выдал VIOLATION (а не молча exit 0)
# 4. git reset + удалить test файл
```

**DRIFT-046-2 prevention:** После реализации T6 убедиться что hook script строка 22 содержит `'.github/workflows/*.yml'` и `'.github/actions/*/action.yml'`:
```bash
rg "git diff.*cached.*name-only" core/internal/hooks/check-no-new-inline-python3.sh
# Должно содержать: '.github/workflows/*.yml' '.github/actions/*/action.yml'
```

---

#### T7 — CICD-03: Migrate core-deploy.yml SHA verification (1.5h)

**Проблема:** `core-deploy.yml` (строки 55-95) использует inline SHA resolution + `gh api` verification вместо shared `sha-resolve` composite action. Причина — `sha-resolve` не имеет `skip` output для downstream `if:` chain.

**Текущая структура `core-deploy.yml`:**
```
Step 1: Determine SHA (inline)          → outputs: sha, ref
Step 2: Verify platform-test (inline)    → outputs: skip (true/false)
Step 3: Deploy core (if: skip != 'true')
```

**Решение — расширить `sha-resolve` composite action с `skip` output:**

Добавить в `.github/actions/sha-resolve/action.yml`:
```yaml
outputs:
  sha:
    description: 'Resolved commit SHA'
    value: ${{ steps.resolve.outputs.sha }}
  skip:
    description: 'Whether downstream steps should be skipped (true if verification fails)'
    value: ${{ steps.verify.outputs.skip }}
```

Добавить в Step 2 (verify):
```yaml
    - name: Verify platform-test succeeded for SHA
      if: inputs.verify == 'true'
      id: verify
      shell: bash
      env:
        GH_TOKEN: ${{ github.token }}
      run: |
        SHA="${{ steps.resolve.outputs.sha }}"
        COUNT=$(gh api \
          "/repos/${{ github.repository }}/actions/workflows/platform-test.yml/runs?head_sha=$SHA&status=success&per_page=1" \
          --jq '.total_count')
        if [ "$COUNT" -eq 0 ]; then
          echo "[IMP:10][sha-resolve][verify] ERROR: no successful platform-test for SHA $SHA"
          echo "skip=true" >> "$GITHUB_OUTPUT"
          exit 1
        fi
        echo "[IMP:9][sha-resolve][verify] platform-test succeeded for SHA $SHA"
        echo "skip=false" >> "$GITHUB_OUTPUT"
```

**⚠️ TRAP[DESIGN] · 2026-07-22 · MED · `skip` output — leaky abstraction**
- `sha-resolve` изначально спроектирован как stateless SHA resolver. Добавление `skip` output делает его осведомлённым о downstream flow control.
- Альтернатива: отдельный `verify-and-skip` output в `sha-resolve` с явным naming. Отклонено — `skip` семантически прозрачен (используется как `if: steps.sha.outputs.skip != 'true'`).
- Mitigation: Document в MODULE_CONTRACT, что `skip` output существует только когда `verify=true`.

**Замена в `core-deploy.yml`:**
```yaml
# Было (строки 55-95 — 40 строк inline SHA resolution + verification):
      - name: Resolve SHA
        id: sha
        # ... TRAP[DEBT] comment + 40 строк inline logic ...

# Стало:
      - name: Resolve SHA and verify platform-test
        id: sha
        uses: ./.github/actions/sha-resolve
        with:
          verify: 'true'

      # SHA доступен как ${{ steps.sha.outputs.sha }}
      # Skip-логика: if: steps.sha.outputs.skip != 'true'
```

**Обновить downstream `if:` conditions:** заменить `steps.verify.outputs.skip` → `steps.sha.outputs.skip` во всех шагах.

**Удалить TRAP[DEBT] комментарий** (строки 55-61).

**Ключевой риск:** `sha-resolve` step должен быть первым после checkout. Проверить, что порядок шагов в `core-deploy.yml` совместим.

**Верификация:**
```bash
rg "gh api.*platform-test" .github/workflows/core-deploy.yml  # должен быть 0 (вся логика в composite)
rg "steps\.sha\.outputs\.skip" .github/workflows/core-deploy.yml  # должен найти uses: + downstream if:
```

---

#### T8 — CICD-05: CI gate timing measurement (0.2h)

**Метод:** Запустить `make gate MODE=fast` на CI 3 раза, записать wall-clock время, сравнить с baseline.

**Baseline (W1-E8):** ~3-5 минут (оценка, не точный замер)
**Цель Brief 027 §10.1:** <90 сек

**Команда:**
```bash
time make gate MODE=fast 2>&1 | tail -1
```

**Фиксация:** `reports/ci-gate-timing-post-wave5.csv` со структурой:
```csv
run, wall_clock_sec, date, git_sha
1, <N>, 2026-07-22, <sha>
2, <N>, 2026-07-22, <sha>
3, <N>, 2026-07-22, <sha>
```

**Обновить:** `core/entrypoint-manifest.yaml` gate `ci-coverage` description — добавить `gate-fast-time=<N>s`.

---

#### T9 — CICD-04: 1 pre-existing конфигурационный failure (DEFERRED)

**Статус:** DEFERRED → DevPlan 042 (test-adaptation-wave4). Зафиксирован в Decision Gate 043 §DG-3.

**Текущее состояние:** 5 из 6 ранее зафиксированных failures исправлены коммитами CICD-06 (22 Jul 2026). Единственный оставшийся failure — `test_e2e_health.py::test_service_health[langfuse-/api/public/health-False]` (401 Unauthorized, конфигурационный).

**Не входит в scope настоящего DevPlan.** CICD-04 оставлен в tracking для visibility.

---

### 5.3 File Manifest

#### CREATE

| # | File | T | Purpose |
|---|------|---|---------|
| 1 | `core/internal/scripts/module_discovery.py` | T2 | Typed Docker module discovery (замена 4× inline в CI). **@see** `core/internal/bootstrap/discover_modules.py` — bootstrap-аналог (YAML-based) |
| 2 | `core/internal/scripts/validate_dora_dashboard.py` | T3 | DORA dashboard JSON validator |
| 3 | `core/internal/scripts/vps_status_check.py` | T5 | VPS project status validator (stdin JSON) |
| 4 | `.github/actions/discover-modules/action.yml` | T2 | Composite action wrapper для module_discovery.py |
| 5 | `tests/unit/test_module_discovery.py` | T2 | Unit-тесты: фильтрация, форматы, edge cases |
| 6 | `tests/unit/test_validate_dora_dashboard.py` | T3 | Unit-тесты: валидный/невалидный дашборд |
| 7 | `tests/unit/test_vps_status_check.py` | T5 | Unit-тесты: stdin parsing, status validation |
| 8 | `reports/ci-gate-timing-post-wave5.csv` | T8 | Post-Wave 5 timing measurement |

#### MODIFY

| # | File | T | Change |
|---|------|---|--------|
| 1 | `core/entrypoint-manifest.yaml:434` | T1 | `s/Workflow count=8/Workflow count=9/` |
| 2 | `.github/workflows/platform-test.yml` | T2, T3 | Замена 5× python3 -c на module_discovery.py + validate_dora_dashboard.py |
| 3 | `.github/workflows/nightly-gate.yml` | T2 | Замена 2× python3 -c на module_discovery.py |
| 4 | `.github/workflows/platform-deploy.yml` | T4 | Замена 1× python3 -c на yaml_query.py |
| 5 | `.github/workflows/deploy-project.yml` | T4, T5 | Замена 5× python3 -c на yaml_query.py + vps_status_check.py |
| 6 | `.github/actions/sha-resolve/action.yml` | T7 | Добавлен `skip` output + `id: verify` |
| 7 | `.github/workflows/core-deploy.yml` | T7 | Замена inline SHA → sha-resolve composite; удаление TRAP[DEBT] |
| 8 | `.pre-commit-config.yaml:270` | T6 | `files:` расширен на `.github/.*\.yml` |
| 9 | `core/internal/hooks/check-no-new-inline-python3.sh` | T6 | Добавлен whitelist для однострочников без `import` |
| 10 | `core/internal/scripts/yaml_query.py` | T5 | Добавлен `--stdin` flag для JSON-from-stdin |
| 11 | `core/entrypoint-manifest.yaml` (gates section) | T1, T8 | Обновлён ci-coverage description |
| 12 | `tests/gates/test_gate_workflow_consistency.py` | T1 | Файл затронут верификацией T1 (manifest count=8→9). **DRIFT-046-6** — добавлен для контекста |

---

### 5.4 Verification Commands

```bash
# Pre-flight (перед стартом)
make gate MODE=fast                          # должен быть зелёный
rg "python3 -c" .github/workflows/           # зафиксировать baseline (сейчас 13)

# После T1
rg "Workflow count=" core/entrypoint-manifest.yaml  # должно быть count=9

# После T2-T5
rg "python3 -c" .github/workflows/           # должно быть 0 (все 13 заменены)
python3 -m pytest tests/unit/test_module_discovery.py tests/unit/test_validate_dora_dashboard.py tests/unit/test_vps_status_check.py -v

# После T6
git add .github/workflows/test-inline.yml && git commit -m "test"  # должен быть заблокирован pre-commit hook
# (cleanup после проверки)

# После T7
rg "gh api.*platform-test" .github/workflows/core-deploy.yml       # должен быть 0
rg "steps\.sha\.outputs\.skip" .github/workflows/core-deploy.yml   # должен найти uses + downstream if:

# После T8
cat reports/ci-gate-timing-post-wave5.csv   # должен содержать 3 замера

# Финальный gate
make gate MODE=fast                          # должен быть зелёный
ruff format . && ruff check --fix .          # все новые Python-файлы
```

---

### 5.5 Risk Register

| ID | Risk | Severity | Mitigation |
|----|------|----------|-----------|
| R-046-1 | `sha-resolve` + `skip` output ломает `build-platform.yml`/`mirror.yml` (backward compat) | MED | `sha-resolve` уже используется ими без `skip`. Новый output игнорируется если не используется в `if:`. Протестировать на fork до merge. |
| R-046-2 | `module_discovery.py` возвращает другой порядок/набор модулей чем inline-версия | LOW | Логика идентична: `glob */module.yaml`, сортировка, фильтр `install_type: system`. Unit-тест сравнивает вывод с текущим inline. **DRIFT-046-1 resolved:** Добавлен cross-reference на существующий `discover_modules.py` (bootstrap), выбран Variant B (отдельный легковесный CI-модуль). |
| R-046-3 | Pre-commit hook на `.github/**/*.yml` даёт false-positive на легитимных однострочниках | LOW | Whitelist: `python3 -c` без `import` разрешён. CI workflow редко используют python3 -c без import. |
| R-046-4 | `--stdin` в `yaml_query.py` конфликтует с `--file` (mutual exclusion) | LOW | Добавить `mutually_exclusive_group`. При ошибке — понятное сообщение. |

---

### 5.6 Effort Summary

| Task | Effort | Priority | Inline Eliminated | Cumulative |
|------|--------|----------|-------------------|------------|
| T1 (manifest) | 0.1h | HIGH | — | — |
| T2 (module_list) | 1.5h | HIGH | 6 | 6/13 |
| T3 (DORA) | 0.5h | MED | 1 | 7/13 |
| T4 (yaml_query) | 0.3h | MED | 2 | 9/13 |
| T5 (stdin JSON) | 1.0h | HIGH | 5 | 13/13 |
| T6 (pre-commit) | 0.5h | HIGH | — | 13/13 |
| T7 (SHA migrate) | 1.5h | MED | — | 13/13 |
| T8 (timing) | 0.2h | LOW | — | 13/13 |
| **Total** | **5.6h** | | **13 → 0** | |

**Последовательность:** T1 → T2+T3+T4+T5 (параллельно) → T6 → T7 → T8

**Gate check после каждых 2 задач:** `make gate MODE=fast` + `ruff check`.

---

### 5.7 Post-Implementation: Metric Update

После завершения всех задач обновить метрики в Decision Gate 043:

| Метрика | До | После |
|---------|----|-------|
| Inline python3 в CI workflow | 13 | 0 |
| Workflow manifest consistency | count=8 (stale) | count=9 (accurate) |
| SHA verification pattern | 2 composite + 1 inline | 3 composite |
| Pre-commit coverage | `core/.*\.sh` only | `core/.*\.sh` + `.github/.*\.yml` |
| CI gate time | baseline ~3-5 min | measured (T8) |
| Test pass rate | 671/672 (99.85%) | 671/672 (99.85%) (CICD-04 deferred до 042) |

---

### 5.8 Dependency Map

```
Brief 027 (AC9 composite migration)
  └── Decision Gate 043 (Python-First validated ✅)
        └── StatusReport 046 (этот документ — diagnosis)
              └── DevPlan 046 §5 (этот раздел — implementation)
                    ├── T1 (manifest fix) — независимая
                    ├── T2-T5 (inline extraction) — независимы друг от друга
                    ├── T6 (pre-commit) — зависит от T1-T5 (все inline должны быть извлечены ДО расширения hook)
                    ├── T7 (SHA migration) — независимая (затрагивает только core-deploy.yml + sha-resolve)
                    └── T8 (timing) — зависит от T1-T7
```

$END_STATUS_REPORT
