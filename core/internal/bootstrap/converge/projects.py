#!/usr/bin/env python3
# GREP_SUMMARY: converge-projects, reconcile-projects, r3, project-directories, stub, ai-platform-yaml, env-platform, generate-env-platform, AI-PLATFORM.md
# STRUCTURE: ▶ parse node.yaml#projects (canonical NodeYaml) → ○ for each: validate name → mkdir -p → chown ci-deploy → stub ai-platform.yaml (if-missing) → .env.platform (if-missing) → AI-PLATFORM.md (if-missing) → ⎋ drift entry {R3}
# region MODULE_CONTRACT
## @purpose  R3 reconcile_projects — per-project directory + stub ai-platform.yaml + .env.platform.
##           Извлечён из reconciler.py (B9 T2, U-31).
## @scope    converge/projects.py: reconcile_projects, parse_projects_yaml, create_empty_env_file,
##           reconcile_env_platform. Вызывается оркестратором reconciler.py.
## @invariants
##   - Локальная R3-семантика stub-создания (mkdir + GENERATED-STUB) ОРТОГОНАЛЬНА удалённому
##     stub→deploy в reconciler_projects.py (DevPlan 116 B9 инвариант 4) — НЕ консолидируется
##   - is_stub-детекция — через shared/stub_detection.is_stub_ai_platform_yaml (единая реализация, T4)
##   - validate_project_name — канон shared/project_registry (B6 T3); invalid → fail + continue
##   - .env.platform через generate_env_platform() (прямой импорт, T9b) с fallback на empty file
##   - AI-PLATFORM.md через gen_project_platform_md() (прямой импорт, DevPlan 133 R3) if-missing
## @rationale DevPlan 116 B9 D3: 8 доменов reconciler по модулям.
## @changes  2026-08-01 · Extracted from reconciler.py (B9 T2); _is_stub → shared/stub_detection (T4)
## @changes  2026-08-03 · DevPlan 133 R3 — +reconcile_project_platform_md (AI-PLATFORM.md if-missing)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from core.internal.bootstrap.converge import infra
from core.internal.bootstrap.converge.infra import (
    report_add,
    run_subprocess,
    set_exit,
)

