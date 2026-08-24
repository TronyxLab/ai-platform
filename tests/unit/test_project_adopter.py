"""
# GREP_SUMMARY: test_project_adopter, adopt-project, generate-yaml, compose-validation, org-validation, deploy-simplify, register-vhost, makefile, agents
# STRUCTURE: ▶ 15 tests covering YAML gen → compose validation → org validation → deploy simplify → register → vhost → Makefile → AGENTS.md → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for project_adopter.py — all business logic methods of ProjectAdopter class
##           and validate_org_against_node_yaml() standalone function.
## @scope    Tests all 10 public methods + standalone validation function.
##           No subprocess calls for business logic — direct Python imports.
##           compose validation tests use static YAML fixtures (no Docker required).
##           vhost_renderer import tested via mock (D4 fallback).
##           register_in_node_yaml tested via mock (wrapping sys.exit per D3).
## @invariants
##   - All tests import the module directly via sys.path.insert
##   - Each test decorated with @ldd_trajectory and asserts IMP:9 log presence
##   - tmp_path used for all temporary files (no hardcoded paths)
##   - No Docker or external service dependency
##   - @pytest.mark.requires_docker NOT set (static tests only)
## @rationale DevPlan 036C §TEST_SPEC: 15 tests covering all acceptance criteria.
## @changes 2026-07-26 · Created (Wave 5c)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

# LDD trajectory decorator
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "scaffold"
sys.path.insert(0, str(_SCRIPT_DIR))

import project_adopter as pa

# ═══════════════════════════════════════════════════════════════════
# region Helpers
# ═══════════════════════════════════════════════════════════════════


def _make_adopter(tmp_path: Path, **kwargs: str | bool | None) -> pa.ProjectAdopter:
    """Create a ProjectAdopter instance with sensible defaults.

    ## @purpose  Factory helper for test ProjectAdopter creation.
    ## @io        ⇥ tmp_path, overrides → ⎋ ProjectAdopter
    """
    defaults: dict[str, str | bool | None] = {
        "project_dir": tmp_path / "test-project",
        "name": "test-project",
        "org": "testorg",
        "node": "tronyx-vps",
        "domain": None,
        "force": False,
    }
    defaults.update(kwargs)
    project_dir = Path(str(defaults.pop("project_dir")))
    project_dir.mkdir(parents=True, exist_ok=True)
    return pa.ProjectAdopter(
        project_dir=project_dir,
        name=str(defaults.pop("name")),
        org=str(defaults.pop("org")),
        node=str(defaults.pop("node")),
        domain=defaults.pop("domain"),  # type: ignore[arg-type]
        force=bool(defaults.pop("force")),
    )


def _make_node_yaml(tmp_path: Path, context: str = "testorg") -> Path:
    """Create a node.yaml with contexts[] canon (DevPlan 116 B6 T1 — 'context' field removed).

    ## @purpose  Create minimal node.yaml for org validation tests.
    ## @io        ⇥ tmp_path, context → ⎋ Path to node.yaml
    """
    node_yaml = tmp_path / "node-configs" / "tronyx-vps" / "node.yaml"
    node_yaml.parent.mkdir(parents=True, exist_ok=True)
    node_yaml.write_text(f"contexts:\n  - name: {context}\nprojects: []\n", encoding="utf-8")
    return node_yaml


# (removed B10 T7: PyYAML — hard dependency; the conditional-skip guard was unfalsifiable per R2)


# endregion Helpers


# ═══════════════════════════════════════════════════════════════════
# region Test 1-3: generate_minimal_ai_platform_yaml
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Generate ai-platform.yaml for backend without domain
# · Scenario: Empty project dir → generate minimal ai-platform.yaml (no domain) → needs.domain:false, expose:false
# · Last fail: N/A (new test)
# · Remove if: generate_minimal_ai_platform_yaml logic changes
@ldd_trajectory
def test_generate_minimal_yaml_no_domain(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Generate ai-platform.yaml for backend project without domain."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain=None)

    result = adopter.generate_minimal_ai_platform_yaml()

    assert result == "generated", "Should generate new yaml"
    assert adopter.yaml_file.exists(), "ai-platform.yaml should exist"

    import yaml

    with Path(adopter.yaml_file).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["name"] == "test-project"
    assert data["type"] == "backend"
    assert data["needs"]["domain"] is False
    assert data["needs"]["expose"] is False
    assert data["target_node"] == "tronyx-vps"

    # LDD trajectory verification
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
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# 🧪 TRAP[TEST] · Regression · Generate ai-platform.yaml with domain → expose:true
# · Scenario: Project with domain → needs.domain:true, expose:true, domain set
# · Last fail: N/A (new test)
# · Remove if: generate_minimal_ai_platform_yaml logic changes
@ldd_trajectory
def test_generate_minimal_yaml_with_domain(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Generate ai-platform.yaml with domain → expose:true."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain="example.com")

    result = adopter.generate_minimal_ai_platform_yaml()

    assert result == "generated"
    import yaml

    with Path(adopter.yaml_file).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert data["needs"]["domain"] is True
    assert data["needs"]["expose"] is True

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
    assert found_imp9


# 🧪 TRAP[TEST] · Regression · Auto-detect project type from directory contents
# · Scenario: frontend dir exists → type=frontend
# · Last fail: N/A (new test)
# · Remove if: type detection logic changes
@ldd_trajectory
def test_generate_minimal_yaml_type_detection(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Auto-detect project type: frontend, backend."""
    caplog.set_level(logging.INFO)

    # Test: frontend via src/index.html
    adopter = _make_adopter(tmp_path / "frontend-project", name="frontend-project")
    (adopter.project_dir / "src").mkdir(parents=True, exist_ok=True)
    (adopter.project_dir / "src" / "index.html").write_text("<html></html>", encoding="utf-8")
    adopter.generate_minimal_ai_platform_yaml()
    import yaml

    with Path(adopter.yaml_file).open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["type"] == "frontend", f"Expected frontend, got {data['type']}"

    # Test: backend (default) — no frontend markers
    adopter3 = _make_adopter(tmp_path / "backend-project", name="backend-project")
    adopter3.generate_minimal_ai_platform_yaml()
    with Path(adopter3.yaml_file).open(encoding="utf-8") as f:
        data3 = yaml.safe_load(f)
    assert data3["type"] == "backend", f"Expected backend, got {data3['type']}"

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
    assert found_imp9


