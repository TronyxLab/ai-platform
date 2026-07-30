$START_VERIFICATION_REPORT

$ARTIFACT_CONTRACT
PURPOSE:               Семантическая QA-верификация DevPlan 088 (NodeYaml Facade Completion) — проверка $ARTIFACT_CONTRACT, self-consistency, корректности typed API, файлового манифеста, coverage mutation API, bootstrap/yaml_read consumer analysis, test breakdown.
DESCRIPTION:           Кросс-файловая верификация DevPlan против реального кода: node.schema.json (41 поле vs заявленные 39), audit yaml.safe_load consumers (найдено 7 ложных в MODIFY), верификация T9.0 (bootstrap.sh yaml_helpers.py — 5/5 calls подтверждено), верификация T6.5 (yaml_read.sh callers — 2/4 реальных, module-interface.sh — ложный), проверка mutation API сигнатур, проверка coverage resolve_node_yaml.
RATIONALE:             DevPlan 088 затрагивает ~35 файлов и удаляет внешнюю зависимость (yq). Неверно классифицированные потребители в MODIFY приведут к wasted work и риску поломки не-node.yaml функциональности. Пропущенный реальный consumer (reconciler_projects.py) оставит неунифицированный yaml.safe_load в кодовой базе.
ACCEPTANCE_CRITERIA:   Неприменимо — это QA-артефакт, не план реализации.
IMPLEMENTS:            QA Role §BEHAVIOR — Semantic Quality Assurance, Phase 1-2 (Static Audit + Cross-File Drift Detection)
IMPACTS:               DevPlan 088 File Manifest MODIFY section, T6.5 callers list, T5 effort estimate, T4 task granularity
REQUIRES:              DevPlan.md (source), core/schemas/node.schema.json, core/internal/shared/node_yaml.py, core/internal/bootstrap/yaml_helpers.py, core/lib/yaml_read.sh, core/entrypoints/bootstrap.sh, полный grep-аудит всех yaml.safe_load/yq eval потребителей
$END_ARTIFACT_CONTRACT

---

# VerificationReport 088: NodeYaml Facade Completion

🔒 **Verified against SHA:** `5a31ef2bafd10b6bbe59345d35625e3b1c108953`
📅 **Date:** 2026-07-28
📐 **Scope:** STANDARD+ (DevPlan 088 — ~35 файлов, config/dependency changes, cross-file drift detection)

---

## Semantic Verdict: **DRIFTED (MAJOR)** — 7 ложных потребителей в MODIFY, пропущен 1 реальный consumer, 2 логические ошибки в T6.5

---

## §1. $ARTIFACT_CONTRACT Audit

| Поле | Статус | Замечание |
|------|--------|-----------|
| PURPOSE | ✅ PASS | Чётко сформулировано |
| DESCRIPTION | ✅ PASS | Полное описание scope |
| RATIONALE | ✅ PASS | 5+ способов чтения node.yaml — обоснованно |
| ACCEPTANCE_CRITERIA | ✅ PASS | 9 измеримых AC (grep counts, make gate, pytest) |
| IMPLEMENTS | ✅ PASS | Ссылки на Superposition Analysis + DP-070 |
| IMPACTS | ✅ PASS | ~35 файлов, разбивка по типам |
| REQUIRES | ✅ PASS | DP-070 + DP-087, рекомендация merge order |

---

## §2. Self-Consistency: File Manifest vs Tasks

### 2.1 Field count: schema vs DevPlan claim

**[MAJOR] DRIFT-FIELDS-1** · `DevPlan.md:38` vs `core/schemas/node.schema.json`

DevPlan заявляет «39 полей (13 top-level + 26 nested)»:
- DevPlan.md:38: `NodeYaml facade coverage (DP-070): 3 поля из 39 (13 top-level + 26 nested)`
- DevPlan.md:155: `Итого: 13 top-level полей, 26+ nested полей = 39 полей`

