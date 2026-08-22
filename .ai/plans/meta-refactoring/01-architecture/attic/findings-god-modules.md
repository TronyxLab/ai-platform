# Direction 4 — God Modules / Classes

Агент: форензик направления «god modules» · Метод: inventory функций/классов + группировка ответственностей · Дата: 2026-08-22

Итог направления: CRITICAL 0 · HIGH 1 (ARCH-030) · MEDIUM 2 (ARCH-031, ARCH-032) · LOW 3 (ARCH-033..035). Проблема размера кодовой базы концентрируется почти целиком в одном структурном долге (монолитный `__init__` agent_check) плюс verification-логика, застрявшая внутри bootstrap CLI; всё остальное — дисциплинированная step-декомпозиция, где длина отражает сложность пайплайна, а не SRP-нарушения.

Проверенно ЗДОРОВЫМ (long-but-cohesive, НЕ god objects): core/internal/deploy/orchestrator.py (DeployOrchestrator: 6 public methods, DI constructor, deploy() декомпозирован `_prepare/_apply/_verify/_rollback`:264-364 — pipeline, не god class); verify_contracts.py (12 focused `_check_*` предикатов одной заботы); scaffold/vhost_renderer.py (`render_all`:851-996 — документированный 6-step atomic pipeline); bootstrap/deploy/deploy_orchestrator.py (route/parallel/sequential пути факторизованы; `_deploy_parallel`:449-557 — 6 staged sections с делегацией topo_sort/docker_orchestrator); docker_orchestrator.py (phase-функции `_phase_hermes/_observability/_rebuild/_up`); template_engine.py, shared/docker_ops.py (плоские one-mechanism библиотеки); issue_cert.py + cert_orchestrator.py (правильный orchestrator→issuer layering); llm/key_provisioner.py (`provision_all`:479-683 ≈200 LOC строго линейный идемпотентный pipeline); loadtest/runner_cli.py, test_runner.py (execution+report pipelines одной заботы); state_machine.py (StateMachine: 7 public methods). Классов >15 public methods в аудированном наборе нет; функции >100 LOC — линейные pipelines, не multi-purpose блобы.

---

### ARCH-030: `agent_check/__init__.py` — вся многоответственная подсистема живёт в package `__init__`
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/agent_check/__init__.py:1-1092 (пакет: `__init__.py` 1092 LOC + `__main__.py` 21 LOC delegate)
- Symbols: 36 символов в одном файле — 14 TypedDicts (`AgentFindingDict`:130 … `_FpRegistryDict`:249), 2 dataclass'а (`AgentFinding`:260, `ChangedFiles`:315), 16 functions + module constants
- Evidence: ≥5 несвязанных ответственностей в import root: JSON/tool report data model (:128-359); git changed-files detection (`_git_changed`:411, `_venv_tool`:360); внешние tool runners (`run_ruff`:497, `run_basedpyright`:567, `run_static`:640 — subprocess orchestration); bespoke doc-header checker (`check_doc_headers`:695); FP-registry verdict resolution (`load_fp_registry`:785, `_selector_verdict`:825); orchestration/dedupe/timing (`run`:892, `_dedupe`:844) + human report rendering (`_human_report`:1002) + CLI (`main`:1045). Самозадокументированный долг: TRAP[DECISION]:117-125 — перенос `agent_check.py` → package `__init__.py` для разрешения file/package коллизии (170 W10-C), «внутренности НЕ декомпозированы», Rev-условие декомпозиции уже записано.
- Failure/maintenance scenario: каждый consumer (`from core.internal.agent_check import AgentFinding`, pytest collection, make target) парсит/компилирует все 1092 строки включая subprocess-runner логику; правка любого runner инвалидирует единственный .pyc для всех потребителей; нет стабильного API surface — внутренний рефакторинг `run_ruff` неотличим от public-API break; тестовая гранулярность заставляет импортировать весь pipeline ради unit-test одного runner'а.
- Impact: самый быстрорастущий файл пакета (advisory rules, fp_registry verdicts, новые linters приземляются сюда); растёт import side-effect surface; риск рефакторинга завышен, т.к. «package root» подразумевает публичный контракт.
- Minimal fix: разложить на `report_types.py`, `changed_files.py`, `tools_ruff.py`/`tools_pyright.py`/`tools_static.py`, `doc_headers.py`, `fp_registry.py`, `runner.py`; `__init__.py` оставить pure re-export shim (существующие импорты/тесты без изменений).
- Code churn: M
- Phase: Pre-launch

