#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint validate schema FQDN lint
# STRUCTURE: ▶ init → ◇ --lint flag? → ⎋ delegate to internal/validate/validate_orchestrator.py → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Entry-point schema-валидации/линта — прямой вызов из check-suite.yaml
##           (суиты validate/lint, План 175 W2.1 — make-таргеты validate/lint удалены)
## @scope    Called from check-suite.yaml (`bash core/entrypoints/validate.sh [--lint]`).
##           Delegates to core/internal/validate/validate_orchestrator.py
## @invariants
##   - All args passed through to validate_orchestrator.py
##   - --lint flag triggers lint mode
## @rationale Тонкий фасад (Strangler): делегирует schema/FQDN-валидацию и lint напрямую
##            в core/internal/validate/validate_orchestrator.py (двух-хоповый фасад
##            validate.sh → internal/validate/validate.sh схлопнут, DevPlan 173 W1.2)
# endregion MODULE_CONTRACT
set -euo pipefail
echo "[IMP:7][validate][main] Starting validate entrypoint" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

echo "[IMP:8][validate][main] Delegating to validate_orchestrator.py" >&2
exec python3 "${PATHS_INTERNAL_DIR}/validate/validate_orchestrator.py" "$@"