Фактический подсчёт по `core/schemas/node.schema.json`:
| Top-level | Nesting | Nested fields |
|-----------|---------|---------------|
| contexts | array→object | 4 (name, description, node_configs_repo, hermes_agent_repo) |
| node | object | 5 (name, host, owner_key, ci_deploy_key, timezone) |
| firewall | object | 1 (extra_ports) |
| secrets | object→object | 4 (enc_file, SecretEntry: name, env_var, description) |
| tor | object | 3 (enabled, skip_verify, bridges_file) |
| modules | array→object | 3 (name, enabled, config_overlay) |
| projects | array→object | 6 (name, repo, type, domain, database, context) |
| repos | object | 2 (core, node_configs) |
| **Total nested** | | **28** |
| **Total top-level** | | **13** |
| **Grand total** | | **41** |

**Расхождение:** 41 (факт) vs 39 (DevPlan) = −2 поля в документации.

**Смягчение:** typed API в §2 определяет ВСЕ 28 nested полей корректно (ContextEntry:4 + NodeDeclaration:5 + FirewallConfig:1 + SecretsConfig:4 + TorConfig:3 + ModuleEntry:3 + ProjectEntry:6 + ReposConfig:2 = 28). Ошибка только в summary count. Имплементация не пострадает.

**Рекомендация:** Исправить `DevPlan.md:38` → `41 полей (13 top-level + 28 nested)`, `DevPlan.md:155` → `41 полей`.

---

### 2.2 False consumers in MODIFY (Wave 2 Python consumers)

**[MAJOR] DRIFT-CONSUMER-1** · `DevPlan.md:298-305` · 7 из 8 Python-файлов в MODIFY — либо уже используют NodeYaml, либо не читают node.yaml

Аудит каждого файла из секции MODIFY (Python consumers):

| Файл в MODIFY | Статус | Доказательство |
|---------------|--------|----------------|
| `cert_collector.py` | ❌ УЖЕ NodeYaml | `core/internal/healthcheck/metrics/cert_collector.py:182` — `NodeYaml(node_yaml_path)` |
| `platform_export_metrics.py` | ❌ УЖЕ NodeYaml | `core/internal/healthcheck/platform_export_metrics.py:32` — `from core.internal.shared.node_yaml import NodeYaml` |
| `monitoring_config_renderer.py` | ❌ НЕ node.yaml | `core/internal/monitoring_config_renderer.py:185` — `yaml.safe_load(raw)` читает monitoring configs, НЕ node.yaml |
| `sync_env_defaults.py` | ❌ НЕ node.yaml | `core/internal/scripts/sync_env_defaults.py:47,65` — читает `.env` файлы, НЕ node.yaml |
| `gen_env_platform.py` | ❌ НЕ node.yaml | `core/internal/scaffold/gen_env_platform.py:40` — читает `platform-env.yaml`, НЕ node.yaml |
| `generate_secrets_manifest.py` | ❌ НЕ node.yaml | `core/internal/scripts/generate_secrets_manifest.py:64,111` — читает `secrets-definitions.yaml` + `module.yaml`, НЕ node.yaml |
| `project_adopter.py` | ✅ РЕАЛЬНЫЙ (частично) | `core/internal/scaffold/project_adopter.py:788,803` — `yq eval` для регистрации; `:1156` — NodeYaml для валидации |
| `vhost_renderer.py` | ❌ УЖЕ NodeYaml | `core/internal/scaffold/vhost_renderer.py:54` — `from core.internal.shared.node_yaml import NodeYaml` |

**Итог:** Из 8 файлов в T5 только 1 реальный потребитель (`project_adopter.py`). Wave 2 T5 практически пуст.

**Рекомендация:** Удалить 5 ложных файлов + 2 уже-мигрированных из MODIFY. Добавить `reconciler_projects.py` (см. §2.3). Сократить effort T5 с 4 до 1.

---

### 2.3 Missing real consumer: reconciler_projects.py

**[MINOR] DRIFT-CONSUMER-2** · `DevPlan.md §4 File Manifest` vs `core/internal/reconciler_projects.py:133`

