# 04-DevPlan — B6: NodeYaml, контекст, DTO, валидация

<!-- GREP_SUMMARY: node-yaml context org ProjectEntry DTO schema-validator validate_project_name _write_back dual-schema contexts get_project_entries fail-fast -->
<!-- STRUCTURE: ┌решения архитектора┐ → ◇ T1 контракт контекста → ◇ T2 extract-удаление → ◇ T3 name regex → ◇ T4 ProjectEntry/парсер → ◇ T5 schema_validator → ◇ T6 _write_back/context_registry → ◇ T7 dual-schema domain → ◇ T8 резолвинг → ◇ T9 гейты → ⊕ T10 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B6 программы хардненинга (116): консолидировать контракты вокруг node.yaml — contexts[]-канон (инвариант 3 кодируется в коде), единый ProjectEntry и парсер проектов, единый validate_project_name, единый schema_validator, кэш-безопасность _write_back, flat-only domain.
## @scope    U-06, U-18, U-19, U-20, U-21, U-35, U-54. Файлы: core/internal/shared/{node_yaml.py,project_registry.py,schema_validator.py(NEW)}, core/internal/scaffold/{context_initializer.py,context_registry.py,project_adopter.py,project_lister.py,project_scaffolder.py,vhost_renderer.py}, core/internal/bootstrap/{deploy/context_deployer.py,converge/reconciler.py}, core/internal/{reconciler_projects.py,deploy/orchestrator.py,validate/validate_orchestrator.py,scripts/jsonschema_validate.py,config/platform_config.py}, core/lib/node-resolver.sh, core/modules/status-page/app.py, core/schemas/node.schema.json, node-configs/test-e2e/node.yaml, tests/.
## @invariants
##   1. Инвариант 3 (context = contexts[] канон, legacy `context:` удалён) кодируется в коде: get_context() читает только contexts[0].name, validate() возвращает ошибку на legacy-поле, node.schema.json требует contexts.
##   2. Единая точка чтения node.yaml (NodeYaml facade): 0 raw yaml.safe_load на node.yaml в core/; status-page — единственное документированное исключение (TRAP[DECISION], модульный контейнер без core/).
##   3. Единый парсер проектов: все парсеры node.yaml#projects делегируют NodeYaml.get_project_entries()/get_projects(); malformed-запись → ConfigValidationError (fail-fast, решение пользователя D3).
##   4. Единый schema_validator (shared/): jsonschema_validate.py и node_yaml.validate() — тонкие обёртки над ним; validate_with_ajv/validate_with_python остаются оркестраторами (ajv — внешний инструмент, python-путь — CLI-wrapper).
##   5. Языковая политика: новый код — только Python; никаких inline python3 в shell.
##   6. Consumer-scan обязателен при любом удалении кода (инвариант 2 программы): rg по потребителям + удаление консервирующих тестов.
##   7. Fail-fast вместо silent fallback: _FALLBACK_CONTEXT="test" удаляется (хардкод-копия SoT CONTEXT), парсер проектов не пропускает malformed.
## @rationale Два аудита сошлись: инвариант 3 декларирован, но не закодирован — validate() требует `context`, get_context() фолбэчит на contexts[]: противоположные представления валидности в одном классе. Typed DTO мертвы (consumers берут raw dicts), валидация распылена по 4 точкам, _write_back отравляет кэш при ошибке диска. Волна делает структурно невозможным расхождение контекста/имён/схем через код + гейты.
## @changes 2026-08-01 · Решения пользователя: (D1) status-page остаётся на raw yaml + TRAP[DECISION] (образ модуля не содержит core/, слои запрещают modules→internal; инвариант фасада действует для core/); (D2) context_registry.py включается в волну — NodeYaml.add_context() + миграция (закрытие последнего raw-пути мутации); (D3) канонический парсер проектов — fail-fast raise ConfigValidationError на malformed.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B6 — 10 задач от контракта контекста до гейтов самоверификации.
  DESCRIPTION: Пошаговый план с точными файлами/строками, критериями приёмки на каждую U-проблему, новыми гейтами (trinity), порядком самоверификации.
  RATIONALE: Бриф фиксирует цели; DevPlan фиксирует решения архитектора (D1-D3, подтверждены пользователем 2026-08-01) и исполнительные шаги, чтобы Coder работал без архитектурных развилок.
  ACCEPTANCE_CRITERIA: (1) get_context/validate согласованы — contexts[] канон, legacy `context` даёт ошибку validate; (2) extract_context_from_node_yaml удалён вместе с потребителями; (3) один ProjectEntry в shared, остальные DTO — views, один парсер проектов; (4) validate_project_name — одна строгая функция, все consumers импортируют; (5) один schema_validator в shared, jsonschema_validate — wrapper; (6) _write_back: мутации через deepcopy, кэш инвалидируется при ошибке, context_registry на фасаде; (7) domain flat-only, dict-ветки удалены; (8) make gate MODE=fast зелёный; (9) гейты B6 зарегистрированы в entrypoint-manifest (trinity).
  IMPLEMENTS: U-06, U-18, U-19, U-20, U-21, U-35, U-54
  IMPACTS: core/internal/shared/{node_yaml.py,project_registry.py,schema_validator.py}, core/internal/scaffold/{context_initializer.py,context_registry.py,project_adopter.py,project_lister.py,project_scaffolder.py,vhost_renderer.py}, core/internal/bootstrap/{deploy/context_deployer.py,converge/reconciler.py}, core/internal/{reconciler_projects.py,deploy/orchestrator.py,validate/validate_orchestrator.py,scripts/jsonschema_validate.py,config/platform_config.py}, core/lib/node-resolver.sh, core/modules/status-page/app.py, core/schemas/node.schema.json, node-configs/test-e2e/node.yaml, core/entrypoint-manifest.yaml, core/AGENTS.md, tests/
  REQUIRES: 03-Brief (B6); решения пользователя 2026-08-01 (D1-D3); greenfield (инвариант 9 программы) — legacy-схемы можно удалять
