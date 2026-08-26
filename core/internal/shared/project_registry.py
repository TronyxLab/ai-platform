#!/usr/bin/env python3
# GREP_SUMMARY: project-registry, validate-project-name, discover-llm-projects, node-yaml
# STRUCTURE: ▶ validate_project_name ┌name┐ → ⎋ bool │ discover_llm_projects ┌node_yaml┐ → ⊕ LLM-проекты → ⎋ list
# region MODULE_CONTRACT
## @purpose  Project registry — thin wrapper over NodeYaml for project registration/deregistration/listing.
##           DevPlan 091 Wave C (AC2): migrated from yaml.safe_load/dump to NodeYaml.add_project/remove_project/get_projects.
##           Soft-idempotency preserved via ConfigValidationError bridge for register (hard-error → soft skip).
## @scope    Shell-accessible via CLI subcommands. Python-importable for direct function calls.
## @invariants
##   1. Library functions return (bool, str) tuple
##   2. CLI __main__ calls sys.exit(0/1) for shell compatibility
##   3. Idempotent: register skips via ConfigValidationError catch; deregister is nodeyaml-idempotent
##   4. No direct yaml.safe_load/dump — all YAML ops through NodeYaml
##   5. Logs to stderr at IMP:9 on success/skip, IMP:7-8 for warnings
## @rationale DRIFT-088-7: 3 yaml.safe_load calls were bypassing NodeYaml facade, creating
##            a parallel node.yaml mutation path that didn't benefit from NodeYaml validation,
##            error handling, and mutation safety. Now a thin bridge — consumers unchanged,
##            but node.yaml access is unified through a single facade.
## @changes  2026-07-25 · DevPlan 070 — Created
##           2026-07-26 · DevPlan 038b — sys.exit replaced with return tuple
##           2026-07-30 · DevPlan 091 Wave C — yaml.safe_load → NodeYaml bridge (AC2)
##           2026-08-24 · REF-0008 В2 — fail-fast FQDN-валидация domain на register-входе
# endregion MODULE_CONTRACT

import logging
import os
import re
import sys
from pathlib import Path

# Standalone CLI bootstrap: when run directly (subprocess), add project root to sys.path
# so that `from core.internal.shared.*` imports resolve. This is the same pattern used
# in context_deployer.py and other CLI-accessible shared modules.
if __name__ == "__main__" or not __package__:
    # os.path.abspath — возвращает STR (sys.path требует строки; Path-объект ломает импорты,
    # gh-известный класс багов: _NamespacePath не находит пакет). PTH100/118/120 — per-file-ignore.
    _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

logger = logging.getLogger(__name__)

# DevPlan 091 Wave C (AC2): NodeYaml replaces yaml.safe_load/dump.
# Imports are module-level — the sys.path bootstrap above ensures they resolve
# in standalone CLI (subprocess) mode. For pytest, rootdir = project root.

from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE as DEFAULT_PROJECTS_ROOT
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigValidationError
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.project_yaml import get_llm, load_project_yaml
from core.internal.shared.verbs import is_verb

# ── LLM-проекты: default projects root на VPS (B2 — переиспользование канона deploy_paths) ──
# DEFAULT_PROJECTS_ROOT = deploy_paths.DEFAULT_PROJECTS_BASE (единый SoT, B2)

# ── Project name validation ─────────────────────────────────────────────────
## @purpose  Canonical project name validation used by deploy_engine, payload_deliverer, reconciler,
##           context_initializer, project_scaffolder. Rejects empty names, path traversal sequences,
##           invalid characters, leading '-'/'_' (strict regex, DevPlan 116 B6 T3), and
##           verb-имена из forced-command словаря (U-56: проект «status» задиспатчился бы как verb).
## @invariants
##   - Regex: ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ — must start with alphanumeric; hyphen/underscore allowed
##     only after the first char. STRICT: rejects leading '-'/'_' (эквивалентен бывшему
##     контекстному валидатору context_initializer).
##   - Verb-reserve (U-56, DevPlan 116 B1 T1): is_verb(name) → False. Verb-имена (ping/exit/status/
##     verify/remove/receive) недоступны как имена проектов — иначе SSH_ORIGINAL_COMMAND
##     неотличим от verb. VERB_RESERVE импортируется из shared/verbs.py (единый источник).
##   - Returns bool (never raises, never sys.exit)
##   - DRY: single implementation shared by 5+ consumers
## @rationale D7 (DevPlan 036E): приватный валидатор имён дублировался в shell,
##            reconciler.py:701, и новой payload_deliverer.py. Единая реализация в project_registry.py
##            устраняет дублирование. Regex ^[a-zA-Z0-9_-]+$ строже shell-версии (reject '/..'/special chars).
##            DevPlan 116 B6 T3 (U-06): regex ужесточён до ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ — reject leading
##            '-'/'_' (эквивалент контекстного валидатора); все 3 локальных валидатора
##            (reconciler, context_initializer, project_scaffolder strip-check)
##            мигрированы на этот канон.
##            DevPlan 116 B1 T1 (U-56): verb-имена резервируются через shared/verbs.py.
## @changes 2026-07-26 · DevPlan 036E — Added validate_project_name() for Wave 5e Strangler-Fig
## @changes 2026-08-01 · DevPlan 116 B6 T3 — regex ужесточён: leading '-'/'_' rejected
## @changes 2026-08-01 · DevPlan 116 B1 T1 — verb-reserve: is_verb(name) → False (U-56)


