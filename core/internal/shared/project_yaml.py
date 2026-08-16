#!/usr/bin/env python3
# GREP_SUMMARY: project-yaml ai-platform-yaml reader target_node domain org-from-path shared reader auto-detect expose needs llm monitoring name type
# STRUCTURE: ▶ load_project_yaml(project_dir) → ◇ PyYAML parse (0 grep) → ⊕ typed accessors (expose/domain/target_node/needs/llm/monitoring/name/type) → ◇ derive_org_from_path → ⎋ detect_project_config (fallback chain)
# region MODULE_CONTRACT
## @purpose  ЕДИНСТВЕННЫЙ читатель ai-platform.yaml (DevPlan 118 E11 + 119 B1). Расширен на B1:
##           load_project_yaml (полный dict) + типизированные аксессоры (expose/domain/target_node/
##           needs/llm/monitoring/name/type) — мигрировано 8 потребителей yaml.safe_load
##           (vhost_renderer, vhost_configurator, conflict_checks, monitoring_config_renderer,
##           project_registry, deploy_engine, generate_catalog, orchestrator) на единый reader.
##           Также читает target_node/domain через PyYAML (не grep), выводит org из пути.
##           Используется adopt-project (project_adopter.detect_project_config).
## @scope    shared-модуль чтения ai-platform.yaml: load_project_yaml + аксессоры (B1),
##           read_project_yaml (target_node/domain), derive_org_from_path, detect_project_config.
##           НЕ содержит casing-валидацию (та живёт в scaffold_helpers — scaffold-слой,
##           shared не может импортировать scaffold).
## @invariants
##   - 0 grep + 0 yaml.safe_load ai-platform.yaml ВНЕ этого модуля (AC-B1.1, B1)
##   - domain: needs.domain → top-level domain fallback; "false"/False/"none"/"no"/"null" → пусто
##   - Отсутствующий/битый yaml → пустой dict (fallback chain, не crash)
##   - get_target_node(required=True): отсутствие → ConfigValidationError (не None, R5 B1)
##   - get_llm: llm-секция возвращается ТОЛЬКО если dict (иначе None)
##   - derive_org_from_path: basename(parent(project_dir)) — путь-производная org
## @rationale E11 (DevPlan открытый вопрос 3): точечный читатель вместо полного reader'а —
##            первый шаг к единому shared/project_yaml (18 парсеров ai-platform.yaml в vhost_renderer).
##            B1 (DevPlan 119): 8 файлов с yaml.safe_load ai-platform.yaml → единый reader;
##            унификация домен-семантики (needs.domain → top-level, false-y → пусто) в одном месте.
## @changes  2026-08-02 | DevPlan 118 E11 — Created (extracted from project_adopter.detect_project_config)
## @changes  2026-08-02 | DevPlan 119 B1 — +load_project_yaml + аксессоры (expose/domain/target_node/
##                      needs/llm/monitoring/name/type); read_project_yaml переписан поверх аксессоров
## @see      core/internal/scaffold/project_adopter.py (detect_project_config)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import yaml

from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError, ConfigValidationError

logger = logging.getLogger(__name__)

# ── False-y domain значения (унифицированы с conflict_checks._FALSEY_DOMAINS, B1) ──
_FALSEY_DOMAIN_VALUES: frozenset[str] = frozenset({"false", "none", "no", "null", ""})


