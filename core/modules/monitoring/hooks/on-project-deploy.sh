#!/usr/bin/env bash
# GREP_SUMMARY: monitoring hook on-project-deploy prometheus grafana loki langfuse catalog targets dashboards retention
# STRUCTURE: ┌args(PROJECT_DIR,PROJECT,NODE_NAME)┐ → ◇ load ai-platform.yaml → ◇ merge L1+L2+L3 config → ◇ prometheus targets → ◇ grafana dashboards → ◇ loki retention → ◇ langfuse projects → ◇ catalog refresh → ⎋ log_imp done
# region MODULE_CONTRACT
## @purpose  Post-deploy hook for monitoring module: reconfigure Prometheus targets, Grafana dashboards, Loki retention, Langfuse projects, and refresh service catalog
## @scope    Invoked by deploy-project.sh after successful project deploy; receives PROJECT_DIR, PROJECT, NODE_NAME
## @invariants
##   - Non-fatal: errors are logged but do not block deploy
##   - Sources ../../../lib/logging.sh for LDD logging
##   - L1 defaults from core/modules/monitoring/defaults.yaml (if exists)
##   - L2 overrides from PLATFORM_ROOT/node-configs/${NODE_NAME}/projects/${PROJECT}.yaml (context override, if exists)
##   - L3 from ai-platform.yaml monitoring section
##   - Prometheus file-based service discovery: /opt/platform/prometheus-targets/${PROJECT}.json
##   - Grafana dashboards: /opt/grafana/provisioning/dashboards/${PROJECT}.json (from template)
##   - Loki runtime config: runtime config file updated with retention stream
##   - Prometheus/Loki reloaded via HTTP POST after config changes
##   - Langfuse project created if LLM needs detected
## @rationale Extracted from deploy-project.sh:_reconfigure_monitoring() to hook system; each module owns its post-deploy logic
## @changes  Extracted from deploy-project.sh:492-783 + _generate_alert_rules (deploy-project.sh:785-801)
# endregion MODULE_CONTRACT

set -euo pipefail

__LOG_PREFIX="monitoring-hook"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../../../lib/logging.sh"

HOOK_PROJECT_DIR="${1:-}"
HOOK_PROJECT="${2:-}"
HOOK_NODE_NAME="${3:-}"

if [[ -z "$HOOK_PROJECT_DIR" || -z "$HOOK_PROJECT" ]]; then
    log_imp 6 "hook" "Missing PROJECT_DIR or PROJECT — skipping monitoring hook"
    exit 0
fi

PLATFORM_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

