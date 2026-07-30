#!/usr/bin/env bash
# Facade — delegates to Python core (generate_catalog.py)
# GREP_SUMMARY: generate-catalog, catalog.json, project-registry, ai-platform-yaml, index
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/generate_catalog.py" "$@"
