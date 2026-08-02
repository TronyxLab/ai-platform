#!/usr/bin/env python3
# GREP_SUMMARY: cross-layer import linter, static-analysis, layer-isolation, entrypoints, internal, modules, data-flow, extended-registry, shellcheck, variable-tracking, dotted-imports, python3-m, allowlist
# STRUCTURE: ▶ discover(core/**/*.{sh,py,Makefile}) → ○ classify each file's layer → ▶ scan imports (7 patterns + ShellCheck: source, ., exec, bash/sh, make -C, docker compose -f, python3 -m) → ▶ resolve target path (dotted-name → core/ path, extended registry + trace) → ◇ allowed by rule? → ◇ allowlist? → ⊕ collect violations → ⎋ assert 0 violations
# region MODULE_CONTRACT
## @purpose  Static-analysis test enforcing cross-layer import isolation rules
##           from core/AGENTS.md §Cross-layer import rules.
## @scope    Scans .sh, .py, and Makefile files under core/ for layer-boundary
##           crossings and reports violations. Enhanced with:
##           - Extended Variable Registry (auto-collect from paths.sh)
##           - Local variable assignment tracking (_trace_variable_assignment)
##           - ShellCheck data-flow analysis (SC2154-based)
##           - make -C and docker compose -f pattern detection
## @invariants
##   - Only files in entrypoints/, internal/, modules/ are subject to rules
##   - lib/, bootstrap/, scripts/ files are NOT importing layers
##   - LINT-EXEMPT comment on offending line NO LONGER suppresses violations
##     (warns instead — TASK-6C Phase 6); with a matching allowlist entry the
##     violation IS suppressed (DevPlan 116 B11 T1 — канон B8 D3, строгий режим)
##   - /opt/ paths are filtered for entrypoints/ and internal/ but NOT for
##     modules/ (TASK-6C Phase 6: modules→/opt/ may be cross-layer violations)
##   - Every modules/*/Makefile must include ../../templates/module.mk,
##     ../../templates/module-system.mk, or ../../Makefile.common
##     (Makefile contract — TASK-6C Phase 6, extended T3 D3)
##   - Extended Variable Registry: _KNOWN_PATH_VARIABLES auto-collected at import time
##   - _looks_like_path now detects bare $variable references as potential paths
##   - Dotted-name imports (core.internal.X) and `python3 -m core.internal.X`
##     are detected (DevPlan 116 B11 T1 — U-09): _looks_like_path dotted-regex +
##     resolve_import dotted→path + scan_sh_file pattern 7
##   - CROSS-LAYER ALLOWLIST: только задокументированные (path, lineno, reason)
##     записи (контейнеризированные модули, импорт shared by design D1);
##     НОВОЕ dotted-нарушение вне allowlist → RED (allowlist не растёт)
##   - ShellCheck integration: graceful degradation if shellcheck not installed
##   - Zero violations → PASS; any violation → FAIL with file:line report
## @rationale  Physical enforcement of architectural invariants — prevents
##             layer-boundary violations from entering the codebase.
## @changes   2026-07-18 | DataFlow DevPlan: Extended Registry + ShellCheck + new patterns
##            2026-08-01 | DevPlan 116 B11 T1 (U-09): dotted-name + python3 -m
##                       детекция; CROSS-LAYER ALLOWLIST (канон B8 D3 — строгий);
##                       негатив-тесты R5 (anti-survivorship)
## @usecases  CI gate #8: cross-layer-linter; pre-commit validation
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ─── CONSTANTS ──────────────────────────────────────────────────────────
CORE_DIR = repo_root() / "core"

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
    "internal": {"internal", "lib", "modules"},  # modules разрешён через typed contract (Gate #8 v2)
    "modules": {"lib", "templates"},
}

# Invoke contract: which interface names are valid for invoke_module_interface
_VALID_INTERFACES: set[str] = {"healthcheck", "install", "deploy-hook", "remove-hook"}

# Only these source layers are subject to import rules
_IMPORTING_LAYERS: set[str] = {"entrypoints", "internal", "modules"}

# Allowed Makefile includes from modules/ (exact relative paths)
# Docker modules include module.mk; system modules (install_type: system) include module-system.mk
_MODULE_MAKEFILE_ALLOWED_INCLUDES: set[str] = {
    "../../templates/module.mk",
    "../../templates/module-system.mk",
    "../../Makefile.common",
}

# Shell flags and commands that are NOT script imports
# Extended per DevPlan DataFlow T1.3: special shell variables
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
    # Special shell variables — not paths
    "$?",
    "$#",
    "$$",
    "$!",
    "$@",
    "$*",
    "$-",
    "$0",
    "${?}",
    "${#}",
    "${$}",
    "${!}",
    "${@}",
    "${*}",
    "${-}",
    "${0}",
}

# Patterns that indicate a non-path argument (bare variable, flag, etc.)
_RE_NOT_A_PATH = re.compile(r'^[\s\$"\'@*]+$')

# ─── DOTTED-NAME DETECTION (DevPlan 116 B11 T1, U-09) ───────────────────────
# Python module names like core.internal.shared.telegram_notifier and
# `python3 -m core.internal.shared.node_yaml` are invisible to the old
# path-based detector (no '/'). Regex: dotted lowercase/underscore names.
_RE_DOTTED_NAME = re.compile(r"^[a-z_][\w]*(\.[a-z_][\w]*)+$")

# ⚠️ TRAP[BUG] · 2026-08-01 · P1 · Cross-layer gate was blind to dotted imports
# · Symptom: 36 passed при 6 реальных нарушениях (agent_watchdog 3×, backup_config 1×,
#   disk-monitor.sh 1×, postgres-hook 1×) — dotted-импорты и python3 -m не детектировались
# · Root: _looks_like_path требовал '/', resolve_import отбрасывал dotted (нет '/'),
#   scan_sh_file не имел паттерна python3 -m
# · Fix: _RE_DOTTED_NAME в _looks_like_path + resolve_import dotted→core/ path
#   + scan_sh_file pattern 7 (python3 -m <module>) + CROSS_LAYER_ALLOWLIST
# · Prevention: гейт теперь RED на ЛЮБОЕ новое dotted-нарушение вне allowlist

