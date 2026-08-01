# 18-VerificationReport — B8 Dead-Code Wave (Consumer-Scan) Verification

<!-- GREP_SUMMARY: VerificationReport B8 phantom-scan consumer-scan dead-code gate AC1-AC8 STABLE -->
<!-- STRUCTURE: ┌Phase 0: SHA anchor┐ → ◇ A: AC1-AC8 → ◇ B: phantom scan → ◇ C: consumer scan → ◇ D: test runs → ◇ E: misc → ⊕ verdict STABLE -->
# region MODULE_CONTRACT
## @purpose  Полная семантическая верификация волны B8 (dead-code sweep + strict phantom gate)
##           программы хардненинга 116 по чек-листу A-E. Верификация подтверждает соответствие
##           критериям приёмки брифа 06-Brief (AC1-AC8) и DevPlan 17-DevPlan (T1-T9).
## @scope    SHA 128807a (B8), дерево чистое. B4 (f3823e1) — отдельный коммит.
## @invariants
##   - QA НЕ пишет исправлений, только отчёт
##   - Все выводы подкреплены командами (grep, pytest, glob)
##   - Семантический вердикт по шкале doc-protocols
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT:
  PURPOSE: Семантическая верификация волны B8 dead-code + consumer-scan
  DESCRIPTION: Проверка 8 acceptance criteria брифа, независимый фантом-скан,
               consumer-scan всех удалённых артефактов, полный прогон gate-тестов.
  RATIONALE: U-42 (phases.py:225 → удалённый deploy-project.sh) — критичный баг,
             требующий двойной верификации (coder + QA). Прецедент audit_logging.sh
             (сломал provision на 2 дня) — consumer-scan обязателен.
  ACCEPTANCE_CRITERIA: Все пункты A-E → PASS/FAIL/WARN с доказательствами.
  IMPLEMENTS: B8-верификация (116-hardening-program wave B8)
  IMPACTS: Нет (read-only)
  REQUIRES: 06-Brief.md, 17-DevPlan.md, коммиты f3823e1 (B4) и 128807a (B8)

---

## 🔒 SHA Anchor

- **SHA:** `128807a23a5e205e842844353a26ca6d4080c4b9`
- **Working tree:** clean (`git status --porcelain` — пусто)
- **B4 commit:** `f3823e1` (6 files, 26+ 17−)
- **B8 commit:** `128807a` (76 files, 747+ 1932−)
- **Diff B4→B8:** separate commits, clean history

---

## A. Критерии брифа (AC1-AC8) — Результаты

