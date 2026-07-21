#!/usr/bin/env bash
# GREP_SUMMARY: deploy-modules docker-network proxy-net shared-db-net backup-net compose-up system-module install.sh ensure-spool verify-only sudoers-modules --modules severity critical warn exit-code topo-sort transitive-deps pre-pull parallel-pull batch-metadata batch-sudoers batch-orphan git-pull-cache parallel-healthcheck
# STRUCTURE: [--modules flag] → ensure_networks → ensure_spool_dirs → parse node.yaml → expand_transitive_deps(--modules) → S3 batch_module_metadata → separate system|docker → filter(--modules set) → system:install.sh + healthcheck → docker:_pre_pull_images(parallel) → _topo_sort.py(groups) → deploy_docker_group(parallel + S4 parallel healthcheck) → S6 _batch_generate_sudoers → S8 _batch_orphan_reconciliation → S9 git-pull-cache → severity_aggregate → [critical→exit 2|warn→exit 1|ok→exit 0]
# region MODULE_CONTRACT
## @purpose  Deploy all modules declared in node.yaml: create Docker networks, run system install.sh or docker compose up
## @scope    Called from node-lifecycle.sh --mode init/update; idempotent via docker network inspect + compose up -d
## @location core/internal/bootstrap/deploy-modules.sh — moved from core/bootstrap/deploy-modules.sh
## @invariant Verified 2026-07-09: 6/6 existing healthcheck.sh modules source ../../lib/healthcheck.sh
##   (platform-secrets has no healthcheck.sh — uses systemd service, not Docker module)
## @invariants
##   - Docker networks and canonical volumes created via provision-environment.sh (NOT local loop)
##   - Fallback to legacy ensure_docker_network loop if provisioner not found
##   - system modules: calls modules/<name>/install.sh (idempotent by design)
##   - docker modules: pre-pull images in parallel (A1), then docker compose up -d (no-op if unchanged)
##   - Pre-pull is non-fatal optimization — compose up -d retries pull if pre-pull failed
##   - healthcheck failures are logged but do NOT abort deploy
##   - node.yaml must be parsed before this script is called; NODE_YAML env var provides path
##   - spool dirs verified (not created) — provisioner creates them via platform-env.yaml volumes[]
##   - Missing spool dirs logged as WARN with recommendation to run `make provision`
##   - module sudoers generated from templates/sudo-whitelist.template via template-engine.sh render
## @rationale Networks must pre-exist before any compose up referencing them
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../lib/paths.sh"
source "${SCRIPT_DIR}/../../internal/audit/audit.sh" 2>/dev/null || true
source "${SCRIPT_DIR}/../../lib/docker.sh"

# 🧐 TRAP[DECISION] · 2026-07-09 · — · Staging via separate compose project, not --with-staging flag
# · Rejected: --with-staging flag in deploy-modules.sh · Reason: staging uses separate compose project
#   (staging-<name>) with +10000 port offset, separate networks — architectural decision, not script flag

# region PLATFORM_NETWORKS_LOADER
# Read PLATFORM_NETWORKS from platform-env.yaml (Single Source of Truth per P5)
_load_platform_networks() {
    local platform_env="${SCRIPT_DIR}/../../../platform-env.yaml"
    if [[ ! -f "$platform_env" ]]; then
        log_step "platform-env" "ERROR" "platform-env.yaml not found at ${platform_env}"
        return 1
    fi
    # Используем python3 (гарантирован после Step 2 bootstrap — python3-yaml installed in apt-deps)
    python3 -c "
import yaml
with open('${platform_env}') as f:
    data = yaml.safe_load(f)
for net in data.get('networks', []):
    print(net['name'])
" 2>/dev/null || {
        log_step "platform-env" "ERROR" "Failed to parse platform-env.yaml (python3+yaml required)"
        return 1
    }
}
PLATFORM_NETWORKS=()
while IFS= read -r net; do
    [[ -n "$net" ]] && PLATFORM_NETWORKS+=("$net")
done < <(_load_platform_networks)
readonly PLATFORM_NETWORKS
# endregion PLATFORM_NETWORKS_LOADER
readonly COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-4}"

# Global array tracking failed module names for severity-based exit code
FAILED_MODULE_NAMES=()

__LOG_PREFIX="deploy-modules"
source "${SCRIPT_DIR}/../../lib/logging.sh"

# 🧐 TRAP[DECISION] · 2026-07-17 · — · ghcr_login() moved to lib/docker.sh
# · Rejected: keeping ghcr_login() defined locally in deploy-modules.sh
# · Reason: ghcr_login() is now a library function in lib/docker.sh alongside
#   docker_login(). deploy-modules.sh sources lib/docker.sh at line 28 and
#   calls ghcr_login() at line 608 — the definition is no longer needed here.
# · Rev: if ghcr_login() signature changes, update lib/docker.sh, not this file.

# 🧐 TRAP[DECISION] · 2026-07-17 · — · ensure_docker_network() as runtime fallback
# · Rejected: removing ensure_docker_network() entirely (only provisioner should create networks)
# · Reason: provision-environment.sh --scope networks is the PRIMARY network creator
#   (reads PLATFORM_NETWORKS from platform-env.yaml). ensure_docker_network() is a runtime
#   fallback for CI scenarios where provisioner hasn't been run (e.g. first-time deploy
#   in a new context). It uses docker network inspect/create loop as belt-and-suspenders.
#   Removing it would break CI environments that skip provision step.
# · Rev: if provision-environment.sh becomes mandatory in ALL deploy paths (CI included),
#   remove this function and make network-precreation a hard requirement.
# region ENSURE_DOCKER_NETWORK
ensure_docker_network() {
    local network_name="$1"
    local driver="${2:-bridge}"

    if docker network inspect "$network_name" &>/dev/null 2>&1; then
        log_step "network:${network_name}" "SKIP" "Docker network '${network_name}' already exists"
        return 0
    fi

    log_step "network:${network_name}" "START" "Creating Docker network: ${network_name} (driver: ${driver})"
    docker network create --driver "$driver" "$network_name"
    log_step "network:${network_name}" "DONE" "Docker network '${network_name}' created"
}
# endregion ENSURE_DOCKER_NETWORK

