# GREP_SUMMARY: gate anti-drift entrypoint-manifest shebang-validation makefile-targets forbidden-scripts unregistered CI TASK-5G1
# STRUCTURE: ▶ Load manifest → ⊕ manifest_paths ∪ exception_patterns ⚡ glob(core/**/*.sh) → ○ for each shebang: ◇ in_exception? → ⊗ in_manifest? → ◇ forbidden_name? → ⎋ PASS|FAIL-fast ‖ ▷ glob(core/modules/*/Makefile) → extract real targets → ○ each: ◇ in_allowed_verbs? → ◇ lifecycle_exception? → ⎋ PASS|FAIL-fast ‖ ▷ forbidden_names → glob verify absent → ⎋ PASS|FAIL

# region MODULE_CONTRACT
## @purpose  Anti-drift CI gate (TASK-5G1): validates that every shebang script under core/
##            is registered in core/entrypoint-manifest.yaml or is a documented exception;
##            every module Makefile target uses an allowed verb; and forbidden script names
##            do not exist anywhere in the project.
## @scope    All .sh shebang files under core/ (excluding node_modules, .venv, __pycache__),
##           all core/modules/*/Makefile targets, and forbidden script name declarations.
## @invariants
##   - Every shebang script in core/entrypoints/ and core/internal/ must appear in manifest delegates_to
##   - core/lib/*.sh — documented exception (library files, not entrypoints)
##   - core/modules/*/healthcheck.sh — documented exception (module healthchecks)
##   - core/modules/*/install.sh — documented exception (module installers)
##   - core/bootstrap/systemd/*.sh — documented exception (system service files)
##   - core/modules/hermes-agent/build/scripts/*.sh — documented exception (s6 overlay)
##   - core/modules/hermes-agent/context/scripts/*.sh — documented exception (context init)
##   - Every module Makefile target must be in allowed_verbs or lifecycle exceptions (start, stop, restart, status, logs)
##   - Forbidden script names must not exist as files under core/
##   - Fail-fast on first violation — pytest.fail() with diagnostic message
## @rationale Prevents drift between the canonical operations registry
##            (entrypoint-manifest.yaml) and the actual filesystem. Without this gate,
##            new entrypoints or Makefile targets can be created without updating the
##            manifest, breaking CI gates that rely on manifest parity.
##            The fail-fast approach ensures the FIRST unregistered item is fixed,
##            not buried in a long error list (TASK-5G1).
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import re

import pytest
import yaml

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths relative to project root ──
_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
_MANIFEST_PATH: pathlib.Path = _PROJECT_ROOT / "core" / "entrypoint-manifest.yaml"
_CORE_DIR: pathlib.Path = _PROJECT_ROOT / "core"

# Documented exception glob patterns (shebang scripts that do NOT need manifest registration)
# @rationale  Scripts in these locations are not canonical entrypoints. They are sourced
#             by entrypoints, not invoked directly from the Makefile or CI pipeline.
_SHEBANG_EXCEPTION_PATTERNS: list[str] = [
    "core/lib/*.sh",
    "core/modules/*/healthcheck.sh",
    "core/modules/*/hooks/*.sh",
    "core/modules/*/install.sh",
    "core/modules/*/ready-check.sh",
    "core/modules/*/scripts/*.sh",
    "core/modules/*/config/*.sh",
    "core/modules/*/config/*/*.sh",
    "core/modules/*/watchdog/*.sh",
    "core/bootstrap/systemd/*.sh",
    "core/internal/healthcheck/*.sh",
    # pre-commit hooks — invoked by pre-commit, not Makefile/CI (DevPlan 028 W1-E7)
    "core/internal/hooks/*.sh",
    "core/modules/hermes-agent/build/scripts/*.sh",
    "core/modules/hermes-agent/context/scripts/*.sh",
    # ssl-provision.sh DELETED (Dead Code Sweep 084) — backward-compat wrapper, no callers
    # module hook scripts — called from deploy-project.sh _trigger_deploy_hooks via module.yaml hooks: section
    "core/modules/nginx/nginx_reload_hook.sh",
    # SSH forced-command entrypoint on VPS — not called from Makefile directly
    "core/entrypoints/deploy.sh",
    # S3 SSL cache (DevPlan 024) — sourced dynamically in node-lifecycle.sh,
    # not a canonical entrypoint. Registered in manifest when 024 is integrated.
    "core/internal/bootstrap/s3-ssl-cache.sh",
    # Stub-project reconciler (DevPlan 025) — sourced from converge.sh --reconcile
    # and node-lifecycle.sh AUTO_RECONCILE. Not a canonical entrypoint per fusion S7.
    "core/internal/deploy/reconcile-projects.sh",
]