---

## 1. Решения архитектора (подтверждены пользователем 2026-08-01)

| # | Вопрос | Решение |
|---|--------|---------|
| D1 | status-page (U-19, raw yaml в модульном контейнере) | Остаётся raw yaml.safe_load + TRAP[DECISION] в MODULE_CONTRACT app.py: образ модуля (python:3.12-alpine) не содержит core/, слои запрещают modules→internal; инвариант «единая точка чтения node.yaml» действует для core/. status-page исключается из гейта «единый парсер проектов» |
| D2 | context_registry.py (raw-мутация вне U-списка брифа) | Включается в волну: новый NodeYaml.add_context() (мутация через _write_back) + миграция context_registry на фасад. Закрывает последний raw-путь мутации node.yaml |
| D3 | Контракт единого парсера проектов | Fail-fast: malformed-запись (не-dict, нет name) → ConfigValidationError; потребители ловят на своём уровне (существующие except-цепочки расширяются) |
| D4 | Удаление `_FALLBACK_CONTEXT="test"` (platform_config.py:39) | Хардкод-копия SoT (platform-infra.yaml env_defaults.CONTEXT="test"). default_context() → get_default("CONTEXT") без литерала; при отсутствии platform-env.yaml → "" (fail-visible вместо тихой лжи). Проверка: hermes-agent watchdog-образ и docker_orchestrator (consumer-scan в T1.8) |
| D5 | get_context() форма contexts[] | Только dict-форма contexts[0].name (node.schema.json items — dict с required name; additionalProperties:false). str-форма `contexts[0]` (str) удаляется как legacy |
| D6 | Гейты B6 без make-обёрток | Пара pytest-гейтов (test_gate_context_contract, test_gate_single_project_parser) с @pytest.mark.gate + регистрация в entrypoint-manifest (G3); make-обёртки не нужны — гейты идут в make gate автоматически (отличие от B2: нет CLI-таргетов) |

**Текущее состояние worktree (старт волны):** застейджены незакоммиченные изменения волны B2 (регенерированные файлы, parity-гейты) — B2 завершена, коммит не сделан. B6 наслаивается на staging: перед стартом Coder'а сделать `git commit` B2-изменений (отдельным логическим коммитом) ИЛИ работать поверх — решает оператор; важно не смешивать B2/B6 в одном коммите.

---

## 2. Задачи

### T1 — U-18: Контракт контекста — contexts[] канон, legacy `context` удалён [FUNDAMENT]

**1. `node_yaml.py::get_context()` (508-541):** удалить ветку legacy-поля (521-525 `data.get("context")`); удалить str-форму `contexts[0]` (533-534). Канон: только `contexts[0].name` (dict). Пустой contexts / отсутствие → `""` (контракт `no raise` сохранён — потребители deploy_orchestrator:330, context_overlay:128, reconciler:1225, adopter:1077 полагаются на него). Обновить docstring/@invariants (Priority: 1. contexts[0].name → 2. empty).

**2. `node_yaml.py::validate()` (742-745):** заменить проверку `context`-поля на три проверки (код кодирует инвариант 3):
- `data.get("context")` присутствует (любой non-None) → error `"Legacy 'context' field is removed — use 'contexts[0].name' (invariant 3)"`;
- `contexts` отсутствует или не list → error `"Missing 'contexts' section"`;
- `contexts[0]` не dict или `name` пуст → error `"Missing or empty 'contexts[0].name'"`.

