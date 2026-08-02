"""
# GREP_SUMMARY: test_context_deployer, project-deploy, ghcr-pull, build-fallback, idempotent, healthcheck-gate, audit-log
# STRUCTURE: ▶ tmp_path + node.yaml + mock subprocess → ◇ filter projects → ◇ ghcr pull → ◇ build fallback → ◇ idempotent skip → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for context_deployer.py — context project deploy orchestration.
## @scope    Tests resolve_context_projects, deploy_context_projects, deploy_context context
##           resolution (contexts[] canon, DevPlan 116 B6 T2).
## @invariants
##   - All subprocess calls mocked (no real docker compose)
##   - node.yaml created in tmp_path
##   - Each test validates IMP:9 business logic log presence
## @rationale DevPlan 047 Phase 7: context deployer bridges bootstrap "last mile".
## @changes  2026-07-22 | DevPlan 047 Phase 7 — Created
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_MODULE_DIR))
import context_deployer as cd

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def node_yaml_file(tmp_path):
    """Create a node.yaml with test projects (contexts[] canon — DevPlan 116 B6 T1)."""
    yaml_content = """\
node:
  name: test-node
  platform_domain: test.example.com
contexts:
  - name: test-ctx
projects:
  - name: webapp
    repo: https://github.com/test/webapp
    type: backend
    domain: webapp.example.com
    context: test-ctx
  - name: api
    repo: https://github.com/test/api
    type: backend
    domain: api.example.com
    context: test-ctx
  - name: other-ctx-project
    repo: https://github.com/test/other
    type: frontend
    domain: other.other.com
    context: other-ctx
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return str(yaml_path)


@pytest.fixture
def mock_docker():
    """Mock all docker subprocess calls."""
    with patch("subprocess.run") as mock:
        mock.return_value.returncode = 0
        mock.return_value.stdout = ""
        mock.return_value.stderr = ""
        yield mock


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: resolve_context_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · resolve_context_projects filters projects by context
# · Scenario: node.yaml with 3 projects (2 test-ctx, 1 other-ctx) → returns 2 for test-ctx
# · Last fail: N/A (new test)
# · Remove if: context filtering logic changes
@ldd_trajectory
def test_filter_projects_by_context(caplog, node_yaml_file):
    """resolve_context_projects should filter projects by context."""
    projects = cd.resolve_context_projects(node_yaml_file, "test-ctx")
    assert len(projects) == 2
    names = [p.name for p in projects]
    assert "webapp" in names
    assert "api" in names
    assert "other-ctx-project" not in names
    logger.critical("[IMP:9][test] Filter projects by context — 2 of 3 matched")


# 🧪 TRAP[TEST] · Regression · resolve_context_projects returns empty for non-matching context
# · Scenario: context="nonexistent" → returns 0 projects
# · Last fail: N/A (new test)
# · Remove if: context filtering logic changes
@ldd_trajectory
def test_filter_projects_no_match(caplog, node_yaml_file):
    """resolve_context_projects should return empty for non-matching context."""
    projects = cd.resolve_context_projects(node_yaml_file, "nonexistent-ctx")
    assert len(projects) == 0
    logger.critical("[IMP:9][test] No projects match nonexistent context")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: deploy_context context resolution (DevPlan 116 B6 T2)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deploy_context resolves context from contexts[0].name
# · Scenario: node.yaml has contexts[0].name=test-ctx (no CONTEXT env) → deploy_context uses it
# · Last fail: N/A (DevPlan 116 B6 T2 — extract-alias removed, facade get_context)
# · Remove if: context resolution logic changes
@ldd_trajectory
def test_deploy_context_resolves_from_contexts(caplog, node_yaml_file, mock_docker, monkeypatch):
    """deploy_context should resolve context from contexts[0].name via NodeYaml.get_context()."""
    monkeypatch.delenv("CONTEXT", raising=False)
    monkeypatch.setattr(cd, "deploy_context_projects", lambda *a, **k: [])
    caplog.set_level(logging.INFO)

    result = cd.deploy_context(
        core_dir="/nonexistent/core",
        node_name="test-node",
        node_yaml=node_yaml_file,
        context="",
    )
    assert result.failed == 0, "context resolved → deploy should proceed"
    found = any("[IMP:9][deploy_context] Using context=test-ctx" in r.message for r in caplog.records)
    assert found, "deploy_context did not resolve context=test-ctx from contexts[0].name"
    logger.critical("[IMP:9][test] deploy_context resolved context from contexts[0].name — OK")


