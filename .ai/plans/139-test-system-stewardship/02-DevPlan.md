# 139-test-system-stewardship — 02-DevPlan.md

$START_DEVPLAN

$ARTIFACT_CONTRACT
PURPOSE:               Актуализировать тестовую систему ai-platform после месяца Strangler-Fig (116–138): удалить синтетику, закрыть 10 blind spots (6 из аудитов + 4 новых), консолидировать дубли, переписать тесты реализации, навести порядок в TRAP-таксономии и процессных артефактах. Цель — тестовое дерево легче на 15-20% без потери обнаруживаемости.
DESCRIPTION:           5 волн после влития предусловий 137/138. W1: безопасная очистка (2 синтетических файла с миграцией W4-E5, 4 мёртвые фикстуры, yaml_read.sh, TRAP-мусор, Rev-обновления, inventory-процедура). W2: переписывание тестов реализации (test_upload, log-строки tor/cron, 3 bash-теста на Python-каноны, flaky-фиксы, top-10 private). W3: консолидация (healthcheck 4→2, module.yaml 7→3, 7 static/unit-пар, ssh-раннеры, cross_layer −70%, deploy_modules). W4: закрытие blind spots (vhost_configurator, compose_validator, build_cache, generate_catalog, phases/system, backup_collector, render-monitoring hook после 138 W3). W5: процессная гигиена (TRAP-словарь, RESOLVED-практика, anti-loop/xdist, deploy.sh тикет, документирование inventory-процедуры).
RATIONALE:             Верификация 139 (см. 01-Brief §2-§3) подтвердила консенсус аудитов и добавила 3 коррекции (C1-C3) и 4 новых blind spot. Синтетика создаёт локальную ложную уверенность (зелёная при любой поломке production); тесты реализации краснеют от безобидных правок и молчат при семантических поломках; дубли добавляют поддержку без обнаруживаемости. Inventory-гейт (test_gate_test_inventory) — обязательная процедурная рамка каждой волны удаления (changelog + регенерация), без неё Phase 1 падает на гейте. Anti-Tamper Replication (T18) — намеренная пара sync_inventory.py ↔ gate, НЕ консолидируется.
ACCEPTANCE_CRITERIA:   (1) test_sequencing.py + test_converge_exit.py удалены, W4-E5 страховки в unit/test_reconciler.py; (2) 4 фикстуры + yaml_read.sh удалены, таблица исключений AGENTS.md обновлена; (3) test_inventory.yaml регенерирован, changelog-записи на каждое удаление, gate зелёный после КАЖДОЙ волны; (4) 7 новых тест-файлов blind spot с IMP:9-траекториями; (5) 3 bash-теста на Python-канонах; (6) healthcheck 4→2, module.yaml 7→3, 7 пар консолидированы, ssh-раннеры без verbatim-копий (гейт ssh_opts_sole_path зелёный); (7) test_cross_layer_imports.py ≤ 600 LOC (direction-based); (8) словарь TRAP-типов в .kilo/agents/code.md, консолидация BUGFIX/BUG-FIX/FIX/LOCAL/UPSTREAM/DRIFT/CARVE-OUT/DESIGN; (9) anti-loop xdist-безопасен (счётчик только на master); (10) make gate MODE=fast зелёный, make check чистый; (11) тикет верификации deploy.sh заведён.
IMPLEMENTS:            01-Brief.md (139); решения пользователя 2026-08-05; аудиты 2026-08-05.
IMPACTS:               tests/ (~25 файлов изменено/удалено, ~8 новых), AGENTS.md, core/AGENTS.md, makefiles/bootstrap.mk, core/lib/yaml_read.sh (удаление), .kilo/agents/code.md, tests/_conftest/counter.py, tests/test_inventory.yaml, tests/test_inventory_changes.yaml.
REQUIRES:              137 + 138 влиты (предусловия); make test-inventory-sync; make gate MODE=fast; ruff; xdist-прогон для верификации отсутствия флаков.
$END_ARTIFACT_CONTRACT

$START_DOCUMENT_PLAN
### Document Plan
**SECTION_GOALS:**
- GOAL [Зафиксировать стратегию и принципы (что KEEP, что DELETE, что CONSOLIDATE)] => G1 (§1)
- GOAL [Целевая архитектура тестовой системы + 3 контракта удалений] => G2 (§2)
- GOAL [Draft Code Graph — структурные якоря] => G3 (§3)
- GOAL [Step-by-step Data Flow — процессы W1 inventory-процедуры и W4 blind spot] => G4 (§4)
- GOAL [Суперпозиция решений по каждому классу находок] => G5 (§5)
- GOAL [5 волн с задачами, AC, зависимостями] => G6 (§6)
- GOAL [Файловый манифест, риски, промт-шаблон, глобальные AC] => G7 (§7-§10)
$END_DOCUMENT_PLAN

## 1. Стратегия

### 1.1 Принципы

