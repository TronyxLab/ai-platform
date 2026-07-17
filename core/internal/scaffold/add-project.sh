#!/usr/bin/env bash
# GREP_SUMMARY: add-project CLI template copy placeholder-replace git-init checklist FQDN-conflict dev-cli register node-yaml context
# STRUCTURE: parse_args → validate_inputs → show_plan → confirm → copy_template → replace_placeholders → git_init → create_github_repo → checklist → [add-vhost] → [register_in_node_yaml] → summary
# region MODULE_CONTRACT
## @purpose  Create a new project from a template in the organization directory, outside ai-platform/.
## @scope    Local developer tool — run once per new project from ai-platform/ root.
## @location core/internal/scaffold/add-project.sh — moved from core/scripts/add-project.sh
## @invariants
##   - Projects created in $PROJECTS_ROOT/$ORG/$NAME/, NOT inside ai-platform/.
##   - Templates in ai-platform/templates/ — copied with placeholder substitution.
##   - git init + initial commit done in the new project directory.
##   - _SETUP_CHECKLIST.md generated with exact GitHub commands.
##   - If --domain: calls add-vhost.sh for nginx config generation.
##   - Never auto-creates GitHub repos (no token access — developer runs gh commands manually).
## @rationale ai-platform/ is a tool, not a workspace. Projects live in org folders.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || echo "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")}"
PROJECTS_ROOT="${PROJECTS_ROOT:-$(dirname "$PLATFORM_ROOT")}"
TEMPLATES_DIR="${PLATFORM_ROOT}/templates"

ORG=""
NAME=""
TEMPLATE=""
NODE=""
DOMAIN=""
DATABASE=""
DRY_RUN=false
CI_MODE="${CI_MODE:-0}"
MODE=""
REGISTER=false
CONTEXT=""

__LOG_PREFIX="add-project"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

# region USAGE
usage() {
    cat <<'HELP'
USAGE: add-project.sh --name <name> --template <type> --org <org> --node <node> [OPTIONS]

REQUIRED:
  --name <name>        Project name (alphanumeric, hyphens, underscores)
  --template <type>    Template: frontend | backend | fullstack
  --org <org>          Organization name (e.g. tronyx-lab)
  --node <node>        Target node name (e.g. tronyx-vps)

OPTIONAL:
  --domain <fqdn>      Domain for nginx vhost (triggers add-vhost.sh)
  --database <name>    Database name for backend/fullstack projects
  --dry-run            Show plan without creating files
  --mode <mode>        dev mode: enables staging
  --register           Register project in node.yaml (requires --context)
  --context <name>     Context name for node.yaml registration

ENVIRONMENT:
  PLATFORM_ROOT        Path to ai-platform/ (auto-detected)
  PROJECTS_ROOT        Path to organizations dir (auto-detected)
  CI_MODE=1            Skip confirmation prompts

EXAMPLES:
  core/scripts/add-project.sh --name tronyx-site --template frontend --org tronyx-lab --node tronyx-vps --domain tronyx.ru
HELP
    exit 1
}
# endregion USAGE

