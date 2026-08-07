#!/usr/bin/env bash
# GREP_SUMMARY: platform-secrets thin-facade installer.py systemd oneshot decrypt sops age secrets env boot tmpfs install enable
# STRUCTURE: guard(root) → exec python3 installer.py → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Тонкий фасад (DevPlan 118 E7): age-key создание/миграция, permission auto-fix,
##           secrets-enc symlink-fallback, systemd unit install, ensure_platform_dirs (setgid 2775)
##           — в core/modules/platform-secrets/installer.py.
## @scope    Called during bootstrap step ⑪ for system-type modules; generates /var/lib/platform/run/secrets.env (142 W2)
## @invariants
##   - <10 LOC thin facade — языковая политика: бизнес-логика в Python
##   - AGE_SECRET_KEY / PLATFORM_ROOT передаются через env (как раньше)
## @rationale Strangler E7: file-менеджмент + systemd → Python (тестируемо, tmp_path)
## @changes  2026-08-02 | DevPlan 118 E7 — сокращён до фасада (было 225 LOC)
# endregion MODULE_CONTRACT

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "[IMP:10][platform-secrets][main] ERROR: must run as root" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-/opt/platform}"
exec python3 "${SCRIPT_DIR}/installer.py"