| Принцип | Содержание |
|---------|-----------|
| **KEEP** | Регрессионные тесты реальных багов (136 W1/W2 D1-D23, fix 126 P0, fix 121); enforcement-гейты sole_path/parity/cross_layer (не артефакты — живой allowlist); Test Honesty R1-R5 + их гейты; TRAP[TEST] 2429 (R5-реестр «remove if»); chaos-harness (11 сценариев); каноны распиливания (atomic_writer, reload_safe, subprocess_io, healthcheck_poller) + их тесты; test_remote_executor (exit 0/1/2/124); test_channels_injection (репурпоз 118→136); Anti-Tamper Replication T18 (sync_inventory ↔ gate — намеренные копии) |
| **DELETE** | 2 синтетических P0-файла (~845 LOC); 4 мёртвые фикстуры; yaml_read.sh (решение пользователя); ~15 TRAP-upgrade-записей; 3 TRAP[TASK-3]; 8 BUGFIX/BUG-FIX → консолидация в BUG; дубль makefiles/bootstrap.mk:91 |
| **REWRITE** | test_upload (внутренности моков → эффект); test_install_tor_proxy + test_cron_installer (точные строки логов → структурные); 3 bash-теста → Python-каноны; ~25-30 private-доступов (top-10 в этой волне); flaky-фиксы (env-мутация, uuid, sys.path) |
| **CONSOLIDATE** | healthcheck 4→2; module.yaml 7→3; 7 static/unit-пар; ssh-раннеры 3→1 канон; test_cross_layer_imports 1809→≤600; test_deploy_modules 62KB → параметризация |
| **ADD** | 10 blind spot модулей (6 аудитов + 4 новых): vhost_configurator, compose_validator, build_cache, generate_catalog, phases/system, backup_collector, render-monitoring hook (после 138 W3) |

### 1.2 Порядок исполнения (критический путь)

```
137 + 138 влиты (предусловия, вне 139)
  → W1 очистка (включая inventory-регенерацию после каждого удаления)
  → W2 переписывание (параллелится с W1 в работе разных субагентов, но коммиты раздельны)
  → W3 консолидация (после W1 — не трогаем файлы, удалённые в W1)
  → W4 blind spots (W4.7 зависит от 138 W3; остальные независимы)
  → W5 процессная гигиена + финальная inventory-регенерация
  → make check → make gate MODE=fast
```

### 1.3 Инварианты (не нарушать)

1. **Inventory-процедура**: каждое удаление теста = запись в test_inventory_changes.yaml (nodeid, reason, issue, approved_by) + регенерация test_inventory.yaml (`make test-inventory-sync`). Порядок: changelog СНАЧАЛА, удаление ПОТОМ, regen в конце волны.
2. **Anti-Tamper T18**: tools/sync_inventory.py и gates/test_gate_test_inventory.py НЕ трогать, не импортировать друг из друга, не рефакторить в shared.
3. **LDD**: каждый новый/переписанный тест — IMP:9-траектория (caplog, `_print_ldd_trajectory` из tests/_conftest/ldd.py, декоратор `@ldd_trajectory`).
4. **xdist**: `-n auto` — стандарт; `xdist_group("serial")` НЕ работает; docker — только канонические фикстуры (platform_services); общие ресурсы — tmp_path.
5. **Gate Trinity**: любые НОВЫЕ gate-тесты — файл tests/gates/ + @pytest.mark.gate + entrypoint-manifest.yaml.
6. **Языковая политика**: новый тестовый код — только Python; никаких синтетических bash-симуляций (это и есть ликвидируемый класс P0).
7. **Не ломать живые контракты**: `_print_ldd_trajectory` (1 определение), `module_yaml_paths` (3 callsites), `_conftest.infra` (lazy singleton), test-site name в node.yaml (жив).
8. **Commit policy U-83**: ≤2 коммита на DevPlan (docs + feat), раздельные feat-коммиты по волнам — норма.

## 2. Целевая архитектура

### 2.1 Состояние тестовой системы ПОСЛЕ (цель)

```
tests/
├── contracts/          # +1 расширение: sequencing-контракт Makefile (NODE/LAUNCH семантика на уровне манифеста)
├── gates/              # без изменений объёма (консолидация TRAP-типов не трогает гейты)
├── unit/               # +7 новых файлов blind spot, +3 мигрированных из корня (bash→Python), +≤3 W4-E5 переносов
├── test_data/          # −4 файла/директории (node_yaml_contexts, node_yaml_invalid, projects/test-site/, projects/tronyx-site/)
├── _conftest/          # counter.py — xdist-безопасный (master-only), ldd.py — без изменений
├── test_inventory.yaml # регенерирован: ~3505 → ~3330 nodeids (W1) → финальная регенерация в W5
└── test_*.py           # −2 (sequencing, converge_exit), −3 bash (миграция в unit/), +0
```

### 2.2 Три контракта удалений (W1)

