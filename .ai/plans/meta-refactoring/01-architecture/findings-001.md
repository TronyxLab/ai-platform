# Направление 1 — Module/package boundaries

Метод: два независимых прохода (rg-классификация импорт-рёбер core/internal/* и core/modules/*; разбор `.importlinter`, slim-линтера и гейтов покрытия) + компактные находки основного прохода. Консолидация: 2026-08-22. Commit: 4425ce0.

## ARCH-0101 — healthcheck импортирует приватный модуль bootstrap-домена
- Severity: HIGH · Confidence: HIGH · Churn: S (<50) · WHEN: pre-launch
- Files: core/internal/healthcheck/tor_proxy_check.py:37 → core/internal/bootstrap/firewall.py
- Symbols: PRIVOXY_PORT
- Evidence: `from core.internal.bootstrap.firewall import PRIVOXY_PORT` — домен healthcheck указывает внутрь файла соседнего домена; у bootstrap нет re-export в `__init__.py`
- Scenario: любой рефакторинг firewall (переименование порта, сплит модуля) молча ломает healthcheck-цепочку Telegram→Tor→Privoxy; гейт это не ловит (см. ARCH-0105)
- Impact: production liveness-проверка прокси-канала ломается при деплое
- Minimal fix: перенести PRIVOXY_PORT в SoT core/internal/shared/platform_ports.py, импортировать из него в обоих местах

## ARCH-0102 — scaffold обходит публичную поверхность practices (deep submodule imports)
- Severity: MEDIUM · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/scaffold/project_scaffolder.py:380; scaffold_helpers.py:256; gen_project_platform_md.py:316-319
- Symbols: sync_practices, load_manifest, evaluate, read_lock, compute_maturity
- Evidence: `from core.internal.practices.sync_practices import sync_practices` и т.п.; `practices/__init__.py` — маркер 23 строки, ноль re-export → «публичный контракт» фиктивен. Подтверждено вторым проходом: 6 импортов scaffold→practices — тяжелейшая cross-domain связка в матрице (см. также ARCH-0206)
- Impact: переименование любого внутреннего модуля practices = 5+ точек правки без CI-сигнала
- Minimal fix: реэкспортировать 5 символов через practices/__init__.py; обновить 3 места импорта

## ARCH-0103 — postgres-hook: sys.path hack как единственное modules→internal ребро
- Severity: LOW · Confidence: HIGH · Churn: S · WHEN: post-launch (TRAP[DECISION] уже стоит)
- Files: core/modules/postgres/hooks/on_project_deploy.py:55-62
- Symbols: _PROJECT_ROOT, sys.path.insert, NodeYaml
- Evidence: `_PROJECT_ROOT = str(Path(__file__).resolve().parents[4])` + `sys.path.insert(0, ...)` + import NodeYaml — allowlist-исключение, работающее только пока layout репо не меняется
- Impact: смена упаковки/pip-install ломает хук тихо; деградация в non-fatal лог
- Minimal fix: тонкий dispatcher/CLI вместо sys.path hack (вариант уже предпочтён TRAP)

## ARCH-0104 — verify_sweep глубоко импортирует shared-субмодуль мимо поверхности пакета
- Severity: LOW · Confidence: HIGH · Churn: S · WHEN: pre-launch
- Files: core/internal/verify_sweep/collection.py:39
- Symbols: ProjectEntry
- Evidence: `from core.internal.shared.node_yaml.projects import ProjectEntry` — глубина 2, тогда как соседние строки 37-38 берут из `shared.node_yaml`
- Impact: мелкая friction; непоследовательность стиля импортов
- Minimal fix: импорт через пакетную поверхность node_yaml

## ARCH-0105 — Enforcement gap: гейт приватных импортов ловит только `_`-префиксы
- Severity: MEDIUM · Confidence: HIGH · Churn: M (<300: детектор + allowlist + тесты) · WHEN: pre-launch
- Files: core/internal/static/private_imports.py:15-21,71-76
- Symbols: detector invariants (a)/(b), пустой allowlist
- Evidence: правила покрывают `from X import _name` и `X._attr`; module_map ключуется только алиасом — находки ARCH-0101/0102/0104 проходят гейт by construction
- Impact: эрозия границ доменов идёт бесшумно; рефакторинги пересекают домены без сигнала CI
- Minimal fix: добавить в детектор карту владения доменами (core/internal/<domain>/ → owner) и флаг sibling-domain импортов, не реэкспортированных `__init__.py`

## ARCH-0106 — internal→modules dotted-импорты не запрещены ни одним контрактом — живое нарушение dev_hosts.py
- Severity: HIGH · Confidence: HIGH · Churn: S-M · Phase: Pre-launch
- Files: core/internal/dev_hosts.py:69-71; .importlinter:47-60; tests/helpers/cross_layer_linter.py:184-190; core/AGENTS.md (cross-layer table)
- Symbols: `dev_hosts.main` → `core.modules.nginx.dev_cert_generator.get_cert_sans`, `DEFAULT_DEV_CERTS_DIR`
- Evidence: `core/internal/dev_hosts.py:71`: `from core.modules.nginx.dev_cert_generator import DEFAULT_DEV_CERTS_DIR, get_cert_sans`. Контракт layers-core объявляет «каждый слой может импортировать себя и ниже» (.importlinter:42-43) — internal→modules разрешён; forbidden-контракт есть только для entrypoints→modules (2.1). Slim-линтер ловит internal→modules только в `.sh` (`lint_core()` итерирует `CORE_DIR.rglob("*.sh")`, cross_layer_linter.py:184; правило `[internal→modules·direct]`:190) — Python-файлы вне scope. Канон core/AGENTS.md: internal вызывает modules ТОЛЬКО через `invoke_module_interface` + регистрацию интерфейса. (Направленный анализ — также ARCH-0202.)
- Scenario: это единственный живой dotted-импорт internal→modules (rg по `core/internal/` даёт ровно одно вхождение), но гейт молчит — паттерн тиражируется: следующий агент «легально» импортирует postgres-SQL/nginx-шаблоны из модулей; рефакторинг module.yaml/внутренностей модуля ломает platform-код без единого RED.
- Impact: связывание ядра платформы с внутренностями сервисных модулей мимо контрактного канала invoke_module_interface; слепая зона gate #8.
- Minimal fix: добавить контракт `forbidden-internal-modules` (source=core.internal, forbidden=core.modules) c точечным ignore для dev_hosts→nginx.dev_cert_generator; параллельно вынести `get_cert_sans`/`DEFAULT_DEV_CERTS_DIR` в shared/ssl_certs (2 потребителя: nginx-модуль + dev_hosts).
- Code churn: 3 файла × ~15 LOC

## ARCH-0107 — forbidden-shared-domains покрывает 3 домена из 23; «shared is a leaf» уже пробит ребром shared→config
- Severity: MEDIUM · Confidence: HIGH · Churn: S · Phase: Pre-launch
- Files: .importlinter:106-114; core/internal/shared/s3_client.py:34; core/internal/config/platform_config.py:33-40
- Symbols: `shared.s3_client.get_s3_client` → `config.platform_config`
- Evidence: контракт 2.4 перечисляет forbidden_modules = {bootstrap, deploy, static} — остальные прямые дети core.internal (23 импортируемых пакета) вне контракта. Живое ребро: `s3_client.py:34`: `from core.internal.config import platform_config`. Цикла нет (`platform_config.py` импортирует только stdlib+yaml), поэтому acyclic-internal-domains тоже молчит. (Направленный разбор этого же ребра — ARCH-0201.)
- Scenario: config — кандидат на потребление shared (yaml_loader/schema_validator): первый же обратный импорт даст цикл и неожиданный RED acyclic-контракта; до тех пор shared может тихо обрастать зависимостями от доменных пакетов, разрушая гарантию «его импортируют ВСЕ, он никого» и переиспользуемость shared контейнерными модулями (прецедент postgres-hook).
- Impact: эрозия leaf-инварианта shared/AGENTS.md (инвариант 5) при зелёных гейтах.
- Minimal fix: расширить forbidden_modules контракта 2.4 полным списком детей core.internal (или инвертировать: source=core.internal.shared, forbidden=core.internal минус shared) + ignore для s3_client→config либо миграция platform_config в shared/.
- Code churn: 1 файл × ~25 LOC

## ARCH-0108 — reconciler_projects.py вынесен на корень core/internal/, чтобы обойти independence bootstrap↔deploy
- Severity: MEDIUM · Confidence: HIGH · Churn: S · Phase: Post-launch
- Files: core/internal/reconciler_projects.py:40-41; .importlinter:118-121; core/internal/bootstrap/converge.py:306-316
- Symbols: `reconciler_projects` → `deploy.channels.ForcedCommandChannel`, `deploy.orchestrator.DeployOrchestrator`
- Evidence: `.importlinter:120-121` прямо документирует обход: «reconciler_projects.py живёт в core/internal/ (не в bootstrap/) — его импорты в deploy.* не затрагивает этот контракт». При этом файл импортирует оба внутренних механизма deploy (channels + orchestrator) — те же классы, для которых context_deployer потребовал явных ignore-записей. Диспетч — через subprocess со склейкой пути `str(core_dir / "internal" / "reconciler_projects.py")` (converge.py:316).
- Scenario: корень core/internal/ превращается в «серую зону» вне всех контрактов: любой cross-domain клей, размещённый там, невидим для independence/forbidden-гейтов (прецедент уже создан и задокументирован как легальный). Перенос файла в bootstrap/ при естественном рефакторинге конвергенции внезапно уронит контракт.
- Impact: третье (неконтролируемое) направление bootstrap→deploy помимо двух typed contract points G3.
- Minimal fix: зарегистрировать два ребра reconciler_projects как ignore_imports в independence-bootstrap-deploy (с обоснованием) — они станут first-class и начнут охраняться unmatched_ignore_imports_alerting=error.
- Code churn: 1 файл × ~8 LOC

## ARCH-0109 — template_engine.py — de-facto shared-библиотека вне реестра shared/ (+ compat-shim re-export в provisioner.py)
- Severity: MEDIUM · Confidence: HIGH · Churn: M · Phase: Post-launch
- Files: core/internal/template_engine.py:26-27,852 LOC; core/internal/scaffold/project_scaffolder.py:54; core/internal/monitoring/config_renderer.py:54; core/internal/bootstrap/deploy/sudoers_generator.py:74; core/internal/provisioner.py:44-52
- Symbols: `render_directory_in_place`, `render_template`, `check_all`, `TemplateError`; re-export `PlatformEnv/VolumeConfig/load_platform_env`
- Evidence: template_engine имеют ≥3 междоменных потребителя (scaffold, monitoring, bootstrap.deploy) + makefiles/helpers.mk:31,37 + гейт test_gate_template_drift — удовлетворяет критерию shared/AGENTS.md («≥2 потребителей»), но отсутствует в инвентаре shared/ и не покрыт leaf-контрактом: acyclic-internal-domains видит его рядовым sibling-доменом. В provisioner.py:44-52 — re-export «для обратной совместимости» типов из shared/yaml_loader при инварианте 9 («обратная совместимость не требуется») и гейте no_backward_compat_shims, который ищет только лексические маркеры (`backward_compat|compat_shim|...`) и пропускает русский комментарий «Re-export для обратной совместимости».
- Scenario: развитие template engine (новые флаги рендера) идёт мимо правил shared (MODULE_CONTRACT-инвентарь, тест-конвенция test_shared_*); новые потребители копируют lazy-import-паттерн monitoring/config_renderer вместо явной зависимости. Re-export провижинит два равноправных импорт-пути одного API (provisioner vs yaml_loader) — дрейф аннотаций типов.
- Impact: расхождение между декларированным инвентарём shared/ и фактическим набором кросс-доменных библиотек; двойной API-путь yaml-типов.
- Minimal fix: перенести template_engine.py в shared/ (реестр + 1 строка таблицы) с обновлением 5 импорт-мест; в provisioner.py оставить один канонический путь (yaml_loader), убрав re-export после переноса тестов.
- Code churn: 7 файлов × ~10 LOC

## ARCH-0110 — bootstrap/deploy недокументированно импортирует llm.config_renderer
- Severity: LOW · Confidence: 90% · Churn: S · WHEN: pre-launch
- Files: core/internal/bootstrap/deploy/deploy_orchestrator.py (import llm.config_renderer)
- Symbols: deploy_orchestrator → llm.config_renderer
- Evidence: живое ребро bootstrap→llm из импорт-матрицы основного прохода; не покрыто forbidden-контрактами (см. ARCH-0107/0206), в документированных G3-рёбрах bootstrap↔deploy не значится
- Scenario: рефакторинг llm-рендерера молча ломает деплой-оркестратор; направление зависимости не декларировано ни в одном контракте
- Impact: скрытая связка деплоя с LLM-подсистемой
- Minimal fix: вызывать через публичный фасад provision-llm/DI-шов (паттерн run_cmd/reconfig_fn уже используется в post_deploy_chain)

## ARCH-0111 — post_deploy_chain лениво импортирует monitoring.config_renderer вне гейт-надзора
- Severity: LOW · Confidence: 90% · Churn: S · WHEN: pre-launch
- Files: core/internal/deploy/hooks/post_deploy_chain.py:160
- Symbols: run_monitoring_reconfig
- Evidence: lazy `from core.internal.monitoring.config_renderer import run_monitoring_reconfig` — best-effort ребро deploy→monitoring (строка матрицы ARCH-0206)
- Scenario: переименование monitoring-рендерера ломает пост-деплой цепочку в best-effort ветке — отказ глотается тихо
- Impact: тихая деградация мониторинга после деплоя
- Minimal fix: обязательный DI-шов reconfig_fn в сигнатуре цепочки (паттерн уже существует)

## Checked clean (нарушений не доказано)
- Inline python/heredoc в core/entrypoints/*.sh: не найдено (19 скриптов; упоминания только в docstring-комплаенсе)
- Слой core/modules чист: только same-module relative imports (status-page) + allowlisted postgres hook (ARCH-0103); backup-cron scripts: 0 импортов core.internal
- `core/entrypoints/*.sh`: прямые `python3 -m core.internal.*` — канон (§Shell-исключения root AGENTS.md); все вызванные модули существуют
- Продакшн → tests.*: runtime-импортов нет (единственный hit — строковый шаблон генератора tests/_conftest)
- static → bootstrap: ложный край grep-матрицы (строковый паттерн в inline_secrets.py:82), AST не подтверждает
