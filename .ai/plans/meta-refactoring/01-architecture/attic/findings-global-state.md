# Direction 5 — Hidden Global State

Агент: форензик направления «hidden global state» · Дата: 2026-08-22

Итог направления: CRITICAL 0 · HIGH 2 (ARCH-040, ARCH-041) · MEDIUM 3 (ARCH-042..044) · LOW 1 (ARCH-045). State-дисциплина необычно хороша для такого размера (singletons отсутствуют, кэши редки, mutable globals документированы с DI seams); реальный долг концентрируется в os.environ как невидимой call-конвенции (ARCH-040) и env-name/default дрейфе между модулями (ARCH-041), которые сейчас не ловит ни один гейт.

---

### ARCH-040: os.environ используется как межмодульный параметр-канал (runtime-записи)
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/bootstrap/deploy/docker_orchestrator.py:353,372 · core/internal/bootstrap/lifecycle/htpasswd.py:138,151 · core/internal/bootstrap/remote_dispatch.py:320 · core/internal/bootstrap/lifecycle/cli.py:249
- Symbols: `deploy_docker_module()`, `ensure_htpasswd()`, `run_update()`, lifecycle CLI arg-normalization
- Evidence: docker_orchestrator.py:372 `os.environ["NGINX_OVERLAY_DIR"] = overlay_dir` — потребляется core/modules/nginx/docker-compose.base.yml:84 (`${NGINX_OVERLAY_DIR:?required}`); контракт живёт в YAML-комментарии, не в сигнатуре. docker_orchestrator.py:353 `os.environ.setdefault("COMPOSE_PROFILES", ...)` — мутация process env mid-deploy; каждый последующий `docker compose` subprocess того же процесса молча наследует. htpasswd.py:138,151 `os.environ["HTPASSWD_FILE"] = htpasswd_file` — поздние читатели в несвязанном слое резолвят через deploy_paths.py:317 (env > default), т.е. данные текут caller→callee через env, невидимые в call graph. remote_dispatch.py:320 `os.environ["AGE_SECRET_KEY_FILE"] = args.age_secret_key_file` — CLI-флаг реэкспортируется в env только чтобы `detect_age_key()` (node_detect chain, priority 3) его увидел; локальный путь секретного ключа утекает в env каждого child-процесса (ssh/rsync/docker).
- Failure/maintenance scenario: рефакторинг переименовал параметр или переставил вызовы → env больше не выставлен → downstream молча падает на другой default (htpasswd путь, compose profile set, key file) без signal от type-checker или гейта; параллельные деплои в одном процессе (DEPLOY_PARALLEL fork mode) гоняются за общим env dict.
- Impact: silent behavior switches в deploy/secrets путях; secret-смежные пути распространяются в нерелевантные child-процессы.
- Minimal fix: прокидывать значения явными аргументами/DI (паттерн существует: параметр `throttle_registry=` в notifications.py:532); env-записи ограничить process-boundary shim'ами.
- Code churn: M
- Phase: Post-launch

