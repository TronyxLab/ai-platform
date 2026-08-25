#!/usr/bin/env python3
# GREP_SUMMARY: channel_pin deploy-project reusable-workflow SHA-pin snapshot SoT scaffold adopter REF-0012 freshness
# STRUCTURE: ▶ const(DEPLOY_CHANNEL_PIN + PIN_COMMENT) → ◇ consumers: project_adopter (генерация) + gate freshness → ⎋ single-source pin
# region MODULE_CONTRACT
## @purpose  Single source of truth пина reusable deploy-project.yml канала для генерации
##           project_adopter'а (QA C2/R9 fix-forward, DevPlan 14 T1.1): adopted проекты должны
##           рождаться от того же актуального SHA, что и шаблонные.
## @scope    core/internal/scaffold/ — потребители: project_adopter.simplify_deploy_yml() и
##           freshness-гейт tests/gates/test_gate_workflow_sha_pins.py (G6).
## @invariants
##   - DEPLOY_CHANNEL_PIN — full 40-hex commit SHA платформы; ОБЯЗАН содержать последний коммит,
##     менявший .github/workflows/deploy-project.yml (гейт проверяет офлайн через
##     git merge-base --is-ancestor last-touch-commit и значение пина)
##   - PIN_COMMENT — честная дата снапшота ≥ даты последнего изменения workflow (ложь в
##     комментарии → RED, fixture CRITICAL.md: комментарий «2026-08-24» при пине от 2026-08-18)
##   - Литеральные двойники пина в templates/template-{backend,frontend}/.github/workflows/deploy.yml
##     обязаны совпадать с этим модулем байт-в-байт (гейт-equalizer трёх мест; шаблоны — статический
##     payload template_engine, python туда не импортируется)
## @rationale Q: почему Python-SoT + литералы в шаблонах, а не полный рендер? A: triple-literal
##   допустим при условии гейта-эквалайзера на всех трёх местах + freshness-критерия; полная
##   шаблонизация пина — churn без снижения риска (DevPlan 14 §Design Decisions).
## @changes 2026-08-25 | Created — T1.1 fix-forward (QA C2 stale-pin 4425ce0 от 2026-08-18,
##                      R9 mutable @main у adopter'а, G6 freshness-гейт)
# endregion MODULE_CONTRACT

# Full commit SHA платформы (main snapshot), содержащий актуальный харденинг deploy-project.yml.
DEPLOY_CHANNEL_PIN = "42679a084ff7b3a1281a4543e336f57cbc687875"

# Честный комментарий-снапшот (формат фиксируется freshness-гейтом: 'main snapshot YYYY-MM-DD …').
PIN_COMMENT = "main snapshot 2026-08-25 (REF-0012)"