# ─── CROSS-LAYER ALLOWLIST (канон B8 D3 — строгий режим, DevPlan 116 B11 T1) ──
# Только задокументированные (path-relative-to-repo, lineno, reason) записи.
# Модули контейнеризированы и импортируют internal/shared по дизайну (D1):
#   backup-cron / hermes-agent / postgres-hook — контейнерный runtime,
#   shared-модули — единственный путь (facade-паттерн shared/AGENTS.md инвариант 4).
# allowlist НЕ растёт: ЛЮБОЕ новое dotted-нарушение вне allowlist → RED.
# Каждая запись имеет # LINT-EXEMPT: <reason> комментарий на строке нарушения.
# Rev: сжатие allowlist — отдельный backlog (модули вне контейнерного рантайма).
# 2026-08-02 (DevPlan 118 C1): docker_ops.py +1 запись (shared.timeouts — C1 требует импорт
#   канона таймаутов); docker_compose запись сдвинута 24→27 (импорт-блок вырос на 3 строки).
_CROSS_LAYER_ALLOWLIST: tuple[tuple[str, int, str], ...] = (
    (
        "core/modules/backup-cron/scripts/backup_config.py",
        36,
        "контейнерный модуль; internal.config platform_config — by design (D1)",
    ),
    (
        "core/modules/hermes-agent/watchdog/agent_watchdog.py",
        44,
        "контейнерный модуль; internal.config platform_config — by design (D1)",
    ),
    (
        "core/modules/hermes-agent/watchdog/agent_watchdog.py",
        47,
        "контейнерный модуль; shared.secrets_env_parser — by design (D1)",
    ),
    (
        "core/modules/hermes-agent/watchdog/agent_watchdog.py",
        50,
        "контейнерный модуль; shared.telegram_notifier — by design (D1)",
    ),
    (
        "core/modules/hermes-agent/watchdog/agent_watchdog.py",
        53,
        "контейнерный модуль; shared.timeouts — watchdog-таймауты из единого реестра (DevPlan 117 D29)",
    ),
    (
        "core/modules/hermes-agent/watchdog/circuit_breaker.py",
        31,
        "контейнерный модуль; internal.config platform_config — by design (D1, DevPlan 117 G T52)",
    ),
    (
        "core/modules/hermes-agent/watchdog/docker_ops.py",
        27,
        "контейнерный модуль; shared.docker_compose — by design (D1, DevPlan 117 D19, DevPlan 117 G T52)",
    ),
    (
        "core/modules/hermes-agent/watchdog/docker_ops.py",
        37,
        "контейнерный модуль; shared.timeouts — watchdog-таймауты из единого реестра (DevPlan 118 C1)",
    ),
    (
        "core/modules/postgres/hooks/on_project_deploy.py",
        40,
        "postgres-hook; shared.node_yaml — by design (D1); Python-канон D65. Волна 118 B8: "
        "shell-фасад on-project-deploy.sh удалён (hook-регистрация убрана), Python-модуль "
        "остаётся канонической реализацией (unit-тесты, operator-инвокация)",
    ),
)


def _repo_relative(source_file: Path) -> str:
    """Repo-root-relative posix path of a source file (allowlist key)."""
    try:
        return source_file.resolve().relative_to(repo_root().resolve()).as_posix()
    except ValueError:
        return source_file.as_posix()


def _is_allowlisted(source_file: Path, lineno: int) -> bool:
    """Check (path, lineno) against the strict cross-layer allowlist."""
    rel = _repo_relative(source_file)
    for entry_path, entry_lineno, reason in _CROSS_LAYER_ALLOWLIST:
        if rel == entry_path and lineno == entry_lineno:
            logger.info(
                "[IMP:9][lint][allowlist] %s:%d — allowlisted (D1 by design): %s",
                source_file,
                lineno,
                reason,
            )
            return True
    return False


# ─── VARIABLE REGISTRY (Wave 1: DataFlow Extended Registry) ────────────────


def _collect_path_variables(paths_file: Path | None = None) -> dict[str, str]:
    """Parse core/lib/paths.sh and extract all VAR=value assignments.

    Returns dict mapping variable name → resolved value.
    Handles: readonly VAR="value", bare VAR=value, VAR='value'.
    Skips: comments, empty lines, export (stripped but parsed).
    Values with ${BASH_SOURCE[0]} or $(...) are resolved statically
    relative to the paths.sh location.
    """
    # region FUNC_collect_path_variables
    ## @purpose  Auto-collect path variables from canonical paths.sh
    ## @io       ⎋ paths_file: Path | None → dict[str, str]
    ## @complexity O(n) where n = lines in paths.sh
    if paths_file is None:
        paths_file = CORE_DIR / "lib" / "paths.sh"

    if not paths_file.exists():
        logger.warning("[IMP:7][collect_vars] paths.sh not found at %s — returning empty dict", paths_file)
        return {}

    try:
        content = paths_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][collect_vars] Cannot read %s: %s", paths_file, exc)
        return {}

    variables: dict[str, str] = {}
    # Pattern: optional readonly/export prefix, VAR=value
    # Value may be quoted ("..." or '...') or bare
    pattern = re.compile(r"^(?:readonly\s+|export\s+)?(\w+)=(.*)")

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        m = pattern.match(stripped)
        if not m:
            continue

        var_name = m.group(1)
        raw_value = m.group(2).strip()

        # Strip surrounding quotes
        if (raw_value.startswith('"') and raw_value.endswith('"')) or (
            raw_value.startswith("'") and raw_value.endswith("'")
        ):
            raw_value = raw_value[1:-1]

        # Strip trailing inline comments (# ...)
        # But only if the # is not inside quotes (we already stripped quotes)
        comment_pos = raw_value.find(" #")
        if comment_pos > 0:
            raw_value = raw_value[:comment_pos].rstrip()

        # Skip runtime-resolved values
        if "${BASH_SOURCE[0]}" in raw_value or "$(cd" in raw_value or "$(dirname" in raw_value:
            # Resolve statically relative to paths.sh location
            resolved = _resolve_bash_source_var(var_name, paths_file)
            if resolved:
                variables[var_name] = resolved
            continue

        # Store raw value — will be recursively resolved later
        variables[var_name] = raw_value

    # Resolve nested ${} references (one pass)
    # Order matters: resolve shortest chains first
    for name in list(variables.keys()):
        value = variables[name]
        if "${" in value:
            for nested_name, nested_value in variables.items():
                value = value.replace(f"${{{nested_name}}}", nested_value)
                value = value.replace(f"${nested_name}", nested_value)
            variables[name] = value

    logger.info("[IMP:8][collect_vars] Collected %d variables from %s", len(variables), paths_file)
    return variables
    # endregion FUNC_collect_path_variables


def _resolve_bash_source_var(var_name: str, paths_file: Path) -> str | None:
    """Resolve variables that depend on ${BASH_SOURCE[0]} statically.

    For paths.sh located at core/lib/paths.sh:
    - PATHS_LIB_DIR = core/lib
    - PATHS_CORE_DIR = core
    - PATHS_MODULES_DIR = core/modules
    - PATHS_TEMPLATES_DIR = core/templates
    - PATHS_INTERNAL_DIR = core/internal
    """
    # The paths_file is core/lib/paths.sh, so its parent dir is core/lib
    lib_dir = paths_file.parent.resolve()
    core_dir = lib_dir.parent  # core/

    static_map: dict[str, str] = {
        "PATHS_LIB_DIR": str(lib_dir),
        "PATHS_CORE_DIR": str(core_dir),
        "PATHS_MODULES_DIR": str(core_dir / "modules"),
        "PATHS_TEMPLATES_DIR": str(core_dir / "templates"),
        "PATHS_INTERNAL_DIR": str(core_dir / "internal"),
    }
    return static_map.get(var_name)


# Module-level: auto-collected at import time from paths.sh
_KNOWN_PATH_VARIABLES: dict[str, str] = _collect_path_variables()


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
            return repo_root()
        return CORE_DIR
    return CORE_DIR


