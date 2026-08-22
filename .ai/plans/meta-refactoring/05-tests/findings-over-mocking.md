# Direction 4 — Over-Mocking

Агент: adversarial-аудит направления «over-mocking» · Дата: 2026-08-22

TOP-10 mock-heavy файлов (комбинированные `MagicMock|patch(|patch.object|patch.dict` hits):
```
39 tests/unit/test_docker_auth.py        31 tests/unit/test_telegram_notifier.py
37 tests/unit/test_ssl_certs.py          29 tests/unit/test_node_yaml_cli.py
36 tests/unit/test_secrets_manager.py    28 tests/unit/test_context_overlay.py
34 tests/unit/test_nginx_harness.py      25 tests/unit/test_dev_cert_generator.py
34 tests/unit/test_docker_orchestrator.py
32 tests/unit/test_deploy_orchestrator.py
```
Контекст: tests/gates практически mock-free (3 hits в 1 файле из 134) — gate-дисциплина отличная. Глобальных патчей time.sleep/time.time ноль. patch.dict(os.environ) сайты (13) все с clear=True внутри with — утечек нет.

Итог направления: mock-дисциплина выше среднего и неравномерна характерным образом. Домашние DI-швы реальны и используются хорошо там, где существуют: test_retry.py и test_channels.py образцовые (паттерн recorder `sleep_fn=sleeps.append`, ноль time-патчей), test_monitoring_post_deploy.py инжектит render steps конструкторным DI, test_modules_healthcheck.py использует invoke_fn, gates/ почти без моков, env-гигиена чистая. Находки кластеризуются там, где швов нет или их игнорируют: process-boundary orchestration (fork/drain в TEST-031/033) откатывается к мокам, которые либо не доказывают ничего (isinstance-ассерты), либо реимплементируют приватные протоколы; две production DI-инвестиции (healthcheck_poll `docker=`/`sleep_fn=`, orphan_reconciler_impl) honor'ятся одними тестами и обходятся другими в тех же файлах — обходы (TEST-032, TEST-035) заодно самые хрупкие, замораживая call counts и monotonic-последовательности, которые реализация вправе менять. MEANINGLESS-находка, скрывающая живой продакшн-баг, не обнаружена, но TEST-031/032/035 каждая останется зелёной сквозь реальные регрессии pre-pull dispatch, poll-timeout и cache-hit логики. Вердикт: 6 находок — 1 MEANINGLESS, 5 BRITTLE; порядок ремедиации 032 (мёртвые швы, самый дешёвый фикс, prod TRAP уже требует), 031, 035, 033, 034, 036.

---

### TEST-031: Fork-based pre-pull тест ассертит только isinstance — исход мока невидим сквозь fork
- Test: tests/unit/test_docker_orchestrator.py:599-623 (`test_pre_pull_images_single`)
- Production code: `docker_orchestrator.pre_pull_images` (parallel dispatch, подсчёт per-module pull failures)
- Claimed guarantee: «fork dispatches to parallel_runner.pull_module_images» (TRAP-комментарий :598)
- Actual guarantee: pre_pull_images возвращает 2-tuple int'ов. Это всё.
- Blind spot: child-процесс мутирует MagicMock в copy-on-write памяти; родитель не видит child-вызовы. Собственный комментарий теста признаёт: «we can't reliably assert the count since it depends on process scheduling» (:617-619). Ассерты `isinstance(ok, int)`/`isinstance(fail, int)` (:620-621) — R2-нефальсифицируемые type-checks. Плюс реальный `time.sleep(0.5)` (:615) — медленно и scheduling-flaky.
- Possible production bug: child падает или зовёт реальный pull_module_images (мок неэффективен post-fork на некоторых путях) → pre-pull failures молча считаются успехом; здесь необнаружимо
- Recommended test: заменить fork на injected runner seam (домашний паттерн: `orphan_reconciler_impl=` DI на :663-671 — модель). Добавить параметр `pull_fn`/`executor` в pre_pull_images; fake возвращает (ok=1, fail=0); ассерт dispatch-аргументов. Без fork, без sleep.
- Existing test to remove/merge: тело теста; имя сохранить для DI-rewrite
- Confidence: HIGH