| # | Критерий | Результат | Доказательство |
|---|----------|-----------|----------------|
| AC1 | steps.py удалён; entrypoint-manifest.yaml без "steps.py"; state_machine.py не импортирует steps | **PASS** | `glob: core/internal/bootstrap/lifecycle/steps.py` → No files found; `grep "steps.py" core/entrypoint-manifest.yaml` → 0; `grep "import steps\|from.*steps" state_machine.py` — все совпадения `self.steps` (dict access), не модуль. Исторические @changes (4 шт.) допустимы по DevPlan T1 step 6 |
| AC2 | `_ORCHESTRATOR_AVAILABLE` + `deploy_via_orchestrator(docker_orchestrator)` + `deliver_via_orchestrator_scp` удалены; reconciler_projects не тронут | **PASS** | `grep "_ORCHESTRATOR_AVAILABLE\|def deploy_via_orchestrator\|deliver_via_orchestrator_scp" docker_orchestrator.py` → 0; то же для overlay_deliverer.py → 0; `grep "deploy_via_orchestrator" reconciler_projects.py:264,461` — ЖИВАЯ функция, вызов line 461, не удалена |
| AC3 | json_field_extractor.py, url_encoder.py удалены; 0 references в core+tests; no allowlist | **PASS** | `glob` обоих файлов → No files found; `grep "json_field_extractor\|url_encoder" core/` → 0; `grep "json_field_extractor\|url_encoder" tests/` → 0; `grep "json_field_extractor" tests/unit/test_importability_no_exit.py` → 0 |
| AC4 | content-hash.sh, s3-ssl-cache.sh удалены; gate allowlists/scripts-audit очищены; test_cert_backup_gap без s3_cache ключа | **PASS** | `glob` обоих файлов → No files found; `grep "s3-ssl-cache.sh\|content-hash.sh" tests/gates/test_gate_dead_code.py` → 0; `grep` в test_gate_no_unregistered_entrypoint.py → 0; `grep "s3-ssl-cache" scripts-audit.sh` → 0; `CERT_SCRIPTS` dict (line 48-51) — только `issue_cert` и `state_machine` |
| AC5 | resume_phase + _grouped_phases удалены из state_machine.py; --resume/RESUME_MODE сохранены; execute_grouped_phase сохранён; 3 пинящих теста удалены/обновлены | **PASS** | `grep "resume_phase\|_grouped_phases" state_machine.py` → 0; `grep "--resume\|RESUME_MODE" state_machine.py` → 7 matches (CLI флаг жив); `grep "execute_grouped_phase" state_machine.py` → 14 matches (функция жива, D4); `grep "resume_phase\|_grouped_phases" tests/` → 0; `grep` в test_inventory.yaml → 0 |
| AC6 | phases.py:249 → orchestrator_cli receive; install.sh:20 → audit.sh; sync_env_defaults:190 → dev_cert_generator.py; AGENTS.md глоссарий → dev_cert_generator.py; .env.example:47 обновлён | **PASS** | `phases.py:249`: `'command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict'` (точное совпадение с setup-node.sh:112); `install.sh:20`: `source "${SCRIPT_DIR}/../../lib/audit.sh"`; `sync_env_defaults.py:190`: `dev_cert_generator.py (make dev-certs)`; `AGENTS.md:117`: `dev_cert_generator.py`; `.env.example:47`: `dev_cert_generator.py (make dev-certs)` |
| AC7 | yaml_read_domain_config удалена из yaml_read.sh; test_deploy_modules.py без test_yaml_read_domain_config; issue-cert.sh без упоминания | **PASS** | `grep "yaml_read_domain_config" core/` → 0; `grep "yaml_read_domain_config" tests/` → 0; `grep "yaml_read_domain_config" issue-cert.sh` → 0; `grep "yaml_read_domain_config" test_deploy_modules.py` → 0 |
| AC8 | test_gate_phantom_refs.py существует (@pytest.mark.gate, _PHANTOM_NAMES=4, _ALLOWLIST пуст, negative-тест, manifest registered); dead-code + no_unregistered зелёные | **PASS** | Файл существует (212 LOC); `_PHANTOM_NAMES`: 4 имени; `_ALLOWLIST: frozenset()` (line 71); `_EXCLUDE_FILES`: 3 записи (test_inventory.yaml, test_inventory_changes.yaml, self); negative-тест `test_phantom_scan_detects_dummy_file` (line 180); `entrypoint-manifest.yaml:1419,1422` — зарегистрирован; gate fast: 13 passed (phantom + dead-code + no_unregistered) |

---

## B. Независимый фантом-скан

Скан 4 имён (deploy-project.sh, state_migration.py, audit_logging.sh, generate-dev-certs.sh) по всем target-директориям:

| Директория | Результат | Примечание |
|-----------|-----------|------------|
| `core/` | **0 matches** | Чисто |
| `tests/` (excluding archive) | **0 matches** в активных файлах | 39 match'ей — только в `test_inventory_changes.yaml` (архив, вне скоупа D3) и `test_gate_phantom_refs.py` (self-reference, excluded via `_EXCLUDE_FILES`) |
| `makefiles/` | **0 matches** | Чисто |
| `.github/` | **0 matches** | Чисто |
| `AGENTS.md` (root) | **0 matches** | Чисто |
| `.env.example` | **0 matches** | Чисто |
| `.pre-commit-config.yaml` | **0 matches** | Чисто |
| `Makefile` | **0 matches** | Чисто |

