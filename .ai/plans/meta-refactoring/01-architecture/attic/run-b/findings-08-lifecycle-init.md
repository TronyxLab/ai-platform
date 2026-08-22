# Init/lifecycle architecture audit

Метод: статический обход цепочек вызовов bootstrap-lifecycle (cli.py → state_machine → phases/ → helpers → deploy_orchestrator/context_deployer/receive_flow/post_deploy_chain/on_project_deploy) + rg-разрезы ленивых импортов (161 вхождение `core.*` внутри функций в core/internal) и import-time side effects. Каждая находка — file:line-цепочка с указанием отсутствующего guard'а; make/gate не запускались.

## ARCH-801: Node-identity guard мёртв на полностью выполненном state — чужая нода проходит как no-op success
- **Severity:** High · **Confidence:** High
- **Files:** core/internal/bootstrap/lifecycle/cli.py:455-458, cli.py:446 · core/internal/bootstrap/lifecycle/state_machine.py:852-862 · **Symbols:** `main`, `setup_state`
- **Evidence:** единственное место, сравнивающее `state.node` с новой нодой — внутри `setup_state` (state_machine.py:853 `if node and self.state.node and self.state.node != node: reset`). Но вызов `setup_state` из `main` защищён условием `cli.py:455`: `if sm.state.mode != args.mode or sm.state.current_step == 0`. После полного успешного init `current_step = 9` (cli.py:941 `_mark_phase_success`), mode совпадает → `setup_state` НЕ вызывается → сравнение недостижимо. TRAP[BUG] 2026-08-03 (фикс node-switch) закрыл только ветку внутри setup_state.
- **Scenario:** e2e-прогон оставил state.json (node=test-e2e, все фазы done, current_step=9); оператор запускает `make bootstrap-node NODE=tronyx-vps` → все 9 фаз «already done — skipping», liveness-probe зелёный, exit 0. Ни одна фаза не выполнилась для новой ноды; hash-инвалидация (T9.3) покрывает только 5 deploy/converge-фаз и только при другом node.yaml.
- **Impact:** ложный «успешный» bootstrap prod-ноды; обнаруживается лишь на первом деплое/e2e-verify (часы спустя).
- **Minimal fix:** сравнение `source.get("NODE_NAME")` с `sm.state.node` поднять в `main()` до guard'а setup_state (mismatch → WARN + reset или abort). **Churn:** ~10 LOC, 1 файл. **Phase:** Pre-launch

