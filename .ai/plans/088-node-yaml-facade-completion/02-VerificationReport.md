$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Семантическая QA-верификация реализации DevPlan 088 (NodeYaml Facade Completion) — проверка AC1-AC9, кросс-файловый drift-анализ, валидация completeness File Manifest.
DESCRIPTION:           Full LARGE-верификация всех 6 фаз: Static Audit (Phase 1), Cross-File Drift Detection (Phase 2), Invariant Verification (Phase 3), Test Quality Deep Audit (Phase 4), Runtime Validation (Phase 5), Config Sync Audit (Phase 6). 25 изменённых файлов (1787 +, 1554 −).
RATIONALE:             DevPlan 088 — HIGH severity: устраняет 5+ параллельных путей чтения node.yaml, удаляет yq как внешнюю зависимость. Неполная реализация оставляет technical debt (неудалённые stub-файлы, дублирующийся resolve_node_yaml, пропущенные тестовые файлы из File Manifest).
ACCEPTANCE_CRITERIA:   Неприменимо — QA-артефакт
IMPLEMENTS:            QA Role §BEHAVIOR — Semantic Quality Assurance, все 6 фаз (LARGE task >20 files)
IMPACTS:               DevPlan 088 реализация, 25 файлов, AC1-AC9 compliance
REQUIRES:              DevPlan 088 DevPlan.md, git diff HEAD, core/schemas/node.schema.json, core/internal/shared/node_yaml.py, bootstrap.sh, add-project.sh, remove-project.sh, project-list.sh, overlay_deliverer.py, domain_verifier.py, node-resolver.sh, yaml_helpers.py, test suite
$END_ARTIFACT_CONTRACT

---

# VerificationReport 088: NodeYaml Facade Completion — Implementation

🔒 **Verified against SHA:** `f28a0a9b3e69983514326cb487ddf6004df1fbbb`
📅 **Date:** 2026-07-30
📐 **Scope:** LARGE — 25 modified files, 1787 insertions, 1554 deletions
⚠️ **Uncommitted:** 24 files (working set, not pushed)

---

## Semantic Verdict: **DRIFTED (MAJOR)** — 1 BROKEN test, 3 MAJOR drifts (duplicate resolve_node_yaml, missing CREATE files, non-deleted DELETE files)

---

## §0. Prior Report Status

Предыдущий VerificationReport (`01-VerificationReport.md`, 2026-07-28) проводил pre-implementation аудит DevPlan и выявил 7 MAJOR находок. Статус исправлений:

| Предыдущая находка | Статус в реализации |
|-------------------|-------------------|
| DRIFT-FIELDS-1 (39→41 полей) | ✅ Исправлено: NodeYaml typed API покрывает все 41 поле |
| DRIFT-CONSUMER-1 (7 ложных потребителей) | ✅ Исправлено: Python consumers классифицированы корректно |
| DRIFT-CONSUMER-2 (reconciler_projects.py пропущен) | ✅ Исправлено: reconciler_projects.py теперь использует NodeYaml |
| DRIFT-CONSUMER-3 (module-interface.sh ложный consumer) | ✅ Исправлено: module-interface.sh не затронут |
| DRIFT-BUG-1 (yaml_read_key undefined) | ⚠️ Не проверено (runtime, требуется production-окружение) |
| AC7-GAP (missing parity tests) | ⚠️ Не реализовано: нет yq↔NodeYaml parity тестов |

---

## §1. Acceptance Criteria Matrix