**3. `node.schema.json`:** (а) required (строки 8-12): убрать `"context"`, добавить `"contexts"`; (б) properties: удалить блок `"context"` (15-19); (в) contexts (20-52): `minItems: 0` → `minItems: 1` (узел обязан иметь контекст — 1 node = 1 context, bootstrap AGENTS.md). Остальное без изменений.

**4. `context_initializer.py::_SKELETON_TEMPLATE` (58-59):** `context: {context_name}` → `contexts:\n  - name: {context_name}`; обновить STRUCTURE-комментарий шаблона (52-53), если упоминает context-поле.

**5. Миграция образцов node.yaml** (все в lockstep — иначе sessionstart-валидация test_data упадёт):
- `node-configs/test-e2e/node.yaml:26` — `context: test` → `contexts:\n  - name: test`; STRUCTURE-комментарий (строка 2) `┌context+node┐` → `┌contexts+node┐`;
- `tests/test_data/node.yaml:14` — `context: test` → `contexts:\n  - name: test`;
- `tests/test_data/node_yaml_valid.yaml:9` — `context: myorg` → `contexts:\n  - name: myorg`.

**6. `extract_context_from_node_yaml` (1359-1387)** — удалить полностью (+ `import warnings` если не нужен; проверить grep по файлу). Consumer-scan: context_deployer.py:58,658,799 — единственные потребители (T2).

**7. `platform_config.py` (D4):** удалить `_FALLBACK_CONTEXT = "test"` (строка 39); `default_context()` (205-210): `get_default("CONTEXT", _FALLBACK_CONTEXT)` → `get_default("CONTEXT", "")`; обновить @purpose «(SoT: test)» → «(SoT: platform-infra.yaml env_defaults.CONTEXT; без fallback — fail-visible)»; модульный контракт (13-14) — убрать упоминание fallback для CONTEXT. Consumer-scan: `agent_watchdog.py:448,919` (модуль hermes-agent — проверить наличие platform-env.yaml в образе/volume watchdog: если файл доставляется — поведение не меняется, CONTEXT=test из SoT; если нет — CONTEXT становится "" → watchdog не использует контекст-пути; задокументировать TRAP-заметку), `docker_orchestrator.py:447` (тот же паттерн, platform-env.yaml на ноде всегда есть).

**8. `context_deployer.py:661`:** сообщение об ошибке `"ensure node.yaml has context/contexts[0]"` → `"ensure node.yaml has contexts[0].name"`.

**Критерий:** `rg "^context:" node-configs tests/test_data templates/template-context/node-configs` → 0 совпадений (кроме allowlist hermes-agent config.yaml — T9); validate() на legacy-фикстуре → error; get_context() → contexts[0].name.

---

### T2 — U-18: Удаление extract_context_from_node_yaml из production-путей [FUNDAMENT]

**Файл:** `core/internal/bootstrap/deploy/context_deployer.py`.

1. **Строки 55-58:** удалить sys.path-hack (`_SHARED_DIR` insert) + `from node_yaml import extract_context_from_node_yaml` (NodeYaml уже импортирован на 42 — проверить отсутствие других использований `_SHARED_DIR`).
2. **Строка 658** (`deploy_context`): `extract_context_from_node_yaml(node_yaml, log_tag="deploy_context")` → обёртка:
```python
if not context and node_yaml and os.path.isfile(node_yaml):
    try:
        context = NodeYaml(node_yaml).get_context()
    except (ConfigParseError, ConfigNotFoundError) as exc:
        logger.warning("[IMP:7][deploy_context] Cannot read context from %s: %s", node_yaml, exc)
```
   (extract-алиас поглощал ошибки и возвращал ""; сохраняем graceful-degradation, но на фасаде; последующий fail-path «CONTEXT not set» уже существует — 659-665.)
3. **Строка 799** (`main()` CLI): та же замена (try/except → "").
4. **`node_yaml.py` MODULE_CONTRACT** (строка 20, инвариант 9 «extract... maintained as deprecated alias») — удалить строку/обновить @changes: «2026-08-01 · DevPlan 116 B6 — deprecated alias удалён».

**Критерий:** `rg extract_context_from_node_yaml core/` → 0; unit-тест deploy_context с contexts[]-фикстурой (контекст резолвится из contexts[0].name); unit-тест битого node.yaml → DeployResult.failed=1 с читаемым логом (не traceback).

---

