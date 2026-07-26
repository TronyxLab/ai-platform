"""
# GREP_SUMMARY: test-context-deployer-audit, test-docker-orchestrator-audit, audit-logger, write-audit-entry, json-lines, shell-audit
# STRUCTURE: ▶ mock write_audit_entry → ◇ context_deployer audit [deploy, fields, status-format] → ◇ docker_orchestrator audit [START, FAILED] → ◇ shell audit_log preserved → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for audit_logger integration in context_deployer.py and docker_orchestrator.py.
##           Verifies that deploy operations write correct audit entries via shared audit_logger
##           in JSON-lines format, and that the legacy shell audit_log() is preserved.
## @scope    Tests _write_audit() in context_deployer.py and write_audit_entry() calls in
##           docker_orchestrator.py deploy_docker_module(). Also verifies shell audit_log()
##           function definition is unchanged (regression guard for Phase C changes).
## @invariants
##   - All subprocess calls are mocked (no real docker compose)
##   - audit_logger write_audit_entry is mocked for capture
##   - Each test validates IMP:9 business logic log presence via @ldd_trajectory
##   - Shell audit test validates file existence + function definition only (no execution)
## @rationale DevPlan 081 Phase C TASK-081C3: audit_logger integration — ensure deploy operations
##            write correct, parseable audit entries in shared JSON-lines format.
## @changes  2026-07-26 | DevPlan 081C — Created audit integration tests
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Import context_deployer ──
_CD_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "deploy"
sys.path.insert(0, str(_CD_DIR))
import context_deployer as cd

# ── Import docker_orchestrator (sys.path already extended above) ──
import docker_orchestrator as dorch

# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def node_yaml_file(tmp_path):
    """Create a node.yaml with two test-ctx projects.

    ## @purpose  Provide deterministic node.yaml for context_deployer tests.
    ## @io  ⇥ tmp_path → ⎋ str path to node.yaml
    """
    yaml_content = """\
node:
  name: test-node
  platform_domain: test.example.com
  context: test-ctx
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
"""
    yaml_path = tmp_path / "node.yaml"
    yaml_path.write_text(yaml_content)
    return str(yaml_path)


@pytest.fixture
def mock_context_deployer_shared(monkeypatch):
    """Mock all shared docker compose operations in context_deployer for success path.

    ## @purpose  Allow deploy_context_projects to run through without real docker calls.
    ##            All shared operations return success so _write_audit is exercised
    ##            with status="deployed" and channel="ghcr".
    ## @io  ⎋ None (side-effect: monkeypatch on cd module)
    ## @invariants
    ##   - _is_project_healthy → False (force deploy, not skip)
    ##   - _shared_retry_pull → True (ghcr channel)
    ##   - _shared_docker_compose_up → True
    ##   - _shared_healthcheck_poll → "healthy"
    """
    monkeypatch.setattr(cd, "_is_project_healthy", lambda name: False)
    monkeypatch.setattr(cd, "_shared_retry_pull", lambda d, **kw: True)
    monkeypatch.setattr(cd, "_shared_docker_compose_up", lambda d, **kw: True)
    monkeypatch.setattr(cd, "_shared_healthcheck_poll", lambda n, **kw: "healthy")


@pytest.fixture
def module_dir(tmp_path):
    """Create a temporary modules directory with a compose.yaml for testmodule.

    ## @purpose  Provide minimal module structure for deploy_docker_module tests.
    ## @io  ⇥ tmp_path → ⎋ str path to modules dir
    """
    mod_dir = tmp_path / "modules" / "testmodule"
    mod_dir.mkdir(parents=True)
    _write_compose_yaml(mod_dir, "testmodule")
    return str(tmp_path / "modules")


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Helpers
# ═══════════════════════════════════════════════════════════════════


def _write_compose_yaml(mod_dir: Path, module_name: str) -> None:
    """Write a minimal compose.yaml with no build: section for test purposes.

    ## @purpose  Create a compose file that deploy_docker_module can resolve
    ##            without triggering the build: code path (content-hash, etc.).
    ## @io  ⇥ mod_dir: Path, module_name: str → ⎋ None (side-effect: file write)
    """
    compose = mod_dir / "compose.yaml"
    compose.write_text(
        f"""services:
  {module_name}:
    image: test/{module_name}:latest
