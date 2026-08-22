# Direction 1 — Module / Package Boundaries

Агент: форензик направления «module/package boundaries» · Метод: AST-census всех cross-package рёбер core/internal/* + fan-in таблицы · Дата: 2026-08-22

Итог направления: 6 находок (0 CRITICAL, 1 HIGH, 5 MEDIUM; confidence 6×HIGH). Census: **16 cross-domain рёбер / 33 sites / 0 циклов** (DAG верифицирован); одна связная кластеризация {bootstrap→deploy,scaffold,config,llm,scripts,provisioner; deploy→practices,monitoring; scaffold→practices}; fan-in лидеры shared: exceptions 76 / timeouts 75 (48 bootstrap-файлов). Module payloads чисты за пределами allowlist'енного postgres hook. Вердикт: целостность границ принципиально здорова — ациклично, контрактно охраняется, низкая попарная связанность; реальные угрозы — концентрация (bootstrap = 34% слоя), сломанное, но не enforceимое правило «shared is a leaf», три конкурирующие package-surface конвенции — не spaghetti.

Примечание: git co-edit forensics недоступны (история сведена к single snapshot b1d6e2b) — структурный fan-in использован как прокси.

---

### ARCH-001: Domain edge census — низкая попарная связанность, но ОДИН связный кластер вокруг bootstrap↔deploy↔scaffold↔practices
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/** (census по AST всех 26 top-level units; prod-only .py)
- Symbols: DeployOrchestrator, LocalChannel, platform_config, practices.{read_lock,load_manifest,evaluate,compute_maturity}, render_template, run_monitoring_reconfig
- Evidence (все cross-package рёбра X≠Y≠shared, AST-exact):
```
from                to                files sites key symbols
scaffold            practices          3    6    load_manifest x2, evaluate, read_lock, compute_maturity, sync_practices
bootstrap           config             4    4    platform_config x4 (cert_orchestrator, preflight, s3_ssl_cache, deploy/context_deployer)
monitoring          template_engine    1    4    TemplateError x2, render_template x2
bootstrap           deploy             1    3    LocalChannel, DeployOrchestrator, OrchestratorDeployResult [contract-allowlisted]
deploy              practices          1    3    PracticesLock, read_lock, load_manifest (verify_contracts)
bootstrap           scaffold           1    2    generate_env_platform, write_project_platform_md (converge/projects)
bootstrap           template_engine    1    2    TemplateError, render_template (sudoers_generator)
reconciler_projects deploy             1    2    ForcedCommandChannel, DeployOrchestrator [см. ARCH-012]
bootstrap           llm                1    1    config_renderer
bootstrap           scripts            1    1    discover_docker_modules
bootstrap           provisioner        1    1    main (!)
deploy              monitoring         1    1    run_monitoring_reconfig (hooks/post_deploy_chain)
dev_hosts           scaffold           1    1    read_node_yaml_projects [см. ARCH-010]
healthcheck         bootstrap          1    1    PRIVOXY_PORT [см. ARCH-011]
scaffold            template_engine    1    1    render_directory_in_place
shared              config             1    1    platform_config ← нарушение, см. ARCH-002
TOTAL: 16 рёбер / 21 файл / 33 sites; ZERO циклов.
Изолированные leaf-домены (только shared): loadtest, lint, validate, secrets, catalog, build, static, agent_check, check_suite, verify_sweep, notify/hooks/ai-instructions.
```
Кластер — {bootstrap, deploy, scaffold, practices, config} плюс сателлиты {monitoring, template_engine, llm, scripts, provisioner}: bootstrap порождает 7 из 16 рёбер; practices.lock API потребляется из трёх доменов (generation через scaffold, verify через deploy) — общий contract point.
- Failure/maintenance scenario: изменение schema practices lock требует ко-правки в 3 доменах (practices/generators, scaffold/gen_project_platform_md.py:316-319, deploy/verify_contracts.py); изменение defaults platform-infra.yaml затрагивает 4 bootstrap entry points + shared/s3_client одновременно.
- Impact: умеренный; blast radius сосредоточен в предсказуемых, типизированных местах; скрытой связанности вне таблицы нет.
- Minimal fix: структурно ничего; задокументировать кластер в навигации core/AGENTS.md, держать два multi-site ребра (scaffold→practices, bootstrap→config) за явными facade модулями при росте.
- Code churn: S
- Phase: Post-launch

### ARCH-002: Инвариант «shared is a leaf» уже нарушен, linter-контракт покрывает только 3 из ~24 доменов
- Severity: HIGH
- Confidence: HIGH
- Files: core/internal/shared/s3_client.py:34; .importlinter:106-114; core/internal/config/platform_config.py
- Symbols: `get_s3_client` → `platform_config.default_s3_region()`
- Evidence: .importlinter:107 декларирует «shared is a leaf (never imports internal domains)» со ссылкой на инвариант 5 shared/AGENTS.md, но forbidden_modules перечисляет только core.internal.bootstrap/deploy/static. shared/s3_client.py:34 уже делает `from core.internal.config import platform_config` — up-import в домен, НЕ входящий в forbidden список: гейт зелёный при ложном инварианте. Обратное направление (config→shared) рёбер не имеет — цикла сегодня нет.
- Failure/maintenance scenario: следующий контрибьютор читает заголовок контракта («shared — чистый leaf»), видит зелёные гейты и импортирует, например, scaffold.scaffold_helpers или practices.manifest в shared; ничто не блокирует. Up-dependencies накапливаются, пока кто-то не импортирует обратно вниз и не упрётся в acyclic gate — после чего путь наименьшего сопротивления — ignore_imports запись, нормализующая ровно ту layer inversion, против которой задуман unmatched_ignore_imports_alerting=error.
- Impact: тихая эрозия единственного глобально enforceимого layering правила; shared (14,055 LOC, 54 модуля, импортируется всеми 24 Python-доменами) становится магнитом для domain helpers.
- Minimal fix: заменить 3-entry enumeration на все sibling-домены (или инвертировать: source=shared → forbidden=`core.internal` минус `core.internal.shared`), затем закрыть единственное живое ребро, внедрив S3 region/default значения параметрами `get_s3_client(...)` (его docstring подтверждает, что callers уже передают endpoint/keys).
- Code churn: S (правка контракта + threading параметра в одном call-site)
- Phase: Pre-launch
- См. также: ARCH-013 (латентный аспект того же контракта, направление dependency direction).

### ARCH-003: bootstrap — мега-домен: 35.3k LOC = 34% внутреннего слоя, 93 файла, исходящие рёбра в 7 доменов
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/** (34 root-модуля incl. cert_orchestrator 1172 LOC, issue_cert 1027, install_tor_proxy 806, firewall.py; subpackages converge/, lifecycle/, security/, deploy/)
- Symbols: provision_env:185 → `provisioner.main`; lifecycle.docker → deploy.orchestrator_metrics; converge.sudoers → deploy.sudoers_generator
- Evidence: LOC census — bootstrap 35,284 от 103,103 внутреннего слоя (далее: shared 14,055, deploy 8,785). Внутренняя анатомия НЕ spaghetti: subpackage edges редкие и нисходящие (deploy→root 3 файла, lifecycle→root 3, security→root 3, converge→deploy 1, root→security 1, root→lifecycle 1; восходящих нет, циклов нет). Проблемы: (a) масштаб/широта забот — provisioning, certs, TOR, docker install, firewall, lifecycle FSM, converge И deploy orchestration в одном «домене»; (b) name shadowing: core.internal.bootstrap.deploy.* (20 файлов) vs core.internal.deploy.* — два пакета с именем deploy на разной глубине, различимые только префиксом; (c) bootstrap/provision_env.py:185 lazy-импортирует core.internal.provisioner.main — вызов CLI-entry функции чужого домена вместо API-символа.
- Failure/maintenance scenario: изменение timeout/path константы (см. ARCH-004 fan-in) плюс изменение deploy-channel приземляется как 15-файловый diff внутри одной reviewable единицы, которую CI не может скоупить ниже «bootstrap»; новый сотрудник/агент, правящий «the deploy package», открывает не тот (shadowed name) — сами AGENTS docs требуют disambiguation обоих путей.
- Impact: крупнейший единый review/test blast radius платформы; boundary fiction между lifecycle-фазами bootstrap и встроенной deploy orchestration.
- Minimal fix: переименовать вложенный bootstrap/deploy/ → bootstrap/phases_deploy/ (убить shadowing); route provision_env через типизированную provisioner-функцию вместо main; физический split отложить до следующей фичи (Strangler-Fig).
- Code churn: M (rename механический; обновление путей import-linter)
- Phase: Pre-launch

### ARCH-004: shared/ god-layer безопасен по широте, сконцентрирован по глубине: exceptions ×76 и timeouts ×75 importers; timeouts — 48-файловая поверхность связанности bootstrap
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/shared/{exceptions,timeouts,deploy_paths,node_yaml/*,subprocess_io,env_facts,atomic_writer,docker_compose}.py
- Symbols: PlatformFatalError et al.; DEPLOY_TIMEOUT, CONVERGE_DOCKER_TIMEOUT, HEALTHCHECK_POLL_* (+>20 других)
- Evidence (fan-in: модули / различных consumer-доменов):
```
shared.exceptions      76 / 18     shared.node_yaml       30 / 7
shared.timeouts        75 / 14     shared.subprocess_io   27 / 7
shared (__init__ pkg)  47 / 11     shared.env_facts       26 / 5
shared.deploy_paths    44 / 10*    shared.atomic_writer   19 / 8
(*в направлении hotspots считался файловый fan-in 87 — методика отличается)
shared.docker_compose  15 / 3      shared.audit_logger    13 / 5
```
Все 24 Python-домена импортируют shared. De-facto domain hub внутри shared не прячется (docker_ops/notifications/node_yaml имеют умеренный fan-in; node_yaml — крупнейший sub-package, 9 файлов). Реальный риск — timeouts: 48 bootstrap-файлов импортируют его, охватывая >20 различных констант — фактически вторая, бесхозная configuration plane для настройки bootstrap/deploy.
- Failure/maintenance scenario: rename/repurpose одной timeout-константы (например, split DEPLOY_TIMEOUT per channel) разворачивается на десятки bootstrap-правок одним коммитом — ровно то co-edit давление, которое ищет porous-boundary тест.
- Impact: усилитель churn высокого уровня, архитектурный риск низкий (константы — leaf data).
- Minimal fix: сгруппировать timeouts по consumer-подсистемам (deploy/lifecycle/security namespaces) с re-export shim'ами на переход; иначе оставить как есть и запретить вход новых non-timeout символов в модуль.
- Code churn: M
- Phase: Post-launch

### ARCH-005: Три несовместимые package-surface конвенции; глубокие импорты и есть реальный API (695 deep vs 54 surface imports); agent_check держит 1092 LOC бизнес-логики в `__init__.py`
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/agent_check/__init__.py (1092 LOC; sibling set = {__main__.py, fp_registry.yaml}); core/internal/check_suite/__init__.py (392 LOC pure re-export facade incl. 18 дублирующих aliased imports); core/internal/verify_sweep/__init__.py (240 LOC: реэкспорты вперемешку с CLI `main()` :146); 13 доменов вообще без `__init__.py` (bootstrap, build, catalog, dev_hosts, healthcheck, provisioner, reconciler_projects, scripts, secrets, template_engine, test_runner, validate, verify — PEP 420 implicit packages)
- Symbols: <>
- Evidence: census стиля потребителей — `from core.internal.shared.X.Y import sym` (depth≥2): 695 symbol imports vs 54 через package root; каждое cross-domain ребро таблицы ARCH-001 — deep (`bootstrap.cert_orchestrator:44 <- core.internal.config.{platform_config}` и т.д.). Только check_suite поддерживает настоящий фасад.
- Failure/maintenance scenario: рефакторинг submodule check_suite требует обновления 392-LOC фасада (единая choke point — хорошо), но тот же ход в любом из 13 init-less доменов означает касание каждого consumer-site напрямую (indirection отсутствует); размещение agent_check означает, что любой будущий split его монолитного __init__ ломает python -m core.internal.agent_check dispatch и всех импортеров разом. Агент, грепающий «package API», получает три разных ответа в зависимости от домена.
- Impact: асимметрия стоимости рефакторинга и tooling/agent confusion; не runtime breakage.
- Minimal fix: выбрать одну конвенцию (рекомендация: минимальный __init__.py везде, декларирующий контракт пакета по house doc-header стилю, zero реэкспортов кроме check_suite); тело agent_check перенести в checker.py, оставив __init__ loader'ом.
- Code churn: M
- Phase: Pre-launch
- Связь: ARCH-030 (god-модуль agent_check).

### ARCH-006: Пять orphan-модулей в корне core/internal (~4.4k LOC) обходят domain-directory конвенцию; template_engine — незарегистрированный 3-доменный hub; test_journal вынужден дублировать junit parsing ради обхода цикла
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/{test_runner(909), template_engine(852), dev_hosts(643), reconciler_projects(562), provisioner(407)}.py; core/internal/shared/test_journal.py:22,159-160
- Symbols: render_template/TemplateError (consumers: monitoring.config_renderer:54,64, bootstrap.deploy.sudoers_generator:74, scaffold.project_scaffolder:54); parse_junit_xml
- Evidence: заявленная структура — core/internal/<domain>/; эти пять сидят в корне пакета. Новые факты (не ARCH-010/012): template_engine — упомянут в навигации AGENTS.md, но структурно бездомен — потребляется 3 доменами с zero регистрации во inbound/outbound shared-layer; test_runner импортируется check_suite/single.py, тогда как shared/test_journal.py документирует (:22 «импорт test_runner создал бы цикл shared↔test_runner»; :159-160 «дублирование осознанное»), что ему пришлось копировать parse_junit_xml логику из-за misplacement, создающего потенциальный цикл; provisioner.main достигается cross-domain из bootstrap.provision_env:185. Известные dodges (dev_hosts, reconciler_projects) ложатся в тот же паттерн — теперь 5 инстансов, не 2.
- Failure/maintenance scenario: баг-фикс в test_runner.parse_junit_xml молча не чинит дублированный парсер в shared/test_journal (уже дивергентны by design); изменение сигнатуры template_engine триггерит правки в 3 доменах, владельцы которых не отслеживают «домен», у которого нет ни каталога, ни фасада, ни владельца.
- Impact: дубликация + бесхозная связанность, сконцентрированные в 5 файлах; дёшево починить сейчас, дорого после появления новых root-модулей.
- Minimal fix: дать каждому каталог-домой (template_engine/ остаётся, но получает __init__.py; test_runner → check_suite/; provisioner → bootstrap/ или свой dir; dev_hosts/reconciler — согласно уже зафиксированным disposition), удалив задокументированную дубликацию после остановки root-импортов test_runner.
- Code churn: M
- Phase: Pre-launch