# region ENSURE_SPOOL_DIRS
ensure_spool_dirs() {
    log_step "spool-dirs" "START" "Verifying platform spool and data directories (verify-only)"
    # 🧐 TRAP[DECISION] · 2026-07-17 · — · ensure_spool_dirs() verify-only
    # · Rejected: mkdir -p in deploy-modules.sh (creating dirs at deploy time)
    # · Reason: spool dirs are declared in platform-env.yaml volumes[] and created
    #   by provision-environment.sh --scope volumes (make provision). deploy-modules.sh
    #   should only verify existence, not create. mkdir -p here would mask missing
    #   platform-env.yaml entries — silent creation prevents detection of drift.
    # · Rev: if provision-environment.sh stops covering all spool dirs, reintroduce
    #   creation here with explicit WARN that provisioner is out of date

    local -a platform_dirs=("/var/log/platform/backup")
    for dir in "${platform_dirs[@]}"; do
        if [[ -d "$dir" ]]; then
            log_step "spool-dirs" "SKIP" "Already exists: ${dir}"
        else
            log_step "spool-dirs" "WARN" "Platform dir ${dir} не существует — запусти make provision"
        fi
    done

    local modules_dir="${PATHS_MODULES_DIR}"
    local spool_found=0
    local spool_missing_modules=()

    for module_yaml in "${modules_dir}"/*/module.yaml; do
        [[ -f "$module_yaml" ]] || continue

        local module_name
        module_name=$(basename "$(dirname "$module_yaml")")

        # Prefer spool_dir over spool_volume — spool_dir is an absolute path.
        # spool_volume is a Docker volume name (not a path) and is verified for reference only.
        local spool_path
        spool_path=$(grep -E '^spool_dir:' "$module_yaml" | head -1 | awk -F': ' '{print $2}' | tr -d ' "' || true)
        if [[ -z "$spool_path" ]]; then
            spool_path=$(grep -E '^spool_volume:' "$module_yaml" | head -1 | awk -F': ' '{print $2}' | tr -d ' "' || true)
        fi

        if [[ -n "$spool_path" ]]; then
            # spool_dir: none = stateless module (explicit declaration, no WARN)
            if [[ "$spool_path" == "none" ]]; then
                log_step "spool-dirs" "INFO" "Stateless module (declared spool_dir: none): ${module_name}"
                continue
            fi

            spool_found=$((spool_found + 1))

            if [[ -d "$spool_path" ]]; then
                log_step "spool-dirs" "SKIP" "Already exists (${module_name}): ${spool_path}"
            else
                log_step "spool-dirs" "WARN" "Module ${module_name} spool ${spool_path} не существует — запусти make provision"
            fi
        else
            # Log warning for modules without spool_dir or spool_volume
            # (some modules legitimately don't need spool dirs, e.g. nginx reverse proxy)
            # 🧐 TRAP[DECISION] · 2026-07-17 · — · WARN preserved for drift detection
            # · Rejected: silent skip (would hide missing decl in new modules)
            # · Reason: absence of spool_dir is legitimate for some modules, but WARN
            #   ensures new modules without declarative metadata are flagged.
            # · Rev: if all modules explicitly declare spool_dir, switch to CRITICAL
            spool_missing_modules+=("$module_name")
            log_step "spool-dirs" "WARN" "No spool_dir or spool_volume in ${module_name}/module.yaml — no spool dir to verify"
        fi
    done

    # 🧐 TRAP[DECISION] · 2026-07-17 · — · wal-archive verify-only (deferred to provisioner)
    # · Rejected: mkdir -p /var/lib/platform/wal-archive in deploy-modules.sh
    # · Reason: wal-archive is now in platform-env.yaml volumes[] — provisioner creates it.
    #   Verify-only to detect drift if provisioner hasn't been run.
    # · Rev: if wal-archive dir is consistently missing, ensure platform-env.yaml entry is correct
    if [[ -d "/var/lib/platform/wal-archive" ]]; then
        log_step "spool-dirs" "SKIP" "Already exists: /var/lib/platform/wal-archive"
    else
        log_step "spool-dirs" "WARN" "postgres wal-archive /var/lib/platform/wal-archive не существует — запусти make provision"
    fi

    if [[ -d "${modules_dir}/observability" ]]; then
        local -a obs_dirs=(
            "/var/lib/platform/grafana-data"
            "/var/lib/platform/prometheus-data"
            "/var/lib/platform/loki-data"
        )
        for dir in "${obs_dirs[@]}"; do
            if [[ -d "$dir" ]]; then
                log_step "spool-dirs" "SKIP" "Already exists (observability): ${dir}"
            else
                log_step "spool-dirs" "WARN" "observability dir ${dir} не существует — запусти make provision"
            fi
        done
    fi

    if [[ "$spool_found" -eq 0 ]]; then
        log_step "spool-dirs" "WARN" "No module.yaml spool paths found — verifying hardcoded fallback dirs"
        local -a fallback_dirs=(
            "/var/lib/platform/postgres-data"
            "/var/lib/platform/backup-spool"
            "/var/lib/platform/backup-spool/postgres"
            "/var/lib/platform/backup-spool/app-data"
        )
        for dir in "${fallback_dirs[@]}"; do
            if [[ -d "$dir" ]]; then
                log_step "spool-dirs" "SKIP" "Already exists: ${dir}"
            else
                log_step "spool-dirs" "WARN" "Fallback dir ${dir} не существует — запусти make provision"
            fi
        done
    fi

    log_step "spool-dirs" "DONE" "Spool dir verification complete (verify-only, spool_field_count=${spool_found})"
}
# endregion ENSURE_SPOOL_DIRS

# region ENSURE_CONTEXT_REPO
ensure_context_repo() {
    local node_yaml="$1"
    local context_name
    context_name=$(grep "^context:" "$node_yaml" | awk '{print $2}' 2>/dev/null || echo "")

    if [[ -z "$context_name" ]]; then
        log_step "context-repo" "SKIP" "No context field in node.yaml — context repo will not be cloned"
        return 0
    fi

    local context_path="/opt/${context_name}/platform"

    if [[ -d "$context_path" ]]; then
        # S9: Git pull caching — skip if pulled within last 5 minutes
        local last_pull_file="/var/lib/platform/.context-pull-ts"
        local now
        now=$(date +%s)
        local last_pull=0
        [[ -f "$last_pull_file" ]] && last_pull=$(cat "$last_pull_file")
        if [[ $((now - last_pull)) -lt 300 ]]; then
            log_step "context-repo" "SKIP" "Pulled recently (${last_pull}) — skipping git pull (S9 cache)"
            return 0
        fi
        log_step "context-repo" "INFO" "Context repo exists: ${context_path} — pulling latest"
        git -C "$context_path" pull --ff-only 2>/dev/null || \
            log_step "context-repo" "WARN" "git pull failed (non-fatal): ${context_path}"
        echo "$now" > "$last_pull_file"
        return 0
    fi

    local context_repo_url
    context_repo_url=$(python3 -c "
import yaml, sys
with open('${node_yaml}') as f:
    data = yaml.safe_load(f)
repos = data.get('repos', {})
print(repos.get('platform', ''))
" 2>/dev/null || echo "")

    if [[ -n "$context_repo_url" ]]; then
        log_step "context-repo" "INFO" "Cloning context repo: ${context_repo_url} → ${context_path}"
        git clone "$context_repo_url" "$context_path" 2>/dev/null || {
            log_step "context-repo" "WARN" "git clone failed — create ${context_path} manually or add repos.platform to node.yaml"
            return 1
        }
        log_step "context-repo" "DONE" "Context repo cloned: ${context_path}"
    else
        log_step "context-repo" "WARN" "No repos.platform in node.yaml — context overlay auto-resolve will fail"
        log_step "context-repo" "WARN" "Create ${context_path} manually or add repos.platform to node.yaml"
    fi
}
# endregion ENSURE_CONTEXT_REPO

# region GENERATE_MODULE_SUDOERS
generate_module_sudoers() {
    local module_name="$1"
    local module_dir
    module_dir="$(realpath "${SCRIPT_DIR}/../../modules/${module_name}")"

    # Render template with module vars
    local template="${SCRIPT_DIR}/../../templates/sudo-whitelist.template"
    if [[ ! -f "$template" ]]; then
        template="${PLATFORM_ROOT}/core/templates/sudo-whitelist.template"
    fi

    local sudoers_file="/etc/sudoers.d/platform-${module_name}"
    local rendered
    rendered="$(mktemp /tmp/platform-sudoers-rendered-XXXXXX)"
    chmod 0440 "$rendered"

    log_step "sudoers:${module_name}" "START" "Rendering template for module ${module_name}"

    local engine="${SCRIPT_DIR}/../template-engine.sh"
    if ! bash "$engine" render "$template" "$rendered" \
        "MODULE_NAME=${module_name}" "PLATFORM_ROOT=${PLATFORM_ROOT:-/opt/platform}"; then
        log_step "sudoers:${module_name}" "FAIL" "Template render FAILED"
        rm -f "$rendered"
        return 1
    fi

    # Generate sudoers from rendered file (remainder of existing logic)
    local tmp_sudoers
    tmp_sudoers="$(mktemp /tmp/platform-sudoers-module-XXXXXX)"
    chmod 0440 "$tmp_sudoers"

    cat > "$tmp_sudoers" <<EOF
# platform module sudoers — ${module_name}
# Generated by deploy-modules.sh at $(date -u '+%Y-%m-%dT%H:%M:%SZ')
# Source: templates/sudo-whitelist.template (rendered via template-engine)
# DO NOT edit manually — managed by core bootstrap

EOF

    local make_bin="/usr/bin/make"
    local module_abs_dir
    module_abs_dir="$(realpath "${module_dir}")"

    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue

        local role action _path
        read -r role action _path <<< "$line" || continue
        [[ -z "$role" || -z "$action" ]] && continue

        if [[ "$action" == make:* ]]; then
            local target="${action#make:}"
            local username
            case "$role" in
                owner)   username="platform" ;;
                agent)   username="platform-agent" ;;
                ci)      username="ci-deploy" ;;
                monitor) username="platform-monitor" ;;
                *)       username="$role" ;;
            esac

            printf '%s ALL=(root) NOPASSWD: %s -C %s %s\n' \
                "$username" "$make_bin" "$module_abs_dir" "$target" >> "$tmp_sudoers"
        fi
    done < <(grep -v '^[[:space:]]*#' "$rendered" | grep -v '^[[:space:]]*$')

    rm -f "$rendered"

    if ! visudo -c -f "$tmp_sudoers" &>/dev/null 2>&1; then
        local visudo_err
        visudo_err="$(visudo -c -f "$tmp_sudoers" 2>&1 || true)"
        log_step "sudoers:${module_name}" "FAIL" "visudo -c FAILED: ${visudo_err} — original NOT touched"
        rm -f "$tmp_sudoers"
        return 1
    fi

    mv "$tmp_sudoers" "$sudoers_file"
    log_step "sudoers:${module_name}" "DONE" "Module sudoers generated: ${sudoers_file}"
}
# endregion GENERATE_MODULE_SUDOERS

# region DEPLOY_SYSTEM_MODULE
deploy_system_module() {
    local module_name="$1"
    local overlay_dir="${2:-}"

    # env_requires gate (T3): fail fast before any deploy action
    if ! _check_env_requires "$module_name"; then
        return 1
    fi

    local install_script="${PATHS_MODULES_DIR}/${module_name}/install.sh"
    if [[ ! -f "$install_script" ]]; then
        log_step "system:${module_name}" "FAIL" "install.sh not found: ${install_script}"
        return 1
    fi

    log_step "system:${module_name}" "START" "Deploying system module: ${module_name}"
    export PLATFORM_CONFIG_OVERLAY="$overlay_dir"
    if ! invoke_module_interface "$module_name" install; then
        log_step "system:${module_name}" "FAIL" "install.sh exited with error — check logs above"
        return 1
    fi
    log_step "system:${module_name}" "DONE" "System module deployed: ${module_name}"
}
# endregion DEPLOY_SYSTEM_MODULE

# region CHECK_IMAGE_EXISTS
## @purpose  Verify Docker image exists in registry before attempting deploy.
##           Uses docker manifest inspect (no layer download) for fast check.
## @param    $1  image_name:tag  Full image reference (e.g. ghcr.io/org/image:tag)
## @return   0 if image manifest found, 1 if not found or inspect failed
## @complexity 1 — single docker command
_check_image_exists() {
    local image_ref="$1"
    log_step "image-check" "START" "Verifying image exists: ${image_ref}"
    if docker manifest inspect "$image_ref" &>/dev/null 2>&1; then
        log_step "image-check" "DONE" "Image found in registry: ${image_ref}"
        return 0
    fi
    log_step "image-check" "FAIL" "Image NOT FOUND in registry: ${image_ref}"
    return 1
}
# endregion CHECK_IMAGE_EXISTS

# region DEPLOY_DOCKER_MODULE
deploy_docker_module() {
    local module_name="$1"
    local overlay_dir="${2:-}"

    # env_requires gate (T3): fail fast before any deploy action
    if ! _check_env_requires "$module_name"; then
        return 1
    fi

    local module_dir="${PATHS_MODULES_DIR}/${module_name}"
    local compose_file="${module_dir}/compose.yaml"
    if [[ ! -f "$compose_file" ]]; then
        compose_file="${module_dir}/docker-compose.yaml"
    fi
    if [[ ! -f "$compose_file" ]]; then
        compose_file="${module_dir}/docker-compose.base.yml"
    fi

    if [[ ! -f "$compose_file" ]]; then
        log_step "docker:${module_name}" "FAIL" "compose file not found in ${module_dir} (tried compose.yaml, docker-compose.yaml, docker-compose.base.yml)"
        return 1
    fi

    log_step "docker:${module_name}" "START" "Deploying docker module: ${module_name}"

    local env_file="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
    local platform_env="${PLATFORM_ROOT:-/opt/platform}/.env"
    local compose_args=("-f" "$compose_file")
    if [[ -f "$env_file" ]]; then
        compose_args+=("--env-file" "$env_file")
    fi
    if [[ -f "$platform_env" ]]; then
        compose_args+=("--env-file" "$platform_env")
    fi
    if [[ -n "$overlay_dir" ]] && [[ -f "${overlay_dir}/compose.override.yaml" ]]; then
        compose_args+=("-f" "${overlay_dir}/compose.override.yaml")
    fi

    if [[ "$module_name" == "hermes-agent" ]]; then
        local legacy_container="hermes-base-agent"
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$legacy_container"; then
            log_step "docker:${module_name}" "INFO" "Stopping legacy container: ${legacy_container}"
            docker stop "$legacy_container" 2>/dev/null || true
            docker rm "$legacy_container" 2>/dev/null || true
            log_step "docker:${module_name}" "INFO" "Legacy container removed: ${legacy_container}"
        fi
    fi

    # Pre-deploy image check for hermes-agent — resolve actual images from compose config (T4)
    # ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Hardcoded hermes images drifted from compose
    # · Symptom: hermes-agent deployed with stale image (tronyx161/hermes-agent-tronyx-lab:latest
    #   vs tronyxlab/hermes-agent-context:v2026.7.1), no tty/command → restart loop 101 times
    # · Root: hardcoded image names duplicated knowledge — compose and deploy-modules.sh diverged
    #   when CONTEXT_IMAGE changed in .env / secrets.env
    # · Fix: derive images from `docker compose config --images` (single source of truth)
    # · Prevention: deploy-modules.sh must NOT hardcode any image names — always resolve from compose
    if [[ "$module_name" == "hermes-agent" ]]; then
        # 🧐 TRAP[DECISION] · 2026-07-21 · — · Replace FAIL with fallback build for hermes-agent L2 images
        # · Rejected: FAIL + print build instructions (old behavior — required manual make targets)
        # · Reason: automatic local build on 404 reduces deploy cycle time and eliminates manual intervention
        # · Rev: if docker compose build becomes unreliable in CI context, revert to explicit build step
        local -a hermes_images=()
        mapfile -t hermes_images < <(docker compose "${compose_args[@]}" --profile "$module_name" config --images 2>/dev/null || true)
        if [[ ${#hermes_images[@]} -eq 0 ]]; then
            log_step "docker:${module_name}" "FAIL" "No images resolved from compose config"
            return 1
        fi
        local _all_found=true
        for _img in "${hermes_images[@]}"; do
            if ! _check_image_exists "$_img"; then
                _all_found=false
                log_step "docker:${module_name}" "WARN" "Pre-built image not found: ${_img} — will build locally"
            fi
        done
        if ! $_all_found; then
            # Ensure L1 base image exists locally (required for L1→L2 build)
            if ! docker image inspect hermes-agent-base:latest &>/dev/null 2>&1; then
                log_step "docker:${module_name}" "WARN" "L1 base image not found locally — attempting pull from GHCR"
                if ! docker pull ghcr.io/tronyx161/hermes-agent-base:latest 2>/dev/null; then
                    log_step "docker:${module_name}" "BUILD" "L1 pull failed — building L1 from source"
                    if ! docker compose "${compose_args[@]}" --profile "$module_name" -f "${module_dir}/docker-compose.base.yml" build \
                        --build-arg CONTEXT="${CONTEXT:-personal}" 2>&1; then
                        log_step "docker:${module_name}" "FAIL" "L1 build failed"
                        return 1
                    fi
                fi
            fi
            log_step "docker:${module_name}" "BUILD" "Building hermes-agent L1→L2 locally (fallback)"
            docker compose "${compose_args[@]}" --profile "$module_name" build 2>&1 || {
                log_step "docker:${module_name}" "FAIL" "Local build failed"
                return 1
            }
        fi
    fi

    if [[ "$module_name" == "observability" ]]; then
        # 🧐 TRAP[DECISION] · 2026-07-17 · — · Dynamic service list via docker compose config --services
        # · Rejected: hardcoded list (drift vector when services are added/removed in docker-compose.base.yml)
        # · Reason: docker compose config --services is the single source of truth for the compose file
        # · Rev: if compose file is broken/unparseable, fallback is empty list (no cleanup)
        local -a obs_containers
        mapfile -t obs_containers < <(docker compose -f "$compose_file" config --services 2>/dev/null)
        # Cache docker ps to avoid N socket calls (H1 fix)
        local _all_containers
        _all_containers=$(docker ps -a --format '{{.Names}}' 2>/dev/null)
        for cname in "${obs_containers[@]}"; do
            if grep -qx "$cname" <<< "$_all_containers"; then
                docker stop "$cname" 2>/dev/null || true
                docker rm "$cname" 2>/dev/null || true
                log_step "docker:${module_name}" "INFO" "Cleaned up pre-existing container: ${cname}"
            fi
        done
    fi

    # ── Orphan container reconciliation (T4) ──
    # Before compose up, check if any service container_name is occupied by a container from
    # a DIFFERENT compose project (foreign). If so, stop + rm it to prevent name-conflict failure.
    # compose up --remove-orphans only removes services NOT in the current compose file, it does
    # NOT handle foreign containers with matching names from other compose projects.
    # ⚠️ TRAP[BUG] · 2026-07-17 · P1 · Foreign container blocks compose up, restart-loop 101
    # 🧐 TRAP[DECISION] · 2026-07-21 · — · Per-module orphan detection kept as pre-deploy safety
    # · Rejected: removing inline orphan code in favor of batch-only (S8 approach)
    # · Reason: inline code runs BEFORE compose up -d and prevents "container name already in use"
    #   errors. _batch_orphan_reconciliation() runs AFTER all modules deploy — handles post-deploy
    #   cleanup but doesn't prevent pre-deploy conflicts. Both are needed for correctness + optimization.
    # · Rev: if compose up -d becomes resilient to name conflicts (docker compose v3?), remove inline code
    # · Symptom: hermes-agent container_name occupied by container from different compose project
    # · Root: no pre-up check — compose up fails silently on name conflict, stale container lives
    # · Fix: reconcile foreign containers by stopping/removing them before compose up
    local _orphan_lines
    _orphan_lines=$(python3 -c "
import json, subprocess, sys

module_name = '${module_name}'
# Build docker compose config command
docker_cmd = ['docker', 'compose']
docker_cmd += '${compose_args[*]}'.split()
docker_cmd += ['--profile', module_name, 'config', '--format', 'json']
try:
    r = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        sys.exit(0)
    cfg = json.loads(r.stdout)
except Exception:
    sys.exit(0)

# Get existing container names
ps_r = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}'], capture_output=True, text=True, timeout=15)
existing = set(ps_r.stdout.splitlines())

for svc in cfg.get('services', {}).values():
    cname = svc.get('container_name', '')
    if not cname:
        cname = svc.get('name', '')
    if not cname or cname not in existing:
        continue
    # Check compose project label on the existing container
    ins_r = subprocess.run(
        ['docker', 'inspect', '--format', '{{index .Config.Labels \"com.docker.compose.project\"}}', cname],
        capture_output=True, text=True, timeout=15)
    proj = ins_r.stdout.strip()
    if not proj or proj != module_name:
        print(f'{cname}|{proj}')
" 2>/dev/null || true)

    if [[ -n "$_orphan_lines" ]]; then
        while IFS='|' read -r _cname _project; do
            [[ -z "$_cname" ]] && continue
            log_step "docker:${module_name}" "INFO" "Reconciling orphan container: ${_cname} (project=${_project:-<none>})"
            docker stop "$_cname" 2>/dev/null || true
            docker rm "$_cname" 2>/dev/null || true
            log_step "docker:${module_name}" "INFO" "Orphan container removed: ${_cname}"
        done <<< "$_orphan_lines"
    fi

    # 🧐 TRAP[DECISION] · 2026-07-16 · — · --profile required for standalone base.yml deploy
    # · Rejected: removing profiles from base.yml (would break root docker-compose.yml include)
    # · Reason: all module base.yml files use profiles:[module-name]; standalone deploy must pass --profile
    # · Rev: if compose file format changes to not require profiles, remove this flag
    compose_args+=("--profile" "$module_name")
    # Export NGINX_OVERLAY_DIR for nginx module so overlay vhosts from node-configs are mounted
    if [[ "$module_name" == "nginx" ]] && [[ -n "$overlay_dir" ]]; then
        export NGINX_OVERLAY_DIR="$overlay_dir"
    fi
    docker compose "${compose_args[@]}" up -d --remove-orphans
    sleep 1
    log_step "docker:${module_name}" "DONE" "Docker module running: ${module_name}"
}
# endregion DEPLOY_DOCKER_MODULE

# region WAIT_FOR_READINESS
wait_for_readiness() {
    local module_name="$1"
    local max_attempts="${2:-15}"
    local interval_sec="${3:-2}"

    log_step "wait:${module_name}" "START" "Waiting for readiness (max_attempts=${max_attempts}, interval=${interval_sec}s)"

    local attempt=0
    while [[ $attempt -lt $max_attempts ]]; do
        if invoke_module_interface "$module_name" healthcheck readiness 2>/dev/null; then
            log_step "wait:${module_name}" "DONE" "Module ready after $((attempt + 1)) attempts"
            return 0
        fi
        attempt=$((attempt + 1))
        if [[ $attempt -lt $max_attempts ]]; then
            sleep "$interval_sec"
        fi
    done

    log_step "wait:${module_name}" "WARN" "Readiness timeout after ${max_attempts} attempts — continuing (non-fatal)"
    return 1
}
# endregion WAIT_FOR_READINESS

readonly HEALTHCHECK_MAX_RETRIES=4
readonly HEALTHCHECK_RETRY_INTERVAL=3

# region RUN_HEALTHCHECK
run_healthcheck() {
    local module_name="$1"
    local install_type="$2"

    log_step "health:${module_name}" "START" "Running healthcheck for: ${module_name}"
    local attempt=0 hc_output
    while [[ $attempt -lt $HEALTHCHECK_MAX_RETRIES ]]; do
        hc_output="$(invoke_module_interface "$module_name" healthcheck liveness 2>&1)" && {
            log_step "health:${module_name}" "DONE" "Healthcheck PASS (attempt $((attempt + 1))/${HEALTHCHECK_MAX_RETRIES})"
            return 0
        }
        attempt=$((attempt + 1))
        if [[ $attempt -eq 1 ]]; then
            log_step "health:${module_name}" "DIAG" "Healthcheck stderr: ${hc_output}"
        fi
        if [[ $attempt -lt $HEALTHCHECK_MAX_RETRIES ]]; then
            log_step "health:${module_name}" "INFO" "Healthcheck attempt ${attempt}/${HEALTHCHECK_MAX_RETRIES} failed, retrying in ${HEALTHCHECK_RETRY_INTERVAL}s..."
            sleep "$HEALTHCHECK_RETRY_INTERVAL"
        fi
    done

    log_step "health:${module_name}" "WARN" "Healthcheck FAILED after ${HEALTHCHECK_MAX_RETRIES} attempts — last error: ${hc_output}"
}
# endregion RUN_HEALTHCHECK

# region PARSE_NODE_YAML
parse_modules_from_node_yaml() {
    local node_yaml="$1"

    python3 - <<PYEOF
import yaml, sys

with open('${node_yaml}') as f:
    data = yaml.safe_load(f)

modules = data.get('modules', {})
if isinstance(modules, dict):
    for name, value in modules.items():
        if isinstance(value, dict):
            enabled = str(value.get('enabled', True)).lower()
            overlay = value.get('config_overlay', '')
        else:
            enabled = str(value).lower() if not isinstance(value, bool) else str(value).lower()
            overlay = ''
        print(f"{name}:{enabled}:{overlay}")
elif isinstance(modules, list):
    for m in modules:
        name = m.get('name', '')
        enabled = str(m.get('enabled', True)).lower()
        overlay = m.get('config_overlay', '')
        print(f"{name}:{enabled}:{overlay}")
PYEOF
}
# endregion PARSE_NODE_YAML

# region DETECT_MODULE_TYPE
detect_install_type() {
    local module_name="$1"
    local module_yaml="${PATHS_MODULES_DIR}/${module_name}/module.yaml"

    if [[ ! -f "$module_yaml" ]]; then
        echo "unknown"
        return 0
    fi

    python3 -c "
import yaml
with open('${module_yaml}') as f:
    d = yaml.safe_load(f)
print(d.get('install_type', 'unknown'))
"
}
# endregion DETECT_MODULE_TYPE

# region GET_MODULE_SEVERITY
## @purpose  Read severity field from module.yaml (critical|warn, default warn)
## @io       str (module_name) → stdout: "critical" or "warn"; return 0
## @complexity 1 — single python3 call
_get_module_severity() {
    local module_name="$1"
    local module_yaml="${PATHS_MODULES_DIR}/${module_name}/module.yaml"
    if [[ ! -f "$module_yaml" ]]; then
        echo "warn"
        return 0
    fi
    python3 -c "
import yaml
with open('${module_yaml}') as f:
    d = yaml.safe_load(f)
print(d.get('severity', 'warn'))
"
}
# endregion GET_MODULE_SEVERITY

# region BATCH_MODULE_METADATA
## @purpose  S3: One python3 call returning name:install_type:severity for ALL modules.
##           Replaces per-module detect_install_type() and _get_module_severity() calls
##           when enriched topo-sort result is unavailable.
## @io       stdout: "name:install_type:severity" per line (sorted by module dir)
## @complexity 1 — single python3 glob+read loop
_batch_module_metadata() {
    python3 -c "
import yaml
from pathlib import Path
modules_dir = Path('${PATHS_MODULES_DIR}')
for yf in sorted(modules_dir.glob('*/module.yaml')):
    with open(yf) as f:
        d = yaml.safe_load(f)
    name = d.get('name', yf.parent.name)
    itype = d.get('install_type', 'unknown')
    sev = d.get('severity', 'warn')
    print(f'{name}:{itype}:{sev}')
"
}
# endregion BATCH_MODULE_METADATA

