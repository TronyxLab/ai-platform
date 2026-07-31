#!/usr/bin/env bash
# GREP_SUMMARY: python_deps.sh, require_python_module, python3, import-check
# STRUCTURE: ▶ require_python_module()
# region MODULE_CONTRACT
## @purpose  Shell library for Python dependency checks — single require_python_module() function
## @scope    Used by scaffold/ and validate/ scripts to replace inline `python3 -c "import X"` patterns
## @invariants
##   - Checks if a Python module is importable via python3
##   - Exits with fatal error (exit 1) if module not found
##   - Prints [IMP:7] on success, [IMP:10] on failure
## @rationale Language policy: inline python3 -c "import X" → require_python_module (this function).
##            This file itself uses `python3 -c "import ${module}"` (line 22) as a SANCTIONED
##            EXCEPTION — availability-check of a library function, not business logic. Tier 1
##            Strangler-trigger does not apply: there is no logic to extract to a .py module.
##            Replacing `import` with `importlib.util.find_spec` would still be a `python3 -c`
##            subprocess call and adds no value. Classification: LEGITIMATE (VR 038 §149).
# endregion MODULE_CONTRACT

# region FUNC_require_python_module
## @purpose  Check if a Python module is importable. Returns 0 if found, 1 if not.
## @param $1  module — Python module name to check
## @stdout   [IMP:7] message on success
## @stderr   [IMP:10] error message on failure
## @complexity O(1) — single python3 import test
require_python_module() {
    local module="$1"
    # ⚠️ TRAP[DECISION] · 2026-07-31 · LOW · python3 -c "import" — sanctioned availability-check
    # · Rejected: extract to .py module (risk: no business logic to extract — Tier 1 N/A)
    # · Reason: require_python_module() IS the sanctioned replacement for inline python3 -c in
    #   callers; its own internal import-check is the irreducible primitive. VR 038 §149: LEGITIMATE.
    # · Rev: если require_python_module() начнёт содержать >1 логическую ветку → Strangler-Fig.
    if python3 -c "import ${module}" 2>/dev/null; then
        return 0
    fi
    echo "[IMP:10][deps] FATAL: Python module '${module}' not installed" >&2
    return 1
}
# endregion FUNC_require_python_module
