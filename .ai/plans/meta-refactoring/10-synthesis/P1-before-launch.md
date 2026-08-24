# P1 — Before Launch (10-Synthesis)

Критерий: CONFIRMED-дефекты высокого риска, не блокирующие сам запуск, но резко повышающие вероятность/цену инцидента в первые недели; плюс дешёвые фикс-пакеты, устраняющие системные паттерны. Порядок — по value/churn. Формат полей тот же, что в P0. Нумерация REF-0101+.

---

## REF-0101 · Credential↔project binding: один CI-ключ управляет всеми проектами ноды

* **Problem:** Один canonical authorized_keys entry для ci-deploy; dispatch валидирует только синтаксис имени — никогда то, какому проекту принадлежит предъявленный ключ. Утечка любого из N repo-ключей = receive/remove/status ЛЮБОГО проекта.
* **Evidence:** `verbs.py:29-36`, `orchestrator_cli.py:457-467/:556-564`, `receive_flow.py:349-365`, `users.py:39`.
* **Source findings:** SEC-0006 (HIGH·B3sec, conf 0.95, must-fix YES), SEC-0042 rider (version-token grammar).
* **Files:** bootstrap/lifecycle/helpers/users.py, shared/verbs.py, deploy/orchestrator_cli.py, deploy/receive_flow.py, CI secrets provisioning (per-repo keys).
* **Root cause:** per-key environment binding отсутствует (`environment="PLATFORM_ALLOWED_PROJECT=<n>"`).
* **Recommended change:** per-project authorized_keys строки с environment-binding, enforce в _dispatch/validate (project arg == PLATFORM_ALLOWED_PROJECT); interim-минимум для launch-week: reject stray version-tokens + явная запись residual в threat-model.
* **Tests required:** dispatch-negative: чужой ключ → JSON ERROR rc=1 до handler'а.
* **Regression risk:** средний: требует ротации ключей во всех repo-CI перед включением — phased: enforce только при наличии binding.
* **Dependencies:** независим; усиливается после REF-0006 (root-двери закрыты раньше).
* **Estimated complexity:** M.
* **Why now:** единственный оставшийся cross-tenant blast radius.
* **Why not larger refactor:** без per-project payload-signature инфраструктуры — ровно два места проверки.

---

## REF-0102 · Мёртвый FQDN-preflight на каждом деплое + расходящиеся валидаторы имён

* **Problem:** Engine резолвит несуществующий `internal/validate/validate.sh` → IMP:6 «skipping FQDN check» — canonical guard уникальности доменов никогда не исполняется (fail-open вопреки docstring «Raises ValidationError»): второй проект с тем же FQDN молча перехватывает vhost. Параллельно `validate_project_name` пропускает `_`/>63 символов, auto_domain строит невалидный домен → перманентный exit-4 render после создания проекта; auto_domain молча возвращает "" на свежей машине.
* **Evidence:** `engine/engine.py:92-96/:438`; `preflight.py:63-79`; `project_registry.py:105`; `project_scaffolder.py:78-88`; `vhost_renderer.py:536/:567-570`.
* **Source findings:** A-37 («first S-fix» аудита), BUG-1001 (HIGH), BUG-1004.
* **Files:** core/internal/deploy/engine/engine.py, scaffold/project_scaffolder.py, shared/project_registry.py.
* **Impact:** silent vhost-hijack класс; half-created проекты блокирующие converge/render.
* **Recommended change:** repoint на entrypoints/validate.sh или прямой вызов validate_orchestrator (заодно убивает python→shell инверсию); raise branch до IMP:8/WARN; ≤63 chars + forbid `_` ДО любой мутации в scaffolder; ConfigValidationError при пустом domain.
* **Tests required:** duplicate-FQDN негатив; validator-parity тест name↔domain грамматик.
* **Regression risk:** низкий (<10 LOC).
* **Dependencies:** независим.
* **Estimated complexity:** S.
* **Why now:** помечено аудитом первым S-фиксом; ловится в момент onboarding проектов launch-week.
* **Why not larger refactor:** пере-address пути, не трогая pipeline валидации.

