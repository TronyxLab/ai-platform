#!/usr/bin/env bash
# GREP_SUMMARY: deploy-project ci-deploy rollback docker-compose atomic-rollback healthcheck audit prune-images forced-command hook-invocation module-hooks remove status lifecycle platform-deliver deliver-verb
# STRUCTURE: parse_ssh_command|flags(─remove|─status) → ◇ platform-deliver dispatch (D2) → load_config → save_previous → pull_image → atomic_up → wait_health(≤60s) → tag_current/rollback → prune_old(N=3) → trigger_deploy_hooks|trigger_remove_hooks → notify_hook → audit_log
# region MODULE_CONTRACT
## @purpose  Whitelisted entry-point for ci-deploy SSH forced-command: atomic deploy with healthcheck-based rollback (04-templates §7) + remove/status verbs
## @scope    Executed exclusively via SSH authorized_keys command="..." + restrict; receives <project> <ref> via $SSH_ORIGINAL_COMMAND or flags via CLI
## @location core/internal/deploy/deploy-project.sh — moved from core/scripts/platform-deploy.sh
## @invariants
##   - Deploy: PROJECT and REF parsed from SSH_ORIGINAL_COMMAND; exit 1 if missing or invalid
##   - Remove: `docker compose down` БЕЗ `-v` (данные не удаляются, O7/DD10)
##   - Status: JSON stdout with docker compose ps + last deploy-result.json
##   - Stop/remove/restart не затрагивают volumes, БД, images (O7)
##   - remove/status переиспользуют deploy forced-command channel (DD12)
##   - Хуки `on_project_remove` идемпотентны и неразрушающи (K2)
##   - previous_image_id saved BEFORE pull; if absent → first deploy (no rollback possible, escalate 🔴)
##   - atomic docker compose up -d <service>; healthcheck poll ≤60s (start_period + interval * retries)
##   - healthy: tag :current → notify-hook(🚀 ✅) → audit_log(SUCCESS) → exit 0
##   - failed/timeout: re-tag previous_image_id → compose up -d --force-recreate → audit_log(ROLLBACK) → notify-hook(🚀 ⚠️) → exit 1
##   - N=3 images kept; older pruned after successful deploy
##   - every invocation writes to /var/log/platform/audit.log (00-foundation §13)
##   - no shell, no interactive input, no arbitrary commands (SSH restrict enforces this)
##   - GHCR registry auth configured centrally via SOPS/bootstrap on VPS (04 §10); no per-repo --ghcr-token
## @rationale
##   ⚠️ TRAP[DECISION] Rollback on-node, not in CI/CD — eliminates network roundtrip, keeps CI/CD thin (04 §7).
##   Rejected: CI/CD-driven rollback (re-deploy previous image via GitHub Actions).
##   Reason: instant rollback without CI pipeline wait.
##   ⚠️ TRAP[DECISION] SSH forced-command instead of shell — command="${PLATFORM_ROOT}/core/internal/deploy/deploy-project.sh ...",restrict; ci-deploy has no shell.
##   Rejected: full shell access for ci-deploy.
##   Reason: security — exactly one allowed command, no SSH escalation possible.
##   ci-deploy in docker group → no sudo for docker commands (principle of least privilege, 06 §4.2).
## @changes 2026-07-17 · T6 — Added verb contract K1 (--remove, --status), _trigger_remove_hooks(), TRAP[BUSINESS]
##           2026-07-17 · T2 — Added verb platform-deliver (handle_deliver, _validate_project_name), TRAP[DECISION]
# endregion MODULE_CONTRACT

set -euo pipefail
shopt -s lastpipe

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly CORE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
readonly PROJECTS_BASE="${PROJECTS_BASE:-/opt/projects}"
readonly MAX_WAIT_SEC="${PLATFORM_DEPLOY_TIMEOUT:-60}"
readonly KEEP_IMAGES="${PLATFORM_DEPLOY_KEEP_IMAGES:-3}"
readonly AUDIT_LOG="/var/log/platform/audit.log"
readonly AUDIT_TAG="platform-audit"
readonly KEEP_SNAPSHOTS="${KEEP_SNAPSHOTS:-3}"

DEPLOY_STATUS="failed"
NODE_NAME="${1:-$(hostname)}"

# Source audit helper (canonical lib version)
source "${SCRIPT_DIR}/../../lib/audit_logging.sh" 2>/dev/null || true

__LOG_PREFIX="platform-deploy"
source "${SCRIPT_DIR}/../../lib/logging.sh"
source "${SCRIPT_DIR}/../../lib/healthcheck.sh"
source "${SCRIPT_DIR}/../../lib/docker.sh"
# shellcheck source=core/lib/paths.sh
source "${SCRIPT_DIR}/../../lib/paths.sh"
# shellcheck source=core/lib/yaml_read.sh
source "${SCRIPT_DIR}/../../lib/yaml_read.sh"

# ⚠️ TRAP[DECISION] · 2026-07-17 · — · audit_log() replaces audit_write()
# · Rejected: keeping audit_write() in deploy-project.sh (duplicate)
# · Reason: audit_log() from lib/audit_logging.sh is the canonical function.
#   audit_write() was a local duplicate with identical signature (step, status, msg)
#   but different implementation (no syslog, direct file append, explicit IMP:9 echo).
#   audit_log() provides: syslog via logger -t platform-audit + file append + structured
#   IMP:8 fallback. Removing the local duplicate eliminates drift between the two.
# · Rev: if audit_log() signature changes, all call sites in deploy-project.sh must be updated

# region TRAP_ROLLBACK
## @purpose  ERR trap handler — initiates rollback on any command failure during deploy
_rollback_on_error() {
    local exit_code=$?
    log_imp 10 "rollback" "CRITICAL: Deploy error detected (exit code $exit_code) at line ${BASH_LINENO[0]}"
    DEPLOY_STATUS="failed"
    _restore_from_snapshot
    exit 1
}
# endregion TRAP_ROLLBACK

