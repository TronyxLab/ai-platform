# Direction 1 — Critical Paths Coverage

Агент: adversarial-аудит направления «critical paths» · Дата: 2026-08-22

## Critical-path → test mapping

| Critical path | Production module | Test files | Level |
|---|---|---|---|
| (a) Deploy pipeline | deploy/orchestrator.py DeployOrchestrator | test_orchestrator.py (DI fakes), test_receive_flow.py, test_receive_flow_atomicity.py, test_orchestrator_cli_dispatch.py, test_orchestrator_receive_version.py, test_deploy_concurrent_lock.py, integration/test_deploy_e2e.py (requires_node) | unit-mocked (docker seam injected True); e2e manual-only |
| (a2) Channels forced/scp | deploy/channels/base.py, scp.py, forced.py | test_channels.py, test_channels_injection.py | unit-mocked (runner DI-seam); retry covered |
| (b) Bootstrap state machine | bootstrap/lifecycle/state_machine.py + cli.py | test_state_machine.py (28), test_bootstrap_phases.py (явно исключает transitions/resume, :11), e2e/test_bootstrap_pipeline.py (requires_node) | unit-mocked subprocess; e2e manual-only |
| (c) Secrets AGE/SOPS | secrets/decrypt_secrets.py, lifecycle/secrets_manager.py, shared/node_detect.py | test_decrypt_secrets.py (5), test_secrets_manager.py (16), test_secrets_env_parser*.py, gates/test_gate_fallback_secrets_sync.py, test_node_detect.py | unit-mocked subprocess (sops никогда реальный); node_detect хорошо покрыт |
| (d) Healthcheck gating | deploy/healthcheck_poller.py, orchestrator._verify_deploy | test_healthcheck_poller.py (7, poller изолирован), FakePoller healthy-only везде | unit-mocked; unhealthy branch не тестируется E2E |
| (e) Forced-command dispatch | deploy/orchestrator_cli._dispatch | test_orchestrator_cli_dispatch.py (12), test_ssh_command_parser.py (29), test_remote_dispatch.py | unit, реальные SSH_ORIGINAL_COMMAND строки; adversarial частично |
| (f) Postgres hook | modules/postgres/hooks/on_project_deploy.py | test_on_project_deploy.py (15) | unit-mocked psql; incl. fatal paths + invalid db name |

Итог направления: инвентарь критических путей широк (каждый deploy/lifecycle модуль имеет ≥1 ссылающийся тест-файл; postgres hook и node_detect chains действительно хорошо покрыты включая fatal paths), но покрытие систематически happy-biased ровно в точках решений, несущих бизнес-риск: healthcheck-unhealthy ветка никогда не исполняется ни одним pipeline-тестом и противоречит задокументированной гарантии «healthcheck rollback» (TEST-001, наивысший риск); security-relevant санитизация (DD5-8 stderr redaction) и adversarial wire path receive-verb имеют ноль негативных/adversarial тестов (TEST-002/003); один канонический bootstrap CLI вообще без тест-файла (TEST-004); два существующих критических теста нефальсифицируемы by construction (TEST-003/005). Вердикт: AMBER — инфраструктура есть и дисциплинированна, но rollback/security decision matrix требует failure-injection тестов, прежде чем заявленные deploy-safety гарантии можно считать enforce'нутыми; приоритет следующей волны — TEST-001 и TEST-002.

---

### TEST-001: Healthcheck-unhealthy decision path имеет нулевое end-to-end покрытие; «healthcheck rollback» существует только при compose-failure — сломанные деплои репортят успех в CI
- Test: NONE для unhealthy-through-pipeline. Poller изолирован: unit/test_healthcheck_poller.py:97 (fake poll fn). Все pipeline-фикстуры инжектят healthy: test_orchestrator.py:192 (FakeHealthcheckPoller), test_receive_flow.py:161-168, test_orchestrator_cli_dispatch.py:239-244
- Production code: core/internal/deploy/orchestrator.py:517 (`DEPLOYED if healthy else PARTIAL`), core/internal/deploy/rollback.py:116 (`is_success()` включает PARTIAL), orchestrator_cli.py:678 (`rc=0 для PARTIAL`), orchestrator.py:452-467 (auto-rollback подключён ТОЛЬКО при compose_fn failure)
- Claimed guarantee: root AGENTS.md deploy model — «атомарный деплой на VPS + healthcheck rollback»; C5 — «healthcheck-rollback откатывает только образ»
- Actual guarantee: unhealthy healthcheck → PARTIAL → exit 0 в CI, post-deploy chain исполняется (hooks/post_deploy_chain.py:15 трактует PARTIAL как info-success), rollback НЕ триггерится. Rollback срабатывает только когда сам docker compose возвращает non-zero
- Blind spot: ни один тест не конструирует unhealthy HealthcheckResult через deploy() или ReceiveFlow.run() с ассертом rc/status/snapshot семантики; также ветка автоматического rollback в _apply_deploy (orchestrator.py:454-467) никогда не исполняется через deploy() — все DI compose fakes возвращают True
- Possible production bug class: crash-looping image задеплоен, контейнер unhealthy, пайплайн зелёный, каталог регенерирован, Telegram шлёт «info» — production раздаёт сломанный/stale сервис, пока человек не заметит; задокументированная страховка на этом пути не существует
- Recommended test: DI orchestrator с poller'ом, возвращающим `HealthcheckResult(status="unhealthy")` + compose_deployer=True → явный ассерт финального контракта (status, rc, ДОЛЖЕН ли rollback срабатывать — принять решение и запинить); плюс тест с compose_deployer=False + существующим snapshot через полный deploy() с ассертом ROLLED_BACK и восстановленного payload
- Existing test to remove/merge: none; ужесточить дизъюнкцию test_receive_flow.py:182 после пина поведения
- Confidence: HIGH

