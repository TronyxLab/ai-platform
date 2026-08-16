# GREP_SUMMARY: e2e-conftest, requires-node, node-ssh, node-state, test-vps-fresh, node-preflight, goloty, NODE_PREBOOTSTRAPPED, test-project, R4, session-fixtures, cold-start
# STRUCTURE: ▶ node_preflight (SSH probe + «голота» + NODE_PREBOOTSTRAPPED gate) → ◇ requires_node (R4 FAIL) → ◇ node_ssh (session) → ◇ node_state (session) → ◇ test_vps_fresh (session autouse: pre-flight → reset state.json) → ◇ test_project_fixture → ⎋
# region MODULE_CONTRACT
## @purpose  DevPlan 095 T4 fixtures for E2E bootstrap pipeline tests (tests/e2e/).
##           node_preflight (DevPlan 136 W6 T6.2 — session autouse «голоты» pre-flight:
##           SSH reachability + bare-node check with NODE_PREBOOTSTRAPPED operator gate),
##           requires_node (R4 enforcement: FAIL not skip), node_ssh (session-scoped
##           NodeSSHClient), node_state (session-scoped NodeState), test_vps_fresh
##           (session-scoped autouse: cold-start state reset before the suite),
##           test_project_fixture (canonical test-project name).
## @scope    Tests/e2e only — loaded by pytest for this directory (package marker required).
## @invariants
##   - requires_node: NODE env missing → pytest.fail (Rule R4), NEVER pytest.skip
##   - node_ssh/node_state are SESSION-scoped (SSH connection reuse, one cold start per suite)
##   - node_preflight is SESSION-scoped AUTUSE and runs BEFORE test_vps_fresh (dependency):
##     SSH reachability probe (R4: FAIL not skip) + «голота» check. Docker/platform present
##     passes ONLY with NODE_PREBOOTSTRAPPED=1 (operator SC2 confirmation, штатный сценарий
##     W6.5→W6.6); present without the env → pytest.fail («нода не пересоздана или забыт env»).
##   - Chaos sessions (marker chaos among SELECTED items) SKIP the goloty check — chaos
##     targets a BOOTSTRAPPED node (tronyx-vps, 126-chaos-resilience), not a bare one;
##     the SSH probe still runs.
##   - test_vps_fresh is SESSION-scoped AUTUSE: resets state.json once before the suite.
##     Node RECREATION is an OPERATOR procedure (SC2, invariant 9) — NOT an auto-reset:
##     test_vps_fresh only rm state.json (cold-start reset for the suite), it NEVER
##     recreates the VPS.
##   - Fixture lifecycle: session-scoped SSH fixtures assume the test-VPS stays up for the
##     whole suite; a mid-suite VPS reboot fails tests loudly (R4), never silently skips.
## @rationale DevPlan 095 §4.1: session-scoped cold start (1× ~10min) + 11 incremental tests
##            (~5min each) vs function-scoped reset (11×600s = 2+ hours).
##            DevPlan 136 W6 T6.2 (B3+): pre-flight «голоты» делает нарушение инварианта 9
##            видимым ДО suite — нода, не пересозданная по SC2, даёт drifted cold-start
##            (баг-класс 135). Пересоздание — операторская процедура SC2, не автосброс.
## ⚠️ TRAP[DECISION] · 2026-07-31 · HI · Cold-start reset: rm state.json, не `make bootstrap-node --force`
## · Rejected: `make bootstrap-node NODE=X --force` (DevPlan §4.1 sketch) — make трактует
##   `--force` как target ("No rule to make target '--force'") — механически сломанная команда.
## · Reason: rm /var/lib/platform/.bootstrap/state.json — документированный операторский сброс
##   (core/internal/bootstrap/AGENTS.md «Сброс»), эквивалент state_machine --force (Clearing state).
##   Первый тест (test_cold_start_bootstrap_9_phases) выполняет полный bootstrap (~10min).
## · Rev: если make получит штатный FORCE=1 флаг — переключить reset_state() на него.
## 🧐 TRAP[DECISION] · 2026-08-05 · HI · Pre-flight «голоты»: gate NODE_PREBOOTSTRAPPED вместо bare-assert
## · Rejected: безусловный assert «docker absent» (DevPlan 136 W6 T6.2 sketch) — операционный
##   поток W6.5→W6.6 (bootstrap-node уже выполнен на пересозданной ноде → docker present —
##   штатный сценарий) дал бы ложный FAIL на легитимной пересозданной ноде
## · Reason: NODE_PREBOOTSTRAPPED=1 — явное операторское подтверждение SC2 (нода пересоздана),
##   после которого docker/platform present трактуется как ожидаемый результат W6.5, а не дрейф 135.
##   Без env + presence → FAIL («нода не пересоздана (инвариант 9) или забыт NODE_PREBOOTSTRAPPED=1»).
## · Rev: если появится авто-пересоздание ноды (API VPS) — заменить операторский env на
##   автоматическую проверку возраста ноды / идентификатора пересоздания.
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import pathlib

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
    with pathlib.Path(node_yaml).open(encoding="utf-8") as f:
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