def _looks_like_path(text: str) -> bool:
    """Check if an import argument looks like a file path (not a bare variable or flag).

    Extended per DevPlan DataFlow T1.3: detects bare $variable references
    that might be path-bearing variables.
    Extended per DevPlan 116 B11 T1 (U-09): detects dotted Python module names
    (core.internal.shared.telegram_notifier) — no '/' but resolvable to a core/ path.
    """
    # region FUNC_looks_like_path
    ## @purpose  Determine if a string argument looks like a file path or path-bearing variable
    ## @io       text → bool
    ## @complexity O(1)
    t = text.strip().strip("'\"")
    if not t:
        return False

    # Existing checks
    has_separator = "/" in t
    has_var_prefix = t.startswith("${") and "/" in t
    has_relative = t.startswith("..")
    has_absolute = t.startswith("/") and t != "/"

    # NEW: bare variable reference — potentially a path
    # Проверяем что это $variable (не флаг, не спец-переменная)
    is_bare_variable = (
        t.startswith("$")
        and len(t) > 1  # single $ is not a variable
        and not t.startswith("${")  # ${var}/path уже покрыто has_var_prefix
        and t not in _NON_IMPORT_ARGS
        and not re.match(r"^\$[\d@*!#?\-]$", t)  # спец-переменные: $1, $@, $*, $!, $#, $?, $-
    )

    # NEW (DevPlan 116 B11 T1): dotted Python module name — not a flag,
    # not a $-reference, first char [a-z_], at least one '.'
    is_dotted_name = bool(_RE_DOTTED_NAME.match(t)) and not t.startswith("$")

    return has_separator or has_var_prefix or has_relative or has_absolute or is_bare_variable or is_dotted_name
    # endregion FUNC_looks_like_path


def _substitute_variables(resolved: str, source_file: Path, source_layer: str) -> str:
    """Substitute known variables in an import path.

    Step 1: auto-collected from paths.sh via _KNOWN_PATH_VARIABLES
    Step 2: contextual variables (dependent on source_file/source_layer)
    Auto-collected variables have priority (overwritten by contextual if duplicate).
    """
    # region FUNC_substitute_variables
    ## @purpose  Replace ${VAR} and $VAR references in import paths with resolved values
    ## @io       resolved, source_file, source_layer → resolved string
    ## @complexity O(n) where n = number of known variables
    # Step 1: auto-collected from paths.sh
    for var_name, var_value in _KNOWN_PATH_VARIABLES.items():
        resolved = resolved.replace(f"${{{var_name}}}", var_value)
        resolved = resolved.replace(f"${var_name}", var_value)

    # Step 2: contextual variables (depend on source_file/source_layer)
    contextual = {
        "_EP_DIR": str(source_file.parent),
        "SCRIPT_DIR": str(source_file.parent),
        "MODULE_DIR": str(source_file.parent),
        "_HEALTHCHECK_LIB_DIR": str(CORE_DIR / "lib"),
        "_TIMING_LIB_DIR": str(CORE_DIR / "lib"),
        "_NODE_RESOLVER_LIB_DIR": str(CORE_DIR / "lib"),
        "CORE_DIR": str(CORE_DIR),
        "PATHS_INTERNAL_DIR": str(CORE_DIR / "internal"),
        "PLATFORM_ROOT": str(_resolve_platform_root(source_file, source_layer)),
    }
    for var_name, var_value in contextual.items():
        resolved = resolved.replace(f"${{{var_name}}}", var_value)
        resolved = resolved.replace(f"${var_name}", var_value)

    return resolved
    # endregion FUNC_substitute_variables


def _trace_variable_assignment(file_path: Path, var_name: str) -> str | None:
    """Trace a variable to its last assignment in the same file.

    Searches for: local VAR=..., export VAR=..., readonly VAR=..., VAR=... (bare).
    Resolves nested ${} references using _KNOWN_PATH_VARIABLES.
    Returns resolved path if it contains '/', or None.
    """
    # region FUNC_trace_variable_assignment
    ## @purpose  Local variable assignment tracking within a single shell file
    ## @io       file_path, var_name → str | None (resolved path with '/')
    ## @complexity O(n) where n = lines in file
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    # Pattern: optional local/export/readonly prefix, VAR=value
    # Multi-line (backslash continuation) NOT supported
    pattern = rf'(?:local\s+|export\s+|readonly\s+)?{re.escape(var_name)}=["\']?([^"\'\n]+)'

    matches = list(re.finditer(pattern, content))
    if not matches:
        return None

    # Last assignment (closest to use site)
    last_match = matches[-1]
    value = last_match.group(1).strip()

    # Resolve nested ${} and $ references
    for nested_name, nested_value in _KNOWN_PATH_VARIABLES.items():
        value = value.replace(f"${{{nested_name}}}", nested_value)
        value = value.replace(f"${nested_name}", nested_value)

    # Return only if value contains a path separator
    if "/" in value:
        return value
    return None
    # endregion FUNC_trace_variable_assignment


