# GREP_SUMMARY: gate context-overlay-git delivery-invariants D1 D2 D3 ensure_context_repo rsync-exclude-git scp-deliver core_deliverer
# STRUCTURE: ▶ test_git_only_in_ensure_context_repo → grep deploy-modules.sh for git clone/pull outside ensure_context_repo() → ◇ test_core_rsync_excludes_git → assert RSYNC_EXCLUDES_{CORE,NODE,SECRETS} contain --exclude=.git (core_deliverer.py) + grep scp-deliver.sh delegation + CI workflows rsync → ⊕ delivery invariants coverage
# region MODULE_CONTRACT
## @purpose  Gate tests for delivery invariants D1/D2/D3:
##           - D1: Core-код NEVER доставляется через git
##           - D2: context-overlay использует git только внутри ensure_context_repo()
##           - D3: AGE-ключи/secrets/SSH-keys никогда не передаются через git
## @scope    Static checks: (a) RSYNC_EXCLUDES_CORE/NODE/SECRETS в core_deliverer.py —
##           единый источник правды excludes после DevPlan 108 (rsync ушёл из shell);
##           (b) git-сканирование deploy-modules.sh; (c) rsync-сканирование CI workflows
##           (core-deploy.yml) + shell-фасадов (defense-in-depth).
## @invariants
##   - Все git clone/pull в deploy-modules.sh — только внутри ensure_context_repo()
##   - RSYNC_EXCLUDES_CORE, RSYNC_EXCLUDES_NODE, RSYNC_EXCLUDES_SECRETS содержат --exclude=.git
##   - scp-deliver.sh фасад делегирует в core_deliverer (excludes достижимы в delivery path)
##   - Все rsync core/ вызовы в CI delivery chain — содержат --exclude '.git/'
## @rationale  D1/D2 — критическая модель доставки: core push (SCP/rsync, без git),
##             context pull (git только через ensure_context_repo). Без gate-теста
##             дрейф модели доставки недетектируем на этапе CI.
## @changes — 2026-07-18 | Created per DevPlan 011 T7 · $TEST_SPEC
## @changes — 2026-07-31 | DevPlan 108: test_core_rsync_excludes_git больше НЕ vacuous —
##            проверяет RSYNC_EXCLUDES_* в core_deliverer.py (0 rsync в shell-фасаде)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from core.internal.bootstrap.core_deliverer import RSYNC_EXCLUDES_CORE, RSYNC_EXCLUDES_NODE, RSYNC_EXCLUDES_SECRETS
from tests._conftest.ldd import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# Delivery scripts to scan for git calls
_DELIVERY_GIT_SCRIPTS = [
    repo_root() / "core" / "internal" / "bootstrap" / "deploy-modules.sh",
]