# Subdirectory names to exclude when globbing for shebang files.
_EXCLUDE_DIRS: set[str] = {"node_modules", ".venv", "__pycache__"}

# Module lifecycle targets are now read from manifest `module_lifecycle` section.
# See _load_module_lifecycle() below.
# This replaces the old _MAKEFILE_LIFECYCLE_EXCEPTIONS hardcoded set (G1.2).


# region HELPERS


def _extract_manifest_script_paths(manifest: dict) -> set[str]:
    """Extract all ``core/…file.sh`` paths from ``delegates_to`` fields in the manifest.

    ## @purpose  Parse the YAML manifest and collect every registered script path
    ##            that appears in any ``delegates_to`` string. Paths may be chained
    ##            with "→" (e.g. "A.sh → B.sh → C.sh").
    ## @io        ⇥ manifest: dict parsed from entrypoint-manifest.yaml
    ##            ⎋ set[str] of unique relative script paths
    ## @complexity  O(N × M) where N = manifest group count, M = paths per delegates_to
    ## @invariants
    ##   - Skips non-list groups (forbidden_directories, forbidden_verbs, forbidden_scripts, allowed_verbs)
    ##   - Paths match the regex ``core/[\\w./-]+\\.sh``
    ##   - Relative paths are stored as-is (e.g. ``core/entrypoints/deploy.sh``)
    ##   - Duplicates are collapsed by the set
    """
    paths: set[str] = set()
    path_re = re.compile(r"core/[\w./-]+\.sh")

    for group, entries in manifest.items():
        if not isinstance(entries, list):
            continue
        if group in ("forbidden_directories", "forbidden_verbs", "forbidden_scripts", "allowed_verbs"):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            delegates_to = entry.get("delegates_to", "")
            if not isinstance(delegates_to, str):
                continue
            for match in path_re.finditer(delegates_to):
                paths.add(match.group())

    logger.info("[IMP:8][GATE1][extract] Extracted %d script paths from manifest", len(paths))
    return paths


def _is_shebang_file(filepath: str | os.PathLike) -> bool:
    """Check whether *filepath* starts with a Unix shebang (``#!``).

    ## @io    ⇥ filepath → ⎋ bool
    ## @complexity  O(1) — reads only the first byte
    ## @invariants
    ##   - OSError (permission, binary decode) → returns False
    """
    try:
        with open(filepath, "rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def _match_shebang_exception(rel_path: str) -> bool:
    """Check whether *rel_path* matches any documented shebang exception pattern.

    ## @purpose  Determines if a script path is exempt from manifest registration.
    ##            Uses pathlib.PurePath.match() for glob-style matching.
    ## @io        ⇥ rel_path: str  (relative to project root, e.g. ``core/lib/logging.sh``)
    ##            ⎋ bool
    ## @complexity  O(P) where P = len(_SHEBANG_EXCEPTION_PATTERNS)
    """
    pp = pathlib.PurePath(rel_path)
    return any(pp.match(pattern) for pattern in _SHEBANG_EXCEPTION_PATTERNS)


def _extract_makefile_targets(makefile_path: str) -> list[str]:
    """Extract declared (non-`.PHONY`, non-variable) targets from a Makefile.

    ## @purpose  Parse a Makefile and return all real target names, skipping
    ##            `.PHONY` declarations, variable assignments (``:=``, ``=``, ``?=``,
    ##            ``+=``), and bare ALL_CAPS variable names.
    ## @io        ⇥ makefile_path: str → ⎋ list[str] of target names
    ## @complexity  O(L) where L = number of lines in the Makefile
    ## @invariants
    ##   - Lines starting with ``#``, ``.``, or ``include`` are ignored
    ##   - Variable assignments (rest starts with ``=``, ``:=``, ``?=``, ``+=``) are skipped
    ##   - Bare ALL_CAPS names without dependencies are skipped (variable stubs)
    ##   - Target aliases (``target: dependency ## desc``) are captured
    """
    targets: list[str] = []
    target_re = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9_-]*)\s*:\s*(.*)$")

    with open(makefile_path) as f:
        for raw_line in f:
            line = raw_line.strip()

            # Skip empty, comment, directive, and include lines
            if not line or line.startswith(("#", ".", "include")):
                continue

            m = target_re.match(line)
            if not m:
                continue

            name = m.group(1)
            rest = m.group(2).strip()

            # Skip Make variable assignments: VAR := val, VAR = val, VAR ?= val, VAR += val
            if rest and re.match(r"^[:?+]?=", rest):
                continue

            # Skip bare ALL_CAPS names (potentially a variable stub)
            if re.match(r"^[A-Z_]+$", name) and not rest:
                continue

            targets.append(name)

    return targets


