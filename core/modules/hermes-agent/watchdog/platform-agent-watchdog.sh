#!/bin/bash
# GREP_SUMMARY: platform-agent-watchdog launcher python3 agent_watchdog.py systemd oneshot
# STRUCTURE: ▶ resolve script_dir → ▶ exec python3 agent_watchdog.py "$@" → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin shell launcher for agent_watchdog.py — preserves backward compatibility
##           for any direct shell invocation while delegating all logic to Python.
## @scope    <30 LOC — passes all arguments to Python daemon verbatim.
## @invariants
##   - Zero business logic — pure delegation
##   - Zero inline python3 calls
##   - exec replaces shell process (no subshell overhead)
##   - Same exit code as Python daemon
## @rationale Shell wrapper exists for backward compatibility with existing systemd unit
##            paths and any manual invocations from operator CLI.
# endregion MODULE_CONTRACT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/agent_watchdog.py" "$@"
