# GREP_SUMMARY: scaffold_helpers gen_ai_platform_yaml gen_makefile gen_agents gen_project_platform_md register_in_node_yaml shared
# STRUCTURE: ▶ gen_ai_platform_yaml (⊕ monitoring per type) → ⚡ gen_project_makefile (sync-env + status) → ⚡ gen_project_agents (DD13 contract) → ⚡ gen_project_platform_md (AI-PLATFORM.md, D2/D3) → ⚡ register_in_node_yaml (NodeYaml CLI) → ⎋ exports
# region MODULE_CONTRACT
## @purpose  Shared scaffold helper functions extracted from project_adopter.py and add-project.sh.
##           Eliminates duplication between new-project (scaffolder) and adopt-project (adopter).
##           gen_ai_platform_yaml, gen_project_makefile, gen_project_agents, register_in_node_yaml.
## @scope    Called from project_scaffolder.py (new-project) and project_adopter.py (adopt-project).
##           All functions write to explicit output_path — no implicit state.
## @invariants
##   - Pure Python — no shell subprocess calls in gen functions
##   - All functions accept explicit output paths (no hidden dirs)
##   - gen_ai_platform_yaml supports full monitoring config (from add-project.sh)
##   - register_in_node_yaml uses NodeYaml CLI mutation API
##   - Behaviour-preserving: drift resolved in favor of project_adopter.py (newer, tested)
## @rationale AC6: add-project ↔ adopt-project share ~4 duplicate functions. Extract once,
##            use from both. Eliminates drift-risk (TRAP-class "two implementations diverge").
## @links    CALLED_BY: project_scaffolder.py, project_adopter.py
##           CALLS: NodeYaml CLI (register)
##           DP-092 Wave 4a
## @changes  2026-07-30 · Wave 4a — extracted from project_adopter.py + add-project.sh
# endregion MODULE_CONTRACT

# 🧐 TRAP[DECISION] · 2026-07-30 · — · Shared functions extracted — adopter uses minimal monitoring, scaffolder uses full
# · Rejected: forcing adopter to use full monitoring (would change adopter behaviour)
# · Reason: adopter is for existing projects — minimal yaml is appropriate. Scaffolder generates
#   full monitoring from template type. Both call same function with different mode= parameter.
# · Rev: if adopter needs full monitoring → add a dedicated mode or separate function

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# DevPlan 118 C3: единый loader COMPOSE_PROFILES — shared/compose_profiles.py (SoT platform-infra.yaml).
# ⚠️ TRAP[BUG] · 2026-08-02 · P1 · функция, импортированная под именем модуля
# · Symptom: adopt-project падал с AttributeError: 'function' object has no attribute 'load_profiles'
# · Root: `from ...compose_profiles import load_profiles as compose_profiles` (функция), затем
# ·   `compose_profiles.load_profiles()` — вызов атрибута на функции. Канон docker_orchestrator:
# ·   `import load_profiles as compose_profiles_load_profiles` (функция без .load_profiles()).
# · Fix: вызов `compose_profiles()` (функция напрямую).
# · Prevention: не алиасить функцию под имя модуля; вызывать функцию напрямую.
from core.internal.shared.compose_profiles import load_profiles as compose_profiles

logger = logging.getLogger(__name__)

# ── Default node for fallback ──────────────────────────────────────────
_DEFAULT_NODE = os.environ.get("PLATFORM_DEFAULT_NODE", "tronyx-vps")

# ── Compose profiles — SoT: platform-infra.yaml env_defaults (DevPlan 116 T2, U-02 + 118 C3) ──
# Repo root resolved relative to this file (core/internal/scaffold/ → 4 levels up).
_REPO_ROOT = Path(__file__).resolve().parents[3]