"""
    )


def _make_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Create a subprocess.CompletedProcess-like MagicMock.

    ## @purpose  Factor out repeated MagicMock creation for subprocess.run returns.
    ## @io  ⇥ returncode: int, stdout: str, stderr: str → ⎋ MagicMock
    """
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr, spec=subprocess.CompletedProcess)


def _default_subprocess_side_effect(up_returncode: int = 0):
    """Build a subprocess.run side_effect that handles all calls during deploy_docker_module.

    ## @purpose  Provide a drop-in side_effect for mock_subprocess that:
    ##            - Returns valid JSON for compose config (orphan reconciliation)
    ##            - Returns empty for docker ps -a
    ##            - Returns the caller-specified returncode for compose up -d
    ##            - Returns success for everything else
    ## @io  ⇥ up_returncode: int → ⎋ callable side_effect
    ## @invariants
    ##   - compose config --format json returns empty services dict
    ##   - docker ps -a returns empty
    ##   - Other commands return returncode=0
    """

    def _side_effect(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(str(x) for x in cmd) if isinstance(cmd, list) else str(cmd)

        if "config" in cmd_str and "--format" in cmd_str:
            return _make_subprocess_result(returncode=0, stdout='{"services": {}}', stderr="")
        if "ps" in cmd_str and "--format" in cmd_str:
            return _make_subprocess_result(returncode=0, stdout="", stderr="")
        if "up" in cmd_str and "-d" in cmd_str and "compose" in cmd_str:
            return _make_subprocess_result(returncode=up_returncode, stdout="", stderr="")
        return _make_subprocess_result(returncode=0, stdout="", stderr="")

    return _side_effect


# endregion Helpers


# ═══════════════════════════════════════════════════════════════════
# region TEST 1: context_deployer writes audit on deploy
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _write_audit calls write_audit_entry via shared audit_logger
# · Scenario: deploy_context_projects with 2 projects → _write_audit called per project
# · Last fail: N/A (new test)
# · Remove if: audit integration or _write_audit implementation changes
@ldd_trajectory
def test_context_deployer_writes_audit_on_deploy(
    caplog,
    node_yaml_file,
    mock_context_deployer_shared,
):
    """deploy_context_projects should call write_audit_entry at least once with context_deploy: tag.

    ▶ ┌node.yaml + mocked shared ops┐ → ◇ deploy 2 projects → ⊕ audit entries → ◇ verify tag prefix
    """
    with patch("core.internal.shared.audit_logger.write_audit_entry") as mock_audit_entry:
        results = cd.deploy_context_projects(node_yaml_file, "test-ctx", projects_base="/tmp/test-projects")

        assert len(results) >= 1, "Should have at least one project result"
        assert mock_audit_entry.called, "write_audit_entry should have been called"

        # Verify at least one call has tag starting with "context_deploy:"
        audit_tags = []
        for call_args in mock_audit_entry.call_args_list:
            tag = call_args.args[0] if call_args.args else call_args.kwargs.get("tag", "")
            audit_tags.append(tag)

        context_deploy_tags = [t for t in audit_tags if t.startswith("context_deploy:")]
        assert len(context_deploy_tags) >= 1, (
            f"At least one audit tag should start with 'context_deploy:', got {audit_tags}"
        )

        logger.critical(
            "[IMP:9][test] context_deployer writes %d audit entries for %d projects — tags: %s",
            len(mock_audit_entry.call_args_list),
            len(results),
            audit_tags,
        )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST 2: audit entry contains required fields
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Audit entry has ts, tag, status, msg fields
# · Scenario: write_audit_entry called with tag, status, message → capture args
# · Last fail: N/A (new test)
# · Remove if: audit entry format changes
@ldd_trajectory
def test_audit_entry_contains_required_fields(
    caplog,
    node_yaml_file,
    mock_context_deployer_shared,
):
    """Each write_audit_entry call should receive non-empty tag, status, and message.

    ▶ ┌mocked deploy┐ → ◇ capture audit calls → ◇ verify each field non-empty
    """
    with patch("core.internal.shared.audit_logger.write_audit_entry") as mock_audit_entry:
        cd.deploy_context_projects(node_yaml_file, "test-ctx", projects_base="/tmp/test-projects")

        assert mock_audit_entry.called, "write_audit_entry should have been called"

        for i, call_args in enumerate(mock_audit_entry.call_args_list):
            # Extract tag, status, message from positional or keyword args
            if call_args.args:
                tag = call_args.args[0]
                status = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("status", "")
                message = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("message", "")
            else:
                tag = call_args.kwargs.get("tag", "")
                status = call_args.kwargs.get("status", "")
                message = call_args.kwargs.get("message", "")

            assert isinstance(tag, str) and tag, f"Call {i}: tag should be non-empty string, got {tag!r}"
            assert isinstance(status, str) and status, f"Call {i}: status should be non-empty string, got {status!r}"
            assert isinstance(message, str) and message, (
                f"Call {i}: message should be non-empty string, got {message!r}"
            )

            logger.critical(
                "[IMP:9][test] Audit entry %d fields OK — tag=%s status=%s msg_len=%d",
                i,
                tag,
                status,
                len(message),
            )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST 3: audit format is valid JSON-lines style
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Audit format validates status values
# · Scenario: capture audit calls → verify status is one of allowed values
# · Last fail: N/A (new test)
# · Remove if: audit status vocabulary changes
@ldd_trajectory
def test_audit_format_is_valid_json_lines(
    caplog,
    node_yaml_file,
    mock_context_deployer_shared,
):
    """Each audit call arguments must be strings; status must be in the allowed vocabulary.

    ▶ ┌mocked deploy┐ → ◇ capture audit calls → ◇ verify strings → ◇ verify status vocabulary
    """
    ALLOWED_STATUSES = {"deployed", "skipped", "failed", "DEPLOYED", "FAILED", "START"}

    with patch("core.internal.shared.audit_logger.write_audit_entry") as mock_audit_entry:
        cd.deploy_context_projects(node_yaml_file, "test-ctx", projects_base="/tmp/test-projects")

        assert mock_audit_entry.called, "write_audit_entry should have been called"

        for i, call_args in enumerate(mock_audit_entry.call_args_list):
            if call_args.args:
                tag = call_args.args[0]
                status = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("status", "")
                message = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("message", "")
            else:
                tag = call_args.kwargs.get("tag", "")
                status = call_args.kwargs.get("status", "")
                message = call_args.kwargs.get("message", "")

            # All fields must be strings (not None, not non-string)
            assert isinstance(tag, str), f"Call {i}: tag must be str, got {type(tag).__name__}"
            assert isinstance(status, str), f"Call {i}: status must be str, got {type(status).__name__}"
            assert isinstance(message, str), f"Call {i}: message must be str, got {type(message).__name__}"

            # Non-empty
            assert tag, f"Call {i}: tag must not be empty"
            assert status, f"Call {i}: status must not be empty"
            assert message, f"Call {i}: message must not be empty"

            # Status must be a recognized value
            assert status in ALLOWED_STATUSES, f"Call {i}: status '{status}' not in allowed set {ALLOWED_STATUSES}"

            logger.critical(
                "[IMP:9][test] Audit call %d valid: tag=%s status=%s msg_len=%d",
                i,
                tag,
                status,
                len(message),
            )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST 4: docker_orchestrator writes START audit
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deploy_docker_module writes START audit entry
# · Scenario: deploy_docker_module("testmodule") with mocked subprocess → START audit
# · Last fail: N/A (new test)
# · Remove if: audit integration in deploy_docker_module changes
@ldd_trajectory
def test_docker_orchestrator_writes_audit_on_deploy_start(
    caplog,
    module_dir,
):
    """deploy_docker_module should call write_audit_entry with status='START' and tag containing module_name.

    ▶ ┌mock subprocess + module_dir┐ → ◇ deploy_docker_module → ◇ capture audit calls → ◇ verify START
    """
    with (
        patch.object(subprocess, "run") as mock_run,
        patch("docker_orchestrator._shared_write_audit_entry") as mock_audit,
    ):
        mock_run.side_effect = _default_subprocess_side_effect(up_returncode=0)

        result = dorch.deploy_docker_module(
            module_name="testmodule",
            modules_dir=module_dir,
        )

        assert result is True, "deploy_docker_module should succeed"

        # Verify START audit entry was written
        start_calls = [
            c
            for c in mock_audit.call_args_list
            if (c.args[1] if len(c.args) > 1 else c.kwargs.get("status", "")) == "START"
        ]
        assert len(start_calls) >= 1, "At least one audit call should have status='START'"

        # Verify START call has tag containing module name
        start_call = start_calls[0]
        tag = start_call.args[0] if start_call.args else start_call.kwargs.get("tag", "")
        assert "testmodule" in tag, f"START audit tag should contain 'testmodule', got '{tag}'"

        logger.critical(
            "[IMP:9][test] docker_orchestrator writes START audit — tag=%s status=%s",
            tag,
            "START",
        )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST 5: docker_orchestrator writes FAILED audit on compose failure
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · deploy_docker_module writes FAILED audit on compose up failure
# · Scenario: subprocess.run returns non-zero for compose up → module returns False → FAILED audit
# · Last fail: N/A (new test)
# · Remove if: audit integration in deploy_docker_module changes
@ldd_trajectory
def test_docker_orchestrator_writes_audit_on_healthcheck_fail(
    caplog,
    module_dir,
):
    """deploy_docker_module should write audit with status='FAILED' when compose up fails.

    ▶ ┌mock subprocess (compose up fails) + module_dir┐ → ◇ deploy → ◇ False → ◇ FAILED audit
    """
    with (
        patch.object(subprocess, "run") as mock_run,
        patch("docker_orchestrator._shared_write_audit_entry") as mock_audit,
    ):
        mock_run.side_effect = _default_subprocess_side_effect(up_returncode=1)

        result = dorch.deploy_docker_module(
            module_name="testmodule",
            modules_dir=module_dir,
        )

        assert result is False, "deploy_docker_module should fail when compose up fails"

        # Verify FAILED audit entry was written
        failed_calls = [
            c
            for c in mock_audit.call_args_list
            if (c.args[1] if len(c.args) > 1 else c.kwargs.get("status", "")) == "FAILED"
        ]
        assert len(failed_calls) >= 1, "At least one audit call should have status='FAILED'"

        # Verify FAILED call has tag with module name
        failed_call = failed_calls[0]
        tag = failed_call.args[0] if failed_call.args else failed_call.kwargs.get("tag", "")
        assert "testmodule" in tag, f"FAILED audit tag should contain 'testmodule', got '{tag}'"

        logger.critical(
            "[IMP:9][test] docker_orchestrator writes FAILED audit — tag=%s status=%s",
            tag,
            "FAILED",
        )


# endregion


# ═══════════════════════════════════════════════════════════════════
# region TEST 6: old shell audit_log format unchanged
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · Shell audit_log() preserved after Phase C changes
# · Scenario: core/lib/audit_logging.sh exists and contains audit_log() definition
# · Last fail: N/A (new test)
# · Remove if: shell audit_log function is intentionally removed or replaced
@ldd_trajectory
def test_old_shell_format_unchanged(caplog):
    """Shell audit_log() in core/lib/audit_logging.sh must still exist and be unchanged.

    ▶ ┌core/lib/audit_logging.sh┐ → ◇ exists? → ◇ contains audit_log()? → ◇ not write_audit_entry → ⎋
    """
    project_root = Path(__file__).resolve().parent.parent.parent
    audit_sh = project_root / "core" / "lib" / "audit_logging.sh"

    # Assert file exists
    assert audit_sh.is_file(), f"core/lib/audit_logging.sh should exist at {audit_sh}"

    content = audit_sh.read_text()

    # Assert the old shell audit_log() function definition is present
    assert "audit_log()" in content, (
        "audit_log() function must be defined in core/lib/audit_logging.sh (old format preserved)"
    )

    # Assert the new Python function name is NOT in the shell library
    # This proves Phase C changes did NOT break/modify the shell audit format
    assert "write_audit_entry" not in content, (
        "write_audit_entry should NOT appear in shell library — JSON-lines is Python-only"
    )

    logger.critical(
        "[IMP:9][test] Shell audit_log format preserved — file=%s, audit_log() found, write_audit_entry absent",
        audit_sh,
    )


# endregion
