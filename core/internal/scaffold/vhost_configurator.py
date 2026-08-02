#!/usr/bin/env python3
# GREP_SUMMARY: vhost-configurator, configure-vhost, update-yaml-for-vhost, configure-vhost-via-subprocess, resolve-node-configs-dir, add-vhost, vhost-renderer
# STRUCTURE: ▶ configure_vhost ┌domain? → SKIP│ ⚡ update_yaml_for_vhost (needs.domain+expose) → ◇ try vhost_renderer (D4 primary) → ⎋ fallback configure_vhost_via_subprocess (add-vhost.sh)
# region MODULE_CONTRACT
## @purpose  Nginx vhost конфигурация для adopt-project (D4) — вынесена из project_adopter.py
##           (B9 T5, U-32). Все функции ПУБЛИЧНЫЕ с явными параметрами вместо self-полей.
## @scope    scaffold/vhost_configurator.py: configure_vhost, update_yaml_for_vhost,
##           configure_vhost_via_subprocess, resolve_node_configs_dir.
##           Вызывается ProjectAdopter (adopt step 8).
## @invariants
##   - Без domain → SKIP (False), без мутаций
##   - D4: try vhost_renderer (Python API) → fallback add-vhost.sh subprocess
##   - update_yaml_for_vhost: needs.domain + expose:true перед генерацией vhost
##   - resolve_node_configs_dir: projects/<org>/node-configs → PROJECTS_ROOT env fallback
## @rationale DevPlan 116 B9 D5: полный сплит project_adopter — vhost-логика в отдельный модуль.
## @changes  2026-08-01 · Extracted from project_adopter.py (B9 T5)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# region FUNC_configure_vhost
## @purpose  Configure nginx vhost for the project if domain is set (D4).
##            Tries direct import of vhost_renderer (TASK-036B), falls back to subprocess add-vhost.sh.
## @param project_dir       Project root directory
## @param domain            Project domain ("" → skip)
## @param org               GitHub org / platform context (для resolve_node_configs_dir fallback)
## @param yaml_file         Path to ai-platform.yaml (обновляется перед vhost)
## @param node_configs_dir  Node configs directory (None → auto-resolve)
## @param log_prefix        Log prefix (typically "adopt")
## @io        ⇥ project_dir: Path, domain: str, org: str, yaml_file: Path,
##           node_configs_dir: Path | None, log_prefix: str → ⎋ bool
## @complexity O(1) — delegates to vhost_renderer or subprocess
## @invariants
##   - If no domain configured → skip (return False)
##   - D4: try/except ImportError → subprocess add-vhost.sh fallback
##   - Updates ai-platform.yaml needs.domain and expose:true before vhost generation
##   - If add-vhost.sh not found → skip with log message
def configure_vhost(
    project_dir: Path,
    domain: str,
    org: str,
    yaml_file: Path,
    node_configs_dir: Path | None = None,
    log_prefix: str = "adopt",
) -> bool:
    """Configure nginx vhost for the project."""
    if not domain:
        logger.info("[IMP:6][%s][vhost] No domain configured — skipping vhost", log_prefix)
        return False

    # Ensure ai-platform.yaml has the domain set
    update_yaml_for_vhost(yaml_file, domain, log_prefix=log_prefix)

    # D4: Try direct import vhost_renderer, fallback to subprocess add-vhost.sh
    try:
        from core.internal.scaffold.vhost_renderer import (  # type: ignore[import-untyped]
            configure_vhost_for_project,
        )

        logger.info("[IMP:7][%s][vhost] Using vhost_renderer (Python API)", log_prefix)
        result = configure_vhost_for_project(
            project_dir=project_dir,
            domain=domain,
            node_configs_dir=node_configs_dir,
        )
        if result:
            logger.info("[IMP:9][%s][vhost] Vhost configured via vhost_renderer for: %s", log_prefix, domain)
            return True
        logger.info("[IMP:8][%s][vhost] vhost_renderer returned False — trying subprocess fallback", log_prefix)
    except ImportError:
        logger.info(
            "[IMP:7][%s][vhost] vhost_renderer not available — using subprocess add-vhost.sh (D4 fallback)",
            log_prefix,
        )

    # Fallback: subprocess add-vhost.sh
    return configure_vhost_via_subprocess(project_dir, domain, org, node_configs_dir, log_prefix=log_prefix)


# endregion FUNC_configure_vhost