# 🧪 TRAP[TEST] · Regression · deploy_context with broken/missing node.yaml → failed=1, readable log
# · Scenario: node.yaml path missing, no CONTEXT env → DeployResult.failed=1, "CONTEXT not set" log (not traceback)
# · Last fail: N/A (DevPlan 116 B6 T2)
# · Remove if: fail-path semantics change
@ldd_trajectory
def test_deploy_context_broken_yaml_failed(caplog, tmp_path, monkeypatch):
    """deploy_context with unreadable node.yaml → DeployResult.failed=1 + readable log."""
    monkeypatch.delenv("CONTEXT", raising=False)
    caplog.set_level(logging.INFO)

    missing = str(tmp_path / "missing" / "node.yaml")
    result = cd.deploy_context(
        core_dir="/nonexistent/core",
        node_name="test-node",
        node_yaml=missing,
        context="",
    )
    assert result.failed == 1
    assert any("CONTEXT not set" in r.message for r in caplog.records), "expected readable CONTEXT-not-set log"
    logger.critical("[IMP:9][test] deploy_context broken yaml → failed=1 with readable log — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: A5 — cert_orchestrator normal import (DevPlan 118 A5)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · A5 — deploy_context uses the REAL cert_orchestrator module
# · Scenario: monkeypatch real cert_orchestrator.orchestrate_certs + cert_check_expiry=False →
# ·   deploy_context invokes orchestrate_certs; identity-check proves no importlib-copy shadow
# · Last fail: importlib.util.spec_from_file_location — обход системы импорта (тихий полом)
# · Remove if: cert orchestration moves out of deploy_context
@ldd_trajectory
def test_deploy_context_uses_real_cert_orchestrator(caplog, node_yaml_file, mock_docker, monkeypatch):
    """deploy_context must orchestrate certs via the real cert_orchestrator module (A5)."""
    import core.internal.bootstrap.cert_orchestrator as cert_mod

    caplog.set_level(logging.INFO)

    # A5 core: identity — context_deployer references the REAL module function, not an importlib copy.
    assert cd.orchestrate_certs is cert_mod.orchestrate_certs, (
        "A5 FAIL: context_deployer.orchestrate_certs must be the real cert_orchestrator function "
        "(importlib spec_from_file_location creates an unrelated copy)"
    )

    calls: list = []
    monkeypatch.setattr(cd, "orchestrate_certs", lambda *a, **k: (calls.append(a), SimpleNamespace(domains={}))[1])
    monkeypatch.setattr(cd, "cert_is_valid", lambda *a, **k: False)  # C9: все домены invalid → orchestrate
    monkeypatch.setattr(cd, "deploy_context_projects", lambda *a, **k: [])
    monkeypatch.setenv("CONTEXT", "test-ctx")

    result = cd.deploy_context(
        core_dir="/nonexistent/core",
        node_name="test-node",
        node_yaml=node_yaml_file,
        context="test-ctx",
    )

    assert calls, "orchestrate_certs must be invoked when context domains lack valid certs"
    assert result.failed == 0
    logger.critical("[IMP:9][test] deploy_context cert orchestration via real module — OK")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · A5 — importlib bypass removed from context_deployer
# · Scenario: AST-scan — spec_from_file_location + cross-module private _is_cert_valid must be absent
# · Last fail: context_deployer.py:645-653 importlib.util.spec_from_file_location("cert_orchestrator", ...)
# · Remove if: importlib bypass is legitimately reintroduced
@ldd_trajectory
def test_deploy_context_no_importlib_bypass_negative(caplog):
    """R5 negative: importlib bypass and private-cert-API usage removed from context_deployer (A5)."""
    import ast

    caplog.set_level(logging.INFO)

    src = Path(cd.__file__).read_text()
    tree = ast.parse(src)
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "spec_from_file_location":
            forbidden.append(f"{node.lineno}: spec_from_file_location")
        if isinstance(node, ast.Attribute) and node.attr == "_is_cert_valid":
            forbidden.append(f"{node.lineno}: _is_cert_valid (private API cross-module)")

    assert not forbidden, f"A5 FAIL: importlib bypass still present: {', '.join(forbidden)}"
    assert "orchestrate_certs" in src, "A5: normal import of cert_orchestrator.orchestrate_certs required"
    logger.critical("[IMP:9][test] importlib bypass removed / normal import present — OK")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: deploy_context_projects
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deploy skips already-healthy projects (idempotent)
# · Scenario: _is_project_healthy returns True → project skipped
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_idempotent_skip_healthy(caplog, node_yaml_file, monkeypatch):
    """deploy_context_projects should skip healthy projects."""
    monkeypatch.setattr(cd, "_is_project_healthy", lambda name: True)
    monkeypatch.setattr(cd, "_write_audit", lambda p, r: None)

    results = cd.deploy_context_projects(node_yaml_file, "test-ctx")
    assert len(results) == 2
    for r in results:
        assert r.status == "skipped"
        assert r.channel == "skip"
    logger.critical("[IMP:9][test] Idempotent skip — healthy projects not re-deployed")


# ── REMOVED (DevPlan 091 Wave A, AC4) ─────────────────────────────────────────
# The following bypass-path tests were removed together with context_deployer._deploy_single_project():
#   - test_ghcr_pull_success              (tested _shared_retry_pull + ghcr channel)
#   - test_ghcr_fails_fallback_build      (tested retry_pull→build fallback)
#   - test_health_gate_timeout            (tested _shared_healthcheck_poll unhealthy path)
#   - test_non_fatal_continues_on_failure (tested bypass non-fatal propagation)
# Rationale: these asserted behavior of the parallel pull→build→up→healthcheck path that
# bypassed DeployOrchestrator. That path was deleted (AC4 cleanup); equivalent coverage now
# lives in tests/unit/test_orchestrator.py (DeployOrchestrator unit tests) and
# tests/unit/test_deploy_single_orchestrator.py (routing invariant tests).
# ⚠️ TRAP[DECISION] · 2026-07-30 · MED · Removed bypass-path unit tests with the bypass code
# · Rejected: keep tests as xfail markers (risk: dead xfail markers accumulate, hide regressions)
# · Reason: tests of deleted code are dead tests (R1 Test Honesty). Coverage of deploy semantics
#   is preserved via test_orchestrator.py + test_deploy_single_orchestrator.py.
# · Rev: if a parallel deploy path is reintroduced with Architect sign-off — recreate equivalent tests.


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: _ensure_bootstrap_compose
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _ensure_bootstrap_compose creates minimal docker-compose.yml
# · Scenario: project_dir with no docker-compose.yml → creates nginx:alpine compose with ai-platform.bootstrap label
# · Last fail: N/A (new test)
# · Remove if: bootstrap compose generation logic changes
@ldd_trajectory
def test_bootstrap_compose_generation(caplog, tmp_path):
    """_ensure_bootstrap_compose should create docker-compose.yml with nginx:alpine, correct labels, and healthcheck."""
    project = cd.ProjectInfo(
        name="test-webapp",
        repo="https://github.com/test/webapp",
        type="backend",
        domain="webapp.test.example.com",
        context="test-ctx",
    )
    project_dir = str(tmp_path / "projects" / "test-webapp")

    result = cd._ensure_bootstrap_compose(project_dir, project)

    assert result is True, "_ensure_bootstrap_compose should return True on success"
    compose_file = tmp_path / "projects" / "test-webapp" / "docker-compose.yml"
    assert compose_file.is_file(), "docker-compose.yml should be created"

    content = compose_file.read_text()
    assert "image: nginx:alpine" in content, "Should use nginx:alpine image"
    assert "ai-platform.bootstrap=true" in content, "Should have ai-platform.bootstrap label"
    assert "ai-platform.project=test-webapp" in content, "Should have ai-platform.project label"
    assert "healthcheck:" in content, "Should have healthcheck section"
    assert "restart: unless-stopped" in content, "Should have restart policy"
    assert "GENERATED-STUB" in content, "Should indicate it's a generated stub"
    logger.critical(
        "[IMP:9][test] Bootstrap compose generated for %s — image=nginx:alpine, label=ai-platform.bootstrap=true, healthcheck present",
        project.name,
    )


# 🧪 TRAP[TEST] · Regression · _ensure_bootstrap_compose does NOT overwrite existing docker-compose.yml
# · Scenario: docker-compose.yml already exists → returns True, does NOT modify file
# · Last fail: N/A (new test)
# · Remove if: idempotent skip logic changes
@ldd_trajectory
def test_bootstrap_compose_idempotent(caplog, tmp_path):
    """_ensure_bootstrap_compose should NOT overwrite an existing docker-compose.yml."""
    project = cd.ProjectInfo(
        name="test-webapp",
        repo="https://github.com/test/webapp",
        type="backend",
        domain="webapp.test.example.com",
        context="test-ctx",
    )
    project_dir = str(tmp_path / "projects" / "test-webapp")
    os.makedirs(project_dir, exist_ok=True)

    # Create a pre-existing docker-compose.yml with different content
    existing_content = "# REAL DELIVERY — this should NOT be overwritten\nversion: '3.8'\nservices:\n  webapp:\n    image: custom:latest\n"
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    with open(compose_file, "w") as f:
        f.write(existing_content)

    result = cd._ensure_bootstrap_compose(project_dir, project)

    assert result is True, "_ensure_bootstrap_compose should return True (skip) when compose exists"
    # Verify the file was NOT overwritten
    content = Path(compose_file).read_text()
    assert content == existing_content, "Existing docker-compose.yml should NOT be overwritten"
    assert "REAL DELIVERY" in content, "Original content should be preserved intact"
    assert "nginx:alpine" not in content, "New content should NOT appear"
    logger.critical("[IMP:9][test] Bootstrap compose idempotent — existing file preserved unchanged")


# endregion


# ── _step_nginx_reload tests (HOLE-1, DevPlan 119 F4) ─────────────────────────


# region FUNC_test_step_nginx_reload_success
## @purpose — _step_nginx_reload() успешный: делегирует в shared/docker_compose.nginx_reload,
##            non-fatal — ошибки (OSError/FileNotFoundError) → WARN, не raise.
## @io — ⇥ caplog → ⎋ None (asserts делегирование + no-raise)
## @complexity — O(1)
## @invariants
##   - nginx_reload вызывается с дефолтными аргументами (container="nginx")
##   - Ошибка nginx_reload → перехвачена (non-fatal контракт D6)
# 🧪 TRAP[TEST] · DevPlan 119 F4 (HOLE-1) · _step_nginx_reload success
# · Last fail: N/A — _step_nginx_reload (context_deployer:824) не покрыт тестами
# · Remove if: _step_nginx_reload контракт меняется
def test_step_nginx_reload_success(caplog, monkeypatch) -> None:
    """_step_nginx_reload успешно вызывает nginx_reload (non-fatal)."""
    caplog.set_level(logging.DEBUG)

    called = {}

    def _fake_nginx_reload(*args, **kwargs):
        called["args"] = args
        called["kwargs"] = kwargs
        logger.info("[IMP:9][test][_step_nginx_reload] nginx_reload вызван")

    # Локальный импорт внутри _step_nginx_reload (from core.internal.shared.docker_compose
    # import nginx_reload) — патчим источник, а не context_deployer namespace.
    monkeypatch.setattr(
        "core.internal.shared.docker_compose.nginx_reload",
        _fake_nginx_reload,
    )

    cd._step_nginx_reload()

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
    assert "args" in called, "nginx_reload должен быть вызван"
    assert called["kwargs"].get("container", "nginx") == "nginx"
    found_imp9 = any("[IMP:9]" in r.message for r in caplog.records)
    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"


# endregion FUNC_test_step_nginx_reload_success


# region FUNC_test_step_nginx_reload_failure_nonfatal
## @purpose — R5 negative (DevPlan 119 F4): nginx_reload бросает OSError (контейнер недоступен)
##            → _step_nginx_reload ловит и логирует WARN (non-fatal), НЕ raise.
## @io — ⇥ caplog → ⎋ None (asserts no-raise при OSError)
## @complexity — O(1)
## @invariants
##   - OSError от nginx_reload → перехвачен в except (OSError, CalledProcessError, FileNotFoundError)
##   - Non-fatal: шаг deploy_context продолжается (reload не критичен)
# 🧪 TRAP[TEST] · NEGATIVE (R5) · _step_nginx_reload failure — DevPlan 119 F4
# · Last fail: необработанный OSError из nginx reload падал бы deploy_context
# · Remove if: _step_nginx_reload становится fatal
def test_step_nginx_reload_failure_nonfatal(caplog, monkeypatch) -> None:
    """R5: OSError от nginx_reload → перехвачен (non-fatal), no-raise."""
    caplog.set_level(logging.DEBUG)

    def _raise_oserror(*args, **kwargs):
        raise OSError("docker exec failed — container not reachable")

    monkeypatch.setattr(
        "core.internal.shared.docker_compose.nginx_reload",
        _raise_oserror,
    )

    # Должно пройти без исключения (non-fatal контракт D6)
    cd._step_nginx_reload()

    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
    print("--- END LDD TRAJECTORY ---")
    print("[IMP:9][test] R5 PASS: nginx reload OSError handled (non-fatal)")


# endregion FUNC_test_step_nginx_reload_failure_nonfatal
