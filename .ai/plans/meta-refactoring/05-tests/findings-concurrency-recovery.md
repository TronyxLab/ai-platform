# Direction 7 — Concurrency & Recovery

Агент: adversarial-аудит направления «concurrency/recovery» · Дата: 2026-08-22

Итог направления: concurrency-реализм бимодальный. Там, где DevPlan 136 W9 требовал regression-тестов, качество превосходно: ровно ОДИН тест с реальными потоками во всех 176k LOC (test_state_store_concurrent_writers.py) плюс настоящий interprocess flock holder-паттерн в deploy-lock тестах — эти два файла золотой стандарт. Всё остальное fork-related — чистые моки: slot limit parallel_runner, zombie reaping и агрегация group failures никогда не исполняются с реальным процессом, а один статический grep-тест («os.fork присутствует») даёт ложную уверенность. Аудит нашёл конкретную травму от этой практики: fake drain-функции не мутируют pid_to_name как реальный drain_all_count, что прячет вероятный продакшн-баг (пропуск ВСЕХ healthcheck в DEPLOY_PARALLEL режиме, TEST-061) — ровно тот failure mode, который предсказывает mock-concurrency теория. Честная оценка рисков: для single-operator CLI платформы межпроцессные гонки вокруг bootstrap/converge действительно теоретичны (локи существуют, их contention пути протестированы), а конкурентная запись fingerprint cache почти безвредна; реальные пробелы — second-run идемпотентность, охраняемая только manual harness (TEST-062), signal safety master AGE-ключа (TEST-064) и flag-included parallel deploy путь, где моки скрыли semantic drift (TEST-061).

---

### TEST-060: FileLock — нет выделенных тестов; nested-reentrancy, timeout-poll и degrade-to-no-lock никогда не верифицировались
- Test: tests/unit/test_deploy_concurrent_lock.py + tests/unit/test_state_store_concurrent_writers.py (косвенно); НЕТ файла test_file_lock*.py
- Production code: core/internal/shared/file_lock.py FileLock (`_REENTRANT` реестр, acquire poll-loop, `_open_fd` деградация)
- Claimed guarantee: «Reentrant в пределах процесса», timeout>0 → poll до дедлайна, недоступный lock-каталог → WARN + разблокировка, stale-PID cleanup (инварианты модуля)
- Actual guarantee: протестирована только межпроцессная семантика (raw-flock holder ×5 contention, release после deploy/save, raise при timeout=0.0)
- Blind spot: nested acquire→release с проверкой глубины (deploy держит лок → DeployHistory.create_snapshot/prune берут ту же — сломанная reentrancy означает deadlock или преждевременный release); timeout>0 poll ветка (протестирован только timeout=0 путь); PermissionError деградация (молча работает без лока); held(); _cleanup_stale. _REENTRANT — глобальная mutable без fixture reset (риск межтестовой утечки)
- Possible production bug: регрессия reentrant-depth accounting вызвала бы deadlock или снятие собственного лока во время деплоя — ни один тест не заметит
- Recommended test: tests/unit/test_file_lock.py — nested acquire (outer instance держит, inner succeeds, inner release сохраняет outer через raw-flock проверку); timeout poll с holder-потоком, освобождающим ~0.1s → acquire succeeds; holder без освобождения → FileLockError; import-ассерт что _REENTRANT пуст после context exit
- Existing test to remove/merge: none
- Confidence: HIGH