# region FUNC_update_yaml_for_vhost
## @purpose  Ensures ai-platform.yaml has needs.domain set and expose:true before vhost generation.
## @param yaml_file   Path to ai-platform.yaml
## @param domain      Project domain
## @param log_prefix  Log prefix (typically "adopt")
## @io        ⎋ side-effect: modifies yaml file
## @complexity O(1)
def update_yaml_for_vhost(yaml_file: Path, domain: str, log_prefix: str = "adopt") -> None:
    """Update ai-platform.yaml for vhost generation."""
    if not yaml_file.exists():
        return

    try:
        import yaml

        # Чтение через единый shared-ридер (B1): yaml_file == <project_dir>/ai-platform.yaml
        from core.internal.shared import project_yaml as shared_project_yaml

        data = shared_project_yaml.load_project_yaml(yaml_file.parent)

        needs = data.get("needs", {})
        if isinstance(needs, dict):
            if domain:
                needs["domain"] = domain
                needs["expose"] = True
            data["needs"] = needs

            with open(yaml_file, "w") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            logger.info(
                "[IMP:7][%s][vhost] ai-platform.yaml updated: needs.domain=%s, expose=true",
                log_prefix,
                domain,
            )
    except (ImportError, yaml.YAMLError):
        logger.info("[IMP:8][%s][vhost] Could not update ai-platform.yaml (PyYAML not available)", log_prefix)


# endregion FUNC_update_yaml_for_vhost


# region FUNC_configure_vhost_via_subprocess
## @purpose  Configure vhost via subprocess add-vhost.sh (D4 fallback).
## @param project_dir       Project root directory
## @param domain            Project domain
## @param org               GitHub org / platform context (для resolve_node_configs_dir fallback)
## @param node_configs_dir  Node configs directory (None → auto-resolve)
## @param log_prefix        Log prefix (typically "adopt")
## @io        ⇥ project_dir: Path, domain: str, org: str,
##           node_configs_dir: Path | None, log_prefix: str → ⎋ bool
## @complexity O(1)
def configure_vhost_via_subprocess(
    project_dir: Path,
    domain: str,
    org: str,
    node_configs_dir: Path | None,
    log_prefix: str = "adopt",
) -> bool:
    """Configure vhost via subprocess add-vhost.sh (D4 fallback)."""
    add_vhost_script = Path(__file__).resolve().parent / "add-vhost.sh"

    if not add_vhost_script.exists():
        logger.info("[IMP:8][%s][vhost] add-vhost.sh not found — skipping vhost generation", log_prefix)
        logger.info(
            "[IMP:8][%s][vhost]   Manual: cp <template>/nginx/default.conf to node-configs overlays",
            log_prefix,
        )
        return False

    if node_configs_dir is None:
        node_configs_dir = resolve_node_configs_dir(project_dir, org=org)

    if not node_configs_dir or not node_configs_dir.is_dir():
        logger.info("[IMP:8][%s][vhost] node-configs dir not found: %s", log_prefix, node_configs_dir)
        logger.info("[IMP:8][%s][vhost]   Manual: create vhost manually in overlays/nginx/", log_prefix)
        return False

    logger.info("[IMP:7][%s][vhost] Configuring nginx vhost via add-vhost.sh for domain: %s", log_prefix, domain)

    result = subprocess.run(
        [
            "bash",
            str(add_vhost_script),
            "--project-dir",
            str(project_dir),
            "--node-configs-dir",
            str(node_configs_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode == 0:
        logger.info("[IMP:9][%s][vhost] Vhost configured via add-vhost.sh for: %s", log_prefix, domain)
        return True
    logger.info("[IMP:8][%s][vhost] add-vhost.sh returned non-zero — check vhost manually", log_prefix)
    if result.stderr.strip():
        logger.info("[IMP:8][%s][vhost] add-vhost.sh stderr: %s", log_prefix, result.stderr.strip()[:500])
    return False


# endregion FUNC_configure_vhost_via_subprocess


# region FUNC_resolve_node_configs_dir
## @purpose  Derive node-configs path: projects/{org}/node-configs/ (или PROJECTS_ROOT env).
## @param project_dir  Project root directory
## @param org          GitHub org / platform context (для parent-структуры)
## @io        ⎋ Path | None
## @complexity O(1)
def resolve_node_configs_dir(project_dir: Path, org: str) -> Path | None:
    """Resolve node-configs directory from project path."""
    # Walk up from project dir to find projects root
    parent = project_dir.parent
    if parent.name == org and parent.parent:
        projects_root = parent.parent
        node_configs = projects_root / "node-configs"
        if node_configs.is_dir():
            return node_configs

    # Try alternative: PROJECTS_ROOT env var
    projects_root_env = os.environ.get("PROJECTS_ROOT")
    if projects_root_env:
        candidate = Path(projects_root_env) / org / "node-configs"
        if candidate.is_dir():
            return candidate

    return None


# endregion FUNC_resolve_node_configs_dir