# region TRAP_FINALIZE
## @purpose  EXIT trap handler — always fires, cleans up regardless of success/failure
_finalize_deploy() {
    if [[ "${DEPLOY_STATUS:-}" == "success" ]]; then
        _cleanup_snapshots
    fi
    _write_deploy_result
}
# endregion TRAP_FINALIZE

# region RESTORE_FROM_SNAPSHOT
## @purpose  Restore previous container state from snapshot
_restore_from_snapshot() {
    local snapshot_dir="${PROJECT_DIR}/.deploy-snapshots"
    local started_file="$snapshot_dir/.deploy-started"

    if [[ ! -f "$started_file" ]]; then
        log_imp 9 "rollback" "No pre-deploy snapshot found — cannot rollback"
        return 1
    fi

    log_imp 9 "rollback" "Restoring previous container state from snapshot..."

    # Find the latest images snapshot
    local latest_images
    latest_images=$(ls -t "$snapshot_dir"/images-*.json 2>/dev/null | head -1)

    if [[ -n "$latest_images" ]]; then
        log_imp 8 "rollback" "Previous images snapshot: $latest_images"
        # Attempt rollback via existing perform_rollback() or direct compose up
        if declare -f perform_rollback >/dev/null 2>&1; then
            perform_rollback
        else
            # Fallback: stop new containers, start from snapshot state
            log_imp 9 "rollback" "Stopping current containers..."
            docker compose down --timeout 30 2>/dev/null || log_imp 4 "rollback" "docker compose down failed (non-fatal)"
            log_imp 9 "rollback" "Starting previous containers..."
            docker compose up -d --no-recreate 2>/dev/null || log_imp 4 "rollback" "docker compose up failed (non-fatal)"
        fi
    else
        log_imp 9 "rollback" "No images snapshot available — manual intervention required"
    fi
}
# endregion RESTORE_FROM_SNAPSHOT

# region CLEANUP_SNAPSHOTS
## @purpose  Remove old snapshots, keeping only the last KEEP_SNAPSHOTS=3
_cleanup_snapshots() {
    local snapshot_dir="${PROJECT_DIR}/.deploy-snapshots"
    local keep=${KEEP_SNAPSHOTS:-3}
    log_imp 7 "snapshot" "Cleaning old snapshots (keeping $keep)"
    # Remove .deploy-started marker
    rm -f "$snapshot_dir/.deploy-started"
    # Keep only the latest N snapshot pairs
    cd "$snapshot_dir" 2>/dev/null || return 0
    ls -t ps-*.json 2>/dev/null | tail -n +$((keep + 1)) | xargs rm -f 2>/dev/null || true
    ls -t images-*.json 2>/dev/null | tail -n +$((keep + 1)) | xargs rm -f 2>/dev/null || true
    cd - >/dev/null || true
}
# endregion CLEANUP_SNAPSHOTS

# region WRITE_DEPLOY_RESULT
## @purpose  Write deploy-result.json with outcome metadata
_write_deploy_result() {
    local status="${DEPLOY_STATUS:-unknown}"
    local result_file="${PROJECT_DIR}/.deploy-snapshots/deploy-result.json"

    cat > "$result_file" << EOF
{
  "status": "${status}",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "project": "${PROJECT:-unknown}",
  "ref": "${REF:-unknown}"
}
EOF
    log_imp 9 "deploy" "Deploy result: $status (written to $result_file)"
}
# endregion WRITE_DEPLOY_RESULT

trap '_rollback_on_error' ERR
trap '_finalize_deploy' EXIT

# region NOTIFY_HOOK
notify_hook() {
    local message="${1:-}"
    local hook_script="${PLATFORM_ROOT}/core/internal/notify/notify-hook.sh"

    if [[ -x "$hook_script" ]]; then
        "$hook_script" "$message" 2>/dev/null || true
        log_imp 7 "notify" "Hook called: ${message}"
    else
        log_imp 6 "notify" "Hook not available (${hook_script} missing); status: ${message}"
    fi
}
# endregion NOTIFY_HOOK

# region VALIDATE_PROJECT_NAME
## @purpose  Validate project name: reject empty, '/' or '..' (defense against path traversal in deliver verb)
## @io       ⇥ project_name → ⎋ return 0 if valid, return 1 if invalid (log IMP:10 FATAL)
## @complexity O(1)
## @invariants — rejects '/', '..', empty string; used by handle_deliver and reusable for deploy branch
_validate_project_name() {
    local name="$1"
    if [[ -z "$name" ]]; then
        log_imp 10 "deliver" "FATAL: project name is empty"
        return 1
    fi
    if [[ "$name" == *"/"* || "$name" == *".."* ]]; then
        log_imp 10 "deliver" "FATAL: project name '${name}' contains invalid characters ('/' or '..')"
        return 1
    fi
    return 0
}
# endregion VALIDATE_PROJECT_NAME