### TEST-002: DD5-8 sops stderr sanitization (redact temp-key path + truncate) полностью непротестирована; malformed/corrupt AGE payloads симулируются только mocked returncode=1
- Test: unit/test_decrypt_secrets.py (5 тестов, ВСЕ мокают subprocess.run; grep «sanitize|redact» по test_decrypt_secrets.py, test_secrets_phase.py → 0 hits). Integration test_secrets_pipeline_integration.py верифицирует только parser consistency, никогда реальную расшифровку
- Production code: core/internal/secrets/decrypt_secrets.py:279-287 (`stderr_raw.replace(tmp_key_path, "<redacted-age-key-path>")` + truncate 500 символов)
- Claimed guarantee: core/AGENTS.md DR section инвариант 5 — «sops stderr санитизируется (truncate + redact temp-key path)»; module header DD5-8 (:22)
- Actual guarantee: неверифицировано — ни один тест не кормит падающий sops-прогон, чей stderr СОДЕРЖИТ tmpfs temp-key path, с ассертом redaction; ни один не доказывает границы truncation
- Blind spot: если рефакторинг переименует переменную key-file или изменит формат tmp-пути, redaction молча станет no-op и путь /dev/shm-ключа (и любой sops-печатный контент) потечёт в PlatformFatalError message → audit/logs/alerting
- Possible production bug class: утечка секрет-материала/путей в персистентный audit trail и Telegram-алерты во время decryption инцидента (ровно когда stderr эмитится больше всего)
- Recommended test: мок sops с rc=1 и stderr, встраивающим точную строку tmp_key_path + >500 символов → ассерт: сообщение исключения содержит `<redacted-age-key-path>`, не путь, и ≤ лимита; опционально один real-sops roundtrip против corrupt (truncated/garbage) .enc.yaml за availability guard
- Existing test to remove/merge: влить в test_decrypt_fail_wrong_key (расширить его fake stderr)
- Confidence: HIGH

### TEST-003: Граница forced-command: receive/remove verbs без adversarial-негативов через реальный wire path; флагманский dispatch тест не может зафейлиться на неверном статусе
- Test: unit/test_orchestrator_cli_dispatch.py — только verify имеет traversal-негатив (:396-444); test_dispatch_receive_version (:225-276) ассертит `payload["status"] in {DEPLOYED,PARTIAL,ROLLED_BACK,SKIPPED,FAILED}` (все пять) и `rc in {0,1}`
- Production code: orchestrator_cli.py:556-564 (validate_project_name guard для status/remove/receive), ssh_command_parser.py parse/classify
- Claimed guarantee: T9.7/L-10 комментарий (:557) — «`;`/`../` инъекция в project_name отсекается здесь»; D5 version-in-JSON contract
- Actual guarantee: guard существует, но его receive-path поведение нигде не ассертится; единственный receive-dispatch тест проходит независимо от того, какой статус вернулся — регрессия DEPLOYED→FAILED при корректном version-поле остаётся зелёной
- Blind spot: сырые wire-байты вида `SSH_ORIGINAL_COMMAND="receive ../../etc/passwd abc"` или `"receive 'a;b' sha"` никогда не кормятся в _dispatch для receive/remove; R5 anti-survivorship протокол (tests/AGENTS.md) требует негатива на каждый детектор — для самого ценного verb его нет
- Possible production bug class: регрессия security-boundary forced-command (перестановка/удаление guard, добавление нового verb без валидации) → произвольная запись файла / path traversal под ci-deploy user на VPS; молча необнаружимо CI
- Recommended test: параметризованные негативы `receive <traversal/semicolon/reserved-name> sha`, `remove ../x` → ассерт JSON ERROR + rc=1 ДО вызова handler'а; ужесточить receive-version тест до точного DEPLOYED + rc==0 (фикстура уже гарантирует healthy+compose-ok)
- Existing test to remove/merge: ужесточить, не удалять, test_dispatch_receive_version
- Confidence: HIGH