# region FUNC_load_compose_profiles_from_platform_env
## @purpose  Read COMPOSE_PROFILES from platform-infra.yaml env_defaults (SoT) через единый
##            loader shared/compose_profiles (DevPlan 118 C3 — прежнее чтение generated
##            platform-env.yaml УДАЛЁНО: два потребителя читали разные источники).
##            Local tool — platform-infra.yaml всегда в репо (SoT). Fail-fast (инвариант 7).
##            Публичная (B9 T5, CS-4) — перенесена из project_adopter._load_compose_profiles_from_platform_env.
## @io       ⇥ None → ⎋ str: comma-separated profile list ⚡ raise FileNotFoundError/KeyError
## @complexity O(1) — single YAML load
## @invariants
##   - Единый loader shared/compose_profiles (SoT platform-infra.yaml) — НЕ platform-env.yaml
##   - Raises readable error if file or key missing — adopt must not proceed with wrong profiles
def load_compose_profiles_from_platform_env() -> str:
    """Return COMPOSE_PROFILES from shared loader (SoT platform-infra.yaml, C3)."""
    profiles = compose_profiles()
    if not profiles:
        raise KeyError(
            "[IMP:10][scaffold_helpers] env_defaults.COMPOSE_PROFILES missing in platform-infra.yaml (SoT) — "
            "run `make generate-platform-env` (DevPlan 116 T2, U-02)."
        )
    result = ",".join(profiles)
    logger.info("[IMP:9][load_compose_profiles] COMPOSE_PROFILES from SoT: %s", result)
    return result


# endregion FUNC_load_compose_profiles_from_platform_env


# region FUNC_gen_ai_platform_yaml
## @purpose  Generate ai-platform.yaml for a project (shared between new-project + adopt).
## @param name         Project name
## @param ptype        Project type: frontend, backend
## @param org          Organization name
## @param node         Target node name
## @param domain       Domain name (optional)
## @param database     Database name (optional)
## @param mode         Deployment mode: "dev" enables staging
## @param output_path  Where to write the YAML file
## @param minimal      If True, generate minimal yaml (adopter mode — no DB/LLM monitoring)
## @io        Writes YAML file to output_path
## @complexity O(1)
## @invariants
##   - Overwrites existing file (caller decides idempotency)
##   - Full monitoring config depends on project type (frontend/backend)
##   - Minimal mode: only name, type, target_node, needs, basic monitoring
def gen_ai_platform_yaml(
    name: str,
    ptype: str,
    org: str,
    node: str = "",
    domain: str = "",
    database: str = "",
    mode: str = "",
    output_path: str | Path = "",
    minimal: bool = False,
) -> str:
    """Generate ai-platform.yaml for a project.

    ## @purpose  Unified yaml generation: add-project.sh generate_ai_platform_yaml +
    ##           project_adopter.generate_minimal_ai_platform_yaml.
    ## @io        ⇥ name, ptype, org, ... → ⎋ str — "generated" | "exists"
    ## @complexity O(1)
    """
    out = Path(output_path) if output_path else Path(".")
    if not output_path:
        logger.info("[IMP:8][helpers][gen_yaml] No output_path — skipping")
        return "skipped"

    logger.info("[IMP:7][helpers][gen_yaml] Generating ai-platform.yaml for %s (type=%s)", name, ptype)

    node = node or _DEFAULT_NODE

    # Build YAML data
    data: dict[str, Any] = {
        "name": name,
        "type": ptype,
        "description": f"{name} project ({ptype})",
        "target_node": node,
    }

    if minimal:
        # Minimal mode (adopter)
        data["needs"] = {
            "domain": bool(domain),
            "expose": bool(domain),
        }
        data["monitoring"] = {
            "metrics": False,
            "logs_retention": "7d",
            "alerting": False,
            "dashboard": False,
        }
    else:
        # Full mode (scaffolder — from add-project.sh)
        data["needs"] = {
            "domain": domain if domain else False,
            "expose": bool(domain),
        }
        # DevPlan 123 T6: database приходит из argparse --database (project_scaffolder, default "")
        # — shell-строка, типизированного accessor'а project_yaml для него нет (проверено: shared/
        # project_yaml.py get_* не покрывает database). Нормализуем сравнение: bool False →
        # str "False" → lower "false" → skip (семантика сохраняется), "False"/"false" → skip.
        if database and str(database).lower() != "false":
            data["needs"]["database"] = database

        # Monitoring per type
        mon_config: dict[str, Any]
        if ptype == "frontend":
            mon_config = {
                "metrics": False,
                "metrics_port": 3000,
                "logs_retention": "3d",
                "alerting": False,
                "dashboard": False,
            }
        else:  # backend
            mon_config = {
                "metrics": True,
                "metrics_port": 8080,
                "logs_retention": "14d",
                "alerting": False,
                "dashboard": False,
            }

        data["monitoring"] = mon_config
        if mode == "dev":
            data["staging"] = True
        # DevPlan 137 W1: quality-секция практик (уровень эскалатора). default auto —
        # решение пользователя 2026-08-05: мок ведёт себя как baseline, эскалатор жив.
        data["quality"] = {"level": "auto"}  # baseline | full | auto (default auto)

    # Write YAML
    try:
        import yaml

        out.parent.mkdir(parents=True, exist_ok=True)
        # Write with header comment
        header = (
            f"# =============================================================================\n"
            f"# ai-platform.yaml — единый манифест проекта AI Platform (деплой + мониторинг)\n"
            f"# =============================================================================\n"
            f"# GENERATED by scaffold_helpers.py\n"
            f"# Template: template-{ptype}\n"
            f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"# =============================================================================\n"
        )
        with open(out, "w") as f:
            f.write(header)
            f.write("\n")
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except (OSError, yaml.YAMLError, ImportError) as exc:
        logger.info("[IMP:10][helpers][gen_yaml] Failed to write YAML: %s", exc)
        return "error"

    logger.info("[IMP:9][helpers][gen_yaml] ai-platform.yaml generated: %s", out)
    logger.info("[IMP:8][helpers][gen_yaml]  REVIEW and adjust type/domain/monitoring values")
    return "generated"


