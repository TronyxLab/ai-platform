#!/usr/bin/env python3
# GREP_SUMMARY: github-ops create-github-repo gh-cli git-remote dry-run graceful-skip timeouts GITHUB_OPS_TIMEOUT
# STRUCTURE: ▶ ┌org + name + project_dir┐ → ◇ gh available? → ◇ dry_run? → ⊕ _checked (view/create/remote/push, все с timeout) → ◇ TimeoutExpired? → ⎋ False │ ⎋ bool
# region MODULE_CONTRACT
## @purpose  GitHub repository creation operations extracted from project_scaffolder.py (DevPlan 117 G T58.1).
##           Mirrors create_github_repo() from the original add-project.sh:591-628 — creates a private
##           GitHub repo, adds the git remote, and pushes the initial commit.
## @scope    Consumed by core/internal/scaffold/project_scaffolder.py (lazy import). Developer-machine only.
## @invariants
##   - Graceful: gh not found → warn, skip (non-fatal)
##   - Repo already exists → skip creation, add remote
##   - dry_run=True → logs only, never touches subprocess
##   - Все gh/git подвызовы несут timeout=GITHUB_OPS_TIMEOUT (AI-0017: зависшая сеть не висит вечно;
##     TimeoutExpired на ЛЮБОМ подвызове → return False)
##   - Фейл создания repo или начального push → return False + IMP:9 (AI-0037: неудача больше
##     НЕ репортится успехом); отсутствие gh и dry-run остаются честным True (skip)
## @rationale  DevPlan 117 G T58.1 — extracted verbatim with LDD logs; DevPlan 17 T3.3 — timeouts +
##            honest failure returns (AI-0017/AI-0037).
## @changes  2026-08-01 · DevPlan 117 G T58.1 — extracted from project_scaffolder.py
## @changes  2026-08-26 · DevPlan 17 T3.3 — GITHUB_OPS_TIMEOUT + False-on-failure контракт
# endregion MODULE_CONTRACT

import logging
import shutil
import subprocess

from core.internal.shared.timeouts import GITHUB_OPS_TIMEOUT

logger = logging.getLogger(__name__)


# region FUNC__create_github_repo_checked
def _create_github_repo_checked(org: str, name: str, project_dir: str) -> bool:
    """Тело операции без таймаут-обёртки (AI-0017: единый TimeoutExpired-контракт наверху).

    ## @purpose  Все gh/git подвызовы несут GITHUB_OPS_TIMEOUT; TimeoutExpired пробрасывается
    ##            вызывающей обёртке — единая точка конвертации в False.
    ## @io        ⇥ org, name, project_dir → ⎋ bool (False = операция не завершена)
    """
    # ── Check if repo already exists ──
    result = subprocess.run(
        ["gh", "repo", "view", f"{org}/{name}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=GITHUB_OPS_TIMEOUT,
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
            timeout=GITHUB_OPS_TIMEOUT,
        )
        if remote_result.returncode != 0:
            subprocess.run(
                ["git", "remote", "add", "origin", f"git@github.com:{org}/{name}.git"],
                cwd=project_dir,
                capture_output=True,
                check=False,
                timeout=GITHUB_OPS_TIMEOUT,
            )
            logger.info("[IMP:7][scaffold][gh] Added git remote: origin git@github.com:%s/%s.git", org, name)
        return True

    logger.info("[IMP:7][scaffold][gh] Creating GitHub repo: %s/%s", org, name)

    result = subprocess.run(
        ["gh", "repo", "create", f"{org}/{name}", "--private", "--description", f"{name} project"],
        capture_output=True,
        text=True,
        check=False,
        timeout=GITHUB_OPS_TIMEOUT,
    )

    if result.returncode == 0:
        logger.info("[IMP:7][scaffold][gh] GitHub repo created: %s/%s", org, name)
        subprocess.run(
            ["git", "remote", "add", "origin", f"git@github.com:{org}/{name}.git"],
            cwd=project_dir,
            capture_output=True,
            check=False,
            timeout=GITHUB_OPS_TIMEOUT,
        )
        # AI-0037: фейл начального push — часть контракта операции → False (не успех)
        push_result = subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=GITHUB_OPS_TIMEOUT,
        )
        if push_result.returncode != 0:
            logger.error(
                "[IMP:9][scaffold][gh] git push failed (rc=%d) — push manually; reporting failure",
                push_result.returncode,
            )
            return False
        logger.info("[IMP:7][scaffold][gh] Initial push to origin/main complete")
        return True

    # AI-0037: неудача создания repo больше НЕ репортится успехом
    logger.error(
        "[IMP:9][scaffold][gh] Failed to create GitHub repo: %s/%s (rc=%d): %s — create manually",
        org,
        name,
        result.returncode,
        (result.stderr or "").strip()[-200:],
    )
    return False


# endregion FUNC__create_github_repo_checked


# region FUNC_create_github_repo
def create_github_repo(org: str, name: str, project_dir: str, dry_run: bool = False) -> bool:
    """Create GitHub repo and push initial commit.

    ▶ ◇ which(gh)? → ⎋ True (skip) → ◇ dry_run? → ⎋ True → ⊕ _checked(org, name, dir)
      → ◇ TimeoutExpired? → ⎋ False + IMP:9 → ⎋ вердикт _checked

    ## @purpose  Mirror of create_github_repo() from add-project.sh:591-628.
    ## @io        ⇥ org, name, project_dir, dry_run → ⎋ bool
    ## @complexity O(1)
    ## @invariants
    ##   - Graceful: gh not found → warn, skip (non-fatal)
    ##   - Repo already exists → skip creation, add remote
    ##   - Единый таймаут-контракт: любой TimeoutExpired из подвызовов → False (AI-0017)
    """
    if not shutil.which("gh"):
        logger.info("[IMP:9][scaffold][gh] WARNING: gh CLI not found — skipping GitHub repo creation")
        return True  # non-fatal

    if dry_run:
        logger.info("[IMP:7][scaffold][gh] [DRY-RUN] Would create GitHub repo: %s/%s", org, name)
        return True

    try:
        return _create_github_repo_checked(org, name, project_dir)
    except subprocess.TimeoutExpired:
        logger.error(
            "[IMP:9][scaffold][gh] gh/git call timed out after %ds — %s/%s operation INCOMPLETE",
            GITHUB_OPS_TIMEOUT,
            org,
            name,
        )
        return False


# endregion FUNC_create_github_repo
