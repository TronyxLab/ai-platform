# 22-DevPlan — B10: Тестовый хардненинг (Test Honesty R1-R5)

<!-- GREP_SUMMARY: test-hardening assert-True R1-gate grep-asserts contract-tests mock-boundary parametrize conftest-lazy networks-race BootstrapPhase pyyaml-skipif ldd-consolidation stale-TRAP e2e-test-node inventory -->
<!-- STRUCTURE: ┌решения D1-D3┐ → ◇ T1 R1-гейт → ◇ T2 контрактные замены → ◇ T3 mock-сокращение → ◇ T4 дубли → ◇ T5 conftest → ◇ T6 enum → ◇ T7 skipif → ◇ T8 LDD → ◇ T9 манифесты → ⊕ T10 самоверификация -->
# region MODULE_CONTRACT
## @purpose  Волна B10 программы хардненинга (116): привести тестовую базу к Test Honesty (R1-R5) — поведение вместо реализации, контракты вместо grep, enforcement-гейт против возврата pass-тестов.
## @scope    U-69..U-77, U-81. Файлы: tests/gates/test_gate_r1_no_pass_tests.py (новый), tests/gates/test_gate_fixture_schema.py, tests/unit/test_llm_policy_schema.py, tests/test_cert_backup_gap.py, tests/test_node_lifecycle_static.py, tests/test_ssl_s3_cache.py, tests/unit/test_deploy_engine.py, tests/test_status_page.py, tests/test_platform_export_metrics.py, tests/test_unit_validate_dora_dashboard.py (удаляется), tests/test_unit_vps_status_check.py (удаляется), tests/_conftest/{infra,networks,node,ldd}.py, tests/unit/test_project_adopter.py, tests/test_add_vhost.py, tests/test_inventory.yaml (generated), core/entrypoint-manifest.yaml, tests/AGENTS.md, tests/e2e/README.md, tests/.
## @invariants
##   1. R1: 0 assert True / 0 константных assert в tests/ — новый ast-гейт (RED при появлении).
##   2. Grep-ассерты на Python-модули заменяются native-поведенческими (импорт + вызов + результат);
##      shell-фасады — dry-run запуском (гибрид, D2); код-присутствие остаётся только там, где
##      dry-run не покрывает контракт (канон инварианта 2: gate проверяет КОД, не комментарий).
##   3. Моки — только на границе I/O (subprocess.run, docker CLI); методы под тестом реальные;
##      ассерты на observable-результат, 0 call_args_list (D1).
##   4. Канонические хелперы (ldd) — один импорт, 0 копий.
##   5. Generated files (test_inventory.yaml, entrypoint-manifest.yaml) — только через генераторы.
## @rationale Бриф 11-Brief фиксирует цели (U-69..U-78, U-81); DevPlan фиксирует решения пользователя
##            (D1-D3, 2026-08-01) и исполнительные шаги с точными файлами. Подтверждённые факты:
##            U-69 фактически сжат B7 (19 assert True из renderer удалены 2026-08-01) — осталось 2
##            реальных (fixture_schema:48, llm_policy_schema:273); U-78 уже закрыт (LDD 94%, 324/344);
##            U-77 фактически 16 копий (не 14); @patch deploy_engine = 34 (не 37); s3_ssl_cache —
##            Python-модуль с существующим unit-покрытием — grep-дубли избыточны; test_add_vhost.py
##            7 passed (TRAP[DEBT]:29 stale); infra.py:271 — subprocess при импорте через __new__;
##            networks.py:90 — TRAP[DEBT] гонки teardown; BootstrapPhase — state_machine.py:96;
##            e2e-набор 11 requires_node (bootstrap_pipeline 8 + failure_scenarios 3), ci.mk:107.
## @changes 2026-08-01 · Решения пользователя (question 2026-08-01): (D1) U-71 — граничные моки +
## @changes  SUPERSEDED 2026-08-01 — закрыт волнами 116; VR не требуется (D5, DevPlan 116 B11 T8 U-84) — 22-DevPlan.md
##           parametrize, ≤5 @patch на файл через общую фикстуру, prod-код не меняется; (D2) U-70 —
##           гибрид: Python native, shell dry-run (bash-субпроцесс, прецедент test_lib_ssh),
##           код-присутствие где dry-run не покрывает; (D3) U-72 — e2e прогон сейчас:
##           make test-node NODE=test-e2e в T10 (VPS доступен).
# endregion MODULE_CONTRACT