# endregion FUNC_gen_ai_platform_yaml


# region FUNC_gen_project_makefile
## @purpose  Generate minimal Makefile in project directory (K3 contract).
##           Does NOT overwrite existing Makefile unless force=True.
## @param name         Project name
## @param domain       Domain (for sync-env reference)
## @param output_path  Where to write the Makefile
## @param force        Overwrite existing Makefile
## @return   "generated" | "exists" | "skipped"
## @complexity O(1)
def gen_project_makefile(
    name: str,
    domain: str = "",
    output_path: str | Path = "",
    force: bool = False,
) -> str:
    """Generate project Makefile.

    ## @purpose  Unified from add-project.sh gen_project_makefile + adopter.gen_project_makefile.
    ## @io        ⇥ name, domain, output_path, force → ⎋ str — "generated" | "exists" | "skipped"
    ## @complexity O(1)
    ## @invariants
    ##   - Does NOT overwrite existing Makefile unless force=True
    ##   - Uses tab indentation (Makefile requirement)
    """
    if not output_path:
        logger.info("[IMP:8][helpers][makefile] No output_path — skipping")
        return "skipped"

    makefile = Path(output_path)

    if makefile.exists():
        if not force:
            logger.info("[IMP:6][helpers][makefile] Makefile exists — SKIP (use --force to regenerate)")
            return "exists"
        logger.info("[IMP:7][helpers][makefile] Force mode: overwriting existing Makefile")

    logger.info("[IMP:7][helpers][makefile] Generating project Makefile: %s", makefile)

    tab = "\t"
    makefile_content = f"""# GENERATED by ai-platform — DO NOT EDIT manually
# Project: {name}
# ai-platform project Makefile (K3 contract) — facade for platform operations

PLATFORM_DIR ?= $(HOME)/projects/ai-platform

## sync-env: Re-generate .env.platform from platform-env.yaml
sync-env:
{tab}@echo "[IMP:7][project] Syncing .env.platform..."
{tab}@$(MAKE) -C $(PLATFORM_DIR) project-sync-env NAME={name} DOMAIN={domain or ""}
{tab}@echo "[IMP:9][project] .env.platform sync complete"

## status: Show live project status from target node
status:
{tab}@echo "[IMP:7][project] Querying project status..."
{tab}@$(MAKE) -C $(PLATFORM_DIR) project-status NAME={name}
{tab}@echo "[IMP:9][project] Status query complete"

## help: Show all available commands
help:
{tab}@grep -E '^## ' $(MAKEFILE_LIST) | column -t -s ':'
"""
    makefile.parent.mkdir(parents=True, exist_ok=True)
    makefile.write_text(makefile_content)
    logger.info("[IMP:9][helpers][makefile] Project Makefile generated: %s", makefile)
    return "generated"


