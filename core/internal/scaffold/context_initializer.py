#!/usr/bin/env python3
# GREP_SUMMARY: context_initializer scaffold context hermes-agent node-configs gh-repo registration idempotent skeleton
# STRUCTURE: ▶ validate_name → ⚡ check_idempotent ─┬─ create_dirs ─┬─ create_skeleton_node_yaml ── gh_repo_create ── register_in_platform_yaml ── report_summary
# region MODULE_CONTRACT
## @purpose  Python Strangler-Fig migration of context-init.sh (364 LOC shell).
##           Scaffolds a new deployment context: directory structure, skeleton node.yaml,
##           GitHub repos, and registration in platform node.yaml.
## @scope    Developer machine only (local scaffold) — no SSH, no VPS operations.
##           Called from context-init.sh facade.
## @invariants
##   - Idempotent: if ~/projects/<name>/ exists → SKIP (exit 0)
##   - Skeleton node.yaml preserves GREP_SUMMARY/STRUCTURE semantic markup
##   - GitHub repo creation is optional (--skip-gh-repo flag)
##   - Registration delegates to context_registry.py (105 LOC, stable)
##   - All steps are independent — continues on non-fatal gh failures
##   - Exit codes: 0=success/skip, 1=validation error, 2=registration error
## @rationale Step 1 of Scaffold → Declare → Apply workflow. Zero inline python3.
##            context_registry.py already exists — delegates, doesn't reimplement.
## @links    CALLED_BY: context-init.sh (facade)
##           CALLS: context_registry.register_context()
##           DP-092 Wave 2
## @changes  2026-07-30 · Wave 2 — initial implementation
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-07-21 · — · secrets-init called at bootstrap, not context-init
# · Rejected: calling secrets-init.sh from context_initializer.py
# · Reason: PLATFORM_MASTER_PASSWORD not available at scaffold time
# · Rev: if context-init gains access to PLATFORM_MASTER_PASSWORD → call secrets-init.sh here

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────
_DEFAULT_PROJECTS_DIR = os.path.join(os.environ.get("HOME", "/"), "projects")
_DEFAULT_NODE = os.environ.get("NODE", "tronyx-vps")
_DEFAULT_ORG = os.environ.get("NODE_ORG", "tronyx-lab")

# ── Skeleton node.yaml template (preserve GREP_SUMMARY/STRUCTURE) ─────
_SKELETON_TEMPLATE = """# GREP_SUMMARY: {context_name} node context declarative apply declarative-deploy
# STRUCTURE: ▶ resolve → ┌contexts+node+modules┐ → ◇ validate ← ⊕ projects+secrets+firewall → ⚡ apply

# Skeleton node.yaml for context '{context_name}'.
# MUST EDIT: Replace placeholder values below with actual configuration.

# Deployment context this node belongs to (contexts[] canon — invariant 3, DevPlan 116 B6)
contexts:
  - name: {context_name}

# --- Node definition (MUST EDIT) ---
node:
  name: {context_name}
  host: "127.0.0.1"
  owner_key: ""
  timezone: "Europe/Moscow"

# --- Modules to deploy (MUST EDIT) ---
modules:
  - name: nginx
    enabled: true
  - name: postgres
    enabled: true
  - name: platform-secrets
    enabled: true

# --- Projects ---
projects: []
"""


# region FUNC_validate_name
## @purpose  Validate context name against canonical project name regex
## @param name  Context name string
## @io        ⎋ raises SystemExit(1) on invalid name
## @complexity O(1)
## @invariants  Thin wrapper over project_registry.validate_project_name (DevPlan 116 B6 T3):
##              единый строгий regex ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ (reject leading -/_), CLI
##              ConfigValidationError(4) контракт (T3.3: SystemExit(1) → raise).
def validate_name(name: str) -> None:
    """Validate context name format.

    ## @purpose  Fail-fast on invalid names before any I/O.
    ## @io        ⇥ name → ⎋ None (raises ConfigValidationError on failure)
    """
    from core.internal.shared.exceptions import ConfigValidationError
    from core.internal.shared.project_registry import validate_project_name

    if not validate_project_name(name):
        logger.info("[IMP:10][context][validate] FATAL: Invalid context name '%s'", name)
        raise ConfigValidationError(
            f"Invalid context name: {name} — use alphanumeric, hyphens, underscores (no leading -/_)"
        )
    logger.info("[IMP:8][context][validate] Context name: %s", name)


# endregion FUNC_validate_name


