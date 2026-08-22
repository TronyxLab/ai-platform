# Hidden global state audit

Метод: rg-sweep всего `core/` (368 .py) по 6 сигнатурам (env-записи, module-level мутабельные контейнеры, `global`/флаги, хардкод-пути вне deploy_paths, existence-check'и bootstrap/deploy, chdir/cwd) + deep-read 12 файлов для восстановления пар writer→reader. Записи в os.environ трактовались как неявный канал межмодульного состояния; для каждой — найден потребитель.

Счётчики: записей os.environ в core/ — **12** (remote_dispatch 1, lifecycle/cli 2, secrets_manager 4, htpasswd 3, helpers/secrets 1, docker_orchestrator 1). Мутабельных module-level кэшей — **4** (`_defaults` platform_config:51, `_TEMP_FILES` decrypt_secrets:83, `_REENTRANT` file_lock:62, `_THROTTLE_REGISTRY` notifications:103; остальные ^_-совпадения — иммутабельные константы/`__all__`) + **2 lru_cache** (notifications:253 maxsize=1, provider_registry:208 maxsize=4).

## ARCH-501: CLI→os.environ инжекция как скрытый транспорт аргументов
- **Severity:** High · **Confidence:** High
- **Files:** core/internal/bootstrap/lifecycle/cli.py:210-250 · **Symbols:** `_CLI_ENV_INJECTIONS`, `_inject_cli_env`
- **Evidence:**
  - cli.py:236 `def _inject_cli_env(args): """Inject CLI args into os.environ ..."""` — 15 пар attr→env (NODE_NAME, NODE_YAML, CONTEXT, TELEGRAM_BOT_TOKEN, GHCR_PULL_TOKEN...)
  - cli.py:244/247 три семантики записи: `setdefault` / `override` / `flag` → `"true"`
  - Читатели: context_deployer.py:856 `os.environ.get("CONTEXT", ...)`, phases/docker.py:597, deploy_orchestrator.py:934, cli.py:1027
- **Scenario:** оператор запускает `bootstrap-node --context prod`; фаза A получает CONTEXT из env (инжекция), но shell-хук, вызванный между фазами с чистым env, и код, читающий `args.context` напрямую, видят разные источники. При `setdefault` предустановленный env тихо побеждает явный CLI-флаг — приоритет «CLI > env» инвертируется незаметно.
- **Impact:** 15 неявных зависимостей argparse→глубокие модули через env; поведение меняется от порядка инициализации; рассинхрон «что передал оператор» vs «что увидела фаза» недиагностируем без знания таблицы инжекций.
- **Minimal fix:** пробрасывать typed config-object (dataclass) через run_init/run_update вместо env-таблицы; env оставить только на shell-границе (node-lifecycle.sh контракт).
- **Churn:** ~M (cli.py + 5 читателей CONTEXT/NODE_NAME) · **Phase:** Pre-launch

## ARCH-502: AGE-ключ доставляется мутируемым os.environ между несвязанными модулями
- **Severity:** High · **Confidence:** High
- **Files:** core/internal/bootstrap/remote_dispatch.py:320 → core/internal/shared/node_detect.py:113-130 · **Symbols:** writer `--age-secret-key-file`, reader `detect_age_key()` Check 3
- **Evidence:**
  - remote_dispatch.py:320 `os.environ["AGE_SECRET_KEY_FILE"] = args.age_secret_key_file` (docstring :24 «export-эквивалент shell»)
  - node_detect.py:130 `# ── Check 3: AGE_SECRET_KEY_FILE content ──` — приоритетная цепочка AGE_SECRET_KEY → SOPS_AGE_KEY → AGE_SECRET_KEY_FILE
  - core/AGENTS.md: «Env-override: AGE_SECRET_KEY (env сессии) перекрывает файл... предпочитать файл (`unset AGE_SECRET_KEY`)» — задокументированная ловушка того же канала
- **Scenario:** CLI-флаг одного entrypoint молча становится env-состоянием процесса; node_detect в другом пакете (shared/) разрешает ключ по цепочке, где сессионный AGE_SECRET_KEY (поставлен CI/окружением) тише перекрывает явно переданный файл. Оператор думает, что деплоит ключом X — расшифровка идёт ключом Y.
- **Impact:** секрет-канал без единой точки контроля; ошибка выбора источника ключа = расшифровка чужим ключом, видимая только как sops-fail глубоко в φ4.
- **Minimal fix:** detect_age_key() принимать explicit-priority аргументом из dispatch (key_source enum), а не читать env-лестницу; writer писать в структуру конфига процесса.
- **Churn:** S-M (node_detect API + 2 call-site) · **Phase:** Pre-launch

## ARCH-503: converge/infra.py — 11 модульных глобалов, reset_state() покрывает 4
- **Severity:** Medium-High · **Confidence:** High
- **Files:** core/internal/bootstrap/converge/infra.py:77-88,94-101 · core/internal/bootstrap/converge/reconciler.py:162-172 · **Symbols:** `drifts`, `exit_code`, `has_errors/warnings`, `node_name`, `dry_run`, `report_only`, `core_dir`, `templates_dir`, `modules_dir`, `converge_run_counter`, `reset_state()`
- **Evidence:**
  - infra.py:77-88 блок «Модульные глобалы (мигрированы из reconciler.py — публичные имена)»: drifts=[], exit_code=0, node_name="", dry_run=False...
  - infra.py:96-100 `global drifts, exit_code, has_errors, has_warnings` — reset только этой четвёрки
  - reconciler.py:162-168 `infra.node_name = args.node_name; infra.dry_run = args.dry_run; infra.core_dir = args.core_dir or ...`
- **Scenario:** второй reconcile-прогон в том же процессе (pytest-сьют, программный вызов двух нод подряд): node_name/dry_run/report_only/converge_run_counter остаются от первого прогона — отчёт report_emit() (:170-176) подписывает JSON старым node_name, dry-run юниты молча применяют мутации (или наоборот). Контракт docstring :14-15 требует `import infra as infra` — from-import заморозит значения в момент импорта.
- **Impact:** cross-run утечка конфигурации выполнения; корректность зависит от дисциплины всех доменных R-юнитов (атрибутный доступ) и от вызова reset_state вручную.
- **Minimal fix:** собрать глобалы в `@dataclass ConvergeContext`, передавать параметром; reset_state удалить за ненадобностью.
- **Churn:** M-L (~10 файлов converge/*, механическая замена `infra.x` → `ctx.x`) · **Phase:** Post-launch

## ARCH-504: platform_config._loaded — latch кэширует и провал загрузки навсегда
- **Severity:** Medium · **Confidence:** High
- **Files:** core/internal/config/platform_config.py:51-52,75-102 · **Symbols:** `_defaults`, `_loaded`, `_load_defaults()`, accessors (backup_config, s3_ssl_cache, cert_orchestrator, preflight, docker_orchestrator, context_deployer — потребители по module contract :7-8)
- **Evidence:**
  - :51-52 `_defaults: dict[str, str] = {}; _loaded = False`
  - :76-78 `if _loaded: return; _loaded = True` — флаг ставится ДО попытки чтения файла
  - :96-102 файл не найден → WARNING + return; повторный вызов никогда не ретраит
- **Scenario:** первый accessor-вызов происходит до появления/корректного PLATFORM_ROOT (тест-фикстура выставляет PLATFORM_ROOT после импорта; долгоживущий orchestrator вызывает get_default до монтирования SoT) — процесс навсегда получает `""` (fail-visible деградирует в fail-silent внутри одного процесса), CONTEXT/S3-defaults пустые во всех последующих фазах.
- **Impact:** порядок импортов определяет конфигурацию; warning виден один раз в начале лога и теряется; sibling-кэши того же класса — provider_registry.load_registry lru_cache(4) и notifications._load_catalog lru_cache(1) (mutable dict наружу).
- **Minimal fix:** ставить `_loaded=True` только после успешного parse (или хранить результат-исключение и ретраить); добавить `reset_cache()` для тестов.
- **Churn:** S (один файл) · **Phase:** Pre-launch

## ARCH-505: bulk-source secrets.env в os.environ — амбиентные креды процесса
- **Severity:** High · **Confidence:** High
- **Files:** core/internal/bootstrap/lifecycle/secrets_manager.py:459-460,503,748,836 · **Symbols:** `ensure_all_secrets` Step 1, `_generate_secret`
- **Evidence:**
  - :459-460 `for key, value in env_vars.items(): if key not in os.environ: os.environ[key] = value` — ВСЕ ключи secrets.env (TELEGRAM_*, GHCR_PULL_TOKEN, S3_*, ...) становятся env процесса
  - :503 `os.environ[var_name] = value` — автогенерированные креды туда же
  - htpasswd.py:138/151 пишет HTPASSWD_FILE, читает compose nginx (docker-compose.base.yml:88 `${HTPASSWD_FILE:-...}`); docker_orchestrator.py:372 пишет NGINX_OVERLAY_DIR, читает compose :84 `${NGINX_OVERLAY_DIR:?required}`
- **Scenario:** любой последующий модуль процесса (нотификации, compose-env passthrough дочерним subprocess) молча получает полный словарь секретов; условие `if key not in os.environ` означает «env сессии оператора побеждает sops-файл» — stale-переменная из shell разработчика тише подменяет продовое значение. Python→compose канал (NGINX_OVERLAY_DIR/HTPASSWD_FILE) работает только если compose вызван наследником этого процесса — прямой `docker compose up` падает на `:?` (задокументировано TRAP B23), т.е. контракт существует исключительно в виде env-мутации родителя.
- **Impact:** surface секретов шире необходимого (любой traceback/env-dump child-процесса); precedence-правила разбросаны по трём механизмам (setdefault/override/compose-interpolation).
- **Minimal fix:** возвращать dict и передавать в compose через `env=`/`--env-file` точечно; для reader-модулей — явный SecretsView-объект вместо os.environ.
- **Churn:** M (secrets_manager + docker_orchestrator env-проброс) · **Phase:** Post-launch

## ARCH-506: файловые маркеры без схемы/версии как межпроцессное состояние
- **Severity:** Medium · **Confidence:** High
- **Files:** core/internal/bootstrap/deploy/deploy_orchestrator.py:48,934 · core/internal/bootstrap/deploy/orchestrator_metrics.py:51,132-139 · core/internal/bootstrap/deploy/context_overlay.py:174-195 · **Symbols:** `.hc_done_in_deploy`, `CONTEXT_PULL_TS_PATH`
- **Evidence:**
  - deploy_orchestrator.py:48 «`_set_hc_marker` [W:1] — touch /var/lib/platform/.bootstrap/.hc_done_in_deploy»; :934 путь резолвится call-time от `os.environ.get("CONTEXT")`
  - orchestrator_metrics.py:51 `Path(os.environ.get("PLATFORM_STATE_DIR", str(deploy_paths.bootstrap_state_dir())), ".hc_done_in_deploy")` — существование файла = «healthcheck уже выполнен, standalone-прогон подавить»
  - context_overlay.py:181 `if pull_ts_path.exists(): last_pull = int(pull_ts_path.read_text().strip())` — голый int-timestamp, <300s = skip pull
- **Scenario:** parallel-deploy touch'ит маркер до реального healthcheck группы и падает посреди — следующий standalone-healthcheck подавлен «выполненным» состоянием; маркер без TTL/owner-pid/version переживает аварийный деплой. Аналогично pull-ts: часы контейнера/ноды назад вперёд → лишний или пропущенный git pull; битый контент гасится в 0 (тихий re-pull) без аларма. Путь маркера зависит от двух env (CONTEXT, PLATFORM_STATE_DIR) — писатель и читатель могут разойтись в путях при частичной инжекции (см. ARCH-501).
- **Impact:** existence-as-state без схемы = рассинхронизация writer/reader версий формата невозможна обнаружить; подавление healthcheck — safety-relevant.
- **Minimal fix:** JSON-маркер {version, ts, pid, context} + TTL-проверка читателем; общий helper в shared/ (один writer/reader-модуль).
- **Churn:** S-M (2 файла + helper) · **Phase:** Post-launch

---
Вне топ-6 (зафиксировано, низкий ранг): engine.py:186-197 chdir уже закрыт contextlib.chdir (TRAP задокументирован); validate_module_yaml.py:54-56 Path.cwd()-эвристика с candidates — cwd-предусловие, локализовано lint-тулом; loadtest/runner_remote.py:291 `/tmp/loadtest-<ts>` — документированный nosec-контракт удалённой ноды; decrypt_secrets._TEMP_FILES + atexit — легитимный cleanup-реестр tmpfs; file_lock._REENTRANT depth-счётчик — осознанный same-process реентрантный lock.
