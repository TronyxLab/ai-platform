# $ARTIFACT_CONTRACT
## @PURPOSE Исправление drift deploy-modules.sh (260 LOC фактически vs 91 заявлено) → реальный фасад ≤80 LOC
## @DESCRIPTION
`core/internal/bootstrap/deploy-modules.sh` задокументирован в AGENTS.md как 91 LOC (W4-E1 Strangler-Fig),
но фактически вырос до 260 LOC. Причины роста:
- DEPLOY_PARALLEL mode (topo_sort + pre-pull + group deploy + system modules) — ~120 строк
- DEPLOY_ORCHESTRATOR CLI support — ~20 строк
- status-metrics.json pre-create — ~10 строк
- HC_DONE_MARKER — ~5 строк
- litellm-config render — ~5 строк
- Severity-based exit логика — ~20 строк

Существующие Python-модули уже покрывают большую часть логики:
- `deploy/secrets_validator.py` — validate-charsets, parse-node-yaml, check-env, batch-check-env, detect-type, module-metadata
- `deploy/docker_orchestrator.py` — deploy, deploy-group, pre-pull
- `deploy/context_overlay.py` — ensure
- `deploy/spool_validator.py` — verify
- `deploy/sudoers_generator.py` — batch-generate
- `deploy/orphan_reconciler.py` — orphan cleanup
- `_topo_sort.py` — topological sort
- `json_field_extractor.py` — JSON field extraction

Нужно: вынести PARALLEL/SEQUENTIAL routing, severity aggregation в Python-оркестратор.
Обновить документацию AGENTS.md (91→80 LOC).
## @RATIONALE
- Drift между документацией и реальностью = risk
- 260 LOC оркестрации в shell при наличии 8 Python-модулей — нарушение Strangler-Fig паттерна
- Рост на 169 LOC с момента W4-E1 — тенденция к обратному росту shell
## @ACCEPTANCE_CRITERIA
- AC1: Новый Python-модуль `deploy/deploy_orchestrator.py` (или расширение существующего) с routing logic
- AC2: Shell-фасад ≤ 80 LOC (arg parsing + вызов Python-оркестратора)
- AC3: DEPLOY_PARALLEL=true путь работает идентично
- AC4: DEPLOY_ORCHESTRATOR=true путь работает идентично
- AC5: Sequential путь (DEPLOY_PARALLEL=false) работает идентично
- AC6: Severity-based exit (CRIT/WARN/DONE) идентичен
- AC7: AGENTS.md обновлён: 91→80 LOC, описание соответствует реальности
- AC8: `make gate MODE=fast` зелёный
- AC9: Все TRAP-аннотации сохранены
## @IMPLEMENTS Brief 100
## @IMPACTS core/internal/bootstrap/deploy-modules.sh, core/internal/bootstrap/deploy/deploy_orchestrator.py (NEW/MODIFY), core/internal/bootstrap/AGENTS.md, core/AGENTS.md
## @REQUIRES Ничего — все Python-зависимости уже существуют