| AC | Описание | Статус | Evidence |
|----|---------|--------|----------|
| **AC1** | NodeYaml typed API покрывает все 41 поле | ✅ **PASS** | node_yaml.py: 9 typed dataclasses (ContextEntry, NodeDeclaration, FirewallConfig, SecretEntry, SecretsConfig, TorConfig, ModuleEntry, ProjectEntry, ReposConfig) + 16 typed getters покрывают все 13 top-level + 28 nested полей |
| **AC2** | 0 yaml.safe_load вне NodeYaml | ✅ **PASS** | Единственное совпадение — `context_overlay.py:15` (docstring comment). Реальных yaml.safe_load для node.yaml вне NodeYaml — 0. |
| **AC3** | 0 yq в core/ | ✅ **PASS** | `grep -rn "yq" core/ --include="*.sh"` — 0 результатов. yq полностью удалён. |
| **AC4** | 1 resolve_node_yaml | ❌ **DRIFT** | 3 реализации: node-resolver.sh (✅ facade→NodeYaml CLI), domain_verifier.py (✅ wraps NodeYaml.resolve()), **overlay_deliverer.py:108** (❌ собственная 3-path реализация, НЕ мигрирована) |
| **AC5** | 0 yaml_helpers.py | ⚠️ **PARTIAL** | Файл существует как deprecation stub (27 строк). Внешних вызовов — 0. bootstrap.sh мигрирован (5/5 вызовов → NodeYaml CLI). Но файл не удалён — противоречит File Manifest DELETE. |
| **AC6** | jsonschema валидация | ✅ **PASS** | `NodeYaml.validate(schema_path=...)` использует Draft7Validator, auto-detects `core/schemas/node.schema.json` |
| **AC7** | Функц. эквивалентность | ⚠️ **PARTIAL** | 55/55 node_yaml тестов проходят. Но тесты на yq↔NodeYaml parity отсутствуют, а test_checkpoint_migration.py BROKEN (import deleted module). |
| **AC8** | make gate MODE=fast | ⚠️ **BLOCKED** | Gate fail на `orchestrator_cli.py` missing GREP_SUMMARY (не связано с DevPlan 088). Node_yaml-specific gate passes. |
| **AC9** | pytest tests/ -v проходит | ❌ **BROKEN** | `test_checkpoint_migration.py` → `ModuleNotFoundError: No module named 'checkpoint_migration'`. Collection прерывается. Полный прогон невозможен. |

---

## §2. Static Audit (Phase 1)

### 2.1 Compliance Matrix — Core Files

| File | GREP_SUMMARY | STRUCTURE | MODULE_CONTRACT | #region pairs | Doxygen Tags | IMP:7-10 LDD | Bare except | Secrets |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `node_yaml.py` (1682 LOC) | ✅ | ✅ | ✅ | ✅ 12 | ✅ | ✅ Все функции | ✅ Нет | ✅ Нет |
| `yaml_helpers.py` (27 LOC) | ✅ | ✅ | ✅ | ❌ 0 | N/A (no functions) | N/A | N/A | N/A |
| `node-resolver.sh` (271 LOC) | ✅ | ✅ | ✅ | ✅ 5 | N/A (shell) | ✅ log_imp | N/A | N/A |
| `domain_verifier.py` | ✅ | ✅ | ✅ | N/A | ✅ | ✅ | N/A | N/A |

### 2.2 Findings

| ID | Severity | File:Line | Issue |
|----|----------|-----------|-------|
| **STA-1** | **WARNING** | `node_yaml.py` (all) | 0 TRAP аннотаций. Mutation API (`add_project`, `remove_project`, `update_project`, `_write_back`) — нетривиальная logic с write-back в файл, заслуживает TRAP[BUG] документирования |
| **STA-2** | **WARNING** | `node_yaml.py:1278` | `except Exception as e` в `_write_back()` — catch-all для fallback с ruamel.yaml на PyYAML. Допустимо как graceful degradation, но широкий скоуп |
| **STA-3** | **INFO** | `yaml_helpers.py` | Файл-заглушка без #region маркеров. Поскольку это маркер депрекации, допустимо |

---

## §3. Cross-File Drift Analysis (Phase 2)

### 3.1 Drift Register