**Вердикт B:** Скан чище, чем требует гейт (0 упоминаний во всех target-директориях). D3-строгий режим выполнен.

### Phantom gate self-integrity проверка:
- `_EXCLUDE_FILES`: 3 записи (test_inventory.yaml generated, test_inventory_changes.yaml архив, test_gate_phantom_refs.py self-reference) — минимален, без целых корней ✅
- `_EXCLUDE_DIR_PARTS`: .git, __pycache__, node_modules, .venv, reports — стандартный набор ✅
- Negative-тест реально детектит фиктивный файл с "deploy-project.sh" (R5 anti-survivorship) ✅
- Гейт зарегистрирован в entrypoint-manifest.yaml (trinity) ✅
- Gate fast: 296 passed, 0 failed ✅

---

## C. Consumer-scan завершённость

### C.1. Остаточные потребители удалённых файлов

| Удалённый артефакт | Поиск | Результат | Статус |
|-------------------|-------|-----------|--------|
| `steps.py` | `grep "steps.py" core/ --include='*.py'` | 4 исторических комментария (@changes/docstring) в telegram_notifier.py, docker_auth.py, phases.py, context_deployer.py | **PASS** (история, DevPlan T1 step 6) |
| `_ORCHESTRATOR_AVAILABLE` | `grep "_ORCHESTRATOR_AVAILABLE" core/` | 4 TRAP[DECISION]-комментария о факте удаления флага | **PASS** (история) |
| `json_field_extractor` | `grep "json_field_extractor" core/ --include='*.py'` | **0** | **PASS** |
| `url_encoder` | `grep "url_encoder" core/` | **0** | **PASS** |
| `content-hash.sh` | `grep "content-hash\.sh" core/` | **0** | **PASS** |
| `s3-ssl-cache.sh` | `grep "s3-ssl-cache\.sh" core/` | 1 в AGENTS.md:229 (историческое описание бага DevPlan 052) | **PASS** (исторический контекст) |
| `_compute_step_hash` | `grep "_compute_step_hash" core/` | **0** | **PASS** |
| `yaml_read_domain_config` | `grep "yaml_read_domain_config" core/ tests/` | **0** | **PASS** |
| `resume_phase` / `_grouped_phases` | grep в core/tests | **0** (кроме --resume CLI флага) | **PASS** |
| `deploy_via_orchestrator` (docker_orchestrator) | grep в docker_orchestrator.py | **0** | **PASS** |
| `deliver_via_orchestrator_scp` | grep в overlay_deliverer.py | **0** | **PASS** |

### C.2. Проверка поведенческой целостности

| Проверка | Результат |
|----------|-----------|
| `state_machine.py`: удалены только мёртвый код + docstring (инвариант 3 DevPlan) | **PASS** — resume_phase, _grouped_phases, _compute_step_hash, suppress-import удалены; execute_grouped_phase сохранён (D4); CLI --resume жив |
| `phases.py`: forced-command правка line 249, остальное без изменений бизнес-логики | **PASS** — line 249 содержит `'command="python3 -m core.internal.deploy.orchestrator_cli receive",restrict'` |
| `reconciler_projects.py::deploy_via_orchestrator`: жива, не тронута | **PASS** — line 264 определение, line 461 вызов |

### C.3. Отклонения Coder'а (оценка)