def _load_module_lifecycle() -> set[str]:
    """Read module_lifecycle targets from the manifest YAML.

    ## @purpose — Load the single source of truth for module lifecycle targets
    ##            from core/entrypoint-manifest.yaml. Replaces the old
    ##            _MAKEFILE_LIFECYCLE_EXCEPTIONS hardcoded set (G1.2).
    ## @io — ⎋ set[str] of lifecycle target names
    ## @complexity — O(1) — single file read + YAML parse
    ## @invariants
    ##   - Returns empty set if section is missing or malformed
    ##   - All module lifecycle targets are defined in a flat list under module_lifecycle
    """
    with open(_MANIFEST_PATH) as f:
        manifest = yaml.safe_load(f)
    targets: list[str] = manifest.get("module_lifecycle", [])
    result: set[str] = set(targets)
    logger.info("[IMP:8][_load_module_lifecycle] Loaded %d targets from manifest module_lifecycle", len(result))
    return result


def _collect_sh_files() -> list[str]:
    """Return sorted list of ``.sh`` file paths under ``core/``,
    excluding ``node_modules/``, ``.venv/``, and ``__pycache__/``.

    ## @io        ⎋ list[str] — paths relative to _PROJECT_ROOT
    ## @complexity  O(F) where F = number of .sh files on disk
    """
    result: list[str] = []
    for sh_path in sorted(_CORE_DIR.rglob("*.sh")):
        rel = os.path.relpath(str(sh_path), str(_PROJECT_ROOT))
        parts = pathlib.PurePath(rel).parts
        skip = False
        for part in parts:
            if part in _EXCLUDE_DIRS:
                skip = True
                break
        if not skip:
            result.append(rel)
    return result


# endregion HELPERS


# region TEST_ALL_SHEBANG_FILES_IN_MANIFEST
## @purpose  Verify every shebang script under core/ is registered in entrypoint-manifest.yaml
##           or matches a documented exception pattern, or has a basename listed in forbidden_scripts.
## @rationale  Prevents new ad-hoc scripts from being added to the codebase without updating
##             the canonical operations registry. The manifest is the single source of truth
##             for what runs on the platform.


