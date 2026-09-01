#!/usr/bin/env python3
# GREP_SUMMARY: context_initializer scaffold context overlay platform node-configs gh-repo registration idempotent skeleton
# STRUCTURE: ▶ validate_name → ⚡ check_idempotent ─┬─ create_dirs(platform/{node-configs,modules/hermes-agent,projects}) ─┬─ create_skeleton_node_yaml ── gh_repo_create(<ctx>-overlay) ── register_in_platform_yaml ── report_summary
# region MODULE_CONTRACT
## @purpose  Python Strangler-Fig migration of context-init.sh (364 LOC shell).
##           Scaffolds a new deployment context: nested overlay directory structure
##           (platform/{node-configs,modules/hermes-agent,projects}), skeleton node.yaml,
##           ONE GitHub overlay repo (`<org>/<ctx>-overlay`), and registration in platform node.yaml.
## @scope    Developer machine only (local scaffold) — no SSH, no VPS operations.
##           Called from context-init.sh facade.
## @invariants
##   - Idempotent: if ~/projects/<name>/ exists → SKIP (exit 0)
##   - Canonical layout (DevPlan 022): весь overlay — под `<ctx>/platform/`; сестринские
##     hermes-agent/ + node-configs/ НЕ создаются
##   - Один GitHub-репо `<org>/<ctx>-overlay` (private); `<ctx>-node-configs` / `<ctx>-hermes-agent`
##     упразднены как отдельные репо (DevPlan 022 D3/D6)
##   - Skeleton node.yaml preserves GREP_SUMMARY/STRUCTURE semantic markup;
##     repos.core = `https://github.com/<org>/<ctx>-overlay.git`
##   - GitHub repo creation is optional (--skip-gh-repo flag)
##   - Registration delegates to context_registry.py (105 LOC, stable)
##   - All steps are independent — continues on non-fatal gh failures
##   - Exit codes: 0=success/skip, 1=validation error, 2=registration error
## @rationale Step 1 of Scaffold → Declare → Apply workflow. Zero inline python3.
##            context_registry.py already exists — delegates, doesn't reimplement.
## @links    CALLED_BY: context-init.sh (facade)
##           CALLS: context_registry.register_context()
##           DP-092 Wave 2; DevPlan 022 TASK-2 (nested layout + single overlay repo)
## @changes  2026-07-30 · Wave 2 — initial implementation
## @changes  2026-09-01 · DevPlan 022 TASK-2 — nested platform/ layout, single `<ctx>-overlay` repo,
##           skeleton repos.core, glob `*/platform/node-configs/<node>/node.yaml`
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-07-21 · — · secrets-init called at bootstrap, not context-init
# · Rejected: calling secrets-init.sh from context_initializer.py
# · Reason: PLATFORM_MASTER_PASSWORD not available at scaffold time
# · Rev: if context-init gains access to PLATFORM_MASTER_PASSWORD → call secrets-init.sh here

# 🧐 TRAP[DECISION] · 2026-09-01 · — · platform-yaml resolve preference: overlay > source fixture > fresh skeleton
# · Rejected: naive matches[0] по первому паттерну — свежий skeleton (теперь
#   platform/node-configs/<node>/node.yaml) попадает в тот же glob и может затереть
#   регистрацию существующего контекстного node.yaml (glob-порядок недетерминирован)
# · Reason: канон = overlay (DevPlan 022 §1.4); source-копия ai-platform/node-configs —
#   dev/test-фикстура; свежий skeleton — последний fallback (регистрация в него — no-op,
#   contexts[] уже содержит контекст из шаблона)
# · Rev: если реестр contexts[] переедет из per-node node.yaml в context.yaml — хелпер упразднить

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, cast

from core.internal.shared.exceptions import (
    PlatformError,  # hoisted — except-ветка main() читает имя (reportPossiblyUnboundVariable)
)

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────
_DEFAULT_PROJECTS_DIR = Path(os.environ.get("HOME", "/"), "projects")
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

