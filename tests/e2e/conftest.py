# GREP_SUMMARY: e2e-conftest, requires-node, node-ssh, node-state, test-vps-fresh, test-project, R4, session-fixtures, cold-start
# STRUCTURE: ▶ requires_node (R4 FAIL) → ◇ node_ssh (session) → ◇ node_state (session) → ◇ test_vps_fresh (session autouse: reset state.json) → ◇ test_project_fixture → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 095 T4 fixtures for E2E bootstrap pipeline tests (tests/e2e/).
##           requires_node (R4 enforcement: FAIL not skip), node_ssh (session-scoped
##           NodeSSHClient), node_state (session-scoped NodeState), test_vps_fresh
##           (session-scoped autouse: cold-start state reset before the suite),
##           test_project_fixture (canonical test-project name).
## @scope    Tests/e2e only — loaded by pytest for this directory (package marker required).
## @invariants
##   - requires_node: NODE env missing → pytest.fail (Rule R4), NEVER pytest.skip
##   - node_ssh/node_state are SESSION-scoped (SSH connection reuse, one cold start per suite)
##   - test_vps_fresh is SESSION-scoped AUTUSE: resets state.json once before the suite —
##     per AGENTS.md invariant 9 (recreatable test-VPS, cold start only)
##   - Fixture lifecycle: session-scoped SSH fixtures assume the test-VPS stays up for the
##     whole suite; a mid-suite VPS reboot fails tests loudly (R4), never silently skips.
## @rationale DevPlan 095 §4.1: session-scoped cold start (1× ~10min) + 11 incremental tests
##            (~5min each) vs function-scoped reset (11×600s = 2+ hours).
## ⚠️ TRAP[DECISION] · 2026-07-31 · HI · Cold-start reset: rm state.json, не `make bootstrap-node --force`
## · Rejected: `make bootstrap-node NODE=X --force` (DevPlan §4.1 sketch) — make трактует
##   `--force` как target ("No rule to make target '--force'") — механически сломанная команда.
## · Reason: rm /var/lib/platform/.bootstrap/state.json — документированный операторский сброс
##   (core/internal/bootstrap/AGENTS.md «Сброс»), эквивалент state_machine --force (Clearing state).
##   Первый тест (test_cold_start_bootstrap_9_phases) выполняет полный bootstrap (~10min).
## · Rev: если make получит штатный FORCE=1 флаг — переключить reset_state() на него.
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os

import pytest

from tests._conftest.node import NodeSSHClient, NodeState, _require_node_env
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_TEST_PROJECT_NAME = "test-project"


# region HELPER_resolve_test_vps_host
def _resolve_test_vps_host() -> str:
    """Resolve test-VPS host from node-configs/<NODE>/node.yaml (FAIL if unresolvable, Rule R4).

    ## @purpose — Единая точка резолва VPS-хоста для node_ssh и test_vps_fresh (DRY,
    ##            DevPlan 133: test_vps_fresh больше не зависит от node_ssh — локальные
    ##            integration-тесты без NODE не тянут VPS-фикстуры).
    ## @io — ⇥ None → ⎋ str (host) | pytest.fail
    ## @complexity — O(1) — single YAML read
    """
    import yaml

    node = _require_node_env()
    node_yaml = repo_root() / "node-configs" / node / "node.yaml"
    if not node_yaml.is_file():
        pytest.fail(
            f"node-configs/{node}/node.yaml not found at {node_yaml}. "
            "Create it per DevPlan 095 T5 (node.name, node.host, node.owner_key).",
            pytrace=False,
        )
    with open(node_yaml) as f:
        data = yaml.safe_load(f) or {}
    host = (data.get("node") or {}).get("host", "")
    if not host:
        pytest.fail(
            f"node.host missing in {node_yaml} — set the test-VPS host/IP (operator action).",
            pytrace=False,
        )
    return host


# endregion HELPER_resolve_test_vps_host


# region FIXTURE_requires_node
@pytest.fixture
def requires_node() -> str:
    """Return NODE name. FAIL (not skip) if NODE env not set (Rule R4).

    ## @purpose — Node fixture: injects NODE name into every E2E test and enforces R4.
    ##            Per DevPlan 095 §2.1/§IMPLEMENTATION NOTE: EVERY test must ALSO have
    ##            @pytest.mark.requires_node (selection) AND this fixture (NODE injection).
    ## @io — ⇥ None → ⎋ str (NODE name) | pytest.fail
    ## @complexity — O(1)
    ## @invariants
    ##   - function scope per DevPlan §4.1 table
    ##   - pytest.fail, never pytest.skip (Rule R4)
    """
    node = _require_node_env()
    logger.info("[IMP:9][fixture][requires_node] NODE=%s", node)
    return node


# endregion FIXTURE_requires_node


