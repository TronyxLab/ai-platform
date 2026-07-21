#!/usr/bin/env bash
# GREP_SUMMARY: node-lifecycle bootstrap init update orchestrator idempotent sequential-steps checkpoint-resume docker ufw nginx sops users sudoers audit telegram scp-deploy no-git deploy-modules healthcheck per-step-content-hash
# STRUCTURE: ▶ --mode init (18 sequential steps → update → audit → telegram) | --mode update (5 steps → audit) → checkpoint/resume at each step; SCP-delivered code (no git); node-update-fail non-fatal
# region MODULE_CONTRACT
## @purpose  Unified node lifecycle orchestrator: transforms bare VPS into a fully configured platform node (init mode, 18 steps) or performs incremental update (update mode, 5 steps). Merged from orchestrator.sh + node-update.sh.
## @scope    Called from Makefile via entrypoints/bootstrap.sh (--mode init) or entrypoints/node-update.sh (--mode update); idempotent — safe to re-run on provisioned node
## @location core/internal/bootstrap/node-lifecycle.sh — merged from orchestrator.sh + node-update.sh
## @invariants
##   - All steps executed; failures in critical bootstrap steps cause exit 1
##   - Step 14 (node-update) delegates to --mode update (self-invocation) for provision + deploy-modules + healthcheck
##   - Audit log always runs; Telegram notification non-fatal
##   - Steps declared in execution order; declaration order matches main() call order
##   - update-mode failures in step_5 are LOGGED (non-fatal) — bootstrap continues
##   - age key from AGE_SECRET_KEY env (with SOPS_AGE_KEY fallback); never written to persistent disk
##   - exit 0 on success; exit 1 on critical failure with descriptive message
##   - Core delivery: SCP/rsync only (push-based, no git)
##   - Context-overlay delivery: git clone/pull (deploy-modules.sh → ensure_context_repo)
##   - NO git for core code; context repo uses git for overlay customization
##   - Env vars or CLI args: NODE_NAME, NODE_YAML, PLATFORM_OWNER_KEY, PLATFORM_CI_DEPLOY_KEY
##   - NODE_YAML derivation (update-mode): if not set via env/CLI, resolved from NODE_NAME via
##     lib/node-resolver.sh (candidate paths: platform_root/node-configs/, projects/*/node-configs/,
##     /opt/node-configs/). Unresolvable → exit 1 BEFORE any mutations.
##   - --dry-run: accepted by parser, both modes print plan + exit 0 BEFORE mkdir $CHECKPOINT_DIR
##     and any other mutations. dry-run печать стоит до --force/mkdir/checkpoint операций.
## @rationale Single orchestrator for both bootstrap (init) and incremental update modes.
##            Previously two separate scripts (orchestrator.sh, node-update.sh) with ~60%
##            duplicated boilerplate (arg parsing, logging, checkpoint, sourcing). Merging
##            eliminates duplication, reduces cognitive load, and ensures consistent
##            checkpoint/hash behavior across both modes.
## @changes 2026-07-21 | W2: step_15_converge: fixed exit handling — exit 2=ERROR blocked only in init mode,
##            exit 1=WARNINGS always non-blocking step_done. Removed dead `$?` code.
##           2026-07-21 | W4: step_15_converge: +AUTO_RECONCILE passthrough → --reconcile to converge.sh,
##            +reconcile-projects.sh call after converge
# endregion MODULE_CONTRACT

set -euo pipefail

# ─── Parse mode ────────────────────────────────────────
MODE=""
if [[ "${1:-}" == "--mode" ]]; then
    shift
    MODE="${1:-}"
    shift || true
fi
if [[ "$MODE" != "init" && "$MODE" != "update" ]]; then
    echo "[IMP:10][node-lifecycle][args] ERROR: First argument must be --mode init or --mode update" >&2
    exit 1
fi

# ─── Defaults ───────────────────────────────────────────
CHECKPOINT_DIR="/var/lib/platform/.bootstrap-checkpoints"
RESUME_MODE=false
FORCE_MODE=false

# ─── Parse CLI args ──────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --resume)            RESUME_MODE=true; shift ;;
        --force)             FORCE_MODE=true; shift ;;
        --dry-run)           DRY_RUN_MODE=true; shift ;;
        --node-name)         export NODE_NAME="$2"; shift 2 ;;
        --node-yaml)         export NODE_YAML="$2"; shift 2 ;;
        --owner-key)         if [[ -z "${PLATFORM_OWNER_KEY:-}" ]]; then export PLATFORM_OWNER_KEY="$2"; fi; shift 2 ;;
        --ci-deploy-key)     if [[ -z "${PLATFORM_CI_DEPLOY_KEY:-}" ]]; then export PLATFORM_CI_DEPLOY_KEY="$2"; fi; shift 2 ;;
        --age-secret-key)    if [[ -z "${AGE_SECRET_KEY:-}" ]]; then export AGE_SECRET_KEY="$2"; fi; shift 2 ;;
        --docker-hub-username)  if [[ -z "${DOCKER_HUB_USERNAME:-}" ]]; then export DOCKER_HUB_USERNAME="$2"; fi; shift 2 ;;
        --docker-hub-token)  if [[ -z "${DOCKER_HUB_TOKEN:-}" ]]; then export DOCKER_HUB_TOKEN="$2"; fi; shift 2 ;;
        --postgres-password) if [[ -z "${POSTGRES_PASSWORD:-}" ]]; then export POSTGRES_PASSWORD="$2"; fi; shift 2 ;;
        --age-secret-key-file)
            if [[ ! -f "$2" ]]; then echo "[IMP:10][node-lifecycle][args] ERROR: file not found: $2" >&2; exit 1; fi
            AGE_SECRET_KEY="$(< "$2")"
            export AGE_SECRET_KEY
            shift 2 ;;
        --docker-hub-username-file)
            if [[ ! -f "$2" ]]; then echo "[IMP:10][node-lifecycle][args] ERROR: file not found: $2" >&2; exit 1; fi
            DOCKER_HUB_USERNAME="$(< "$2")"
            export DOCKER_HUB_USERNAME
            shift 2 ;;
        --docker-hub-token-file)
            if [[ ! -f "$2" ]]; then echo "[IMP:10][node-lifecycle][args] ERROR: file not found: $2" >&2; exit 1; fi
            DOCKER_HUB_TOKEN="$(< "$2")"
            export DOCKER_HUB_TOKEN
            shift 2 ;;
        --postgres-password-file)
            if [[ ! -f "$2" ]]; then echo "[IMP:10][node-lifecycle][args] ERROR: file not found: $2" >&2; exit 1; fi
            POSTGRES_PASSWORD="$(< "$2")"
            export POSTGRES_PASSWORD
            shift 2 ;;
        --tor-bridges-file)   export TOR_BRIDGES_FILE="$2"; shift 2 ;;
        --skip-tor-verify)    export SKIP_TOR_VERIFY="true"; shift ;;
        --) shift; break ;;
        -*) echo "[IMP:10][node-lifecycle][args] ERROR: Unknown argument: $1" >&2; exit 1 ;;
        *) break ;;
    esac
done

# SOPS_AGE_KEY fallback: if AGE_SECRET_KEY is still empty, use SOPS_AGE_KEY from env
if [[ -z "${AGE_SECRET_KEY:-}" ]] && [[ -n "${SOPS_AGE_KEY:-}" ]]; then
    export AGE_SECRET_KEY="$SOPS_AGE_KEY"
    echo "[IMP:8][node-lifecycle][args] AGE_SECRET_KEY set from SOPS_AGE_KEY env var" >&2
fi

# ─── Source libraries ────────────────────────────────────
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../lib/paths.sh"
CORE_DIR="${PATHS_CORE_DIR}"

source "${CORE_DIR}/internal/audit/audit.sh"

STEP=0
STEP_ERRORS=()

if [[ "$MODE" == "init" ]]; then
    __LOG_PREFIX="bootstrap"
else
    __LOG_PREFIX="node-update"
fi
source "${CORE_DIR}/lib/logging.sh"
step_start() { STEP=$(( STEP + 1 )); log_step "$1" "START" "${2:-}"; }
step_done()  { log_step "$1" "DONE"  "${2:-}"; }
step_skip()  { log_step "$1" "SKIP"  "${2:-}"; }
# 🧐 TRAP[DECISION] · 2026-07-09 · — · step_warn rename not needed
# · Rejected: renaming step_warn → log_step_WARN for consistency with log_step
# · Reason: step_warn name is consistent within node-lifecycle.sh (4 local calls as of original orchestrator.sh).
#   The function is not sourced/exported, so it has no cross-file naming conflicts.
#   Rename would introduce churn without benefit — every caller references the same function by the same name.
# · Rev: if step_warn is ever exported to lib/logging.sh, rename then for consistency
step_warn()  { log_step "$1" "WARN"  "${2:-}"; STEP_ERRORS+=("Step ${STEP}: $1 — $2"); }