## ARCH-802: UPDATE-граф зависимостей дыряв — φ11 без deps, φ12 без φ10; `--run-phase` выводит порядок из-под контроля списка
- **Severity:** Medium · **Confidence:** High
- **Files:** core/internal/bootstrap/lifecycle/state_machine.py:241-245 (`_phase_dependency_graph`) · cli.py:366-374 (`_run_single_phase` — проверка только членства в `ALL_PHASES`, без mode) · lifecycle/phases/preconditions.py:301-312 (для φ9/φ10/φ11 handler'ов нет)
- **Evidence:** INIT-граф кодирует конфиг-гейт транзитивно (φ7←φ5, φ8←{φ4,φ6,φ7}); UPDATE-граф: `REGISTRY_UPDATE: set()` (φ11 выполняет provision+overlays+llm+healthcheck без единого prereq) и `DEPLOY_UPDATE: {SECRETS_UPDATE, REGISTRY_UPDATE}` — φ10 (node-config-update: verify-core/валидация node.yaml) не является предком φ12. Порядок держится только на списке `UPDATE_PHASE_ORDER` (state_machine.py:203-209).
- **Scenario:** recovery после сбоя: `--run-phase deploy_update` при незавершённых φ10 → деплой против непроверенного node.yaml; precondition φ12 (preconditions.py:249-266) проверяет только существование deploy-modules.sh и docker daemon — расшифровку секретов (φ9) и валидацию конфига не проверяет.
- **Impact:** инвариант «секреты до compose, конфиг до деплоя» в update-режиме — конвенция порядка списка, а не механизм, которым DAG заменён по @rationale DevPlan 087.
- **Minimal fix:** `REGISTRY_UPDATE ← {SECRETS_UPDATE, NODE_CONFIG_UPDATE}`, `DEPLOY_UPDATE ← {SECRETS_UPDATE, NODE_CONFIG_UPDATE, REGISTRY_UPDATE}`; в `_run_single_phase` фильтровать по `phase_list(sm.state.mode)`. **Churn:** ~8 LOC, 2 файла. **Phase:** Pre-launch

## ARCH-803: postgres-хук (БД/роль/креды) выполняется строго ПОСЛЕ старта проекта, non-fatal, env в контейнер не реинжектируется
- **Severity:** High · **Confidence:** High
- **Files:** core/internal/deploy/receive_flow.py:552-569 (`deploy()` → post-chain только при success) · core/internal/deploy/hooks/post_deploy_chain.py:251-293 (фиксированный порядок notify→catalog→monitoring→module-hooks) · core/modules/postgres/hooks/on_project_deploy.py:88-184 (CREATE DATABASE/ROLE), :274-276 (regen `.env.platform` ТОЛЬКО при первом создании роли), :12 (инвариант non-fatal) · post_deploy_chain.py:222-229 (сбой hook = WARN)
- **Evidence:** цепочка receive: unpack → validate → `deploy()` (compose up, receive_flow.py:549-551) → и только затем `_run_post_deploy_chain` (receive_flow.py:562) → `_module_deploy_hooks` → postgres-хук. Требование «роль/БД/GRANT создаются хук-ом postgres при деплое» (root AGENTS.md §Контракт окружения) нигде не закодировано как предусловие compose up.
- **Scenario:** первый деплой проекта с `needs.database`: контейнер стартует с `.env.platform` без `PLATFORM_POSTGRES_PASSWORD` → хук создаёт БД/роль и перегенерирует `.env.platform`, но контейнер НЕ пересоздаётся/не рестартуется → приложение живёт со старым env до следующего деплоя; healthcheck остаётся зелёным, если /health не ходит в БД. Сбой самого хука — WARN, статус DEPLOYED не меняется.
- **Impact:** «DB ready до app up» — инвариант, нарушаемый по построению; первый деплой DB-проекта деградирует незаметно.
- **Minimal fix:** pre-up вызов хука (needs.database читаем из payload ai-platform.yaml до compose up) либо post-hook с recreate при изменении env. **Churn:** средний (1 точка в ReceiveFlow.deploy). **Phase:** Pre-launch (UX первого деплоя); Post-launch допустимо как компенсация мониторингом

## ARCH-804: под-оркестраторы загружаются importlib-by-path — зависимость state_machine→deploy-слой скрыта от статики; 2 прод-инцидента уже на счету
- **Severity:** Medium · **Confidence:** High
- **Files:** core/internal/bootstrap/lifecycle/helpers/domains.py:71-103 (`import_deploy_context` — spec_from_file_location("context_deployer")), :167-181 (`ssl_provision_via_orchestrator` — то же для cert_orchestrator) · TRAP[BUG] 2026-08-03 P1 (sys.modules-регистрация, RC121 прод φ7/φ8) · контраст: state_machine.py:280-295 `PHASE_DISPATCH` — статические импорты (W5-C3)
- **Evidence:** φ8/φ12 вызывают context_deployer (deploy-слой!) через файловый путь `Path(core_dir)/"internal"/"bootstrap"/"deploy"/"context_deployer.py"`; каждый вызов `ssl_provision_via_orchestrator` re-exec'ит свежую копию cert_orchestrator (включая его import-time try-двери ARCH-806). Файл отсутствует → WARN + молчаливый skip всего context-deploy шага (non-fatal, domains.py:90-91,180-181).
- **Scenario:** частичная доставка core (rsync-обрыв) → context_deployer.py отсутствует → φ8 возвращает True (шаг 1 модулей успешен, шаг 2 «провалился» тихо) → проекты не задеплоены, фаза done.
- **Impact:** порядок «φ8 включает деплой проектов» деградирует без сигнала; связь слоёв невидима для importlinter/гейтов.
- **Minimal fix:** обычные package-импорты (bootstrap→deploy разрешён направлением по core/AGENTS.md; прецедент — context_deployer.py:38 A5). **Churn:** ~30 LOC, 1 файл. **Phase:** Post-launch

## ARCH-805: 161 ленивый импорт в core/internal — порядок «секреты → env-потребители» не прикреплён ни к одному import-ребру
- **Severity:** Low · **Confidence:** Medium
- **Files:** lifecycle/secrets_manager.py:112-126,285-291,609,641-648,859 (15 ленивых импортов) · lifecycle/helpers/secrets.py:114,128 · lifecycle/phases/docker.py:458,469,595 · lifecycle/helpers/reporting.py:96,194,284 · всего `rg '^\s{4,}(from|import) core\.' core/internal` = 161
- **Evidence:** φ4 резолвит SECRETS_ENV_FILE/AGE-цепочку лениво в момент вызова; env-aware фазы и htpasswd резолвят те же пути независимо и тоже лениво. Контракт hc-маркера «писатель φ8 (deploy_orchestrator.py:554,928) → читатель φ11 (docker.py:595)» держится только на общем хелпере `orchestrator_metrics.hc_marker_path`, импортируемом лениво с обеих сторон; маркер ставится всегда (deploy_orchestrator.py:928-941, DEPLOY_BEST_EFFORT), даже при упавшей группе — подавление φ11-healthcheck по конвенции.
- **Scenario:** рефакторинг порядка резолюции в secrets_manager ломает φ6/φ8 только на ноде (runtime); grep-дискавери — единственный способ найти потребителей.
- **Impact:** скрытые init-зависимости; цена ошибки — прод-прогон, не гейт.
- **Minimal fix:** поднять leaf-импорты (shared.deploy_paths, secrets_env_parser, orchestrator_metrics) на модульный уровень lifecycle-пакета; протокол маркера задокументировать рядом с hc_marker_path. **Churn:** ~40 строк, 5 файлов. **Phase:** Post-launch

## ARCH-806: import-time «двери» — try-import тихо меняет стратегию (S3-cache/provider-registry), HOME замораживается при импорте
- **Severity:** Low · **Confidence:** High
- **Files:** core/internal/bootstrap/cert_orchestrator.py:74-96 (`s3_ssl_cache=None` / `load_registry=None` при ImportError → фолбэк-префиксы `("WEBNAMES","S3_","PLATFORM_")` вместо strict allowlist-реестра) · core/internal/scaffold/context_initializer.py:49 (`_DEFAULT_PROJECTS_DIR = Path(os.environ.get("HOME","/"), "projects")` на верхнем уровне)
- **Evidence:** выбор стратегии credential-resolution происходит в момент импорта: реестр есть → longest-suffix strict (154 W1); нет → префиксный фильтр. Из-за ARCH-804 модуль re-exec'ится на каждый φ7/φ12 → состояние «двери» может различаться между прогонами одного дерева при частичном rsync. S3-cache-off → restore-first деградирует до acme-only (только IMP:7 warning, гейтов нет).
- **Scenario:** устаревший core на VPS без provider_registry → issue идёт по фолбэку с другой постурой кредов; оператор видит одну IMP:7-строку в 1172-LOC фазе.
- **Impact:** поведение определяется файловой композицией /opt/platform/core, а не конфигом; тихая деградация DR-канала сертификатов.
- **Minimal fix:** для provider_registry — loud ConfigValidationError (реестр едет в core с 154 W1, fallback-ветка мертва на актуальной ноде); пути — через функции, не константы импорта. **Churn:** ~15 LOC, 2 файла. **Phase:** Post-launch

---
Проверено: ~24 файла (cli.py, state_machine.py, state_store.py, preconditions.py, phases/{docker,certs}.py, helpers/{domains,secrets,reporting}.py, cert_orchestrator.py, context_deployer.py, deploy_orchestrator.py, orchestrator_metrics.py, post_deploy_chain.py, receive_flow.py, orchestrator_cli.py, ssh_command_parser.py, verbs.py, topo_sort.py, on_project_deploy.py, s3_ssl_cache.py, provider_registry.py, context_initializer.py + rg-срезы core/internal).
