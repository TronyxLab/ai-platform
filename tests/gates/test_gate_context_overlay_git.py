# GREP_SUMMARY: gate context-overlay-git delivery-invariants D1 D2 D3 ensure_context_repo rsync-exclude-git scp-deliver
# STRUCTURE: ▶ test_git_only_in_ensure_context_repo → grep deploy-modules.sh for git clone/pull outside ensure_context_repo() → ◇ test_core_rsync_excludes_git → grep scp-deliver.sh + CI workflows for rsync core/ without --exclude '.git/' → ⊕ delivery invariants coverage
# region MODULE_CONTRACT
## @purpose  Gate tests for delivery invariants D1/D2/D3:
##           - D1: Core-код NEVER доставляется через git
##           - D2: context-overlay использует git только внутри ensure_context_repo()
##           - D3: AGE-ключи/secrets/SSH-keys никогда не передаются через git
## @scope    Static grep-scan of delivery scripts (deploy-modules.sh, scp-deliver.sh)
##           and CI workflows (core-deploy.yml) for git calls outside allowed function
##           and rsync calls without .git exclusion.
## @invariants
##   - Все git clone/pull в deploy-modules.sh — только внутри ensure_context_repo()
##   - Все rsync core/ вызовы в delivery chain — содержат --exclude '.git/'
## @rationale  D1/D2 — критическая модель доставки: core push (SCP/rsync, без git),
##             context pull (git только через ensure_context_repo). Без gate-теста
##             дрейф модели доставки недетектируем на этапе CI.
## @changes — 2026-07-18 | Created per DevPlan 011 T7 · $TEST_SPEC
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# Delivery scripts to scan for git calls
_DELIVERY_GIT_SCRIPTS = [
    repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh",
]

# Delivery scripts to scan for rsync --exclude '.git/'
_DELIVERY_RSYNC_SCRIPTS = [
    repo_root() / "core" / "internal" / "bootstrap" / "scp-deliver.sh",
]

# CI workflow files for rsync verification
_DELIVERY_CI_WORKFLOWS = [
    repo_root() / ".github" / "workflows" / "core-deploy.yml",
]

# Git command patterns — look for git clone, git pull, git fetch
_GIT_CMD_PATTERN = re.compile(r"\bgit\s+(clone|pull|fetch)\b")

# Function boundary pattern — find ensure_context_repo() { ... }
_FUNC_START_PATTERN = re.compile(r"^\s*ensure_context_repo\s*\(\s*\)\s*\{")
_FUNC_END_PATTERN = re.compile(r"^\s*\}")

# rsync patterns — match rsync commands delivering directories (use --delete flag
# as proxy for "delivering a directory tree" where .git exclusion matters)
_RSYNC_DELETE_PATTERN = re.compile(r"\brsync\s+.*--delete\b")

# Exclude pattern — check for --exclude=.git, --exclude '.git/', --exclude '.git'
# Both = and space separators are valid rsync syntax
_EXCLUDE_GIT_PATTERN = re.compile(r"--exclude[=\s]+['\"]?\.git/?['\"]?")

# Allowed git calls outside ensure_context_repo — tool installation, not code delivery
_ALLOWED_GIT_FILES = {
    "install-acme.sh",  # acme.sh tool installation (not platform code)
    "context-init.sh",  # scaffold: new context creation (not delivery)
}

# rsync calls that do NOT need --exclude (single-file deliveries)
# These rsync lines deliver individual files, not directory trees
_RSYNC_SINGLE_FILE_INDICATORS = [
    "platform-env.yaml",
    "Makefile",
    "makefile",
]


# 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Delivery invariant tests missing
# · Symptom: D1/D2 invariants UNVERIFIABLE — no gate coverage for delivery model
# · Root: T7 not implemented in DevPlan 011
# · Test: grep delivery scripts for git outside ensure_context_repo + rsync without --exclude
# · Prevention: make gate MODE=full must fail if delivery scripts drift


