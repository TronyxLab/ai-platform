# Direction 8 — Flaky Test Sources

Агент: adversarial-аудит направления «flaky tests» · Дата: 2026-08-22

## Sleep Census (полная таблица)

| File:line | Sleep | Слой | Назначение | Вердикт |
|---|---|---|---|---|
| tests/unit/test_status_page.py:1094 | 3.0s | unit | Симуляция медленного upstream (`_slow_health`) | Load-bearing сценарий, реальная стоимость |
| tests/unit/test_status_page.py:1109 | 0.3s | unit | «дать /health войти в блокировку» — race window | Scheduling-dependent |
| tests/unit/test_docker_orchestrator.py:615 | 0.5s | unit | Ожидание forked child после pre_pull_images | Lazy + scheduling-dependent |
| tests/unit/test_check_suite.py:817 | 30s child | unit | Child переживает timeout=1 → kill верифицирован | Load-bearing, bounded (~1s) |
| tests/unit/test_shared_subprocess_io.py:204 | 2.5s child | unit | Heartbeat emission над окном тишины | Load-bearing, wall-clock стоимость |
| tests/unit/test_shared_subprocess_io.py:225 | 300s child | unit | Timeout killpg тест (timeout=2) | Load-bearing, bounded |
| tests/test_hermes_init.py:423 | 2s ×15 | docker | Poll docker ps до появления контейнера | Load-bearing polling |
| tests/test_local_resilience.py:219,348 | interval/1s | docker | Restart-count polling | Load-bearing polling |
| test_smoke_{nginx,platform,infra_metrics,monitoring,logging,langfuse,litellm}.py | wait_s/retry_interval/backoff ×22 | smoke/docker | Retry backoff циклы | Load-bearing polling |
| tests/_conftest/{compose,health,containers,reuse,node}.py | 2-5s | fixtures | Service health polling | Load-bearing polling |
| tests/integration/test_flaky_detection.py:172 | 1s | integration | «дать нагрузке разогнаться» | Scheduling-dependent |
| tests/e2e/chaos_audit.py, test_chaos_resilience.py | 2-300s ×19 | e2e (requires_node) | Chaos settling windows | Вне make check; manual |

~75% sleep-сайтов — load-bearing polling в docker/smoke слоях, покрытые существующей quarantine-политикой (docker/network only). Экспозиция — строки unit-слоя.

Итог направления: общая flakiness-экспозиция LOW-to-MODERATE и хорошо сдержана архитектурой: docker/smoke слои держат ~85% реальных sleeps и уже покрыты docker-only quarantine протоколом (инвариант 11 tests/AGENTS.md), а детерминированный unit/gates слой показывает необычно хорошую clock-дисциплину (замоканный time.time в context-overlay, backdated mtimes с громкими guard-ассертами в wal_sync). Четыре actionable дефекта остаются, все в unit-слое: вакуумный fork-тест (TEST-071 — худший: fork-in-threaded-worker опасность + нулевая assertion-сила), race 0.3s entry-window (TEST-072), несбрасываемый _REENTRANT глобал (TEST-073 — латентный до первого mid-test исключения), и повторяющийся паттерн direct-env-write (TEST-074 — тот же класс, что отгруженный инцидент 2026-08-02, т.е. эмпирически самый дорогой flake-вектор этой команды). Ничто из этого сейчас не жжёт CI на постоянной основе; ожидаемый вклад в steady-state red rate <1% unit-прогонов, сконцентрировано на нагруженных xdist worker'ах.

---

