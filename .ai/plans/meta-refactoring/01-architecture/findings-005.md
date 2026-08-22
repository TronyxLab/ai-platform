# Направление 5 — Hidden global state

Метод: rg module-level mutable containers + mutation sites; singleton/lru_cache/global; mutable default args; env-scatter. Известное из волны 1 (basicConfig ×3, signal/atexit, env-at-import scaffold/status-page) не ре-репортилось. Агент: explore, 15 tool calls. Дата 2026-08-22.
Итог: mutable-default-args: 0; class-level mutables: 0; глобальные операторы только в 2 сайтах ниже.

## ARCH-0043 — converge/infra.py: module-level аккумуляторы дрейфа/exit_code без гарантии reset
- Severity: MEDIUM · Confidence: HIGH · Churn: M · WHEN: post-launch (митигируется дисциплиной reset_state())
- Files: core/internal/bootstrap/converge/infra.py:77-88,96,138,152,194
- Symbols: `drifts: list[dict[str,str]] = []`, `exit_code`, `has_errors/warnings`, `converge_run_counter` (dead-global), `global` statements :96/:138/:194
- Evidence: mutation `drifts.append(entry)` (:152); `reset_state()` (:94) существует, но вызов — caller-discipline, guard'а нет
- Scenario: два reconcile/report цикла в одном процессе без reset_state() (тесты, in-process converge) аккумулируют дрейфы и sticky exit_code ≥1 → неверный вердикт
- Minimal fix: ReconcileState dataclass, возвращаемый runner'ом; reset_state() — единственный entry-guard

## ARCH-0044 — platform_config._defaults: `_loaded=True` ставится ДО чтения файла; первый caller замораживает env
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch (единственный источник дефолтов платформы)
- Files: core/internal/config/platform_config.py:51-52,75-78,83,117
- Symbols: `_defaults: dict = {}`, `_loaded`, `_load_defaults()`, PLATFORM_ROOT ambient read
- Evidence: `_loaded = True` (:78) выполняется ДО чтения platform-infra.yaml — любой transient fail (нет файла/неверный PLATFORM_ROOT при первом вызове) навсегда пинит `{}` для процесса; sentinel "" = S3 disabled молча
- Scenario: тест A ставит PLATFORM_ROOT → populate; тест B с другим root получает дефолты A навсегда; в проде ранний caller до core-deliver получает ""-дефолты с одним WARNING
- Minimal fix: `_loaded=True` только после успешного парса; добавить `_reset()` тест-хук

## ARCH-0045 — file_lock._REENTRANT: процесс-глобальный depth-registry может тихо отключить flock
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch (корректность сериализации деплоя)
- Files: core/internal/shared/file_lock.py:62,191,233,254,260
- Symbols: `_REENTRANT: dict[str,int] = {}` keyed by abspath, общий для ВСЕХ FileLock-инстансов
- Evidence: `_REENTRANT[self._key] += 1` (:191), `-= 1` (:254), `del` (:260); release() мимо exception-path оставляет depth >0
- Scenario: пропущенный release() на error-path (или fork, унаследовавший dict) → следующий acquire() считает себя реентрантным и SKIPает реальный flock → межпроцессная сериализация деплой-writers/state.json молча потеряна
- Impact: тихая потеря конкурентной защиты на deploy-пути
- Minimal fix: depth как instance attr + try/finally гарантия в __exit__; depth не переживает объект FileLock

## ARCH-0046 — decrypt_secrets._TEMP_FILES: мутабельный carrier при atexit/signal (коррелят ARCH-0015)
- Severity: LOW · Confidence: HIGH · Churn: S · WHEN: post-launch
- Files: core/internal/secrets/decrypt_secrets.py:83,127,252,303
- Evidence: append/remove/clear по глобальному списку tmp-key paths; несколько расшифровок в процессе накапливают пути; late remove() по уже стёртому пути роняет cleanup
- Minimal fix: instance/param-scoped tracker; cleanup идемпотентный (set, tolerate missing)

## ARCH-0047 — notifications._THROTTLE_REGISTRY: process-global dedup без bound и pruning
- Severity: LOW · Confidence: HIGH · Churn: S · WHEN: post-launch
- Files: core/internal/shared/notifications.py:103,532,542-550
- Evidence: `dict[tuple[str,str], float]`, default-DI override есть; два notify-цикла одного события в течение DEFAULT_THROTTLE_SECONDS=3600 в long-running процессе (status-page/watchdog) → второе подавлено; registry никогда не prune'ится (unbounded growth)
- Minimal fix: TTL/bound на registry; подавление уже логируется (IMP:8)

## ARCH-0048 — loadtest scenarios: LT_* конфигурация целиком заморожена на import (6 файлов × ~10 сайтов)
- Severity: LOW · Confidence: HIGH · Churn: M · WHEN: post-launch
- Files: core/loadtest/scenarios/{web,llm,llm_stream,langfuse_ingest,s3,db}.py:37-64 (+status-page corroborate)
- Evidence: module-level `os.environ.get("LT_...")`, включая `json.loads(os.environ.get("LT_BODY","{}"))` — ambient parse недоверенного env на import; LT_ENABLED гейт считается по замороженному значению
- Impact: смена LT_* между прогонами в одном процессе не действует; cross-test pollution
- Minimal fix: scenario_config() функция, читающая env на каждый вызов

## ARCH-0049 — s3_ssl_cache: ambient-чтение S3_BUCKET размазано по 7 call sites мимо platform_config
- Severity: LOW · Confidence: MED · Churn: M · WHEN: post-launch
- Files: core/internal/bootstrap/s3_ssl_cache.py:196,249,361,518,632,686,800
- Evidence: идентичное выражение `os.environ.get("S3_BUCKET", platform_config.…)` повторяется ≥7 раз; каждая функция перечитывает live env независимо
- Scenario: env мутирует между операциями в одном процессе → upload в bucket A, restore из bucket B (split-brain кэша сертификатов)
- Minimal fix: `_resolve_bucket()` один раз на операцию или DI-поток (cert_orchestrator уже DI-thread'ит s3_cache — зеркалировать)

## ARCH-0050 — provider_registry.load_registry: lru_cache(path) поверх мутабельного dict внутри frozen dataclass
- Severity: LOW · Confidence: MED · Churn: S · WHEN: post-launch
- Files: core/internal/bootstrap/provider_registry.py:208-223,76
- Evidence: `@functools.lru_cache(maxsize=4)` возвращает CertProviderRegistry (frozen) с `providers: dict` (default_factory) внутри — ложная неизменяемость; обновление certs-providers.yaml на диске (core-deliver rsync) не инвалидирует кэш в долгоживущем процессе
- Minimal fix: ключ cache = path+mtime_ns; возвращать MappingProxyType/deep-frozen копию

## Checked clean
Mutable-default-args: 0 хитов. Class-level mutables: 0. Read-only константы (без runtime-мутаций): SKIP_MODULES, _PORT_NAME_MAP, PHASES/PRECONDITIONS/HANDLERS, SSH_OPTS, _ALWAYS_EXCLUDE, BAD_DOCKER_STATES, RSYNC_EXCLUDES_*.
