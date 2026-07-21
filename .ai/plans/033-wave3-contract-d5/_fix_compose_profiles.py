#!/usr/bin/env python3
# GREP_SUMMARY: fix-compose-profiles, wave3, COMPOSE_PROFILES, tmp-script
# STRUCTURE: ┌4 source files┐ → ○ read → ┌apply patches┐ → ⊕ Σ written → ⎋ exit
# region MODULE_CONTRACT
## @purpose — Apply COMPOSE_PROFILES export to 4 remaining files (Makefile + deploy-modules.sh + deploy-project.sh + adopt-project.sh) for ${VAR:?error} compatibility (DevPlan 033 Option A)
## @scope — One-shot migration script, not part of platform runtime
## @invariants — All 4 files must exist and contain expected patterns before patching; exits 1 if pattern not found
## @rationale — Temporary migration tool for DD3 reversal; will be removed after all branches are updated
# endregion MODULE_CONTRACT

import sys

COMPOSE_PROFILES = "postgres,redis,nginx,clickhouse,backup-cron,hermes-agent,monitoring,logging,litellm,langfuse,infra-metrics,minio,status-page"

files = {}

# TASK-1: Makefile — insert after line 283
with open("Makefile", "r") as f:
    lines = f.readlines()
insert_at = 283  # line 284 is validate-modules
new_lines = [
    "\n",
    "# COMPOSE_PROFILES source of truth: all 13 Docker modules with profiles.\n",
    "# Used by CI and production scripts for ${VAR:?error} compatibility (DevPlan 033 Option A).\n",
    "_get_all_profiles:\n",
    '\t@echo "' + COMPOSE_PROFILES + '"\n',
    "\n",
    "# Export COMPOSE_PROFILES globally — covers gate, test, and all docker compose invocations.\n",
    "# Uses ?= so existing env takes precedence.\n",
    "export COMPOSE_PROFILES ?= " + COMPOSE_PROFILES + "\n",
]
for i, line in enumerate(new_lines):
    lines.insert(insert_at + i, line)
files["Makefile"] = "".join(lines)

# TASK-4: deploy-project.sh — replace block before line 718
with open("core/internal/deploy/deploy-project.sh", "r") as f:
    content = f.read()
old = '    local config_output image_pattern\n    config_output="$(docker compose config 2>/dev/null)" || {'
new = '    local config_output image_pattern\n    # COMPOSE_PROFILES — required for ${VAR:?error} enforcement (DevPlan 033 W3-E3 Option A).\n    export COMPOSE_PROFILES="${COMPOSE_PROFILES:-' + COMPOSE_PROFILES + '}"\n    config_output="$(docker compose config 2>/dev/null)" || {'
if old not in content:
    print(f"ERROR: pattern not found in deploy-project.sh")
    sys.exit(1)
files["core/internal/deploy/deploy-project.sh"] = content.replace(old, new)

# TASK-5: adopt-project.sh — replace block
with open("core/internal/scaffold/adopt-project.sh", "r") as f:
    content = f.read()
old = '    if command -v docker &>/dev/null; then\n        local docker_result\n        docker_result="$(docker compose -f "$compose_path" config 2>/dev/null)" || true'
new = '    if command -v docker &>/dev/null; then\n        local docker_result\n        # COMPOSE_PROFILES — required for ${VAR:?error} enforcement (DevPlan 033 W3-E3 Option A).\n        export COMPOSE_PROFILES="${COMPOSE_PROFILES:-' + COMPOSE_PROFILES + '}"\n        docker_result="$(docker compose -f "$compose_path" config 2>/dev/null)" || true'
if old not in content:
    print(f"ERROR: pattern not found in adopt-project.sh")
    sys.exit(1)
files["core/internal/scaffold/adopt-project.sh"] = content.replace(old, new)

# TASK-6: deploy-modules.sh — insert before observability block
with open("core/internal/bootstrap/deploy-modules.sh", "r") as f:
    content = f.read()
old = '    if [[ "$module_name" == "observability" ]]; then'
if old not in content:
    print(f"ERROR: pattern not found in deploy-modules.sh")
    sys.exit(1)
new = '    # COMPOSE_PROFILES — required for ${VAR:?error} enforcement (DevPlan 033 W3-E3 Option A).\n    # Line 501 below calls docker compose config --services without --profile — needs COMPOSE_PROFILES.\n    export COMPOSE_PROFILES="${COMPOSE_PROFILES:-' + COMPOSE_PROFILES + '}"\n    if [[ "$module_name" == "observability" ]]; then'
files["core/internal/bootstrap/deploy-modules.sh"] = content.replace(old, new)

# Write all
for path, data in files.items():
    with open(path, "w") as f:
        f.write(data)
    print(f"✓ {path}")
