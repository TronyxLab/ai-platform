#!/usr/bin/env python3
# GREP_SUMMARY: context_initializer scaffold context overlay platform node-configs gh-repo deploy-key registration idempotent skeleton overlay-deploy-key-install node-side
# STRUCTURE: ▶ validate_name → ⚡ check_idempotent ─┬─ create_dirs(platform/{node-configs,modules/hermes-agent,projects}) ─┬─ create_skeleton_node_yaml ── gh_repo_create(<ctx>-overlay) ──◇ provision_deploy_key(read-only) ── register_in_platform_yaml ── report_summary(+node-side install runbook) ── ◇ CLI install-node-deploy-key → install_overlay_deploy_key_node_side (node-side key+known_hosts TOFU pin+alias via SSH/core channel)
# region MODULE_CONTRACT
## @purpose  Python Strangler-Fig migration of context-init.sh (364 LOC shell).
##           Scaffolds a new deployment context: nested overlay directory structure
##           (platform/{node-configs,modules/hermes-agent,projects}), skeleton node.yaml,
##           ONE GitHub overlay repo (`<org>/<ctx>-overlay`) with read-only deploy key,
##           and registration in platform node.yaml. Plus (DevPlan 029 T6)
##           install_overlay_deploy_key_node_side — node-side установка overlay deploy key
##           + SSH-алиаса github.com-overlay по SSH/core-каналу во время operator bootstrap.
## @scope    Developer/operator machine: context-init scaffold — локальный (no SSH/VPS);
##           install-node-deploy-key subcommand выполняется на машине оператора в
##           bootstrap.sh (после SCP-фазы) и ходит на ноду по SSH (ключ — через stdin).
##           Called from context-init.sh facade / bootstrap.sh overlay-key step.
## @invariants
##   - Idempotent: if ~/projects/<name>/ exists → SKIP (exit 0)
##   - Canonical layout (DevPlan 022): весь overlay — под `<ctx>/platform/`; сестринские
##     hermes-agent/ + node-configs/ НЕ создаются
##   - Один GitHub-репо `<org>/<ctx>-overlay` (private); `<ctx>-node-configs` / `<ctx>-hermes-agent`
##     упразднены как отдельные репо (DevPlan 022 D3/D6)
##   - Skeleton node.yaml preserves GREP_SUMMARY/STRUCTURE semantic markup;
##     repos.core = SSH-алиасный URL `git@github.com-overlay:<org>/<ctx>-overlay.git`
##     (DevPlan 024 D2; SSH-алиас `github.com-overlay` ставится на ноде по runbook
##     core/internal/bootstrap/AGENTS.md — VPS-доступ к приватному overlay)
##   - Deploy key (DevPlan 024 D2): read-only (`gh repo deploy-key add` БЕЗ --allow-write);
##     keypair в `<ctx>/.secrets/` (0600/0644, вне platform/-репо); repo-side автоматизирован,
##     node-side — ручной шаг по runbook (печатается в summary)
##   - GitHub repo creation is optional (--skip-gh-repo flag)
##   - Registration delegates to context_registry.py (105 LOC, stable)
##   - All steps are independent — continues on non-fatal gh failures
##   - Exit codes: 0=success/skip, 1=validation error, 2=registration error
## @rationale Step 1 of Scaffold → Declare → Apply workflow. Zero inline python3.
##            context_registry.py already exists — delegates, doesn't reimplement.
## @links    CALLED_BY: context-init.sh (facade); bootstrap.sh overlay-key step (T6)
##           CALLS: context_registry.register_context(); NodeYaml; shared.ssh_opts.SSH_OPTS
##           DP-092 Wave 2; DevPlan 022 TASK-2 (nested layout + single overlay repo);
##           DevPlan 024 TASK-2 (deploy key + SSH-алиасный repos.core);
##           DevPlan 029 TASK-6 (node-side overlay deploy key via core/SSH channel)
## @changes  2026-09-03 · Live-node fix — install_overlay_deploy_key_node_side remote script:
##           github.com host-key TOFU pin (ssh-keygen -F guard + ssh-keyscan) between key install
##           and alias block — wiped known_hosts (VPS re-provision) broke overlay clone
##           ("Host key verification failed", bootstrap exit 10)
## @changes  2026-09-03 · F2 amplifier fix (DevPlan 031 T2) — missing/empty dev overlay key при
##           SSH-алиасном repos.core: WARN-skip (exit 0) → FAIL-LOUD (PlatformFatalError exit 10);
##           ретро-контекст БЕЗ git@github.com-overlay: repos.core — по-прежнему exit 0 skip
## @changes  2026-09-02 · DevPlan 029 T6 — install_overlay_deploy_key_node_side (node-side
##           overlay deploy key via core/SSH channel) + CLI subcommand install-node-deploy-key
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