`reconciler_projects.py` содержит `parse_node_yaml_projects()` (строка 118) с прямым `yaml.safe_load(f)` node.yaml (строка 134) и `resolve_ssh_host()` с ещё одним `yaml.safe_load(f)` node.yaml (строка 271). Это реальный consumer, НЕ использующий NodeYaml facade, НЕ включённый в MODIFY.

**Рекомендация:** Добавить `core/internal/reconciler_projects.py` в MODIFY секцию Wave 2.

---

### 2.4 T6.5: False consumer module-interface.sh

**[MAJOR] DRIFT-CONSUMER-3** · `DevPlan.md:262` vs `core/lib/module-interface.sh:110,190`

DevPlan T6.5 перечисляет: `issue-cert.sh, node-lifecycle.sh, deploy-project.sh, module-interface.sh`.

`module-interface.sh` использует `yaml_get_list("$module_yaml", "interfaces")` и `yaml_get_field("$module_yaml", "$hook_field")` — чтение **module.yaml**, НЕ node.yaml. Миграция на NodeYaml CLI сломает этот файл.

`deploy-project.sh` (строка 33) `source`s `yaml_read.sh`, но grep не нашёл вызовов `yaml_get_field`/`yaml_get_list`/`yaml_read_domain_config`/`yaml_read_key` — возможно, используется для platform-env.yaml, а не node.yaml.

**Реальные callers yaml_read.sh для node.yaml:**
1. `issue-cert.sh:587` — `yaml_read_domain_config "$NODE_YAML"` ✅
2. `node-lifecycle.sh:97` — `yaml_read_key "$NODE_YAML" "tor.enabled"` ✅ (но `yaml_read_key` не определена — см. §2.5)

**Рекомендация:** Исключить `module-interface.sh` из T6.5. Проверить `deploy-project.sh` — возможно исключить или заменить на фасад только для node.yaml-специфичных вызовов.

---

### 2.5 node-lifecycle.sh: yaml_read_key — undefined function

**[MINOR] DRIFT-BUG-1** · `core/internal/bootstrap/node-lifecycle.sh:97` vs `core/lib/yaml_read.sh`