# endregion Test 1-3: generate_minimal_ai_platform_yaml


# ═══════════════════════════════════════════════════════════════════
# region Test 4-7: validate_compose_networks
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Compose with proxy-net external + 1 service connected → PASS
# · Scenario: compose.yaml has proxy-net external:true and 1 service connected → validate_compose_networks returns valid
# · Last fail: N/A (new test)
# · Remove if: validate_compose_networks logic changes
@ldd_trajectory
def test_validate_compose_networks_has_proxy(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Compose with proxy-net external + 1 service connected → PASS."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain="example.com")

    compose_content = """
services:
  web:
    image: nginx:alpine
    networks:
      proxy-net:
        aliases:
          - web

networks:
  proxy-net:
    name: proxy-net
    external: true
"""
    compose_file = adopter.project_dir / "compose.yaml"
    compose_file.write_text(compose_content, encoding="utf-8")

    result = adopter.validate_compose_networks(compose_file)
    assert result.valid is True
    assert "1 service(s)" in result.message

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
    assert found_imp9


# 🧪 TRAP[TEST] · Regression · Compose without proxy-net external → FAIL with instructions
# · Scenario: compose.yaml has proxy-net but external:false → validation fails
# · Last fail: N/A (new test)
# · Remove if: validate_compose_networks logic changes
@ldd_trajectory
def test_validate_compose_networks_no_proxy(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Compose without proxy-net external → FAIL."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain="example.com")

    compose_content = """
services:
  web:
    image: nginx:alpine
    networks:
      - default

networks:
  default:
    driver: bridge
"""
    compose_file = adopter.project_dir / "compose.yaml"
    compose_file.write_text(compose_content, encoding="utf-8")

    result = adopter.validate_compose_networks(compose_file)
    assert result.valid is False
    assert "proxy-net" in result.message or "external" in result.message

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
    assert found_imp9


# 🧪 TRAP[TEST] · Regression · Compose with proxy-net but 0 services connected → FAIL
# · Scenario: compose has proxy-net external but no service connected → validation fails
# · Note: Need to disable docker command for this test because `docker compose config`
#   drops external networks not referenced by services. PyYAML path preserves all networks.
# · Last fail: N/A (new test)
# · Remove if: validate_compose_networks logic changes
@ldd_trajectory
def test_validate_compose_networks_no_services(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Compose with proxy-net external, but 0 services connected → FAIL."""
    # DI (167 D3): which-fn (docker-отсутствие) → PyYAML path (docker compose config
    # drops unused external networks) — без глобального патча shutil.which
    import shutil as _shutil

    def _no_docker_which(cmd: str, *args: object, **kwargs: object) -> str | None:
        if cmd == "docker":
            return None
        return _shutil.which(cmd, *args, **kwargs)

    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain="example.com")

    compose_content = """
services:
  web:
    image: nginx:alpine
    networks:
      - default

networks:
  proxy-net:
    name: proxy-net
    external: true
  default:
    driver: bridge
"""
    compose_file = adopter.project_dir / "compose.yaml"
    compose_file.write_text(compose_content, encoding="utf-8")

    result = adopter.validate_compose_networks(compose_file, which_fn=_no_docker_which)
    assert result.valid is False
    assert "no service" in result.message.lower()

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
    assert found_imp9


# 🧪 TRAP[TEST] · Regression · No domain → skip proxy-net validation
# · Scenario: ProjectAdopter without domain → validate_compose_networks returns valid=True (skip)
# · Last fail: N/A (new test)
# · Remove if: domain-aware skip logic changes
@ldd_trajectory
def test_validate_compose_networks_no_domain_skip(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """No domain configured → skip proxy-net validation."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain=None)

    compose_file = adopter.project_dir / "compose.yaml"
    compose_file.write_text("services: {}", encoding="utf-8")

    result = adopter.validate_compose_networks(compose_file)
    assert result.valid is True
    assert "skip" in result.message.lower() or "No domain" in result.message

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
    assert found_imp9


# endregion Test 4-7: validate_compose_networks


# ═══════════════════════════════════════════════════════════════════
# region Test 8-9: validate_org_against_node_yaml
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Org mismatch → ValueError
# · Scenario: org="wrongorg" vs node.yaml context="testorg" → raise ValueError
# · Last fail: N/A (new test)
# · Remove if: validate_org_against_node_yaml logic changes
@ldd_trajectory
def test_validate_org_mismatch(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Org does not match node.yaml context → raise ValueError."""
    caplog.set_level(logging.INFO)
    node_yaml = _make_node_yaml(tmp_path, context="testorg")

    from core.internal.shared.exceptions import ConfigValidationError

    with pytest.raises(ConfigValidationError, match="does not match"):
        pa.validate_org_against_node_yaml("wrongorg", node_yaml)

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
    assert found_imp9


# 🧪 TRAP[TEST] · Regression · Casing mismatch → returns node.yaml variant
# · Scenario: org="TestOrg" vs node.yaml context="testorg" → returns "testorg" (node.yaml casing)
# · Last fail: N/A (new test)
# · Remove if: org normalization logic changes
@ldd_trajectory
def test_validate_org_casing_mismatch(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Org casing differs from node.yaml → returns node.yaml variant."""
    caplog.set_level(logging.INFO)
    node_yaml = _make_node_yaml(tmp_path, context="TestOrg")

    result = pa.validate_org_against_node_yaml("testorg", node_yaml)
    assert result == "TestOrg", "Should return node.yaml casing"

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
    assert found_imp9


# endregion Test 8-9: validate_org_against_node_yaml


# ═══════════════════════════════════════════════════════════════════
# region Test 10-11: simplify_deploy_yml
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Simplify deploy.yml with reusable workflow
# · Scenario: Old deploy.yml → simplify → becomes reusable workflow pattern
# · Last fail: N/A (new test)
# · Remove if: simplify_deploy_yml logic changes
@ldd_trajectory
def test_simplify_deploy_yml(caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simplify deploy.yml with reusable workflow."""
    caplog.set_level(logging.INFO)

    # Force-mode to skip interactive prompt
    adopter = _make_adopter(tmp_path, domain="example.com", force=True)
    (adopter.project_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)

    # Create old-style deploy.yml
    old_deploy = adopter.deploy_yml
    old_deploy.write_text(
        "name: Deploy\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo old\n",
        encoding="utf-8",
    )

    result = adopter.simplify_deploy_yml()

    assert result is True, "deploy.yml should be simplified"
    assert old_deploy.exists(), "deploy.yml should still exist"
    # Check it has the reusable workflow pattern
    content = old_deploy.read_text(encoding="utf-8")
    assert "deploy-project.yml@main" in content, "Should use reusable workflow"
    assert "ghcr.io" in content, "Should have image registry"
    assert old_deploy.with_suffix(".yml.bak").exists(), "Backup should exist"

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
    assert found_imp9


# 🧪 TRAP[TEST] · 2026-08-24 · NEGATIVE (R5) · adopter не генерирует input image_tag (REF-0001, FAIL-0802)
# · Scenario: reusable deploy-project.yml НЕ имеет input image_tag → сгенерированный вызов
#   с `image_tag:` детерминированно красил CI adopted-проекта («Unexpected inputs»)
# · Last fail: project_adopter.py:230/239 — image_tag в обоих deploy-job'ах генерации
# · Remove if: deploy-project.yml объявит легитимный input image_tag
def test_simplify_deploy_yml_no_image_tag_input(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """R5 negative: генерация adopter'а БЕЗ несуществующего input image_tag."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain="example.com", force=True)
    (adopter.project_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (adopter.deploy_yml).write_text(
        "name: Deploy\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n", encoding="utf-8"
    )

    result = adopter.simplify_deploy_yml()

    assert result is True, "deploy.yml should be simplified"
    content = adopter.deploy_yml.read_text(encoding="utf-8")
    logger.info("[IMP:8][test][adopt] generated deploy.yml scanned for image_tag")
    assert "image_tag" not in content, (
        "REF-0001/FAIL-0802: генератор не должен передавать несуществующий input image_tag "
        "(образ доставляется push-каналом build-and-push, receive получает тег из github.sha)"
    )
    # Оба deploy-job'а продолжают зависеть от build-and-push (порядок канала сборки→деплой)
    assert content.count("needs: [build-and-push]") == 2, (
        "Оба deploy-job'а (main/staging) должны иметь needs: [build-and-push]"
    )
    logger.info("[IMP:9][test][adopt] PASS: image_tag отсутствует, needs=[build-and-push] ×2")


# 🧪 TRAP[TEST] · Regression · deploy.yml already simplified → skip
# · Scenario: deploy.yml already uses reusable workflow → simplify returns False (no-op)
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_simplify_deploy_yml_already_simplified(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """deploy.yml already uses reusable workflow → skip."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain="example.com")
    (adopter.project_dir / ".github" / "workflows").mkdir(parents=True, exist_ok=True)

    # Already simplified deploy.yml
    simplified = """name: Deploy
on: [push]
jobs:
  deploy:
    uses: testorg/ai-platform/.github/workflows/deploy-project.yml@main
"""
    adopter.deploy_yml.write_text(simplified, encoding="utf-8")

    result = adopter.simplify_deploy_yml()

    assert result is False, "Should skip already simplified deploy.yml"

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
    assert found_imp9


# endregion Test 10-11: simplify_deploy_yml


# ═══════════════════════════════════════════════════════════════════
# region Test 12: register_in_node_yaml (mocked system_exit)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Register new project via scaffold_helpers delegation
# · Scenario: Mock scaffold_helpers.register_in_node_yaml → register_in_node_yaml delegates (B9 T5, CS-5)
# · Last fail: N/A (test updated after _register_project_safe removal, B9 T5)
# · Remove if: register_in_node_yaml delegation logic changes
@ldd_trajectory
def test_register_in_node_yaml_new(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Register new project via scaffold_helpers.register_in_node_yaml delegation."""
    caplog.set_level(logging.INFO)

    adopter = _make_adopter(tmp_path, domain="example.com")
    node_yaml = tmp_path / "node-configs" / "tronyx-vps" / "node.yaml"
    node_yaml.parent.mkdir(parents=True, exist_ok=True)
    node_yaml.write_text("context: testorg\nprojects: []\n", encoding="utf-8")

    # Track calls to the register_fn (B9 T5: deprecated _register_project_safe removed — CS-5)
    called: list[dict[str, str]] = []

    def mock_register(**kwargs: str) -> bool:
        called.append(kwargs)
        logger.info("[IMP:9][test][register] scaffold_helpers.register_in_node_yaml called: %s", kwargs.get("name"))
        return True

    result = adopter.register_in_node_yaml(node_yaml, register_fn=mock_register)

    assert result is True, "register_in_node_yaml should delegate successfully"
    assert len(called) == 1, "scaffold_helpers.register_in_node_yaml should have been called once"
    assert called[0]["name"] == "test-project"
    assert called[0]["org"] == "testorg"
    assert called[0]["ptype"] == "adopted"

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
    assert found_imp9


# endregion Test 12: register_in_node_yaml (mocked system_exit)


# ═══════════════════════════════════════════════════════════════════
# region Test 13: configure_vhost (mocked)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · configure_vhost with mocked vhost_renderer
# · Scenario: Mock vhost_renderer import → configure_vhost uses Python API (D4 primary path)
# · Last fail: N/A (new test)
# · Remove if: configure_vhost import logic changes
@ldd_trajectory
def test_configure_vhost_mocked(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Configure vhost with mocked vhost_renderer (D4 primary path)."""
    caplog.set_level(logging.INFO)

    adopter = _make_adopter(tmp_path, domain="example.com")

    # Create ai-platform.yaml for vhost update (PyYAML hard dep — B10 T7)
    import yaml

    with Path(adopter.yaml_file).open("w", encoding="utf-8") as f:
        yaml.dump({"name": "test-project", "needs": {"domain": False, "expose": False}}, f)

    # Mock vhost_renderer module
    import types

    mock_renderer = types.ModuleType("vhost_renderer")

    def mock_configure(project_dir: Path, domain: str, node_configs_dir: Path | None = None) -> bool:
        logger.info("[IMP:9][test][vhost] Mock configure_vhost_for_project called: domain=%s", domain)
        return True

    mock_renderer.configure_vhost_for_project = mock_configure

    # We need to inject into sys.modules before the import happens in configure_vhost
    # Since configure_vhost has a try/except ImportError, we mock by setting the import to succeed
    monkeypatch.setitem(sys.modules, "core.internal.scaffold.vhost_renderer", mock_renderer)

    # D4 fallback also needs add-vhost.sh to not exist (or be mocked)
    # The mock_renderer module will be found first → uses Python API path
    result = adopter.configure_vhost(node_configs_dir=tmp_path / "node-configs")

    # When vhost_renderer is mocked and returns True, configure_vhost should return True
    assert result is True, "configure_vhost should succeed with mock"

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
    assert found_imp9


# endregion Test 13: configure_vhost (mocked)


# ═══════════════════════════════════════════════════════════════════
# region Test 14: gen_project_makefile
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Generate project Makefile with correct targets
# · Scenario: Generate Makefile → verify sync-env, status, help targets
# · Last fail: N/A (new test)
# · Remove if: gen_project_makefile logic changes
@ldd_trajectory
def test_generate_makefile(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Generate project Makefile with correct targets."""
    caplog.set_level(logging.INFO)

    adopter = _make_adopter(tmp_path, domain="example.com")

    result = adopter.gen_project_makefile()

    assert result == "generated"
    makefile = adopter.project_dir / "Makefile"
    assert makefile.exists()
    content = makefile.read_text(encoding="utf-8")

    # Verify key components
    assert "# GENERATED by ai-platform" in content
    assert "Project: test-project" in content
    assert "sync-env:" in content
    assert "status:" in content
    assert "help:" in content
    assert "project-sync-env" in content
    assert "project-status" in content

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
    assert found_imp9


# endregion Test 14: gen_project_makefile


# ═══════════════════════════════════════════════════════════════════
# region Test 15: gen_project_agents
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Generate project AGENTS.md with correct fields
# · Scenario: Generate AGENTS.md → verify org, node, domain, platform-provides sections
# · Last fail: N/A (new test)
# · Remove if: gen_project_agents logic changes
@ldd_trajectory
def test_generate_agents(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Generate project AGENTS.md with correct fields."""
    caplog.set_level(logging.INFO)

    adopter = _make_adopter(tmp_path, domain="example.com", org="TestOrg")

    result = adopter.gen_project_agents()

    assert result == "generated"
    agents_file = adopter.project_dir / "AGENTS.md"
    assert agents_file.exists()
    content = agents_file.read_text(encoding="utf-8")

    # Verify key components
    assert "# AGENTS.md — test-project" in content
    assert "org: TestOrg" in content
    assert "node: tronyx-vps" in content
    assert "Domain: example.com" in content
    assert "make sync-env" in content
    assert "make status" in content
    # Without domain
    assert "Template-based services: postgres" in content

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
    assert found_imp9


# endregion Test 15: gen_project_agents


# ═══════════════════════════════════════════════════════════════════
# region Test 16 (bonus): existing yaml → skip
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Existing ai-platform.yaml → returns "exists" (no-op)
# · Scenario: ai-platform.yaml already exists → generate_minimal returns "exists"
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_generate_minimal_yaml_exists(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """Existing ai-platform.yaml → returns 'exists' (idempotent)."""
    caplog.set_level(logging.INFO)
    adopter = _make_adopter(tmp_path, domain="example.com")

    # Create the yaml file first
    adopter.yaml_file.write_text("name: existing-project\n", encoding="utf-8")

    result = adopter.generate_minimal_ai_platform_yaml()

    assert result == "exists", "Should skip existing yaml"
    # Verify content unchanged
    content = adopter.yaml_file.read_text(encoding="utf-8")
    assert "name: existing-project" in content

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
    assert found_imp9


# endregion Test 16 (bonus): existing yaml → skip


# ═══════════════════════════════════════════════════════════════════
# region Test 17: _resolve_node_yaml_path via canonical NodeYaml.resolve (DevPlan 116 B6 T8.1)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _resolve_node_yaml_path finds PROJECTS_BASE/org/node-configs/node/node.yaml
# · Scenario: PROJECTS_BASE env → {root}/testorg/node-configs/tronyx-vps/node.yaml создан → resolve находит его
# · Last fail: N/A (DevPlan 116 B6 T8.1 — ручные эвристики → NodeYaml.resolve)
# · Remove if: adopter node.yaml resolution changes
@ldd_trajectory
def test_resolve_node_yaml_path_via_nodeyaml_resolve(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_resolve_node_yaml_path: canonical resolve Path 1 = PROJECTS_BASE/org/node-configs/<node>/node.yaml."""
    caplog.set_level(logging.INFO)

    # Set PROJECTS_BASE to an isolated tmp dir (Zero Hardcode Rule)
    projects_root = tmp_path / "projects-root"
    projects_root.mkdir()
    monkeypatch.setenv("PROJECTS_BASE", str(projects_root))

    adopter = _make_adopter(tmp_path, domain="example.com")  # org=testorg, node=tronyx-vps
    node_yaml = projects_root / "testorg" / "node-configs" / "tronyx-vps" / "node.yaml"
    node_yaml.parent.mkdir(parents=True)
    node_yaml.write_text("contexts:\n  - name: testorg\nnode:\n  name: tronyx-vps\n  host: 1.2.3.4\n", encoding="utf-8")

    resolved = adopter._resolve_node_yaml_path()
    assert resolved is not None, "resolve must find the node.yaml under PROJECTS_BASE/org/node-configs/<node>/"
    assert Path(resolved) == node_yaml, f"expected {node_yaml}, got {resolved}"

    logger.critical("[IMP:9][test] _resolve_node_yaml_path via NodeYaml.resolve → %s — OK", resolved)


# endregion Test 17: _resolve_node_yaml_path via canonical NodeYaml.resolve (DevPlan 116 B6 T8.1)