# --- Context overlay repo (DevPlan 022: единственный overlay-репо контекста) ---
repos:
  core: https://github.com/{org}/{context_name}-overlay.git

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
        msg = f"Invalid context name: {name} — use alphanumeric, hyphens, underscores (no leading -/_)"
        raise ConfigValidationError(msg)
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
## @purpose  Create nested context overlay structure: platform/{node-configs,modules/hermes-agent,projects}
## @param context_dir  Path to the new context directory
## @io        stdout: created directory messages; side-effect: mkdir -p
## @complexity O(1)
## @invariants  Канонический layout (DevPlan 022 TASK-2): весь overlay под platform/;
##              сестринские hermes-agent/ + node-configs/ НЕ создаются.
def create_dirs(context_dir: Path) -> None:
    """Create the nested context overlay directory structure.

    ## @purpose  DevPlan 022 TASK-2: nested layout вместо сестринских каталогов.
    ## @io        ⇥ context_dir → ⎋ None (creates platform/{node-configs,modules/hermes-agent,projects})
    """
    logger.info("[IMP:7][context][create] Creating context overlay structure under %s", context_dir)

    platform_dir = context_dir / "platform"
    platform_dir.mkdir(parents=True, exist_ok=False)
    logger.info("[IMP:8][context][create] Created: %s/", platform_dir)

    node_configs_dir = platform_dir / "node-configs"
    node_configs_dir.mkdir(parents=True, exist_ok=False)
    logger.info("[IMP:8][context][create] Created: %s/", node_configs_dir)

    hermes_dir = platform_dir / "modules" / "hermes-agent"
    hermes_dir.mkdir(parents=True, exist_ok=False)
    logger.info("[IMP:8][context][create] Created: %s/", hermes_dir)

    projects_dir = platform_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=False)
    logger.info("[IMP:8][context][create] Created: %s/", projects_dir)

    logger.info("[IMP:9][context][create] Context overlay structure created: %s", platform_dir)
    print(f"  ✅ Created: {context_dir}/")
    print(f"  ✅ Created: {platform_dir}/")
    print(f"  ✅ Created: {node_configs_dir}/")
    print(f"  ✅ Created: {hermes_dir}/")
    print(f"  ✅ Created: {projects_dir}/")


# endregion FUNC_create_dirs


# region FUNC_create_skeleton_node_yaml
## @purpose  Generate a skeleton node.yaml file with GREP_SUMMARY/STRUCTURE markup preserved
## @param path          Target path for node.yaml (canon: `platform/node-configs/<node>/node.yaml`)
## @param context_name  Context name for placeholder substitution
## @param org           GitHub org for repos.core URL (`<org>/<ctx>-overlay.git`)
## @io        stdout: created/edited message; side-effect: writes file
## @complexity O(1)
## @invariants  org обязателен (fail-fast): пустой org дал бы malformed URL
##              `https://github.com//<ctx>-overlay.git` в skeleton.
def create_skeleton_node_yaml(path: Path, context_name: str, org: str) -> None:
    """Create skeleton node.yaml for the new context.

    ## @purpose  DevPlan 022 TASK-2: skeleton с repos.core = `<org>/<ctx>-overlay.git`.
    ##           Preserves GREP_SUMMARY/STRUCTURE comments per R7 (semantic markup).
    ## @io        ⇥ path, context_name, org → ⎋ None (writes file)
    ## @invariants  Overwrites existing skeleton (not idempotent in this function —
    ##              check_idempotent prevents calling this if context dir exists)
    """
    if not org:
        from core.internal.shared.exceptions import ConfigValidationError

        msg = f"org is required for skeleton repos.core URL (context '{context_name}')"
        raise ConfigValidationError(msg)

    logger.info("[IMP:8][context][skeleton] Creating skeleton node.yaml")

    skeleton_content = _SKELETON_TEMPLATE.format(context_name=context_name, org=org)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skeleton_content, encoding="utf-8")

    logger.info("[IMP:9][context][skeleton] Skeleton node.yaml created: %s", path)
    print(f"  ✅ Created: {path}")
    print("  ⚠️  Edit this file: set node.host, node.owner_key, and modules")