# region FIXTURE_node_ssh
@pytest.fixture(scope="session")
def node_ssh() -> NodeSSHClient:
    """Session-scoped SSH client to the test-VPS (host resolved from node-configs/<NODE>/node.yaml).

    ## @purpose — Single SSH connection pool for the whole suite (DevPlan §4.1: session scope).
    ##            Host/user resolution: node-configs/<NODE>/node.yaml (NodeYaml.resolve Path 1),
    ##            SSH_USER env override (default root).
    ## @io — ⇥ None → ⎋ NodeSSHClient
    ## @complexity — O(1)
    ## @invariants
    ##   - NODE missing → pytest.fail (R4) — session fixtures cannot depend on the
    ##     function-scoped requires_node fixture, so the check is duplicated here
    ##   - node.yaml must exist at {repo_root}/node-configs/<NODE>/node.yaml (DevPlan 095 T5)
    """
    node = _require_node_env()
    host = _resolve_test_vps_host()
    user = os.environ.get("SSH_USER", "root")
    logger.info("[IMP:9][fixture][node_ssh] Node=%s host=%s user=%s", node, host, user)
    return NodeSSHClient(host=host, user=user)


# endregion FIXTURE_node_ssh


# region FIXTURE_node_state
@pytest.fixture(scope="session")
def node_state(node_ssh: NodeSSHClient) -> NodeState:
    """Session-scoped state.json reader/resetter for the test-VPS."""
    logger.info("[IMP:8][fixture][node_state] State file: /var/lib/platform/.bootstrap/state.json")
    return NodeState(node_ssh)


# endregion FIXTURE_node_state


# region FIXTURE_test_vps_fresh
@pytest.fixture(scope="session", autouse=True)
def test_vps_fresh() -> None:
    """Reset test-VPS to clean state before the E2E suite (session-scoped autouse).

    ## @purpose — Cold start per AGENTS.md invariant 9 (recreatable test-VPS).
    ##            Resets state.json → the first test performs the full 9-INIT-phase
    ##            bootstrap; subsequent tests run incrementally (DevPlan §4.1 DD3).
    ##            ⚠️ DevPlan 133: НЕ autouse-зависит от node_state — локальные
    ##            integration-тесты (tests/e2e/test_shared_db_access.py, маркер
    ##            integration, без NODE env) не должны тянуть VPS-фикстуры.
    ##            R4 сохраняется: VPS-тесты фейлятся через requires_node fixture.
    ## @io — ⇥ None → ⎋ None (side-effect: state.json removed on the VPS)
    ## @complexity — O(1) — single SSH rm
    ## @invariants
    ##   - Runs EXACTLY ONCE per pytest session (session scope)
    ##   - NODE env отсутствует → reset пропускается (локальный стек, не VPS)
    ##   - Failure to reset → suite fails loudly (R4), not skip
    ##   - Does NOT touch running containers or docker state — state.json only
    ## @rationale DevPlan §4.1: 1 cold start (~10min) + 11 incremental tests (~5min each)
    ##            ≈ 1h total vs function-scoped reset ≈ 2h+. State leak between tests is
    ##            mitigated by test_pipeline_idempotent_rebootstrap (T13) and per-test
    ##            reset_phase in failure scenarios (T14). See TRAP[DECISION] in module
    ##            contract: reset via rm state.json (documented operator reset), not
    ##            the mechanically-broken `make bootstrap-node --force`.
    """
    if not os.environ.get("NODE"):
        logger.info("[IMP:7][fixture][test_vps_fresh] NODE not set — skip VPS state reset (local e2e tests)")
        return
    logger.info("[IMP:9][fixture][test_vps_fresh] Resetting state.json before E2E suite (cold start)")
    _require_node_env()  # NODE guaranteed by the guard above (R4 для VPS-тестов)
    node_state = NodeState(NodeSSHClient(host=_resolve_test_vps_host(), user=os.environ.get("SSH_USER", "root")))
    result = node_state.reset_state(timeout=60)
    assert result.exit_code == 0, f"Fresh state reset failed: {result.stderr}"


# endregion FIXTURE_test_vps_fresh


# region FIXTURE_test_project_fixture
@pytest.fixture(scope="session")
def test_project_fixture() -> str:
    """Canonical test-project name (deterministic per Anti-Loop Note — 0 parameterized)."""
    logger.info("[IMP:8][fixture][test_project_fixture] project=%s", _TEST_PROJECT_NAME)
    return _TEST_PROJECT_NAME


# endregion FIXTURE_test_project_fixture


# region FIXTURE_fixture_dir
@pytest.fixture(scope="session")
def test_project_dir() -> str:
    """Absolute path to the canonical test-project fixture directory.

    ## @purpose — tests/e2e/fixtures/test-project/ — source for payload tar assembly (T9/T16).
    ## @io — ⇥ None → ⎋ str (absolute path)
    ## @complexity — O(1)
    """
    return str(repo_root() / "tests" / "e2e" / "fixtures" / "test-project")


# endregion FIXTURE_fixture_dir