from core.internal.shared.deploy_paths import projects_base  # canonical PROJECTS_BASE resolver (F-017)
from core.internal.shared.exceptions import (
    PlatformError,  # hoisted — except-ветка main() читает имя (reportPossiblyUnboundVariable)
)

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────
_DEFAULT_PROJECTS_DIR = Path(os.environ.get("HOME", "/"), "projects")
_DEFAULT_NODE = os.environ.get("NODE", "tronyx-vps")
_DEFAULT_ORG = os.environ.get("NODE_ORG", "tronyx-lab")

# Operator-side projects root for the overlay dev key (DevPlan 029 T6):
#   <projects_root>/<ctx>/.secrets/<ctx>-overlay-deploy-key
# canonical location per root AGENTS.md «Корневой контракт ~/projects/» (dev key —
# ~/projects/<ctx>/.secrets/, runbook core/internal/bootstrap/AGENTS.md). shared/deploy_paths
# already defines the canonical PROJECTS_BASE resolver (env → /opt/projects → dev-fallback
# ~/projects on the operator machine, plan 012 T18/F-017) — reuse it instead of a new literal.
DEFAULT_PROJECTS_ROOT: Path = projects_base()

# ── Skeleton node.yaml template (preserve GREP_SUMMARY/STRUCTURE) ─────
_SKELETON_TEMPLATE = """# GREP_SUMMARY: {context_name} node context declarative apply declarative-deploy
# STRUCTURE: ▶ resolve → ┌contexts+node+modules┐ → ◇ validate ← ⊕ projects+secrets+firewall → ⚡ apply

# Skeleton node.yaml for context '{context_name}'.
# MUST EDIT: Replace placeholder values below with actual configuration.

# Deployment context this node belongs to (contexts[] canon — invariant 3, DevPlan 116 B6)
contexts:
  - name: {context_name}

# --- Context overlay repo (DevPlan 022: единственный overlay-репо контекста; 024: SSH-алиас) ---
repos:
  core: git@github.com-overlay:{org}/{context_name}-overlay.git

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
##              `git@github.com-overlay://<ctx>-overlay.git` в skeleton.
def create_skeleton_node_yaml(path: Path, context_name: str, org: str) -> None:
    """Create skeleton node.yaml for the new context.

    ## @purpose  DevPlan 022 TASK-2: skeleton с repos.core; DevPlan 024 TASK-2:
    ##           repos.core = SSH-алиасный URL `git@github.com-overlay:<org>/<ctx>-overlay.git`.
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


