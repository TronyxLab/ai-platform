# Overengineering audit

Метод: статический анализ core/internal (grep/read-only), трассировка цепочек вызовов
entrypoint → lib → Python CLI → driver → facade для 3 операций (`make status`, `make project-status`,
`make healthcheck`); подсчёт потребителей каждого shared-модуля/класса (rg -l, исключая tests/unit,
__pycache__, собственный файл). Задокументированные решения (3 шаблонизатора, parity-гейты,
generated-манифесты как контракт) не флагались; флагались только недокументированные издержки.

## ARCH-901: module_interface.invoke — круговой заход Python→bash→Python
- **Severity:** Medium · **Confidence:** High
- **Files:** `core/internal/shared/module_interface.py` (321 LOC), `core/lib/module-interface.sh` (26 LOC) · **Symbols:** `invoke()` (module_interface.py:74-83), `dispatch()` (module_interface.py:204), `invoke_module_interface()` (module-interface.sh:22-24) · **Evidence:** `invoke()` собирает `bash -c "source paths.sh && source module-interface.sh && invoke_module_interface …"` (module_interface.py:74-83); единственная функция shell-фасада — `python3 -m core.internal.shared.module_interface invoke "$@"` (module-interface.sh:24) — возврат в ТОТ ЖЕ модуль, где живёт вся логика (`dispatch()`, D4/DevPlan 119). Bash-хоп — рудимент до-D4-логики, оставшийся после переноса её в Python. `source paths.sh` в команде ничего не потребляет: dispatch резолвит пути сам (resolve_module_dir, module_interface.py:127-135).
- **Scenario:** `make healthcheck` (makefiles/modules.mk:79) → healthcheck.sh:17 (exec python) → modules_healthcheck → `invoke()` → bash → python (тот же модуль) → `dispatch()` → bash `core/modules/<m>/healthcheck.sh` → docker compose. 6 хопов на 1 интерфейс; ~2 лишних процесса + 2 старта интерпретатора (~40–60 ms) на каждый вызов.
- **Impact:** 4 прод-потребителя платят накладными на каждый вызов (modules_healthcheck.py:45, deploy_orchestrator.py:120, post_deploy_chain.py:196, healthcheck_runner.py:25): healthcheck ≈ 2×N docker-модулей вызовов за прогон; deploy/post-deploy chain — то же. Логика размазана по 3 файлам (py + 2 sh); DI-seam'ы и тесты (test_module_interface.py, test_shared_module_interface.py) тестируют bash-транзит вместо бизнес-поведения.
- **Minimal fix:** `invoke()` вызывает `dispatch()` in-process (тот же модуль); CLI-вход `python3 -m … invoke` сохранить для внешних shell-вызовов. Удалить мёртвый `source paths.sh`.
- **Churn:** S (~20 LOC + правка 2 unit-тестов) · **Phase:** Pre-launch

