#!/usr/bin/env python3
# GREP_SUMMARY: cross-layer import linter, static-analysis, layer-isolation, entrypoints, internal, modules
# STRUCTURE: ▶ discover(core/**/*.{sh,py,Makefile}) → ○ classify each file's layer → ▶ scan imports → ▶ resolve target path → ◇ allowed by rule? → ⊕ collect violations → ⎋ assert 0 violations
# region MODULE_CONTRACT
## @purpose  Static-analysis test enforcing cross-layer import isolation rules
##           from core/AGENTS.md §Cross-layer import rules.
## @scope    Scans .sh, .py, and Makefile files under core/ for layer-boundary
##           crossings and reports violations.
## @invariants
##   - Only files in entrypoints/, internal/, modules/ are subject to rules
##   - lib/, bootstrap/, scripts/ files are NOT importing layers
##   - LINT-EXEMPT comment on offending line NO LONGER suppresses violations
##     (warns instead — TASK-6C Phase 6)
##   - /opt/ paths are filtered for entrypoints/ and internal/ but NOT for
##     modules/ (TASK-6C Phase 6: modules→/opt/ may be cross-layer violations)
##   - Every modules/*/Makefile must include ../../templates/module.mk or
##     ../../Makefile.common (Makefile contract — TASK-6C Phase 6)
##   - Zero violations → PASS; any violation → FAIL with file:line report
## @rationale  Physical enforcement of architectural invariants — prevents
##             layer-boundary violations from entering the codebase.
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ─── CONSTANTS ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = PROJECT_ROOT / "core"

# Layer classification: path prefixes to layer names
_LAYER_PREFIXES: dict[str, str] = {
    "entrypoints": "entrypoints/",
    "internal": "internal/",
    "modules": "modules/",
    "lib": "lib/",
    "templates": "templates/",
    "scripts": "scripts/",
    "bootstrap": "bootstrap/",
    "schemas": "schemas/",
}

# Import rules: source_layer -> set of allowed target layers
_IMPORT_RULES: dict[str, set[str]] = {
    "entrypoints": {"internal", "lib"},
    "internal": {"internal", "lib"},
    "modules": {"lib", "templates"},
}

# Only these source layers are subject to import rules
_IMPORTING_LAYERS: set[str] = {"entrypoints", "internal", "modules"}

# Allowed Makefile includes from modules/ (exact relative paths)
_MODULE_MAKEFILE_ALLOWED_INCLUDES: set[str] = {
    "../../templates/module.mk",
    "../../Makefile.common",
}

# Shell flags and commands that are NOT script imports
_NON_IMPORT_ARGS: set[str] = {
    "-c",
    "-s",
    "-i",
    "-l",
    "--login",
    "-r",
    "--restricted",
    "+o",
    "-o",
    "-n",
    "-x",
    "-e",
    "-u",
    "-p",
    "-v",
}

# Patterns that indicate a non-path argument (bare variable, flag, etc.)
_RE_NOT_A_PATH = re.compile(r'^[\s\$"\'@*]+$')


# ─── LAYER CLASSIFICATION ───────────────────────────────────────────────


def classify_layer(file_path: Path) -> str | None:
    """Determine which layer a file belongs to based on its path relative to core/.

    Returns layer name or None if outside any known layer.
    """
    path_str = file_path.as_posix()
    for layer, prefix in _LAYER_PREFIXES.items():
        if f"core/{prefix}" in path_str:
            return layer
    return None


# ─── PATH RESOLUTION ────────────────────────────────────────────────────


def _resolve_platform_root(source_file: Path, source_layer: str) -> Path:
    """Infer the PLATFORM_ROOT variable value based on source file location."""
    if source_layer == "entrypoints":
        return CORE_DIR
    if source_layer == "internal":
        path_str = source_file.as_posix()
        if "/internal/scaffold/" in path_str or "/internal/build/" in path_str:
            return PROJECT_ROOT
        return CORE_DIR
    return CORE_DIR


def _looks_like_path(text: str) -> bool:
    """Check if an import argument looks like a file path (not a bare variable or flag)."""
    t = text.strip().strip("'\"")
    # Must contain a directory separator or a ${...} variable reference with a path
    has_separator = "/" in t
    has_var_prefix = t.startswith("${") and "/" in t
    has_relative = t.startswith("..")
    has_absolute = t.startswith("/") and t != "/"
    return has_separator or has_var_prefix or has_relative or has_absolute