### TEST-061: Fake drain прячет всегда-пустой `all_names` — групповые healthchecks в DEPLOY_PARALLEL режиме исполняются для НУЛЯ модулей
- Test: tests/unit/test_parallel_runner.py (`os.fork` замокан → 1; `drain_all_fn=lambda _, __: (0, 1, ["mod1"])` игнорирует pid_to_name), tests/unit/test_docker_orchestrator_rollback.py (тот же fake-drain паттерн — известен TEST-033), tests/unit/test_deploy_modules_packages.py (static grep литерала «os.fork»)
- Production code: core/internal/bootstrap/deploy/parallel_runner.py:344 (`all_names = list(pid_to_name.values())`, вычисляется ПОСЛЕ drain_all_count, который ВЫТАСКИВАЕТ все записи из pid_to_name) + deploy_orchestrator.py:554 (`_set_hc_marker()` вызывается безусловно → standalone healthcheck пропущен)
- Claimed guarantee: «Healthchecks исполняются после завершения всех деплоев… для ВСЕХ модулей группы» (инвариант parallel_runner); hc_done маркер оправдан тем, что «healthcheck уже сделан внутри группы»
- Actual guarantee: реальный drain_all_count очищает pid_to_name ДО вычисления all_names → цикл healthcheck перебирает 0 модулей, лог "total=%d" показывает 0; маркер всё равно пишется → в режиме DEPLOY_PARALLEL healthchecks не исполняются вообще (ни групповые, ни standalone)
- Blind spot: ни один тест не гоняет реальные форки: slot limit parallel_limit=4, zombie reaping при ChildProcessError, агрегация partial-group failures с >1 модулем — все на моках; fake drain сигнатуры (не мутируют pid_to_name) расходятся с реальным drain контрактом именно там, где это важно
- Possible production bug: ДА — групповой healthcheck пропускается в проде при DEPLOY_PARALLEL=true (маркер пишется, docker-фаза затем скипает standalone по phases/docker.py:223). Деплои «зеленеют» без единой health-проверки
- Recommended test: (a) unit с РЕАЛЬНЫМ drain_all_count + замоканными waitpid, ассертящий возвращённые имена совпадают с pid_to_name и healthcheck-цикл получает непустой all_names — сегодня провалится; (b) один fork-smoke с deploy_module_fn → module-level helper, зовущий os._exit(0/1) и файловый счётчик, ассерт max concurrency ≤ parallel_limit и отсутствие зомби (waitpid исчерпан)
- Existing test to remove/merge: слить два идентичных rollback-теста в test_parallel_runner.py (test_rollback_on_failure ≡ test_rollback_invokes_compose_down); заменить static grep os.fork в test_deploy_modules_packages.py на (b)
- Confidence: HIGH

### TEST-062: Инвариант double-run bootstrap охраняется ТОЛЬКО requires_node manual harness; большинство mutating helpers без second-run тестов
- Test: tests/integration/test_multi_bootstrap_idempotency.py (@requires_node — исключён из make check/gate); частично: test_cron_installer.py (already-present ветка с pre-seeded crontab), test_firewall.py::test_apply_docker_user_policy_idempotent, test_watchdog.py (cron/journald helpers: literal second call → mtime unchanged — эталонный шаблон)
- Production code: lifecycle/state_machine.py (phase skip по state.json), cron_installer.install_acme_cron, sudoers_generator batch, firewall apply
- Claimed guarantee: root AGENTS.md инвариант 6 — «make bootstrap-node строго идемпотентен; 2-й вызов = no-op»
- Actual guarantee: эмпирическая проверка только на живом test-VPS (~15-30 мин, manual, release-checklist шаг); CI никогда не исполняет операцию дважды
- Blind spot: test_state_machine.py гоняет init flow однократно (resume тестируется только single-phase); sudoers_generator без double-write→identical теста; дублированные cron строки / ufw rules / users проявляются только на живой ноде. already-present проверки cron_installer pre-seed состояние, не исполняя фактический второй вызов — пинят read-ветку, не write-then-recheck поведение
- Possible production bug: регрессия дубликации в любой φ1-φ13 фазе (install_acme_cron перестал матчить собственную строку) проходит весь CI
- Recommended test: double-run StateMachine.run_init_mode против tmp state.json с mocked phase runners — ассерт 0 исполнений фаз на втором проходе и неизменный hash; добавить second-call тесты в стиле test_install_cron_watchdog_idempotent для generate_module_sudoers и install_acme_cron (call → call again → identical output, no duplicates)
- Existing test to remove/merge: none (harness остаётся эмпирическим слоем)
- Confidence: HIGH

### TEST-063: fingerprint save_cache — ни один тест не гоняет concurrent writers или corrupt/partial cache; сам atomic_writer никогда не тестируется с двумя писателями
- Test: НЕТ для concurrent save_cache writers (grep по tests/: упоминание .json.tmp только в state_store suite); test_check_suite.py покрывает fingerprint stability/invalidate/replay через executor, но не race/cache-corruption запись; tests/unit/test_atomic_writer.py — single-process (success/validator/injected failure/bytes)
- Production code: core/internal/check_suite/fingerprint.py:196 save_cache (фиксированное path.with_suffix(".json.tmp") — покрытие дефекта по инструкции, сам дефект не пере-репортится); shared/atomic_writer.py
- Claimed guarantee: docstring save_cache: «атомарно… конкурентные executor'ы не портят файл»
- Actual guarantee: concurrent-writer гарантия неверифицирована; load_cache↔save_cache roundtrip и corrupt JSON→None ветки тоже без прямых тестов
- Blind spot: два одновременных make check (operator + CI, xdist + manual) пишут одно имя tmp — никто не наблюдает результат; единственный concurrent-writer atomic-тест во всём дереве находится у его consumer (state_store), парадоксально доказывая паттерн, но оставляя канонический writer без собственных race-тестов
- Possible production bug: известный fixed-tmp дефект (не пере-репортится); вне его риск ограничен (mkstemp+replace замена межпроцессно-безопасна для содержимого) — остаточный риск last-writer-wins потеря обновлений replay cache (безвредно)
- Recommended test: два потока × N save_cache вызовов в один путь → финальный файл valid JSON одного writer'а (скопировать state_store consistency шаблон); corrupt JSON → load_cache=None; прямой save/load roundtrip
- Existing test to remove/merge: none
- Confidence: HIGH на coverage gap; MED на severity

