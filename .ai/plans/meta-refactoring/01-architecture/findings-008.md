# Направление 8 — Initialization / lifecycle architecture

Метод: поиск import-time side effects, signal/atexit-регистраций, hash-invalidation в state machine, lazy-import кластеров, env-at-import. Агент: explore, 27 tool calls. Дата 2026-08-22.

## ARCH-0014 — CRITICAL: import-time logging.basicConfig(WARNING) глушит телеметрию на всём remote-пути
- Severity: CRITICAL · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/bootstrap/remote_executor.py:60; overlay_deliverer.py:46; lint/pre_push_branch_detect.py:44
- Symbols: basicConfig на уровне модуля
- Evidence: `logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)` на module scope. Задокументированный P1: remote_dispatch.py:79-81 — при импорте remote_executor root уже имеет handlers → последующий basicConfig(INFO) no-op → INFO/[IMP:7-9] отфильтрованы
- Scenario: любая перестановка порядка импортов (новый helper импортирует overlay_deliverer) молча выключает диагностику на операторском/CI пути; отладка partial-failure в launch week вслепую
- Impact: root=WARNING подавляет весь LDD-telemetry поток remote-операций
- Minimal fix: перенести basicConfig в каждый main(); модули без handlers (паттерн уже доказан в docker_daemon.py:63)

## ARCH-0015 — импорт decrypt_secrets перехватывает SIGTERM/SIGINT процесса + atexit
- Severity: HIGH · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/secrets/decrypt_secrets.py:142-144; workaround-свидетельство age_key_backup.py:203
- Symbols: atexit.register(_cleanup_temp_files), signal.signal(SIGTERM/SIGINT)
- Evidence: `signal.signal(signal.SIGTERM, _signal_handler)` с комментарием «module-level, runs once on import»; handler делает os.kill(os.getpid(), signum); age_key_backup уже несёт комментарий «Не импортируем decrypt_secrets (его импорт регистрирует atexit+signal…)»
- Scenario: Ctrl-C оператора во время фазы, которая импортировала decrypt_secrets, убивает весь оркестратор; любое будущее библиотечное переиспользование тихо hijack'ает сигналы
- Impact: глобальная подмена signal disposition не-owning процессами
- Minimal fix: регистрировать handlers только в main()/CLI-entry; expose cleanup_temp_files() как явный API

## ARCH-0016 — bootstrap idempotency: done-статус вечен для φ1–φ7/φ9–φ10 (hash-invalidation только φ8+)
- Severity: HIGH · Confidence: HIGH · Churn: M · WHEN: pre-launch
- Files: internal/bootstrap/lifecycle/state_machine.py:265-271,579-602
- Symbols: _HASH_INVALIDATED_PHASES, phase_needs_rerun
- Evidence: `frozenset({DEPLOY_SERVICES, CONVERGE_SERVICES, REGISTRY_UPDATE, DEPLOY_UPDATE, CONVERGE_UPDATE})` + `if phase_value not in _HASH_INVALIDATED_PHASES: return False` — для остальных фаз done персистит навсегда
- Scenario: инвариант «bootstrap-node второй вызов = no-op» выполняется даже когда ВХОДЫ изменились: правка cert provider/ssh keys/firewall → повторный bootstrap молча SKIP'ает φ1–φ7 → устаревший security posture уезжает в launch
- Impact: stale firewall/certs/users после re-provisioning
- Minimal fix: распространить hash-invalidation на φ1–φ7/φ9–φ10 либо явный skip-notice (DRIFT-style) в выводе

## ARCH-0017 — мутации вне state machine не имеют аналогичного guard'а (asymmetry)
- Severity: MEDIUM · Confidence: HIGH · Churn: S/M · WHEN: new-context — pre-launch; receive — post-launch
- Files: scaffold/context_initializer.py:149-153; deploy/receive_flow.py:505-598
- Symbols: mkdir(exist_ok=False), ReceiveFlow.run
- Evidence: `hermes_dir.mkdir(parents=True, exist_ok=False)` — повторный new-context после частичной неудачи = hard error вместо resume; receive деплоит идентичный payload безусловно (только file-lock от параллельности, orchestrator.py:24)
- Impact: recovery от partial failure требует ручной чистки; лишний recreate контейнеров на identical push
- Minimal fix: resume/overwrite-путь для new-context; payload-content-hash skip в receive

## ARCH-0018 — список из 14 фаз продублирован трижды в одном файле (+проза в AGENTS.md)
- Severity: MEDIUM · Confidence: HIGH · Churn: M · WHEN: post-launch
- Files: state_machine.py:191-209 (INIT_PHASE_ORDER), 226-246 (_phase_dependency_graph), 265-271 (_HASH_INVALIDATED_PHASES)
- Evidence: три независимых перечисления одного множества фаз должны синхронизироваться вручную
- Scenario: добавление фазы требует 3 правки; пропуск одной — silent (нет hash-invalidation) или loud (PhaseDependencyError fail-safe работает — проверено execute_phase:693)
- Impact: drift между order/dependency/hash-семантикой
- Minimal fix: генерировать _HASH_INVALIDATED_PHASES из dependency graph; parity-gate

## ARCH-0019 — lazy imports как обход init-order coupling (кластер 23 bootstrap + 16 deploy)
- Severity: MEDIUM · Confidence: MED · Churn: S/M · WHEN: post-launch
- Files: deploy/context_promoter.py:312; orchestrator.py:929,1042,1203; org_secrets_provisioner.py:157; age_key_backup.py:353 и др.
- Evidence: `from … import …  # лениво` — откладывают side effects (ARCH-0014/0015) до call time, маскируя хрупкий порядок импортов
- Scenario: новый eager-импорт любого из этих модулей меняет поведение в произвольных точках (order-dependent logging/signal state)
- Impact: частичные failure paths расходятся в зависимости от истории импортов
- Minimal fix: убрать первопричины (ARCH-0014/0015); lazy оставить только для тяжёлых зависимостей

## ARCH-0020 — env-переменные читаются один раз на import (замороженные дефолты)
- Severity: LOW · Confidence: MED · Churn: S · WHEN: post-launch
- Files: scaffold/context_initializer.py:49-51 (`_DEFAULT_NODE = os.environ.get("NODE", "tronyx-vps")`), project_remover.py:78, project_lister.py:48,52; modules/status-page/app.py:54-66 (LISTEN_PORT)
- Evidence: module-level `os.environ.get(...)` — поздние изменения env игнорируются
- Scenario: тестовый harness/CLI ставит NODE после import — получает stale дефолты; status-page требует env строго до старта процесса
- Impact: неверные node/org в scaffold-операциях при специфичных порядках запуска
- Minimal fix: ленивое чтение внутри функций / config-object

## ARCH-0021 — assemble_payload оставляет tar.gz в /tmp при неудаче (cleanup на caller'е без finally)
- Severity: LOW · Confidence: LOW/HYPOTHESIS (интеракция с retry не проверена) · Churn: S · WHEN: post-launch
- Files: deploy/payload_deliverer.py:142,171; orchestrator_cli.py:633
- Evidence: docstring «caller responsible for cleanup»; mkstemp(suffix=".tar.gz"); caller без try/finally
- Impact: накопление мусора в /tmp на CI-retry, disk pressure на узких нодах
- Minimal fix: finally/context-manager на единственном call site

## Checked clean
- receive_flow.py rmtree в finally (:461-486,596-598); payload_deliverer tmp cleanup (:267-269)
- Порядок стадий enforced data structure (dependency graph), не scattered string matching; PhaseDependencyError — fail-safe подтверждён