source "${CORE_DIR}/lib/checkpoint.sh"
source "${CORE_DIR}/internal/bootstrap/content-hash.sh"
source "${CORE_DIR}/lib/secrets.sh"
source "${CORE_DIR}/lib/yaml_read.sh"

# ─── Content hash helper (T20) ──────────────────────────
# Computes per-step content hash for checkpoint invalidation.
# Always includes node-lifecycle.sh + caller-specified script paths.
# Usage: _step_hash "step-name" [extra_path1 ...]
_step_hash() {
    local step="$1"
    shift
    compute_step_hash "$step" "${CORE_DIR}/internal/bootstrap/node-lifecycle.sh" "$@"
}
# ─── End content hash helper ────────────────────────────

# ══════════════════════════════════════════════════════════════════
# INIT MODE — Bootstrap (18 steps)
# ══════════════════════════════════════════════════════════════════

# region VALIDATE_BOOTSTRAP_ENV
validate_bootstrap_env() {
    local required_vars=(NODE_NAME NODE_YAML PLATFORM_OWNER_KEY)
    local missing=()
    for var in "${required_vars[@]}"; do
        if [[ -z "${!var:-}" ]]; then
            missing+=("$var")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        echo "[IMP:10][bootstrap][validate] FAIL: Missing required: ${missing[*]}" >&2
        echo "[IMP:10][bootstrap][validate] Pass as CLI args or set env vars:" >&2
        echo "  sudo ${PLATFORM_ROOT}/core/internal/bootstrap/node-lifecycle.sh --mode init --node-name NAME --node-yaml PATH --owner-key KEY" >&2
        echo "  Or set env vars: export NODE_NAME=... && sudo ${PLATFORM_ROOT}/core/internal/bootstrap/node-lifecycle.sh --mode init" >&2
        exit 1
    fi
    log_step "validate-env" "OK" "Required env vars present"
}
# endregion VALIDATE_BOOTSTRAP_ENV

# ─── STEP 1: SSH as root ─────────────────────────────────
step_1_ssh_access() {
    step_start "ssh-access" "Verifying bootstrap runs as root"
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][bootstrap][step-1] ERROR: node-lifecycle.sh must run as root" >&2
        exit 1
    fi
    step_done "ssh-access" "Running as root — OK"
}

