#!/usr/bin/env bash
# GREP_SUMMARY: firewall thin-facade python3 -m firewall ufw declarative idempotent
# STRUCTURE: parse extra_ports args → exec python3 -m core.internal.bootstrap.firewall → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E3): декларативная ufw-политика (reset→defaults→baseline→
##           extra→deny 5432→enable + verify) — в core/internal/bootstrap/firewall.py.
## @scope    Called during bootstrap phase φ1 (phases.py firewall_script) via bash firewall.sh.
## @invariants
##   - <20 LOC thin facade — языковая политика: бизнес-логика в Python
##   - extra_ports передаются позиционно (как раньше: IFS=' ' read)
## @rationale Strangler E3: ufw-оркестрация + валидация портов → Python (тестируемо)
## @changes  2026-08-02 | DevPlan 118 E3 — сокращён до фасада (было 167 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${_SCRIPT_DIR}/../.." && pwd)}"
exec python3 -m core.internal.bootstrap.firewall "$@"
