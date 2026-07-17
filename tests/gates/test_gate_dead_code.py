#!/usr/bin/env python3
# GREP_SUMMARY: gate dead-code reachability entrypoint-manifest shell-script call-graph source exec glob shebang
# STRUCTURE: ▶ _parse_manifest_edges + _scan_makefile_refs + _scan_precommit_refs → ⊕ seeds
#            → ○ BFS via _find_source_calls → ⟦reachable_set⟧
#            → glob core/**/*.sh → ∖ exceptions → ◇ entrypoints|internal without caller?
#            → ⎋ DEAD_CODE fail
# region MODULE_CONTRACT
## @purpose  Gate test: detect dead shell scripts in core/entrypoints/ and core/internal/.
##           Parses entrypoint-manifest.yaml, Makefile, and .pre-commit-config.yaml to
##           build a call graph via delegates_to + source/./exec calls. Computes the set
##           of reachable scripts vs all shebang files. Fails if any script in
##           core/entrypoints/ or core/internal/ has no live caller.
## @scope    Static audit — no Docker, no network, no runtime deps. Pure filesystem analysis.
## @invariants
##   - entrypoint-manifest.yaml is the canonical source of truth for Makefile→entrypoint edges
##   - Makefile is scanned for direct script references not in manifest (e.g. healthcheck.sh)
##   - .pre-commit-config.yaml is scanned for script references (e.g. lint.sh grepsummary)
##   - source/./exec calls within reachable .sh files define internal call graph edges
##   - Documented exceptions (lib/*.sh, module healthcheck/install, bootstrap/systemd,
##     hermes-agent build/context scripts, core/templates/) are excluded from dead-code check
##   - Any .sh file under core/entrypoints/ or core/internal/ without a live caller is
##     reported as DEAD_CODE — the test fails with a list of dead paths
## @rationale  Dead scripts accumulate confusion during audits, refactoring, and deployment.
##             This gate enforces that every operational script is reachable from the Makefile
##             or from another reachable script. Adding a new entrypoint or internal script
##             without wiring it into the call graph is now a test failure.
## @changes — CREATED: 2026-07-09 | TASK-5G3: dead code detection gate
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re
from collections import deque

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Project paths ──────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)
MANIFEST_PATH: str = os.path.join(PLATFORM_ROOT, "core", "entrypoint-manifest.yaml")
MAKEFILE_PATH: str = os.path.join(PLATFORM_ROOT, "Makefile")
PRECOMMIT_PATH: str = os.path.join(PLATFORM_ROOT, ".pre-commit-config.yaml")

# ── Regex patterns ─────────────────────────────────────────────────────────

# Matches source/./exec calls: source "path", . "path", exec "path"
# NOTE: $ is intentionally included in the capture character class to allow
# ${SCRIPT_DIR}/${PLATFORM_ROOT}/${CORE_DIR} variable references.
# _resolve_source_path filters unresolvable variables.
_SOURCE_EXEC_RE: re.Pattern = re.compile(r'(?:^|\s)(?:source|\.|exec)\s+["\']?([^"\';\s&|()<>`]+)["\']?')

# Matches bash "path.sh" direct execution: bash "${CORE_DIR}/.../script.sh"
_BASH_EXEC_RE: re.Pattern = re.compile(r'bash\s+["\']?([^"\';\s&|()<>`]+\.sh)["\']?')

# Matches /opt/platform/... absolute paths in variable assignments or strings
_PLATFORM_PATH_RE: re.Pattern = re.compile(r'["\'](/opt/platform/[^"\';\s]+\.sh)["\']')

# Matches any .sh path reference in a file (for Makefile, pre-commit, etc.)
_SH_PATH_RE: re.Pattern = re.compile(r"(?:^|\s|[→])([a-zA-Z0-9_./-]+\.sh)")