### ARCH-031: `lifecycle/cli.py` — CLI-слой встраивает verification suites, state repair и phase orchestration
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/lifecycle/cli.py:142-1164
- Symbols: parser (`build_parser`:142, `_CliArgs`:83); state persistence repair (`_recover_corrupt_state`:268, `_reset_state`:312, `_validate_init_env`:341); phase orchestration (`main`:403, `run_init_mode`:675, `run_update_mode`:716, `_run_phases`:757, `_mark_phase_*`:917/959); smoke tests (`_forced_command_smoke`:498, `_smoke_check_authorized_keys`:525, `_smoke_check_dispatch_ping`:553); security verification (`_final_verification_pass`:601); preflight/liveness (`_maybe_run_preflight`:998, `_run_liveness_probe`:1104)
- Evidence: ≥4 ответственности, которые не CLI: (a) SSH forced-command smoke probes (~100 LOC, :498-600) — integration-test concern; (b) security posture S1-S9 verification pass с JSON parsing (:601-657) — дублирует домен check_security_cli/security_posture; (c) corrupt-state recovery/reset — state_store concern протекает в CLI; (d) phase-run bookkeeping. Внутренняя факторизация хорошая (DI hooks повсюду, dedup `_run_phases` W5-C2) — код «хочет» быть извлечённым.
- Failure/maintenance scenario: изменение forced-command протокола или security_posture output schema требует правки CLI entrypoint bootstrap'а; семантика fail smoke-check'ей («FAIL → exit 0 сохранён») похоронена в CLI-коде, невидима для меняющих exit-code контракт в shared/contracts.py.
- Impact: самый проходимый файл bootstrap-пути впитывает каждую verification-правку; smoke/verify трудно unit-тестировать без argparse-слоя.
- Minimal fix: извлечь `lifecycle/verification.py` (smoke trio + `_final_verification_pass`) и перенести state-repair в `state_store.py`; CLI оставляет только parse→dispatch.
- Code churn: M
- Phase: Post-launch (bootstrap hot path — рефакторинг за parity-тестами)

### ARCH-032: `context_deployer.py` — context-deploy pipeline совмещает stub-compose factory, LLM provisioner и audit/domain utility
- Severity: MEDIUM
- Confidence: MED
- Files: core/internal/bootstrap/deploy/context_deployer.py:243-1276
- Symbols: workflow steps (`deploy_context`:893, `_step_certs`:975, `_step_deploy_projects`:1037, `_step_vhosts`:1064, `_step_nginx_reload`:1109, `_step_verify`:1135); per-project deploy (`_deploy_single_project_via_orchestrator`:352); stub compose templating + chown (`_ensure_bootstrap_compose`:545, `_is_bootstrap_stub_compose`:645); health gate (`_is_project_healthy`:685); audit (`_write_audit`:742); LLM facade (`_render_and_provision_llm`:766); domain extraction (`extract_domains_for_context`:795); CLI (`build_parser`:1193, `main`:1227)
- Evidence: D6-декомпозиция (docstring :8-10, «god-function разбита на typed-шаги») починила function-level SRP, но module-level смешение осталось: templating с inline f-string compose YAML + filesystem ownership (`chown ci-deploy`) на :545-631; LLM provisioning shim на :766-777; node.yaml domain scraping на :795-829; audit JSONL writing на :742-765. Это deployment orchestration + config generation + credential-free infra mutation + cross-domain facades в одном модуле.
- Failure/maintenance scenario: дрейф stub-compose конвенции уже породил два TRAP[BUG] (:565-571, :585-593) с требованием синхронизации с конвенциями DeployOrchestrator — ровно та связанность, которую изолировал бы отдельный `stub_compose.py`; LLM-policy изменения рябят через deployer-файл.
- Impact: крупнейший файл репозитория продолжает расти, потому что каждая смежная с φ8 забота приземляется сюда; два bug-trap'а документируют повторяющиеся неудачи convention-sync.
- Minimal fix: извлечь `bootstrap_stub_compose.py` (generate + stub detection + ownership) и перенести `extract_domains_for_context` рядом с NodeYaml readers; steps/CLI не трогать.
- Code churn: M
- Phase: Post-launch