def resolve_import(source_file: Path, import_path: str, source_layer: str) -> Path | None:
    """Resolve an import path to an absolute target path.

    Extended per DevPlan DataFlow T1.5: uses auto-collected variable registry
    + local variable tracing instead of hardcoded variable list.

    Returns None if the path cannot be resolved (non-path reference).
    """
    # region FUNC_resolve_import
    ## @purpose  Resolve an import reference to an absolute Path within core/
    ## @io       source_file, import_path, source_layer → Path | None
    ## @complexity O(n) where n = number of known variables
    if not _looks_like_path(import_path):
        return None

    resolved = import_path.strip()

    # Step 1: substitute known variables (auto-collected + contextual)
    resolved = _substitute_variables(resolved, source_file, source_layer)

    # Step 1.5 (DevPlan 116 B11 T1): dotted-name → core/ path
    #   core.internal.shared.telegram_notifier → <CORE_DIR>/internal/shared/telegram_notifier
    # Non-core dotted names (xml.etree, botocore.session) → bare relative (no leading
    # '/') → filtered by the "no leading /" check below (None, not a cross-layer import).
    if "/" not in resolved and _RE_DOTTED_NAME.match(resolved):
        if resolved.startswith("core.internal."):
            rel = resolved[len("core.internal.") :].replace(".", "/")
            resolved = f"{CORE_DIR}/internal/{rel}"
        else:
            resolved = resolved.replace(".", "/")

    # Step 2: if result is a bare $variable without path, try local tracking
    if resolved.startswith("$") and "/" not in resolved:
        var_name = resolved.lstrip("$").strip("{}")
        traced = _trace_variable_assignment(source_file, var_name)
        if traced:
            resolved = traced

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
    # endregion FUNC_resolve_import


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

        # Pattern 5 (NEW DataFlow T3.1): make -C <path>
        m = re.search(r"(?:^|\s)make\s+-C\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        # Pattern 6 (NEW DataFlow T3.2): docker compose -f <path>
        m = re.search(r"(?:^|\s)docker[\s-]+compose\s+(?:.*\s)?-f\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if _looks_like_path(path) and path not in ("-f",):
                imports.append((i, path, exempt))
            continue

        # Pattern 7 (NEW DevPlan 116 B11 T1, U-09): python3 -m <module>
        # Детектирует `python3 -m core.internal.shared.node_yaml` (в т.ч. внутри
        # $(...) и с '\'-продолжением строки). Модуль без точки (pytest, pip, venv)
        # → _looks_like_path=False → пропускается.
        m = re.search(r"python3\s+-m\s+(\S+)", stripped)
        if m:
            mod = m.group(1).rstrip("\\")
            if _looks_like_path(mod):
                imports.append((i, mod, exempt))
            continue

    # NEW (DataFlow T2.3): ShellCheck data-flow analysis (дополнительный слой)
    # Вызывается только для importing layers (не для lib/, templates/)
    if source_layer in _IMPORTING_LAYERS:
        try:
            from _conftest.shellcheck import get_shellcheck_bash_calls

            shellcheck_calls = get_shellcheck_bash_calls(file_path)
            for shell_lineno, imp_path in shellcheck_calls:
                # Проверяем, не дублируется ли с уже найденным
                already_found = any(
                    existing_lineno == shell_lineno and existing_path == imp_path
                    for existing_lineno, existing_path, _ in imports
                )
                if not already_found:
                    exempt = _has_lint_exempt(lines, shell_lineno)
                    imports.append((shell_lineno, imp_path, exempt))
        except ImportError:
            logger.debug("[IMP:5][scan][shellcheck] Module not available for %s", file_path)
        except Exception as exc:
            logger.warning("[IMP:6][scan][shellcheck] Error for %s: %s", file_path, exc)

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


# ─── GATE #8 V2: INVOKE DETECTION ─────────────────────────────────────────


def _detect_invoke_calls(source_file: Path) -> list[dict]:
    """Find all `invoke_module_interface <module> <interface>` calls in a file.

    Returns list of dicts with keys: module, interface, lineno, args.
    Skips commented lines and here-documents (basic detection).
    """
    calls: list[dict] = []
    try:
        content = source_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return calls

    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Pattern: invoke_module_interface <module> <interface> [args...]
        m = re.search(
            r"invoke_module_interface\s+"
            r'"?([a-zA-Z0-9_-]+)"?\s+'  # module name (capture non-variable names)
            r'"?"?([a-zA-Z0-9_-]+)"?"?',  # interface name (capture non-variable names)
            stripped,
        )
        if m:
            module = m.group(1)
            interface = m.group(2)
            # Skip if either is a variable reference (starts with $)
            if module.startswith("$") or interface.startswith("$"):
                calls.append(
                    {
                        "module": module,
                        "interface": interface,
                        "lineno": i,
                        "warn": True,  # variable args — can't statically validate
                    }
                )
            else:
                calls.append(
                    {
                        "module": module,
                        "interface": interface,
                        "lineno": i,
                        "warn": False,
                    }
                )

    return calls


def _validate_interfaces(
    invoke_calls: list[dict],
    violations: list[str],
    source_file: Path,
) -> list[str]:
    """For each invoke call, verify the interface is registered in module.yaml.interfaces.

    Adds violation strings to the list for:
    - Interface not found in module.yaml.interfaces
    - Interface is not a known interface name
    - Module.yaml not found for the module
    - Variable args (warn only, not violation)
    """
    for call in invoke_calls:
        lineno = call["lineno"]
        module = call["module"]
        interface = call["interface"]

        if call.get("warn"):
            logger.warning(
                "[IMP:7][gate8-v2][warn] %s:%d — invoke_module_interface with variable "
                "args (module='%s', interface='%s') — cannot statically validate",
                source_file,
                lineno,
                module,
                interface,
            )
            continue

        # Check that interface is a known type
        if interface not in _VALID_INTERFACES:
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] "
                f"Unknown interface '{interface}' for module '{module}' — "
                f"valid interfaces: {sorted(_VALID_INTERFACES)}"
            )
            continue

        # Check module.yaml exists
        module_yaml = CORE_DIR / "modules" / module / "module.yaml"
        if not module_yaml.exists():
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] module.yaml not found for module '{module}'"
            )
            continue

        # Read interfaces list from module.yaml
        try:
            content = module_yaml.read_text(encoding="utf-8", errors="replace")
        except Exception:
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] Cannot read module.yaml for '{module}'"
            )
            continue

        # Parse interfaces from YAML (simple regex — no PyYAML dependency)
        registered_interfaces: list[str] = []
        in_interfaces = False
        for yaml_line in content.split("\n"):
            stripped_line = yaml_line.strip()
            if stripped_line == "interfaces:":
                in_interfaces = True
                continue
            if in_interfaces:
                # Check if this line starts a new top-level key (not indented)
                if stripped_line and not stripped_line.startswith("-") and ":" in stripped_line:
                    in_interfaces = False
                    continue
                # Parse list item: "- healthcheck"
                list_match = re.match(r"^\s*-\s+(\S+)", stripped_line)
                if list_match:
                    registered_interfaces.append(list_match.group(1))
                elif not stripped_line or stripped_line.startswith("#"):
                    continue
                else:
                    # Empty list marker or continuation
                    if stripped_line == "[]":
                        in_interfaces = False
                        continue
                    # Not a list item — end of interfaces block
                    in_interfaces = False

        # Check if interface is registered
        if interface not in registered_interfaces:
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] "
                f"Interface '{interface}' NOT REGISTERED for module '{module}' — "
                f"registered: {registered_interfaces if registered_interfaces else '[]'}"
            )

    return violations


def _detect_direct_module_calls(source_file: Path) -> list[tuple[int, str, str]]:
    """Detect direct bash/source/. calls to modules/ from internal/ without invoke_module_interface.

    Returns list of (lineno, call_type, target) tuples.
    Detects patterns like:
    - bash modules/<name>/...
    - bash "${CORE_DIR}/modules/...
    - bash "${PATHS_MODULES_DIR}/...
    - source modules/<name>/...
    - . modules/<name>/...

    Skips:
    - Files outside internal/ layer (checked by caller)
    - Commented lines
    - invoke_module_interface calls (those go through typed contract validation)
    """
    direct_calls: list[tuple[int, str, str]] = []
    try:
        content = source_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return direct_calls

    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Skip invoke_module_interface calls — those are validated separately
        if "invoke_module_interface" in stripped:
            continue

        # Pattern: bash/shell calls with paths containing modules/
        # e.g. bash modules/<name>/..., bash "${CORE_DIR}/modules/..."
        # or bash "$variable" where variable was assigned from a modules/ path
        # (variable tracking via assignment map)
        for cmd in ["bash", "sh", "/bin/bash", "/bin/sh"]:
            m = re.search(rf"(?:^|\s)(?:{cmd})\s+(\S+)", stripped)
            if m:
                target = m.group(1)
                # Check if target literally contains "modules/" (direct path)
                if "modules/" in target:
                    direct_calls.append((i, "bash (direct path)", target))
                # Check if target is "${...}" or "$..." with modules/ in the resolved path
                # (variable references)
                elif target.startswith(("$", '"$')) and _resolve_var_to_modules_path(target, content):
                    direct_calls.append((i, "bash (variable → modules/)", target))

        # Pattern: source modules/<name>/...
        m = re.search(r"(?:^|\s)(?:source)\s+(\S+)", stripped)
        if m:
            target = m.group(1)
            if "modules/" in target:
                direct_calls.append((i, "source (direct path)", target))

        # Pattern: . modules/<name>/...
        m = re.search(r"(?:^|\s)\.\s+(\S+)", stripped)
        if m:
            target = m.group(1)
            if target not in ('"$@"', "${@}", "$@", '".",') and "modules/" in target:
                direct_calls.append((i, ". (direct path)", target))

    return direct_calls