| DRIFT-ID | Severity | Type | Description |
|----------|----------|------|-------------|
| **DRIFT-088-1** | **MAJOR** | Duplicate resolve_node_yaml | `overlay_deliverer.py:108` содержит собственную 3-path реализацию resolve_node_yaml — НЕ мигрирована на NodeYaml.resolve(). AC4 нарушен: 3 реализации вместо 1. |
| **DRIFT-088-2** | **CRITICAL** | Broken import | `tests/unit/test_checkpoint_migration.py:31` — `import checkpoint_migration as cm` → `ModuleNotFoundError`. Модуль удалён в DevPlan 087. Блокирует `pytest tests/` collection. |
| **DRIFT-088-3** | **MAJOR** | Missing CREATE files | DevPlan File Manifest обещает CREATE `tests/unit/test_node_yaml_full.py` и `tests/unit/test_node_yaml_mutation.py`. Файлы НЕ созданы. Тесты распределены по существующим test_node_yaml.py и test_node_yaml_facade.py. |
| **DRIFT-088-4** | **MAJOR** | Non-deleted DELETE file | DevPlan File Manifest: DELETE `core/internal/bootstrap/yaml_helpers.py`. Файл существует как deprecation stub (27 LOC). AC5 частично нарушен. |
| **DRIFT-088-5** | **MAJOR** | Non-deleted DELETE file | DevPlan File Manifest: DELETE `tests/unit/test_yaml_helpers.py`. Файл существует с `test_yaml_helpers_deprecated` (1 тест). |
| **DRIFT-088-6** | **WARNING** | Gate-blocking infra | `core/internal/deploy/orchestrator_cli.py` missing GREP_SUMMARY — блокирует `make gate MODE=fast`. Не связано с DevPlan 088, но препятствует AC8. |
| **DRIFT-088-7** | **WARNING** | Raw dict access | `reconciler_projects.py:132-133` использует `NodeYaml(node_yaml_path).load()` + `data.get("projects")` вместо typed `node.get_projects()`. Migration incomplete. |
| **DRIFT-088-8** | **WARNING** | Missing test parity | DevPlan T4e обещал «parity-тест yq eval vs NodeYaml CLI». Не реализован. AC7 проверяется только через существующие тесты. |

### 3.2 resolve_node_yaml — Detailed Comparison

| Файл | Строка | Реализация | Статус миграции |
|------|--------|-----------|----------------|
| `core/lib/node-resolver.sh` | 122 | Shell: `python3 -m core.internal.shared.node_yaml --resolve` | ✅ **FACADE** (T8) |
| `core/internal/verify/domain_verifier.py` | 109 | Python: `NodeYaml.resolve()` wrapper | ✅ **FACADE** (T2.5) |
| `core/internal/bootstrap/overlay_deliverer.py` | 108 | Python: собственная 3-path логика (glob + os.path.isfile) | ❌ **NOT MIGRATED** |

### 3.3 yaml_helpers.py Consumers Audit

| File | Line | Call | Status |
|------|------|------|--------|
| `bootstrap.sh` | 119 | `OWNER_KEY=$(python3 .../yaml_helpers.py ...)` | ✅ Мигрирован → NodeYaml CLI `--get node.owner_key` |
| `bootstrap.sh` | 137 | `CI_DEPLOY_KEY=$(python3 .../yaml_helpers.py ...)` | ✅ Мигрирован → `--get node.ci_deploy_key` |
| `bootstrap.sh` | 151 | `PLATFORM_DOMAIN=$(python3 .../yaml_helpers.py ...)` | ✅ Мигрирован → `--get domain` |
| `bootstrap.sh` | 152 | `CONTEXT=$(python3 .../yaml_helpers.py ...)` | ✅ Мигрирован → `--get context` |
| `bootstrap.sh` | 154 | `CONTEXT=$(python3 .../yaml_helpers.py ...)` | ✅ Мигрирован → `--get contexts.0.name` |

**Итого:** 5/5 вызовов мигрированы. yaml_helpers.py не вызывается ниоткуда.

### 3.4 yq Removal Audit

| Файл | До | После |
|------|----|-------|
| `add-project.sh` | `yq eval -i ".projects += [...]"` | `python3 -m core.internal.shared.node_yaml --add-project` |
| `remove-project.sh` | `yq eval -i "del(.projects[] \| select(.name == ...))"` | `python3 -m core.internal.shared.node_yaml --remove-project` |
| `project-list.sh` | 13 `yq eval` вызовов | `python3 -m core.internal.shared.node_yaml --json-output` + python3 json parsing |
| `project_adopter.py` | `_register_via_yq()` с yq eval | NodeYaml CLI через subprocess / mutation API |

**Итого:** yq полностью удалён из core/. ✅ AC3

---

## §4. Invariant Verification (Phase 3)