---

## REF-0103 · Deploy-path таймауты: 60-секундное окно реально ~21–41 мин, hang'и без дедлайна

* **Problem:** Вложенные бюджеты: poller 20×(60+3)s ≈ 21 мин на project (задокументировано 60s); cold-skip gate спит 60s до проверки существования compose-file; healthcheck-invoke наследует COMPOSE_UP_TIMEOUT=180s вместо 60s SoT; φ11 serial retries ≈390s/module; git push --mirror и docker_login без timeout/GIT_SSH_COMMAND → вечный hang release/bootstrap; lib/ssh.sh default 600s vs Python SoT 900s (+ложный parity-комментарий); module_interface timeout убивает только bash (внуки docker живут); TimeoutExpired вне except-списков context_deployer/llm_provision → crash после N деплоев; bootstrap deploy-many timeout → (0,[]) exit 0.
* **Evidence:** `healthcheck_poller.py:143-157`, `context_deployer.py:716-719/:376`; `modules_healthcheck.py:243-249`; `reporting.py:90-91/:130-139`; `context_promoter.py:171-179`; `docker_auth.py:117-124`; `lib/ssh.sh:111` vs `timeouts.py:130`; `module_interface.py:83-86`; `context_deployer.py:1093/:1117/:1173`; `deploy_orchestrator.py:607-610`.
* **Source findings:** BUG-0201≡PERF-001/003, PERF-050/AI-0012/AI-0014, PERF-010, BUG-0204/AI-0013, BUG-0205, AI-0020(+AI-0002/0039), BUG-0203, BUG-0603, DATA-606, FAIL-0304 (litellm request_timeout unset — сюда же), A-08 adjacent.
* **Files:** core/internal/deploy/healthcheck_poller.py, bootstrap/deploy/{context_deployer,deploy_orchestrator}.py, core/internal/shared/{module_interface,docker_auth}.py, core/lib/ssh.sh, monitoring config (request_timeout), llm pipeline excepts.
* **Impact:** один нездоровый проект блокирует очередь деплоя десятками минут; зависший registry/git морозит release-checklist step 4 бесконечно.
* **Recommended change:** единый monotonic deadline (start+max_retries×interval) прокидывается вниз; single-shot skip-gate; HEALTHCHECK_CMD_TIMEOUT=60 для liveness-invokes; GIT_SSH_COMMAND из ssh_opts + DEPLOY_TIMEOUT на mirror; DOCKER_AUTH_TIMEOUT; lib/ssh.sh default ← 900 + fix комментария/TRAP; killpg через subprocess_io canon; добавить SubprocessError в 5 except-кортежей; TimeoutExpired → все незавершённые = failed (не (0,[])); litellm request_timeout: 120s одной строкой конфига.
* **Tests required:** wall-time budget тест poller (≤ бюджет); argv-тест mirror (GIT_SSH_COMMAND присутствует); except-таблица тест SubprocessError.
* **Regression risk:** средний: сокращение окон может красить легитимные slow-start — согласовать с start_period (REF-0003).
* **Dependencies:** частично перекликается с REF-0018 (status-page hang family) — разные файлы.
* **Estimated complexity:** M (суммарно ~1 день, декомпозируется на 8 XS/S).
* **Why now:** launch-week = серия деплоев под давлением; текущие окна гарантируют pileup.
* **Why not larger refactor:** не переписываем polling-архитектуру — выравниваем бюджеты на SoT.

---

## REF-0104 · LLM key-store: одна OOM необратимо теряет ВСЕ virtual keys; lookup плодит дубликаты