# Matches core/entrypoints/ or core/internal/ paths in Makefile or text,
# even when prefixed by $(_platform_root)/ or other variable prefixes.
_CORE_SH_PATH_RE: re.Pattern = re.compile(r"(core/(?:entrypoints|internal)/[\w./-]+\.sh)")

# Matches variable-like dotsourcing: source "${VAR}/path/to/script.sh"
_VAR_SOURCE_RE: re.Pattern = re.compile(r'\$\{[A-Z_]+}\/[^"\';\s]+\.sh')

# ── Documented exceptions (not required to have live callers) ──────────────
# These are documented as not requiring callers per TASK-5G3.
_EXCEPTION_PREFIXES: tuple[str, ...] = (
    "core/lib/",  # Libraries — sourced by convention
    "core/modules/",  # Module scripts — called via dynamic iteration
    "core/bootstrap/systemd/",  # systemd unit scripts
    "core/internal/healthcheck/",  # Cron-triggered healthcheck scripts (docker-healthcheck.sh)
    "core/templates/",  # Templates — not executable
)
_EXCEPTION_SUFFIXES: tuple[str, ...] = (
    "healthcheck.sh",  # iterated by healthcheck entrypoint via glob
    "install.sh",  # called from module Makefile
    "ready-check.sh",  # called from compose readiness probe
)
_EXCEPTION_PATHS: tuple[str, ...] = (
    # hermes-agent build/context scripts — called from Dockerfile, not from shell
    "core/modules/hermes-agent/build/scripts/",
    "core/modules/hermes-agent/context/scripts/",
    # generate-catalog.sh — called via variable-based path resolution
    # (${CORE_DIR}/internal/catalog/generate-catalog.sh) from node-lifecycle.sh
    # and deploy-project.sh; static call graph builder cannot resolve ${CORE_DIR}
    "core/internal/catalog/generate-catalog.sh",
)


# region HELPERS


def _rel_path(abs_path: str) -> str:
    """Convert an absolute path to a project-relative path.

    ## @purpose  Normalise paths for comparison and logging.
    ## @io       ⇥ abs_path: str → ⎋ str: relative path from PLATFORM_ROOT
    ## @complexity  O(1)
    """
    if abs_path.startswith(PLATFORM_ROOT):
        return abs_path[len(PLATFORM_ROOT) + 1 :]
    return abs_path


def _is_exception(rel_path: str) -> bool:
    """Check if a relative script path matches any documented exception.

    ## @purpose  Exclude library scripts, module healthcheck/install scripts,
    ##           systemd scripts, and template scripts from dead-code check.
    ## @io       ⇥ rel_path: str relative to PLATFORM_ROOT → ⎋ bool
    ## @complexity  O(P + S + E) where P=prefixes, S=suffixes, E=exact paths
    """
    for prefix in _EXCEPTION_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    for suffix in _EXCEPTION_SUFFIXES:
        if rel_path.endswith(suffix):
            return True
    return any(rel_path.startswith(path) for path in _EXCEPTION_PATHS)


