# 18-DevPlan — B9: SRP-декомпозиция монолитов

<!-- GREP_SUMMARY: SRP state_machine reconciler project_adopter private-api deploy_orchestrator is_stub decomposition helpers state_store infra -->
<!-- STRUCTURE: ┌решения пользователя D1-D5┐ → ◇ T1 state_machine (helpers/state_store/cli) → ◇ T2 reconciler (8 доменов + infra) → ◇ T3 7 приватных API + 3 находки → ◇ T4 is_stub + канал reconcile → ◇ T5 project_adopter → ◇ T6 гейты → ⊕ T7 самоверификация → ⎋ критерии/манифест/риски -->
# region MODULE_CONTRACT
## @purpose  Волна B9 программы хардненинга (116): SRP-декомпозиция 4 монолитов core/internal (state_machine 2284 LOC, reconciler 2286 LOC, deploy_orchestrator 925 LOC, project_adopter 1173 LOC) с легализацией межмодульных контрактов.
## @scope    U-07, U-08, U-28, U-31, U-32. Файлы: core/internal/bootstrap/lifecycle/{state_machine,phases,__init__}.py + новый пакет lifecycle/helpers/ + новые lifecycle/{state_store,cli}.py; core/internal/bootstrap/converge/{reconciler.py → 8 доменных модуля + infra.py}; core/internal/bootstrap/deploy/{deploy_orchestrator.py,secrets_validator.py,docker_orchestrator.py,sudoers_generator.py,orphan_reconciler.py,context_deployer.py,__init__.py}; core/internal/bootstrap/{_topo_sort.py → topo_sort.py,converge.sh}; core/internal/{reconciler_projects.py,deploy/reconcile-projects.sh}; core/internal/scaffold/{project_adopter.py,scaffold_helpers.py} + новые scaffold/{compose_validator.py,vhost_configurator.py}; новый core/internal/shared/stub_detection.py; тесты (unit/gates); манифесты (entrypoint-manifest.yaml, bootstrap/AGENTS.md, lifecycle/__init__.py, converge/__init__.py).
## @invariants
##   1. Мораторий на структурные правки state_machine.py снят (TRAP B9); семантика 14 фаз НЕ меняется — переносится только код, не поведение.
##   2. Публичные API — через публичные имена + экспорт через __init__.py; приватные функции НЕ используются между модулями (новый гейт T6.1, allowlist пуст).
##   3. Декомпозиция без consumer-scan не выполняется (прецедент B8: audit_logging.sh сломал provision) — каждое перемещение/удаление: rg по потребителям (код+тесты+CI+манифест) → обновление → зелёный gate.
##   4. Локальная R3-семантика stub-создания в reconciler (projects.py) и удалённый stub→deploy в reconciler_projects.py — ОРТОГОНАЛЬНЫ (docstring reconciler_projects.py:20), не консолидируются; консолидируется только дубль is_stub-детекции.
##   5. Генераторы YAML project_adopter уже делегируют в scaffold_helpers (DP-092 Wave 4a) — дубль gen_ai_platform_yaml отсутствует, работа T5 — вынос compose-валидации и vhost-логики + удаление мёртвого deprecated-кода.
##   6. LOC-гейт (T6.2, allowlist): state_machine.py ≤ 1200, reconciler.py ≤ 800, project_adopter.py ≤ 600.
## @rationale Бриф фиксирует цели; DevPlan фиксирует решения пользователя (D1-D5, подтверждены 2026-08-01) и результаты consumer-scan (3 находки вне брифа: _extract_domains_for_context, _render_sudoers_rules, _topo_sort module rename), чтобы Coder работал без архитектурных развилок.
# endregion MODULE_CONTRACT