| Контракт | Содержание | Критерий готовности |
|----------|-----------|---------------------|
| K1: test_converge_exit | Сверить W4-E5 (drift, idempotency, _is_stub edge, project-name validation) с unit/test_reconciler.py → перенести недостающие (≤3) → удалить файл | unit/test_reconciler.py покрывает все 4 класса edge-кейсов; файл удалён |
| K2: test_sequencing | Расширить tests/contracts/test_make_target_contracts.py: проверить, что NODE/LAUNCH переменные и preflight-семантика зарегистрированы в entrypoint-manifest (контракт регистрации, НЕ симуляция bash) | Новый контракт-тест зелёный; файл удалён |
| K3: фикстуры | Удалить node_yaml_contexts.yaml, node_yaml_invalid.yaml, projects/test-site/, projects/tronyx-site/ — ТОЛЬКО директории/файлы; НЕ трогать запись test-site в tests/test_data/node.yaml (живой consumer — test_node_yaml_domains.py:94) | rg по репо = 0 ссылок на удалённые пути; node.yaml нетронут |

## 3. Draft Code Graph (XML)

```xml
<graph>
  <!-- W1 deletions -->
  <entity name="tests_test_sequencing_py" type="FILE" keywords="synthetic-bash P0 DELETE" />
  <entity name="tests_test_converge_exit_py" type="FILE" keywords="synthetic-bash P0 DELETE" />
  <entity name="core_lib_yaml_read_sh" type="FILE" keywords="dead-code callsites-0 DELETE-user-decision" />
  <entity name="tests_unit_test_reconciler_py" type="FILE" keywords="W4-E5-migration TARGET +≤3" />
  <entity name="tests_contracts_test_make_target_contracts_py" type="FILE" keywords="sequencing-contract EXTEND" />

  <!-- W2 rewrites -->
  <entity name="tests_test_upload_py" type="FILE" keywords="mock-internals→effect REWRITE" />
  <entity name="tests_unit_test_install_tor_proxy_py" type="FILE" keywords="log-string→structural REWRITE" />
  <entity name="tests_unit_test_cron_installer_py" type="FILE" keywords="log-string→structural REWRITE" />
  <entity name="tests_unit_test_healthcheck_lib_py" type="FILE" keywords="bash→python-canon MIGRATE" />
  <entity name="tests_unit_test_module_interface_py" type="FILE" keywords="bash→python-canon MIGRATE" />
  <entity name="tests_unit_test_shared_module_interface_py" type="FILE" keywords="bash→python-canon MIGRATE" />

  <!-- W3 consolidation -->
  <entity name="healthcheck_consolidation" type="GROUP" keywords="4→2 files poller-canon" />
  <entity name="module_yaml_consolidation" type="GROUP" keywords="7→3 gate+negative+param" />
  <entity name="static_unit_pairs" type="GROUP" keywords="7-pairs→unified" />
  <entity name="core_internal_verify_sweep_py" type="FILE" keywords="ssh-runner DEDUP import-vps_readiness" />
  <entity name="core_internal_scaffold_project_lister_py" type="FILE" keywords="ssh-runner DI→shared" />
  <entity name="tests_test_cross_layer_imports_py" type="FILE" keywords="1809→≤600 direction-based" />

  <!-- W4 blind spots -->
  <entity name="core_internal_scaffold_vhost_configurator_py" type="FILE" keywords="blind-spot tests-ADD skip-fallback" />
  <entity name="core_internal_scaffold_compose_validator_py" type="FILE" keywords="blind-spot NEW tests-ADD cascade" />
  <entity name="core_internal_bootstrap_deploy_build_cache_py" type="FILE" keywords="blind-spot NEW tests-ADD cache" />
  <entity name="core_internal_catalog_generate_catalog_py" type="FILE" keywords="blind-spot NEW tests-ADD" />
  <entity name="core_internal_bootstrap_lifecycle_phases_system_py" type="FILE" keywords="blind-spot behavioral-tests" />
  <entity name="core_internal_healthcheck_metrics_backup_collector_py" type="FILE" keywords="blind-spot NEW tests-ADD" />
  <entity name="run_monitoring_reconfig" type="FUNC" keywords="138-W3-prereq hook-test W4.7" />

  <!-- W5 process -->
  <entity name="kilo_agents_code_md" type="FILE" keywords="TRAP-dictionary RESOLVED-practice" />
  <entity name="tests_conftest_counter_py" type="FILE" keywords="anti-loop xdist-master-only" />
  <entity name="deploy_sh" type="FILE" keywords="ticket-only DO-NOT-TOUCH" />
</graph>
```

## 4. Step-by-step Data Flow

### 4.1 Inventory-процедура волны удаления (W1, повторяется в W5)

```
┌─ агент удаляет тест ─┐
│ 1. rg удаляемые nodeid по дереву (нет живых ссылок)
│ 2. запись в test_inventory_changes.yaml (nodeid/reason/issue/approved_by)
│ 3. удаление файла/директории
│ 4. make test-inventory-sync → регенерация test_inventory.yaml
│ 5. pytest tests/gates/test_gate_test_inventory.py -q → PASS (сверка baseline)
│ 6. git diff — changelog + baseline + удаления в одном коммите
└─ commit feat(139): W1 — safe deletions ─┘
```