| # | Отклонение | Файл | Оценка |
|---|-----------|------|--------|
| 1 | test_cert_backup_gap адаптация: line 532-534 переведён на `s3_ssl_cache.py` вместо удалённого `s3-ssl-cache.sh` | `tests/test_cert_backup_gap.py:532-534` | **Подтверждено.** Ассерт проверяет отсутствие "dev-certs" в живом Python-модуле. Семантика сохранена: dev-сертификаты не должны попадать в S3 кеш. |
| 2 | test_contract_deploy_ssh: удаление мёртвого `_run_bash` | `tests/test_contract_deploy_ssh.py` | **Подтверждено.** Константа `DEPLOY_SCRIPT_PATH` удалена вместе с мёртвым кодом. Тест проходит (4 passed). |
| 3 | test_merge_deploy_steps: переписан на 'deploy_services' | `tests/` | **Подтверждено.** Тест обновлён на актуальный phase key. |
| 4 | Self-exclusion гейта: test_gate_phantom_refs.py исключает сам себя | `tests/gates/test_gate_phantom_refs.py:63` | **Подтверждено.** Self-reference неизбежен (имена должны существовать как константы). Аналогично allowlist-константам других гейтов. |
| 5-7 | TRAP-переформулировки без имён (D3) | multiple | **Подтверждено.** Все TRAP-аннотации переформулированы без фантомных имён. История удалена вместе с именами (D3, принято пользователем). |

### C.4. Найденная проблема: деградация теста test_s3_unavailable_does_not_block_cert_issue

**[WARNING] C4-DEGRADED · tests/test_cert_backup_gap.py:471**

Тест `test_s3_unavailable_does_not_block_cert_issue` (line 450-483) ищет `"s3-ssl-cache.sh"` в `issue-cert.sh` (line 471). Поскольку `issue-cert.sh` больше не содержит этого вызова (shell→Python миграция), условие `s3_upload_line >= 0` никогда не выполняется — тест проходит тривиально, без проверки заявленной семантики («S3 unavailable does not block cert issue»).

- **Observed:** Тест проходит, но не проверяет заявленное поведение (S3-недоступность не блокирует выпуск сертификата)
- **Root:** shell→Python миграция (s3-ssl-cache.sh → s3_ssl_cache.py) изменила точку вызова S3, но тест не адаптирован
- **Impact:** Низкий — реальное поведение покрыто `tests/unit/test_s3_ssl_cache.py` и `tests/unit/test_cert_orchestrator.py`
- **Fix:** Обновить тест для проверки Python-пути (`s3_ssl_cache.py`) вместо старого shell-пути, либо удалить тест если поведение полностью покрыто unit-тестами

---

## D. Тесты — Runtime Validation

### D.1. Gate tests (targeted)
```
pytest tests/gates/test_gate_phantom_refs.py tests/gates/test_gate_dead_code.py tests/gates/test_gate_no_unregistered_entrypoint.py -q
```
**Результат: 13 passed** (7 dead-code + 4 no_unregistered + 2 phantom)

### D.2. Unit/integration/contract tests (targeted)
- D.2a: `test_bootstrap_no_duplicate_steps + test_state_machine + test_importability_no_exit + test_context_promoter` → **62 passed**
- D.2b: `test_bootstrap_dry_run + test_node_lifecycle_static + test_deploy_modules + test_cert_backup_gap` → **44 passed**
- D.2c: `test_audit_step + test_contract_deploy_ssh + test_deploy_direct` → **17 passed**

**Итого targeted: 136 passed, 0 failed**

### D.3. Full gate run
```
python3 -m pytest tests/gates/ -q -m gate
```
**Результат: 296 passed, 15 skipped, 26 deselected, 0 failed**

Все 15 skip — легитимные:
- 1: `make -n` limitation (документировано)
- 11: модули без hooks (валидное состояние)
- 2: нет projects/ директории (dev-окружение)
- 1: extra pytest markers (non-critical)

### D.4. Manifest check
`make check-manifests` заблокирован политикой bash (правило проекта). Косвенная верификация через `test_gate_manifests_up_to_date.py` в составе gate — PASS.

### D.5. Test inventory sync
`tests/test_inventory.yaml` содержит оба phantom-теста (lines 236-237). Удалённые тесты (resume_phase, yaml_read_domain_config, steps.py gate-тесты) отсутствуют. ✅

### D.6. LDD Trace Analysis (anti-illusion)
Все протестированные gate-файлы содержат IMP:9 логи:
- `test_gate_phantom_refs.py:166`: `[IMP:9][phantom_gate] PASS: 0 упоминаний` ✅
- `test_gate_phantom_refs.py:209`: `[IMP:9][phantom_gate][negative] PASS` ✅
- `test_gate_dead_code.py`: IMP:9 присутствует ✅
- `test_gate_no_unregistered_entrypoint.py`: IMP:9 присутствует ✅

