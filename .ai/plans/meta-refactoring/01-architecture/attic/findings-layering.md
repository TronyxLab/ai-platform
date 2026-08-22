# Direction 6 — Infrastructure / Application / Domain Coupling

Агент: форензик направления «infra leaking into business logic» · Метод: количественный grep-census + чтение top-offenders · Дата: 2026-08-22

Итог направления: subprocess raw usage 117 call-lines / 56 файлов против 26 adopters wrapper-канона (scaffold хуже всех: 20:1), 52 вызова без timeout включая promote/scaffold critical path; HTTP-дисциплина фактически чистая (единственный httpx outlier TRAP-документирован; urllib заперт в shared-адаптерах); path literals сокращены до ~24 сайтов; sleeps 7/9 неинъектируемы; sys.exit полностью загейчен. Adapter-дисциплина реальна, но принята наполовину — канон существует и работает там, где применён; протечки концентрируются в scaffold/ и deploy/promote модулях, написанных до или вне унификации DevPlan 118/119, а самый острый остаточный риск — отсутствие timeout'ов, а не отсутствие wrapper'ов.

---

### ARCH-051: Raw subprocess sprawl — 117 call-sites минуя канон run_subprocess
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/scaffold/github_ops.py:47-96; core/internal/practices/maturity.py:177; core/internal/deploy/context_promoter.py:127,171,179,293; core/internal/bootstrap/firewall.py; core/internal/validate/validate_orchestrator.py (полный список: 56 файлов с raw subprocess.* и нулевым использованием run_subprocess)
- Symbols: `create_github_repo`, `_git_first_commit`, `promote_via_ssh`, `apply_rules`
- Evidence: 117 raw `subprocess.run/Popen/check_*` call-lines под core/internal вне shared/ (bootstrap 60, scaffold 20, deploy 9, verify_sweep 6, scripts 4, static 3, lint 3, healthcheck 3, secrets 2, practices 2, loadtest 2, прочие по 1). Только 26 business-файлов импортируют канонический wrapper/DI CommandRunner; scaffold-домен: 20 raw вызовов против 1 пользователя wrapper. Прочитанный github_ops.py: задокументированный инвариант «Never raises — все subprocess failures → warn» нарушен in-code — ни один из 6 вызовов не завёрнут в try/except; отсутствующий бинарник mid-flow кидает FileNotFoundError напрямую через scaffold pipeline.
- Failure/maintenance scenario: каждый raw сайт реимплементирует error normalization (rc→None/False/WARN) с локальной семантикой; три исторически дивергентных диалекта существовали до унификации DevPlan 118 C10 — унификация остановилась на ~26 файлах, поэтому следующий агент, копирующий соседний файл, снова копирует неверный диалект.
- Impact: несогласованная failure-таксономия для идентичных операций; нетестируемость без monkeypatch там, где DI seam отсутствует; drift контракт-vs-реализация (github_ops).
- Minimal fix: strangler-миграция небутстрапных бизнес-модулей (scaffold/, deploy/, practices/) на run_subprocess/CommandRunner; bootstrap system-provisioning модули (firewall, docker_installer, tor_setup) могут сохранить raw вызовы — они сами являются infra-слоем; зафиксировать эту границу в docstring shared/subprocess_io.
- Code churn: M
- Phase: Post-launch