### TEST-064: decrypt_secrets DD5-3 signal handling (SIGTERM/SIGINT + atexit) без замены тестам после миграции с shell trap
- Test: tests/unit/test_decrypt_secrets.py (finally-путь: dd if=/dev/zero wipe, _TEMP_FILES пуст — хорошо покрыто); tests/unit/test_contract_decrypt.py:114 прямо констатирует «тесты trap удалены… логика очистки уже в Python» — но Python-тест signal handlers/atexit так и не добавлен; grep по tests/ _signal_handler/_cleanup_temp_files/SIGTERM: 0 результатов
- Production code: core/internal/secrets/decrypt_secrets.py:130-144 (_signal_handler → cleanup → SIG_DFL → self-kill; atexit.register; handlers SIGTERM/SIGINT)
- Claimed guarantee: DD5-3 «Очистка через atexit.register + обработчики сигналов SIGTERM/SIGINT (заменяет shell trap)» — security инвариант (master AGE ключ в /dev/shm)
- Actual guarantee: только in-process finally cleanup протестирована; kill-during-operation пути (systemd timeout-stop для platform-secrets.service, Ctrl-C оператора во время sops) без покрытия
- Blind spot: удаление signal.signal(...) регистрации или изменение порядка cleanup-before-reraise прошли бы весь suite; TRAP[DECISION] :31-35 сам помечает Rev replacement-контракта pending
- Possible production bug: не доказан (код выглядит корректным); риск — unguarded регрессия утекает ключ в /dev/shm после signal-завершения
- Recommended test: seed _TEMP_FILES tmp_path файлами → вызвать decrypt_secrets._signal_handler(signal.SIGTERM, None) с mocked os.kill → ассерт файлы wiped (zeroed)/удалены, список очищен, disposition сброшен; плюс import-time ассерт signal.getsignal(signal.SIGTERM) is decrypt_secrets._signal_handler
- Existing test to remove/merge: none
- Confidence: HIGH

### TEST-065: Watchdog sequences хорошо протестированы, кроме flapping-recovery и silent corrupt-state reset — расходится с каноном state_store
- Test: tests/unit/test_watchdog.py (healthy-noop, first-write, second-restart ≥10 мин, cooldown 30 мин, restart:no, RestartCount>5, dry-run, docker ps fail, R5 negative live-unhealthy — сильный набор)
- Production code: core/internal/healthcheck/watchdog.py:389-395 (_load_state: corrupt JSON → молча empty state) и переход healthy → очистить unhealthy_since
- Claimed guarantee: resilient recovery с cooldown; сравнить с bootstrap state_store, где тот же класс бага «corruption → silent fresh reset» исправлен fail-loudly (StateCorruptError, TRAP[BUG] state_store.py:281)
- Actual guarantee: watchdog state corruption молча теряет last_restart/unhealthy_since; ни один тест не кормит corrupt JSON watchdog'у; ни один не проверяет очистку unhealthy_since записи при восстановлении контейнера (healthy тест стартует с пустого state)
- Blind spot: flapping сценарий (unhealthy→recorded→healthy→entry-cleared→unhealthy-again) никогда не моделируется; после corruption (ручной truncate, partial write) watchdog может раньше времени рестартить recently-restarted live-but-unhealthy контейнер — защита RestartCount>5 тут НЕ помогает, т.к. ручные docker restart не инкрементят RestartCount
- Possible production bug: приглушённый — cron flock -n сериализация + порог 10 минут делают шторм маловероятным; state loss после одного corruption события реалистична, последствия ограничены одним лишним рестартом
- Recommended test: 3-progression FakeRunCmd: unhealthy→recorded, healthy→cleared, unhealthy→re-recorded (flap); corrupt state JSON → ассерт empty-state IMP warning log (и решить: должен ли watchdog матчить fail-loudly семантику state_store)
- Existing test to remove/merge: none
- Confidence: MED
