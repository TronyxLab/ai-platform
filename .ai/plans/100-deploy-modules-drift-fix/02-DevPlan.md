$START_DEVPLAN
# DevPlan 100 — deploy-modules.sh Drift Fix: Routing + Severity → Python

$ARTIFACT_CONTRACT
PURPOSE:               Исправить drift deploy-modules.sh (260 LOC фактически vs 91 заявлено) —
                       вынести PARALLEL/SEQUENTIAL/ORCHESTRATOR routing и severity aggregation
                       в новый Python-модуль `deploy/deploy_orchestrator.py`. Shell-фасад ≤50 LOC.
DESCRIPTION:           Strangler-Fig extraction третьего уровня: после W4-E1 (docker-логика →
                       docker_orchestrator.py) и W2 (secrets/spool/sudoers/orphans → Python),
                       routing logic осталась в shell и выросла на 169 LOC. Новый модуль
                       `deploy_orchestrator.py` ИМПОРТИРУЕТ существующие Python-модули (а не
                       subprocess-ит их), принимает routing-решение, агрегирует severity,
                       возвращает exit code {0,1,2}. Shell сокращается до чистого фасада:
                       arg parse → root/node_yaml check → network provision → docker login →
                       exec Python orchestrator → ⎋.
RATIONALE:             260 LOC shell при 8 Python-модулях в deploy/ — нарушение Strangler-Fig
                       (AGENTS.md: «Shell-фасады <100-200 LOC»). Рост на 169 LOC с W4-E1 —
                       тенденция к обратному росту shell. Python-импорт быстрее subprocess
                       и тестируем через mock, без fork.
ACCEPTANCE_CRITERIA:   AC1: Новый `deploy/deploy_orchestrator.py` с routing logic + severity
                       AC2: Shell-фасад ≤50 LOC (arg parsing + вызов Python-оркестратора)
                       AC3: DEPLOY_PARALLEL=true путь работает идентично
                       AC4: DEPLOY_ORCHESTRATOR=true путь работает идентично
                       AC5: Sequential путь (DEPLOY_PARALLEL=false) работает идентично
                       AC6: Severity-based exit (CRIT→2/WARN→0/DONE→0) идентичен
                       AC7: bootstrap/AGENTS.md таблица обновлена: 91→50 LOC
                       AC8: `make gate MODE=fast` зелёный
                       AC9: TRAP[CROSS-LAYER] сохранён в shell-фасаде
IMPLEMENTS:            Brief 100 (`.ai/plans/100-deploy-modules-drift-fix/01-Brief.md`)
IMPACTS:
                       - `core/internal/bootstrap/deploy/deploy_orchestrator.py` (NEW)
                       - `core/internal/bootstrap/deploy-modules.sh` (MODIFY: 260→~50 LOC)
                       - `core/internal/bootstrap/AGENTS.md` (MODIFY: table entry 91→50)
                       - `tests/unit/test_deploy_orchestrator.py` (NEW)
                       - `tests/test_deploy_modules.py` (MODIFY: static grep references)
                       - `tests/test_deploy_smoke.py` (MODIFY: smoke tests for new facade)
                       - `tests/test_hermes_l2_fallback.py` (MODIFY: grep references)
REQUIRES:              Все Python-зависимости уже существуют в deploy/ и bootstrap/.
                       Никаких новых пакетов. Python ≥3.10 уже на всех нодах.
$END_ARTIFACT_CONTRACT

---

## 0. Debt Intake

Перед проектированием — аудит существующих TRAP/DEBT в зоне изменений:

```bash
grep "TRAP\[DEBT\]\|TRAP\[DECISION\]" core/internal/bootstrap/deploy-modules.sh \
     core/internal/bootstrap/deploy/*.py core/internal/bootstrap/_topo_sort.py
```

**Находки:**

| # | Источник | Тип | Содержание | Решение |
|---|----------|-----|-----------|---------|
| D1 | `deploy-modules.sh:234` | TRAP[CROSS-LAYER] | `provision-llm.sh call REMOVED — internal/ must not call entrypoints/` | IN_SCOPE — перенести в shell-фасад (строка остаётся в шапке shell) |
| D2 | `docker_orchestrator.py:37-46` | TRAP[DEBT] | 5 test-side failures в test_docker_orchestrator.py (mock bytes vs str) | DEFER — не в скоупе 100, уже задокументирован в DevPlan 042 Phase 4 |

**DEBT-регистры предыдущих волн:** `.ai/plans/*/*-Debt.md` — релевантных для deploy-modules не найдено.

---

## 1. Problem Matrix

| # | Проблема | Статус на 2026-07-31 | Решается как |
|---|----------|----------------------|--------------|
| P1 | Shell 260 LOC, задокументирован как 91 — drift | Подтверждено: `wc -l` = 260 | TASK-2: сократить до ≤50 LOC |
| P2 | Routing logic (~142 строк PARALLEL/ORCHESTRATOR/sequential) в shell | Подтверждено: строки 75-199 + severity 243-260 | TASK-1: вынести в `deploy_orchestrator.py` |
| P3 | Shell вызывает Python через subprocess (`python3 deploy/docker_orchestrator.py ...`) вместо импорта | Подтверждено: 7 вызовов `python3` в deploy-modules.sh | TASK-1: Python orchestrator ИМПОРТИРУЕТ модули |
| P4 | Static tests (test_deploy_modules.py: 52 grep-ссылки на deploy-modules.sh) сломаются | Подтверждено: grep выявил 52+15+14=81 ссылку | TASK-4b: обновить grep-цели |
| P5 | TRAP[CROSS-LAYER] должен выжить | Подтверждено: 1 TRAP в строке 234 | TASK-2: сохранить в шапке фасада |

---

## 2. Architecture Overview

### 2.1 Current State (260 LOC shell)

