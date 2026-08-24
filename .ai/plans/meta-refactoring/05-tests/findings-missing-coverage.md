# Direction 2 — Missing Coverage

Агент: adversarial-аудит направления «missing coverage» · Дата: 2026-08-22

## Domain census (core/internal)

Prod LOC vs импортирующие тест-файлы (grep core.internal.<d> + from <pkg> import <mod> + bare sys.path imports) и name-дедицированные tests/unit/test_<d>*.py LOC. Сортировка по prod-LOC-per-importing-test-file:

| # | Domain | Prod LOC | Importing test files | LOC/file | Name-dedicated files / LOC | Verdict |
|---|--------|---------|---------------------|----------|---------------------------|---------|
| 1 | agent_check | 1266 | 0 | ∞ | 0 / 0 | FLAG: zero dedicated |
| 2 | verify_sweep | 1604 | 1 | 1604 | 1 / 635 | adequate (facade re-export протестирован) |
| 3 | check_suite | 2539 | 2 | 1270 | 1 / 1051 | adequate (facade imports multiline) |
| 4 | loadtest | 3707 | 8 | 463 | 8 / 2260 | adequate |
| 5 | scripts | 5942 | 13 | 457 | 1 / 223 | FLAG по правилу (<300) — см. TEST-012 |
| 6 | healthcheck | 3470 | 8 | 434 | 3 / 875 | adequate |
| 7 | practices | 3866 | 9 | 430 | 5 / 1565 | adequate |
| 8 | bootstrap | 36037 | 86 | 419 | 5 / 3595 | ratio высок; 628 zero-ref LOC внутри (TEST-011) |
| 9 | monitoring | 1640 | 4 | 410 | 9 / 2103 | adequate |
| 10 | validate | 765 | 2 | 383 | 5 / 1933 | over-covered |
| 11 | scaffold | 7670 | 23 | 333 | 2 / 616 | thin-but-adequate per-file |
| 12 | llm | 2301 | 8 | 288 | 7 / 2140 | hole: admin_client (TEST-014) |
| 13 | verify | 537 | 2 | 269 | 2 / 1271 | over-covered |
| 14 | lint | 1737 | 7 | 248 | 0 name-matched / 1328 через test_dead_code_checker | adequate (naming artifact) |
| 15 | deploy | 8885 | 36 | 247 | 17 / 5007 | best-covered large domain |
| 16 | static | 3495 | 15 | 233 | 15 / 1698 | adequate |
| 17 | build | 176 | 1 | 176 | 1 / 380 | fine |
| 18 | secrets | 503 | 3 | 168 | 9 / 3719 | over-covered |
| 19 | catalog | 305 | 1 | 305 | 1 / 306 | fine |
| 20 | config | 269 | 2 | 135 | 1 / 384 | fine |
| 21 | shared | 14114 | 131 | 108 | 16 / 3697 | best ratio |

