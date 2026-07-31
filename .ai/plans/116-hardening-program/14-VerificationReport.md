$START_VERIFICATION_REPORT

# 14-VerificationReport — B6: NodeYaml, контекст, DTO, валидация (DevPlan 116, волна 04)

🔒 Verified against SHA `8046b222d6c10a5ce57ed03af895fe6773e24359` (HEAD, ветка `main`).
⚠️ Рабочее дерево НЕ чистое: ~50+ staged/modified файлов — это и есть реализация волны B6 (коммит не сделан).

$ARTIFACT_CONTRACT:
  PURPOSE: Верификация реализации DevPlan 116 B6 (04-DevPlan.md) — 7 AC брифа, 10 задач T1-T10, 2 новых гейта.
  DESCRIPTION: Статический аудит (Phase 1), cross-file drift-детекция (Phase 2), инварианты (Phase 3), качество тестов (Phase 4), runtime-валидация (Phase 5). Семантический вердикт + Issues для fix-лупа (если есть).
  RATIONALE: 03-Brief фиксирует цели волны; QA-роль независимо верифицирует реализацию кодером — не верит отчёту кодера, проверяет сама: файлы, grep-критерии, тесты, LDD-траектории.
  ACCEPTANCE_CRITERIA: (1) get_context/validate согласованы; (2) extract_context_from_node_yaml удалён; (3) единый ProjectEntry + парсер; (4) единый validate_project_name (строгий regex); (5) единый schema_validator; (6) _write_back deepcopy + инвалидация кэша; (7) domain flat-only + удалённые dict-ветки.
  IMPLEMENTS: U-06, U-18, U-19, U-20, U-21, U-35, U-54
  IMPACTS: core/internal/shared/{node_yaml.py,project_registry.py,schema_validator.py}, scaffold/*, bootstrap/deploy/*, bootstrap/converge/*, node.schema.json, node-configs/*, tests/
  REQUIRES: 03-Brief (B6); 04-DevPlan (T1-T10); решения пользователя D1-D6 (2026-08-01)

---

<!-- GREP_SUMMARY: VerificationReport B6 node-yaml context ProjectEntry DTO schema-validator validate_project_name _write_back dual-schema gate trinity STABLE -->
<!-- STRUCTURE: ┌scope+method┐ → ◇ AC 1-7 audit → ◇ grep-criteria → ◇ runtime (161/161 PASS) → ◇ LDD IMP:9 → ◇ R1-R5 test quality → ⊕ вердикт STABLE -->

---

## 1. Scope и методология

**Scope:** 22 ключевых файла (node_yaml.py, schema_validator.py NEW, project_registry.py, context_deployer.py, context_initializer.py, context_registry.py, project_lister.py, project_scaffolder.py, vhost_renderer.py, reconciler.py, project_adopter.py, project_remover.py, jsonschema_validate.py, validate_orchestrator.py, platform_config.py, node-resolver.sh, status-page/app.py, node.schema.json, node-configs/test-e2e/node.yaml, tests/test_data/node.yaml, tests/test_data/node_yaml_valid.yaml) + 2 новых гейта + 11 тестовых файлов.

**Метод:** Phase 1 (статический аудит grep-критериев DevPlan), Phase 2 (cross-file drift: grep-таблицы), Phase 5 (runtime: 161 тест), LDD IMP:9 верификация.

**Размер задачи:** LARGE (>20 файлов, архитектурные/схемные изменения, удаление legacy-веток, изменение node.schema.json required fields).

---

## 2. Результаты аудита AC 1-7 (03-Brief)

| # | Acceptance Criterion | Status | Evidence |
|---|---------------------|--------|----------|
| AC1 | get_context и validate() согласованы: contexts[] канон, legacy context удалён | **PASS** | node_yaml.py:519-540 — get_context() только contexts[0].name; node_yaml.py:725-738 — validate() 3 проверки (legacy → error, missing contexts → error, empty contexts[0].name → error); node.schema.json:8-12 — required=["node","modules","contexts"], minItems:1, "context" удалён; grep `^context:` node-configs/ tests/test_data/ → 0 |
| AC2 | extract_context_from_node_yaml удалён + потребители | **PASS** | grep `extract_context_from_node_yaml` core/ → 0; context_deployer.py:660,805 — `NodeYaml(node_yaml).get_context()`; node_yaml.py MODULE_CONTRACT: deprecated alias ссылка удалена |
| AC3 | Один ProjectEntry в shared, единый парсер проектов (get_project_entries) | **PASS** | grep `class ProjectEntry` core/ → 1 (node_yaml.py:213); node_yaml.py:1121-1154 — get_project_entries() canon parser, fail-fast ConfigValidationError на malformed; vhost_renderer.py — импорт из shared; reconciler.py — `NodeYaml(node_yaml_path).get_project_entries()`; context_deployer.py:ProjectInfo.from_entry() |
| AC4 | validate_project_name — единая строгая функция (регулярка ^[a-zA-Z0-9][a-zA-Z0-9_-]*$) | **PASS** | project_registry.py:68-81 — строгий regex (reject leading -/_); grep `_validate_project_name\|_VALID_NAME_RE` core/ → 0; context_initializer.py:93-95 — тонкий враппер; project_scaffolder.py — replace("-","_") удалён; tests: `validate_project_name("-foo") is False` |
| AC5 | Единый schema_validator в shared, jsonschema_validate — wrapper | **PASS** | schema_validator.py (NEW, 132 строк): validate_yaml_against_schema() + validate_dict_against_schema(); grep `Draft7Validator` core/internal/scripts/jsonschema_validate.py → 0; grep `Draft7Validator` core/internal/shared/node_yaml.py → 0; node_yaml.validate():755-760 — делегирование; jsonschema_validate.py — тонкий CLI-wrapper |
| AC6 | _write_back: мутации через deepcopy, кэш инвалидируется при ошибке; context_registry на фасаде | **PASS** | node_yaml.py:1184,1238,1281,1341 — `copy.deepcopy(self._load())` в add/remove/update/add_context; node_yaml.py:1433 — `self._data = None` перед raise в PyYAML failure; grep `\.raw()` core/internal/scaffold/context_registry.py → 0; tests: disk_error_cache_clean ×3, add_context ×3 — PASS |
| AC7 | Domain flat-only, dict-ветки удалены | **PASS** | node_yaml.py:607-618 — get_domain_config flat-only; node_yaml.py:1074-1080 — get_domain isinstance(str) only; node_yaml.py:720-721 — validate() dict-domain → error; node.schema.json:48-51 — domain: {type: string}; grep `domain:` dict в fixtures → 0 |

---

## 3. Рантайм-результаты

### 3.1 Pytest: 161/161 PASS (2.62s)

```
tests/gates/test_gate_context_contract.py ......    6 passed
tests/gates/test_gate_single_project_parser.py ...  3 passed
tests/test_converge_exit.py .........               9 passed
tests/unit/test_context_deployer.py .......         7 passed
tests/unit/test_jsonschema_validate.py .........    9 passed
tests/unit/test_node_yaml.py ......                 6 passed
tests/unit/test_node_yaml_mutation.py .............. 14 passed
tests/unit/test_project_adopter.py ................. 17 passed
tests/unit/test_project_registry.py .................... 20 passed
tests/unit/test_reconciler.py .......................... 34 passed
tests/unit/test_shared_schema_validator.py ......   6 passed
tests/unit/test_vhost_renderer.py ...................... 30 passed
─────────────────────────────────────────────────────────
TOTAL: 161 passed in 2.62s
```

### 3.2 Grep-критерии (DevPlan T1-T9)

| # | Критерий | Результат | Evidence |
|---|----------|-----------|----------|
| G1 | `rg "^context:" node-configs tests/test_data` → 0 | **PASS** | 0 совпадений; все 3 фикстуры мигрированы на contexts[] |
| G2 | `rg extract_context_from_node_yaml core/` → 0 | **PASS** | 0 совпадений |
| G3 | `rg "node\._data" core/` → 0 | **PASS** | 0 совпадений |
| G4 | `rg "class ProjectEntry" core/` → 1 (node_yaml.py) | **PASS** | Только node_yaml.py:213 |
| G5 | `rg "Draft7Validator" jsonschema_validate.py` → 0 | **PASS** | 0 совпадений |
| G6 | `rg "Draft7Validator" node_yaml.py` → 0 | **PASS** | 0 совпадений |
| G7 | `rg "_validate_project_name\|_VALID_NAME_RE" core/` → 0 | **PASS** | 0 совпадений |
| G8 | `rg "\.raw()" context_registry.py` → 0 | **PASS** | 0 совпадений |
| G9 | `rg "^context:" templates/template-context/node-configs` → 0 | **PASS** | 0 совпадений |
| G10 | _FALLBACK_CONTEXT в platform_config.py — удалён | **PASS** | platform_config.py:41 — NOTE о удалении; default_context() → get_default("CONTEXT", "") |
| G11 | context_deployer.py:661 сообщение → "contexts[0].name" | **PASS** | context_deployer.py:665 — "ensure node.yaml has contexts[0].name" |
| G12 | context_initializer skeleton → contexts[] | **PASS** | context_initializer.py:55 — `contexts:\n  - name: {context_name}` |
| G13 | node.schema.json required → contexts (без context) | **PASS** | node.schema.json:8-12 — required: ["node","modules","contexts"]; minItems:1 |
| G14 | extract_node_host → NodeYaml CLI | **PASS** | node-resolver.sh:256-259 — `python3 -m core.internal.shared.node_yaml --file ... --get node.host` |

---

## 4. LDD-траектория (IMP:9 верификация)

Все 9 gate-тестов (6 context_contract + 3 single_project_parser) логируют IMP:9 — подтверждено через `pytest -s --log-cli-level=DEBUG | grep "IMP:9"`:

```
[IMP:9][gate_context_contract][a] PASS: 3 samples, 0 legacy context
[IMP:9][gate_context_contract][b] PASS: 0 extract-alias references in core/
[IMP:9][gate_context_contract][c] PASS: 0 node._data references in core/
[IMP:9][gate_context_contract][d] PASS: validate() rejects legacy 'context' field
[IMP:9][gate_context_contract][d] PASS: validate() rejects dict-form domain
[IMP:9][gate_context_contract][e] PASS: domain flat-only in all samples
[IMP:9][gate_single_parser][a] PASS: no node.yaml yaml.safe_load in callsites
[IMP:9][gate_single_parser][b] PASS: single ProjectEntry in shared
[IMP:9][gate_single_parser][c] PASS: single Draft7Validator point in shared/schema_validator
```

Unit-тесты ключевых бизнес-операций также логируют IMP:9:
- `[IMP:9][NodeYaml.validate] Found N error(s)` — validate()
- `[IMP:9][NodeYaml.get_project_entries] N project(s) parsed` — canon parser
- `[IMP:9][NodeYaml.add_project] Added project` — mutations
- `[IMP:9][NodeYaml.add_context] Added context` — add_context
- `[IMP:9][NodeYaml._write_back] Written` — write-back

**Anti-Illusion Rule:** ✅ PASS — IMP:9 бизнес-логика присутствует во всех успешных сценариях.

---

## 5. Тест-качество R1-R5

| Rule | Description | Verdict | Evidence |
|------|-------------|---------|----------|
| R1 | NO pass-tests (нет ассертов или `assert True`) | **PASS** | Все тесты имеют meaningful asserts; 0 `assert True`/bare `try/except pass` в B6-скоупе |
| R2 | NO unfalsifiable asserts (`assert isinstance(x, object)`) | **PASS** | Все ассерты на конкретные бизнес-контракты: `assert validate_project_name("-foo") is False`, `assert any("Legacy 'context'" in e for e in errors)` |
| R3 | STALE SKIP = RED (skip > 90 дней) | **PASS** | 0 skip-маркеров в: test_gate_context_contract.py, test_gate_single_project_parser.py, test_node_yaml_mutation.py, test_shared_schema_validator.py |
| R4 | NO_SERVICE = FAIL, not skip | **N/A** | Docker-зависимых тестов в B6-скоупе нет; все тесты статические (unit/gate) |
| R5 | ANTI-SURVIVORSHIP — negative test для каждого gate | **PASS** | AC1: `test_validate_rejects_legacy_context` + `test_validate_rejects_dict_domain`; AC4: `test_validate_project_name_leading_dash_underscore` (-foo→False, _bar→False, ../escape→False); AC6: `test_add_project_disk_error_cache_clean` ×3 + `test_add_context_duplicate_raises`; AC5: `test_shared_schema_validator.py` — не-dict root → SchemaError, битый YAML → YAMLError |

**Skip rate:** 0% (в B6-скоупе нет skip'ов).

---

## 6. Gate Trinity — регистрация

| Gate | Файл tests/gates/ | @pytest.mark.gate | entrypoint-manifest | Статус |
|------|-------------------|-------------------|---------------------|--------|
| test_gate_context_contract | ✅ test_gate_context_contract.py | ✅ 6 тестов с маркером | ✅ 6 записей (id: test_no_extract..., test_no_private..., test_node_yaml_samples..., test_node_yaml_samples_domain..., test_validate_rejects_dict..., test_validate_rejects_legacy...) | **TRINITY OK** |
| test_gate_single_project_parser | ✅ test_gate_single_project_parser.py | ✅ 3 теста с маркером | ✅ 3 записи (id: test_callsites_no_node..., test_single_draft7..., test_single_project_entry...) | **TRINITY OK** |

Все 9 gate-тестов запускаются в `make gate MODE=fast` и зелёные.

---

## 7. Решения архитектора — реализация

| # | Решение | Статус | Evidence |
|---|---------|--------|----------|
| D1 | status-page raw yaml + TRAP[DECISION] | **PASS** | app.py:43-49 — TRAP[DECISION] с обоснованием (layer violation + image bloat); исключён из гейта T9 |
| D2 | context_registry на фасад (add_context) | **PASS** | context_registry.py → NodeYaml.add_context(); grep `.raw()` → 0; тесты add_context ×3 PASS |
| D3 | Fail-fast парсер проектов (ConfigValidationError) | **PASS** | node_yaml.py:1133-1142 — ConfigValidationError на malformed с индексом записи; потребители расширяют except-цепочки |
| D4 | _FALLBACK_CONTEXT удалён (fail-visible) | **PASS** | platform_config.py:41-44 — NOTE + default_context() → ""; consumer-scan: watchdog + docker_orchestrator — задокументировано |
| D5 | get_context только dict-form contexts[0].name | **PASS** | node_yaml.py:530-537 — удалены str-form и legacy context; schema требует dict |
| D6 | Гейты B6 без make-обёрток | **PASS** | 2 pytest-гейта с @pytest.mark.gate, зарегистрированы в manifest (G3), запускаются через `make gate` |

---

## 8. Отклонения и риски

**Отклонений от DevPlan не обнаружено.** Все 10 задач T1-T10 реализованы в соответствии с планом:
- T1: контракт контекста — contexts[] канон, validate() 3 проверки, schema обновлён, скелетон мигрирован, фикстуры мигрированы ✅
- T2: extract_context_from_node_yaml удалён, context_deployer на фасаде ✅
- T3: единый строгий validate_project_name, 3 локальных валидатора мигрированы ✅
- T4: единый ProjectEntry + get_project_entries canon parser, vhost_renderer/reconciler/context_deployer/reconciler_projects мигрированы ✅
- T5: единый schema_validator, jsonschema_validate — wrapper, node_yaml.validate — delegate ✅
- T6: deepcopy в мутациях, cache invalidation, add_context, context_registry на фасаде ✅
- T7: domain flat-only, dict-ветки удалены ✅
- T8: project_adopter resolve, project_lister dict copy, node-resolver.sh CLI, status-page TRAP[DECISION] ✅
- T9: 2 гейта, trinity-регистрация, grep-критерии ✅
- T10: 161/161 тестов зелёные, grep-таблица 14/14 ✅

**Риски (зафиксированы, не блокируют):**
- [INFO] Рабочее дерево не чистое — все изменения волны B6 не закоммичены (ожидаемо для верификации после реализации)
- [INFO] status-page остаётся на raw yaml.safe_load — исключение документировано TRAP[DECISION] (D1), Rev-условие указано
- [INFO] _FALLBACK_CONTEXT удалён — watchdog без platform-env.yaml получит CONTEXT="" вместо "test" (fail-visible). Consumer-scan выполнен, поведение задокументировано в DevPlan

---

## 9. Семантический вердикт

**VERDICT: STABLE**

| Параметр | Значение |
|----------|----------|
| Acceptance Criteria (7) | 7/7 PASS |
| Pytest (scope) | 161/161 PASS (0 failures, 0 skips) |
| Grep-критерии (14) | 14/14 PASS |
| Gate Trinity (2 gates, 9 tests) | 9/9 PASS, оба гейта зарегистрированы |
| LDD IMP:9 | Anti-Illusion PASS — IMP:9 логи во всех gate-тестах и ключевых unit-тестах |
| R1-R5 Test Honesty | Все правила PASS (R4 N/A) |
| Инвариант 3 (context = contexts[] canon) | Кодирован в коде: get_context, validate, node.schema.json, grep-гейты |
| Cross-file drift | 0 расхождений (все grep-критерии зелёные) |
| Архитектурные решения D1-D6 | Все 6 реализованы |

**Issues для fix-лупа: НЕТ.** Все критерии выполнены, отклонений не обнаружено. Волна B6 готова к коммиту.

$END_VERIFICATION_REPORT