def resolve_import(source_file: Path, import_path: str, source_layer: str) -> Path | None:
    """Resolve an import path to an absolute target path.

    Returns None if the path cannot be resolved (non-path reference).
    """
    if not _looks_like_path(import_path):
        return None

    resolved = import_path.strip()

    # Replace known variable references
    if "${_EP_DIR}" in resolved:
        resolved = resolved.replace("${_EP_DIR}", str(source_file.parent))
    if "${SCRIPT_DIR}" in resolved:
        resolved = resolved.replace("${SCRIPT_DIR}", str(source_file.parent))
    if "${MODULE_DIR}" in resolved:
        resolved = resolved.replace("${MODULE_DIR}", str(source_file.parent))
    if "${_HEALTHCHECK_LIB_DIR}" in resolved:
        resolved = resolved.replace("${_HEALTHCHECK_LIB_DIR}", str(CORE_DIR / "lib"))
    if "${_TIMING_LIB_DIR}" in resolved:
        resolved = resolved.replace("${_TIMING_LIB_DIR}", str(CORE_DIR / "lib"))
    if "${_NODE_RESOLVER_LIB_DIR}" in resolved:
        resolved = resolved.replace("${_NODE_RESOLVER_LIB_DIR}", str(CORE_DIR / "lib"))
    if "${CORE_DIR}" in resolved:
        resolved = resolved.replace("${CORE_DIR}", str(CORE_DIR))
    if "${PATHS_INTERNAL_DIR}" in resolved:
        resolved = resolved.replace("${PATHS_INTERNAL_DIR}", str(CORE_DIR / "internal"))
    if "${PLATFORM_ROOT}" in resolved:
        platform_root = _resolve_platform_root(source_file, source_layer)
        resolved = resolved.replace("${PLATFORM_ROOT}", str(platform_root))

    # Simple $VAR patterns
    if "$_EP_DIR" in resolved:
        resolved = resolved.replace("$_EP_DIR", str(source_file.parent), 1)
    if "$SCRIPT_DIR" in resolved:
        resolved = resolved.replace("$SCRIPT_DIR", str(source_file.parent), 1)
    if "$MODULE_DIR" in resolved:
        resolved = resolved.replace("$MODULE_DIR", str(source_file.parent), 1)
    if "$PATHS_INTERNAL_DIR" in resolved:
        resolved = resolved.replace("$PATHS_INTERNAL_DIR", str(CORE_DIR / "internal"), 1)

    # Strip quotes
    resolved = resolved.replace('"', "").replace("'", "")

    # Strip leading ./
    if resolved.startswith("./"):
        resolved = resolved[2:]

    # If the result is just a bare name (no /), it's probably a command, not a path
    if "/" not in resolved:
        return None

    # If the result doesn't start with /, ../, or a variable prefix, skip
    if not resolved.startswith("/") and not resolved.startswith("..") and not resolved.startswith("${"):
        return None

    # Resolve relative paths
    result = Path(resolved)
    if result.is_absolute():
        final = result.resolve()
    else:
        final = (source_file.parent / result).resolve()

    # Must be within the core/ tree to be a cross-layer import
    if not str(final).startswith(str(CORE_DIR)):
        return None

    return final


def layer_of_target(resolved_path: Path) -> str | None:
    """Determine which layer a resolved target path belongs to."""
    path_str = resolved_path.as_posix()
    for layer, prefix in _LAYER_PREFIXES.items():
        if f"core/{prefix}" in path_str:
            return layer
    return None


# ─── SCANNERS ───────────────────────────────────────────────────────────


def _has_lint_exempt(lines: list[str], lineno: int) -> bool:
    """Check if the given or preceding line has a LINT-EXEMPT comment."""
    for check in (lineno - 1, lineno - 2):
        if 0 <= check < len(lines):
            line = lines[check].strip()
            if "# LINT-EXEMPT:" in line:
                return True
    return False


