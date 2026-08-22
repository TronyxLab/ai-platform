# Направление 4 — God modules/classes

Метод: wc -l top-25 py / top-12 sh; class/method counts; breadth of unrelated imports; кластеризация ответственности по именам функций. Агент: explore, 16 tool calls. Дата 2026-08-22.
Итог: GODS: 4 подтверждено; 4 кандидата опровергнуто как big-but-cohesive; shell-боги не найдены.

## ARCH-0039 — context_deployer.py: пайплайн деплоя контекста поверх 5 инфраструктурных доменов
- Severity: CRITICAL (размер+позиция φ8 на критическом пути) · Confidence: HIGH · Churn: S (extract ONE cluster) · WHEN: pre-launch
- Files: internal/bootstrap/deploy/context_deployer.py — 1276 LOC (#1 в репо), 4 класса / 19 module fns
- Symbols/clusters: (a) project-deploy+health `_deploy_single_project_via_orchestrator/_is_project_healthy/_ensure_bootstrap_compose`; (b) certs/vhosts/nginx `_step_certs/_step_vhosts/_step_nginx_reload`; (c) LLM provisioning `_render_and_provision_llm`; (d) audit `_write_audit`; (e) domain resolution `extract_domains_for_context/_resolve_context`; (f) CLI main
- Evidence: 14 import statements / 7+ доменов (cert_orchestrator+ssl_certs, DeployOrchestrator+LocalChannel, llm_paths+platform_ports, node_yaml, docker_compose, subprocess_io, timeouts); DevPlan 118 D6 уже разбивал god-function на steps — файл всё ещё оркестрирует 5 подсистем
- Scenario: фикс cert-provider и фикс LLM-key provisioning двумя людьми в hotfix week — один файл, соседние регионы, φ8 критический путь → merge conflicts и регрессия idempotent skip
- Minimal fix: извлечь ОДИН кластер `_render_and_provision_llm` (L766-794 + константы LITELLM_*) → llm/config_renderer (уже существует)

## ARCH-0040 — orchestrator.py DeployOrchestrator: god-CLASS (файл когезивен как фасад)
- Severity: HIGH · Confidence: HIGH · Churn: M (post-launch extract) · WHEN: pre-launch = freeze, split = post-launch
- Files: internal/deploy/orchestrator.py — 1220 LOC, 1 класс × 14 методов (+3 dataclasses)
- Clusters: deploy lifecycle (deploy/_prepare/_apply/_verify/_deploy_compose); rollback (_rollback_deploy/_rollback_compose/_restore_payload_files); status/remove; receive flow (receive/_assemble_payload); multi deploy_many; post-deploy chain
- Evidence: docstring декларирует намеренный single facade (Engine/Deliverer/Channel/Audit/History/Poller); receive() — CI forced-command entry (test fan-in 11 — см. ARCH-0027)
- Scenario: team A чинит rollback snapshot, team B — receive tar assembly: один класс на production hotfix пути
- Minimal fix: НЕ сплитить до запуска; freeze контрактными тестами. Post-launch: receive+_assemble_payload+_restore_payload_files → существующий sibling deploy/receive_flow.py

## ARCH-0041 — project_scaffolder.py: render + git/GitHub + vhost + env-gen в одном workflow
- Severity: MEDIUM · Confidence: MED · Churn: S · WHEN: pre-launch (дешёвый extract)
- Files: internal/scaffold/project_scaffolder.py — 928 LOC, 14 fns
- Clusters: template render; env/practices gen; git_init_project+create_github_repo (L497-552); run_add_vhost; checklist; plan/confirm; CLI
- Evidence: docstring признаёт происхождение из 782-LOC shell strangler; 4 несвязанных подсистемы импортом
- Scenario: коллизия фиксов vhost-registration vs template-placeholder на new-project пути (dev machine) во время онбординга launch week
- Minimal fix: extract git-пары → scaffold/git_init модуль

## ARCH-0042 — deploy_orchestrator.py: 3 живые стратегии деплоя + LLM render + статус-метрики
- Severity: MED · Confidence: MED · Churn: S · WHEN: pre-launch (LLM extract), остальное post-launch
- Files: internal/bootstrap/deploy/deploy_orchestrator.py ~1000 LOC, 16 fns
- Clusters: mode routing orchestrate/_route_deploy c ТРЕМЯ живыми стратегиями (_sequential/_parallel/_orchestrator); _render_litellm_config (~30 LOC, чужой домен); status metrics; severity/exit policy; preflight/parse
- Evidence: imports 7 deploy-субмодулей; strategy-ветки — самая дрейфовая поверхность bootstrap (рядом TRAP[BUG] :365 и hardcode ARCH-0031)
- Minimal fix: _render_litellm_config → llm/config_renderer; стратегии консолидировать после запуска

## Опровергнуто (big-but-cohesive, НЕ god)
- sync_env_defaults.py (961): 20 `_section_*` renderer'ов — breadth данных одного concern (manifest→env rendering)
- docker_ops.py (824): когезивный docker-CLI фасад, один домен, DI-friendly
- test_runner.py (~800): borderline, dev-only tooling — post-launch кандидат максимум
- Shell (~76 *.sh): богов нет; крупнейший node-lifecycle.sh = 3 функции (thin dispatcher per keep-decision)

Top sizes (py, non-test): context_deployer 1276 · orchestrator 1220 · deploy_orchestrator ~1000 · sync_env_defaults 961 · project_scaffolder 928 · docker_ops 824 · test_runner ~800 · key_provisioner ~700 · generate_entrypoint_manifest ~650