### ARCH-052: Subprocess без timeout на deploy/promote критическом пути
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/deploy/context_promoter.py:127 (`ssh -T git@github.com`, no timeout), :171 (`git push --mirror`, no timeout), :179,293; core/internal/scaffold/github_ops.py:47,56,64,75,84,90 (все 6); core/internal/scaffold/project_scaffolder.py:211 (`rsync`, check=True), :511-513
- Symbols: `check_ssh_available`, `promote_via_ssh`, `create_github_repo`, `sync_templates`
- Evidence: 52 из 117 raw вызовов без kwarg timeout=. Top offenders: context_promoter 4/4, github_ops 6/6, project_scaffolder 4. Контраст: канон run_subprocess нормализует TimeoutExpired → rc=124 и берёт defaults из shared/timeouts.py.
- Failure/maintenance scenario: шаг release-checklist make context-promote зависает на hung mirror push через flaky SSH → subprocess.run блокируется неопределённо → CI job сжигает весь GitHub runner ceiling с нулевой диагностикой; тот же класс в new-project при зависании gh/git push против мёртвого прокси mid-scaffold, оставляя полусозданный проект.
- Impact: hang-risk ровно на двух самых operator-visible флоу (promote, scaffold); нарушение SoT-политики timeouts.py умолчанием, а не value-drift'ом.
- Minimal fix: добавить timeout= из shared/timeouts (или завернуть через run_subprocess) в context_promoter + github_ops + project_scaffolder; ~10 call sites.
- Code churn: S
- Phase: Pre-launch

### ARCH-053: Inline remote-command construction вне ssh_cmd_builder, дивергентный quoting
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/scaffold/project_remover.py:353-359; core/internal/loadtest/runner_remote.py:198; core/internal/verify_sweep/collection.py:444; позитивный контраст: core/internal/deploy/channels/scp.py:126, channels/forced.py:95, check_suite/single.py:97
- Symbols: `ssh_compose_down` (compose_cmd f-string), chmod/cp helpers runner_remote
- Evidence: project_remover hand-build'ит `"cd /opt/projects/{project} && docker compose down --timeout … || docker compose -p {project} down"` — встраивает знание layout (/opt/projects) И интерполирует без shlex; loadtest строит `f"chmod -R a+rwX {remote_dir}"` без кавычек, тогда как собственный инвариант №5 того же файла требует shlex.quote для container args; collection.py `f"cat {ctx.remote_conf_dir}/*.conf"` без кавычек. При этом deploy channels квотируют всё через shlex.quote — две сосуществующие дисциплины.
- Failure/maintenance scenario: имена проектов registry-валидированы сегодня, так что injection латентна, не жива; но любой будущий caller с невалидированным именем (или context/dir с пробелами) превращает интерполяцию в remote shell syntax; изменение layout /opt/projects ломает remove-project молча в runtime вместо rename в deploy_paths.
- Impact: quoting-дисциплина расщеплена на 3 стиля (printf %q в ssh_cmd_builder, shlex.quote в channels, raw interpolation в scaffold/loadtest/verify_sweep).
- Minimal fix: route compose_cmd через deploy_paths.projects_base() + shlex.quote (или helper ssh_cmd_builder); закавычить remote_dir/conf_dir.
- Code churn: S
- Phase: Pre-launch