# region CHECK_ENV_REQUIRES
## @purpose  Read secrets-manifest.yaml and verify all secrets required by the
##           given module (consumers includes module_name, tier ∈ {required, generated})
##           are non-empty in current process env OR in the secrets env file.
##           Fail-fast before any deploy action. Manifest-driven per Plan 018.
## @io       str (module_name) → stdout: comma-separated missing vars (on failure); return 0/1
## @complexity 1 — single python3 call per module (YAML manifest lookup)
## @invariants
##   - Checks both process env (${!var}) and SECRETS_ENV_FILE (secrets.env)
##   - Uses secrets-manifest.yaml consumers[] to determine per-module secrets
##   - If manifest absent → return 0 (graceful degradation, SSoT not yet available)
##   - Missing required vars → log_step FAIL with list + return 1
##   - Incident 2026-07-17: minio deployed with empty MINIO_ROOT_USER/PASSWORD → Access Denied
## @rationale Manifest-driven approach replaces module.yaml env_requires parsing.
##            secrets-manifest.yaml is the Single Source of Truth. Gate validates
##            bidirectional consistency between module.yaml env_requires and manifest.
_check_env_requires() {
    local module_name="$1"
    local manifest="${PATHS_CORE_DIR}/secrets-manifest.yaml"
    if [[ ! -f "$manifest" ]]; then
        log_step "env-gate:${module_name}" "INFO" "Manifest not found at ${manifest} — skipping env check (graceful degradation)"
        return 0
    fi

    local _missing
    _missing=$(python3 -c "
import yaml, os, sys
with open('${manifest}') as f:
    data = yaml.safe_load(f)
secrets = data.get('secrets', [])
# Find all secrets where consumers includes this module AND tier ∈ {required, generated}
module_secrets = [s for s in secrets if '${module_name}' in s.get('consumers', [])
                  and s.get('tier') in ('required', 'generated')]
if not module_secrets:
    sys.exit(0)
secrets_file = os.environ.get('SECRETS_ENV_FILE', '/run/platform/secrets.env')
_env_map = {}
if os.path.isfile(secrets_file):
    with open(secrets_file) as sf:
        for line in sf:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                _env_map[k.strip()] = v.strip()
missing = [s['name'] for s in module_secrets
           if not os.environ.get(s['name'], '') and not _env_map.get(s['name'], '')]
if missing:
    print(','.join(missing))
    sys.exit(1)
sys.exit(0)
")
    local _py_exit=$?
    if [[ "$_py_exit" -ne 0 ]] && [[ -n "$_missing" ]]; then
        log_step "env-gate:${module_name}" "FAIL" "Missing required env vars: ${_missing} — add to SOPS secrets"
        return 1
    fi
    return 0
}
# endregion CHECK_ENV_REQUIRES

# region VALIDATE_SECRET_CHARSETS
## @purpose  Validate all secrets with charset field in secrets-manifest.yaml match their declared regex charset.
##           Fails fast before any docker compose up if any secret violates its charset constraint.
## @io       None (reads secrets-manifest.yaml + env vars) → return 0/1
## @complexity 2 — single python3 call iterating over manifest secrets
## @invariants
##   - Only secrets with explicit charset field are validated (no charset = skip)
##   - Empty/missing env vars are skipped (checked separately by _check_env_requires)
##   - Uses re.match (full string match, not re.search)
##   - Graceful degradation: if manifest file not found → WARN + return 0
##   - IMP:8 log for OK, IMP:9 for FAIL (stderr)
## @rationale Charset constraint prevents pgbouncer crash-loop from special characters in POSTGRES_PASSWORD.
##            Validation happens at deploy time (not decrypt time) because secrets-manifest.yaml is consumed
##            by deploy-modules.sh and this is the last checkpoint before docker compose up.
_validate_secret_charsets() {
    local manifest="${PLATFORM_ROOT}/core/secrets-manifest.yaml"
    local failed=0

    if [[ ! -f "$manifest" ]]; then
        log_step "charset" "WARN" "Manifest not found at ${manifest} — skipping charset validation (graceful degradation)"
        return 0
    fi

    python3 -c "
import yaml, os, sys, re
with open('${manifest}') as f:
    data = yaml.safe_load(f)
failed = 0
for s in data.get('secrets', []):
    charset = s.get('charset', '')
    if not charset:
        continue
    name = s['name']
    val = os.environ.get(name, '')
    if not val:
        continue
    if not re.match(charset, val):
        print(f'[IMP:9][charset] FAIL: {name} does not match charset {charset}', file=sys.stderr)
        failed += 1
    else:
        print(f'[IMP:8][charset] OK: {name} matches {charset}', file=sys.stderr)
sys.exit(failed)
" || {
        log_step "charset" "FAIL" "Secret charset validation failed — aborting deploy"
        return 1
    }

    log_step "charset" "DONE" "All secrets passed charset validation"
    return 0
}
# endregion VALIDATE_SECRET_CHARSETS