### T3 — U-06: единый validate_project_name — строгий regex [FUNDAMENT]

**Канон:** `core/internal/shared/project_registry.py:60-72`. Regex `^[a-zA-Z0-9_-]+$` → `^[a-zA-Z0-9][a-zA-Z0-9_-]*$` (строгий: reject leading `-`/`_`; эквивалентен `context_initializer._VALID_NAME_RE`). Обновить docstring/@invariants (51-56) и D7-@rationale (54-56).

**Миграция 3 локальных валидаторов:**

| Место | Сейчас | Станет |
|-------|--------|--------|
| `reconciler.py:700-719` `_validate_project_name` | Собственная regex + проверка `/` и `..` | Удалить; callsites → `validate_project_name` (импорт как в `_parse_projects_yaml` 680-683: try/except-импорт `core.internal.shared.project_registry`). `/`/`..` уже покрыты строгим regex (`^[a-zA-Z0-9]` не пропускает `/`) — проверить и задокументировать эквивалентность |
| `context_initializer.py:49,87-99` | `_VALID_NAME_RE` + `validate_name` c sys.exit | Удалить `_VALID_NAME_RE`; `validate_name(name)`: `if not validate_project_name(name): ...sys.exit(1)` — тонкий враппер сохраняет CLI-контракт (SystemExit) |
| `project_scaffolder.py:626` | `if not args.name.replace("-", "").replace("_", "").isalnum()` (пропускает leading -/_) | `if not validate_project_name(args.name):` — поведение УСИЛЯЕТСЯ (reject leading -/_); TRAP[BUG]-комментарий на строке |

**Существующие потребители канона** (deploy_engine, payload_deliverer) — не трогаем (уже импортируют).

**Тесты:**
- `tests/test_project_registry.py`: negative-кейсы leading `-`, `_`, `..`, `/`, пустая строка → False (канон усилился);
- `tests/test_converge_exit.py` (test_ln_0 и др., инвентарь: `tests/test_converge_exit.py`): тесты, консервировавшие `_validate_project_name`-ветки reconciler, перевести на канон — импорт/прямой вызов `validate_project_name` вместо удалённой функции (consumer-scan обязателен);
- `tests/unit/test_reconciler.py`: обновить кейсы, если ссылаются на `_validate_project_name` (grep по tests/ — консервирующие).

**Критерий:** `rg "_validate_project_name|_VALID_NAME_RE|replace\(\"-\""` core/ tests/ → 0 (кроме новой записи в гейте); `validate_project_name("-foo") is False`.

---

### T4 — U-20: единый ProjectEntry + единый парсер проектов [FUNDAMENT]

**1. Канон:** `node_yaml.py::ProjectEntry` (204-225) — остаётся единственным определением (поля name/repo/type/domain/database/context, defaults). 

**2. Новый метод `NodeYaml.get_project_entries() -> list[ProjectEntry]`** (рядом с get_project, ~1100):
- итерирует `get_projects()`; строка-запись (str) или не-dict / dict без name → `ConfigValidationError` (D3 fail-fast) с указанием индекса записи;
- `ProjectEntry(name=..., repo=..., type=..., domain=..., database=..., context=...)` из dict (пустые → "");
- IMP:9-лог количества; docstring с контрактом.

**3. `vhost_renderer.py:79-87` `class ProjectEntry`** — удалить локальный dataclass; импортировать `ProjectEntry` из shared (`from core.internal.shared.node_yaml import ProjectEntry`). Совместимость: конструкции `ProjectEntry(name=..., domain=...)` валидны (остальные поля default). Consumer-scan внутри файла (usage `ProjectEntry(`).

**4. `reconciler.py::_parse_projects_yaml` (682-694):** заменить ручной парсинг на `NodeYaml(node_yaml_path).get_project_entries()`; вернуть `list[dict]` {name, domain} (формат вызова сохраняется): `[{"name": e.name, "domain": e.domain} for e in entries]`. except-цепочку (692-694) расширить ConfigValidationError. Важно: текущая версия принимала str-записи (`{"name": p, "domain": ""}`) — D3 отменяет str-форму (schema требует dict) — задокументировать.

**5. `context_deployer.py::ProjectInfo` (83-109):** `from_dict` → `from_entry(entry: ProjectEntry)`; callsites (поиск `ProjectInfo.from_dict` / `ProjectInfo(` в файле) — парсинг через `NodeYaml(node_yaml).get_project_entries()`. ProjectInfo остаётся локальным DTO деплоя (view над ProjectEntry).