### 4.2 W4.7 render-monitoring hook-тест (после влития 138 W3)

```
138 W3: run_monitoring_reconfig() извлечён из main() → DeployOrchestrator._run_post_deploy_chain (non-blocking)
  → 139 W4.7: unit-тест run_monitoring_reconfig (извлечённая функция)
    · с monitoring-секцией → рендер вызывается, [IMP:9][hook] monitoring on-project-deploy START/DONE
    · без секции → skip без рендера
    · сбой рендера → WARN non-fatal, деплой продолжается
  → интеграционный тест: receive-деплой → hook-логика (mock-оркестратор, не реальный VPS)
```

## 5. Суперпозиция решений

| # | Вопрос | Рассмотренные варианты | Решение |
|---|--------|------------------------|---------|
| S1 | yaml_read.sh (100 LOC, 0 callsites) | (a) удалить сейчас — факт 0 callsites, Rev-условие «при 0 ссылок в течение 90 дней» формально не истекло; (b) ждать 2026-11-01; (c) архив в files/ | **УДАЛИТЬ СЕЙЧАС** (решение пользователя; снять строку из таблицы shell-исключений AGENTS.md) |
| S2 | Координация 137/138 (авторизованы, не реализованы) | (a) предусловия — порядок исполнения фиксирован, их тесты вне консолидации; (b) параллельные ветки — риск конфликтов; (c) зонтичное исполнение — теряется per-plan трейл | **ПРЕДУСЛОВИЯ** (решение пользователя; W0 фиксирует: 139 стартует после влития 137+138) |
| S3 | test_converge_exit (643 LOC, W4-E5 страховки) | (a) сверка + перенос + удаление; (b) удалить целиком; (c) оставить | **СВЕРКА + УДАЛЕНИЕ** (решение пользователя; перенос ≤3 тестов в unit/test_reconciler.py) |
| S4 | test-site/tronyx-site фикстуры | (a) удалить обе директории (факт: 0 ссылок, C1/C2); (b) оставить (осторожность аудита 1) | **УДАЛИТЬ ОБЕ**, но НЕ трогать имя test-site в node.yaml (живой consumer) |
| S5 | healthcheck консолидация | (a) 4→2 (contract + static фасад); (b) 8 файлов → 2 (включая hermes/project/poller — неверно: разные домены) | **4→2**: contract + static фасада; unit/test_healthcheck_poller.py — канон (не трогать); hermes/project — отдельные домены (не консолидировать) |
| S6 | module_yaml_paths | (a) удалить (аудит 2); (b) KEEP — 3 живых callsites (аудит 1 + верификация) | **KEEP** (коррекция C3) |
| S7 | test_cross_layer_imports | (a) direction-based allowlist; (b) полное удаление; (c) без изменений | **DIRECTION-BASED**: allowlist направлений (не пар), целевой объём ≤600 LOC |
| S8 | TRAP-таксономия | (a) словарь в .kilo/agents/code.md + консолидация мелких типов в BUG/DECISION; (b) RESOLVED-практика обязательна; (c) TRAP[ARCHIVED] (0 применений за 2 мес) — исполнять или убрать | **СЛОВАРЬ + КОНСОЛИДАЦИЯ**; ARCHIVED → заменить на RESOLVED-практику (убрать из словаря) |
| S9 | Anti-Loop counter | (a) warning-уровень + xdist master-only; (b) вынести в tools/; (c) удалить | **WARNING + MASTER-ONLY** (counter.py — счётчик только на master, PYTEST_XDIST_WORKER-гейт); файлы остаются в tests/ (терять протокол нельзя — он ловит зацикливание агентов) |

## 6. Волны

### W1 — Безопасная очистка (SAFE deletions + TRAP cleanup) — 1-2 дня

**Задачи:**
1. **K1** — test_converge_exit.py: сверить W4-E5 (drift-detection, reconcile idempotency, _is_stub edge, project-name validation) с unit/test_reconciler.py → перенести недостающие (≤3 теста, IMP:9-траектория) → удалить файл.
2. **K2** — test_sequencing.py: расширить tests/contracts/test_make_target_contracts.py контрактом регистрации NODE/LAUNCH/preflight-семантики → удалить файл.
3. **K3** — фикстуры: удалить node_yaml_contexts.yaml, node_yaml_invalid.yaml, projects/test-site/, projects/tronyx-site/ (только пути, не node.yaml).
4. yaml_read.sh: удалить; снять строку из таблицы shell-исключений root AGENTS.md; проверить оставшиеся упоминания (только история/комментарии — не трогать).
5. TRAP-чистка: ~15 upgrade-записей «upgraded X→Y» (changelog в git), 3 TRAP[TASK-3] (start_period 30→15s), консолидация 8 BUGFIX/BUG-FIX → BUG, дубль makefiles/bootstrap.mk:91 (копия :63 на чужом таргете — удалить строку).
6. Обновить bootstrap.sh:15/16 (Rev наступили и закрыты 118 B6 — текст: «подтверждено 2026-08-05» / снять Rev).
7. Inventory: changelog-записи + `make test-inventory-sync` → регенерация; зафиксировать новый счёт (~3505 → ~3330 nodeids).
8. Проверка: `pytest tests/gates/test_gate_test_inventory.py -q` PASS; `make check` чистый.

