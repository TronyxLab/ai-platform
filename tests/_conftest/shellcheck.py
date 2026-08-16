# GREP_SUMMARY: shellcheck, SC2154, data-flow, static-analysis, cross-layer, variable-tracking, bash-import-detection
# STRUCTURE: ▶ _check_shellcheck_available → ▶ _parse_shellcheck_sc2154 → ▶ get_shellcheck_bash_calls → ⊕ [(lineno, path)]
# region MODULE_CONTRACT
## @purpose  ShellCheck integration for cross-layer data-flow detection.
##           Uses ShellCheck SC2154 diagnostics to detect patterns where a variable
##           is assigned from a path literal and then used in a bash/sh/source command.
##           This catches cases that _trace_variable_assignment misses due to scope
##           boundaries (e.g., variable assigned in one function, used in another).
## @scope    Provides three public functions:
##           - _check_shellcheck_available() → (bool, version_msg)
##           - _parse_shellcheck_sc2154(file_path) → [var_names]
##           - get_shellcheck_bash_calls(file_path) → [(lineno, import_path)]
## @invariants
##   - Graceful degradation: all functions return empty/default if ShellCheck unavailable
##   - Minimum ShellCheck version: 0.9.0 (for structured JSON output)
##   - SC2154 diagnostic used as proxy for "variable came from outside current scope"
##   - No file modification — read-only analysis
## @rationale  ShellCheck provides data-flow analysis without writing a custom bash parser.
##             SC2154 ("variable is referenced but not assigned") is a pragmatic proxy —
##             it fires when a variable is used but not assigned in the current scope,
##             which matches our "variable-bearing path" detection pattern.
## @changes   2026-07-18 | NEW — DataFlow DevPlan Wave 2 (T2.1)
## @usecases  Integrated into scan_sh_file() as additional detection layer B
# endregion MODULE_CONTRACT

import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum ShellCheck version for structured JSON output
MIN_SHELLCHECK_VERSION = (0, 9, 0)


# ─── VERSION CHECK ────────────────────────────────────────────────────────


