# Direction 7 — Duplicated Business Logic

Агент: форензик направления «duplicated business logic» · Метод: side-by-side чтение обеих реализаций каждой пары · Дата: 2026-08-22

Итог направления: 6 находок (0 CRITICAL, 1 HIGH, 3 MEDIUM, 2 LOW); confidence 5×HIGH, 1×MED. Долг дублирования LOW-to-MODERATE и сконцентрирован на стыках bootstrap/lifecycle: флагманские SoT-решения (ssh_opts, docker_ops, healthcheck D5 criterion в poller'ах, NodeYaml/project_yaml facades, compose_files, module_interface для 12 из 13 call sites) выдержали side-by-side сверку; остаток — second-generation drift — helpers, написанные до консолидационной волны или обходящие её (retry.py, atomic_writer, platform_config), плюс один инвертированный предикат в metrics collector.

Проверенные НЕ-находки (сверено side-by-side, комплаенс или документировано):
- Node/context resolution (#4): консолидировано — `NodeYaml.resolve` 3-path canon единственная цепочка; context_deployer._resolve_context и context_overlay._read_context_name оба делегируют `NodeYaml(node_yaml).get_context()`; AGE-key chain single-sourced в node_detect.detect_age_key (5-check chain совпадает с таблицей core/AGENTS.md). Единственный sanctioned micro-copy: `_format_cli_value` в node_resolver.py:71-89 зеркалит node_yaml/cli.py (private-import gate запрещает шарить — задокументированный TRAP).
- YAML loads (#5): ноль raw `yaml.safe_load` node.yaml/ai-platform.yaml вне facades (AC-B1.1 project_yaml.py держится; verify_sweep/collection.py:207 ассертит то же). Raw loads других артефактов (certs-providers.yaml, practices.lock, templates, check-suite manifest) легитимны; единственный реальный случай — ARCH-062 (platform-infra.yaml).
- Compose assembly (#6): без overlap — compose_files.py владеет name lists/resolution, docker_compose.py — исполнением; bootstrap/deploy/compose_args.py:58 `resolve_compose_file` — документированный delegating wrapper. Digest form parsing: gate-side shape validation (test_gate_image_tag_form.py:47) vs runtime digest-drift classification (docker_posture.py:148) — комплементарные семантики.
- Copy-paste name pairs: `render_template` ×2 — loadtest/config.py:554 несёт явный keep-TRAP (177 W3.3 S5); `validate_compose_networks`/`register_in_node_yaml` ×2 — delegating wrappers с DI seams; `resolve_chat_id` ×2 — документированный telegram_notifier shim.

---

### ARCH-060: Module-liveness retry реализован дважды — bootstrap/deploy vs lifecycle/helpers, второй обходя typed module-interface sole path
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/bootstrap/deploy/healthcheck_runner.py:98-137 (impl A) vs core/internal/bootstrap/lifecycle/helpers/reporting.py:84-175 (impl B); третий single-shot sibling: core/internal/healthcheck/modules_healthcheck.py:207-230
- Symbols: `run_healthcheck()` / `run_healthchecks()` / `invoke_module_interface`
- Evidence: та же семантика — «retry module liveness до pass, лог DIAG на первый fail, non-fatal WARN на исчерпание». A (healthcheck_runner.py:106-129): `for attempt in range(max_retries): success, output = invoke_healthcheck_full(module_name, "liveness") ... time.sleep(retry_interval)` — идёт через shared/module_interface.invoke (C5 sole path), константы `DEFAULT_HEALTHCHECK_MAX_RETRIES = HEALTHCHECK_POLL_MAX_RETRIES` (=20) × `HEALTHCHECK_POLL_INTERVAL` (=3s) из shared/timeouts.py. B (reporting.py:90-91,130-162): `hc_max_retries = 10`, `hc_retry_interval = 10` — захардкоженные литералы в обход SoT timeouts.py (U-11/D34); вызов минует shared/module_interface.invoke пересборкой собственной shell-команды: `hc_cmd = f"source {shlex.quote(platform_root + '/core/lib/paths.sh')} && invoke_module_interface {…} healthcheck liveness"` + `subprocess.run(["bash","-c",hc_cmd], timeout=30)`. Retry window различается: A=60s, B=100s+30s-timeout.
- Failure/maintenance scenario: drift-fix (новый interface arg, изменение timeout в shared/timeouts) приземляется в shared/module_interface.invoke — B продолжает звать bash-функцию напрямую со stale константами; φ11 node-update healthcheck выдаёт другой PASS/FAIL, чем deploy-path healthcheck того же модуля. Raw `paths.sh` sourcing в B тихо ре-связывается с filesystem layout, который инкапсулирует module_interface.py.
- Impact: расходящиеся health verdicts между deploy и update путями; два места правки для одной policy; инвариант C5 «typed contract» эродирует без падающего гейта (gate проверяет internal→modules вызовы, не этот inline rebuild внутри helpers).
- Minimal fix: заменить внутренний цикл B на `module_interface_invoke(mod_name, "healthcheck", "liveness", ...)` (тот же импорт, что у A) и брать константы из shared/timeouts (`HEALTHCHECK_POLL_MAX_RETRIES/INTERVAL`); ~20 LOC diff, удалить TRAP-wrapped bash -c блок.
- Code churn: S
- Phase: Post-launch

### ARCH-061: Два независимых visudo-validate + atomic-replace sudoers writer'а с расходящейся temp-локацией, timeout-ключом и failure-контрактом
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/setup_node.py:299-322 (impl A `SudoersInstaller.install`) vs core/internal/bootstrap/converge/sudoers.py:68-127 (impl B `atomic_write_sudoers`)
- Symbols: `SudoersInstaller.install/_write_temp/_validate_visudo` vs `atomic_write_sudoers/safe_cleanup_tmp`
- Evidence: идентичный pipeline, четыре дивергенции: Temp location — A `tempfile.mkstemp(dir=self.tmp_dir)` (/tmp parity) vs B `NamedTemporaryFile(dir=parent_dir)` (целевой родитель). Timeout SoT key — A visudo с `SUDOERS_CMD_TIMEOUT` vs B `run_subprocess([...], timeout=FILE_OP_TIMEOUT)` — две разные записи реестра для той же операции. Error contract — A кидает `SudoersError` после unlink tmp; B возвращает False и имеет defensive catch-all `except Exception ... return False` (sudoers.py:122). Mode handling — A chmod 0440 tmp перед replace; B chmod 0440 tmp И повторный chmod target после replace (:113). B сам документирует долг: `# Rev: при миграции на shared/atomic_writer (validator=visudo) — убрать локальный шов` (sudoers.py:110).
- Failure/maintenance scenario: lockout-safety фикс применён к одному writer'у (visudo флаг, mode, fsync) — converge self-heal или bootstrap init остаётся на старом поведении; tuned visudo timeout в одном SoT-ключе не затрагивает другой путь.
- Impact: security-critical файл (/etc/sudoers.d) пишется двумя code path, чья эквивалентность — только конвенция; известная отложенная миграция на `atomic_writer(validator=...)`.
- Minimal fix: приземлить уже аннотированную миграцию: расширить shared/atomic_writer validator callback, оба call site делегируют; DI seams сохранить.
- Code churn: M
- Phase: Post-launch

### ARCH-062: platform-infra.yaml имеет двух «sole reader» с идентичными resolution chains и противоположной failure-семантикой
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/config/platform_config.py:66-95 (impl A) vs core/internal/shared/compose_profiles.py:47-104 (impl B)
- Symbols: `platform_config._load_defaults/get_default` vs `compose_profiles.resolve_infra_path/load_profiles`
- Evidence: A объявляет `@purpose Единый Python-фасад для чтения default-значений из platform-infra.yaml (SoT). Все consumers платформы получают default'ы только через этот модуль.`; B переимплементирует загрузку и признаёт зеркальность: «Path-резолвинг: … script-relative корень репо — зеркало platform_config (T8.3/D23)». Resolution chains байт-эквивалентны: A:83-94 `os.environ.get("PLATFORM_ROOT") → Path(platform_root)/"core"/"platform-infra.yaml"` else `Path(__file__).resolve().parents[3]/...`; B:50-60 те же два кандидата, тот же порядок. Оба делают собственный `yaml.safe_load` + env_defaults extraction. Дивергенция — failure semantics: A fail-visible (`""` + WARNING, :14-15) vs B fail-fast (`raise FileNotFoundError` :89, KeyError для отсутствующего ключа — «никогда silent []»).
- Failure/maintenance scenario: перенос/rename platform-infra.yaml или новый env_defaults key, потребляемый через profiles, требует зеркалирования в двух резолверах; missing-file инцидент всплывает как пустые defaults для backup/cert consumers, но hard crash для compose-profile consumers — несогласованный operator experience при одной root cause.
- Impact: два заявленных sole-reader = нет sole reader; drift между cached-lazy (A) и per-call (B) load timing также расходится при изменении env mid-process.
- Minimal fix: извлечь `resolve_infra_path()` в shared (маленький platform_infra reader), platform_config потребляет его, сохраняя sentinel/accessor слой; B тоже делегирует.
- Code churn: S
- Phase: Post-launch

### ARCH-063: Atomic-write паттерн переимплементирован вне shared/atomic_writer — одна копия отменяет собственную concurrency-гарантию
- Severity: MEDIUM
- Confidence: MED
- Files: core/internal/check_suite/fingerprint.py:206-218 (impl A) vs core/internal/shared/atomic_writer.py (canon, mkstemp+fsync); secondary: core/internal/dev_hosts.py:373-418
- Symbols: `save_cache()` vs `atomic_write()/atomic_write_json()`; `_atomic_write()` (sudo variant)
- Evidence: A: `tmp = path.with_suffix(".json.tmp")` — фиксированное tmp имя, затем json.dump + `Path(tmp).replace(path)`. Собственный docstring утверждает безопасность («конкурентные executor'ы не портят файл») — ложно при двух конкурентных писателях: оба открывают/трунцируют ОДИН `.json.tmp`, интерливнутые dump'ы могут быть rename'уты наружу (torn cache), в отличие от unique mkstemp temp канона. Список consumers канона в shared/AGENTS.md включает 10+ модулей; A среди них нет, документированного исключения нет. dev_hosts._atomic_write (373-418) реимплементирует tmp+os.replace с добавленным sudo-mv branch для неписабельных родителей — дивергенция с явным @rationale (tmpfs/APFS atomicity trade-off), т.е. semi-sanctioned, но всё равно параллельное тело, которое не подхватит фиксы atomic_writer (fsync-before-replace). watchdog.py save_state exempt: stdlib-only cron constraint задокументирована в MODULE_CONTRACT.
- Failure/maintenance scenario: `make check WORKERS>1` или два suite-процесса → повреждённый/потерянный fingerprint cache (silent; в лучшем случае деградация до full re-run, в худшем неверный replay результат); будущий durability-fix atomic_writer пропускает эти копии.
- Impact: маловероятная data corruption в tooling cache + N растущих параллельных write тел.
- Minimal fix: направить save_cache на atomic_write_json; watchdog оставить документированным исключением; опционально мигрировать dev_hosts после появления post-write move hook.
- Code churn: S
- Phase: Pre-launch

### ARCH-064: Health criterion drift — metrics collector судит running-without-healthcheck контейнеры как NOT healthy вопреки D5-канону
- Severity: LOW
- Confidence: HIGH (дивергенция верифицирована; текущий blast radius мал)
- Files: core/internal/healthcheck/metrics/docker_collector.py:265-274 (impl A) vs core/internal/shared/docker_compose.py:588-593 + core/lib/healthcheck.sh:110-138 (canon B)
- Symbols: `_get_health_status(state)` vs критерий `healthcheck_poll` / `check_docker_health`
- Evidence: A: `return health.get("Status") == "healthy"` — контейнер running БЕЗ HEALTHCHECK (Health.Status absent/"") → healthy=False. Canon B (docker_compose.py:593): `if not (state == "running" and health in {"healthy", "", "none"}): all_healthy = False` — running-without-healthcheck ЕСТЬ healthy; shell facade реализует то же (lib/healthcheck.sh:123-132). Watchdog согласен инверсией (`_is_eligible`: `health is None or health in {"healthy","none"} → False`). Все module healthcheck.sh корректно делегируют check_docker_health (13 модулей проверено — raw re-derivations не найдены).
- Failure/maintenance scenario: любой новый consumer метрики `healthy` (status-page renderer сейчас читает только running/exit_code — enrich.py:126-132) покажет здоровые сервисы как unhealthy, противореча вердикту `make healthcheck` по тому же контейнеру; операторы ловят фантомные инциденты.
- Impact: латентная wrong-verdict поверхность; однострочная дивергенция от канона, консолидация которого стоила DevPlan 116 D5.
- Minimal fix: `_get_health_status` → `status == "healthy" or (status in {"", None} and state.get("Running"))`, либо переиспользовать маленький shared predicate рядом с docker_ops inspect helpers.
- Code churn: S
- Phase: Pre-launch

### ARCH-065: Остаточные fixed-interval retry-циклы вне shared/retry.py (после consolidation DevPlan 177)
- Severity: LOW
- Confidence: HIGH
- Files: top-3: core/internal/bootstrap/install_tor_proxy.py:557-571 (A) · core/internal/bootstrap/deploy/healthcheck_runner.py:65-74 и :106-129 (B) · core/internal/deploy/healthcheck_poller.py:143-156 (C)
- Symbols: TorProxyInstaller.verify_tor_circuit / wait_for_readiness + run_healthcheck / HealthcheckPoller.poll_until_healthy
- Evidence: shared/retry.py консолидировал 4 цикла (MODULE_CONTRACT перечисляет их); grep показывает оставшиеся ad-hoc формы: A — `for attempt in range(1, VERIFY_MAX_ATTEMPTS + 1)` с локальными константами и self.clock sleep; B — два hand-rolled цикла с time.sleep(interval) — константы из timeouts SoT, но loop/sleep/logging логика продублирована вместо делегации (shared.retry поддерживает result-mode + sleep_fn DI); C — частично sanctioned: MODULE_CONTRACT позиционирует HTTP-poll окно как собственную политику; docker leg уже делегирует общий criterion.
- Failure/maintenance scenario: улучшения backoff/jitter/logging попадают только в shared.retry; эти циклы остаются fixed-interval, каждый логирует попытки в своём формате; суммарные poll окна расходятся (уже: 60s vs 120s для почти одинаковых liveness waits через ARCH-060).
- Impact: медленный дрейф без текущего misbehavior; A/B/C индивидуально корректны.
- Minimal fix: конвертировать циклы A и B в `retry(..., exception_mode=False, sleep_fn=...)`; C оставить с documented keep-note или сложить позже.
- Code churn: S
- Phase: Pre-launch