# region EXPAND_TRANSITIVE_DEPS
## @purpose  Expand comma-separated module list with transitive depends_on using BFS over module.yaml DAG
## @io       str (comma-separated module names) → stdout: space-separated expanded list
## @complexity 3 — O(V+E) BFS over module DAG; validates all seed modules exist
## @errors   Prints ERROR to stderr and exits 1 if any seed module is unknown
## @invariants
##   - Only modules with module.yaml files are considered (system and docker)
##   - Unknown seed modules → stderr + exit 1
##   - Modules with no depends_on have empty dependency lists
##   - Circular deps are not trapped (BFS converges on visited set)
_expand_transitive_deps() {
    local modules_filter="$1"
    python3 -c "
import yaml, sys
from pathlib import Path

modules_dir = '${PATHS_MODULES_DIR}'
seed_modules = [m.strip() for m in '$modules_filter'.split(',') if m.strip()]

if not seed_modules:
    sys.exit(0)

# Build DAG from all module.yaml files (system + docker)
modules_path = Path(modules_dir)
dag = {}
for yf in sorted(modules_path.glob('*/module.yaml')):
    with open(yf) as f:
        data = yaml.safe_load(f)
    if data is None:
        continue
    name = data.get('name', yf.parent.name)
    deps = data.get('depends_on')
    if isinstance(deps, list):
        dag[name] = [d for d in deps if isinstance(d, str)]
    else:
        dag[name] = []

# Validate: all seed modules must exist in DAG
for m in seed_modules:
    if m not in dag:
        print(f'ERROR: Unknown module: {m}', file=sys.stderr)
        sys.exit(1)

# BFS to find transitive closure through depends_on
expanded = set(seed_modules)
queue = list(seed_modules)
while queue:
    node = queue.pop(0)
    for dep in dag.get(node, []):
        if dep not in expanded:
            expanded.add(dep)
            queue.append(dep)

print(' '.join(sorted(expanded)))
"
}
# endregion EXPAND_TRANSITIVE_DEPS

