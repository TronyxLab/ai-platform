# Direction 5 — Integration Gaps

Агент: adversarial-аудит направления «integration gaps» · Дата: 2026-08-22

## Suite inventory

| Suite/dir | Marker | Runs in CI? | Preconditions | Поведение без предусловий |
|---|---|---|---|---|
| tests/unit/ (355) + unmarked root/integration | static_audit catch-all | ДА — push-gate, platform-gate-fast, platform-test step 1 (fast) | нет (mocked) | n/a |
| tests/gates/ non-Docker (~132) | gate | ДА — fast+full | нет | n/a |
| gates-docker (gate+requires_docker) | fast only, allow_no_tests: true | ~0 декорированных тестов существует → всегда 0-collected PASS | Docker daemon | exit-5 tolerated |
| tests/contracts/ | contract | ДА (fast+full) | нет | n/a |
| ai-instructions suite | ai_instructions | ДА (fast+full) | vendor/ | n/a |
| predeploy non-Docker | predeploy | ДА (fast/full) | нет | n/a |
| predeploy-docker | predeploy+requires_docker | platform-test MODE=ci-docker only | running stack | honesty fail в CI (REQUIRE_HONESTY_MODE=fail), skip локально (default marker) |
| smoke (test_smoke_*.py) | smoke+requires_docker | platform-test ci-docker ONLY (не в fast gates) | полный стек ~23 контейнера | CI: fail при отсутствии; локальный default = pytest.skip (honesty.py:42 default marker) |
| component | component | как smoke | hermes/clickhouse | same |
| integration suite id | integration and not local_stack | platform-test steps 419-448 | INTEGRATION_MODE/API keys | всегда 0 collected → rc=5 → mapped to success |
| local_stack (tests/e2e/test_shared_db_access.py) | integration+local_stack | НИГДЕ автоматом (исключён 4425ce0); вручную make up + MARKER=integration | prod-named postgres/pgbouncer | R4 FAIL без стека |
| requires_node (tests/e2e/) | requires_node | НЕТ — фильтруется из всех suite; вручную make test-node | NODE env, SSH, AGE key | FAIL (R4) |
| chaos | chaos | НЕТ — ручное операционное окно | NODE, SSH | fail |
| local_auth | local_auth | ORPHANED: нет check-suite id И исключён из static_audit expr | hermes :9119 + env password | require_env/skip-or-fail |
| e2e (test_e2e_health.py) | e2e | ORPHANED: нет check-suite id, исключён из static_audit expr; ни один workflow не использует | GRAFANA_URL/E2E_LANGFUSE_URL; self-skip prod-целей без CI=true | skip |

Итог направления: детерминированные слои (trinity static/unit/gates, golden parity, manifest freshness) over-enforced до редкого стандарта — но каждый seam, пересекающий process/service границу, полый: CI integration stage — театр (TEST-041), флагманская shared-DB гарантия потеряла единственный spanning-тест из-за local_stack exclusion (TEST-042), две половины deploy wire format ни разу не встречались в тесте (TEST-043), bootstrap sh→python handoff верифицируется grep'ом против устаревшего модуля (TEST-044), stale второй интерпретатор suite-манифеста до сих пор в поставке (TEST-045), и два целиком зарегистрированных suite нигде не исполняются (TEST-046). Вердикт: зелёный CI здесь доказывает units и статические контракты, не интеграцию; реальный риск платформы концентрируется ровно там, где тесты исключены, осиротели или сделаны skip-by-default.

---

### TEST-041: CI «integration» stage вакуумна — собирает 0 тестов, всегда выходит зелёной
- Test: NONE collectable
- Production code: .github/workflows/platform-test.yml:419-448 (error-path/live steps, INTEGRATION_MODE env) ↔ core/check-suite.yaml#integration cmd `-m "integration and not local_stack"` ↔ pyproject marker registry
- Claimed guarantee: стадии «Integration: error-path (no tokens) + live (real API call)» валидируют межмодульную интеграцию на CI
- Actual guarantee: единственные держатели pytest.mark.integration — test_shared_db_access.py (исключён как local_stack) и test_flaky_detection.py (маркер намеренно снят, :254 «Rejected»); suite всегда собирает 0 → exit 5 → оба шага печатают «skipping (exit 5 is OK)». INTEGRATION_MODE=error-path не потребляется ничем в core/ (только комментарий _conftest/checklist.py)
- Blind spot: четыре замоканных файла в tests/integration/ гоняются только потому, что UNMARKED и случайно едут static_audit catch-all — недокументированная, не-enforceимая зависимость
- Possible production bug: прямого нет; CI-сигнал ложно рекламирует несуществующую интеграционную валидацию
- Recommended test: гейт, ассертящий что MARKER=integration собирает ≥N тестов, либо удалить оба workflow-шага; явно маркировать замоканные integration-файлы и убрать их из catch-all
- Existing test to remove/merge: ассерты test_gate_ci_env_vars.py на INTEGRATION_MODE должны верифицировать существование consumer'а
- Confidence: HIGH