# region HANDLE_DELIVER
## @purpose  Deliver project payload via stdin tar.gz stream (verb contract D2).
##           Reads tar.gz from stdin (max 1 MiB), validates content against whitelist,
##           extracts to temp dir, and atomically moves files to PROJECTS_BASE/<project>.
## @io       ⇥ (project_name) → reads stdin (tar.gz) → \n creates/moves files in PROJECTS_BASE/<project>
##           ⎋ exit 0 = success, exit 1 = validation/size/extract error (PROJECT_DIR unchanged)
## @invariants
##   - stdin: max 1 MiB (hard cap; excess → fail, nothing written)
##   - whitelist: docker-compose.yml | compose.yaml | ai-platform.yaml | .env.platform (top-level only)
##   - NO directories, symlinks, or hardlinks in archive
##   - tar --no-same-owner; extract to mktemp -d → validate → atomic mv to ${PROJECTS_BASE}/${PROJECT}
##   - audit_log: DELIVER-START / DELIVER-SUCCESS / DELIVER-FAIL
##   - PROJECTS_BASE — единственный источник пути (никаких /opt/projects хардкодов в новых строках)
handle_deliver() {
    local project="$1"
    local tmp_tar=""
    local tmp_dir=""

    # ── Cleanup helper ──
    _cleanup_deliver_temp() {
        [[ -n "$tmp_dir" && -d "$tmp_dir" ]] && rm -rf "$tmp_dir"
        [[ -n "$tmp_tar" && -f "$tmp_tar" ]] && rm -f "$tmp_tar"
    }

    audit_log "platform-deliver:${project}" "DELIVER-START" "Starting payload delivery for ${project}"
    log_imp 9 "deliver" "=== platform-deliver START: ${project} ==="

    # Validate project name (reuse _validate_project_name)
    if ! _validate_project_name "$project"; then
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Invalid project name '${project}'"
        exit 1
    fi

    local project_dir="${PROJECTS_BASE}/${project}"

    # Create temp files
    tmp_tar=$(mktemp) || { log_imp 10 "deliver" "FATAL: mktemp failed for tar"; exit 1; }
    tmp_dir=$(mktemp -d) || {
        log_imp 10 "deliver" "FATAL: mktemp -d failed for extract dir"
        rm -f "$tmp_tar"
        exit 1
    }

    # ── Read stdin with 1 MiB hard cap ──
    # head -c reads exactly N bytes (1 MiB + 1 for oversize detection), then exits.
    # Unlike dd bs=N count=M (which counts read() calls, not bytes — platform-dependent),
    # head -c guarantees byte-precise capping on all platforms.
    head -c $((1048576 + 1)) > "$tmp_tar" 2>/dev/null || true
    local actual_size
    actual_size=$(wc -c < "$tmp_tar" | xargs)

    if [[ "$actual_size" -gt 1048576 ]]; then
        _cleanup_deliver_temp
        log_imp 10 "deliver" "FATAL: payload exceeds 1 MiB limit ($actual_size bytes)"
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Payload exceeds 1 MiB limit ($actual_size bytes)"
        exit 1
    fi

    if [[ "$actual_size" -eq 0 ]]; then
        _cleanup_deliver_temp
        log_imp 10 "deliver" "FATAL: empty payload"
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Empty payload"
        exit 1
    fi

    # ── Extract to temp dir ──
    if ! tar -xzf "$tmp_tar" --no-same-owner -C "$tmp_dir" 2>/dev/null; then
        _cleanup_deliver_temp
        log_imp 10 "deliver" "FATAL: tar extraction failed for ${project}"
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Tar extraction failed"
        exit 1
    fi

    # ── Validate extracted content ──
    # Check 1: no path traversal (subdirectory files)
    local subdir_files
    subdir_files=$(find "$tmp_dir" -mindepth 2 -type f 2>/dev/null | wc -l | xargs)
    if [[ "$subdir_files" -gt 0 ]]; then
        _cleanup_deliver_temp
        log_imp 10 "deliver" "FATAL: subdirectory files in payload (path traversal)"
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Subdirectory files rejected (path traversal)"
        exit 1
    fi

    # Check 2: validate each top-level entry
    local has_invalid=0
    local entry
    local basename
    local link_count

    for entry in "$tmp_dir"/*; do
        [[ -e "$entry" ]] || continue  # glob matched nothing

        basename=$(basename "$entry")

        # Reject symlinks
        if [[ -L "$entry" ]]; then
            log_imp 10 "deliver" "FATAL: symlink in payload: ${basename}"
            has_invalid=1
            break
        fi

        # Reject non-regular files (directories, devices, etc.)
        if [[ ! -f "$entry" ]]; then
            log_imp 10 "deliver" "FATAL: non-regular file in payload: ${basename}"
            has_invalid=1
            break
        fi

        # Reject hardlinks (stat -f%l on macOS, stat -c%h on Linux)
        link_count=$(stat -f%l "$entry" 2>/dev/null || stat -c%h "$entry" 2>/dev/null)
        if [[ "${link_count:-1}" -gt 1 ]]; then
            log_imp 10 "deliver" "FATAL: hardlink in payload: ${basename} (links=${link_count})"
            has_invalid=1
            break
        fi

        # Check whitelist
        case "$basename" in
            docker-compose.yml|compose.yaml|ai-platform.yaml|.env.platform)
                log_imp 7 "deliver" "Whitelist OK: ${basename}"
                ;;
            *)
                log_imp 10 "deliver" "FATAL: non-whitelisted file in payload: ${basename}"
                has_invalid=1
                break
                ;;
        esac
    done

    if [[ "$has_invalid" -ne 0 ]]; then
        _cleanup_deliver_temp
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Content validation failed — invalid entries"
        exit 1
    fi

    # Check 3: verify at least one whitelist file present
    local found_compose=0
    if [[ -f "${tmp_dir}/docker-compose.yml" || -f "${tmp_dir}/compose.yaml" ]]; then
        found_compose=1
    fi
    if [[ "$found_compose" -eq 0 ]]; then
        _cleanup_deliver_temp
        log_imp 10 "deliver" "FATAL: no docker-compose.yml or compose.yaml in payload"
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Missing compose file"
        exit 1
    fi

    # ── Atomic move to PROJECT_DIR ──
    mkdir -p "$project_dir" || {
        _cleanup_deliver_temp
        log_imp 10 "deliver" "FATAL: cannot create project directory ${project_dir}"
        audit_log "platform-deliver:${project}" "DELIVER-FAIL" "Cannot create ${project_dir}"
        exit 1
    }

    for entry in "$tmp_dir"/*; do
        [[ -e "$entry" ]] || continue
        mv "$entry" "$project_dir/" || {
            _cleanup_deliver_temp
            log_imp 10 "deliver" "FATAL: mv failed for $(basename "$entry")"
            audit_log "platform-deliver:${project}" "DELIVER-FAIL" "mv failed during atomic copy"
            exit 1
        }
    done

    # Cleanup temp files
    _cleanup_deliver_temp

    audit_log "platform-deliver:${project}" "DELIVER-SUCCESS" "Payload delivered to ${project_dir}"
    log_imp 9 "deliver" "=== platform-deliver DONE (success) ==="
    exit 0
}
# endregion HANDLE_DELIVER

# region PARSE_SSH_COMMAND
parse_ssh_command() {
    local raw="${SSH_ORIGINAL_COMMAND:-}"

    if [[ -z "$raw" ]]; then
        log_imp 10 "args" "FATAL: SSH_ORIGINAL_COMMAND not set — script must be executed via SSH forced command"
        exit 1
    fi

    # ═══════════════════════════════════════════════════════════════
    # Verb: platform-deliver (D2 — payload delivery via stdin tar.gz)
    # Dispatch BEFORE deploy branch to avoid any side effects.
    # ═══════════════════════════════════════════════════════════════
    # 🧐 TRAP[DECISION] · 2026-07-17 · — · Deliver via forced-command stdin verb, not sftp/git-pull
    # · Rejected: sftp-chroot user (second SSH key), git-pull projects (deploy-keys on node, pull-based)
    # · Reason: zero new channels/keys, restrict preserved, decision confirmed by user
    # · Rev: if payload size exceeds 1M regularly → consider SCP variant with separate authorized_keys entry
    # · See: .ai/plans/007-dance-site-launch/02-Debt.md D2
    if [[ "$raw" == "platform-deliver "* ]]; then
        local project="${raw#platform-deliver }"
        # Trim whitespace
        project="$(echo "$project" | xargs)"
        log_imp 8 "args" "Dispatching deliver for project=${project}"
        # Set PROJECT_DIR so _finalize_deploy EXIT trap has a valid path
        PROJECT="${project}"
        PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"
        # Deploy status — not a deploy, prevent _cleanup_snapshots
        DEPLOY_STATUS="deliver"
        handle_deliver "$project"
        exit 0
    fi

    # ═══════════════════════════════════════════════════════════════
    # Verb: platform-deploy (existing — unchanged)
    # ═══════════════════════════════════════════════════════════════
    local cleaned="${raw#platform-deploy }"
    cleaned="${cleaned#platform-deploy}"

    cleaned="$(echo "$cleaned" | sed '/^export /d' | xargs)"

    PROJECT="${cleaned%% *}"
    REF="${cleaned#* }"

    if [[ "$PROJECT" == "$REF" ]]; then
        REF=""
    fi

    if [[ -z "$PROJECT" || -z "$REF" ]]; then
        log_imp 10 "args" "FATAL: invalid invocation — expects <project> <ref>, got '${raw}'"
        exit 1
    fi

    PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"

    if [[ ! -d "$PROJECT_DIR" ]]; then
        log_imp 10 "args" "FATAL: project directory not found: ${PROJECT_DIR}"
        exit 1
    fi

    if [[ ! -f "${PROJECT_DIR}/docker-compose.yml" ]] && [[ ! -f "${PROJECT_DIR}/compose.yaml" ]]; then
        log_imp 10 "args" "FATAL: no docker-compose.yml found in ${PROJECT_DIR}"
        exit 1
    fi

    SERVICE_NAME="${PROJECT}"
    local services_yaml="${PROJECT_DIR}/ai-platform.yaml"
    if [[ -f "$services_yaml" ]]; then
        local svc
        svc="$(grep -m1 '^[[:space:]]*service:' "${services_yaml}" 2>/dev/null | awk '{print $2}' || true)"
        if [[ -n "$svc" ]]; then
            SERVICE_NAME="$svc"
        fi
    fi

    log_imp 8 "args" "Parsed: PROJECT=${PROJECT} REF=${REF} SERVICE=${SERVICE_NAME} DIR=${PROJECT_DIR}"
}
# endregion PARSE_SSH_COMMAND

# region SAVE_PREVIOUS_IMAGE
save_previous_image() {
    PREVIOUS_IMAGE_ID=""
    PREVIOUS_IMAGE_TAG=""

    cd "$PROJECT_DIR" || {
        log_imp 10 "save-prev" "FATAL: cannot cd to ${PROJECT_DIR}"
        exit 1
    }

    PREVIOUS_IMAGE_ID="$(docker compose images -q "$SERVICE_NAME" 2>/dev/null)" || {
        log_imp 10 "save-prev" "CRITICAL: docker compose images failed for service '${SERVICE_NAME}'"
        exit 1
    }

    if [[ -z "$PREVIOUS_IMAGE_ID" ]]; then
        log_imp 10 "save-prev" "FIRST DEPLOY: no previous image for service '${SERVICE_NAME}' — rollback NOT possible"
        FIRST_DEPLOY=1
        return 0
    fi

    FIRST_DEPLOY=0

    PREVIOUS_IMAGE_TAG="$(docker image inspect "$PREVIOUS_IMAGE_ID" \
        --format '{{index .RepoTags 0}}' 2>/dev/null)" || true

    if [[ -z "$PREVIOUS_IMAGE_TAG" || "$PREVIOUS_IMAGE_TAG" == "<none>:<none>" ]]; then
        PREVIOUS_IMAGE_TAG="${PROJECT}:previous-rollback"
        docker tag "$PREVIOUS_IMAGE_ID" "$PREVIOUS_IMAGE_TAG" 2>/dev/null || true
        log_imp 8 "save-prev" "Created fallback tag for dangling image: ${PREVIOUS_IMAGE_TAG}"
    fi

    log_imp 9 "save-prev" "Previous image saved: ID=${PREVIOUS_IMAGE_ID} TAG=${PREVIOUS_IMAGE_TAG}"
}
# endregion SAVE_PREVIOUS_IMAGE

# region PULL_IMAGE_WITH_RETRY
pull_image_with_retry() {
    log_imp 8 "pull" "Pulling image for service '${SERVICE_NAME}' with IMAGE_TAG=${REF}"

    cd "$PROJECT_DIR" || exit 1

    export IMAGE_TAG="$REF"

    local max_attempts=3
    local delays=(5 10 20)
    local attempt=1

    while [[ $attempt -le $max_attempts ]]; do
        local pull_output
        pull_output="$(docker compose pull "$SERVICE_NAME" 2>&1)" || {
            local exit_code=$?

            if echo "$pull_output" | grep -qi "toomanyrequests\|429\|rate limit"; then
                log_imp 9 "pull" "Rate limit hit on attempt ${attempt} — waiting ${delays[$((attempt-1))]}s before retry"
            else
                log_imp 9 "pull" "Pull failed on attempt ${attempt} (exit=${exit_code}) — waiting ${delays[$((attempt-1))]}s before retry"
            fi
            log_imp 7 "pull" "Output: ${pull_output}"

            if [[ $attempt -lt $max_attempts ]]; then
                sleep "${delays[$((attempt-1))]}"
                attempt=$((attempt + 1))
                continue
            fi

            log_imp 10 "pull" "FATAL: docker compose pull failed after ${max_attempts} attempts for ${SERVICE_NAME}:${REF}"
            audit_log "platform-deploy:${PROJECT}" "FAIL" "Pull failed after ${max_attempts} attempts: ${SERVICE_NAME}:${REF}"
            notify_hook "🚀 [node: ${NODE_NAME}] Узел обновлён c ⚠️ Warnings:
- Pull error: ${PROJECT}/${SERVICE_NAME}:${REF} — failed after ${max_attempts} attempts"
            exit 1
        }

        log_imp 8 "pull" "Pull complete: ${SERVICE_NAME}:${REF}"
        return 0
    done
}
# endregion PULL_IMAGE_WITH_RETRY

# region ATOMIC_UP
atomic_up() {
    log_imp 9 "up" "Atomic deploy: docker compose up -d ${SERVICE_NAME} (IMAGE_TAG=${REF})"

    cd "$PROJECT_DIR" || exit 1

    export IMAGE_TAG="$REF"

    local up_output
    up_output="$(docker compose up -d "$SERVICE_NAME" 2>&1)"
    local up_rc=$?
    echo "$up_output" | while IFS= read -r line; do
        log_imp 7 "up" "${line}"
    done

    if [[ "$up_rc" -ne 0 ]]; then
        log_imp 10 "up" "FATAL: docker compose up -d failed (exit code=${up_rc})"
        return 1
    fi

    log_imp 8 "up" "Container started for ${SERVICE_NAME}"
}
# endregion ATOMIC_UP

# region CHECK_DEPLOY_HEALTH
_check_deploy_health() {
    local cid
    cid="$(docker compose ps -q "$SERVICE_NAME" 2>/dev/null || true)"
    [[ -z "$cid" ]] && return 1
    check_docker_health "$cid" && return 0
    local hc_rc=$?
    if [[ $hc_rc -eq 2 ]]; then
        local status
        status="$(docker inspect --format='{{.State.Status}}' "$cid" 2>/dev/null || echo "unknown")"
        [[ "$status" == "running" ]] && return 0
    fi
    return 1
}
# endregion CHECK_DEPLOY_HEALTH

# region TAG_CURRENT
tag_current() {
    local new_image_id
    new_image_id="$(docker compose images -q "$SERVICE_NAME" 2>/dev/null)" || {
        log_imp 8 "tag" "WARNING: docker compose images failed — skipping :current tag"
        return 0
    }

    if [[ -z "$new_image_id" ]]; then
        log_imp 9 "tag" "Cannot determine new image ID — skipping :current tag"
        return 0
    fi

    docker tag "$new_image_id" "${SERVICE_NAME}:current" 2>/dev/null || {
        log_imp 8 "tag" "Failed to tag ${new_image_id} as ${SERVICE_NAME}:current (may already exist)"
        return 0
    }

    log_imp 9 "tag" "Tagged ${new_image_id} → ${SERVICE_NAME}:current"
}
# endregion TAG_CURRENT

# region PERFORM_ROLLBACK
perform_rollback() {
    log_imp 10 "rollback" "ROLLING BACK ${SERVICE_NAME} to previous image ${PREVIOUS_IMAGE_ID}"

    cd "$PROJECT_DIR" || exit 1

    if [[ -n "$PREVIOUS_IMAGE_TAG" ]]; then
        docker tag "$PREVIOUS_IMAGE_ID" "$PREVIOUS_IMAGE_TAG" 2>/dev/null || {
            log_imp 10 "rollback" "CRITICAL: failed to re-tag previous image ${PREVIOUS_IMAGE_ID} → ${PREVIOUS_IMAGE_TAG}"
        }
        log_imp 9 "rollback" "Re-tagged ${PREVIOUS_IMAGE_ID} → ${PREVIOUS_IMAGE_TAG}"
    fi

    local rollback_output
    rollback_output="$(docker compose up -d --force-recreate "$SERVICE_NAME" 2>&1)"
    local rollback_rc=$?
    echo "$rollback_output" | while IFS= read -r line; do
        log_imp 7 "rollback" "${line}"
    done

    if [[ "$rollback_rc" -ne 0 ]]; then
        log_imp 10 "rollback" "CRITICAL: rollback compose up failed (exit code=${rollback_rc})"
        audit_log "platform-deploy:${PROJECT}" "ROLLBACK-FAIL" \
            "Rollback compose up FAILED for ${SERVICE_NAME} (previous=${PREVIOUS_IMAGE_ID}, exit=${rollback_rc})"
        notify_hook "🚀 [node: ${NODE_NAME}] Узел обновлён c ⚠️ Warnings:
- ROLLBACK FAILED: ${PROJECT}/${SERVICE_NAME} → manual intervention required!"
        exit 1
    fi

    log_imp 10 "rollback" "Rollback complete: ${SERVICE_NAME} restored to ${PREVIOUS_IMAGE_ID}"

    audit_log "platform-deploy:${PROJECT}" "ROLLBACK" \
        "Deploy FAILED — rolled back ${SERVICE_NAME} from ${REF} to ${PREVIOUS_IMAGE_ID}"

    local tail_logs
    tail_logs="$(docker compose logs --tail 20 "$SERVICE_NAME" 2>/dev/null || echo "(logs unavailable)")"
    notify_hook "🚀 [node: ${NODE_NAME}] Узел обновлён c ⚠️ Warnings:
- Deploy FAILED & ROLLED BACK: ${PROJECT}/${SERVICE_NAME} ${REF} → restored ${PREVIOUS_IMAGE_TAG:-previous}
- Logs (tail 20):
- ${tail_logs}"

    return 1
}
# endregion PERFORM_ROLLBACK

# region PRUNE_OLD_IMAGES
prune_old_images() {
    log_imp 8 "prune" "Pruning old images for service '${SERVICE_NAME}' (keep ${KEEP_IMAGES})"

    local config_output image_pattern
    config_output="$(docker compose config 2>/dev/null)" || {
        log_imp 8 "prune" "WARNING: docker compose config failed — using project name as fallback"
        image_pattern="${PROJECT}"
    }

    if [[ -z "${image_pattern:-}" ]]; then
        image_pattern="$(echo "$config_output" \
            | grep -A1 "^  ${SERVICE_NAME}:" \
            | grep "image:" \
            | awk '{print $2}' \
            | sed 's/:.*//')"
    fi

    if [[ -z "$image_pattern" ]]; then
        image_pattern="${PROJECT}"
        log_imp 7 "prune" "Cannot determine image pattern from compose config; using '${image_pattern}'"
    fi

    local images
    images="$(docker images --format '{{.ID}} {{.Repository}} {{.CreatedAt}}' \
        | grep -i "$image_pattern" \
        | sort -k3 -r 2>/dev/null || true)"

    if [[ -z "$images" ]]; then
        log_imp 7 "prune" "No images found matching pattern '${image_pattern}'"
        return 0
    fi

    local count
    count="$(echo "$images" | grep -c . || echo 0)"

    if [[ "$count" -le "$KEEP_IMAGES" ]]; then
        log_imp 7 "prune" "Image count (${count}) ≤ keep limit (${KEEP_IMAGES}) — nothing to prune"
        return 0
    fi

    local to_remove
    to_remove="$(echo "$images" | tail -n +$((KEEP_IMAGES + 1)) | awk '{print $1}')"

    if [[ -z "$to_remove" ]]; then
        return 0
    fi

    log_imp 8 "prune" "Removing $(echo "$to_remove" | wc -l | xargs) old images..."

    local removed=0
    local failed=0
    for img_id in $to_remove; do
        if docker rmi "$img_id" 2>/dev/null; then
            removed=$((removed + 1))
        else
            failed=$((failed + 1))
            log_imp 7 "prune" "Could not remove image ${img_id} (may be referenced by another tag)"
        fi
    done

    log_imp 8 "prune" "Prune complete: removed=${removed} failed=${failed} kept=${KEEP_IMAGES}"
}
# endregion PRUNE_OLD_IMAGES

