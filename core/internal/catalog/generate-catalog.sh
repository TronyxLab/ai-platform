#!/usr/bin/env bash
# GREP_SUMMARY: generate-catalog catalog.json project-registry ai-platform-yaml index
# STRUCTURE: ▶ init → ○ scan $PROJECTS_ROOT/*/ai-platform.yaml → ⊕ generate catalog.json → ⎋ exit
# region MODULE_CONTRACT
## @purpose  Generate ${PLATFORM_ROOT}/catalog.json — центральный реестр всех проектов платформы
## @scope    Called by deploy-project.sh _reconfigure_monitoring() after each successful deploy.
## @location core/internal/catalog/generate-catalog.sh — moved from core/scripts/generate-catalog.sh
## @invariants
##   - Обходит $PROJECTS_ROOT/*/*/ai-platform.yaml
##   - Генерирует валидный JSON-массив с name, type, node, domain, database, metrics_port
##   - catalog.json сохраняется в ${PLATFORM_ROOT}/catalog.json
## @rationale Единый источник правды для AI-агентов о составе проектов платформы.
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Canonical PLATFORM_ROOT — source paths.sh; fallback to filesystem resolution
# shellcheck source=core/lib/paths.sh
if [[ -f "${SCRIPT_DIR}/../../lib/paths.sh" ]]; then
    source "${SCRIPT_DIR}/../../lib/paths.sh"
else
    # fallback: paths.sh not yet available; see core/lib/paths.sh (SoT)
    PLATFORM_ROOT="${PLATFORM_ROOT:-/opt/platform}"
fi

CATALOG_FILE="${CATALOG_FILE:-${PLATFORM_ROOT}/catalog.json}"
PROJECTS_ROOT="${PROJECTS_ROOT:-/opt/projects}"
CATALOG_DIR="$(dirname "$CATALOG_FILE")"

__LOG_PREFIX="generate-catalog"
source "${PLATFORM_ROOT}/core/lib/logging.sh"

log_imp 8 "START" "Scanning projects in ${PROJECTS_ROOT}"

# Ensure catalog directory exists (idempotent)
mkdir -p "${CATALOG_DIR}"

python3 - "$CATALOG_FILE" "$PROJECTS_ROOT" <<'PYEOF' 2>&1
import sys, json, os, yaml

catalog_file = sys.argv[1]
projects_root = sys.argv[2]

catalog = []

for org_dir in os.listdir(projects_root):
    org_path = os.path.join(projects_root, org_dir)
    if not os.path.isdir(org_path):
        continue
    for proj_dir in os.listdir(org_path):
        proj_path = os.path.join(org_path, proj_dir)
        if not os.path.isdir(proj_path):
            continue
        yaml_file = os.path.join(proj_path, 'ai-platform.yaml')
        if not os.path.isfile(yaml_file):
            continue

        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            entry = {
                'name': data.get('name', proj_dir),
                'type': data.get('type', 'unknown'),
                'node': data.get('target_node', ''),
                'org': org_dir,
                'domain': None,
                'database': None,
                'metrics_port': None,
            }
            needs = data.get('needs', {})
            if isinstance(needs, dict):
                entry['domain'] = needs.get('domain') if needs.get('domain') and needs.get('domain') != False else None
                entry['database'] = needs.get('database') if needs.get('database') and needs.get('database') != False else None

            monitoring = data.get('monitoring', {})
            if isinstance(monitoring, dict):
                entry['metrics_port'] = monitoring.get('metrics_port')

            catalog.append(entry)
            log_message = f"  {org_dir}/{proj_dir} (type={entry['type']})"
            print(f"[IMP:8][generate-catalog] {log_message}", file=sys.stderr)
        except Exception as e:
            print(f"[IMP:6][generate-catalog]  WARN: {yaml_file}: {e}", file=sys.stderr)

catalog.sort(key=lambda x: (x['org'], x['name']))

os.makedirs(os.path.dirname(catalog_file), exist_ok=True)
with open(catalog_file, 'w') as f:
    json.dump(catalog, f, indent=2, ensure_ascii=False)

count = len(catalog)
print(f"[IMP:9][generate-catalog] DONE: {count} projects registered in {catalog_file}", file=sys.stderr)
PYEOF
_GEN_CATALOG_RC=$?
if [ "$_GEN_CATALOG_RC" -ne 0 ]; then
    log_imp 9 "ERROR" "Failed to generate catalog"
    exit 1
fi
unset _GEN_CATALOG_RC