# region LOAD_PROJECT_CONFIG
## @purpose  Load and merge monitoring configuration from ai-platform.yaml + L1 defaults + L2 overrides
## @io       Output: merged_config JSON string, project_type, needs_llm flags via global variables
_load_project_config() {
    local ai_yaml="${HOOK_PROJECT_DIR}/ai-platform.yaml"

    if [[ ! -f "$ai_yaml" ]]; then
        log_imp 8 "config" "No ai-platform.yaml found — skipping monitoring reconfig"
        return 1
    fi

    local project_config
    project_config="$(python3 -c "
import sys, json, yaml
with open('${ai_yaml}') as f:
    data = yaml.safe_load(f)
print(json.dumps(data))
" 2>/dev/null || echo "{}")"

    local has_monitoring
    has_monitoring="$(echo "$project_config" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print('true' if isinstance(data.get('monitoring'), dict) else 'false')
" 2>/dev/null || echo "false")"

    if [[ "$has_monitoring" != "true" ]]; then
        log_imp 8 "config" "No monitoring section in ai-platform.yaml — skipping (backward compat)"
        return 1
    fi

    PROJECT_TYPE="$(echo "$project_config" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('type', ''))
" 2>/dev/null || echo "")"

    log_imp 8 "config" "Loading monitoring config for ${HOOK_PROJECT} (type=${PROJECT_TYPE})"

    local defaults_file="${PLATFORM_ROOT}/core/modules/monitoring/defaults.yaml"
    local l1_config="{}"
    if [[ -f "$defaults_file" ]]; then
        l1_config="$(python3 -c "
import sys, json, yaml
with open('${defaults_file}') as f:
    data = yaml.safe_load(f)
result = dict(data.get('monitoring', {}))
type_defaults = data.get('type-defaults', {}).get('${PROJECT_TYPE}', {})
result.update(type_defaults)
print(json.dumps(result))
" 2>/dev/null || echo "{}")"
        log_imp 8 "config" "L1 defaults loaded"
    else
        log_imp 6 "config" "L1 defaults file not found: ${defaults_file}"
    fi

    local l2_config="{}"
    local context_override="${PLATFORM_ROOT}/node-configs/${HOOK_NODE_NAME}/projects/${HOOK_PROJECT}.yaml"
    if [[ -f "$context_override" ]]; then
        l2_config="$(python3 -c "
import sys, json, yaml
with open('${context_override}') as f:
    data = yaml.safe_load(f)
print(json.dumps(data.get('monitoring', {})))
" 2>/dev/null || echo "{}")"
        log_imp 8 "config" "L2 context overrides loaded"
    fi

    local l3_config
    l3_config="$(echo "$project_config" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(json.dumps(data.get('monitoring', {})))
" 2>/dev/null || echo "{}")"

    MERGED_CONFIG="$(python3 -c "
import sys, json
l1 = json.loads('''$(echo "$l1_config" | python3 -c "import sys; print(sys.stdin.read())")''')
l2 = json.loads('''$(echo "$l2_config" | python3 -c "import sys; print(sys.stdin.read())")''')
l3 = json.loads('''$(echo "$l3_config" | python3 -c "import sys; print(sys.stdin.read())")''')
result = dict(l1)
result.update(l2)
result.update(l3)
print(json.dumps(result))
" 2>/dev/null || echo "{}")"

    NEEDS_LLM="$(echo "$project_config" | python3 -c "
import sys, json
data = json.load(sys.stdin)
needs = data.get('needs', {})
llm = needs.get('llm', False)
print(str(bool(llm)).lower())
" 2>/dev/null || echo "false")"

    log_imp 9 "config" "Merged monitoring config for ${HOOK_PROJECT}"
    return 0
}
# endregion LOAD_PROJECT_CONFIG

# region PROMETHEUS_TARGETS
## @purpose  Generate Prometheus file-based service discovery target for the project
_generate_prometheus_targets() {
    local metrics_enabled
    metrics_enabled="$(echo "$MERGED_CONFIG" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(str(data.get('metrics', 'false')).lower())
" 2>/dev/null || echo "false")"

    if [[ "$metrics_enabled" != "true" ]]; then
        log_imp 8 "prometheus" "Metrics disabled for ${HOOK_PROJECT} — skipping Prometheus target"
        return 0
    fi

    local metrics_port
    metrics_port="$(echo "$MERGED_CONFIG" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('metrics_port', 3000))
" 2>/dev/null || echo "3000")"

    local targets_dir="${PLATFORM_ROOT}/prometheus-targets"
    mkdir -p "$targets_dir"

    local target_file="${targets_dir}/${HOOK_PROJECT}.json"
    python3 -c "
import json
target = {
    'targets': ['${HOOK_PROJECT}:${metrics_port}'],
    'labels': {
        'project': '${HOOK_PROJECT}',
        'type': '${PROJECT_TYPE}',
        'node': '${HOOK_NODE_NAME}',
        'service': '${HOOK_PROJECT}'
    }
}
with open('${target_file}', 'w') as f:
    json.dump(target, f, indent=2)
print('Generated: ${target_file}')
" 2>&1 | while IFS= read -r line; do
        log_imp 7 "prometheus" "${line}"
    done

    log_imp 9 "prometheus" "Prometheus target file generated: ${target_file} (port=${metrics_port})"
}
# endregion PROMETHEUS_TARGETS