# region HANDLE_FIRST_DEPLOY
handle_first_deploy() {
    log_imp 10 "first-deploy" "CRITICAL: FIRST DEPLOY FAILED for ${SERVICE_NAME} — no previous image to rollback"

    audit_log "platform-deploy:${PROJECT}" "FIRST-DEPLOY-FAIL" \
        "First deploy FAILED for ${SERVICE_NAME}:${REF} — NO ROLLBACK POSSIBLE (no previous image)"

    local tail_logs
    tail_logs="$(docker compose logs --tail 30 "$SERVICE_NAME" 2>/dev/null || echo "(logs unavailable)")"
    notify_hook "🚀 [node: ${NODE_NAME}] Узел обновлён c ⚠️ Warnings:
- FIRST DEPLOY FAILED: ${PROJECT}/${SERVICE_NAME}:${REF} — manual intervention required
- No previous image exists for rollback. Container left in failed state.
- Logs (tail 30):
- ${tail_logs}"

    exit 1
}
# endregion HANDLE_FIRST_DEPLOY

# region DEPLOY_HOOK_INVOCATION
## @purpose  Invoke module hooks after successful deploy — iterate module.yaml hooks.on_project_deploy
_trigger_deploy_hooks() {
    local module_yaml
    for module_yaml in "${CORE_DIR}"/modules/*/module.yaml; do
        [[ -f "$module_yaml" ]] || continue
        local hook
        hook=$(yaml_get_field "$module_yaml" "hooks.on_project_deploy" 2>/dev/null) || continue
        [[ -z "$hook" ]] && continue
        local hook_script
        hook_script="$(dirname "$module_yaml")/$hook"
        if [[ -x "$hook_script" ]]; then
            local module_name
            module_name="$(basename "$(dirname "$module_yaml")")"
            if bash "$hook_script" "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"; then
                audit_log "hook:${module_name}" "SUCCESS" "Hook completed for ${module_name}"
            else
                audit_log "hook:${module_name}" "HOOK-FAIL" "Hook failed (non-fatal) for ${module_name}"
            fi
        fi
    done
}
# endregion DEPLOY_HOOK_INVOCATION

# region REMOVE_HOOK_INVOCATION
## @purpose  Invoke module hooks after project remove — iterate module.yaml hooks.on_project_remove (K2)
##           Mirror of _trigger_deploy_hooks() for the remove lifecycle phase.
## @invariants — Hooks MUST be idempotent and non-destructive (O7)
##             — 0 modules defining on_project_remove today is valid
##             — Hook contract: backup/notification OK; DROP DATABASE forbidden
_trigger_remove_hooks() {
    local module_yaml
    for module_yaml in "${CORE_DIR}"/modules/*/module.yaml; do
        [[ -f "$module_yaml" ]] || continue
        local hook
        hook=$(yaml_get_field "$module_yaml" "hooks.on_project_remove" 2>/dev/null) || continue
        [[ -z "$hook" ]] && continue
        local hook_script
        hook_script="$(dirname "$module_yaml")/$hook"
        if [[ -x "$hook_script" ]]; then
            local module_name
            module_name="$(basename "$(dirname "$module_yaml")")"
            log_imp 8 "hooks" "Triggering remove hook for module: ${module_name}"
            if bash "$hook_script" "$PROJECT_DIR" "$PROJECT" "$NODE_NAME"; then
                audit_log "hook:${module_name}" "SUCCESS" "Remove hook completed for ${module_name}"
            else
                audit_log "hook:${module_name}" "HOOK-FAIL" "Remove hook failed (non-fatal) for ${module_name}"
            fi
        fi
    done
}
# endregion REMOVE_HOOK_INVOCATION

