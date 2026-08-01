#!/usr/bin/env python3
"""
# GREP_SUMMARY: test-bootstrap-phases, BootstrapPhase, phase-dependency-graph, precondition-check, state-machine, bootstrap-lifecycle, phase-enum, unit-tests
# STRUCTURE: ▶ BootstrapPhase enum (14 values) → ◇ _phase_dependency_graph edges (φ8.5→φ8, φ13→φ12, φ12←φ9,φ11) → ◇ precondition_check() success/failure (φ1 root, φ4 age-key, φ5 node-yaml, φ6 registry-warn, φ8 docker, φ9-φ13 pass) → ⊕ LDD trajectory IMP:7-10 assertions → ⎋ 15 tests total
# region MODULE_CONTRACT
## @purpose  Unit tests for BootstrapPhase enum, _phase_dependency_graph, and precondition_check()
##           from state_machine.py. Covers T9 acceptance criteria: 14 phase values verified,
##           dependency graph edges validated, precondition_check() success and failure paths
##           for critical phases (φ1 root, φ4 age key, φ5 node-yaml, φ6 registry, φ8 docker).
## @scope    Tests the phase-level logic of bootstrap lifecycle — enum integrity, dependency
##           graph completeness, and intra-phase precondition validation. Does NOT test
##           StateMachine state transitions, checkpoint/resume, or full init/update flows.
## @invariants
##   - All subprocess-dependent tests mock subprocess.run to avoid real system calls
##   - File operations use tmp_path exclusively — never /var/lib/platform or /etc
##   - Each test validates IMP:9 business logic log presence via caplog + ldd_trajectory
##   - BootstrapState instances are created fresh per test (no shared state)
##   - monkeypatch.setenv / monkeypatch.setattr used for os.environ and os.geteuid mocking
## @rationale Direct BootstrapPhase enum + dependency graph + precondition_check testing
##            without full StateMachine orchestration. Isolates phase logic from step logic
##            for targeted regression coverage.
## @changes
##   2026-07-30 · Created — DevPlan 087 T9: phase precondition unit tests
# endregion MODULE_CONTRACT
"""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory

# ── Import the module under test ──
_MODULE_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "lifecycle"
sys.path.insert(0, str(_MODULE_DIR))
import state_machine as sm

# Re-export for concise test references
BootstrapPhase = sm.BootstrapPhase
_phase_dependency_graph = sm._phase_dependency_graph

# B9 T2: precondition_check переехал в state_store.py; исключения raise'ятся из канонического
# пакетного state_machine (ленивый импорт в state_store) — НЕ из script-загруженного sm
from core.internal.bootstrap.lifecycle.state_machine import PhasePreconditionError

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# region Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def state() -> sm.BootstrapState:
    """Create a fresh BootstrapState instance for precondition_check tests."""
    return sm.BootstrapState()


@pytest.fixture
def tmp_core_dir(tmp_path: Path) -> Path:
    """Create a temporary core directory structure for deploy-related tests."""
    core_dir = tmp_path / "core"
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True, exist_ok=True)
    (bootstrap_dir / "deploy-modules.sh").write_text("#!/bin/bash\necho ok\n")
    (bootstrap_dir / "converge.sh").write_text("#!/bin/bash\necho ok\n")
    (bootstrap_dir / "install-acme.sh").write_text("#!/bin/bash\necho ok\n")
    return core_dir


# endregion Fixtures


# ═══════════════════════════════════════════════════════════════════
# region Tests: BootstrapPhase enum integrity
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · BootstrapPhase.phase_count() returns 14 (AC1)
# · Scenario: Call phase_count() → must return 14
# · Last fail: N/A (new test)
# · Remove if: BootstrapPhase enum values are consolidated or expanded
# region FUNC_test_bootstrap_phase_enum_has_14_values
@ldd_trajectory
def test_bootstrap_phase_enum_has_14_values(caplog) -> None:
    """BootstrapPhase should define exactly 14 consolidated phases."""
    count = BootstrapPhase.phase_count()
    assert count == 14, f"Expected 14 phases, got {count}"
    assert len(BootstrapPhase.ALL_PHASES) == 14
    assert len(BootstrapPhase.INIT_PHASES) == 9
    assert len(BootstrapPhase.UPDATE_PHASES) == 5
    logger.critical("[IMP:9][test] BootstrapPhase.phase_count() = 14 — OK")


# endregion FUNC_test_bootstrap_phase_enum_has_14_values


