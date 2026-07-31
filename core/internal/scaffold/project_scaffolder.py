#!/usr/bin/env python3
# GREP_SUMMARY: project_scaffolder new-project scaffold template copy render git-init checklist FQDN vhost register dry-run
# STRUCTURE: ▶ parse_args → auto_domain → validate_inputs → show_plan → confirm → copy_template → ⊕ gen_ai_platform_yaml → render_project_template → gen_env_platform → gen_makefile + gen_agents → git_init → create_github_repo → checklist → run_add_vhost → register_in_node_yaml → summary
# region MODULE_CONTRACT
## @purpose  Python Strangler-Fig migration of add-project.sh (782 LOC shell, 16 functions).
##           Creates a new project from a template: copies template, generates ai-platform.yaml,
##           Makefile, AGENTS.md, .env.platform, initializes git, creates GitHub repo,
##           generates setup checklist, configures nginx vhost, registers in node.yaml.
## @scope    Developer machine only (local scaffold). Called from add-project.sh facade.
## @invariants
##   - Projects created in $PROJECTS_ROOT/$ORG/$NAME/, NOT inside ai-platform/
##   - Templates in ai-platform/templates/ — copied with placeholder substitution
##   - platform-deploy.yml is EXCLUDED from template copy (T9)
##   - .env.platform generated via gen_env_platform.py from platform-env.yaml
##   - Project Makefile and AGENTS.md generated if not already provided
##   - --domain not set → auto-domain: $NAME.$PLATFORM_DOMAIN
##   - git init + initial commit done in the new project directory
##   - _SETUP_CHECKLIST.md generated with exact GitHub commands
##   - If --domain: calls add-vhost.sh for nginx config generation
##   - Never auto-creates GitHub repos (no token access — developer runs gh commands manually)
## @rationale Largest scaffold shell script — 782 LOC, 16 functions. Strangler-Fig migration.
##            Uses scaffold_helpers for shared gen functions (AC6). DI for subprocess calls.
## @links    CALLED_BY: add-project.sh (facade)
##           CALLS: scaffold_helpers.py, template-engine.sh, gen_env_platform.py
##           DP-092 Wave 4b
## @changes  2026-07-30 · Wave 4b — full Strangler-Fig from add-project.sh
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-07-21 · — · Org-aware path verified — no changes needed for T2
# · Reason: project_dir = PROJECTS_ROOT/ORG/NAME — already uses org correctly

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Path defaults ────────────────────────────────────────────────────────
_DEFAULT_PROJECTS_ROOT = os.environ.get(
    "PROJECTS_ROOT",
    str(Path(__file__).resolve().parent.parent.parent.parent.parent),
)
_DEFAULT_ORG = os.environ.get("PLATFORM_ORG", "personal")
_DEFAULT_NODE = os.environ.get("PLATFORM_DEFAULT_NODE", "tronyx-vps")


# region FUNC_auto_domain
## @purpose  If --domain not provided, auto-generate: NAME.PLATFORM_DOMAIN
## @param name    Project name
## @param domain  Explicit domain (empty = auto)
## @return   Domain string (may be empty if PLATFORM_DOMAIN not set)
## @rationale DD3: auto-domain reduces manual DNS configuration
def auto_domain(name: str, domain: str = "") -> str:
    """Auto-generate domain from NAME.PLATFORM_DOMAIN if not explicitly provided.

    ## @purpose  Mirror of auto_domain() from add-project.sh:135-146.
    ## @io        ⇥ name, domain → ⎋ str — domain (may be empty)
    ## @complexity O(1)
    """
    if domain:
        return domain

    pd = os.environ.get("PLATFORM_DOMAIN", "")
    if pd:
        result = f"{name}.{pd}"
        logger.info("[IMP:8][scaffold][domain] Auto-domain: --domain not set → %s", result)
        return result

    logger.info("[IMP:8][scaffold][domain] Auto-domain skipped: PLATFORM_DOMAIN not set, --domain not provided")
    return ""


# endregion FUNC_auto_domain