```
deploy-modules.sh (260 LOC)
├── [shell] source libs + arg parse                          (~10 LOC)
├── [shell] root check + NODE_YAML                           (~5 LOC)
├── [shell] network provision (system-level)                 (~9 LOC)
├── [shell] docker_login + ghcr_login                        (~3 LOC)
├── [python3] context_overlay.py ensure                      (~2 LOC)
├── [python3] spool_validator.py verify                      (~2 LOC)
├── [shell]  status-metrics.json pre-create                  (~5 LOC)
├── [python3] secrets_validator.py validate-charsets         (~4 LOC)
├── [python3] secrets_validator.py parse-node-yaml           (~4 LOC)
├── ═══ ROUTING (142 LOC) ═══                                ← ЦЕЛЬ ЭКСТРАКЦИИ
│   ├── DEPLOY_PARALLEL=true:
│   │   ├── [python3] _topo_sort.py → JSON                   (~16 LOC)
│   │   ├── [python3] json_field_extractor.py ×3             (~8 LOC)
│   │   ├── [python3] docker_orchestrator.py pre-pull        (~7 LOC)
│   │   ├── [python3] secrets_validator.py batch-check-env   (~4 LOC)
│   │   ├── DEPLOY_ORCHESTRATOR=true:
│   │   │   └── [python3] orchestrator_cli.py deploy-many    (~20 LOC)
│   │   ├── [python3] docker_orchestrator.py deploy-group    (~30 LOC, цикл по группам)
│   │   ├── [shell]  system modules (invoke_module_interface) (~12 LOC)
│   │   └── [shell]  HC_DONE_MARKER                           (~7 LOC)
│   └── DEPLOY_PARALLEL != true:
│       └── [shell] sequential for-loop                      (~25 LOC)
├── [python3] litellm config_renderer.py                      (~5 LOC)
├── [python3] sudoers_generator.py                            (~3 LOC)
├── [python3] orphan_reconciler.py                            (~5 LOC)
└── [shell]  severity-based exit                              (~17 LOC)
```

### 2.2 Target State (≤50 LOC shell + Python orchestrator)

```
deploy-modules.sh (≤50 LOC)                   deploy/deploy_orchestrator.py (~250 LOC)
├── source libs                               import docker_orchestrator
├── arg parse                                 import secrets_validator
├── root + NODE_YAML check                    import context_overlay
├── network provision (system-level)          import spool_validator
├── docker_login + ghcr_login                 import sudoers_generator
├── TRAP[CROSS-LAYER] (preserved)             import orphan_reconciler
└── exec python3 deploy_orchestrator.py       import _topo_sort
         │                                    import json_field_extractor
         └──────────────────────────────────► orchestrate(...)
                                                   │
                                              ┌────┴────┐
                                              │ ROUTING │
                                              └────┬────┘
                                          ┌────────┼────────┐
                                   PARALLEL   ORCHESTRATOR  SEQ
                                          │         │        │
                                          ▼         ▼        ▼
                                    _deploy_    _deploy_   _deploy_
                                    parallel()  orch()     seq()
                                          │
                                          ▼
                                    _aggregate_severity()
                                          │
                                          ▼
                                    return {0,1,2}
```

### 2.3 Draft Code Graph (XML)

```xml
<code_graph>
  <entity id="deploy_orchestrator_py" type="PYTHON_MODULE"
          keywords="deploy-orchestrator routing severity parallel sequential import-native">
    <annotation>
      NEW: core/internal/bootstrap/deploy/deploy_orchestrator.py
      Routing orchestrator for module deployment. Imports existing Python modules
      (docker_orchestrator, secrets_validator, _topo_sort, etc.) — NO subprocess.
      CLI + importable orchestrate() function. Returns exit code {0,1,2}.
    </annotation>
    <sections>
      <func name="orchestrate" sig="(node_yaml, modules_dir, core_dir, templates_dir, modules_filter='', deploy_parallel=False, deploy_orchestrator=False) → int">
        Main entry point (importable). Preflight → parse → route → postflight → severity → exit_code.
      </func>
      <func name="main" sig="() → None">CLI entry: argparse → orchestrate() → sys.exit()</func>
      <func name="_preflight" sig="(core_dir, node_yaml) → None">
        context_overlay.ensure(), spool_validator.verify(), status_metrics pre-create, secrets_validator.validate_charsets()
      </func>
      <func name="_parse_modules" sig="(node_yaml, modules_dir, core_dir) → ModuleLists">
        Parse node.yaml via secrets_validator, returns enabled/all module names + metadata.
      </func>
      <func name="_route_deploy" sig="(modules, flags) → (deployed, failed)">
        Decision: PARALLEL → _deploy_parallel, ORCHESTRATOR → _deploy_orchestrator, else → _deploy_sequential.
      </func>
      <func name="_deploy_parallel" sig="(enabled_names, modules_dir, ...) → (deployed, failed)">
        _topo_sort → pre_pull → batch_check_env → group_deploy → system_modules → HC_DONE_MARKER.
      </func>
      <func name="_deploy_orchestrator" sig="(module_names, ...) → (deployed, failed)">
        Calls orchestrator_cli.deploy_many() via subprocess (separate CLI, separate concern).
      </func>
      <func name="_deploy_sequential" sig="(enabled_names, ...) → (deployed, failed)">
        For-loop: detect_type → docker_orchestrator.deploy_docker_module() | invoke_module_interface for system.
      </func>
      <func name="_deploy_system_modules" sig="(system_names) → (deployed, failed)">
        Sequential system module deploy via invoke_module_interface (shell wrapper call).
      </func>
      <func name="_postflight" sig="(all_names, core_dir, ...) → None">
        sudoers_generator.batch_generate(), orphan_reconciler.reconcile(), litellm config render.
      </func>
      <func name="_aggregate_severity" sig="(failed_modules, modules_info) → (crit_count, warn_count)">
        Look up severity from enriched modules dict (topo_sort output), fallback to per-module metadata call.
      </func>
      <func name="_compute_exit_code" sig="(crit, warn, deployed) → int">
        CRIT>0 → 2, WARN>0 → 0 (with log), else → 0.
      </func>
    </sections>
    <crossLinks>
      <link target="docker_orchestrator_py" relation="imports deploy_docker_module, deploy_docker_group, _pre_pull_images"/>
      <link target="secrets_validator_py" relation="imports validate_charsets, parse_node_yaml, check_env, batch_check_env, detect_type"/>
      <link target="context_overlay_py" relation="imports ensure"/>
      <link target="spool_validator_py" relation="imports verify"/>
      <link target="sudoers_generator_py" relation="imports batch_generate"/>
      <link target="orphan_reconciler_py" relation="imports reconcile"/>
      <link target="topo_sort_py" relation="imports load_module_yamls, filter_docker_modules, build_dag, kahn_topological_sort"/>
      <link target="json_field_extractor_py" relation="imports field extraction (if still needed)"/>
      <link target="deploy_modules_sh" relation="called_by exec python3"/>
    </crossLinks>
  </entity>

  <entity id="deploy_modules_sh" type="SHELL_FACADE"
          keywords="deploy-modules shell facade thin wrapper 50-loc">
    <annotation>
      MODIFIED: core/internal/bootstrap/deploy-modules.sh — 260→~45 LOC facade.
      Arg parse → root/node_yaml → network provision → docker login → exec Python.
      TRAP[CROSS-LAYER] preserved. All Python delegation removed.
    </annotation>
    <crossLinks>
      <link target="deploy_orchestrator_py" relation="exec python3 deploy_orchestrator.py"/>
    </crossLinks>
  </entity>

  <entity id="bootstrap_agents_md" type="DOCUMENTATION"
          keywords="AGENTS.md table summary 91→50 shell-facades">
    <annotation>
      MODIFIED: core/internal/bootstrap/AGENTS.md — update table row (deploy-modules.sh: 1664→50),
      update deploy-modules.sh description section.
    </annotation>
  </entity>

  <entity id="test_deploy_orchestrator_py" type="TEST"
          keywords="unit test deploy_orchestrator routing severity mock import">
    <annotation>
      NEW: tests/unit/test_deploy_orchestrator.py — unit tests for routing + severity.
      Mock existing Python module functions. No subprocess — native imports.
    </annotation>
  </entity>

  <entity id="test_deploy_modules_py" type="TEST_MODIFY"
          keywords="static audit grep update references deploy_orchestrator">
    <annotation>
      MODIFIED: tests/test_deploy_modules.py — update static grep references
      from deploy-modules.sh patterns to deploy_orchestrator.py patterns.
    </annotation>
  </entity>

  <entity id="test_deploy_smoke_py" type="TEST_MODIFY"
          keywords="smoke test deploy-modules exit code">
    <annotation>
      MODIFIED: tests/test_deploy_smoke.py — smoke tests for facade exit codes.
      New facade may behave differently (exec replaces process).
    </annotation>
  </entity>

  <entity id="test_hermes_l2_fallback_py" type="TEST_MODIFY"
          keywords="hermes fallback grep references deploy_orchestrator">
    <annotation>
      MODIFIED: tests/test_hermes_l2_fallback.py — update grep references.
    </annotation>
  </entity>
</code_graph>
```

