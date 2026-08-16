# GREP_SUMMARY: adopt-project org validation fail-fast ghcr lowercase uses exact-case context-mismatch node-yaml detect_project_config E11
# STRUCTURE: ▶ tmp_path fixtures (ai-platform.yaml + node.yaml) → ○ 2 test functions (org-from-path, casing-mismatch) → ◇ assert detect_project_config → ⊕ LDD trajectory → ⎋ IMP:9/10 assertion
# region MODULE_CONTRACT
## @purpose  Tests for Contract 4 — adopt-project org/context/casing validation (DevPlan 008 T4).
##           DevPlan 118 E11: grep-YAML-парсинг перенесён из adopt-project.sh в
##           project_adopter.detect_project_config (PyYAML, не grep); casing-валидация — scaffold_helpers.
##           Тесты вызывают Python detect_project_config напрямую (нативные импорты, tmp_path).
## @scope    2 test functions (test_adopt_derives_org_from_path, test_context_mismatch_detected).
##           Каждый тест создаёт ai-platform.yaml/node.yaml в tmp_path и вызывает detect_project_config.
## @invariants
##   - Zero hardcoded paths — все тесты используют tmp_path для PROJECTS_BASE
##   - Native imports (core.internal.scaffold.project_adopter.detect_project_config) — no subprocess
##   - LDD trajectory printed from caplog; success tests assert IMP:9, failure tests IMP:10
## @rationale Contract 4 prevents Debt D3 recurrence (config-drift from "personal" default).
##            E11: shell grep-YAML удалён — тесты фиксируют Python-имплементацию (R5 anti-survivorship:
##            удалённый shell-API заменён тестами на новый Python-detect).
## @changes CREATED: 2026-07-17 · T4 — Contract 4 org/context/casing tests (shell)
##           2026-08-02 · DevPlan 118 E11 — переписаны на Python detect_project_config (shell grep удалён)
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.scaffold.project_adopter import detect_project_config
from core.internal.shared.exceptions import ConfigValidationError

logger = logging.getLogger(__name__)


# region HELPERS


