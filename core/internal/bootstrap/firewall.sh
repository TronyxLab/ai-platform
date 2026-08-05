#!/usr/bin/env bash
# GREP_SUMMARY: firewall thin-facade python3 -m firewall ufw incremental idempotent source-ip
# STRUCTURE: parse --source-ip + extra_ports args → exec python3 -m core.internal.bootstrap.firewall → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E3 + 136 W10): инкрементальная ufw-политика
##           (enable→default-deny→ssh-first→baseline→extra from <ip>→module-deny→5432-deny +
##           stale-reconcile + verify) — в core/internal/bootstrap/firewall.py. S-14/T10.10:
##           firewall НИКОГДА не выключается (нет disable/reset — инкрементальный apply).
## @scope    Called during bootstrap phase φ1 (phases.py firewall_script) via bash firewall.sh.
## @invariants
##   - <20 LOC thin facade — языковая политика: бизнес-логика в Python
##   - extra_ports требуют --source-ip <ip> (S-8/T10.6: allow from <ip>, НЕ Anywhere)
##   - Аргументы пробрасываются позиционно в Python CLI ("$@")
## @rationale Strangler E3: ufw-оркестрация + валидация портов → Python (тестируемо)
## @changes  2026-08-02 | DevPlan 118 E3 — сокращён до фасада (было 167 LOC)
## @changes  2026-08-05 | DevPlan 136 W10 — контракт сменён на инкрементальный (S-14), --source-ip (S-8)
# endregion MODULE_CONTRACT

set -euo pipefail
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${_SCRIPT_DIR}/../.." && pwd)}"
exec python3 -m core.internal.bootstrap.firewall "$@"