def _resolve_source_path(raw_path: str, script_abs_dir: str, script_rel: str | None = None) -> str | None:
    """Resolve a sourced/exec'd script path to an absolute path within the project.

    ## @purpose  Convert a path found in source/./exec call to absolute path.
    ##           Handles ${SCRIPT_DIR}, ${CORE_DIR}, ${PLATFORM_ROOT} variable
    ##           expansions and plain relative paths.
    ## @io       ⇥ raw_path: str from source/exec call
    ##           ⇥ script_abs_dir: absolute directory of the sourcing script
    ##           ⇥ script_rel: relative path (for context-dependent var resolution)
    ##           ⎋ str | None: resolved absolute path, or None if unresolvable
    ## @complexity  O(1)
    ## @invariants
    ##   - ${SCRIPT_DIR} → script_abs_dir
    ##   - ${CORE_DIR} → PLATFORM_ROOT + /core
    ##   - ${PLATFORM_ROOT} → context-dependent:
    ##     - If script is in core/entrypoints/: PLATFORM_ROOT = parent of entrypoints/ = core/
    ##     - Otherwise: PLATFORM_ROOT = project root
    ##   - Plain relative path → resolved from script_abs_dir
    ##   - Absolute paths outside PLATFORM_ROOT → None (skip)
    ##   - Unresolvable variable references (e.g. ${_HEALTHCHECK_LIB_DIR}) → None
    """
    path = raw_path.strip().strip('"').strip("'")
    if not path:
        return None

    # If it contains a variable we can't resolve, skip
    if "${" in path:
        # Determine context-dependent PLATFORM_ROOT value.
        # In entrypoints (core/entrypoints/), PLATFORM_ROOT = SCRIPT_DIR/.. = core/
        # In other scripts, PLATFORM_ROOT may be the project root or /opt/platform/
        if script_rel and script_rel.startswith("core/entrypoints/"):
            pl_platform_root = os.path.dirname(script_abs_dir)  # .../core/entrypoints/ → .../core/
        else:
            pl_platform_root = PLATFORM_ROOT

        # Known resolvable variables
        known_vars = {
            "${SCRIPT_DIR}": script_abs_dir,
            "${PLATFORM_ROOT}": pl_platform_root,
            "${CORE_DIR}": os.path.join(PLATFORM_ROOT, "core"),
        }
        resolved = False
        for var, replacement in known_vars.items():
            if var in path:
                path = path.replace(var, replacement)
                resolved = True
        if not resolved:
            # Contains unknown variable — can't resolve statically
            return None

    # Make absolute
    if not os.path.isabs(path):
        path = os.path.join(script_abs_dir, path)

    # Normalise
    path = os.path.normpath(path)

    # Must be within the project tree; also try /opt/platform/ → project root
    if not path.startswith(PLATFORM_ROOT):
        # Production paths like /opt/platform/core/... → map to project core/
        prod_prefix = "/opt/platform/"
        if path.startswith(prod_prefix):
            path = os.path.join(PLATFORM_ROOT, path[len(prod_prefix) :])
            path = os.path.normpath(path)
        else:
            return None

    # Must exist and be a file
    if os.path.isfile(path):
        return path

    return None


def _find_source_calls(script_abs_path: str, script_rel: str) -> list[str]:
    """Find all source/./exec/bash calls and platform-path references within a shell script.

    ## @purpose  Read a .sh file and extract all referenced script paths from:
    ##           - source/./exec statements
    ##           - bash "path.sh" direct execution
    ##           - /opt/platform/... absolute paths in strings/variables
    ##           - ${CORE_DIR}/.../script.sh references in bash calls
    ## @io       ⇥ script_abs_path: str, script_rel: str → ⎋ list[str]
    ##           of resolved absolute paths
    ## @complexity  O(L) where L = lines in file
    ## @invariants
    ##   - Returns only paths that resolve to existing files within PLATFORM_ROOT
    ##   - Silently skips unresolvable paths (unknown variables, non-existent)
    ##   - Silently skips if file can't be read
    """
    if not os.path.isfile(script_abs_path):
        return []

    script_dir = os.path.dirname(script_abs_path)
    results: set[str] = set()

    try:
        with open(script_abs_path) as f:
            content = f.read()
    except OSError:
        logger.warning("[IMP:7][_find_source_calls] Cannot read: %s", script_abs_path)
        return []

    # Pattern 1: source/./exec calls
    for match in _SOURCE_EXEC_RE.finditer(content):
        raw_path = match.group(1)
        resolved = _resolve_source_path(raw_path, script_dir, script_rel)
        if resolved:
            results.add(resolved)

    # Pattern 2: bash "path.sh" direct execution
    for match in _BASH_EXEC_RE.finditer(content):
        raw_path = match.group(1)
        resolved = _resolve_source_path(raw_path, script_dir, script_rel)
        if resolved:
            results.add(resolved)

    # Pattern 3: /opt/platform/... paths in strings (variable assignments etc.)
    for match in _PLATFORM_PATH_RE.finditer(content):
        raw_path = match.group(1)
        resolved = _resolve_source_path(raw_path, script_dir, script_rel)
        if resolved:
            results.add(resolved)

    return sorted(results)


