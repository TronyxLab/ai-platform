# Stale reads & cache consistency audit

Метод: статический forensic-обход шести производных-состояний (generated-манифесты, fingerprint-кэш check_suite, .env.platform, practices.lock, Python-кэши долгоживущих процессов, rendered nginx). Каждый вывод подтверждён чтением кода (file:line); make-таргеты не запускались.

## DATA-401: .env.platform не имеет контракта свежести — stale DSN доставляется payload'ом молча
- **Severity:** HIGH · **Confidence:** HIGH
- **Files:** core/internal/scaffold/gen_env_platform.py:302-303 · core/internal/deploy/verify_contracts.py:505-526,798-818 · core/modules/postgres/hooks/on_project_deploy.py:274
- **Symbols:** `generate()` · `_check_env_file()` · `ensure_project_db_access()` (regen только при первом создании роли)
- **Invariant:** «.env.platform — GENERATED; устарел → make sync-env» — но ни один автоматический потребитель не проверяет свежесть; в файле нет hash/version источника (только timestamp-шапка).
- **Violating scenario (START→END):**
  START: платформа меняет порт/хост сервиса в platform-infra.yaml → `make context-promote` → нода обновлена (platform-env.yaml на ноде свежий).
  Операция: проект `git push` → CI → receive: payload доставляет ЗАКОММИЧЕННЫЙ устаревший .env.platform (FILES payload'а включает его) → `orchestrator.deploy` → compose up с env_file=.env.platform.
  Чтение stale: контейнеры проекта резолвят PLATFORM_*_DSN/URL на старый порт/хост.
  END: сервис работает против несуществующего endpoint'а (или молча против старого инстанса).
- **Почему END некорректен:** verify_contracts (K3) проверяет только ИМЯ env_file (`_check_env_file`: допустим только '.env.platform', содержимое/свежесть не сравниваются с platform-env.yaml ноды); drift-practices на VPS смотрит только practices.lock, не env; postgres-хук перегенерирует .env.platform только при первом создании роли. Блокирующего сигнала нет — детект лишь по факту падения healthcheck/приложения.
- **Evidence:** gen_env_platform.py:303 `lines.append(f"# Generated: {datetime.now(...)}")` — единственная метка; verify_contracts.py:517 `if fname != ENV_FILE_PLATFORM` — единственная env-проверка; on_project_deploy.py:274 «ТОЛЬКО при первом создании роли».
- **Impact:** после любой миграции портов/хостов платформы все неперегенерированные проекты деплоятся с невалидным окружением; окно неограничено.
- **Minimal fix:** писать в .env.platform stamp `PLATFORM_ENV_SHA256=<sha256 нормализованного provides-блока platform-env.yaml>`; K3 на VPS сравнивает stamp с локальным platform-env.yaml (block в L1 или warning в L2) + `make sync-env` как repair.
- **Required test:** R5-negative — изменить порт в platform-env.yaml, задеплоить проект без sync-env → ожидаем [PRACTICES:*]-находку/блок; текущий код даёт PASS.
- **Phase:** K3/deploy-gate

## DATA-402: practices.lock file_hashes не проверяются вне локальной K1 — VPS K3 сверяет только version
- **Severity:** MED · **Confidence:** HIGH
- **Files:** core/internal/deploy/verify_contracts.py:798-818 · core/internal/practices/check_project/drift.py:65-80 · core/internal/practices/generators.py:520 · .github/workflows/deploy-project.yml:293-297
- **Symbols:** `_check_drift_practices()` · `_detect_drift()` · `render_lock()` (files: path→sha256)
- **Invariant:** «practices.lock — снапшот канона; sha256 GENERATED-файлов» — lock несёт file_hashes, но единственный потребитель хэшей — локальный drift-гейт K1.
- **Violating scenario:** dev вручную правит pyproject.toml (GENERATED-шапка сохранена) → commit+push → CI K2 не сверяет хэши (только maturity-age warning) → payload атомарно доставляет изменённый файл + lock с тем же version → VPS K3: `lock.version < canon_version` = false → [PRACTICES:OK]. Дрейф живёт до случайного локального `make project-check`.
- **Evidence:** verify_contracts.py:809 `if lock.version < canon_version:` — единственная drift-проверка на VPS; drift.py:67-80 — полная сверка `lock.files` vs диск, но вызывается только из check_project (локально).
- **Impact:** L2-контракт «ручная правка GENERATED = дрейф» не enforced на VPS вообще; в active-full блок невозможен по построению.
- **Minimal fix:** в `_check_drift_practices` добавить сверку `lock.files` sha256 против файлов project_dir (O(F), файлы уже на VPS); finding `drift-practices (file)`.
- **Required test:** unit на verify_contracts: изменённый GENERATED-файл + lock version==canon → finding; negative на текущее поведение.
- **Phase:** K3

## DATA-403: окно SoT→generated: локальные потребители читают generated без staleness-guard, детектор — только полный make check/CI
- **Severity:** MED · **Confidence:** HIGH
- **Files:** core/internal/scripts/generate_help.py:66 · core/internal/lint/doc_header_validator.py:450 · tests/_conftest/env.py:34,123-137 · core/check-suite.yaml:107
- **Symbols:** `load_registry()` (make help) · doc_header_validator (agent-check) · `SMOKE_ENV_GENERATED`/fallback env_defaults_generated · check-manifests gate
- **Invariant:** «Generated files коммитятся, но НЕ редактируются вручную; divergence ловит check-manifests» — но check-manifests исполняется только в полном `make check`/CI, а перечисленные потребители читают generated напрямую при каждом вызове.
- **Violating scenario:** правка Makefile/.PHONY или module.yaml/secret-definitions.yaml локально до `make generate-manifests`: `make help` показывает устаревший реестр глаголов; `make agent-check` валидирует doc-headers против устаревшего entrypoint-manifest (ложный RED по новому таргету / ложный GREEN по удалённому — allowed_verbs продолжает пропускать); smoke читает platform-env.yaml (primary) — self-consistent зелёный против устаревшей фактуры.
- **Evidence:** generate_help.py:66 «Load entrypoint-manifest.yaml → opaque mapping»; env.py:34 импорт SMOKE_ENV_GENERATED + fallback при отсутствии platform-env.yaml; детектор — tests/gates/test_gate_manifests_up_to_date.py (в сьюте check-manifests, diagnostic-прогон).
- **Impact:** в окне «правка SoT → generate» локальные узкие таргеты принимают решения по устаревшему состоянию; направление в основном false-RED (безопасно), но allowed_verbs-пропуск удалённого глагола и self-consistent smoke — false-GREEN.
- **Minimal fix:** лёгкий freshness-чек в потребителях (mtime/sha SoT vs generated, как check-requirements byte-level) с warning «run make generate-manifests»; или прогон check-manifests в quick-пути agent-check.
- **Required test:** unit — mutate Makefile .PHONY без регенерации → help/agent-check сигнализируют stale.
- **Phase:** dev-loop/agent-check

## DATA-404: fingerprint-кэш make check: ключ = контент tracked+untracked дерева; ignored-файлы вне ключа, unreadable-файлы молча пропускаются
- **Severity:** LOW · **Confidence:** HIGH (механика), impact редкий
- **Files:** core/internal/check_suite/fingerprint.py:76-100,114-139 · core/internal/check_suite/diagnostic.py:104-112,305
- **Symbols:** `tree_files()` (`git ls-files -c -o --exclude-standard`) · `compute_fingerprint()` · `_maybe_replay_cached()`
- **Invariant:** «любая правка/untracked-файл → miss» — не выполняется для (а) gitignored-файлов, (б) файлов, чтение которых упало (OSError→continue, файл исключён из хэша молча).
- **Violating scenario:** проверка из diagnostic-набора зависит от gitignored-артефакта (или tracked-файл временно unreadable в момент хэширования) → дерево изменилось, fp идентичен предыдущему → replay печатает кэшированный зелёный отчёт (fp+status=green) без единого запуска проверок.
- **Evidence:** fingerprint.py:132-133 `except OSError: continue` (файл не попадает в хэш); fingerprint.py:81 `--exclude-standard` (ignored вне ключа); diagnostic.py:112 `cached.get("fingerprint") == fp and status == "green"`.
- **Impact:** ложный «зелёный» replay возможен, но поверхность мала: env-зависимые сьюты (smoke/component/predeploy-docker/e2e-verify) в diagnostic не входят (check-suite.yaml diagnostic:false), report*.xml/.test_counter.json исключены осознанно.
- **Minimal fix:** включать в хэш имена+факты ошибок чтения (счётчик unreadable → fp-соль) и warning при skip; для tracked-файлов OSError → fail-loud вместо continue.
- **Required test:** unit — chmod 000 на tracked-файле → fp меняется/ошибка видима; ignored-файл, читаемый проверкой, документированно вне ключа.
- **Phase:** make check (diagnostic cache)

## DATA-405: nginx «rendered but not applied» — маркер отсутствует, reload живёт в другом канале
- **Severity:** MED · **Confidence:** HIGH
- **Files:** core/internal/scaffold/vhost_renderer.py:942-967,1053 · core/modules/nginx/nginx_reload_hook.sh:34-65 · core/internal/scaffold/nginx_harness.py:55
- **Symbols:** `render_all()` (atomic mv → overlay dir) · `_trigger_deploy_hooks()` → nginx_reload_hook.sh · `nginx_t_harness()`
- **Invariant:** I3/I5 рендера (all-or-nothing, content-hash в шапке .conf) фиксируют «файл сгенерирован и валиден», но НЕ фиксируют «nginx применил» — нет записи о последнем reload и нет сверки overlay-hash ↔ live.
- **Violating scenario:** `make render-vhosts NODE=<n>` пишет свежие .conf в `<node-configs>/<node>/overlays/nginx` (локально) и завершается успехом; доставка на ноду — отдельный канал (converge/SCP), reload — только хук on_project_deploy при деплое проекта. Между рендером и reload nginx обслуживает старые vhost'ы, и ни один маркер/команда это не показывает; `make render-vhosts` выглядит как «применено».
- **Evidence:** vhost_renderer.py:967 «Atomic mv complete: %d vhost(s) → %s» — финальная точка пайплайна, reload/hook не вызывается; nginx_reload_hook.sh:5 «Called by _trigger_deploy_hooks() after project deploy» — единственный reload-канал.
- **Impact:** новый/изменённый домен не обслуживается (или обслуживается старая конфигурация) до следующего деплоя проекта/конверга; диагностика требует ручного сравнения.
- **Minimal fix:** после render-all печатать статус «rendered, NOT applied — apply: converge/deploy» + поле last_rendered_hash; сверка overlay content-hash vs live conf.d на ноде в `make check`/healthcheck nginx-модуля.
- **Required test:** integration — render-all без последующего deploy → статус/маркер «pending apply»; после reload-хука маркер снят.
- **Phase:** render-vhosts/nginx-module

## DATA-406: Python-кэши долгоживущих процессов — TTL-by-design у экспортёра; torn-read окна у status-page
- **Severity:** LOW · **Confidence:** MED
- **Files:** core/internal/healthcheck/platform_export_metrics.py:13,370 · core/internal/healthcheck/metrics/cache.py:15-16,87-113 · core/internal/healthcheck/metrics/json_writer.py:11-16,46-59 · core/modules/status-page/app.py:63-66,86
- **Symbols:** CacheManager (TTL 3600 + mtime-invalidation) · `_get_image_sizes_cached()` · `atomic_write()` (direct overwrite, inode-preserving) · `load_node_yaml()` per-request
- **Invariant:** «inventory-данные кэшируются с TTL 1h + mtime-invalidation» — осознанный дизайн; статус-page читает node.yaml/metrics per-request без кэша (stale-кэша нет), но writer пишет direct overwrite (не rename) → конкурентный reader может прочитать частичный JSON.
- **Violating scenario:** platform_export_metrics пишет status-metrics.json в момент, когда status-page читает его → json.load падает → collectors/config.py возвращает fallback {} → страница/агрегат показывает пустые данные (не stale, а missing) до следующего цикла; удаление проекта отражается в метриках с лагом до 1h (TTL, задокументировано).
- **Evidence:** json_writer.py:11 «Direct overwrite (NOT os.replace) — preserves inode» (TRAP[DOCKER-BIND-MOUNT]); cache.py:16 «mtime-based invalidation»; app.py:86 node.yaml читается в обработчике запроса.
- **Impact:** кратковременные пустые/отложенные данные наблюдаемости; не влияет на деплой-решения. Отдельный плюс: compose_profiles.py:13 читает SoT platform-infra.yaml, минуя generated — stale-окна нет.
- **Minimal fix:** reader-side retry/last-good-копия последнего валидного JSON (пишется writer'ом рядом) при JSONDecodeError.
- **Required test:** concurrency-тест — writer в цикле + reader: ни одного частичного разбора за N итераций (или fallback last-good срабатывает).
- **Phase:** monitoring/status-page

---
Сводка: 6 находок (1 HIGH, 2 MED по существу + 1 MED-инфра, 2 LOW). Общий здоровый слой: fingerprint — контент-хэш всего дерева (не mtime), dispatch forced-command читает CANONICAL_VERBS из shared/verbs.py (не generated), compose_profiles читает SoT напрямую.
