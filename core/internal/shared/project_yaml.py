#!/usr/bin/env python3
# GREP_SUMMARY: project-yaml ai-platform-yaml reader target_node domain org-from-path shared reader auto-detect
# STRUCTURE: ▶ read_project_yaml(project_dir) → ◇ PyYAML parse → ⊕ target_node/domain (0 grep) → ◇ derive_org_from_path → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Общий читатель ai-platform.yaml (DevPlan 118 E11) — кандидат из аудита монолитов
##           (vhost_renderer — 18 парсеров ai-platform.yaml). Читает target_node/domain через PyYAML
##           (не grep), выводит org из пути. Используется adopt-project (project_adopter.detect_project_config).
## @scope    shared-модуль чтения ai-platform.yaml: read_project_yaml (target_node/domain),
##           derive_org_from_path. НЕ содержит casing-валидацию (та живёт в scaffold_helpers —
##           scaffold-слой, shared не может импортировать scaffold).
## @invariants
##   - 0 grep: ai-platform.yaml читается PyYAML (E11 — анти-survivorship)
##   - domain == "false" → пусто (legacy-семантика adopt-project.sh)
##   - Отсутствующий/битый yaml → пустой dict (fallback chain, не crash)
##   - derive_org_from_path: basename(parent(project_dir)) — путь-производная org
## @rationale E11 (DevPlan открытый вопрос 3): точечный читатель вместо полного reader'а —
##            первый шаг к единому shared/project_yaml (18 парсеров ai-platform.yaml в vhost_renderer).
##            Одновременно снижает project_adopter.py ниже LOC-лимита 600 (гейт B9 T6.2).
## @changes  2026-08-02 | DevPlan 118 E11 — Created (extracted from project_adopter.detect_project_config)
## @see      core/internal/scaffold/project_adopter.py (detect_project_config)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)


# region FUNC_read_project_yaml
## @purpose  Прочитать ai-platform.yaml: target_node + domain (PyYAML, 0 grep).
## @io       ⇥ project_dir: Path → ⎋ dict[str, str] — {target_node, domain} (пустые при отсутствии)
## @complexity O(1) — 1 PyYAML read
## @invariants
##   - yaml-файл отсутствует / не dict → пустые значения
##   - domain == "false" → "" (legacy семантика adopt-project.sh:47)
def read_project_yaml(project_dir: Path) -> dict[str, str]:
    """Read ai-platform.yaml → {target_node, domain} via PyYAML (0 grep, E11)."""
    result = {"target_node": "", "domain": ""}
    yaml_file = project_dir / "ai-platform.yaml"
    if not yaml_file.is_file():
        return result
    try:
        data: Any = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            result["target_node"] = str(data.get("target_node", "") or "")
            result["domain"] = str(data.get("domain", "") or "")
            if result["domain"] == "false":
                result["domain"] = ""
    except (OSError, yaml.YAMLError) as exc:
        logger.info("[IMP:7][project_yaml][read] ai-platform.yaml parse skipped (%s)", exc)
        return result
    logger.info(
        "[IMP:8][project_yaml][read] ai-platform.yaml read (target_node=%s domain=%s)",
        result["target_node"],
        result["domain"],
    )
    return result


# endregion FUNC_read_project_yaml


# region FUNC_derive_org_from_path
## @purpose  Вывести org из пути: basename(parent(project_dir)) — путь-производная org.
## @io       ⇥ project_dir: Path → ⎋ str — org из пути ("" при пустом parent)
## @complexity O(1)
def derive_org_from_path(project_dir: Path) -> str:
    """Derive org from directory path (basename of parent dir)."""
    return project_dir.parent.name or ""


# endregion FUNC_derive_org_from_path


