# GREP_SUMMARY: check-project-checks-registry, handlers-registry, HANDLERS, registration, 19-checks, kebab-case-dispatch
# STRUCTURE: ┌HANDLERS: dict[id→handler]┐ ← ⊕ tool.py (9: gitleaks/ruff/shellcheck/pyright/eslint/build/pytest) → ⊕ file.py (6: hygiene/commit-msg/grep-summary/docs-in-code/transition/agent-check) → ⊕ compose.py (3: compose-config/restart-policies/compose-service-networks) → ⊕ drift.py (drift-gate) → ⎋ реестр диспетчера exec.run_check
# region MODULE_CONTRACT
## @purpose  Реестр handler-ов K1-канала (DevPlan 170 W10-A декомпозиция): единая точка
##           регистрации 19 проверок канона — kebab-case id (practices_manifest.yaml) →
##           Python-функция. checks/tool.py (9 инструментальных), checks/file.py (6 файловых),
##           checks/compose.py (3 compose) + drift.py (drift-gate) = 19. Реестр наполняет
##           exec.HANDLERS ПРИ ИМПОРТЕ (регистрация до первого вызова run_check).
## @scope    Потребители: exec.run_check (диспетчер), __init__.py пакета (импорт checks —
##           гарантия регистрации), гейты тестируют набор handler-ов через прогон.
## @invariants
##   - Ровно 19 id канона имеют local-обработчик (неизвестный id → SKIP в exec)
##   - Регистрация происходит при импорте МОДУЛЯ checks (не функции) — идемпотентна
##   - id канона НЕ меняются (контракт practices_manifest.yaml + nodeid тестов)
##   - Публичные имена handler-ов (check_*) — гейт private-imports (U-07/ns): приватные
##     имена не пересекают границы модулей core/ (allowlist пуст)
## @rationale Выделение реестра из монолита: HANDLERS + dispatch остаются в exec.py,
##            регистрация — здесь (группировка handler-ов по типу проверки, research-A §2);
##            разрыв цикла exec↔checks: exec не импортирует handler-и, только реестр.
## @changes  2026-08-15 · DevPlan 170 W10-A — создан (выделен из check_project.py:1201-1221;
##           публичные имена handler-ов — ns-канон)
##           2026-08-31 · Plan 019 TASK-5 — +compose-service-networks (K1-зеркало K3-чеков;
##           18 → 19)
# endregion MODULE_CONTRACT

from core.internal.practices.check_project.checks import compose, file, tool
from core.internal.practices.check_project.drift import check_drift_gate
from core.internal.practices.check_project.exec import HANDLERS

# ── Регистрация обработчиков (18 проверок K1, канон practices_manifest.yaml) ──
HANDLERS.update({
    # tool.py — инструментальные (внешние CLI)
    "gitleaks": tool.check_gitleaks,
    "hygiene": file.check_hygiene,
    "commit-msg": file.check_commit_msg,
    "compose-config": compose.check_compose_config,
    "restart-policies": compose.check_restart_policies,
    "compose-service-networks": compose.check_compose_service_networks,
    "ruff-format": tool.check_ruff_format,
    "shellcheck": tool.check_shellcheck,
    "pytest-baseline": tool.check_pytest_baseline,
    "build": tool.check_build,
    "ruff-check": tool.check_ruff_check,
    "pyright": tool.check_pyright,
    "eslint": tool.check_eslint,
    "pytest-full": tool.check_pytest_full,
    # file.py — файловые/SCM
    "grep-summary": file.check_grep_summary,
    "docs-in-code": file.check_docs_in_code,
    "transition-traces-ban": file.check_transition_traces_ban,
    "agent-check": file.check_agent_check,
    # drift.py — дрейф-гейт практик
    "drift-gate": check_drift_gate,
})