# region FUNC_check_idempotent
## @purpose  Check if context directory already exists — SKIP if yes (idempotent)
## @param context_dir  Path to the context directory
## @io        ⎋ bool — True если контекст уже существует (skip)
## @complexity O(1)
def check_idempotent(context_dir: Path) -> bool:
    """Return True if context directory already exists (skip).

    ## @purpose  Mirror of _check_idempotent from context-init.sh:116-126 (T3.3: sys.exit(0) → return True).
    ## @io        ⇥ context_dir → ⎋ bool (True = exists, caller решает exit 0)
    """
    if context_dir.exists():
        logger.info("[IMP:9][context][idempotent] SKIP: Context already exists at %s", context_dir)
        print(f"SKIP: Context directory already exists: {context_dir}")
        return True

    logger.info("[IMP:7][context][idempotent] Context does not exist — proceeding with scaffold")
    return False


# endregion FUNC_check_idempotent


# region FUNC_create_dirs
## @purpose  Create context directory structure: hermes-agent/ + node-configs/
## @param context_dir  Path to the new context directory
## @io        stdout: created directory messages; side-effect: mkdir -p
## @complexity O(1)
def create_dirs(context_dir: Path) -> None:
    """Create the context directory structure.

    ## @purpose  Mirror of _create_dirs from context-init.sh:129-143.
    ## @io        ⇥ context_dir → ⎋ None (creates dirs)
    """
    logger.info("[IMP:7][context][create] Creating context directory structure under %s", context_dir)

    hermes_dir = context_dir / "hermes-agent"
    hermes_dir.mkdir(parents=True, exist_ok=False)
    logger.info("[IMP:8][context][create] Created: %s/", hermes_dir)

    node_configs_dir = context_dir / "node-configs"
    node_configs_dir.mkdir(parents=True, exist_ok=False)
    logger.info("[IMP:8][context][create] Created: %s/", node_configs_dir)

    logger.info("[IMP:9][context][create] Context directory structure created: %s", context_dir)
    print(f"  ✅ Created: {context_dir}/")
    print(f"  ✅ Created: {hermes_dir}/")
    print(f"  ✅ Created: {node_configs_dir}/")


# endregion FUNC_create_dirs


# region FUNC_create_skeleton_node_yaml
## @purpose  Generate a skeleton node.yaml file with GREP_SUMMARY/STRUCTURE markup preserved
## @param path          Target path for node.yaml
## @param context_name  Context name for placeholder substitution
## @io        stdout: created/edited message; side-effect: writes file
## @complexity O(1)
def create_skeleton_node_yaml(path: Path, context_name: str) -> None:
    """Create skeleton node.yaml for the new context.

    ## @purpose  Mirror of _create_skeleton_node_yaml from context-init.sh:151-190.
    ##           Preserves GREP_SUMMARY/STRUCTURE comments per R7 (semantic markup).
    ## @io        ⇥ path, context_name → ⎋ None (writes file)
    ## @invariants  Overwrites existing skeleton (not idempotent in this function —
    ##              check_idempotent prevents calling this if context dir exists)
    """
    logger.info("[IMP:8][context][skeleton] Creating skeleton node.yaml")

    skeleton_content = _SKELETON_TEMPLATE.format(context_name=context_name)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skeleton_content)

    logger.info("[IMP:9][context][skeleton] Skeleton node.yaml created: %s", path)
    print(f"  ✅ Created: {path}")
    print("  ⚠️  Edit this file: set node.host, node.owner_key, and modules")


# endregion FUNC_create_skeleton_node_yaml