### 4.1 Architectural Invariants (из root AGENTS.md)

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Makefile — единый фасад | ✅ HELD | Все shell-скрипты вызываются через make targets. NodeYaml CLI используется shell-скриптами через `python3 -m`, а не напрямую как entrypoint. |
| 2 | Модель деплоя: git push → CI | ✅ HELD | Изменения не затрагивают деплой-модель |
| 4 | AGENTS.md — 3 канонических файла | ✅ HELD | Не изменены |
| 6 | make bootstrap-node — идемпотентный | ⚠️ AT_RISK | bootstrap.sh:5 вызовов yaml_helpers.py заменены на NodeYaml CLI. Функциональная эквивалентность проверена кодом, но не рантайм-тестом на production-окружении. |
| 11 | Manifest Generation Contract | ✅ HELD | Не затронут |

### 4.2 NodeYaml Module Invariants (self-declared)

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Lazy-load | ✅ HELD | `__init__` не читает файл. `_data is None` до первого `_load()` |
| 2 | Cache | ✅ HELD | `_data` кэшируется, `reload()` инвалидирует |
| 3 | Dotted-key access | ✅ HELD | `get("node.host")` traverses nested dicts |
| 7 | get(key) raises ConfigValidationError when no default | ✅ HELD | `ConfigValidationError` на missing key без default |
| 8 | get_list(key) returns [] on missing | ✅ HELD | Тест `test_get_list_missing_key` подтверждает |
| 10 | resolve() searches 3 paths | ✅ HELD | 3-path search в NodeYaml.resolve() |
| 11 | validate() runs jsonschema Draft7 | ✅ HELD | `jsonschema.Draft7Validator` используется |
| 12 | Mutation methods write back via ruamel.yaml | ✅ HELD | `_write_back()` использует ruamel.yaml с fallback на PyYAML |

---

## §5. Test Quality Deep Audit (Phase 4)

### 5.1 Test Inventory

| Test File | Tests | Status |
|-----------|-------|--------|
| `test_node_yaml.py` | 7 | ✅ PASS — тестирует extract_context (deprecated alias) |
| `test_node_yaml_facade.py` | 45 | ✅ PASS — тестирует NodeYaml.get(), get_list(), CLI, validate, cache |
| `test_domain_verifier.py` | 11 | ✅ PASS — resolve_node_yaml, expose domains, HTTP verify |
| `test_yaml_helpers.py` | 1 | ✅ PASS — test_yaml_helpers_deprecated |

### 5.2 Coverage Gaps

| Gap | Severity | Description |
|-----|----------|-------------|
| **GAP-MUTATION** | **HIGH** | Нет тестов на mutation API (`add_project`, `remove_project`, `update_project`, `_write_back`). DevPlan T4d обещал 8 тестов в `test_node_yaml_mutation.py` — файл не создан. |
| **GAP-RESOLVE** | **MEDIUM** | `NodeYaml.resolve()` тестируется косвенно через `test_domain_verifier.py::test_resolve_node_yaml_*` (3 теста), но нет прямых unit-тестов для `NodeYaml.resolve()`. DevPlan T4b обещал 5 тестов. |
| **GAP-VALIDATE** | **MEDIUM** | `NodeYaml.validate()` тестируется через `test_validate_valid` и `test_validate_invalid` (2 теста). DevPlan T4c обещал 5 тестов с jsonschema validation. |
| **GAP-PARITY** | **LOW** | DevPlan T4e обещал parity-тест yq eval vs NodeYaml CLI — не реализован. |
| **GAP-TYPED** | **LOW** | Нет явных тестов для typed getters: `get_contexts()`, `get_firewall()`, `get_secrets_config()`, `get_tor_config()`, `get_repos()`, `get_node_declaration()`, `get_postgres_init_databases()`, `get_acme_dns_plugin()`, `get_email()`, `get_domain()`. Тестируются только косвенно через `--typed-all` CLI. |

### 5.3 Fragile Tests

| ID | File | Issue |
|----|------|-------|
| **BROKEN-1** | `tests/unit/test_checkpoint_migration.py` | ImportError: `checkpoint_migration` module deleted (DevPlan 087). Блокирует collection. Требует удаления или замены. |

### 5.4 Test Health Score: **55/100**

```
100
-10 (BROKEN-1: test_checkpoint_migration.py blocks full collection)
-15 (GAP-MUTATION: нет тестов mutation API — HIGH risk)
-5 (GAP-RESOLVE: недостаточно coverage для resolve)
-5 (GAP-VALIDATE: недостаточно coverage для validate jsonschema)
-2 (GAP-PARITY: нет parity тестов)
-3 (GAP-TYPED: нет тестов typed getters)
-5 (DRIFT-088-3: CREATE test files не созданы)
```

