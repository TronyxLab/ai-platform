# 03-Brief — B6: NodeYaml, контекст, DTO, валидация

<!-- GREP_SUMMARY: node-yaml context org ProjectEntry DTO schema-validator validate_project_name _write_back dual-schema -->
<!-- STRUCTURE: ┌scope┐ → ◇ контекст → ◇ DTO → ◇ валидаторы → ⊕ критерии → ⎋ зависимости -->
# region MODULE_CONTRACT
## @purpose  Волна B6: консолидировать контракты вокруг node.yaml — контекст/org, DTO проектов, валидацию имён и схем, кэш-безопасность записи.
## @scope    U-06, U-18, U-19, U-20, U-21, U-35, U-54
## @invariants
##   - Единая точка чтения node.yaml (NodeYaml facade); shell-обходы не добавляются.
##   - Инвариант 3 (context = физический путь, поле context УДАЛЕНО) кодируется в коде, не только в AGENTS.md.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Устранить онтологический конфликт контекста, свести 5 Project DTO к одному канону, консолидировать валидацию имён/схем, починить _write_back.
  DESCRIPTION: Выравнивание get_context/validate() (противоречие legacy context vs contexts[]), удаление deprecated extract_context_from_node_yaml из production-путей, единый ProjectEntry, единый validate_project_name, единый schema_validator, фикс инвалидации кэша NodeYaml, решение по dual-schema domain (greenfield: legacy dict-форма удаляется).
  RATIONALE: Инвариант 3 декларирован, но не закодирован: validate() требует поле context, get_context() фолбэчит на contexts[] — противоположные представления валидности в одном классе. Typed DTO мертвы — consumers берут raw dicts.
  ACCEPTANCE_CRITERIA: (1) get_context и validate() согласованы (contexts[] канон, legacy context удалён); (2) extract_context_from_node_yaml удалён вместе с потребителями; (3) один ProjectEntry в shared, остальные DTO — расширения/views; (4) validate_project_name — одна функция, regex канонический (строгий: без leading -/_), все consumers импортируют её; (5) один schema_validator, 4 старых — thin wrappers; (6) _write_back: мутации через shallow-copy, кэш инвалидируется при ошибке; (7) dual-schema domain: dict-форма удалена (greenfield), get_domain_config — flat-only.
  IMPLEMENTS: U-06 (name regex), U-18 (context/org ×6 источников), U-19 (NodeYaml-резолвинг ×4), U-20 (Project DTO ×5), U-21 (schema-валидация ×4), U-35 (_write_back кэш), U-54 (dual-schema domain)
  IMPACTS: core/internal/shared/node_yaml.py, project_registry.py, scaffold/*, deploy/context_deployer.py, bootstrap/deploy/*, core/modules/status-page/app.py, tests/
  REQUIRES: 01-Brief (greenfield — legacy-схемы можно удалять)

---

## Scope

| U | Проблема | Ключевые файлы |
|---|----------|----------------|
| U-06 | Name regex: registry `^[a-zA-Z0-9_-]+$` vs context_initializer строже | project_registry.py:72, context_initializer.py:49,87-99, reconciler.py:702-718, project_scaffolder.py:626 |
| U-18 | Контекст: 6 источников; validate() требует context, get_context() фолбэчит contexts[]; deprecated alias в проде | node_yaml.py:522-537,743-745,1359-1387; context_deployer.py:58,658,799; project_adopter.py:1021; platform_config.py:39-40 |
| U-19 | Резолвинг node.yaml ×4 + `_data` + raw yaml в status-page | node_yaml.py:799-851, node-resolver.sh:135, project_adopter.py:988-1012, project_lister.py:58-74,132, status-page/app.py:122-126 |
| U-20 | ProjectEntry ×2 (collision) + ProjectSpec/Info/Status + 3 парсера | node_yaml.py:204, vhost_renderer.py:80, reconciler_projects.py:55, context_deployer.py:83-109, orchestrator.py:119 |
| U-21 | Schema-валидация ×4 | validate_orchestrator.py:218,288,327, jsonschema_validate.py:73, node_yaml.py:705 |
| U-35 | _write_back: in-place мутация, кэш не инвалидируется при ошибке | node_yaml.py:1130,1176,1186,1215,1226,1264,1278,1289 |
| U-54 | Dual-schema domain: dict-легаси без образцов | node_yaml.py:607-667, node.schema.json:62 |

## Ключевые артефакты

1. Согласование контракта контекста: `contexts[]` — канон; `validate()` валидирует contexts; legacy `context` удаляется; grep-гейт на поле `context` в node.yaml-образцах.
2. Удаление `extract_context_from_node_yaml` + переписывание context_deployer на `get_context()`; проверка 3 call sites.
3. Единый `ProjectEntry` в shared/node_yaml.py; vhost_renderer/others — imports; парсеры projects — одна функция.
4. `validate_project_name` — единая строгая функция (reject leading -/_), reconciler._validate_project_name удаляется, тест test_converge_exit переводится на канон.
5. `shared/schema_validator.py` — единый вход; 4 валидатора — wrappers (или удаление при отсутствии потребителей).
6. `_write_back`: shallow-copy перед мутацией; invalidate cache в except; тест на «успешную» мутацию при ошибке диска.
7. Dual-schema: удаление dict-ветки get_domain_config; issue-cert.sh:594 переводится на node_yaml CLI; node.schema.json — flat-only.

## Гейт самоверификации волны

- Гейт «единый парсер проектов»: все парсеры node.yaml#projects делегируют одной функции.
- Гейт-allowlist: 0 вхождений `extract_context_from_node_yaml` и legacy `domain:` dict в core/.

## Зависимости

- От: B2 (паритет-гейты), 01-Brief (greenfield — удаление legacy разрешено).
- К: B5 (shared-модули используют единый ProjectEntry).
