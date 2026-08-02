#!/usr/bin/env python3
# GREP_SUMMARY: test-project-yaml ai-platform-yaml reader target_node domain derive-org-path detect casing node.yaml E11
# STRUCTURE: ┌tmp fixtures (ai-platform.yaml + node.yaml)┐ → ◇ read_project_yaml (0 grep) → ◇ derive_org_from_path → ◇ detect_project_config (full fallback chain + casing) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/project_yaml.py (DevPlan 118 E11 — общий читатель
##           ai-platform.yaml, кандидат из аудита монолитов). Native imports, tmp_path fixtures.
## @scope    Tests: read_project_yaml (target_node/domain, domain "false" → empty, missing file,
##           malformed yaml), derive_org_from_path, detect_project_config (node/domain/org fallback
##           chain, casing validation vs node.yaml, org fail-fast).
## @invariants
##   - Native imports only; tmp_path fixtures (zero hardcoded paths)
##   - R5 anti-survivorship: negative-тесты (org fail-fast, casing mismatch)
##   - LDD: IMP:9 on detect success, IMP:10 on fail-fast
## @rationale E11: shared/project_yaml.py — читатель ai-platform.yaml (0 grep). Тесты фиксируют
##           контракт detect (fallback-цепочки из adopt-project.sh).
## @changes  2026-08-02 | DevPlan 118 E11 — Created
# endregion MODULE_CONTRACT

import logging
from pathlib import Path

import pytest

from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.project_yaml import derive_org_from_path, detect_project_config, read_project_yaml

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate PROJECTS_ROOT/PLATFORM_ORG/PLATFORM_DEFAULT_NODE for casing/path tests."""
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_path))
    monkeypatch.delenv("PLATFORM_ORG", raising=False)
    monkeypatch.delenv("PLATFORM_DEFAULT_NODE", raising=False)


# region TEST_read_project_yaml
def test_read_project_yaml_parses(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_read_project_yaml_parses — DevPlan 118 E migration unit test
    """read_project_yaml: target_node/domain parsed via PyYAML (0 grep)."""
    proj = tmp_path / "org" / "app"
    proj.mkdir(parents=True)
    (proj / "ai-platform.yaml").write_text("name: app\ntarget_node: prod\nomain: x\ndomain: app.example.com\n")
    cfg = read_project_yaml(proj)
    assert cfg["target_node"] == "prod"
    assert cfg["domain"] == "app.example.com"


def test_read_project_yaml_domain_false_empty(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_read_project_yaml_domain_false_empty — DevPlan 118 E migration unit test
    """read_project_yaml: domain 'false' → empty (legacy adopt-project.sh semantics)."""
    proj = tmp_path / "app"
    proj.mkdir()
    (proj / "ai-platform.yaml").write_text("domain: false\n")
    assert read_project_yaml(proj)["domain"] == ""


def test_read_project_yaml_missing_file(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_read_project_yaml_missing_file — DevPlan 118 E migration unit test
    """read_project_yaml: no ai-platform.yaml → empty values."""
    proj = tmp_path / "app"
    proj.mkdir()
    assert read_project_yaml(proj) == {"target_node": "", "domain": ""}


def test_read_project_yaml_malformed(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_read_project_yaml_malformed — DevPlan 118 E migration unit test
    """read_project_yaml: malformed yaml → empty values (graceful, no crash)."""
    proj = tmp_path / "app"
    proj.mkdir()
    (proj / "ai-platform.yaml").write_text("not: [valid")
    assert read_project_yaml(proj) == {"target_node": "", "domain": ""}


# endregion


# region TEST_derive_org_from_path
def test_derive_org_from_path_basename_parent(tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_derive_org_from_path_basename_parent — DevPlan 118 E migration unit test
    """derive_org_from_path: basename(parent) — путь-производная org."""
    proj = tmp_path / "myorg" / "app"
    proj.mkdir(parents=True)
    assert derive_org_from_path(proj) == "myorg"


# endregion


# region TEST_detect_project_config
def test_detect_full_fallback_chain(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_detect_full_fallback_chain — DevPlan 118 E migration unit test
    """detect_project_config: ai-platform.yaml node/domain + org from path (0 grep)."""
    caplog.set_level(logging.INFO)
    proj = tmp_path / "myorg" / "app"
    proj.mkdir(parents=True)
    (proj / "ai-platform.yaml").write_text("target_node: prod-node\ndomain: app.example.com\n")

    detected = detect_project_config(proj)
    assert detected == {"name": "app", "org": "myorg", "node": "prod-node", "domain": "app.example.com"}

    found_imp9 = any("[IMP:9]" in r.message and "Detected" in r.message for r in caplog.records)
    assert found_imp9, "IMP:9 detect log expected"


def test_detect_explicit_args_override(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_detect_explicit_args_override — DevPlan 118 E migration unit test
    """detect_project_config: explicit args override yaml/path."""
    caplog.set_level(logging.INFO)
    proj = tmp_path / "org" / "app"
    proj.mkdir(parents=True)
    (proj / "ai-platform.yaml").write_text("target_node: yaml-node\n")

    detected = detect_project_config(proj, name="custom", org="explicit-org", node="cli-node", domain="cli.example.com")
    assert detected == {"name": "custom", "org": "explicit-org", "node": "cli-node", "domain": "cli.example.com"}


def test_detect_org_fail_fast(
    # 🧪 TRAP[TEST] · 2026-08-02 · test_detect_org_fail_fast — DevPlan 118 E migration unit test
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """detect_project_config: no org anywhere → ConfigValidationError (fail-fast, TRAP B1)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        "core.internal.shared.project_yaml.Path.resolve", lambda self, strict=False: Path("/") / self.name
    )
    proj = tmp_path / "solo"
    proj.mkdir()

    with pytest.raises(ConfigValidationError, match="PROJECT_ORG is not set"):
        detect_project_config(proj)


def test_detect_casing_validation_vs_node_yaml(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_detect_casing_validation_vs_node_yaml — DevPlan 118 E migration unit test
    """detect_project_config: node.yaml context casing mismatch → node.yaml variant (canonical)."""
    caplog.set_level(logging.INFO)
    proj = tmp_path / "app"
    proj.mkdir()
    node_dir = tmp_path / "TronyxLab" / "node-configs" / "tronyx-vps"
    node_dir.mkdir(parents=True)
    (node_dir / "node.yaml").write_text("contexts:\n  - name: tronyxlab\n")

    detected = detect_project_config(proj, org="TronyxLab", node="tronyx-vps")
    assert detected["org"] == "tronyxlab", "casing mismatch → node.yaml variant wins"

    combined = "\n".join(r.message for r in caplog.records)
    assert "Casing mismatch" in combined, "casing WARN expected"


# endregion