def _check_shellcheck_available() -> tuple[bool, str]:
    """Check if shellcheck is available and version >= MIN_SHELLCHECK_VERSION.

    Returns (available, version_string or error_message).
    Graceful on FileNotFoundError, TimeoutExpired, parse error.
    """
    # region FUNC_check_shellcheck_available
    ## @purpose  Verify ShellCheck binary presence and minimum version
    ## @io       None → (bool, str)
    ## @complexity O(1) — single subprocess call
    try:
        result = subprocess.run(
            ["shellcheck", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        version_str = _check_version_str(result)
        if version_str is None:
            return False, f"cannot parse version from: {result.stdout[:80]}"
        if not _version_meets_minimum(version_str):
            return False, f"version {version_str} < {'.'.join(map(str, MIN_SHELLCHECK_VERSION))}"

        logger.info("[IMP:8][shellcheck] ShellCheck %s available", version_str)
    except FileNotFoundError:
        return False, "shellcheck not found in PATH"
    except subprocess.TimeoutExpired:
        return False, "shellcheck --version timed out"
    except Exception as exc:  # ruff: ignore[BLE001] — best-effort graceful degradation
        return False, f"unexpected error: {exc}"
    else:
        return True, version_str
    # endregion FUNC_check_shellcheck_available


def _version_meets_minimum(version_str: str) -> bool:
    """X.Y.Z ≥ MIN_SHELLCHECK_VERSION? (PLW0717-хелпер)

    ## @io — ⇥ version_str → ⎋ bool
    ## @complexity O(1)
    """
    major, minor, patch = (int(x) for x in version_str.split("."))
    return (major, minor, patch) >= MIN_SHELLCHECK_VERSION


def _check_version_str(result: subprocess.CompletedProcess) -> str | None:
    """Валидная ли версия shellcheck (rc==0 + парсинг X.Y.Z) (PLW0717-хелпер).

    ## @io — ⇥ result → ⎋ str|None ("1.2.3" или None при rc!=0/нет матча)
    ## @complexity O(1)
    """
    if result.returncode != 0:
        return None
    return _parse_shellcheck_version(result.stdout)


def _parse_shellcheck_version(stdout: str) -> str | None:
    """Извлечь "X.Y.Z" из `shellcheck --version` stdout (PLW0717-хелпер).

    ## @io — ⇥ stdout: str → ⎋ str|None ("1.2.3" или None при отсутствии матча)
    ## @complexity O(1) — regex
    """
    m = re.search(r"version:\s*(\d+)\.(\d+)\.(\d+)", stdout)
    if not m:
        return None
    return f"{int(m.group(1))}.{int(m.group(2))}.{int(m.group(3))}"


# ─── SC2154 PARSING ───────────────────────────────────────────────────────


def _parse_shellcheck_sc2154(file_path: Path) -> list[str]:
    """Run shellcheck -f json and extract variable names from SC2154 warnings.

    SC2154 = "variable is referenced but not assigned" — ShellCheck detected
    a variable that is used but was never assigned in the current scope.
    This means the variable likely comes from an external source (source'd file,
    environment, or outer scope).

    Returns list of variable names that triggered SC2154.
    Empty list on any error (graceful degradation).
    """
    # region FUNC_parse_shellcheck_sc2154
    ## @purpose  Extract SC2154 variable names from ShellCheck JSON output
    ## @io       file_path → list[str]
    ## @complexity O(1) — single shellcheck run per file
    try:
        result = subprocess.run(
            ["shellcheck", "-f", "json", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if _is_shellcheck_error(result):
            # returncode 1 = warnings found (normal), >1 = error
            logger.warning(
                "[IMP:6][shellcheck] ShellCheck error on %s: %s",
                file_path,
                result.stderr[:200],
            )
            return []
        return _log_sc2154_vars(file_path, _extract_sc2154_vars(result.stdout))
    except json.JSONDecodeError:
        logger.warning("[IMP:6][shellcheck] Invalid JSON from shellcheck on %s", file_path)
        return []
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:6][shellcheck] Timeout running shellcheck on %s", file_path)
        return []
    except Exception as exc:  # ruff: ignore[BLE001] — best-effort graceful degradation
        logger.warning("[IMP:6][shellcheck] Error running shellcheck on %s: %s", file_path, exc)
        return []
    # endregion FUNC_parse_shellcheck_sc2154


def _log_sc2154_vars(file_path: Path, sc2154_vars: list[str]) -> list[str]:
    """Залогировать найденные SC2154-переменные и вернуть список (PLW0717-хелпер).

    ## @io — ⇥ file_path, sc2154_vars → ⎋ list[str] (те же переменные)
    ## @complexity O(1)
    """
    if sc2154_vars:
        logger.info(
            "[IMP:8][shellcheck] Found %d SC2154 variables in %s: %s",
            len(sc2154_vars),
            file_path,
            sc2154_vars,
        )
    return sc2154_vars


def _is_shellcheck_error(result: subprocess.CompletedProcess) -> bool:
    """True при фатальной ошибке shellcheck (rc>1; rc 0/1 — норма) (PLW0717-хелпер).

    ## @io — ⇥ result → ⎋ bool
    ## @complexity O(1)
    """
    return result.returncode not in {0, 1} and result.returncode > 1


def _extract_sc2154_vars(stdout: str) -> list[str]:
    """Извлечь имена переменных SC2154 из JSON-вывода shellcheck (PLW0717-хелпер).

    ## @io — ⇥ stdout: str (JSON diagnostics) → ⎋ list[str] имён переменных
    ## @complexity O(N) где N = diagnostics
    """
    diagnostics = json.loads(stdout) if stdout.strip() else []
    sc2154_vars: list[str] = []
    for diag in diagnostics:
        if diag.get("code") == 2154:
            # Extract variable name from message: "VAR is referenced but not assigned."
            message = diag.get("message", "")
            m = re.match(r"^(\w+)\s+is\s+referenced", message)
            if m:
                sc2154_vars.append(m.group(1))
    return sc2154_vars


# ─── MAIN DETECTION ───────────────────────────────────────────────────────


def get_shellcheck_bash_calls(file_path: Path) -> list[tuple[int, str]]:
    """Detect bash/sh/source calls where the argument is a variable assigned from a path literal.

    Approach:
    1. Run shellcheck to find SC2154 variables (used but not locally assigned)
    2. For each SC2154 variable, grep the file for its assignment (local/export/VAR=)
    3. If assignment value looks like a path, check if variable is used in bash/sh/source/. call
    4. Return (lineno, import_path) for each detected call

    Returns empty list if shellcheck not available (graceful degradation).
    """
    # region FUNC_get_shellcheck_bash_calls
    ## @purpose  Detect bash/sh/source calls with path-bearing variables via ShellCheck
    ## @io       file_path → list[tuple[int, str]]
    ## @complexity O(n) where n = file lines + 1 shellcheck subprocess call
    available, version_str = _check_shellcheck_available()
    if not available:
        logger.warning(
            "[IMP:7][shellcheck] ShellCheck unavailable: %s — skipping data-flow analysis",
            version_str,
        )
        return []

    logger.info("[IMP:8][shellcheck] ShellCheck %s available — analysing %s", version_str, file_path)

    # Step 1: get SC2154 variables
    sc2154_vars = _parse_shellcheck_sc2154(file_path)
    if not sc2154_vars:
        return []

    # Step 2: read file content
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
    except Exception:  # ruff: ignore[BLE001] — best-effort: read_text сбой → пустой список
        return []

    # Step 3: for each SC2154 variable, find its assignment
    var_assignments: dict[str, str] = {}
    for var_name in sc2154_vars:
        pattern = rf'(?:local\s+|export\s+|readonly\s+)?{re.escape(var_name)}=["\']?([^"\'\n]+)'
        matches = list(re.finditer(pattern, content))
        if matches:
            value = matches[-1].group(1).strip()
            # Resolve nested ${} using simple heuristic
            for nested in re.finditer(r"\$\{(\w+)\}", value):
                nested_name = nested.group(1)
                if nested_name in var_assignments:
                    value = value.replace(nested.group(0), var_assignments[nested_name])
            if "/" in value:
                var_assignments[var_name] = value

    if not var_assignments:
        logger.info(
            "[IMP:7][shellcheck] No path-bearing assignments found for SC2154 variables in %s",
            file_path,
        )
        return []

    # Step 4: find bash/sh/source/. calls using these variables
    results: list[tuple[int, str]] = []
    bash_pattern = re.compile(r"(?:^|\s)(?:bash|/bin/bash|sh|/bin/sh|source|\.)\s+(\S+)")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = bash_pattern.search(stripped)
        if m:
            arg = m.group(1)
            # Strip quotes
            arg_clean = arg.strip("'\"")
            # Check if arg is a known path-bearing variable
            if arg_clean.startswith("$"):
                var_name = arg_clean.lstrip("$").strip("{}")
                if var_name in var_assignments:
                    results.append((i, arg))
                    logger.info(
                        "[IMP:9][shellcheck] Detected bash call with path var '%s' at %s:%d → %s",
                        var_name,
                        file_path,
                        i,
                        arg,
                    )

    return results
    # endregion FUNC_get_shellcheck_bash_calls