# region PARSE_ARGS
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name)     NAME="$2"; shift 2 ;;
            --template) TEMPLATE="$2"; shift 2 ;;
            --org)      ORG="$2"; shift 2 ;;
            --node)     NODE="$2"; shift 2 ;;
            --domain)   DOMAIN="$2"; shift 2 ;;
            --database) DATABASE="$2"; shift 2 ;;
            --dry-run)  DRY_RUN=true; shift ;;
            --mode)     MODE="$2"; shift 2 ;;
            --register) REGISTER=true; shift ;;
            --context)  CONTEXT="$2"; shift 2 ;;
            --help|-h)  usage ;;
            *)          log_crit "Unknown argument: $1"; usage ;;
        esac
    done

    local missing=()
    [[ -z "$NAME" ]] && missing+=("--name")
    [[ -z "$TEMPLATE" ]] && missing+=("--template")
    [[ -z "$ORG" ]] && missing+=("--org")
    [[ -z "$NODE" ]] && missing+=("--node")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_crit "Missing required arguments: ${missing[*]}"
        usage
    fi

    case "$TEMPLATE" in
        frontend|backend|fullstack) ;;
        *) log_crit "Invalid template type: '$TEMPLATE'. Must be: frontend | backend | fullstack"; exit 1 ;;
    esac

    if [[ "$REGISTER" == true ]] && [[ -z "$CONTEXT" ]]; then
        log_crit "--register requires --context <name>."
        exit 1
    fi

    if [[ ! "$NAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        log_crit "Invalid project name: '$NAME'. Use alphanumeric, hyphens, underscores only."
        exit 1
    fi

    local template_dir="${TEMPLATES_DIR}/template-${TEMPLATE}"
    if [[ ! -d "$template_dir" ]]; then
        log_crit "Template not found: ${template_dir}"
        exit 1
    fi
}
# endregion PARSE_ARGS

# region SHOW_PLAN
show_plan() {
    local project_dir="${PROJECTS_ROOT}/${ORG}/${NAME}"
    local template_dir="${TEMPLATES_DIR}/template-${TEMPLATE}"

    echo ""
    echo "──────────────────────────────────────────────────────"
    echo "  📁 Project dir:  ${project_dir}/"
    echo "  🔗 GitHub repo:  https://github.com/${ORG}/${NAME}"
    echo "  📦 Template:     template-${TEMPLATE}"
    echo "  🖥  Target node: ${NODE}"
    [[ -n "$DOMAIN" ]]   && echo "  🌐 Domain:      ${DOMAIN}"
    [[ -n "$DATABASE" ]] && echo "  🗄  Database:    ${DATABASE}"
    echo "  🏷️  Org:         ${ORG}"
    echo "──────────────────────────────────────────────────────"
    echo ""
}
# endregion SHOW_PLAN

# region CONFIRM
confirm() {
    if [[ "$DRY_RUN" == true ]] || [[ "$CI_MODE" == "1" ]]; then
        return 0
    fi
    read -r -p "  Продолжить? [y/N] " response
    case "$response" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) log_warn "Отменено пользователем."; exit 0 ;;
    esac
}
# endregion CONFIRM

# region COPY_TEMPLATE
copy_template() {
    local src="${TEMPLATES_DIR}/template-${TEMPLATE}"
    local dst="${PROJECTS_ROOT}/${ORG}/${NAME}"

    log_info "Copying template: ${src} → ${dst}"

    if [[ "$DRY_RUN" == true ]]; then
        log_ok "[DRY-RUN] Would copy: ${src} → ${dst}"
        return 0
    fi

    if [[ -d "$dst" ]]; then
        log_crit "Project directory already exists: ${dst}"
        exit 1
    fi

    mkdir -p "$(dirname "$dst")"
    rsync -a "${src}/" "${dst}/"
    log_ok "Template copied to: ${dst}"
}
# endregion COPY_TEMPLATE

