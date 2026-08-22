# Направление 2 — Dependency direction

Метод: два независимых прохода — rg-классификация рёбер слой→слой + shell-sourcing; AST-матрица импортов всех 329 `.py`-файлов `core/internal` (30 в `core/modules`) со сверкой контрактов `.importlinter` (import-linter wired: `core/check-suite.yaml:59`). Консолидация: 2026-08-22. Commit: 4425ce0.

**Мини-матрица нарушенных направлений (пакет → пакет: N импортов, AST-точно):**

```
shared → config:        1   (лист нарушен; контрактом НЕ покрыт)         → ARCH-0201
healthcheck → bootstrap: 1  (cross-domain; покрыт только acyclic)        → ARCH-0206 / ARCH-0101
deploy → practices:     2   (cross-domain; покрыт только acyclic)        → ARCH-0206
deploy → monitoring:    1   (lazy; покрыт только acyclic)                → ARCH-0111
scaffold → practices:   6   (cross-domain; покрыт только acyclic)        → ARCH-0102 / ARCH-0206
core.internal → core.modules: 1 (layers-контракт разрешает «вниз» — gap) → ARCH-0202
python → *.sh-фасады:   4 сайта (1 — мёртвый путь)                       → ARCH-0204 / ARCH-0205
```

## ARCH-0201 — leaf-слой shared импортирует домен config (лист-инвариант нарушен, направление не покрыто ни одним контрактом)
- Severity: HIGH · Confidence: HIGH (направление самого config не проверено — если config импортирует shared выше по цепочке, апгрейд до CRITICAL; второй проход оценивал MEDIUM, т.к. цикла сейчас нет) · Churn: M · WHEN: pre-launch
- Files: core/internal/shared/s3_client.py:34 → core/internal/config/platform_config.py
- Symbols: platform_config accessor, get_s3_client
- Evidence: `from core.internal.config import platform_config` внутри пакета shared — единственное нарушение среди 50+ импортов shared. Контракт `forbidden-shared-domains` (.importlinter:106-114) запрещает shared→{bootstrap, deploy, static} — `config`, `practices`, `llm`, `scripts`, `healthcheck`, `scaffold` и остальные ~20 сиблингов вне списка. `platform_config.py` сам — leaf (imports: stdlib+yaml), цикла нет, поэтому acyclic-контракт молчит.
- Scenario: рефакторинг config рябью идёт в листовый слой; `platform_config` начнёт импортировать доменный модуль (например, node_yaml-резолвер) → shared (импортируемый всеми ~277 рёбрами) получит транзитивную зависимость от домена; граф перестаёт быть ацикличным на границе листа. Гейт не сработает ни на одно новое ребро shared→{config|practices|…}.
- Impact: инверсия на фундаментальном слое — база для будущих циклов; эрозия инварианта 5 shared/AGENTS.md («слой зависимостей — только вниз»)
- Minimal fix: вынести platform_config-аксессор в shared/env_reader (уже существует) или передавать endpoint/credentials параметрами из вызывающего домена; затем добавить core.internal.config в forbidden_modules контракта 2.4

## ARCH-0202 — internal-домен импортирует internals модульного слоя; layers-контракт это разрешает
- Severity: HIGH · Confidence: HIGH · Churn: S-M · WHEN: pre-launch
- Files: core/internal/dev_hosts.py:71 (+ sys.path-хак :63-68) → core/modules/nginx/dev_cert_generator.py
- Symbols: DEFAULT_DEV_CERTS_DIR, get_cert_sans
- Evidence: `from core.modules.nginx.dev_cert_generator import DEFAULT_DEV_CERTS_DIR, get_cert_sans` — восходящее ребро internal→core.modules. Layers-контракт (.importlinter:47-53) перечисляет слои сверху вниз: entrypoints > internal > modules — import-linter разрешает higher→lower, т.е. internal→modules легален по контракту. Запреты покрывают только entrypoints→modules (2.1) и modules→internal (2.3); направление internal→modules не запрещено НИ ОДНИМ контрактом. (Границы/гейт-покрытие — также ARCH-0106.)
- Scenario: следующий «удобный» импорт модульного хелпера из internal пройдёт CI молча; при рефакторинге nginx-модуля ломается core-код dev-hosts (обратная зависимость модуль→ядро уже запрещена — правки придётся делать в ядре).
- Impact: инверсия знания: платформенное ядро сцеплено с конкретным сервисным модулем; ломается заменяемость nginx-модуля
- Minimal fix: перенести DEFAULT_DEV_CERTS_DIR/get_cert_sans в core/internal/shared/ssl_certs как канон, модуль импортирует из ядра (легальное направление); добавить forbidden-контракт internal→modules с пустым allowlist

