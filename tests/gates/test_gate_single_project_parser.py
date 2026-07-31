# GREP_SUMMARY: gate single-project-parser ProjectEntry canon get_project_entries yaml.safe_load facade single-source
# STRUCTURE: ▶ (a) бывшие callsites: 0 × yaml.safe_load на node.yaml-пути, 0 × class ProjectEntry (vhost_renderer) → ◇ (b) rg "class ProjectEntry" core/ → 1 → ◇ (c) rg "Draft7Validator" (jsonschema_validate, node_yaml) → 0 → ⎋ pass|fail
# region MODULE_CONTRACT
## @purpose  Гейт «единый парсер проектов» (DevPlan 116 B6 T9.2, D6): все парсеры
##           node.yaml#projects делегируют NodeYaml.get_project_entries()/get_projects().
##           (a) бывшие callsites не парсят node.yaml через yaml.safe_load и не объявляют
##           собственный ProjectEntry, (b) единственное определение ProjectEntry в shared,
##           (c) единственная Draft7Validator-точка — shared/schema_validator.py.
## @scope    Read-only gate. Статический grep-анализ 4 callsites + core/.
##           vhost_renderer.py легитимно использует yaml.safe_load для ai-platform.yaml
##           (не node.yaml) — проверка окном ±300 символов, а не запрет всех safe_load.
## @invariants
##   - (a) reconciler.py / context_deployer.py / project_lister.py / vhost_renderer.py:
##         yaml.safe_load НЕ применяется к node.yaml-путям (окно ±300 символов без "node.yaml");
##         vhost_renderer.py не объявляет class ProjectEntry
##   - (b) `rg "class ProjectEntry" core/` → ровно 1 (core/internal/shared/node_yaml.py)
##   - (c) `rg "Draft7Validator" core/internal/scripts/jsonschema_validate.py
##         core/internal/shared/node_yaml.py` → 0 (единственная точка — shared/schema_validator)
##   - Test marked @pytest.mark.gate — runs in `make gate MODE=fast`
## @rationale Typed DTO мертвы (consumers брали raw dicts), парсеры распылены. Единый
##            ProjectEntry + единый парсер + grep-гейты делают расхождение невозможным.
## @changes 2026-08-01 | Created (DevPlan 116 B6 T9.2)
# endregion MODULE_CONTRACT

import logging
import re

import pytest

from tests.conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

ROOT = repo_root()
CORE_DIR = ROOT / "core"

_CALLSITES = [
    CORE_DIR / "internal" / "bootstrap" / "converge" / "reconciler.py",
    CORE_DIR / "internal" / "bootstrap" / "deploy" / "context_deployer.py",
    CORE_DIR / "internal" / "scaffold" / "project_lister.py",
    CORE_DIR / "internal" / "scaffold" / "vhost_renderer.py",
]

# Окно вокруг yaml.safe_load: если "node.yaml" встречается в нём — это парсинг node.yaml
_WINDOW = 300


# region TEST_A_CALLSITES_NO_NODE_YAML_SAFE_LOAD
@pytest.mark.gate
@ldd_trajectory
def test_callsites_no_node_yaml_safe_load(caplog) -> None:
    """(a) бывшие callsites: yaml.safe_load не применяется к node.yaml-путям; нет class ProjectEntry."""
    for path in _CALLSITES:
        content = path.read_text(encoding="utf-8")
        # vhost_renderer не объявляет собственный ProjectEntry (канон в shared)
        if path.name == "vhost_renderer.py":
            assert not re.search(r"^\s*class ProjectEntry", content, re.M), (
                f"{path.name}: local class ProjectEntry declared — import from shared (DevPlan 116 B6 T4.3)"
            )
        # yaml.safe_load не применяется к node.yaml (окно ±300 символов).
        # Матчим ТОЛЬКО вызовы `yaml.safe_load(` — docstring-упоминания без '(' не считаются.
        for m in re.finditer(r"yaml\.safe_load\s*\(", content):
            start = max(0, m.start() - _WINDOW)
            end = min(len(content), m.end() + _WINDOW)
            window = content[start:end]
            assert "node.yaml" not in window, (
                f"{path.name}: yaml.safe_load at char {m.start()} within {_WINDOW} chars of a "
                f"node.yaml reference — node.yaml must be parsed via NodeYaml facade (DevPlan 116 B6 T4/T9)"
            )
        logger.info("[IMP:9][gate_single_parser][a] OK: %s", path.name)
    logger.critical("[IMP:9][gate_single_parser][a] PASS: no node.yaml yaml.safe_load in callsites")


# endregion


# region TEST_B_SINGLE_PROJECT_ENTRY
@pytest.mark.gate
@ldd_trajectory
def test_single_project_entry_definition(caplog) -> None:
    """(b) `rg "class ProjectEntry" core/` → ровно 1 (shared/node_yaml.py — канон)."""
    hits = []
    for p in CORE_DIR.rglob("*.py"):
        content = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*class ProjectEntry", content, re.M):
            hits.append(str(p.relative_to(ROOT)))
    assert hits == ["core/internal/shared/node_yaml.py"], (
        f"expected exactly 1 ProjectEntry definition in shared, got {hits}"
    )
    logger.critical("[IMP:9][gate_single_parser][b] PASS: single ProjectEntry in shared (%s)", hits[0])


# endregion


# region TEST_C_SINGLE_DRAFT7_POINT
@pytest.mark.gate
@ldd_trajectory
def test_single_draft7_validator_point(caplog) -> None:
    """(c) Draft7Validator отсутствует в jsonschema_validate.py и node_yaml.py (T5)."""
    targets = [
        CORE_DIR / "internal" / "scripts" / "jsonschema_validate.py",
        CORE_DIR / "internal" / "shared" / "node_yaml.py",
    ]
    for path in targets:
        content = path.read_text(encoding="utf-8")
        assert "Draft7Validator" not in content, (
            f"{path.name}: Draft7Validator present — единственная точка — shared/schema_validator.py (T5)"
        )
        logger.info("[IMP:9][gate_single_parser][c] OK: %s", path.name)
    logger.critical("[IMP:9][gate_single_parser][c] PASS: single Draft7Validator point in shared/schema_validator")


# endregion