def _extract_sh_paths_from_text(text: str) -> set[str]:
    """Extract all .sh script paths from arbitrary text.

    ## @purpose  Parse Makefile rules, pre-commit config entries, or
    ##           delegates_to strings for .sh script references.
    ## @io       ⇥ text: str → ⎋ set[str] of relative paths (e.g. core/entrypoints/deploy.sh)
    ## @complexity  O(N) where N = lines in text
    """
    paths: set[str] = set()
    for match in _SH_PATH_RE.finditer(text):
        candidate = match.group(1).strip()
        # Normalise: strip leading/trailing punctuation
        candidate = candidate.strip("'\".")
        if not candidate:
            continue
        # Must contain at least one / to be a path (not bare filename)
        if "/" not in candidate:
            continue
        # Must end with .sh
        if not candidate.endswith(".sh"):
            continue
        # Must point to a project path
        if candidate.startswith(("core/", "/")):
            paths.add(candidate)
    return paths


def _extract_sh_path_from_segment(segment: str) -> str | None:
    """Extract a .sh path from a delegation segment, stripping trailing arguments.

    ## @purpose  Handles patterns like "build.sh build-platform" or
    ##           "validate.sh --lint" where arguments follow the .sh path.
    ## @io       ⇥ segment: str (e.g. "core/entrypoints/build.sh build-platform")
    ##           ⎋ str | None: the .sh path part, or None if no .sh found
    ## @complexity  O(1)
    """
    tokens = segment.split()
    for token in tokens:
        cleaned = token.strip("'\"")
        if cleaned.endswith(".sh"):
            return cleaned
    return None


def _parse_manifest_edges() -> dict[str, set[str]]:
    """Parse entrypoint-manifest.yaml and extract call edges from delegates_to.

    ## @purpose  Build initial call graph: for each entry, extract all .sh paths
    ##           from delegates_to by splitting on → and filtering for .sh paths.
    ##           Handles paths with trailing arguments (e.g. "build.sh build-platform").
    ## @io       ⎋ dict[str, set[str]]: {caller_sh_path: {callee_sh_paths}}
    ##           where keys are relative paths (e.g. core/entrypoints/deploy.sh)
    ##           and values are sets of relative callee paths.
    ## @complexity  O(E * P) where E = manifest entries, P = paths per delegates_to
    ## @invariants
    ##   - Entry groups without delegates_to → ignored
    ##   - delegates_to strings without any .sh path → ignored
    ##   - Leading path in delegates_to = entrypoint (root), subsequent = internal
    ##   - All paths are stored as relative (core/...)
    """
    edges: dict[str, set[str]] = {}

    if not os.path.isfile(MANIFEST_PATH):
        logger.warning("[IMP:7][_parse_manifest_edges] Manifest not found: %s", MANIFEST_PATH)
        return edges

    with open(MANIFEST_PATH) as f:
        data = yaml.safe_load(f)

    for group_name, entries in data.items():
        if not isinstance(entries, list):
            continue
        if group_name in ("forbidden_directories", "forbidden_scripts", "forbidden_verbs", "allowed_verbs"):
            continue

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delegates_to: str = entry.get("delegates_to", "")
            if not isinstance(delegates_to, str) or not delegates_to.strip():
                continue

            # Split by → to get individual delegation steps
            segments = [s.strip() for s in delegates_to.split("→")]
            # Extract .sh paths from each segment (handles trailing args)
            sh_paths: list[str] = []
            for seg in segments:
                extracted = _extract_sh_path_from_segment(seg)
                if extracted:
                    sh_paths.append(extracted)

            if not sh_paths:
                continue

            # Build edges: each segment calls the next
            for i in range(len(sh_paths) - 1):
                caller = sh_paths[i]
                callee = sh_paths[i + 1]
                edges.setdefault(caller, set()).add(callee)

            # If there's only one .sh path, it's a root with no internal callee
            # from the manifest — the source/exec parsing will find its callees
            if len(sh_paths) == 1:
                edges.setdefault(sh_paths[0], set())

    logger.info(
        "[IMP:8][_parse_manifest_edges] Extracted %d caller(s) from manifest: %s", len(edges), sorted(edges.keys())
    )
    return edges


