# 17-DevPlan — B8: Dead-code волна (consumer-scan)

<!-- GREP_SUMMARY: dead-code steps.py orchestrator-available content-hash s3-ssl-cache resume_phase dangling-refs consumer-scan phantom-gate -->
<!-- STRUCTURE: ┌решения пользователя D1-D4┐ → ◇ T1 steps.py → ◇ T2 _ORCHESTRATOR_AVAILABLE → ◇ T3 json_field_extractor/url_encoder → ◇ T4 content-hash/s3-ssl-cache → ◇ T5 phases.py+фантомы → ◇ T6 yaml_read_domain_config → ◇ T7 resume_phase → ◇ T8 гейты → ⊕ T9 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B8 программы хардненинга (116): удаление мёртвого кода с обязательным consumer-scan (прецедент audit_logging.sh — сломал provision на 2 дня). Каждое удаление: rg по потребителям → удаление консервирующих тестов → зелёный gate.
## @scope    U-26, U-27, U-40, U-41, U-42, U-64, U-66. Файлы: core/internal/bootstrap/lifecycle/{steps.py,state_machine.py,phases.py,__init__.py}, bootstrap/deploy/{docker_orchestrator.py,deploy_orchestrator.py}, bootstrap/{overlay_deliverer.py,content-hash.sh,s3-ssl-cache.sh,json_field_extractor.py}, notify/url_encoder.py, lib/{yaml_read.sh,audit.sh,docker.sh,healthcheck.sh}, core/modules/platform-secrets/install.sh, core/entrypoints/{build.sh,deploy.sh}, core/internal/{deploy/*,catalog,notify,scripts/sync_env_defaults.py,shared/*}, core/modules/nginx/*, makefiles/deploy.mk, .github/workflows/platform-deploy.yml, AGENTS.md, core/entrypoint-manifest.yaml, tests/*, tests/unit/*, tests/integration/*, tests/e2e/*, tests/gates/*, core/internal/{bootstrap/AGENTS.md,shared/AGENTS.md}.
## @invariants
##   1. Любое удаление сопровождается: rg по потребителям (код+тесты+CI+манифест) → удаление консервирующих тестов → зелёный gate.
##   2. Мёртвый код не «чинится», а удаляется; если потребитель существует — он мигрирует, а не сохраняется ради него.
##   3. state_machine.py: удаляется ТОЛЬКО мёртвый код (resume_phase, _grouped_phases, _compute_step_hash, suppress-import); структурные правки — запрещены (мораторий B9, разрешён мёртвый код — 01-Brief §1).
##   4. reconciler_projects.py::deploy_via_orchestrator (line 264, вызов line 461) — ЖИВАЯ функция, НЕ трогается (U-26 относится только к копиям в docker_orchestrator/overlay_deliverer).
##   5. execute_grouped_phase (state_machine.py:876-956) ОСТАЁТСЯ (D4) — машинерия sub-step идемпотентности, тестируется напрямую.
## @rationale Бриф фиксирует цели; DevPlan фиксирует решения пользователя (D1-D4, 2026-08-01) и исполнительные шаги с точными файлами/строками, чтобы Coder работал без архитектурных развилок. Consumer-scan выявил 3 потребителя, не указанных в брифе: core/modules/platform-secrets/install.sh:20 (LIVE source удалённого audit_logging.sh — бриф указывал неверный путь), tests/unit/test_bootstrap_no_duplicate_steps.py (2 gate-теста читают steps.py), tests/gates/test_gate_thin_wrapper.py:54 (stale allowlist-запись deploy-project.sh).
## @changes 2026-08-01 · Решения пользователя: (D1) B8 работает поверх грязного дерева (B4 незакоммичена — коммиты B8 будут содержать перемешанные изменения B4, принято); (D2) phases.py forced-command → `python3 -m core.internal.deploy.orchestrator_cli receive` (паттерн setup-node.sh:112, фикс сейчас, B1 позже апгрейдит до dispatch); (D3) гейт фантомов СТРОГИЙ — 0 упоминаний 4 имён (deploy-project.sh/state_migration.py/audit_logging.sh/generate-dev-certs.sh) в коде и CI, включая docstring/TRAP (история удаляется, принято); (D4) U-66 бриф-буквально: удалить resume_phase+_grouped_phases+3 пинящих теста, execute_grouped_phase остаётся.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B8 — 9 задач от steps.py до строгого гейта фантомов с нулевым allowlist.
  DESCRIPTION: Пошаговый план с точными файлами/строками, полным реестром фантомных упоминаний (46 сайтов), критериями приёмки на каждую U-проблему, новым гейтом (trinity), порядком самоверификации.
  RATIONALE: Бриф фиксирует цели; DevPlan фиксирует решения пользователя (D1-D4, подтверждены 2026-08-01) и результаты consumer-scan (включая не-упомянутые в брифе потребители), чтобы Coder работал без развилок.
  ACCEPTANCE_CRITERIA: (1) steps.py удалён (манифест/AGENTS.md обновлены, 2 gate-теста из test_bootstrap_no_duplicate_steps удалены); (2) _ORCHESTRATOR_AVAILABLE + deploy_via_orchestrator (docker_orchestrator) + deliver_via_orchestrator_scp удалены; (3) json_field_extractor, url_encoder удалены (test_importability_no_exit allowlist очищен); (4) content-hash.sh, s3-ssl-cache.sh удалены (gate allowlists, scripts-audit, CERT_SCRIPTS, docs); (5) phases.py:249 → orchestrator_cli receive; install.sh:20 → audit.sh; sync_env_defaults:190 + .env.example регенерация; AGENTS.md:107; 46 фантомных сайтов очищены; (6) yaml_read_domain_config удалена + test_deploy_modules.py:725-831 удалён; (7) resume_phase/_grouped_phases удалены, 3 пинящих теста обработаны, execute_grouped_phase сохранён; (8) test_gate_phantom_refs.py (strict, _ALLOWLIST пуст) + dead-code gate и no_unregistered_entrypoint с чистыми allowlist — зелёные; (9) make gate MODE=fast зелёный.
  IMPLEMENTS: U-26 (_ORCHESTRATOR_AVAILABLE), U-27 (steps.py), U-40 (CLI-утилиты), U-41 (фасады), U-42 (dangling refs), U-64 (yaml_read_domain_config), U-66 (resume_phase)
  IMPACTS: core/internal/bootstrap/lifecycle/*, core/lib/{yaml_read.sh,audit.sh,docker.sh,healthcheck.sh}, core/internal/bootstrap/{content-hash.sh,s3-ssl-cache.sh,json_field_extractor.py,deploy/*,AGENTS.md}, core/internal/notify/url_encoder.py, core/internal/{deploy/*,catalog/generate_catalog.py,scripts/sync_env_defaults.py,shared/*,AGENTS.md}, core/modules/{platform-secrets/install.sh,nginx/*,monitoring/hooks/*,postgres/hooks/*}, core/entrypoints/{build.sh,deploy.sh}, makefiles/deploy.mk, .github/workflows/platform-deploy.yml, AGENTS.md, core/entrypoint-manifest.yaml, tests/*, tests/unit/*, tests/integration/*, tests/e2e/*, tests/gates/*
  REQUIRES: 06-Brief (B8); решения пользователя 2026-08-01 (D1-D4); B4-состояние в рабочем дереве (незакоммичено — D1); B5 (shared-модули — удаляемые копии не являются shared)
---

## 1. Решения пользователя (подтверждены 2026-08-01)

| # | Вопрос | Решение |
|---|--------|---------|
| D1 | Незакоммиченная волна B4 (65 файлов) в рабочем дереве | **B8 работает поверх грязного дерева.** B4 остаётся незакоммиченной; B8-коммиты будут содержать перемешанные изменения B4 (принято пользователем). Coder НЕ откатывает и НЕ коммитит B4 отдельно; финальный B8-коммит включает всё дерево. Верификация — против дерева as-is |
| D2 | phases.py:249 forced-command → удалённый deploy-project.sh | **Фикс сейчас:** `forced_command = 'command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict'` — канонический паттерн setup-node.sh:112 (строка ~112: `command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict`). B1 позже апгрейдит канал до dispatch (SSH_ORIGINAL_COMMAND-диспетчер) — B8 не ждёт B1 |
| D3 | Строгость гейта фантомов | **Строгий: 0 упоминаний** 4 имён (deploy-project.sh, state_migration.py, audit_logging.sh, generate-dev-certs.sh) в коде и CI, ВКЛЮЧАЯ docstring/TRAP-историю. Allowlist гейта — пустая константа. Исторические TRAP-аннотации, упоминающие имена, переформулируются/удаляются (потеря истории принята). Границы скана: core/**, tests/** (кроме generated test_inventory.yaml), makefiles/**, .github/**, AGENTS.md (root), .env.example, .pre-commit-config.yaml, Makefile. ВНЕ скоупа (архив): reports/*, tests/test_inventory.yaml (generated), tests/test_inventory_changes.yaml (запись истории инвентаря) |
| D4 | Объём U-66 | **Бриф-буквально:** удалить `resume_phase` + `_grouped_phases` + 3 пинящих теста (test_bootstrap_dry_run.py::test_resume_phase_partial_failure, test_failure_scenarios.py::test_resume_phase7_after_midphase_kill, статический ассерт в test_node_lifecycle_static.py). `execute_grouped_phase` ОСТАЁТСЯ (машинерия sub-step идемпотентности; TRAP[DEBT] hint на разводку в B9). 2 прямых теста execute_grouped_phase — остаются, убираются ассерты на `_grouped_phases` |

---

## 2. Текущее состояние worktree (старт волны)

- HEAD `c3ae21a` (main, B5 закоммичен). Рабочее дерево ГРЯЗНОЕ: B4 (16-DevPlan) — 65 файлов изменено + 7 untracked (contracts.py, 4 gate-теста, 2 unit-теста). D1: B8 работает поверх этого состояния.
- НЕ ЗАКОММИЧЕНЫ изменения B4 в файлах пересечения: docker_orchestrator.py, state_machine.py, phases.py, json_field_extractor.py (перемещён из scripts/ в bootstrap/ волной B4!), url_encoder.py, deploy_orchestrator.py, sync_env_defaults.py, shared/AGENTS.md, core/AGENTS.md, entrypoint-manifest.yaml.
- **Внимание:** json_field_extractor.py физически находится в `core/internal/bootstrap/` (НЕ в scripts/ как в брифе) — бриф содержит устаревший путь.
- platform-secrets/install.sh физически в `core/modules/platform-secrets/install.sh` (бриф: «platform-secrets/install.sh» — путь уточнён).
- Verified факты (поле для Coder):
  - `core/internal/deploy/deploy-project.sh` НЕ существует; единственный live-source удалённого audit_logging.sh — `core/modules/platform-secrets/install.sh:20` (`source "${SCRIPT_DIR}/../../lib/audit_logging.sh" 2>/dev/null || true` — молчаливый no-op, класс бага «audit_logging сломал provision»).
  - `docker_orchestrator.deploy_via_orchestrator` (980-1037) и `overlay_deliverer.deliver_via_orchestrator_scp` (224-277) — 0 callers. `reconciler_projects.deploy_via_orchestrator` (264, вызов 461) — ЖИВАЯ, отдельная функция.
  - `resume_phase` (967-990): единственный вызов в core — из самого себя (990 → execute_grouped_phase). CLI `--resume` (1294, 1411-1412) — только логирует, resume_phase не вызывает. `_grouped_phases` (224) — 0 читателей в core.
  - `_compute_step_hash` (1608-1656) — 0 callers (мёртвый, bonus-чистка).
  - `execute_grouped_phase` (876-956) — вызывается только из resume_phase + 2 прямых теста (test_bootstrap_dry_run 618-683, ~690-708).
  - `yaml_read_domain_config` (yaml_read.sh:121-131) — 0 production-callers; issue-cert.sh:584 уже делегирует NodeYaml CLI --domain-config (комментарий «replaces yaml_read_domain_config»).
  - content-hash.sh (87 LOC) и s3-ssl-cache.sh (26 LOC) — 0 source'еров; оба allowlist'ены/исключены в гейтах (test_gate_dead_code._EXCEPTION_PATHS, test_gate_no_unregistered_entrypoint:73).
  - `steps.py` (615 LOC): только suppress-import в state_machine.py:65-69 + docstring-упоминания; манифест consumers (747,757); 2 gate-теста в test_bootstrap_no_duplicate_steps.py читают файл (TRAP[TEST]: «Remove if: steps.py is fully replaced by phases.py and deleted»).
  - Фантомный реестр: 46 сайтов упоминаний 4 имён (полный список — T5).
  - Существующие гейты: test_gate_dead_code.py (7 passed сейчас), test_gate_no_unregistered_entrypoint.py — проходят; allowlist-записи для удаляемых файлов подлежат чистке.

---

## 3. Задачи

### T1 — U-27: Удаление steps.py + _compute_step_hash [FUNDAMENT]

**1. `core/internal/bootstrap/lifecycle/steps.py` — УДАЛИТЬ (615 LOC).**

**2. `state_machine.py`:**
- Строки 65-69: удалить suppress-import блок + комментарии («Import steps module...», «PYTHONPATH must include...», «Handle standalone execution...»).
- Строка 556: docstring «actual step logic lives in steps.py (optional) or is inlined» → «actual step logic lives in phases.py (14 phase implementations)».
- Строки 1608-1656: удалить мёртвую `_compute_step_hash()` (0 callers; wrapper вокруг _step_hash).

**3. `lifecycle/__init__.py` (строки 2, 8-13, 24-25):** убрать steps.py и state_migration.py из STRUCTURE/@scope/@rationale/Modules-списка (state_migration.py — фантом, чистится здесь же, а не в T5).

**4. `core/entrypoint-manifest.yaml:747,757`:** удалить `- steps.py` из consumers списков telegram_notifier и docker_auth (секция не генерируется — ручная правка; `make check-manifests` подтвердит отсутствие генерации секции).

**5. `tests/unit/test_bootstrap_no_duplicate_steps.py` (gate-файл, 6 тестов):**
- Удалить из `_PATHS` (строки 38, 45): `STEPS_PY` константа + запись «steps.py».
- УДАЛИТЬ 2 теста-консерватора (TRAP[TEST] разрешает): `test_no_step_deploy_context_in_steps` (136-165), `test_no_step_underscore_functions_in_steps` (232-261).
- Остаются 4 теста (SHELL_TO_PYTHON_STEP, .done, step_1_, LOC) — они не читают steps.py, работают после правки _PATHS.
- Docstring (5-7): убрать «across state_machine.py and steps.py» → «across state_machine.py and phases.py».

**6. Docstring-упоминания steps.py (ИСТОРИЯ, не гейт-список — оставить):** phases.py:523, context_deployer.py:579, shared/AGENTS.md:6,9 (docker_auth/telegram_notifier @changes «Migrated from steps.py»).

**Критерий приёмки:** `ls core/internal/bootstrap/lifecycle/steps.py` → нет; `rg "steps.py" core/internal --glob '*.py'` → только исторические комментарии (phases.py:523, context_deployer.py:579); `rg "_compute_step_hash" core` → 0; `pytest tests/unit/test_bootstrap_no_duplicate_steps.py` → 4 passed.

---

### T2 — U-26: Удаление _ORCHESTRATOR_AVAILABLE + мёртвых функций [FUNDAMENT]

**1. `docker_orchestrator.py`:**
- Строки 127, 132: удалить `_ORCHESTRATOR_AVAILABLE = False` и его установку в True.
- Строки 971-1037: удалить region FUNC_deploy_via_orchestrator + функцию целиком.

**2. `overlay_deliverer.py`:**
- Строки 46, 51: удалить `_ORCHESTRATOR_AVAILABLE` + установку.
- Строки 219-277: удалить region FUNC_deliver_via_orchestrator_scp + функцию.

**3. НЕ ТРОГАТЬ:** `reconciler_projects.py` (своя `deploy_via_orchestrator` line 264, вызов line 461 — живая); `context_deployer.py:45-46` (уже очищен волной 091 — комментарий оставлен).

**Критерий приёмки:** `rg "_ORCHESTRATOR_AVAILABLE" core` → 0; `rg "def deploy_via_orchestrator" core/internal/bootstrap/deploy/docker_orchestrator.py` → 0; `rg "deliver_via_orchestrator_scp" core` → 0; `pytest tests/unit/test_docker_orchestrator.py` → PASS.

---

### T3 — U-40: Удаление json_field_extractor + url_encoder [FUNDAMENT]

**1. УДАЛИТЬ:**
- `core/internal/bootstrap/json_field_extractor.py` (161 LOC; физический путь — bootstrap/, НЕ scripts/).
- `core/internal/notify/url_encoder.py` (46 LOC).

**2. `tests/unit/test_importability_no_exit.py:43`:** удалить allowlist-запись `"core.internal.bootstrap.json_field_extractor"`.

**3. `deploy_orchestrator.py` — docstring-правки (убрать ложное «for other shell consumers»):**
- Строка 23: «json_field_extractor.py is NOT called (R5 — exists for other shell consumers)» → удалить строку (утверждение ложно).
- Строка 29: «D3: JSON interop (json_field_extractor) obsolete in Python — native json.loads/json.dumps; module kept for other shell consumers» → «D3: JSON interop via native json.loads/json.dumps (Python)».
- Строка 761: «(legacy json_field_extractor --default warn parity)» → «(default warn severity)».

**Критерий приёмки:** файлы удалены; `rg "json_field_extractor|url_encoder" core tests` → 0 (кроме @changes-истории в shared/AGENTS.md — проверить, что её нет: docker_auth:9 и telegram_notifier:6 упоминают steps.py, не json_field_extractor — ок); `pytest tests/unit/test_importability_no_exit.py tests/unit/test_deploy_orchestrator.py` → PASS.

---

### T4 — U-41: Удаление content-hash.sh + s3-ssl-cache.sh [FUNDAMENT]

**1. УДАЛИТЬ:** `core/internal/bootstrap/content-hash.sh` (87 LOC), `core/internal/bootstrap/s3-ssl-cache.sh` (26 LOC). Python-замены НЕ трогаются: `shared/content_hash.py`, `bootstrap/s3_ssl_cache.py` (живые, покрыты unit-тестами).

**2. Гейт-allowlist (обнуление):**
- `tests/gates/test_gate_dead_code.py`: удалить из `_EXCEPTION_PATHS` запись `"core/internal/bootstrap/s3-ssl-cache.sh"` + её комментарий «DevPlan 024, sourced dynamically...» (строки ~76-77). Строка 94: комментарий «and deploy-project.sh; static call graph builder...» → переформулировать без имени (D3).
- `tests/gates/test_gate_no_unregistered_entrypoint.py:73`: удалить allowlist-запись `"core/internal/bootstrap/s3-ssl-cache.sh"` + комментарий «S3 SSL cache (DevPlan 024)...». Строка 67: комментарий «called from deploy-project.sh _trigger_deploy_hooks» → переформулировать без имени (D3).

**3. `core/internal/scripts-audit.sh:45`:** удалить запись `"core/internal/bootstrap/s3-ssl-cache.sh"    # SSL cache (DevPlan 024)`.

**4. `tests/test_cert_backup_gap.py:48-53`:** удалить ключ `"s3_cache": "core/internal/bootstrap/s3-ssl-cache.sh"` из CERT_SCRIPTS (ключ не читается ни одним тестом — мёртвая запись; тесты test_s3_cache_upload_all_4_cert_files / test_ssl_cache_prefix_distinct_from_backup работают на Python-модуле s3_ssl_cache.py — не трогать).

**5. Документация:**
- `core/internal/bootstrap/AGENTS.md` (сюда же фантомные правки T5.5): строка 12 @scope — убрать «content-hash» из списка; секция DevPlan 052 — удалить строку «s3-ssl-cache.sh (REDUCED) — CLI-фасад ~30 строк» из таблицы компонентов; строка 19 — фантом state_migration.py (см. T5).
- `core/internal/shared/AGENTS.md:30` (content_hash.py): consumers «state_machine, content-hash.sh» → «state_machine».

**Критерий приёмки:** файлы удалены; `pytest tests/gates/test_gate_dead_code.py tests/gates/test_gate_no_unregistered_entrypoint.py tests/test_cert_backup_gap.py` → PASS; `rg "s3-ssl-cache\.sh" core --glob '*.sh' --glob '*.py'` → 0; `rg "content-hash\.sh" core` → 0.

---

### T5 — U-42: phases.py forced-command + 46 фантомных сайтов [CRITICAL]

**1. `core/internal/bootstrap/lifecycle/phases.py:249` (D2):**
```python
forced_command = f'command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict'
```
(замена `f'command="{core_dir}/internal/deploy/deploy-project.sh {node_name}",restrict'`; core_dir/node_name в forced-command больше не нужны). Строка 213: docstring «ci-deploy gets forced-command prefix for deploy-project.sh» → «ci-deploy gets forced-command prefix for orchestrator_cli receive».

**2. `core/modules/platform-secrets/install.sh:20` (LIVE-баг):**
`source "${SCRIPT_DIR}/../../lib/audit_logging.sh" 2>/dev/null || true` → `source "${SCRIPT_DIR}/../../lib/audit.sh" 2>/dev/null || true` (audit.sh — канонический shell-аудит, содержит compatibility-константы, audit.sh:32). Проверить, какие функции install.sh использует из source-файла (audit_log/audit_step) — при несовпадении API адаптировать вызовы к audit.sh (consumer-scan обязателен).

**3. `sync_env_defaults.py:190`:** «generate-dev-certs.sh (make dev-certs)» → «dev_cert_generator.py (make dev-certs)». Затем: `make sync-env-defaults` (регенерация .env.example — строка 47 обновится автоматически) + `make check-env-defaults` PASS.

**4. `AGENTS.md:107` (root, глоссарий):** «make dev-certs → generate-dev-certs.sh» → «make dev-certs → dev_cert_generator.py».

**5. Полный реестр фантомных сайтов (D3 — все очистить; переформулировать без имени или удалить строку):**

| Файл | Строки | Действие |
|------|--------|----------|
| core/entrypoint-manifest.yaml | 640, 730 | описание «replaces deleted audit_logging.sh (aa6bd61...)» → «replaces legacy shell audit»; «Unified deploy CLI — replaces deploy-project.sh» → «Unified deploy CLI» |
| core/entrypoints/build.sh | 11 | @changes «replaced exec, source audit_logging.sh» → убрать имя |
| core/entrypoints/deploy.sh | 24, 34 | docstring: убрать «deploy-project.sh» |
| core/internal/bootstrap/AGENTS.md | 19 | «(state_migration.py — удалён в DevPlan 091 Wave B...)» → переформулировать без имени |
| core/internal/bootstrap/lifecycle/__init__.py | 2, 9, 25 | (см. T1.3 — state_migration.py ×3) |
| core/internal/bootstrap/deploy/context_deployer.py | 14 | «(same as deploy-project.sh)» → убрать |
| core/internal/catalog/generate_catalog.py | 7, 21 | «Вызывается deploy-project.sh _reconfigure_monitoring()...» → «Вызывается после успешного деплоя...» |
| core/internal/deploy/deploy_engine.py | 11, 13, 39, 67, 82 | docstring/TRAP[DECISION] «deploy-project.sh Strangler-Fig migrated...» → «legacy deploy shell migrated...» (имя убрать) |
| core/internal/deploy/orchestrator.py | 659, 673 | «Replaces deploy-project.sh» → «Replaces the legacy shell deploy pipeline» |
| core/internal/deploy/orchestrator_cli.py | 15, 25 | то же |
| core/internal/deploy/payload_deliverer.py | 8, 9, 103 | «in deploy-project.sh shell facade» → «in legacy shell facade» |
| core/internal/notify/notify-hook.sh | 6 | «Called by deploy-project.sh with optional --severity» → «Called with optional --severity flag» |
| core/internal/provision-environment.sh | 24-25 | TRAP[BUG] «Stale source of deleted audit_logging.sh...» → переформулировать без имени (сохранить суть TRAP: stale source сломал provision) |
| core/internal/scripts/sync_env_defaults.py | 190 | (см. T5.3) |
| core/internal/shared/AGENTS.md | 29 | «заменяет ... shell audit_logging.sh» → «заменяет shell-аудит» |
| core/internal/shared/deploy_paths.py | 30 | комментарий «→ deploy-project.sh» → «→ orchestrator_cli receive» |
| core/internal/shared/platform_deliver.py | 6, 17 | docstring «in deploy-project.sh and reconcile-projects.sh» → «in legacy shell deliverers» |
| core/internal/shared/project_registry.py | 57 | «дублировался в deploy-project.sh:207» → «дублировался в legacy shell» |
| core/internal/shared/ssh_command_parser.py | 7, 20 | «deploy.sh and deploy-project.sh» → «deploy.sh and legacy deploy shell» |
| core/lib/audit.sh | 6, 21, 32 | docstring «Replaces deleted core/lib/audit_logging.sh...», «audit_logging.sh deletion broke...», «Compatibility constants (same names as deleted audit_logging.sh)» → переформулировать без имени (константы остаются) |
| core/lib/docker.sh | 22, 24, 40 | @links «USED_BY: core/internal/deploy/deploy-project.sh» → «USED_BY: orchestrator_cli.py / deploy_engine.py» (реальный потребитель); «REPLACES: inline docker_login() in deploy-project.sh:82» → «REPLACES: inline docker_login() in legacy deploy shell» |
| core/lib/healthcheck.sh | 26 | «USED_BY: deploy-project.sh, all module/*/healthcheck.sh» → «USED_BY: deploy_engine.py, all module/*/healthcheck.sh» |
| core/lib/yaml_read.sh | 27 | «USED_BY: core/internal/deploy/deploy-project.sh» → «USED_BY: node-lifecycle.sh» (или реальный потребитель) |
| core/modules/monitoring/hooks/on-project-deploy.sh | 7 | «Invoked by deploy-project.sh after successful project deploy» → «Invoked after successful project deploy» |
| core/modules/nginx/module.yaml | 31 | «Module hooks invoked by deploy-project.sh lifecycle phases» → «...invoked by project deploy lifecycle» |
| core/modules/nginx/nginx_reload_hook.sh | 6, 10 | «from deploy-project.sh _trigger_deploy_hooks()» → «from project deploy hooks» |
| core/modules/nginx/dev_cert_generator.py | 8 | «migration of core/modules/nginx/generate-dev-certs.sh» → «migration of legacy generate-dev-certs shell» (имя упоминается — убрать) |
| core/modules/nginx/dev-config/ssl-dev.conf | 11 | комментарий «Fix: generate-dev-certs.sh — идемпотентная генерация...» → «Fix: dev_cert_generator.py — ...» |
| core/modules/postgres/hooks/on-project-deploy.sh | 6, 14, 15 | «Invoked by deploy-project.sh...», «Extracted from deploy-project.sh:auto_create_db()...», «Extracted from deploy-project.sh:803-856» → переформулировать без имени |
| makefiles/deploy.mk | 66 | «(replaces deleted core/entrypoints/deploy-project.sh — STALE ref bugfix)» → «(delegates to DeployOrchestrator via orchestrator_cli)» |
| .github/workflows/platform-deploy.yml | 132, 184 | комментарии «authorized_keys runs deploy-project.sh», «passes project + sha + environment to deploy-project.sh» → «runs orchestrator_cli receive» (workflow удалится в B1 — правка комментариев всё равно обязательна по D3) |
| tests/test_audit_step.py | 12, 15 | @changes/@rationale: убрать «audit_logging.sh» |
| tests/test_contract_deploy_ssh.py | 39, 46, 47, 91, 114, 535 | docstring/комментарии: убрать имя (тест проходит — только тексты; DEPLOY_SCRIPT_PATH: строка 39 — константа не используется в активном коде? ПРОВЕРИТЬ: если используется в _run_bash — переписать тест на реальный объект, НЕ на удалённый файл) |
| tests/test_contract_entrypoints.py | 46 | комментарий «core/entrypoints/deploy.sh → git push → CI → core/internal/deploy/deploy-project.sh» → «→ core/internal/deploy/orchestrator_cli.py receive» |
| tests/test_deploy_direct.py | 4-7 | docstring «Unit tests for deploy-project.sh entrypoint validation...» → «Unit tests for bash snippet parity (validate_project/extract_org semantics, reimplemented inline)» — тест тестирует inline-сниппеты, НЕ файл |
| tests/test_project_lifecycle.py | 16, 293, 298, 341, 345 | комментарии про удалённый deploy-project.sh → переформулировать/удалить |
| tests/test_stub_detection.py | 6 | «deploy-project.sh --status stub-aware output» → «stub-aware status output» |
| tests/test_nginx_dev_certs.py | 5 | «migrated from generate-dev-certs.sh» → «migrated from legacy dev-certs shell» |
| tests/test_ssh_command_parser.py | 13 | «when deploy.sh/deploy-project.sh are migrated...» → «when deploy.sh is migrated...» |
| tests/test_context_deployer_audit_integration.py | 456-463 | комментарии «audit_logging.sh existed...» → переформулировать без имени |
| tests/test_state_machine.py | 967 | комментарий «removed together with state_migration.py» → «removed with legacy migration» |
| tests/integration/test_bootstrap_dry_run.py | 10, 49, 54, 912 | docstring + TRAP[DECISION] «Inlined MIGRATION_MAP from deleted state_migration.py» + комментарии → переформулировать без имени (D3) |
| tests/unit/test_context_promoter.py | 382-423 | **переименовать тест** `test_audit_logging_imp9` → `test_audit_step_imp9` (проверяет новый audit-контракт; регион-комментарии обновить) |
| tests/gates/test_gate_thin_wrapper.py | 54 | удалить stale allowlist-запись `"deploy-project.sh"` (+ её комментарий) |
| tests/gates/test_gate_dead_code.py | 94 | (см. T4.2) |
| tests/gates/test_gate_no_unregistered_entrypoint.py | 67 | (см. T4.2) |

**6. ПОСЛЕ правок:** `make test-inventory-sync` (переименование test_audit_logging_imp9 → обновит tests/test_inventory.yaml:1693; удаления тестов — T6/T7) + `git add tests/test_inventory.yaml`.

**Критерий приёмки:** `rg --hidden --no-ignore "deploy-project\.sh|state_migration\.py|audit_logging\.sh|generate-dev-certs\.sh" core tests makefiles .github AGENTS.md .env.example .pre-commit-config.yaml Makefile` → 0 (архив reports/ и test_inventory_changes.yaml — вне скана, D3); `make check-env-defaults` PASS; `pytest tests/unit/test_context_promoter.py tests/test_audit_step.py` → PASS.

---

### T6 — U-64: Удаление yaml_read_domain_config [FUNDAMENT]

**1. `core/lib/yaml_read.sh:102-132`:** удалить region yaml_read_domain_config + функцию (файл остаётся — другие функции живы; line 27 USED_BY — см. T5).

**2. `tests/test_deploy_modules.py:725-831`:** удалить region FUNC_test_yaml_read_domain_config + тест целиком (включая TRAP[TEST] «Remove if: YAML domain extraction approach changes» — подход изменён: NodeYaml CLI --domain-config; тест-консерватор).

**3. `core/internal/bootstrap/issue-cert.sh:584`:** комментарий «S7: Parse NODE_YAML via NodeYaml CLI --domain-config (replaces yaml_read_domain_config)» → «(replaces legacy yaml_read_domain_config shell helper)» — упоминание удаляемой функции убрать.

**Критерий приёмки:** `rg "yaml_read_domain_config" core tests` → 0; `pytest tests/test_deploy_modules.py` → PASS; `source core/lib/yaml_read.sh` (bash -n) → OK.

---

### T7 — U-66: Удаление resume_phase + _grouped_phases [FUNDAMENT]

**1. `state_machine.py`:**
- Строки 213-223: удалить TRAP[DEBT] блок (долг резолвится удалением — блок устаревает).
- Строки 224-~231: удалить `_grouped_phases: frozenset[str] = frozenset(...)`.
- Строки 958-990: удалить region FUNC_resume_phase + метод.
- Строка 3 (STRUCTURE): «○ _execute_phase() / _execute_grouped_phase() → ⊕ _resume_phase() → ⚡ save()» → «○ _execute_phase() / _execute_grouped_phase() → ⚡ save()».
- Строки 12, 38 (docstring): убрать «_resume_phase()» упоминания.
- НЕ трогать: `execute_grouped_phase` (876-956), `_step_hash` (743), CLI `--resume` (1268, 1294, 1411-1412 — флаг жив, логирует продолжение), `_run_init_mode/_run_update_mode` (execute_phase-циклы с skip done-фаз — живой путь).

**2. `tests/integration/test_bootstrap_dry_run.py`:**
- Строка 45: import — убрать `_grouped_phases`.
- Строки 710-790: УДАЛИТЬ `test_resume_phase_partial_failure` (пинит resume_phase + _grouped_phases) + его region-комментарии.
- Строки 618-683 `test_skip_already_done_phases`: убрать ассерты `_grouped_phases` (627-628); вызов execute_grouped_phase (670) и skip-семантику ОСТАВИТЬ.
- Строка 3 (STRUCTURE docstring): убрать «test_resume_phase_partial_failure» и «_grouped_phases» упоминания.

**3. `tests/e2e/test_failure_scenarios.py:61-~95`:** УДАЛИТЬ `test_resume_phase7_after_midphase_kill` + TRAP/комментарии (17-32, 68, 80 — упоминания resume_phase). Тест e2e (requires_node) — локально не запускается, удаление безопасно.

**4. `tests/e2e/README.md:99`:** удалить/переформулировать пункт «**sub_step-resume** — resume_phase() мёртвый код (TRAP[DEBT] state_machine.py:213)».

**5. `tests/test_node_lifecycle_static.py` (~строки 550-556):** в `test_checkpoint_step_uses_content_hash` Check 2 убрать `assert "resume_phase" in sm_content`; ассерты `_step_hash` и `execute_grouped_phase` ОСТАВИТЬ.

**6. `core/internal/bootstrap/AGENTS.md`:** STRUCTURE (строка 4), @scope (строка 8 — «_resume_phase()»), инвариант 2 (строка ~11), таблица идемпотентности (строка 143: «Хеш проверяется при _resume_phase() — unchanged+done = SKIP» → «Хеш проверяется при execute_grouped_phase() — unchanged+done = SKIP»), секция «Partial failure recovery (_resume_phase())» → переписать: «Повторный запуск: _run_init_mode/_run_update_mode пропускают done-фазы (execute_phase); sub-step SKIP — через execute_grouped_phase».

**Критерий приёмки:** `rg "resume_phase|_grouped_phases" core/internal/bootstrap/lifecycle core/internal/bootstrap/AGENTS.md tests` → 0 (кроме CLI-флага `--resume`/`RESUME_MODE` в node-lifecycle.sh — они остаются); `pytest tests/integration/test_bootstrap_dry_run.py tests/test_node_lifecycle_static.py tests/unit/test_state_machine.py` → PASS.

---

### T8 — Новый гейт: test_gate_phantom_refs.py (trinity, D3-строгий) [FUNDAMENT]

**1. Новый `tests/gates/test_gate_phantom_refs.py`** (@pytest.mark.gate, MODULE_CONTRACT + GREP_SUMMARY/STRUCTURE по стандарту gate):
- Константа `_PHANTOM_NAMES = ("deploy-project.sh", "state_migration.py", "audit_logging.sh", "generate-dev-certs.sh")`.
- Скан: файлы по корням `core/`, `tests/`, `makefiles/`, `.github/`, + `AGENTS.md`, `.env.example`, `.pre-commit-config.yaml`, `Makefile`. Исключения: `tests/test_inventory.yaml` (generated), `reports/` (архив), `tests/test_inventory_changes.yaml` (архив истории инвентаря), `__pycache__`/`.git`/`node_modules`/`.venv`.
- Детект: любое вхождение имени как подстроки (regex `re.escape(name)`), ВКЛЮЧАЯ комментарии/docstring (D3-строгий).
- `_ALLOWLIST: frozenset[str] = frozenset()` — пустая константа + комментарий «D3 2026-08-01: строгий режим, история удаляется вместе с именами».
- Fail-сообщение: список файлов:строк с именем.
- TRAP[DECISION]-комментарий: фиксирует D3 (строгий режим, rev-дата 2026-10-21 — пересмотр при необходимости).

**2. Регистрация:** auto-discovered (G3) — `make fix-gate` пересоберёт секцию gates в entrypoint-manifest.yaml; `make check-manifests` PASS. Make-обёртка НЕ нужна (прецедент B6 D6).

**3. Само-проверка гейта (falsifiability):** тест содержит negative-проверку — запускает сканер на tmp_path с фиктивным файлом, содержащим «deploy-project.sh» → ожидает детект (R5 anti-survivorship: negative test для гейта обязателен).

**Критерий приёмки:** `pytest tests/gates/test_gate_phantom_refs.py` → PASS; строка гейта появилась в entrypoint-manifest.yaml gates (auto-discovered); `make check-manifests` PASS.

---

### T9 — Самоверификация волны (порядок)

1. `make fix-gate && git add -u` (exec-bit, ruff, manifest regen).
2. `make sync-env-defaults` (T5.3) + `make check-env-defaults` PASS + `git add .env.example`.
3. `make test-inventory-sync` (удаления/ренеймы тестов T5/T6/T7) + `git add tests/test_inventory.yaml`.
4. Таргетные прогоны:
   - `pytest tests/unit/test_bootstrap_no_duplicate_steps.py tests/unit/test_docker_orchestrator.py tests/unit/test_overlay_deliverer.py tests/unit/test_importability_no_exit.py` (T1-T3);
   - `pytest tests/gates/test_gate_dead_code.py tests/gates/test_gate_no_unregistered_entrypoint.py tests/gates/test_gate_thin_wrapper.py tests/gates/test_gate_phantom_refs.py tests/test_cert_backup_gap.py` (T4, T8);
   - `pytest tests/integration/test_bootstrap_dry_run.py tests/test_node_lifecycle_static.py tests/unit/test_state_machine.py tests/test_deploy_modules.py` (T6-T7);
   - `pytest tests/test_audit_step.py tests/unit/test_context_promoter.py tests/test_contract_deploy_ssh.py tests/test_deploy_direct.py tests/unit/test_s3_ssl_cache.py tests/unit/test_cert_orchestrator.py` (T5-регресс).
5. rg-критерии: 4 фантома (D3-скан) → 0; `_ORCHESTRATOR_AVAILABLE` → 0; `yaml_read_domain_config` → 0; `resume_phase|_grouped_phases` → 0 (кроме CLI `--resume`).
6. `make gate MODE=fast` → зелёный (включая check-manifests + test_gate_test_inventory после sync).
7. Обновить shared/AGENTS.md инвентарь: НЕТ новых shared-модулей; проверить, что consumers-строки (content_hash.py:30) актуальны.
8. TRAP[DECISION] в root AGENTS.md: фиксация D2 (phases.py → orchestrator_cli receive, B1 апгрейдит) и D3 (строгий гейт фантомов, rev 2026-10-21) — краткая запись по формату TRAP.
9. Плановые артефакты: 17-DevPlan.md (этот файл); после реализации — VerificationReport (следующий NN).

---

## 4. Риски и решения

| Риск | Митигация |
|------|-----------|
| B4-изменения в рабочем дереве перемешиваются с B8-коммитом (D1) | Принято пользователем. Верификация B8 — против дерева as-is; при ревью коммита diff будет содержать B4-правки — зафиксировано в DevPlan для QA |
| install.sh:20 молчаливый source удалённого audit_logging.sh (класс бага «сломал provision») | T5.2: замена на audit.sh + consumer-scan использования функций; `|| true` больше не маскирует отсутствие файла |
| test_contract_deploy_ssh.py: DEPLOY_SCRIPT_PATH на удалённый файл | Тест сегодня проходит (13 passed) — проверить, используется ли константа в активном коде _run_bash; при использовании — переписать на реальный объект (orchestrator_cli receive), при мёртвой — только docstring-чистка |
| Удаление TRAP[DECISION]/TRAP[BUG]-истории (D3-строгий) | Принято пользователем; новая история фиксируется TRAP в T9.8; тестовая история — в test_inventory_changes.yaml (архив, вне скана) |
| e2e test_failure_scenarios (requires_node) удаляется без локального прогона | Удаление безопасно: тест пинит мёртвый путь (resume_phase), что задокументировано в его собственных комментариях (17-32); e2e вне make gate |
| Manifest consumers (747,757) — ручная правка | Секция не генерируется (проверено: generate_entrypoint_manifest.py не содержит shared-modules секции); make check-manifests подтвердит |
| steps.py: 2 gate-теста удаляются | TRAP[TEST] в test_bootstrap_no_duplicate_steps.py явно разрешает («Remove if: steps.py is fully replaced by phases.py and deleted») |
| .env.example регенерация затронет другие строки | sync_env_defaults генерирует из SoT (platform-env.yaml + secret-definitions.yaml) — diff ожидается ровно 1 строкой (47); при большем diff — остановиться и разобраться |
| Удаление execute_grouped_phase НЕ происходит (D4) — остаётся орфан в проде, покрыт 2 прямыми тестами | Задокументировано в TRAP[DEBT]-замене: новый комментарий в state_machine.py у места execute_grouped_phase («D4 2026-08-01: вызывается только из тестов; разводка в run-циклы — B9»), чтобы орфан не потерялся |
| Гейт фантомов ложные срабатывания на reports/ | reports/ вне скоупа скана (архив, D3); границы зафиксированы в гейте константой _SCAN_ROOTS |

---

## 5. Критерии завершения волны (AC брифа 06-Brief)

1. ✅ steps.py удалён (манифест/AGENTS.md обновлены; test_bootstrap_no_duplicate_steps: 2 теста-консерватора удалены) (T1).
2. ✅ _ORCHESTRATOR_AVAILABLE + deploy_via_orchestrator (docker_orchestrator) + deliver_via_orchestrator_scp удалены; reconciler_projects не тронут (T2).
3. ✅ json_field_extractor, url_encoder удалены; ложное «for other shell consumers» устранено; test_importability_no_exit allowlist очищен (T3).
4. ✅ content-hash.sh, s3-ssl-cache.sh удалены вместе с CERT_SCRIPTS-записью и gate-allowlist'ами (T4).
5. ✅ phases.py:249 переведён на реальный forced-command (orchestrator_cli receive); install.sh:20, sync_env_defaults:190, AGENTS.md:107, .env.example (регенерация) — очищены; 46 фантомных сайтов — 0 упоминаний (T5).
6. ✅ yaml_read_domain_config удалена + test_deploy_modules.py:725-831 удалён (T6).
7. ✅ resume_phase/_grouped_phases удалены; 3 пинящих теста обработаны (1 удалён, 2 обновлены); execute_grouped_phase сохранён (T7).
8. ✅ Гейт dead-code зелёный с нулевым allowlist; новый test_gate_phantom_refs.py (strict, пустой allowlist) зелёный; test_gate_no_unregistered_entrypoint чистый (T4, T8).
9. ✅ make gate MODE=fast зелёный (T9).

Гейт самоверификации волны: rg-критерии T9.5 + `make gate MODE=fast` + 4-фантомный скан → 0.