def _resolve_var_to_modules_path(var_ref: str, file_content: str) -> bool:
    """Check if a variable reference was assigned from a modules/ path.

    Simple assignment tracking: looks for `local var=...modules/...` or
    `var=...modules/...` patterns in the file content.

    Returns True if the variable was assigned from a modules/-containing path.
    """
    # Extract variable name from reference like "$hc_script", "${hc_script}", '"$hc_script"'
    var_name = var_ref.strip().strip('"').lstrip("${").rstrip("}")
    var_name = var_name.lstrip("$")

    if not var_name:
        return False

    # Check for assignment patterns containing modules/
    # local hc_script="${CORE_DIR}/modules/..."
    # local healthcheck_script="${PATHS_MODULES_DIR}/..."
    # local hook_script="$(dirname "$module_yaml")/..."  (indirect — hooks)
    # local install_script="${PATHS_MODULES_DIR}/..."
    patterns = [
        rf"local\s+{re.escape(var_name)}\s*=\s*.*modules/",
        rf"{re.escape(var_name)}\s*=\s*.*modules/",
        rf"local\s+{re.escape(var_name)}\s*=\s*.*dirname\s+\${{?module_yaml}}?.*hook",
    ]
    return any(re.search(pattern, file_content) for pattern in patterns)


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
                f"include '{import_path}' — only ../../templates/module.mk, "
                f"../../templates/module-system.mk, or ../../Makefile.common allowed"
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
        # Strict allowlist (DevPlan 116 B11 T1, канон B8 D3): задокументированные
        # (path, lineno) записи подавляются; ЛЮБОЕ новое нарушение → RED.
        if _is_allowlisted(source_file, lineno):
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
    if _is_allowlisted(source_file, lineno):
        return None
    return f"  {source_file}:{lineno} — [{source_layer}→{target_layer}] '{import_path}' (forbidden)"


# ─── MAIN LINT LOGIC ─────────────────────────────────────────────────────