# region FUNC_load_project_yaml
## @purpose  Прочитать ai-platform.yaml → полный dict (PyYAML, 0 grep). Единственная точка
##           yaml.safe_load ai-platform.yaml в core/ (AC-B1.1). Lenient: отсутствие/битый
##           yaml/не-dict → {} (fallback chain, никогда не raise).
## @io       ⇥ project_dir: Path → ⎋ dict[str, object] — полное содержимое ai-platform.yaml
## @complexity O(1) — 1 PyYAML read
## @invariants
##   - Файл отсутствует / не dict / YAMLError → {} (мигрированные потребители полагаются на это)
##   - НЕ мутирует входной dict — возвращает свежий dict
def load_project_yaml(project_dir: Path) -> dict[str, object]:
    """Read ai-platform.yaml → full dict ({} if missing/unparseable, B1 canonical reader)."""
    yaml_file = Path(project_dir) / "ai-platform.yaml"
    if not yaml_file.is_file():
        logger.info("[IMP:7][project_yaml][load] ai-platform.yaml not found: %s", yaml_file)
        return {}
    try:
        # yaml.safe_load → Any; object-граница — проверки isinstance ниже (W11)
        data: object = cast(object, yaml.safe_load(yaml_file.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        logger.info("[IMP:7][project_yaml][load] ai-platform.yaml parse skipped (%s)", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("[IMP:8][project_yaml][load] ai-platform.yaml is not a dict — returning {}")
        return {}
    return data


# endregion FUNC_load_project_yaml


# region FUNC_get_needs
## @purpose  Типизированный аксессор needs-секции (B1). Возвращает dict ({} если не dict).
## @io       ⇥ data: dict → ⎋ dict[str, object]
## @complexity O(1)
def get_needs(data: dict[str, object]) -> dict[str, object]:
    """Extract needs section from ai-platform.yaml dict ({} if absent/not-dict)."""
    needs = data.get("needs", {})
    return needs if isinstance(needs, dict) else {}


# endregion FUNC_get_needs


# region FUNC_get_expose
## @purpose  Типизированный аксессор expose (B1): needs.expose → top-level expose (fallback).
## @io       ⇥ data: dict → ⎋ bool
## @complexity O(1)
## @invariants
##   - needs.expose приоритетнее; top-level expose — фолбэк (vhost_renderer семантика)
def get_expose(data: dict[str, object]) -> bool:
    """Extract expose flag (needs.expose → top-level expose fallback, B1)."""
    expose = get_needs(data).get("expose", False)
    if not expose:
        expose = data.get("expose", False)
    return bool(expose)


# endregion FUNC_get_expose


# region FUNC_get_domain
## @purpose  Типизированный аксессор domain (B1): needs.domain → top-level domain (fallback).
##           False-y значения ("false"/False/"none"/"no"/"null"/"") → "" (унифицировано с
##           conflict_checks._FALSEY_DOMAINS; read_project_yaml "false" → "").
## @io       ⇥ data: dict → ⎋ str — нормализованный domain ("" если none)
## @complexity O(1)
def get_domain(data: dict[str, object]) -> str:
    """Extract domain (needs.domain → top-level domain fallback; false-y → "", B1)."""
    domain = get_needs(data).get("domain")
    if not domain:
        domain = data.get("domain")
    if domain is None or domain is False:
        return ""
    domain_str = str(domain).strip()
    if domain_str.lower() in _FALSEY_DOMAIN_VALUES:
        return ""
    return domain_str


# endregion FUNC_get_domain


# region FUNC_get_target_node
## @purpose  Типизированный аксессор target_node (B1): top-level target_node.
## @io       ⇥ data: dict, required: bool → ⎋ str ("" если none)
## @raises   ConfigValidationError — required=True и target_node отсутствует/пуст (R5: не None)
## @complexity O(1)
def get_target_node(data: dict[str, object], *, required: bool = False) -> str:
    """Extract target_node (top-level; required=True → ConfigValidationError when missing, B1)."""
    val = str(data.get("target_node", "") or "").strip()
    if required and not val:
        msg = "ai-platform.yaml: target_node is required (missing or empty)"
        raise ConfigValidationError(msg)
    return val


# endregion FUNC_get_target_node


# region FUNC_get_name
## @purpose  Типизированный аксессор name (B1): name → project (fallback) → "".
## @io       ⇥ data: dict → ⎋ str
## @complexity O(1)
def get_name(data: dict[str, object]) -> str:
    """Extract project name (name → project fallback, B1)."""
    return str(data.get("name") or data.get("project") or "")


# endregion FUNC_get_name


# region FUNC_get_project_type
## @purpose  Типизированный аксессор type (B1): top-level type → "".
## @io       ⇥ data: dict → ⎋ str
## @complexity O(1)
def get_project_type(data: dict[str, object]) -> str:
    """Extract project type (top-level, B1)."""
    return str(data.get("type", "") or "")


# endregion FUNC_get_project_type


# region FUNC_get_monitoring
## @purpose  Типизированный аксессор monitoring-секции (B1): top-level monitoring → {}.
## @io       ⇥ data: dict → ⎋ dict[str, object]
## @complexity O(1)
def get_monitoring(data: dict[str, object]) -> dict[str, object]:
    """Extract monitoring section ({} if absent/not-dict, B1)."""
    mon = data.get("monitoring", {})
    return mon if isinstance(mon, dict) else {}


# endregion FUNC_get_monitoring


# region FUNC_get_llm
## @purpose  Типизированный аксессор llm-секции (B1): llm → dict | None (None если не dict).
## @io       ⇥ data: dict → ⎋ dict[str, object] | None
## @complexity O(1)
## @invariants
##   - Возвращает llm ТОЛЬКО если dict (project_registry.discover_llm_projects семантика)
def get_llm(data: dict[str, object]) -> dict[str, object] | None:
    """Extract llm section (None if absent/not-dict, B1)."""
    llm = data.get("llm")
    return llm if isinstance(llm, dict) else None


# endregion FUNC_get_llm


# region FUNC_get_expose_config
## @purpose  Типизированный аксессор vhost-eligibility конфига (B1, DevPlan get_expose_config):
##           {name, domain, target_node, expose, needs} — единая основа для vhost_renderer
##           read_project_yaml (expose:true + domain + target_node → eligible).
## @io       ⇥ data: dict → ⎋ dict[str, object]
## @complexity O(1)
def get_expose_config(data: dict[str, object]) -> dict[str, object]:
    """Extract vhost-eligibility config {name, domain, target_node, expose, needs} (B1)."""
    return {
        "name": get_name(data),
        "domain": get_domain(data),
        "target_node": get_target_node(data),
        "expose": get_expose(data),
        "needs": get_needs(data),
    }


# endregion FUNC_get_expose_config


# region FUNC_read_project_yaml
## @purpose  Прочитать ai-platform.yaml: target_node + domain (PyYAML, 0 grep).
##           Переписан поверх load_project_yaml/get_domain/get_target_node (B1) — domain теперь
##           needs.domain → top-level fallback (унификация с vhost/conflict-семантикой).
## @io       ⇥ project_dir: Path → ⎋ dict[str, str] — {target_node, domain} (пустые при отсутствии)
## @complexity O(1) — 1 PyYAML read
## @invariants
##   - yaml-файл отсутствует / не dict → пустые значения
##   - domain == "false" → "" (семантика adopt-project.sh:47)
def read_project_yaml(project_dir: Path) -> dict[str, str]:
    """Read ai-platform.yaml → {target_node, domain} via PyYAML (0 grep, E11/B1)."""
    data = load_project_yaml(project_dir)
    result = {
        "target_node": get_target_node(data),
        "domain": get_domain(data),
    }
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
## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · resolve_fn на detect_project_config (path-resolve seam)
## · Rejected: прямой вызов project_dir.resolve()
## · Reason: seam = тестируемость реального org-from-path (derive_org_from_path) без глобального
## ·   патча pathlib.Path.resolve (класс-мутация затрагивает весь процесс); default неизменен
## · Rev: появление общего path-нормализатора → единый DI-параметр
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
    *,
    resolve_fn: Callable[[Path], Path] | None = None,
) -> dict[str, str]:
    """Auto-detect name/org/node/domain for adoption (E11, replaces grep-YAML in shell)."""
    project_dir = resolve_fn(project_dir) if resolve_fn is not None else project_dir.resolve()
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
        msg = "PROJECT_ORG is not set. Use --org <github-org> or set PLATFORM_ORG env."
        raise ConfigValidationError(msg)

    # ── casing-валидация vs node.yaml context (NodeYaml из shared — E11, Python не grep) ──
    projects_root = os.environ.get("PROJECTS_BASE", "")
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
        msg = (
            f"Project org '{org}' does not match node.yaml context '{node_context}'. "
            f"Use --org {node_context} or update node.yaml context."
        )
        raise ConfigValidationError(msg)
    if org != node_context:
        logger.warning("[IMP:9][project_yaml][validate] Casing mismatch — using node.yaml variant: %s", node_context)
        return str(node_context)
    return org


# endregion FUNC_validate_org_vs_node_yaml