# region HELPER_GIT_SCAN
def _scan_git_calls(script_path: Path) -> list[dict]:
    """Scan a shell script for git clone/pull/fetch calls and determine if they
    are inside the ensure_context_repo() function.

    ## @purpose — Find git operations in delivery scripts and check function context
    ## @complexity — O(L) where L = lines in file
    ## @io — ⎋ list[dict] with keys: line, lineno, in_ensure_context_repo
    """
    violations = []
    if not script_path.exists():
        return violations

    try:
        lines = script_path.read_text(errors="replace").split("\n")
    except (OSError, UnicodeDecodeError):
        return violations

    inside_ensure_context_repo = False
    brace_depth = 0

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # Track function boundaries
        if _FUNC_START_PATTERN.search(stripped):
            inside_ensure_context_repo = True
            brace_depth = 1
            continue

        if inside_ensure_context_repo and stripped:
            # Count braces to track function end
            brace_depth += stripped.count("{") - stripped.count("}")
            if "{" in stripped and brace_depth == 0:
                brace_depth += 1  # opening brace on same line as func start
            if brace_depth <= 0 and _FUNC_END_PATTERN.match(stripped):
                inside_ensure_context_repo = False
                continue

        # Check for git commands
        match = _GIT_CMD_PATTERN.search(stripped)
        if match and not stripped.lstrip().startswith("#"):
            violations.append(
                {
                    "line": stripped,
                    "lineno": lineno,
                    "in_ensure_context_repo": inside_ensure_context_repo,
                    "command": match.group(0),
                }
            )

    return violations


# endregion HELPER_GIT_SCAN


# region HELPER_RSYNC_SCAN
def _scan_rsync_excludes(script_path: Path) -> list[dict]:
    """Scan a shell script for rsync calls that deliver directory trees
    (using --delete flag) and check if they include --exclude=.git.

    ## @purpose — Verify rsync commands in delivery chain exclude .git directory.
    ##            Uses --delete flag as heuristic: directory-level syncs need
    ##            --exclude=.git; single-file deliveries don't.
    ## @complexity — O(L) where L = lines in file
    ## @io — ⎋ list[dict] with keys: line, lineno, has_exclude_git
    """
    violations = []
    if not script_path.exists():
        return violations

    try:
        lines = script_path.read_text(errors="replace").split("\n")
    except (OSError, UnicodeDecodeError):
        return violations

    # Track multi-line rsync commands — accumulate lines until command ends
    in_rsync = False
    rsync_buffer = []
    rsync_start_lineno = 0

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.lstrip().startswith("#"):
            continue

        # Detect rsync start (explicit rsync command, not a variable)
        if re.match(r"^\s*(if\s+!)?\s*rsync\s+", stripped):
            in_rsync = True
            rsync_buffer = [stripped]
            rsync_start_lineno = lineno
            # Check if command ends on same line (no line continuation)
            if not stripped.rstrip().endswith("\\"):
                in_rsync = False
                _check_rsync_buffer(rsync_buffer, rsync_start_lineno, violations)
            continue

        if in_rsync:
            rsync_buffer.append(stripped)
            if not stripped.rstrip().endswith("\\"):
                in_rsync = False
                _check_rsync_buffer(rsync_buffer, rsync_start_lineno, violations)

    return violations


def _check_rsync_buffer(buffer: list[str], start_lineno: int, violations: list[dict]) -> None:
    """Check accumulated rsync command: directory syncs need --exclude=.git."""
    full_cmd = " ".join(line.rstrip("\\").strip() for line in buffer)

    # Only check rsync commands that sync directories (indicated by --delete)
    if not _RSYNC_DELETE_PATTERN.search(full_cmd):
        return

    # Skip single-file deliveries (platform-env.yaml, Makefile)
    if any(indicator in full_cmd for indicator in _RSYNC_SINGLE_FILE_INDICATORS):
        return

    has_exclude_git = bool(_EXCLUDE_GIT_PATTERN.search(full_cmd))

    if not has_exclude_git:
        violations.append(
            {
                "line": full_cmd[:200],
                "lineno": start_lineno,
                "has_exclude_git": False,
            }
        )


# endregion HELPER_RSYNC_SCAN