# Delivery scripts to scan for rsync --exclude '.git/' (defense-in-depth — rsync moved
# to core_deliverer.py in DevPlan 108, but a reintroduced shell rsync must still exclude .git)
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
        lines = script_path.read_text(encoding="utf-8", errors="replace").split("\n")
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
            violations.append({
                "line": stripped,
                "lineno": lineno,
                "in_ensure_context_repo": inside_ensure_context_repo,
                "command": match.group(0),
            })

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
        lines = script_path.read_text(encoding="utf-8", errors="replace").split("\n")
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
        violations.append({
            "line": full_cmd[:200],
            "lineno": start_lineno,
            "has_exclude_git": False,
        })


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
        lines = workflow_path.read_text(encoding="utf-8", errors="replace").split("\n")
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
        violations.append({
            "line": full_block[:200],
            "lineno": start_lineno,
            "has_exclude_git": False,
        })


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
    # (allowlist _ALLOWED_GIT_FILES упразднён DevPlan 171 W3.7 — строгий «0 git в *.sh»;
    # легитимные git-вызовы мигрированы в Python: install_acme.py)
    bootstrap_dir = repo_root() / "core" / "internal" / "bootstrap"
    if bootstrap_dir.exists():
        for script_file in sorted(bootstrap_dir.glob("*.sh")):
            if script_file.name in {s.name for s in _DELIVERY_GIT_SCRIPTS}:
                continue  # Already scanned

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
    """Verify core/ rsync delivery excludes .git — in core_deliverer.py (real source of truth).

    ## @purpose — Validate D1: Core-код NEVER доставляется через git.
    ##            DevPlan 108: rsync-исключения живут в core_deliverer.py
    ##            (RSYNC_EXCLUDES_CORE/NODE/SECRETS). Этот тест проверяет константы
    ##            НАПРЯМУЮ — без этого gate был VACUOUS PASS (0 rsync в shell-фасаде =
    ##            0 нарушений = потеря покрытия). Дополнительно: scp-deliver.sh фасад
    ##            должен делегировать в core_deliverer, и CI workflows rsync core/
    ##            обязаны содержать --exclude '.git/'.
    ## @io — ⎋ None (asserts excludes in Python constants + CI workflows)
    ## @complexity — O(L) where L = sum of lines in delivery scripts + CI workflows
    """
    # 🧪 TRAP[TEST] · 2026-07-31 · D1 delivery invariant: .git excluded in ALL rsync phases
    # · Regression: removal of --exclude=.git from any phase delivers the .git dir to VPS
    # ·   (repo metadata, hooks, potentially secrets in history) — defense-in-depth
    # · Scenario: assert RSYNC_EXCLUDES_{CORE,NODE,SECRETS} contain --exclude=.git
    # · Last fail: 2026-07-31 — VACUOUS PASS (0 rsync в scp-deliver.sh после DevPlan 108)
    # · Remove if: exclude lists intentionally drop .git (needs Architect approval)
    total_violations = 0
    total_scanned = 0

    # ── core_deliverer.py: RSYNC_EXCLUDES_* (single source of truth, DevPlan 108) ──
    # Real coverage: FAILs if --exclude=.git is removed from any delivery phase list.
    for name, excludes in [
        ("RSYNC_EXCLUDES_CORE", RSYNC_EXCLUDES_CORE),
        ("RSYNC_EXCLUDES_NODE", RSYNC_EXCLUDES_NODE),
        ("RSYNC_EXCLUDES_SECRETS", RSYNC_EXCLUDES_SECRETS),
    ]:
        has_git_exclude = "--exclude=.git" in excludes
        logger.info(
            "[IMP:9][gate][context_overlay_git] %s contains --exclude=.git: %s (%d excludes)",
            name,
            has_git_exclude,
            len(excludes),
        )
        total_scanned += 1
        if not has_git_exclude:
            logger.error("[IMP:9][gate][context_overlay_git] FAIL: %s missing --exclude=.git", name)
            total_violations += 1

    # ── scp-deliver.sh facade → core_deliverer delegation (excludes reachable in path) ──
    facade_path = repo_root() / "core" / "internal" / "bootstrap" / "scp-deliver.sh"
    if facade_path.exists():
        facade_content = facade_path.read_text(errors="replace")
        has_delegation = "core_deliverer" in facade_content and "deliver" in facade_content
        logger.info(
            "[IMP:9][gate][context_overlay_git] scp-deliver.sh delegates to core_deliverer deliver: %s",
            has_delegation,
        )
        total_scanned += 1
        if not has_delegation:
            logger.error(
                "[IMP:9][gate][context_overlay_git] FAIL: scp-deliver.sh no longer delegates to core_deliverer — "
                "the RSYNC_EXCLUDES_* .git exclusions would be unreachable in the delivery path"
            )
            total_violations += 1

    # ── Scan delivery shell scripts (defense-in-depth: reintroduced shell rsync) ─────
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
        f"[IMP:9][gate][context_overlay_git] FAIL: {total_violations} violation(s) "
        f"(.git exclusion missing) across {total_scanned} checked item(s)"
    )

    logger.info("[IMP:9][gate][context_overlay_git] PASS: .git excluded in all rsync phases (Python + CI)")