### TEST-004: ZERO прямых тест-файлов — deploy_context_cli.py (make deploy-context entry, φ8/φ12 step)
- Test: NONE — `grep -rn "deploy_context_cli" tests/ --include="*.py"` → 0 hits (exit=1 верифицирован); systematic name-sweep по bootstrap/deploy/*.py показывает его единственным 0-reference модулем
- Production code: core/internal/bootstrap/deploy/deploy_context_cli.py:79 `main()` + :66 `_resolve_local_node_yaml`
- Claimed guarantee: root AGENTS.md canon table — `make deploy-context` каноническая φ8/φ12 операция; main()-контракт exit codes (core/AGENTS.md)
- Actual guarantee: arg wiring, node-yaml resolution fallback и exit-code passthrough CLI канонического verb'а исполняются только вручную на test-VPS (context_deployer под ним хорошо протестирован — 18 ссылающихся файлов — риск confined в CLI seam, включая правило main()-контракта «no sys.exit вне main»)
- Blind spot: неверный argparse default или регрессия resolution ломает bootstrap φ8 (fresh node) и converge φ12 — первый сигнал это упавший реальный bootstrap, не CI
- Possible production bug class: fresh-node bootstrap failure / converge no-op, обнаруженные во время DR drill или rebuild ноды (RTO impact, часы по core/AGENTS.md)
- Recommended test: unit тест, зовущий `main(["--node","n","--context","c"])` с monkeypatched context_deployer entry + tmp node.yaml layout; ассерт delegation args + exit-code propagation incl. ConfigValidationError путь
- Existing test to remove/merge: none
- Confidence: HIGH (zero-import факт), MED (business exposure — тонкий модуль)

### TEST-005: Mock-only тесты, структурно неспособные зафейлиться (новые, сверх известных TEST-031/033/034): rollback-with-snapshot дизъюнкция и sequential-deploy system-module liveness
- Test: unit/test_orchestrator.py:317-338 `test_rollback_with_snapshot` ассертит `result.status in {DeployStatus.DEPLOYED, DeployStatus.FAILED}` — принимает оба исхода rollback'а, который заявляет верифицировать; compose_rollback fake захардкожен True (:194), а False-ветка (rollback.py:238 FAILED/«Rollback failed») не тестируется нигде. Также unit/test_deploy_orchestrator.py:694 system-module liveness ассертится только как `mock_invoke.assert_any_call("nginx","healthcheck","liveness")` — значение результата игнорируется
- Production code: core/internal/deploy/rollback.py:194-253 `rollback()`, :306 `_rollback_compose` error path
- Claimed guarantee: инвариант 3 (MODULE_CONTRACT orchestrator) — «rollback() восстанавливает compose_state из snapshot»
- Actual guarantee: тест доказывает только «не упал»; регрессия, делающая rollback всегда FAILED (или всегда заявляющая успех без восстановления ничего), проходит
- Blind spot: ветка rollback_ok=False → FAILED + audit «rollback failed» никогда не ассертится; интерплей snapshot_payload_dir restore + failed compose не тестируется вместе
- Possible production bug class: экстренный make-level rollback во время плохого релиза репортит DEPLOYED, пока production крутит сломанный образ (false-confidence инцидент во время firefighting)
- Recommended test: два детерминированных кейса через constructor DI — compose_rollback=True → DEPLOYED + audit row `operation=rollback,result=DEPLOYED`; compose_rollback=False → FAILED + error_info содержит «Rollback failed»; ассерт восстановленного содержимого файла в обоих
- Existing test to remove/merge: заменить тело test_rollback_with_snapshot (node id сохранить)
- Confidence: HIGH

### TEST-006: Crash mid-phase (шаг заморожен в status="running") resume ассертится только через контракт phase_is_done, никогда через полный restart flow
- Test: unit/test_state_machine.py:218-240 (грузит «running», ассертит персистентность), :853 (phase_is_done(running)=False), :1274 resume тест использует done+missing фазы с полностью сфейканным execute_phase; ни один тест не гоняет run_init_mode над state.json с фазой «running»/interrupted и не ассертит реисполнение + терминальное состояние
- Production code: core/internal/bootstrap/lifecycle/state_machine.py:487-507 (load/save), execute_phase (:661); статусы pending|running|done|skipped|failed|done_with_warnings (state_store.py:87-94)
- Claimed guarantee: инвариант state machine — checkpoint/resume на /var/lib/platform/.bootstrap/state.json; TRAP[BUG]:492 чинил silent reset corrupt state
- Actual guarantee: resume-after-crash mid-phase полагается на композицию `phase_is_done("running") == False` со skip-логикой run-цикла — композиция не протестирована; будущая оптимизация, трактующая «running» как «in flight, skip», пройдёт текущий suite
- Possible production bug class: SSH drop mid-bootstrap оставляет фазу running; следующий make bootstrap-node либо пропускает half-applied фазу (сломанное состояние ноды), либо deadlock — обнаруживается только на реальном прерванном bootstrap
- Recommended test: seed state.json с φk status="running" (остальные done), гонять cli.run_init_mode с fake execute_phase, записывающим вызовы → ассерт: φk реисполнена ровно один раз и завершается done; зеркально для status="failed"
- Existing test to remove/merge: none; расширяет test_resume_missing_phase_executes
- Confidence: MED