### 2.4 Design Decisions

**D1: Python orchestrator ИМПОРТИРУЕТ существующие модули, а не subprocess-ит их**
## @rationale
**Q:** Почему импорт, а не `subprocess.run(["python3", "docker_orchestrator.py", ...])`?
**A:** (1) Импорт быстрее (нет fork+exec overhead на каждый вызов). (2) Импорт тестируем через `unittest.mock.patch` — не нужен subprocess-mock с реальным fork. (3) Импорт даёт typed interface — IDE/ruff проверяют сигнатуры. (4) Все модули уже в `deploy/__init__.py` как пакет — импорт легален. (5) Языковая политика: «Python-модули вызываются напрямую, shell — тонкий фасад».
**Rejected:** subprocess-вызовы (текущий подход) — медленнее, сложнее тестировать, нарушает layered architecture.

**Исключение:** `orchestrator_cli.py deploy-many` остаётся subprocess-вызовом, т.к. это отдельный CLI (другой слой — `core/internal/deploy/`, не `bootstrap/deploy/`), и он требует специфического окружения.

**D2: Shell facade использует `exec python3` вместо `python3 ...; exit $?`**
## @rationale
**Q:** Почему `exec`, а не subprocess?
**A:** `exec` заменяет shell-процесс Python-процессом — тот же PID, чище process tree, автоматический проброс exit code. Не нужен отдельный `exit $?`. Подходит для финального шага фасада (после всей системной подготовки).
**Rejected:** `python3 deploy_orchestrator.py ...; exit $?` — лишний shell-процесс висит в памяти, два процесса вместо одного.

**D3: `_topo_sort.py` + `json_field_extractor.py` интегрируются в orchestrator**
## @rationale
**Q:** Нужен ли `json_field_extractor.py` после миграции?
**A:** `json_field_extractor.py` существует только для shell↔JSON interop (избегает inline `python3 -c`). После миграции routing в Python, JSON-манипуляции делаются нативно (`json.loads`/`json.dumps`). `json_field_extractor.py` остаётся как утилита (может использоваться другими shell-скриптами), но НЕ вызывается из нового orchestrator-а.
**Rejected:** удаление `json_field_extractor.py` — риск сломать других потребителей (вне скоупа 100).

**D4: `invoke_module_interface` для system-модулей остаётся shell-вызовом**
## @rationale
**Q:** Почему не импортировать `invoke_module_interface`?
**A:** `invoke_module_interface` — это shell-функция из `core/lib/module-interface.sh`. Она делает `systemctl`, `docker inspect`, вызывает `healthcheck.sh` — всё через subprocess/bash. Python-оркестратор вызывает её через `subprocess.run(["bash", "-c", "source module-interface.sh && invoke_module_interface ...])` — это намеренный subprocess для shell-операций.
**Rejected:** реимплементация `invoke_module_interface` на Python — out of scope, ~500 LOC shell-логики.

---

## 3. Step-by-Step Data Flow