# region GRAFANA_DASHBOARDS
## @purpose  Generate Grafana dashboard from template if dashboard monitoring is enabled
_generate_grafana_dashboards() {
    local dashboard_enabled
    dashboard_enabled="$(echo "$MERGED_CONFIG" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(str(data.get('dashboard', 'false')).lower())
" 2>/dev/null || echo "false")"

    if [[ "$dashboard_enabled" != "true" ]]; then
        log_imp 8 "grafana" "Dashboard disabled for ${HOOK_PROJECT} — skipping"
        return 0
    fi

    local dashboards_dir="/opt/grafana/provisioning/dashboards"
    local template="${PLATFORM_ROOT}/core/modules/monitoring/config/dashboards/project-template.json"

    if [[ ! -f "$template" ]]; then
        log_imp 6 "grafana" "Dashboard template not found: ${template} — skipping"
        return 0
    fi

    mkdir -p "$dashboards_dir"
    local dash_file="${dashboards_dir}/${HOOK_PROJECT}.json"
    local engine="${PLATFORM_ROOT}/core/internal/template-engine.sh"
    if [[ -f "$engine" ]]; then
        "$engine" render "$template" "$dash_file" \
            "PROJECT=${HOOK_PROJECT}" \
            "TYPE=${PROJECT_TYPE}" \
            "NODE=${HOOK_NODE_NAME}"
    else
        log_imp 6 "grafana" "template-engine.sh not found at ${engine} — falling back to sed"
        sed \
            -e "s/\$PROJECT/${HOOK_PROJECT}/g" \
            -e "s/\$TYPE/${PROJECT_TYPE}/g" \
            -e "s/\$NODE/${HOOK_NODE_NAME}/g" \
            "$template" > "$dash_file"
    fi
    log_imp 9 "grafana" "Dashboard generated: ${dash_file}"
}
# endregion GRAFANA_DASHBOARDS

# region LOKI_RETENTION
## @purpose  Update Loki runtime config with project retention stream
_update_loki_retention() {
    local logs_retention
    logs_retention="$(echo "$MERGED_CONFIG" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('logs_retention', '7d'))
" 2>/dev/null || echo "7d")"

    local loki_runtime_config="${PLATFORM_ROOT}/core/modules/logging/config/loki-runtime-config.yml"
    local retention_hours
    case "$logs_retention" in
        forever) retention_hours=0 ;;
        *d)      retention_hours=$(( ${logs_retention%d} * 24 )) ;;
        *h)      retention_hours=${logs_retention%h} ;;
        *)       retention_hours=168 ;;
    esac

    python3 -c "
import yaml, sys, os

runtime_path = '${loki_runtime_config}'
project = '${HOOK_PROJECT}'
period_h = ${retention_hours}

config = {}
if os.path.exists(runtime_path):
    with open(runtime_path) as f:
        config = yaml.safe_load(f) or {}

streams = config.setdefault('limits_config', {}).setdefault('retention_stream', [])

exists = any(
    s.get('selector', '') == '{compose_project=\"' + project + '\"}'
    for s in streams
)

if not exists:
    new_rule = {
        'selector': '{compose_project=\"' + project + '\"}',
        'priority': 0,
        'period': str(period_h) + 'h',
    }
    inserted = False
    for i, s in enumerate(streams):
        if 'compose_project=~' in s.get('selector', ''):
            streams.insert(i, new_rule)
            inserted = True
            break
    if not inserted:
        streams.append(new_rule)

    with open(runtime_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    print('ADDED')
else:
    print('EXISTS')
" 2>&1 | while IFS= read -r line; do
        log_imp 7 "loki" "Loki retention: ${line}"
    done

    log_imp 9 "loki" "Loki runtime config updated for ${HOOK_PROJECT}: ${logs_retention} (${retention_hours}h)"
}
# endregion LOKI_RETENTION

# region LANGFUSE_PROJECTS
## @purpose  Create Langfuse project for the project if LLM needs are detected
_create_langfuse_project() {
    if [[ "$NEEDS_LLM" != "true" ]]; then
        log_imp 8 "langfuse" "No LLM needs declared — skipping Langfuse project"
        return 0
    fi

    local ai_retention
    ai_retention="$(echo "$MERGED_CONFIG" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('ai_retention', '30d'))
" 2>/dev/null || echo "30d")"

    log_imp 8 "langfuse" "LLM detected — creating Langfuse project for ${HOOK_PROJECT}..."
    local lf_create
    lf_create="$(curl -s -X POST http://langfuse:3000/api/public/projects \
        -H "Authorization: Bearer ${LANGFUSE_SECRET_KEY:-}" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"${HOOK_PROJECT}\", \"retention\": ${ai_retention%d}}" 2>&1 || echo "FAILED")"
    if echo "$lf_create" | grep -qi "already exists\|name.*taken\|409"; then
        log_imp 8 "langfuse" "Langfuse project '${HOOK_PROJECT}' already exists — skipping"
    elif echo "$lf_create" | grep -qi "error\|FAILED\|401\|403"; then
        log_imp 6 "langfuse" "Langfuse project creation failed (may not be configured): ${lf_create}"
    else
        log_imp 9 "langfuse" "Langfuse project created: ${HOOK_PROJECT}"
    fi
}
# endregion LANGFUSE_PROJECTS