### TEST-042: local_stack exclusion осиротила shared-DB seam (hook → pgbouncer → role isolation)
- Test: tests/e2e/test_shared_db_access.py (3 spanning-теста) — существует, исключён из CI с 4425ce0
- Production code: postgres module hook (CREATE DB/role/GRANT, wildcard pgbouncer.ini) ↔ project facade PLATFORM_POSTGRES_DSN через pgbouncer:6432
- Claimed guarantee: root AGENTS.md «Что предоставляет платформа»: per-project role isolation работает end-to-end
- Actual guarantee: unit-покрытие мокает docker exec; сам файл заявляет, что он «единственный способ доказать, что wildcard pgbouncer + роль/GRANT работают end-to-end». CI потерял: полный connect-cycle, R5-negative, сторожащий исправленный баг «pgbouncer no such database» hard-list, идемпотентный redeploy с unchanged ролью
- Blind spot: регрессии pgbouncer auth/userlist/wildcard и изменения GRANT-модели hook'а проходят все автоматические слои и всплывают только на staging
- Possible production bug: рецидив ровно того класса бага, который пинит R5-negative (hardcoded DB list вместо wildcard)
- Recommended test: портировать эти 3 теста в ci-docker gate против уже provisioned postgres/pgbouncer контейнеров тест-стека (снять требование prod-named stack), сохранив R4 fail-семантику
- Existing test to remove/merge: none; оставить как есть после CI-пригодности
- Confidence: HIGH

### TEST-043: forced-command wire format не имеет client→server round-trip span
- Test: NONE spanning. Стороны покрыты раздельно: tests/unit/test_channels_injection.py ассертит client quoting с замоканным subprocess; tests/unit/test_ssh_command_parser.py парсит hand-written строки; injection-negative кормит литерал «status a;b» прямо в _dispatch
- Production code: core/internal/deploy/channels/forced.py (строит `receive <shlex.quote(project)> <version>`) ↔ core/internal/deploy/orchestrator_cli.py dispatch + deploy/ssh_command_parser.py (parse_ssh_command/_strip_prefixes/classify_verb)
- Claimed guarantee: CI deliver path (deploy-project.yml receive verb) говорит на том диалекте, который парсит VPS dispatcher
- Actual guarantee: формат отправителя и грамматика приёмника эволюционируют под независимыми suite; quote/split normalization, позиции аргументов, добавление verb'ов никогда не исполняются вместе; в tests/e2e/ нет SSH_ORIGINAL_COMMAND round-trip
- Blind spot: любой format drift (новый appended arg, изменённая prefix normalization) проходит оба suite и фейлится только на реальном деплое
- Possible production bug: forced-command деплои падают с JSON dispatcher error на VPS при зелёном CI
- Recommended test: round-trip unit тест — построить remote_cmd через channel internals для каждого канонического verb → прогнать через parse_ssh_command → ассертить совпадение parsed intent, включая injection-строки (`;`, пробелы, кавычки)
- Existing test to remove/merge: влить test_forced_command_remote_cmd_quoted в round-trip параметризацию
- Confidence: MED