# region DEPLOY_DOCKER_GROUP
deploy_docker_group() {
    local -a entries=("$@")
    local -a pids=()
    local -a names=()
    local parallel_limit="${COMPOSE_PARALLEL_LIMIT:-4}"
    local group_deployed=0 group_failed=0

    log_step "parallel" "INFO" "Parallel limit: ${parallel_limit}, modules: ${#entries[@]}"

    for entry in "${entries[@]}"; do
        local mod_name="${entry%%:*}"
        local mod_overlay="${entry#*:}"
        [[ "$mod_overlay" == "$mod_name" ]] && mod_overlay=""

        while [[ ${#pids[@]} -ge $parallel_limit ]]; do
            for i in "${!pids[@]}"; do
                if ! kill -0 "${pids[$i]}" 2>/dev/null; then
                    wait "${pids[$i]}"
                    if [[ $? -eq 0 ]]; then
                        group_deployed=$(( group_deployed + 1 ))
                    else
                        group_failed=$(( group_failed + 1 ))
                        FAILED_MODULE_NAMES+=("${names[$i]}")
                    fi
                    unset 'pids[$i]'
                    unset 'names[$i]'
                fi
            done
            pids=("${pids[@]}")
            names=("${names[@]}")
            [[ ${#pids[@]} -ge $parallel_limit ]] && sleep 1
        done

        (
            deploy_docker_module "$mod_name" "$mod_overlay" && exit 0 || exit 1
        ) &
        pids+=($!)
        names+=("$mod_name")
    done

    local i
    for i in "${!pids[@]}"; do
        wait "${pids[$i]}"
        if [[ $? -eq 0 ]]; then
            group_deployed=$(( group_deployed + 1 ))
        else
            group_failed=$(( group_failed + 1 ))
            FAILED_MODULE_NAMES+=("${names[$i]}")
        fi
    done

    # S4: Parallel healthcheck after all modules in group complete
    local -a _hc_pids=() _hc_names=()
    local _hc_name
    for _hc_name in "${names[@]}"; do
        (run_healthcheck "$_hc_name" "docker" && exit 0 || exit 1) &
        _hc_pids+=($!)
        _hc_names+=("$_hc_name")
    done
    local _hc_i=0
    for _hc_pid in "${_hc_pids[@]}"; do
        wait "$_hc_pid" || log_step "health:${_hc_names[$_hc_i]}" "WARN" "Healthcheck failed"
        _hc_i=$((_hc_i + 1))
    done

    deployed=$(( deployed + group_deployed ))
    failed=$(( failed + group_failed ))
    log_step "parallel" "DONE" "Group complete: deployed=${group_deployed} failed=${group_failed}"
}
# endregion DEPLOY_DOCKER_GROUP

# 🧐 TRAP[DECISION] · 2026-07-21 · — · Pre-pull phase antes docker compose up -d
# · Rejected: pull on demand (docker compose up -d pulls if image missing)
# · Reason: Sequential per-project pull inside up -d causes ~3-4 min latency
#   per deploy cycle on cold server (8 of 13 modules had NO nightly pre-warming).
#   Dedicated parallel pull phase batches all downloads, utilizing full bandwidth,
#   and reduces total bootstrap time by ~40-50%. Pull failure is non-fatal —
#   up -d retries.
# · Rev: if nightly warm-images.sh covers all 13 modules AND deploy is never
#   on cold cache (re-deploy always within 24h of warm), remove pre-pull phase.

# region PRE_PULL_IMAGES
## @purpose  Parallel pre-pull of all docker module images BEFORE topo-sorted compose up.
##           Executes docker compose pull for each module in parallel (respecting COMPOSE_PARALLEL_LIMIT).
##           Pull failure is non-fatal — docker compose up -d will retry if image is missing.
##           This eliminates per-module pull latency during deploy_docker_group, reducing total
##           bootstrap time by ~40-50% (images are already cached when compose up runs).
## @io       List of "module_name:overlay_dir" entries → stdout/logs, return 0
## @complexity 2 — parallel pull with PID tracking, same pattern as deploy_docker_group
## @invariants
##   - Pull parallels limit = COMPOSE_PARALLEL_LIMIT (default 4)
##   - Compose file resolution mirrors deploy_docker_module: compose.yaml → docker-compose.yaml → docker-compose.base.yml
##   - Profiles are passed to docker compose pull (modules require --profile)
##   - Overlay compose files are included if provided (context overlay)
##   - Secrets env-file is passed if exists (some pulls need env for image resolution)
##   - Failure is LOGGED but NOT fatal — up -d retries pull internally
##   - Already-cached images return immediately (no-op pull)
## @rationale Q: Why pull separately from up -d? A: docker compose up -d pulls images
##   sequentially within each project even when modules are parallel. A dedicated pull
##   phase batches ALL image downloads at once, utilizing full network bandwidth.
##   Q: Why non-fatal? A: compose up -d already retries pull — pre-pull is optimization,
##   not correctness. Failing here and succeeding in up -d is harmless.
## @changes   CREATED: 2026-07-21 · A1 — Pre-pull phase (DevPlan 020 optimization)
_pre_pull_images() {
    local -a entries=("$@")
    local -a pids=()
    local parallel_limit="${COMPOSE_PARALLEL_LIMIT:-4}"
    local pull_ok=0 pull_fail=0

    log_step "pre-pull" "START" "Pre-pulling images for ${#entries[@]} docker modules (parallel limit: ${parallel_limit})"

    for entry in "${entries[@]}"; do
        local mod_name="${entry%%:*}"
        local mod_overlay="${entry#*:}"
        [[ "$mod_overlay" == "$mod_name" ]] && mod_overlay=""

        # ── Parallel slot waiter (same pattern as deploy_docker_group) ──
        while [[ ${#pids[@]} -ge $parallel_limit ]]; do
            for i in "${!pids[@]}"; do
                if ! kill -0 "${pids[$i]}" 2>/dev/null; then
                    wait "${pids[$i]}"
                    if [[ $? -eq 0 ]]; then
                        pull_ok=$(( pull_ok + 1 ))
                    else
                        pull_fail=$(( pull_fail + 1 ))
                    fi
                    unset 'pids[$i]'
                fi
            done
            pids=("${pids[@]}")
            [[ ${#pids[@]} -ge $parallel_limit ]] && sleep 1
        done

        # ── Subshell: pull images for one module ──
        (
            local module_dir="${PATHS_MODULES_DIR}/${mod_name}"
            local compose_file="${module_dir}/compose.yaml"
            [[ ! -f "$compose_file" ]] && compose_file="${module_dir}/docker-compose.yaml"
            [[ ! -f "$compose_file" ]] && compose_file="${module_dir}/docker-compose.base.yml"

            if [[ ! -f "$compose_file" ]]; then
                log_step "pre-pull:${mod_name}" "SKIP" "No compose file found — skipping pull"
                exit 0
            fi

            # Skip modules with local build (no registry image — pull would fail)
            if grep -q '^\s\+build:' "$compose_file" 2>/dev/null; then
                log_step "pre-pull:${mod_name}" "SKIP" "Local build detected (has build: section) — skipping pull"
                exit 0
            fi

            local -a pull_args=("-f" "$compose_file")

            local env_file="${SECRETS_ENV_FILE:-/run/platform/secrets.env}"
            local platform_env="${PLATFORM_ROOT:-/opt/platform}/.env"
            [[ -f "$env_file" ]] && pull_args+=("--env-file" "$env_file")
            [[ -f "$platform_env" ]] && pull_args+=("--env-file" "$platform_env")

            if [[ -n "$mod_overlay" ]] && [[ -f "${mod_overlay}/compose.override.yaml" ]]; then
                pull_args+=("-f" "${mod_overlay}/compose.override.yaml")
            fi

            pull_args+=("--profile" "$mod_name")

            log_step "pre-pull:${mod_name}" "PULL" "Pulling images (compose: $(basename "${compose_file}"))"
            docker compose "${pull_args[@]}" pull 2>&1 && exit 0 || exit 1
        ) &
        pids+=($!)
    done

    # ── Drain remaining PIDs ──
    local i
    for i in "${!pids[@]}"; do
        wait "${pids[$i]}"
        if [[ $? -eq 0 ]]; then
            pull_ok=$(( pull_ok + 1 ))
        else
            pull_fail=$(( pull_fail + 1 ))
        fi
    done

    log_step "pre-pull" "DONE" "Pre-pull complete: success=${pull_ok} failed=${pull_fail}"
    return 0
}
# endregion PRE_PULL_IMAGES

# region RENDER_SUDOERS_RULES
## @purpose  S6: Extract sudoers rule text for one module (template render + rule generation).
##           Outputs sudoers entries to stdout for batch collection.
## @io       $1=module_name → stdout: sudoers rules, return 0/1
## @complexity 2 — template-engine render + grep/parse
## @invariants
##   - Does NOT validate with visudo (validation is in _batch_generate_sudoers)
##   - Does NOT write to /etc/sudoers.d/ (single call only)
##   - Rendered temp file is cleaned up after parsing
_render_sudoers_rules() {
    local module_name="$1"
    local module_dir
    module_dir="$(realpath "${SCRIPT_DIR}/../../modules/${module_name}")"

    local template="${SCRIPT_DIR}/../../templates/sudo-whitelist.template"
    if [[ ! -f "$template" ]]; then
        template="${PLATFORM_ROOT}/core/templates/sudo-whitelist.template"
    fi

    local rendered
    rendered="$(mktemp /tmp/platform-sudoers-rendered-XXXXXX)"
    chmod 0440 "$rendered"

    local engine="${SCRIPT_DIR}/../template-engine.sh"
    if ! bash "$engine" render "$template" "$rendered" \
        "MODULE_NAME=${module_name}" "PLATFORM_ROOT=${PLATFORM_ROOT:-/opt/platform}"; then
        rm -f "$rendered"
        return 1
    fi

    local make_bin="/usr/bin/make"
    local module_abs_dir
    module_abs_dir="$(realpath "${module_dir}")"

    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${line// }" ]] && continue
        local role action _path
        read -r role action _path <<< "$line" || continue
        [[ -z "$role" || -z "$action" ]] && continue
        if [[ "$action" == make:* ]]; then
            local target="${action#make:}"
            local username
            case "$role" in
                owner)   username="platform" ;;
                agent)   username="platform-agent" ;;
                ci)      username="ci-deploy" ;;
                monitor) username="platform-monitor" ;;
                *)       username="$role" ;;
            esac
            printf '%s ALL=(root) NOPASSWD: %s -C %s %s\n' \
                "$username" "$make_bin" "$module_abs_dir" "$target"
        fi
    done < <(grep -v '^[[:space:]]*#' "$rendered" | grep -v '^[[:space:]]*$')

    rm -f "$rendered"
}
# endregion RENDER_SUDOERS_RULES

# region BATCH_GENERATE_SUDOERS
## @purpose  S6: Generate one /etc/sudoers.d/platform-modules file for ALL modules.
##           Replaces per-module generate_module_sudoers() calls with a single
##           batch operation: collects rules from _render_sudoers_rules(),
##           validates once with visudo -c, writes to sudoers.d.
## @io       $@=module_names → writes /etc/sudoers.d/platform-modules
## @complexity O(n) where n = number of modules (n template renders + 1 visudo validation)
_batch_generate_sudoers() {
    local -a module_names=("$@")
    [[ ${#module_names[@]} -eq 0 ]] && return 0

    local tmp_sudoers
    tmp_sudoers="$(mktemp /tmp/platform-sudoers-all-XXXXXX)"
    chmod 0440 "$tmp_sudoers"

    cat > "$tmp_sudoers" <<'EOF'
# platform modules sudoers — ALL modules
# Generated by deploy-modules.sh
# DO NOT edit manually
EOF

    local mod_name
    for mod_name in "${module_names[@]}"; do
        _render_sudoers_rules "$mod_name" >> "$tmp_sudoers" || true
    done

    if visudo -c -f "$tmp_sudoers" &>/dev/null; then
        mv "$tmp_sudoers" /etc/sudoers.d/platform-modules
        log_step "sudoers" "DONE" "Batch sudoers generated for ${#module_names[@]} modules"
    else
        log_step "sudoers" "FAIL" "visudo -c FAILED for batch sudoers"
        rm -f "$tmp_sudoers"
    fi
}
# endregion BATCH_GENERATE_SUDOERS

# region BATCH_ORPHAN_RECONCILIATION
## @purpose  S8: Batch orphan container reconciliation — single python3 call for ALL
##           docker modules, replacing per-module inline python3 in deploy_docker_module().
##           Collects all compose files, does one docker ps -a, compares container names
##           with compose project labels, stops + removes foreign containers.
## @io       $@=module_name:overlay entries → stdout/logs, return 0
## @complexity 2 — one python3 call collecting all compose configs + docker inspect
## @rationale  Per-module orphan detection created 13 python3 spawns per update cycle.
##             Batch approach: 1 python3 call for all modules, reducing total spawn time.
_batch_orphan_reconciliation() {
    local -a module_entries=("$@")
    [[ ${#module_entries[@]} -eq 0 ]] && return 0

    local _batch_orphans
    _batch_orphans=$(python3 -c "
import json, subprocess, sys, os
from pathlib import Path

modules_dir = '${PATHS_MODULES_DIR}'
entries = '${module_entries[*]}'.split()
compose_files = []

for entry in entries:
    mod_name = entry.split(':')[0]
    mod_dir = os.path.join(modules_dir, mod_name)
    for cf in ['compose.yaml', 'docker-compose.yaml', 'docker-compose.base.yml']:
        cf_path = os.path.join(mod_dir, cf)
        if os.path.isfile(cf_path):
            compose_files.append((mod_name, cf_path))
            break

# Single docker ps -a for all modules
try:
    ps_r = subprocess.run(['docker', 'ps', '-a', '--format', '{{.Names}}'],
        capture_output=True, text=True, timeout=15)
    existing = set(ps_r.stdout.splitlines())
except Exception:
    sys.exit(0)

for mod_name, cf_path in compose_files:
    try:
        cfg_r = subprocess.run(
            ['docker', 'compose', '-f', cf_path, '--profile', mod_name, 'config', '--format', 'json'],
            capture_output=True, text=True, timeout=30)
        if cfg_r.returncode != 0:
            continue
        cfg = json.loads(cfg_r.stdout)
        for svc in cfg.get('services', {}).values():
            cname = svc.get('container_name', '') or svc.get('name', '')
            if not cname or cname not in existing:
                continue
            ins_r = subprocess.run(
                ['docker', 'inspect', '--format', '{{index .Config.Labels \"com.docker.compose.project\"}}', cname],
                capture_output=True, text=True, timeout=15)
            proj = ins_r.stdout.strip()
            if not proj or proj != mod_name:
                print(f'{cname}|{proj}')
    except Exception:
        continue
" 2>/dev/null || true)

    if [[ -n "$_batch_orphans" ]]; then
        while IFS='|' read -r _cname _project; do
            [[ -z "$_cname" ]] && continue
            log_step "orphan" "INFO" "Reconciling orphan container: ${_cname} (project=${_project:-<none>})"
            docker stop "$_cname" 2>/dev/null || true
            docker rm "$_cname" 2>/dev/null || true
            log_step "orphan" "INFO" "Orphan container removed: ${_cname}"
        done <<< "$_batch_orphans"
    fi
}
# endregion BATCH_ORPHAN_RECONCILIATION

main() {
    local modules_filter=""
    local SKIP_PROVISION=false

    # ── Parse flags (--modules, --skip-provision) ──
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --modules)
                shift
                if [[ -z "${1:-}" || "$1" == --* ]]; then
                    echo "[IMP:10][deploy-modules][main] ERROR: --modules requires a comma-separated list" >&2
                    exit 1
                fi
                modules_filter="$1"
                shift
                ;;
            --skip-provision)
                SKIP_PROVISION=true
                shift
                ;;
            *)
                # Stop parsing at first positional arg (backward compat)
                break
                ;;
        esac
    done

    if [[ "$(id -u)" -ne 0 ]]; then
        echo "[IMP:10][deploy-modules][main] ERROR: must run as root" >&2
        exit 1
    fi

    local node_yaml="${NODE_YAML:-}"
    if [[ -z "$node_yaml" ]] || [[ ! -f "$node_yaml" ]]; then
        echo "[IMP:10][deploy-modules][main] ERROR: NODE_YAML not set or file not found: '${node_yaml}'" >&2
        exit 1
    fi

    # ── Secret charset validation (fail-fast before any docker compose ops) ──
    _validate_secret_charsets || exit 1

    # ── S1: Skip provisioner if --skip-provision flag set ──
    # Provisioner is called from node-lifecycle.sh step 2 when --skip-provision
    # is used, avoiding duplicate provisioner calls (up to 5× per update cycle).
    if [[ "${SKIP_PROVISION:-false}" != "true" ]]; then
        local provisioner="${PATHS_INTERNAL_DIR}/provision-environment.sh"
        if [[ -f "$provisioner" ]]; then
            log_step "provisioner" "START" "Delegating networks to provisioner"
            bash "$provisioner" --scope networks || log_step "provisioner" "WARN" "Provisioner --scope networks had warnings"
            log_step "provisioner" "INFO" "Provisioner networks complete — delegated from PLATFORM_NETWORKS loop"

            log_step "provisioner" "START" "Delegating volumes to provisioner"
            bash "$provisioner" --scope volumes || log_step "provisioner" "WARN" "Provisioner --scope volumes had warnings"
            log_step "provisioner" "INFO" "Provisioner volumes complete"
        else
            log_step "provisioner" "WARN" "Provisioner not found at ${provisioner} — using legacy network loop"
            for network in "${PLATFORM_NETWORKS[@]}"; do
                ensure_docker_network "$network"
            done
            log_step "networks" "DONE" "All platform networks ready (legacy): ${PLATFORM_NETWORKS[*]}"
        fi
    else
        log_step "provisioner" "SKIP" "Provisioner skipped (--skip-provision flag set — called from node-lifecycle.sh step 2)"
    fi

    ensure_spool_dirs
    docker_login
    ghcr_login
    ensure_context_repo "$node_yaml"

    local modules_raw
    modules_raw="$(parse_modules_from_node_yaml "$node_yaml")"

    if [[ -z "$modules_raw" ]]; then
        log_step "modules" "SKIP" "No modules declared in ${node_yaml}"
        exit 0
    fi

    if grep -q '^hermes-agent:true' <<< "$modules_raw"; then
        export PLATFORM_HERMES_ENABLED=true
        log_step "hermes-dashboard" "INFO" "hermes-agent enabled — PLATFORM_HERMES_ENABLED=true exported"
    else
        export PLATFORM_HERMES_ENABLED=false
        log_step "hermes-dashboard" "INFO" "hermes-agent not found — PLATFORM_HERMES_ENABLED=false"
    fi

    if grep -q '^observability:true' <<< "$modules_raw"; then
        export PLATFORM_OBSERVABILITY_ENABLED=true
        log_step "observability" "INFO" "observability enabled — PLATFORM_OBSERVABILITY_ENABLED=true exported"
    else
        export PLATFORM_OBSERVABILITY_ENABLED=false
        log_step "observability" "INFO" "observability not found — PLATFORM_OBSERVABILITY_ENABLED=false"
    fi

    # ── S3 (DevPlan 019): Ensure PLATFORM_DOMAIN from node.yaml ──
    # PLATFORM_DOMAIN must be in docker compose environment for nginx vhost templates.
    # Extracted from node.yaml as SSoT — independent of checkpoint state in node-lifecycle.sh.
    if [[ -n "${NODE_YAML:-}" ]] && [[ -f "$NODE_YAML" ]]; then
        local _domain
        _domain=$(python3 -c "import yaml; print(yaml.safe_load(open('$NODE_YAML')).get('domain',''))" 2>/dev/null)
        if [[ -n "$_domain" ]]; then
            export PLATFORM_DOMAIN="$_domain"
            echo "[IMP:9][deploy-modules][S3] PLATFORM_DOMAIN=${_domain} exported from node.yaml" >&2
        else
            echo "[IMP:8][deploy-modules][S3] WARN: domain not found in node.yaml — PLATFORM_DOMAIN may be empty" >&2
        fi
    fi

    # ══════════════════════════════════════════════════════════════════
    # S10: Enriched topo-sort — module metadata + deploy groups
    # _topo_sort.py now returns {"groups": [...], "modules": {...}}
    # so we can read install_type and severity from enriched output
    # instead of calling detect_install_type / _get_module_severity
    # per module (eliminates 26 python3 spawns per update cycle).
    # ══════════════════════════════════════════════════════════════════
    local _topo_script="${PATHS_INTERNAL_DIR}/bootstrap/_topo_sort.py"
    local _topo_result=""
    local -A _MODULE_TYPES=()
    local -A _MODULE_SEVERITIES=()

    # Build list of all module names from modules_raw for enriched query
    local -a _all_module_names=()
    local _tmp_name
    while IFS=: read -r _tmp_name _; do
        [[ -z "$_tmp_name" ]] && continue
        _all_module_names+=("$_tmp_name")
    done <<< "$modules_raw"

    if [[ ${#_all_module_names[@]} -gt 0 ]]; then
        _topo_result=$(python3 "$_topo_script" \
            --modules-dir "${PATHS_MODULES_DIR}" \
            --filter-names "${_all_module_names[@]}") || {
            log_step "topo-sort" "WARN" "Enriched topo-sort failed — falling back to legacy per-module detection"
            _topo_result=""
        }
    fi

    # Parse enriched modules dict into associative arrays
    if [[ -n "$_topo_result" ]]; then
        local _mod_entry
        while IFS= read -r _mod_entry; do
            [[ -z "$_mod_entry" ]] && continue
            local _mn="${_mod_entry%%|*}"
            local _rest="${_mod_entry#*|}"
            local _mt="${_rest%%|*}"
            local _ms="${_rest#*|}"
            if [[ -n "$_mn" ]]; then
                _MODULE_TYPES["$_mn"]="$_mt"
                _MODULE_SEVERITIES["$_mn"]="$_ms"
            fi
        done < <(echo "$_topo_result" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for name, meta in data.get('modules', {}).items():
    print(f'{name}|{meta[\"install_type\"]}|{meta[\"severity\"]}')
")
        log_step "topo-sort" "INFO" "Enriched metadata loaded for ${#_MODULE_TYPES[@]} modules"
    fi

    # ── S3: Batch metadata fallback (one python3 call replaces per-module detect_install_type + _get_module_severity) ──
    # Populates _MODULE_TYPES/_MODULE_SEVERITIES for any modules not covered by enriched topo
    if [[ ${#_MODULE_TYPES[@]} -lt ${#_all_module_names[@]} ]]; then
        local _bmname _bmtype _bmsev
        while IFS=: read -r _bmname _bmtype _bmsev; do
            [[ -z "$_bmname" ]] && continue
            if [[ -z "${_MODULE_TYPES[$_bmname]:-}" ]]; then
                _MODULE_TYPES["$_bmname"]="$_bmtype"
                _MODULE_SEVERITIES["$_bmname"]="$_bmsev"
            fi
        done < <(_batch_module_metadata)
        log_step "topo-sort" "INFO" "Batch metadata fallback loaded: ${#_MODULE_TYPES[@]} modules total"
    fi

    local deployed=0 skipped=0 failed=0
    local -a system_modules=()
    local -a docker_modules=()

    while IFS=: read -r mod_name mod_enabled mod_overlay; do
        [[ -z "$mod_name" ]] && continue

        if [[ "$mod_enabled" != "true" ]]; then
            log_step "module:${mod_name}" "SKIP" "Module disabled in node.yaml"
            skipped=$(( skipped + 1 ))
            continue
        fi

        local install_type
        # S3+S10: read from enriched associative array (populated by topo + batch fallback)
        install_type="${_MODULE_TYPES[$mod_name]:-unknown}"
        log_step "module:${mod_name}" "INFO" "install_type=${install_type}"

        if [[ -z "$mod_overlay" ]]; then
            local context_name
            context_name=$(grep "^context:" "$node_yaml" | awk '{print $2}' 2>/dev/null || echo "")
            if [[ -n "$context_name" ]]; then
                local convention_path="/opt/${context_name}/platform/modules/${mod_name}"
                if [[ -d "$convention_path" ]]; then
                    mod_overlay="$convention_path"
                    log_step "module:${mod_name}" "INFO" "Auto-resolved context overlay: ${convention_path}"
                fi
            fi
        fi

        case "$install_type" in
            system)
                system_modules+=("$mod_name:$mod_overlay")
                ;;
            docker)
                docker_modules+=("$mod_name:$mod_overlay")
                ;;
            *)
                log_step "module:${mod_name}" "WARN" "Unknown install_type '${install_type}' — skipping"
                skipped=$(( skipped + 1 ))
                ;;
        esac
    done <<< "$modules_raw"

    # ── If --modules flag given, filter to expanded set (seed + transitive depends_on) ──
    if [[ -n "$modules_filter" ]]; then
        local expanded_modules
        expanded_modules="$(_expand_transitive_deps "$modules_filter")" || {
            local expand_exit=$?
            log_step "modules" "FAIL" "Module filter expansion failed (exit ${expand_exit})"
            exit 1
        }

        log_step "modules" "INFO" "Expanded --modules filter: ${expanded_modules}"

        # Filter system modules
        local -a filtered_system=()
        for entry in "${system_modules[@]}"; do
            local m="${entry%%:*}"
            if [[ " $expanded_modules " == *" $m "* ]]; then
                filtered_system+=("$entry")
            else
                log_step "module:${m}" "SKIP" "Excluded by --modules filter"
                skipped=$((skipped + 1))
            fi
        done
        system_modules=("${filtered_system[@]}")

        # Filter docker modules
        local -a filtered_docker=()
        for entry in "${docker_modules[@]}"; do
            local m="${entry%%:*}"
            if [[ " $expanded_modules " == *" $m "* ]]; then
                filtered_docker+=("$entry")
            else
                log_step "module:${m}" "SKIP" "Excluded by --modules filter"
                skipped=$((skipped + 1))
            fi
        done
        docker_modules=("${filtered_docker[@]}")
    fi

    local entry mod_name mod_overlay
    for entry in "${system_modules[@]}"; do
        mod_name="${entry%%:*}"
        mod_overlay="${entry#*:}"
        [[ "$mod_overlay" == "$mod_name" ]] && mod_overlay=""
        if deploy_system_module "$mod_name" "$mod_overlay"; then
            deployed=$(( deployed + 1 ))
        else
            failed=$(( failed + 1 ))
            FAILED_MODULE_NAMES+=("$mod_name")
        fi
        run_healthcheck "$mod_name" "system"
    done

    # ── Pre-pull all docker module images (A1) ──
    # OPTIMIZATION: Pull images in parallel before topo-sorted compose up.
    # docker compose up -d pulls images sequentially per module — a dedicated
    # pull phase batches ALL downloads at once, reducing total bootstrap time
    # by ~40-50% on cold VPS. Failure is non-fatal (compose up retries pull).
    if [[ ${#docker_modules[@]} -gt 0 ]]; then
        log_step "pre-pull" "INFO" "Pre-pulling images for ${#docker_modules[@]} docker modules before deploy"
        _pre_pull_images "${docker_modules[@]}"
    fi

    # ── Topological sort: reuse enriched _topo_result from S10 ──
    # _topo_result was already computed above (includes both groups and
    # enriched modules metadata). If it failed, fall back to sequential deploy.
    if [[ -z "$_topo_result" ]]; then
        local -a _docker_names=()
        for entry in "${docker_modules[@]}"; do
            _docker_names+=("${entry%%:*}")
        done
        _topo_result=$(python3 "$_topo_script" \
            --modules-dir "${PATHS_MODULES_DIR}" \
            --filter-names "${_docker_names[@]}") || {
            log_step "topo-sort" "FAIL" "Topological sort failed — falling back to sequential deploy"
            for entry in "${docker_modules[@]}"; do
                local m="${entry%%:*}"
                local o="${entry#*:}"
                [[ "$o" == "$m" ]] && o=""
                if deploy_docker_module "$m" "$o"; then
                    deployed=$(( deployed + 1 ))
                else
                    failed=$(( failed + 1 ))
                    FAILED_MODULE_NAMES+=("$m")
                fi
                run_healthcheck "$m" "docker"
            done
            _topo_result=""
        }
    fi

    if [[ -n "$_topo_result" ]]; then
        # Parse JSON groups from _topo_sort.py
        local _group_idx=0
        local _hermes_deployed=false
        while true; do
            local _group_json
            _group_json=$(echo "$_topo_result" | python3 -c "
import json,sys
groups = json.load(sys.stdin)['groups']
if $_group_idx < len(groups):
    print(json.dumps(groups[$_group_idx]))
else:
    print('')
" 2>/dev/null)
            [[ -z "$_group_json" ]] && break

            local -a _group_entries=()
            while IFS= read -r _mod_name; do
                [[ -z "$_mod_name" ]] && continue
                for entry in "${docker_modules[@]}"; do
                    if [[ "${entry%%:*}" == "$_mod_name" ]]; then
                        _group_entries+=("$entry")
                        [[ "$_mod_name" == "hermes-agent" ]] && _hermes_deployed=true
                    fi
                done
            done < <(echo "$_group_json" | python3 -c "
import json,sys
for m in json.load(sys.stdin):
    print(m)
")

            _group_idx=$((_group_idx + 1))
            if [[ ${#_group_entries[@]} -gt 0 ]]; then
                log_step "parallel" "G${_group_idx}" "Deploying group ${_group_idx} (parallel): ${_group_entries[*]}"
                deploy_docker_group "${_group_entries[@]}"
            fi
        done

        # Hermes-agent special handling: readiness polling after its group
        if [[ "$_hermes_deployed" == "true" ]]; then
            log_step "parallel" "POST" "Polling hermes-agent readiness..."
            wait_for_readiness "hermes-agent" 15 2 || log_imp 9 "readiness" "hermes-agent not ready after timeout"
        fi
    fi

    # ── S6: Batch sudoers generation (one file for ALL modules) ──
    # Collect all module names (system + docker) for batch sudoers
    local -a _all_module_names_for_sudoers=()
    for entry in "${system_modules[@]}"; do
        _all_module_names_for_sudoers+=("${entry%%:*}")
    done
    for entry in "${docker_modules[@]}"; do
        _all_module_names_for_sudoers+=("${entry%%:*}")
    done
    _batch_generate_sudoers "${_all_module_names_for_sudoers[@]}"

    # ── S8: Batch orphan reconciliation (one python3 call for ALL docker modules) ──
    _batch_orphan_reconciliation "${docker_modules[@]}"

    log_step "main" "DONE" "Module deploy complete: deployed=${deployed} skipped=${skipped} failed=${failed}"

    # ── Severity-based exit code ──
    # If --modules filter was applied but has no matches, that's a usage error
    if [[ -n "$modules_filter" ]] && [[ "$deployed" -eq 0 ]] && [[ "$skipped" -eq "$(echo "$modules_filter" | tr ',' ' ' | wc -w | tr -d ' ')" ]]; then
        log_step "main" "WARN" "All specified modules were excluded after filtering — exiting 1"
        FAILED_MODULE_NAMES+=("${modules_filter}")
    fi

    local critical_failed=0 warn_failed=0
    for failed_mod in "${FAILED_MODULE_NAMES[@]}"; do
        local sev
        # S3+S10: read severity from enriched associative array (populated by topo + batch fallback)
        sev="${_MODULE_SEVERITIES[$failed_mod]:-warn}"
        if [[ "$sev" == "critical" ]]; then
            critical_failed=$((critical_failed + 1))
            log_step "main" "CRITICAL" "Module ${failed_mod} FAILED with severity=critical → exit 2"
        else
            warn_failed=$((warn_failed + 1))
            log_step "main" "WARN" "Module ${failed_mod} FAILED with severity=warn"
        fi
    done

    if [[ "$critical_failed" -gt 0 ]]; then
        log_step "main" "WARN" "Severity summary: critical=${critical_failed} warn=${warn_failed} — exiting 2"
        exit 2
    elif [[ "$warn_failed" -gt 0 ]]; then
        log_step "main" "WARN" "Severity summary: critical=${critical_failed} warn=${warn_failed} — exiting 1"
        exit 1
    elif [[ "$failed" -gt 0 ]]; then
        log_step "main" "WARN" "Legacy fallback: some modules failed but severity undetermined — exiting 1"
        exit 1
    fi
}

main "$@"