**AC W1:** (a) оба P0-файла удалены, W4-E5 страховки в unit/test_reconciler.py; (b) 4 фикстуры + yaml_read.sh удалены, таблица AGENTS.md обновлена; (c) test_inventory.yaml регенерирован, changelog-записи на каждое удаление; (d) TRAP-мусор вычищен (upgrade/TASK-3/BUGFIX/дубль bootstrap.mk); (e) bootstrap.sh Rev обновлены; (f) gate зелёный.

### W2 — Переписывание тестов реализации — 2-3 дня

**Задачи:**
1. **test_upload.py**: `fake._uploads`/`_call_count` → проверить ЭФФЕКТ: файл в spool, exit code, retry-поведение, ответ публичного API. Мок сохранить только как DI-контракт (подпись), не как объект утверждений.
2. **test_install_tor_proxy.py + test_cron_installer.py**: точные строки логов → структурные проверки (IMP-код, severity, факт события через caplog records, а не text match).
3. **3 bash-теста → Python-каноны**:
   - unit/test_healthcheck_lib.py: healthcheck.sh → healthcheck_poller.py (канон D5) — критерий состояния (running AND healthy|""|none);
   - unit/test_module_interface.py + unit/test_shared_module_interface.py: module-interface.sh → shared/module_interface.py invoke (канон 119 D4).
4. **Flaky-фиксы** (xdist): test_status_page.py:256 env-мутация → monkeypatch.setenv + restore; test_hermes_init.py uuid.uuid4() → seed/фиксированный uuid; test_node_yaml_consumers.py:73 относительный sys.path → абсолютный (Path(__file__).parent...).
5. **Top-10 private-доступов → публичные контракты**: для каждого решить «поведение vs деталь»; неотъемлемые контракты (_resolve_org, _format_bytes, _traverse_dotted_list_aware) — либо поднять в публичные с документацией, либо тестировать через публичный путь. Список-кандидат: test_status_page.py (_format_bytes), test_context_promoter (_resolve_org), test_node_yaml_cli (_traverse_dotted_list_aware), test_secrets_manager (sm._ensure_htpasswd), test_upload, +5 по выбору из аудитов.
6. Проверка: `make test-summary TEST_FILE=...` по каждому изменённому файлу; `make check`.

**AC W2:** (a) test_upload проверяет эффект, не внутренности мока; (b) 0 assert на точные строки логов в tor/cron; (c) 3 файла мигрированы на Python-каноны (bash-фасады покрыты static-контрактами); (d) 0 env-мутаций без monkeypatch, 0 uuid без seed, 0 относительных sys.path; (e) top-10 private-доступов закрыты; (f) xdist-прогон изменённых файлов без флаков.

### W3 — Консолидация дублей — 2-3 дня

**Задачи:**
1. **Healthcheck 4→2**: test_healthcheck_contract.py + test_healthcheck_static.py + test_lib_healthcheck.py + unit/test_modules_healthcheck.py → (1) контрактный файл на Python-каноне healthcheck_poller.py + (2) static-grep фасада. НЕ трогать: unit/test_healthcheck_poller.py (канон), unit/test_hermes_healthcheck.py, unit/test_project_healthcheck.py (отдельные домены).
2. **Module.yaml 7→3**: test_*_static.py ×3 + test_gate_module_yaml_contract.py + 2 negative + validate_module_yaml → gate-контракт + negative + 1 параметризованный static. (Консолидировать static-вариации; гейт-контракт и negative — неприкосновенны.)
3. **7 static/unit-пар**: clickhouse, logging, monitoring + остальные 4 → единый файл с двумя уровнями проверок (контракт + реализация в одном месте, параметризация по домену).
4. **SSH-раннеры**: verify_sweep._default_ssh_runner → `from core.internal.shared.vps_readiness import _default_ssh_runner` (сверка сигнатуры: host/user/cmd/timeout/ssh_lib_path — идентична); project_lister._ssh_read (DI, timeout default SSH_READ_TIMEOUT) → shared-раннер с DI-параметром; удалить локальные копии. Гейт ssh_opts_sole_path остаётся зелёным.
5. **test_cross_layer_imports.py**: 1809 LOC → direction-based: allowlist НАПРАВЛЕНИЙ (запрещённые пары слоёв), не пар модулей; целевой объём ≤600 LOC; R5-negative (dotted py import RED + python3 -m RED) сохранить.
6. **test_deploy_modules.py (62KB)**: параметризация/сплит по подобластям (пакеты, env, фасады).
7. Проверка: `make check` + `make gate MODE=fast`.

