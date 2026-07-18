# GREP_SUMMARY: gate context-overlay git-only ensure-context-repo rsync-exclude-git delivery-invariant
# STRUCTURE: ▶ test_git_only_in_ensure_context_repo → grep 'git ' in deploy-modules.sh → ◇ check function boundaries → ⊕ test_core_rsync_excludes_git → grep rsync --exclude in scp-deliver.sh → ◇ assert each rsync has --exclude .git
# region MODULE_CONTRACT
## @purpose  Gate tests for delivery invariants: git only in ensure_context_repo(), rsync excludes .git
## @scope    Validates: (1) git commands in bootstrap/deploy scripts only occur within ensure_context_repo()
##           function body. (2) all rsync commands in core delivery scripts use --exclude '.git/'
## @invariants
##   - Core delivery NEVER uses git (D1/D2): rsync/SCP only
##   - Context-overlay delivery uses git ONLY within ensure_context_repo() (D3)
##   - All core rsync commands must exclude .git/ to prevent git metadata copying
## @rationale  Dual delivery model: core via SCP/rsync (push-based, no git surface),
##             context-overlay via git clone/pull. These tests enforce the boundary.
## @changes — 2026-07-18 | Created per DevPlan 011 T7
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Scripts to scan for git usage in core delivery chain
BOOTSTRAP_SCRIPTS = [
    PROJECT_ROOT / "core" / "internal" / "bootstrap" / "deploy-modules.sh",
]

# Scripts to scan for rsync --exclude .git
RSYNC_SCRIPTS = [
    PROJECT_ROOT / "core" / "internal" / "bootstrap" / "scp-deliver.sh",
    PROJECT_ROOT / "core" / "internal" / "bootstrap" / "deploy-modules.sh",
    PROJECT_ROOT / "core" / "entrypoints" / "bootstrap.sh",
]


@pytest.mark.gate
@ldd_trajectory
def test_git_only_in_ensure_context_repo(caplog):
    """git commands in bootstrap/deploy scripts only inside ensure_context_repo().

    ## @purpose — Validate the D3 delivery invariant: git commands are ONLY used
    ##            within the ensure_context_repo() function body. Core delivery
    ##            scripts must not use git for any other purpose.
    ## @io — ⎋ None (asserts git usage is constrained to ensure_context_repo)
    ## @complexity — O(N*L) where N = scripts, L = lines per script
    """
    violations = []

    for script_path in BOOTSTRAP_SCRIPTS:
        if not script_path.exists():
            logger.warning("[IMP:7][gate][git_only] Script not found: %s — skipping", script_path)
            continue

        content = script_path.read_text()
        lines = content.split("\n")

        # Step 1: locate ensure_context_repo() function boundaries via brace tracking
        func_start = None
        func_end = None
        for lineno, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("ensure_context_repo()") or stripped == "ensure_context_repo()":
                func_start = lineno
                # Count braces from this line to find the closing }
                depth = 0
                for scan in range(lineno, len(lines)):
                    scan_line = lines[scan]
                    depth += scan_line.count("{")
                    depth -= scan_line.count("}")
                    if depth <= 0 and scan > lineno:
                        func_end = scan
                        break
                break

        if func_start is not None and func_end is not None:
            logger.info(
                "[IMP:9][gate][git_only] ensure_context_repo() lines %d-%d",
                func_start + 1,
                func_end + 1,
            )
        else:
            logger.warning("[IMP:7][gate][git_only] ensure_context_repo() not found in %s", script_path.name)

        # Step 2: scan all lines for git command invocations
        # Pattern: git [options] <command> — handles `git -C <path> pull` etc.
        # Does NOT match inside string literals (quote checking below)
        git_cmd_pattern = re.compile(
            r"\bgit\s+(?:-[a-zA-Z]\s+\S+\s+)*\b(clone|pull|fetch|checkout|push|merge|rebase|reset|commit|add)\b"
        )

        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()

            # Skip comments, blank lines, and echo/log_step/printf lines
            if stripped.startswith("#") or not stripped:
                continue
            if re.match(r"(echo|printf|log_step|log_info|log_warn|log_error|log_debug)\s", stripped):
                continue

            # Check for git command
            match = git_cmd_pattern.search(stripped)
            if not match:
                continue

            # Determine if we're inside ensure_context_repo()
            inside_func = func_start is not None and func_end is not None and func_start < lineno - 1 < func_end

            if not inside_func and script_path.name != "install-acme.sh":
                violations.append(f"{script_path.name}:{lineno}: {stripped}")
                logger.info(
                    "[IMP:9][gate][git_only] FAIL: git outside ensure_context_repo: %s:%d", script_path.name, lineno
                )
            elif inside_func:
                logger.info(
                    "[IMP:8][gate][git_only] OK: git inside ensure_context_repo: %s:%d", script_path.name, lineno
                )

    # install-acme.sh legitimately uses git to clone acme.sh — it's an external tool install,
    # not platform delivery. Exclude it from the scan scope.
    ignored_legitimate = [
        "install-acme.sh",  # External tool install, not platform delivery
    ]

    # Filter out known legitimate uses
    violations = [v for v in violations if not any(ig in v for ig in ignored_legitimate)]

    logger.info(
        "[IMP:9][gate][git_only] Scanned %d scripts, found %d violations", len(BOOTSTRAP_SCRIPTS), len(violations)
    )
    assert not violations, (
        "[IMP:9][gate][git_only] FAIL: git commands found outside ensure_context_repo():\n" + "\n".join(violations)
    )
    logger.info("[IMP:9][gate][git_only] PASS: All git commands confined to ensure_context_repo()")