# R3-каноны констант — прямые импорты из shared SoT (pyright reportPrivateLocalImportUsage)
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE as PROJECTS_BASE
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.stub_detection import is_stub_ai_platform_yaml
from core.internal.shared.timeouts import FILE_OP_TIMEOUT

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# R3 — reconcile_projects
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_projects
## @purpose  Read node.yaml#projects and ensure per-project directories,
##           ownership ci-deploy:ci-deploy, stub ai-platform.yaml, and
##           .env.platform via gen-env-platform.sh (if-missing).
## @io       stdout/stderr: LDD logs [IMP:7-10]
##           side-effect: mkdir -p, chown, touch, stub creation
## @param node_yaml_path  Path to node.yaml
## @param dry_run         If True, only report planned mutations
## @param report_only     If True, skip mutations entirely
## @return  Drift report entry dict
## @edge-cases
##   - projects: [] or missing → SKIP
##   - Invalid project name (/ or ..) → fail
##   - Existing non-stub ai-platform.yaml → NOT touched
##   - Existing stub → NOT overwritten (no-op)
##   - Existing .env.platform → NOT touched (if-missing)
def reconcile_projects(
    node_yaml_path: str,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """Reconcile project directories and stubs from node.yaml.

    Returns a drift entry dict with status: ok|skipped|mutated|fail.
    """
    unit = "R3"
    logger.info("[IMP:8][converge][%s] START: reconcile_projects — ensuring project directories and stubs", unit)

    node_yaml = Path(node_yaml_path)
    if not node_yaml.is_file():
        msg = f"node.yaml not found: {node_yaml_path}"
        logger.error("[IMP:10][converge][%s] FATAL: %s", unit, msg)
        report_add(unit, "fail", msg)
        set_exit(2)
        return {"unit": unit, "status": "fail", "detail": msg}

    # ── Parse projects from node.yaml ──
    projects = parse_projects_yaml(str(node_yaml))

    if not projects:
        logger.info("[IMP:9][converge][%s] SKIP: No projects defined in node.yaml or projects: []", unit)
        report_add(unit, "skipped", "No projects defined in node.yaml")
        return {"unit": unit, "status": "skipped", "detail": "No projects defined in node.yaml"}

    projects_dir = Path(PROJECTS_BASE)
    mutated = 0
    errors = 0

    # DevPlan 116 B6 T3: единый канон validate_project_name (project_registry) вместо
    # локального приватного валидатора имён. Lazy-import по паттерну _parse_projects_yaml 680-683.
    try:
        from core.internal.shared.project_registry import validate_project_name
    except ImportError:
        logger.error("[IMP:10][converge][%s] FAIL: project_registry not importable", unit)
        validate_project_name = None

    for proj in projects:
        proj_name = proj.get("name", "")
        if not proj_name:
            continue

        logger.info("[IMP:7][converge][%s] Processing project: %s", unit, proj_name)

        # Validate project name (canonical strict regex — rejects leading -/_, '/', '..')
        if validate_project_name is None or not validate_project_name(proj_name):
            errors += 1
            report_add(unit, "fail", f"Invalid project name: {proj_name}")
            set_exit(2)
            continue

        proj_dir = projects_dir / proj_name

        # ── mkdir -p ──
        if not proj_dir.is_dir():
            if dry_run or report_only:
                logger.info("[IMP:9][converge][%s] WOULD create directory: %s", unit, proj_dir)
                mutated += 1
            else:
                logger.info("[IMP:8][converge][%s] Creating directory: %s", unit, proj_dir)
                try:
                    proj_dir.mkdir(parents=True, exist_ok=True)
                    logger.info("[IMP:9][converge][%s] DONE: %s created", unit, proj_dir)
                    mutated += 1
                except OSError as exc:
                    logger.error("[IMP:10][converge][%s] FAIL: mkdir -p %s failed: %s", unit, proj_dir, exc)
                    errors += 1
                    set_exit(2)
                    continue

        # ── chown ci-deploy:ci-deploy (directory only, not recursive) ──
        if not dry_run and not report_only and proj_dir.is_dir():
            _ = run_subprocess(["chown", "ci-deploy:ci-deploy", str(proj_dir)], timeout=FILE_OP_TIMEOUT)

        # ── stub ai-platform.yaml (if-missing) ──
        stub_file = proj_dir / "ai-platform.yaml"
        if not stub_file.is_file():
            if dry_run or report_only:
                logger.info("[IMP:9][converge][%s] WOULD create stub: %s", unit, stub_file)
                mutated += 1
            else:
                logger.info("[IMP:8][converge][%s] Creating stub: %s", unit, stub_file)
                try:
                    stub_content = (
                        f"# GENERATED-STUB by converge — overwritten by CI deliver\n"
                        f"# This is a placeholder created during node convergence.\n"
                        f"# CI deliver will replace it with the actual project configuration.\n"
                        f"project: {proj_name}\n"
                        f"service: {proj_name}\n"
                    )
                    _ = stub_file.write_text(stub_content)
                    _ = run_subprocess(["chown", "ci-deploy:ci-deploy", str(stub_file)], timeout=FILE_OP_TIMEOUT)
                    logger.info("[IMP:9][converge][%s] DONE: stub created for %s", unit, proj_name)
                    mutated += 1
                except OSError as exc:
                    logger.error("[IMP:10][converge][%s] FAIL: stub creation failed for %s: %s", unit, proj_name, exc)
                    errors += 1
                    set_exit(2)
                    continue
        elif is_stub_ai_platform_yaml(str(stub_file)):
            logger.info("[IMP:7][converge][%s] STUB: %s is a GENERATED-STUB (awaiting deploy)", unit, stub_file)
            report_add(unit, "awaiting_deploy", f"Project {proj_name}: stub present, awaiting CI deploy")
        else:
            logger.info("[IMP:7][converge][%s] SKIP: %s already exists (real config — deployed)", unit, stub_file)
            report_add(unit, "converged", f"Project {proj_name}: deployed")

        # ── .env.platform (if-missing) ──
        reconcile_env_platform(proj_name, str(proj_dir), unit, dry_run, report_only)

        # ── AI-PLATFORM.md (if-missing, DevPlan 133 R3) ──
        reconcile_project_platform_md(proj_name, str(proj_dir), str(node_yaml), unit, dry_run, report_only)

    # Final report
    if mutated > 0:
        report_add(unit, "mutated", f"{mutated} project item(s) created/fixed")
        set_exit(1)
    elif errors > 0:
        report_add(unit, "fail", f"{errors} project(s) had errors")
    else:
        report_add(unit, "converged", "All project directories and stubs present")

    logger.info("[IMP:9][converge][%s] DONE: projects reconciled (mutated=%d errors=%d)", unit, mutated, errors)
    return {
        "unit": unit,
        "status": "converged" if not errors else "fail",
        "detail": f"mutated={mutated} errors={errors}",
    }


# endregion FUNC_reconcile_projects


# region FUNC_parse_projects_yaml
## @purpose  Parse projects list from node.yaml via canonical NodeYaml.get_project_entries().
def parse_projects_yaml(node_yaml_path: str) -> list[dict[str, str]]:
    """Parse projects from node.yaml.

    DevPlan 116 B6 T4: manual dict/str parsing replaced with the single canonical parser
    NodeYaml.get_project_entries(). str-form entries are REJECTED (decision D3 —
    node.schema.json requires dict records; str-form cancelled).
    Returns empty list on parse error or missing section.
    """
    try:
        entries = NodeYaml(node_yaml_path).get_project_entries()
        return [{"name": e.name, "domain": e.domain} for e in entries]
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as exc:
        logger.warning("[IMP:8][parse_projects_yaml] Failed to parse projects from %s: %s", node_yaml_path, exc)
        return []


# endregion FUNC_parse_projects_yaml


# region FUNC_create_empty_env_file
## @purpose  Helper: create an empty .env.platform file with correct permissions and ownership.
##           Used as fallback when generate_env_platform() is unavailable or fails.
## @param env_file  Path to .env.platform file
## @param unit      R-unit name for logging
## @return  True if the file was created successfully, False on error
def create_empty_env_file(env_file: Path, unit: str) -> bool:
    """Create an empty .env.platform file with 0640 ci-deploy:ci-deploy.

    Args:
        env_file: Path to the .env.platform file to create.
        unit: R-unit name for logging.

    Returns:
        True on success, False on OSError.
    """
    try:
        _ = env_file.write_text("", encoding="utf-8")
        _ = run_subprocess(["chmod", "0640", str(env_file)], timeout=FILE_OP_TIMEOUT)
        _ = run_subprocess(["chown", "ci-deploy:ci-deploy", str(env_file)], timeout=FILE_OP_TIMEOUT)
        logger.info("[IMP:9][converge][%s] DONE: %s created (fallback: empty)", unit, env_file)
    except OSError as exc:
        logger.error("[IMP:10][converge][%s] FAIL: .env.platform creation failed: %s", unit, exc)
        set_exit(2)
        return False
    else:
        return True


# endregion FUNC_create_empty_env_file


# region FUNC_reconcile_env_platform
## @purpose  Ensure .env.platform exists in project directory (if-missing).
##           T9b: replaced subprocess call to gen-env-platform.sh with direct
##           Python import of generate_env_platform() from gen_env_platform module.
## @param proj_name    Project name (used for DSN substitution and logging)
## @param proj_dir     Path to the project directory
## @param unit         R-unit name for logging (typically "R3")
## @param dry_run      If True, only log planned mutations
## @param report_only  If True, skip mutations entirely
def reconcile_env_platform(
    proj_name: str,
    proj_dir: str,
    unit: str,
    dry_run: bool,
    report_only: bool,
) -> None:
    """Create .env.platform via generate_env_platform() or fallback to empty file.

    Uses direct Python import (T9b) instead of shell subprocess. Falls back
    to empty file if platform-env.yaml is missing or generation fails.

    Reports via report_add and set_exit; does not modify caller's local mutated counter —
    the approximate count from report entries is sufficient for the exit code contract.
    """

    env_file = Path(proj_dir) / ".env.platform"
    if env_file.is_file():
        logger.info("[IMP:7][converge][%s] SKIP: %s already exists (if-missing policy)", unit, env_file)
        return

    if dry_run or report_only:
        logger.info("[IMP:9][converge][%s] WOULD create: %s via generate_env_platform()", unit, env_file)
        report_add(unit, "mutated", f".env.platform would be created for {proj_name}")
        set_exit(1)
        return

    logger.info("[IMP:8][converge][%s] Creating .env.platform via generate_env_platform() for %s", unit, proj_name)

    # Determine platform-env.yaml path relative to infra.core_dir
    platform_env_path = str(Path(infra.core_dir).parent / "platform-env.yaml")
    domain = os.environ.get("PLATFORM_DOMAIN", "ai-platform.local")

    # ruff: ignore[PLW0717] — нужно >5 свободных локальных переменных — извлечение неразумно
    try:
        # Lazy import to match codebase pattern (same as core.internal.shared imports)
        from core.internal.scaffold.gen_env_platform import generate_env_platform, resolve_placement_for_project

        # DevPlan 010 T2.1: cross-node адресация .env.platform — best-effort резолв
        # (placement, target_node); не резолвится → legacy Docker DNS emission.
        placement, consumer_node = resolve_placement_for_project(proj_dir)
        lines = generate_env_platform(
            platform_env_path,
            domain=domain,
            project_name=proj_name,
            placement=placement,
            consumer_node=consumer_node,
        )
        _ = env_file.write_text("\n".join(lines) + "\n")
        _ = run_subprocess(["chmod", "0640", str(env_file)], timeout=FILE_OP_TIMEOUT)
        _ = run_subprocess(["chown", "ci-deploy:ci-deploy", str(env_file)], timeout=FILE_OP_TIMEOUT)
        logger.info("[IMP:9][converge][%s] DONE: %s generated via generate_env_platform()", unit, env_file)
        report_add(unit, "mutated", f".env.platform created for {proj_name}")
        set_exit(1)
    except FileNotFoundError:
        logger.warning(
            "[IMP:9][converge][%s] WARN: platform-env.yaml not found at %s — creating empty .env.platform",
            unit,
            platform_env_path,
        )
        _ = create_empty_env_file(env_file, unit)
    except (ImportError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "[IMP:9][converge][%s] WARN: generate_env_platform() failed for %s — creating empty .env.platform: %s",
            unit,
            proj_name,
            exc,
        )
        _ = create_empty_env_file(env_file, unit)


# endregion FUNC_reconcile_env_platform


# region FUNC_reconcile_project_platform_md
## @purpose  Ensure AI-PLATFORM.md exists in project directory (if-missing, DevPlan 133 R3).
##           Генерация только при ОТСУТСТВИИ файла (if-missing семантика, как .env.platform) —
##           существующий AI-PLATFORM.md НЕ трогается (ручные правки статики сохраняются).
## @param proj_name      Project name
## @param proj_dir       Path to the project directory
## @param node_yaml_path Path to node.yaml (для GENERATED-секции: enabled-модули ноды)
## @param unit           R-unit name for logging (typically "R3")
## @param dry_run        If True, only log planned mutations
## @param report_only    If True, skip mutations entirely
## @complexity O(M + S) — direct import of gen_project_platform_md (Python→Python)
## @invariants
##   - if-missing: существующий AI-PLATFORM.md → SKIP (никогда не перезаписывается)
##   - direct import (как generate_env_platform, T9b) — без subprocess
##   - Сбой генерации → WARN + empty-фолбэк не требуется (пропуск, non-fatal)
def reconcile_project_platform_md(
    proj_name: str,
    proj_dir: str,
    node_yaml_path: str,
    unit: str,
    dry_run: bool,
    report_only: bool,
) -> None:
    """Create AI-PLATFORM.md via gen_project_platform_md (if-missing policy, DevPlan 133 R3)."""

    platform_file = Path(proj_dir) / "AI-PLATFORM.md"
    if platform_file.is_file():
        logger.info("[IMP:7][converge][%s] SKIP: %s already exists (if-missing policy)", unit, platform_file)
        return

    if dry_run or report_only:
        logger.info("[IMP:9][converge][%s] WOULD create: %s via gen_project_platform_md()", unit, platform_file)
        report_add(unit, "mutated", f"AI-PLATFORM.md would be created for {proj_name}")
        set_exit(1)
        return

    logger.info("[IMP:8][converge][%s] Creating AI-PLATFORM.md via gen_project_platform_md() for %s", unit, proj_name)

    # platform-env.yaml path — тот же канон, что reconcile_env_platform (T9b)
    platform_env_path = str(Path(infra.core_dir).parent / "platform-env.yaml")

    # ruff: ignore[PLW0717] — нужно >5 свободных локальных переменных — извлечение неразумно
    try:
        from core.internal.scaffold.gen_project_platform_md import write_project_platform_md

        status = write_project_platform_md(
            proj_dir,
            node_name=proj_name,
            node_yaml_path=node_yaml_path,
            platform_env_path=platform_env_path,
        )
        _ = run_subprocess(["chmod", "0640", str(platform_file)], timeout=FILE_OP_TIMEOUT)
        _ = run_subprocess(["chown", "ci-deploy:ci-deploy", str(platform_file)], timeout=FILE_OP_TIMEOUT)
        logger.info("[IMP:9][converge][%s] DONE: AI-PLATFORM.md %s for %s", unit, status, proj_name)
        report_add(unit, "mutated", f"AI-PLATFORM.md created for {proj_name}")
        set_exit(1)
    except (ImportError, OSError, ValueError) as exc:
        logger.warning(
            "[IMP:9][converge][%s] WARN: AI-PLATFORM.md generation failed for %s (non-fatal): %s",
            unit,
            proj_name,
            exc,
        )


# endregion FUNC_reconcile_project_platform_md