```
User/CI: make bootstrap-node NODE=prod
  │
  ▼
entrypoints/bootstrap.sh
  │
  ▼
node-lifecycle.sh --mode init
  │
  ▼
φ8: deploy-modules.sh (NEW FACADE ~45 LOC)
  │
  ├── 1. source lib/paths.sh lib/docker.sh lib/logging.sh
  ├── 2. arg parse: --modules, --skip-provision
  ├── 3. root check: [[ "$(id -u)" -eq 0 ]]
  ├── 4. NODE_YAML check: [[ -n "$NODE_YAML" && -f "$NODE_YAML" ]]
  ├── 5. network provision: bash provision-environment.sh --scope networks/volumes
  ├── 6. docker_login; ghcr_login (shell functions, write ~/.docker/config.json)
  └── 7. exec python3 deploy/deploy_orchestrator.py \
         --node-yaml "$NODE_YAML" \
         --modules-dir "$PATHS_MODULES_DIR" \
         --core-dir "$PATHS_CORE_DIR" \
         --templates-dir "$PATHS_TEMPLATES_DIR" \
         --modules-filter "$MODULES_FILTER" \
         --deploy-parallel "${DEPLOY_PARALLEL:-false}" \
         --deploy-orchestrator "${DEPLOY_ORCHESTRATOR:-false}"
              │
              ▼
deploy/deploy_orchestrator.py::orchestrate()
  │
  ├── PHASE 1: PREFLIGHT
  │   ├── context_overlay.ensure(node_yaml)          # Python import
  │   ├── spool_validator.verify(modules_dir)         # Python import
  │   ├── _create_status_metrics_json()               # inline Python
  │   └── secrets_validator.validate_charsets(...)    # Python import
  │
  ├── PHASE 2: PARSE MODULES
  │   ├── secrets_validator.parse_node_yaml(node_yaml) → ModuleRawList
  │   └── Filter: enabled_only, modules_filter
  │
  ├── PHASE 3: ROUTE & DEPLOY
  │   ├── IF deploy_parallel AND modules exist:
  │   │   ├── topo_sort.load_module_yamls() → filter_docker_modules() → build_dag() → kahn_topological_sort()
  │   │   │   → result: {groups: [[...], [...]], modules: {name: {install_type, severity}}}
  │   │   ├── docker_orchestrator._pre_pull_images(enabled_docker_modules)
  │   │   ├── secrets_validator.batch_check_env(all_modules)
  │   │   ├── IF deploy_orchestrator:
  │   │   │   └── subprocess: orchestrator_cli deploy-many --projects "a,b,c" --scp
  │   │   ├── ELSE:
  │   │   │   └── FOR each topo_group:
  │   │   │       └── docker_orchestrator.deploy_docker_group(group_entries)
  │   │   ├── _deploy_system_modules(system_names)
  │   │   │   └── FOR each system module:
  │   │   │       └── subprocess: invoke_module_interface $name install
  │   │   └── _set_hc_marker()  # touch /var/lib/platform/.bootstrap/.hc_done_in_deploy
  │   │
  │   └── ELSE (sequential fallback):
  │       └── FOR each enabled module:
  │           ├── secrets_validator.check_env(module_name)
  │           ├── secrets_validator.detect_type(module_name)
  │           ├── IF docker: docker_orchestrator.deploy_docker_module(module_name)
  │           └── IF system: subprocess invoke_module_interface $name install
  │
  ├── PHASE 4: POSTFLIGHT
  │   ├── sudoers_generator.batch_generate(all_names)   # Python import
  │   ├── orphan_reconciler.reconcile(enabled_names)     # Python import
  │   └── config_renderer.render_litellm_config(...)     # Python import/subprocess
  │
  └── PHASE 5: SEVERITY → EXIT CODE
      ├── FOR each failed module:
      │   └── lookup severity from topo_modules dict OR per-module metadata call
      ├── CRIT > 0 → return 2
      ├── WARN > 0 → log WARN, return 0
      └── else → return 0
```

---

## 4. Change Impact (Cascade)

Изменение deploy-modules.sh затрагивает каскадно:

| # | Файл | Тип воздействия | Причина |
|---|------|:---:|---------|
| C1 | `core/internal/bootstrap/deploy/__init__.py` | POTENTIAL | Может потребоваться экспорт нового модуля `deploy_orchestrator` |
| C2 | `tests/test_deploy_modules.py` | MODIFY | 52 grep-ссылки на `deploy-modules.sh` → обновить на `deploy_orchestrator.py` |
| C3 | `tests/test_deploy_smoke.py` | MODIFY | Smoke-тесты валидируют exit code фасада |
| C4 | `tests/test_hermes_l2_fallback.py` | MODIFY | 14 grep-ссылок на `deploy-modules.sh` |
| C5 | `core/internal/bootstrap/lifecycle/phases.py` | NO CHANGE | Вызывает `deploy-modules.sh` как subprocess — интерфейс не меняется |
| C6 | `core/internal/bootstrap/lifecycle/state_machine.py` | NO CHANGE | Ссылается на `deploy-modules.sh` по имени — имя не меняется |
| C7 | `core/AGENTS.md` | NO CHANGE | GENERATED файл — обновится через `make generate-agents-md` |
| C8 | `core/entrypoint-manifest.yaml` | NO CHANGE | GENERATED файл — не редактируется вручную |

---

## 5. File Manifest

| # | Файл | Действие | Тип | LOC (оценка) |
|---|------|:--------:|-----|:------------:|
| F1 | `core/internal/bootstrap/deploy/deploy_orchestrator.py` | CREATE | PYTHON | ~250 |
| F2 | `core/internal/bootstrap/deploy-modules.sh` | MODIFY | SHELL | 260→~45 |
| F3 | `core/internal/bootstrap/AGENTS.md` | MODIFY | MARKDOWN | 280→~282 (таблица + описание) |
| F4 | `tests/unit/test_deploy_orchestrator.py` | CREATE | PYTHON | ~300 |
| F5 | `tests/test_deploy_modules.py` | MODIFY | PYTHON | grep-цели обновлены |
| F6 | `tests/test_deploy_smoke.py` | MODIFY | PYTHON | адаптация smoke |
| F7 | `tests/test_hermes_l2_fallback.py` | MODIFY | PYTHON | grep-цели обновлены |

---

## 6. Contracts

### 6.1 `deploy_orchestrator.py::orchestrate()` signature

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class DeployResult:
    deployed: int       # successfully deployed modules
    failed: list[str]   # failed module names
    crit_count: int     # critical failures
    warn_count: int     # warning failures
    exit_code: int      # 0=success, 1=warnings, 2=critical

def orchestrate(
    node_yaml: str,
    modules_dir: str,
    core_dir: str,
    templates_dir: str,
    *,
    modules_filter: str = "",
    deploy_parallel: bool = False,
    deploy_orchestrator: bool = False,
) -> DeployResult:
    """Main orchestration entry point — importable and CLI-callable.
    
    Returns DeployResult with exit_code. Caller should sys.exit(result.exit_code).
    """