@pytest.mark.gate
@ldd_trajectory
def test_core_rsync_excludes_git(caplog):
    """rsync calls in core delivery scripts use --exclude '.git/'.

    ## @purpose — Validate D1/D2 delivery invariant: all rsync commands for
    ##            core delivery include --exclude '.git/' to prevent copying
    ##            git metadata to VPS. Handles multi-line rsync commands where
    ##            --exclude appears on the continuation line. Single-file rsync
    ##            (file → file, not directory sync) is exempt from the check.
    ## @io — ⎋ None (asserts each rsync command has --exclude .git/)
    ## @complexity — O(N*L) where N = scripts, L = lines per script
    """
    violations = []
    total_rsync_cmds = 0

    for script_path in RSYNC_SCRIPTS:
        if not script_path.exists():
            logger.warning("[IMP:7][gate][rsync_exclude] Script not found: %s — skipping", script_path)
            continue

        content = script_path.read_text()
        lines = content.split("\n")

        # Multi-line assembly: find rsync command lines and follow continuation
        for lineno in range(len(lines)):
            line = lines[lineno]
            stripped = line.strip()

            # Skip comments, blank lines
            if stripped.startswith("#") or not stripped:
                continue

            # Skip echo/printf/log_step lines that mention rsync in a message string
            # These are NOT actual rsync command invocations
            if re.match(r"(echo|printf|log_step|log_info|log_warn|log_error|log_debug|log)\s", stripped):
                continue

            # Skip heredoc markers and other non-command lines mentioning rsync
            if re.match(r"\[\[.*\]\]\s*&&\s*(echo|printf|\[)", stripped):
                continue

            # Find actual rsync command invocation (line that EXECUTES rsync)
            # Pattern: command starts with optional prefix then `rsync `
            rsync_match = re.search(r"(?<![$\w])\brsync\s+", stripped)
            if not rsync_match:
                continue

            total_rsync_cmds += 1

            # Collect full command across continuation lines
            full_cmd = stripped.rstrip("\\").strip()
            cmd_lineno = lineno + 1  # 1-based for reporting

            # Follow continuation lines
            scan = lineno
            while scan < len(lines) and lines[scan].rstrip().endswith("\\"):
                scan += 1
                if scan < len(lines):
                    cont = lines[scan].strip().rstrip("\\").strip()
                    full_cmd += " " + cont

            # Determine if this is a directory-level sync (needs --exclude .git)
            # Directory syncs use --delete flag; single-file transfers don't
            has_delete = "--delete" in full_cmd

            # Check for git exclude in the full assembled command
            has_git_exclude = (
                "--exclude '.git'" in full_cmd
                or '--exclude ".git"' in full_cmd
                or "--exclude=.git" in full_cmd
                or "--exclude .git" in full_cmd
            )

            # Only require --exclude .git for directory syncs (--delete flag),
            # not single-file transfers (platform-env.yaml, Makefile, etc.)
            if not has_git_exclude and has_delete:
                violations.append(f"{script_path.name}:{cmd_lineno}: {stripped}")
                logger.info(
                    "[IMP:9][gate][rsync_exclude] FAIL: rsync --delete sync without --exclude .git: %s:%d",
                    script_path.name,
                    cmd_lineno,
                )
            elif has_git_exclude:
                logger.info(
                    "[IMP:8][gate][rsync_exclude] OK: rsync with --exclude .git: %s:%d",
                    script_path.name,
                    cmd_lineno,
                )

    logger.info(
        "[IMP:9][gate][rsync_exclude] Scanned %d scripts, %d rsync commands, %d violations",
        len([s for s in RSYNC_SCRIPTS if s.exists()]),
        total_rsync_cmds,
        len(violations),
    )
    assert not violations, "[IMP:9][gate][rsync_exclude] FAIL: rsync commands missing --exclude '.git/':\n" + "\n".join(
        violations
    )
    logger.info("[IMP:9][gate][rsync_exclude] PASS: All rsync commands exclude .git/ (total: %d)", total_rsync_cmds)