# 🧪 TRAP[TEST] · REGRESSION(GATE-5G1) · SCENARIO(shebang-manifest-parity) · LAST_FAIL(unregistered entrypoint) · REMOVE_IF(all shebangs registered)
@pytest.mark.gate
@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Gate invariant — first line of defense against drift in platform contracts
# · Last fail: N/A (preventive)
# · Remove if: entire gate category is superseded by a newer mechanism
def test_all_shebang_files_in_manifest(caplog) -> None:
    """Core check: every shebang file must be in manifest delegates_to or
    be a documented exception (lib/, module healthcheck/install, bootstrap/systemd/, s6 scripts)."""

    # 1. Load manifest
    assert _MANIFEST_PATH.is_file(), f"Manifest not found: {_MANIFEST_PATH}"
    with open(_MANIFEST_PATH) as f:
        manifest = yaml.safe_load(f)
    logger.info("[IMP:8][GATE1][shebang] Loaded manifest: %s", _MANIFEST_PATH)

    registered_paths = _extract_manifest_script_paths(manifest)
    forbidden_names: set[str] = set(manifest.get("forbidden_scripts", []))
    logger.info(
        "[IMP:8][GATE1][shebang] %d registered paths, %d forbidden names", len(registered_paths), len(forbidden_names)
    )

    # 2. Collect all .sh files
    all_sh = _collect_sh_files()
    logger.info("[IMP:8][GATE1][shebang] Found %d .sh files under core/", len(all_sh))

    # 3. Validate each shebang file
    n_ok: int = 0
    n_exception: int = 0
    n_forbidden: int = 0

    logger.info("[IMP:9][GATE1][shebang] Starting per-file validation — first FAIL stops")

    for rel_path in all_sh:
        abs_path = _PROJECT_ROOT / rel_path
        if not _is_shebang_file(abs_path):
            logger.info("[IMP:7][GATE1][shebang] Skip non-shebang: %s", rel_path)
            continue

        # (a) Check documented exception patterns
        if _match_shebang_exception(rel_path):
            logger.info("[IMP:7][GATE1][shebang] Exception: %s", rel_path)
            n_exception += 1
            continue

        # (b) Check if basename is in forbidden_scripts (known-deprecated, handled by test 3)
        basename = pathlib.PurePath(rel_path).name
        if basename in forbidden_names:
            logger.info("[IMP:7][GATE1][shebang] Forbidden name (known-deprecated): %s", rel_path)
            n_forbidden += 1
            continue

        # (c) Check if path is registered in manifest delegates_to
        if rel_path in registered_paths:
            logger.info("[IMP:7][GATE1][shebang] Registered: %s", rel_path)
            n_ok += 1
            continue

        # (d) Fail-fast — first unregistered script
        logger.error("[IMP:9][GATE1][shebang] FAIL: Unregistered script '%s'", rel_path)
        _print_registration_summary(registered_paths)
        pytest.fail(
            f"Unregistered shebang script detected: '{rel_path}'\n"
            f"\n"
            f"This script exists on disk but is NOT listed in:\n"
            f"  - manifest delegates_to paths\n"
            f"  - documented exception patterns\n\n"
            f"Required action:\n"
            f"  1. Add '{rel_path}' to core/entrypoint-manifest.yaml as a delegates_to\n"
            f"     for the appropriate make_target, OR\n"
            f"  2. If this script is a library/util, add it to _SHEBANG_EXCEPTION_PATTERNS\n"
            f"     in this test file.\n"
            f"\n"
            f"Fail-fast: fix this script first, then re-run to find others."
        )

    logger.info(
        "[IMP:9][GATE1][shebang] ALL PASS: %d registered, %d exceptions, %d forbidden-name",
        n_ok,
        n_exception,
        n_forbidden,
    )


def _print_registration_summary(registered_paths: set[str]) -> None:
    """Print diagnostic summary of registered manifest paths on test failure."""
    print("\n--- Registered manifest delegates_to paths ---", flush=True)
    for p in sorted(registered_paths):
        print(f"  {p}", flush=True)
    print(f"  ({len(registered_paths)} total)", flush=True)
    print("--- Exception patterns ---", flush=True)
    for p in _SHEBANG_EXCEPTION_PATTERNS:
        print(f"  {p}", flush=True)
    print("--- End ---", flush=True)


# endregion TEST_ALL_SHEBANG_FILES_IN_MANIFEST


# region TEST_ALL_MAKEFILE_TARGETS_IN_ALLOWED_VERBS
## @purpose  Verify every target declared in a module Makefile uses an allowed verb
##           from the manifest or is a documented lifecycle exception.
## @rationale  Makefiles must not introduce ad-hoc verbs that bypass the canonical
##             operations dictionary. Target → verb consistency ensures all operations
##             are traceable in the manifest and CI gates.