### TEST-032: healthcheck_poll timeout тест обходит построенные для него sleep_fn/docker DI-швы
- Test: tests/unit/test_shared_docker_compose.py:292-325 (`test_healthcheck_poll_timeout`)
- Production code: `docker_compose.healthcheck_poll` (docker_compose.py:520-527) — параметры `docker:` и `sleep_fn:` добавлены DevPlan 167 D2 с in-code TRAP[DI-SEAM] (:548-549), декларирующим «0 monkeypatch в тестах»
- Claimed guarantee: timeout-путь даёт «unhealthy» после дедлайна
- Actual guarantee: захардкоженный 12-значный `time.monotonic` side_effect список (:304-307) ведёт цикл, чей iteration count тест заморозил; time.sleep и docker_ops.subprocess.run тоже замоканы (:302-303)
- Blind spot: если цикл легитимно зовёт monotonic на раз/два больше (переставлен deadline-check), StopIteration убивает тест — хрупкость by construction. Ни один из 4 healthcheck_poll тестов файла не использует `docker=`/`sleep_fn=` (верифицировано: ноль вхождений) → production DI-швы мертвы, непокрыты
- Possible production bug: регрессия шва (sleep_fn игнорируется, реальный sleep) невидима; реальный poll-цикл, спящий 3s за итерацию в unit-прогонах — ровно failure mode, от которого шов строился
- Recommended test: fake docker object с `_DockerOpsProtocol` (running|unhealthy), sleep_fn-recorder, timeout=1. Часы — единственный пробел: в production нет clock_fn параметра (time.monotonic :558,560); добавить рядом с sleep_fn — тогда тесту не нужен ни один патч
- Existing test to remove/merge: 4 poll-теста слить в 2 (healthy-path, timeout-path) на DI
- Confidence: HIGH

### TEST-033: Rollback тест подделывает drain-функции, реимплементируя их приватный mutation-протокол
- Test: tests/unit/test_docker_orchestrator_rollback.py:74-114 (`_fake_drain_completed`/`_fake_drain_all`, используются 3 тестами)
- Production code: drain-цикл `parallel_runner.deploy_docker_group` + декодирование exit-status `os.waitpid` (parallel_runner.py:153,314)
- Claimed guarantee: «atomic success-or-rollback» (W5-E1 AC-1) — failure в группе → `docker compose down` всех siblings
- Actual guarantee: rollback по pre-digested списку failures исполняет `compose down` per module (само rollback-решение верифицировано честно через capture compose_down_calls). Но весь child-result pipeline подделан: os.fork→12345, os.waitpid→(0,0), drain-фейки мутируют структуры pids/pid_to_name caller'а in place, форсируя выход цикла (:74-88)
- Blind spot: фейки дублируют внутренний контракт drain_completed_count (pop pid, mutate pid_to_name, return (done, failed, names)). Любой легитимный рефакторинг контракта (dataclass вместо мутации входов) ломает все 3 теста; наоборот, реальные баги — misdecoding waitpid-статуса (WIFEXITED), zombie leak на failure-пути, time.sleep(1) slot-waiter spin — здесь нетестируемы. Патч os.waitpid избыточен (drain патчится поверх)
- Possible production bug: маппинг exit-status→success инвертирован или статус не waited → rollback на успехе или пропуск failures; suite остаётся зелёным
- Recommended test: оставить subprocess/fork на границе, но вести цикл fake runner'ом с реальными CompletedProcess exit codes и пустить настоящий drain-logic, заменяя os.waitpid только на syscall-краю — либо добавить drain_fn DI seam (house style) и фейкать на шве контракт-шейпнутыми возвратами, без in-place мутации
- Existing test to remove/merge: none; переработать helper `_call_group_deploy`
- Confidence: MED