### ARCH-054: Хардкод time.sleep poll-циклы в orchestration, без clock DI
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/deploy/docker_orchestrator.py:580; core/internal/deploy/healthcheck_poller.py:156; core/internal/bootstrap/deploy/healthcheck_runner.py:74,129; core/internal/bootstrap/lifecycle/helpers/reporting.py:162; core/internal/bootstrap/docker_registry_auth.py:341-342 (локальный `import time` внутри метода); core/internal/bootstrap/deploy/parallel_runner.py:153,314
- Symbols: `_phase_up`, `HealthcheckPoller.poll`, `wait_for_readiness`, `_wait_docker_daemon`
- Evidence: 9 time.sleep сайтов всего вне тестов; только file_lock.py (shared), install_tor_proxy (self.clock DI, W4c canon) и shared/retry.py (sleep_fn param) следуют injectable паттерну. 7 orchestration сайтов спят напрямую — четыре независимых healthcheck/poll цикла (healthcheck_poller, healthcheck_runner ×2, reporting inline retry, docker_registry_auth daemon-poll) сосуществуют рядом с shared/retry.py и docker_compose.healthcheck_poll().
- Failure/maintenance scenario: unit-testing φ12 deploy-update или registry-auth retry путей либо реально спит (медленные suite'ы), либо monkeypatch'ит time.sleep глобально; изменение интервала требует правки 4 файлов, потому что poll policies пере-кодируются per loop несмотря на HEALTHCHECK_POLL_* константы.
- Impact: стоимость тестового времени + дублирование policy; contrast-паттерн существует в репо (install_tor_proxy Clock), доказывая форму фикса.
- Minimal fix: добавить параметр `sleep_fn: Callable = time.sleep` в 4 poller'а; опционально делегировать retry-циклы в shared/retry.retry.
- Code churn: S
- Phase: Post-launch

### ARCH-055: Дублированные deployment-layout литералы вне deploy_paths SoT
- Severity: LOW
- Confidence: HIGH
- Files: DEFAULT_ACME_HOME="/opt/acme.sh" определён 5×: core/internal/bootstrap/install_acme.py:52, issue_cert.py:90, s3_ssl_cache.py:87, cert_orchestrator.py:530, cron_installer.py:54; state-файлы под /var/lib/platform/run/: reboot_policy.py:59, cert_expiry_check.py:56, watchdog.py:80 (документированный literal); python_deps.py:75 (`HASH_DIR=/var/lib/platform/.bootstrap`); secrets/decrypt_secrets.py:369 (`/opt/node-configs/secrets`); modules_healthcheck.py:287 (`f"/opt/node-configs/{node}/node.yaml"`)
- Symbols: `DEFAULT_ACME_HOME`, `STATE_FILE`, `HASH_DIR`
- Evidence: осталось лишь ~24 code-line литерала вне комментариев (ранние миграции вычистили основную массу — remote_executor, overlay_deliverer используют platform_remote_base()/projects_base()). Остаточные кластеры: acme home ×5 файлов, /var/lib/platform state dirs ×4, node-configs ×2.
- Failure/maintenance scenario: перенос install dir acme.sh или state-dir конвенции требует касания 9 файлов; пропущенный (например, default cron_installer) молча дивергирует между installer и reader — cert renewal cron указывает на старый путь, пока orchestrator читает новый.
- Impact: низкий сегодня (малый счётчик, стабильные пути), но это ровно класс дрейфа, для убийства которого создан deploy_paths.
- Minimal fix: поднять ACME_HOME в shared/deploy_paths (или certs SoT) + reuse для родителей state-файлов; 5-строчные константы.
- Code churn: S
- Phase: Post-launch

### ARCH-056: print-to-stderr как logging внутри library-модулей
- Severity: LOW
- Confidence: MED
- Files: core/internal/shared/project_registry.py:233,244,255,258 (`deregister_project` печатает IMP-tagged сообщения в stderr); core/internal/provisioner.py:264,280,400; core/internal/bootstrap/discover_modules.py:180,304 (мешает logger + print в одном flow)
- Symbols: `deregister_project`, `provision_environment`
- Evidence: ~442 print сайта существуют вне shared/, но триаж показывает: большинство легитимно сидят в CLI слоях (__main__, *_cli.py, make-tool реализации типа project_remover/lister). Настоящая library-layer утечка узкая: project_registry (shared/, потребляется DeployEngine/lifecycle) эмитит `[IMP:x]`-диагностику через print(file=sys.stderr) вместо logger — невидимо для caplog/log-aggregation, нетестируемо стандартной LDD telemetry. Позитивный контроль: sys.exit leakage уже решён — AST-детектор static/sys_exit_contract.py enforce'ит exit-in-main-only с пустым allowlist (145 hits верифицированы как main()-scoped).
- Failure/maintenance scenario: consumer импортирует deregister_project внутри долгоживущего сервиса → stderr-шум минует structured logs; QA caplog assertions (LDD trajectory rule) не видят IMP:10 failure путь.
- Impact: minor пробел composability/observability в одном shared модуле + provisioner; не системно.
- Minimal fix: заменить print(msg, file=sys.stderr) на logger-вызовы в project_registry/provisioner/discover_modules (~15 строк).
- Code churn: S
- Phase: Post-launch