# 🧪 TRAP[TEST] · REGRESSION(GATE-5G1) · SCENARIO(makefile-verb-parity) · LAST_FAIL(unregistered make target) · REMOVE_IF(all targets registered in allowed_verbs)
@pytest.mark.gate
@ldd_trajectory
def test_all_makefile_targets_in_allowed_verbs(caplog) -> None:
    """Validate every module Makefile target against manifest allowed_verbs + lifecycle exceptions."""

    # 1. Load allowed_verbs from manifest
    with open(_MANIFEST_PATH) as f:
        manifest = yaml.safe_load(f)

    allowed_verbs: set[str] = set(manifest.get("allowed_verbs", []))
    module_lifecycle: set[str] = _load_module_lifecycle()
    all_allowed: set[str] = allowed_verbs | module_lifecycle

    logger.info(
        "[IMP:8][GATE1][makefile] Loaded %d allowed_verbs + %d module_lifecycle = %d total",
        len(allowed_verbs),
        len(module_lifecycle),
        len(all_allowed),
    )

    # 2. Glob module Makefiles
    makefiles = sorted(_PROJECT_ROOT.glob("core/modules/*/Makefile"))
    logger.info("[IMP:8][GATE1][makefile] Found %d module Makefiles", len(makefiles))

    logger.info("[IMP:9][GATE1][makefile] Starting per-Makefile target validation — first FAIL stops")

    for mf in makefiles:
        targets = _extract_makefile_targets(str(mf))
        rel_mf = os.path.relpath(str(mf), str(_PROJECT_ROOT))

        if not targets:
            logger.info("[IMP:7][GATE1][makefile] %s: no (non-.PHONY) targets", rel_mf)
            continue

        logger.info("[IMP:7][GATE1][makefile] %s: targets %s", rel_mf, targets)

        for tgt in targets:
            if tgt in all_allowed:
                logger.info("[IMP:7][GATE1][makefile] OK: %-20s → %s", tgt, rel_mf)
            else:
                logger.error("[IMP:9][GATE1][makefile] FAIL: unregistered target '%s' in %s", tgt, rel_mf)
                _print_target_summary(tgt, allowed_verbs, module_lifecycle, rel_mf)
                pytest.fail(
                    f"Unregistered Makefile target: '{tgt}'\n"
                    f"  Makefile: {rel_mf}\n"
                    f"\n"
                    f"Target '{tgt}' is not in:\n"
                    f"  - manifest allowed_verbs ({len(allowed_verbs)} verbs)\n"
                    f"  - manifest module_lifecycle ({len(module_lifecycle)} targets)\n"
                    f"\n"
                    f"Required action:\n"
                    f"  1. Rename the target to an allowed verb, OR\n"
                    f"  2. Add '{tgt}' to allowed_verbs in entrypoint-manifest.yaml, OR\n"
                    f"  3. Add '{tgt}' to module_lifecycle in entrypoint-manifest.yaml "
                    f"(only for module lifecycle targets).\n"
                    f"\n"
                    f"Fail-fast: fix this target first, then re-run."
                )

    logger.info("[IMP:9][GATE1][makefile] ALL PASS: %d Makefiles checked", len(makefiles))


def _print_target_summary(tgt: str, allowed_verbs: set[str], module_lifecycle: set[str], makefile_rel: str) -> None:
    """Print diagnostic summary on Makefile target failure."""
    print(f"\n--- Target '{tgt}' from {makefile_rel} is unregistered ---", flush=True)
    print("  Allowed verbs:", sorted(allowed_verbs), flush=True)
    print("  Module lifecycle:", sorted(module_lifecycle), flush=True)
    print("  Combined allowed:", sorted(allowed_verbs | module_lifecycle), flush=True)
    print("--- End ---", flush=True)


# endregion TEST_ALL_MAKEFILE_TARGETS_IN_ALLOWED_VERBS


# region TEST_FORBIDDEN_SCRIPTS_ABSENT
## @purpose  Verify that none of the forbidden script names (from manifest) exist
##           as files anywhere under core/. These scripts are deprecated or removed
##           and must not reappear.
## @rationale  Forbidden scripts (dev.sh, platform-push.sh, apply.sh, etc.) were removed
##             in Phase 2 refactoring. Their reappearance would indicate that old patterns
##             are being reintroduced.