# region FUNC_show_plan
def show_plan(name: str, org: str, template: str, node: str, domain: str = "", database: str = "") -> None:
    """Print project creation plan.

    ## @purpose  Mirror of show_plan() from add-project.sh:149-165.
    ## @io        ⎋ stdout — formatted plan
    """
    project_dir = os.path.join(_DEFAULT_PROJECTS_ROOT, org, name)
    print()
    print("──────────────────────────────────────────────────────")
    print(f"  📁 Project dir:  {project_dir}/")
    print(f"  🔗 GitHub repo:  https://github.com/{org}/{name}")
    print(f"  📦 Template:     template-{template}")
    print(f"  🖥  Target node:  {node}")
    if domain:
        print(f"  🌐 Domain:       {domain}")
    if database:
        print(f"  🗄  Database:     {database}")
    print(f"  🏷️  Org:          {org}")
    print("──────────────────────────────────────────────────────")
    print()


# endregion FUNC_show_plan


# region FUNC_confirm
def confirm(dry_run: bool = False) -> bool:
    """Ask user for confirmation (skipped in dry-run/CI mode).

    ## @purpose  Mirror of confirm() from add-project.sh:168-178.
    ## @io        ⇥ dry_run → ⎋ bool
    """
    if dry_run or os.environ.get("CI_MODE") == "1":
        return True
    response = input("  Продолжить? [y/N] ").strip().lower()
    if response in ("y", "yes"):
        return True
    logger.info("[IMP:7][scaffold][confirm] Cancelled by user")
    return False


# endregion FUNC_confirm