### TEST-034: Deploy-order тест определяет тот topo-порядок, который заявляет верифицировать (recipe-testing)
- Test: tests/unit/test_deploy_orchestrator.py:586-640 (`test_deploy_parallel_calls_topo_sort`)
- Production code: ordering-семантика `deploy_orchestrator._deploy_parallel` (DevPlan 050: «parallel deploy order stops being topo-driven»)
- Claimed guarantee: порядок деплоя управляется топологическими группами
- Actual guarantee: `_deploy_parallel` пересылает всё, что вернул kahn_topological_sort, в deploy_docker_group + 4 assert_called_once_with collaborator-проверки (:601-604, :613-617). Порядок захардкожен в моке: `return_value=[["postgres"], ["redis"]]`
- Blind spot: цикл в build_dag, неверное направление рёбер или обход kahn хардкодом проходят, пока вызов происходит. TRAP-комментарий заявляет защиту регрессии topo-driven порядка — тест не может обнаружить её потерю. (Митигация: реальная семантика живёт в tests/unit/test_topo_sort.py:84-210 с настоящими DAG — хорошая layering; этот тест — чистый wiring в костюме семантики)
- Possible production bug: _deploy_parallel деплоит dependents раньше dependencies после рефакторинга, поменявшего group iteration — необнаружено
- Recommended test: фейкать только load_module_yams (file boundary); гонять реальные build_dag+kahn на 2-уровневом DAG; ассертить через call order, что deploy_docker_group получил [postgres] раньше [redis]. Минус 3 из 4 collaborator-ассертов
- Existing test to remove/merge: слить с вариантами test_deploy_parallel (:91, :135), мокающими тот же pipeline
- Confidence: MED

### TEST-035: Context-overlay cache тест патчит весь класс Path
- Test: tests/unit/test_context_overlay.py:100-127 (`test_ensure_context_pull_cached`; тот же паттерн :141, :264, :352)
- Production code: cache read/write timestamp-файла в `context_overlay.ensure_context_repo` (time.time на context_overlay.py:177)
- Claimed guarantee: свежий pull (<300s) → пропуск git pull (S9 cache)
- Actual guarantee: арифметика `int(time.time()) - int(timestamp_str) < 300` над MagicMock. Патчится сам Path (`@patch("context_overlay.Path")`) — построение пути, чтение файла, обработка whitespace/corrupt-timestamp непротестированы; os.path.isdir→True и subprocess.run патчатся сверху (4 stacked патча)
- Blind spot: если реализация переключится на open()/read_text(encoding=...) или добавит второе использование Path, blanket-мок молча reroute'ит ветки. Комментарий теста сам miscompute'ит сценарий («elapsed=950» для now=1000, ts=950 — elapsed 50). В production нет clock seam, так что фейкать время вынужденно — но Path фейкать не нужно: tmp_path существует
- Possible production bug: cache timestamp пишется в другой путь, чем читается (always-miss или always-hit) — необнаружимо; always-hit пропускал бы pull'ы вечно в production
- Recommended test: реальный tmp_path для timestamp-файла (записать «950»), `monkeypatch.setattr(context_overlay.time, "time", lambda: 1000)`, оставить только subprocess-патч. Ассерты те же
- Existing test to remove/merge: none; de-mock 4 тестов на месте
- Confidence: HIGH

### TEST-036: Blanket `os.path.isfile → True` filesystem-моки рядом с доступными tmp_path фикстурами
- Test: tests/unit/test_cron_installer.py:78 (`test_install_cron_already_present`); тот же паттерн tests/unit/test_llm_provision.py:50, :68, :93; tests/unit/test_docker_auth.py:344, :380-383 (isdir/exists/isfile все → True)
- Production code: file probing в cron_installer.install_acme_cron, llm_provision, preconditions docker_auth
- Claimed guarantee: идемпотентный no-op при наличии cron entry / ветвление по presence файлов
- Actual guarantee: ветка взята под аксиомой «каждый файл, который SUT может проверить, существует». Sibling-тест test_install_cron_fresh (:105) показывает лучший дискриминирующий isfile side_effect — но всё ещё мокает вместо записи реального файла, который сам же именует через _mk_acme_sh(tmp_path)
- Blind spot: если реализация добавит ещё одну existence-пробу (crontab binary, второй скрипт), blanket-True молча перещёлкнет ветку — тест верифицирует путь, который не может произойти, или маскирует пробу не того пути. Домашнее правило: filesystem на tmp_path — не мокинг; у этих тестов tmp_path уже в scope и используется для всего, кроме проверяемого условия
- Possible production bug: install_acme_cron проверяет переименованный путь acme.sh → blanket True прячет промах; cron установлен против несуществующего бинарника
- Recommended test: создавать файлы, которые подразумевает сценарий, под tmp_path (acme.sh реальный; s3_ssl_cache.py реальный для fresh, отсутствующий для already-present, где маркер несёт crontab output); оставить замоканным только subprocess.run (настоящая граница). Патчи os.path.isfile удалить
- Existing test to remove/merge: none; in-place de-mocking, ~10 сайтов
- Confidence: MED