# endregion FUNC_create_skeleton_node_yaml


# region FUNC_gh_repo_create
## @purpose  Create the single context overlay GitHub repo (optional).
## @param org          GitHub org/username
## @param ctx          Context name
## @param skip         Skip repo creation flag
## @param context_dir  Path to context directory (git init+push on context_dir/platform)
## @param gh_runner    Injectable gh CLI callable (for testing)
## @param git_runner   Injectable git callable (DI test seam; None → subprocess)
## @return   (overlay_repo: str | None, reserved: None, warnings: int)
##           Второй элемент зарезервирован под прежний контракт (hermes_agent_repo) —
##           всегда None с DevPlan 022 (репо `<ctx>-hermes-agent` упразднён).
## @complexity O(1) — subprocess calls
## @invariants
##   - РОВНО ОДИН репо: `<org>/<ctx>-overlay` (private) — DevPlan 022 D3/D6;
##     `<ctx>-node-configs` / `<ctx>-hermes-agent` упразднены
##   - Git init+push выполняется на context_dir/platform (весь overlay — один репо)
# 📝 TRAP[DEBT] · 2026-09-01 · MED · scaffold не провижинит deploy key для VPS-клона приватного overlay
# · Observed: миграция tronyx-lab (022 TASK-5) — приватный `<ctx>-overlay` недоступен с VPS по
#   unauthenticated HTTPS (нужен read-only deploy key + SSH-алиас в repos.core, см.
#   TRAP[DECISION] в deploy/context_overlay.py); skeleton здесь пишет HTTPS-URL в repos.core
# · Suspected: new-context должен генерировать deploy key (gh repo deploy-key add) и
#   SSH-алиасный repos.core — иначе первый деплой проекта контекста упадёт на clone
# · Impact: новый контекст = ручной шаг deploy key после scaffold
# · When: обнаружено при миграции tronyx-lab (DevPlan 022 TASK-5, вне скоупа плана)
# · Rev: первый new-context после 022 → добавить deploy-key шаг в gh_repo_create + тест
##   - Graceful degradation: gh not found → warn, continue (not fail)
##   - gh not authenticated → warn, continue
##   - Repo exists → treat as success (reuse)
def gh_repo_create(
    org: str,
    ctx: str,
    skip: bool = False,
    context_dir: Path | None = None,
    gh_runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
    git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
) -> tuple[str | None, None, int]:
    """Create the single context overlay GitHub repo.

    ## @purpose  DevPlan 022 TASK-2: один overlay-репо вместо двух сестринских.
    ## @io        ⇥ org, ctx, skip, context_dir, gh_runner → ⎋ (overlay_repo, None, warnings)
    ## @complexity O(1)
    """
    warnings = 0
    overlay_repo = f"{org}/{ctx}-overlay"

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
            except FileNotFoundError:
                return -1, "", "gh: command not found"
            else:
                return result.returncode, result.stdout, result.stderr

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

    created_overlay_repo: str | None = None

    # Create the single overlay repo
    logger.info("[IMP:8][context][gh] Creating repo: %s", overlay_repo)
    rc, stdout, stderr = gh_runner([
        "gh",
        "repo",
        "create",
        overlay_repo,
        "--private",
        "--description",
        f"Context overlay for '{ctx}'",
    ])
    if rc == 0:
        created_overlay_repo = overlay_repo
        logger.info("[IMP:9][context][gh] Created GitHub repo: %s", overlay_repo)
        print(f"  ✅ Created GitHub repo: {overlay_repo} (private)")
        # Git init + push of the whole overlay (context_dir/platform)
        if context_dir:
            _git_init_and_push(context_dir / "platform", overlay_repo, ctx, git_runner=git_runner)
    elif "already exists" in (stdout + stderr).lower():
        logger.info("[IMP:9][context][gh] Repo already exists: %s", overlay_repo)
        created_overlay_repo = overlay_repo
    else:
        logger.info("[IMP:9][context][gh] WARNING: Failed to create %s: %s", overlay_repo, stderr.strip())
        warnings += 1

    return created_overlay_repo, None, warnings