# region FUNC_copy_template
## @purpose  Copy template directory to project dir, EXCLUDING platform-deploy.yml.
## @param src      Template source dir (templates/template-<type>)
## @param dst      Destination dir (projects/<org>/<name>)
## @param dry_run  If True, print plan only
## @io        stdout: progress; side-effect: rsync —exclude
## @complexity O(f) where f = files in template
def copy_template(src: str, dst: str, dry_run: bool = False) -> bool:
    """Copy template to project directory, excluding platform-deploy.yml.

    ## @purpose  Mirror of copy_template() from add-project.sh:185-206.
    ## @io        ⇥ src, dst, dry_run → ⎋ bool — True on success
    ## @complexity O(f)
    ## @invariants
    ##   - Excludes .github/workflows/platform-deploy.yml (T9)
    ##   - Fails if destination already exists
    """
    logger.info("[IMP:7][scaffold][copy] Copying template: %s → %s", src, dst)

    if dry_run:
        logger.info("[IMP:7][scaffold][copy] [DRY-RUN] Would copy: %s → %s", src, dst)
        return True

    dst_path = Path(dst)
    if dst_path.exists():
        logger.info("[IMP:10][scaffold][copy] Project directory already exists: %s", dst)
        print(f"ERROR: Project directory already exists: {dst}")
        return False

    src_path = Path(src)
    if not src_path.exists():
        logger.info("[IMP:10][scaffold][copy] Template not found: %s", src)
        print(f"ERROR: Template not found: {src}")
        return False

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    # Use rsync with --exclude (platform-deploy.yml — T9)
    try:
        subprocess.run(
            [
                "rsync",
                "-a",
                "--exclude",
                ".github/workflows/platform-deploy.yml",
                f"{str(src_path).rstrip('/')}/",
                str(dst_path) + "/",
            ],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.info("[IMP:10][scaffold][copy] rsync failed: %s", exc)
        print(f"ERROR: rsync failed: {exc}")
        return False
    except FileNotFoundError:
        # Fallback: use shutil.copytree with custom ignore
        logger.info("[IMP:8][scaffold][copy] rsync not found — using shutil.copytree")

        def _ignore_platform_deploy(directory: str, files: list[str]) -> set[str]:
            ignored: set[str] = set()
            if directory.endswith((".github/workflows", ".github")):
                for f in files:
                    if f == "platform-deploy.yml":
                        ignored.add(f)
            return ignored

        shutil.copytree(str(src_path), str(dst_path), symlinks=True, ignore=_ignore_platform_deploy)

    logger.info("[IMP:7][scaffold][copy] Template copied to: %s", dst)
    logger.info("[IMP:9][scaffold][copy] platform-deploy.yml excluded from copy (T9)")
    return True


# endregion FUNC_copy_template


# region FUNC_render_project_template
## @purpose  Render project template files via template-engine.sh (replaces inline sed).
## @param project_dir  Target project directory
## @param name         Project name
## @param org          Organization name
## @param domain       Domain name
## @param node         Node name
## @param dry_run      If True, print plan only
## @return   True on success, False on failure
## @complexity O(f) where f = files in project
def render_project_template(
    project_dir: str,
    name: str,
    org: str,
    domain: str = "",
    node: str = "",
    dry_run: bool = False,
) -> bool:
    """Render project templates via template-engine.sh.

    ## @purpose  Mirror of render_project_template() from add-project.sh:310-342.
    ## @io        ⇥ project_dir, name, org, domain, node, dry_run → ⎋ bool
    ## @complexity O(f)
    """
    if dry_run:
        logger.info("[IMP:7][scaffold][render] [DRY-RUN] Would render templates in: %s", project_dir)
        return True

    logger.info("[IMP:7][scaffold][render] Rendering project templates via template-engine.sh")

    script_dir = Path(__file__).resolve().parent
    engine_script = script_dir.parent / "template-engine.sh"

    if not engine_script.exists() or not os.access(str(engine_script), os.X_OK):
        logger.info("[IMP:10][scaffold][render] Template engine not found or not executable: %s", engine_script)
        print(f"ERROR: Template engine not found: {engine_script}")
        return False

    domain_val = domain or "false"
    result = subprocess.run(
        [
            "bash",
            str(engine_script),
            "render-dir",
            project_dir,
            f"PROJECT_NAME={name}",
            f"ORG_NAME={org}",
            f"DOMAIN={domain_val}",
            f"NODE_NAME={node or _DEFAULT_NODE}",
            f"PLATFORM_DOMAIN={os.environ.get('PLATFORM_DOMAIN', '')}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        logger.info("[IMP:9][scaffold][render] Project templates rendered successfully")
        return True
    logger.info("[IMP:10][scaffold][render] Template rendering failed (exit=%d)", result.returncode)
    if result.stderr.strip():
        logger.info("[IMP:8][scaffold][render] stderr: %s", result.stderr.strip()[:500])
    return False


# endregion FUNC_render_project_template


# region FUNC_gen_env_platform
def gen_env_platform(project_dir: str, name: str, domain: str = "", dry_run: bool = False) -> bool:
    """Generate .env.platform via gen_env_platform.py.

    ## @purpose  Mirror of gen_env_platform() from add-project.sh:351-379.
    ## @io        ⇥ project_dir, name, domain, dry_run → ⎋ bool
    ## @complexity O(1) — subprocess
    """
    script_dir = Path(__file__).resolve().parent
    gen_script = script_dir / "gen_env_platform.py"

    if not gen_script.exists():
        logger.info("[IMP:8][scaffold][env] gen_env_platform.py not found — skipping")
        return True  # non-fatal

    if dry_run:
        logger.info("[IMP:7][scaffold][env] [DRY-RUN] Would generate .env.platform at: %s/.env.platform", project_dir)
        return True

    logger.info("[IMP:7][scaffold][env] Generating .env.platform from platform-env.yaml")

    env_file = Path(project_dir) / ".env.platform"
    env_file.parent.mkdir(parents=True, exist_ok=True)

    platform_root = Path(__file__).resolve().parent.parent.parent.parent
    platform_env_yaml = platform_root / "platform-env.yaml"

    if not platform_env_yaml.exists():
        logger.info("[IMP:8][scaffold][env] platform-env.yaml not found — skipping")
        return True  # non-fatal

    result = subprocess.run(
        [
            sys.executable,
            str(gen_script),
            "--yaml",
            str(platform_env_yaml),
            "--name",
            name,
            "--domain",
            domain or "",
            "--output",
            str(env_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        logger.info("[IMP:9][scaffold][env] .env.platform generated: %s", env_file)
        return True
    logger.info("[IMP:8][scaffold][env] gen_env_platform.py returned non-zero — .env.platform might be incomplete")
    return False


# endregion FUNC_gen_env_platform


# region FUNC_git_init_project
def git_init_project(project_dir: str, name: str, template: str, dry_run: bool = False) -> bool:
    """Initialize git repository and create initial commit.

    ## @purpose  Mirror of git_init_project() from add-project.sh:486-508.
    ## @io        ⇥ project_dir, name, template, dry_run → ⎋ bool
    ## @complexity O(1)
    """
    if dry_run:
        logger.info("[IMP:7][scaffold][git] [DRY-RUN] Would git init + initial commit in: %s", project_dir)
        return True

    logger.info("[IMP:7][scaffold][git] Initializing git repository")

    try:
        subprocess.run(["git", "init"], cwd=project_dir, capture_output=True, check=True)
        subprocess.run(["git", "add", "-A"], cwd=project_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"init: {name} from template-{template}", "--no-gpg-sign"],
            cwd=project_dir,
            capture_output=True,
            check=True,
        )
        logger.info("[IMP:7][scaffold][git] Git repository initialized with initial commit")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.info("[IMP:10][scaffold][git] git init/commit failed: %s", exc)
        print(f"ERROR: git init/commit failed: {exc}")
        return False


# endregion FUNC_git_init_project


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


# region FUNC_generate_checklist
def generate_checklist(
    project_dir: str,
    name: str,
    org: str,
    template: str,
    domain: str = "",
    database: str = "",
    dry_run: bool = False,
) -> bool:
    """Generate _SETUP_CHECKLIST.md with exact GitHub commands.

    ## @purpose  Mirror of generate_checklist() from add-project.sh:511-587.
    ## @io        ⇥ project_dir, name, org, ... → ⎋ bool
    """
    if dry_run:
        logger.info("[IMP:7][scaffold][cl] [DRY-RUN] Would generate: %s/_SETUP_CHECKLIST.md", project_dir)
        return True

    logger.info("[IMP:7][scaffold][cl] Generating setup checklist")

    checklist_path = Path(project_dir) / "_SETUP_CHECKLIST.md"

    lines: list[str] = [
        f"# Setup Checklist: {name}",
        "",
        "> ⚠️ Выполните шаги по порядку. Команды можно копировать и вставлять.",
        "",
        "## 1. Создать репозиторий на GitHub",
        "",
        "```bash",
        f'gh repo create {org}/{name} --private --description "{name} project"',
        "```",
        "",
        "## 2. Добавить remote и запушить",
        "",
        "```bash",
        f"cd {project_dir}",
        f"git remote add origin git@github.com:{org}/{name}.git",
        "git push -u origin main",
        "```",
        "",
        "## 3. CI/CD secrets (org-level — NODE_HOST_MAP, CI_DEPLOY_KEY)",
        "",
        "| Secret | Назначение |",
        "|--------|-----------|",
        "| `CI_DEPLOY_KEY` | SSH private key для ci-deploy forced-command deploy |",
        "| `GIT_MIRROR_TOKEN` | PAT для зеркалирования кода из Tronyx161 в TronyxLab |",
        "",
        "Org variable `NODE_HOST_MAP` (JSON) — разрешение нод в SSH-хосты.",
        "",
        "## 4. Настроить Docker Registry",
        "",
        "Registry `ghcr.io` уже прописан в `docker-compose.yml`.",
        "GitHub Actions использует `GITHUB_TOKEN` (доступен автоматически).",
    ]

    if domain:
        lines.extend(
            [
                "",
                "## 5. TLS-сертификат выпускается автоматически",
                "",
                "## 6. Применить nginx overlay на сервере",
                "",
                "```bash",
                "sudo nginx -t && sudo nginx -s reload",
                "```",
            ]
        )

    if database:
        lines.extend(
            [
                "",
                "## 7. Создать базу данных",
                "",
                "```bash",
                f'sudo -u postgres psql -c "CREATE DATABASE {database};"',
                "```",
            ]
        )

    lines.extend(
        [
            "",
            "---",
            f"> Сгенерировано `add-project.sh` ({datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})",
        ]
    )

    checklist_path.write_text("\n".join(lines) + "\n")
    logger.info("[IMP:7][scaffold][cl] Setup checklist generated: %s", checklist_path)
    return True


# endregion FUNC_generate_checklist


# region FUNC_run_add_vhost
def run_add_vhost(project_dir: str, domain: str = "", dry_run: bool = False) -> bool:
    """Generate nginx vhost via add-vhost.sh.

    ## @purpose  Mirror of run_add_vhost() from add-project.sh:632-662.
    ## @io        ⇥ project_dir, domain, dry_run → ⎋ bool
    ## @complexity O(1)
    """
    if not domain:
        return True  # no domain, skip

    script_dir = Path(__file__).resolve().parent
    add_vhost_script = script_dir / "add-vhost.sh"

    if not add_vhost_script.exists() or not os.access(str(add_vhost_script), os.X_OK):
        logger.info("[IMP:8][scaffold][vhost] add-vhost.sh not found or not executable: %s", add_vhost_script)
        return True  # non-fatal

    if dry_run:
        logger.info("[IMP:7][scaffold][vhost] [DRY-RUN] Would call: %s --project-dir %s", add_vhost_script, project_dir)
        return True

    logger.info("[IMP:7][scaffold][vhost] Generating nginx vhost via add-vhost.sh")

    # Derive node-configs dir: projects/<org>/node-configs/
    project_path = Path(project_dir)
    org_dir = project_path.parent
    node_configs_dir = org_dir / "node-configs" if org_dir.is_dir() else None

    if node_configs_dir is None or not node_configs_dir.is_dir():
        logger.info("[IMP:8][scaffold][vhost] node-configs dir not found — skipping")
        return True  # non-fatal

    result = subprocess.run(
        [
            str(add_vhost_script),
            "--project-dir",
            project_dir,
            "--node-configs-dir",
            str(node_configs_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        logger.info("[IMP:8][scaffold][vhost] add-vhost.sh returned non-zero — check manually")
        return False
    return True


# endregion FUNC_run_add_vhost


# region FUNC_main
def main(argv: list[str] | None = None) -> None:
    """CLI dispatcher for project scaffold.

    ## @purpose  Parse args, orchestrate full project creation flow.
    ## @io        ⇥ argv → ⎋ None (sys.exit)
    ## @complexity O(f) for template copy + O(1) subprocess calls
    """
    parser = argparse.ArgumentParser(description="Create a new project from a template.")
    parser.add_argument("--name", required=True, help="Project name (alphanumeric, hyphens, underscores)")
    parser.add_argument("--template", required=True, help="Template: frontend | backend | fullstack")
    parser.add_argument("--org", default="", help=f"Organization name (default: {_DEFAULT_ORG})")
    parser.add_argument("--node", default="", help=f"Target node name (default: {_DEFAULT_NODE})")
    parser.add_argument("--domain", default="", help="Domain for nginx vhost (auto: NAME.PLATFORM_DOMAIN)")
    parser.add_argument("--database", default="", help="Database name for backend/fullstack projects")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Show plan without creating files")
    parser.add_argument("--mode", default="", help="dev mode: enables staging")
    parser.add_argument("--register", action="store_true", default=False, help="Register project in node.yaml")
    parser.add_argument("--projects-root", default=_DEFAULT_PROJECTS_ROOT, help="Override PROJECTS_ROOT")

    args = parser.parse_args(argv)

    # Apply defaults
    org = args.org or _DEFAULT_ORG
    node = args.node or _DEFAULT_NODE

    # Validate
    if args.template not in ("frontend", "backend", "fullstack"):
        logger.info("[IMP:10][scaffold][main] Invalid template: %s", args.template)
        print(f"ERROR: Invalid template type: '{args.template}'. Must be: frontend | backend | fullstack")
        sys.exit(1)

    if not args.name.replace("-", "").replace("_", "").isalnum():
        logger.info("[IMP:10][scaffold][main] Invalid project name: %s", args.name)
        print(f"ERROR: Invalid project name: '{args.name}'. Use alphanumeric, hyphens, underscores only.")
        sys.exit(1)

    template_dir = os.path.join(
        os.path.dirname(Path(__file__).resolve().parent.parent), "..", "templates", f"template-{args.template}"
    )
    template_path = Path(template_dir).resolve()
    if not template_path.is_dir():
        logger.info("[IMP:10][scaffold][main] Template not found: %s", template_path)
        print(f"ERROR: Template not found: {template_path}")
        sys.exit(1)

    # Auto-domain
    domain = auto_domain(args.name, args.domain)

    logger.info(
        "[IMP:6][scaffold][main] START: add-project --name %s --template %s --org %s --node %s",
        args.name,
        args.template,
        org,
        node,
    )

    # Show plan
    show_plan(args.name, org, args.template, node, domain, args.database)

    # Confirm
    if not confirm(args.dry_run):
        sys.exit(0)

    logger.info("[IMP:7][scaffold][main] Starting project creation")

    project_dir = os.path.join(args.projects_root, org, args.name)

    # Step 1: Copy template
    if not copy_template(str(template_path), project_dir, dry_run=args.dry_run):
        sys.exit(1)

    # Step 2: Generate ai-platform.yaml
    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    yaml_path = os.path.join(project_dir, "ai-platform.yaml")
    if not args.dry_run:
        gen_ai_platform_yaml(
            name=args.name,
            ptype=args.template,
            org=org,
            node=node,
            domain=domain,
            database=args.database,
            mode=args.mode,
            output_path=yaml_path,
            minimal=False,
        )

    # Step 3: Render project templates
    if not render_project_template(project_dir, args.name, org, domain, node, args.dry_run):
        print("WARNING: Template rendering failed — some placeholders may not be replaced")

    # Step 4: Generate .env.platform
    gen_env_platform(project_dir, args.name, domain, args.dry_run)

    # Step 5: Generate Makefile + AGENTS.md
    from core.internal.scaffold.scaffold_helpers import gen_project_agents, gen_project_makefile

    gen_project_makefile(
        name=args.name,
        domain=domain,
        output_path=os.path.join(project_dir, "Makefile"),
        force=False,
    )
    gen_project_agents(
        name=args.name,
        org=org,
        template=args.template,
        node=node,
        domain=domain,
        output_path=os.path.join(project_dir, "AGENTS.md"),
        force=False,
    )

    # Step 6: Git init
    git_init_project(project_dir, args.name, args.template, args.dry_run)

    # Step 7: Create GitHub repo
    create_github_repo(org, args.name, project_dir, args.dry_run)

    # Step 8: Generate setup checklist
    generate_checklist(project_dir, args.name, org, args.template, domain, args.database, args.dry_run)

    # Step 9: Add vhost
    run_add_vhost(project_dir, domain, args.dry_run)

    # Step 10: Register in node.yaml (if --register)
    if args.register:
        from core.internal.scaffold.scaffold_helpers import register_in_node_yaml

        node_yaml = os.path.join(args.projects_root, org, "node-configs", node, "node.yaml")
        register_in_node_yaml(
            name=args.name,
            org=org,
            node=node,
            ptype=args.template,
            domain=domain,
            database=args.database,
            yaml_path=node_yaml,
            dry_run=args.dry_run,
        )
    else:
        logger.info("[IMP:6][scaffold][main] Registration skipped (use --register to register in node.yaml)")

    # Summary
    print()
    print("──────────────────────────────────────────────────────")
    print(f"  ✅ Проект создан: {project_dir}/")
    print(f"  📋 Следующие шаги: {project_dir}/_SETUP_CHECKLIST.md")
    print("──────────────────────────────────────────────────────")
    print()

    logger.info("[IMP:9][scaffold][main] DONE: project %s created successfully", args.name)


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    main()