# region CATALOG_REFRESH
## @purpose  Refresh service catalog after deploy
_refresh_catalog() {
    local catalog_script="${PLATFORM_ROOT}/core/internal/catalog/generate-catalog.sh"
    if [[ -x "$catalog_script" ]]; then
        "$catalog_script" 2>/dev/null || log_imp 6 "catalog" "catalog.json generation failed"
        log_imp 8 "catalog" "Catalog refresh invoked"
    else
        log_imp 7 "catalog" "Catalog script not found: ${catalog_script} — skipping"
    fi
}
# endregion CATALOG_REFRESH

# region RELOAD_SERVICES
## @purpose  Reload Prometheus and Loki after configuration changes
_reload_services() {
    log_imp 8 "reload" "Reloading Prometheus..."
    local prom_reload
    prom_reload="$(curl -s -X POST http://prometheus:9090/-/reload 2>&1 || echo "FAILED")"
    log_imp 8 "reload" "Prometheus reload: ${prom_reload}"

    log_imp 8 "reload" "Reloading Loki..."
    local loki_reload
    loki_reload="$(curl -s -X POST http://loki:3100/reload 2>&1 || echo "FAILED")"
    log_imp 8 "reload" "Loki reload: ${loki_reload}"
}
# endregion RELOAD_SERVICES

# region GENERATE_ALERT_RULES
## @purpose  Generate Prometheus alert rules for the project if alerting is enabled
_generate_alert_rules() {
    local alerting_enabled
    alerting_enabled="$(echo "$MERGED_CONFIG" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(str(data.get('alerting', 'false')).lower())
" 2>/dev/null || echo "false")"

    if [[ "$alerting_enabled" != "true" ]]; then
        log_imp 8 "alerting" "Alerting disabled for ${HOOK_PROJECT} — skipping alert rules"
        return 0
    fi

    local template="${PLATFORM_ROOT}/core/modules/monitoring/config/alert-rules.yml"
    local rules_dir="/opt/prometheus/rules"
    local output_file="${rules_dir}/${HOOK_PROJECT}-alerts.yml"

    if [[ ! -f "$template" ]]; then
        log_imp 6 "alerting" "Alert rules template not found: ${template} — skipping"
        return 0
    fi

    mkdir -p "$rules_dir"
    local engine="${PLATFORM_ROOT}/core/internal/template-engine.sh"
    if [[ -f "$engine" ]]; then
        "$engine" render "$template" "$output_file" "PROJECT=${HOOK_PROJECT}"
    else
        log_imp 6 "alerting" "template-engine.sh not found at ${engine} — falling back to sed"
        sed "s/\${PROJECT}/${HOOK_PROJECT}/g" "$template" > "$output_file"
    fi
    log_imp 8 "alerting" "Alert rules generated: ${output_file}"
}
# endregion GENERATE_ALERT_RULES

# region MAIN
## @purpose  Main hook entry: orchestrate all monitoring post-deploy operations
main() {
    log_imp 9 "hook" "=== monitoring on-project-deploy START: ${HOOK_PROJECT} ==="

    if ! _load_project_config; then
        log_imp 8 "hook" "No monitoring config — skipping hook for ${HOOK_PROJECT}"
        exit 0
    fi

    _generate_alert_rules
    _generate_prometheus_targets
    _generate_grafana_dashboards
    _update_loki_retention
    _reload_services
    _create_langfuse_project
    _refresh_catalog

    log_imp 9 "hook" "=== monitoring on-project-deploy DONE: ${HOOK_PROJECT} ==="
}
# endregion MAIN

main "$@"
