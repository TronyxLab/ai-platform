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
## @rationale Language policy: inline python3 -c "import X" → require_python_module (this function)
# endregion MODULE_CONTRACT

# region FUNC_require_python_module
## @purpose  Check if a Python module is importable. Returns 0 if found, 1 if not.
## @param $1  module — Python module name to check
## @stdout   [IMP:7] message on success
## @stderr   [IMP:10] error message on failure
## @complexity O(1) — single python3 import test
require_python_module() {
    local module="$1"
    if python3 -c "import ${module}" 2>/dev/null; then
        return 0
    fi
    echo "[IMP:10][deps] FATAL: Python module '${module}' not installed" >&2
    return 1
}
# endregion FUNC_require_python_module