# region HELPER_CI_RSYNC_SCAN
def _scan_ci_rsync(workflow_path: Path) -> list[dict]:
    """Scan a CI workflow YAML for rsync steps with --delete and check
    for --exclude=.git. Handles multi-line YAML run: | blocks.

    ## @purpose — CI workflow level rsync verification
    ## @complexity — O(L) where L = lines in file
    ## @io — ⎋ list[dict] with keys: line, lineno, has_exclude_git
    """
    violations = []
    if not workflow_path.exists():
        return violations

    try:
        lines = workflow_path.read_text(errors="replace").split("\n")
    except (OSError, UnicodeDecodeError):
        return violations

    # Track multi-line run: | blocks — accumulate shell commands
    in_run_block = False
    run_buffer = []
    run_start_lineno = 0
    run_indent = 0

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.lstrip().startswith("#"):
            continue

        # Detect YAML run: | block start
        if re.match(r"^\s*run:\s*\|\s*$", line):
            in_run_block = True
            run_buffer = []
            run_start_lineno = lineno
            run_indent = len(line) - len(line.lstrip()) + 2  # rough indent guess
            continue

        if in_run_block:
            # End of block: empty line or line with less/equal indent
            if not stripped:
                continue  # empty lines inside block are OK
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= run_indent and stripped:
                # Block ended — check accumulated shell commands
                _check_ci_run_block(run_buffer, run_start_lineno, violations)
                in_run_block = False
                # Reprocess this line normally (it might be another run: |)
                continue

            run_buffer.append(stripped)

    # Check last block if file ends inside one
    if in_run_block and run_buffer:
        _check_ci_run_block(run_buffer, run_start_lineno, violations)

    return violations


def _check_ci_run_block(buffer: list[str], start_lineno: int, violations: list[dict]) -> None:
    """Check accumulated CI run: | block for rsync --delete without --exclude=.git."""
    full_block = " ".join(buffer)

    if not _RSYNC_DELETE_PATTERN.search(full_block):
        return

    # Skip single-file deliveries
    if any(indicator in full_block for indicator in _RSYNC_SINGLE_FILE_INDICATORS):
        return

    has_exclude = bool(_EXCLUDE_GIT_PATTERN.search(full_block))
    if not has_exclude:
        violations.append(
            {
                "line": full_block[:200],
                "lineno": start_lineno,
                "has_exclude_git": False,
            }
        )


# endregion HELPER_CI_RSYNC_SCAN


@pytest.mark.gate
@ldd_trajectory
def test_git_only_in_ensure_context_repo(caplog):
    """Verify all git clone/pull commands in deploy-modules.sh are inside ensure_context_repo().

    ## @purpose — Validate D2: context-overlay использует git только
    ##            внутри ensure_context_repo(). Git commands outside this
    ##            function in delivery scripts signal model drift.
    ## @io — ⎋ None (asserts no git calls outside ensure_context_repo)
    ## @complexity — O(L) where L = lines in deploy-modules.sh
    """
    # 🧪 TRAP[TEST] · 2026-07-18 · D2 delivery invariant: git only in ensure_context_repo()
    total_violations = 0
    total_scanned = 0

    for script_path in _DELIVERY_GIT_SCRIPTS:
        if not script_path.exists():
            logger.warning("[IMP:7][gate][context_overlay_git] Script not found: %s — skipping", script_path)
            continue

        violations = _scan_git_calls(script_path)
        total_scanned += 1

        git_calls = [v for v in violations if not v["in_ensure_context_repo"]]
        inside_count = sum(1 for v in violations if v["in_ensure_context_repo"])
        outside_count = len(git_calls)

        logger.info(
            "[IMP:9][gate][context_overlay_git] %s: %d git calls (%d inside ensure_context_repo, %d outside)",
            script_path.name,
            len(violations),
            inside_count,
            outside_count,
        )

        for v in violations:
            loc = "inside" if v["in_ensure_context_repo"] else "OUTSIDE"
            logger.info(
                "[IMP:8][gate][context_overlay_git]   L%d [%s]: %s",
                v["lineno"],
                loc,
                v["line"],
            )

        if git_calls:
            for v in git_calls:
                logger.error(
                    "[IMP:9][gate][context_overlay_git] FAIL: git %s at L%d OUTSIDE ensure_context_repo()",
                    v["command"],
                    v["lineno"],
                )
            total_violations += len(git_calls)

    # Also scan for git calls in other bootstrap scripts (informational)
    bootstrap_dir = repo_root() / "core" / "internal" / "bootstrap"
    if bootstrap_dir.exists():
        for script_file in sorted(bootstrap_dir.glob("*.sh")):
            if script_file.name in {s.name for s in _DELIVERY_GIT_SCRIPTS}:
                continue  # Already scanned
            if script_file.name in _ALLOWED_GIT_FILES:
                continue  # Tool installation, not delivery

            all_violations = _scan_git_calls(script_file)
            outside = [v for v in all_violations if not v["in_ensure_context_repo"]]
            if outside:
                logger.warning(
                    "[IMP:7][gate][context_overlay_git] Git calls found in %s (non-delivery script): %d outside ensure_context_repo",
                    script_file.name,
                    len(outside),
                )
                for v in outside:
                    logger.warning(
                        "[IMP:7][gate][context_overlay_git]   L%d: %s",
                        v["lineno"],
                        v["line"],
                    )

    assert total_violations == 0, (
        f"[IMP:9][gate][context_overlay_git] FAIL: {total_violations} git call(s) outside ensure_context_repo() "
        f"in {total_scanned} delivery script(s)"
    )

    logger.info(
        "[IMP:9][gate][context_overlay_git] PASS: All git calls in delivery scripts are inside ensure_context_repo()"
    )