## ARCH-902: стек schema-валидации — трёхслойный subprocess-wrapping + рудиментарный ajv-бэкенд
- **Severity:** Medium · **Confidence:** High
- **Files:** `core/internal/validate/validate_orchestrator.py` (587 LOC), `core/internal/scripts/jsonschema_validate.py` (157 LOC), `core/internal/shared/schema_validator.py` (151 LOC), `core/entrypoints/validate.sh` · **Symbols:** `detect_validator` (validate_orchestrator.py:135), `validate_with_ajv` (:252), `validate_with_python` (:329), `main` (jsonschema_validate.py:79) · **Evidence:** python-путь валидации одного YAML: validate_orchestrator (процесс 1) → subprocess `python -m scripts.jsonschema_validate` (процесс 2) → import schema_validator. Ядро могло вызываться in-process одной функцией (`schema_validator.validate_yaml_against_schema`). Docstring фиксирует осознанность: «этот вызов — wrapper над wrapper'ом, не трогаем» (validate_orchestrator.py:339-341) — путь закреплён DevPlan 093 D6 без технической причины. Единственный потребитель jsonschema_validate.py — сам orchestrator (+собственные тесты). Второй бэкенд: ajv (Node.js CLI) имеет ПРИОРИТЕТ (detect_validator :157-158), но нигде не провижинится — ajv отсутствует в .github/workflows/*, package.json, requirements (найден только в templates/template-frontend/package-lock.json как транзитивный чужой dep). validate_with_ajv (~77 LOC + tmp-file танцы + DI-параметры detect_validator) обслуживается «байт-идентично validate.sh» — порт унаследованного shell-поведения через две миграции.
- **Scenario:** `make check` → check-suite → validate.sh → validate_orchestrator: для каждого YAML — спавн subprocess даже при доступном in-process ядре; при наличии глобального ajv у разработчика — незапланированный Node-путь с другим форматом ошибок.
- **Impact:** +1 процесс на файл валидации; ~230 LOC (jsonschema_validate 157 + ajv-ветка ~77) существуют ради сохранения исторического пути/мёртвой ветки; двойной формат ошибок (« > » vs многострочный) надо поддерживать синхронно.
- **Minimal fix:** validate_orchestrator импортирует `schema_validator.validate_yaml_against_schema` напрямую; удалить ajv-ветку и detect_validator (оставить python-канон); jsonschema_validate.py оставить как тонкий CLI или удалить вместе с гейтом-зеркалом.
- **Churn:** M (один файл-оркестратор + тесты test_validate_orchestrator/test_jsonschema_validate/test_gate_single_project_parser) · **Phase:** Pre-launch

## ARCH-903: sync_env_defaults — 56 хардкодных fallback-дефолтов дублируют SoT; дрейф уже случился
- **Severity:** Low-Medium · **Confidence:** High
- **Files:** `core/internal/scripts/sync_env_defaults.py` (961 LOC) vs `core/platform-infra.yaml` (SoT) · **Symbols:** `_get_env_val(env_defaults, K, DEFAULT)` ×56, `_section_*` ×25 · **Evidence:** генератор `.env.example` повторяет значения platform-infra.yaml вторым экземпляром в Python-коде: `PGBOUNCER_IMAGE` fallback `"edoburu/pgbouncer:v1.25.2-p0"` БЕЗ digest (sync_env_defaults.py:370) против digest-pinned SoT (platform-infra.yaml:173, политика Digest-pin AGENTS.md) — копия уже разошлась с каноном. Аналогично POSTGRES_HOST="pgbouncer" (:357), REDIS_HOST="redis" (:385) и ещё 53 дефолта.
- **Scenario:** изменение значения в platform-infra.yaml не ломает генерацию (ключ присутствует, fallback молчит), но при удалении/переименовании ключа в .env.example бесшумно попадает устаревшее значение — silent config drift без гейта (гейт ловит только рассинхрон файла с генератором, а не генератора с SoT).
- **Impact:** 56 точек потенциального дрейфа вне манифестного контракта; 961 LOC, из которых ~700 — 25 рукописных `_section_*` функций, группировка которых могла бы быть данными (поле group в infra-манифесте), а не кодом.
- **Minimal fix:** fallback = fail-fast (`_get_val_required`) либо чтение дефолтов из platform-infra.yaml; хардкодные строковые дефолты из секций убрать. Полная дата-драйв переработка секций — опционально (Post-launch).
- **Churn:** S-M (один файл) · **Phase:** Pre-launch (устранение дубликатов), Post-launch (data-driven секции)