$START_DEVPLAN
$ARTIFACT_CONTRACT:
  PURPOSE: Реализация волны B10 — честная тестовая база: 0 pass-тестов (enforcement-гейт), контрактные тесты вместо grep на исходники, моки только на границе I/O, консолидация дублей и хелперов, стабилизация conftest.
  DESCRIPTION: Новый ast-гейт R1 (assert True/константы/bare-pass в except → RED) + фикс 2 оставшихся pass-тестов; контрактные замены grep-ассертов (cert_orchestrator, phases/domains, ssl_s3_cache — native; node-lifecycle/node-update, issue-cert — dry-run); рефакторинг test_deploy_engine.py на граничную фикстуру ≤5 @patch + parametrize; mock-сокращение status_page/export_metrics; слияние дублей Dora/VPS (unit/ побеждает, root-версии удаляются); ленивая инициализация infra (без subprocess при импорте) + хардненинг сетевых гонок; node.py на BootstrapPhase enum; снятие 5 PyYAML-skipif; консолидация 16 копий _print_ldd_trajectory в 1 импорт; удаление stale TRAP; регенерация манифестов + e2e-прогон make test-node NODE=test-e2e.
  RATIONALE: RC6: тесты фиксируют реализацию (grep на исходники, mock call_args_list), блокируют легитимный рефакторинг и консервируют мёртвый код. U-69: pass-тесты не фальсифицируемы (R1-RED по собственным правилам проекта). U-70: grep-ассерты на Python-модули дублируют unit-покрытие (s3_ssl_cache) или проверяют подстроки вместо поведения. U-71: 34 @patch фиксируют внутреннюю структуру, не результат. U-74: subprocess при импорте infra замедляет статические сессии и ломает изоляцию.
  ACCEPTANCE_CRITERIA: (1) 0 assert True в tests/ — гейт R1 (ast-скан) зарегистрирован (trinity), RED при регрессе; (2) test_monitoring_config_renderer — реальные ассерты (уже выполнено B7 — verify); (3) grep-ассерты заменены поведенческими: ssl_s3_cache (unit, дубли удалены), cert_orchestrator (вызов orchestrate_certs), node_lifecycle (dry-run запуск); (4) test_deploy_engine.py ≤5 @patch на файл, 0 call_args_list в 3 файлах, сценарии сохранены (parametrize); (5) дубли Dora/VPS слиты — один файл на модуль; (6) infra: 0 subprocess при импорте (lazy), сетевая гонка задокументирована/исправлена; (7) node.py импортирует BootstrapPhase; (8) PyYAML-skipif снят (5); (9) ровно 1 def _print_ldd_trajectory (16 копий → импорт); (10) stale TRAP test_add_vhost:29 удалён; (11) e2e (11 requires_node) зелёный через make test-node NODE=test-e2e (прогон в волне, D3); (12) LDD-покрытие ≥ 80% (фактически 94% — verify, не расширять).
  IMPLEMENTS: U-69 (pass-тесты), U-70 (grep-ассерты), U-71 (mock-heavy), U-72 (e2e локальный прогон), U-73 (дубли), U-74 (conftest), U-75 (фазы строками), U-76 (skipif), U-77 (ldd ×16), U-81 (stale TRAP)
  IMPACTS: tests/ (unit, gates, root-static, e2e, _conftest/), core/entrypoint-manifest.yaml, tests/test_inventory.yaml (generated), tests/AGENTS.md, tests/e2e/README.md
  REQUIRES: B8 (17-DevPlan — удаление мёртвого кода вместе с консервирующими тестами), B6/B9 (новые границы фиксируются контрактными тестами); решения пользователя 2026-08-01 (D1-D3); чистое рабочее дерево на старте (пользователь коммитит перед началом)
$END_ARTIFACT_CONTRACT

---

## 1. Решения пользователя (подтверждены 2026-08-01)

| D | Вопрос | Решение |
|---|--------|---------|
| D1 | U-71: глубина mock-сокращения deploy_engine (34 @patch, ~20 тест-функций) | **Граничные моки + parametrize.** Рефакторинг тест-файла: одна общая фикстура `deploy_boundary` с ≤5 @patch (subprocess.run + 4 shared docker-compose хелпера на границе I/O), сценарии (success / health-fail / rollback / pull-fail / first-deploy / compose-ps-пуст) через parametrize, методы DeployEngine реальные, ассерты на DeployResult.status / rollback-флаги / ImageInfo. 0 call_args_list. Prod-код НЕ меняется (DI-инъекция отклонена — больший churn без выгоды на границе). |
| D2 | U-70: grep-ассерты на shell-фасады | **Гибрид.** Python-модули (phases.py, domains.py, cert_orchestrator.py, s3_ssl_cache.py) → native-поведенческие (импорт + вызов + ассерт результата). Shell-фасады (node-lifecycle.sh флаги, node-update.sh forwarding, issue-cert.sh fallback) → поведенческий dry-run: bash-субпроцесс в тесте (прецедент test_lib_ssh.py). Код-присутствие остаётся ТОЛЬКО там, где dry-run не покрывает контракт (канон инварианта 2: gate проверяет КОД) — с явным комментарием-обоснованием. |
| D3 | U-72: e2e-прогон (11 requires_node) | **Прогнать сейчас.** В T10 выполняется `make test-node NODE=test-e2e` (тестовый VPS доступен, AGE-ключ на месте). Результат фиксируется в DevPlan; при недоступности VPS в момент прогона — пометка в отчёте QA + повтор после волны (manual-шаг). |

