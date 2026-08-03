#!/usr/bin/env bash
# GREP_SUMMARY: platform-export-metrics wrapper → platform_export_metrics.py
# STRUCTURE: ▶ set vars → auto-detect PLATFORM_ROOT + NODE_NAME → ensure PYTHONPATH + NODE_NAME in env → ensure dirs → protective dir check → exec python3 coordinator → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Bash wrapper for platform_export_metrics.py — ensures PYTHONPATH, NODE_NAME, directories, then execs Python coordinator
## @scope    Called from host cron every minute via flock -n /run/lock/platform-metrics.lock timeout 50s
## @invariants
##   - set -euo pipefail — strict error handling
##   - Auto-detects PLATFORM_ROOT (/opt/platform or 3 levels up from script)
##   - Auto-detects NODE_NAME via python3 -m core.internal.shared.node_detect (excludes
##     scripts/ and secrets/; fallback: "unknown") — single detector canon (DevPlan 116 B3 T2)
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

# Auto-detect NODE_NAME via canonical node_detect (DevPlan 116 B3 T2, U-38):
#   node_detect.auto_detect_node_name excludes scripts/ and secrets/ subdirectories.
#   Priority: explicit NODE_NAME env → node_detect → "unknown" (WARN).
# @changes 2026-08-01 · Replaced `ls | grep -v secrets | head -1` hack (did NOT exclude
#           scripts/ → scripts dir could be picked as NODE_NAME). Single detector now.
if [ -z "${NODE_NAME:-}" ]; then
    NODE_NAME=$(python3 -m core.internal.shared.node_detect --detect-node-name 2>/dev/null) || NODE_NAME="unknown"
    if [ "$NODE_NAME" = "unknown" ]; then
        echo "[IMP:7][platform-export-metrics][WARN] Node detection failed — NODE_NAME=unknown" >&2
    fi
fi

# Set PYTHONPATH so 'from core.internal.healthcheck.metrics...' works
export PYTHONPATH="${PLATFORM_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export NODE_NAME

# Output path: тот же env, что и Python-координатор (STATUS_METRICS_JSON).
# Прод-дефолт /run/platform/status-metrics.json (tmpfs); dev-локаль (macOS, /run read-only)
# переопределяет через env — см. .env STATUS_METRICS_JSON.
STATUS_METRICS_JSON="${STATUS_METRICS_JSON:-/run/platform/status-metrics.json}"
export STATUS_METRICS_JSON

# Ensure output directory exists (tmpfs on prod, empty after reboot). Fail-fast на
# НЕсоздаваемом выходном каталоге — это реальная ошибка конфигурации (R4, не skip).
METRICS_DIR="$(dirname "$STATUS_METRICS_JSON")"
mkdir -p "$METRICS_DIR"

# Cache dir — best-effort (не-блокирующий): на dev-локаль может не быть /var/cache прав.
mkdir -p /var/cache/platform/metrics 2>/dev/null || true

# Protective: ensure status-metrics.json is a file, not a directory (P1 safeguard)
if [ -d "$STATUS_METRICS_JSON" ]; then
    rmdir "$STATUS_METRICS_JSON" 2>/dev/null || true
fi

exec python3 "${SCRIPT_DIR}/platform_export_metrics.py" "$@"