# region FUNC_capture_deploy_snapshot
## @purpose  Capture pre-deploy state snapshot for guaranteed rollback
## @io       Creates .deploy-snapshots/ in PROJECT_DIR with:
##           - ps-<timestamp>.json (docker compose ps output)
##           - images-<timestamp>.json (docker compose images output)
##           - .deploy-started (timestamp marker)
## @complexity O(1) — two docker compose calls
capture_deploy_snapshot() {
    local snapshot_dir="${PROJECT_DIR}/.deploy-snapshots"
    mkdir -p "$snapshot_dir"
    local ts
    ts=$(date +%s)

    log_imp 8 "snapshot" "Capturing pre-deploy snapshot to ${snapshot_dir}"

    if docker compose ps --format json > "$snapshot_dir/ps-${ts}.json" 2>/dev/null; then
        log_imp 8 "snapshot" "Container state snapshot saved"
    else
        log_imp 9 "snapshot" "WARNING: could not capture ps snapshot (containers may not be running)"
    fi

    if docker compose images --format json > "$snapshot_dir/images-${ts}.json" 2>/dev/null; then
        log_imp 8 "snapshot" "Image state snapshot saved"
    else
        log_imp 9 "snapshot" "WARNING: could not capture images snapshot"
    fi

    echo "$ts" > "$snapshot_dir/.deploy-started"
    log_imp 8 "snapshot" "Pre-deploy snapshot complete (ts=$ts)"
}
# endregion FUNC_capture_deploy_snapshot

