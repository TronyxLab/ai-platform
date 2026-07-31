# shellcheck shell=bash
# GREP_SUMMARY: vps-readiness facade python delegation
# STRUCTURE: ▶ source ssh.sh → ◇ check_vps_ready() → python3 -m core.internal.shared.vps_readiness "$@" → ⎋ exit $?
# region MODULE_CONTRACT
## @purpose  Thin shell facade — delegates to core.internal.shared.vps_readiness.py (DevPlan 105).
##           Preserves the check_vps_ready() API for makefiles/deploy.mk:37-38 and CI workflows.
## @scope    Sourced by makefiles/deploy.mk (deploy pre-flight). NOT an executable script.
## @invariants
##   - Source-safe: defines only check_vps_ready() function, no side effects at source time
##   - exit 0 = VPS ready; exit 1 = any check FAIL (exit code propagated from Python)
##   - --quick / --json флаги пробрасываются в Python CLI как есть
## @rationale Strangler-Fig: business logic migrated to Python, bash остаётся тонким фасадом.
##            НЕ source'ит logging.sh/paths.sh (D5) — IMP-логи теперь в Python, paths не нужен.
##            Только ssh.sh — для _default_ssh_runner() внутри Python (ssh_read + SSH_OPTS_COMMON).
## @changes 2026-07-31 | 181 LOC → ~20 LOC фасад (DevPlan 105)
# endregion MODULE_CONTRACT

# shellcheck source=./ssh.sh
source "${BASH_SOURCE[0]%/*}/ssh.sh"  # SSH_OPTS_COMMON, ssh_read, ssh_exec

check_vps_ready() {
    python3 -m core.internal.shared.vps_readiness "$@"
}