# endregion FUNC_gen_project_makefile


# region FUNC_gen_project_agents
## @purpose  Generate AGENTS.md in project directory (DD13 contract, ≤60 lines).
##           Does NOT overwrite existing unless force=True.
## @param name         Project name
## @param org          Organization name
## @param template     Template type (frontend/backend)
## @param node         Target node name
## @param domain       Domain name (optional)
## @param output_path  Where to write AGENTS.md
## @param force        Overwrite existing AGENTS.md
## @return   "generated" | "exists" | "skipped"
## @complexity O(1)
def gen_project_agents(
    name: str,
    org: str = "",
    template: str = "",
    node: str = "",
    domain: str = "",
    output_path: str | Path = "",
    force: bool = False,
) -> str:
    """Generate project AGENTS.md.

    ## @purpose  Unified from add-project.sh gen_project_agents + adopter.gen_project_agents.
    ## @io        ⇥ name, org, template, node, domain, output_path, force → ⎋ str
    ## @complexity O(1)
    ## @invariants
    ##   - Does NOT overwrite existing AGENTS.md unless force=True
    ##   - DD13 contract: ≤60 lines, platform services, DO NOT rules, commands
    """
    if not output_path:
        logger.info("[IMP:8][helpers][agents] No output_path — skipping")
        return "skipped"

    agents_file = Path(output_path)

    if agents_file.exists():
        if not force:
            logger.info("[IMP:6][helpers][agents] AGENTS.md exists — SKIP (use --force to regenerate)")
            return "exists"
        logger.info("[IMP:7][helpers][agents] Force mode: overwriting existing AGENTS.md")

    logger.info("[IMP:7][helpers][agents] Generating project AGENTS.md: %s", agents_file)

    node_val = node or _DEFAULT_NODE
    agents_content = f"""# AGENTS.md — {name} (ai-platform project)

## Platform provides
Template-based services: postgres, redis, litellm, langfuse, minio, clickhouse, nginx
See `.env.platform` for exact host/port/DSN/URL.

Domain: {domain or "<not set>"}
Node: {node_val}
Target node: {node_val}

## DO NOT
- Edit `.env.platform` manually (regenerate with `make sync-env`)
- Store secrets, tokens, or API keys in project files
- Delete this file or Makefile (project platform contract)

## Commands from this directory
- `make sync-env` — regenerate .env.platform from platform-env.yaml
- `make status` — show live container status from target node
- `make help` — show all available commands

## Configuration
```
name: {name}
org: {org}
template: {template}
node: {node_val}
"""
    agents_file.parent.mkdir(parents=True, exist_ok=True)
    agents_file.write_text(agents_content)
    logger.info("[IMP:9][helpers][agents] Project AGENTS.md generated: %s", agents_file)
    return "generated"


# endregion FUNC_gen_project_agents