@pytest.fixture(autouse=True)
def _isolate_projects_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """PROJECTS_BASE → tmp_path so node.yaml casing validation resolves there."""
    monkeypatch.setenv("PROJECTS_BASE", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ORG", raising=False)
    monkeypatch.delenv("PLATFORM_DEFAULT_NODE", raising=False)


# endregion HELPERS


# ═════════════════════════════════════════════════════════════════════════════
# TEST 1: org derived from directory path
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_adopt_derives_org_from_path
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-07-20 · adopt-project derives org from directory path (D2)
# · Regression: if context field is re-added to YAML, path derivation may be shadowed
# · Scenario: project at tmp/testorg/myproject/ → org derived as testorg (E11 Python path)
# · Last fail: N/A (updated for E11 Python detect_project_config)
# · Remove if: path-based org derivation is replaced
def test_adopt_derives_org_from_path(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """detect_project_config derives org from directory path when no --org is given.

    # ▶ detect_project_config(proj_dir_in_org) → ◇ org ← basename(parent) → testorg → ⎋ pass
    """
    caplog.set_level(logging.DEBUG)

    proj_dir = tmp_path / "testorg" / "myproject"
    proj_dir.mkdir(parents=True)

    detected = detect_project_config(project_dir=proj_dir)

    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    assert detected["name"] == "myproject", f"name must be basename, got {detected['name']}"
    assert detected["org"] == "testorg", f"Expected org derived as 'testorg', got {detected['org']}"
    assert detected["node"] == "tronyx-vps", "node fallback must be tronyx-vps (default)"
    assert not detected["domain"]

    logger.info("[IMP:9][test_adopt_derives_org_from_path][assert] Org 'testorg' derived from path")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion TEST_test_adopt_derives_org_from_path


# NOTE: test_ghcr_path_lowercased and test_uses_preserves_exact_case removed —
# adopt-project.sh was reduced to a ~90 LOC Strangler-Fig facade (DevPlan 090 Wave 5c).
# simplify_deploy_yml logic moved to core/internal/scaffold/project_adopter.py (Python).
# Shell-level tests for this function are no longer applicable.


# ═════════════════════════════════════════════════════════════════════════════
# TEST 2: node.yaml context casing mismatch detected and adapted
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_context_mismatch_detected
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-07-17 · node.yaml context casing mismatch detected and adapted
# · Regression: if casing drift goes undetected, ghcr paths break on case-sensitive FS
# · Scenario: node.yaml context="tronyxlab" vs --org TronyxLab → WARN + node.yaml variant wins (E11 Python)
# · Last fail: N/A (updated for E11 Python detect_project_config)
# · Remove if: node.yaml context validation is removed
def test_context_mismatch_detected(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """detect_project_config: casing mismatch → node.yaml variant (canonical, lowercase).

    # ▶ node.yaml context='tronyxlab', --org TronyxLab → detect_project_config
    #   → ◇ validate_org_against_node_yaml (Python) → casing differs → org=tronyxlab → ⎋ pass
    """
    caplog.set_level(logging.DEBUG)

    proj_dir = tmp_path / "myproject"
    proj_dir.mkdir()

    # Create node.yaml with lowercase context at PROJECTS_BASE/TronyxLab/node-configs/tronyx-vps/
    node_yaml_dir = tmp_path / "TronyxLab" / "node-configs" / "tronyx-vps"
    node_yaml_dir.mkdir(parents=True)
    (node_yaml_dir / "node.yaml").write_text("contexts:\n  - name: tronyxlab\n")

    detected = detect_project_config(
        project_dir=proj_dir,
        org="TronyxLab",
        node="tronyx-vps",
    )

    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            imp_level = int(record.message.split("[IMP:")[1].split("]")[0])
            if imp_level >= 7:
                logger.info("%s", record.message)
            if imp_level >= 9:
                found_imp9 = True
    logger.info("--- END LDD TRAJECTORY ---")

    combined = "\n".join(r.message for r in caplog.records)
    assert "Casing mismatch" in combined, f"Expected 'Casing mismatch' WARN in logs:\n{combined[:1000]}"
    assert detected["org"] == "tronyxlab", f"Expected org=tronyxlab (node.yaml variant), got {detected['org']}"

    logger.info("[IMP:9][test_context_mismatch_detected][assert] Casing drift detected and adapted")
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion TEST_test_context_mismatch_detected


# ═════════════════════════════════════════════════════════════════════════════
# TEST 3 (R5 negative): org fail-fast when no --org / path / PLATFORM_ORG
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_detect_fails_without_org
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-08-02 · R5 NEGATIVE (E11) · detect_project_config — org fail-fast
# · Scenario: project dir at tmp root (no parent org dir), no --org, no PLATFORM_ORG → ConfigValidationError
# · Last fail: N/A — new R5 negative for E11 (replaces shell fail-fast "PROJECT_ORG is not set")
# · Remove if: org fail-fast is removed
def test_detect_fails_without_org(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """detect_project_config: no org anywhere → ConfigValidationError (fail-fast, TRAP B1).

    Trigger: project at filesystem root (parent.name == "") + no PLATFORM_ORG —
    path-derivation yields empty org → fail-fast (DI resolve_fn → root-level path).
    """
    caplog.set_level(logging.DEBUG)
    proj_dir = tmp_path / "solo-project"
    proj_dir.mkdir()

    # DI (167 D3): resolve_fn симулирует project на "/" (parent.name == "") —
    # path-derivation yields empty org; без глобального патча pathlib.Path.resolve
    with pytest.raises(ConfigValidationError, match="PROJECT_ORG is not set"):
        detect_project_config(project_dir=proj_dir, resolve_fn=lambda p: Path("/") / p.name)

    logger.info("[IMP:9][test_detect_fails_without_org][assert] Org fail-fast triggered (ConfigValidationError)")


# endregion TEST_test_detect_fails_without_org


# ═════════════════════════════════════════════════════════════════════════════
# TEST 4 (E11): ai-platform.yaml target_node/domain parsed via PyYAML (0 grep)
# ═════════════════════════════════════════════════════════════════════════════


# region TEST_test_detect_reads_ai_platform_yaml
@pytest.mark.contract
# 🧪 TRAP[TEST] · 2026-08-02 · E11 · detect_project_config — PyYAML auto-detect
# · Scenario: ai-platform.yaml with target_node/domain → node/domain resolved (no grep-YAML in shell)
# · Last fail: N/A — new E11 test (shell grep удалён)
# · Remove if: ai-platform.yaml auto-detection is removed
def test_detect_reads_ai_platform_yaml(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """detect_project_config: ai-platform.yaml target_node/domain parsed via PyYAML."""
    caplog.set_level(logging.DEBUG)
    proj_dir = tmp_path / "org" / "myproject"
    proj_dir.mkdir(parents=True)
    (proj_dir / "ai-platform.yaml").write_text("name: myproject\ntarget_node: prod-node\ndomain: app.example.com\n")

    detected = detect_project_config(project_dir=proj_dir)
    assert detected["node"] == "prod-node", f"node from ai-platform.yaml, got {detected['node']}"
    assert detected["domain"] == "app.example.com", f"domain from ai-platform.yaml, got {detected['domain']}"
    assert detected["org"] == "org"

    logger.info("[IMP:9][test_detect_reads_ai_platform_yaml][assert] PyYAML auto-detect works")


# endregion TEST_test_detect_reads_ai_platform_yaml
