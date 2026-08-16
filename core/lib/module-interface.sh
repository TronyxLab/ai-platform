#!/usr/bin/env bash
# GREP_SUMMARY: module-interface, invoke-module-interface, typed-contract, cross-layer, dispatch, thin-facade
# STRUCTURE: ┌module + interface + args┐ → ◇ python3 -m core.internal.shared.module_interface invoke "$@" → ⎋ exit 0|1|2
# region MODULE_CONTRACT
## @purpose  Тонкий фасад cross-layer вызова модулей (DevPlan 119 D4): ВСЯ логика
##           (validate module.yaml#interfaces → dispatch) — в shared/module_interface.py.
##           Dual-SoT устранён (118 C5 → 119 D4, AUDIT-1 F6). Shell не дублирует Python-канон.
## @invariants  invoke_module_interface() → python3 -m ... invoke "$@" (exit 0=skip/success, 1=fail, 2=invalid);
##              PYTHONPATH-экспорт при source (паттерн audit.sh); не source paths.sh/yaml_read.sh (circular deps)
## @changes  2026-07-18 · Created (T2) | 2026-08-02 · D4 — 206→26 LOC, validate/dispatch → Python dispatch()
# endregion MODULE_CONTRACT
set -euo pipefail

_IM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# PYTHONPATH-init по паттерну core/lib/audit.sh: репо-рут для python3 -m core.* (add-vhost.sh:33 канон)
export PYTHONPATH="${_IM_DIR}/../..:${PYTHONPATH:-}"

echo "[IMP:7][module-interface][lib] Loading module interface library (thin facade)" >&2

# region INVOKE_MODULE_INTERFACE
## @purpose  Делегирует dispatch в shared/module_interface.py (D4) — единый канон.
## @io       $1=module, $2=interface, $@...=args → ⎋ exit 0|1|2 (passthrough от Python dispatch)
invoke_module_interface() {
    python3 -m core.internal.shared.module_interface invoke "$@"
}
# endregion INVOKE_MODULE_INTERFACE