def _scan_makefile_refs() -> set[str]:
    """Scan Makefile for direct .sh references (entrypoints called outside manifest).

    ## @purpose  Catch scripts like healthcheck.sh that are called from Makefile
    ##           but not tracked as .sh paths in manifest delegates_to.
    ##           Handles both bare paths and paths prefixed by $(_platform_root)/.
    ## @io       ⎋ set[str] of relative script paths
    ## @complexity  O(L) where L = lines in Makefile
    """
    if not os.path.isfile(MAKEFILE_PATH):
        return set()
    with open(MAKEFILE_PATH) as f:
        content = f.read()
    raw_paths = _extract_sh_paths_from_text(content)
    # Also match paths with variable prefix like $(_platform_root)/core/...
    for match in _CORE_SH_PATH_RE.finditer(content):
        raw_paths.add(match.group(1))
    # Filter to only core/ paths (not system paths)
    result: set[str] = set()
    for p in raw_paths:
        if p.startswith("core/") and p.endswith(".sh"):
            result.add(p)
    logger.info("[IMP:8][_scan_makefile_refs] Found %d .sh reference(s) in Makefile: %s", len(result), sorted(result))
    return result


def _scan_precommit_refs() -> set[str]:
    """Scan .pre-commit-config.yaml for direct .sh references.

    ## @purpose  Catch scripts like lint.sh that are called from pre-commit hooks
    ##           but not tracked in manifest delegates_to.
    ## @io       ⎋ set[str] of relative script paths
    ## @complexity  O(L) where L = lines in pre-commit config
    """
    if not os.path.isfile(PRECOMMIT_PATH):
        return set()
    with open(PRECOMMIT_PATH) as f:
        content = f.read()
    raw_paths = _extract_sh_paths_from_text(content)
    result: set[str] = set()
    for p in raw_paths:
        if p.startswith("core/") and p.endswith(".sh"):
            result.add(p)
    logger.info(
        "[IMP:8][_scan_precommit_refs] Found %d .sh reference(s) in pre-commit: %s", len(result), sorted(result)
    )
    return result


def _find_all_shell_scripts() -> set[str]:
    """Glob all shebang (#!) shell scripts under core/.

    ## @purpose  Discover every executable/script .sh file in the core/ tree.
    ##           Only includes files that have a shebang line (#!/...).
    ## @io       ⎋ set[str] of relative paths (e.g. core/entrypoints/deploy.sh)
    ## @complexity  O(N) where N = files in core/**/*.sh
    ## @invariants
    ##   - Only .sh files are considered (not Python, YAML, etc.)
    ##   - Only files with a shebang are included (excludes non-executable data)
    ##   - .sh files without shebang are silently skipped
    """
    core_dir = os.path.join(PLATFORM_ROOT, "core")
    scripts: set[str] = set()
    for entry in pathlib.Path(core_dir).rglob("*.sh"):
        if not entry.is_file():
            continue
        # Check shebang
        try:
            with open(entry) as f:
                first_line = f.readline(128)
        except OSError:
            continue
        if first_line.startswith("#!"):
            rel = str(entry.relative_to(PLATFORM_ROOT))
            scripts.add(rel)
    logger.info("[IMP:8][_find_all_shell_scripts] Found %d shebang .sh file(s) under core/", len(scripts))
    return scripts