* **Problem:** persist_project_key пишет JSON прямо open("w") без tmp/lock/fsync → truncate → следующий reader глотает JSONDecodeError в {} и ЗАЛИВАЕТ одно-ключевой store поверх всех; chmod после close (0644-окно plaintext); transient lookup failure неотличим от «no key» → GENERATE дубля ключа с тем же metadata; httpx exceptions не пойманы → mid-loop abort; /key/info скачивается целиком на каждого consumer (O(N²)), пагинации нет → возможные дубли budget-bearing ключей; провижин-фаза non_fatal=True → bootstrap зелёный при упавшем LiteLLM.
* **Evidence:** `key_provisioner.py:392-396/:374-378/:589/:626-657`; `admin_client.py:421-427/:361-363/:118-122/:353-355/:372-373`; `phases/docker.py:558-562`.
* **Source findings:** DATA-902+DATA-305 (CRITICAL), DATA-202 (HIGH), SEC-0003 (overlap REF-0007), PERF-081/082(HYP)/083, FAIL-0305/AI-0010.
* **Files:** core/internal/llm/key_provisioner.py, llm/admin_client.py, lifecycle/phases/docker.py.
* **Impact:** потеря всех ключей проектов = mass 401 до ручного re-provision; cost-bearing дубликаты в LiteLLM DB.
* **Recommended change:** atomic_write_json + FileLock(store.lock) + corrupt→fail-fast (никогда overwrite-all) + mkstemp-mode 0600; различать 404(None) vs transport-error(raise/skip-WARNING); fetch key-list once + filter; pagination loop (сначала verify: два provision-llm подряд, счёт ключей); long-lived httpx.Client; фазовый summary: failure-count ≠ skipped + WARN→ERROR.
* **Tests required:** corruption-chain unit (truncate → next load fails loud); lookup-semantics unit (timeout ≠ 404); pagination integration (mocked transport).
* **Regression risk:** низкий-средний; поведение provision меняется предсказуемо.
* **Dependencies:** PERF-082 — HYPOTHESIS: начать с верификации (5 мин), фикс по результату.
* **Estimated complexity:** M (0.5 дня).
* **Why now:** единственный CRITICAL data-loss вне backup-домена.
* **Why not larger refactor:** не переносим store в postgres — канон-механика записи + семантика ошибок.

---

## REF-0105 · Payload receive: замена набора файлов без транзакции + backup удаляется даже при ошибке