---

## §6. Runtime Validation (Phase 5)

### 6.1 Test Results

| Scope | Passed | Failed | Errors | Time |
|-------|--------|--------|--------|------|
| `tests/unit/test_node_yaml.py` | 7 | 0 | 0 | <1s |
| `tests/unit/test_node_yaml_facade.py` | 36 | 0 | 0 | <1s |
| `tests/unit/test_domain_verifier.py` | 11 | 0 | 0 | <1s |
| `tests/unit/test_yaml_helpers.py` | 1 | 0 | 0 | <1s |
| **NodeYaml-related total** | **55** | **0** | **0** | **<3s** |
| `tests/gates/test_gate_*.py` (70 tests) | 70 | 1* | 0 | 17s |
| `tests/` (full, excl. broken import) | >140+ | 1* | 0 | timeout >300s |

\* `test_gate_grep_summary.py` fails on `orchestrator_cli.py` missing GREP_SUMMARY — не связано с DevPlan 088.

### 6.2 LDD Trace Analysis

**IMP:9 coverage in node_yaml.py (critical business-logic paths):**
- `_load()`: IMP:9 on FileNotFoundError (line 341), YAMLError (line 344), non-dict root (line 353) ✅
- `get()`: IMP:9 on missing key without default (line 445), non-dict intermediate (line 440) ✅
- `get_list()`: IMP:9 on non-dict intermediate (line 487), non-list value (line 494) ✅
- `get_projects()`: IMP:9 on projects not a list (line 566) ✅
- `validate()`: IMP:9 on found errors (line 781), validation OK (line 783) ✅
- `resolve()`: IMP:9 on found (line 846) ✅
- `add_project()`: IMP:9 on project added (line 1167) ✅
- `remove_project()`: IMP:9 on project removed (line 1197) ✅
- `update_project()`: IMP:9 on project updated (line 1238) ✅
- `_write_back()`: IMP:9 on ruamel.yaml (line 1274), PyYAML (line 1286) ✅

**IMP:10 coverage:**
- `validate()`: IMP:10 on schema errors, JSON error, jsonschema unavailable (lines 769, 772, 775, 778) ✅
- `resolve()`: IMP:10 on not found (line 850) ✅
- `add_project()`: IMP:10 on duplicate project (line 1148) ✅
- `_write_back()`: IMP:10 on write failure (line 1288) ✅

**Anti-Illusion Verdict:** ✅ **PASS** — IMP:9-10 логирование присутствует на всех критических путях (загрузка, доступ, валидация, мутация).

### 6.3 Acceptance Criteria Verification

Все измеримые AC (grep counts) проверены и подтверждены в §1. Единственное исключение — AC8 (make gate) заблокирован на инфраструктурной проблеме, не связанной с DevPlan 088.

---

## §7. Config Sync Audit (Phase 6)

### 7.1 Core consumers of node.yaml — Migration Status

| Файл | До | После | Статус |
|------|----|-------|--------|
| `bootstrap.sh` | yaml_helpers.py (5 вызовов) | NodeYaml CLI (5 вызовов) | ✅ |
| `add-project.sh` | yq eval -i | NodeYaml CLI --add-project | ✅ |
| `remove-project.sh` | yq eval -i | NodeYaml CLI --remove-project | ✅ |
| `project-list.sh` | yq eval (13 вызовов) | NodeYaml CLI --json-output | ✅ |
| `node-resolver.sh` | собственная 3-path shell логика | NodeYaml CLI --resolve | ✅ |
| `converge.sh` | node-resolver.sh | node-resolver.sh (facade) | ✅ |
| `node-lifecycle.sh` | node-resolver.sh | node-resolver.sh (facade) | ✅ |
| `reconciler_projects.py` | yaml.safe_load(f) | NodeYaml.load() + raw dict | ⚠️ Partial |
| `overlay_deliverer.py` | resolve_node_yaml (собственная) | resolve_node_yaml (собственная) | ❌ |
| `domain_verifier.py` | resolve_node_yaml (собственная) | NodeYaml.resolve() wrapper | ✅ |

---

## §8. File Manifest Completeness