**6. `reconciler_projects.py::ProjectSpec` (53-58):** view — `@classmethod from_entry(cls, entry: ProjectEntry, org: str = "")`; парсинг через канон в вызывающем коде. ProjectSpec — reconcile-DTO, остаётся локальным.

**7. `project_lister.py:140-144`:** (а) мутирует `p["node"]`/`p["host"]` прямо в dict из `get_projects()` — это ССЫЛКА на данные node.yaml (кэш NodeYaml!): исправить на копию `entry = dict(p)`; (б) строка 132 `node._data.get("node", {})` → `node.get_node_info().fqdn` (T8).

**8. `orchestrator.py::ProjectStatus` (119)** — deploy-статус, НЕ node.yaml DTO — не трогаем (зафиксировать в DevPlan-комментарии к T4).

**9. `status-page/app.py::get_vhosts` (185-190)** — исключён (D1): TRAP[DECISION] в T8.3.

**Критерий:** единственный парсер node.yaml#projects — `get_project_entries()`/`get_projects()`; `rg "class ProjectEntry" core/` → 1 совпадение (node_yaml.py); `rg "yaml.safe_load" core/internal/bootstrap core/internal/scaffold core/internal/reconciler_projects.py` → 0 на node.yaml-путях (T9 гейт).

---

### T5 — U-21: единый schema_validator [FUNDAMENT]

**1. NEW `core/internal/shared/schema_validator.py`** (паттерн shared-модулей DevPlan 086/116-B2; MODULE_CONTRACT + GREP_SUMMARY/STRUCTURE):
- `validate_yaml_against_schema(yaml_file: Path, schema_file: Path) -> list[str]` — перенос ядра из `jsonschema_validate.py:73-99` БЕЗ изменений формата («  Error at '<path>': <message>») + guard `isinstance(schema, dict)` (TRAP[BUG] 2026-07-31) + exception-контракт (yaml.YAMLError / json.JSONDecodeError / jsonschema SchemaError);
- `validate_dict_against_schema(data: dict, schema: dict) -> list[str]` — in-memory вариант (для NodeYaml.validate): Draft7Validator.iter_errors → список «путь > msg» (формат как текущий node_yaml.validate 766-768: `" -> ".join(absolute_path)`).

**2. `jsonschema_validate.py`** — тонкий CLI-wrapper (~40 строк): argparse-парсинг, вызов `shared.validate_yaml_against_schema`, печать ошибок, exit 0/2 (контракт сохранён). Тело `validate_yaml_against_schema` и `_error_path` — удалить (логика в shared; _error_path — внутренний хелпер shared).

**3. `node_yaml.py::validate()` (757-778):** inline jsonschema-блок заменить на shared: `json.load(schema)` → `validate_dict_against_schema(data, schema)`; формат ошибок (766-768) сохраняется (shared возвращает готовые строки); try/except-структура (JSONDecodeError/SchemaError/ImportError → errors.append) сохраняется.