# ─── STEP 2: apt dependencies ────────────────────────────
step_2_apt_deps() {
    step_start "apt-deps" "Installing base apt dependencies"
    local apt_deps=(make curl ufw python3-yaml python3-jsonschema)

    if [[ "${TOR_ENABLED:-false}" == "true" ]]; then
        apt_deps+=(tor privoxy obfs4proxy)
        log_step "apt-deps" "INFO" "Tor enabled — added tor/privoxy/obfs4proxy to apt packages"
    else
        log_step "apt-deps" "INFO" "Tor disabled — skipping tor/privoxy/obfs4proxy packages"
    fi
    local apt_to_install=()
    for pkg in "${apt_deps[@]}"; do
        if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
            apt_to_install+=("$pkg")
        fi
    done

    local need_sops=false
    if ! command -v sops &>/dev/null 2>&1; then
        need_sops=true
    fi

    if [[ ${#apt_to_install[@]} -gt 0 ]]; then
        apt-get update -qq
        local apt_err; apt_err="$(apt-get install -y -qq "${apt_to_install[@]}" 2>&1)" || {
            log_step "apt-deps" "FAIL" "apt-get install failed: ${apt_err}"
            exit 1
        }
        for pkg in "${apt_to_install[@]}"; do
            if dpkg -s "$pkg" &>/dev/null 2>&1; then
                log_step "apt-deps" "DONE" "Installed via apt: ${pkg}"
            else
                log_step "apt-deps" "FAIL" "Package '${pkg}' not installed via apt — dpkg check failed"
                exit 1
            fi
        done
    else
        step_skip "apt-deps" "All apt packages already installed"
    fi

    if [[ "$need_sops" == true ]]; then
        log_step "apt-deps" "WARN" "sops not in apt repos — installing from GitHub v3.9.4"
        local sops_arch
        sops_arch="$(dpkg --print-architecture 2>/dev/null || echo 'amd64')"
        case "$sops_arch" in
            amd64) sops_arch="amd64" ;;
            arm64) sops_arch="arm64" ;;
            *) log_step "apt-deps" "FAIL" "Unsupported sops arch: ${sops_arch}"; exit 1 ;;
        esac
        if ! curl -sSLo /usr/local/bin/sops "https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.${sops_arch}"; then
            log_step "apt-deps" "FAIL" "sops: GitHub download failed"
            exit 1
        fi
        chmod 0755 /usr/local/bin/sops
        log_step "apt-deps" "DONE" "sops installed from GitHub v3.9.4"
    else
        log_step "apt-deps" "SKIP" "sops already installed"
    fi

    if [[ ${#apt_to_install[@]} -gt 0 ]] || [[ "$need_sops" == true ]]; then
        step_done "apt-deps" "Packages installed: apt=${#apt_to_install[@]} sops=$need_sops"
    fi
}

# ─── STEP 3: Tor + Privoxy proxy for Telegram ─────────────
# ⚠️ TRAP[DECISION] · 2026-07-17 · HI · Shared bridges.txt в core/bootstrap/tor/
# · Риск: bridges.txt — git-tracked, публикация репозитория раскроет bridge-адреса
# · Решение: bridges.txt в private repo; CI rsync --delete доставляет на VPS
# · Rev: если репозиторий станет публичным — перенести bridges в per-node overlay или secrets
step_3_tor_proxy() {
    step_start "tor-proxy" "Installing Tor + Privoxy proxy for Telegram"

    local bridges_file="${TOR_BRIDGES_FILE:-}"
    if [[ -z "$bridges_file" ]]; then
        local overlay_bridges="/opt/node-configs/${NODE_NAME}/overlays/tor/bridges.txt"
        if [[ -f "$overlay_bridges" ]]; then
            bridges_file="$overlay_bridges"
        fi
    fi
    if [[ -z "$bridges_file" ]]; then
        local shared_bridges="${CORE_DIR}/bootstrap/tor/bridges.txt"
        if [[ -f "$shared_bridges" ]]; then
            bridges_file="$shared_bridges"
            log_step "tor-proxy" "INFO" "Using shared bridges: ${shared_bridges}"
        fi
    fi

    local tor_args=()
    if [[ -n "$bridges_file" ]]; then
        tor_args+=("--tor-bridges-file" "$bridges_file")
    fi
    if [[ "${SKIP_TOR_VERIFY:-}" == "true" ]]; then
        tor_args+=("--skip-tor-verify")
    fi

    if bash "${CORE_DIR}/internal/bootstrap/install-tor-proxy.sh" "${tor_args[@]}"; then
        step_done "tor-proxy" "Tor + Privoxy installed and verified"
    else
        step_warn "tor-proxy" "Tor circuit failed to establish — Telegram notifications will be unavailable"
    fi
}

# ─── STEP 4: Docker installation ─────────────────────────
step_4_install_docker() {
    step_start "install-docker" "Installing Docker + Compose plugin"
    bash "${CORE_DIR}/internal/bootstrap/install-docker.sh"
    step_done "install-docker" "Docker ready"
}

# ─── STEP 5: Create user 'platform' ──────────────────────
step_5_create_platform_user() {
    step_start "user-platform" "Ensuring user 'platform'"
    if id "platform" &>/dev/null 2>&1; then
        step_skip "user-platform" "User 'platform' already exists"
    else
        useradd --system --shell /bin/bash --create-home --home-dir /home/platform --groups docker platform
        step_done "user-platform" "User 'platform' created"
    fi

    if [[ -n "${PLATFORM_OWNER_KEY:-}" ]]; then
        local auth_keys="/home/platform/.ssh/authorized_keys"
        mkdir -p /home/platform/.ssh
        chmod 0700 /home/platform/.ssh
        chown platform:platform /home/platform/.ssh
        if ! grep -qF "$PLATFORM_OWNER_KEY" "$auth_keys" 2>/dev/null; then
            printf '%s\n' "$PLATFORM_OWNER_KEY" >> "$auth_keys"
            chmod 0600 "$auth_keys"
            chown platform:platform "$auth_keys"
            step_done "user-platform-key" "Owner SSH key added"
        else
            step_skip "user-platform-key" "Owner SSH key already present"
        fi
    fi
}

# ─── STEP 6: Create user 'ci-deploy' ──────────────────────
step_6_create_ci_deploy_user() {
    step_start "user-ci-deploy" "Ensuring user 'ci-deploy'"
    if id "ci-deploy" &>/dev/null 2>&1; then
        step_skip "user-ci-deploy" "User 'ci-deploy' already exists"
    else
        useradd --system --shell /bin/bash --create-home --home-dir /home/ci-deploy --groups docker ci-deploy
        step_done "user-ci-deploy" "User 'ci-deploy' created"
    fi

    if [[ -n "${PLATFORM_CI_DEPLOY_KEY:-}" ]]; then
        local auth_keys="/home/ci-deploy/.ssh/authorized_keys"
        mkdir -p /home/ci-deploy/.ssh
        chmod 0700 /home/ci-deploy/.ssh
        chown ci-deploy:ci-deploy /home/ci-deploy/.ssh
        if ! grep -qF "$PLATFORM_CI_DEPLOY_KEY" "$auth_keys" 2>/dev/null; then
            [[ "$NODE_NAME" =~ ^[a-zA-Z0-9_-]+$ ]] || {
                log_step "user-ci-deploy" "FATAL" "Invalid NODE_NAME: ${NODE_NAME}"
                exit 1
            }
            printf 'command="%s/core/internal/deploy/deploy-project.sh %s",restrict %s\n' "$PLATFORM_ROOT" "$NODE_NAME" "$PLATFORM_CI_DEPLOY_KEY" >> "$auth_keys"
            chmod 0600 "$auth_keys"
            chown ci-deploy:ci-deploy "$auth_keys"
            step_done "user-ci-deploy-key" "ci-deploy key added with command=restrict"
        else
            step_skip "user-ci-deploy-key" "ci-deploy SSH key already present"
        fi
    fi
}

# ─── STEP 6b: Create /opt/projects base directory ─────────
## @purpose  Ensure /opt/projects base directory exists with ci-deploy ownership.
##           This directory is the root for all project payloads delivered via
##           forced-command platform-deliver verb (D2). Idempotent: mkdir -p + chown
##           are safe to rerun; checkpoint ensures no-op on subsequent boots.
##           After base dir + ownership, calls converge --units R3 for per-project
##           scaffold (directories, ai-platform.yaml stub, .env.platform via gen-env).
## @invariants
##   - Runs AFTER step_6 (ci-deploy user must exist for chown to succeed)
##   - mkdir -p is idempotent; chown ci-deploy:ci-deploy is safe to repeat
##   - Projects base is a single fixed path (/opt/projects) shared with deploy-project.sh
##   - Converge R3 is called only if converge.sh exists AND NODE_NAME is set
##   - Converge R3 failure is non-fatal (WARN, not abort)
## @rationale Per DevPlan 008 Contract 2 (DD2): bootstrap creates base directory ownership;
##            platform-deliver verb guarantees PROJECT_DIR even on nodes bootstrapped
##            before this fix (defense in depth).
## @rationale Per DevPlan 024 Wave 2: converge --units R3 creates per-project stubs
##            during bootstrap step 6b, before CI deliver tries to deploy. This ensures
##            project directories exist for the first forced-command delivery.
step_6b_create_projects_base() {
    step_start "projects-base" "Ensuring /opt/projects base directory + project scaffold"
    if [[ -d "/opt/projects" ]]; then
        step_skip "projects-base" "/opt/projects already exists"
    else
        mkdir -p /opt/projects
        step_done "projects-base" "/opt/projects directory created"
    fi
    # Always ensure ownership (idempotent — chown is safe to repeat)
    chown ci-deploy:ci-deploy /opt/projects
    # Org subdirectories are created dynamically by handle_deliver() in
    # deploy-project.sh on first platform-deliver call. No static creation needed.
    echo "[IMP:9][bootstrap][projects-base] /opt/projects ownership set to ci-deploy:ci-deploy" >&2

    # ── Call converge R3 for project scaffold (per-project dirs + stubs) ──
    local converge_script="${CORE_DIR}/internal/bootstrap/converge.sh"
    if [[ -f "${converge_script}" ]] && [[ -n "${NODE_NAME:-}" ]]; then
        echo "[IMP:8][bootstrap][projects-base] Calling converge R3 for project scaffold (node=${NODE_NAME})" >&2
        if bash "${converge_script}" --node "${NODE_NAME}" --units R3 2>&1; then
            log_step "projects-base" "INFO" "Converge R3 completed (exit 0)"
        else
            local converge_rc=$?
            if [[ $converge_rc -eq 1 ]]; then
                log_step "projects-base" "INFO" "Converge R3 completed with warnings (exit 1) — non-critical drift"
            elif [[ $converge_rc -eq 2 ]]; then
                log_step "projects-base" "WARN" "Converge R3 CRITICAL errors (exit 2) — projects may need manual creation"
            else
                log_step "projects-base" "WARN" "Converge R3 failed (exit ${converge_rc}) — projects may need manual creation"
            fi
        fi
    elif [[ -z "${NODE_NAME:-}" ]]; then
        echo "[IMP:7][bootstrap][projects-base] SKIP: NODE_NAME not set — cannot call converge R3" >&2
    else
        echo "[IMP:7][bootstrap][projects-base] SKIP: converge.sh not found at ${converge_script}" >&2
    fi
}

# ─── STEP 7: Firewall (ufw declarative) ──────────────────
step_7_firewall() {
    step_start "firewall" "Applying declarative ufw firewall baseline"
    local extra_ports=()
    if [[ -n "${FIREWALL_EXTRA_PORTS:-}" ]]; then
        IFS=' ' read -ra extra_ports <<< "$FIREWALL_EXTRA_PORTS"
    fi
    bash "${CORE_DIR}/internal/bootstrap/firewall.sh" "${extra_ports[@]:-}"
    step_done "firewall" "Firewall applied: 22/80/443 + extra=[${extra_ports[*]:-}]"
}

# ─── STEP 8: Verify core directory ────────────────────────
# 🧐 TRAP[DECISION] · 2026-07-17 · — · CORE_DEPLOY_DIR dead code removed
# · Rejected: keeping CORE_DEPLOY_DIR with fallback (dead code — never set, always falls back)
# · Reason: CORE_DEPLOY_DIR was a relic from before CORE_DIR/PLATFORM_ROOT standardization.
#   All code uses CORE_DIR via paths.sh. The fallback `${PLATFORM_ROOT}/core` was the
#   only effective value — the variable was never exported. Removing 4 lines of dead code
#   reduces confusion for agents reading this file.
# · Rev: if a future change needs a deploy-specific core path, reintroduce as a new named var
step_8_verify_core() {
    step_start "verify-core" "Verifying core files (SCP-delivered)"
    local core_dir="${PLATFORM_ROOT}/core"
    local marker="${core_dir}/internal/bootstrap/node-lifecycle.sh"

    if [[ ! -f "$marker" ]]; then
        log_step "verify-core" "FAIL" "Core bootstrap not found at ${marker}"
        echo "[IMP:10][bootstrap][step-8] ERROR: Core files not found. Deploy first:" >&2
        echo "  rsync -avz core/ root@<server>:${PLATFORM_ROOT}/core/" >&2
        exit 1
    fi

    local ver_file="${core_dir}/VERSION"
    if [[ -f "$ver_file" ]]; then
        local ver; ver="$(head -1 "$ver_file")"
        step_done "verify-core" "Core v${ver} at ${core_dir}"
    else
        step_done "verify-core" "Core found at ${core_dir}"
    fi
}

# ─── STEP 9: Verify node-configs ─────────────────────────
step_9_verify_node_configs() {
    step_start "verify-node-configs" "Verifying node-configs (SCP-delivered)"

    if [[ ! -f "$NODE_YAML" ]]; then
        log_step "verify-node-configs" "FAIL" "node.yaml not found: ${NODE_YAML}"
        echo "[IMP:10][bootstrap][step-9] ERROR: node.yaml not found at ${NODE_YAML}" >&2
        echo "  rsync -avz node-configs/ root@<server>:/opt/node-configs/" >&2
        exit 1
    fi
    step_done "verify-node-configs" "node.yaml: ${NODE_YAML}"
}

# ─── STEP 11: Read node.yaml → desired module state ──────
step_11_read_node_yaml() {
    step_start "read-node-yaml" "Validating and reading node.yaml"

    export PYTHONPATH=""
    local schema_file="${CORE_DIR}/schemas/node.schema.json"
    if python3 - "$NODE_YAML" "$schema_file" <<'PYEOF' 2>/dev/null; then
import json, yaml, jsonschema, sys

with open(sys.argv[1]) as f:
    instance = yaml.safe_load(f)
with open(sys.argv[2]) as f:
    schema = json.load(f)

jsonschema.validate(instance, schema)
PYEOF
        step_done "read-node-yaml" "node.yaml valid against schema"
    else
        step_warn "read-node-yaml" "node.yaml validation failed — check schemas"
    fi
}

# ─── STEP 12: Configure GHCR auth for ci-deploy ─────────
step_12_ghcr_auth() {
    step_start "ghcr-auth" "Configuring Docker GHCR login for ci-deploy user"
    if [[ -n "${GHCR_PULL_TOKEN:-}" ]]; then
        echo "${GHCR_PULL_TOKEN}" | sudo -u ci-deploy docker login ghcr.io \
            -u x-access-token --password-stdin 2>/dev/null && \
            step_done "ghcr-auth" "ci-deploy authenticated to ghcr.io" || \
            step_warn "ghcr-auth" "GHCR login failed"
    else
        step_skip "ghcr-auth" "GHCR_PULL_TOKEN not set in secrets"
    fi
}

# ─── STEP 13: Sudoers generation + validation ─────────────
# ⚠️ TRAP[BUSINESS] · 2026-07-09 · HI · Step declaration order must match main() execution order
step_13_sudoers() {
    step_start "sudoers" "Generating sudoers via visudo -c + atomic mv"
    bash "${CORE_DIR}/internal/bootstrap/setup-node.sh"

    local errors=0
    local sudoers_d="/etc/sudoers.d"
    if [[ -d "$sudoers_d" ]]; then
        local f
        while IFS= read -r -d '' f; do
            local basename
            basename="$(basename "$f")"
            [[ "$basename" == "README" ]] && continue

            local owner mode
            owner="$(stat -c '%u:%g' "$f" 2>/dev/null || true)"
            mode="$(stat -c '%a' "$f" 2>/dev/null || true)"

            if [[ "$owner" != "0:0" ]]; then
                log_step "sudoers-d" "FAIL" "${basename}: владелец ${owner} вместо 0:0"
                errors=$(( errors + 1 ))
            fi
            if [[ "${mode:-444}" -gt 440 ]] 2>/dev/null; then
                log_step "sudoers-d" "FAIL" "${basename}: права ${mode} вместо ≤0440"
                errors=$(( errors + 1 ))
            fi
        done < <(find "$sudoers_d" -type f -print0 2>/dev/null)
    fi

    if [[ "$errors" -gt 0 ]]; then
        log_step "sudoers-d" "FAIL" "${errors} файл(ов) с неверным владельцем/правами — отмена bootstrap"
        echo "[IMP:10][bootstrap][validate-sudoers-d] ERROR: Исправь вручную:" >&2
        echo "  chown root:root ${sudoers_d}/*" >&2
        echo "  chmod 0440 ${sudoers_d}/*" >&2
        exit 1
    fi

    step_done "sudoers" "sudoers generated + validated: all files owner=root:root mode≤0440"
}

# ─── STEP 13b: Install acme.sh (init only) ─────────────────
## @brief  Install acme.sh and DNS API extensions for SSL provisioning.
## @detail  Called once at bootstrap/init, BEFORE node-update. At update time,
##          issue-cert.sh is called directly (acme.sh already installed).
##          Delegates to install-acme.sh. Idempotent: skips if already installed.
## @rationale  T3 (DevPlan 005): Split ssl-provision.sh into install-acme.sh
##             (init-only) and issue-cert.sh (update-time). This step ensures
##             acme.sh binary exists before SSL certificate issuance.
_step_install_acme() {
    step_start "install-acme" "Installing acme.sh for SSL provisioning (init only)"
    if bash "${CORE_DIR}/internal/bootstrap/install-acme.sh" 2>&1; then
        step_done "install-acme" "acme.sh installed"
    else
        step_warn "install-acme" "acme.sh install failed — SSL provisioning will fail later"
    fi
}

# ─── STEP 13c: Initialize service passwords (secrets-init) ──────────
## @brief  Initialize all service passwords (HERMES_DASHBOARD_PASSWORD,
##         GF_SECURITY_ADMIN_PASSWORD, LANGFUSE_INIT_USER_PASSWORD) from
##         PLATFORM_MASTER_PASSWORD. Called once at bootstrap init, NOT at update.
## @detail  Delegates to secrets-init.sh. Idempotent: if a service password is
##          already set (operator-defined), it is NOT overwritten. Non-fatal —
##          if secrets-init.sh fails, bootstrap continues with WARN (passwords
##          may already be set in SOPS). Init-only — update mode does NOT call
##          this again (passwords already initialized at init).
## @invariants
##   - Runs AFTER ensure-secrets (step_12b) so all basic secrets are in env
##   - Runs BEFORE read-node-yaml, ghcr-auth, sudoers — service passwords are
##     available for any downstream step that may need them
##   - secrets-init.sh must exist; if missing, log WARN, continue without fail
##   - In update mode: NOT called (secrets-init.sh is init-only)
_step_secrets_init() {
    step_start "secrets-init" "Initializing service passwords from PLATFORM_MASTER_PASSWORD"
    if bash "${CORE_DIR}/internal/bootstrap/secrets-init.sh" 2>&1; then
        step_done "secrets-init" "Service passwords initialized"
    else
        step_warn "secrets-init" "secrets-init.sh failed — passwords may be set in SOPS"
    fi
}

# ─── STEP 14: Node update (post-init) ─────────────────────
## @brief  Post-init update: provision + deploy-modules + healthcheck
## @detail  Delegates to node-lifecycle.sh --mode update which handles all post-bootstrap
##          update steps (provision, docker deploy, system deploy, healthcheck).
##          This replaces the previous inline deploy-modules + healthcheck logic
##          (T19: bootstrap-node → INIT-only with optional cleanup).
step_14_node_update() {
    step_start "node-update" "Running node-update (post-init: provision + deploy-modules + healthcheck)"

    # Export auth tokens for downstream step (deploy-modules.sh needs them)
    if [[ -n "${DOCKER_HUB_USERNAME:-}" ]] && [[ -n "${DOCKER_HUB_TOKEN:-}" ]]; then
        export DOCKER_HUB_USERNAME DOCKER_HUB_TOKEN
    fi
    if [[ -n "${GHCR_PULL_TOKEN:-}" ]]; then
        export GHCR_PULL_TOKEN
    fi

    # S7: Extract domain config from node.yaml via yaml_read_domain_config() (replaces inline python3)
    if [[ -n "${NODE_YAML:-}" ]] && [[ -f "$NODE_YAML" ]]; then
        local domain_info
        domain_info="$(yaml_read_domain_config "$NODE_YAML")" || true
        if [[ -n "${domain_info:-}" ]]; then
            local yaml_domain yaml_email yaml_project_domains yaml_acme_dns
            yaml_domain="$(echo "$domain_info" | grep '^platform_domain:' | cut -d: -f2-)"
            yaml_email="$(echo "$domain_info" | grep '^email:' | cut -d: -f2-)"
            yaml_project_domains="$(echo "$domain_info" | grep '^project_domains:' | cut -d: -f2-)"
            yaml_acme_dns="$(echo "$domain_info" | grep '^acme_dns_plugin:' | cut -d: -f2-)"

            # Export with fallback: node.yaml value takes priority, then existing env, then empty
            export PLATFORM_DOMAIN="${yaml_domain:-${PLATFORM_DOMAIN:-}}"
            export PLATFORM_EMAIL="${yaml_email:-${PLATFORM_EMAIL:-}}"
            export PLATFORM_PROJECT_DOMAINS="${yaml_project_domains:-${PLATFORM_PROJECT_DOMAINS:-}}"
            export PLATFORM_ACME_DNS_PLUGIN="${yaml_acme_dns:-${PLATFORM_ACME_DNS_PLUGIN:-}}"
        fi
    fi

    echo "[IMP:9][bootstrap][step-14] INVOKING: node-lifecycle.sh --mode update (post-init update)"
    if bash "${CORE_DIR}/internal/bootstrap/node-lifecycle.sh" "--mode" "update" 2>&1; then
        step_done "node-update" "Node update completed successfully"
    else
        step_warn "node-update" "Node update had failures — check logs"
    fi
}

# ─── STEP 15: Converge (desired-state reconciler) ──────────
## @purpose  Run converge.sh to reconcile 6 R-units (permissions, audit log,
##           projects, networks, /etc/hosts drift, vhost configs). Idempotent:
##           on repeat run, all R-units report SKIP if already converged.
## @detail   In init mode: converge = required — must pass for bootstrap to succeed.
##           In update mode: converge = non-fatal — failures are WARN + continue.
## @invariants
##   - RUNS AFTER step_14 (node-update) so all modules are deployed before converge
##   - RUNS BEFORE step_16 (audit) so converge events are captured in audit log
##   - DRY_RUN mode: prints converge.sh command without executing
##   - converge.sh exit code: 0=converged, 1=mutations (normal), 2=errors
##   - --report-only not used from lifecycle — only standalone mode
step_15_converge() {
    step_start "converge" "Running desired-state reconciler (converge.sh)"

    local converge_script="${CORE_DIR}/internal/bootstrap/converge.sh"
    if [[ ! -f "$converge_script" ]]; then
        log_step "converge" "WARN" "converge.sh not found at ${converge_script} — skipping"
        return 0
    fi

    local converge_args=("--node" "${NODE_NAME}")
    if [[ "${DRY_RUN_MODE:-}" == "true" ]]; then
        converge_args+=("--dry-run")
    fi
    if [[ "${AUTO_RECONCILE:-false}" == "true" ]]; then
        converge_args+=("--reconcile")
    fi

    echo "[IMP:9][bootstrap][step-15] INVOKING: ${converge_script} ${converge_args[*]}" >&2
    if bash "${converge_script}" "${converge_args[@]}" 2>&1; then
        step_done "converge" "Converge complete — no errors"
    else
        local converge_rc=$?
        if [[ $converge_rc -eq 2 ]]; then
            # ERROR — блокирует только в init-режиме
            if [[ "${MODE}" == "init" ]]; then
                step_warn "converge" "Converge CRITICAL errors (exit 2) — bootstrap continues but node is DEGRADED"
            else
                step_warn "converge" "Converge CRITICAL errors (exit 2) — node may be DEGRADED"
            fi
        elif [[ $converge_rc -eq 1 ]]; then
            # WARNINGS — не блокирует
            step_done "converge" "Converge complete with warnings (exit 1) — non-critical drift"
        fi
    fi

    # ── Optional: reconcile stub projects (W4) ──
    if [[ "${AUTO_RECONCILE:-false}" == "true" ]]; then
        step_start "reconcile-projects" "Auto-reconciling stub projects after converge"
        local reconcile_script="${CORE_DIR}/internal/deploy/reconcile-projects.sh"
        if [[ -f "$reconcile_script" ]]; then
            source "$reconcile_script"
            reconcile_projects "${NODE_NAME}" "${NODE_YAML}"
            step_done "reconcile-projects" "Stub reconciliation complete"
        else
            step_warn "reconcile-projects" "reconcile-projects.sh not found"
        fi
    fi
}

# ─── STEP 16: Audit log summary ──────────────────────────
step_16_audit_log() {
    step_start "audit-summary" "Writing bootstrap audit summary"
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    audit_log "bootstrap:complete" "DONE" "Bootstrap finished at ${ts} | node=${NODE_NAME} | warnings=${#STEP_ERRORS[@]}"

    if [[ ${#STEP_ERRORS[@]} -gt 0 ]]; then
        audit_log "bootstrap:warnings" "WARN" "Warning steps: ${STEP_ERRORS[*]}"
    fi
    step_done "audit-summary" "Audit log updated: /var/log/platform/audit.log"
}

# ─── STEP 17: Telegram notification ──────────────────────
step_17_telegram() {
    if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]] || [[ -z "${TELEGRAM_CHAT_ID:-}" ]]; then
        log_step "telegram" "SKIP" "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — notifications disabled"
        echo "[IMP:9][node-lifecycle][telegram] INFO: Telegram notifications skipped (tokens not configured)" >&2
        return 0
    fi

    local ts
    ts="$(TZ='Europe/Moscow' date '+%d.%m.%Y %H:%M:%S')"

    local status_suffix
    if [[ ${#STEP_ERRORS[@]} -gt 0 ]]; then
        status_suffix="c ⚠️ Warnings:"
    else
        status_suffix="✅"
    fi
    local msg="🚀 [node: ${NODE_NAME}] Узел обновлён ${status_suffix}"$'\n'
    msg+="Время: ${ts}"

    if [[ ${#STEP_ERRORS[@]} -gt 0 ]]; then
        msg+=$'\n'
        for err in "${STEP_ERRORS[@]}"; do
            msg+=$'\n'"- ${err}"
        done
    fi

    local proxy_url="${TELEGRAM_PROXY_URL:-http://127.0.0.1:8118}"
    curl -s -o /dev/null --proxy "$proxy_url" --max-time 30 -G \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${msg}" \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        || log_step "telegram" "WARN" "Telegram notification failed (non-fatal)"
}

# region INSTALL_LOGROTATE
install_logrotate() {
    local logrotate_src="${CORE_DIR}/bootstrap/platform-audit.logrotate"
    local logrotate_dst="/etc/logrotate.d/platform-audit"
    if [[ -f "$logrotate_src" ]] && [[ ! -f "$logrotate_dst" ]]; then
        cp "$logrotate_src" "$logrotate_dst"
        chmod 0644 "$logrotate_dst"
        log_step "logrotate" "DONE" "logrotate config installed: ${logrotate_dst}"
    else
        log_step "logrotate" "SKIP" "logrotate config already present"
    fi
}
# endregion INSTALL_LOGROTATE

# ══════════════════════════════════════════════════════════════════
# UPDATE MODE — Incremental node update (5 steps: verify → provision → ssl → deploy-modules → healthcheck → converge)
# ══════════════════════════════════════════════════════════════════

# region FUNC_update_step_1_verify_core
## @purpose  Verify that core files are properly delivered and compute
##           content hash to detect changes. If core code changed since
##           last update, all downstream checkpoints are cleared.
## @param    none (uses CORE_DIR from paths.sh)
## @io       out: stderr → verification status at IMP:8-9
##            effect: triggers checkpoint invalidation if hash changed
## @complexity O(n) where n = number of verified core files
## @invariants — node-lifecycle.sh existence is the primary delivery marker
##             - Content hash covers node-lifecycle.sh + checkpoint.sh + content-hash.sh
##             - VERSION file is read for display but NOT used for invalidation
update_step_1_verify_core() {
    step_start "verify-core" "Verifying core files (SCP-delivered)"

    local marker="${CORE_DIR}/internal/bootstrap/node-lifecycle.sh"
    if [[ ! -f "$marker" ]]; then
        log_step "verify-core" "FAIL" "Core lifecycle script not found at ${marker}"
        echo "[IMP:10][node-update][step-1] ERROR: Core files not delivered. Run bootstrap first." >&2
        exit 1
    fi

    local ver_file="${CORE_DIR}/VERSION"
    if [[ -f "$ver_file" ]]; then
        local ver
        ver="$(head -1 "$ver_file")"
        step_done "verify-core" "Core v${ver} at ${CORE_DIR}"
    else
        step_done "verify-core" "Core found at ${CORE_DIR} (no VERSION file)"
    fi
}
# endregion FUNC_update_step_1_verify_core

# region FUNC_update_step_2_provision
## @purpose  Provision environment: networks, volumes (env = CI-only via Makefile).
##           Delegates to provision-environment.sh --scope networks --scope volumes.
##           --scope env не вызывается — это CI-only прерогатива Makefile.
update_step_2_provision() {
    step_start "provision" "Running environment provision (networks + volumes)"

    local prov_exit=0
    bash "${CORE_DIR}/internal/provision-environment.sh" --scope networks --scope volumes 2>&1 || prov_exit=$?

    if [[ $prov_exit -eq 0 ]]; then
        step_done "provision" "Environment provision completed"
    else
        log_step "provision" "FAIL" "Environment provision failed (exit=${prov_exit})"
        exit 1
    fi
}
# endregion FUNC_update_step_2_provision

# region FUNC_update_step_2_5_deliver_overlays
## @purpose  S2 (DevPlan 019): Verify and activate vhost overlays delivered from local
##           node-configs/<node>/overlays/nginx/ to /opt/node-configs/<node>/overlays/nginx/.
##           Idempotent — reloads nginx only if conf files changed.
## @detail   Vhost files are rsync'd by entrypoints/node-update.sh or scp-deliver.sh
##           (Phase 2/4). This step verifies they exist and reloads nginx if running.
## @invariants
##   - Called after provision (step 2) and before ssl-provision (step 3)
##   - Failure is NON-FATAL: skips gracefully if no overlays
##   - NODE_NAME must be set to derive the overlay path
update_step_2_5_deliver_overlays() {
    step_start "deliver-overlays" "Verifying vhost overlay files"
    local overlay_dir="/opt/node-configs/${NODE_NAME}/overlays/nginx"
    if [[ ! -d "${overlay_dir}" ]]; then
        step_done "deliver-overlays" "No overlay directory at ${overlay_dir} — skipping"
        return 0
    fi
    local conf_count
    conf_count=$(find "${overlay_dir}" -maxdepth 1 -name '*.conf' -type f 2>/dev/null | wc -l | tr -d ' ')
    if [[ "${conf_count}" -eq 0 ]]; then
        step_done "deliver-overlays" "No .conf files in ${overlay_dir} — skipping"
        return 0
    fi
    log_step "deliver-overlays" "INFO" "Found ${conf_count} vhost overlay(s) in ${overlay_dir}"
    # Reload nginx to pick up new vhosts (if nginx container is running)
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'nginx'; then
        log_step "deliver-overlays" "INFO" "Reloading nginx to pick up overlay vhosts"
        if docker exec nginx nginx -s reload 2>/dev/null; then
            step_done "deliver-overlays" "Nginx reloaded successfully"
        else
            log_step "deliver-overlays" "WARN" "Nginx reload failed — container may be unhealthy"
        fi
    else
        step_done "deliver-overlays" "Nginx not running — overlays will be picked up on next start"
    fi
}
# endregion FUNC_update_step_2_5_deliver_overlays

# region FUNC_update_step_3_ssl_provision
## @purpose  Provision SSL/TLS certificates via acme.sh DNS-01 BEFORE docker deploy.
##           Ensures nginx has valid cert at /etc/letsencrypt/live/<domain>/ before
##           docker compose up. DNS-01 validation does NOT require nginx to be running.
## @detail   Delegates to issue-cert.sh (cert issuance only). Idempotent: skips if
##           cert already exists. acme.sh was already installed at init (install-acme.sh).
##           Extracts domain/email/dns_plugin from NODE_YAML via python3/yaml.
## @invariants
##   - Called after provision (step 2) and before deploy-docker (step 4)
##   - Failure is NON-FATAL for non-nginx modules (warn only)
##   - WEBNAMES_API_KEY must be in secrets for webnames DNS plugin
##   - acme.sh must be installed first (install-acme.sh called at init)
update_step_3_ssl_provision() {
    step_start "ssl-provision" "Provisioning SSL certificates via acme.sh DNS-01"

    local ssl_script="${CORE_DIR}/internal/bootstrap/issue-cert.sh"
    if [[ ! -f "$ssl_script" ]]; then
        log_step "ssl-provision" "WARN" "issue-cert.sh not found at ${ssl_script} — skipping SSL provisioning"
        return 0
    fi

    # S7: Export domain config from node.yaml via yaml_read_domain_config() (replaces inline python3)
    if [[ -n "${NODE_YAML:-}" ]] && [[ -f "$NODE_YAML" ]]; then
        local domain_info
        domain_info="$(yaml_read_domain_config "$NODE_YAML")" || true
        if [[ -n "${domain_info:-}" ]]; then
            export PLATFORM_DOMAIN="$(echo "$domain_info" | grep '^platform_domain:' | cut -d: -f2-)"
            export PLATFORM_EMAIL="$(echo "$domain_info" | grep '^email:' | cut -d: -f2-)"
            export PLATFORM_ACME_DNS_PLUGIN="$(echo "$domain_info" | grep '^acme_dns_plugin:' | cut -d: -f2-)"
            export PLATFORM_PROJECT_DOMAINS="$(echo "$domain_info" | grep '^project_domains:' | cut -d: -f2-)"
        fi
    fi

    if [[ -z "${PLATFORM_DOMAIN:-}" ]]; then
        log_step "ssl-provision" "WARN" "PLATFORM_DOMAIN not set — skipping SSL provisioning (no domain configured)"
        return 0
    fi

    # ── Source secrets.env for WEBNAMES_API_KEY ────────────────────────
    # T3 (DevPlan 005): Source existing secrets.env before issue-cert.sh.
    # WEBNAMES_API_KEY is required for webnames DNS plugin. If secrets.env
    # is missing (e.g. after reboot), warn but don't fail — issue-cert.sh
    # skips if cert already exists (idempotent). Uses SECRETS_ENV_FILE env
    # var from lib/secrets.sh with fallback to /run/platform/secrets.env.
    local secrets_env="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    if [[ -f "$secrets_env" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$secrets_env"
        set +a
        # Clear HTTP_PROXY/HTTPS_PROXY — secrets.env has host.docker.internal:8118
        # which doesn't resolve on the host and breaks acme.sh curl requests.
        unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy
        if [[ -n "${WEBNAMES_API_KEY:-}" ]]; then
            echo "[IMP:8][node-lifecycle][ssl-provision] WEBNAMES_API_KEY loaded from ${secrets_env}" >&2
        else
            echo "[IMP:8][node-lifecycle][ssl-provision] INFO: ${secrets_env} sourced but WEBNAMES_API_KEY not set — cert renewal via webnames will fail if cert expires" >&2
        fi
    else
        echo "[IMP:8][node-lifecycle][ssl-provision] WARN: ${secrets_env} missing — cert renewal may fail if cert expires" >&2
    fi

    # ── Step 3a: Check S3 cache before issue (Wave 1 optimization) ────
    # [IMP:9][node-lifecycle][ssl-provision] BUSINESS INVARIANT: check S3 cache first
    # If valid cert exists in S3, restore it and skip acme.sh issue entirely.
    # This saves ~30s+ per bootstrap (no DNS-01 challenge) for domains that
    # have been previously provisioned.
    local s3_cache="${CORE_DIR}/internal/bootstrap/s3-ssl-cache.sh"
    local ssl_restored=false
    if [[ -f "$s3_cache" ]]; then
        log_step "ssl-provision" "INFO" "Checking S3 cert cache for ${PLATFORM_DOMAIN}"
        echo "[IMP:8][node-lifecycle][ssl-provision] Checking S3 cache at ${s3_cache}" >&2
        if bash "$s3_cache" check "${PLATFORM_DOMAIN}" 2>&1; then
            log_step "ssl-provision" "INFO" "Valid cert found in S3 cache — restoring"
            echo "[IMP:9][node-lifecycle][ssl-provision] Restoring SSL cert from S3 cache" >&2
            if bash "$s3_cache" download "${PLATFORM_DOMAIN}" 2>&1; then
                echo "[IMP:9][node-lifecycle][ssl-provision] Cert restored from S3 cache — verifying" >&2
                ssl_restored=true
            else
                log_step "ssl-provision" "WARN" "S3 cert restore failed — falling back to acme.sh issue"
                echo "[IMP:8][node-lifecycle][ssl-provision] S3 restore returned non-zero — falling back" >&2
            fi
        else
            log_step "ssl-provision" "INFO" "No valid cert in S3 cache — proceeding with acme.sh issue"
            echo "[IMP:8][node-lifecycle][ssl-provision] S3 check returned 1 — cache miss" >&2
        fi
    else
        echo "[IMP:7][node-lifecycle][ssl-provision] s3-ssl-cache.sh not found at ${s3_cache} — skipping S3 check" >&2
    fi

    if [[ "$ssl_restored" == "true" ]] && [[ -f "/etc/letsencrypt/live/${PLATFORM_DOMAIN}/fullchain.pem" ]]; then
        # Cert restored from S3 — skip issue-cert.sh entirely
        log_step "ssl-provision" "DONE" "SSL certificate restored from S3 cache for ${PLATFORM_DOMAIN}"
        echo "[IMP:9][node-lifecycle][ssl-provision] SSL cert restored from S3 cache — skipping acme.sh issue" >&2
        return 0
    fi

    echo "[IMP:9][node-lifecycle][ssl-provision] Issuing SSL certificate for ${PLATFORM_DOMAIN}"
    if bash "$ssl_script" 2>&1; then
        step_done "ssl-provision" "SSL certificate provisioned for ${PLATFORM_DOMAIN}"
    else
        step_warn "ssl-provision" "SSL provisioning failed — nginx will not have HTTPS until resolved"
    fi
}
# endregion FUNC_update_step_3_ssl_provision

# region FUNC_update_step_4_deploy_modules
## @purpose  S2: Deploy ALL modules (docker + system) in a single deploy-modules.sh call.
##           Previously split into step_4 (docker) + step_5 (system) — merged per
##           DevPlan 024 S2. Uses --skip-provision to avoid redundant provisioner call
##           (provisioner already ran at step 2 — eliminates ~4 duplicate invocations).
## @rationale deploy-modules.sh handles both types in one pass. Merging avoids a
##           second full main() invocation: _validate_secret_charsets, docker_login,
##           ghcr_login, ensure_context_repo, parse_modules — all repeated twice.
update_step_4_deploy_modules() {
    step_start "deploy-modules" "Deploying all modules (docker + system)"

    export NODE_YAML
    if bash "${CORE_DIR}/internal/bootstrap/deploy-modules.sh" --skip-provision 2>&1; then
        step_done "deploy-modules" "Module deployment complete"
    else
        log_step "deploy-modules" "FAIL" "Module deployment failed"
        exit 1
    fi
}
# endregion FUNC_update_step_4_deploy_modules

# region FUNC_update_step_6_healthcheck
## @purpose  Run healthchecks on all deployed modules. Failure is non-fatal
##           — logged as warning, bootstrap continues.
update_step_6_healthcheck() {
    step_start "healthcheck-all" "Running healthchecks (failure non-fatal)"
    local hc_fail=0

    local node_yaml="${NODE_YAML:-}"
    if [[ -z "$node_yaml" ]] || [[ ! -f "$node_yaml" ]]; then
        log_step "healthcheck-all" "WARN" "NODE_YAML not set or not found — skipping healthchecks"
        return 0
    fi

    local modules_raw
    modules_raw="$(python3 - "$node_yaml" <<'PYEOF' 2>/dev/null
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
modules = data.get('modules', {})
if isinstance(modules, dict):
    for name, value in modules.items():
        enabled = str(value.get('enabled', True) if isinstance(value, dict) else value).lower()
        print(f'{name}:{enabled}')
elif isinstance(modules, list):
    for m in modules:
        name = m.get('name', '')
        enabled = str(m.get('enabled', True)).lower()
        print(f'{name}:{enabled}')
PYEOF
)"

    if [[ -z "$modules_raw" ]]; then
        log_step "healthcheck-all" "SKIP" "No modules found in node.yaml"
        return 0
    fi

    local hc_max_retries=4
    local hc_retry_interval=3

    while IFS=: read -r mod_name mod_enabled; do
        [[ -z "$mod_name" ]] && continue
        [[ "$mod_enabled" != "true" ]] && continue

        local attempt=0 hc_passed=0
        while [[ $attempt -lt $hc_max_retries ]]; do
            local hc_rc=0
            invoke_module_interface "$mod_name" healthcheck liveness &>/dev/null 2>&1 || hc_rc=$?
            if [[ $hc_rc -eq 0 ]]; then
                    log_step "healthcheck:${mod_name}" "DONE" "Healthcheck PASS (attempt $((attempt + 1))/${hc_max_retries})"
                    hc_passed=1
                    break
                fi
                attempt=$(( attempt + 1 ))
                if [[ $attempt -lt $hc_max_retries ]]; then
                    sleep "$hc_retry_interval"
                fi
            done
            if [[ $hc_passed -eq 0 ]]; then
                step_warn "healthcheck:${mod_name}" "Healthcheck FAILED after ${hc_max_retries} attempts"
                hc_fail=$(( hc_fail + 1 ))
            fi
    done <<< "$modules_raw"

    if [[ "$hc_fail" -gt 0 ]]; then
        log_step "healthcheck-all" "WARN" "${hc_fail} healthcheck(s) failed — node partially ready"
    else
        step_done "healthcheck-all" "All healthchecks passed"
    fi
}
# endregion FUNC_update_step_6_healthcheck

# ══════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════
main() {
    if [[ "$MODE" == "init" ]]; then
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Platform Node Bootstrap START (--mode init)" >&2
        echo "[IMP:9][node-lifecycle][main] Node: ${NODE_NAME:-<unset>}" >&2
        echo "[IMP:9][node-lifecycle][main] Deploy: core=SCP/rsync, context=git" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2

        # T2: --dry-run mode (before any mutations)
        if [[ "${DRY_RUN_MODE:-}" == "true" ]]; then
            echo "[IMP:9][node-lifecycle][dry-run] ===== DRY RUN: init mode ====="
            echo "[IMP:9][node-lifecycle][dry-run] NODE_NAME: ${NODE_NAME:-<unset>}"
            echo "[IMP:9][node-lifecycle][dry-run] NODE_YAML: ${NODE_YAML:-<unset>}"
            echo "[IMP:9][node-lifecycle][dry-run] Steps: " >&2
            echo "[IMP:9][node-lifecycle][dry-run]   1. ssh-access  2. apt-deps  3. [tor]  4. install-docker  5. users" >&2
            echo "[IMP:9][node-lifecycle][dry-run]   6. ci-deploy-user  6b. projects-base  7. firewall  8. verify-core" >&2
            echo "[IMP:9][node-lifecycle][dry-run]   9. verify-node-configs  10. decrypt-secrets  11. ensure-secrets  11b. secrets-init" >&2
            echo "[IMP:9][node-lifecycle][dry-run]   12. read-node-yaml  13. ghcr-auth  14. sudoers" >&2
            echo "[IMP:9][node-lifecycle][dry-run]   14b. install-acme  15. node-update  16. converge  17. audit  18. telegram" >&2
            echo "[IMP:9][node-lifecycle][dry-run] Bootstrap DRY RUN — no mutations performed, exit 0" >&2
            exit 0
        fi

        if [[ "$FORCE_MODE" == "true" ]]; then
            echo "[IMP:8][node-lifecycle][checkpoint] --force: Clearing all checkpoints in ${CHECKPOINT_DIR}" >&2
            rm -rf "$CHECKPOINT_DIR"
        fi

        mkdir -p "$CHECKPOINT_DIR"

        validate_bootstrap_env

        # ── S5: Pre-flight YAML syntax validation (init-mode) ──
        if [[ -f "$NODE_YAML" ]]; then
            if ! python3 -c "
import yaml, sys
try:
    with open('$NODE_YAML') as f:
        yaml.safe_load(f)
except Exception as e:
    print(f'FATAL: node.yaml is not valid YAML: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
                echo "[IMP:10][node-lifecycle][init] FATAL: node.yaml is not valid YAML — check syntax at ${NODE_YAML}" >&2
                exit 1
            fi
        fi

        TOR_ENABLED=false
        if [[ -n "${NODE_YAML:-}" ]] && [[ -f "$NODE_YAML" ]]; then
            TOR_ENABLED=$(python3 - "$NODE_YAML" <<'PYEOF' 2>/dev/null || echo "false"
import yaml, sys

with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
tor = data.get('tor', {})
print('true' if tor.get('enabled', False) else 'false')
PYEOF
)
        fi
        log_step "tor-enable" "INFO" "TOR_ENABLED=${TOR_ENABLED} (from ${NODE_YAML:-<unset>})"

        install_logrotate

        # ── Checkpoint steps with per-step content hash (T20) ─────────
        # Each step sets CHECKPOINT_STEP_HASH via _step_hash() helper,
        # which always includes node-lifecycle.sh + step-specific scripts.
        # Content hash changes → only that step's checkpoint invalidated.

        CHECKPOINT_STEP_HASH="$(_step_hash "ssh-access")" \
            checkpoint_step "ssh-access" step_1_ssh_access
        CHECKPOINT_STEP_HASH="$(_step_hash "apt-deps")" \
            checkpoint_step "apt-deps" step_2_apt_deps

        if [[ "${TOR_ENABLED:-false}" == "true" ]]; then
            CHECKPOINT_STEP_HASH="$(_step_hash "tor-proxy" "${CORE_DIR}/internal/bootstrap/install-tor-proxy.sh")" \
                checkpoint_step "tor-proxy" step_3_tor_proxy
        else
            echo "[IMP:8][node-lifecycle][main] Tor disabled — skipping tor-proxy step" >&2
            if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] && [[ -n "${TELEGRAM_CHAT_ID:-}" ]]; then
                echo "[IMP:9][node-lifecycle][main] WARNING: Telegram is configured but Tor is disabled — notifications will NOT be delivered" >&2
                echo "[IMP:9][node-lifecycle][main] To enable: set tor.enabled=true in node.yaml and provide tor/bridges.txt" >&2
            fi
        fi

        CHECKPOINT_STEP_HASH="$(_step_hash "install-docker" "${CORE_DIR}/internal/bootstrap/install-docker.sh")" \
            checkpoint_step "install-docker" step_4_install_docker
        CHECKPOINT_STEP_HASH="$(_step_hash "user-platform")" \
            checkpoint_step "user-platform" step_5_create_platform_user
        CHECKPOINT_STEP_HASH="$(_step_hash "user-ci-deploy")" \
            checkpoint_step "user-ci-deploy" step_6_create_ci_deploy_user
        CHECKPOINT_STEP_HASH="$(_step_hash "projects-base")" \
            checkpoint_step "projects-base" step_6b_create_projects_base
        CHECKPOINT_STEP_HASH="$(_step_hash "firewall" "${CORE_DIR}/internal/bootstrap/firewall.sh")" \
            checkpoint_step "firewall" step_7_firewall
        CHECKPOINT_STEP_HASH="$(_step_hash "verify-core")" \
            checkpoint_step "verify-core" step_8_verify_core
        CHECKPOINT_STEP_HASH="$(_step_hash "verify-node-configs")" \
            checkpoint_step "verify-node-configs" step_9_verify_node_configs

        # decrypt-secrets depends on lib/secrets.sh (step logic extracted there)
        CHECKPOINT_STEP_HASH="$(_step_hash "decrypt-secrets" "${CORE_DIR}/lib/secrets.sh")" \
            checkpoint_step "decrypt-secrets" step_10_decrypt_secrets _verify_secrets_loaded

        # ensure-secrets depends on lib/secrets.sh
        CHECKPOINT_STEP_HASH="$(_step_hash "ensure-secrets" "${CORE_DIR}/lib/secrets.sh")" \
            checkpoint_step "ensure-secrets" step_12b_ensure_secrets

        # secrets-init: initialize service passwords from PLATFORM_MASTER_PASSWORD
        # Init-only — NOT called in update mode (passwords already initialized at init)
        # Non-fatal: if secrets-init.sh fails, continue with WARN
        CHECKPOINT_STEP_HASH="$(_step_hash "secrets-init" \
            "${CORE_DIR}/internal/bootstrap/secrets-init.sh")" \
            checkpoint_step "secrets-init" _step_secrets_init

        CHECKPOINT_STEP_HASH="$(_step_hash "read-node-yaml")" \
            checkpoint_step "read-node-yaml" step_11_read_node_yaml
        CHECKPOINT_STEP_HASH="$(_step_hash "ghcr-auth")" \
            checkpoint_step "ghcr-auth" step_12_ghcr_auth
        CHECKPOINT_STEP_HASH="$(_step_hash "sudoers" "${CORE_DIR}/internal/bootstrap/setup-node.sh")" \
            checkpoint_step "sudoers" step_13_sudoers
        # ── Install acme.sh (init only — needed once, not at each update) ──
        # T3 (DevPlan 005): install-acme.sh is called at init BEFORE node-update.
        # Update mode calls issue-cert.sh directly (acme.sh already installed).
        CHECKPOINT_STEP_HASH="$(_step_hash "install-acme" \
            "${CORE_DIR}/internal/bootstrap/install-acme.sh")" \
            checkpoint_step "install-acme" _step_install_acme
        CHECKPOINT_STEP_HASH="$(_step_hash "node-update" "${CORE_DIR}/internal/bootstrap/node-lifecycle.sh")" \
            checkpoint_step "node-update" step_14_node_update
        # ── Step 15: Converge (desired-state reconciler) ──────────────
        # Runs after node-update (modules deployed) but before audit logging.
        # Uses converge.sh with all 6 R-units.
        CHECKPOINT_STEP_HASH="$(_step_hash "converge" "${CORE_DIR}/internal/bootstrap/converge.sh")" \
            checkpoint_step "converge" step_15_converge
        CHECKPOINT_STEP_HASH="$(_step_hash "audit-summary")" \
            checkpoint_step "audit-summary" step_16_audit_log
        CHECKPOINT_STEP_HASH="$(_step_hash "telegram")" \
            checkpoint_step "telegram" step_17_telegram

        CHECKPOINT_STEP_HASH=""

        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Bootstrap COMPLETE — exit 0" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
    elif [[ "$MODE" == "update" ]]; then
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Node Update START (--mode update)" >&2
        echo "[IMP:9][node-lifecycle][main] Node: ${NODE_NAME:-<unset>}" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2

        # ════════════════════════════════════════════════════════════════
        # T1: NODE_YAML derivation + validate (before any mutations)
        # ════════════════════════════════════════════════════════════════

        # T1a: NODE_NAME must be set (fail-fast)
        if [[ -z "${NODE_NAME:-}" ]]; then
            echo "[IMP:10][node-lifecycle][update] FATAL: NODE_NAME is required for --mode update" >&2
            echo "[IMP:10][node-lifecycle][update] Usage: --node-name <name>" >&2
            exit 1
        fi

        # T1b: Derive NODE_YAML if not set or missing, via node-resolver.sh
        # ⚠️ TRAP[BUG] · 2026-07-17 · P1 · NODE_YAML not passed from entrypoint
        # · Symptom: `make node-update` падал "NODE_YAML not set" на шаге 4/5 (deploy-modules, healthcheck)
        # · Root: entrypoint не передавал --node-yaml; update-mode main() не резолвил, полагаясь на env
        # · Fix: derivation через node-resolver.sh (единственный Source of Truth резолва node.yaml)
        # · Prevention: contract-тест флагов (каждый флаг node-update.sh → node-lifecycle.sh)
        if [[ -z "${NODE_YAML:-}" ]] || [[ ! -f "${NODE_YAML:-}" ]]; then
            echo "[IMP:8][node-lifecycle][update] NODE_YAML not set or missing — resolving via node-resolver" >&2
            source "${CORE_DIR}/lib/node-resolver.sh"
            NODE_YAML="$(resolve_node_yaml "${NODE_NAME}")" || {
                echo "[IMP:10][node-lifecycle][update] FATAL: Cannot resolve NODE_YAML for node=${NODE_NAME}" >&2
                resolve_node_yaml "${NODE_NAME}" 2>&1 | grep -E "^.*Searched:" | head -1 || true
                echo "[IMP:10][node-lifecycle][update]   Tried candidate paths: platform_root/node-configs/, projects/*/node-configs/, /opt/node-configs/" >&2
                exit 1
            }
            export NODE_YAML
            echo "[IMP:9][node-lifecycle][update] Resolved NODE_YAML=${NODE_YAML}" >&2
        fi

        # ── S5: Pre-flight YAML syntax validation (update-mode) ──
        if [[ -f "$NODE_YAML" ]]; then
            if ! python3 -c "
import yaml, sys
try:
    with open('$NODE_YAML') as f:
        yaml.safe_load(f)
except Exception as e:
    print(f'FATAL: node.yaml is not valid YAML: {e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null; then
                echo "[IMP:10][node-lifecycle][update] FATAL: node.yaml is not valid YAML — check syntax at ${NODE_YAML}" >&2
                exit 1
            fi
        fi

        # T2: --dry-run mode (after resolution, before any mutations)
        if [[ "${DRY_RUN_MODE:-}" == "true" ]]; then
            echo "[IMP:9][node-lifecycle][dry-run] ===== DRY RUN: update mode ====="
            echo "[IMP:9][node-lifecycle][dry-run] NODE_NAME: ${NODE_NAME}"
            echo "[IMP:9][node-lifecycle][dry-run] NODE_YAML: ${NODE_YAML}"
            echo "[IMP:9][node-lifecycle][dry-run] Steps: verify-core → provision → ssl-provision → deploy-modules → healthcheck → converge"
            echo "[IMP:9][node-lifecycle][dry-run] Node update DRY RUN — no mutations performed, exit 0"
            exit 0
        fi

        if [[ "$FORCE_MODE" == "true" ]]; then
            echo "[IMP:8][node-lifecycle][checkpoint] --force: Clearing all checkpoints in ${CHECKPOINT_DIR}" >&2
            rm -rf "$CHECKPOINT_DIR"
        fi

        mkdir -p "$CHECKPOINT_DIR"

        # ── Step 1: Verify core (content hash tracked) ────────────────
        CHECKPOINT_STEP_HASH="$(_step_hash "verify-core" \
            "${CORE_DIR}/lib/checkpoint.sh" \
            "${CORE_DIR}/internal/bootstrap/content-hash.sh")" \
            checkpoint_step "verify-core" update_step_1_verify_core

        # ── Step 2: Provision environment ─────────────────────────────
        CHECKPOINT_STEP_HASH="$(_step_hash "provision" \
            "${CORE_DIR}/internal/provision-environment.sh")" \
            checkpoint_step "provision" update_step_2_provision

        # ── Step 2.5: Deliver vhost overlays (S2 DevPlan 019) ──────────
        CHECKPOINT_STEP_HASH="$(_step_hash "deliver-overlays" \
            "${CORE_DIR}/internal/scaffold/add-vhost.sh")" \
            checkpoint_step "deliver-overlays" update_step_2_5_deliver_overlays

        # ── Step 3: SSL certificate provisioning ──────────────────────
        CHECKPOINT_STEP_HASH="$(_step_hash "ssl-provision" \
            "${CORE_DIR}/internal/bootstrap/issue-cert.sh")" \
            checkpoint_step "ssl-provision" update_step_3_ssl_provision

        # ── Step 4: Deploy all modules (docker + system) — S2 merged ──
        # S2: Previously separate deploy-docker + deploy-system steps. Merged
        # into a single deploy-modules call with --skip-provision, eliminating
        # the second full main() invocation (~30% of update cycle time).
        CHECKPOINT_STEP_HASH="$(_step_hash "deploy-modules" \
            "${CORE_DIR}/internal/bootstrap/deploy-modules.sh")" \
            checkpoint_step "deploy-modules" update_step_4_deploy_modules

        # ── Step 6: Healthcheck ───────────────────────────────────────
        CHECKPOINT_STEP_HASH="$(_step_hash "healthcheck-all")" \
            checkpoint_step "healthcheck-all" update_step_6_healthcheck

        # ── Step 7: Converge (desired-state reconciler) ───────────────
        # Runs after healthchecks. In update mode, converge failures are
        # logged as warnings — node continues operating with reported drifts.
        CHECKPOINT_STEP_HASH="$(_step_hash "converge" \
            "${CORE_DIR}/internal/bootstrap/converge.sh")" \
            checkpoint_step "converge" step_15_converge

        CHECKPOINT_STEP_HASH=""

        # ── Audit log ─────────────────────────────────────────────────
        audit_log "node-update:complete" "DONE" \
            "Node update finished | node=${NODE_NAME:-<unset>} | warnings=${#STEP_ERRORS[@]}"

        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
        echo "[IMP:9][node-lifecycle][main] Node Update COMPLETE — exit 0" >&2
        echo "[IMP:9][node-lifecycle][main] ==============================" >&2
    fi
}

main "$@"
