#!/usr/bin/env bash
# GREP_SUMMARY: add-vhost nginx vhost generate remove render-all shell-facade vhost-renderer dispatch
# STRUCTURE: ▶ init → ⎋ exec python3 -m core.internal.scaffold.vhost_renderer → ⊕ exit
# region MODULE_CONTRACT
## @purpose  Shell facade for vhost_renderer.py (Strangler-Fig). Делегирует ВСЮ логику
##           (arg-парсинг, dispatch add/remove/render-all) в vhost_renderer.py CLI
##           (DevPlan 173 W2.3 — legacy-флаги --add/--remove/--render-all нормализуются в
##           _normalize_mode). Zero inline python3.
## @scope    Entry point called by: Makefile render-vhosts, project_scaffolder.run_add_vhost,
##           context_deployer._step_vhosts, scaffold.sh add-vhost. All business logic in Python.
## @invariants
##   - Zero inline python3 -c / <<PYEOF blocks (enforced by CI grep)
##   - All YAML parsing, template generation, nginx harness — in Python
##   - Exit code propagated from Python module
## @rationale  Языковая политика: 149-LOC shell (parse_args + dispatch) → exec; вся логика в Python.
## @changes    2026-07-26 · Wave 5b — Rewritten as thin shell facade (129 LOC)
## @changes    2026-08-16 · DevPlan 173 W2.3 — parse_args+dispatch извлечены в vhost_renderer._normalize_mode
# endregion MODULE_CONTRACT

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLATFORM_ROOT="${PLATFORM_ROOT:-$(cd "${SCRIPT_DIR}/../../.." 2>/dev/null && pwd || true)}"

# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · python3 -m core.* fails outside repo root (ModuleNotFoundError)
# · Fix: export PYTHONPATH с PLATFORM_ROOT — канонический паттерн audit.sh/converge.sh/provision-llm.sh.
export PYTHONPATH="${PLATFORM_ROOT}:${PYTHONPATH:-}"

exec python3 -m core.internal.scaffold.vhost_renderer "$@"