def scan_sh_file(file_path: Path, source_layer: str | None = None) -> list[tuple[int, str, bool]]:
    """Scan a .sh file for sourcing and direct script invocations.

    Returns list of (lineno, import_path, has_exempt).

    For modules/ layer, /opt/ paths are NOT filtered (they may be cross-layer violations).
    For entrypoints/ and internal/ layers, /opt/ and /etc/ paths are filtered (they are
    legitimate production path references, not script imports).
    """
    imports: list[tuple[int, str, bool]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][scan][sh] Cannot read %s: %s", file_path, exc)
        return imports

    lines = content.split("\n")
    skip_opt_filter = source_layer == "modules"  # modules: let /opt/ paths through for cross-layer check

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        exempt = _has_lint_exempt(lines, i)

        # Pattern 1: source <path>
        m = re.search(r"(?:^|\s)(?:source)\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if not skip_opt_filter and (path.startswith(("/etc/", "/opt/"))):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        # Pattern 2: . <path>  (shorthand for source)
        m = re.search(r"(?:^|\s)\.\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if path in ('"$@"', "${@}", "$@", '".",'):
                continue
            if not skip_opt_filter and (path.startswith(("/etc/", "/opt/"))):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        # Pattern 3: exec <path> — skip docker exec, ssh exec, etc.
        # Only match when exec is at a command boundary
        m = re.search(r"(?:^|;|&&|\|\|)\s*exec\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if path in (">", ">>", "<", "2>", "2>>", ";"):
                continue
            if path.startswith((">", "<")):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        # Pattern 4: bash/sh <path> (script invocation)
        m = re.search(r"(?:^|\s)(?:bash|/bin/bash|sh|/bin/sh)\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if path in _NON_IMPORT_ARGS:
                continue
            if path.startswith("-"):
                continue
            if not skip_opt_filter and (path.startswith(("/etc/", "/opt/"))):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

    return imports


def scan_py_file(file_path: Path) -> list[tuple[int, str, bool]]:
    """Scan a .py file for import statements.

    Returns list of (lineno, import_path, has_exempt).
    Only local-project imports are relevant — stdlib/third-party modules
    are filtered by resolve_import.
    """
    imports: list[tuple[int, str, bool]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][scan][py] Cannot read %s: %s", file_path, exc)
        return imports

    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        exempt = _has_lint_exempt(lines, i)

        # from <module> import ...
        m = re.search(r"^from\s+(\S+)\s+import", stripped)
        if m:
            imports.append((i, m.group(1), exempt))
            continue

        # import <module>
        m = re.search(r"^import\s+(\S+)", stripped)
        if m:
            mod = m.group(1)
            mod = mod.split(",")[0] if "," in mod else mod
            imports.append((i, mod, exempt))
            continue

    return imports


def scan_makefile(file_path: Path) -> list[tuple[int, str, bool]]:
    """Scan a Makefile for include statements.

    Returns list of (lineno, include_path, has_exempt).
    """
    imports: list[tuple[int, str, bool]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][scan][make] Cannot read %s: %s", file_path, exc)
        return imports

    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        exempt = _has_lint_exempt(lines, i)

        m = re.search(r"^include\s+(\S+)", stripped)
        if m:
            imports.append((i, m.group(1), exempt))
            continue

    return imports


# ─── VIOLATION CHECK ─────────────────────────────────────────────────────


def check_violation(
    source_file: Path,
    lineno: int,
    import_path: str,
    import_type: str,
    exempt: bool,
    resolved: Path | None = None,
) -> str | None:
    """Check if an import violates cross-layer rules.

    Returns formatted violation string, or None if allowed.
    LINT-EXEMPT no longer suppresses violations (TASK-6C); a warning is logged
    if LINT-EXEMPT is found on a violation line.
    """
    if exempt:
        logger.warning(
            "[IMP:7][lint][LINT-EXEMPT] %s:%d — LINT-EXEMPT present but "
            "no longer suppresses violations (TASK-6C). Remove the LINT-EXEMPT comment.",
            source_file,
            lineno,
        )

    source_layer = classify_layer(source_file)
    if source_layer is None or source_layer not in _IMPORTING_LAYERS:
        return None

    # ── Makefile special rules ──
    if import_type == "make":
        if source_layer == "modules":
            if import_path in _MODULE_MAKEFILE_ALLOWED_INCLUDES:
                return None
            return (
                f"  {source_file}:{lineno} — [modules·make] "
                f"include '{import_path}' — only ../../templates/module.mk "
                f"or ../../Makefile.common allowed"
            )
        return None

    # ── Python imports ──
    if import_type == "py":
        if resolved is None:
            return None
        target_layer = layer_of_target(resolved)
        if target_layer is None:
            return None
        allowed = _IMPORT_RULES.get(source_layer, set())
        if target_layer in allowed:
            return None
        return f"  {source_file}:{lineno} — [{source_layer}→{target_layer}] import '{import_path}' (forbidden)"

    # ── Shell imports → resolved already filtered to core/ paths with layers ──
    if resolved is None:
        return None
    target_layer = layer_of_target(resolved)
    if target_layer is None:
        return None
    allowed = _IMPORT_RULES.get(source_layer, set())
    if target_layer in allowed:
        return None
    return f"  {source_file}:{lineno} — [{source_layer}→{target_layer}] '{import_path}' (forbidden)"


# ─── MAIN LINT LOGIC ─────────────────────────────────────────────────────


def lint_core() -> list[str]:
    """Run the cross-layer import linter across all files in core/.

    Returns sorted list of violation strings.
    """
    violations: list[str] = []

    sh_files = sorted(CORE_DIR.rglob("*.sh"))
    py_files = sorted(CORE_DIR.rglob("*.py"))
    make_files = sorted(CORE_DIR.rglob("Makefile"))

    for fpath in sh_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        imports = scan_sh_file(fpath, source_layer)
        for lineno, imp_path, exempt in imports:
            resolved = resolve_import(fpath, imp_path, source_layer)
            msg = check_violation(fpath, lineno, imp_path, "sh", exempt, resolved)
            if msg:
                violations.append(msg)

    for fpath in py_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        imports = scan_py_file(fpath)
        for lineno, imp_path, exempt in imports:
            resolved = resolve_import(fpath, imp_path, source_layer)
            msg = check_violation(fpath, lineno, imp_path, "py", exempt, resolved)
            if msg:
                violations.append(msg)

    for fpath in make_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        imports = scan_makefile(fpath)
        for lineno, imp_path, exempt in imports:
            msg = check_violation(fpath, lineno, imp_path, "make", exempt)
            if msg:
                violations.append(msg)

    # ── Makefile Contract Check: every modules/*/Makefile must include module.mk ──
    modules_makefiles = sorted((CORE_DIR / "modules").rglob("Makefile"))
    read_errors: list[Path] = []
    for mf in modules_makefiles:
        try:
            content = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            logger.error("[IMP:7][lint_core] Failed to read Makefile: %s", mf, exc_info=True)
            read_errors.append(mf)
            continue
        has_allowed_include = any(allowed in content for allowed in _MODULE_MAKEFILE_ALLOWED_INCLUDES)
        if not has_allowed_include:
            violations.append(
                f"  {mf} — [modules·makefile-contract] "
                f"Missing include of {_MODULE_MAKEFILE_ALLOWED_INCLUDES} — "
                f"every module Makefile must include templates/module.mk"
            )

    if read_errors:
        pytest.fail(f"Failed to read {len(read_errors)} Makefile(s): {read_errors}")

    return sorted(violations)


# ─── TEST ────────────────────────────────────────────────────────────────
# region TEST_test_cross_layer_imports


@pytest.mark.gate
@ldd_trajectory
def test_cross_layer_imports(caplog) -> None:
    """Enforce layer isolation: zero cross-layer import violations in core/.

    Failure means real violations exist. Fix them by moving the import to an
    allowed target layer, or add `# LINT-EXEMPT: <reason>` to the offending line.
    """
    # region FUNC_test_cross_layer_imports
    ## @purpose  Enforce zero cross-layer import violations in core/
    ## @io       None → assertion
    ## @complexity O(n) where n = number of source files under core/

    violations = lint_core()

    print("\n" + "=" * 70)
    print("  CROSS-LAYER IMPORT LINTER REPORT")
    print("=" * 70)

    if not violations:
        print("  ✅ 0 violations — all imports respect layer isolation rules\n")
        logger.info("[IMP:9][lint][result] PASS — 0 cross-layer import violations")
    else:
        print(f"  ❌ {len(violations)} cross-layer import violation(s) found:\n")
        for v in violations:
            print(v)
        print("\n" + "-" * 70)
        print("  To fix: move imports to allowed layers or add")
        print("  `# LINT-EXEMPT: <reason>` to the offending line.")
        logger.info(f"[IMP:9][lint][result] FAIL — {len(violations)} violation(s)")
        print("=" * 70 + "\n")

    assert len(violations) == 0, f"Cross-layer import violations found ({len(violations)}):\n" + "\n".join(violations)


# endregion FUNC_test_cross_layer_imports
# endregion TEST_test_cross_layer_imports
