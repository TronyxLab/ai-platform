#!/usr/bin/env bash
# GREP_SUMMARY: s3-ssl-cache, ssl-cert-cache, s3-upload, s3-download, cert-check, cli-facade
# STRUCTURE: ▶ ┌args┐ → ◇ parse command → ◇ python3 s3_ssl_cache.py <command> <args> → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Thin CLI facade — delegates all business logic to s3_ssl_cache.py.
##           Reduced from 602 lines (DevPlan 052 Phase 1). All S3 operations
##           now execute in the same Python process as the caller, eliminating
##           the subshell credential propagation bug.
## @scope    Called from issue-cert.sh (--reloadcmd and --renew-hook) for backward
##           compatibility. cert_orchestrator.py imports s3_ssl_cache directly.
## @location core/internal/bootstrap/s3-ssl-cache.sh
## @usage    s3-ssl-cache.sh upload|download|check|bulk-restore <domain|--node-yaml PATH>
## @invariants
##   - Delegates ALL business logic to python3 s3_ssl_cache.py
##   - Exit code matches python3 exit code (0=success, 1=failure)
##   - Arguments passed through verbatim
##   - module-level contract from s3_ssl_cache.py is the single source of truth
## @changes  CREATED: 2026-07-21 · Wave 1 SSL S3 cache (DevPlan 024)
##           REDUCED: 2026-07-25 · DevPlan 052 Phase 1 — 602→~30 lines, business logic in s3_ssl_cache.py
# endregion MODULE_CONTRACT

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command="${1:-}"
shift || true
exec python3 "${SCRIPT_DIR}/s3_ssl_cache.py" "$command" "$@"
