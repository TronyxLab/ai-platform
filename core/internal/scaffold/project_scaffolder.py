#!/usr/bin/env python3
# GREP_SUMMARY: project_scaffolder new-project scaffold template copy render git-init checklist FQDN vhost register AI-PLATFORM.md dry-run
# STRUCTURE: ▶ parse_args → auto_domain → validate_inputs → show_plan → confirm → copy_template → ⊕ gen_ai_platform_yaml → render_project_template → gen_env_platform → gen_makefile + gen_agents + gen_platform_md → git_init → create_github_repo → checklist → run_add_vhost → register_in_node_yaml → summary
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
##           CALLS: scaffold_helpers.py, template_engine.py (render_directory_in_place), gen_env_platform.py
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
from typing import ClassVar, cast

from core.internal.shared.app_config import AppConfig
from core.internal.shared.exceptions import ConfigValidationError

# template_engine native import (DevPlan 094 Wave 2.C — 0 subprocess).
# Invocation: `python3 -m core.internal.scaffold.project_scaffolder` — project root
# is on sys.path via -m, so core.internal.template_engine resolves without PYTHONPATH.
from core.internal.template_engine import render_directory_in_place

logger = logging.getLogger(__name__)

# ── Path defaults (W4a: ЧИСТЫЕ константы — env резолвится в main() через AppConfig.from_env()) ──
_DEFAULT_PROJECTS_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
_DEFAULT_ORG = "personal"
_DEFAULT_NODE = "tronyx-vps"


# region FUNC_auto_domain
## @purpose  If --domain not provided, auto-generate: NAME.PLATFORM_DOMAIN
## @param name               Project name
## @param domain             Explicit domain (empty = auto)
## @param platform_domain    PLATFORM_DOMAIN value (None = ленивый env-фолбэк, DI)
## @return   Domain string (may be empty if PLATFORM_DOMAIN not set)
## @rationale DD3: auto-domain reduces manual DNS configuration
def auto_domain(name: str, domain: str = "", platform_domain: str | None = None) -> str:
    """Auto-generate domain from NAME.PLATFORM_DOMAIN if not explicitly provided.

    ## @purpose  Mirror of auto_domain() from add-project.sh:135-146.
    ## @io        ⇥ name, domain, platform_domain (None = ленивый env-фолбэк) → ⎋ str
    ## @complexity O(1)
    """
    if domain:
        return domain

    pd = platform_domain if platform_domain is not None else os.environ.get("PLATFORM_DOMAIN", "")
    if pd:
        result = f"{name}.{pd}"
        logger.info("[IMP:8][scaffold][domain] Auto-domain: --domain not set → %s", result)
        return result

    logger.info("[IMP:8][scaffold][domain] Auto-domain skipped: PLATFORM_DOMAIN not set, --domain not provided")
    return ""


# endregion FUNC_auto_domain


# region FUNC_show_plan
def show_plan(
    name: str,
    org: str,
    template: str,
    node: str,
    domain: str = "",
    database: str = "",
    projects_root: str | None = None,
) -> None:
    """Print project creation plan.

    ## @purpose  Mirror of show_plan() from add-project.sh:149-165.
    ## @io        ⎋ stdout — formatted plan
    """
    project_dir = Path(projects_root if projects_root is not None else _DEFAULT_PROJECTS_ROOT) / org / name
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
def confirm(*, dry_run: bool = False, ci_mode: str | None = None) -> bool:
    """Ask user for confirmation (skipped in dry-run/CI mode).

    ## @purpose  Mirror of confirm() from add-project.sh:168-178.
    ## @io        ⇥ dry_run, ci_mode (None = ленивый env-фолбэк) → ⎋ bool
    ## @invariants
    ##   - bool keyword-only (v1.0.1: FBT001/FBT002 — канон ruff.toml «НОВЫЙ код —
    ##     keyword-only bool», agent-check blocking на изменённом файле)
    """
    if dry_run or (ci_mode if ci_mode is not None else os.environ.get("CI_MODE")) == "1":
        return True
    response = input("  Продолжить? [y/N] ").strip().lower()
    if response in {"y", "yes"}:
        return True
    logger.info("[IMP:7][scaffold][confirm] Cancelled by user")
    return False


# endregion FUNC_confirm