$ARTIFACT_CONTRACT
PURPOSE:               Реализация волны B9 — SRP-декомпозиция state_machine, reconciler, deploy_orchestrator, project_adopter с публикацией приватных межмодульных контрактов и консолидацией is_stub-детекции.
DESCRIPTION:           T1: state_machine → lifecycle/helpers/ (7 модулей I/O) + lifecycle/state_store.py (persistence) + lifecycle/cli.py (CLI/main) → state_machine ~950 LOC, цикл phases↔state_machine устранён (односторонняя helpers). T2: reconciler → converge/{infra,perms,audit,projects,networks,vhosts,volumes,sudoers,runtime}.py + оркестратор. T3: 7 приватных API → публичные + экспорт через deploy/__init__.py + 3 находки consumer-scan (extract_domains_for_context, render_sudoers_rules, topo_sort). T4: shared/stub_detection.py (единая is_stub) + прямой вызов reconciler_projects.py из converge.sh, фасад reconcile-projects.sh удалён. T5: project_adopter → scaffold/{compose_validator,vhost_configurator}.py + удаление мёртвого deprecated-кода. T6: гейты private-import (ast, allowlist пуст) + LOC (allowlist). T7: самоверификация.
RATIONALE:             Бриф фиксирует цели; DevPlan фиксирует решения пользователя (D1-D5, 2026-08-01) и результаты consumer-scan, чтобы Coder работал без развилок. Consumer-scan выявил 3 сайта вне брифа (context_deployer._extract_domains_for_context, sudoers_generator._render_sudoers_rules, bootstrap/_topo_sort.py), нарушающие тот же гейт приватных импортов.
ACCEPTANCE_CRITERIA:   (1) state_machine.py ≤ 1200 LOC (оркестрация; persistence в state_store.py, I/O в lifecycle/helpers/, CLI в lifecycle/cli.py); (2) reconciler.py ≤ 800 LOC, 8 доменов в converge/*.py + infra.py; (3) 7 функций опубликованы через deploy/__init__.py, 0 приватных межмодульных импортов в core/ (гейт T6.1 зелёный); (4) is_stub — одна функция в shared/stub_detection.py, reconcile-канал — прямой вызов из converge.sh, reconcile-projects.sh удалён; (5) project_adopter: compose-валидация и vhost-логика вынесены, deprecated registration-код удалён, генераторы YAML через scaffold_helpers; (6) все тесты зелёные (unit + gates), make gate MODE=fast зелёный; e2e bootstrap (make test-node) не регрессировал.
IMPLEMENTS:            U-07 (7 приватных API), U-08 (state_machine-монолит), U-28 (is_stub дубль + reconcile), U-31 (reconciler), U-32 (project_adopter)
IMPACTS:               core/internal/bootstrap/lifecycle/*, core/internal/bootstrap/converge/*, core/internal/bootstrap/deploy/*, core/internal/bootstrap/{_topo_sort.py,topo_sort.py,converge.sh,AGENTS.md}, core/internal/{reconciler_projects.py,deploy/reconcile-projects.sh,shared/stub_detection.py}, core/internal/scaffold/*, core/entrypoint-manifest.yaml, core/AGENTS.md (при изменении канонических цепочек), tests/unit/*, tests/gates/*, tests/test_converge_exit.py, tests/test_stub_detection.py, tests/test_deploy_modules.py
REQUIRES:               07-Brief (B9); решения пользователя 2026-08-01 (D1-D5); B8 (чистая база — user коммитит перед стартом); B5 (shared-модули), B4 (типизированные исключения)
$END_ARTIFACT_CONTRACT

---

## 1. Решения пользователя (подтверждены 2026-08-01)

| # | Вопрос | Решение |
|---|--------|---------|
| D1 | U-08: куда выносить I/O-хелперы state_machine (~730 LOC: apt/users/ssh/secrets/validation/domains) | **lifecycle/helpers/ пакет** (7 модулей, публичные имена). phases.py остаётся файлом, меняет `_sm._x` → `helpers.*`. Зависимость: state_machine → phases → helpers, односторонняя |
| D2 | U-08: state persistence (BootstrapState/StepState + state.json I/O, ~270 LOC) | **Вынести в lifecycle/state_store.py** — state_machine = чистая оркестрация (~950 LOC, запас под гейт ≤1200) |
| D3 | U-31: layout 8 доменов reconciler + инфраструктура (report/exit/subprocess — модульные глобалы) | **converge/ пакет: converge/infra.py (публичные функции) + 8 доменных модулей**; reconciler.py — оркестратор R1-R9 + main |
| D4 | U-28: консолидация reconcile-канала (converge.sh source'ит shell-фасад reconcile-projects.sh) | **Прямой вызов reconciler_projects.py из converge.sh** (паттерн строки 119), фасад reconcile-projects.sh удаляется, entrypoint-manifest обновляется |
| D5 | U-32: объём декомпозиции project_adopter | **Полный сплит**: scaffold/compose_validator.py + scaffold/vhost_configurator.py + удаление deprecated `_register_via_node_yaml`/`_register_project_safe` + `_load_compose_profiles_from_platform_env` → scaffold_helpers |

## 2. Результаты consumer-scan (находки вне брифа)

| # | Находка | Место | Действие |
|---|---------|-------|----------|
| CS-1 | `_extract_domains_for_context` — приватная функция context_deployer, вызывается кросс-модульно | state_machine.py:1597 (→ helpers/domains.py) | Публикация `extract_domains_for_context` (T3) |
| CS-2 | `_render_sudoers_rules` — приватная функция sudoers_generator, вызывается кросс-модульно | reconciler.py:1714 (→ converge/sudoers.py) | Публикация `render_sudoers_rules` (T3) |
| CS-3 | `from core.internal.bootstrap import _topo_sort` — импорт приватного имени МОДУЛЯ | deploy_orchestrator.py:79 | Переименование `_topo_sort.py` → `topo_sort.py` (T3), обновление импортеров (deploy_orchestrator + tests/test_deploy_modules.py) |
| CS-4 | `_load_compose_profiles_from_platform_env` — приватный хелпер чтения COMPOSE_PROFILES (SoT platform-env.yaml) | project_adopter.py:90-112 | Переезд в scaffold_helpers как публичный (T5) |
| CS-5 | `_register_project_safe` (701-725), `_register_via_node_yaml` (726-744) — DEPRECATED, 0 потребителей | project_adopter.py | Удаление (T5) |
| CS-6 | test_stub_detection.py:56-67/112-121/160-169 — inline bash-КОПИИ _is_stub, тестируют несуществующую shell-функцию | tests/test_stub_detection.py | Замена на unit-тесты shared/stub_detection.py (T4) |
| CS-7 | node-lifecycle.sh вызывает `python3 lifecycle/state_machine.py --mode ...` как CLI | node-lifecycle.sh (SM_SCRIPT) | Переключение на lifecycle/cli.py (T1) |

## 3. T1 (U-08): state_machine — оркестрация без монолита

### 3.1 Новые модули

**`core/internal/bootstrap/lifecycle/state_store.py`** (NEW) — state persistence:
- `StepState` (перенос из state_machine.py:262-304)
- `BootstrapState` (перенос :304-526)
- state.json I/O: функции `load_state(path) -> BootstrapState` / `save_state(state, path)` (локализовать текущие методы load/save в StateMachine и перенести сюда)
- state_machine.py импортирует BootstrapState/StepState из state_store и re-экспортирует (публичный контракт пакета — потребители: тесты, cli.py не меняют импорты)

**`core/internal/bootstrap/lifecycle/cli.py`** (NEW) — CLI/main:
- `build_parser` (перенос :1191), `main` (:1260), `run_init_mode` (ex-`_run_init_mode` :1390), `run_update_mode` (ex-`_run_update_mode` :1465)
- Импортирует `StateMachine` из state_machine, `write_audit_log`/`send_telegram` из helpers/reporting

**`core/internal/bootstrap/lifecycle/helpers/`** (NEW пакет, 7 модулей, все функции ПУБЛИЧНЫЕ):

| Модуль | Функции (ex-приватная) | Источник |
|--------|------------------------|----------|
| `helpers/subprocess_io.py` | `run_subprocess(cmd, step_name, *, timeout=…, non_fatal=…)` | `_subprocess_run` :1606-1664 |
| `helpers/system.py` | `is_pkg_installed`, `install_apt_packages`, `ensure_sops`, `ghcr_auth` | `_is_pkg_installed` :1666, `_install_apt_packages` :1683, `_ensure_sops` :1702, `_ghcr_auth` :1998 |
| `helpers/users.py` | `create_user`, `add_ssh_key`, `ensure_projects_base` | `_create_user` :1741, `_add_ssh_key` :1771, `_ensure_projects_base` :1809 |
| `helpers/secrets.py` | `decrypt_secrets`, `ensure_secrets_exist` | `_decrypt_secrets` :1853, `_ensure_secrets_exist` :1893 |
| `helpers/validation.py` | `verify_core_files`, `validate_node_yaml`, `validate_sudoers` | `_verify_core_files` :1832, `_validate_node_yaml` :1949, `_validate_sudoers` :2016 |
| `helpers/domains.py` | `import_deploy_context`, `extract_domains`, `ssl_provision_via_orchestrator` | `_import_deploy_context` :1556, `_import_extract_domains` :1587, `_ssl_provision_via_orchestrator` :2065 |
| `helpers/reporting.py` | `run_healthchecks`, `write_audit_log`, `send_telegram` | `_run_healthchecks` :2115, `_write_audit_log` :2208, `_send_telegram` :2236 |

- helpers/domains.py использует `extract_domains_for_context` (публичная после T3, CS-1)
- Каждый модуль: MODULE_CONTRACT region, GREP_SUMMARY/STRUCTURE, LDD-логи [IMP:1-10] — переносятся вместе с функциями

### 3.2 state_machine.py после декомпозиции (~950 LOC)

Остаётся: MODULE_CONTRACT, исключения (StateTransitionError/PhaseDependencyError/PhasePreconditionError :67-88), `BootstrapPhase` enum (:88-236), `_should_retry` (:236), `StateMachine` class (:526-1188: dependency graph, precondition_check, execute_phase, execute_grouped_phase — **динамический импорт 14 фаз из phases.py :797-813 НЕ меняется**), импорт+re-export BootstrapState/StepState.
Удаляется: deprecated `RUN_STEPS` region (:1534-1548), весь регион хелперов (:1548-2284), CLI-регион (:1188-1534).
Совместимость: внизу файла ленивый импорт `if __name__ == "__main__": from core.internal.bootstrap.lifecycle.cli import main; sys.exit(main())` (без цикла импортов — cli.py загружается только при прямом запуске скрипта).

### 3.3 phases.py

- Замена `from core.internal.bootstrap.lifecycle import state_machine as _sm` (:66) на прямые импорты helpers.*
- Замена 30+ вызовов `_sm._x` (строки 107, 120, 135, 145, 166, 183, 236, 238, 246, 250, 258, 306, 319, 330, 370, 378, 417, 432, 482, 495, 597, 642, 657, 701, 740, 748, 781, 794, 840, 939, 1011) на `helpers.<module>.<public_name>`
- Бизнес-логика 14 фаз НЕ трогается (семантика φ1-φ13+φ8.5 неизменна)

### 3.4 Потребители

- node-lifecycle.sh (CS-7): `SM_SCRIPT="${SCRIPT_DIR}/lifecycle/cli.py"` (или оставить state_machine.py — compat-заглушка покрывает; канонический путь — cli.py)
- tests/unit/test_state_machine.py (:41 `import state_machine as sm`, :452/:810 canonical-импорты) — проверить ссылки на перенесённые хелперы (rg по каждому символу)
- Любые прямые запуски `python3 state_machine.py` (rg `state_machine` в .sh/.yml/.py) — через compat-заглушку работают

## 4. T2 (U-31): reconciler — оркестратор + 8 доменов

### 4.1 Новые модули в `core/internal/bootstrap/converge/` (все функции ПУБЛИЧНЫЕ)

| Модуль | Функции (ex-приватная) | Источник (reconciler.py) |
|--------|------------------------|--------------------------|
| `infra.py` | `reset_state`, `unit_enabled`, `report_init`, `report_add`, `report_emit`, `set_exit`, `run_subprocess`, `try_chmod` + модульные глобалы (REPORT, exit-code) | :102, :123, :144, :151, :164, :196, :219, :250 |
| `perms.py` | `reconcile_perms` | :282-354 |
| `audit.py` | `reconcile_audit_log`, `reconcile_ci_deploy_group` (ex-`_reconcile_ci_deploy_group`) | :371-497, :499-539 |
| `projects.py` | `reconcile_projects`, `parse_projects_yaml` (ex-`_parse_projects_yaml`), `create_empty_env_file` (ex-`_create_empty_env_file`), `reconcile_env_platform` (ex-`_reconcile_env_platform`) | :558-691, :693, :721, :755 |
| `networks.py` | `reconcile_networks`, `check_proxy_connectivity` (ex-`_check_proxy_connectivity`) | :861-941, :943 |
| `vhosts.py` | `detect_hosts_drift`, `verify_vhosts`, `resolve_nginx_overlay` (ex-`_resolve_nginx_overlay`), `detect_orphan_vhosts` (ex-`_detect_orphan_vhosts`), `run_nginx_test` (ex-`_run_nginx_test`) | :1016, :1102, :1210, :1245, :1272 |
| `volumes.py` | `reconcile_volumes`, `parse_node_modules_yaml` (ex-`_parse_node_modules_yaml`), `extract_named_volumes` (ex-`_extract_named_volumes`) | :1404, :1321, :1359 |
| `sudoers.py` | `reconcile_sudoers`, `build_sudoers_content` (ex-`_build_sudoers_content`), `atomic_write_sudoers` (ex-`_atomic_write_sudoers`), `safe_cleanup_tmp` (ex-`_safe_cleanup_tmp`) | :1671, :1566, :1589, :1642 |
| `runtime.py` | `reconcile_runtime_state`, `resolve_container_name` (ex-`_resolve_container_name`), `get_container_state` (ex-`_get_container_state`), `load_cooldown` (ex-`_load_cooldown`), `save_cooldown` (ex-`_save_cooldown`) | :1927, :1816, :1847, :1873, :1895 |

### 4.2 reconciler.py после (~200-300 LOC)

- Оркестратор: main() (:2096) + последовательный вызов доменных `reconcile_*` (порядок R1-R9 без изменений)
- `_import_sudoers_generator` shim (:1542) удаляется — прямые импорты
- `_is_stub` (:825-843) УДАЛЯЕТСЯ — projects.py использует shared/stub_detection (T4)
- `_render_sudoers_rules` вызов (:1714) → `sudoers_generator.render_sudoers_rules` (публичная, T3 CS-2)
- Инфра-вызовы → `infra.*`

### 4.3 Тесты

- tests/unit/test_reconciler.py: импорты `reconciler._is_stub` (:441/:459/:476) → stub_detection; `reconciler._unit_enabled` → infra; вызовы report/subprocess → infra
- tests/unit/test_reconciler_r7_volumes.py, test_reconciler_r8_sudoers.py, test_reconciler_r9_runtime.py: проверить импорты перенесённых функций
- tests/test_converge_exit.py:397 (`from reconciler import _is_stub`): → shared/stub_detection

## 5. T3 (U-07): deploy_orchestrator — публикация приватных API

### 5.1 7 функций брифа (переименование в home-модулях, публичные имена + контрактные докстринги)

| Приватная | Публичная | Модуль | Вызов из |
|-----------|-----------|--------|----------|
| `_validate_secret_charsets` | `validate_secret_charsets` | secrets_validator.py | deploy_orchestrator.py:268 |
| `_pre_pull_images` | `pre_pull_images` | docker_orchestrator.py | deploy_orchestrator.py:441 |
| `_batch_check_env` | `batch_check_env` | secrets_validator.py | deploy_orchestrator.py:448 |
| `_check_env_requires` | `check_env_requires` | secrets_validator.py | deploy_orchestrator.py:584 |
| `_batch_generate_sudoers` | `batch_generate_sudoers` | sudoers_generator.py | deploy_orchestrator.py:706 |
| `_batch_orphan_reconciliation` | `batch_orphan_reconciliation` | orphan_reconciler.py | deploy_orchestrator.py:718 |
| `_get_module_severity` | `get_module_severity` | secrets_validator.py | deploy_orchestrator.py:775 |

### 5.2 Находки consumer-scan (CS-1..CS-3)

| Приватная | Публичная | Модуль | Вызов из |
|-----------|-----------|--------|----------|
| `_extract_domains_for_context` | `extract_domains_for_context` | context_deployer.py | helpers/domains.py (ex-state_machine:1597) |
| `_render_sudoers_rules` | `render_sudoers_rules` | sudoers_generator.py | converge/sudoers.py (ex-reconciler:1714) |
| модуль `_topo_sort` | модуль `topo_sort` | bootstrap/_topo_sort.py → bootstrap/topo_sort.py | deploy_orchestrator.py:79, tests/test_deploy_modules.py |

### 5.3 deploy_orchestrator.py и __init__.py

- Убрать `as _x` алиасы (строки 80-98), вызывать публичные имена
- `core/internal/bootstrap/deploy/__init__.py`: экспорт публичного контракта через `__all__` — `orchestrate`, `DeployResult`, + 7 публичных функций (ре-экспорт из home-модулей)
- Внутримодульные приватные (orchestrate-пайплайн: `_preflight`, `_route_deploy`, `_parse_modules` и пр.) остаются приватными — вне скоупа гейта (гейт — только межмодульные импорты)

### 5.4 Тесты

- tests/unit/test_docker_orchestrator.py: `dorch._pre_pull_images` (5+ сайтов) → публичное имя
- tests/unit/test_deploy_orchestrator.py: `orch._route_deploy`/`_aggregate_severity`/`_compute_exit_code`/`_parse_modules` — внутримодульные, НЕ меняются
- rg-скан по каждому переименованному символу (secrets_validator/sudoers_generator/orphan_reconciler/context_deployer тесты)

## 6. T4 (U-28): is_stub + консолидация reconcile-канала

### 6.1 shared/stub_detection.py (NEW)

- `is_stub_ai_platform_yaml(path: str | Path) -> bool`: первая строка файла содержит "GENERATED-STUB"; False при missing/empty/OSError/IndexError (алгоритм — объединение reconciler_projects.py:146-166 и reconciler.py:825-843, поведение идентично)
- Потребители: reconciler_projects.py `is_stub_project(project_dir)` → тонкий делегирующий wrapper (публичный API сохраняется, тесты test_project_reconciler не ломаются); converge/projects.py — прямой вызов
- Удалить `_is_stub` из reconciler (после T2 — не переносится в projects.py)

### 6.2 reconcile-канал (D4)

- converge.sh:125-134: замена source-канала на прямой вызов (паттерн строки 119):
  ```bash
  if [[ "${CONVERGE_RECONCILE}" == "true" ]]; then
      local reconcile_script="${script_dir}/../reconciler_projects.py"
      local -a rec_cmd=("${CONVERGE_PYTHON:-python3}" "${reconcile_script}" "--node" "${CONVERGE_NODE}" "--node-yaml" "${NODE_YAML_PATH}")
      [[ -n "${NODE_HOST_MAP:-}" ]] && rec_cmd+=("--node-host-map" "${NODE_HOST_MAP}")
      [[ "${CONVERGE_DRY_RUN}" == "true" ]] && rec_cmd+=("--dry-run")
      "${rec_cmd[@]}" || { echo "[IMP:10][converge][main] Reconcile step failed" >&2; [[ 2 -gt $recon_rc ]] && recon_rc=2; }
  fi
  ```
- Удаление `core/internal/deploy/reconcile-projects.sh` (единственный потребитель — converge.sh, verified; node-lifecycle/bootstrap/deploy-modules не ссылаются)
- entrypoint-manifest.yaml:626 (scripts list `core/internal/deploy/reconcile-projects.sh`) — регенерация через `make generate-entrypoint-manifest` (или правка скрипта-генератора, если список статический — проверить)
- `--reconcile` флаг НЕ deprecate'ится — канал консолидирован, семантика прежняя

### 6.3 Тесты

- tests/test_stub_detection.py (CS-6): inline bash-копии _is_stub (3 теста) удаляются; новые unit-тесты shared/stub_detection (stub/real/missing/empty) в tests/unit/test_stub_detection_shared.py (или адаптация существующего файла)
- tests/test_converge_exit.py:397: `from reconciler import _is_stub` → `from core.internal.shared.stub_detection import is_stub_ai_platform_yaml`
- tests/unit/test_project_reconciler.py: без изменений (wrapper сохраняет API)

## 7. T5 (U-32): project_adopter — сплит ответственностей

### 7.1 Новые модули

**`core/internal/scaffold/compose_validator.py`** (NEW):
- `ValidationResult` dataclass (перенос :134-146)
- `validate_compose_networks(compose_path: Path) -> ValidationResult` (ex-:522)
- `try_parse_compose(compose_path: Path) -> dict | None` (ex-`_try_parse_compose` :560)
- `analyze_proxy_net(data: dict) -> tuple[bool, int, str]` (ex-`_analyze_proxy_net` :601)
- Логирование через собственный logger (module-level), LDD-логи переносятся

**`core/internal/scaffold/vhost_configurator.py`** (NEW):
- `configure_vhost(project_dir, name, domain, node, compose_profiles, ...) -> bool` (ex-:745)
- `update_yaml_for_vhost(...)` (ex-`_update_yaml_for_vhost` :786)
- `configure_vhost_via_subprocess(...)` (ex-`_configure_vhost_via_subprocess` :819)
- `resolve_node_configs_dir(...)` (ex-`_resolve_node_configs_dir` :870)
- Точные сигнатуры кодер выводит из текущих методов (явные параметры вместо self-полей: project_dir/name/domain/node/org/force/compose_profiles/yaml_file)

### 7.2 project_adopter.py после (~400-500 LOC)

- Класс ProjectAdopter: adopt()-оркестрация, YAML-генераторы (уже делегируют в scaffold_helpers), simplify_deploy_yml/delete_platform_deploy_yml, gen_env_platform/gen_project_makefile/gen_project_agents, register_in_node_yaml (делегирует), print_diff_report
- Удаление deprecated `_register_project_safe` (:701-725) и `_register_via_node_yaml` (:726-744) — 0 потребителей (CS-5)
- `_load_compose_profiles_from_platform_env` (:90-112) → `scaffold_helpers.load_compose_profiles_from_platform_env` (публичный, CS-4)
- Остаётся под гейтом ≤600 LOC

### 7.3 Тесты

- tests/unit/test_project_adopter.py (`import project_adopter as pa`): вызовы `pa.validate_compose_networks`/`pa._try_parse_compose` → compose_validator; `pa._load_compose_profiles_from_platform_env` → scaffold_helpers
- rg-скан по перенесённым символам

## 8. T6: Гейты волны (trinity: tests/gates/ + @pytest.mark.gate + auto-discover в entrypoint-manifest)

### 8.1 Гейт приватных межмодульных импортов

**`tests/gates/test_gate_no_private_cross_module_imports.py`** (NEW):
- ast-скан всех `core/internal/**/*.py` (кроме `__pycache__`):
  - (a) `from X import _name` — импорт имени с `_`-префиксом → violation (CS-3: `from core.internal.bootstrap import _topo_sort` должен быть уже переименован к моменту зелёного гейта)
  - (b) Attribute-доступ `X._attr(...)`/`X._attr`, где X — модуль, импортированный в этом файле (карта имя→модуль из Import/ImportFrom) → violation
- Исключения: `import re as _re`-алиасы (импортируется публичный модуль/функция, приватный только АЛИАС); stdlib; динамические импорты внутри функций покрываются теми же правилами
- Allowlist: ПУСТ (прецедент B8 D3 — строгий гейт)
- Гейт сканирует core/ (production), НЕ tests/ (white-box unit-тесты легитимно вызывают приватные внутримодульные функции)

### 8.2 LOC-гейт (allowlist)

**`tests/gates/test_gate_loc_allowlist.py`** (NEW):
- `ALLOWLIST = {"core/internal/bootstrap/lifecycle/state_machine.py": 1200, "core/internal/bootstrap/converge/reconciler.py": 800, "core/internal/scaffold/project_adopter.py": 600}` — превышение → RED
- Обоснование: acceptance B9 (1)(2)(5); остальные новые модули — под дефолтным check-file-lines 500 (non-blocking warning)

### 8.3 Манифесты

- `make generate-manifests` + `make check-manifests` (entrypoint-manifest gates auto-discover + scripts list без reconcile-projects.sh; core/AGENTS.md generated-секции при изменении цепочек)
- Обновить docstring-контракты: lifecycle/__init__.py, converge/__init__.py, bootstrap/AGENTS.md (структура lifecycle-пакета), core/AGENTS.md (при изменении канонической цепочки deploy — не ожидается)

## 9. T7: Самоверификация (порядок)

1. `make fix-gate && git add -u`
2. `make gate MODE=fast` — зелёный
3. Таргетированный прогон: `pytest tests/unit/test_state_machine.py tests/unit/test_reconciler.py tests/unit/test_reconciler_r7_volumes.py tests/unit/test_reconciler_r8_sudoers.py tests/unit/test_reconciler_r9_runtime.py tests/unit/test_project_adopter.py tests/unit/test_project_reconciler.py tests/unit/test_deploy_orchestrator.py tests/unit/test_docker_orchestrator.py tests/unit/test_sudoers_generator.py tests/unit/test_orphan_reconciler.py tests/unit/test_secrets_validator.py tests/test_converge_exit.py tests/gates/` — зелёный
4. `make check-manifests` — зелёный
5. LOC-гейт + private-import-гейт — зелёные (шаг 2/3 покрывает)
6. **e2e bootstrap: `make test-node NODE=<test>`** — обязателен по брифу («e2e-прогон (make test-node) обязателен»); если test-VPS недоступен — задокументировать BLOCKED, эскалация на оператора

## 10. Критерии приёмки (маппинг на бриф)

| AC брифа | Где проверяется |
|----------|-----------------|
| (1) state_machine ≤ 1200: оркестрация + persistence; I/O в phases/helpers (→ lifecycle/helpers/); цикл phases↔state_machine устранён | T1 + T6.2 (гейт) + T7.3 |
| (2) reconciler: домены R1-R9, sudoers, vhosts, volumes | T2 + T6.2 (reconciler ≤ 800) |
| (3) deploy_orchestrator: 7 функций опубликованы через __init__ / публичные API | T3 + T6.1 (гейт, 0 violation) |
| (4) is_stub — одна функция в shared; reconcile-каналы консолидированы | T4 + T6.1 |
| (5) project_adopter: YAML через scaffold_helpers (дубль удалён — уже делегировано), compose-валидация и vhost-логика вынесены | T5 + T6.2 (≤ 600) |
| (6) все тесты зелёные; e2e bootstrap не регрессировал | T7 |

## 11. Файловый манифест

**NEW:** `lifecycle/state_store.py`, `lifecycle/cli.py`, `lifecycle/helpers/{__init__,subprocess_io,system,users,secrets,validation,domains,reporting}.py`, `converge/{infra,perms,audit,projects,networks,vhosts,volumes,sudoers,runtime}.py`, `scaffold/{compose_validator,vhost_configurator}.py`, `shared/stub_detection.py`, `tests/gates/test_gate_no_private_cross_module_imports.py`, `tests/gates/test_gate_loc_allowlist.py`, `tests/unit/test_stub_detection_shared.py`

**MODIFIED:** `lifecycle/state_machine.py` (→ ~950), `lifecycle/phases.py` (импорты helpers), `lifecycle/__init__.py`, `converge/reconciler.py` (→ ~250), `converge/__init__.py`, `bootstrap/converge.sh` (прямой вызов), `deploy/deploy_orchestrator.py`, `deploy/secrets_validator.py`, `deploy/docker_orchestrator.py`, `deploy/sudoers_generator.py`, `deploy/orphan_reconciler.py`, `deploy/context_deployer.py`, `deploy/__init__.py`, `scaffold/project_adopter.py`, `scaffold/scaffold_helpers.py`, `reconciler_projects.py`, `node-lifecycle.sh`, `core/entrypoint-manifest.yaml` (generated), `core/internal/bootstrap/AGENTS.md`, `core/AGENTS.md` (generated-секции при необходимости), `tests/unit/{test_state_machine,test_reconciler,test_reconciler_r7_volumes,test_reconciler_r8_sudoers,test_reconciler_r9_runtime,test_project_adopter,test_project_reconciler,test_deploy_orchestrator,test_docker_orchestrator,test_sudoers_generator,test_orphan_reconciler,test_secrets_validator}.py`, `tests/test_converge_exit.py`, `tests/test_deploy_modules.py`, `tests/test_stub_detection.py` (или удаление)

**RENAMED:** `bootstrap/_topo_sort.py` → `bootstrap/topo_sort.py`

**DELETED:** `deploy/reconcile-projects.sh`, deprecated-регионы project_adopter, `RUN_STEPS` region state_machine

## 12. Риски и митигации

| Риск | Митигация |
|------|-----------|
| Динамический импорт phases в execute_phase сломается при рефакторинге | Строка 797-813 НЕ трогается; тесты test_state_machine (init/update mode) покрывают |
| Модульные глобалы report/exit в reconciler при переезде в infra.py | Перенос ГЛОБАЛОВ вместе с функциями в infra.py; тесты на reset (test_reconciler) зелёные |
| Большой дифф тестов с sys.path-хаками (test_converge_exit, test_project_adopter) | Consumer-scan до каждого перемещения (инвариант 3); точечные правки импортов |
| LOC-гейт 1200 при будущих правках state_machine | Запас: реальный LOC ~950 (D2 — persistence вынесена) |
| R3 (local stub create) vs reconciler_projects (remote stub deploy) — соблазн консолидировать | Не консолидировать (инвариант 4) — ортогональные домены, задокументировано |
| e2e test-node недоступен (нет test-VPS) | BLOCKED-документация + эскалация (T7.6), остальные гейты зелёные |

$START_DEVPLAN
$END_DEVPLAN
