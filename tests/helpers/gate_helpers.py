# GREP_SUMMARY: gate-helpers, load-yaml, repo-root, assert-ldd-imp9, boilerplate-dedup
# STRUCTURE: ▶ load_yaml(path) → ◇ yaml.safe_load → ⎋ dict
#            ▶ repo_root() → ◇ __file__ resolution (cached) → ⎋ Path
#            ▶ module_yaml_paths() → ◇ glob core/modules/ → ⎋ list[Path]
#            ▶ assert_ldd_imp9(caplog, min_count) → ◇ filter records → ⊕ assert count → ⎋ None
# region MODULE_CONTRACT
## @purpose  Единый source of truth для boilerplate в gate-тестах.
##           Устраняет 6 копий _load_yaml, 57 объявлений PROJECT_ROOT, 10+ копий assert_ldd_imp9.
## @scope    All tests under tests/gates/ и tests/ использующие YAML loading, project root, LDD assertions.
## @invariants
##   - repo_root() кешируется (module-level) — вычисление один раз за сессию
##   - load_yaml использует yaml.safe_load (не FullLoader) для security
##   - assert_ldd_imp9 fails test если нет ни одного [IMP:9]+ log (Test Honesty LDD)
## @rationale Brief 027 §3.1 W1-E4: −25-30% строк в gate-тестах, единый source of truth.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E4)
# endregion MODULE_CONTRACT

import functools
import io
import logging
import pathlib
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# region REPO_ROOT


@functools.lru_cache(maxsize=1)
def repo_root() -> pathlib.Path:
    """Cached project root. Resolves from this file: tests/helpers/ → tests/ → project root."""
    return pathlib.Path(__file__).resolve().parent.parent.parent


# endregion REPO_ROOT


# region YAML_HELPERS


def load_yaml(path: pathlib.Path | str) -> Any:
    """Load YAML file. Uses yaml.safe_load for security.

    Handles !override compose tags by stripping them before parsing.
    """
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"[gate_helpers] YAML file not found: {p}")
    raw = p.read_text()
    # Strip !override tags (compose merge marker, not valid YAML)
    raw = re.sub(r":\s*!override\b", ":", raw)
    return yaml.safe_load(io.StringIO(raw))


def module_yaml_paths() -> list[pathlib.Path]:
    """Glob all module.yaml files under core/modules/."""
    root = repo_root()
    return sorted((root / "core" / "modules").glob("*/module.yaml"))


# endregion YAML_HELPERS


# region LDD_ASSERTIONS


def assert_ldd_imp9(caplog, min_count: int = 1) -> None:
    """Assert that at least min_count [IMP:9+] log records exist in caplog.

    Implements Test Honesty LDD enforcement (RULES.md §TESTING).
    """
    imp9_plus = [
        r
        for r in caplog.records
        if "[IMP:" in r.message
        and any(int(lvl) >= 9 for lvl in [r.message.split("[IMP:")[1].split("]")[0]] if lvl.isdigit())
    ]
    assert len(imp9_plus) >= min_count, (
        f"[gate_helpers] LDD assertion failed: expected >={min_count} [IMP:9+] logs, "
        f"got {len(imp9_plus)}. Records: {[r.message for r in caplog.records[:5]]}"
    )


# endregion LDD_ASSERTIONS


# region WORKFLOW_HELPERS


def load_workflow(workflow_name: str) -> dict:
    """Load a workflow YAML file from .github/workflows/.

    ## @purpose — Parse a CI workflow YAML for structural validation tests.
    ## @io — workflow_name → ⎋ dict (parsed YAML)
    ## @complexity — O(1)
    """
    path = repo_root() / ".github" / "workflows" / workflow_name
    logger.info("[IMP:8][load_workflow] Loading workflow: %s", path)
    return load_yaml(path)


def get_on_section(workflow: dict) -> dict:
    """Get the 'on' trigger section from a workflow, handling PyYAML 'on'→True conversion.

    ## @purpose — YAML parses 'on:' as boolean key True. This helper normalizes
    ##            access by trying both 'on' (string) and True (boolean) keys.
    ## @io — workflow → ⎋ dict (on section)
    ## @complexity — O(1)
    """
    on_section = workflow.get("on") or workflow.get(True) or {}
    if not isinstance(on_section, dict):
        return {}
    return on_section


# endregion WORKFLOW_HELPERS