### ARCH-033: `orchestrator_cli.py` — forced-command сервер и операторский клиент в одном CLI
- Severity: LOW
- Confidence: MED
- Files: core/internal/deploy/orchestrator_cli.py:121-826
- Symbols: server side (`_dispatch`:515, `_handle_receive`:457, `_handle_ping`:285, `_handle_status`:320, `_handle_remove`:337, `_handle_exit`:301); client side (`build_parser`:121, `build_channel`:235, `_handle_deliver`:609, `_handle_deploy`:695, `_handle_deploy_many`:728, `_handle_rollback`:754); verify bridge (`_handle_verify`:362)
- Evidence: две роли в одном файле: VPS-side forced-command receiver (security-sensitive: валидирует dispatch verbs поверх SSH) и operator-side deliver/deploy/rollback client. Оба тонкие над `DeployOrchestrator`, blast radius мал, но trust boundaries различны (server handlers исполняются как SSH forced-command user).
- Failure/maintenance scenario: клиентский flag-рефакторинг случайно меняет dispatch argv parsing, потребляемый remote CI (`receive <project> <version>` wire contract); аудит attack surface требует чтения и клиентского кода.
- Minimal fix: разделить на `dispatch_server.py` (receive/ping/status/exit) и `cli.py` (deliver/deploy/rollback) c общим `channels.py`.
- Code churn: S
- Phase: Post-launch

### ARCH-034: `phases/system.py` — семь различных lifecycle-фаз, собранных в один «system» модуль
- Severity: LOW
- Confidence: MED
- Files: core/internal/bootstrap/lifecycle/phases/system.py:101-1236
- Symbols: `phase_system_bootstrap`:395 (φ1), `phase_user_accounts`:678 (φ2), `phase_platform_setup`:794 (φ3), `phase_node_configuration`:1008 (φ5), `phase_converge_services`:1141 (φ8.5), `phase_node_config_update`:1169 (φ10), `phase_converge_update`:1222 (φ13)
- Evidence: OS provisioning (apt/docker/tor/firewall), user/SSH-key management, platform dirs/cron/sudoers, node.yaml validation и converge execution — разные домены, объединённые только «всё, что не docker/secrets/certs». Каждая phase-функция сама хорошо факторизована (spot-check `phase_system_bootstrap`:395-586 — 15 нумерованных шагов, извлечённых в helpers с non-fatal IssueCollector контрактом). По инварианту 3 bootstrap/AGENTS.md группировка намеренная — это finding о bundle/naming, не мандат на рефакторинг.
- Failure/maintenance scenario: merge conflicts концентрируются здесь (каждый DevPlan по φ1/φ2/φ3 касается одного файла); grep-навигация неоднозначна из-за sibling `helpers/system.py`.
- Minimal fix: разбить на per-phase файлы (`phases/system_bootstrap.py`, `phases/users.py`, `phases/platform_setup.py`) с re-export в `phases/__init__.py` — соответствует существующему aggregator-паттерну.
- Code churn: S (moves only)
- Phase: Post-launch

### ARCH-035: `helpers/system.py` vs `phases/system.py` — НЕ дубликация (проверено), но идентичные basenames маскируют layered-контракт
- Severity: LOW
- Confidence: HIGH
- Files: core/internal/bootstrap/lifecycle/helpers/system.py:100-972; core/internal/bootstrap/lifecycle/phases/system.py:117-126
- Symbols: helpers = I/O примитивы (`install_apt_packages`:174, `ensure_sops`:236, `ghcr_auth`:267, `install_zram`:687, `purge_cruft`:805, `ensure_fstab_policy`:972); phases = orchestration, потребляющая их через namespace-DI (`hs.install_apt_packages` на phases/system.py:126)
- Evidence: единственная одноимённая пара (`_install_apt_packages` phases:117 vs `install_apt_packages` helpers:174) — delegation wrapper, не дублированная логика; направление state_machine → phases → helpers enforce-ится и задокументировано (инвариант 3 bootstrap/AGENTS.md). Остаточный риск чисто когнитивный: два `system.py` в sibling-пакетах с разными ролями.
- Failure/maintenance scenario: агент правит wrapper, полагая его реализацией (или наоборот); import-lint не отличает «helper» от «phase» правки по пути.
- Minimal fix: переименовать helpers-модуль в `helpers/system_io.py` (или phases-модуль в `phases/os.py`); zero behavior change.
- Code churn: S
- Phase: Pre-launch
