#!/usr/bin/env bash
# GREP_SUMMARY: postgres hook on-project-deploy auto-create-db database project needs.database
# STRUCTURE: ┌args(PROJECT_DIR,PROJECT)┐ → ◇ read ai-platform.yaml → ◇ extract needs.database → ◇ validate db_name → ◇ docker exec psql CREATE DATABASE IF NOT EXISTS → ⎋ log_imp done
# region MODULE_CONTRACT
## @purpose  Post-deploy hook for postgres module: auto-create project database if declared in ai-platform.yaml needs.database
## @scope    Invoked by deploy-project.sh after successful project deploy; receives PROJECT_DIR, PROJECT, NODE_NAME (unused)
## @invariants
##   - Non-fatal: errors are logged but do not block deploy
##   - Sources ../../../lib/logging.sh for LDD logging
##   - Only creates database if ai-platform.yaml has needs.database set to a valid name
##   - Database name validated: ^[a-zA-Z0-9_]+$
##   - POSTGRES_PASSWORD must be available (via environment or secrets)
##   - docker exec postgres psql used to create database
## @rationale Extracted from deploy-project.sh:auto_create_db() to hook system; postgres module owns its post-deploy DB creation logic
## @changes  Extracted from deploy-project.sh:803-856
# endregion MODULE_CONTRACT

set -euo pipefail

__LOG_PREFIX="postgres-hook"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../../lib/logging.sh"

HOOK_PROJECT_DIR="${1:-}"
HOOK_PROJECT="${2:-}"

if [[ -z "$HOOK_PROJECT_DIR" || -z "$HOOK_PROJECT" ]]; then
    log_imp 6 "hook" "Missing PROJECT_DIR or PROJECT — skipping postgres hook"
    exit 0
fi

# region AUTO_CREATE_DB
## @purpose  Create project database if needs.database is declared in ai-platform.yaml
_auto_create_db() {
    local ai_yaml="${HOOK_PROJECT_DIR}/ai-platform.yaml"

    if [[ ! -f "$ai_yaml" ]]; then
        log_imp 8 "db" "No ai-platform.yaml found — skipping"
        return 0
    fi

    local db_name
    # NodeYaml CLI for needs.database (DevPlan 038c — replaces inline python3 import yaml)
    # Note: database: false in YAML returns "False" string, not empty.
    # Handle both missing key (default "") and explicit false.
    db_name="$(python3 -m core.internal.shared.node_yaml \
        --file "${ai_yaml}" \
        --get needs.database \
        --default "" 2>/dev/null || echo "")"
    # Convert "False" → "" for backward compat with database: false in YAML
    if [[ "$db_name" == "False" || "$db_name" == "false" ]]; then
        db_name=""
    fi

    if [[ -z "$db_name" ]]; then
        log_imp 7 "db" "No database declared in needs.database — skipping"
        return 0
    fi

    log_imp 8 "db" "Creating database '${db_name}' for project '${HOOK_PROJECT}'..."

    [[ "$db_name" =~ ^[a-zA-Z0-9_]+$ ]] || {
        log_imp 10 "db" "FATAL: invalid db_name: ${db_name}"
        return 1
    }

    local pg_password="${PGPASSWORD:-${POSTGRES_PASSWORD:-}}"
    if [[ -z "$pg_password" ]]; then
        log_imp 6 "db" "POSTGRES_PASSWORD not available — skipping DB creation"
        return 0
    fi

    local db_output
    db_output="$(docker exec postgres psql -U postgres -c "CREATE DATABASE ${db_name} OWNER postgres;" 2>&1)" || {
        log_imp 10 "db" "CRITICAL: psql exec failed for database '${db_name}'"
        return 1
    }

    if echo "$db_output" | grep -qi "already exists"; then
        log_imp 8 "db" "Database '${db_name}' already exists — skipping"
    elif echo "$db_output" | grep -qi "ERROR"; then
        log_imp 9 "db" "Failed to create database '${db_name}': ${db_output}"
        return 1
    else
        log_imp 9 "db" "Database '${db_name}' created for project '${HOOK_PROJECT}'"
    fi
}
# endregion AUTO_CREATE_DB

# region MAIN
## @purpose  Main hook entry: orchestrate post-deploy DB creation
main() {
    log_imp 9 "hook" "=== postgres on-project-deploy START: ${HOOK_PROJECT} ==="
    _auto_create_db
    log_imp 9 "hook" "=== postgres on-project-deploy DONE: ${HOOK_PROJECT} ==="
}
# endregion MAIN

main "$@"