**AC W3:** (a) healthcheck 2 файла, module.yaml 3 файла, 7 пар консолидированы; (b) 0 verbatim-копий ssh-раннера (rg _default_ssh_runner → 1 def); (c) cross_layer ≤600 LOC с direction-allowlist и сохранёнными negative; (d) gate зелёный; (e) покрытие консолидированных доменов не упало (сверка до/после по nodeid-инвентарю).

### W4 — Закрытие blind spots — 3-4 дня

**Задачи:**
1. **vhost_configurator.py** (222 LOC): unit — configure_vhost: пустой domain → SKIP (False) без мутаций; update_yaml_for_vhost: needs.domain + expose:true перед генерацией; fallback configure_vhost_via_subprocess при недоступности vhost_renderer (mock); resolve_node_configs_dir: PROJECTS_ROOT fallback. Рендер НЕ перетестировать (покрыт test_vhost_renderer.py).
2. **compose_validator.py** (219 LOC, НОВЫЙ blind spot): unit — 3-method cascade (docker compose config → PyYAML → best-effort skip), proxy-net external:true + ≥1 service, без domain → valid=True, compose-файлы не мутируются.
3. **build_cache.py** (280 LOC, НОВЫЙ): unit — cache hit/miss, инвалидация, ключи, отсутствие гонок (xdist-безопасность: tmp_path-изоляция).
4. **generate_catalog.py** (260 LOC, НОВЫЙ): unit — генерация каталога из project_yaml, пустой вход, обработка отсутствующих проектов.
5. **phases/system.py** (655 LOC): поведенческие тесты фаз users/docker/system — через execution harness (mock-шелы/фикстуры, НЕ subprocess реальной ноды): успешный прогон, precondition-fail (не-root, отсутствие AGE-ключа), idempotent re-run.
6. **backup_collector.py** (116 LOC, НОВЫЙ): unit — сборка backup-метрик, отсутствие бэкапов, ошибки чтения.
7. **render-monitoring hook** (ПОСЛЕ 138 W3): unit-тест run_monitoring_reconfig (извлечённой функции): с monitoring-секцией → START/DONE IMP:9; без секции → skip; сбой → non-fatal WARN. Интеграционный тест — mock-оркестратор (receive-деплой), не реальный VPS.
8. Проверка: каждый новый файл — LDD IMP:9-траектория; `make test-summary TEST_FILE=...`; `make check`.

**AC W4:** (a) 7 модулей/функций имеют unit-покрытие (vhost_configurator, compose_validator, build_cache, generate_catalog, phases/system, backup_collector, run_monitoring_reconfig); (b) каждый тест с IMP:9; (c) рендер не дублируется (test_vhost_renderer не тронут); (d) gate зелёный; (e) W4.7 выполнен после влития 138 W3 (иначе — задача в Debt с Rev-датой).

### W5 — Процессная гигиена — 1-2 дня

**Задачи:**
1. **TRAP-таксономия**: словарь типов в .kilo/agents/code.md — канонический набор: TEST, BUG, DECISION, BUSINESS, PERF, INCIDENT, INDEX, CROSS-LAYER, DOCKER-BIND-MOUNT. Консолидация: BUGFIX(6)/BUG-FIX(2)/FIX(9)/UPSTREAM(1)/DRIFT(1)/CARVE-OUT(1)/DESIGN(4)/LOCAL(7) → BUG или DECISION (по смыслу). TRAP[ARCHIVED] (0 применений) — убрать из словаря, заменить RESOLVED-практикой.
2. **RESOLVED-практика**: инструкция в .kilo/agents/code.md — закрытые TRAP помечаются `TRAP[BUG] · RESOLVED · <дата> · <ссылка на волну>` (образец: state_machine.py:648); применять при закрытии волн 139.
3. **Anti-Loop**: _conftest/counter.py — счётчик только на master (PYTEST_XDIST_WORKER-гейт), иначе warning; .test_counter.json остаются, но не пишутся с воркеров. Проверка: прогон `-n auto` — счётчик не искажается (регрессия инцидента 124).
4. **deploy.sh тикет**: завести тикет верификации brief A на production (StatusReport после ближайшего деплоя); код НЕ трогать. Зафиксировать в Debt-реестре 139 с Rev-датой.
5. **Документирование**: в tests/AGENTS.md — раздел «Удаление тестов» (процедура: rg → changelog → удаление → test-inventory-sync → gate).
6. Финальная регенерация inventory + `make check` + `make gate MODE=fast`.

**AC W5:** (a) словарь TRAP-типов в .kilo/agents/code.md; (b) 0 маркеров неканонических типов (кроме осознанных исторических — задокументировать); (c) RESOLVED-инструкция и ≥3 примера применения; (d) anti-loop xdist-безопасен; (e) тикет deploy.sh заведён; (f) tests/AGENTS.md документирует процедуру удаления; (g) gate зелёный.