# region HELPER_evaluate_goloty
def _evaluate_goloty(has_docker: bool, has_platform: bool, operator_confirmed: bool) -> str | None:
    """Decide the pre-flight «голоты» verdict: None = PASS, str = FAIL reason (Rule R4).

    ## @purpose — Чистое решение «голоты» без I/O (DRY, by-inspection testable):
    ##            bare node → PASS; docker/platform present + operator SC2 confirmation
    ##            (NODE_PREBOOTSTRAPPED=1) → PASS; present without confirmation → FAIL
    ##            (drifted node — баг-класс 135, инвариант 9).
    ## @io — ⇥ has_docker: bool, has_platform: bool, operator_confirmed: bool → ⎋ None | str
    ## @complexity — O(1)
    ## @invariants
    ##   - bare node (no docker AND no /opt/platform) ALWAYS passes — cold-start precondition
    ##   - operator_confirmed (NODE_PREBOOTSTRAPPED=1) bypasses the presence check —
    ##     штатный сценарий W6.5→W6.6 (node recreated per SC2, bootstrap already run)
    ##   - presence without confirmation → FAIL (drifted node, not recreated per invariant 9)
    """
    if not has_docker and not has_platform:
        return None
    if operator_confirmed:
        return None
    return "нода не пересоздана (инвариант 9) или забыт NODE_PREBOOTSTRAPPED=1"


# endregion HELPER_evaluate_goloty


# region FIXTURE_node_preflight
@pytest.fixture(scope="session", autouse=True)
def node_preflight(request: pytest.FixtureRequest) -> None:
    """Pre-flight «голоты» (DevPlan 136 W6 T6.2, B3+): before the suite verify the test-VPS
    is SSH-reachable and in the expected state — bare (no docker/platform) OR operator-confirmed
    recreated (NODE_PREBOOTSTRAPPED=1). Chaos sessions skip the goloty check (bootstrapped node).

    ## @purpose — Fail-fast gate before any bootstrap-pipeline test: a node NOT recreated per
    ##            SC2 (invariant 9) would run the cold-start suite against drifted state —
    ##            the exact bug class of 135. Runs once per session, before test_vps_fresh
    ##            (test_vps_fresh depends on this fixture for ordering).
    ## @io — ⇥ None → ⎋ None | pytest.fail
    ## @complexity — O(1) — single SSH round-trip (combined reachability + goloty probe)
    ## @invariants
    ##   - NODE env absent → no-op (local integration tests, no VPS)
    ##   - SSH unreachable → pytest.fail (R4: NO_SERVICE = FAIL, not skip)
    ##   - Chaos session (marker chaos among SELECTED items — request.session.items) → SKIP
    ##     the goloty check: chaos targets a BOOTSTRAPPED node (tronyx-vps per 126), not a
    ##     bare one; the SSH probe still runs
    ##   - bare node (docker + /opt/platform absent) → PASS — cold-start precondition
    ##   - docker/platform present AND NODE_PREBOOTSTRAPPED=1 → PASS (operator SC2-confirmed
    ##     recreated node; bootstrap W6.5 already run — штатный сценарий W6.5→W6.6)
    ##   - docker/platform present AND NODE_PREBOOTSTRAPPED unset → pytest.fail
    ## @rationale DevPlan 136 §3 Data Flow: pre-flight (W6) — нода пересоздана оператором SC2
    ##            (инвариант 9). Пересоздание — операторская процедура SC2, НЕ автосброс:
    ##            test_vps_fresh сбрасывает только state.json (cold-start для suite), не
    ##            пересоздаёт ноду. Гейт NODE_PREBOOTSTRAPPED — см. TRAP[DECISION] в контракте.
    """
    if not os.environ.get("NODE"):
        logger.info("[IMP:7][fixture][node_preflight] NODE not set — skip pre-flight (local e2e tests)")
        return
    node = _require_node_env()
    host = _resolve_test_vps_host()
    user = os.environ.get("SSH_USER", "root")
    ssh = NodeSSHClient(host=host, user=user)
    logger.info("[IMP:9][fixture][node_preflight] Pre-flight start Node=%s host=%s user=%s", node, host, user)

    # 1. SSH reachability + goloty probe — one round-trip (R4: FAIL not skip on unreachable)
    probe = ssh.ssh_read(
        "uname -s; "
        "command -v docker >/dev/null 2>&1 && echo DOCKER=present || echo DOCKER=absent; "
        "test -d /opt/platform && echo PLATFORM=present || echo PLATFORM=absent; "
        "echo PROBE_OK",
        timeout=30,
    )
    if probe.exit_code != 0 or "PROBE_OK" not in probe.stdout:
        pytest.fail(
            f"Pre-flight FAIL: test-VPS {node} ({host}) недоступна по SSH (exit={probe.exit_code}): "
            f"{probe.stderr.strip()[:200]} — проверьте доступность ноды / SSH_KEY / firewall 22. "
            "Per Rule R4: NO_SERVICE = FAIL, not skip.",
            pytrace=False,
        )
    has_docker = "DOCKER=present" in probe.stdout
    has_platform = "PLATFORM=present" in probe.stdout

    # 2. Chaos session — goloty check is N/A (chaos needs a BOOTSTRAPPED node)
    chaos_session = any(item.get_closest_marker("chaos") is not None for item in request.session.items)
    if chaos_session:
        logger.info(
            "[IMP:9][fixture][node_preflight] Chaos session (marker chaos) — goloty check skipped "
            "(chaos targets a BOOTSTRAPPED node, tronyx-vps per 126); SSH probe OK"
        )
        return

    # 3. «Голота» gate (bare node | NODE_PREBOOTSTRAPPED=1 | FAIL)
    operator_confirmed = os.environ.get("NODE_PREBOOTSTRAPPED", "").strip() == "1"
    verdict = _evaluate_goloty(has_docker, has_platform, operator_confirmed)
    if verdict is None:
        if operator_confirmed:
            logger.info(
                "[IMP:9][fixture][node_preflight] docker/platform present + NODE_PREBOOTSTRAPPED=1 "
                "(SC2-confirmed recreated node, bootstrap already run) — PASS"
            )
        else:
            logger.info(
                "[IMP:9][fixture][node_preflight] Bare node confirmed (docker absent, /opt/platform absent) — "
                "cold-start precondition PASS"
            )
        return
    pytest.fail(
        f"Pre-flight FAIL: test-VPS {node} ({host}) уже содержит docker/platform "
        f"(docker={'present' if has_docker else 'absent'}, "
        f"/opt/platform={'present' if has_platform else 'absent'}) — {verdict}. "
        "Пересоздайте VPS по процедуре SC2 (оператор) и запустите bootstrap-node (W6.5), "
        "либо подтвердите пересоздание: export NODE_PREBOOTSTRAPPED=1",
        pytrace=False,
    )