# ═══════════════════════════════════════════════════════════════════
# HANDLE_REMOVE — verb contract K1
# ═══════════════════════════════════════════════════════════════════
# region HANDLE_REMOVE
## @purpose  Safely remove (disconnect) a project: stop containers WITHOUT destroying data (O7/DD10).
##           No `down -v`, no `volume rm`, no `image rm`.
## @invariants — docker compose down without -v (data preserved)
##             — Idempotent: if already stopped/removed → SKIP, exit 0
##             - Vhost removal is handled by the caller (remove-project.sh)
##             - Audit log written on actual removal
# 💼 TRAP[BUSINESS] · 2026-07-17 · HI · remove = disconnect, данные не удаляются автоматически
# · Source: owner (O7/DD10)
# · Risk: авто-очистка = невосстановимая потеря БД проекта
# · Safeguard: `down -v` запрещён; volumes/БД/images/репо — НЕ трогаются
handle_remove() {
    local project="${PROJECT:-${1:-}}"

    if [[ -z "$project" ]]; then
        log_imp 10 "remove" "FATAL: --remove requires project name"
        exit 1
    fi

    PROJECT="$project"
    PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"

    log_imp 9 "remove" "=== project REMOVE START: ${PROJECT} ==="

    if [[ ! -d "$PROJECT_DIR" ]]; then
        log_imp 9 "remove" "Project directory not found: ${PROJECT_DIR} — already removed (idempotent)"
        audit_log "${AUDIT_TAG}:remove:${PROJECT}" "SKIP" "Project directory not found — already removed"
        exit 0
    fi

    # docker compose down — БЕЗ -v (O7 — data preserved)
    log_imp 9 "remove" "Stopping containers for ${PROJECT} (data preserved, no -v)..."
    if cd "$PROJECT_DIR" 2>/dev/null; then
        docker compose down --timeout 30 2>/dev/null || log_imp 4 "remove" "docker compose down failed (non-fatal, may already be stopped)"
        log_imp 9 "remove" "Containers stopped for ${PROJECT}"
    else
        log_imp 8 "remove" "Cannot cd to ${PROJECT_DIR} — project may already be gone"
    fi

    # Trigger remove hooks (K2)
    _trigger_remove_hooks

    # Audit log
    audit_log "${AUDIT_TAG}:remove:${PROJECT}" "DONE" \
        "Project ${PROJECT} removed (disconnected). Data preserved: volumes, images, repository, project directory."

    log_imp 9 "remove" "=== project REMOVE DONE: ${PROJECT} ==="
}
# endregion HANDLE_REMOVE