# region FUNC__default_subprocess_runner
def _default_subprocess_runner(cmd: list[str]) -> tuple[int, str, str]:
    """Execute a CLI command via subprocess (default DI runner: gh / ssh-keygen).

    ## @purpose  Общий дефолт gh_runner/keygen_runner (024 TASK-2: extracted из gh_repo_create
    ##            для переиспользования в provision_deploy_key).
    ## @io        ⇥ cmd → ⎋ (returncode, stdout, stderr); FileNotFoundError → (-1, "", "<bin>: command not found")
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return -1, "", f"{cmd[0]}: command not found"
    else:
        return result.returncode, result.stdout, result.stderr


# endregion FUNC__default_subprocess_runner


# region FUNC__default_ssh_runner
def _default_ssh_runner(cmd: list[str], stdin_text: str) -> tuple[int, str, str]:
    """Run ssh via subprocess with the secret payload on stdin (default DI runner, T6).

    ## @purpose  Default for install_overlay_deploy_key_node_side. KEY CONTENT rides on
    ##            stdin (subprocess input) — NEVER in argv (no ps/process-list leak).
    ## @io        ⇥ cmd, stdin_text → ⎋ (returncode, stdout, stderr);
    ##            FileNotFoundError → (-1, "", "<bin>: command not found");
    ##            TimeoutExpired → (-1, "", "ssh timed out after 60s")
    ## @complexity O(1) — single subprocess.run (timeout 60)
    """
    try:
        result = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError:
        return -1, "", f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return -1, "", "ssh timed out after 60s"
    else:
        return result.returncode, result.stdout, result.stderr


# endregion FUNC__default_ssh_runner


# region FUNC_provision_deploy_key
## @purpose  Provision read-only deploy key for VPS access to the private `<ctx>`-overlay repo
##           (DevPlan 024 TASK-2, D2/D3).
## @param org           GitHub org/username
## @param ctx           Context name
## @param context_dir   Context directory — keypair lands in `<context_dir>/.secrets/` (ВНЕ platform/-репо)
## @param gh_runner     Injectable gh CLI callable (DI test seam)
## @param keygen_runner Injectable ssh-keygen callable (DI test seam; None → subprocess)
## @return   (pub_key_path | None, warnings: int)
## @complexity O(1) — subprocess calls (ssh-keygen, gh)
## @invariants
##   - Read-only: `gh repo deploy-key add` БЕЗ `--allow-write` (D2)
##   - Keypair: `<context_dir>/.secrets/<ctx>-overlay-deploy-key` — 0600 приватный / 0644 pub;
##     каталог контекста НЕ git-репо (репо — только platform/) → риск коммита исключён геометрией (D3)
##   - Idempotent: приватный ключ существует → keygen SKIP, pub переиспользуется в add
##   - Graceful (D2): gh недоступен/не авторизован → warn + warnings+1, БЕЗ keygen, continue
##     (fresh-context-first: нода может не существовать в момент scaffold — node-side установка
##     НЕ автоматизируется, печатается в summary по runbook bootstrap/AGENTS.md)
##   - Дубликат в repo → «already exists» = success (паттерн reuse gh_repo_create)
def provision_deploy_key(
    org: str,
    ctx: str,
    context_dir: Path,
    *,
    gh_runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
    keygen_runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
) -> tuple[str | None, int]:
    """Provision a read-only deploy key for the context overlay repo.

    ## @purpose  DevPlan 024 TASK-2: ssh-keygen ed25519 → `gh repo deploy-key add` (read-only)
    ##            → путь pub-ключа для summary. Возвращает (path | None, warnings).
    ## @io        ⇥ org, ctx, context_dir, gh_runner, keygen_runner → ⎋ (pub_path | None, warnings)
    ## @complexity O(1)
    """
    warnings = 0
    overlay_repo = f"{org}/{ctx}-overlay"
    if gh_runner is None:
        gh_runner = _default_subprocess_runner
    if keygen_runner is None:
        keygen_runner = _default_subprocess_runner

    # Graceful guard: без gh (недоступен/не авторизован) провижининг невозможен —
    # keygen НЕ вызывается, node-side runbook печатается в report_summary (D2).
    rc, _, _ = gh_runner(["gh", "--version"])
    if rc != 0:
        logger.info("[IMP:9][context][deploy-key] WARNING: gh CLI not found — deploy key NOT provisioned")
        print("  ⚠️  gh CLI not found — deploy key not provisioned (install manually, see summary)")
        return None, 1
    rc, _, _ = gh_runner(["gh", "auth", "status"])
    if rc != 0:
        logger.info("[IMP:9][context][deploy-key] WARNING: gh CLI not authenticated — deploy key NOT provisioned")
        print("  ⚠️  gh not authenticated — deploy key not provisioned (install manually, see summary)")
        return None, 1

    secrets_dir = context_dir / ".secrets"
    key_path = secrets_dir / f"{ctx}-overlay-deploy-key"
    pub_path = secrets_dir / f"{ctx}-overlay-deploy-key.pub"

    # Keypair (идемпотентно: существующий ключ переиспользуется)
    if key_path.exists():
        logger.info("[IMP:8][context][deploy-key] Keypair already exists — reuse: %s", key_path)
    else:
        logger.info("[IMP:8][context][deploy-key] Generating ed25519 keypair: %s", key_path)
        rc, _, stderr = keygen_runner([
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-q",
            "-C",
            f"overlay-deploy-{ctx}",
            "-f",
            str(key_path),
        ])
        if rc != 0:
            logger.info("[IMP:9][context][deploy-key] WARNING: ssh-keygen failed: %s", stderr.strip())
            print("  ⚠️  ssh-keygen failed — deploy key not provisioned (see summary runbook)")
            return None, 1

    if not pub_path.exists():
        logger.info("[IMP:9][context][deploy-key] WARNING: pub key missing after keygen: %s", pub_path)
        print("  ⚠️  deploy key .pub missing — deploy key not provisioned (see summary runbook)")
        return None, 1

    # Канонические права: приватный 0600 / pub 0644 (применяется и к reuse-ветке)
    key_path.chmod(0o600)
    pub_path.chmod(0o644)

    # Repo-side: read-only deploy key (БЕЗ --allow-write); дубликат = success (reuse)
    rc, stdout, stderr = gh_runner([
        "gh",
        "repo",
        "deploy-key",
        "add",
        str(pub_path),
        "--repo",
        overlay_repo,
        "--title",
        f"vps-{ctx}-readonly",
    ])
    if rc == 0:
        logger.info("[IMP:9][context][deploy-key] Read-only deploy key added to %s: %s", overlay_repo, pub_path)
        print(f"  ✅ Deploy key (read-only) added to {overlay_repo}: {pub_path}")
    elif "already exists" in (stdout + stderr).lower():
        logger.info("[IMP:9][context][deploy-key] Deploy key already exists in %s — reuse", overlay_repo)
        print(f"  ✅ Deploy key already exists in {overlay_repo} (reuse)")
    else:
        logger.info(
            "[IMP:9][context][deploy-key] WARNING: deploy-key add failed for %s: %s",
            overlay_repo,
            stderr.strip(),
        )
        print(f"  ⚠️  Failed to add deploy key to {overlay_repo}: {stderr.strip()}")
        warnings += 1

    return str(pub_path), warnings


# endregion FUNC_provision_deploy_key


# region FUNC_install_overlay_deploy_key_node_side
## @purpose  Install the context overlay deploy key + github.com-overlay SSH alias ON the node
##           over the SSH/core channel during operator-side bootstrap (DevPlan 029 T6, AC5).
##           Automates the manual node-side runbook steps (scp + chmod 600 + github.com
##           host-key TOFU pin + ~/.ssh/config Host github.com-overlay block) of
##           core/internal/bootstrap/AGENTS.md
##           section 'VPS-доступ к приватному overlay (deploy key)'.
## @param node_yaml     Path to node.yaml — contexts[0].name (get_context) + repos.core
## @param ssh_host      SSH host of the node; ssh runs as root@<ssh_host>
## @param projects_root Operator projects base; dev key at
##                      {projects_root}/{ctx}/.secrets/{ctx}-overlay-deploy-key
##                      (None -> DEFAULT_PROJECTS_ROOT = deploy_paths.projects_base())
## @param ssh_runner    Injectable (cmd, stdin_text) -> (rc, stdout, stderr) runner (DI seam)
## @param dry_run       Print the install plan and return 0 (no SSH)
## @return   0 — installed / skipped (no alias repos.core, dry-run)
## @raises   PlatformFatalError (exit 10) — dev overlay deploy key missing at canonical
##           location (F2 fail-loud, DevPlan 031 T2) ИЛИ ssh rc != 0 (manual remediation)
## @complexity O(1) — one ssh call (remote script via bash -s, key on stdin)
## @invariants
##   - Key content NEVER in argv: ssh stdin carries remote-script + key (quoted heredoc);
##     cmd list — ssh flags from shared.ssh_opts.SSH_OPTS (SoT — no flag duplication)
##   - Skip semantics (exit 0, no noise): no contexts[0].name / repos.core does not start
##     with git@github.com-overlay: (не-алиасный контекст — ретро без overlay-ключа не в
##     скоупе автоматизации, см. runbook)
##   - F2 fail-loud (DevPlan 031 T2): repos.core SSH-алиасный + dev-ключ ОТСУТСТВУЕТ в
##     {projects_root}/{ctx}/.secrets/{ctx}-overlay-deploy-key (или файл пуст) → exit 10
##     с remediation, НЕ молчаливый WARN-skip: без ключа нода НЕ получит ключ+алиас →
##     overlay-clone гарантированно упадёт (production-outage amplifier 2026-09-03,
##     asi "Could not resolve hostname"); silent WARN превращал «забыли ключ» в outage без сигнала
##   - Fresh-context-first closed: install runs during bootstrap — node IS reachable
##     (SSH_HOST), so the key is installed right away (AC5)
##   - Remote script is idempotent: grep 'Host github.com-overlay' -> append only if absent;
##     github.com host key pinned TOFU (ssh-keygen -F guard -> ssh-keyscan only when absent)
##     so a wiped ~/.ssh/known_hosts (VPS re-provision) cannot fail the overlay clone
##     (live-node blocker 2026-09-03)
def install_overlay_deploy_key_node_side(
    *,
    node_yaml: str,
    ssh_host: str,
    projects_root: Path | None = None,
    ssh_runner: Callable[[list[str], str], tuple[int, str, str]] | None = None,
    dry_run: bool = False,
) -> int:
    """Install overlay deploy key + known_hosts TOFU pin + ssh alias on the node (DevPlan 029 T6).

    ## @purpose  AC5: bootstrap clones the private overlay without manual scp/chmod/ssh-config —
    ##            the key is installed over the SSH/core channel during operator bootstrap.
    ## @io        ⇥ node_yaml, ssh_host, projects_root, ssh_runner, dry_run → ⎋ int (0 | raise 10)
    ## @complexity O(1)
    """
    from core.internal.shared.exceptions import PlatformFatalError
    from core.internal.shared.node_yaml import NodeYaml
    from core.internal.shared.ssh_opts import SSH_OPTS

    node = NodeYaml(node_yaml)
    ctx = node.get_context()
    repo_val = node.get("repos.core", default="")
    repo = repo_val if isinstance(repo_val, str) else ""

    overlay_prefix = "git@github.com-overlay:"
    if not ctx or not repo.startswith(overlay_prefix):
        logger.info(
            "[IMP:9][context][overlay-key][skip] node.yaml has no SSH-alias repos.core "
            "(context=%r, repos.core=%r) — node-side overlay key install skipped (exit 0)",
            ctx,
            repo,
        )
        return 0

    base = DEFAULT_PROJECTS_ROOT if projects_root is None else projects_root
    key_path = base / ctx / ".secrets" / f"{ctx}-overlay-deploy-key"
    org = repo[len(overlay_prefix) :].split("/", 1)[0]

    if not key_path.is_file():
        # ⚠️ TRAP[BUG] · 2026-09-03 · P1 · F2 silent-skip amplifier (DevPlan 031 T2)
        # · Symptom: asi overlay-clone production-outage «Could not resolve hostname» — инсталлер
        # ·   НЕ установил ключ+алиас на ноду, clone упал, сигнала на этапе установки не было.
        # · Root: repos.core SSH-алисаный + dev-ключа нет в ~/projects/<ctx>/.secrets/ → WARN +
        # ·   exit 0 («ретро-контекст, ручная установка могла состояться»). Silent-skip превращал
        # ·   «забыли положить ключ» в outage БЕЗ сигнала — отказ проявлялся только на ноде (clone).
        # · Fix: alias repos.core + отсутствующий/пустой dev-ключ → fail-loud PlatformFatalError
        # ·   (exit 10) с remediation на КАНОНИЧЕСКУЮ локацию; не-алисаный repos.core — exit 0 skip.
        # · Prevention: любой bootstrap-шаг, без которого гарантированно упадёт следующий шаг,
        # ·   обязан сигнализировать loud-отказом на этапе установки, а не exit 0.
        logger.error(
            "[IMP:10][context][overlay-key][fatal] dev overlay deploy key MISSING: %s — repos.core "
            "uses git@github.com-overlay: alias, node-side key install is REQUIRED for overlay clone",
            key_path,
        )
        msg = (
            f"Overlay deploy key not found at canonical location: {key_path} — repos.core of "
            f"context {ctx!r} uses the SSH alias git@github.com-overlay:, so the node CANNOT clone "
            f"the private overlay without this key (F2 silent-skip amplifier, DevPlan 031 T2). "
            f"Remediation: place the read-only keypair at {key_path} (0600) and rerun bootstrap — "
            f"see runbook core/internal/bootstrap/AGENTS.md 'VPS-доступ к приватному overlay "
            f"(deploy key)' steps 1-2 (keygen + gh repo deploy-key add). "
            f"If the node already has a manually installed key, copy THAT keypair here (canonical "
            f"location is the single source of truth for re-runs)."
        )
        raise PlatformFatalError(msg)

    if dry_run:
        logger.info(
            "[IMP:9][context][overlay-key][dry-run] WOULD install overlay deploy key %s + "
            "github.com-overlay ssh alias on root@%s (ctx=%s)",
            key_path,
            ssh_host,
            ctx,
        )
        return 0

    key_text = key_path.read_text(encoding="utf-8").strip()
    if not key_text:
        # F2 fail-loud (DevPlan 031 T2): пустой key-файл семантически = отсутствующему —
        # нода не получит рабочий ключ, overlay-clone упадёт. Не WARN-skip (см. TRAP[BUG] выше).
        logger.error(
            "[IMP:10][context][overlay-key][fatal] dev overlay deploy key EMPTY: %s — repos.core "
            "uses git@github.com-overlay: alias, node-side key install is REQUIRED for overlay clone",
            key_path,
        )
        msg = (
            f"Overlay deploy key file is empty: {key_path} — repos.core of context {ctx!r} uses the "
            f"SSH alias git@github.com-overlay:, so the node CANNOT clone the private overlay (F2 "
            f"fail-loud, DevPlan 031 T2). Remediation: replace with the real read-only keypair "
            f"(0600) and rerun bootstrap."
        )
        raise PlatformFatalError(msg)

    # Remote script executed as root via 'ssh root@<host> bash -s'. The key rides on stdin
    # INSIDE a quoted heredoc: bash parses heredocs deterministically (no read-ahead race) and
    # the key content is present ONLY on the ssh channel — never in argv/process list.
    remote_lines = [
        "set -euo pipefail",
        'install -d -m 0700 "$HOME/.ssh"',
        "cat > \"$HOME/.ssh/id_ed25519_github_overlay\" <<'__OVERLAY_DEPLOY_KEY_EOF__'",
        key_text,
        "__OVERLAY_DEPLOY_KEY_EOF__",
        'chmod 600 "$HOME/.ssh/id_ed25519_github_overlay"',
        # TOFU host-key pin for github.com: a wiped ~/.ssh/known_hosts (VPS re-provision/wipe)
        # otherwise fails the overlay clone with "Host key verification failed" even though
        # key+alias are installed (live-node blocker 2026-09-03, bootstrap exit 10).
        # Guard = idempotent (already-pinned nodes untouched); keyscan failure must abort
        # (set -euo pipefail, NO || true) — unreachable GitHub is a loud install failure.
        'if ! ssh-keygen -F github.com -f "$HOME/.ssh/known_hosts" >/dev/null 2>&1; then',
        '  ssh-keyscan -t rsa,ecdsa,ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null',
        "fi",
        "if ! grep -q 'Host github.com-overlay' \"$HOME/.ssh/config\" 2>/dev/null; then",
        "  cat >> \"$HOME/.ssh/config\" <<'__OVERLAY_SSH_ALIAS_EOF__'",
        "Host github.com-overlay",
        "  HostName github.com",
        "  IdentityFile ~/.ssh/id_ed25519_github_overlay",
        "  IdentitiesOnly yes",
        "__OVERLAY_SSH_ALIAS_EOF__",
        "fi",
        'chmod 600 "$HOME/.ssh/config"',
        "",
    ]
    remote_script = "\n".join(remote_lines)

    cmd = ["ssh", *SSH_OPTS, f"root@{ssh_host}", "bash", "-s"]
    if ssh_runner is None:
        ssh_runner = _default_ssh_runner
    rc, stdout, stderr = ssh_runner(cmd, remote_script)

    if rc != 0:
        detail = (stderr.strip() or stdout.strip()) or "unknown ssh error"
        logger.error(
            "[IMP:10][context][overlay-key][install] ssh overlay deploy-key install failed on root@%s (rc=%s): %s",
            ssh_host,
            rc,
            detail,
        )
        msg = (
            f"Overlay deploy-key install on root@{ssh_host} failed (ssh rc={rc}): {detail} — "
            f"fix SSH access to the node and rerun bootstrap, or install the key manually per "
            f"runbook bootstrap/AGENTS.md 'VPS-доступ к приватному overlay (deploy key)' "
            f"(scp {key_path} <node>:~/.ssh/id_ed25519_github_overlay + chmod 600 + "
            f"ssh-config Host github.com-overlay)"
        )
        raise PlatformFatalError(msg)

    logger.info(
        "[IMP:9][context][overlay-key][install] Overlay deploy key + github.com-overlay alias "
        "installed on root@%s (ctx=%s, org=%s)",
        ssh_host,
        ctx,
        org,
    )
    return 0


# endregion FUNC_install_overlay_deploy_key_node_side


# region FUNC_gh_repo_create
## @purpose  Create the single context overlay GitHub repo (optional).
## @param org          GitHub org/username
## @param ctx          Context name
## @param skip         Skip repo creation flag
## @param context_dir  Path to context directory (git init+push on context_dir/platform)
## @param gh_runner    Injectable gh CLI callable (for testing)
## @param git_runner   Injectable git callable (DI test seam; None → subprocess)
## @param keygen_runner Injectable ssh-keygen callable for provision_deploy_key (DI test seam; None → subprocess)
## @return   (overlay_repo: str | None, reserved: None, warnings: int)
##           Второй элемент зарезервирован под прежний контракт (hermes_agent_repo) —
##           всегда None с DevPlan 022 (репо `<ctx>-hermes-agent` упразднён).
## @complexity O(1) — subprocess calls
## @invariants
##   - РОВНО ОДИН репо: `<org>/<ctx>-overlay` (private) — DevPlan 022 D3/D6;
##     `<ctx>-node-configs` / `<ctx>-hermes-agent` упразднены
##   - Git init+push выполняется на context_dir/platform (весь overlay — один репо)
# 🧐 TRAP[DECISION] · 2026-09-01 · — · Node-side доставка deploy key — РУЧНОЙ шаг по runbook (DevPlan 024 D2/D3)
# · Rejected: (1) SSH-install на scaffold — нода может не существовать/не быть забутстраплена
#   (fresh-context-first: контекст создаётся раньше ноды); scaffold получил бы SSH-зависимость
#   и новые failure-моды; (2) sops-канал `OVERLAY_DEPLOY_KEY` (secret-definitions.yaml + φ5) —
#   touch SoT-манифеста секретов и его гейтов
# · Reason: scaffold автоматизирует ТОЛЬКО repo-side (keygen + gh repo deploy-key add +
#   SSH-алиасный URL); ключ нужен на ноде к моменту первого deploy-context/ensure_context_repo,
#   а не к моменту scaffold; node-side install-инструкция печатается в report_summary,
#   канон — runbook core/internal/bootstrap/AGENTS.md («VPS-доступ к приватному overlay»)
# · Rev: второй контекст / следующий fresh-node bootstrap → автоматизация sops-каналом
#   (secret-definitions.yaml + φ5 secrets_provision)
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
    keygen_runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
) -> tuple[str | None, None, int]:
    """Create the single context overlay GitHub repo.

    ## @purpose  DevPlan 022 TASK-2: один overlay-репо вместо двух сестринских;
    ##            DevPlan 024 TASK-2: после подтверждения репо — provision_deploy_key
    ##            (read-only deploy key, до _git_init_and_push).
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
        gh_runner = _default_subprocess_runner

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
    elif "already exists" in (stdout + stderr).lower():
        logger.info("[IMP:9][context][gh] Repo already exists: %s", overlay_repo)
        created_overlay_repo = overlay_repo
    else:
        logger.info("[IMP:9][context][gh] WARNING: Failed to create %s: %s", overlay_repo, stderr.strip())
        warnings += 1

    # Deploy key — после подтверждения репо (created | already exists), до git init+push
    # (DevPlan 024 TASK-2). Graceful: провижининг не влияет на остальные шаги.
    if created_overlay_repo and context_dir:
        _key_path, key_warnings = provision_deploy_key(
            org, ctx, context_dir, gh_runner=gh_runner, keygen_runner=keygen_runner
        )
        warnings += key_warnings
        if created_overlay_repo and rc == 0:
            # Git init + push of the whole overlay (context_dir/platform)
            _git_init_and_push(context_dir / "platform", overlay_repo, ctx, git_runner=git_runner)

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
## @param deploy_key_pub       Path to provisioned deploy key .pub (optional; DevPlan 024 TASK-2)
## @io        stdout: formatted summary table
## @complexity O(1)
## @invariants  deploy_key_pub set → печатается строка Deploy key + node-side install-инструкция
##              (runbook core/internal/bootstrap/AGENTS.md) + предупреждение «не коммитить» (D3)
def report_summary(
    ctx_name: str,
    context_dir: Path,
    warnings: int,
    platform_yaml: str,
    node_cfg_repo: str | None = None,
    hermes_agent_repo: str | None = None,
    node: str | None = None,
    deploy_key_pub: str | None = None,
) -> None:
    """Print context init summary (nested overlay paths, DevPlan 022 TASK-2; deploy key, 024).

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
    if deploy_key_pub:
        print(f"│   ✅ Deploy key: {deploy_key_pub} → {node_cfg_repo} (read-only)")
    print(f"│   ✅ Registered in: {platform_yaml}")
    if deploy_key_pub:
        print("│")
        print("│ Node-side install (runbook: core/internal/bootstrap/AGENTS.md):")
        print(f"│   1. scp {deploy_key_pub.removesuffix('.pub')} <node>:~/.ssh/id_ed25519_github_overlay")
        print("│   2. ssh <node> chmod 600 ~/.ssh/id_ed25519_github_overlay")
        print("│   3. ~/.ssh/config on node:")
        print("│        Host github.com-overlay")
        print("│          HostName github.com")
        print("│          IdentityFile ~/.ssh/id_ed25519_github_overlay")
        print("│          IdentitiesOnly yes")
        print("│   4. Verify: git ls-remote git@github.com-overlay:<org>/<ctx>-overlay.git")
        print("│ ⚠️  NEVER commit the private key (context .secrets/ is OUTSIDE the platform/ git repo)")
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


class _InstallNodeDeployKeyArgs(argparse.Namespace):
    """Typed argparse namespace for the install-node-deploy-key subcommand (W11).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    node_yaml: ClassVar[str]
    ssh_host: ClassVar[str]
    projects_root: ClassVar[Path]
    dry_run: ClassVar[bool]


