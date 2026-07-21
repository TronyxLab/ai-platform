#!/usr/bin/env bash
# GREP_SUMMARY: deploy-modules docker-network proxy-net shared-db-net backup-net compose-up system-module install.sh ensure-spool verify-only sudoers-modules --modules severity critical warn exit-code topo-sort transitive-deps
# STRUCTURE: [--modules flag] → ensure_networks → ensure_spool_dirs → parse node.yaml → expand_transitive_deps(--modules) → separate system|docker → filter(--modules set) → system:install.sh → docker:_topo_sort.py(groups) → deploy_docker_group(parallel) → severity_aggregate → [critical→exit 2|warn→exit 1|ok→exit 0]
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
##   - docker modules: docker compose up -d (no-op if unchanged)
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
readonly COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-2}"

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
        log_step "context-repo" "INFO" "Context repo exists: ${context_path} — pulling latest"
        git -C "$context_path" pull --ff-only 2>/dev/null || \
            log_step "context-repo" "WARN" "git pull failed (non-fatal): ${context_path}"
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
    local compose_args=("-f" "$compose_file")
    if [[ -f "$env_file" ]]; then
        compose_args+=("--env-file" "$env_file")
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
        local -a hermes_images=()
        mapfile -t hermes_images < <(docker compose "${compose_args[@]}" --profile "$module_name" config --images 2>/dev/null || true)
        if [[ ${#hermes_images[@]} -eq 0 ]]; then
            log_step "docker:${module_name}" "FAIL" "No images resolved from compose config — compose file may be broken"
            return 1
        fi
        local _all_found=true
        for _img in "${hermes_images[@]}"; do
            if ! _check_image_exists "$_img"; then
                _all_found=false
                log_step "docker:${module_name}" "FAIL" "hermes-agent image not found: ${_img}"
            fi
        done
        if ! $_all_found; then
            echo "[IMP:10][deploy-modules][hermes-agent] Build required:" >&2
            echo "  make hermes-build-platform    # Build L1 base image locally" >&2
            echo "  make hermes-push-l1           # Push L1 to ghcr.io" >&2
            echo "  make hermes-build-context     # Build L2 context image" >&2
            return 1
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
    local parallel_limit="${COMPOSE_PARALLEL_LIMIT:-2}"
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
                        run_healthcheck "${names[$i]}" "docker"
                        generate_module_sudoers "${names[$i]}" || true
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
            run_healthcheck "${names[$i]}" "docker"
            generate_module_sudoers "${names[$i]}" || true
        else
            group_failed=$(( group_failed + 1 ))
            FAILED_MODULE_NAMES+=("${names[$i]}")
        fi
    done

    deployed=$(( deployed + group_deployed ))
    failed=$(( failed + group_failed ))
    log_step "parallel" "DONE" "Group complete: deployed=${group_deployed} failed=${group_failed}"
}
# endregion DEPLOY_DOCKER_GROUP

main() {
    local modules_filter=""

    # ── Parse --modules flag ──
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

    # Provisioner: canonical Docker networks + volumes (idempotent)
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
        install_type="$(detect_install_type "$mod_name")"
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
        generate_module_sudoers "$mod_name" || true
    done

    # ── Topological sort via _topo_sort.py (dynamic group auto-resolution) ──
    # B3: Group order is auto-resolved from module.yaml depends_on.
    # _topo_sort.py reads all module.yaml files, builds a DAG from depends_on,
    # and applies Kahn's algorithm to produce parallel-deploy groups.
    # NO hardcoded group constants — groups are dynamically computed.
    # The output JSON groups are consumed below: each group deploys in parallel,
    # group[0] has no dependencies, group[1] depends on group[0], etc.
    local _topo_script="${PATHS_INTERNAL_DIR}/bootstrap/_topo_sort.py"
    local -a _docker_names=()
    for entry in "${docker_modules[@]}"; do
        _docker_names+=("${entry%%:*}")
    done

    local _topo_result
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
            generate_module_sudoers "$m" || true
        done
        _topo_result=""
    }

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
        sev="$(_get_module_severity "$failed_mod")"
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