**4. `validate_orchestrator.py:218,288,327`** — не трогаем: validate_with_ajv — wrapper над внешним ajv (по природе), validate_with_python — wrapper над jsonschema_validate CLI (теперь над wrapper'ом shared — единый вход соблюдён), validate_file — dispatch. Задокументировать в комментарии.

**5. Реестр:** запись в `core/internal/shared/AGENTS.md` (таблица инвентаря — правило 4) + `core/AGENTS.md` §New shared modules (086) при необходимости.

**Тесты:**
- `tests/unit/test_shared_schema_validator.py` (NEW): норма (валид/невалид), не-dict root схемы → SchemaError, битый YAML → YAMLError, empty dict;
- `tests/unit/test_jsonschema_validate.py`: перевести на wrapper-контракт (CLI-поведение через прямой вызов shared);
- `tests/unit/test_node_yaml.py` / facade: validate() schema-часть — регрессия формата.

**Критерий:** `rg "Draft7Validator" core/internal/scripts/jsonschema_validate.py core/internal/shared/node_yaml.py` → 0 (единственная Draft7Validator — в shared/schema_validator.py); make validate / test_jsonschema_validate зелёные.

---

### T6 — U-35: _write_back — deepcopy + инвалидация кэша; context_registry на фасад [FUNDAMENT]

**1. Мутации на deepcopy** (`node_yaml.py`): в `add_project` (1141-1177), `remove_project` (1194-1217), `update_project` (1233-1266) — первая строка `data = self._load()` → `data = copy.deepcopy(self._load())` (TRAP 1130-1140: in-place мутация кэша; deepcopy — т.к. update_project мутирует вложенные dict-записи, shallow недостаточен). `_write_back` по-прежнему инвалидирует `self._data = None` на успехе; при ошибке кэш не отравлен (мутировалась копия).

**2. `_write_back` (1297-1336):**
- PyYAML-failure ветка (1333-1335): добавить `self._data = None` ПЕРЕД raise (страховка от любого кэша, TRAP 1289-1296 — фикс);
- TRAP-комментарии 1278-1288 (broad except Exception) — обновить: except уже сужен до `(yaml.YAMLError, OSError)` — зафиксировать статус «fixed», убрать устаревший текст;
- TRAP-комментарии 1289-1296 — пометить fixed с датой и ссылкой на DevPlan.

**3. NEW `NodeYaml.add_context(name, description="", node_configs_repo="", hermes_agent_repo="") -> bool`** (D2; рядом с мутациями):
- deepcopy-паттерн; отсутствующий/None `contexts` → создаётся list;
- дубликат name → `ConfigValidationError` (как add_project);
- запись: `{"name": name}` + непустые description/node_configs_repo/hermes_agent_repo (схема: additionalProperties:false — только эти 4 поля);
- `_write_back` + IMP:9-лог; возвращает True.

**4. `context_registry.py::register_context` (28-80):** заменить `NodeYaml(yaml_path).raw()` + yaml.dump (63-75) на `NodeYaml(yaml_path).add_context(...)`:
- дубликат: поймать ConfigValidationError → вернуть "EXISTS" (контракт сохранён);
- чтение/запись-ошибки: существующие except → sys.exit(1);
- удалить `import yaml` если не нужен. Consumer-scan: `context_initializer.py` (единственный caller register_context — проверить передачу аргументов).

**Тесты (`tests/unit/test_node_yaml_mutation.py`):**
- «успешная мутация при ошибке диска» (бриф AC6): patch `builtins.open` на запись → OSError; `add_project` → ConfigParseError; затем `node.reload()`/повторный `_load` → НЕ содержит добавленного проекта (кэш чист);
- remove/update — тот же сценарий;
- add_context: добавление, дубликат → ConfigValidationError, отсутствие contexts → создаётся;
- `tests/unit/test_context_registry.py` (если есть — проверить; иначе включить в test_node_yaml_mutation): register_context "OK"/"EXISTS"/error-пути.

**Критерий:** тест «ошибка диска» зелёный; `rg "\.raw\(\)" core/internal/scaffold/context_registry.py` → 0; мутации node.yaml в core/ — только через _write_back.

---

### T7 — U-54: dual-schema domain — flat-only [CODE]

**1. `node_yaml.py::get_domain_config` (607-667):** удалить dict-ветки: (а) platform_domain — только `data.get("domain")` как str (623-630 — dict-ветку убрать); (б) email — только top-level `data.get("email", "")` (634-640 — nested-фолбэк убрать); (в) acme_dns_plugin — только top-level (644-650 — nested убрать). project_domains (652-656) — без изменений. Docstring «Data source priority» — заменить на «Flat schema only (invariant: domain is a string)».

**2. `node_yaml.py::get_domain` (1073-1096):** dict-ветку (1088-1092) удалить — только str; docstring «Supports both» → «Flat string only».

**3. `node_yaml.py::validate()` (731-740):** domain-dict больше не валиден: `elif isinstance(domain, dict):` → error `"'domain' must be a string (flat schema — legacy dict form removed)"`.

**4. `node.schema.json` domain (53-57)** — уже string-only, не трогаем.

**5. `issue-cert.sh:594`** — уже переведён на NodeYaml CLI `--domain-config` (верификация: grep по файлу; задача брифа выполнена ранее — отметить в отчёте, не менять).

**6. Гейт-часть (T9):** negative unit-тест: фикстура с `domain: {platform: ...}` → validate() содержит error; `rg "domain:" c node-configs tests/test_data` — только string-форма (проверка в test_gate_context_contract, пункт (e)).

**Критерий:** `get_domain_config`/`get_domain` не имеют веток `isinstance(domain_raw, dict)`; validate() на dict-domain → error; существующие фикстуры валидны.

---

### T8 — U-19: резолвинг node.yaml — единый путь [CODE]

**1. `project_adopter.py::_resolve_node_yaml_path` (1013-1035):** заменить ручные эвристики на канонический resolve:
```python
try:
    return NodeYaml.resolve(node_name=self.node, config_dir=os.path.join(projects_root, self.org))._path
except ConfigNotFoundError:
    # Fallback: parent-структура проекта (adopter запускается из project dir)
    parent = self.project_dir.parent
    if parent.name == self.org and parent.parent:
        candidate = parent.parent / "node-configs" / self.node / "node.yaml"
        if candidate.exists():
            return str(candidate)
    return None if not projects_root else str(Path(projects_root) / self.org / "node-configs" / self.node / "node.yaml")
```
   (resolve Path 1 = `{config_dir}/node-configs/{node}/node.yaml` ≡ PROJECTS_ROOT/org/... — семантика сохранена; финальный fallback-возврат пути сохранён — caller обрабатывает отсутствие файла.) Consumer-scan: вызовы `_resolve_node_yaml_path` внутри файла.

**2. `project_lister.py:132`:** `node._data.get("node", {}).get("host", "")` → `node.get_node_info().fqdn` (typed getter; + T4.7 копия dict). Проверить: `get_node_info().fqdn` = node.fqdn — а фикстуры используют node.host! `get_node_info` читает `node.get("fqdn", "")` — в образцах только `host`. НЕ эквивалентно. Правильно: `node.get("node.host", default="")` (dotted-ключ, фасад). Указать в DevPlan точно: `host = node.get("node.host", default="")`.

**3. `node-resolver.sh`:** (а) `resolve_node_yaml` (122-144) — уже тонкий CLI-wrapper ✓ не трогаем; (б) `extract_node_host` (~150+): ПРОВЕРИТЬ тело — если содержит inline python3/yaml-парсинг → заменить на `"$(python3 -m core.internal.shared.node_yaml --file "$yaml_path" --get node.host 2>/dev/null)"` с сохранением exit-контракта (0/1); обновить docstring/инварианты (37-38 «MUST use python3 + yaml» → «MUST use NodeYaml CLI --get node.host»).

**4. `status-page/app.py` (D1):** TRAP[DECISION] в MODULE_CONTRACT (119-133 region): причина — образ python:3.12-alpine без core/, слоевой запрет modules→internal (core/AGENTS.md Cross-layer), node.yaml монтируется ro как данные; исключение из инварианта «единая точка чтения» и из гейта T9; Rev: при переносе чтения node.yaml в core-сервис — удалить raw-парсинг.

**5. Ужесточение `load_node_yaml` (120-131)?** — НЕТ (минимальный скоуп по D1: только TRAP; broad `except Exception` остаётся осознанно для модульного контейнера).

**Критерий:** `rg "node\._data|_data\." core/` → 0; `extract_node_host` — через CLI; adopter-резолвинг — через `NodeYaml.resolve` (unit-тест tmp-структурой: PROJECTS_ROOT/org/node-configs/node/node.yaml → найден).

---

### T9 — Гейты самоверификации волны (pytest trinity, без make-обёрток — D6) [ENFORCEMENT]

**1. NEW `tests/gates/test_gate_context_contract.py`** (@pytest.mark.gate; entrypoint-manifest — регенерация G3; repair: нет repair-команд — контрактные гейты):
- (a) **node.yaml-образцы**: все файлы репо, matching `**/node.yaml` + `tests/test_data/node_yaml_valid.yaml` (allowlist ИСКЛЮЧЕНИЙ: `templates/template-context/modules/hermes-agent/config.yaml`, `core/modules/hermes-agent/build/templates/profiles/platform/config.yaml` — hermes-agent config, другой домен): top-level `^context:` → 0 совпадений; `contexts:` присутствует (regex `^contexts:`);
- (b) `rg extract_context_from_node_yaml` по core/ → 0;
- (c) `rg "node\._data"` по core/ → 0;
- (d) negative-контракт: `NodeYaml.validate()` на tmp-фикстуре с legacy `context:` → error содержит «Legacy 'context'»; фикстура с dict-domain → error «must be a string»;
- (e) domain flat-only: в node.yaml-образцах (п. a) нет `domain:` с вложенным dict (regex `^domain:\s*$` с отступом-продолжением — или положиться на (d) + schema-валидацию образцов sessionstart).

**2. NEW `tests/gates/test_gate_single_project_parser.py`** (@pytest.mark.gate):
- (a) бывшие callsites (`reconciler.py`, `context_deployer.py`, `project_lister.py`, `vhost_renderer.py`) не содержат `yaml.safe_load` (на node.yaml-пути) и не объявляют `class ProjectEntry` (vhost_renderer) — grep с allowlist {};
- (b) `rg "class ProjectEntry" core/` → ровно 1 (shared/node_yaml.py);
- (c) `rg "Draft7Validator" core/internal/scripts/jsonschema_validate.py core/internal/shared/node_yaml.py` → 0 (T5).

**3. Регистрация:** `make generate-manifests` (G3 entrypoint-manifest + G4 core/AGENTS.md — новые gate-файлы попадут автоматически); проверить repair-поля; `make check-manifests` зелёный.

**4. `core/AGENTS.md` §New shared modules** — добавить `schema_validator` (086-стиль, если секция живая).

**Критерий:** оба гейта зелёные; check-manifests зелёный; `make gate MODE=fast` проходит.

---

### T10 — Самоверификация волны [VERIFY]

1. Сначала — разделить staging B2: `git commit` волны B2 отдельно (или согласовать с оператором) — не смешивать B2/B6.
2. `make fix-gate && git add -u` — чистое дерево.
3. `make gate MODE=fast` — зелёный (локально, macOS); `make check-manifests` — зелёный.
4. Consumer-scan чек-лист по каждому удалению (инвариант 2 программы):
   - `extract_context_from_node_yaml` (context_deployer 658/799, import 58);
   - `_FALLBACK_CONTEXT` (agent_watchdog 448/919, docker_orchestrator 447 — поведение при отсутствии platform-env.yaml);
   - `_validate_project_name` (callsites в reconciler), `_VALID_NAME_RE` (context_initializer), scaffolder strip-check;
   - vhost_renderer `ProjectEntry` (usage), `ProjectInfo.from_dict` (callsites), `_parse_projects_yaml` (callsites в reconciler);
   - `node._data` (project_lister:132);
   - context_registry raw-dump (единственный caller — context_initializer).
5. Обновление консервирующих тестов: `tests/test_converge_exit.py`, `tests/unit/test_reconciler.py`, `test_project_registry.py`, `test_node_yaml_mutation.py`, `test_jsonschema_validate.py`, `test_context_deployer.py`, `test_project_adopter.py`, `test_vhost_renderer.py`, `tests/test_data/*` (миграция фикстур).
6. Коммит одним или несколькими логическими коммитами (стиль репо: `fix(116): ...` / `feat(116): ...`), включая миграцию образцов node.yaml и регенерированные entrypoint-manifest/core-AGENTS.md.

---

## 3. Порядок и зависимости

T1 → T2 → T3 → T4 → T5 → T6 (независимы после T1; T6.3-4 — после T1.5, т.к. add_context касается contexts[]) → T7 → T8 → T9 (гейты после предметов) → T10.

Критический путь: T1 (контракт контекста + миграция фикстур) → T4/T5 (каноны DTO/схем) → T9 (гейты на финальном состоянии).

Логические волны для Coder'а (одна сессия, последовательно):
- **Wave 1:** T1 + T2 (контекст-контракт, фикстуры, миграция образцов, context_deployer) — обязательный фундамент;
- **Wave 2:** T3 + T7 (regex-канон, flat-domain) — независимы от Wave 1 результата;
- **Wave 3:** T4 + T5 + T6 (DTO/парсер, schema_validator, мутации);
- **Wave 4:** T8 (резолвинг) + T9 (гейты) + T10 (верификация).

## 4. Риски

| Риск | Митигация |
|------|-----------|
| validate() начинает падать на legacy `context:` — фикстуры/sessionstart до миграции (T1.5) | Миграция образцов в том же T1, до прогона гейтов; порядок Coder-волн фиксирован |
| e2e (test-e2e node.yaml): φ5 validate на ноде с legacy | Мигрированный contexts[0].name="test" — контракт CONTEXT не меняется; e2e-инвентарь без изменений |
| watchdog-образ без platform-env.yaml → CONTEXT="" (D4) | Consumer-scan + TRAP-заметка; если образ не имеет файла — контекст-зависимые пути watchdog деградируют явно ("" вместо лжи "test") |
| vhost_renderer `ProjectEntry` import — несовместимость конструкций | Все поля shared имеют defaults; unit-тест vhost_renderer подтвердит |
| deepcopy в мутациях — производительность | O(N) на tiny-файл — пренебрежимо; выигрыш — кэш-безопасность |
| Структура contexts[] (str-записи) в реальных node.yaml на VPS | Greenfield (инвариант 9 программы): нода пересоздаётся; schema требует dict — str-форма невалидна по SoT |
| Регенерация entrypoint-manifest ломает другие гейты | B2 уже прошёл этот путь (check-manifests + fix-gate); регенерация в T9.3, проверка check-manifests |

## 5. Сдача волны

Все 7 AC брифа (согласование контекста, extract-удаление, единый ProjectEntry, единый validate_project_name, единый schema_validator, _write_back-фикс, flat-only domain) + критерии T1-T10; `make gate MODE=fast` зелёный; оба новых гейта (test_gate_context_contract, test_gate_single_project_parser) зелёные; мигрированы все node.yaml-образцы; TRAP[DECISION] для status-page в app.py; коммит(ы) без смешивания с B2.
