# GREP_SUMMARY: loadtest internal package runner config prometheus report baseline capacity remote
# STRUCTURE: ┌пакет core/internal/loadtest┐ → ◇ config (SoT+env) → ◇ runner_cli (CLI) → ◇ prometheus_pull
#           → ◇ report → ◇ baseline → ◇ capacity → ◇ runner_remote → ⎋
# region MODULE_CONTRACT
## @purpose  Пакет Python-подсистемы нагрузочного тестирования (DevPlan 146 W1-W5):
##           config (SoT+env+NODE), runner_cli (CLI-оркестратор), prometheus_pull
##           (PromQL-saturation), report (report.json/markdown/junit), baseline
##           (history.json + regression), capacity (ступенчатый ramp), runner_remote
##           (LOAD_RUNNER=node). Вход: python3 -m core.internal.loadtest.runner_cli.
## @scope    core/internal/loadtest/ — бизнес-логика платформы (языковая политика:
##           Python); locust-сценарии живут ОТДЕЛЬНО в core/loadtest/scenarios/
##           (не импортируются — locust optional-зависимость).
## @invariants
##   - Модули не импортируют locust (только preflight find_spec в runner_cli)
##   - Exit-коды по shared/contracts.py: 0/1/2/3/4/10 (инвариант 9 DevPlan 146)
##   - Сетевые операции — urllib (requests не runtime-зависимость платформы)
## @rationale Выделенный пакет под домен load-testing: изоляция от core/internal/scripts
##            (CLI-режим, не фасад), единая точка входа (runner_cli), чистые функции
##            для native pytest (DevPlan 146 File Manifest).
## @changes  2026-08-11 | DevPlan 146 W1-W5 — Created
# endregion MODULE_CONTRACT