| Заявлено | Фактически | Статус |
|----------|-----------|--------|
| **CREATE:** `tests/unit/test_node_yaml_full.py` | ❌ Не создан | **DRIFT** |
| **CREATE:** `tests/unit/test_node_yaml_mutation.py` | ❌ Не создан | **DRIFT** |
| **DELETE:** `yaml_helpers.py` | ⚠️ Существует как deprecation stub | **DRIFT** |
| **DELETE:** `test_yaml_helpers.py` | ⚠️ Существует c 1 deprecation тестом | **DRIFT** |
| **MODIFY:** `node_yaml.py` | ✅ Расширен с 711→1682 LOC | **DONE** |
| **MODIFY:** `project_adopter.py` | ✅ yq→NodeYaml CLI | **DONE** |
| **MODIFY:** `reconciler_projects.py` | ✅ yaml.safe_load→NodeYaml (partial) | **DONE** |
| **MODIFY:** `overlay_deliverer.py` | ⚠️ resolve_node_yaml НЕ мигрирован | **PARTIAL** |
| **MODIFY:** `domain_verifier.py` | ✅ resolve_node_yaml→NodeYaml.resolve() wrapper | **DONE** |
| **MODIFY:** `add-project.sh` | ✅ yq→NodeYaml CLI | **DONE** |
| **MODIFY:** `remove-project.sh` | ✅ yq→NodeYaml CLI | **DONE** |
| **MODIFY:** `project-list.sh` | ✅ yq→NodeYaml CLI + python3 json | **DONE** |
| **MODIFY:** `node-resolver.sh` | ✅ facade→NodeYaml CLI | **DONE** |
| **MODIFY:** `yaml_read.sh` | ✅ facade→NodeYaml CLI | **DONE** |
| **MODIFY:** `bootstrap.sh` | ✅ yaml_helpers→NodeYaml CLI (5/5) | **DONE** |
| **MODIFY:** `issue-cert.sh` | ✅ yaml_read→NodeYaml CLI | **DONE** |
| **MODIFY:** `node-lifecycle.sh` | ✅ node-resolver→NodeYaml CLI | **DONE** |
| **MODIFY:** `deploy-project.sh` | ✅ yaml_read→NodeYaml CLI | **DONE** |
| **MODIFY:** `state_machine.py` | 🔄 Рефакторинг (830 строк изменено) | Изменён, но не в File Manifest |
| **MODIFY:** `state_machine.py` bootstrap imports | Добавлены NodeYaml imports | Изменения корректны |

---

## §9. Findings Registry

| ID | Severity | Type | Location | Description | Fix |
|----|----------|------|----------|-------------|-----|
| **BROKEN-1** | **BLOCKER** | Broken import | `tests/unit/test_checkpoint_migration.py:31` | `import checkpoint_migration as cm` → ModuleNotFoundError. Блокирует `pytest tests/` collection. | Удалить test_checkpoint_migration.py или заменить на migration compat test |
| **DRIFT-088-1** | **MAJOR** | Duplicate resolve_node_yaml | `overlay_deliverer.py:108` vs `node_yaml.py:800` | Собственная 3-path реализация, не мигрирована на NodeYaml.resolve(). AC4 нарушен. | Заменить `overlay_deliverer.py:resolve_node_yaml()` на вызов `NodeYaml.resolve()` |
| **DRIFT-088-2** | **MAJOR** | Missing CREATE | File Manifest | `test_node_yaml_full.py` и `test_node_yaml_mutation.py` не созданы. DevPlan T4a-T4e тесты распределены по другим файлам без mutation coverage. | Создать файлы или обновить File Manifest в DevPlan под фактическую структуру |
| **DRIFT-088-3** | **MAJOR** | Non-deleted DELETE | File Manifest | `yaml_helpers.py` и `test_yaml_helpers.py` не удалены (stubs). AC5 частично нарушен. | Полностью удалить оба файла |
| **DRIFT-088-4** | **WARNING** | Gate-blocking infra | `orchestrator_cli.py` | Missing GREP_SUMMARY блокирует gate (не связано с 088) | Добавить GREP_SUMMARY в orchestrator_cli.py |
| **DRIFT-088-5** | **WARNING** | Partial migration | `reconciler_projects.py:132-133` | Использует NodeYaml.load() + raw dict вместо typed getters | Заменить `node.load()` + `data.get("projects")` на `node.get_projects()` |
| **GAP-MUTATION** | **HIGH** | Missing tests | `node_yaml.py:1131-1239` | Mutation API (add/remove/update project) не покрыт unit-тестами | Написать 8+ тестов mutation API с tmp_path fixtures |
| **GAP-TYPED** | **MEDIUM** | Missing tests | `node_yaml.py:858-1067` | 10 typed getters не имеют прямых unit-тестов | Написать тесты для typed getters |
| **STA-1** | **WARNING** | No TRAP annotations | `node_yaml.py` (all) | 0 TRAP аннотаций для mutation API | Добавить TRAP[BUG] на mutation методы |