# region FUNC_gh_repo_create
## @purpose  Create GitHub repos for node-configs and hermes-agent (optional).
## @param org          GitHub org/username
## @param ctx          Context name
## @param skip         Skip repo creation flag
## @param context_dir  Path to context directory (for git init)
## @param gh_runner    Injectable gh CLI callable (for testing)
## @return   (node_cfg_repo: str | None, hermes_agent_repo: str | None, warnings: int)
## @complexity O(1) — subprocess calls
## @invariants
##   - Graceful degradation: gh not found → warn, continue (not fail)
##   - gh not authenticated → warn, continue
##   - Repo exists → treat as success (reuse)
def gh_repo_create(
    org: str,
    ctx: str,
    skip: bool = False,
    context_dir: Path | None = None,
    gh_runner: callable | None = None,
) -> tuple[str | None, str | None, int]:
    """Create GitHub repos for node-configs and hermes-agent.

    ## @purpose  Mirror of _gh_repo_create from context-init.sh:193-264.
    ## @io        ⇥ org, ctx, skip, context_dir, gh_runner → ⎋ (node_repo, agent_repo, warnings)
    ## @complexity O(1)
    """
    warnings = 0
    node_repo = f"{org}/{ctx}-node-configs"
    agent_repo = f"{org}/{ctx}-hermes-agent"

    if skip:
        logger.info("[IMP:7][context][gh] SKIP: GitHub repo creation disabled")
        logger.info("[IMP:9][context][gh] GitHub repo creation skipped (--skip-gh-repo)")
        print("  ⏭ SKIP: GitHub repo creation (--skip-gh-repo)")
        return None, None, warnings

    # Default gh runner: subprocess
    if gh_runner is None:

        def _default_gh_runner(cmd: list[str]) -> tuple[int, str, str]:
            """Execute gh CLI command."""
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return result.returncode, result.stdout, result.stderr
            except FileNotFoundError:
                return -1, "", "gh: command not found"

        gh_runner = _default_gh_runner

    # Check gh availability
    rc, _, _ = gh_runner(["gh", "--version"])
    if rc != 0:
        logger.info("[IMP:9][context][gh] WARNING: gh CLI not found — skipping GitHub repo creation")
        print("  ⚠️  gh CLI not found — skip GitHub repo creation")
        return None, None, 1

    # Check gh auth
    rc, _, _ = gh_runner(["gh", "auth", "status"])
    if rc != 0:
        logger.info("[IMP:9][context][gh] WARNING: gh CLI not authenticated — skipping")
        print("  ⚠️  gh not authenticated — skip GitHub repo creation")
        return None, None, 1

    created_node_repo: str | None = None
    created_agent_repo: str | None = None

    # Create node-configs repo
    logger.info("[IMP:8][context][gh] Creating repo: %s", node_repo)
    rc, stdout, stderr = gh_runner(
        ["gh", "repo", "create", node_repo, "--private", "--description", f"Node configurations for context '{ctx}'"]
    )
    if rc == 0:
        created_node_repo = node_repo
        logger.info("[IMP:9][context][gh] Created GitHub repo: %s", node_repo)
        print(f"  ✅ Created GitHub repo: {node_repo} (private)")
        # Git init + push in node-configs
        if context_dir:
            _git_init_and_push(context_dir / "node-configs", node_repo, ctx)
    elif "already exists" in (stdout + stderr).lower():
        logger.info("[IMP:9][context][gh] Repo already exists: %s", node_repo)
        created_node_repo = node_repo
    else:
        logger.info("[IMP:9][context][gh] WARNING: Failed to create %s: %s", node_repo, stderr.strip())
        warnings += 1

    # Create hermes-agent repo
    logger.info("[IMP:8][context][gh] Creating repo: %s", agent_repo)
    rc, stdout, stderr = gh_runner(
        ["gh", "repo", "create", agent_repo, "--private", "--description", f"Hermes-agent overlay for context '{ctx}'"]
    )
    if rc == 0:
        created_agent_repo = agent_repo
        logger.info("[IMP:9][context][gh] Created GitHub repo: %s", agent_repo)
        print(f"  ✅ Created GitHub repo: {agent_repo} (private)")
    elif "already exists" in (stdout + stderr).lower():
        logger.info("[IMP:9][context][gh] Repo already exists: %s", agent_repo)
        created_agent_repo = agent_repo
    else:
        logger.info("[IMP:9][context][gh] WARNING: Failed to create %s: %s", agent_repo, stderr.strip())
        warnings += 1

    return created_node_repo, created_agent_repo, warnings


def _git_init_and_push(
    repo_dir: Path,
    repo_slug: str,
    ctx: str,
    git_runner: callable | None = None,
) -> bool:
    """Initialize git in repo_dir and push to GitHub.

    ## @purpose  Helper for gh_repo_create: git init + commit + remote add + push.
    ## @io        ⇥ repo_dir, repo_slug, ctx → ⎋ bool
    ## @complexity O(1)
    """
    if git_runner is None:

        def _default_git(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=str(cwd))
            return result.returncode, result.stdout, result.stderr

        git_runner = _default_git

    repo_dir.mkdir(parents=True, exist_ok=True)

    # git init
    rc, _, _ = git_runner(["git", "init", "--initial-branch=main"], repo_dir)
    if rc != 0:
        git_runner(["git", "init"], repo_dir)
        git_runner(["git", "checkout", "-b", "main"], repo_dir)

    # git add + commit
    git_runner(["git", "add", "-A"], repo_dir)
    git_runner(["git", "commit", "-m", f"chore: initial scaffold for context '{ctx}'"], repo_dir)

    # git remote + push
    git_runner(["git", "remote", "add", "origin", f"git@github.com:{repo_slug}.git"], repo_dir)
    rc, _stdout, _stderr = git_runner(["git", "push", "-u", "origin", "main"], repo_dir)
    if rc != 0:
        logger.info("[IMP:9][context][gh] WARNING: Initial push to %s failed", repo_slug)
        return False
    return True