### TEST-044: node-lifecycle.sh → cli.py flag forwarding верифицируется только grep'ом — против неверного файла
- Test: tests/unit/test_node_lifecycle_static.py (grep-only; :306 исполняет скрипт только для no-mode error path); tests/unit/test_state_machine.py зовёт методы машины напрямую
- Production code: core/internal/bootstrap/node-lifecycle.sh `_delegate() { python3 lifecycle/cli.py "$@"; }` ↔ lifecycle/cli.py build_parser argparse
- Claimed guarantee: контракт entrypoint↔internal флагов («--dry-run: parser flag accepted, passed through»)
- Actual guarantee: Check 1 грепает shell на `--dry-run`; Check 2 ассертит dry_run handling в state_machine.py, но цель делегации — cli.py (164 W3: «compat-заглушка удалена», единственный канал — cli.py). Ни один тест не исполняет bash → cli.py happy-path argv forwarding
- Blind spot: rename/перенос argparse-флага в cli.py (например, --node-yaml, --tor-enabled) ломает bootstrap/update фазы, пока shell-грепы и machine-level unit-тесты зелёные
- Possible production bug: φ-фазы молча теряют флаги на sh→python границе при make node-update
- Recommended test: subprocess тест `bash node-lifecycle.sh --mode update --dry-run` с tmp node.yaml, ассертящий surfacing plan-вывода cli.py (dry_run_plan печатается, мутаций нет)
- Existing test to remove/merge: слить Checks 2-4 из test_dry_run_flag_accepted в этот execution-тест
- Confidence: MED

### TEST-045: Двойной интерпретатор suite id — test_runner.MARKER_MAP дрейфанул от канона check-suite.yaml
- Test: NONE, сравнивающий их; test_gate_check_suite_consistency.py покрывает только внутреннюю структуру/golden parity check-suite.yaml
- Production code: core/check-suite.yaml cmds (SoT по DevPlan 120) ↔ core/internal/test_runner.py MARKER_MAP + _STATIC_AUDIT_EXPR
- Claimed guarantee: TRAP[DESIGN] в test_runner: маппинг сознательно дублирует бывший ci.mk, рефакторинг «при первом расхождении»
- Actual guarantee: дивергенция уже отгружена — MARKER_MAP["integration"] = `-m integration -rs` БЕЗ `not local_stack` (собрал бы R4-фейлящиеся shared_db тесты); записи smoke/component/predeploy/local_auth/e2e не используются check-suite (который зовёт прямой pytest или не имеет suite); ci.mk test target удалён в DevPlan 165 — MARKER_MAP остался unmaintained shadow-интерпретатором
- Blind spot: любой вызов `python -m core.internal.test_runner --marker integration` (документированный module interface) противоречит exclusion-контракту манифеста
- Possible production bug: агент/оператор гоняет stale путь без стека → вводящий в заблуждение hard FAIL, диагностируемый как поломка платформы
- Recommended test: деривировать MARKER_MAP из check-suite.yaml на import time (единый интерпретатор) либо parity-гейт на равенство выражений per suite id
- Existing test to remove/merge: none; расширить test_gate_check_suite_consistency.py parity-кейсом
- Confidence: MED

### TEST-046: Два зарегистрированных-marker suite нигде не исполняются — hermes auth и Grafana/Langfuse health имеют ноль запусков
- Test: NONE исполняется автоматом. Файлы существуют: tests/test_local_auth.py, tests/test_e2e_health.py
- Production code: hermes dashboard basic-auth (:9119, htpasswd из secrets_manager) ↔ local_auth тесты; nginx-published /api/health endpoints (grafana/langfuse) ↔ e2e тесты
- Claimed guarantee: маркеры, зарегистрированные в pyproject.toml, подразумевают исполнимые suite (registry gate test_gate_pytest_markers.py enforce'ит регистрацию, не исполнение)
- Actual guarantee: ни один маркер не имеет check-suite.yaml suite id; оба исключены из static_audit catch-all expr (`not local_auth`, `not e2e`); ни один workflow их не упоминает; test_e2e_health.py:46 дополнительно self-skip prod-целей без CI=true
- Blind spot: регрессия dashboard-auth (htpasswd/provisioning drift) и поломка публичных health-endpoint доходят до staging незамеченными; orphan-статус невидим, т.к. gate регистрации маркеров проходит
- Possible production bug: сломанный Langfuse/Grafana health endpoint или misprovisioned Hermes auth — обнаруживаются только людьми
- Recommended test: прицепить test_e2e_health к ci-docker gate (сервисы существуют в тест-стеке, prod URL → test алиасы); дать local_auth явный diagnostic-false suite id, чтобы manual-only статус был хотя бы декларирован
- Existing test to remove/merge: none
- Confidence: HIGH