@pytest.mark.gate
@ldd_trajectory
def test_core_rsync_excludes_git(caplog):
    """Verify rsync calls delivering core/ include --exclude '.git/'.

    ## @purpose — Validate D1: Core-код NEVER доставляется через git.
    ##            Rsync commands delivering core/ or node-configs/ must
    ##            explicitly exclude .git/ for defense-in-depth.
    ## @io — ⎋ None (asserts rsync calls include --exclude '.git/')
    ## @complexity — O(L) where L = sum of lines in delivery scripts + CI workflows
    """
    # 🧪 TRAP[TEST] · 2026-07-18 · D1 delivery invariant: rsync --delete excludes .git
    total_violations = 0
    total_scanned = 0

    # ── Scan delivery shell scripts ──────────────────────────────────────
    for script_path in _DELIVERY_RSYNC_SCRIPTS:
        if not script_path.exists():
            logger.warning("[IMP:7][gate][context_overlay_git] Script not found: %s — skipping", script_path)
            continue

        violations = _scan_rsync_excludes(script_path)
        total_scanned += 1

        total_in_script = len(violations)
        for v in violations:
            logger.error(
                "[IMP:9][gate][context_overlay_git] FAIL: rsync --delete without --exclude=.git at L%d: %s",
                v["lineno"],
                v["line"][:120],
            )
            total_violations += 1

        logger.info(
            "[IMP:9][gate][context_overlay_git] %s: %d rsync --delete calls, %d missing --exclude=.git",
            script_path.name,
            total_in_script,
            total_in_script,
        )

    # ── Scan CI workflows ────────────────────────────────────────────────
    for workflow_path in _DELIVERY_CI_WORKFLOWS:
        if not workflow_path.exists():
            logger.warning("[IMP:7][gate][context_overlay_git] Workflow not found: %s — skipping", workflow_path)
            continue

        violations = _scan_ci_rsync(workflow_path)
        total_scanned += 1

        for v in violations:
            logger.error(
                "[IMP:9][gate][context_overlay_git] FAIL: CI rsync --delete without --exclude=.git at L%d: %s",
                v["lineno"],
                v["line"][:120],
            )
            total_violations += 1

        logger.info(
            "[IMP:9][gate][context_overlay_git] %s: %d rsync --delete calls, %d missing --exclude=.git",
            workflow_path.name,
            len(violations),
            len(violations),
        )

    assert total_violations == 0, (
        f"[IMP:9][gate][context_overlay_git] FAIL: {total_violations} rsync --delete call(s) "
        f"without --exclude=.git in {total_scanned} file(s)"
    )

    logger.info("[IMP:9][gate][context_overlay_git] PASS: All rsync --delete calls include --exclude=.git")
