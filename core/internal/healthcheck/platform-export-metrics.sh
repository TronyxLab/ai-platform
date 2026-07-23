#!/usr/bin/env bash
# GREP_SUMMARY: platform-export-metrics wrapper → platform_export_metrics.py
# STRUCTURE: ▶ set vars → auto-detect PLATFORM_ROOT + NODE_NAME → ensure PYTHONPATH + NODE_NAME in env → ensure dirs → protective dir check → exec python3 coordinator → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Bash wrapper for platform_export_metrics.py — ensures PYTHONPATH, NODE_NAME, directories, then execs Python coordinator
## @scope    Called from host cron every minute via flock -n /run/lock/platform-metrics.lock timeout 50s
## @invariants
##   - set -euo pipefail — strict error handling
##   - Auto-detects PLATFORM_ROOT (/opt/platform or 3 levels up from script)
##   - Auto-detects NODE_NAME from /opt/node-configs/ (fallback: "unknown")
##   - Exports PYTHONPATH so `from core.internal.healthcheck.metrics...` works
##   - Creates /run/platform/ (tmpfs) and /var/cache/platform/metrics/ if missing
##   - Protective: removes status-metrics.json if it exists as directory (P1 safeguard)
##   - Exec python3 directly — replaces shell process
## @rationale Thin wrapper per language policy — Python does all business logic.
##            Auto-detection of PYTHONPATH + NODE_NAME removes dependency on cron environment setup (P2 fix).
##            Protective directory check prevents Docker bind-mount race condition (P1 fix).
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-detect platform root: /opt/platform (canonical) or 3 levels up from script
if [ -z "${PLATFORM_ROOT:-}" ]; then
    PLATFORM_ROOT="/opt/platform"
    [ -d "$PLATFORM_ROOT" ] || PLATFORM_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

# Auto-detect NODE_NAME from node-configs directory (fallback: "unknown")
if [ -z "${NODE_NAME:-}" ]; then
    NODE_NAME=$(ls /opt/node-configs/ 2>/dev/null | grep -v secrets | head -1)
    [ -z "$NODE_NAME" ] && NODE_NAME="unknown"
fi

# Set PYTHONPATH so 'from core.internal.healthcheck.metrics...' works
export PYTHONPATH="${PLATFORM_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export NODE_NAME

# Ensure cache + output directories exist on tmpfs (empty after reboot)
mkdir -p /run/platform /var/cache/platform/metrics

# Protective: ensure status-metrics.json is a file, not a directory (P1 safeguard)
if [ -d /run/platform/status-metrics.json ]; then
    rmdir /run/platform/status-metrics.json 2>/dev/null || true
fi

exec python3 "${SCRIPT_DIR}/platform_export_metrics.py" "$@"