## 2. Текущее состояние worktree (старт волны)

- `assert True` в tests/: **2 реальных кода** (остальные 12 — комментарии/docstrings): `test_gate_fixture_schema.py:48` (`assert True  # Explicit assertion to satisfy Test Honesty R1` — хвост после реальных ассертов + `_validate_test_fixtures()` с pytest.exit), `test_llm_policy_schema.py:273` (`assert True, "Exception was raised as expected"` — хвост в pytest.raises-блоке). **U-69 частично закрыт B7** (2026-08-01: удалены 19 хвостовых assert True из test_monitoring_config_renderer; CLI-тест вынесен в test_render_monitoring_cli.py).
- R1-гейта нет: `rg "assert True" tests/gates/` → только сам тест fixture_schema (нарушитель) и docstring в negative-гейте litellm.
- Grep-ассерты на исходники (U-70): `test_cert_backup_gap.py` — 329-342 (issue-cert.sh: s3_ssl_cache/upload/WARN), 386-396 (cert_orchestrator.py: _is_cert_valid/_upload_to_s3/import s3_ssl_cache/issue_cert_script), 463, 566-582 (domains.py: ssl_provision_via_orchestrator); `test_node_lifecycle_static.py` — 169-173 (node-lifecycle.sh/node-update.sh: --node-name/--dry-run), 410-428 (phases.py/domains.py: phase_certificates/ssl_provision_via_orchestrator/cert_orchestrator); `test_ssl_s3_cache.py` — 387-444 (s3_ssl_cache.py: upload_cert/download_cert/check_cert/bulk_restore/openssl/boto3 — **Python-модуль, unit-покрытие УЖЕ существует**: tests/unit/test_s3_ssl_cache.py: upload/download/check/bulk_restore/CLI через monkeypatch → grep-блок избыточен).
- Mock-heavy (U-71): `test_deploy_engine.py` — **34 @patch** (~20 тест-функций; патчи на внутренние методы _preflight_checks/_save_previous_image/_atomic_up/_perform_rollback + shared-хелперы _shared_retry_pull/_shared_healthcheck_poll/_shared_docker_compose_*); `test_status_page.py` — 16 `mock.patch("subprocess.run")`; `test_platform_export_metrics.py` — 20 mock/patch-вхождений.
- Дубли (U-73): `tests/test_unit_validate_dora_dashboard.py` (root, subprocess-CLI, 7 тестов) vs `tests/unit/test_validate_dora_dashboard.py` (native + CLI, 8 тестов); `tests/test_unit_vps_status_check.py` (root, subprocess-CLI) vs `tests/unit/test_vps_status_check.py` (native + CLI). Root-версии — legacy-двойники.
- conftest (U-74): `infra.py:271` — module-level `infra = _TestInfra()` → `__new__` → `_load_test_infra()` → **subprocess discover_modules.py при импорте** (T21-протокол `from _conftest.infra import infra` сохранён, но subprocess неизбежен); `networks.py:90` — TRAP[DEBT] 2026-07-15: parallel teardown destroys shared external networks; `docker network rm` в _conftest НЕ найден — гонка между compose down параллельных сессий (сеть создана одним проектом, удалена его teardown'ом, второй проект падает «network declared as external, but could not be found»).
- node.py (U-75): `_conftest/node.py:46-63` — INIT_PHASES (9)/UPDATE_PHASES (5) как `list[str]` литералов; канон `BootstrapPhase` — `core/internal/bootstrap/lifecycle/state_machine.py:96` (enum; state.json-ключи = значения enum).
- PyYAML skipif (U-76): `test_project_adopter.py:107,148,183,424,453` — 5× `@pytest.mark.skipif(not _has_yaml_module(), reason="PyYAML not available")` + хелпер; PyYAML — hard dep (импортируется core-модулями).
- LDD (U-77): **16 локальных `def _print_ldd_trajectory`** + канон `_conftest/ldd.py:34` (сигнатура `(caplog)`; ~9 файлов используют вариант с `test_name`-префиксом — канон надо расширить опциональным параметром).
- LDD-покрытие (U-78): **94%** (324/344 тестовых файлов с IMP:9) — AC12 ≥80% уже выполнен, расширение НЕ требуется (verify в T10).
- Stale TRAP (U-81): `test_add_vhost.py:29` — TRAP[DEBT] 2026-07-31 «Все 7 тестов этого файла падают… gate MODE=fast RED (83 failures)» — **проверено: 7 passed** (`python3 -m pytest tests/test_add_vhost.py`), TRAP stale.
- e2e (U-72): 11 requires_node-тестов (test_bootstrap_pipeline.py 8 + test_failure_scenarios.py 3), `makefiles/ci.mk:107` test-node (NODE env → pytest.fail по R4), исключены из `make test`/`make gate` (фильтр not requires_node), tests/e2e/README.md существует. Прогон требует NODE=test-e2e + AGE_SECRET_KEY + SSH-доступ.
- Манифесты: `tests/test_inventory.yaml` (generated) — регенерируется `make test-inventory-sync` (обязателен после удаления файлов T4); `core/entrypoint-manifest.yaml` gates — регистрация нового гейта T1 (trinity).

## 3. Задачи

### T1 — U-69: ast-гейт R1 + фикс 2 оставшихся pass-тестов [CRITICAL]

**Файлы:** `tests/gates/test_gate_r1_no_pass_tests.py` (новый), `tests/gates/test_gate_fixture_schema.py` (:48), `tests/unit/test_llm_policy_schema.py` (:273), `core/entrypoint-manifest.yaml` (gates — T9), `tests/gates/AGENTS.md` (инвентарь — T9)

**Шаги:**

1. **Новый гейт** `test_gate_r1_no_pass_tests.py` (@pytest.mark.gate + trinity, REPAIR: repair_class L2 — ручная правка теста):
   - ast-скан всех `tests/**/*.py`, исключая `tests/_conftest/`, `tests/helpers/`, `tests/tools/`, `tests/test_data/`, `tests/e2e/fixtures/` (не тестовые модули);
   - RED при: (а) `assert` с константным выражением (`True`/`False`/`None`/число/строка/кортеж констант — `ast.Constant`/`ast.NameConstant`/`ast.Tuple` of constants); (б) except-блок с bare `pass` (`except:`/`except X:` → `pass`); (в) файл-тест без единого assert;
   - allowlist пуст (строгий режим, паттерн B8 D3); негатив (R5): инлайн-фикстура с `assert True` → RED;
   - docstring со ссылкой на R1 (.kilo/rules/testing.md) и 11-Brief AC1.
2. **Фикс** `test_gate_fixture_schema.py:48` — удалить хвостовой `assert True` (тест уже имеет реальные ассерты :40 + `_validate_test_fixtures()` завершает pytest.exit при провале).
3. **Фикс** `test_llm_policy_schema.py:273` — заменить `assert True, "Exception was raised as expected"` на реальный ассерт по `exc_info.value` (тип исключения + поле/сообщение про missing aliases).
4. **Учёт**: `test_cert_collector.py`/`test_yaml_query.py` и пр. содержат слово «assert True» только в STRUCTURE-комментариях — гейт сканирует код, не docstring (явное исключение `ast.Expr` vs комментарии).

**Критерий:** `rg "assert True" tests/` = 0 в коде (комментарии допускаются — гейт их игнорирует); гейт зелёный; негатив-тест доказывает детект; 2 фикса зелёные.

### T2 — U-70: контрактные замены grep-ассертов (гибрид, D2) [CRITICAL]

**Файлы:** `tests/test_ssl_s3_cache.py` (387-444 удаляются), `tests/unit/test_s3_ssl_cache.py` (расширение при пробелах), `tests/test_cert_backup_gap.py` (329-342, 386-396, 463, 566-582), `tests/unit/test_cert_orchestrator_contract.py` (новый), `tests/unit/test_phase_certificates_contract.py` (новый), `tests/test_node_lifecycle_static.py` (169-173, 410-428), `core/internal/bootstrap/lifecycle/{phases.py,helpers/domains.py}`, `core/internal/bootstrap/cert_orchestrator.py` (read-only, контракт)

**Шаги:**

1. **s3_ssl_cache (Python)**: grep-блок 387-444 **удаляется** — дублирует существующий unit-файл (upload/download/check/bulk_restore/CLI). Проверить пробелы unit-покрытия (openssl-checkend-путь, «non-fatal» return False) — при пробеле добавить 1-2 теста в `tests/unit/test_s3_ssl_cache.py`, не возвращать grep.
2. **cert_orchestrator (Python)**: grep 386-396 → native `tests/unit/test_cert_orchestrator_contract.py`:
   - импорт `orchestrate_certs`; monkeypatch `s3_ssl_cache` (upload/download/check_cert) + `issue_cert_script` (заглушка-скрипт в tmp_path);
   - сценарии: сертификат валиден на диске → SKIP upload (restore-first, `_is_cert_valid`); нет на диске → download из S3; S3 miss → issue-cert fallback; S3 fail → WARN, не raise (non-fatal);
   - ассерты на результат (файл создан/статус/флаг fallback), IMP:9-логи.
3. **phases.py / helpers/domains.py (Python)**: grep 410-428 → native `tests/unit/test_phase_certificates_contract.py`:
   - импорт `phase_certificates` (phases.py) и `ssl_provision_via_orchestrator` (helpers/domains.py); контракт: hasattr + signature (экстракция доменов `extract_domains`, делегирование `orchestrate_certs`); где вызов дорог (phase-оркестрация) — introspection AST/`inspect.getsource` запрещён: только импорт + вызов с фейковым контекстом (fixture tmp + monkeypatch helpers).
4. **node-lifecycle.sh / node-update.sh (shell, D2)**: grep 169-173 (--node-name/--dry-run) → поведенческий dry-run:
   - `subprocess.run(["bash", NODE_LIFECYCLE, "--mode", "init", "--dry-run", "--node-name", "test-node", "--node-yaml", <tmp>], ...)` → exit 0; `--node` alias → тот же результат (forwarding-контракт);
   - node-update.sh forwarding: dry-run с флагами → exit 0;
   - прецедент: `tests/test_lib_ssh.py` (субпроцесс shell в тестах — канон для фасадов);
   - PYTHONPATH-настройка как в самом скрипте (TRAP[BUG] 2026-07-31: `${SCRIPT_DIR}/../../..:${SCRIPT_DIR}/lifecycle`).
5. **issue-cert.sh (shell, D2)**: fallback-контракт в cert_orchestrator — покрыт native-тестом T2 п.2 (fallback-ветка через заглушку-скрипт); grep-ассерты 329-342/463 (WARN/upload в issue-cert.sh) — заменить на dry-run issue-cert.sh если у скрипта есть dry-run/проверяемый режим; иначе код-присутствие с комментарием-обоснованием (D2-оговорка).

**Критерий:** 0 grep-ассертов (`in content` на исходники) по Python-модулям; shell-фасады — dry-run; `rg "def upload_cert" tests/` (в grep-контексте) = 0; новые unit-файлы зелёные (native, tmp_path, monkeypatch).

### T3 — U-71: mock-сокращение — граничные моки + parametrize (D1) [CRITICAL]

**Файлы:** `tests/unit/test_deploy_engine.py` (rewrite), `tests/test_status_page.py`, `tests/test_platform_export_metrics.py`

**Шаги:**

1. **test_deploy_engine.py (D1)** — полный рефакторинг:
   - одна файловая фикстура `deploy_boundary` (autouse): ≤5 @patch — `subprocess.run` + `_shared_retry_pull` + `_shared_healthcheck_poll` + `_shared_docker_compose_up` + `_shared_docker_compose_ps` (граница I/O docker CLI); методы DeployEngine (`_preflight_checks`, `_save_previous_image`, `_atomic_up`, `_perform_rollback`) — РЕАЛЬНЫЕ;
   - сценарии через parametrize (сохранить эквивалент текущего набора): success, first-deploy (prev=None), health-fail → rollback success, health-fail → rollback fail, pull-fail → abort, images-список (status/image-проверки), compose-ps (status-контракт);
   - ассерты ТОЛЬКО на observable-результат: `DeployResult.status`, rollback-флаги, `ImageInfo`-поля, exit-коды; **0 `call_args_list`**;
   - сравнить список тест-функций/сценариев до/после (в @changes) — потери недопустимы.
2. **test_status_page.py**: 16 `mock.patch("subprocess.run")` → одна фикстура `mock_subprocess` с side_effect-диспетчером по аргументам; ассерты на результат (rendered HTML, status-поля), не на вызовы.
3. **test_platform_export_metrics.py**: аналогично (общая фикстура, ассерты на stdout/структуру вывода, exit).
4. **LDD**: каждый сценарий сохраняет IMP:9-логи (существующие logger-вызовы в prod-коде не трогаются).

**Критерий:** `rg "@patch|mock.patch" tests/unit/test_deploy_engine.py` ≤ 5 (итог файла); `rg "call_args_list"` в 3 файлах = 0; `python3 -m pytest tests/unit/test_deploy_engine.py tests/test_status_page.py tests/test_platform_export_metrics.py` зелёные (эквивалентный набор сценариев).

### T4 — U-73: слияние дублей Dora/VPS — один файл на модуль [FUNDAMENT]

**Файлы:** `tests/test_unit_validate_dora_dashboard.py` (удаляется), `tests/test_unit_vps_status_check.py` (удаляется), `tests/unit/test_validate_dora_dashboard.py`, `tests/unit/test_vps_status_check.py`, `tests/test_inventory.yaml` (регенерация — T9)

**Шаги:**

1. **Consumer-scan**: diff root vs unit версий — сценарии, покрытые ТОЛЬКО в root (subprocess-CLI exit-коды, capsys-сценарии, parse_status_json вариации); перенести недостающие в unit/-версии (unit/ уже содержит native + CLI-субпроцесс — см. STRUCTURE-комментарии).
2. Удалить оба root-файла (`tests/test_unit_*.py`).
3. `make test-inventory-sync` (T9) — inventory без удалённых файлов; `test_gate_test_inventory` зелёный.

**Критерий:** `ls tests/test_unit_validate_dora_dashboard.py tests/test_unit_vps_status_check.py` = not found; по одному файлу на модуль в unit/; покрытие сценариев сохранено (сравнение списков тестов до/после в @changes).

### T5 — U-74: conftest — ленивый infra + сетевые гонки [FUNDAMENT]

**Файлы:** `tests/_conftest/infra.py` (:271), `tests/_conftest/networks.py` (:90), `tests/AGENTS.md` (T16/T21-протокол), `tests/conftest.py` (проверка), `tests/unit/test_infra_lazy.py` (новый)

**Шаги:**

1. **infra.py:271 — ленивая инициализация**:
   - `infra` → lazy-прокси (PEP 562 `__getattr__` модуля ИЛИ прокси-класс с `__getattr__`-делегированием), подпроцесс `discover_modules.py --test-infra` запускается при ПЕРВОМ обращении к accessor-методу, НЕ при импорте;
   - протокол импорта T21 (`from _conftest.infra import infra`) сохраняется без изменений у потребителей;
   - `_load_test_infra` кэш остаётся (один subprocess на сессию).
2. **networks.py:90 — teardown-гонка**:
   - `ensure_external_networks` → verify-цикл: `docker network inspect` → если отсутствует → `create` → повторный inspect (устойчивость к гонке create/remove);
   - зафиксировать контракт: общие тестовые сети — `external: true` в тестовых compose и НИКОГДА не удаляются в teardown (docker network rm в тестах запрещён; проверить отсутствие — уже подтверждено);
   - TRAP[DEBT] 2026-07-15 обновляется: RESOLVED-частично (hardening + документация), причина (parallel compose down) зафиксирована.
3. **Тест** `tests/unit/test_infra_lazy.py`: импорт `_conftest.infra` БЕЗ вызова accessor'а не запускает subprocess (monkeypatch `subprocess.run`/`_load_test_infra` → вызовов 0); первый accessor-вызов — ровно 1 запуск; повторные — кэш.
4. **tests/AGENTS.md**: T16/T21-раздел обновляется («infra — lazy: subprocess при первом доступе, не при импорте; static-сессии без Docker не запускают discover_modules»).

**Критерий:** `python3 -m pytest tests/unit/test_infra_lazy.py` зелёный; `python3 -c "import _conftest.infra"` не порождает subprocess (замер/mock); полный прогон unit зелёный (ни один тест не полагается на eager-инициализацию).

### T6 — U-75: node.py — BootstrapPhase enum вместо литералов [FUNDAMENT]

**Файлы:** `tests/_conftest/node.py` (:46-63), `core/internal/bootstrap/lifecycle/state_machine.py` (:96, read-only/минимальное расширение)

**Шаги:**

1. `INIT_PHASES`/`UPDATE_PHASES` — заменить литеральные `list[str]` на значения enum: `INIT_PHASES = [p.value for p in BootstrapPhase if ...]` или константы из enum (единый канон state.json-ключей); проверить соответствие значений (system_bootstrap … converge_services / secrets_update … converge_update).
2. Если enum не покрывает нужные фазы/атрибуты (mode init/update классификация) — минимальное обратно-совместимое расширение enum (это канон, prod-польза выше); иначе — только импорт.
3. `phases()` (node.py:266-271) использует enum-списки без изменения семантики.

**Критерий:** 0 литеральных строк фаз в node.py (кроме ссылок на enum-значения); unit-проверка: enum-значения == state.json-ключи (тест в _conftest-потребителе или tests/unit/test_node_phases_enum.py — маленький, native).

### T7 — U-76: PyYAML skipif ×5 — снятие [FUNDAMENT]

**Файлы:** `tests/unit/test_project_adopter.py` (:107,148,183,424,453 + `_has_yaml_module`)

**Шаги:**

1. Удалить 5 декораторов `@pytest.mark.skipif(not _has_yaml_module(), reason="PyYAML not available")` и хелпер `_has_yaml_module` (PyYAML — hard dep: yaml импортируется core-модулями; skip = unfalsifiable, R2).
2. Проверить: 5 тестов выполняются (не skipped) в прогоне; docstring-упоминания актуализировать.

**Критерий:** `rg "skipif|_has_yaml_module" tests/unit/test_project_adopter.py` = 0; прогон: 5 тестов passed (0 skipped).

### T8 — U-77: LDD-консолидация — 16 копий → 1 импорт [FUNDAMENT]

**Файлы:** `tests/_conftest/ldd.py` (:34, расширение сигнатуры), 16 файлов с локальными def: `tests/unit/test_dev_cert_generator.py`, `tests/test_lib_ssh.py`, `tests/test_env_contract.py`, `tests/unit/test_remote_executor.py`, `tests/unit/test_monitoring_config_renderer.py`, `tests/unit/test_llm_policy_schema.py`, `tests/unit/test_llm_env_chain.py`, `tests/unit/test_llm_config_renderer_integration.py`, `tests/unit/test_render_monitoring_cli.py`, `tests/unit/test_llm_key_provisioner.py`, `tests/unit/test_project_reconciler.py`, `tests/unit/test_llm_config_renderer.py`, `tests/integration/test_bootstrap_dry_run.py`, `tests/gates/test_gate_llm_provisioner.py`, `tests/gates/test_gate_make_contract.py`, `tests/gates/test_gate_llm_aliases.py`

**Шаги:**

1. Канон `_conftest/ldd.py:34` — расширить: `_print_ldd_trajectory(caplog, test_name: str | None = None)` — опциональный `test_name` для префикса траектории (совместимость с ~9 файлами, использующими именованный вывод).
2. В 16 файлах: удалить локальные def, добавить `from _conftest.ldd import _print_ldd_trajectory` (паттерн импорта как у `_conftest.infra`, tests/AGENTS.md T21); вызовы с `test_name` сохраняются.
3. `tests/_conftest/node.py:444` (`assert_ldd_imp9_e2e`) — НЕ трогается (отдельная E2E-семантика, задокументирована).

**Критерий:** `rg "^def _print_ldd_trajectory" tests/` = 1 (канон); `rg "from _conftest.ldd import" tests/` = 16 (по файлу); полный прогон affected-файлов зелёный (сигнатура совместима).

### T9 — U-81 + манифесты + e2e-документация [FUNDAMENT]

**Файлы:** `tests/test_add_vhost.py` (:29 TRAP удаляется), `core/entrypoint-manifest.yaml` (gates: r1_no_pass_tests), `tests/test_inventory.yaml` (регенерация T4), `tests/gates/AGENTS.md` (инвентарь T1), `tests/AGENTS.md` (T5), `tests/e2e/README.md` (чек-лист CI-preflight)

**Шаги:**

1. **test_add_vhost.py:29** — stale TRAP[DEBT] «Все 7 тестов падают» удаляется (проверено: 7 passed; TRAP-история не нужна — B8-гейт фантомов не применяется к TRAP-комментариям, но stale-предупреждение о провале вводит в заблуждение).
2. **entrypoint-manifest.yaml gates** — регистрация `r1_no_pass_tests` (trinity T1): `make generate-entrypoint-manifest`; repair-поля: repair_class L2 (ручная правка теста), repair_description «удалить assert True/константный assert или bare-pass в except».
3. **test_inventory.yaml**: `make test-inventory-sync` после T4 (удаление 2 файлов) и любых переименований — `test_gate_test_inventory` зелёный.
4. **tests/e2e/README.md** — чек-лист CI-preflight: шаг «локальный прогон e2e: `make test-node NODE=test-e2e`» (D3; без CI-джобы — решение пользователя 11-Brief AC11).

**Критерий:** TRAP удалён; `make check-manifests` 0 diff; гейт зарегистрирован (trinity); inventory синхронен.

### T10 — Самоверификация волны [GATE]

**Файлы:** новые/изменённые тесты T1-T8, entrypoint-manifest.yaml, test_inventory.yaml

**Шаги (строго по порядку):**

1. **Регенерация манифестов**: `make generate-entrypoint-manifest` + `make test-inventory-sync` → `git diff` — только ожидаемые изменения (T9); `make check-manifests`.
2. **Гейт волны**: `python3 -m pytest tests/gates/test_gate_r1_no_pass_tests.py -m gate` зелёный + негатив (assert True в фикстуре → RED).
3. **Unit-тесты волны**: `python3 -m pytest tests/unit/test_deploy_engine.py tests/unit/test_cert_orchestrator_contract.py tests/unit/test_phase_certificates_contract.py tests/unit/test_s3_ssl_cache.py tests/unit/test_llm_policy_schema.py tests/unit/test_project_adopter.py tests/unit/test_infra_lazy.py tests/gates/test_gate_fixture_schema.py tests/test_node_lifecycle_static.py tests/test_cert_backup_gap.py tests/test_ssl_s3_cache.py tests/test_add_vhost.py tests/unit/test_validate_dora_dashboard.py tests/unit/test_vps_status_check.py` — зелёные.
4. **Полный gate**: `make gate MODE=fast` (или `make preflight`) зелёный; `make test MARKER=static` зелёный.
5. **e2e (D3)**: `make test-node NODE=test-e2e` — 11 requires_node зелёные; результат фиксируется в отчёте QA (при недоступности VPS — пометка + повтор после волны).
6. **Consumer-scans финальные**: `rg "assert True" tests/` = 0 в коде; `rg "^def _print_ldd_trajectory" tests/` = 1; `rg "skipif" tests/unit/test_project_adopter.py` = 0; `rg "call_args_list" tests/unit/test_deploy_engine.py tests/test_status_page.py tests/test_platform_export_metrics.py` = 0; `rg "@patch" tests/unit/test_deploy_engine.py` ≤ 5; `ls tests/test_unit_validate_dora_dashboard.py tests/test_unit_vps_status_check.py` not found; LDD-покрытие ≥ 80% (verify: 324+/344); `git status` — только ожидаемые файлы волны.
7. **`make fix-gate && git add -u`** перед коммитом (CI pre-flight, .kilo/rules/_project.md).

**Критерий:** все шаги зелёные; гейт R1 ловит регрессию (assert True/константы/bare-pass); 0 grep-ассертов на Python; моки только на границе; дубли слиты; conftest ленивый; enum-фазы; skipif снят; LDD 1 def; TRAP удалён; e2e 11/11.

## 4. Риски и решения

| Риск | Митигация |
|------|-----------|
| Рефакторинг test_deploy_engine теряет сценарии покрытия | Сравнение списка тест-функций/сценариев до/после (зафиксировано в @changes); parametrize покрывает все ветки (success/first-deploy/health-fail/rollback×2/pull-fail/status); при сомнении — доп. параметр, не возврат call_args_list. |
| dry-run node-lifecycle.sh в тестах требует окружения (PYTHONPATH, python3, node.yaml) | Скрипт сам экспортирует PYTHONPATH (TRAP[BUG] 2026-07-31); фикстура node.yaml в tmp_path; прецедент test_lib_ssh; при непреодолимых сложностях — код-присутствие с комментарием-обоснованием (оговорка D2). |
| Ленивый infra ломает тесты, ожидающие данные при импорте | Все потребители используют accessor-методы (ленивые); тест test_infra_lazy доказывает 0 subprocess до первого доступа; полный unit-прогон — арбитр. |
| BootstrapPhase-значения ≠ литералы node.py | Enum — канон state.json-ключей (state_machine); при расхождении — минимальное расширение enum (обратно-совместимо); e2e-семантика не меняется. |
| VPS для e2e недоступен в момент T10 | Прогон фиксируется как результат волны; при недоступности — пометка в отчёте QA + повтор после волны (manual-шаг, не блокирует merge). |
| Удаление grep-ассертов снимает защиту shell-контрактов | Shell-часть переводится на dry-run (D2); где dry-run невозможен — код-присутствие остаётся с обоснованием (инвариант 2: gate проверяет КОД). |
| R1-гейт ложно-блокирует легитимный код (константный assert в фикстурах) | Скан только тестовых файлов (исключены _conftest/helpers/tools/test_data/e2e fixtures); allowlist пуст; при ложных срабатываниях — сузить скоуп скана, не отключать гейт. |
| Удаление root-дублей ломает inventory/маркеры | Consumer-scan перед удалением; make test-inventory-sync; test_gate_test_inventory/ci_coverage — арбитры (T9). |

## 5. Критерии завершения волны (AC брифа 11-Brief)

- [ ] (1) 0 assert True в tests/ — гейт R1 (ast-скан) зарегистрирован (trinity), RED при регрессе (T1).
- [ ] (2) test_monitoring_config_renderer: реальные ассерты deep_merge/build_merged_config — УЖЕ выполнено (B7); verify в T10.
- [ ] (3) grep-ассерты (cert_backup_gap, node_lifecycle_static, ssl_s3_cache) заменены поведенческими: native для Python-модулей, dry-run для shell (D2) (T2).
- [ ] (4) mock-сокращение: deploy_engine ≤5 @patch на файл (граничная фикстура + parametrize), 0 call_args_list в 3 файлах (D1) (T3).
- [ ] (5) дубли Dora/VPS слиты — один файл на модуль (unit/ побеждает, root-версии удалены) (T4).
- [ ] (6) conftest: infra — ленивая инициализация (0 subprocess при импорте), teardown-гонка сетей задокументирована/исправлена (T5).
- [ ] (7) node.py импортирует BootstrapPhase (T6).
- [ ] (8) PyYAML-skipif снят (5 декораторов + хелпер удалены) (T7).
- [ ] (9) 16 копий _print_ldd_trajectory → 1 импорт (T8).
- [ ] (10) stale TRAP test_add_vhost:29 удалён (T9).
- [ ] (11) e2e-набор (11 requires_node) зелёный локально через `make test-node NODE=test-e2e` (D3; без CI-джобы — решение пользователя) (T10).
- [ ] (12) LDD-покрытие ≥ 80% — факт 94%, verify без расширения (T10).
- [ ] Гейт r1_no_pass_tests зарегистрирован в entrypoint-manifest (trinity) с repair-полями L2 (T9).
- [ ] `make gate MODE=fast` + `make test MARKER=static` зелёные; `make check-manifests` 0 diff (T10).
- [ ] `make fix-gate && git add -u` выполнен перед коммитом (CI pre-flight, .kilo/rules/_project.md).

$END_DEVPLAN