def validate_project_name(name: str) -> bool:
    """Validate project name: alphanumeric first char, then alphanumeric/underscore/hyphen.

    Rejects verb-имена из forced-command словаря (U-56): проект «status» невалиден,
    потому что `status` в SSH_ORIGINAL_COMMAND — это verb диспетчера.

    Args:
        name: Project name string to validate.

    Returns:
        True if valid, False otherwise.
    """
    if not name or not isinstance(name, str):
        return False
    # Verb-reserve (U-56): verb-имена не доступны для проектов — dispatcher-неоднозначность
    if is_verb(name):
        return False
    # Strict regex: must start [a-zA-Z0-9]; then [a-zA-Z0-9_-]* — no spaces, slashes,
    # path traversal ('.' not in class), or leading '-'/'_' (DevPlan 116 B6 T3).
    return bool(re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", name))


# region FUNC_discover_llm_projects
## @purpose — Discover LLM-enabled projects: читает projects из node.yaml, для каждого
##            резолвит projects_root/{org}/{name}/ai-platform.yaml и фильтрует по llm.enabled=true.
##            Реальная альтернатива хардкод-шима key_provisioner.discover_projects (DevPlan 117 D24).
## @io — ⇥ node_yaml_path: str = "", projects_root: str = "", log_prefix: str = "discover-llm-projects"
##        → ⎋ list[dict[str, Any]]: [{"name": ..., "llm": {...}}] — только llm.enabled=true проекты
## @complexity — O(P * Y) где P = проекты в node.yaml, Y = parse ai-platform.yaml
## @invariants
##   - node_yaml_path пуст → NodeYaml.resolve() (env NODE_NAME/PLATFORM_ROOT, 3-path)
##   - projects_root пуст → env PROJECTS_BASE → DEFAULT_PROJECTS_ROOT (/opt/projects)
##   - org проектов: из repo "org/repo" (канон ProjectSpec.from_entry)
##   - Проект без ai-platform.yaml → skip (WARN); без llm.enabled=true → skip
##   - Возвращает только {"name", "llm"} — формат key_provisioner consumers (без repo/domain)
##   - Никогда не raise: ошибки чтения/парсинга → WARN + skip (graceful degradation)
## @rationale key_provisioner.discover_projects был хардкод-шимом (3 тестовых проекта). Реальная
##            детекция LLM-проектов (ai-platform.yaml llm.enabled: true) — через NodeYaml + файловый
##            скан, единый для platform. Количество проектов ≤10 на ноде — O(projects) приемлемо.
## @changes 2026-08-01 · DevPlan 117 D24 — создан (делегирование shim key_provisioner)
def discover_llm_projects(
    node_yaml_path: str = "",
    projects_root: str = "",
    log_prefix: str = "discover-llm-projects",
) -> list[dict[str, object]]:
    """Discover LLM-enabled projects from node.yaml + ai-platform.yaml llm.enabled=true.

    ## @purpose — Реальная детекция LLM-проектов вместо хардкод-шима (DevPlan 117 D24).
    ##            Читает node.yaml через NodeYaml, резолвит каждый проект до ai-platform.yaml
    ##            (projects_root/org/name/), фильтрует по llm.enabled=true.
    ## @returns list[dict] — [{"name": ..., "llm": {...}}] только enabled-проекты
    """
    try:
        ny = NodeYaml(node_yaml_path) if node_yaml_path else NodeYaml.resolve()
    except (ConfigNotFoundError, OSError, ValueError) as e:
        logger.warning("[IMP:8][%s][resolve] Failed to resolve node.yaml: %s", log_prefix, e)
        return []

    root = projects_root or os.environ.get("PROJECTS_BASE") or DEFAULT_PROJECTS_ROOT

    result: list[dict[str, object]] = []
    try:
        project_entries = ny.get_projects()
    except (ConfigNotFoundError, ConfigValidationError, OSError, ValueError) as e:
        logger.warning("[IMP:8][%s][read] Failed to read projects: %s", log_prefix, e)
        return []

    for entry in project_entries:
        name = str(entry.get("name", "") or "")
        repo = str(entry.get("repo", "") or "")
        if not name:
            continue
        org = ""
        if "/" in repo:
            org = repo.split("/", maxsplit=1)[0]

        ai_yaml = (
            os.path.join(root, org, name, "ai-platform.yaml") if org else os.path.join(root, name, "ai-platform.yaml")
        )
        if not os.path.isfile(ai_yaml):
            logger.info("[IMP:7][%s][skip] No ai-platform.yaml for %s at %s", log_prefix, name, ai_yaml)
            continue

        # B1: единый shared-ридер ai-platform.yaml (load_project_yaml + get_llm)
        data = load_project_yaml(Path(ai_yaml).parent)
        if not data:
            logger.warning("[IMP:7][%s][error] Failed to parse %s", log_prefix, ai_yaml)
            continue

        llm = get_llm(data)
        if not llm or not llm.get("enabled"):
            logger.info("[IMP:7][%s][skip] %s — llm not enabled in %s", log_prefix, name, ai_yaml)
            continue

        result.append({"name": name, "llm": llm})
        logger.info("[IMP:9][%s][found] LLM-enabled project: %s", log_prefix, name)

    logger.info("[IMP:9][%s][done] %d LLM-enabled project(s) found", log_prefix, len(result))
    return result


# endregion FUNC_discover_llm_projects


# AI-0059r (DevPlan 17 T6.4): CLI register/deregister/list срезан — конкурирующий «второй»
# реестровый CLI; канон регистрации — scaffold-путь (make new-project → scaffold.mk).
# Библиотечные функции validate_project_name/discover_llm_projects сохранены.
if __name__ == "__main__":
    import sys

    print(
        "project_registry: CLI удалён (AI-0059r). "
        "Канон регистрации проектов — make new-project (scaffold); "
        "библиотечные функции импортируйте напрямую.",
        file=sys.stderr,
    )
    raise SystemExit(2)