def _build_call_graph() -> tuple[set[str], dict[str, set[str]]]:
    """Build the full directed call graph from manifest, Makefile, pre-commit, and source/exec analysis.

    ## @purpose  Combine all sources of call edges into a single graph.
    ##           Starts with manifest edges, then BFS-expands via source/exec
    ##           calls in each reachable script.
    ## @io       ⎋ tuple(seeds: set[str], graph: dict[str, set[str]])
    ##           where seeds = relative paths of root scripts (entrypoints from
    ##           manifest + Makefile + pre-commit), and graph = {caller_rel:
    ##           {callee_rel, ...}}
    ## @complexity  O(V + E + V * L) where V = scripts, E = manifest edges,
    ##              L = average lines per script
    ## @invariants
    ##   - Graph nodes are relative paths (core/entrypoints/..., core/internal/...)
    ##   - Seeds are all scripts referenced by Makefile, manifest (first .sh per
    ##     delegates_to), or pre-commit config
    ##   - Self-loops (script sourcing itself) are excluded
    """
    logger.info("[IMP:8][_build_call_graph] Building call graph from manifest, Makefile, pre-commit...")

    # Step 1: Parse manifest edges
    manifest_edges = _parse_manifest_edges()

    # Step 2: Collect seeds from all sources
    seeds: set[str] = set()
    # From manifest: all caller paths and first .sh in each delegates_to chain
    for caller_rel in manifest_edges:
        seeds.add(caller_rel)
        for callee_rel in manifest_edges[caller_rel]:
            seeds.add(callee_rel)

    # From Makefile
    seeds.update(_scan_makefile_refs())

    # From pre-commit
    seeds.update(_scan_precommit_refs())

    # Step 3: Build full graph by expanding source/exec calls
    graph: dict[str, set[str]] = {s: set() for s in seeds}
    # Also add entries from manifest edges
    for caller, callees in manifest_edges.items():
        graph.setdefault(caller, set()).update(callees)
        for c in callees:
            graph.setdefault(c, set())

    # BFS to expand via source/exec
    visited: set[str] = set()
    queue: deque[str] = deque(seeds)

    while queue:
        script_rel = queue.popleft()
        if script_rel in visited:
            continue
        visited.add(script_rel)

        script_abs = os.path.join(PLATFORM_ROOT, script_rel)
        if not os.path.isfile(script_abs):
            continue

        callees = _find_source_calls(script_abs, script_rel)
        for callee_abs in callees:
            callee_rel = _rel_path(callee_abs)
            graph.setdefault(script_rel, set()).add(callee_rel)
            graph.setdefault(callee_rel, set())
            if callee_rel not in visited:
                queue.append(callee_rel)

    # Step 4: Compute the full reachable set via BFS from seeds
    reachable: set[str] = set()
    bfs_queue: deque[str] = deque(seeds)
    bfs_visited: set[str] = set()

    while bfs_queue:
        node = bfs_queue.popleft()
        if node in bfs_visited:
            continue
        bfs_visited.add(node)
        if os.path.isfile(os.path.join(PLATFORM_ROOT, node)):
            reachable.add(node)
        for callee in graph.get(node, set()):
            if callee not in bfs_visited:
                bfs_queue.append(callee)

    logger.info(
        "[IMP:9][_build_call_graph] Seeds=%d, Graph nodes=%d, Reachable=%d", len(seeds), len(graph), len(reachable)
    )
    return seeds, reachable


