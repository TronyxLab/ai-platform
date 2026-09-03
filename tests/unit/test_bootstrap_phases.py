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
##   - E3 (160): env-прекондишены через env= дикты, root/isfile — через facts= (0 setenv/geteuid);
##     subprocess.run (command -v, docker info) — patch() контекст для _check_command_exists
## @rationale Direct BootstrapPhase enum + dependency graph + precondition_check testing
##            without full StateMachine orchestration. Isolates phase logic from step logic
##            for targeted regression coverage.
## @changes
##   2026-07-30 · Created — DevPlan 087 T9: phase precondition unit tests
##   2026-08-13 · DevPlan 160 E3 — precondition_check через env=/facts= дикты (0 setenv/
##               os.geteuid/os.path.isfile); _registry_step_firewall через runner=/env=;
##               canonical_base через env= (phase_user_accounts env-параметр E3)
# endregion MODULE_CONTRACT
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._conftest.ldd import ldd_trajectory
from tests._conftest.r1 import r1_delegates

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


# region HELPERS


class _FakeRunner:
    """Scripted CommandRunner (DI-канон W4b) для phase-тестов (W4d): записывает команды.

    ## @purpose — Замена monkeypatch helpers_subprocess.run_subprocess в тестах фаз:
    ##            каждая команда фазы записывается (calls), возвращается rc=0 (успех).
    ## @io — ⇥ cmd → ⎋ CompletedProcess(returncode=0) (scripted)
    ## @complexity — O(1)
    ## @invariants — run() НЕ raise; default rc=0 (фазы завершаются без non-fatal issues)
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(list(cmd), returncode=0, stdout="", stderr="")


class _FakeFacts:
    """EnvironmentFacts-fake (E3, DevPlan 160): is_root/path_isfile — 0 monkeypatch os.

    ## @purpose — DI для facts= параметра precondition_check: is_root управляет root-guard'ом
    ##            (вместо патча os.geteuid); path_isfile — по умолчанию
    ##            реальный os.path.isfile (tmp-файлы видны), override-абельный (negative-кейсы).
    ## @io — ⇥ is_root: bool, path_isfile: Callable | None → ⎋ fake
    """

    def __init__(self, is_root: bool = True, path_isfile=None) -> None:
        self._is_root = is_root
        self._path_isfile = path_isfile if path_isfile is not None else os.path.isfile

    def is_root(self) -> bool:
        return self._is_root

    def which(self, binary: str) -> str | None:  # pragma: no cover — не используется
        return binary

    def path_isfile(self, path) -> bool:
        return self._path_isfile(path)


# endregion HELPERS


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
    assert count == 15, f"Expected 15 phases, got {count}"
    assert len(BootstrapPhase.ALL_PHASES) == 15
    assert len(BootstrapPhase.INIT_PHASES) == 10
    assert len(BootstrapPhase.UPDATE_PHASES) == 5
    logger.critical("[IMP:9][test] BootstrapPhase.phase_count() = 15 — OK")


# endregion FUNC_test_bootstrap_phase_enum_has_14_values


# 🧪 TRAP[TEST] · Regression · All 14 phase value strings match canonical names
# · Scenario: Iterate ALL_PHASES → verify each value string is expected
# · Last fail: N/A (new test)
# · Remove if: phase naming convention changes
# region FUNC_test_bootstrap_phase_enum_values
@ldd_trajectory
def test_bootstrap_phase_enum_values(caplog) -> None:
    """All 14 phase values should have canonical string names."""
    expected_init = frozenset({
        "system_bootstrap",
        "user_accounts",
        "platform_setup",
        "secrets_provision",
        "node_configuration",
        "registry_auth",
        "certificates",
        "deploy_services",
        "converge_services",
        "final_verify",
    })
    expected_update = frozenset({
        "secrets_update",
        "node_config_update",
        "registry_update",
        "deploy_update",
        "converge_update",
    })
    assert expected_init == BootstrapPhase.INIT_PHASES, (
        f"INIT_PHASES mismatch: {BootstrapPhase.INIT_PHASES - expected_init} extra, "
        f"{expected_init - BootstrapPhase.INIT_PHASES} missing"
    )
    assert expected_update == BootstrapPhase.UPDATE_PHASES, (
        f"UPDATE_PHASES mismatch: {BootstrapPhase.UPDATE_PHASES - expected_update} extra, "
        f"{expected_update - BootstrapPhase.UPDATE_PHASES} missing"
    )
    assert len(BootstrapPhase.ALL_PHASES) == 15
    logger.critical("[IMP:9][test] All 15 BootstrapPhase values verified — OK")


# endregion FUNC_test_bootstrap_phase_enum_values

# endregion Tests: BootstrapPhase enum integrity


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
    assert len(graph_phases) == 14, f"Expected 14 explicit phases in graph, got {len(graph_phases)}"
    logger.critical("[IMP:9][test] _phase_dependency_graph has all 15 phases (14 keys + φ1 root) — OK")


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

# endregion Tests: _phase_dependency_graph


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
) -> None:
    """φ1 precondition should pass when running as root with apt-get/dpkg available."""
    # W5-C1 (план 170): _check_command_exists переведён на shutil.which (stdlib) —
    # subprocess-патч заменён на патч which в phases/preconditions (0 реальных команд).
    # E3 (160): root-факты через facts= DI (0 monkeypatch os.geteuid)
    with patch(
        "core.internal.bootstrap.lifecycle.phases.preconditions.shutil.which",
        return_value="/usr/bin/apt-get",
    ) as mock_which:
        state.precondition_check(
            BootstrapPhase.SYSTEM_BOOTSTRAP,
            facts=_FakeFacts(is_root=True),
        )

        # Verify _check_command_exists was called for both tools
        call_args = [call[0][0] for call in mock_which.call_args_list]
        assert "apt-get" in call_args, "apt-get check expected"
        assert "dpkg" in call_args, "dpkg check expected"

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
) -> None:
    """φ1 precondition should fail when not running as root (euid != 0)."""
    # E3 (160): root-факты через facts= DI (0 monkeypatch os.geteuid)
    with pytest.raises(PhasePreconditionError) as excinfo:
        state.precondition_check(
            BootstrapPhase.SYSTEM_BOOTSTRAP,
            facts=_FakeFacts(is_root=False),
        )

    assert "euid=0" in str(excinfo.value), f"Error should mention euid=0, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ1 precondition failed as non-root — OK")


# endregion FUNC_test_precondition_check_system_bootstrap_no_root

# endregion Tests: precondition_check() — φ1 system-bootstrap


# ═══════════════════════════════════════════════════════════════════
# region Tests: precondition_check() — φ4 secrets-provision
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · φ4 precondition passes when AGE_SECRET_KEY env var set
# · Scenario: AGE_SECRET_KEY env var set → precondition_check(φ4) OK
# · Last fail: N/A (new test)
# · Remove if: φ4 age key check logic changes
# region FUNC_test_precondition_check_secrets_with_age_key
@ldd_trajectory
@r1_delegates
def test_precondition_check_secrets_with_age_key(
    caplog,
    state: sm.BootstrapState,
) -> None:
    """φ4 precondition should pass when AGE_SECRET_KEY env var is present.

    🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
    state.precondition_check (raises RuntimeError на нарушение precondition).
    """
    # E3 (160): env-дикт (AGE_SECRET_KEY) — 0 setenv/delenv.
    # Значение — заведомо фейковое (не секрет): gitleaks generic-api-key не срабатывает.
    state.precondition_check(
        BootstrapPhase.SECRETS_PROVISION,
        env={"AGE_SECRET_KEY": "age-secret-key-test-fixture-value"},
        facts=_FakeFacts(),
    )

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
) -> None:
    """φ4 precondition should fail when no age key env var or file exists."""
    # E3 (160): env={} (пустая env-цепочка) + facts.path_isfile→False (изоляция от
    # /etc/age/key.txt dev-машины) — 0 monkeypatch os.environ/os.path.isfile
    with pytest.raises(PhasePreconditionError) as excinfo:
        state.precondition_check(
            BootstrapPhase.SECRETS_PROVISION,
            env={},
            facts=_FakeFacts(path_isfile=lambda _: False),
        )

    assert "AGE_SECRET_KEY" in str(excinfo.value), f"Error should mention AGE_SECRET_KEY, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ4 precondition failed without age key — OK")


# endregion FUNC_test_precondition_check_secrets_no_age_key


# 🧪 TRAP[TEST] · 2026-08-06 · REGRESSION · W4 — φ4 precondition: AGE_SECRET_KEY_FILE env достаточно
# · Scenario: env-цепочка (канон, W4 DevPlan 140): AGE_SECRET_KEY_FILE установлен (файл-на-диске
# ·   НЕ требуется — чтение/отсутствие файла — ответственность node_detect.detect_age_key) →
# ·   precondition φ4 проходит без файла-на-диске
# · Last fail: N/A (new test — W4: env-цепочка первична, /etc/age/key.txt — restore-first fallback)
# · Remove if: φ4 precondition env-цепочка меняется
# region FUNC_test_precondition_check_secrets_age_key_file_env
@ldd_trajectory
@r1_delegates
def test_precondition_check_secrets_age_key_file_env(
    caplog,
    state: sm.BootstrapState,
) -> None:
    """W4: φ4 precondition проходит при AGE_SECRET_KEY_FILE env (файл-на-диске не нужен).

    🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
    state.precondition_check (raises PhasePreconditionError на нарушение — R5-negative
    test_precondition_check_secrets_no_age_key рядом).
    """
    # E3 (160): env-дикт (AGE_SECRET_KEY_FILE) — 0 setenv/delenv
    state.precondition_check(
        BootstrapPhase.SECRETS_PROVISION,
        env={"AGE_SECRET_KEY_FILE": "/tmp/nonexistent-age-key-file.txt"},
        facts=_FakeFacts(path_isfile=lambda _: False),
    )

    logger.critical("[IMP:9][test] φ4 precondition passed with AGE_SECRET_KEY_FILE env only (W4) — OK")


# endregion FUNC_test_precondition_check_secrets_age_key_file_env

# endregion Tests: precondition_check() — φ4 secrets-provision


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
@r1_delegates
def test_precondition_check_node_config_success(
    caplog,
    state: sm.BootstrapState,
    tmp_path: Path,
) -> None:
    """φ5 precondition should pass when NODE_YAML points to an existing file.

    🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
    state.precondition_check (raises RuntimeError на нарушение precondition).
    """
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("node:\n  name: test-node\n")
    # E3 (160): NODE_YAML — env-дикт (0 setenv); facts.path_isfile — реальный os.path.isfile
    state.precondition_check(
        BootstrapPhase.NODE_CONFIGURATION,
        env={"NODE_YAML": str(node_yaml)},
        facts=_FakeFacts(),
    )

    logger.critical("[IMP:9][test] φ5 precondition passed with valid NODE_YAML — OK")


# endregion FUNC_test_precondition_check_node_config_success


# 🧪 TRAP[TEST] · Regression · φ5 precondition fails when NODE_YAML is invalid
# · Scenario: NODE_YAML set to non-existent path → PhasePreconditionError raised
# · Last fail: N/A (new test)
# · Remove if: φ5 node-yaml check logic changes
# GUARD-PRESERVE (168): φ5 precondition guard (144 W3) — negative NODE_YAML, R5-пара к test_precondition_check_node_config_success (anti-survivorship)
# region FUNC_test_precondition_check_node_config_no_yaml
@ldd_trajectory
def test_precondition_check_node_config_no_yaml(
    caplog,
    state: sm.BootstrapState,
) -> None:
    """φ5 precondition should fail when NODE_YAML path does not exist."""
    # E3 (160): NODE_YAML — env-дикт (0 setenv)
    with pytest.raises(PhasePreconditionError) as excinfo:
        state.precondition_check(
            BootstrapPhase.NODE_CONFIGURATION,
            env={"NODE_YAML": "/tmp/non-existent-node.yaml"},
            facts=_FakeFacts(),
        )

    assert "NODE_YAML" in str(excinfo.value), f"Error should mention NODE_YAML, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ5 precondition failed without valid NODE_YAML — OK")


# endregion FUNC_test_precondition_check_node_config_no_yaml

# endregion Tests: precondition_check() — φ5 node-configuration


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
) -> None:
    """φ6 precondition should not raise when GHCR_PULL_TOKEN is missing — only warn."""
    # E3 (160): env={} (без GHCR_PULL_TOKEN) — 0 delenv
    state.precondition_check(
        BootstrapPhase.REGISTRY_AUTH,
        env={},
        facts=_FakeFacts(),
    )

    # Verify a WARNING-level log was emitted about missing token
    warning_found = any("GHCR_PULL_TOKEN" in r.message and r.levelname == "WARNING" for r in caplog.records)
    assert warning_found, (
        "Expected a WARNING log about missing GHCR_PULL_TOKEN, "
        f"records: {[(r.levelname, r.message) for r in caplog.records]}"
    )
    logger.critical("[IMP:9][test] φ6 precondition passed with GHCR_PULL_TOKEN warning — OK")


# endregion FUNC_test_precondition_check_registry_auth

# endregion Tests: precondition_check() — φ6 registry-auth


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
@r1_delegates
def test_precondition_check_deploy_success(
    caplog,
    state: sm.BootstrapState,
    tmp_core_dir: Path,
) -> None:
    """φ8 precondition should pass when deploy-modules.sh exists and Docker is running.

    🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
    state.precondition_check (raises RuntimeError на нарушение precondition).
    """
    # E3 (160): CORE_DIR — env-дикт (0 setenv); facts.path_isfile — реальный os.path.isfile
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Docker info output"
        mock_run.return_value.stderr = ""

        state.precondition_check(
            BootstrapPhase.DEPLOY_SERVICES,
            env={"CORE_DIR": str(tmp_core_dir)},
            facts=_FakeFacts(),
        )

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
    tmp_core_dir: Path,
) -> None:
    """φ8 precondition should fail when Docker daemon is not running."""
    # E3 (160): CORE_DIR — env-дикт (0 setenv)
    with patch("subprocess.run") as mock_run:
        # Mock docker info to fail
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "Cannot connect to the Docker daemon"

        with pytest.raises(PhasePreconditionError) as excinfo:
            state.precondition_check(
                BootstrapPhase.DEPLOY_SERVICES,
                env={"CORE_DIR": str(tmp_core_dir)},
                facts=_FakeFacts(),
            )

    assert "Docker" in str(excinfo.value), f"Error should mention Docker, got: {excinfo.value}"
    logger.critical("[IMP:9][test] φ8 precondition failed — Docker not running — OK")


# endregion FUNC_test_precondition_check_deploy_no_docker

# endregion Tests: precondition_check() — φ8 deploy-services


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
@r1_delegates
def test_precondition_check_update_phases(
    caplog,
    state: sm.BootstrapState,
    tmp_core_dir: Path,
) -> None:
    """φ9-φ13 update phases should pass precondition_check without raising errors.

    🧪 TRAP[TEST] · F1 (DevPlan 118) · @r1_delegates: fail-механизм делегирован
    state.precondition_check (raises RuntimeError на нарушение precondition).

    φ9-φ11 have no strict preconditions.
    φ12 (deploy_update) requires deploy-modules.sh + Docker.
    φ13 (converge_update) warns but does not raise.
    """
    # φ9-φ11: no preconditions — pass without any setup
    for phase in (BootstrapPhase.SECRETS_UPDATE, BootstrapPhase.NODE_CONFIG_UPDATE, BootstrapPhase.REGISTRY_UPDATE):
        state.precondition_check(phase, env={}, facts=_FakeFacts())

    # φ12: needs deploy-modules.sh + docker running (E3: CORE_DIR env-дикт, 0 setenv)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""
        state.precondition_check(
            BootstrapPhase.DEPLOY_UPDATE,
            env={"CORE_DIR": str(tmp_core_dir)},
            facts=_FakeFacts(),
        )

    # φ13: converge-update — warns but does not raise
    state.precondition_check(
        BootstrapPhase.CONVERGE_UPDATE,
        env={"CORE_DIR": str(tmp_core_dir)},
        facts=_FakeFacts(),
    )

    logger.critical("[IMP:9][test] All update phases (φ9-φ13) passed precondition_check — OK")


# endregion FUNC_test_precondition_check_update_phases

# endregion Tests: precondition_check() — φ9-φ13 update phases (no strict preconditions)


# region FUNC_test_phase_user_accounts_forced_command_dispatch
# 🧪 TRAP[TEST] · 2026-08-01 · Regression: K1 — ci-deploy forced command = orchestrator_cli dispatch (волна 117 D1)
# · Scenario: phases.py phase_user_accounts пишет ci-deploy authorized_keys с `dispatch` (не receive)
# · Last fail: K1 (two writers: phases.py receive vs setup-node.sh dispatch → receive побеждал на новых нодах)
# · Remove if: forced-command канал заменён другим CI-механизмом доставки
@ldd_trajectory
def test_phase_user_accounts_forced_command_dispatch(caplog) -> None:
    """phases (system domain) ci-deploy forced command uses orchestrator_cli dispatch (K1, AC-A1).

    DevPlan 119 E3: phases.py → phases/ пакет; phase_user_accounts живёт в phases/system.py.
    """
    phases_path = (
        Path(__file__).resolve().parent.parent.parent
        / "core"
        / "internal"
        / "bootstrap"
        / "lifecycle"
        / "phases"
        / "system.py"
    )
    content = phases_path.read_text()

    # Единственный писатель ci-deploy ключа — users.py add_ssh_key с forced-command dispatch
    assert "orchestrator_cli dispatch" in content, (
        "FAIL: phases/system.py must write 'orchestrator_cli dispatch' forced command (K1, волна 117 D1)"
    )
    assert 'command="python3 -m core.internal.deploy.orchestrator_cli receive"' not in content, (
        "FAIL: phases/system.py must NOT write 'orchestrator_cli receive' forced command "
        "(K1 — receive игнорирует SSH_ORIGINAL_COMMAND)"
    )
    logger.critical("[IMP:9][test] ci-deploy forced command = orchestrator_cli dispatch — OK")


# endregion FUNC_test_phase_user_accounts_forced_command_dispatch


# region FUNC_test_phase_user_accounts_forced_command_canonical_base
# 🧪 TRAP[TEST] · DevPlan 125 T3 (FL20) · forced-command command= использует канон platform_remote_base
# · Regression: литерал /opt/platform возвращается в command= (хардкод без единого источника)
# · Scenario: phase_user_accounts (mocks helpers_users) → add_ssh_key forced_command_prefix
# ·   содержит cd <platform_remote_base()> && PYTHONPATH=<base> + orchestrator_cli dispatch + restrict
# · Last fail: 2026-08-03 — phases/system.py:230 захардкожен '/opt/platform' без теста на строку command=
# · Remove if: forced-command канал заменён другим CI-механизмом доставки
@ldd_trajectory
def test_phase_user_accounts_forced_command_canonical_base(caplog) -> None:
    """DevPlan 125 T3: forced_command_prefix = канонический platform_remote_base (не литерал)."""
    caplog.set_level(logging.INFO)

    from core.internal.bootstrap.lifecycle.phases import system as phases_system
    from core.internal.shared.deploy_paths import platform_remote_base

    captured: dict[str, object] = {}

    def _fake_add_ssh_key(username: str, key: str, forced_command_prefix: str | None = None, *, runner=None) -> None:
        captured["username"] = username
        captured["forced_command_prefix"] = forced_command_prefix

    # DI (W-H): users_helpers= namespace (0 патчей helpers_users)
    class _FakeUsers:
        @staticmethod
        def create_user(*_a, **_k):
            return None

        add_ssh_key = staticmethod(_fake_add_ssh_key)

        @staticmethod
        def ensure_projects_base(*_a, **_k):
            return None

    # E3 (160): PLATFORM_* — env-дикт (фаза читает через env=), 0 setenv
    phases_system.phase_user_accounts(
        "/opt/platform/core",
        "test-node",
        "/opt/node-configs/test-node/node.yaml",
        env={
            "PLATFORM_OWNER_KEY": "ssh-ed25519 AAAA test-owner-key",
            "PLATFORM_CI_DEPLOY_KEY": "ssh-ed25519 AAAA test-ci-key",
        },
        users_helpers=_FakeUsers(),
    )

    assert captured["username"] == "ci-deploy", "forced-command ключ добавляется пользователю ci-deploy"
    prefix = str(captured.get("forced_command_prefix"))
    base = str(platform_remote_base())
    assert base in prefix, f"command= должен содержать канонический base {base}"
    assert f"cd {base}" in prefix, f"command= должен cd в канонический base: {prefix}"
    assert f"PYTHONPATH={base}" in prefix, f"command= должен ставить PYTHONPATH=<base>: {prefix}"
    assert "orchestrator_cli dispatch" in prefix, "forced-command = orchestrator_cli dispatch (K1/117 D1)"
    assert "restrict" in prefix, "forced-command должен содержать restrict (ssh security)"
    logger.critical("[IMP:9][test] forced-command command= использует канон platform_remote_base — OK")


# endregion FUNC_test_phase_user_accounts_forced_command_canonical_base


# ═══════════════════════════════════════════════════════════════════
# region Tests: φ3 platform_setup — Docker Hub auth (D5, D6 — DevPlan 136 W1 T1.2)
# ═══════════════════════════════════════════════════════════════════


# region FUNC_test_phase_platform_setup_runs_docker_auth_with_empty_creds
## @purpose — D5 (8327c1d): φ3 с ПУСТЫМИ DOCKER_HUB_* кредами (ТОЧНЫЙ вход бага) →
##            docker_registry_auth.py всё равно запускается (mirror конфигурируется без auth).
##            Ранее при пустых кредах скрипт пропускался целиком → mirror не настроен →
##            anonymous rate-limit (429) на первом бутстрапе (φ3 идёт до φ4 secrets).
## @io — ⇥ caplog, monkeypatch, tmp_path → ⎋ None (assert вызова runner)
## @complexity — O(1) + 1 fake
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D5 — φ3 docker auth с ПУСТЫМИ кредами (8327c1d)
# · Scenario: DOCKER_HUB_USERNAME/TOKEN удалены из env (пустые креды), docker_registry_auth.py
# ·   существует в CORE_DIR → phase_platform_setup вызывает runner.run(["python3", auth_script])
# ·   (W4d: fake-раннер DI — патч helpers_subprocess.run_subprocess удалён)
# · Last fail: 2026-08-04 — φ3 пропускал docker_registry_auth при пустых кредах → 429 на пуллах
# · Remove if: φ3 меняет стратегию auth (скрипт больше не запускается при пустых кредах)
@ldd_trajectory
def test_phase_platform_setup_runs_docker_auth_with_empty_creds(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    """D5: φ3 с пустыми кредами — docker_registry_auth.py всё равно запускается (fake-раннер)."""
    caplog.set_level(logging.INFO)

    from core.internal.bootstrap.lifecycle.phases import system as phases_system

    core_dir = tmp_path / "core"
    bootstrap_dir = core_dir / "internal" / "bootstrap"
    bootstrap_dir.mkdir(parents=True)
    (bootstrap_dir / "docker_registry_auth.py").write_text("#!/usr/bin/env python3\nprint('ok')\n")
    (core_dir / "internal" / "provision-environment.sh").write_text("#!/bin/bash\nexit 0\n")
    (bootstrap_dir / "setup-node.sh").write_text("#!/bin/bash\nexit 0\n")

    # W4d DI: fake-раннер вместо патча helpers_subprocess.run_subprocess (subprocess-канал фазы)
    # DI (W-H): sys_helpers/val_helpers namespace (0 патчей helpers)
    fake = _FakeRunner()

    class _FakeSys:
        @staticmethod
        def install_cron_metrics(*_a, **_k):
            return True

        @staticmethod
        def install_cron_watchdog(*_a, **_k):
            return True

    class _FakeVal:
        @staticmethod
        def validate_sudoers(*_a, **_k):
            return None

    ok = phases_system.phase_platform_setup(
        str(core_dir), "test-node", "node.yaml", runner=fake, sys_helpers=_FakeSys(), val_helpers=_FakeVal()
    )

    assert ok is True, "φ3 с пустыми кредами должен завершиться без non-fatal issues"
    captured = fake.calls
    auth_calls = [c for c in captured if any("docker_registry_auth.py" in str(part) for part in c)]
    assert auth_calls, f"D5 regression: docker_registry_auth.py должен запускаться с ПУСТЫМИ кредами: {captured}"
    assert auth_calls[0][0] == "python3", f"D5: скрипт запускается через python3: {auth_calls[0]}"
    logger.critical("[IMP:9][test] D5 PASS: docker_registry_auth запущен при пустых кредах")


# endregion FUNC_test_phase_platform_setup_runs_docker_auth_with_empty_creds


# region FUNC_test_docker_registry_auth_syspath_bootstrap_four_levels
## @purpose — D6 (665aad0): docker_registry_auth.py — прямой запуск (direct-script, cwd≠root) обязан
##            иметь sys.path bootstrap = корень РЕПО (4 уровня parent), а не core/ (3 уровня).
##            Статический R5 negative на точный вход бага: `python3 docker_registry_auth.py` из чужого cwd.
## @io — ⇥ caplog → ⎋ None (source-asserts)
## @complexity — O(F) где F = размер скрипта
# 🧪 TRAP[TEST] · 2026-08-05 · Regression · D6 — docker_registry_auth.py sys.path 4 уровня (665aad0)
# · Scenario: direct-script invocация — bootstrap (sys.path.insert корень репо) ДО core.internal импортов
# · Last fail: 2026-08-04 — 3 уровня вставляли core/ → `from core.internal...` ModuleNotFoundError
# · Remove if: docker_registry_auth.py перестаёт быть direct-script (entrypoint/пакетизация)
@ldd_trajectory
def test_docker_registry_auth_syspath_bootstrap_four_levels(caplog: pytest.LogCaptureFixture) -> None:
    """D6: docker_registry_auth.py self-bootstrap = корень репо (4 уровня), до core.internal импортов."""
    caplog.set_level(logging.INFO)

    auth_path = (
        Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "docker_registry_auth.py"
    )
    content = auth_path.read_text(encoding="utf-8")

    assert "parent.parent.parent.parent" in content, "D6: bootstrap обязан быть 4 уровня (корень репо)"
    assert "sys.path.insert" in content, "D6: sys.path.insert обязателен для direct-script invocации"
    lines = content.splitlines()
    bootstrap_line = next(i for i, line in enumerate(lines, 1) if "sys.path.insert" in line)
    # Первый core.internal импорт ПОСЛЕ bootstrap (докстринг-упоминания не считаются)
    import_lines = [
        i
        for i, line in enumerate(lines, 1)
        if line.lstrip().startswith(("from core.internal", "import core.internal")) and i > bootstrap_line
    ]
    assert import_lines, "D6: core.internal импорты обязаны присутствовать после bootstrap"
    assert bootstrap_line < min(import_lines), "D6: self-bootstrap обязан идти ДО core.internal импортов"
    logger.critical("[IMP:9][test] D6 PASS: docker_registry_auth.py 4-level sys.path bootstrap")


# endregion FUNC_test_docker_registry_auth_syspath_bootstrap_four_levels

# endregion Tests: φ3 platform_setup — Docker Hub auth (D5, D6 — DevPlan 136 W1 T1.2)


# region Tests: φ7 certificates — acme fail не блокирует деплой (ночная сессия 141, B4)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · phase_certificates: acme-fail при живых сертификатах (141 B4)
# · Scenario: оригинальная форма — _install_acme вернул False (git clone через tor-прокси падал,
# ·   run timeout 120s) → non_fatal_issues=True → фаза вернула False → done_with_warnings →
# ·   dependency-гейт заблокировал deploy_services → cold bootstrap FAILED (сертификаты при этом
# ·   выданы: S3 restore + wildcard *.tronyx.ru, summary failed=0).
# · Last fail: 2026-08-06 test_01_cold_start_bootstrap_9_phases (rc=2, «Phase deploy_services
# ·   requires prerequisite phase(s): certificates»)
# · Remove if: фаза certificates перестанет быть гейтом для deploy_services
@ldd_trajectory
def test_phase_certificates_acme_fail_ssl_ok_returns_true(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """B4-R5: acme.sh install fail + ssl_provision OK → фаза возвращает True (НЕ done_with_warnings)."""
    from core.internal.bootstrap.lifecycle.phases import certs as certs_mod

    caplog.set_level(logging.INFO)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("domain: tronyx.ru\nprojects: []\n")

    with (
        patch.object(certs_mod, "_install_acme", return_value=False),
        # P0 (2026-08-27): контракт статусов — успех = "provisioned"/"converged" (прежнее None
        # маскировало import-skip; см. test_ssl_provision_import_unavailable_* в contract-файле)
        patch.object(
            certs_mod.helpers_domains, "ssl_provision_via_orchestrator", return_value="provisioned"
        ) as ssl_mock,
    ):
        result = certs_mod.phase_certificates(str(tmp_path), "tronyx-vps", str(node_yaml))

    assert result is True, "B4-R5 FAIL: acme-fail при успешном ssl-provision должен давать True"
    ssl_mock.assert_called_once_with(str(tmp_path), str(node_yaml))
    messages = [r.message for r in caplog.records]
    assert any("non-fatal" in m and "acme.sh" in m for m in messages), (
        "B4-R5 FAIL: acme-fail обязан логироваться WARN (non-fatal)"
    )
    assert any("[IMP:9]" in m and "φ7 complete" in m for m in messages), (
        "B4-R5 FAIL: фаза должна завершиться IMP:9 (LDD)"
    )


# 🧪 TRAP[TEST] · NEGATIVE (R5) · phase_certificates: ssl-provision реально упал → False (141 B4)
# · Scenario: защита от обратной регрессии — если САМ deliverable (сертификаты) не выдался,
# ·   фаза обязана вернуть False (done_with_warnings → блок деплоя КОРРЕКТЕН).
# · Last fail: N/A (эталонное поведение, не регрессия)
# · Remove if: изменится контракт «деплой без сертификатов запрещён»
@ldd_trajectory
def test_phase_certificates_ssl_fail_returns_false(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """B4: ssl-provision падает → фаза возвращает False (блок деплоя без сертификатов корректен)."""
    from core.internal.bootstrap.lifecycle.phases import certs as certs_mod

    caplog.set_level(logging.INFO)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("domain: tronyx.ru\nprojects: []\n")

    with (
        patch.object(certs_mod, "_install_acme", return_value=True),
        patch.object(certs_mod.helpers_domains, "ssl_provision_via_orchestrator", side_effect=RuntimeError("no certs")),
    ):
        result = certs_mod.phase_certificates(str(tmp_path), "tronyx-vps", str(node_yaml))

    assert result is False, "B4: ssl-fail обязан давать False (done_with_warnings)"


# region Tests: φ7 certificates — P0 (2026-08-27) import-unavailable → честный статус фазы


# 🧪 TRAP[TEST] · 2026-08-27 · P0 · (a) import-unavailable + серты НЕ на диске → done_with_warnings
# · Scenario: cert_orchestrator not importable (холодный bootstrap, неполный core) + fullchain.pem
# ·   отсутствует → фаза обязана вернуть False (done_with_warnings → resume перевыполнит фазу).
# · Last fail: 2026-08-27 P0 на tronyx-vps — φ7 печатала «SSL certificates provisioned for all
# ·   domains» и mark DONE при НЕ выполненном провижининге (S3-кеш пуст = restore-first мёртв)
# · Remove if: контракт статусов ssl_provision_via_orchestrator изменится
@ldd_trajectory
def test_phase_certificates_import_unavailable_certs_missing_warnings(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch
) -> None:
    """P0 (a): import-unavailable + certs-missing → фаза False (warnings-status, НЕ done)."""
    from core.internal.bootstrap.lifecycle.helpers import domains as domains_helpers
    from core.internal.bootstrap.lifecycle.phases import certs as certs_mod
    from core.internal.shared import deploy_paths

    caplog.set_level(logging.DEBUG)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    # Импорт недоступен (guarded-import дал None) + домены известны + сертов на диске НЕТ
    monkeypatch.setattr(certs_mod, "_install_acme", lambda _core_dir: True)
    monkeypatch.setattr(domains_helpers, "orchestrate_certs", None)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", lambda _yaml, _ctx: ["example.com"])
    monkeypatch.setattr(deploy_paths, "letsencrypt_live", lambda: tmp_path)

    result = certs_mod.phase_certificates(str(tmp_path), "tronyx-vps", str(node_yaml))

    assert result is False, (
        f"P0 FAIL: import-unavailable + certs-missing обязан давать False (done_with_warnings), got {result!r}"
    )
    messages = [r.message for r in caplog.records]
    assert any("certificates NOT provisioned" in m for m in messages), (
        "P0 FAIL: фаза обязана логировать «certificates NOT provisioned» (не «provisioned»)"
    )


# 🧪 TRAP[TEST] · 2026-08-27 · P0 · (b) import-unavailable + серты НА диске → success (converged)
# · Scenario: orchestrate_certs недоступен, но acme.sh уже выдал fullchain.pem для всех доменов —
# ·   фаза True (converged), НЕ наказывает повтором issuance и не блокирует деплой.
# · Last fail: N/A (новое поведение — дисковая converged-проверка)
# · Remove if: converged-семантика изменится
@ldd_trajectory
def test_phase_certificates_import_unavailable_certs_present_success(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch
) -> None:
    """P0 (b): import-unavailable + certs-present → фаза True (converged, без повторного issue)."""
    import core.internal.shared.ssl_certs as ssl_certs_module
    from core.internal.bootstrap.lifecycle.helpers import domains as domains_helpers
    from core.internal.bootstrap.lifecycle.phases import certs as certs_mod
    from core.internal.shared import deploy_paths

    caplog.set_level(logging.DEBUG)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    # Серты уже на диске для всех доменов (LE live → tmp_path)
    (tmp_path / "example.com").mkdir()
    (tmp_path / "example.com" / "fullchain.pem").write_text("FAKE-CERT")

    monkeypatch.setattr(certs_mod, "_install_acme", lambda _core_dir: True)
    monkeypatch.setattr(domains_helpers, "orchestrate_certs", None)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", lambda _yaml, _ctx: ["example.com"])
    monkeypatch.setattr(deploy_paths, "letsencrypt_live", lambda: tmp_path)
    # F14 (DevPlan 030): converged-проверка wildcard-aware (cert_covers_domain → _cert_covers_domain);
    # FAKE-CERT не парсится openssl — мокаем single-cert primitive, чтобы on-disk покрытие подтвердилось.
    monkeypatch.setattr(ssl_certs_module, "_cert_covers_domain", lambda _c, _d: True)

    result = certs_mod.phase_certificates(str(tmp_path), "tronyx-vps", str(node_yaml))

    assert result is True, f"P0 FAIL: import-unavailable + certs-present обязан давать True (converged), got {result!r}"
    messages = [r.message for r in caplog.records]
    assert any("provisioned for all domains" in m for m in messages), (
        "P0 FAIL: converged-путь обязан логировать успех фазы (IMP:9)"
    )


# 🧪 TRAP[TEST] · 2026-08-27 · Regression (c) · импорт доступен → прежнее поведение (issuance → done)
# · Scenario: orchestrate_certs доступен и выдаёт серты → фаза True; тихий skip невозможен.
# · Last fail: N/A (эталонное поведение — регрессионный щит для нового контракта)
# · 2026-09-02 (F-10): helper маппит по счётчикам CertResult — фейк должен нести
# ·   реальные counters (issued=1), не bare to_dict().
# · Remove if: orchestration-контракт изменится
@ldd_trajectory
def test_phase_certificates_import_available_previous_behavior(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch
) -> None:
    """P0 (c): импорт доступен → прежнее поведение: issuance выполняется, фаза True."""
    from core.internal.bootstrap.cert_orchestrator import CertResult, DomainCertResult
    from core.internal.bootstrap.lifecycle.helpers import domains as domains_helpers
    from core.internal.bootstrap.lifecycle.phases import certs as certs_mod

    caplog.set_level(logging.DEBUG)
    node_yaml = tmp_path / "node.yaml"
    node_yaml.write_text("projects: []\nmodules: []\n")

    calls: list[tuple] = []

    def _fake_orchestrate(domains, issue_cert_script, secrets_env, migrate_cron=False):
        calls.append((list(domains), str(issue_cert_script)))
        result = CertResult()
        result.add(DomainCertResult(domain="example.com", status="issued"))
        return result

    monkeypatch.setattr(certs_mod, "_install_acme", lambda _core_dir: True)
    monkeypatch.setattr(domains_helpers, "orchestrate_certs", _fake_orchestrate)
    monkeypatch.setattr(domains_helpers, "extract_domains_for_context", lambda _yaml, _ctx: ["example.com"])

    result = certs_mod.phase_certificates(str(tmp_path), "tronyx-vps", str(node_yaml))

    assert result is True, f"P0 regression (c) FAIL: импорт доступен → фаза True (issuance), got {result!r}"
    assert calls and calls[0][0] == ["example.com"], (
        f"P0 regression (c) FAIL: orchestrate_certs обязан вызываться с доменами, got {calls!r}"
    )


# endregion Tests: φ7 certificates — P0 (2026-08-27) import-unavailable → честный статус фазы


# 🧪 TRAP[TEST] · NEGATIVE (R5) · _install_acme: proxy-чистая env (141 B4)
# · Scenario: оригинальная форма — install-acme.sh наследовал HTTP_PROXY/HTTPS_PROXY из env
# ·   cli.py (source_secrets_env) → git clone через privoxy→tor → падение/timeout. Скрипт
# ·   документирует «Proxy vars are expected to be clean» — фикс: subprocess env без прокси.
# · Last fail: 2026-08-06 — install-acme.sh exit=1 (clone через tor)
# · Remove if: install-acme.sh перестанет требовать чистую env (или уберёт git clone)
def test_install_acme_runs_with_clean_proxy_env(tmp_path: Path) -> None:
    """B4: _install_acme передаёт subprocess env БЕЗ proxy-переменных."""
    from core.internal.bootstrap.lifecycle.phases import certs as certs_mod

    captured: dict = {}

    def _fake_run(cmd, capture_output, text, timeout, env, **kwargs):
        captured["env"] = env
        captured["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": ""})()

    with (
        patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://127.0.0.1:8118", "HTTPS_PROXY": "http://127.0.0.1:8118"},
            clear=False,
        ),
        patch.object(certs_mod.subprocess, "run", side_effect=_fake_run),
    ):
        script = tmp_path / "internal" / "bootstrap" / "install-acme.sh"
        script.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env bash\n")
        ok = certs_mod._install_acme(str(tmp_path))

    assert ok is True
    assert "HTTP_PROXY" not in captured["env"], "B4-R5 FAIL: HTTP_PROXY не вычищен из env subprocess"
    assert "HTTPS_PROXY" not in captured["env"], "B4-R5 FAIL: HTTPS_PROXY не вычищен из env subprocess"
    assert captured["cmd"] == ["bash", str(script)]


# endregion Tests: φ7 certificates — acme fail не блокирует деплой (ночная сессия 141, B4)


# region FUNC_test_registry_update_firewall_reapply
## 🧪 TRAP[TEST] · 142 B34 (R5) · φ11 registry_update re-applies firewall.sh при TOR_ENABLED
## · Regression: ufw-правило tor-privoxy 8118 применялось ТОЛЬКО в φ1 (init); после фикса B30
## ·   (Bad port syntax) правило на живой ноде не появлялось без полного bootstrap; update-режим
## ·   firewall не трогал → C6 (grafana→telegram через docker-мост) оставался мёртвым.
## · Last fail: 2026-08-07 (node-update --force, ufw без 8118, grafana notify connection refused)
## · Remove if: firewall re-apply уберётся из φ11
def test_registry_update_firewall_reapply(tmp_path, caplog) -> None:
    """φ11: при TOR_ENABLED=true вызывает firewall.sh (incremental re-apply, non-fatal)."""
    import core.internal.bootstrap.lifecycle.phases.docker as docker_phases

    caplog.set_level(logging.INFO)
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "internal" / "bootstrap").mkdir(parents=True)
    firewall_script = core_dir / "internal" / "bootstrap" / "firewall.sh"
    firewall_script.write_text("#!/bin/bash\nexit 0\n")

    # E3 (160): runner/env — DI-параметры (0 monkeypatch subprocess_io.run_subprocess / os.environ)
    fake = _FakeRunner()
    result = docker_phases._registry_step_firewall(
        str(core_dir),
        runner=fake,
        env={"TOR_ENABLED": "true"},
    )
    assert result is False, "firewall re-apply не должен считаться non-fatal issue"
    assert any("firewall.sh" in " ".join(c) for c in fake.calls), f"firewall.sh должен вызываться: {fake.calls}"
    logger.info("[IMP:9][test] B34 verified: φ11 re-applies firewall.sh (TOR_ENABLED=true)")


# endregion FUNC_test_registry_update_firewall_reapply