# 🧪 TRAP[TEST] · Regression · All 14 phase value strings match canonical names
# · Scenario: Iterate ALL_PHASES → verify each value string is expected
# · Last fail: N/A (new test)
# · Remove if: phase naming convention changes
# region FUNC_test_bootstrap_phase_enum_values
@ldd_trajectory
def test_bootstrap_phase_enum_values(caplog) -> None:
    """All 14 phase values should have canonical string names."""
    expected_init = frozenset(
        {
            "system_bootstrap",
            "user_accounts",
            "platform_setup",
            "secrets_provision",
            "node_configuration",
            "registry_auth",
            "certificates",
            "deploy_services",
            "converge_services",
        }
    )
    expected_update = frozenset(
        {
            "secrets_update",
            "node_config_update",
            "registry_update",
            "deploy_update",
            "converge_update",
        }
    )
    assert expected_init == BootstrapPhase.INIT_PHASES, (
        f"INIT_PHASES mismatch: {BootstrapPhase.INIT_PHASES - expected_init} extra, "
        f"{expected_init - BootstrapPhase.INIT_PHASES} missing"
    )
    assert expected_update == BootstrapPhase.UPDATE_PHASES, (
        f"UPDATE_PHASES mismatch: {BootstrapPhase.UPDATE_PHASES - expected_update} extra, "
        f"{expected_update - BootstrapPhase.UPDATE_PHASES} missing"
    )
    assert len(BootstrapPhase.ALL_PHASES) == 14
    logger.critical("[IMP:9][test] All 14 BootstrapPhase values verified — OK")


# endregion FUNC_test_bootstrap_phase_enum_values

# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: _phase_dependency_graph
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · _phase_dependency_graph contains all phases with deps (AC7)
# · Scenario: All 14 phases represented — 13 explicit entries (φ1 root excluded as it has no deps)
# · Last fail: N/A (new test — DevPlan 087 phase dependency graph)
# · Remove if: phase dependency graph structure changes
# region FUNC_test_phase_dependency_graph_has_all_phases
@ldd_trajectory
def test_phase_dependency_graph_has_all_phases(caplog) -> None:
    """Dependency graph should cover all 14 phases.

    φ1 (system_bootstrap) is the root and is NOT an explicit key in the graph
    because it has no prerequisite phases — it is the implicit entry point.
    The remaining 13 phases are explicit keys. All keys must be valid
    BootstrapPhase values.
    """
    graph_phases = set(_phase_dependency_graph.keys())
    extra = graph_phases - BootstrapPhase.ALL_PHASES
    assert not extra, f"Extra phases in dependency graph: {extra}"

    # φ1 (system_bootstrap) is the root — not a key because it has no deps
    missing = BootstrapPhase.ALL_PHASES - graph_phases - {BootstrapPhase.SYSTEM_BOOTSTRAP}
    assert not missing, f"Missing phases in dependency graph: {missing}"

    assert BootstrapPhase.SYSTEM_BOOTSTRAP not in graph_phases, (
        "φ1 (system_bootstrap) should NOT be in graph — it is the root entry point"
    )
    assert len(graph_phases) == 13, f"Expected 13 explicit phases in graph, got {len(graph_phases)}"
    logger.critical("[IMP:9][test] _phase_dependency_graph has all 14 phases (13 keys + φ1 root) — OK")


# endregion FUNC_test_phase_dependency_graph_has_all_phases


# 🧪 TRAP[TEST] · Regression · Converge phases depend on deploy (φ8.5←φ8, φ13←φ12)
# · Scenario: CONVERGE_SERVICES depends on DEPLOY_SERVICES
#             CONVERGE_UPDATE depends on DEPLOY_UPDATE
# · Last fail: N/A (new test)
# · Remove if: converge dependency edges change
# region FUNC_test_phase_dependency_graph_converge
@ldd_trajectory
def test_phase_dependency_graph_converge(caplog) -> None:
    """φ8.5 (converge_services) should depend on φ8 (deploy_services).
    φ13 (converge_update) should depend on φ12 (deploy_update)."""
    # φ8.5 ← φ8 (converge depends on deploy)
    converge_deps = _phase_dependency_graph[BootstrapPhase.CONVERGE_SERVICES]
    assert BootstrapPhase.DEPLOY_SERVICES in converge_deps, f"φ8.5 should depend on φ8, got deps: {converge_deps}"
    assert len(converge_deps) == 1, f"φ8.5 should have exactly 1 dependency, got {len(converge_deps)}"

    # φ13 ← φ12 (converge_update depends on deploy_update)
    converge_update_deps = _phase_dependency_graph[BootstrapPhase.CONVERGE_UPDATE]
    assert BootstrapPhase.DEPLOY_UPDATE in converge_update_deps, (
        f"φ13 should depend on φ12, got deps: {converge_update_deps}"
    )
    assert len(converge_update_deps) == 1, f"φ13 should have exactly 1 dependency, got {len(converge_update_deps)}"

    logger.critical("[IMP:9][test] Converge dependencies verified — φ8.5←φ8, φ13←φ12 — OK")