def _compute_dead_scripts() -> tuple[list[str], list[str]]:
    """Compute dead scripts in entrypoints/ and internal/.

    ## @purpose  Main analysis function: finds all scripts, computes reachability,
    ##           and returns lists of dead scripts in entrypoints/ and internal/.
    ## @io       ⎋ tuple(dead_entrypoints: list[str], dead_internal: list[str])
    ##           — each list contains relative paths of dead scripts
    ## @complexity  Delegates to _build_call_graph + _find_all_shell_scripts
    ## @invariants
    ##   - Exception scripts are never reported as dead
    ##   - Scripts not on disk are not reported (data was already filtered)
    """
    seeds, reachable = _build_call_graph()
    all_scripts = _find_all_shell_scripts()

    dead_entrypoints: list[str] = []
    dead_internal: list[str] = []

    for script_rel in sorted(all_scripts):
        if _is_exception(script_rel):
            continue

        if script_rel.startswith("core/entrypoints/"):
            if script_rel not in reachable and script_rel not in seeds:
                dead_entrypoints.append(script_rel)

        elif script_rel.startswith("core/internal/") and script_rel not in reachable and script_rel not in seeds:
            dead_internal.append(script_rel)

    logger.info(
        "[IMP:9][_compute_dead_scripts] All shebang scripts=%d, Dead entrypoints=%d, Dead internal=%d",
        len(all_scripts),
        len(dead_entrypoints),
        len(dead_internal),
    )

    return dead_entrypoints, dead_internal


# endregion HELPERS


# ── Session-scoped fixture ─────────────────────────────────────────────────

# region FIXTURES


@pytest.fixture(scope="session")
def _dead_code_analysis() -> tuple[list[str], list[str]]:
    """Session-scoped fixture: run dead code analysis once per session.

    ## @purpose  Caches the analysis result so both test functions
    ##           use the same computed data without re-analysis.
    ## @io       ⎋ tuple(dead_entrypoints, dead_internal) as lists of relative paths
    """
    return _compute_dead_scripts()


# endregion FIXTURES


# ── Gate tests ─────────────────────────────────────────────────────────────

# region FUNC_test_all_internal_scripts_reachable
## @purpose  Verify every shebang script under core/internal/ has at least one live caller.
## @rationale  Prevents orphan internal scripts from accumulating without caller linkage.

# 🧪 TRAP[TEST] · REGRESSION(5G3) · SCENARIO(internal-reachability) · LAST_FAIL(core/internal/validate/lint.sh, core/internal/bootstrap/tls.sh) · REMOVE_IF(dead scripts removed or added to documented exceptions)


@pytest.mark.gate
@ldd_trajectory
def test_all_internal_scripts_reachable(
    _dead_code_analysis: tuple[list[str], list[str]],
    caplog,
) -> None:
    """Verify that every .sh script under core/internal/ has at least one live caller.

    # ▶ _compute_dead_scripts ─◇ dead_internal empty? ─→ PASS
    #                           └→ FAIL: DEAD_CODE: no caller found for {path}

    A script is considered reachable if it is:
    - Referenced in manifest delegates_to, OR
    - Called via source/./exec/bash from another reachable script, OR
    - Referenced in Makefile or .pre-commit-config.yaml

    ## @rationale  Orphan internal scripts cause confusion during audits
    ##             and risk being forgotten when the entrypoint they served
    ##             is migrated or removed. Every internal script must have
    ##             at least one path from the Makefile to it.

    ## @scope  Shebang-files only. Does NOT scan comments for stale references.
    ## ⚠️ TRAP[DEBT] · Future: implement test_gate_stale_comments to scan comments for stale script references

    ## @invariants
    ##   - core/lib/*.sh — libraries sourced by convention, exempt
    ##   - core/modules/*/*.sh — module-local, exempt
    ##   - core/bootstrap/systemd/*.sh — systemd units, exempt
    ##   - core/modules/hermes-agent/build/scripts/*.sh — Dockerfile, exempt
    ##   - core/modules/hermes-agent/context/scripts/*.sh — Dockerfile, exempt
    ##   - core/templates/* — templates, exempt
    ##   - *healthcheck.sh, *install.sh, *ready-check.sh — called dynamically, exempt
    """
    _, dead_internal = _dead_code_analysis

    logger.info(
        "[IMP:7][test_all_internal_scripts_reachable] Checking %d internal script(s) for dead code...",
        len(dead_internal),
    )

    # Print dead code analysis results (IMP:9 logged to caplog)
    if dead_internal:
        logger.warning(
            "[IMP:9][test_all_internal_scripts_reachable] DEAD_CODE: %d internal script(s) without live caller",
            len(dead_internal),
        )
        for dead_path in dead_internal:
            print(f"  DEAD_CODE: no caller found for {dead_path}")
    else:
        logger.info("[IMP:9][test_all_internal_scripts_reachable] PASS: all internal scripts are reachable")

    assert not dead_internal, (
        f"[IMP:10][test_all_internal_scripts_reachable] FAIL: "
        f"{len(dead_internal)} internal script(s) have no live caller:\n"
        + "\n".join(f"  DEAD_CODE: no caller found for {p}" for p in dead_internal)
    )


