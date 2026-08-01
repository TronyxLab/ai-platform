#!/usr/bin/env python3
# GREP_SUMMARY: github-ops create-github-repo gh-cli git-remote dry-run graceful-skip non-fatal
# STRUCTURE: ▶ ┌org + name + project_dir┐ → ◇ gh available? → ◇ dry_run? → ◇ repo exists? → ⊕ gh repo create → ⊕ git remote add → ⊕ git push -u origin main → ⎋ bool
# region MODULE_CONTRACT
## @purpose  GitHub repository creation operations extracted from project_scaffolder.py (DevPlan 117 G T58.1).
##           Mirrors create_github_repo() from the original add-project.sh:591-628 — creates a private
##           GitHub repo, adds the git remote, and pushes the initial commit. Fully non-fatal.
## @scope    Consumed by core/internal/scaffold/project_scaffolder.py (lazy import). Developer-machine only.
## @invariants
##   - Graceful: gh not found → warn, skip (non-fatal)
##   - Repo already exists → skip creation, add remote
##   - dry_run=True → logs only, never touches subprocess
##   - Never raises — all subprocess failures → warn + return True (non-fatal)
## @rationale  DevPlan 117 G T58.1 — extracted verbatim (L356-435, ~80 LOC) with all LDD logs and
##            docstring preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T58.1 — extracted from project_scaffolder.py
# endregion MODULE_CONTRACT

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


# region FUNC_create_github_repo
def create_github_repo(org: str, name: str, project_dir: str, dry_run: bool = False) -> bool:
    """Create GitHub repo and push initial commit.

    ## @purpose  Mirror of create_github_repo() from add-project.sh:591-628.
    ## @io        ⇥ org, name, project_dir, dry_run → ⎋ bool
    ## @complexity O(1)
    ## @invariants
    ##   - Graceful: gh not found → warn, skip (non-fatal)
    ##   - Repo already exists → skip creation, add remote
    """
    # Check gh availability
    if not shutil.which("gh"):
        logger.info("[IMP:9][scaffold][gh] WARNING: gh CLI not found — skipping GitHub repo creation")
        return True  # non-fatal

    if dry_run:
        logger.info("[IMP:7][scaffold][gh] [DRY-RUN] Would create GitHub repo: %s/%s", org, name)
        return True

    # Check if repo already exists
    result = subprocess.run(
        ["gh", "repo", "view", f"{org}/{name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        logger.info("[IMP:7][scaffold][gh] GitHub repo already exists: %s/%s — skipping creation", org, name)
        # Add remote if not already set
        remote_result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if remote_result.returncode != 0:
            subprocess.run(
                ["git", "remote", "add", "origin", f"git@github.com:{org}/{name}.git"],
                cwd=project_dir,
                capture_output=True,
                check=False,
            )
            logger.info("[IMP:7][scaffold][gh] Added git remote: origin git@github.com:%s/%s.git", org, name)
        return True

    logger.info("[IMP:7][scaffold][gh] Creating GitHub repo: %s/%s", org, name)

    result = subprocess.run(
        ["gh", "repo", "create", f"{org}/{name}", "--private", "--description", f"{name} project"],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        logger.info("[IMP:7][scaffold][gh] GitHub repo created: %s/%s", org, name)
        subprocess.run(
            ["git", "remote", "add", "origin", f"git@github.com:{org}/{name}.git"],
            cwd=project_dir,
            capture_output=True,
            check=False,
        )
        push_result = subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if push_result.returncode != 0:
            logger.info("[IMP:8][scaffold][gh] WARNING: git push failed — push manually")
        else:
            logger.info("[IMP:7][scaffold][gh] Initial push to origin/main complete")
        return True
    logger.info("[IMP:8][scaffold][gh] WARNING: Failed to create GitHub repo: %s/%s — create manually", org, name)
    return True  # non-fatal


# endregion FUNC_create_github_repo