# endregion FUNC_test_phase_dependency_graph_converge


# 🧪 TRAP[TEST] · Regression · Update deploy phase depends on secrets and registry (φ12←φ9,φ11)
# · Scenario: DEPLOY_UPDATE depends on SECRETS_UPDATE and REGISTRY_UPDATE
# · Last fail: N/A (new test)
# · Remove if: update dependency edges change
# region FUNC_test_phase_dependency_graph_update
@ldd_trajectory
def test_phase_dependency_graph_update(caplog) -> None:
    """φ12 (deploy_update) should depend on φ9 (secrets_update) and φ11 (registry_update)."""
    update_deps = _phase_dependency_graph[BootstrapPhase.DEPLOY_UPDATE]
    assert BootstrapPhase.SECRETS_UPDATE in update_deps, (
        f"φ12 should depend on φ9 (secrets_update), got deps: {update_deps}"
    )
    assert BootstrapPhase.REGISTRY_UPDATE in update_deps, (
        f"φ12 should depend on φ11 (registry_update), got deps: {update_deps}"
    )
    assert len(update_deps) == 2, f"φ12 should have exactly 2 dependencies, got {len(update_deps)}"

    # Verify update entry points have no deps
    assert _phase_dependency_graph[BootstrapPhase.SECRETS_UPDATE] == set(), "φ9 should have no deps"
    assert _phase_dependency_graph[BootstrapPhase.REGISTRY_UPDATE] == set(), "φ11 should have no deps"

    logger.critical("[IMP:9][test] Update dependency edges verified — φ12←φ9,φ11 — OK")


# endregion FUNC_test_phase_dependency_graph_update

# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: precondition_check() — φ1 system-bootstrap
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · φ1 precondition passes when root + tools available
# · Scenario: os.geteuid() = 0, subprocess.run for command -v returns success
#   → precondition_check(φ1) completes without error
# · Last fail: N/A (new test)
# · Remove if: φ1 precondition logic changes
# region FUNC_test_precondition_check_system_bootstrap_root
@ldd_trajectory
def test_precondition_check_system_bootstrap_root(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
) -> None:
    """φ1 precondition should pass when running as root with apt-get/dpkg available."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "/usr/bin/apt-get"
        mock_run.return_value.stderr = ""

        state.precondition_check(BootstrapPhase.SYSTEM_BOOTSTRAP)

        # Verify _check_command_exists was called for both tools
        call_args = [call[0] for call in mock_run.call_args_list]
        assert any("apt-get" in str(args) for args in call_args), "apt-get check expected"
        assert any("dpkg" in str(args) for args in call_args), "dpkg check expected"

    logger.critical("[IMP:9][test] φ1 precondition passed with root + tools — OK")


# endregion FUNC_test_precondition_check_system_bootstrap_root


# 🧪 TRAP[TEST] · Regression · φ1 precondition fails when euid != 0
# · Scenario: os.geteuid() = 1000 → PhasePreconditionError raised
# · Last fail: N/A (new test)
# · Remove if: φ1 root check logic changes
# region FUNC_test_precondition_check_system_bootstrap_no_root
@ldd_trajectory
def test_precondition_check_system_bootstrap_no_root(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
) -> None:
    """φ1 precondition should fail when not running as root (euid != 0)."""
    monkeypatch.setattr(os, "geteuid", lambda: 1000)

    with pytest.raises(PhasePreconditionError) as excinfo:
        state.precondition_check(BootstrapPhase.SYSTEM_BOOTSTRAP)

    assert "euid=0" in str(excinfo.value), f"Error should mention euid=0, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ1 precondition failed as non-root — OK")


# endregion FUNC_test_precondition_check_system_bootstrap_no_root

# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: precondition_check() — φ4 secrets-provision
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · φ4 precondition passes when AGE_SECRET_KEY env var set
# · Scenario: AGE_SECRET_KEY env var set → precondition_check(φ4) OK
# · Last fail: N/A (new test)
# · Remove if: φ4 age key check logic changes
# region FUNC_test_precondition_check_secrets_with_age_key
@ldd_trajectory
def test_precondition_check_secrets_with_age_key(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
) -> None:
    """φ4 precondition should pass when AGE_SECRET_KEY env var is present."""
    monkeypatch.setenv("AGE_SECRET_KEY", "AGE-SECRET-KEY-1234567890abcdef")
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)

    state.precondition_check(BootstrapPhase.SECRETS_PROVISION)

    logger.critical("[IMP:9][test] φ4 precondition passed with AGE_SECRET_KEY — OK")


# endregion FUNC_test_precondition_check_secrets_with_age_key


# 🧪 TRAP[TEST] · Regression · φ4 precondition fails when no age key available
# · Scenario: No AGE_SECRET_KEY, no SOPS_AGE_KEY, /etc/age/key.txt doesn't exist
#   → PhasePreconditionError raised
# · Last fail: N/A (new test)
# · Remove if: φ4 age key check logic changes
# region FUNC_test_precondition_check_secrets_no_age_key
@ldd_trajectory
def test_precondition_check_secrets_no_age_key(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
) -> None:
    """φ4 precondition should fail when no age key env var or file exists."""
    monkeypatch.delenv("AGE_SECRET_KEY", raising=False)
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)

    # Mock os.path.isfile to return False for /etc/age/key.txt
    original_isfile = os.path.isfile

    def mock_isfile(path: str) -> bool:
        if path == "/etc/age/key.txt":
            return False
        return original_isfile(path)

    monkeypatch.setattr(os.path, "isfile", mock_isfile)

    with pytest.raises(PhasePreconditionError) as excinfo:
        state.precondition_check(BootstrapPhase.SECRETS_PROVISION)

    assert "AGE_SECRET_KEY" in str(excinfo.value), f"Error should mention AGE_SECRET_KEY, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ4 precondition failed without age key — OK")


# endregion FUNC_test_precondition_check_secrets_no_age_key

# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: precondition_check() — φ5 node-configuration
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · φ5 precondition passes when NODE_YAML points to existing file
# · Scenario: NODE_YAML set to tmp_path / node.yaml that exists
#   → precondition_check(φ5) OK
# · Last fail: N/A (new test)
# · Remove if: φ5 node-yaml check logic changes
# region FUNC_test_precondition_check_node_config_success
@ldd_trajectory
def test_precondition_check_node_config_success(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """φ5 precondition should pass when NODE_YAML points to an existing file."""
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("node:\n  name: test-node\n")
    monkeypatch.setenv("NODE_YAML", str(node_yaml))

    state.precondition_check(BootstrapPhase.NODE_CONFIGURATION)

    logger.critical("[IMP:9][test] φ5 precondition passed with valid NODE_YAML — OK")


# endregion FUNC_test_precondition_check_node_config_success


# 🧪 TRAP[TEST] · Regression · φ5 precondition fails when NODE_YAML is invalid
# · Scenario: NODE_YAML set to non-existent path → PhasePreconditionError raised
# · Last fail: N/A (new test)
# · Remove if: φ5 node-yaml check logic changes
# region FUNC_test_precondition_check_node_config_no_yaml
@ldd_trajectory
def test_precondition_check_node_config_no_yaml(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
) -> None:
    """φ5 precondition should fail when NODE_YAML path does not exist."""
    monkeypatch.setenv("NODE_YAML", "/tmp/non-existent-node.yaml")

    with pytest.raises(PhasePreconditionError) as excinfo:
        state.precondition_check(BootstrapPhase.NODE_CONFIGURATION)

    assert "NODE_YAML" in str(excinfo.value), f"Error should mention NODE_YAML, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ5 precondition failed without valid NODE_YAML — OK")


# endregion FUNC_test_precondition_check_node_config_no_yaml

# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: precondition_check() — φ6 registry-auth
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · φ6 emits warning but does not raise when GHCR_PULL_TOKEN missing
# · Scenario: GHCR_PULL_TOKEN not set → precondition_check(φ6) passes with WARN log
# · Last fail: N/A (new test)
# · Remove if: φ6 registry auth logic changes
# region FUNC_test_precondition_check_registry_auth
@ldd_trajectory
def test_precondition_check_registry_auth(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
) -> None:
    """φ6 precondition should not raise when GHCR_PULL_TOKEN is missing — only warn."""
    monkeypatch.delenv("GHCR_PULL_TOKEN", raising=False)

    state.precondition_check(BootstrapPhase.REGISTRY_AUTH)

    # Verify a WARNING-level log was emitted about missing token
    warning_found = any("GHCR_PULL_TOKEN" in r.message and r.levelname == "WARNING" for r in caplog.records)
    assert warning_found, (
        "Expected a WARNING log about missing GHCR_PULL_TOKEN, "
        f"records: {[(r.levelname, r.message) for r in caplog.records]}"
    )
    logger.critical("[IMP:9][test] φ6 precondition passed with GHCR_PULL_TOKEN warning — OK")


# endregion FUNC_test_precondition_check_registry_auth

# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: precondition_check() — φ8 deploy-services
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · φ8 precondition passes when script exists + docker running
# · Scenario: deploy-modules.sh exists, docker info returns success
#   → precondition_check(φ8) OK
# · Last fail: N/A (new test)
# · Remove if: φ8 deploy precondition logic changes
# region FUNC_test_precondition_check_deploy_success
@ldd_trajectory
def test_precondition_check_deploy_success(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
    tmp_core_dir: Path,
) -> None:
    """φ8 precondition should pass when deploy-modules.sh exists and Docker is running."""
    monkeypatch.setenv("CORE_DIR", str(tmp_core_dir))

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Docker info output"
        mock_run.return_value.stderr = ""

        state.precondition_check(BootstrapPhase.DEPLOY_SERVICES)

    logger.critical("[IMP:9][test] φ8 precondition passed — deploy-modules.sh + docker OK")


# endregion FUNC_test_precondition_check_deploy_success


# 🧪 TRAP[TEST] · Regression · φ8 precondition fails when docker not running
# · Scenario: deploy-modules.sh exists, docker info returns non-zero
#   → PhasePreconditionError raised
# · Last fail: N/A (new test)
# · Remove if: φ8 docker check logic changes
# region FUNC_test_precondition_check_deploy_no_docker
@ldd_trajectory
def test_precondition_check_deploy_no_docker(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
    tmp_core_dir: Path,
) -> None:
    """φ8 precondition should fail when Docker daemon is not running."""
    monkeypatch.setenv("CORE_DIR", str(tmp_core_dir))

    with patch("subprocess.run") as mock_run:
        # Mock docker info to fail
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Cannot connect to the Docker daemon"

        with pytest.raises(PhasePreconditionError) as excinfo:
            state.precondition_check(BootstrapPhase.DEPLOY_SERVICES)

    assert "Docker" in str(excinfo.value), f"Error should mention Docker, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ8 precondition failed — Docker not running — OK")


# endregion FUNC_test_precondition_check_deploy_no_docker

# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: precondition_check() — φ9-φ13 update phases (no strict preconditions)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · φ9-φ13 update phases pass precondition checks
# · Scenario: φ9-φ11 (light preconditions) pass without setup;
#             φ12 (deploy_update) needs deploy-modules.sh + docker;
#             φ13 (converge_update) issues warning but does not raise
# · Last fail: N/A (new test)
# · Remove if: update phase precondition logic changes
# region FUNC_test_precondition_check_update_phases
@ldd_trajectory
def test_precondition_check_update_phases(
    caplog,
    state: sm.BootstrapState,
    monkeypatch,
    tmp_core_dir: Path,
) -> None:
    """φ9-φ13 update phases should pass precondition_check without raising errors.

    φ9-φ11 have no strict preconditions.
    φ12 (deploy_update) requires deploy-modules.sh + Docker.
    φ13 (converge_update) warns but does not raise.
    """
    # φ9-φ11: no preconditions — pass without any setup
    for phase in (BootstrapPhase.SECRETS_UPDATE, BootstrapPhase.NODE_CONFIG_UPDATE, BootstrapPhase.REGISTRY_UPDATE):
        state.precondition_check(phase)

    # φ12: needs deploy-modules.sh + docker running
    monkeypatch.setenv("CORE_DIR", str(tmp_core_dir))
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        state.precondition_check(BootstrapPhase.DEPLOY_UPDATE)

    # φ13: converge-update — warns but does not raise
    state.precondition_check(BootstrapPhase.CONVERGE_UPDATE)

    logger.critical("[IMP:9][test] All update phases (φ9-φ13) passed precondition_check — OK")


# endregion FUNC_test_precondition_check_update_phases

# endregion
