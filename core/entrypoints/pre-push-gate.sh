#!/usr/bin/env bash
# GREP_SUMMARY: pre-push-gate, make-gate, fast-mode, pre-push, blocking, pipx
# STRUCTURE: ┌pre-push┐ → ◇ pipx install (non-blocking) → ◇ make gate MODE=fast (blocking) → ⊕ exit 0 | exit 1
# region MODULE_CONTRACT
## @purpose — Pre-push hook that runs fast gate before allowing git push.
##            Non-blocking: pipx install (warning only).
##            Blocking: make gate MODE=fast — if this fails, push is blocked.
## @io — (stdin from git pre-push) → exit 0 (allow push) / exit 1 (block push)
## @complexity — O(1)
## @rationale — Prevents pushing code that fails static analysis, gate tests, or predeploy checks.
##              Fast mode excludes Docker-dependent tests for speed (~3 min).
## @invariants
##   - pipx install failure is non-blocking (warning only)
##   - make gate MODE=fast failure blocks push
##   - Runs `always_run: true` in pre-commit-config.yaml
## @changes — 2026-07-10 | Created per TestsMetaDevPlan2.md TASK-2
##            2026-07-31 | DevPlan 104 — re-enabled: removed exit 0 + heredoc blocker, restored make gate MODE=fast
# endregion MODULE_CONTRACT

set -euo pipefail
echo "[IMP:7][pre-push-gate][main] Starting pre-push gate" >&2
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

if [[ "${1:-}" == "--help" ]]; then
    echo "Usage: $(basename "$0")"
    echo ""
    echo "Pre-push hook that runs fast gate before allowing git push."
    echo "Blocking: make gate MODE=fast — if this fails, push is blocked."
    echo "Non-blocking: pipx install failure is warning only."
    echo ""
    echo "Invoked by git pre-push hook or pre-commit (always_run: true)."
    exit 0
fi

echo "[IMP:9][pre-push-gate][main] Running fast gate before push (blocking)"

# Non-blocking: update pipx project install
if command -v pipx >/dev/null 2>&1; then
    pipx install --force "$(git rev-parse --show-toplevel)" 2>&1 | tail -3 || true
else
    echo "[pre-push-gate] pipx not installed — skipping pipx update (non-blocking)"
fi

echo "[IMP:9][pre-push-gate][main] Executing make gate MODE=fast"
make gate MODE=fast
