"""
# GREP_SUMMARY: test_node_yaml, get-context, node-yaml, yaml, shared-lib, context-extraction, contexts-canon, H1, mixin-parity, atomic-write
# STRUCTURE: ▶ tmp_path + caplog → ◇ contexts dict array → ◇ missing → ◇ empty → ◇ legacy context field (negative) → ◇ mixin parity (R5, H1) → ◇ atomic write (R5, H2) → ⎋ LDD IMP:9 assertions
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/node_yaml/ (DevPlan 119 H1) — NodeYaml.get_context()
##           + R5-тесты декомпозиции: mixin-parity (все потребители .get() работают через агрегатор)
##           и atomic-write (нет partial write node.yaml, _write_back → atomic_writer, H2).
## @scope    Tests the contexts[] canon (invariant 3, DevPlan 116 B6 T1): contexts[0].name extraction,
##           empty handling, and negative case for the REMOVED legacy top-level 'context' field.
##           R5 (Test Honesty): test_node_yaml_mixin_parity_negative + test_write_back_atomic_negative.
## @invariants
##   - All YAML files created via tmp_path (no hardcoded paths)
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - get_context() → "" when contexts missing/empty (no-raise contract)
## @changes  2026-08-03 · DevPlan 119 H1 — R5: test_node_yaml_mixin_parity_negative + test_write_back_atomic_negative
## @changes  2026-07-25 · DevPlan 070 — Created
## @changes  2026-08-01 · DevPlan 116 B6 T1 — rewritten: extract-context alias tests → get_context() canon
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

# Load the LDD trajectory decorator
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SHARED_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "shared"
sys.path.insert(0, str(_SHARED_DIR))
import node_yaml as ny

# ═══════════════════════════════════════════════════════════════════
# region Tests: NodeYaml.get_context (contexts[] canon)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · get_context from contexts[0].name dict
# · Scenario: node.yaml with `contexts: [{name: "myorg"}]` → returns "myorg"
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: contexts[] canon semantics change
@ldd_trajectory
def test_get_context_from_contexts_dict(caplog, tmp_path):
    """NodeYaml.get_context() should return contexts[0].name (dict-form canon)."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("contexts:\n  - name: myorg\n    description: test\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == "myorg"

    logger.critical("[IMP:9][test] get_context_from_contexts_dict: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get_context ignores legacy top-level context field
# · Scenario: node.yaml with `context: legacy` + `contexts: [{name: "canon"}]` → returns "canon"
# · Last fail: N/A (canon per DevPlan 116 B6 T1 — legacy field removed from priority)
# · Remove if: contexts[] canon semantics change
@ldd_trajectory
def test_get_context_prefers_contexts_over_legacy(caplog, tmp_path):
    """get_context() must read contexts[0].name, NOT the removed legacy 'context' field."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("context: legacy\ncontexts:\n  - name: canon\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == "canon"

    logger.critical("[IMP:9][test] get_context_prefers_contexts: context=%s — OK", result)


# 🧪 TRAP[TEST] · Regression · get_context when contexts missing → ""
# · Scenario: node.yaml with no contexts field → returns "" (no raise)
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: no-raise contract changes
@ldd_trajectory
def test_get_context_missing(caplog, tmp_path):
    """get_context() should return '' when contexts field is absent."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("domain: example.com\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_missing: result='' — OK")


# 🧪 TRAP[TEST] · Regression · get_context on empty contexts list → ""
# · Scenario: `contexts: []` → returns "" (no IndexError, no raise)
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: no-raise contract changes
@ldd_trajectory
def test_get_context_empty_contexts(caplog, tmp_path):
    """get_context() should return '' for an empty contexts list (no IndexError)."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("contexts: []\n")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_empty_contexts: result='' — OK")


# 🧪 TRAP[TEST] · Regression · get_context on empty YAML → ""
# · Scenario: empty YAML file → returns ""
# · Last fail: N/A (canon per DevPlan 116 B6 T1)
# · Remove if: no-raise contract changes
@ldd_trajectory
def test_get_context_empty_yaml(caplog, tmp_path):
    """get_context() should return '' for an empty YAML file."""
    yaml_file = tmp_path / "node.yaml"
    yaml_file.write_text("")

    result = ny.NodeYaml(str(yaml_file)).get_context()
    assert result == ""

    logger.critical("[IMP:9][test] get_context_empty_yaml: result='' — OK")


# 🧪 TRAP[TEST] · Regression · get_context on missing file → raises ConfigNotFoundError
# · Scenario: nonexistent file path → ConfigNotFoundError (get_context no longer absorbs errors)
# · Last fail: N/A (canon per DevPlan 116 B6 T2 — exception-absorbing alias removed)
# · Remove if: facade error contract changes
@ldd_trajectory
def test_get_context_missing_file_raises(caplog, tmp_path):
    """get_context() on a nonexistent file must raise ConfigNotFoundError (facade, not absorbing alias)."""
    from core.internal.shared.exceptions import ConfigNotFoundError

    missing = tmp_path / "nonexistent.yaml"

    # R1 (B10 T1): pytest.raises instead of try/except with bare pass
    with pytest.raises(ConfigNotFoundError):
        ny.NodeYaml(str(missing)).get_context()

    logger.critical("[IMP:9][test] get_context_missing_file_raises: ConfigNotFoundError — OK")


# ═══════════════════════════════════════════════════════════════════
# region R5 (DevPlan 119 H1): mixin parity + atomic write
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_node_yaml_mixin_parity — H1 декомпозиция
# · Scenario: монолит node_yaml.py (1164 LOC) декомпозирован в пакет node_yaml/ (миксины).
# ·   Публичный API агрегатора ДОЛЖЕН быть идентичен монолиту — иначе ~21 потребитель
# ·   NodeYaml.get() сломается (AC-H1.2/AC-H3.1, verify-then-delete).
# · Last fail: монолит до H1 (все 21 потребитель работали через node_yaml.py)
# · Remove if: публичный API NodeYaml намеренно меняется (архитектурное решение)
@ldd_trajectory
def test_node_yaml_mixin_parity_negative(caplog, tmp_path):
    """R5 negative (H1): все методы монолита доступны на агрегаторе NodeYaml (parity).

    ## @purpose  verify-then-delete: декомпозиция не должна потерять публичный API —
    ##            иначе 21 потребитель NodeYaml.get() ломается (AC-H1.2/AC-H3.1).
    ## @invariants  Полный набор методов монолита node_yaml.py (до 119 H1) присутствует
    ##              на NodeYaml-агрегаторе (наследуется из миксинов + ядро).
    """
    node = ny.NodeYaml(str(tmp_path / "node.yaml"))
    expected = {
        # ядро (NodeYamlCore)
        "get",
        "get_list",
        "load",
        "reload",
        "raw",
        # DomainsMixin
        "get_context",
        "get_domain_config",
        "add_context",
        # ProjectsMixin
        "get_projects",
        "get_project",
        "get_project_entries",
        "add_project",
        "remove_project",
        "update_project",
        # ModulesMixin
        "get_modules",
        # NodeMixin
        "get_node_info",
        # ValidationMixin
        "validate",
        # ResolveMixin
        "resolve",
    }
    missing = sorted(name for name in expected if not hasattr(node, name))
    assert not missing, f"R5 FAIL (H1): NodeYaml-агрегатор потерял методы монолита: {missing}"
    logger.critical("[IMP:9][test] mixin_parity: %d методов монолита доступны на агрегаторе — OK", len(expected))


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_write_back_atomic — H2 partial write
# · Scenario: _write_back() делегирует в shared/atomic_writer (tempfile+fsync+os.replace, E5/H2).
# ·   Прерывание/сбой записи НЕ должен оставлять частично записанный node.yaml (R5: no partial write).
# · Last fail: до E5 — ручной os.replace без fsync/cleanup (риск partial write)
# · Remove if: _write_back перестаёт использовать atomic_writer (архитектурное решение)
@ldd_trajectory
def test_write_back_atomic_negative(caplog, tmp_path, monkeypatch):
    """R5 negative (H2): _write_back использует atomic_writer — нет partial write node.yaml.

    ## @purpose  AC-H2.2: R5 negative-тест — прерывание записи не оставляет мусор/target
    ##            в частичном состоянии (атомарность через shared/atomic_writer, DevPlan 119 E5/H2).
    ## @invariants  monkeypatch os.replace на сбой → OSError пробрасывается, target НЕ
    ##              перезаписан частично, временные .tmp файлы не остаются в директории.
    """

    import core.internal.shared.atomic_writer as aw_mod

    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text("contexts:\n  - name: c1\nnode:\n  name: n\n  host: 1.2.3.4\n")
    node = ny.NodeYaml(str(yaml_path))
    node.load()  # cache filled

    def _boom(src: str, dst: str) -> None:
        raise OSError("simulated replace failure (R5 H2)")

    monkeypatch.setattr(aw_mod.os, "replace", _boom)
    from core.internal.shared.exceptions import ConfigParseError

    with pytest.raises(ConfigParseError):
        node.add_project(ny.ProjectEntry(name="p1", repo="org/p1", type="backend"))

    # target не должен содержать добавленный проект (partial write исключён)
    content = yaml_path.read_text()
    assert "p1" not in content, f"R5 FAIL (H2): partial write — проект попал в target: {content}"
    # временные .tmp файлы не остаются
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"R5 FAIL (H2): остались временные файлы: {leftovers}"
    logger.critical("[IMP:9][test] write_back_atomic: сбой replace → target нетронут, tmp нет — OK")


# endregion R5 (DevPlan 119 H1)


# endregion Tests: NodeYaml.get_context
