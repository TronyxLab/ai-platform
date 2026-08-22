# Direction 3 — Circular Dependencies

Агент: форензик направления «circular dependencies» · Метод: статический импорт-граф + верификация каждого ребра чтением кода · Дата: 2026-08-22

Итог направления: **3 подтверждённых module-level цикла** (все маскируются partial-package/submodule fallback CPython и порядком импортов), 0 циклов в shell (`lib/*.sh` — чистый DAG), 0 двунаправленных config-file петель. Сегодня import не ломается, но три package-root паттерна находятся в одном reorder от ImportError.

---

### ARCH-021: Пакет `core.internal.bootstrap.deploy` ↔ собственные оркестраторы (SCC из 3 узлов на продакшн bootstrap-пути)
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/deploy/__init__.py:19-20 → deploy_orchestrator.py:87 → __init__.py; __init__.py:20 ↔ docker_orchestrator.py:102
- Symbols: `ModuleDeployResult`, `orchestrate`, `pre_pull_images`; `context_overlay`, `spool_validator`, `sudoers_generator`, `healthcheck_runner`, `parallel_runner`
- Evidence: `deploy/__init__.py:19` `from ...deploy.deploy_orchestrator import ModuleDeployResult, orchestrate`; `:20` `from ...docker_orchestrator import pre_pull_images`; `deploy_orchestrator.py:87` импортирует пакетный корень `core.internal.bootstrap.deploy` (context_overlay, docker_orchestrator, orphan_reconciler, secrets_validator, spool_validator, sudoers_generator); `docker_orchestrator.py:102` аналогично (healthcheck_runner, hermes_workflow, observability, orphan_reconciler, parallel_runner) — все на уровне модуля.
- Failure/maintenance scenario: первый импорт любого оркестратора заставляет выполняться `__init__` пакета посреди загрузки модуля; разрешение работает только через fallback CPython ≥3.7 для частично инициализированного пакета. Любой будущий module-level доступ `deploy.<attr>` внутри оркестратора или перестановка символов в `__init__` → `ImportError: cannot import name ... from partially initialized module`. Script-mode запуск (`python3 deploy_orchestrator.py`) дополнительно двойной-грузит модуль как `__main__` + dotted name (расщепление isinstance/metadata dataclass'ов).
- Impact: деплой модулей φ8/φ12 (критичнейшая фаза ноды) висит на этом хрупком треугольнике; рефакторинг пакета высокорисковый для агентов.
- Minimal fix: убрать sibling-submodule импорты из реэкспорт-позиции `deploy/__init__.py` — оркестраторы импортируют конкретные submodule (`from core.internal.bootstrap.deploy.context_overlay import ...`); `__init__` оставить чистым реэкспортом последним.
- Code churn: S
- Phase: Pre-launch

### ARCH-022: Пакет `check_suite` ↔ `manifest`/`gate` — импорт символов из частично инициализированного `__init__`
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/check_suite/__init__.py:78,110,113,149 ↔ manifest.py:32, gate.py:37 (через diagnostic.py:34,36)
- Symbols: `PROJECT_ROOT`, `VALID_TIERS`, `VALID_GATE_MODES`; `run_diagnostic`, `run_gate`, `list_checks`
- Evidence: `__init__.py:78` импорт diagnostic (`DEFAULT_MAX_WORKERS, run_diagnostic`); `:110` gate (`run_gate`); `:113` manifest (`list_checks`, ...); `diagnostic.py:36` ← manifest; `manifest.py:32` `from core.internal.check_suite import PROJECT_ROOT, VALID_GATE_MODES, VALID_TIERS` — замыкает петлю `__init__ → diagnostic → manifest → __init__`; `gate.py:37` замыкает `__init__ → gate → __init__`.
- Failure/maintenance scenario: работает только потому, что константы объявлены в `__init__.py:57-62` ДО блока RE_EXPORTS (L78). Перенос реэкспорт-блока выше или определение `PROJECT_ROOT` после импортов мгновенно ломает каждый `make check`/`make gate` ImportError'ом частичной инициализации.
- Impact: единственная верификационная точка входа платформы зависит от неявного top-to-bottom порядка файла.
- Minimal fix: вынести константы в `check_suite/_constants.py`; подмодули импортируют оттуда; `__init__` реэкспортирует без изменений.
- Code churn: S
- Phase: Pre-launch

### ARCH-023: Пакет `deploy.engine` ↔ `lifecycle` через fallback flow-submodule
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/deploy/engine/__init__.py:17-18 → cli.py:25 → engine/engine.py:49 → lifecycle.py:29 → engine/__init__.py
- Symbols: `main`, `DeployEngine`, `_flow` (единый patchable-holder `shared_docker_compose_up`)
- Evidence: `engine/__init__.py:17` ← cli.main, `:18` ← engine.DeployEngine; `cli.py:25` ← engine.DeployEngine; `engine/engine.py:49` ← lifecycle (...); `lifecycle.py:29` `from core.internal.deploy.engine import flow as _flow` — пакет ещё инициализируется, спасает только submodule-fallback; `flow.py:19-32` импортирует исключительно `shared.*` (нет обратного ребра) — единственная причина молчания цикла.
- Failure/maintenance scenario: свежий интерпретатор с `import core.internal.deploy.engine.lifecycle` первым идёт по цепочке `__init__ → cli → engine → lifecycle → (partial package)`; если у `flow` появится обратная ссылка или `lifecycle` тронет другой атрибут `engine.__init__` на уровне модуля — deploy engine падает при импорте со спутанным partial-init traceback. TRAP[DECISION] на lifecycle.py:25-28: holder активно патчится тестами — изменение порядка молча сломает patch-target.
- Impact: транспортный слой деплоя (каналы/rollback) держится на случайной толерантности к порядку импортов.
- Minimal fix: в `engine/__init__.py` импортировать `flow` (leaf, shared-only) до `cli`/`engine`; либо перенести binding holder'а в `results.py`.
- Code churn: S
- Phase: Pre-launch

### ARCH-024: importlib file-path загрузки создают shadow-идентичности модулей (cycle-dodging инфраструктура с P1-прецедентом)
- Severity: MEDIUM
- Confidence: HIGH
- Files: core/internal/bootstrap/lifecycle/helpers/domains.py:72-83,117-126,166-178; bootstrap/lifecycle/secrets_manager.py:131-152; (безобидные реестры: static/registry.py:65, scaffold/__init__.py:44, shared/node_yaml/__init__.py:159-162)
- Symbols: `spec_from_file_location("context_deployer", ...)`, `spec_from_file_location("cert_orchestrator", ...)`, `importlib.import_module("exceptions")`
- Evidence: `domains.py:73` грузит `internal/bootstrap/deploy/context_deployer.py` под top-level именем `"context_deployer"` (повтор `:118`); `domains.py:167-170` грузит `cert_orchestrator.py` как `"cert_orchestrator"` — скрытые рёбра `lifecycle.helpers.domains → bootstrap.deploy.context_deployer / bootstrap.cert_orchestrator` невидимы статическому анализу. TRAP[BUG] context_deployer.py:995-996 документирует удаление такого же importlib-workaround для cert_orchestrator (DevPlan 118 A5, «тихий полом...») — миграция остановилась до `domains.py`. `secrets_manager.py:147` биндит shared exceptions под bare script-mode именем (TRAP[DECISION]:130).
- Failure/maintenance scenario: один code object может существовать дважды (dotted + shadow имя), расщепляя state и ломая isinstance/dataclass — ровно класс уже выпущенного P1 (RC 121, sys.modules registration TRAP domains.py:76-81). Cross-layer гейт (bootstrap→deploy разрешено, обратно запрещено) эти рёбра не видит вообще.
- Impact: dependency-аудиты и import-linters занижают реальную связанность на SSL-provision/deploy-context пути.
- Minimal fix: заменить `spec_from_file_location` на guarded обычные импорты (non-fatal политика сохранена), по прецеденту DevPlan 118 A5.
- Code churn: M
- Phase: Post-launch

### ARCH-025: Кластеры function-local импортов — deferred coupling, не скрытые циклы
- Severity: LOW
- Confidence: HIGH
- Files: 66 файлов / 161 локальных core-импорта; топ: bootstrap/lifecycle/secrets_manager.py (15), healthcheck/platform_export_metrics.py (12), scaffold/project_adopter.py (9), scaffold/gen_project_platform_md.py (8), scaffold/project_scaffolder.py (7), bootstrap/cert_orchestrator.py (5), shared/telegram_notifier.py (5)
- Symbols: cross-domain lazy рёбра — `converge/projects.py:297,369` → scaffold.gen_env_platform/gen_project_platform_md; `deploy/hooks/post_deploy_chain.py:160` → monitoring.config_renderer; `bootstrap/provision_env.py:185` → provisioner.main; `deploy/engine/lifecycle.py:136` → first_deploy.handle_first_deploy
- Evidence: каждая пара проверена на обратное module-level ребро — не найдено (provisioner импортирует только shared; first_deploy.py:22 только shared.exceptions; gen_env_platform/config_renderer только shared/template_engine) ⇒ это отсрочка init-order/стоимости, не обход циклов.
- Failure/maintenance scenario: лениво импортируемые ветки не исполняются при статических проверках; опечатка/дрейф пути всплывает только mid-bootstrap/mid-deploy на ноде. Концентрация кластера (secrets_manager 15) — сигнал двойной роли script-mode+library (см. ARCH-024).
- Impact: скрытая связанность делает blast-radius оценку scaffold/bootstrap рефакторингов ненадёжной; runtime-цикла сегодня нет.
- Minimal fix: поднять cross-domain импорты на уровень модуля где нет init-order ограничений; script-mode fallback оставить только где standalone-исполнение — задокументированный контракт.
- Code churn: M
- Phase: Post-launch

### ARCH-026: `node.yaml` — несколько writer-доменов за единым read-facade (замкнутой петли нет)
- Severity: LOW
- Confidence: MED
- Files: writers: scaffold/scaffold_helpers.py:544-649 (`register_in_node_yaml`, NodeYaml CLI mutation), scaffold/context_registry.py:35+, scaffold/project_remover.py:245+; read-facade: shared/node_yaml/* + shared/node_resolver.py; consumer-writer: bootstrap/converge/projects.py:38 (читает NodeYaml) → пишет project .env.platform/AI-PLATFORM.md через scaffold-генераторы (`:297`,`:369`)
- Symbols: `register_in_node_yaml`, `register_context`, `NodeYaml`
- Evidence: scaffold пишет `node-configs/<node>/node.yaml`; bootstrap/converge читает тот же артефакт и генерирует per-project GENERATED файлы генераторами домена scaffold; модуля, который потребляет чужой артефакт и кормит свой обратно тому же домену, не найдено (потоки однонаправленные: ai-platform.yaml → AI-PLATFORM.md; practices.lock → verify_contracts).
- Failure/maintenance scenario: три домена мутируют центральный state-файл ноды при read-only-biased типизированном фасаде; schema-дрейф одного writer (scaffold) молча ломает consumer другого домена (converge) без compile-time сигнала.
- Impact: только координационная стоимость; runtime-цикла нет.
- Minimal fix: все мутации через один write-API `NodeYaml` со schema-validation на выходе (+ гейт на inventory писателей).
- Code churn: M
- Phase: Post-launch