### TEST-071: Fork-based pre_pull тест не ассертит ничего и делает fork внутри pytest
- Test: tests/unit/test_docker_orchestrator.py:599-623 (`test_pre_pull_images_single`, sleep на :615)
- Production code: bypassed seam — `prunner.pull_module_images` monkeypatch голым attribute assignment (не monkeypatch.setattr), затем os.fork dispatch в dorch.pre_pull_images
- Claimed guarantee: pre_pull_images dispatch'ит в parallel_runner и возвращает счётчики
- Actual guarantee: «returns two ints» — комментарий признаёт «we can't reliably assert the count»; sleep(0.5) надеется, что дети завершатся до finally-восстановления функции
- Blind spot: (a) ассерты нефальсифицируемы (R2 территория — isinstance(int) всегда true); (b) os.fork внутри xdist worker'а, исполняющего threaded тесты (ThreadingHTTPServer тесты делят worker pool) — fork+threads небезопасно, особенно на macOS; (c) если порядок fork когда-нибудь станет ленивым dispatch, поздний child исполнит РЕАЛЬНЫЙ docker pull
- Possible production bug: masked regression — сломанный dispatch, возвращающий (0,0), проходит идентично
- Recommended test: инжектировать pull callable через существующий DI seam (паттерн orphan_reconciler_impl, DevPlan 167 D3) вместо attribute patch + fork; ассерт fake вызван N раз синхронно; удалить sleep
- Existing test to remove/merge: none (этот тест сам кандидат на замену)
- Confidence: HIGH

### TEST-072: /healthz fast-path тест гонится с окном входа 0.3s и ассертит wall-clock latency
- Test: tests/unit/test_status_page.py:1071-1124 (`test_slow_health_does_not_block_healthz`)
- Production code: n/a (ThreadingHTTPServer исполнен по-настоящему, ephemeral port 0 — port hygiene корректна)
- Claimed guarantee: медленный /health никогда не блокирует /healthz (<1.5s)
- Actual guarantee: если фоновый поток не вошёл в 3s sleep _slow_health за time.sleep(0.3) (:1109), тест проходит БЕЗ исполнения регрессионного условия (false pass); наоборот, на нагруженном -n auto runner'е thread-start + accept latency может задвинуть elapsed за 1.5s (false fail)
- Blind spot: нет синхронизации, доказывающей, что /health находится mid-handler перед вызовом /healthz; latency bound меряется на общем CPU CI
- Possible production bug: напрасный CI red на занятых раннерах; регрессия молча неверифицирована на тихих
- Recommended test: заменить надежду 0.3s на event — _slow_health выставляет threading.Event после входа, тест ждёт его (с timeout) перед пробой /healthz; elapsed-ассерт оставить с увеличенным запасом или ассертить только status==200
- Existing test to remove/merge: none
- Confidence: HIGH (механика), MED (наблюдаемая частота флейков)

### TEST-073: file_lock._REENTRANT process-global dict никогда не сбрасывается между тестами
- Test: core/internal/shared/file_lock.py:62 потребляется tests/unit/test_deploy_concurrent_lock.py, tests/unit/test_state_store_concurrent_writers.py; ноль ссылок на _REENTRANT где-либо в tests/
- Production code: реальный seam — module-level mutable dict keyed by resolved path
- Claimed guarantee: семантика lock acquire/release верифицирована per test изолированно
- Actual guarantee: держится только если каждый предыдущий тест того же xdist worker'а релизнулся чисто; любое исключение между acquire() и release() в протестированном production-коде (или будущем тесте) оставляет depth≥1 → последующий acquire() шорткатится как reentrant no-op → тесты взаимного исключения становятся вакуумно зелёными
- Blind spot: нет autouse reset fixture (контраст: test_converge_audit.py:106 `_restore_audit_globals` показывает собственный канонический фикс-паттерн команды ровно для этого класса утечки)
- Possible production bug: false green регрессий T9.x lock-release (ровно та гарантия, ради которой эти тесты существуют)
- Recommended test: autouse fixture в обоих файлах: save/clear/restore file_lock._REENTRANT вокруг каждого теста; опционально canary, ассертящий пустоту post-yield
- Existing test to remove/merge: none
- Confidence: MED (путь утечки требует mid-test исключения сегодня; структурный риск определён)