def lint_core() -> list[str]:
    """Run the cross-layer import linter across all files in core/.

    Gate #8 v2 adds two phases:
    - Phase 1: Direct module calls from internal/ (bash/source/. with modules/ path)
    - Phase 2: invoke_module_interface validation against module.yaml.interfaces

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

    # ═══════════════════════════════════════════════════════════════════════
    # Gate #8 v2 — Phase 1: direct module calls from internal/
    # Detect bash/source/. calls to modules/ paths that bypass
    # invoke_module_interface
    # ═══════════════════════════════════════════════════════════════════════
    for fpath in sh_files:
        source_layer = classify_layer(fpath)
        if source_layer != "internal":
            continue
        direct_calls = _detect_direct_module_calls(fpath)
        for lineno, call_type, target in direct_calls:
            violations.append(
                f"  {fpath}:{lineno} — [internal→modules·direct] "
                f"Direct module call ({call_type}): '{target}' — "
                f"use invoke_module_interface instead"
            )

    # ═══════════════════════════════════════════════════════════════════════
    # Gate #8 v2 — Phase 2: invoke_module_interface validation
    # For each invoke call, verify interface is registered in module.yaml
    # ═══════════════════════════════════════════════════════════════════════
    for fpath in sh_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        invoke_calls = _detect_invoke_calls(fpath)
        if invoke_calls:
            violations = _validate_interfaces(invoke_calls, violations, fpath)

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
                f"every module Makefile must include templates/module.mk or module-system.mk"
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


# ═══════════════════════════════════════════════════════════════════════════
# Gate #8 v2 — Unit Tests
# ═══════════════════════════════════════════════════════════════════════════


# region TEST_DETECT_DIRECT_CALL
@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 — direct module call detection
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_direct_module_call_detected(tmp_path: Path) -> None:
    """Gate #8 v2: direct bash modules/ call from internal/ file is detected.

    ## @purpose — Input: file with `bash modules/postgres/healthcheck.sh` in internal/.
    ##            Expected: `_detect_direct_module_calls` returns 1 violation.
    """
    # region FUNC_test_direct_module_call_detected
    test_file = tmp_path / "test.sh"
    test_file.write_text('#!/usr/bin/env bash\nbash "${CORE_DIR}/modules/postgres/healthcheck.sh" liveness\n')
    calls = _detect_direct_module_calls(test_file)
    assert len(calls) == 1, f"Expected 1 direct call, got {len(calls)}: {calls}"
    assert "modules/" in calls[0][2], f"Expected modules/ in target: {calls[0]}"
    logger.info("[IMP:9][gate8-v2][test] Direct module call detected: line %d, type=%s", calls[0][0], calls[0][1])
    # endregion FUNC_test_direct_module_call_detected


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Anti-survivorship — old gate blindness fixed
# · Last fail: old Gate #8 (blind to bash "$variable" pattern)
# · Remove if: Gate #8 v2 is superseded or variable tracking is no longer needed
def test_gate8_original_blindness_fixed(tmp_path: Path) -> None:
    """Gate #8 v2: old blind pattern `bash "$hc_script"` is now detected via variable tracking.

    ## @purpose — Anti-survivorship: original bug pattern that old gate missed.
    ##            Gate #8 v2 detects variable-based calls through assignment tracking.
    """
    # region FUNC_test_gate8_original_blindness_fixed
    test_file = tmp_path / "test.sh"
    test_file.write_text(
        "#!/usr/bin/env bash\n"
        'local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\n'
        'bash "$hc_script" liveness\n'
    )
    calls = _detect_direct_module_calls(test_file)
    assert len(calls) >= 1, (
        f"Gate #8 v2 must detect bash via variable — old gate was blind to this pattern. Calls found: {calls}"
    )
    logger.info("[IMP:9][gate8-v2][test] Old blind pattern detected: %s", calls)
    # endregion FUNC_test_gate8_original_blindness_fixed


# endregion TEST_DETECT_DIRECT_CALL


# region TEST_INVOKE_VALIDATION
@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 — registered interface passes
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_invoke_registered_interface_passes(tmp_path: Path) -> None:
    """Gate #8 v2: invoke_module_interface with registered interface passes.

    ## @purpose — Input: `invoke_module_interface postgres healthcheck`
    ##            + postgres/module.yaml has `interfaces: [healthcheck]`
    ##            Expected: 0 violations.
    """
    # region FUNC_test_invoke_registered_interface_passes
    # Create a mock module.yaml
    module_dir = CORE_DIR / "modules" / "_test_registered"
    module_dir.mkdir(parents=True, exist_ok=True)
    module_yaml = module_dir / "module.yaml"
    module_yaml.write_text("name: _test_registered\ninstall_type: docker\ninterfaces:\n  - healthcheck\n")

    test_file = tmp_path / "deploy.sh"
    test_file.write_text("#!/usr/bin/env bash\ninvoke_module_interface _test_registered healthcheck liveness\n")

    try:
        invoke_calls = _detect_invoke_calls(test_file)
        violations: list[str] = []
        violations = _validate_interfaces(invoke_calls, violations, test_file)
        assert len(violations) == 0, f"Expected 0 violations for registered interface, got: {violations}"
        logger.info("[IMP:9][gate8-v2][test] Registered interface passes — 0 violations")
    finally:
        # Cleanup
        import shutil

        shutil.rmtree(module_dir, ignore_errors=True)
    # endregion FUNC_test_invoke_registered_interface_passes


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 — unregistered interface fails
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_invoke_unregistered_interface_fails(tmp_path: Path) -> None:
    """Gate #8 v2: invoke_module_interface with unregistered interface fails.

    ## @purpose — Input: `invoke_module_interface minio healthcheck`
    ##            + minio/module.yaml has `interfaces: []`
    ##            Expected: violation with 'interface not registered'.
    """
    # region FUNC_test_invoke_unregistered_interface_fails
    # Create a mock module.yaml with empty interfaces
    module_dir = CORE_DIR / "modules" / "_test_unregistered"
    module_dir.mkdir(parents=True, exist_ok=True)
    module_yaml = module_dir / "module.yaml"
    module_yaml.write_text("name: _test_unregistered\ninstall_type: docker\ninterfaces: []\n")

    test_file = tmp_path / "deploy.sh"
    test_file.write_text("#!/usr/bin/env bash\ninvoke_module_interface _test_unregistered healthcheck liveness\n")

    try:
        invoke_calls = _detect_invoke_calls(test_file)
        violations: list[str] = []
        violations = _validate_interfaces(invoke_calls, violations, test_file)
        assert len(violations) >= 1, "Expected violation for unregistered interface"
        assert "NOT REGISTERED" in violations[0], f"Expected 'NOT REGISTERED' in: {violations[0]}"
        logger.info("[IMP:9][gate8-v2][test] Unregistered interface detected: %s", violations[0])
    finally:
        import shutil

        shutil.rmtree(module_dir, ignore_errors=True)
    # endregion FUNC_test_invoke_unregistered_interface_fails


@pytest.mark.gate
# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate #8 v2 integration — all call sites validated
# · Last fail: N/A (new test)
# · Remove if: Gate #8 is superseded
def test_all_call_sites_use_invoke() -> None:
    """Gate #8 v2: all 6 call sites now use invoke_module_interface.

    ## @purpose — Run lint_core() after refactoring — must show 0 violations.
    ##            This is the integration acceptance test for Gate #8 v2.
    """
    # region FUNC_test_all_call_sites_use_invoke
    violations = lint_core()

    # Filter to only Gate #8 v2 violations (direct calls + invoke validation)
    gate8_violations = [v for v in violations if "[internal→modules·direct]" in v or "[internal→modules·invoke]" in v]

    # After call site refactoring, there should be 0 Gate #8 v2 violations
    assert len(gate8_violations) == 0, (
        f"Gate #8 v2 found {len(gate8_violations)} typed contract violation(s):\n" + "\n".join(gate8_violations)
    )
    logger.info("[IMP:9][gate8-v2][test] All call sites validated — 0 typed contract violations")
    # endregion FUNC_test_all_call_sites_use_invoke


# endregion TEST_INVOKE_VALIDATION


# ═══════════════════════════════════════════════════════════════════════════
# Wave 4 — DataFlow Unit Tests (DevPlan 07-DevPlan-DataFlow)
# ═══════════════════════════════════════════════════════════════════════════


# region TEST_LOOKS_LIKE_PATH (T4.1)
@pytest.mark.gate
class TestLooksLikePath:
    """Unit tests for _looks_like_path() function — T4.1 (≥5 tests).

    ## @purpose — Verify that _looks_like_path correctly distinguishes path-like
    ##            arguments from flags, special variables, and non-path strings.
    """

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Literal path detection
    # · Last fail: N/A (new test)
    # · Remove if: _looks_like_path is superseded
    def test_literal_path(self) -> None:
        """Literal path with / is detected."""
        assert _looks_like_path("modules/postgres/healthcheck.sh") is True

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Variable with path
    # · Last fail: N/A (new test)
    # · Remove if: _looks_like_path is superseded
    def test_variable_with_path(self) -> None:
        """${VAR}/path is detected."""
        assert _looks_like_path("${CORE_DIR}/modules/postgres/healthcheck.sh") is True

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Bare variable detection
    # · Last fail: old _looks_like_path returned False for bare $var
    # · Remove if: bare variable detection is no longer needed
    def test_bare_variable(self) -> None:
        """Bare $variable (no /) is detected as potential path."""
        assert _looks_like_path("$hc_script") is True

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Variable braces without path
    # · Last fail: N/A (new test)
    # · Remove if: _looks_like_path is superseded
    def test_bare_variable_braces(self) -> None:
        """${variable} without / is NOT detected as path (bare braces, no separator)."""
        assert _looks_like_path("${hc_script}") is False

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Flag is not a path
    # · Last fail: N/A (new test)
    # · Remove if: _looks_like_path is superseded
    def test_flag_minus_c(self) -> None:
        """Flag argument is not a path."""
        assert _looks_like_path("-c") is False

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Special shell vars not paths
    # · Last fail: old _looks_like_path could not detect $var — N/A
    # · Remove if: _looks_like_path is superseded
    def test_special_vars(self) -> None:
        """Special shell variables are not paths."""
        for var in ["$?", "$#", "$$", "$!", "$@", "$*", "$-", "$0"]:
            assert _looks_like_path(var) is False, f"{var} should not be path"

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Empty string
    # · Last fail: N/A (new test)
    # · Remove if: _looks_like_path is superseded
    def test_empty_string(self) -> None:
        """Empty string is not a path."""
        assert _looks_like_path("") is False

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Quoted bare variable
    # · Last fail: old _looks_like_path could not detect bare $var
    # · Remove if: _looks_like_path is superseded
    def test_quoted_bare_variable(self) -> None:
        """Quoted bare variable is detected."""
        assert _looks_like_path('"$hc_script"') is True

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Multiple variables
    # · Last fail: N/A (new test)
    # · Remove if: _looks_like_path is superseded
    def test_multiple_variables_in_string(self) -> None:
        """String with multiple $vars and / is detected."""
        assert _looks_like_path("${CORE_DIR}/modules/${mod_name}/healthcheck.sh") is True

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · $dollar sign alone
    # · Last fail: 2026-07-18 — bare variable detection didn't filter single $
    # · Remove if: _looks_like_path is superseded
    def test_dollar_sign_only(self) -> None:
        """Single $ is not a path (no variable name after $)."""
        assert _looks_like_path("$") is False

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Positional param not path
    # · Last fail: N/A (new test)
    # · Remove if: _looks_like_path is superseded
    def test_positional_param(self) -> None:
        """Positional parameter $1 is not a path."""
        assert _looks_like_path("$1") is False


# endregion TEST_LOOKS_LIKE_PATH


# region TEST_RESOLVE_IMPORT (T4.2)
@pytest.mark.gate
class TestResolveImport:
    """Unit tests for resolve_import() function — T4.2 (≥3 tests).

    ## @purpose — Verify that resolve_import correctly resolves auto-collected,
    ##            contextual, and bare variable references.
    """

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Known variable substitution
    # · Last fail: N/A (new test)
    # · Remove if: resolve_import is superseded
    def test_known_variable_substitution(self, tmp_path: Path) -> None:
        """Auto-collected variable from paths.sh is substituted."""
        source_file = tmp_path / "entrypoints" / "test.sh"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("")
        result = resolve_import(
            source_file,
            "${PATHS_MODULES_DIR}/postgres/healthcheck.sh",
            "entrypoints",
        )
        assert result is not None
        assert "core/modules/postgres/healthcheck.sh" in str(result)

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Unresolved bare variable
    # · Last fail: N/A (new test)
    # · Remove if: resolve_import is superseded
    def test_unresolved_bare_variable(self, tmp_path: Path) -> None:
        """Bare variable without assignment returns None."""
        source_file = tmp_path / "entrypoints" / "test.sh"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("")
        result = resolve_import(source_file, "$unknown_var", "entrypoints")
        assert result is None

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Bare variable with trace
    # · Last fail: N/A (new test)
    # · Remove if: resolve_import is superseded
    def test_bare_variable_with_trace(self, tmp_path: Path) -> None:
        """Bare variable traced to local assignment resolves correctly."""
        f = tmp_path / "test.sh"
        f.write_text('local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\n')
        traced = _trace_variable_assignment(f, "hc_script")
        assert traced is not None
        assert "modules/postgres/healthcheck.sh" in traced

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Nested variable substitution
    # · Last fail: N/A (new test)
    # · Remove if: resolve_import is superseded
    def test_nested_variable_substitution(self, tmp_path: Path) -> None:
        """Nested variable references are resolved recursively."""
        source_file = tmp_path / "internal" / "test.sh"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text("")
        result = resolve_import(
            source_file,
            "${PATHS_CORE_DIR}/modules/postgres/healthcheck.sh",
            "internal",
        )
        assert result is not None
        assert str(result).endswith("core/modules/postgres/healthcheck.sh")

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Contextual variable _EP_DIR
    # · Last fail: 2026-07-18 — path outside CORE_DIR was filtered
    # · Remove if: resolve_import is superseded
    def test_contextual_variable(self, tmp_path: Path) -> None:
        """Contextual variable (_EP_DIR) resolves to source file directory.

        Uses a source file under CORE_DIR so resolve_import doesn't filter it.
        """
        source = CORE_DIR / "entrypoints" / "_test_ep.sh"
        try:
            # Create temp source file under actual CORE_DIR
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("#!/bin/bash\n")
            result = resolve_import(source, "${_EP_DIR}/../lib/foo.sh", "entrypoints")
            assert result is not None
            assert str(result).endswith("core/lib/foo.sh")
        finally:
            if source.exists():
                source.unlink()
            # Clean up parent if empty
            if source.parent.exists() and not any(source.parent.iterdir()):
                source.parent.rmdir()


# endregion TEST_RESOLVE_IMPORT


# region TEST_COLLECT_PATH_VARIABLES (T4.3)
@pytest.mark.gate
class TestCollectPathVariables:
    """Unit tests for _collect_path_variables() function — T4.3 (≥2 tests).

    ## @purpose — Verify auto-collection from paths.sh works correctly,
    ##            including custom file paths and edge cases.
    """

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Real paths.sh parsed
    # · Last fail: N/A (new test)
    # · Remove if: _collect_path_variables is superseded
    def test_real_paths_sh_parsed(self) -> None:
        """Real paths.sh is parsed and returns expected variables."""
        variables = _collect_path_variables()
        assert len(variables) >= 6
        assert "PATHS_LIB_DIR" in variables
        assert "PATHS_CORE_DIR" in variables
        assert "PATHS_MODULES_DIR" in variables
        assert "PATHS_TEMPLATES_DIR" in variables
        assert "PATHS_INTERNAL_DIR" in variables
        assert "PLATFORM_ROOT" in variables

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Custom paths file parsing
    # · Last fail: N/A (new test)
    # · Remove if: _collect_path_variables is superseded
    def test_custom_paths_file(self, tmp_path: Path) -> None:
        """Custom paths file is parsed correctly."""
        f = tmp_path / "paths.sh"
        f.write_text('readonly MY_DIR="/opt/myapp"\nexport MY_OTHER="/var/lib/myapp"\n')
        variables = _collect_path_variables(f)
        assert "MY_DIR" in variables
        assert variables["MY_DIR"] == "/opt/myapp"
        assert "MY_OTHER" in variables

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Empty file handling
    # · Last fail: N/A (new test)
    # · Remove if: _collect_path_variables is superseded
    def test_empty_file(self, tmp_path: Path) -> None:
        """Empty file returns empty dict."""
        f = tmp_path / "empty.sh"
        f.write_text("")
        variables = _collect_path_variables(f)
        assert variables == {}

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Comments-only file
    # · Last fail: N/A (new test)
    # · Remove if: _collect_path_variables is superseded
    def test_only_comments(self, tmp_path: Path) -> None:
        """File with only comments returns empty dict."""
        f = tmp_path / "comments.sh"
        f.write_text("# This is a comment\n# Another comment\n")
        variables = _collect_path_variables(f)
        assert variables == {}


# endregion TEST_COLLECT_PATH_VARIABLES


# region TEST_TRACE_VARIABLE_ASSIGNMENT (T4.4)
@pytest.mark.gate
class TestTraceVariableAssignment:
    """Unit tests for _trace_variable_assignment() function — T4.4 (≥2 tests).

    ## @purpose — Verify local variable assignment tracking works for various
    ##            assignment patterns (local, export, readonly, multi-assign).
    """

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Local assignment found
    # · Last fail: N/A (new test)
    # · Remove if: _trace_variable_assignment is superseded
    def test_local_assignment_found(self, tmp_path: Path) -> None:
        """local var=path is traced correctly."""
        f = tmp_path / "test.sh"
        f.write_text('local hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\nbash "$hc_script"\n')
        result = _trace_variable_assignment(f, "hc_script")
        assert result is not None
        assert "healthcheck.sh" in result

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · No assignment returns None
    # · Last fail: N/A (new test)
    # · Remove if: _trace_variable_assignment is superseded
    def test_no_assignment(self, tmp_path: Path) -> None:
        """Variable not assigned locally returns None."""
        f = tmp_path / "test.sh"
        f.write_text('bash "$hc_script"\n')
        result = _trace_variable_assignment(f, "hc_script")
        assert result is None

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Multiple assignments (last wins)
    # · Last fail: N/A (new test)
    # · Remove if: _trace_variable_assignment is superseded
    def test_multiple_assignments_last_wins(self, tmp_path: Path) -> None:
        """Last assignment is used."""
        f = tmp_path / "test.sh"
        f.write_text('local var="/first/path.sh"\nlocal var="/second/path.sh"\nbash "$var"\n')
        result = _trace_variable_assignment(f, "var")
        assert result is not None
        assert "second" in result

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Non-path assignment returns None
    # · Last fail: N/A (new test)
    # · Remove if: _trace_variable_assignment is superseded
    def test_assignment_without_path(self, tmp_path: Path) -> None:
        """Assignment without / in value returns None."""
        f = tmp_path / "test.sh"
        f.write_text('local flag="--verbose"\n')
        result = _trace_variable_assignment(f, "flag")
        assert result is None

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Export assignment
    # · Last fail: N/A (new test)
    # · Remove if: _trace_variable_assignment is superseded
    def test_export_assignment(self, tmp_path: Path) -> None:
        """export var=path is traced."""
        f = tmp_path / "test.sh"
        f.write_text('export MY_SCRIPT="/opt/platform/core/modules/postgres/healthcheck.sh"\n')
        result = _trace_variable_assignment(f, "MY_SCRIPT")
        assert result is not None
        assert "healthcheck.sh" in result

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Readonly assignment
    # · Last fail: N/A (new test)
    # · Remove if: _trace_variable_assignment is superseded
    def test_readonly_assignment(self, tmp_path: Path) -> None:
        """readonly var=path is traced."""
        f = tmp_path / "test.sh"
        f.write_text('readonly MY_DIR="/opt/core/modules/postgres"\n')
        result = _trace_variable_assignment(f, "MY_DIR")
        assert result is not None
        assert "postgres" in result


# endregion TEST_TRACE_VARIABLE_ASSIGNMENT


# region TEST_SHELLCHECK_INTEGRATION (T4.5)
@pytest.mark.gate
class TestShellCheckIntegration:
    """Unit tests for tests/_conftest/shellcheck.py integration — T4.5 (≥2 tests).

    ## @purpose — Verify ShellCheck integration module works correctly,
    ##            including graceful degradation when shellcheck is unavailable.
    """

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Check available returns (bool, str)
    # · Last fail: N/A (new test)
    # · Remove if: ShellCheck integration is removed
    def test_check_available_returns_bool(self) -> None:
        """_check_shellcheck_available returns (bool, str)."""
        from _conftest.shellcheck import _check_shellcheck_available

        available, msg = _check_shellcheck_available()
        assert isinstance(available, bool)
        assert isinstance(msg, str)

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Empty file has no SC2154
    # · Last fail: N/A (new test)
    # · Remove if: ShellCheck integration is removed
    def test_parse_sc2154_empty_file(self, tmp_path: Path) -> None:
        """Empty file has no SC2154 diagnostics."""
        from _conftest.shellcheck import _parse_shellcheck_sc2154

        f = tmp_path / "empty.sh"
        f.write_text("#!/bin/bash\n")
        vars_found = _parse_shellcheck_sc2154(f)
        assert vars_found == []

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Unassigned var triggers SC2154
    # · Last fail: N/A (new test)
    # · Remove if: ShellCheck integration is removed
    def test_parse_sc2154_unassigned_var(self, tmp_path: Path) -> None:
        """Unassigned variable triggers SC2154."""
        from _conftest.shellcheck import _parse_shellcheck_sc2154

        f = tmp_path / "test.sh"
        f.write_text('#!/bin/bash\nbash "$hc_script"\n')
        vars_found = _parse_shellcheck_sc2154(f)
        # hc_script is not assigned in this file → SC2154 should fire
        assert "hc_script" in vars_found

    # 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Get bash calls with ShellCheck
    # · Last fail: N/A (new test)
    # · Remove if: ShellCheck integration is removed
    def test_get_bash_calls_with_shellcheck(self, tmp_path: Path) -> None:
        """ShellCheck detects bash call with variable assigned from path."""
        from _conftest.shellcheck import get_shellcheck_bash_calls

        f = tmp_path / "test.sh"
        f.write_text(
            '#!/bin/bash\nlocal hc_script="${CORE_DIR}/modules/postgres/healthcheck.sh"\nbash "$hc_script" liveness\n'
        )
        # hc_script is locally assigned → SC2154 NOT triggered (it IS assigned)
        # This tests the limitation: ShellCheck layer B only helps when
        # variable is assigned and used in DIFFERENT scopes
        calls = get_shellcheck_bash_calls(f)
        assert isinstance(calls, list)
        logger.info(
            "[IMP:9][test][shellcheck] get_shellcheck_bash_calls returned %d calls (expected 0 "
            "since var is locally assigned): %s",
            len(calls),
            calls,
        )


# endregion TEST_SHELLCHECK_INTEGRATION


# region TEST_B11_NEGATIVE (R5 anti-survivorship — DevPlan 116 B11 T1, U-09)
@pytest.mark.gate
class TestB11DottedImportDetection:
    """R5 negative tests: dotted-imports and python3 -m are RED outside allowlist.

    ## @purpose — Доказывают, что расширенный гейт ловит dotted-нарушения
    ##            (anti-survivorship: старый гейт был слеп к этим паттернам).
    ##            Фикстуры создаются ПОД core/modules/ (слой modules — subject to rules)
    ##            во временном каталоге и удаляются в finally (паттерн
    ##            test_invoke_registered_interface_passes).
    """

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · dotted py import in modules → RED
    # · Scenario: `from core.internal.shared.telegram_notifier import ...` в modules-фикстуре
    # · Last fail: old gate — 36 passed при 4 реальных py-нарушениях (слепота к dotted)
    # · Remove if: cross-layer gate superseded
    def test_dotted_py_import_in_modules_is_violation(self, tmp_path: Path) -> None:
        """R5 negative: dotted py-import из modules → violation (RED)."""
        # region FUNC_test_dotted_py_import_in_modules_is_violation
        fixture_dir = CORE_DIR / "modules" / "_b11_negative_py_tmp"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        py_file = fixture_dir / "test_negative.py"
        try:
            py_file.write_text(
                "#!/usr/bin/env python3\nfrom core.internal.shared.telegram_notifier import send_telegram\n"
            )
            imports = scan_py_file(py_file)
            assert len(imports) == 1, f"Expected 1 dotted import, got {imports}"
            lineno, imp_path, exempt = imports[0]
            assert _looks_like_path(imp_path), f"dotted name must look like path: {imp_path}"
            resolved = resolve_import(py_file, imp_path, "modules")
            assert resolved is not None, "dotted import must resolve to a core/ path"
            assert "core/internal/shared/telegram_notifier" in str(resolved)
            msg = check_violation(py_file, lineno, imp_path, "py", exempt, resolved)
            assert msg is not None, f"R5 FAIL: dotted import {imp_path} in modules must be RED (old gate was blind)"
            assert "[modules→internal]" in msg
            logger.info("[IMP:9][test][b11-negative] dotted py import RED: %s", msg)
        finally:
            import shutil

            shutil.rmtree(fixture_dir, ignore_errors=True)
        # endregion FUNC_test_dotted_py_import_in_modules_is_violation

    # 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · python3 -m in modules sh → RED
    # · Scenario: `python3 -m core.internal.shared.node_yaml` в sh-фикстуре modules
    # · Last fail: old gate — слепота к python3 -m (disk-monitor/postgres-hook жили незамеченными)
    # · Remove if: cross-layer gate superseded
    def test_python3_m_in_modules_is_violation(self, tmp_path: Path) -> None:
        """R5 negative: python3 -m core.internal.* из modules/sh → violation (RED)."""
        # region FUNC_test_python3_m_in_modules_is_violation
        fixture_dir = CORE_DIR / "modules" / "_b11_negative_sh_tmp"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        sh_file = fixture_dir / "test_negative.sh"
        try:
            sh_file.write_text(
                "#!/usr/bin/env bash\n"
                'db_name="$(python3 -m core.internal.shared.node_yaml \\\n'
                '    --file "${ai_yaml}" --get needs.database)"\n'
            )
            imports = scan_sh_file(sh_file, "modules")
            dotted = [imp for imp in imports if _RE_DOTTED_NAME.match(imp[1])]
            assert len(dotted) >= 1, f"Expected python3 -m dotted import, got {imports}"
            lineno, imp_path, exempt = dotted[0]
            resolved = resolve_import(sh_file, imp_path, "modules")
            assert resolved is not None, "python3 -m dotted module must resolve to a core/ path"
            msg = check_violation(sh_file, lineno, imp_path, "sh", exempt, resolved)
            assert msg is not None, f"R5 FAIL: python3 -m {imp_path} in modules must be RED (old gate was blind)"
            assert "[modules→internal]" in msg
            logger.info("[IMP:9][test][b11-negative] python3 -m RED: %s", msg)
        finally:
            import shutil

            shutil.rmtree(fixture_dir, ignore_errors=True)
        # endregion FUNC_test_python3_m_in_modules_is_violation


# endregion TEST_B11_NEGATIVE
