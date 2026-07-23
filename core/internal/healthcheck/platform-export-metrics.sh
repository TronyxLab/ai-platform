#!/usr/bin/env bash
# GREP_SUMMARY: platform-export-metrics wrapper → platform_export_metrics.py
# STRUCTURE: ▶ set vars → ○ ensure dirs → ○ exec python3 coordinator → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Bash wrapper for platform_export_metrics.py — ensures directories, then execs Python coordinator
## @scope    Called from cron every minute via flock -n /run/lock/platform-metrics.lock timeout 50s
## @invariants
##   - set -euo pipefail — strict error handling
##   - Creates /run/platform/ (tmpfs) and /var/cache/platform/metrics/ if missing
##   - Exec python3 directly — replaces shell process
##   - <25 lines per DevPlan spec
## @rationale Thin wrapper per language policy — Python does all business logic
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
METRICS_DIR="${SCRIPT_DIR}/metrics"

# Ensure cache + output directories exist on tmpfs (empty after reboot)
mkdir -p /run/platform /var/cache/platform/metrics

exec python3 "${SCRIPT_DIR}/platform_export_metrics.py" "$@"