```

### 6.2 Shell facade contract

```bash
# deploy-modules.sh — thin facade
# Usage: deploy-modules.sh [--modules a,b,c] [--skip-provision]
# Env: NODE_YAML=/path/to/node.yaml (required)
#      DEPLOY_PARALLEL=true|false (default: false)
#      DEPLOY_ORCHESTRATOR=true|false (default: false)
# Exit: 0=success, 1=warnings, 2=critical failures
# Side effects: docker login (~/.docker/config.json), network provision, 
#               module deployment via Python orchestrator
```

### 6.3 Existing Python module contracts (unchanged)

| Модуль | Ключевые функции | Возврат | Используется в orchestrator как |
|--------|-----------------|---------|-------------------------------|
| `docker_orchestrator.py` | `deploy_docker_module()`, `deploy_docker_group()`, `_pre_pull_images()` | `bool` / `(int, int, list, list)` | прямой импорт |
| `secrets_validator.py` | `validate_charsets()`, `parse_node_yaml()`, `check_env()`, `batch_check_env()`, `detect_type()` | varies | прямой импорт |
| `context_overlay.py` | `ensure()` | `None` (side effect) | прямой импорт |
| `spool_validator.py` | `verify()` | `None` (side effect) | прямой импорт |
| `sudoers_generator.py` | `batch_generate()` | `None` (side effect) | прямой импорт |
| `orphan_reconciler.py` | `reconcile()` | `None` (side effect) | прямой импорт |
| `_topo_sort.py` | `load_module_yamls()`, `filter_docker_modules()`, `build_dag()`, `kahn_topological_sort()` | `list[dict]`, `dict`, `list[list[str]]` | прямой импорт |

⚠️ **Предупреждение:** сигнатуры функций в таблице — по документации модулей. Реальные сигнатуры будут **верифицированы на Step 1.5** (VERIFY_SHARED_CONTRACTS) перед реализацией TASK-1. Coder читает фактические сигнатуры и адаптирует вызовы.

---

## 7. Factual Corrections to Brief 100

| # | Утверждение брифа | Факт (верифицировано 2026-07-31) | Поправка в DevPlan |
|---|-------------------|----------------------------------|-------------------|
| FIX1 | `json_field_extractor.py` в `core/internal/` | Реальный путь: `core/internal/bootstrap/json_field_extractor.py` | Использовать реальный путь |
| FIX2 | `deploy_orchestrator.py` — NEW | Подтверждено: файла нет в `deploy/` | Ок |
| FIX3 | AC7: «AGENTS.md 91→80 LOC» | AGENTS.md файл = 280 строк. «91» — это ячейка таблицы shell-фасадов (стр. 251). Корректно: обновить ячейку таблицы 91→50. | Уточнено |
| FIX4 | «Импортируются 8 Python-модулей» | Фактически в `deploy/` 10 .py файлов (включая `__init__.py`). Из них 4 не используются в deploy-modules.sh: `__init__.py`, `compose_preflight.py`, `content_hash.py`, `context_deployer.py`. | Список уточнён |
| FIX5 | Нет упоминания static-тестов | 81 grep-ссылка на `deploy-modules.sh` в 3 тестовых файлах | Добавлены TASK-4b, TASK-4c, TASK-4d |
| FIX6 | AC2: «≤80 LOC» | При `exec python3` фасад получается ~45 LOC | Цель ужесточена до ≤50 LOC (в рамках ≤80) |

---

## 8. $TASKS

<!-- TASK-LIST:START -->
| ID | Описание | Владелец | Артефакт | Приоритет | Зависимости | Сложность |
|----|----------|:--------:|----------|:---------:|:-----------:|:---------:|
| TASK-1 | Создать `deploy/deploy_orchestrator.py` — routing + severity | Coder | F1 | HIGH | — | 7 |
| TASK-2 | Сократить `deploy-modules.sh` до ≤50 LOC фасада | Coder | F2 | HIGH | TASK-1 | 4 |
| TASK-3 | Обновить `bootstrap/AGENTS.md` — таблица + описание | Coder | F3 | MEDIUM | TASK-2 | 2 |
| TASK-4a | Создать `tests/unit/test_deploy_orchestrator.py` | Coder | F4 | HIGH | TASK-1 | 6 |
| TASK-4b | Обновить `tests/test_deploy_modules.py` — grep-цели | Coder | F5 | MEDIUM | TASK-1, TASK-2 | 4 |
| TASK-4c | Обновить `tests/test_deploy_smoke.py` — smoke-тесты фасада | Coder | F6 | MEDIUM | TASK-2 | 2 |
| TASK-4d | Обновить `tests/test_hermes_l2_fallback.py` — grep-цели | Coder | F7 | LOW | TASK-1 | 1 |
| TASK-5 | `make gate MODE=fast` — верификация | QA | — | HIGH | TASK-1..TASK-4d | 2 |
<!-- TASK-LIST:END -->

### Критический путь
```
TASK-1 ──► TASK-2 ──► TASK-3
   │
   ├──► TASK-4a ──┐
   ├──► TASK-4b ──┤
   ├──► TASK-4c ──┼──► TASK-5
   └──► TASK-4d ──┘