# region FUNC_gen_project_platform_md
## @purpose  Generate AI-PLATFORM.md (platform contract, DevPlan 133 D2/D3) — единая точка
##           вызова генератора gen_project_platform_md.py из scaffold-слоя (аналог
##           gen_project_agents/gen_project_makefile). Разрешает node.yaml
##           (PROJECTS_ROOT/org/node-configs/node/node.yaml) и platform-env.yaml
##           (repo root). Не перезаписывает существующий файл без маркеров (без force).
## @param name         Project name
## @param org          Organization name
## @param node         Target node name
## @param domain       Domain (fallback для ${DOMAIN} подстановки)
## @param project_dir  Project directory (для резолва node.yaml и needs)
## @param output_path  Where to write AI-PLATFORM.md
## @param force        Overwrite existing file without GENERATED markers
## @return   "generated" | "updated" | "exists" | "skipped"
## @complexity O(M + S) — delegate to gen_project_platform_md
## @invariants
##   - Node.yaml resolution: PROJECTS_ROOT/org/node-configs/node/node.yaml
##   - platform-env.yaml: repo root (тот же канон, что project_scaffolder.gen_env_platform)
##   - Без PROJECTS_ROOT → node_yaml_path="" → генератор рендерит warning-секцию (graceful)
def gen_project_platform_md(
    name: str,
    org: str = "",
    node: str = "",
    domain: str = "",
    project_dir: str = "",
    output_path: str | Path = "",
    force: bool = False,
) -> str:
    """Generate AI-PLATFORM.md (platform contract) — "generated" | "updated" | "exists" | "skipped"."""
    if not output_path:
        logger.info("[IMP:8][helpers][platform_md] No output_path — skipping")
        return "skipped"

    from core.internal.scaffold.gen_project_platform_md import write_project_platform_md

    target = Path(output_path)

    # ── Resolve node.yaml: PROJECTS_ROOT/org/node-configs/node/node.yaml ──
    node_yaml_path = ""
    projects_root = os.environ.get("PROJECTS_ROOT", "")
    if projects_root and org and node:
        candidate = Path(projects_root) / org / "node-configs" / node / "node.yaml"
        if candidate.is_file():
            node_yaml_path = str(candidate)
            logger.info("[IMP:8][helpers][platform_md] node.yaml resolved: %s", node_yaml_path)
        else:
            logger.info("[IMP:7][helpers][platform_md] node.yaml not found at %s — graceful section", candidate)

    status = write_project_platform_md(
        str(project_dir) if project_dir else str(target.parent),
        node_name=node,
        node_yaml_path=node_yaml_path,
        platform_env_path=str(_REPO_ROOT / "platform-env.yaml"),
        force=force,
        domain=domain,
    )
    logger.info("[IMP:9][helpers][platform_md] AI-PLATFORM.md %s: %s", status, target)
    return status


# endregion FUNC_gen_project_platform_md


# region FUNC_register_in_node_yaml
## @purpose  Register a project in node.yaml via NodeYaml CLI mutation API.
##           Shared between new-project (scaffolder) and adopt-project (adopter).
## @param name         Project name
## @param org          Organization name
## @param node         Node name
## @param ptype        Project type (frontend/backend/adopted)
## @param domain       Domain name (optional)
## @param database     Database name (optional)
## @param yaml_path    Explicit path to node.yaml
## @param dry_run      If True, print plan only
## @param context      Deployment context (optional)
## @return   True on success, False on failure/skip
## @complexity O(1) — subprocess NodeYaml CLI
## @invariants
##   - Checks for duplicate via NodeYaml CLI --find-project before adding
##   - Idempotent: skips if project already registered
##   - Uses NodeYaml CLI mutation API --add-project
##   - Falls back to manual instructions if python3 unavailable
def register_in_node_yaml(
    name: str,
    org: str = "",
    node: str = "",
    ptype: str = "",
    domain: str = "",
    database: str = "",
    yaml_path: str | Path = "",
    dry_run: bool = False,
    context: str = "",
) -> bool:
    """Register project in node.yaml.

    ## @purpose  Unified from add-project.sh register_in_node_yaml + adopter.register_in_node_yaml.
    ## @io        ⇥ name, org, node, ptype, domain, database, yaml_path → ⎋ bool
    ## @complexity O(1)
    ## @invariants
    ##   - Idempotent: skips duplicate registration
    ##   - Dry-run: prints plan, no mutation
    """
    if not yaml_path:
        logger.info("[IMP:8][helpers][register] No yaml_path — skipping registration")
        return False

    yaml_path = Path(yaml_path) if not isinstance(yaml_path, Path) else yaml_path

    if not yaml_path.exists():
        logger.info("[IMP:8][helpers][register] node.yaml not found: %s", yaml_path)
        logger.info("[IMP:8][helpers][register]   Manually add to %s:", yaml_path)
        logger.info("[IMP:8][helpers][register]     - name: %s", name)
        logger.info("[IMP:8][helpers][register]     repo: %s/%s", org, name)
        return False

    if dry_run:
        logger.info(
            "[IMP:7][helpers][register] [DRY-RUN] Would register in node.yaml: name=%s repo=%s/%s type=%s",
            name,
            org,
            name,
            ptype,
        )
        return True

    logger.info("[IMP:7][helpers][register] Registering project in node.yaml: %s", yaml_path)

    # Check for duplicates via NodeYaml CLI
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "core.internal.shared.node_yaml",
                "--file",
                str(yaml_path),
                "--find-project",
                name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() and name in result.stdout:
            logger.info("[IMP:7][helpers][register] Project already registered: %s — SKIP (idempotent)", name)
            return True
    except FileNotFoundError:
        logger.info("[IMP:8][helpers][register] python3 not available — cannot register")
        return False

    # Register via NodeYaml CLI mutation API
    domain_arg = domain or "-"
    database_arg = database or "-"
    context_arg = context or os.environ.get("CONTEXT", "-")

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "core.internal.shared.node_yaml",
                "--file",
                str(yaml_path),
                "--add-project",
                name,
                f"{org}/{name}",
                ptype or "project",
                domain_arg,
                database_arg,
                context_arg,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][helpers][register] Project registered in node.yaml: %s", name)
            return True

        logger.info("[IMP:8][helpers][register] NodeYaml CLI add-project failed: %s", result.stderr.strip())
        return False
    except FileNotFoundError:
        logger.info("[IMP:8][helpers][register] python3 not available — register manually")
        return False