# endregion FUNC_gh_repo_create


# region FUNC_register_in_platform_yaml
## @purpose  Register context entry in platform node.yaml via context_registry.
## @param yaml_path          Path to platform node.yaml
## @param ctx_name           Context name
## @param ctx_desc           Context description
## @param node_cfg_repo      Node configs repo URL (optional)
## @param hermes_agent_repo  Hermes agent repo URL (optional)
## @io        stdout: registration status; side-effect: writes YAML
## @return    0 on success, 2 on error (matching shell exit codes)
## @complexity O(1) — delegates to context_registry
def register_in_platform_yaml(
    yaml_path: str,
    ctx_name: str,
    ctx_desc: str = "",
    node_cfg_repo: str = "",
    hermes_agent_repo: str = "",
) -> int:
    """Register context in platform node.yaml.

    ## @purpose  Mirror of _register_in_platform_yaml from context-init.sh:268-295.
    ##           Delegates to context_registry.register_context().
    ## @io        ⇥ yaml_path, ctx_name, ctx_desc, ... → ⎋ int — 0=ok, 2=error
    """
    logger.info("[IMP:8][context][register] Adding context entry: name=%s", ctx_name)

    from core.internal.scaffold.context_registry import register_context

    try:
        result = register_context(
            yaml_path=yaml_path,
            name=ctx_name,
            desc=ctx_desc,
            node_cfg_repo=node_cfg_repo or "",
            hermes_agent_repo=hermes_agent_repo or "",
        )
    except SystemExit as e:
        logger.info("[IMP:10][context][register] FATAL: YAML registration failed (exit=%s)", e.code)
        return 2

    if result == "EXISTS":
        logger.info("[IMP:9][context][register] SKIP: Context '%s' already registered", ctx_name)
        return 0

    logger.info("[IMP:9][context][register] Context '%s' registered in %s", ctx_name, yaml_path)
    print(f"  ✅ Registered context '{ctx_name}' in: {yaml_path}")
    return 0


# endregion FUNC_register_in_platform_yaml


# region FUNC_report_summary
## @purpose  Print formatted summary of the context initialization
## @param ctx_name             Context name
## @param context_dir          Path to context directory
## @param warnings             Warning count
## @param platform_yaml        Path to platform node.yaml (for display)
## @param node_cfg_repo        Node configs repo URL (optional)
## @param hermes_agent_repo    Hermes agent repo URL (optional)
## @io        stdout: formatted summary table
## @complexity O(1)
def report_summary(
    ctx_name: str,
    context_dir: Path,
    warnings: int,
    platform_yaml: str,
    node_cfg_repo: str | None = None,
    hermes_agent_repo: str | None = None,
) -> None:
    """Print context init summary.

    ## @purpose  Mirror of _report_summary from context-init.sh:298-322.
    ## @io        ⇥ ... → ⎋ stdout
    """
    print()
    print("┌─ Context Init Summary ─────────────────────────────────┐")
    print(f"│ Context:     {ctx_name}")
    print(f"│ Directory:   {context_dir}")
    print(f"│ Warnings:    {warnings}")
    print("│")
    print("│ Created:")
    print(f"│   ✅ {context_dir}/")
    print(f"│   ✅ {context_dir}/hermes-agent/")
    print(f"│   ✅ {context_dir}/node-configs/")
    print(f"│   ✅ {context_dir}/node-configs/node.yaml (skeleton)")
    if node_cfg_repo:
        print(f"│   ✅ GitHub: {node_cfg_repo}")
    if hermes_agent_repo:
        print(f"│   ✅ GitHub: {hermes_agent_repo}")
    print(f"│   ✅ Registered in: {platform_yaml}")
    print("└────────────────────────────────────────────────────────┘")
    print()

    logger.info("[IMP:9][context][summary] Context '%s' initialized | Warnings: %d", ctx_name, warnings)


# endregion FUNC_report_summary


