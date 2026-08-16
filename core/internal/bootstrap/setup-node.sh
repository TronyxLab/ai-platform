#!/usr/bin/env bash
# GREP_SUMMARY: setup-node sudoers visudo facade python-delegation node node-lifecycle atomic
# STRUCTURE: ▶ ┌NODE_NAME|hostname (argv игнорируется)┐ → ⚡ PYTHONPATH export (script-path канон) → ▶ exec python3 setup_node.py "$@" → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Thin shell facade (DevPlan 164 W3.5-1, SH→Python) — generate_sudoers в setup_node.py
##           (135 LOC shell → фасад <100). Прямое замещение: имя/путь сохранены; аргументы
##           passthrough (но setup_node.py их ИГНОРИРУЕТ — NODE_NAME env | hostname, shell parity).
## @scope    Called from lifecycle/phases/system.py φ3 (phase_platform_setup) via `bash setup-node.sh`
##           (non_fatal, fatal_rc=127) — фасад сохраняет контракт вызова (bash, без аргументов).
## @invariants
##   - Exit-коды {0,1} — passthrough setup_node.py (root-guard / invalid NODE_NAME / visudo fail → 1)
##   - PYTHONPATH="${CORE_DIR}/.." — script-path exec канон (TRAP[BUG] 2026-07-31: core.* imports)
##   - НИКАКОЙ бизнес-логики — только exec (валидация S-9, visudo -c, atomic mv — в setup_node.py)
## @rationale Языковая политика (root AGENTS.md): BUSINESS_LOGIC >100 LOC → Python. Содержимое
##            sudoers (сужение docker/rsync NOPASSWD, T10.1) перенесено 1:1 в render_sudoers().
##            `exec` сохраняет exit-код процесса (0/1) без обёртки.
## @changes 2026-08-14 | DevPlan 164 W3.5-1 — 135 LOC shell generate_sudoers → фасад (setup_node.py создан)
## @links   core/internal/bootstrap/setup_node.py (generate_sudoers), tests/unit/test_setup_node.py,
##          tests/gates/test_gate_sudoers_hardening.py (гейт парсит render_sudoers)
# endregion MODULE_CONTRACT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · ModuleNotFoundError: 'core' при script-path exec (канон
# · прежнего converge.sh) — фасад, запускающий Python с core.* импортами, ОБЯЗАН экспортировать
# · PYTHONPATH="${ROOT}:${PYTHONPATH:-}".
export PYTHONPATH="${CORE_DIR}/..:${PYTHONPATH:-}"

exec "${CONVERGE_PYTHON:-python3}" "${SCRIPT_DIR}/setup_node.py" "$@"