# 🧪 TRAP[TEST] · REGRESSION(GATE-5G1) · SCENARIO(forbidden-scripts-absent) · LAST_FAIL(N/A) · REMOVE_IF(no forbidden names on disk)
@pytest.mark.gate
@ldd_trajectory
def test_forbidden_scripts_absent(caplog) -> None:
    """Verify no forbidden script name (from manifest) exists as a file under core/."""

    # 1. Load forbidden scripts from manifest
    with open(_MANIFEST_PATH) as f:
        manifest = yaml.safe_load(f)

    forbidden_script_names: list[str] = manifest.get("forbidden_scripts", [])
    logger.info(
        "[IMP:8][GATE1][forbidden] Checking %d forbidden names: %s", len(forbidden_script_names), forbidden_script_names
    )

    if not forbidden_script_names:
        logger.info("[IMP:7][GATE1][forbidden] No forbidden_scripts declared in manifest — skip")
        return

    # 2. Collect all .sh files
    all_sh = _collect_sh_files()

    # 3. Build lookup: basename → [list of paths]
    #    Skip paths that match documented exception patterns (core/lib/, core/modules/*/healthcheck.sh, etc.).
    forbidden_map: dict[str, list[str]] = {}
    for rel_path in all_sh:
        if _match_shebang_exception(rel_path):
            logger.info("[IMP:7][GATE1][forbidden] Exception pattern match — skip '%s' from forbidden check", rel_path)
            continue
        basename = pathlib.PurePath(rel_path).name
        if basename in forbidden_script_names:
            forbidden_map.setdefault(basename, []).append(rel_path)

    # 4. Fail-fast on first match
    logger.info("[IMP:9][GATE1][forbidden] Scanning for forbidden scripts — first hit stops")
    for name in sorted(forbidden_script_names):
        locations = forbidden_map.get(name, [])
        if locations:
            detail = "; ".join(locations)
            logger.error("[IMP:9][GATE1][forbidden] FAIL: Found '%s' at %s", name, detail)
            pytest.fail(
                f"Forbidden script '{name}' exists on disk at: {detail}\n"
                f"\n"
                f"This script name is listed in manifest.forbidden_scripts and must NOT exist.\n"
                f"These scripts were deprecated/removed in Phase 2 refactoring:\n"
                f"  - dev.sh → functionality integrated in Makefile\n"
                f"  - platform-push.sh → replaced by CI workflow\n"
                f"  - apply.sh → replaced by make deploy / make context-promote\n"
                f"  - bare-metal-reset.sh → orphaned, 0 callers\n"
                f"  - prepare-bare-server.sh → orphaned\n"
                f"  - stage-manager.sh → orphaned\n"
                f"  - image-prewarm.sh → orphaned\n"
                f"\n"
                f"Required action: remove or rename the file.\n"
                f"Fail-fast: fix the first hit, then re-run."
            )

    logger.info("[IMP:9][GATE1][forbidden] ALL PASS: No forbidden scripts found")


# endregion TEST_FORBIDDEN_SCRIPTS_ABSENT


# region TEST_NO_SSL_PROVISION_EXCEPTION
## @purpose  Verify ssl-provision.sh is NOT in the exception list (Dead Code Sweep 084).
## @rationale  After T2 deletion, the file no longer exists — keeping it as an exception
##             would be dead documentation. This test ensures the exception is removed.

# 🧪 TRAP[TEST] · REGRESSION(084) · SCENARIO(ssl-provision-exception-removed) · LAST_FAIL(N/A) · REMOVE_IF(exception permanently absent)


@pytest.mark.gate
@ldd_trajectory
def test_no_ssl_provision_exception(caplog) -> None:
    """Verify ssl-provision.sh is NOT in _SHEBANG_EXCEPTION_PATTERNS (Dead Code Sweep 084).

    # ▶ grep exception patterns for ssl-provision → ◇ not found? → PASS
    #                                                  └→ FAIL: exception still present
    """
    logger.info("[IMP:8][test_no_ssl_provision_exception] Checking _SHEBANG_EXCEPTION_PATTERNS for ssl-provision.sh...")

    violations: list[str] = []
    for pattern in _SHEBANG_EXCEPTION_PATTERNS:
        if "ssl-provision.sh" in pattern:
            violations.append(pattern)

    if violations:
        logger.error(
            "[IMP:10][test_no_ssl_provision_exception] FAIL: ssl-provision.sh still in exception patterns: %s",
            violations,
        )
        pytest.fail(
            f"ssl-provision.sh is still listed in _SHEBANG_EXCEPTION_PATTERNS: {violations}\n"
            f"The file was deleted in Dead Code Sweep 084 T2 — remove the exception pattern."
        )

    logger.info("[IMP:9][test_no_ssl_provision_exception] PASS: No ssl-provision.sh exception patterns found")


# endregion TEST_NO_SSL_PROVISION_EXCEPTION