`node-lifecycle.sh:97` вызывает `yaml_read_key "$NODE_YAML" "tor.enabled"`. Функция `yaml_read_key` НЕ определена в `yaml_read.sh` (определены: `yaml_get_field`, `yaml_get_list`, `yaml_read_domain_config`). Это либо латентный баг (функция не существует → runtime error), либо функция определена в другом (не-source'd) файле.

**Рекомендация:** При выполнении T6.5 проверить, что `node-lifecycle.sh` действительно вызывает `yaml_read_key`, и заменить на `python3 -m core.internal.shared.node_yaml --file ... --get tor.enabled`.

---

## §3. Mutation API Verification (T3.5)

### 3.1 Сигнатуры vs потребности

| Метод | Сигнатура | Покрывает? | Замечание |
|-------|-----------|-----------|-----------|
| `add_project(project: ProjectEntry)` | `DevPlan.md:200` | ✅ add-project.sh:703 (`yq eval -i ".projects += [...]"`) | Проверка дубликатов по `name` заявлена |
| `remove_project(name: str) -> bool` | `DevPlan.md:209` | ✅ remove-project.sh:196 (`yq eval -i "del(.projects[] \| select(.name == ...))"`) | НЕ удаляет связанные ресурсы (DB, certs) — корректно |
| `update_project(name: str, **updates) -> bool` | `DevPlan.md:218` | ⚠️ НЕТ потребителя | project-list.sh не мутирует; remove-project.sh не обновляет поля. `update_project` — задел на будущее |

**Вердикт (T3.5):** `add_project` и `remove_project` достаточны для замены всех yq-мутаций. `update_project` — опережающий API без текущего потребителя (допустимо как архитектурное решение, DD7 объясняет). Сигнатуры корректны.

---

## §4. T9.0: bootstrap.sh yaml_helpers.py coverage

**[PASS]** `DevPlan.md:265` · Все 5 вызовов `yaml_helpers.py` в `core/entrypoints/bootstrap.sh` верифицированы:

| Строка | Поле | Значение |
|--------|------|----------|
| `bootstrap.sh:119` | `node.owner_key` | `OWNER_KEY=$(python3 .../yaml_helpers.py "${NODE_YAML}" "node.owner_key")` |
| `bootstrap.sh:137` | `node.ci_deploy_key` | `CI_DEPLOY_KEY=$(python3 .../yaml_helpers.py "${NODE_YAML}" "node.ci_deploy_key")` |
| `bootstrap.sh:151` | `domain` | `PLATFORM_DOMAIN=$(python3 .../yaml_helpers.py "${NODE_YAML}" "domain")` |
| `bootstrap.sh:152` | `context` | `CONTEXT=$(python3 .../yaml_helpers.py "${NODE_YAML}" "context")` |
| `bootstrap.sh:154` | `contexts.0.name` | `CONTEXT=$(python3 .../yaml_helpers.py "${NODE_YAML}" "contexts.0.name")` (fallback) |

**Итого:** 5/5 вызовов покрыто. T9.0 effort 2 — адекватно.

**WARNING:** `yaml_helpers.py:29` упоминает usecase `docker.mirror`, но bootstrap.sh НЕ вызывает это поле. Docstring drift в файле, запланированном на удаление — не критично.

---

## §5. T6.5: yaml_read.sh callers — audit summary

| Caller | Использует node.yaml? | Действие |
|--------|----------------------|----------|
| `issue-cert.sh` | ✅ `yaml_read_domain_config "$NODE_YAML"` | Мигрировать на NodeYaml CLI |
| `node-lifecycle.sh` | ✅ `yaml_read_key "$NODE_YAML" "tor.enabled"` (⚠️ undefined!) | Мигрировать + исправить undefined функцию |
| `deploy-project.sh` | ⚠️ sources yaml_read.sh, no yaml_* calls found | Проверить на T6.5 |
| `module-interface.sh` | ❌ `yaml_get_list(module.yaml)` / `yaml_get_field(module.yaml)` | **НЕ мигрировать** — не node.yaml |

---

## §6. T4: Test Breakdown (30+ тестов)

**[MINOR] DRIFT-TASK-1** · `DevPlan.md:248` · T4 объединяет 5 категорий тестов в одну задачу:

| Категория | Кол-во | Файл |
|-----------|--------|------|
| Typed props | 15 | `test_node_yaml_full.py` |
| Resolve | 5 | `test_node_yaml_full.py` |
| Validate | 5 | `test_node_yaml_full.py` |
| Mutation | 8 | `test_node_yaml_mutation.py` (CREATE отдельно) |
| CLI | 7 | `test_node_yaml_full.py` |

Для 30+ тестов с effort 5 создание отдельных подзадач (T4a-T4e) улучшило бы отслеживаемость. Однако текущая группировка допустима — все тесты в одном модуле, общая инфраструктура fixture.

**Рекомендация:** Дополнительно: разбить T4 в Implementation Commands (§6) на подзадачи для Coder-а.

---

## §7. Wave Structure & Dependencies

| Волна | Задачи | Статус | Замечание |
|-------|--------|--------|-----------|
| Wave 1: API Expansion | T1-T4 | ✅ Корректно | NodeYaml расширение + resolve + validate + mutation |
| Wave 2: Python Consumers | T4.5-T6 | ⚠️ Scope reduction | После T4.5 аудита Wave 2 сократится до project_adopter.py + reconciler_projects.py |
| Wave 3: Shell Migration | T6.5-T10 | ✅ Корректно | yq removal + yaml_read + bootstrap — основная работа |
| Wave 4: Gate | T11-T13 | ✅ Корректно | Gate tests + fix-gate |

**Критичность T4.5 (аудит consumers):** Поскольку 7 из 8 заявленных Python-потребителей — ложные, T4.5 СТАНОВИТСЯ КЛЮЧЕВОЙ задачей. Она определит реальный scope Wave 2 и предотвратит wasted work. DevPlan уже включает T4.5 (DD10), но не отмечает, что он может кардинально изменить Wave 2 scope.

---

## §8. AC Measurability

| AC | Измерим? | Метод |
|----|---------|-------|
| AC1 (39→41 полей покрыто) | ✅ | grep count typed fields в node_yaml.py vs schema |
| AC2 (0 yaml.safe_load.*node) | ✅ | `grep -rn "yaml\.safe_load.*node" core/internal/` |
| AC3 (0 yq) | ✅ | `grep -rn "yq.*eval" core/` |
| AC4 (1 resolve_node_yaml) | ✅ | grep count реализаций |
| AC5 (0 yaml_helpers.py) | ✅ | grep count вызовов |
| AC6 (validate jsonschema) | ✅ | unit test assert |
| AC7 (функц. эквивалентность) | ⚠️ Частично | «Все существующие тесты проходят» — но нет тестов на yq-эквивалентность |
| AC8 (make gate зеленый) | ✅ | CI gate |
| AC9 (pytest проходит) | ✅ | `python -m pytest tests/ -v` |

**AC7:** Не хватает explicit тестов на эквивалентность yq-выводов vs NodeYaml-выводов для shell-скриптов. Рекомендуется добавить `test_node_yaml_yq_parity.py` или включить в T4 (CLI tests).

---

## §9. Дополнительные наблюдения

### 9.1 resolve_node_yaml — три реализации верифицированы

| Файл | Строка | Статус в DevPlan |
|------|--------|-----------------|
| `node-resolver.sh` | Shell (yq-based) | T8: фасад → NodeYaml.resolve() |
| `overlay_deliverer.py:108` | Python, NodeYaml.load() | В MODIFY ✅ |
| `domain_verifier.py:109` | Python, без NodeYaml | В MODIFY (T2.5) ✅ |

DevPlan корректно идентифицировал все три. DD8 объясняет обнаружение domain_verifier.py реализации.

### 9.2 project_adopter.py: двойной статус

`project_adopter.py` частично использует NodeYaml (`validate_org_against_node_yaml:1156`), но также содержит `_register_via_yq()` c `yq eval` (строки 788, 803). Файл правильно помечен как `(частично)` в MODIFY. Mutation API (T3.5) должен заменить `_register_via_yq()`.

### 9.3 Ложные потребители из §4 (НЕ МОДИФИЦИРУЮТСЯ) — корректны

Все файлы в секции «НЕ МОДИФИЦИРУЮТСЯ» правильно исключены:
- `secrets_validator.py` — читает secrets.yaml ✅
- `discover_modules.py` — читает compose.yaml + module.yaml ✅
- `template_engine.py` — читает template-manifest.yaml ✅
- `generate_entrypoint_manifest.py` — читает entrypoint-manifest.yaml ✅
- `generate_agents_md.py` — читает entrypoint-manifest.yaml ✅
- `reconcile-projects.sh` — фасад, не yq ✅

### 9.4 DD5: validate() использует core/schemas/node.schema.json — корректно

DevPlan явно запрещает создание нового `node_yaml_schema.json`. Существующий `core/schemas/node.schema.json` — authoritative source. ✅

---

## §10. Findings Registry

| ID | Severity | Type | Location | Description |
|----|----------|------|----------|-------------|
| DRIFT-FIELDS-1 | **MAJOR** | Field count | `DevPlan.md:38,155` | Заявлено 39 полей, реально 41 (13 top + 28 nested). typed API корректен, только summary ошибочен. |
| DRIFT-CONSUMER-1 | **MAJOR** | False consumers | `DevPlan.md:298-305` | 7 из 8 Python consumers в MODIFY — ложные (уже на NodeYaml или не читают node.yaml). Wave 2 T5 практически пуст. |
| DRIFT-CONSUMER-2 | **MINOR** | Missing consumer | `DevPlan.md §4 File Manifest` | `reconciler_projects.py` — реальный consumer с yaml.safe_load(node.yaml), пропущен в MODIFY. |
| DRIFT-CONSUMER-3 | **MAJOR** | False consumer T6.5 | `DevPlan.md:262` | `module-interface.sh` использует yaml_read.sh для module.yaml (не node.yaml). Миграция на NodeYaml CLI сломает. |
| DRIFT-BUG-1 | **MINOR** | Undefined function | `node-lifecycle.sh:97` vs `yaml_read.sh` | `yaml_read_key` не определена в yaml_read.sh. Латентный runtime bug, влияет на T6.5. |
| DRIFT-TASK-1 | **MINOR** | Task granularity | `DevPlan.md:248` | T4 (30+ тестов, effort 5) без подзадач. Рекомендуется разбить на T4a-T4e. |
| AC7-GAP | **MINOR** | Test gap | `DevPlan.md:14` | AC7 требует «функциональную эквивалентность», но нет тестов на yq↔NodeYaml parity для shell consumers. |

---

## §11. Итоговый вердикт

| Вердикт | Severity | Причина |
|---------|----------|---------|
| **DRIFTED** | **MAJOR** | 3 MAJOR-находки: ложные потребители в Wave 2 MODIFY (7/8 файлов), ложный consumer в T6.5 (module-interface.sh), расхождение field count. DevPlan требует значительной корректировки File Manifest перед имплементацией. |

**Health Score:** 70/100
- −5 за DRIFT-CONSUMER-1 (7 ложных потребителей — wasted work risk)
- −5 за DRIFT-CONSUMER-3 (module-interface.sh — риск поломки)
- −5 за DRIFT-FIELDS-1 (некорректный field count)
- −3 за DRIFT-CONSUMER-2 (пропущенный consumer)
- −3 за DRIFT-BUG-1 (undefined yaml_read_key)
- −3 за DRIFT-TASK-1 (task granularity)
- −3 за AC7-GAP (missing parity tests)
- −3 за отсутствие явного warn о критичности T4.5 (scope redefinition)

---

## §12. Рекомендации

1. **[BLOCKER — нет]** Ни одной BLOCKER-находки. DevPlan пригоден к исполнению после внесения правок.

2. **[MAJOR] Исправить MODIFY в File Manifest:**
   - Удалить: `cert_collector.py`, `platform_export_metrics.py`, `monitoring_config_renderer.py`, `sync_env_defaults.py`, `gen_env_platform.py`, `generate_secrets_manifest.py`, `vhost_renderer.py`
   - Добавить: `core/internal/reconciler_projects.py`
   - Сократить effort T5 с 4 до 1

3. **[MAJOR] Исправить T6.5 callers:**
   - Исключить `module-interface.sh`
   - Проверить `deploy-project.sh` (возможно, исключить)
   - Добавить note о `yaml_read_key` undefined в `node-lifecycle.sh`

4. **[MAJOR] Исправить field count:**
   - `DevPlan.md:38` → `41 полей (13 top-level + 28 nested)`
   - `DevPlan.md:155` → `41 полей`

5. **[MINOR] Добавить reconciler_projects.py в MODIFY**

6. **[MINOR] Разбить T4 на подзадачи в §6 Implementation Commands**

7. **[MINOR] Добавить parity tests в AC7 или T4 (yq↔NodeYaml equivalence)**

8. **[INFO] Явно отметить T4.5 как scope-defining задачу** — добавить примечание, что результаты T4.5 могут сократить Wave 2 до 1-2 файлов.

---

## §13. Handoff

Для исправления MAJOR-находок (DRIFT-CONSUMER-1, DRIFT-CONSUMER-3, DRIFT-FIELDS-1) рекомендуется делегировать архитектору (Architect) через `task` tool:

```
task(subagent_type="Plan", description="Fix DevPlan 088 drift",
  prompt="Review VerificationReport at .ai/plans/088-node-yaml-facade-completion/01-VerificationReport.md. Fix MAJOR findings: update MODIFY consumer list, correct field count, fix T6.5 callers. Update DevPlan.md accordingly.")
```

$END_VERIFICATION_REPORT