## ARCH-0203 — lib/secrets.sh прокалывает два слоя вниз
- Severity: MEDIUM · Confidence: HIGH · Churn: M · WHEN: pre-launch
- Files: core/lib/secrets.sh:53-54
- Symbols: decrypt_secrets.py вызов, secrets_manager.py вызов
- Evidence: `python3 "${CORE_DIR}/internal/secrets/decrypt_secrets.py"` и `python3 "${CORE_DIR}/internal/bootstrap/lifecycle/secrets_manager.py"` — остальные lib/*.sh фасады делегируют только core.internal.shared.*
- Scenario: документированный паттерн lib→shared нарушен; bootstrap/lifecycle получает скрытого lib-caller'а; U-09 cross-layer gate используется для достижения доменного кода, не только shared
- Impact: скрытая связка фасада с оркестрацией; изменение lifecycle бьёт по shell-фасаду
- Minimal fix: маршрутизировать через shared-фасад секретов либо поднять вызов в entrypoint-слой caller'а

## ARCH-0204 — Мёртвый путь validate.sh — FQDN-preflight молча пропускается на каждом деплое
- Severity: HIGH · Confidence: HIGH · Churn: S (<10 LOC, 1 файл) · WHEN: pre-launch
- Files: core/internal/deploy/engine/engine.py:92-96,438 · core/internal/deploy/preflight.py:63-79
- Symbols: DeployEngine._validate_script, run_preflight_checks
- Evidence: engine.py жёстко резолвит `parents[3]/"internal"/"validate"/"validate.sh"`, но файла нет: `rg --files -g 'validate.sh' core` → только `core/entrypoints/validate.sh` (двух-хоп схлопнут в DevPlan 173 W1.2, см. его docstring). preflight.py:63: `if Path(validate_script).is_file() and os.access(...)` → else-ветка: `logger.info("[IMP:6] validate.sh not found — skipping FQDN check")`. Путь никогда не существует → каноническая FQDN-uniqueness проверка (`--check-fqdn`) не выполняется ни на одном деплое, молча, на IMP:6.
- Scenario: два проекта с одинаковым FQDN в `ai-platform.yaml` деплоятся последовательно — конфликт не детектируется, второй проект перехватывает vhost/nginx-роутинг; диагностика post-factum через `make verify-domains`.
- Impact: тихая деградация pre-deploy safety; guard-ветка маскирует регрессию (fail-open вместо fail-fast, противоречит контракту docstring «Raises: ValidationError if FQDN conflict»)
- Minimal fix: перенаправить путь на core/entrypoints/validate.sh (или вызвать validate_orchestrator.py напрямую из Python — заодно устраняет инверсию python→shell); else-ветку поднять до IMP:8/WARN

## ARCH-0205 — Python вызывает тонкие shell-фасады над Python — двойной хоп, инверсия фасада
- Severity: MEDIUM · Confidence: HIGH · Churn: M (4 сайта, 4 файла) · WHEN: pre-launch (llm_provision, post_deploy_chain), post-launch (vhost fallback)
- Files: core/internal/bootstrap/deploy/llm_provision.py:48-52 (→ entrypoints/provision-llm.sh → python3 key_provisioner.py) · core/internal/deploy/hooks/post_deploy_chain.py:268-269 (→ notify/notify-hook.sh → telegram_notifier notify; → catalog/generate-catalog.sh → generate_catalog.py) · core/internal/scaffold/vhost_configurator.py:191 (→ sibling add-vhost.sh → vhost_renderer.py)
- Symbols: _plw_body_render_and_provision_llm_2, run_post_deploy_chain, run_add_vhost
- Evidence: каждый вызываемый `.sh` — self-описанный thin-facade: provision-llm.sh — «Thin shell facade (<30 lines)… Unconditionally delegates to Python», add-vhost.sh — «exec python3 -m core.internal.scaffold.vhost_renderer». Python-код платформы порождает subprocess bash, который exec'ает Python из того же дерева — канон «shell вызывает python» нарушен в обратную сторону (python→shell→python).
- Scenario: ошибка диагностируется по stderr двух процессов; timeout/bash-отсутствие добавляют отказные режимы; vhost_configurator вызывает фасад СВОЕГО же пакета (scaffold→scaffold через shell).
- Impact: лишний процесс+парсинг аргументов строками, потеря типизации/исключений, двойной лог-трейл; противоречит языковой политике (фасады — только для Makefile-входов)
- Minimal fix: прямые импорты: key_provisioner.main(), telegram_notifier.notify(), generate_catalog.main(), vhost_renderer CLI-функция; .sh остаются точками входа для Makefile/CI (у post_deploy_chain уже есть DI-швы run_cmd/reconfig_fn — расширить паттерн)

## ARCH-0206 — Cross-domain вертикали внутри internal не покрыты направленными контрактами
- Severity: LOW · Confidence: HIGH · Churn: M (7 файлов) · WHEN: post-launch
- Files: core/internal/healthcheck/tor_proxy_check.py:37 (`from core.internal.bootstrap.firewall import PRIVOXY_PORT`) · core/internal/deploy/verify_contracts.py:71-72 (`from core.internal.practices.generators import PracticesLock, read_lock`; `practices.manifest import load_manifest`) · core/internal/deploy/hooks/post_deploy_chain.py:160 (lazy monitoring.config_renderer) · core/internal/scaffold/{gen_project_platform_md.py:316-319, scaffold_helpers.py:256, project_scaffolder.py:380} (→ practices, 6 импортов)
- Symbols: PRIVOXY_PORT, read_lock, load_manifest, run_monitoring_reconfig
- Evidence: матрица AST (см. шапку). Единственный контракт на sibling-направления — `acyclic-internal-domains` (.importlinter:144-150): ловит циклы, но НЕ направление. bootstrap→deploy запрещён явно (2.2), а симметричные вертикали healthcheck→bootstrap, deploy→practices/monitoring, scaffold→practices — нет: любое новое ребро между 24 пакетами internal легально, пока нет цикла.
- Scenario: practices начнёт импортировать verify (ответ на deploy→practices) → цикл через 2 хопа; acyclic сработает, но к этому моменту направление «кто домен-оркестратор» уже размыто — фикс потребует разворачивать 2 ребра.
- Impact: медленная деградация слоёв внутри internal: сегодня 6 нарушенных рёбер, контрактного механизма остановить рост нет
- Minimal fix: точечно: PRIVOXY_PORT → shared/platform_ports.py; чтение practices.lock → shared-ридер или DI-параметр в verify_contracts; monitoring-reconfig — обязательный reconfig_fn. Стратегически — layers-контракт внутри internal (orchestrators > domains > shared)

## ARCH-0207 — postgres-hook: doc-vs-code расхождение allowlist-контракта
- Severity: LOW · Confidence: 85% · Churn: S · WHEN: post-launch
- Files: core/modules/postgres/hooks/on_project_deploy.py:62
- Symbols: on_project_deploy imports
- Evidence: контракт/документация хука заявляют allowlist «only node_yaml», фактически импортируется также `shared.subprocess_io` — вторая незадекларированная поверхность
- Scenario: потребитель контракта (гейт/ревьюер) считает allowlist полным; фактическое ребро выпадает из надзора
- Impact: микродрейф между задекларированным и фактическим контрактом модуля
- Minimal fix: обновить allowlist-записи ignore_imports/докуменацию хука по факту

## Checked clean (инверсий не найдено)
- Импортов core.entrypoints из core/ нет (единственный grep-hit — negative-probe в тесте)
- module→module deep imports: отсутствуют (postgres hook → shared — легальное потребление контракта)
- Странные локации чисты: provisioner.py, template_engine.py импортируют только shared; reconciler_projects.py → deploy+shared (вниз, легально; серая зона контрактов — ARCH-0108)
- lib→shared фасады (docker.sh, audit.sh, node-resolver.sh, vps-readiness.sh, module-interface.sh) — легальны
- Продакшн → tests.*: runtime-импортов нет; deploy → bootstrap: 0 импортов (контракт 2.2 зелёный)

## Покрытие контрактами (gap-сводка)
`forbidden-shared-domains` покрывает 3 из ~24 сиблингов shared (ARCH-0107/0201); направление internal→modules не покрыто (ARCH-0106/0202); sibling-вертикали покрыты только на цикличность (ARCH-0206); корень core/internal/ — серая зона вне контрактов (ARCH-0108).