# region FUNC_copy_template
## @purpose  Copy template directory to project dir, EXCLUDING platform-deploy.yml.
## @param src      Template source dir (templates/template-<type\>)
## @param dst      Destination dir (projects/<org\>/<name\>)
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
    ##   - v1.0.1: excludes локальный мусор (.ruff_cache/__pycache__/.pytest_cache/
    ##     node_modules/.DS_Store) — untracked-кеши шаблонной папки НЕ должны
    ##     попадать в новые проекты (TRAP[BUG] Фазы 3: .ruff_cache протекал через rsync)
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

    # v1.0.1: локальный мусор шаблонной папки НЕ копируется (untracked-кеши на dev-машине)
    TEMPLATE_EXCLUDES = (
        ".github/workflows/platform-deploy.yml",  # T9
        ".ruff_cache",
        "__pycache__",
        ".pytest_cache",
        "node_modules",
        ".DS_Store",
    )
    # Use rsync with --exclude (platform-deploy.yml — T9)
    try:
        rsync_cmd = [
            "rsync",
            "-a",
        ]
        for exc in TEMPLATE_EXCLUDES:
            rsync_cmd.extend(["--exclude", exc])
        rsync_cmd.extend([
            f"{str(src_path).rstrip('/')}/",
            str(dst_path) + "/",
        ])
        subprocess.run(rsync_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        logger.info("[IMP:10][scaffold][copy] rsync failed: %s", exc)
        print(f"ERROR: rsync failed: {exc}")
        return False
    except FileNotFoundError:
        # Fallback: use shutil.copytree with custom ignore
        logger.info("[IMP:8][scaffold][copy] rsync not found — using shutil.copytree")

        def _ignore_platform_deploy(directory: str, files: list[str]) -> set[str]:
            ignored: set[str] = set()
            for f in files:
                if f in TEMPLATE_EXCLUDES or f in {
                    ".ruff_cache",
                    "__pycache__",
                    ".pytest_cache",
                    "node_modules",
                    ".DS_Store",
                }:
                    ignored.add(f)
            if directory.endswith((".github/workflows", ".github")):
                for f in files:
                    if f == "platform-deploy.yml":
                        ignored.add(f)
            return ignored

        shutil.copytree(str(src_path), str(dst_path), symlinks=True, ignore=_ignore_platform_deploy)

    logger.info("[IMP:7][scaffold][copy] Template copied to: %s", dst)
    logger.info("[IMP:9][scaffold][copy] platform-deploy.yml + локальный мусор excluded from copy")
    return True


# endregion FUNC_copy_template


# region FUNC_render_project_template
## @purpose  Render project template files via template_engine.render_directory_in_place (native).
## @param project_dir  Target project directory
## @param name         Project name
## @param org          Organization name
## @param domain       Domain name
## @param node         Node name
## @param dry_run      If True, print plan only
## @param platform_domain  PLATFORM_DOMAIN value (None = ленивый env-фолбэк, DI)
## @return   True on success, False on failure
## @complexity O(f) where f = files in project
def render_project_template(
    project_dir: str,
    name: str,
    org: str,
    domain: str = "",
    node: str = "",
    dry_run: bool = False,
    platform_domain: str | None = None,
) -> bool:
    """Render project templates via template_engine.render_directory_in_place (native).

    ## @purpose  Mirror of render_project_template() from add-project.sh:310-342.
    ##            Direct in-process render — no subprocess, no shell wrapper
    ##            (DevPlan 094 Wave 2.C).
    ## @io        ⇥ project_dir, name, org, domain, node, dry_run,
    ##                platform_domain (None = ленивый env-фолбэк) → ⎋ bool
    ## @complexity O(f)
    """
    if dry_run:
        logger.info("[IMP:7][scaffold][render] [DRY-RUN] Would render templates in: %s", project_dir)
        return True

    logger.info("[IMP:7][scaffold][render] Rendering project templates via template_engine.render_directory_in_place")

    domain_val = domain or "false"
    errors = render_directory_in_place(
        project_dir,
        vars={
            "PROJECT_NAME": name,
            "ORG_NAME": org,
            "DOMAIN": domain_val,
            "NODE_NAME": node or _DEFAULT_NODE,
            "PLATFORM_DOMAIN": (
                platform_domain if platform_domain is not None else os.environ.get("PLATFORM_DOMAIN", "")
            ),
        },
    )

    if errors == 0:
        logger.info("[IMP:9][scaffold][render] Project templates rendered successfully")
        return True
    logger.info("[IMP:10][scaffold][render] Template rendering failed (%d error(s))", errors)
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

    # v1.0.1 (TRAP[BUG] Фаза 3): запуск скрипта ПО ПУТИ (`python core/internal/...`) ломал
    # `import core` (sys.path[0] = каталог скрипта, репо-корень не на path) → .env.platform
    # НИКОГДА не генерировался (non-fatal skip) → в проекте нет PLATFORM_* env → test_health
    # падал на localhost:80. Фикс: запуск МОДУЛЕМ с PYTHONPATH=platform_root.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(platform_root) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "core.internal.scaffold.gen_env_platform",
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
        env=env,
    )

    if result.returncode == 0:
        logger.info("[IMP:9][scaffold][env] .env.platform generated: %s", env_file)
        return True
    logger.info("[IMP:8][scaffold][env] gen_env_platform.py returned non-zero — .env.platform might be incomplete")
    return False