Zero-test-reference production модули (word-level floor по всем 489 файлам, с поправкой на facade re-exports и bare sys.path imports): agent_check/* (1266), bootstrap/docker_user_policy.py (211), scripts/manifest_driver.py (211), bootstrap/check_security_cli.py (145), bootstrap/deploy/deploy_context_cli.py (131), practices/check_project/fixer.py (98), shared/http_probe.py (94), lint/doxygen_checker.py (89), bootstrap/webnames_protocol.py (89), bootstrap/lifecycle/phases/capabilities.py (52), static/vulture_whitelist.py (66, data-only), deploy/rollback.py (348, untracked WIP), hermes-agent/build/scripts/patch-basic-auth-provider.py (109). Сумма ≈ 2900 LOC закоммиченного/стейджнутого production-кода без единой тестовой ссылки на любом уровне.

Итог направления: объём и риск расходятся в узкой полосе, не по всей платформе. 355-файловый suite честно покрывает длинный хвост: 42/44 module-скриптов, все top-churn файлы (top-20 churn → 18/20 имеют дедицированные тесты; остальные 2 — heartbeat_check.py, heartbeat.py — вычурчены до удаления), и даже пугающие ratio-аутлаеры (verify_sweep 1604:1, check_suite 1270:1, scripts 27:1, lint 0-name-matched) растворяются при import/facade-трассировке. Реальный missing coverage концентрируется в четырёх кластерах: (1) весь пакет agent_check — 1266 LOC за обязательным агентским гейтом, ноль тестов и вводяще названный twin-suite (TEST-010); (2) ~700 LOC bootstrap CLI/policy кода, включая два canon-table verb'а и iptables policy (TEST-011); (3) само-референтная дыра manifest_driver.py — непротестированный чекер внутри единственной команды, которую гоняет каждый агент (TEST-012); (4) LiteLLMAdminClient, единственный live-API клиент, вымоканный из каждого касающегося его теста (TEST-014). Нетто: детерминированные слои over-enforced относительно риска, при этом ~2900 LOC закоммиченного production-кода — непропорционально код, который гейтит другой код или говорит с внешним миром — не имеет фальсифицируемого теста ни на каком уровне.

---

### TEST-010: Пакет make agent-check (1266 LOC) имеет ноль тестов — а его name-twin тесты тестируют другую реализацию
- Test: NONE для core.internal.agent_check. `grep -rl "core\.internal\.agent_check" tests` → 0 файлов. Четыре теста с именем test_agent_check_* (tests/unit/test_practices_check_project.py:634-690) зовут core.internal.practices.check_project.checks.file.check_agent_check (:319), чей docstring говорит «НЕ копия платформенного agent_check — только 2 быстрых шага» (:305). Ничто не импортирует пакет (grep по core+tests: только собственные 3 файла); единственный consumer — makefiles/dev.mk:2 `$(PYTHON) -m core.internal.agent_check`
- Production code: agent_check/runners.py (755): run() :649-740 (~92 LOC, 8-step orchestrator), run_ruff() :254-323, run_basedpyright() :324-396, run_static() :397-451, check_doc_headers() :452-541 (~90 LOC), load_fp_registry() :542-581; __init__.py main() :176 + JSON report contract; types.py (265)
- Claimed guarantee: «make agent-check — обязательный шаг агента» (root AGENTS.md) с документированным JSON-выходом `{rule, file, line, message, fixable}` и FP-registry семантикой — подразумевает верифицированный инструмент
- Actual guarantee: <> — ни одного ассерта на parsing, verdicts, exit codes или JSON shape. check_doc_headers() — вторая, дивергентная реализация header validation рядом с протестированным lint/doc_header_validator.py (561 LOC, протестирован)
- Blind spot: FP-registry _selector_verdict/_dedupe логика молча меняет, какие findings видят агенты; ruff/basedpyright output-parsing ломается на апгрейдах инструментов; drift exit-code контракта делает обязательный гейт зелёным при падении
- Possible production bug: agent-check репортит PASS после parser-регрессии — L1-сигнал, которому доверяет каждый агент, нефальсифицируем
- Recommended test: golden-тесты run() с fake ruff/pyright/static subprocess-выходами (ассерт findings mapping + exit codes), parity-тест check_doc_headers vs lint/doc_header_validator на общем fixture-корпусе, JSON-schema round-trip AgentCheckReport
- Existing test to remove/merge: none; переименовать 4 practices-twin'а в test_project_agent_check_step_* для убийства иллюзии
- Confidence: HIGH (caveat: runners.py/types.py — untracked WIP; разрыв отчасти «ещё не написан», но закоммиченные __init__.py/__main__.py тоже не тестированы)

### TEST-011: bootstrap zero-ref quartet — два production CLI и iptables policy без тестов на любом уровне
- Test: NONE. Word-level greps возвращают 0 тест-файлов для всех четырёх модулей
- Production code: bootstrap/check_security_cli.py (145; main() :98 — make check-security → remote S1-S9 исполнение, _local_fallback :74), bootstrap/deploy/deploy_context_cli.py (131; main() :79 — make deploy-context; см. также TEST-004), bootstrap/docker_user_policy.py (211; apply_docker_user_policy() :121, build_firewall_rule() :188 — DOCKER-USER iptables rules), bootstrap/webnames_protocol.py (89; inject_webnames/shred_secrets — credential handling). Также lifecycle/phases/capabilities.py (52)
- Claimed guarantee: core/AGENTS.md canon table представляет make check-security и make deploy-context как first-class верифицированные verbs; runbook step 5 говорит «Verify: make check-security NODE=»
- Actual guarantee: <> — S1-S9 проверки за ними хорошо протестированы через facade security_posture (13× check_sshd, 6× check_file_perms в test_security_posture.py), но слой CLI arg→dispatch→exit-code и целые модули docker_user_policy/webnames никогда не исполняются под тестом
- Blind spot: flag drift между exec-строкой core/entrypoints/check-security.sh и CliArgs (тот же класс, что TEST-044); регрессии построения iptables-правил (build_firewall_rule) всплывают только на живой ноде; shred_secrets, молча фейлящийся, оставляет plaintext credentials на диске
- Possible production bug: make check-security выходит 0, ничего не проверив (fallback path :74); неверное DOCKER-USER правило ломает container networking на bootstrap
- Recommended test: main(argv) dispatch-тесты с mocked remote_executor для обоих CLI; pure-function тесты build_firewall_rule/inject_webnames (string-in/string-out, моки не нужны)
- Existing test to remove/merge: none
- Confidence: HIGH (zero-coverage), MED (impact — оба CLI ручные операторские пути)

### TEST-012: scripts domain 27:1 — по большей части naming artifact; реальная дыра manifest_driver, чекер внутри make check
- Test: 1 name-дедицированный файл (223 LOC) на 5942 prod LOC — но per-module census показывает 14/15 модулей с дедицированным тест-файлом (sync_env_defaults.py 974 ↔ 622 test LOC/19 тестов; generate_entrypoint_manifest.py 754 ↔ dedicated файл). Честный вердикт: thin-but-adequate, не missing
- Production code: scripts/manifest_driver.py (211): main() :166, _run_check() :131, _gmake_path() :110 — 0 тест-ссылок
- Claimed guarantee: make check — «единственная тестовая команда агента»; его G1-G6 manifest-freshness шаги запускают `python3 core/internal/scripts/manifest_driver.py check` (core/check-suite.yaml:108)
- Actual guarantee: драйвер, решающий свежесть манифестов (и, значит, падение make check), сам неверифицирован; каждый другой генератор, который он ведёт (generate_entrypoint_manifest.py, generate_secrets_manifest.py…), имеет тесты
- Blind spot: баг агрегации label/exit в _run_check или резолва gmake-пути → freshness-гейты проходят вакуумно или make check спонтанно фейлится для всех пользователей 355-файлового suite
- Possible production bug: false-green manifest freshness (дрейф уходит в поставку, потому что собственный failure mode чекера не протестирован)
- Recommended test: 3-4 теста, ведущие main(["check"]) против tmp-манифестов со stubbed gmake — один зелёный, один drift-detected, один gmake-missing
- Existing test to remove/merge: none
- Confidence: MED

### TEST-013: core/modules Python действительно хорошо покрыт; ровно одна дыра — а shell hooks структурны
- Test: backup-cron upload.py (976 LOC) протестирован — test_upload.py:160,186,208 + test_upload_validation.py импортируют create_s3_client/upload_file; wal_sync.py (522) ↔ test_wal_sync.py; retention.py (494), backup_postgres.py (356), entrypoint.py (186) — все с дедицированными файлами. status-page app.py (310) протестирован app-level (test_status_page.py:13-17); hermes healthcheck_deps.py (270) импортируется напрямую test_hermes_healthcheck.py:33. Per-file census из 44 module .py: 42 referenced
- Production code (untested): hermes-agent/build/scripts/patch-basic-auth-provider.py (109 LOC, 0 ссылок) — build-time патчинг auth provider
- Claimed guarantee: module scripts production-critical (бэкапы, status page, hermes auth), и suite подразумевает их охрану
- Actual guarantee: Python module logic охраняется; ~1100 LOC module shell (install.sh/healthcheck.sh по 15 модулям) охраняется только структурно — test_gate_healthcheck_contract.py, test_gate_healthcheck_drift.py grep/parse контракты, без поведения
- Blind spot: логическая регрессия patch-basic-auth-provider молча попадает в следующий hermes image build; изменения поведения shell-hook (exit codes, retry-циклы) проходят все гейты и всплывают только на ноде
- Possible production bug: hermes dashboard auth patch становится no-op → basic-auth отсутствует на :9119 (ровно seam, который TEST-046 пометил как orphaned-runtime)
- Recommended test: гонять patch-basic-auth-provider.py против fixture provider-файла, ассертить patched output; shell hooks оставить структурными (adequate для их размера)
- Existing test to remove/merge: none
- Confidence: HIGH

### TEST-014: LiteLLMAdminClient — единственный live-API клиент платформы на 100% вымокан из собственного тест-suite
- Test: test_llm_key_provisioner.py (7 тестов) — каждый тест monkeypatch'ит клиент: `patch.object(kp, "LiteLLMAdminClient", return_value=mock_client)` (:140, :423); docstring файла: «with mocked LiteLLMAdminClient»
- Production code: core/internal/llm/admin_client.py (493 LOC): LiteLLMAdminClient HTTP-слой (key/user management против LiteLLM admin API), KeyInfo, error handling. 0 прямых тест-ссылок (единственное упоминание — rationale-комментарий в tests/gates/test_gate_imports.py:14)
- Claimed guarantee: make provision-llm / φ11 llm-keys фаза провижинит virtual keys надёжно; llm domain показывает 2140 dedicated test LOC
- Actual guarantee: всё вокруг клиента (provisioning flow, policy schema, config rendering) протестировано; request construction, response parsing и error paths клиента исполняются под тестом ровно ноль раз — нет fake transport, нет recorded-response fixture
- Blind spot: drift API-формы или регрессия error-handling (например, трактовка HTTP 400 как успеха) проходит CI и фейлится только на реальном node provision, блокируя φ11
- Possible production bug: ключ provisioned, но репорчен как failed (или наоборот) — неверный вердикт на идемпотентно-критичной операции
- Recommended test: transport-injection тесты (respx/httpx MockTransport или recorded-cassette fixture), покрывающие create-key happy path, 401, 429, malformed-JSON
- Existing test to remove/merge: none; provisioner-тесты оставить — они тестируют правильную единицу
- Confidence: HIGH