# endregion FUNC_register_in_node_yaml


# region FUNC_validate_org_against_node_yaml
## @purpose  Validate org against node.yaml context (case-insensitive). Returns canonical org.
##            Перенесена из project_adopter.py (B9 T5, U-32) — shared org-валидация (D6 full PyYAML
##            версия; shell-версия — fast grep в adopt-project.sh). Re-export из project_adopter.
## @io        ⇥ org: str, node_yaml_path: Path → ⎋ str — canonical org from node.yaml
##            ⚡ raises ConfigValidationError if org does not match (even case-insensitive)
## @complexity O(1)
## @invariants
##   - Case-insensitive comparison; casing differs → node.yaml variant (canonical)
##   - node.yaml not found / no context → org unchanged
##   - D6: duplicated in shell (fast grep) AND Python (full PyYAML)
def validate_org_against_node_yaml(org: str, node_yaml_path: Path) -> str:
    """Validate org against node.yaml context (case-insensitive). Returns canonical org."""
    if not node_yaml_path.exists():
        logger.info("[IMP:9][validate_org] node.yaml not found at %s — skipping context validation", node_yaml_path)
        return org

    try:
        from core.internal.shared.exceptions import ConfigValidationError
        from core.internal.shared.node_yaml import ConfigNotFoundError, ConfigParseError, NodeYaml

        node = NodeYaml(str(node_yaml_path))
        node_context = node.get_context()
    except (ConfigNotFoundError, ConfigParseError):
        logger.info("[IMP:9][validate_org] Cannot parse node.yaml — skipping context validation")
        return org
    if not node_context:
        logger.info("[IMP:9][validate_org] node.yaml has no context field — skipping validation")
        return org

    # Case-insensitive comparison
    if org.lower() != str(node_context).lower():
        logger.info(
            "[IMP:9][validate_org] FAIL-FAST: org='%s' vs node.yaml context='%s' — mismatch detected",
            org,
            node_context,
        )
        raise ConfigValidationError(
            f"Project org '{org}' does not match node.yaml context '{node_context}'. "
            f"Use --org {node_context} or update node.yaml context."
        )

    # Casing mismatch → adopt node.yaml variant
    if org != node_context:
        logger.info(
            "[IMP:9][validate_org] Casing mismatch: org='%s' vs node.yaml context='%s' — using node.yaml variant",
            org,
            node_context,
        )
        return str(node_context)

    logger.info("[IMP:9][validate_org] node.yaml context validated: %s", org)
    return org


# endregion FUNC_validate_org_against_node_yaml