# endregion FIXTURE_node_preflight


# region FIXTURE_test_vps_fresh
@pytest.fixture(scope="session", autouse=True)
def test_vps_fresh(node_preflight) -> None:
    """Reset test-VPS to clean state before the E2E suite (session-scoped autouse).

    ## @purpose — Cold start per AGENTS.md invariant 9 (recreatable test-VPS).
    ##            Resets state.json → the first test performs the full 9-INIT-phase
    ##            bootstrap; subsequent tests run incrementally (DevPlan §4.1 DD3).
    ##            Runs AFTER node_preflight (dependency — «голота» verified before reset).
    ##            ⚠️ DevPlan 133: НЕ autouse-зависит от node_state — локальные
    ##            integration-тесты (tests/e2e/test_shared_db_access.py, маркер
    ##            integration, без NODE env) не должны тянуть VPS-фикстуры.
    ##            R4 сохраняется: VPS-тесты фейлятся через requires_node fixture.
    ##            ⚠️ DevPlan 136 W6 T6.2 (B3+): пересоздание ноды — ОПЕРАТОРСКАЯ процедура
    ##            SC2 (инвариант 9), НЕ автосброс. Этот фикстур НЕ пересоздаёт VPS —
    ##            только rm state.json (cold-start сброс для suite). «Голоту» ноды
    ##            гарантирует node_preflight (SSH + docker/platform + NODE_PREBOOTSTRAPPED gate).
    ## @io — ⇥ node_preflight (ordering dependency) → ⎋ None (side-effect: state.json removed on the VPS)
    ## @complexity — O(1) — single SSH rm
    ## @invariants
    ##   - Runs EXACTLY ONCE per pytest session (session scope)
    ##   - NODE env отсутствует → reset пропускается (локальный стек, не VPS)
    ##   - Failure to reset → suite fails loudly (R4), not skip
    ##   - Does NOT touch running containers or docker state — state.json only
    ##   - Does NOT recreate the VPS — recreation is operator procedure SC2, not auto-reset
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