### TEST-074: Прямые os.environ записи без per-test undo в s3 client тестах
- Test: tests/unit/test_shared_s3_client.py:44-47,72-76,90-91 (также tests/unit/test_ssl_s3_cache.py:133-264, который восстанавливает вручную)
- Production code: bypassed seam — boto3 замокан; env пишется прямо в process state
- Claimed guarantee: env-fallback precedence верифицирована изолированно
- Actual guarantee: fixture clean_env (monkeypatch.delenv) санитизирует только запросившие её тесты; сырые `os.environ["S3_ACCESS_KEY"]="s3-key"` записи персистят в worker'е после конца теста и утекают в любой поздний тест этого worker'а, читающий S3_*/AWS_* без clean_env
- Blind spot: это точный класс уже отгруженного отказа — TRAP[BUG] 2026-08-02 в test_status_page.py (утечка NODE_NAME ломала node-lifecycle тесты между файлами); там починено архитектурно (DI), здесь — нет
- Possible production bug: order-dependent падения downstream тестов, чья причина указывает мимо виновника (документированный симптом: вводящий в заблуждение «Expected NODE_NAME-required diagnostic»)
- Recommended test: провести записи через существующий monkeypatch.setenv внутри тестов (fixture уже в scope), либо расширить clean_env snapshot/restore шести ключей post-yield
- Existing test to remove/merge: none
- Confidence: HIGH

### TEST-075: Real-time subprocess streaming тесты платят секундами wall clock за прогон
- Test: tests/unit/test_shared_subprocess_io.py:201-233 (child спит 2.5s; child спит 300s, убивается на timeout=2), tests/unit/test_check_suite.py:817 (30s child, timeout=1)
- Production code: реальный seam (намеренно — killpg/orphan поведение невозможно честно сфейкать)
- Claimed guarantee: heartbeat emission, timeout kill, сохранение partial-output
- Actual guarantee: корректное поведение, но heartbeat=1 требует ≥1s наблюдаемой тишины — под тяжёлым xdist CPU contention startup интерпретатора + первый read могут джиттерить; killpg тесты зависят от signal delivery, ни с чем не гонящегося (robust), но жгут ~2-3s каждый в unit gate
- Blind spot: суммарно добавленная unit-gate latency (~8-10s) приглашает кого-то «оптимизировать» таймауты плотнее позже, создавая флейк ретроактивно
- Possible production bug: nothing masked; cost-only находка
- Recommended test: оставить как есть (честно, bounded); пометить общим @pytest.mark.slow_realtime тегом, чтобы будущая подстройка таймаутов была видна в review
- Existing test to remove/merge: none
- Confidence: MED

### TEST-076: Wall-clock boundary аудит — expiry/retention/cooldown тесты защищены; два тонких запаса остаются
- Test: tests/unit/test_context_overlay.py:102-179 (cache TTL полностью замокан: @patch("context_overlay.time.time")); tests/unit/test_wal_sync.py:308-316 (явный os.utime backdating ПЛЮС self-checking guard assert, громко фейлящийся, не флеймящийся); tests/unit/test_dead_code_checker.py:183-232; tests/unit/test_practices_maturity.py:166-217; tests/unit/test_platform_export_metrics.py:720-751
- Production code: n/a (часы инжектированы/замоканы на швах)
- Claimed guarantee: pass/fail независимо от момента запуска suite
- Actual guarantee: держится всюду в аудите кроме двух запасов: (a) test_practices_maturity.py:217 ассертит `(now - commit_dt).days >= 4` для коммита dated now-5d — 1 день запаса переживает timezone/DST нормализацию _git_commit_at, но локаль с >1h offset handling в git date parsing эродирует его; (b) guard test_wal_sync.py:316 использует days_old - 1 арифметику — корректно, но молча предполагает отсутствие filesystem mtime granularity грубее 1 дня
- Blind spot: системного нет; оба запаса ≥1 дня — вероятность переворота близка к нулю
- Possible production bug: none plausible
- Recommended test: действий не требуется; при касании maturity-теста расширить до >= 3 для симметрии с собственным intent
- Existing test to remove/merge: none
- Confidence: HIGH (что они не-flaky)
