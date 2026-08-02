#!/usr/bin/env bash
# GREP_SUMMARY: modules-healthcheck thin-facade python3 -m modules_healthcheck module-orchestration
# STRUCTURE: parse MODE → exec python3 -m core.internal.healthcheck.modules_healthcheck → ⎋ pass-through exit
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E4): оркестрация healthcheck всех модулей (dispatch через
##           shared/module_interface, restart-loop docker inspect, MODE=deep) — в
##           core/internal/healthcheck/modules_healthcheck.py.
## @scope    Вызывается ТОЛЬКО из core/entrypoints/healthcheck.sh (make healthcheck)
## @invariants
##   - <10 LOC thin facade — языковая политика: бизнес-логика в Python
##   - exit 0 = все модули healthy; exit 1 = хотя бы один unhealthy (pass-through)
## @rationale Strangler E4: grep install_type + raw docker inspect → Python (YAML-парсер + module_interface)
## @changes  2026-08-02 | DevPlan 118 E4 — сокращён до фасада (было 127 LOC)
# endregion MODULE_CONTRACT
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: $0 [MODE=deep]"
    echo ""
    echo "Iterate all platform module healthcheck scripts and run them."
    echo "Default: healthcheck.sh liveness (via module_interface dispatch) for all modules."
    echo "MODE=deep: run module-specific healthcheck.sh MODE=deep for all modules."
    echo ""
    echo "Returns 0 if all pass, 1 if any fail."
    exit 0
fi

echo "[IMP:7][modules-healthcheck][main] Starting module healthcheck orchestration" >&2
exec python3 -m core.internal.healthcheck.modules_healthcheck "$@"