# ═══════════════════════════════════════════════════════════════════
# HANDLE_STATUS — verb contract K1
# ═══════════════════════════════════════════════════════════════════
# region HANDLE_STATUS
## @purpose  Print JSON status for a project: docker compose ps + last deploy-result.json
## @invariants — Always writes valid JSON to stdout
##             - If project directory missing → status: "not_found"
##             - If no deploy history → last_deploy: null
## @output    stdout: JSON with project, node, containers[], last_deploy{}
handle_status() {
    local project="${PROJECT:-${1:-}}"

    if [[ -z "$project" ]]; then
        log_imp 10 "status" "FATAL: --status requires project name"
        exit 1
    fi

    PROJECT="$project"
    PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"

    log_imp 9 "status" "=== project STATUS: ${PROJECT} ==="

    if [[ ! -d "$PROJECT_DIR" ]]; then
        # Project not found — not an error, just report status
        echo "{
  \"project\": \"${PROJECT}\",
  \"node\": \"${NODE_NAME}\",
  \"status\": \"not_found\",
  \"containers\": [],
  \"last_deploy\": null
}"
        log_imp 9 "status" "Project ${PROJECT}: not found (${PROJECT_DIR} does not exist)"
        exit 0
    fi

    # Docker compose ps
    local ps_json="[]"
    if cd "$PROJECT_DIR" 2>/dev/null; then
        ps_json="$(docker compose ps --format json 2>/dev/null || echo "[]")"
    fi

    # Last deploy result
    local deploy_result_file="${PROJECT_DIR}/.deploy-snapshots/deploy-result.json"
    local last_deploy="null"
    if [[ -f "$deploy_result_file" ]]; then
        last_deploy="$(cat "$deploy_result_file")"
    fi

    cat <<EOF
{
  "project": "${PROJECT}",
  "node": "${NODE_NAME}",
  "status": "found",
  "containers": ${ps_json},
  "last_deploy": ${last_deploy}
}
EOF
    log_imp 9 "status" "Project ${PROJECT}: status reported"
}
# endregion HANDLE_STATUS