# region FUNC_detect_project_config
## @purpose  Auto-detect name/org/node/domain для adopt-project (DevPlan 118 E11) — заменяет
##           grep-YAML в adopt-project.sh parse_args. Общий читатель ai-platform.yaml +
##           org-from-path + casing-валидация vs node.yaml (NodeYaml из shared, НЕ scaffold —
##           shared слой не импортирует scaffold).
## @io       ⇥ project_dir: Path, name/org/node/domain: str | None → ⎋ dict[str, str]
##           {name, org, node, domain} — резолвнутые значения (fallback-цепочки сохранены)
## @complexity O(1) — 1 PyYAML read + path-операции
## @raises   ConfigValidationError — org пуст после fallback-цепочек (fail-fast)
## @invariants
##   - name: basename(project_dir) если не задан
##   - node: --node → ai-platform.yaml target_node → PLATFORM_DEFAULT_NODE → "tronyx-vps"
##   - domain: --domain → ai-platform.yaml domain (значение "false" → пусто)
##   - org: --org → basename(parent(project_dir)) → PLATFORM_ORG → fail-fast ConfigValidationError
##   - casing-валидация: node.yaml context (case-insensitive; casing-diff → node.yaml вариант)
##   - 0 grep: YAML читается PyYAML (E11 — анти-survivorship: grep-YAML удалён из shell)
def detect_project_config(
    project_dir: Path,
    name: str | None = None,
    org: str | None = None,
    node: str | None = None,
    domain: str | None = None,
) -> dict[str, str]:
    """Auto-detect name/org/node/domain for adoption (E11, replaces grep-YAML in shell)."""
    project_dir = project_dir.resolve()
    resolved_name = name or project_dir.name

    yaml_cfg = read_project_yaml(project_dir)
    yaml_node, yaml_domain = yaml_cfg["target_node"], yaml_cfg["domain"]

    # ── node: --node → yaml → PLATFORM_DEFAULT_NODE → tronyx-vps ──
    resolved_node = node or yaml_node or os.environ.get("PLATFORM_DEFAULT_NODE", "") or "tronyx-vps"

    # ── domain: --domain → yaml (skip "false") ──
    resolved_domain = (domain or yaml_domain) or ""

    # ── org: --org → path-basename → PLATFORM_ORG → fail-fast ──
    resolved_org = org or derive_org_from_path(project_dir) or os.environ.get("PLATFORM_ORG", "")
    if not resolved_org:
        raise ConfigValidationError("PROJECT_ORG is not set. Use --org <github-org> or set PLATFORM_ORG env.")

    # ── casing-валидация vs node.yaml context (NodeYaml из shared — E11, Python не grep) ──
    projects_root = os.environ.get("PROJECTS_ROOT", "")
    if projects_root:
        candidate = Path(projects_root) / resolved_org / "node-configs" / resolved_node / "node.yaml"
        if candidate.is_file():
            resolved_org = _validate_org_vs_node_yaml(resolved_org, candidate)
        else:
            logger.info(
                "[IMP:6][project_yaml][detect] node.yaml not found at %s — skipping context validation", candidate
            )

    result = {"name": resolved_name, "org": resolved_org, "node": resolved_node, "domain": resolved_domain}
    logger.info(
        "[IMP:9][project_yaml][detect] Detected: name=%s org=%s node=%s domain=%s",
        resolved_name,
        resolved_org,
        resolved_node,
        resolved_domain or "<none>",
    )
    return result


# endregion FUNC_detect_project_config


# region FUNC_validate_org_vs_node_yaml
## @purpose  Casing-валидация org vs node.yaml context (case-insensitive; casing-diff → node.yaml
##           вариант canonical). Python-аналог scaffold_helpers.validate_org_against_node_yaml
##           (shared НЕ может импортировать scaffold — слой-правило).
## @io       ⇥ org: str, node_yaml_path: Path → ⎋ str — canonical org
## @complexity O(1) — NodeYaml context read
## @raises   ConfigValidationError — case-insensitive mismatch
def _validate_org_vs_node_yaml(org: str, node_yaml_path: Path) -> str:
    """Validate org against node.yaml context (case-insensitive). Returns canonical org."""
    try:
        from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError
        from core.internal.shared.node_yaml import NodeYaml

        node = NodeYaml(str(node_yaml_path))
        node_context = node.get_context()
    except (ConfigNotFoundError, ConfigParseError, OSError, ValueError):
        logger.info("[IMP:7][project_yaml][validate] node.yaml context read skipped (parse/not-found)")
        return org
    if not node_context:
        logger.info("[IMP:7][project_yaml][validate] node.yaml has no context field — skipping validation")
        return org
    if org.lower() != str(node_context).lower():
        logger.warning(
            "[IMP:9][project_yaml][validate] FAIL-FAST: org='%s' vs node.yaml context='%s' — mismatch detected",
            org,
            node_context,
        )
        raise ConfigValidationError(
            f"Project org '{org}' does not match node.yaml context '{node_context}'. "
            f"Use --org {node_context} or update node.yaml context."
        )
    if org != node_context:
        logger.warning("[IMP:9][project_yaml][validate] Casing mismatch — using node.yaml variant: %s", node_context)
        return str(node_context)
    return org


# endregion FUNC_validate_org_vs_node_yaml