## ARCH-904: shared/content_hash.py — мёртвый shared-модуль при живых inline-дублях того же алгоритма
- **Severity:** Low · **Confidence:** High
- **Files:** `core/internal/shared/content_hash.py` (146 LOC), `core/internal/bootstrap/python_deps.py`, `core/internal/bootstrap/reboot_policy.py` · **Symbols:** `compute_content_hash` (content_hash.py:43); дубли: `_compute_content_hash` (python_deps.py:101-115), `content_hash` (reboot_policy.py:140-142) · **Evidence:** rg по всему репо: единственный импортёр content_hash — его собственный тест (tests/unit/test_shared_content_hash.py:21). Ноль прод-потребителей. При этом два bootstrap-модуля реализуют sha256-хеширование контента инлайн каждый по-своему (файл-стрим vs строка[:16]) — консолидация, ради которой модуль существует, не проведена.
- **Scenario:** vulture/dead-code детектор не видит модуль мёртвым (тест держит alive); разработчик ищет канон хеширования, находит shared/content_hash, использует редко или пишет третий дубль.
- **Impact:** 146 LOC мёртвого кода в shared/ (слой с формальным критерием ≥2 потребителей) + 2 несогласованные реализации; риск третьей реализации.
- **Minimal fix:** либо удалить content_hash.py (+тест), либо консолидировать python_deps/reboot_policy на нём (расширить API: file-hash и string-hash варианты).
- **Churn:** S · **Phase:** Pre-launch

## ARCH-905: AppConfig — два поля на одном env-ключе PROJECTS_BASE с разными семантиками
- **Severity:** Low · **Confidence:** High
- **Files:** `core/internal/shared/app_config.py` · **Symbols:** `projects_root` (:112), `projects_base` (:119) · **Evidence:** оба поля читают PROJECTS_BASE: `projects_root` — локальный путь (default parents[4] от app_config.py = dev-машина), `projects_base` — remote-канон `/opt/projects` (DEFAULT_PROJECTS_BASE из deploy_paths.py:152). Один ключ env управляет двумя разными концепциями (машина оператора vs нода).
- **Scenario:** экспорт PROJECTS_BASE=/opt/projects для remote-операций молча меняет projects_root скаффолдера на несуществующий локальный путь (и наоборот); ошибка проявляется далеко от места установки переменной.
- **Impact:** скрытая связка полей внутри «единой точки конфигурации»; путаница при чтении @modulemap (два имени — один ключ).
- **Minimal fix:** разные ключи (PROJECTS_ROOT_LOCAL / PLATFORM_PROJECTS_BASE) либо одно поле + явный remote/local резолв в deploy_paths.
- **Churn:** S (app_config + 2 вызова в project_scaffolder/orchestrator) · **Phase:** Post-launch

## Проверено, не подтвердилось

1. Реестры с единственной реализацией — опровергнуто: static DETECTORS ×14 (registry.py), PHASE_DISPATCH ×14 фаз, HANDLERS ×18 проверок (checks/__init__.py), provider_registry ×4 провайдера (certs-providers.yaml), channels ×3 (scp/local/forced) — везде множественные реализации.
2. Слои конфигурации как таковые — опровергнуто в части «лишний слой»: цепочка platform-infra.yaml → generate_platform_env → platform-env.yaml → потребители — задокументированный Manifest Generation Contract (инвариант 11); найдена только дубликация дефолтов (ARCH-903).
3. stub_detection.py — опровергнуто: решает документированную дедупликацию U-28, 5 прод-потребителей (reconciler_projects, converge/projects, orchestrator, engine, context_deployer).
4. contracts.py / module_interface.py / verbs.py — опровергнуто: 10+ импортёров EXIT_*-констант; invoke() — 4 прод-потребителя (см. ARCH-901 — проблема в механике, не в существовании); verbs — 2 потребителя + gate.
5. manifest_driver.py, env_reader.py, topo_sort.py, catalog/*, validate_dora_dashboard.py — ложно-«нулёвые» потребители: реальное использование через check-suite.yaml/makefiles/.github/workflows подтверждено.
6. Двойная верификация verify/domain_verifier vs verify_sweep (2141 LOC суммарно) — частично: назначение разное (per-project домены в контексте деплоя vs node-wide HTTP+TLS sweep релиз-чеклиста), probe-примитив уже консолидирован в shared/http_probe (172 W5.4); консолидация верхнего уровня возможна, но не является overengineering-нарушением.

---
checked: ~140 files