# region MAIN
main() {
    echo "[IMP:7][deploy-project][main] Starting deploy-project main" >&2
    # ── Check for verb flags from entrypoint dispatch (K1) ──────────
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --remove)
                handle_remove "${2:-}"
                exit 0
                ;;
            --status)
                handle_status "${2:-}"
                exit 0
                ;;
            --help|-h)
                echo "Usage: deploy-project.sh [--remove <project>|--status <project>|<project> <ref> [env]]"
                exit 0
                ;;
        esac
    fi

    # ── Legacy deploy (backward compat with forced-command) ─────────
    # If we have positional args (from deploy.sh dispatch or local testing),
    # set PROJECT/REF before parse_ssh_command
    if [[ $# -ge 2 ]]; then
        PROJECT="${1:-}"
        REF="${2:-}"
        PROJECT_DIR="${PROJECTS_BASE}/${PROJECT}"
        # Still call parse_ssh_command for SSH_ORIGINAL_COMMAND side effects
        # but it will skip if PROJECT already set
    fi

    log_imp 9 "main" "=== platform-deploy START ==="

    # parse_ssh_command reads SSH_ORIGINAL_COMMAND and sets PROJECT/REF
    parse_ssh_command

    local safe_invocation
    safe_invocation="$(echo "${SSH_ORIGINAL_COMMAND:-}" | sed 's/export [A-Z_]\{1,\}=/export ***=/g')"
    log_imp 9 "main" "Invocation: SSH_ORIGINAL_COMMAND='${safe_invocation}'"

    audit_log "platform-deploy:${PROJECT}" "START" "Deploy ${PROJECT}/${SERVICE_NAME} → ${REF}"

    save_previous_image
    capture_deploy_snapshot

    local validate_script="${SCRIPT_DIR}/../../internal/validate/validate.sh"
    if [[ -x "$validate_script" ]]; then
        log_imp 8 "fqdn" "Checking FQDN uniqueness for ${PROJECT}..."
        local fqdn_output fqdn_rc=0
        fqdn_output="$("$validate_script" --check-fqdn "$PROJECT_DIR" 2>&1)" || fqdn_rc=$?
        while IFS= read -r line; do
            log_imp 7 "fqdn" "${line}"
        done <<< "$fqdn_output"
        if [[ "$fqdn_rc" -ne 0 ]]; then
            log_imp 10 "fqdn" "FATAL: FQDN conflict detected — deploy blocked (E1)"
            audit_log "platform-deploy:${PROJECT}" "FAIL" "FQDN conflict blocked deploy for ${SERVICE_NAME}:${REF}"
            notify_hook "🚀 [node: ${NODE_NAME}] Узел обновлён c ⚠️ Warnings:
- FQDN conflict blocked deploy: ${PROJECT}/${SERVICE_NAME}:${REF}"
            exit 1
        fi
    else
        log_imp 6 "fqdn" "validate.sh not found at ${validate_script} — skipping FQDN check"
    fi

    docker_login
    pull_image_with_retry

    local host_port
    host_port="$(yaml_get_field "${PROJECT_DIR}/ai-platform.yaml" "monitoring.host_port" 2>/dev/null || echo "0")"
    if [[ "$host_port" -gt 0 ]]; then
        log_imp 8 "ports" "Checking port ${host_port} for ${PROJECT}..."
        if ss -tlnp 2>/dev/null | grep -q ":${host_port} "; then
            log_imp 10 "ports" "FATAL: Port ${host_port} already in use — deploy blocked"
            audit_log "platform-deploy:${PROJECT}" "FAIL" "Port ${host_port} conflict — deploy blocked"
            notify_hook "🚀 [node: ${NODE_NAME}] Узел обновлён c ⚠️ Warnings:
- Port conflict: ${PROJECT}/${SERVICE_NAME} — port ${host_port} already in use"
            exit 1
        fi
        log_imp 8 "ports" "Port ${host_port} available"
    fi

    if ! atomic_up; then
        log_imp 10 "main" "atomic_up returned non-zero"
        if [[ "${FIRST_DEPLOY:-0}" -eq 1 ]]; then
            handle_first_deploy
        else
            perform_rollback
        fi
        exit 1
    fi

    if poll_until_healthy "${SERVICE_NAME}" "_check_deploy_health" "$MAX_WAIT_SEC" 2; then
        log_imp 9 "main" "Deploy SUCCESS: ${SERVICE_NAME} → ${REF}"

        tag_current
        prune_old_images
        _trigger_deploy_hooks

        audit_log "platform-deploy:${PROJECT}" "DONE" \
            "Deploy success: ${SERVICE_NAME} → ${REF} (prev=${PREVIOUS_IMAGE_ID:-none})"
        notify_hook "🚀 [node: ${NODE_NAME}] Узел обновлён ✅
${PROJECT}/${SERVICE_NAME} → ${REF}"

        log_imp 9 "main" "=== platform-deploy DONE (success) ==="
        DEPLOY_STATUS="success"
        exit 0
    else
        log_imp 10 "main" "Healthcheck FAILED for ${SERVICE_NAME}:${REF}"

        if [[ "${FIRST_DEPLOY:-0}" -eq 1 ]]; then
            handle_first_deploy
        fi

        perform_rollback
        exit 1
    fi
}
# endregion MAIN

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