## @purpose  CLI for the install-node-deploy-key subcommand (DevPlan 029 T6): delegates to
##           install_overlay_deploy_key_node_side; catches PlatformError → exit code contract.
## @io        ⇥ rest_argv (после первого токена install-node-deploy-key) → ⎋ int
## @complexity O(1)
def _main_install_node_deploy_key(rest_argv: list[str]) -> int:
    """CLI body for 'context_initializer install-node-deploy-key' (T6).

    ## @purpose  Operator bootstrap hook: установка overlay deploy key + SSH-алиаса на ноде
    ##            по SSH/core-каналу. Exit-коды: 0 = ok/skip, e.exit_code на PlatformError.
    ## @io        ⇥ rest_argv → ⎋ int (contract T4: main() -> int)
    """
    parser = argparse.ArgumentParser(
        prog="context_initializer install-node-deploy-key",
        description=(
            "Install the context overlay deploy key + github.com-overlay ssh alias on the node "
            "over the SSH/core channel (DevPlan 029 T6). Skipped (exit 0) when node.yaml has no "
            "git@github.com-overlay: repos.core or the dev key is missing."
        ),
    )
    parser.add_argument("--node-yaml", required=True, help="Path to node.yaml (contexts[] + repos.core)")
    parser.add_argument("--ssh-host", required=True, help="SSH host of the node (installed as root@<host>)")
    parser.add_argument(
        "--projects-root",
        default=DEFAULT_PROJECTS_ROOT,
        help=f"Operator projects root (default: {DEFAULT_PROJECTS_ROOT})",
    )
    parser.add_argument("--dry-run", action="store_true", default=False, help="Print the install plan (no SSH)")

    args = parser.parse_args(rest_argv, namespace=_InstallNodeDeployKeyArgs())

    try:
        return install_overlay_deploy_key_node_side(
            node_yaml=args.node_yaml,
            ssh_host=args.ssh_host,
            projects_root=Path(args.projects_root),
            dry_run=args.dry_run,
        )
    except PlatformError as e:
        logger.critical(
            "[IMP:10][overlay-key][cli] FATAL: overlay deploy-key install failed (exit=%d): %s", e.exit_code, e
        )
        return e.exit_code