# region FUNC_main
## @purpose  CLI entry point — full context scaffold orchestration
## @io        stdout: progress messages; exit 0 on success, 1 on validation error, 2 on registration error
## @complexity O(1) (subprocess calls for gh, otherwise pure Python)
def main(argv: list[str] | None = None) -> int:
    """CLI dispatcher for context initializer.

    ## @purpose  Parse args, orchestrate full context-init flow.
    ## @io        ⇥ argv → ⎋ int exit code (contract T4: main() -> int)
    ## @complexity O(1)
    """
    parser = argparse.ArgumentParser(
        description="Scaffold a new deployment context: create dirs, skeleton node.yaml, GitHub repos, register.",
    )
    parser.add_argument("name", nargs="?", default="", help="Context name (also used as directory name)")
    parser.add_argument("--name", dest="name_opt", default="", help="Context name (alternate form)")
    parser.add_argument("--description", default="", help="Human-readable description")
    parser.add_argument("--org", default=_DEFAULT_ORG, help=f"GitHub org/username (default: {_DEFAULT_ORG})")
    parser.add_argument("--node", default=_DEFAULT_NODE, help=f"Node name for resolution (default: {_DEFAULT_NODE})")
    parser.add_argument("--node-yaml", default="", help="Explicit path to platform node.yaml")
    parser.add_argument("--skip-gh-repo", action="store_true", default=False, help="Skip GitHub repository creation")
    parser.add_argument("--projects-dir", default=_DEFAULT_PROJECTS_DIR, help="Projects directory")

    args = parser.parse_args(argv)

    # Resolve context name (positional or --name)
    context_name = args.name or args.name_opt
    if not context_name:
        logger.info("[IMP:10][context][main] FATAL: No context name provided")
        print("ERROR: No context name provided")
        parser.print_usage()
        return 1

    start_time = time.time()

    logger.info("[IMP:9][context][main] ══════════════════════════════════════════")
    logger.info("[IMP:9][context][main]   context-init — Declarative Context Scaffold")
    logger.info("[IMP:9][context][main] ══════════════════════════════════════════")

    try:
        from core.internal.shared.exceptions import PlatformError

        validate_name(context_name)

        context_dir = Path(args.projects_dir) / context_name
        if check_idempotent(context_dir):
            return 0

        create_dirs(context_dir)

        skeleton_path = context_dir / "node-configs" / "node.yaml"
        create_skeleton_node_yaml(skeleton_path, context_name)

        description = args.description or ""
        gh_org = args.org
        node_cfg_repo, hermes_agent_repo, gh_warnings = gh_repo_create(
            org=gh_org,
            ctx=context_name,
            skip=args.skip_gh_repo,
            context_dir=context_dir,
        )

        # Resolve platform node.yaml path
        platform_yaml = args.node_yaml
        if not platform_yaml:
            # Try to resolve via node-resolver
            from pathlib import Path as _Path

            # Search for node.yaml in PROJECTS_ROOT (common pattern)
            search_dirs = [
                _Path(args.projects_dir) / "*" / "node-configs" / args.node / "node.yaml",
                _Path(args.projects_dir) / "ai-platform" / "node-configs" / args.node / "node.yaml",
            ]
            for pattern in search_dirs:
                matches = list(_Path(args.projects_dir).glob(str(pattern.relative_to(args.projects_dir))))
                if matches:
                    platform_yaml = str(matches[0])
                    break

        if not platform_yaml or not Path(platform_yaml).exists():
            logger.info("[IMP:10][context][main] FATAL: Could not resolve platform node.yaml")
            print(f"ERROR: Could not resolve platform node.yaml for NODE={args.node}")
            return 1

        logger.info("[IMP:7][context][resolve] Platform node.yaml resolved: %s", platform_yaml)

        reg_rc = register_in_platform_yaml(
            yaml_path=platform_yaml,
            ctx_name=context_name,
            ctx_desc=description,
            node_cfg_repo=node_cfg_repo or "",
            hermes_agent_repo=hermes_agent_repo or "",
        )
        if reg_rc != 0:
            return reg_rc

        total_warnings = gh_warnings
        report_summary(
            ctx_name=context_name,
            context_dir=context_dir,
            warnings=total_warnings,
            platform_yaml=platform_yaml,
            node_cfg_repo=node_cfg_repo,
            hermes_agent_repo=hermes_agent_repo,
        )

        elapsed = time.time() - start_time
        logger.info("[IMP:9][context][main] context-init COMPLETE — %.0fs", elapsed)
        return 0
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