# endregion FUNC_test_all_internal_scripts_reachable


# region FUNC_test_all_entrypoints_have_live_caller
## @purpose  Verify every shebang script under core/entrypoints/ has at least one live caller.
## @rationale  Prevents orphan entrypoints from accumulating without Makefile/pre-commit registration.

# 🧪 TRAP[TEST] · REGRESSION(5G3) · SCENARIO(entrypoint-caller) · LAST_FAIL(no failures) · REMOVE_IF(entrypoints removed from core/entrypoints/)


@pytest.mark.gate
@ldd_trajectory
def test_all_entrypoints_have_live_caller(
    _dead_code_analysis: tuple[list[str], list[str]],
    caplog,
) -> None:
    """Verify that every .sh script under core/entrypoints/ has at least one live caller.

    # ▶ _compute_dead_scripts ─◇ dead_entrypoints empty? ─→ PASS
    #                            └→ FAIL: DEAD_CODE: no caller found for {path}

    Entrypoints must be registered in entrypoint-manifest.yaml,
    referenced in Makefile, or referenced in .pre-commit-config.yaml.

    ## @rationale  Every entrypoint must be reachable from a build/CI/deploy
    ##             action. Orphan entrypoints are dead weight — they occupy
    ##             the namespace but serve no operational purpose.

    ## @invariants
    ##   - Entrypoints registered in manifest delegates_to → caller = Makefile
    ##   - Entrypoints referenced in Makefile but not in manifest → caller = Makefile
    ##   - Entrypoints referenced in .pre-commit-config.yaml → caller = pre-commit
    ##   - Entrypoints sourced by another reachable script → caller = that script
    ##   - All other entrypoints are DEAD_CODE and cause test failure
    """
    dead_entrypoints, _ = _dead_code_analysis

    logger.info(
        "[IMP:7][test_all_entrypoints_have_live_caller] Checking %d entrypoint(s) for dead code...",
        len(dead_entrypoints),
    )

    # Print dead code analysis results (IMP:9 logged to caplog)
    if dead_entrypoints:
        logger.warning(
            "[IMP:9][test_all_entrypoints_have_live_caller] DEAD_CODE: %d entrypoint(s) without live caller",
            len(dead_entrypoints),
        )
        for dead_path in dead_entrypoints:
            print(f"  DEAD_CODE: no caller found for {dead_path}")
    else:
        logger.info("[IMP:9][test_all_entrypoints_have_live_caller] PASS: all entrypoints have live caller")

    assert not dead_entrypoints, (
        f"[IMP:10][test_all_entrypoints_have_live_caller] FAIL: "
        f"{len(dead_entrypoints)} entrypoint(s) have no live caller:\n"
        + "\n".join(f"  DEAD_CODE: no caller found for {p}" for p in dead_entrypoints)
    )


# endregion FUNC_test_all_entrypoints_have_live_caller