## @purpose  CLI entry point — full context scaffold orchestration
## @io        stdout: progress messages; exit 0 on success, 1 on validation error, 2 on registration error
## @complexity O(1) (subprocess calls for gh, otherwise pure Python)
def main(argv: list[str] | None = None) -> int:
    """CLI dispatcher for context initializer.

    ## @purpose  Parse args, orchestrate full context-init flow. Первый токен
    ##            'install-node-deploy-key' делегируется в _main_install_node_deploy_key
    ##            (DevPlan 029 T6) — остальной контракт main() неизменен.
    ## @io        ⇥ argv → ⎋ int exit code (contract T4: main() -> int)
    ## @complexity O(1)
    """
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    # Subcommand dispatch (DevPlan 029 T6): operator bootstrap overlay-key step.
    if raw_argv and raw_argv[0] == "install-node-deploy-key":
        return _main_install_node_deploy_key(raw_argv[1:])

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

    args = parser.parse_args(raw_argv, namespace=_ContextInitArgs())

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

        # Deploy key .pub — по файловой конвенции provision_deploy_key (DevPlan 024 D3):
        # файл существует ⇔ провижининг состоялся (gh-fail/skip-ветки keygen не создают).
        deploy_key_pub: str | None = None
        if context_dir:
            expected_pub = context_dir / ".secrets" / f"{context_name}-overlay-deploy-key.pub"
            if expected_pub.exists():
                deploy_key_pub = str(expected_pub)

        total_warnings = gh_warnings
        report_summary(
            ctx_name=context_name,
            context_dir=context_dir,
            warnings=total_warnings,
            platform_yaml=platform_yaml,
            node_cfg_repo=node_cfg_repo,
            hermes_agent_repo=hermes_agent_repo,
            node=args.node,
            deploy_key_pub=deploy_key_pub,
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