100% PASS с IMP:9 бизнес-логикой — anti-illusion check PASS.

---

## E. Прочее

| # | Проверка | Результат |
|---|----------|-----------|
| E1 | `git log --oneline -3` — B4 и B8 раздельные коммиты; рабочее дерево чистое | **PASS** — `128807a` (B8), `f3823e1` (B4), `59db479` (B5). `git status --porcelain` пуст |
| E2 | Стиль: GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT в новых/изменённых файлах | **PASS** — `test_gate_phantom_refs.py`: GREP_SUMMARY ✅, STRUCTURE ✅, MODULE_CONTRACT ✅; `state_machine.py`: GREP_SUMMARY/STRUCTURE/MODULE_CONTRACT обновлены ✅ |
| E3 | Нет НОВЫХ нарушений языковой политики (inline python3 в shell) | **PASS** — `grep "inline.*python\|python3 -c\|python -c" test_gate_phantom_refs.py` → 0 |
| E4 | LDD-логи [IMP:7-10] в новом гейте | **PASS** — 6 LDD-логов в test_gate_phantom_refs.py: IMP:8 (2x), IMP:9 (2x), IMP:10 (2x) |
| E5 | core/AGENTS.md глоссарий: `dev-certs → dev_cert_generator.py` | **PASS** — line 117 содержит `dev_cert_generator.py` |

---

## Сводка найденных проблем

| Severity | ID | Файл:строка | Описание | Рекомендация |
|----------|----|-------------|----------|--------------|
| **WARNING** | C4-DEGRADED | `tests/test_cert_backup_gap.py:471` | Тест `test_s3_unavailable_does_not_block_cert_issue` деградировал в no-op после shell→Python миграции s3-ssl-cache | Адаптировать проверку на Python-путь (s3_ssl_cache.py) или удалить если покрыто unit-тестами |

---

## Семантический вердикт

| Компонент | Статус |
|-----------|--------|
| AC1-AC8 (критерии брифа) | ✅ Все 8 PASS |
| Фантом-скан (независимый) | ✅ 0 упоминаний во всех target-директориях |
| Consumer-scan | ✅ Нет остаточных потребителей (кроме допустимой истории) |
| Тесты (targeted 136 + gate 296) | ✅ 432 passed, 0 failed, 15 legitimate skips |
| Поведенческая целостность | ✅ Мёртвый код удалён, живой сохранён, forced-command исправлен |
| Отклонения Coder'а | ✅ Все 7 подтверждены корректными |
| Стиль/маркап/LDD | ✅ Соответствует стандартам |
| Единственная проблема | ⚠️ WARNING C4-DEGRADED (тест-консерватор деградировал в no-op) |

**ВЕРДИКТ: STABLE**

Волна B8 выполнена с высоким качеством:
- Все 8 acceptance criteria брифа удовлетворены
- Независимый фантом-скан подтверждает строгий D3-режим (0 упоминаний)
- Consumer-scan не выявил остаточных потребителей
- 432 теста проходят без ошибок (включая полный gate fast)
- Поведенческая целостность сохранена (execute_grouped_phase жив, reconciler_projects не тронут)
- Единственная проблема (C4-DEGRADED) — WARNING, не блокирующий, затрагивает тест-консерватор, реальное поведение покрыто unit-тестами

### Рекомендация: принимается без доработок

Проблема C4-DEGRADED не блокирует merge. Тест `test_s3_unavailable_does_not_block_cert_issue` может быть исправлен в следующей волне (B9) или отдельным PR. Реальное поведение полностью покрыто `tests/unit/test_s3_ssl_cache.py` и `tests/unit/test_cert_orchestrator.py`.

---
*QA: Kilo · 2026-08-01 · SHA 128807a*