### ARCH-041: Неявные env-контракты с расходящимися defaults (PLATFORM_DOMAIN, node identity triple)
- Severity: HIGH
- Confidence: HIGH
- Files: PLATFORM_DOMAIN — core/modules/postgres/hooks/on_project_deploy.py:416, core/modules/status-page/collectors/checks/platform.py:31, core/internal/bootstrap/converge/projects.py:292 (все `"ai-platform.local"`); core/internal/bootstrap/preflight.py:507, issue_cert.py:177, lifecycle/secrets_manager.py:633, scaffold/project_scaffolder.py:81 (`""`); scaffold/vhost_renderer.py:1138, dev_hosts.py:601 (`None`). Node identity — scaffold/context_initializer.py:50 (`NODE`, `"tronyx-vps"`), verify/domain_verifier.py:473 (`NODE`, `""`), healthcheck/modules_healthcheck.py:285, lifecycle/cli.py:1028, bootstrap/deploy/context_deployer.py:1260, secrets_manager.py:554 (`NODE_NAME`, `""`), scaffold/scaffold_helpers.py:58, gen_project_platform_md.py:585, shared/project_yaml.py:299 (`PLATFORM_DEFAULT_NODE`; mixed `"tronyx-vps"`/`""`), deploy/receive_flow.py:560 (`NODE_NAME`→`NODE` fallback chain)
- Symbols: `PLATFORM_DOMAIN`, `NODE`, `NODE_NAME`, `PLATFORM_DEFAULT_NODE`
- Evidence: таблица дивергенции defaults для одного логического понятия («platform domain»): `"ai-platform.local"` ×3 vs `""` ×4 vs unset ×2; три различных env-имени для «какая нода» с захардкоженным литералом `"tronyx-vps"` в 4 модулях (context_initializer.py:50, scaffold_helpers.py:58, gen_project_platform_md.py:585, project_yaml.py:299) — нарушение собственного zero-hardcode правила вне SoT.
- Failure/maintenance scenario: unset env на свежем раннере → cert issuance падает видимо (`""`), тогда как postgres hook тихо provisioning'ит против `ai-platform.local`, а scaffolder пинит `tronyx-vps` — одна команда, три разных failure mode в зависимости от того, какой модуль первым резолвнул переменную.
- Impact: environment-dependent поведенческая дивергенция между модулями; захардкоженное имя ноды блокирует multi-node/context reuse.
- Minimal fix: направить обе через существующие SoT facades (platform_config.get_default для PLATFORM_DOMAIN; единый node_resolver вход для node triple), удалить per-module литералы.
- Code churn: M
- Phase: Pre-launch (до следующего context onboarding'а)

### ARCH-042: Import-time environment snapshots — порядок импортов становится поведением
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/scaffold/scaffold_helpers.py:58 (`_DEFAULT_NODE = os.environ.get("PLATFORM_DEFAULT_NODE", "tronyx-vps")`) · core/internal/scaffold/context_initializer.py:50 · core/modules/status-page/collectors/checks/platform.py:31 (`PLATFORM_DOMAIN = os.environ.get(...)`) · core/internal/bootstrap/deploy/orchestrator_metrics.py:55 (`_STATUS_METRICS_PATH = str(deploy_paths.status_metrics_json())`)
- Symbols: `_DEFAULT_NODE` ×2, `PLATFORM_DOMAIN` (module constant), `_STATUS_METRICS_PATH`
- Evidence: четыре module-level константы замораживают env при импорте; ни одна не перечитывается в call time. Контраст: domain_verifier.py:473 корректно читает в call time.
- Failure/maintenance scenario: тесты или долгоживущие in-process потребители, делающие setenv после первого импорта, получают stale значения (классический генератор flaky-тестов); status-page collector контейнер наследует env времени image/import вместо runtime-конфигурации.
- Impact: order-dependent поведение; изменения env невидимы после первого импорта любого из этих модулей.
- Minimal fix: перевести на call-time resolution внутри функций (механически, низкий риск).
- Code churn: S
- Phase: Pre-launch

### ARCH-043: Lazy глобальный кэш `_defaults`/`_loaded` в platform_config (first-call-wins, sticky failures)
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/config/platform_config.py:51-52 (`_defaults: dict = {}`, `_loaded = False`), mutation site :75-78,117 (`global _defaults, _loaded`; `_loaded = True` до попытки загрузки)
- Symbols: `_load_defaults()`, `get_default()`
- Evidence: consumers из @scope (backup_config, s3_ssl_cache, cert_orchestrator, preflight, docker_orchestrator, context_deployer) все идут через `get_default()`; snapshot PLATFORM_ROOT первого caller'а кэшируется навсегда (:83 read внутри guarded блока); `_loaded=True` ставится ДО I/O (:78) — транзиентно отсутствующий/непарсящийся platform-infra.yaml кэшируется как перманентный `""`.
- Failure/maintenance scenario: тест A импортирует и триггерит загрузку с неверным PLATFORM_ROOT → все последующие accessor'ы процесса возвращают stale defaults; на ноде с временной недоступностью файла (mount ordering) весь процесс деградирует до пустых defaults несмотря на последующее восстановление.
- Impact: stale-config класс багов, почти отлаживаемых только при знании о кэше; не thread-safe (безобидно для CLI сегодня, латентно для in-process использования).
- Minimal fix: expose `reload()`/cache-clear seam для тестов, перенос `_loaded = True` после успешного parse, документировать non-reentrancy.
- Code churn: S
- Phase: Post-launch

### ARCH-044: status-metrics.json — два writer-семейства с несинхронизированными lock-доменами
- Severity: MEDIUM
- Confidence: MED
- Files: core/internal/bootstrap/deploy/deploy_orchestrator.py:952-962 (`_create_status_metrics_json`: plain `open("w")` + `fh.write`, без FileLock, не-атомарно) · core/internal/healthcheck/platform_export_metrics.py:81-88 + core/internal/healthcheck/metrics/json_writer.py (atomic write, host-cron flock `/run/lock/platform-metrics.lock` — system.py:293, platform-export-metrics.sh:6) · readers: core/modules/status-page/collectors/*
- Symbols: `_create_status_metrics_json()`, `_STATUS_METRICS_PATH` (deploy_orchestrator.py:136), `status_metrics_json()` resolver
- Evidence: один и тот же путь `/var/lib/platform/run/status-metrics.json` пишется deploy-оркестратором (Python FileLock домен здесь не используется) и минутным cron (shell flock + atomic replace). Deploy-side write митигирует blast radius через `if exists: return` (:954), но окно создания — голая не-атомарная запись, гонящаяся с cron writer'ом/atomic replace. Python fcntl.flock и shell flock(1) совместимы, но deploy-site не берёт ни тот, ни другой.
- Failure/maintenance scenario: fresh node / post-cleanup деплой гонится с cron tick → status-page читает порванный или пустой placeholder JSON; будущие правки, превращающие placeholder в полный rewrite, молча затрут живые метрики.
- Impact: повреждённые/пустые метрики статус-страницы на узкой гонке; ownership split (bootstrap/deploy vs healthcheck) означает, что ни один модуль не может безопасно сменить формат.
- Minimal fix: route deploy-side create через shared/atomic_writer + взять тот же platform-metrics lock (или делегировать platform_export_metrics).
- Code churn: S
- Phase: Post-launch

### ARCH-045: Runtime-мутируемые module-level реестры (throttle, temp-files, reentrancy depth)
- Severity: LOW
- Confidence: HIGH
- Files: core/internal/shared/notifications.py:103 `_THROTTLE_REGISTRY: dict[tuple[str,str], float] = {}` (мутация в `notify_event` через default binding :532) · core/internal/secrets/decrypt_secrets.py:83 `_TEMP_FILES: list[str] = []` (append :252, remove :302-303, clear :127, atexit :142) · core/internal/shared/file_lock.py:62 `_REENTRANT: dict[str,int] = {}`
- Symbols: `_THROTTLE_REGISTRY`, `_TEMP_FILES`, `_REENTRANT`
- Evidence: три подлинно mutable process-global реестра. Митигации уже присутствуют: throttle имеет DI seam (параметр `throttle_registry=`, docstring :103 фиксирует rationale process-one-shot); temp-files — сознательная atexit+signal cleanup архитектура (TRAP[DECISION]:31); reentrancy registry документирует rationale non-reentrancy flock (:59-61). Остаточные риски — неограниченный рост throttle keys (long-lived in-process consumers) и depth leak `_REENTRANT` при acquire между failed release путями — оба сейчас недостижимы в CLI-shaped процессах.
- Failure/maintenance scenario: notification suppression state персистит через логически независимые операции одного long-running процесса (suppressed alert маскирует второе настоящее событие в пределах окна).
- Impact: minor; маркер дисциплины — это единственные честные mutable globals, найденные аудитом, и все документированы.
- Minimal fix: не требуется; опционально cap размера throttle-реестра и `clear_throttle()` test seam.
- Code churn: S
- Phase: Post-launch