# endregion FUNC_gen_env_platform


# region FUNC_gen_project_practices
def gen_project_practices(project_dir: str, dry_run: bool = False) -> bool:
    """Generate baseline practices (DevPlan 137 W1 step 11): GENERATED files + practices.lock.

    ## @purpose  При new-project ВСЕГДА генерируются 5 GENERATED-файлов практик
    ##           (pyproject.toml, .pre-commit-config.yaml, tests/conftest.py,
    ##           tests/test_health.py, practices.lock) через sync_practices (K1-канон).
    ##           level=auto (из ai-platform.yaml quality.level) → свежий проект получает
    ##           state=baseline (эскалатор жив, решение пользователя 2026-08-05).
    ## @io        ⇥ project_dir, dry_run → ⎋ bool
    ## @complexity O(N * C) — рендер + атомарные записи
    ## @invariants
    ##   - Вызывается ПОСЛЕ gen_platform_md и ДО git_init (практики попадают в init-коммит)
    ##   - ВСЕГДА (не только для определённых типов) — baseline практики для всех шаблонов
    """
    if dry_run:
        logger.info("[IMP:7][scaffold][practices] [DRY-RUN] Would generate baseline practices in: %s", project_dir)
        return True

    from core.internal.practices.sync_practices import sync_practices

    report = sync_practices(Path(project_dir), force=True)
    logger.info(
        "[IMP:9][scaffold][practices] Baseline practices generated (state=%s, level=%s, lock=%s)",
        report.state,
        report.level,
        report.lock_status,
    )
    return True


# endregion FUNC_gen_project_practices