```

### Детализация задач

**TASK-1: `deploy_orchestrator.py`**
- Файл: `core/internal/bootstrap/deploy/deploy_orchestrator.py`
- Контракт: §6.1 (сигнатура `orchestrate()`)
- Импортирует: `docker_orchestrator`, `secrets_validator`, `context_overlay`, `spool_validator`, `sudoers_generator`, `orphan_reconciler`, `_topo_sort`
- Реализует: `orchestrate()`, `main()`, `_preflight()`, `_parse_modules()`, `_route_deploy()`, `_deploy_parallel()`, `_deploy_orchestrator()`, `_deploy_sequential()`, `_deploy_system_modules()`, `_postflight()`, `_aggregate_severity()`, `_compute_exit_code()`
- Acceptance: модуль импортируется без ошибок, `orchestrate()` возвращает `DeployResult`, все routing-ветки покрыты unit-тестами (TASK-4a)

**TASK-2: Shell facade**
- Файл: `core/internal/bootstrap/deploy-modules.sh`
- Цель: 260→≤50 LOC
- Сохранить: `source lib/*.sh`, arg parse (`--modules`, `--skip-provision`), root check, NODE_YAML check, network provision, `docker_login; ghcr_login`, TRAP[CROSS-LAYER]
- Заменить на `exec python3 deploy/deploy_orchestrator.py ...`
- Удалить: весь routing-блок (строки 75-199, 208-260), вызовы `python3 deploy/*.py` (кроме `exec`)
- Acceptance: `wc -l deploy-modules.sh` ≤ 50, manual review: нет routing-логики

**TASK-3: AGENTS.md update**
- Файл: `core/internal/bootstrap/AGENTS.md`
- Обновить таблицу shell-фасадов (строка `deploy-modules.sh: 1664 → 91` → `1664 → 50`)
- Обновить строку «Итого»: `392 → 351` (392 − 91 + 50 = 351)
- Обновить секцию «deploy-modules.sh — две ветки»: описать новый фасад + ссылку на `deploy_orchestrator.py`
- Acceptance: grep `deploy-modules.sh.*1664.*50` находит обновлённую строку

**TASK-4a: Unit tests**
- Файл: `tests/unit/test_deploy_orchestrator.py`
- Маркер: без `requires_docker` (unit-тесты с mock)
- Фикстуры: `tmp_path` для временных node.yaml/module.yaml файлов
- Тестовые сценарии: см. §9 $TEST_SPEC
- Acceptance: `pytest tests/unit/test_deploy_orchestrator.py -v` — зелёный, ≥1 IMP:9 лог на тест

**TASK-4b: Static test updates**
- Файл: `tests/test_deploy_modules.py`
- Обновить grep-цели: `deploy-modules.sh` → `deploy_orchestrator.py` где релевантно (assertions вида `assert "orphan_reconciler.py" in dm_content`)
- Проверить: `test_skip_provision_flag`, `test_batch_module_metadata`, `test_batch_sudoers`, `test_batch_orphan` и др.
- Acceptance: `pytest tests/test_deploy_modules.py -v` — зелёный, ≥1 IMP:9 лог в caplog на тест

**TASK-4c: Smoke test updates**
- Файл: `tests/test_deploy_smoke.py`
- Адаптировать smoke-тесты под новый фасад (exec меняет процесс — subprocess.run вызов shell-фасада всё ещё работает, но PID/process-tree меняется)
- Acceptance: `pytest tests/test_deploy_smoke.py -v` — зелёный, ≥1 IMP:9 лог в caplog

**TASK-4d: Hermes fallback test updates**
- Файл: `tests/test_hermes_l2_fallback.py`
- Обновить grep-цели с `deploy-modules.sh` на `deploy_orchestrator.py` (статический тест `test_hermes_fallback_code_present`)
- Acceptance: `pytest tests/test_hermes_l2_fallback.py -v` — зелёный, ≥1 IMP:9 лог в caplog

**TASK-5: Gate verification**
- `make fix-gate && make gate MODE=fast`
- Проверить: все gate-тесты зелёные, AC8 выполнен
- Acceptance: exit code 0

---

## 9. $TEST_SPEC

**Общие требования ко всем новым тестам:**
- Native imports (не subprocess для бизнес-логики)
- `tmp_path` fixture для временных файлов (не хардкоженные пути)
- LDD caplog: `caplog.set_level(logging.DEBUG)`, ≥1 `[IMP:9]` лог на каждый успешный сценарий
- Test Honesty R1: каждый тест имеет assert (нет pass-тестов)
- Test Honesty R2: assert проверяет meaningful свойство (не language guarantee)
- Маркер: `@pytest.mark.static_audit` для статических тестов, без `requires_docker` для unit-тестов

| Test file | Test function | Scenario | Module under test |
|-----------|---------------|----------|-------------------|
| `tests/unit/test_deploy_orchestrator.py` | `test_orchestrate_sequential_routing` | `deploy_parallel=False` → вызывает `_deploy_sequential` | `deploy_orchestrator._route_deploy` |
| `tests/unit/test_deploy_orchestrator.py` | `test_orchestrate_parallel_routing` | `deploy_parallel=True, deploy_orchestrator=False` → вызывает `_deploy_parallel` | `deploy_orchestrator._route_deploy` |
| `tests/unit/test_deploy_orchestrator.py` | `test_orchestrate_orchestrator_routing` | `deploy_parallel=True, deploy_orchestrator=True` → вызывает `_deploy_orchestrator` | `deploy_orchestrator._route_deploy` |
| `tests/unit/test_deploy_orchestrator.py` | `test_severity_critical_modules_exit_2` | `failed=["critical_mod"]`, severity=critical → `_compute_exit_code` → 2 | `deploy_orchestrator._aggregate_severity`, `_compute_exit_code` |
| `tests/unit/test_deploy_orchestrator.py` | `test_severity_warn_modules_exit_0` | `failed=["warn_mod"]`, severity=warn → `_compute_exit_code` → 0 | `deploy_orchestrator._aggregate_severity`, `_compute_exit_code` |
| `tests/unit/test_deploy_orchestrator.py` | `test_severity_no_failures_exit_0` | `failed=[]` → `_compute_exit_code` → 0 | `deploy_orchestrator._compute_exit_code` |
| `tests/unit/test_deploy_orchestrator.py` | `test_empty_modules_noop` | No enabled modules → early return (deployed=0, failed=[]) | `deploy_orchestrator._parse_modules` |
| `tests/unit/test_deploy_orchestrator.py` | `test_parse_modules_from_node_yaml` | Valid node.yaml → ModuleLists with correct enabled/all | `deploy_orchestrator._parse_modules` |
| `tests/unit/test_deploy_orchestrator.py` | `test_preflight_calls_all_steps` | orchestrate() → _preflight calls context_overlay, spool, secrets validate | `deploy_orchestrator._preflight` |
| `tests/unit/test_deploy_orchestrator.py` | `test_postflight_calls_all_steps` | orchestrate() → _postflight calls sudoers, orphans, litellm config | `deploy_orchestrator._postflight` |
| `tests/unit/test_deploy_orchestrator.py` | `test_deploy_parallel_calls_topo_sort` | `_deploy_parallel()` → импортирует и вызывает `_topo_sort` functions | `deploy_orchestrator._deploy_parallel` |
| `tests/unit/test_deploy_orchestrator.py` | `test_deploy_sequential_iterates_modules` | `_deploy_sequential()` с 3 модулями → 3 вызова deploy | `deploy_orchestrator._deploy_sequential` |
| `tests/test_deploy_modules.py` | `test_skip_provision_flag` | (updated) grep `deploy_orchestrator.py` for skip_provision pattern | static audit |
| `tests/test_deploy_modules.py` | `test_batch_module_metadata` | (updated) grep `deploy_orchestrator.py` for secrets_validator import | static audit |
| `tests/test_deploy_modules.py` | `test_batch_sudoers` | (updated) grep `deploy_orchestrator.py` for sudoers_generator import | static audit |
| `tests/test_deploy_modules.py` | `test_batch_orphan` | (updated) grep `deploy_orchestrator.py` for orphan_reconciler import | static audit |
| `tests/test_deploy_smoke.py` | `test_deploy_modules_no_node_yaml` | (updated) facade exit 1 without NODE_YAML | shell facade |
| `tests/test_deploy_smoke.py` | `test_deploy_modules_missing_node_yaml_file` | (updated) facade exit 1 with nonexistent file | shell facade |

---

## 10. $PARALLEL_GROUPS

### Wave 1 (independent, no shared files)
```
TASK-1: deploy/deploy_orchestrator.py (NEW)      → F1 (новый файл)
TASK-4a: tests/unit/test_deploy_orchestrator.py   → F4 (новый файл, может читать F1)
```
**Команда:**
```
coder Read DevPlan.md, implement Wave 1: TASK-1, TASK-4a.
TASK-1: Create core/internal/bootstrap/deploy/deploy_orchestrator.py
TASK-4a: Create tests/unit/test_deploy_orchestrator.py
```

### Wave 2 (depends on Wave 1)
```
TASK-2: deploy-modules.sh facade                 → F2 (MODIFY, depends on TASK-1 contract)
TASK-3: bootstrap/AGENTS.md update               → F3 (MODIFY, depends on TASK-2 output)
```
**Команда:**
```
coder Read DevPlan.md, implement Wave 2: TASK-2, TASK-3.
TASK-2: Shrink core/internal/bootstrap/deploy-modules.sh to ≤50 LOC
TASK-3: Update core/internal/bootstrap/AGENTS.md table + description
```

### Wave 3 (test adaptation, depends on Wave 1 + Wave 2)
```
TASK-4b: tests/test_deploy_modules.py update     → F5 (MODIFY, depends on F1+F2)
TASK-4c: tests/test_deploy_smoke.py update       → F6 (MODIFY, depends on F2)
TASK-4d: tests/test_hermes_l2_fallback.py update → F7 (MODIFY, depends on F1)
```
**Команда:**
```
coder Read DevPlan.md, implement Wave 3: TASK-4b, TASK-4c, TASK-4d.
```

### Wave 4 (final verification)
```
TASK-5: make gate MODE=fast
```
**Команда:**
```
coder Read DevPlan.md, implement Wave 4: TASK-5.
Run: make fix-gate && make gate MODE=fast
```

---

## 11. Acceptance Criteria → Task Mapping

| AC | Описание | Покрывается |
|----|----------|:-----------:|
| AC1 | Новый `deploy/deploy_orchestrator.py` | TASK-1 |
| AC2 | Shell-фасад ≤50 LOC | TASK-2 |
| AC3 | DEPLOY_PARALLEL=true идентично | TASK-1 + TASK-4a (test_orchestrate_parallel_routing) |
| AC4 | DEPLOY_ORCHESTRATOR=true идентично | TASK-1 + TASK-4a (test_orchestrate_orchestrator_routing) |
| AC5 | Sequential идентично | TASK-1 + TASK-4a (test_orchestrate_sequential_routing) |
| AC6 | Severity-based exit идентичен | TASK-1 + TASK-4a (test_severity_*) |
| AC7 | AGENTS.md таблица 91→50 | TASK-3 |
| AC8 | `make gate MODE=fast` зелёный | TASK-5 |
| AC9 | TRAP[CROSS-LAYER] сохранён | TASK-2 (явная проверка при review) |

---

## 12. Risks & Mitigations

| # | Риск | Вероятность | Влияние | Mitigation |
|---|------|:----------:|:-------:|------------|
| R1 | Сигнатуры Python-функций не совпадают с документацией → ошибки импорта | MEDIUM | HIGH | **Step 1.5 перед TASK-1:** Coder читает фактические сигнатуры `docker_orchestrator.deploy_docker_module()`, `secrets_validator.parse_node_yaml()` и др. Адаптирует вызовы под реальность. |
| R2 | `exec python3` не пробрасывает `docker_login` credentials (разные процессы) | LOW | HIGH | `docker_login` пишет `~/.docker/config.json` — файловая система, не процесс. `exec` замена в том же PID — `config.json` видим. |
| R3 | `_extract_bash_func` (стр. 838 test_deploy_modules.py) определена, но `_run_bash_func` (её единственный caller, стр. 883) не вызывается ни одним тестом — мёртвый код. Реальный риск: статические grep-assertions вида `assert "orphan_reconciler.py" in dm_content` (стр. 1005) сломаются при сокращении shell-фасада. | LOW | MEDIUM | Обновить grep-цели: где раньше искали shell-паттерны в deploy-modules.sh → искать Python-паттерны в deploy_orchestrator.py через `_extract_python_func`. `_extract_bash_func` можно удалить как мёртвый код. |
| R4 | `orchestrator_cli deploy-many` ожидает другой формат имён модулей vs проектов | MEDIUM | MEDIUM | Проверить: DEPLOY_ORCHESTRATOR используется только для docker-модулей (не system). Имена модулей = имена docker compose проектов. |
| R5 | `json_field_extractor.py` используется другими скриптами → удаление запрещено | LOW | HIGH | **НЕ удалять** `json_field_extractor.py`. Только перестать вызывать из нового orchestrator-а. |
| R6 | `make generate-manifests` перезаписывает core/AGENTS.md → ручные правки потеряны | LOW | HIGH | core/AGENTS.md — GENERATED файл, НЕ редактировать вручную. bootstrap/AGENTS.md — ручной, редактировать можно. |
| R7 | Unit-тесты TASK-4a с mock проверяют routing-логику, но не поведенческую идентичность (AC3-AC6: «работает идентично»). Нет интеграционного теста сравнения old shell vs new Python. | MEDIUM | HIGH | Smoke-тесты фасада (TASK-4c) частично покрывают exit-code-контракт. При несовпадении поведения — канареечный деплой на test-VPS (`make test-node`). Рассмотреть добавление comparison-теста в TASK-4c: запустить старый shell и новый Python на одних данных, сравнить exit code + side effects. |

---

## 13. Non-Goals

- ❌ НЕ удалять `json_field_extractor.py` — используется другими shell-скриптами
- ❌ НЕ трогать `core/internal/deploy/orchestrator_cli.py` — отдельная подсистема
- ❌ НЕ мигрировать `invoke_module_interface` на Python — ~500 LOC shell-логики, out of scope
- ❌ НЕ менять `core/AGENTS.md` вручную — generated файл (обновится через `make generate-agents-md`)
- ❌ НЕ менять `core/entrypoint-manifest.yaml` — generated файл
- ❌ НЕ менять сигнатуры существующих Python-модулей — только вызовы

---

## 14. Next Steps

### Wave 1: Python orchestrator + unit tests
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/100-deploy-modules-drift-fix/02-DevPlan.md, implement Wave 1: TASK-1, TASK-4a.
TASK-1: Create core/internal/bootstrap/deploy/deploy_orchestrator.py with routing + severity aggregation
TASK-4a: Create tests/unit/test_deploy_orchestrator.py with routing + severity unit tests
```

### Wave 2: Shell facade + docs
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/100-deploy-modules-drift-fix/02-DevPlan.md, implement Wave 2: TASK-2, TASK-3.
TASK-2: Shrink deploy-modules.sh to ≤50 LOC facade
TASK-3: Update bootstrap/AGENTS.md
```

### Wave 3: Test adaptation
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/100-deploy-modules-drift-fix/02-DevPlan.md, implement Wave 3: TASK-4b, TASK-4c, TASK-4d.
```

### Wave 4: Gate verification
```
coder Read /Users/tronyx/projects/ai-platform/.ai/plans/100-deploy-modules-drift-fix/02-DevPlan.md, implement Wave 4: TASK-5.
```


---

## QA Review (2026-07-31)

**Вердикт: APPROVED-WITH-CORRECTIONS**

**Проверка:** DevPlan 100 против кодовой базы на SHA `fbe306d` (git rev-parse HEAD). Scope: STANDARD+ → Phase 1, 2, 5, 6 + частично 3, 4.

### Внесённые поправки

| # | Секция | Поправка | Обоснование |
|---|--------|----------|-------------|
| C1 | §7 FIX4 | Уточнена формулировка: «10 .py файлов (включая `__init__.py`)» вместо «10 .py файлов (+ `__init__.py`...)» — исходная грамматика была ambiguous | `ls deploy/` = 10 .py total (9 модулей + `__init__.py`). Исходная формулировка `+` могла читаться как «плюс к 10 ещё __init__.py» |
| C2 | §12 R3 | Risk scope сужен: `_extract_bash_func` — мёртвый код (0 активных вызовов), а не активный риск. Реальный риск — статические grep-assertions вида `assert "X.py" in dm_content` (17 сайтов) | `_run_bash_func` (единственный caller `_extract_bash_func`) не вызывается ни одним тестом |
| C3 | §12 R7 | Добавлен риск R7: unit-тесты с mock не гарантируют поведенческую идентичность AC3-AC6 («работает идентично») | AC3-AC6 требуют идентичного поведения, но TASK-4a использует mock-изоляцию. Smoke-тесты (TASK-4c) частично покрывают |
| C4 | §8 TASK-3 | Добавлено обновление строки «Итого»: `392 → 351` (392 − 91 + 50) | При изменении `deploy-modules.sh` с 91 на 50, суммарный LOC shell-фасадов меняется |
| C5 | §8 TASK-4a/b/c/d | Acceptance criteria дополнены требованием `≥1 IMP:9 лог в caplog` на каждый тест | Соответствие `.kilo/rules/testing.md`: Anti-Illusion Rule — 100% PASS без IMP:9 = FAIL |
| C6 | §8 TASK-4a | Явно указан `tmp_path` fixture для временных node.yaml/module.yaml | Zero Hardcode Rule (`.kilo/rules/testing.md`) |
| C7 | §9 $TEST_SPEC | Добавлена преамбула с общими требованиями: native imports, tmp_path, LDD caplog, Test Honesty R1/R2, маркеры | Унификация требований ко всем новым тестам в одном месте |

### Подтверждённые утверждения DevPlan (без поправок)

| Утверждение | Факт | Статус |
|------------|------|:------:|
| `deploy-modules.sh` = 260 LOC | `wc -l` = 260 | ✓ |
| `deploy_orchestrator.py` не существует | `glob` = empty | ✓ |
| `json_field_extractor.py` путь: `core/internal/bootstrap/` | Реальный путь подтверждён | ✓ |
| AGENTS.md таблица: стр. 251 = `1664 \| 91` | Подтверждено чтением | ✓ |
| TRAP[CROSS-LAYER] в deploy-modules.sh:234 | Строка 234, комментарий сохранён | ✓ |
| `_extract_bash_func` определена в test_deploy_modules.py | Стр. 838 | ✓ (мёртвый код) |
| `_extract_python_func` используется в тестах | 13 сайтов вызова | ✓ |
| deploy/ содержит compose_preflight.py, content_hash.py, context_deployer.py (не используются) | Подтверждено grep-ом deploy-modules.sh | ✓ |
| Планы 099, 101-105 не существуют | `glob` = empty | ✓ (нет конфликтов) |

### Проверка инвариантов

| Инвариант | Статус | Комментарий |
|-----------|:------:|-------------|
| Makefile — единый фасад | HELD | `deploy-modules.sh` вызывается из bootstrap pipeline (make bootstrap-node) |
| Python-first (новый код) | HELD | Новый `deploy_orchestrator.py` — Python, shell-фасад ≤50 LOC |
| Shell-фасад <100-200 LOC | HELD | Цель ≤50 LOC в рамках invariant |
| Manifest Generation Contract | HELD | core/AGENTS.md — generated (не трогать), bootstrap/AGENTS.md — ручной |
| Идемпотентность bootstrap | HELD | Фасад не меняет state.json механизм |
| TRAP-аннотации сохранены | HELD | TRAP[CROSS-LAYER] явно tracked в D1 |

### Проверка формата

| Элемент | Статус |
|---------|:------:|
| `$START_DEVPLAN` / `$END_DEVPLAN` | ✓ |
| `$ARTIFACT_CONTRACT` (7 полей) | ✓ |
| `$TASKS` | ✓ |
| `$PARALLEL_GROUPS` | ✓ |
| `$TEST_SPEC` | ✓ (дополнен) |
| Нет заглушек (`...`, `TODO`, `etc.`) | ✓ |

### Оставшиеся риски

1. **R7 (MEDIUM):** Отсутствие интеграционного comparison-теста для AC3-AC6. Рекомендация: добавить в TASK-4c comparison-тест (запустить старый shell + новый Python на fixture-данных, сравнить exit code + side effects). При недоступности Docker на CI — пометить `@pytest.mark.requires_docker`.

2. **Сигнатуры Python-функций (R1):** DevPlan §6.3 честно предупреждает, что сигнатуры — по документации и будут верифицированы на Step 1.5. Coder должен прочитать фактические сигнатуры перед реализацией. QA подтверждает: 7 модулей в `deploy/` суммарно ~220 KB Python-кода, сигнатуры могли измениться с момента документирования.

3. **AGENTS.md секция «deploy-modules.sh — две ветки» (стр. 90-118):** Требует содержательного обновления (не только LOC). Описание PARALLEL/ORCHESTRATOR/SEQUENTIAL режимов должно переместиться в документирующую секцию `deploy_orchestrator.py`. TASK-3 это покрывает, но объём изменений в AGENTS.md может быть больше чем +2 строки.

### Контрольные вопросы для Coder (перед реализацией)

- [ ] Прочитаны ли фактические сигнатуры всех 7 импортируемых модулей? (Step 1.5)
- [ ] Проверен ли `deploy/__init__.py` на необходимость экспорта `deploy_orchestrator`?
- [ ] Учтён ли `context_deployer.py` (TASK-1 postflight вызывает `config_renderer`, не `context_deployer`)?
- [ ] Будут ли smoke-тесты (TASK-4c) по-прежнему работать с `exec python3` (PID меняется)?
- [ ] Сохранён ли `set -euo pipefail` в shell-фасаде?

$END_DEVPLAN