## 7. Файловый манифест

### 7.1 Удаление (W1)

| Файл | LOC | Обоснование |
|------|-----|-------------|
| tests/test_sequencing.py | 202 | P0 синтетика (K2) |
| tests/test_converge_exit.py | 643 | P0 синтетика (K1, после миграции W4-E5) |
| core/lib/yaml_read.sh | 100 | 0 callsites, решение пользователя |
| tests/test_data/node_yaml_contexts.yaml | — | 0 refs |
| tests/test_data/node_yaml_invalid.yaml | — | 0 refs |
| tests/test_data/projects/test-site/ | — | 0 refs (C2: только директория) |
| tests/test_data/projects/tronyx-site/ | — | 0 refs (C1: «live» — ложь аудита) |

### 7.2 Изменение (W2-W5)

| Файл | Волна | Действие |
|------|-------|----------|
| tests/contracts/test_make_target_contracts.py | W1 | + контракт NODE/LAUNCH/preflight регистрации (K2) |
| tests/unit/test_reconciler.py | W1 | + ≤3 W4-E5 теста |
| tests/test_upload.py | W2 | переписывание на эффект |
| tests/unit/test_install_tor_proxy.py | W2 | структурные проверки |
| tests/unit/test_cron_installer.py | W2 | структурные проверки |
| tests/unit/test_healthcheck_lib.py | W2 | миграция на healthcheck_poller |
| tests/unit/test_module_interface.py | W2 | миграция на shared/module_interface |
| tests/unit/test_shared_module_interface.py | W2 | миграция |
| tests/test_status_page.py | W2 | monkeypatch, публичный путь |
| tests/unit/test_hermes_init.py | W2 | uuid seed |
| tests/test_node_yaml_consumers.py | W2 | абсолютный sys.path |
| tests/test_healthcheck_contract.py, test_healthcheck_static.py, test_lib_healthcheck.py, unit/test_modules_healthcheck.py | W3 | консолидация 4→2 |
| tests/test_*_static.py ×3 + module.yaml группа | W3 | консолидация 7→3 |
| 7 static/unit-пар | W3 | объединение |
| core/internal/verify_sweep.py | W3 | import из vps_readiness |
| core/internal/scaffold/project_lister.py | W3 | shared-раннер с DI |
| tests/test_cross_layer_imports.py | W3 | 1809→≤600 |
| tests/test_deploy_modules.py | W3 | параметризация |
| tests/unit/test_vhost_configurator.py | W4 | НОВЫЙ (7 тестовых файлов blind spot) |
| tests/unit/test_compose_validator.py | W4 | НОВЫЙ |
| tests/unit/test_build_cache.py | W4 | НОВЫЙ |
| tests/unit/test_generate_catalog.py | W4 | НОВЫЙ |
| tests/unit/test_bootstrap_system_phases.py | W4 | НОВЫЙ (поведенческий) |
| tests/unit/test_backup_collector.py | W4 | НОВЫЙ |
| tests/unit/test_monitoring_reconfig.py | W4 | НОВЫЙ (после 138 W3) |
| .kilo/agents/code.md | W5 | словарь TRAP + RESOLVED |
| tests/_conftest/counter.py | W5 | master-only |
| tests/AGENTS.md | W5 | процедура удаления тестов |
| AGENTS.md (root) | W1 | таблица shell-исключений − yaml_read.sh |
| makefiles/bootstrap.mk | W1 | удалить дубль :91 |
| core/entrypoints/bootstrap.sh | W1 | строки 15-16: Rev «CI auto-deploy» / «Wave 4 — parse_args spec» — наступили и закрыты (118 B6), текст → «подтверждено 2026-08-05» |
| tests/test_inventory.yaml + test_inventory_changes.yaml | W1/W5 | регенерация + changelog |

### 7.3 НЕ трогать (осознанные решения)

- tests/gates/test_gate_test_inventory.py + tests/tools/sync_inventory.py (Anti-Tamper T18)
- unit/test_healthcheck_poller.py, unit/test_hermes_healthcheck.py, unit/test_project_healthcheck.py
- unit/test_vhost_renderer.py (1123 LOC — рендер покрыт)
- tests/test_data/node.yaml (запись test-site жива)
- tests/e2e/test_chaos_resilience.py, 136 W1/W2 регрессии, fix(126) P0-тесты
- core/entrypoints/deploy.sh (175 LOC — тикет, не код)
- module_yaml_paths() (C3 — жив)

## 8. Риски