# region GENERATE_AI_PLATFORM_YAML
generate_ai_platform_yaml() {
    local project_dir="$1"
    local name="$2"
    local type="$3"
    local org="$4"
    local node="$5"
    local domain="$6"
    local database="$7"
    local mode="$8"
    local context="${9:-personal}"

    local yaml_file="${project_dir}/ai-platform.yaml"

    log_info "Generating ai-platform.yaml: ${yaml_file}"

    if [[ "$DRY_RUN" == true ]]; then
        log_ok "[DRY-RUN] Would generate ai-platform.yaml with context=${context} at: ${yaml_file}"
        return 0
    fi

    cat > "$yaml_file" <<YAML
# =============================================================================
# ai-platform.yaml — единый манифест проекта AI Platform (деплой + мониторинг)
# =============================================================================
# GENERATED by add-project.sh — DO NOT EDIT MANUALLY
# Template: template-${type}
# Generated: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
# =============================================================================

name: ${name}
type: ${type}
description: "${name} project (${type})"
target_node: ${node}
context: ${context}

needs:
YAML

    if [[ -n "$domain" && "$domain" != "false" ]]; then
        cat >> "$yaml_file" <<YAML
  domain: ${domain}
  expose: true
YAML
    else
        cat >> "$yaml_file" <<YAML
  domain: false
  expose: false
YAML
    fi

    if [[ -n "$database" && "$database" != "false" ]]; then
        cat >> "$yaml_file" <<YAML
  database: ${database}
YAML
    fi

    if [[ "$type" == "fullstack" ]]; then
        cat >> "$yaml_file" <<YAML
  llm: remote
YAML
    fi

    local mon_metrics mon_logs mon_ai mon_alert mon_dashboard mon_port mon_ai_line
    case "$type" in
        frontend) mon_metrics="false"; mon_logs="3d"; mon_ai=""; mon_alert="false"; mon_dashboard="false"; mon_port=3000 ;;
        backend)  mon_metrics="true";  mon_logs="14d"; mon_ai=""; mon_alert="false"; mon_dashboard="false"; mon_port=8080 ;;
        fullstack) mon_metrics="true"; mon_logs="30d"; mon_ai="30d"; mon_alert="true"; mon_dashboard="true"; mon_port=8080 ;;
    esac

    if [[ -n "$mon_ai" ]]; then
        mon_ai_line="  ai_retention: ${mon_ai}"
    else
        mon_ai_line="  # ai_retention: not set (no LLM dependency)"
    fi

    cat >> "$yaml_file" <<YAML

monitoring:
  metrics: ${mon_metrics}
  metrics_port: ${mon_port}
  logs_retention: ${mon_logs}
${mon_ai_line}
  alerting: ${mon_alert}
  dashboard: ${mon_dashboard}
YAML

    if [[ "$mode" == "dev" ]]; then
        cat >> "$yaml_file" <<YAML

staging: true
YAML
        echo "Staging enabled for project"
        log_ok "Staging enabled for ${name} (mode=dev)"
    fi

    log_ok "ai-platform.yaml generated for ${name} (type=${type})"
}
# endregion GENERATE_AI_PLATFORM_YAML

# region REPLACE_PLACEHOLDERS
replace_placeholders() {
    local project_dir="${PROJECTS_ROOT}/${ORG}/${NAME}"

    if [[ "$DRY_RUN" == true ]]; then
        log_ok "[DRY-RUN] Would replace placeholders in: ${project_dir}"
        return 0
    fi

    log_info "Replacing placeholders in project files"

    local domain_val="${DOMAIN:-false}"

    while IFS= read -r -d '' file; do
        if [[ "$file" == *"/.git/"* ]]; then
            continue
        fi

        if ! file -b --mime-type "$file" | grep -qE '^text/|application/json|application/xml|inode/x-empty'; then
            log_info "Skipping non-text file: ${file}"
            continue
        fi

        local modified=false

        if grep -q "__PROJECT_NAME__" "$file" 2>/dev/null; then
            sed -i.bak "s/__PROJECT_NAME__/${NAME}/g" "$file" && rm "${file}.bak"
            modified=true
        fi
        if grep -q "__DOMAIN__" "$file" 2>/dev/null; then
            sed -i.bak "s/__DOMAIN__/${domain_val}/g" "$file" && rm "${file}.bak"
            modified=true
        fi
        if grep -q "__ORG_NAME__" "$file" 2>/dev/null; then
            sed -i.bak "s/__ORG_NAME__/${ORG}/g" "$file" && rm "${file}.bak"
            modified=true
        fi
        if [[ "$modified" == true ]]; then
            log_info "  Replaced placeholders in: ${file#$project_dir/}"
        fi
    done < <(find "$project_dir" -type f -print0)

    log_ok "Placeholders replaced"
}
# endregion REPLACE_PLACEHOLDERS