# region FUNC_scaffold_instructions
def scaffold_instructions(project_dir: str, template: str, dry_run: bool = False) -> bool:
    """Generate project .kilo instructions from the live canon (DevPlan 001 T5.2).

    ## @purpose  После gen_project_practices и ДО git_init: ai-instructions sync --project-dir
    ##           кладёт .kilo/ (rules/agents/skills с уровнями наследования по template) +
    ##           kilo.json instructions + ai-instructions.lock в init-коммит проекта.
    ##           Шаблоны НЕ содержат снапшот инструкций (TRAP §6) — генерация из живого канона.
    ## @io        ⇥ project_dir, template (backend|frontend), dry_run → ⎋ bool
    ## @invariants
    ##   - requires_instructions_version шаблона > pin платформы → fail (анти-дрейф, по образцу practices)
    ##   - Синхронизация идемпотентна (повторный scaffold — no-op по контенту)
    ##   - dev-оверрайд канона: env AI_INSTRUCTIONS_CANON_PATH (локальное дерево вместо pin-cache/clone)
    """
    platform_root = Path(__file__).resolve().parents[3]
    # ⚠️ TRAP[BUG] · 2026-08-16 · P1 · scaffold_instructions падал на ai_instructions (underscore)
    # · Symptom: new-project/adopt-project abort (return 1) — pins.yaml не находился,
    # ·   sync-команда получала несуществующий --config путь.
    # · Root: путь каталога писался ai_instructions (имя Python-модуля), а каталог в
    # ·   репозитории — core/internal/ai-instructions (kebab-case, DevPlan 001 T4.x).
    # · Fix: путь каталога — ai-instructions (дефис); имя модуля в -m остаётся ai_instructions.
    # · Prevention: pytest-тест scaffold_instructions_path (tests/unit/test_project_scaffolder.py).
    pins_path = platform_root / "core" / "internal" / "ai-instructions" / "ai-instructions-pins.yaml"

    if dry_run:
        logger.info("[IMP:7][scaffold][instructions] [DRY-RUN] Would sync instructions for: %s", project_dir)
        return True

    # ── Сверка requires_instructions_version (анти-дрейф, по образцу practices) ──
    template_yaml = Path(project_dir) / "template.yaml"
    if template_yaml.is_file():
        import yaml as _yaml

        tpl_data = _yaml.safe_load(template_yaml.read_text(encoding="utf-8")) or {}
        required = str(tpl_data.get("requires_instructions_version") or "").strip()
        if required:
            pins_data = _yaml.safe_load(pins_path.read_text(encoding="utf-8")) or {}
            pinned = str((pins_data.get("templates") or {}).get("requires_instructions_version") or "").strip()
            if pinned and _ver_tuple(required) > _ver_tuple(pinned):
                msg = (
                    f"Template {tpl_data.get('name', 'unknown')} requires instructions v{required}, "
                    f"but platform pins v{pinned}. Update the platform first."
                )
                logger.info("[IMP:10][scaffold][instructions] %s", msg)
                print(f"ERROR: {msg}")
                return False

    # ── Синк компилятором (контракт: python3 -m ai_instructions sync --config <pins> …) ──
    cmd = [
        sys.executable,
        "-m",
        "ai_instructions",
        "sync",
        "--config",
        str(pins_path),
        "--project-dir",
        str(project_dir),
        "--template",
        template,
    ]
    canon_override = os.environ.get("AI_INSTRUCTIONS_CANON_PATH")
    if canon_override:
        cmd += ["--canon-path", canon_override]
    try:
        result = subprocess.run(cmd, cwd=platform_root, capture_output=True, text=True, check=False, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.info("[IMP:10][scaffold][instructions] ai-instructions sync failed: %s", exc)
        print(f"ERROR: ai-instructions sync unavailable: {exc} (dev-setup: uv pip install -e ../ai-instructions)")
        return False
    if result.returncode != 0:
        logger.info(
            "[IMP:10][scaffold][instructions] ai-instructions sync exit=%d: %s",
            result.returncode,
            result.stderr.strip(),
        )
        print(f"ERROR: ai-instructions sync failed (exit {result.returncode}): {result.stderr.strip()[-400:]}")
        return False
    logger.info("[IMP:9][scaffold][instructions] Instructions generated: %s/.kilo (template=%s)", project_dir, template)
    return True


def _ver_tuple(version: str) -> tuple[int, ...]:
    """'0.7.0' → (0, 7, 0); нечисловые сегменты отбрасываются (сравнение по int-префиксу)."""
    parts: list[int] = []
    for seg in version.lstrip("v").split("."):
        if not seg.isdigit():
            break
        parts.append(int(seg))
    return tuple(parts)


# endregion FUNC_scaffold_instructions


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
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.info("[IMP:10][scaffold][git] git init/commit failed: %s", exc)
        print(f"ERROR: git init/commit failed: {exc}")
        return False
    else:
        return True


# endregion FUNC_git_init_project


# region FUNC_create_github_repo
def create_github_repo(org: str, name: str, project_dir: str, dry_run: bool = False) -> bool:
    """Lazy facade for core.internal.scaffold.github_ops.create_github_repo.

    ## @purpose — Backward-compatible entry point retained in project_scaffolder so existing
    ##            callers (main) keep the same import path. Implementation moved verbatim to
    ##            github_ops.py (DevPlan 117 G T58.1). Lazy import keeps start-up time unchanged (AC-G5).
    ## @io — ⇥ org, name, project_dir, dry_run → ⎋ bool
    ## @complexity — O(1) + delegate
    ## @invariants
    ##   - Graceful: gh not found → warn, skip (non-fatal)
    ##   - Repo already exists → skip creation, add remote
    """
    from core.internal.scaffold.github_ops import create_github_repo as _impl

    return _impl(org, name, project_dir, dry_run)


# endregion FUNC_create_github_repo


# region FUNC_generate_checklist
def generate_checklist(
    project_dir: str,
    name: str,
    org: str,
    template: str,  # ruff: ignore[ARG001]
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
        "| `MIRROR_SSH_KEY` | SSH private key для mirror push (Tronyx161 → TronyxLab; 177 W2.1 — GIT_MIRROR_TOKEN удалён) |",
        "",
        "Org variable `NODE_HOST_MAP` (JSON) — разрешение нод в SSH-хосты.",
        "",
        "## 4. Настроить Docker Registry",
        "",
        "Registry `ghcr.io` уже прописан в `docker-compose.yml`.",
        "GitHub Actions использует `GITHUB_TOKEN` (доступен автоматически).",
    ]

    if domain:
        lines.extend([
            "",
            "## 5. TLS-сертификат выпускается автоматически",
            "",
            "## 6. Применить nginx overlay на сервере",
            "",
            "```bash",
            "sudo nginx -t && sudo nginx -s reload",
            "```",
        ])

    if database:
        lines.extend([
            "",
            "## 7. Создать базу данных",
            "",
            "```bash",
            f'sudo -u postgres psql -c "CREATE DATABASE {database};"',
            "```",
        ])

    lines.extend([
        "",
        "---",
        f"> Сгенерировано `add-project.sh` ({datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})",
    ])

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
class _ScaffoldArgs(argparse.Namespace):
    """Typed argparse namespace (W11: reportAny=error — Namespace attribute access via Any).

    ## @purpose  Декларирует типы CLI-опций. ClassVar-аннотации БЕЗ значений (только типы):
    ##            значения в class-атрибутах ломают hasattr (перебивают parser-дефолты);
    ##            parse_args(namespace=...) заполняет инстанс-атрибуты.
    ## @invariants  Атрибуты не инициализированы на уровне класса (hasattr до parse = False).
    """

    name: ClassVar[str]
    template: ClassVar[str]
    org: ClassVar[str]
    node: ClassVar[str]
    domain: ClassVar[str]
    database: ClassVar[str]
    dry_run: ClassVar[bool]
    mode: ClassVar[str]
    register: ClassVar[bool]
    projects_root: ClassVar[str]


def main(argv: list[str] | None = None, config: AppConfig | None = None) -> int:
    """CLI dispatcher for project scaffold.

    ## @purpose  Parse args, orchestrate full project creation flow.
    ##            Composition root (W4a): AppConfig.from_env() создаётся ЗДЕСЬ и прокидывается
    ##            параметрами (defaults, show_plan, render) — никаких import-time env-чтений.
    ## @io        ⇥ argv, config (None = AppConfig.from_env()) → ⎋ int exit code (контракт T4)
    ## @complexity O(f) for template copy + O(1) subprocess calls
    """
    cfg = config if config is not None else AppConfig.from_env()

    parser = argparse.ArgumentParser(description="Create a new project from a template.")
    parser.add_argument("--name", required=True, help="Project name (alphanumeric, hyphens, underscores)")
    parser.add_argument("--template", required=True, help="Template: frontend | backend")
    parser.add_argument("--org", default="", help=f"Organization name (default: {cfg.platform_org})")
    parser.add_argument("--node", default="", help=f"Target node name (default: {cfg.platform_default_node})")
    parser.add_argument("--domain", default="", help="Domain for nginx vhost (auto: NAME.PLATFORM_DOMAIN)")
    parser.add_argument("--database", default="", help="Database name for backend projects")
    parser.add_argument("--dry-run", action="store_true", default=False, help="Show plan without creating files")
    parser.add_argument("--mode", default="", help="dev mode: enables staging")
    parser.add_argument("--register", action="store_true", default=False, help="Register project in node.yaml")
    parser.add_argument("--projects-root", default=cfg.projects_root, help="Override PROJECTS_ROOT")

    args = parser.parse_args(argv, namespace=_ScaffoldArgs())

    # Apply defaults
    org = args.org or cfg.platform_org
    node = args.node or cfg.platform_default_node

    # Validate
    if args.template not in {"frontend", "backend"}:
        logger.info("[IMP:10][scaffold][main] Invalid template: %s", args.template)
        print(f"ERROR: Invalid template type: '{args.template}'. Must be: frontend | backend")
        return 1

    # ⚠️ TRAP[BUG] · 2026-08-01 · P2 · Old strip-check accepted leading '-'/'_' project names
    # · Symptom: "--foo" passed the old replace-based isalnum strip-check — it stripped ALL
    #   hyphens (including leading) → "foo".isalnum() == True → name "--foo" accepted.
    # · Root: replace-based check never validated character POSITION — only character set.
    # · Fix: canonical validate_project_name (regex ^[a-zA-Z0-9][a-zA-Z0-9_-]*$, DevPlan 116 B6 T3) —
    #   rejects leading '-'/'_' (поведение УСИЛЕНО).
    # · Prevention: единый канон validate_project_name для всех имён проектов/контекстов.
    from core.internal.shared.project_registry import validate_project_name

    if not validate_project_name(args.name):
        logger.info("[IMP:10][scaffold][main] Invalid project name: %s", args.name)
        print(f"ERROR: Invalid project name: '{args.name}'. Use alphanumeric, hyphens, underscores (no leading -/_).")
        return 1

    template_dir = Path(
        Path(Path(__file__).resolve().parent.parent).parent, "..", "templates", f"template-{args.template}"
    )
    template_path = Path(template_dir).resolve()
    if not template_path.is_dir():
        logger.info("[IMP:10][scaffold][main] Template not found: %s", template_path)
        print(f"ERROR: Template not found: {template_path}")
        return 1

    # Auto-domain
    domain = auto_domain(args.name, args.domain, cfg.platform_domain)

    logger.info(
        "[IMP:6][scaffold][main] START: add-project --name %s --template %s --org %s --node %s",
        args.name,
        args.template,
        org,
        node,
    )

    # Show plan
    show_plan(args.name, org, args.template, node, domain, args.database, cfg.projects_root)

    # Confirm
    if not confirm(dry_run=args.dry_run, ci_mode=cfg.ci_mode):
        return 0

    logger.info("[IMP:7][scaffold][main] Starting project creation")

    project_dir = Path(args.projects_root) / org / args.name

    # Step 1: Copy template
    if not copy_template(str(template_path), str(project_dir), dry_run=args.dry_run):
        return 1

    # Step 1b: Validate template.yaml (DevPlan 141 A7) — informational, graceful
    from core.internal.scaffold.scaffold_helpers import read_template_yaml

    try:
        read_template_yaml(template_path)
    except ConfigValidationError as exc:
        logger.info("[IMP:10][scaffold][template_yaml] template.yaml validation failed: %s", exc)
        print(f"ERROR: template.yaml validation failed: {exc}")
        return 1

    # Step 2: Generate ai-platform.yaml
    from core.internal.scaffold.scaffold_helpers import gen_ai_platform_yaml

    yaml_path = Path(project_dir) / "ai-platform.yaml"
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
    if not render_project_template(str(project_dir), args.name, org, domain, node, args.dry_run, cfg.platform_domain):
        print("WARNING: Template rendering failed — some placeholders may not be replaced")

    # Step 4: Generate .env.platform
    gen_env_platform(str(project_dir), args.name, domain, args.dry_run)

    # Step 5: Generate Makefile + AGENTS.md + AI-PLATFORM.md
    from core.internal.scaffold.scaffold_helpers import (
        gen_project_agents,
        gen_project_makefile,
        gen_project_platform_md,
    )

    gen_project_makefile(
        name=args.name,
        domain=domain,
        output_path=Path(project_dir) / "Makefile",
        force=True,
    )
    gen_project_agents(
        name=args.name,
        org=org,
        template=args.template,
        node=node,
        domain=domain,
        output_path=Path(project_dir) / "AGENTS.md",
        force=True,
    )
    # DevPlan 133 D3: AI-PLATFORM.md — контракт проекта с платформой (после gen_env_platform)
    gen_project_platform_md(
        name=args.name,
        org=org,
        node=node,
        domain=domain,
        project_dir=str(project_dir),
        output_path=Path(project_dir) / "AI-PLATFORM.md",
        force=False,
    )

    # DevPlan 137 W1 шаг 11: baseline-практики (ВСЕГДА, level=auto → state=baseline).
    # До git_init (шаг 6) — GENERATED-файлы и practices.lock попадают в init-коммит.
    gen_project_practices(str(project_dir), args.dry_run)

    # DevPlan 001 T5.2: инструкции проекта из ЖИВОГО канона (шаблон снапшота не несёт).
    # После practices и ДО git_init — .kilo/ + kilo.json + ai-instructions.lock в init-коммит.
    if not scaffold_instructions(str(project_dir), args.template, args.dry_run):
        return 1

    # Step 6: Git init
    git_init_project(str(project_dir), args.name, args.template, args.dry_run)

    # Step 7: Create GitHub repo
    create_github_repo(org, args.name, str(project_dir), args.dry_run)

    # Step 8: Generate setup checklist
    generate_checklist(str(project_dir), args.name, org, args.template, domain, args.database, args.dry_run)

    # Step 9: Add vhost
    run_add_vhost(str(project_dir), domain, args.dry_run)

    # Step 10: Register in node.yaml (if --register)
    if args.register:
        from core.internal.scaffold.scaffold_helpers import register_in_node_yaml

        node_yaml = Path(args.projects_root) / org / "node-configs" / node / "node.yaml"
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
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    logging.basicConfig(
        level=cast(int, getattr(logging, os.environ.get("LOG_LEVEL", "INFO"))),
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    sys.exit(main())