| Риск | Вероятность | Влияние | Митигация |
|------|-------------|---------|-----------|
| Консолидация healthcheck/module.yaml теряет покрытие | СРЕДНЯЯ | HIGH | Тест-матрица «кто что ловит» до консолидации; сверка nodeid-инвентаря до/после |
| W4.7 (render-monitoring) блокирован невлитым 138 | СРЕДНЯЯ | НИЗКАЯ | Остальные W4 независимы; при задержке — Debt-запись с Rev-датой |
| Inventory-гейт падает на промежуточных коммитах | НИЗКАЯ | HIGH | Процедура 4.1: changelog СНАЧАЛА, regen в конце волны, коммит атомарен |
| xdist-флаки после переписывания (W2) | СРЕДНЯЯ | СРЕДНЯЯ | Прогон изменённых файлов с `-n auto` до коммита; flaky = баг теста (124) |
| Перенос W4-E5 в test_reconciler искажает страховку | НИЗКАЯ | СРЕДНЯЯ | Перенос без изменения входов (те же фикстуры/входы), diff-ревью |
| TRAP-консолидация задевает живые ссылки (issue-cert) | НИЗКАЯ | СРЕДНЯЯ | Только типы BUGFIX/FIX/LOCAL/UPSTREAM/DRIFT/CARVE-OUT/DESIGN; кластеры KEEP не трогать (таблица §8 аудитов) |
| Рефактор ssh-раннеров меняет поведение (таймаут +5s) | НИЗКАЯ | СРЕДНЯЯ | Сверка сигнатур и timeout-семантики до импорта; test_remote_executor как сетка |

## 9. Промт-шаблон Code-субагента (SC4)

```
Волна {W1..W5} плана 139-test-system-stewardship: {краткое описание задачи}.

Контекст: {ссылка на 02-DevPlan.md §6.W{n} + файловый манифест §7}.

Жёсткие правила:
1. Inventory-процедура при удалении тестов: rg живых ссылок → запись в test_inventory_changes.yaml (nodeid/reason/issue/approved_by) → удаление → make test-inventory-sync → pytest tests/gates/test_gate_test_inventory.py -q.
2. Anti-Tamper T18: sync_inventory.py и test_gate_test_inventory.py НЕ трогать.
3. LDD: каждый тест — IMP:9-траектория через tests/_conftest/ldd.py (_print_ldd_trajectory или @ldd_trajectory).
4. xdist: -n auto-прогон изменённых файлов до коммита; никаких env-мутаций без monkeypatch, uuid без seed, относительных sys.path.
5. Никаких синтетических bash-симуляций (класс P0, ликвидируется).
6. Gate Trinity для новых gate-тестов.

Верификация: make test-summary TEST_FILE=<изменённые> → make check → (в конце волны) make gate MODE=fast.
Запрещён git checkout/restore для отката одиночных файлов — точечные edit.
```

## 10. AC глобальные (сводка)

1. W1-W5 выполнены в порядке §1.2; каждый feat-коммит по волне (U-83).
2. Тестовое дерево: −~845 LOC синтетики, −4 фикстуры, −100 LOC мёртвого shell; +~7 новых тест-файлов blind spot.
3. Inventory: baseline регенерирован, changelog полный, gate зелёный на каждом шаге.
4. 0 verbatim-копий ssh-раннера; cross_layer ≤600 LOC; healthcheck 2 файла; module.yaml 3 файла.
5. TRAP: словарь зафиксирован, 0 неканонических типов (исторические — задокументированы), RESOLVED-практика активна.
6. Anti-loop xdist-безопасен (регрессия 124 проверена).
7. deploy.sh — тикет заведён, код не тронут.
8. `make check` чистый; `make gate MODE=fast` зелёный; e2e (необязательно, при доступном test-VPS) — smoke прогон.

## 11. Открытые вопросы и TRAP-заметки

⚠️ TRAP[DECISION] · 2026-08-05 · MED · 139 — коррекции аудитов C1-C3
· Rejected: следовать аудитам буквально (удаление module_yaml_paths — сломало бы живой гейт; оставление tronyx-site — мёртвый груз в дереве)
· Reason: независимая верификация против дерева выявила 3 расхождения; МЕТА-план фиксирует верифицированное состояние.
· Rev: при следующем аудите — перепроверить статусы C1-C3.

⚠️ TRAP[DECISION] · 2026-08-05 · MED · 139 — счёт тестов
· Аудиты: 3334/3505; collect (с параметризацией): 4428; inventory baseline: 3505 nodeids.
· Решение: источником истины считать test_inventory.yaml (3505); регенерация W1/W5 фиксирует актуальный счёт.
· Rev: не требуется — процедура регенерации самодокументирующая.

⚠️ TRAP[DEBT] · 2026-08-05 · MED · 139 W4.7 — render-monitoring hook-тест
· Status: OPEN — ожидает влития 138 W3 (run_monitoring_reconfig extraction).
· Rev: 2026-09-01 — если 138 W3 не влит, задача остаётся Debt с новой Rev-датой.

⚠️ TRAP[DEBT] · 2026-08-05 · MED · 139 W5 — deploy.sh верификация brief A
· Status: OPEN — тикет заведён в W5; удаление deploy.sh возможно только после подтверждения legacy-нод на orchestrator_cli dispatch.
· Rev: 2026-11-01 — дедлайн верификации (вместе с yaml_read.sh Rev-окном, закрытым решением пользователя).

$END_DEVPLAN