# region GIT_INIT
git_init_project() {
    local project_dir="${PROJECTS_ROOT}/${ORG}/${NAME}"

    if [[ "$DRY_RUN" == true ]]; then
        log_ok "[DRY-RUN] Would git init + initial commit in: ${project_dir}"
        return 0
    fi

    log_info "Initializing git repository"

    (
        cd "$project_dir"
        git init
        git add -A
        git commit -m "init: ${NAME} from template-${TEMPLATE}" --no-gpg-sign
    ) || {
        log_fail "git init/commit failed"
        return 1
    }

    log_ok "Git repository initialized with initial commit"
}
# endregion GIT_INIT

# region GEN_SETUP_CHECKLIST
generate_checklist() {
    local project_dir="${PROJECTS_ROOT}/${ORG}/${NAME}"
    local checklist="${project_dir}/_SETUP_CHECKLIST.md"

    if [[ "$DRY_RUN" == true ]]; then
        log_ok "[DRY-RUN] Would generate: ${checklist}"
        return 0
    fi

    log_info "Generating setup checklist"

    cat > "$checklist" <<CHECKLIST
# Setup Checklist: ${NAME}

> ⚠️ Выполните шаги по порядку. Команды можно копировать и вставлять.

## 1. Создать репозиторий на GitHub

\`\`\`bash
gh repo create ${ORG}/${NAME} --private --description "${NAME} project"
\`\`\`

## 2. Добавить remote и запушить

\`\`\`bash
cd ${PROJECTS_ROOT}/${ORG}/${NAME}
git remote add origin git@github.com:${ORG}/${NAME}.git
git push -u origin main
\`\`\`

## 3. Org-level secrets (установлены в TronyxLab org-secrets)

| Secret | Назначение |
|--------|-----------|
| \`NODE_CONFIGS_TOKEN\` | PAT read-only на TronyxLab/ai-platform для клонирования node-configs |
| \`CI_DEPLOY_KEY\` | SSH private key для ci-deploy forced-command deploy |
| \`GIT_MIRROR_TOKEN\` | PAT для зеркалирования кода из Tronyx161 в TronyxLab |
| \`GHCR_PULL_TOKEN\` | Classic PAT read:packages для ghcr.io docker login на VPS |

## 4. Настроить Docker Registry

Registry \`ghcr.io\` уже прописан в \`docker-compose.yml\`.
GitHub Actions использует \`GITHUB_TOKEN\` (доступен автоматически).
CHECKLIST

    if [[ -n "$DOMAIN" ]]; then
        cat >> "$checklist" <<CHECKLIST

## 5. TLS-сертификат выпускается автоматически

## 6. Применить nginx overlay на сервере

\`\`\`bash
sudo nginx -t && sudo nginx -s reload
\`\`\`
CHECKLIST
    fi

    if [[ -n "$DATABASE" ]]; then
        cat >> "$checklist" <<CHECKLIST

## \${CHECKLIST_STEP}. Создать базу данных

\`\`\`bash
sudo -u postgres psql -c "CREATE DATABASE ${DATABASE};"
\`\`\`
CHECKLIST
    fi

    cat >> "$checklist" <<CHECKLIST

---

> Сгенерировано \`add-project.sh\` ($(date -u '+%Y-%m-%dT%H:%M:%SZ'))
CHECKLIST

    log_ok "Setup checklist generated: ${checklist}"
}
# endregion GEN_SETUP_CHECKLIST

# region CREATE_GITHUB_REPO
create_github_repo() {
    if ! command -v gh &>/dev/null; then
        log_warn "GitHub CLI (gh) not found — skipping repo creation."
        return 0
    fi

    if ! gh auth status &>/dev/null; then
        log_warn "gh not authenticated — skipping repo creation."
        return 0
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log_ok "[DRY-RUN] Would create GitHub repo: ${ORG}/${NAME}"
        return 0
    fi

    local project_dir="${PROJECTS_ROOT}/${ORG}/${NAME}"

    if gh repo view "${ORG}/${NAME}" &>/dev/null; then
        log_info "GitHub repo already exists: ${ORG}/${NAME} — skipping creation"
        if ! git -C "$project_dir" remote get-url origin &>/dev/null; then
            git -C "$project_dir" remote add origin "git@github.com:${ORG}/${NAME}.git"
            log_ok "Added git remote: origin git@github.com:${ORG}/${NAME}.git"
        fi
        return 0
    fi

    log_info "Creating GitHub repo: ${ORG}/${NAME}"

    if gh repo create "${ORG}/${NAME}" --private --description "${NAME} project" 2>&1; then
        log_ok "GitHub repo created: ${ORG}/${NAME}"
        git -C "$project_dir" remote add origin "git@github.com:${ORG}/${NAME}.git"
        git -C "$project_dir" push -u origin main 2>&1 || log_warn "git push failed — push manually"
        log_ok "Initial push to origin/main complete"
    else
        log_warn "Failed to create GitHub repo: ${ORG}/${NAME} — create manually"
    fi
}
# endregion CREATE_GITHUB_REPO

# region RUN_ADD_VHOST
run_add_vhost() {
    if [[ -z "$DOMAIN" ]]; then
        return 0
    fi

    local add_vhost_script="${SCRIPT_DIR}/add-vhost.sh"

    if [[ ! -x "$add_vhost_script" ]]; then
        log_warn "add-vhost.sh not found or not executable: ${add_vhost_script}"
        return 0
    fi

    if [[ "$DRY_RUN" == true ]]; then
        log_ok "[DRY-RUN] Would call: ${add_vhost_script} --project-dir ${PROJECTS_ROOT}/${ORG}/${NAME}"
        return 0
    fi

    log_info "Generating nginx vhost via add-vhost.sh"

    local node_configs_dir="${PROJECTS_ROOT}/${ORG}/node-configs"
    if [[ ! -d "$node_configs_dir" ]]; then
        log_warn "node-configs dir not found: ${node_configs_dir}"
        return 0
    fi

    "$add_vhost_script" \
        --project-dir "${PROJECTS_ROOT}/${ORG}/${NAME}" \
        --node-configs-dir "$node_configs_dir" || {
        log_warn "add-vhost.sh returned non-zero — check manually"
    }
}
# endregion RUN_ADD_VHOST

# region REGISTER_IN_NODE_YAML
register_in_node_yaml() {
    local name="$1"
    local org="$2"
    local node="$3"
    local ptype="$4"
    local context="$5"
    local domain="$6"
    local database="$7"

    local node_configs_dir="${PROJECTS_ROOT}/${org}/node-configs"
    local node_yaml="${node_configs_dir}/${node}/node.yaml"

    log_info "Registering project in node.yaml: ${node_yaml}"

    if [[ ! -f "$node_yaml" ]]; then
        log_warn "node.yaml not found: ${node_yaml} — skipping registration"
        return 0
    fi

    if command -v yq &>/dev/null; then
        local existing
        existing=$(yq eval ".projects[] | select(.name == \"${name}\" or .repo == \"${org}/${name}\") | .name" "$node_yaml" 2>/dev/null || true)
        if [[ -n "$existing" ]]; then
            log_ok "Project already registered in node.yaml: ${name} — SKIP (idempotent)"
            return 0
        fi

        if [[ "$DRY_RUN" == true ]]; then
            log_ok "[DRY-RUN] Would register in node.yaml: name=${name} repo=${org}/${name} type=${ptype} context=${context}"
            return 0
        fi

        local yaml_entry="{\"name\": \"${name}\", \"repo\": \"${org}/${name}\", \"type\": \"${ptype}\", \"context\": \"${context}\""
        if [[ -n "$domain" ]]; then
            yaml_entry+=", \"domain\": \"${domain}\""
        fi
        if [[ -n "$database" ]]; then
            yaml_entry+=", \"database\": \"${database}\""
        fi
        yaml_entry+="}"

        yq eval -i ".projects += [${yaml_entry}]" "$node_yaml"
        log_ok "Project registered in node.yaml: ${name} (context: ${context})"

    elif command -v python3 &>/dev/null && python3 -c "import yaml" 2>/dev/null; then
        log_info "yq not found — using Python3+yaml fallback for node.yaml registration"

        if [[ "$DRY_RUN" == true ]]; then
            log_ok "[DRY-RUN] Would register in node.yaml: name=${name} repo=${org}/${name} type=${ptype} context=${context}"
            return 0
        fi

        REG_NAME="$name" \
        REG_REPO="${org}/${name}" \
        REG_TYPE="$ptype" \
        REG_CONTEXT="$context" \
        REG_DOMAIN="$domain" \
        REG_DATABASE="$database" \
        REG_NODE_YAML="$node_yaml" \
        python3 <<'PYEOF' || log_warn "Python registration failed — register manually"
import os, yaml, sys

name = os.environ.get('REG_NAME', '')
repo = os.environ.get('REG_REPO', '')
ptype = os.environ.get('REG_TYPE', '')
ctx = os.environ.get('REG_CONTEXT', '')
domain = os.environ.get('REG_DOMAIN', '')
database = os.environ.get('REG_DATABASE', '')
node_yaml_path = os.environ.get('REG_NODE_YAML', '')

if not name or not repo or not node_yaml_path:
    sys.exit(0)

with open(node_yaml_path) as f:
    data = yaml.safe_load(f)

if 'projects' in data:
    for p in data['projects']:
        if p.get('name') == name or p.get('repo') == repo:
            print(f"[IMP:9][add-project][register] Idempotent SKIP — {name} already in node.yaml", file=sys.stderr)
            sys.exit(0)

entry = {'name': name, 'repo': repo, 'type': ptype, 'context': ctx}
if domain:
    entry['domain'] = domain
if database:
    entry['database'] = database

if 'projects' not in data:
    data['projects'] = []
data['projects'].append(entry)

with open(node_yaml_path, 'w') as f:
    yaml.dump(data, f, default_flow_style=False, sort_keys=False)

print(f"[IMP:9][add-project][register] Registered {name} → {node_yaml_path}", file=sys.stderr)
PYEOF
    else
        log_warn "Neither yq nor Python3+yaml available — cannot auto-register in node.yaml"
        log_warn "Manually add to ${node_yaml}:"
        log_warn "  - name: ${name}"
        log_warn "    repo: ${org}/${name}"
    fi
}
# endregion REGISTER_IN_NODE_YAML

# region MAIN
main() {
    log_info "START: add-project --name ${NAME} --template ${TEMPLATE} --org ${ORG} --node ${NODE}"

    parse_args "$@"
    show_plan
    confirm

    log_ok "Starting project creation"

    copy_template
    generate_ai_platform_yaml \
        "${PROJECTS_ROOT}/${ORG}/${NAME}" \
        "$NAME" "$TEMPLATE" "$ORG" "$NODE" "$DOMAIN" "$DATABASE" "$MODE" "$CONTEXT"
    replace_placeholders
    git_init_project
    create_github_repo
    generate_checklist
    run_add_vhost

    if [[ "$REGISTER" == true ]]; then
        register_in_node_yaml \
            "$NAME" "$ORG" "$NODE" "$TEMPLATE" "$CONTEXT" "$DOMAIN" "$DATABASE"
    else
        log_info "Registration skipped (use --register --context <name> to register in node.yaml)"
    fi

    local project_dir="${PROJECTS_ROOT}/${ORG}/${NAME}"
    echo ""
    echo "──────────────────────────────────────────────────────"
    echo "  ✅ Проект создан: ${project_dir}/"
    echo "  📋 Следующие шаги: ${project_dir}/_SETUP_CHECKLIST.md"
    echo "──────────────────────────────────────────────────────"
    echo ""
    log_ok "DONE: project ${NAME} created successfully"
}

# endregion MAIN

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