* **Problem:** receive заменяет {compose, ai-platform.yaml, .env.platform, practices.lock} per-file os.remove→replace — атомарно каждый файл, но не набор; `finally rmtree(backup_dir)` уничтожает единственную rollback-копию даже когда исключение вышло до orchestrator-rollback; переименованный compose.yaml переживает доставку и ПОБЕЖДАЕТ по резолюции → нода тихо гоняет старый конфиг с зелёным CI; remove→replace окно даёт ENOENT читателям; EOFError вне except-кортежа ломает JSON-контракт CI; whitelist file-list живёт в 3 несинхронизированных копиях (повтор класса B20a practices.lock).
* **Evidence:** `receive_flow.py:30-32/:441-462/:485-486/:452-460/:520-535/:592`; `shared/compose_files.py:39-53`; `deploy-project.yml:354-358` vs `payload_deliverer.py:72-84`.
* **Source findings:** DATA-101+DATA-704, FAIL-0711, FAIL-0704, DATA-703, FAIL-0705 (rider), A-25.
* **Files:** core/internal/deploy/receive_flow.py, shared/compose_files.py, payload_deliverer.py, deploy-project.yml.
* **Impact:** half-applied payload без средств отката до следующего receive; git↔node divergence без сигналов.
* **Recommended change:** backup_dir вне target_dir + restore-from-backup в except до rmtree; replace без pre-remove; удалить canonical PROJECT_COMPOSE_FILENAMES, отсутствующие в staging; EOFError в except; prefix-sweep orphan tmpdir; единая константа file-list, потребляемая CI (генерация) и обеими сторонами.
* **Tests required:** crash-injection unit (исключение между replace'ами → восстановление); stale-compose deletion unit; whitelist triple-sync structural test.
* **Regression risk:** средний: меняется порядок мутаций аварийного пути.
* **Dependencies:** опирается на локи REF-0011 (копирование уже под flock).
* **Estimated complexity:** M.
* **Why now:** это единственная транзакция доставки прод-пейлоада; сейчас её нет вовсе.
* **Why not larger refactor:** не строим content-addressed payload-store — directory-swap семантика поверх текущего потока.

---

## REF-0106 · Честность state-machine: zombie-done, catch-all без аудита, false idempotency φ1/φ7

* **Problem:** Generic exception вне трёх перехваченных типов выходит traceback'ом БЕЗ save/_audit_failed/notify — audit-log утверждает, что последний прогон успешен; done-markers 14 фаз никогда не сверяются с существованием артефактов (удалили letsencrypt/secrets.env → «already done — skipping», exit 0); φ1(firewall/tor) и φ7(certs) вне hash-invalidation → expired cert/TOR-drift маскируются skip'ом; state.json без schema_version, round-trip strip'ает неизвестные поля; node-update hash не покрывает lifecycle/phases/*.py (правка логики фазы = skip) и не смешивает delivered SHA; «running» статус никогда не пишется.
* **Evidence:** `cli.py:810-857/:471-478`; `cli.py:797-807` + `state_machine.py:703`; `state_machine.py:262-271`; `state_store.py:158-186`; `_phase_input_hash:525-563` (docstring :511 лжёт про phases/__init__.py); core-deploy.yml:263 (make node-update на каждый push).
* **Source findings:** DATA-705, DATA-802, DATA-203, DATA-803, A-01/AI-0038, DATA-804 (частично), TEST-19/20.
* **Files:** core/internal/bootstrap/lifecycle/{cli,state_machine,state_store}.py, phases/preconditions.py.
* **Impact:** нода «здорова» по state.json при физических отсутствующих артефактах; расследование инцидентов вслепую (audit говорит success).
* **Recommended change:** четвёртый `except Exception` → mark failed + _audit_failed + save + return 1 (KeyboardInterrupt отдельно); postcondition exists-check на skip-пути для критичных артефактов (certs live dir, secrets.env, overlay dir) → reset pending; φ7 freshness-probe (cert expiry) перед skip; schema_version + strict load; skip-notice (DRIFT-style WARN) при hash-hit; микс delivered-SHA в hash (полное решение хэша — пост-launch, notice/guard сейчас).
* **Tests required:** TEST-20 resume-flow (running→re-executed once); double-run idempotency unit (TEST-19 style); unknown-schema-version → StateCorruptError.
* **Regression risk:** средний: fail-loud skip может участить ре-исполнение фаз — идемпотентность фаз уже канон (invariant 6).
* **Dependencies:** глобальный mutex REF-0011 (optional part) снижает частоту гонок state.
* **Estimated complexity:** M (1 день).
* **Why now:** CI делает node-update на каждый push в main — канал должен быть честным до начала интенсивных релизов.
* **Why not larger refactor:** не переписываем transition-table (DATA-804 full) — добавляем 5 guard'ов поверх текущего графа.

---

## REF-0107 · False-green гейты: гейт-обманщики внутри системы верификации

* **Problem:** (1) `--only exception_patterns` (underscore) vs detector `exception-patterns` → все 14 детекторов skip, PASS без проверок — воспроизведено живьём; `--only` принимает любые имена (rename детектора = тихий no-op). (2) Argless validate ищет YAML там, где routable-схем нет → «All files valid» всегда. (3) Девять каналов мапят pytest rc=5 (0 collected) в PASS — весь docker-tier может исчезнуть молча. (4) Honesty mode по умолчанию = массовый skip; enforcement pin покрывает 2 из 3 workflow. (5) fingerprint cache не учитывает toolchain/env → replay старых green'ов после pip-upgrade; fixed `.json.tmp` рвётся двумя make check. (6) Manifest freshness: pytest-вариант — вакуумный git-diff на свежем checkout; парити судится тем же генератором. (7) check_suite __init__↔manifest цикл и engine↔lifecycle cycle — мины под make check; dual PlatformFatalError классы глотают except.
* **Evidence:** `core/check-suite.yaml:80` + `static/registry.py:166` (live `[skip]×14`); `validate_orchestrator.py:540/:560-562`; `check-suite.yaml:145/:191/:243` + runner/gate/workflows; `tests/_conftest/honesty.py:39-46`; `fingerprint.py:56-100/:210-214`; `test_gate_manifests_up_to_date.py:70-77`; `check_suite/__init__.py:57-113`, `engine/__init__:18→lifecycle.py:29`, `decrypt_secrets.py:60`.
* **Source findings:** DEP-0016 (CRITICAL, «наивысшее value/effort ratio») ≡ PERF-071, PERF-074, TEST-14, TEST-15, TEST-16, BUG-0305, TEST-13, DEP-0010, DEP-0011, DEP-0003, DEP-0018, DEP-0037 (narrow except secrets_manager import-fallback).
* **Files:** core/internal/static/registry.py, core/check-suite.yaml, check_suite/__init__.py(+constants.py new), validate_orchestrator.py, tests/_conftest/honesty.py + gates, check_suite/fingerprint.py, scripts/manifest_driver.py, shared/node_yaml/__init__.py (re-export cleanup).
* **Impact:** во время launch-week фиксов эти механизмы — единственный арбитр; их ложный PASS = сертификация непроверенного.
* **Recommended change:** validate --only против реестра, unknown → exit 2 (или удалить дублирующую запись); discovery roots core/modules+node-configs; collection floors (--collect-only ≥1) для исторически наполненных suites; honesty deny-by-default glob по всем workflow с pytest; fingerprint salt = toolchain-digest + 3 env-vars + unique tmp via atomic_write_json; независимый semantic-validator manifests (source-yaml ↔ generated, без импорта генератора); constants → constants.py; lifecycle импортирует engine.flow прямым module-path; lint-правило единого import-пути исключений.
* **Tests required:** сами изменения — тесты (floors gate, honesty glob gate, fingerprint-differs тест).
* **Regression risk:** низкий: большинство правок делают гейты строже; риск всплытия накопленных нарушений — принять (это цель).
* **Dependencies:** делать РАНО (волна 0-1): честные гейты нужны прежде остальных фиксов.
* **Estimated complexity:** M (1 день пакетом).
* **Why now:** система проверки сама выдаёт false-green — базовое доверие ко всем остальным проверкам.
* **Why not larger refactor:** не консолидируем gate-amplification архитектуру (DEP-0054..59 — P2) — точечно чиним обманщиков.

---

## REF-0108 · Status-page/metrics: thread-leak от одного DNS lookup, OOM-траектория audit-log, экспорт зануляет метрики под нагрузкой

* **Problem:** fan_out_checks: as_completed без timeout + uninterruptible gethostbyname → wedged DNS permanently leak'ит request+pool threads; pids-limit 256 → каскад 500s → Docker HEALTHCHECK flap/restart именно в инцидент; каждый GET гоняет полный probe-suite (~11 curls) без кэша при 60s freshness данных; read_audit_log читает ВЕСЬ append-only jsonl каждую минуту (измерено 21MB/119k строк, растёт линейно → OOM экспортёра за недели); docker stats по timeout молча зануляет CPU/mem всех контейнеров; certs TTL-cache не подключен (O(D×L) x509 parses/мин); import-time basicConfig(WARNING) глушит LDD-телеметрию всего remote-пути.
* **Evidence:** `aggregate.py:78-83`, `platform.py:127`, `app.py:303-305` + base.yml limits; `app.py:137-149`; `audit_logger.py:270-271` (docstring обещает tail-read — код не делает); `docker_collector.py:120-121`; `platform_export_metrics.py:194-198`; `remote_executor.py:60`, `overlay_deliverer.py:46`.
* **Source findings:** PERF-041 (TOP#1 risk), PERF-040/042, PERF-030, PERF-052/053, A-08, SEC-0044 rider (label escaping), BUG-1002/AI-0065 canon-collector (rider REF-0010).
* **Files:** core/modules/status-page/{app.py,collectors/*}, core/internal/shared/audit_logger.py, healthcheck/metrics/{docker_collector,cert_collector}.py, platform_export_metrics.py, remote_executor.py, overlay_deliverator.py.
* **Impact:** публичная витрина статуса и источник status-metrics.json деградируют синхронно с нагрузкой; observability-канон «метрики важнее всего в инцидент» нарушается дизайном.
* **Recommended change:** as_completed(timeout=total)+TimeoutError handler; getaddrinfo-in-executor; mtime-keyed cache 30-60s для probes/metrics (~20 строк); reverse chunked tail-read (спека уже в docstring); stats раз в N прогонов + error-not-zeros; CacheManager для certs (ttl 3600); basicConfig перенести в main() обоих модулей.
* **Tests required:** thread-count stability тест (fake DNS-blocker); tail-read unit на 200k-line fixture; cache-hit unit.
* **Regression risk:** низкий.
* **Dependencies:** heartbeat (REF-0010) ставим после этого фикса.
* **Estimated complexity:** M (0.5 дня).
* **Why now:** единственный long-lived HTTP-сервис платформы с известной утечкой потоков.
* **Why not larger refactor:** не переводим на async-стек — DI-таймаут + кэш + tail-read.

---

## REF-0109 · Целостность общих файлов: node.yaml RMW без лока теряет регистрации, комментарии стираются каждой мутацией

* **Problem:** add/remove/update_project — deepcopy(load)→mutate→whole-file write-back БЕЗ лока → last-writer-wins стирает чужую регистрацию проекта; ruamel используется без round-trip load → комментарии/headers нодового yaml уничтожаются при КАЖДОЙ мутации (логи утверждают обратное); loki-retention/catalog.json RMW вне лока и без atomic write (torn YAML POST'ится в reload); converge TOCTOU затирает свежедоставленный .env.platform пустым fallback; 6 прямых write_text сайтов в node-configs (prometheus targets, daemon.json, htpasswd...).
* **Evidence:** `projects.py:204-241`, `_core.py:325-352/:97/:347-363`; grep FileLock в node_yaml/=0; `loki_retention.py:89→135-136`, `generate_catalog.py:145-146`; `converge/projects.py:277-300/:237`; список DATA-105.
* **Source findings:** DATA-301+DATA-104, DATA-901 (HIGH), BUG-0304, DATA-906, BUG-0306, DATA-105, DATA-903 (rider).
* **Files:** core/internal/shared/node_yaml/{projects,_core}.py, monitoring/{loki_retention,catalog/generate_catalog}.py, bootstrap/converge/{projects,runtime}.py, monitoring/prometheus_targets.py, bootstrap/docker_installer.py, scaffold/htpasswd.py.
* **Impact:** потеря регистрации проекта = невидимость в reconciler/vhosts; мониторинг-конфиг torn при двух пересекающихся receive.
* **Recommended change:** FileLock(<node.yaml>.lock, timeout=30) вокруг _write_back call-path; ruamel round-trip load=f→dump same object + pin dep + loud warning на fallback; platform-level lock (/var/lock/platform-monitoring.lock) вокруг RMW + atomic_write_text; converge create через O_EXCL/EEXIST→SKIP; 6 сайтов → atomic_writer (сохранить mode для daemon.json/htpasswd).
* **Tests required:** concurrent-writers unit (шаблон state_store suite есть); comment-preservation unit (round-trip fixture).
* **Regression risk:** низкий-средний (локи + формат записи).
* **Dependencies:** независим.
* **Estimated complexity:** M (0.5 дня).
* **Why now:** HIGH deterministic data-loss на операторских операциях, которые учащаются в launch week.
* **Why not larger refactor:** не переезжаем на registry-DB — лок + правильный loader.

---

## REF-0110 · Sequential deploy игнорирует depends_on; failed-группа не останавливает dependents

* **Problem:** Канонический fresh-node путь (DEPLOY_PARALLEL=false) деплоит по порядку списка node.yaml — module.yaml#depends_on учитывается только в parallel-ветке; ошибка topo-sort деградирует в unordered; failed группа откатывается и цикл продолжается — зависимые стартуют против отсутствующих зависимостей.
* **Evidence:** `deploy_orchestrator.py:479-485/:520-542/:669-730`; TRAP hermes-agent compose:22-32 (cross-file deps unsupported).
* **Source findings:** A-07 (P2 0.75, pre-launch по run-c #8).
* **Files:** core/internal/bootstrap/deploy/deploy_orchestrator.py.
* **Impact:** fresh-node crash-loops против absent DB/S3; каскадные вводящие в заблуждение отказы.
* **Recommended change:** kahn-линеаризация и для sequential (функция уже есть); topo-failure → fail-fast ConfigValidationError; critical-failure предыдущей группы abort remaining.
* **Tests required:** order-test с реальным build_dag+kahn на 2-level DAG (TEST-29 rewrite заодно).
* **Regression risk:** средний: изменение порядка первого бутстрапа — проверить на test-VPS.
* **Dependencies:** использует REF-0005 (честный failed-учёт).
* **Estimated complexity:** S-M.
* **Why now:** первый production bootstrap произойдёт в launch window.
* **Why not larger refactor:** переиспользуем существующую топо-механику, не проектируем новую DAG-систему.

---

## REF-0111 · Docker-smoke execution contract размножен в 3 слоя (hang-класс инцидентов)

* **Problem:** Параметры исполнения docker-тестов (xdist:false, per-test timeout, pre-cleanup) закодированы трижды: check-suite.yaml, conftest-compose, workflow; расхождение = 900s-hang класс (инцидент 2026-08-17, ≥10 commit'ов починки; каждый регресс = engineer-day и блокировка hotfix-верификации).
* **Evidence:** `check-suite.yaml:194-224` (TRAP xdist-hang in-file), `tests/_conftest/compose.py:152-157`, `platform-test.yml:328-340`.
* **Source findings:** A-03 (#3 sequencing run-c).
* **Files:** те же три.
* **Recommended change:** check-suite.yaml владеет параметрами (xdist:/timeout_s:/pre_cleanup:), остальные двое потребляют; parity-gate по образцу test_gate_workflow_consistency.
* **Tests required:** parity-gate сам является тестом.
* **Regression risk:** низкий.
* **Dependencies:** независим.
* **Estimated complexity:** S-M.
* **Why now:** hang блокирует верификацию hotfix'ов — в launch week это равносильно остановке релиза.
* **Why not larger refactor:** ownership-перенос, не редизайн test-инфры (A-40 extraction — P2).

---

## REF-0112 · Core-delivery: CI rsync и core_deliverer тянут разные exclude-set'ы в один prod-tree

* **Problem:** Оба канала запускают rsync --delete с РАЗНЫМИ exclude-наборами → чередование каналов переписывает прод-дерево; документированный инвариант «test-compose не доставляются на прод» нарушается основным каналом (CI кладёт 13 docker-compose.test.yml + .pytest_cache в /opt/platform/core/); 3 TRAP[BUG] инцидента уже в этом блоке workflow.
* **Evidence:** `core-deploy.yml:163-171/:213-220` vs `core_deliverer.py:55-72`.
* **Source findings:** A-04 (#5 sequencing).
* **Files:** .github/workflows/core-deploy.yml, core/internal/bootstrap/core_deliverer.py.
* **Impact:** divergent prod tree per channel; фантомные конфиги при триаже инцидента.
* **Recommended change:** CI вызывает `python3 -m …core_deliverer` (один owner exclude-set) или генерирует excludes из него.
* **Tests required:** structural: workflow содержит вызов deliverer (grep-gate).
* **Regression risk:** низкий.
* **Dependencies:** независим; осторожно с AGE_SECRET_KEY транспортом (REF-0007 меняет тот же workflow).
* **Estimated complexity:** S.
* **Why now:** DR-канал и основной канал должны давать идентичное дерево до первого реального DR.
* **Why not larger refactor:** не объединяем delivery-подсистемы — один вызов.

---

## REF-0113 · SoT-гигиена волна: порты/константы/дефолты вне SoT (7 мелких фиксов одним пакетом)

* **Problem:** MODULE_PORTS_DENY и port-scanner name-map — ручные копии вне gate (миграция порта = stale deny-rule → возможный public exposure + сломанный generate_platform_env); PRIVOXY_PORT живёт в bootstrap/firewall, а healthcheck импортирует наверх (gate цементирует misplacement); s3_client импортирует config (единственное нарушение leaf-invariant shared); dev_hosts импортирует internals nginx-модуля (internal→modules edge без контракта); cert_collector дефолт указывает на literal test-node; default-node/org захардкожены в ≥7 файлах с 3 цепочками fallback.
* **Evidence:** `firewall.py:81/:89-103`, `port_scanner.py:31-52`; `tor_proxy_check.py:36-37` + `test_gate_port_parity.py:26`; `s3_client.py:34`; `dev_hosts.py:69-71`; `cert_collector.py:338`; `app_config.py:61-62`, `project_yaml.py:299`, `project_scaffolder.py:61` и др.
* **Source findings:** A-11, A-14 (double-run corroboration), A-15, A-16, DEP-0033=A-12 subset, A-02.
* **Files:** перечислены выше + shared/platform_ports.py.
* **Impact:** rot defense-in-depth и healthcheck-цепочки при первых же миграциях портов; wrong-node deploys при втором контексте.
* **Recommended change (по пунктам, каждый S):** MODULE_PORTS_DENY из imported PLATFORM_PORT_* + расширить parity-gate; PRIVOXY_PORT → shared/platform_ports.py (5 потребителей); get_s3_client() pure (params from callers); forbidden internal→modules контракт + перенос helper'ов; test-node literal → derive from context; default_node()/default_org() resolver в app_config.
* **Tests required:** соответствующие parity-gates расширяются/добавляются.
* **Regression risk:** низкий (рефакторинг констант с гейтами).
* **Dependencies:** выполнять ПОСЛЕ freeze-объявления (P3): это разрешённые аддитивные перемещения.
* **Estimated complexity:** S-M суммарно (пакет выходного дня).
* **Why now:** дешёвая ликвидация целого класса «тихий drift» перед интенсивными изменениями launch week.
* **Why not larger refactor:** не строим единую constant-платформу (DEP-0020 — P2) — двигаем 6 конкретных констант.

---

## REF-0114 · Context-promote: org-secrets частичный успех невидим + GHCR-токен истекает молча

* **Problem:** Batch gh secret set коллапсирует результаты в один bool → promote рапортует SUCCESS-with-WARN (logger.info!), audit DONE, exit 0; ничего не записывает, какие секреты легли → зеркальный CI падает позже с пустым VPS_SSH_KEY (точное повторение задокументированного инцидента). GHCR PAT: login non-fatal → anonymous fallback; first-deploy при истёкшем токене = FATAL exit 10; proactive expiry-сигнала нет.
* **Evidence:** `org_secrets_provisioner.py:211-222/:265-271` (invariant №1 обещает True — impl возвращает False); `context_promoter.py:309-327`; `docker_auth.py:151/:174-176`.
* **Source findings:** BUG-0607+BUG-0903, A-24, FAIL-0602/0603 (Batch C).
* **Files:** core/internal/deploy/org_secrets_provisioner.py, context_promoter.py, shared/docker_auth.py, secret-definitions.yaml (expiry field).
* **Impact:** launch-day отказ нового контекстного org с misleading диагностикой; первый деплой приватных образов падает непонятно.
* **Recommended change:** вернуть (uploaded[], failed[]); logger.warning + audit WARN/FAIL per gap; post-promote guard `gh secret list ⊇ {VPS_HOST,VPS_SSH_KEY,AGE_SECRET_KEY}`; expiry-field в secret-definitions + предупреждение в vps_readiness preflight (или private-manifest probe).
* **Tests required:** partial-failure unit; secret-list-guard unit.
* **Regression risk:** низкий.
* **Dependencies:** независим.
* **Estimated complexity:** S.
* **Why now:** context-promote — шаг 4 release-checklist; его ложный success ломает всю последующую цепочку.
* **Why not larger refactor:** не строим secrets-reconciliation подсистему — возвращаемый кортеж + один guard.