---

## §10. Health Score: **62/100**

```
100
-10 (BROKEN-1: test_checkpoint_migration.py blocks collection)
-5 (DRIFT-088-1: duplicate resolve_node_yaml)
-3 (DRIFT-088-2: missing CREATE test files)
-3 (DRIFT-088-3: non-deleted DELETE files)
-15 (GAP-MUTATION: mutation API без тестов)
-2 (DRIFT-088-5: partial reconciler migration)
```

---

## §11. Recommendations

### BLOCKER — требует немедленного исправления

1. **[BLOCKER] BROKEN-1:** Удалить `tests/unit/test_checkpoint_migration.py` или заменить его на migration compat test (модуль `checkpoint_migration` удалён в DevPlan 087, тест orphaned).

### MAJOR — требует исправления перед merge

2. **[MAJOR] DRIFT-088-1:** Мигрировать `overlay_deliverer.py:resolve_node_yaml()` на `NodeYaml.resolve()`. Заменить собственную 3-path реализацию (строки 108-138) на вызов:
   ```python
   from core.internal.shared.node_yaml import NodeYaml, ConfigNotFoundError
   ny = NodeYaml.resolve(node_name=node_name, config_dir=platform_root)
   return ny._path
   ```

3. **[MAJOR] DRIFT-088-2/DRIFT-088-3:** Привести File Manifest в соответствие с реальностью:
   - Option A: удалить `yaml_helpers.py` и `test_yaml_helpers.py`
   - Option B: обновить DevPlan File Manifest (DELETE → MODIFY: reduce to stub)
   - Решить: создать `test_node_yaml_mutation.py` с mutation API тестами или явно задокументировать coverage gap

### HIGH — рекомендуется исправить

4. **[HIGH] GAP-MUTATION:** Написать unit-тесты для mutation API:
   - `test_add_project_success` (добавление проекта, проверка write-back)
   - `test_add_project_duplicate` (ConfigValidationError)
   - `test_remove_project_success` (удаление проекта)
   - `test_remove_project_not_found` (возвращает False)
   - `test_update_project_success` (обновление поля)
   - `test_update_project_not_found` (возвращает False)
   - `test_write_back_ruamel_yaml` (проверить сохранение комментариев)
   - `test_write_back_pyyaml_fallback` (fallback при отсутствии ruamel)

### WARNING — желательно исправить

5. **[WARNING] DRIFT-088-4:** Добавить GREP_SUMMARY в `core/internal/deploy/orchestrator_cli.py`
6. **[WARNING] DRIFT-088-5:** Довести `reconciler_projects.py` до полной миграции — заменить `node.load()` на typed getters
7. **[WARNING] STA-1:** Добавить TRAP[BUG] аннотации на mutation методы в node_yaml.py

---

## §12. Handoff

Для исправления BLOCKER и MAJOR находок рекомендуется делегировать Coder:

```
# BLOCKER: удалить orphaned тест
coder: delete tests/unit/test_checkpoint_migration.py
      (module checkpoint_migration был удалён в DP-087)

# MAJOR: мигрировать overlay_deliverer.py resolve_node_yaml
coder implement:
  1. overlay_deliverer.py:108 — заменить resolve_node_yaml() на NodeYaml.resolve()
  2. Удалить yaml_helpers.py + test_yaml_helpers.py (stubs)
  3. Создать test_node_yaml_mutation.py с 8 тестами mutation API
  4. Добавить GREP_SUMMARY в orchestrator_cli.py
```

После исправлений — повторный `python -m pytest tests/ -v` и `make gate MODE=fast`.

$END_VERIFICATION_REPORT