def _git_init_and_push(
    repo_dir: Path,
    repo_slug: str,
    ctx: str,
    git_runner: Callable[[list[str], Path], tuple[int, str, str]] | None = None,
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
## @param register_fn        DI (DevPlan 167 D3): fake register-функция для тестов; None → канон
## @io        stdout: registration status; side-effect: writes YAML
## @return    0 on success, 2 on error (matching shell exit codes)
## @complexity O(1) — delegates to context_registry
def register_in_platform_yaml(
    yaml_path: str,
    ctx_name: str,
    ctx_desc: str = "",
    node_cfg_repo: str = "",
    hermes_agent_repo: str = "",
    *,
    register_fn: Callable[..., str] | None = None,
) -> int:
    """Register context in platform node.yaml.

    ## @purpose  Mirror of _register_in_platform_yaml from context-init.sh:268-295.
    ##            Delegates to context_registry.register_context().
    ##            register_fn (167 D3): тесты передают fake — 0 патчей context_registry.
    ##            🧐 TRAP[DI-SEAM] · 2026-08-14 · — · register_fn на register_in_platform_yaml
    ##            · Rejected: прямой вызов context_registry.register_context
    ##            · Reason: seam = тестируемость реального делегирования (параметры name/desc/
    ##            ·   repos) без глобального патча context_registry; default (None → канон) неизменен
    ##            · Rev: переход регистрации на другой канал (CLI) → параметр устаревает
    ## @io        ⇥ yaml_path, ctx_name, ctx_desc, ... → ⎋ int — 0=ok, 2=error
    """
    logger.info("[IMP:8][context][register] Adding context entry: name=%s", ctx_name)

    from core.internal.scaffold.context_registry import register_context

    register_impl = register_context if register_fn is None else register_fn

    try:
        result = register_impl(
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
## @param node_cfg_repo        Overlay repo URL (optional)
## @param hermes_agent_repo    Reserved (legacy contract) — always None since DevPlan 022
## @param node                 Node name for skeleton path display (optional)
## @io        stdout: formatted summary table
## @complexity O(1)
def report_summary(
    ctx_name: str,
    context_dir: Path,
    warnings: int,
    platform_yaml: str,
    node_cfg_repo: str | None = None,
    hermes_agent_repo: str | None = None,
    node: str | None = None,
) -> None:
    """Print context init summary (nested overlay paths, DevPlan 022 TASK-2).

    ## @purpose  Mirror of _report_summary from context-init.sh:298-322 — paths nested.
    ## @io        ⇥ ... → ⎋ stdout
    """
    skeleton_display = (
        f"{context_dir}/platform/node-configs/{node}/node.yaml (skeleton)"
        if node
        else f"{context_dir}/platform/node-configs/ (skeleton node.yaml)"
    )
    print()
    print("┌─ Context Init Summary ─────────────────────────────────┐")
    print(f"│ Context:     {ctx_name}")
    print(f"│ Directory:   {context_dir}")
    print(f"│ Warnings:    {warnings}")
    print("│")
    print("│ Created:")
    print(f"│   ✅ {context_dir}/")
    print(f"│   ✅ {context_dir}/platform/")
    print(f"│   ✅ {context_dir}/platform/node-configs/")
    print(f"│   ✅ {context_dir}/platform/modules/hermes-agent/")
    print(f"│   ✅ {context_dir}/platform/projects/")
    print(f"│   ✅ {skeleton_display}")
    if node_cfg_repo:
        print(f"│   ✅ GitHub: {node_cfg_repo}")
    if hermes_agent_repo:
        print(f"│   ✅ GitHub: {hermes_agent_repo}")
    print(f"│   ✅ Registered in: {platform_yaml}")
    print("└────────────────────────────────────────────────────────┘")
    print()

    logger.info("[IMP:9][context][summary] Context '%s' initialized | Warnings: %d", ctx_name, warnings)


# endregion FUNC_report_summary


## @purpose  Resolve the platform node.yaml to register a scaffolded context in.
## @param projects_dir  Projects base directory
## @param node          Node name for resolution
## @param fallback      Fresh-skeleton path — last-resort registration target
## @io        ⎋ str — resolved path ("" if nothing found and no fallback)
## @complexity O(glob)
## @invariants  Preference (TRAP[DECISION] 2026-09-01): существующий overlay node.yaml
##              (pattern 1, свежий skeleton исключён) → source-фикстура ai-platform/node-configs
##              (pattern 2, dev/test) → свежий skeleton (fallback; contexts[] уже содержит
##              контекст — регистрация no-op). Канон = overlay (DevPlan 022 §1.4).
def _resolve_platform_node_yaml(projects_dir: Path, node: str, fallback: str = "") -> str:
    """Resolve platform node.yaml via glob (nested overlay pattern first).

    ## @purpose  DevPlan 022 TASK-2: pattern 1 = `*/platform/node-configs/<node>/node.yaml`;
    ##           pattern 2 (ai-platform source fixture) сохранён.
    ## @io        ⇥ projects_dir, node, fallback → ⎋ str path
    """
    search_patterns = [
        projects_dir / "*" / "platform" / "node-configs" / node / "node.yaml",
        projects_dir / "ai-platform" / "node-configs" / node / "node.yaml",
    ]
    for pattern in search_patterns:
        matches = list(projects_dir.glob(str(pattern.relative_to(projects_dir))))
        # Свежий skeleton не затирает регистрацию существующих контекстов (TRAP[DECISION]).
        non_skeleton = [m for m in matches if str(m) != fallback]
        if non_skeleton:
            return str(non_skeleton[0])
    return fallback


# region FUNC_main
class _ContextInitArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    name: ClassVar[str]
    name_opt: ClassVar[str]
    description: ClassVar[str]
    org: ClassVar[str]
    node: ClassVar[str]
    node_yaml: ClassVar[str]
    skip_gh_repo: ClassVar[bool]
    projects_dir: ClassVar[Path]


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

    args = parser.parse_args(argv, namespace=_ContextInitArgs())

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

    # ruff: ignore[PLW0717] — тело try >5 операторов (длинный main-блок scaffold) — извлечение неразумно
    try:
        validate_name(context_name)

        context_dir = Path(args.projects_dir) / context_name
        if check_idempotent(context_dir):
            return 0

        create_dirs(context_dir)

        skeleton_path = context_dir / "platform" / "node-configs" / args.node / "node.yaml"
        create_skeleton_node_yaml(skeleton_path, context_name, args.org)

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
            # Preference: existing overlay node.yaml → ai-platform source fixture →
            # fresh skeleton (fallback; см. TRAP[DECISION] у _resolve_platform_node_yaml)
            platform_yaml = _resolve_platform_node_yaml(Path(args.projects_dir), args.node, fallback=str(skeleton_path))

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
            node=args.node,
        )

        elapsed = time.time() - start_time
        logger.info("[IMP:9][context][main] context-init COMPLETE — %.0fs", elapsed)
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code
    else:
        return 0


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        # W11: getattr(logging, str) → Any; level must be int for basicConfig
        level=cast(int, getattr(logging, os.environ.get("LOG_LEVEL", "INFO"))),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
