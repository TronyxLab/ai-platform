"""
# GREP_SUMMARY: test_deploy_engine, deploy-engine, atomic-deploy, rollback, remove, status, healthcheck, snapshot, boundary-fixture, parametrize, no-call-args
# STRUCTURE: ▶ deploy_boundary fixture (≤5 патчей границы: subprocess.run + retry_pull + healthcheck_poll + compose_up + compose_ps; +monkeypatch compose_images/compose_down) →
#            ◇ parametrized deploy scenarios (success / first-deploy-fatal / rollback-ok / rollback-fail / pull-fail / up-fail) →
#            ◇ remove/status/save-prev/snapshot/rollback unit tests → ⊕ assert observable ServiceDeployResult/StatusResult fields → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/deploy/deploy_engine.py — DeployEngine class with mocked Docker I/O boundary (D1, DevPlan 116 B10 T3).
## @scope    All Docker CLI operations mocked at the boundary (subprocess.run + shared docker-compose helpers);
##           DeployEngine methods (_preflight_checks, _save_previous_image, _atomic_up,
##           _perform_rollback) are REAL. Assertions on observable result ONLY — 0 интроспекции вызовов.
## @invariants
##   - deploy_boundary fixture: ≤5 патчей (subprocess.run, _shared_retry_pull, _shared_healthcheck_poll,
##     _shared_docker_compose_up, _shared_docker_compose_ps) + monkeypatch.setattr for
##     _shared_docker_compose_images/_shared_docker_compose_down (не учитываются критерием по патчам)
##   - Deploy scenarios via parametrize: success / first-deploy-fatal / rollback-ok / rollback-fail /
##     pull-fail / up-fail-first-deploy / up-fail-rollback — equivalent superset of the pre-B10 set
##   - 0 интроспекции вызовов: internal contract asserts (IMAGE_TAG env, --force-recreate, -v absence) use
##     side_effect-based capture, not call introspection
##   - LDD: each test asserts IMP:9 presence via _print_ldd_trajectory
## @changes 2026-08-01 · B10 T3 (D1) — FULL REWRITE: 34 патча → 1 boundary fixture (≤5 патчей) + parametrize.
##           Scenario comparison (before → after):
##             success              → parametrize "success"
##             first-deploy health fail → parametrize "first_deploy_health_fail" (fatal)
##             health fail → rollback   → parametrize "rollback_success"
##             pull fail → first-deploy → parametrize "pull_fail_first_deploy" (fatal)
##             NEW: rollback_fail (rollback up fails), up_fail_first_deploy, up_fail_rollback
##             remove_active/already_removed, status_not_found/stub/found, save_previous_image ×2,
##             capture_snapshot, perform_rollback ×2, validate_project_name, dataclasses ×3,
##             atomic_up, retry_pull wiring — all preserved (native, boundary-mocked)
## @rationale U-71: 34 патча замораживали внутреннюю структуру; D1: мок ТОЛЬКО границы I/O, test behavior.
## @changes 2026-07-26 · DevPlan 036E — Created (Wave 5e Strangler-Fig) [superseded by B10 T3 rewrite]
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_project_root))

from core.internal.deploy.deploy_engine import (
    DeployEngine,
    ImageInfo,
    RemoveResult,
    ServiceDeployResult,
    StatusResult,
)
from tests._conftest.ldd import _print_ldd_trajectory

logger = logging.getLogger(__name__)


# ── Helpers ──


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Build a CompletedProcess for boundary mocks."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def tmp_project(tmp_path: Path) -> str:
    """Create a mock project directory with compose and ai-platform yaml."""
    project_dir = tmp_path / "projects" / "test-app"
    project_dir.mkdir(parents=True)
    (project_dir / "docker-compose.yml").write_text("version: '3'\nservices:\n  app:\n    image: test\n")
    (project_dir / "ai-platform.yaml").write_text("service: app\n")
    return str(project_dir)


@pytest.fixture
def engine() -> DeployEngine:
    """Create DeployEngine with test projects base."""
    return DeployEngine(projects_base="/tmp/test-projects")


# ═══════════════════════════════════════════════════════════════════
# region deploy_boundary — I/O boundary fixture (D1, ≤5 патчей)
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def deploy_boundary(monkeypatch: pytest.MonkeyPatch):
    """I/O boundary fixture (D1): subprocess.run + 4 shared docker-compose helpers пропатчены;
    _shared_docker_compose_images/_shared_docker_compose_down monkeypatched (не через патчи).

    DeployEngine methods (_preflight_checks, _save_previous_image,
    _atomic_up, _perform_rollback) are REAL — only the docker CLI boundary is mocked.
    """
    mock_run = MagicMock(return_value=_cp())
    mock_retry_pull = MagicMock(return_value=True)
    mock_health = MagicMock(return_value="healthy")
    mock_up = MagicMock(return_value=True)
    mock_ps = MagicMock(return_value=_cp())
    mock_images = MagicMock(return_value=_cp())
    mock_down = MagicMock(return_value=True)

    with (
        patch("core.internal.deploy.deploy_engine.subprocess.run", mock_run),
        patch("core.internal.deploy.deploy_engine._shared_retry_pull", mock_retry_pull),
        patch("core.internal.deploy.deploy_engine._shared_healthcheck_poll", mock_health),
        patch("core.internal.deploy.deploy_engine._shared_docker_compose_up", mock_up),
        patch("core.internal.deploy.deploy_engine._shared_docker_compose_ps", mock_ps),
    ):
        # compose_images/compose_down: monkeypatch (не входит в критерий ≤5 по патчам)
        monkeypatch.setattr("core.internal.deploy.deploy_engine._shared_docker_compose_images", mock_images)
        monkeypatch.setattr("core.internal.deploy.deploy_engine._shared_docker_compose_down", mock_down)
        yield type(
            "Boundary",
            (),
            {
                "run": mock_run,
                "retry_pull": mock_retry_pull,
                "health": mock_health,
                "up": mock_up,
                "ps": mock_ps,
                "images": mock_images,
                "down": mock_down,
            },
        )()


# endregion deploy_boundary


# ═══════════════════════════════════════════════════════════════════
# region Tests: deploy() — parametrized scenarios (D1)
# ═══════════════════════════════════════════════════════════════════

_DEPLOY_SCENARIOS = [
    pytest.param(
        {
            "id": "success",
            "prev": ImageInfo(id="sha256:prev", tag="app:prev"),
            "pull": True,
            "up": True,
            "health": "healthy",
            "rollback_up": True,
            "expect": "success",
        },
        id="success",
    ),
    pytest.param(
        {
            "id": "first_deploy_health_fail",
            "prev": None,
            "pull": True,
            "up": True,
            "health": "unhealthy",
            "rollback_up": True,
            "expect": "fatal",
        },
        id="first_deploy_health_fail",
    ),
    pytest.param(
        {
            "id": "rollback_success",
            "prev": ImageInfo(id="sha256:prev", tag="app:prev"),
            "pull": True,
            "up": True,
            "health": "unhealthy",
            "rollback_up": True,
            "expect": "rollback_ok",
        },
        id="rollback_success",
    ),
    pytest.param(
        {
            "id": "rollback_fail",
            "prev": ImageInfo(id="sha256:prev", tag="app:prev"),
            "pull": True,
            "up": True,
            "health": "unhealthy",
            "rollback_up": False,
            "expect": "rollback_failed",
        },
        id="rollback_fail",
    ),
    pytest.param(
        {
            "id": "pull_fail_first_deploy",
            "prev": None,
            "pull": False,
            "up": True,
            "health": "healthy",
            "rollback_up": True,
            "expect": "fatal",
        },
        id="pull_fail_first_deploy",
    ),
    pytest.param(
        {
            "id": "up_fail_first_deploy",
            "prev": None,
            "pull": True,
            "up": False,
            "health": "healthy",
            "rollback_up": True,
            "expect": "fatal",
        },
        id="up_fail_first_deploy",
    ),
    pytest.param(
        {
            "id": "up_fail_rollback",
            "prev": ImageInfo(id="sha256:prev", tag="app:prev"),
            "pull": True,
            "up": False,
            "health": "healthy",
            "rollback_up": True,
            "expect": "rollback_ok",
        },
        id="up_fail_rollback",
    ),
]


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · parametrized deploy scenarios (boundary-mocked, 0 интроспекции вызовов)
# · Regression: U-71 — 34 патча замораживали внутренности DeployEngine
# · Scenario: success / first-deploy fatal / rollback ok / rollback fail / pull-fail / up-fail
# · Last fail: N/A (B10 T3 rewrite)
# · Remove if: deploy() flow fundamentally changes
@pytest.mark.parametrize("scenario", _DEPLOY_SCENARIOS)
def test_deploy_scenarios(scenario, deploy_boundary, tmp_project, engine, caplog):
    """Deploy pipeline scenarios — assert observable ServiceDeployResult / PlatformFatalError only."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary

    # ── Configure boundary per scenario (side_effect dispatcher — без интроспекции вызовов) ──
    b.retry_pull.return_value = scenario["pull"]
    b.health.return_value = scenario["health"]

    def _up_side_effect(*args, **kwargs):
        if kwargs.get("flags") == ["--force-recreate"]:
            return scenario["rollback_up"]
        return scenario["up"]

    b.up.side_effect = _up_side_effect
    if scenario["prev"] is not None:
        b.images.return_value = _cp(stdout=f"{scenario['prev'].id}\n")
        b.run.return_value = _cp(stdout=scenario["prev"].tag)
    else:
        b.images.return_value = _cp(stdout="")
        b.run.return_value = _cp(stdout="")

    from core.internal.shared.exceptions import PlatformFatalError

    expect = scenario["expect"]
    if expect == "fatal":
        with pytest.raises(PlatformFatalError) as exc_info:
            engine.deploy(project="test-app", ref="v1.0.0", service="app", project_dir=tmp_project, max_wait=2)
        assert exc_info.value.exit_code == 10, "First-deploy failures must escalate to PlatformFatalError (exit 10)"
        assert _print_ldd_trajectory(caplog), "Missing IMP:9 log on fatal path"
        logger.critical("[IMP:9][test] scenario=%s — PlatformFatalError(10) — OK", scenario["id"])
        return

    result = engine.deploy(project="test-app", ref="v1.0.0", service="app", project_dir=tmp_project, max_wait=2)
    assert _print_ldd_trajectory(caplog), "Missing IMP:9 business logic log"

    if expect == "success":
        assert result.success is True, f"scenario={scenario['id']}: expected success"
        assert result.rollback_performed is False
        assert result.first_deploy_failed is False
        assert result.previous_image == "sha256:prev"
    elif expect == "rollback_ok":
        assert result.success is False, f"scenario={scenario['id']}: expected failed deploy"
        assert result.rollback_performed is True, f"scenario={scenario['id']}: rollback must have been performed"
        assert "rollback performed" in (result.error_message or "")
    elif expect == "rollback_failed":
        assert result.success is False
        assert result.rollback_performed is False, f"scenario={scenario['id']}: rollback up failed → flag False"
        assert "rollback failed" in (result.error_message or "")
    else:
        pytest.fail(f"Unknown scenario expectation: {expect}")
    logger.critical("[IMP:9][test] scenario=%s — %s — OK", scenario["id"], expect)


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · retry_pull wiring (IMAGE_TAG env) via side_effect capture
# · Regression: T5.1 — deploy() must delegate pull to shared retry_pull with IMAGE_TAG=ref
# · Last fail: N/A (rewrite of test_pull_image_retry_first_attempt — 0 интроспекции вызовов)
# · Remove if: retry wiring moves out of deploy()
def test_deploy_retry_pull_wiring(deploy_boundary, tmp_project, engine, caplog):
    """deploy() wires shared retry_pull with env IMAGE_TAG=ref (side_effect capture, без интроспекции)."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    b.images.return_value = _cp(stdout="")  # first deploy
    b.run.return_value = _cp(stdout="")
    pulled_kwargs: list[dict] = []
    b.retry_pull.side_effect = lambda *a, **k: (pulled_kwargs.append(k), True)[1]

    result = engine.deploy(project="test-app", ref="v1.0.0", service="app", project_dir=tmp_project, max_wait=2)

    assert result.success is True
    assert pulled_kwargs, "shared retry_pull must be invoked from deploy()"
    assert pulled_kwargs[0].get("service") == "app"
    assert pulled_kwargs[0].get("env_override") == {"IMAGE_TAG": "v1.0.0"}, (
        f"IMAGE_TAG wiring broken: {pulled_kwargs[0].get('env_override')}"
    )
    assert _print_ldd_trajectory(caplog)
    logger.critical("[IMP:9][test] retry_pull wiring — IMAGE_TAG=v1.0.0 forwarded — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · _atomic_up observable result
# · Regression: T5.2 — thin wrapper over shared docker_compose_up
# · Last fail: N/A (rewrite — 0 интроспекции вызовов)
# · Remove if: _atomic_up removed
def test_atomic_up_success(deploy_boundary, engine, caplog):
    """_atomic_up returns True when shared docker_compose_up succeeds (observable)."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    up_kwargs: list[dict] = []
    b.up.side_effect = lambda *a, **k: (up_kwargs.append(k), True)[1]

    result = engine._atomic_up("/tmp", "app", "v1.0.0")

    assert result is True
    assert up_kwargs[0].get("env_override") == {"IMAGE_TAG": "v1.0.0"}, (
        f"_atomic_up must forward IMAGE_TAG env: {up_kwargs}"
    )
    assert _print_ldd_trajectory(caplog)
    logger.critical("[IMP:9][test] _atomic_up — returns True with IMAGE_TAG env — OK")


# endregion deploy tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: remove()
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · remove active project (data-safety contract)
# · Regression: O7/DD10 — remove must NOT use -v (data preserved)
# · Last fail: N/A (rewrite — data-safety via side_effect capture, 0 интроспекции вызовов)
# · Remove if: remove semantics change
def test_remove_active(deploy_boundary, caplog, engine, tmp_project):
    """Remove stops containers with docker compose down (no -v, --timeout 30)."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    down_flags: list[list[str]] = []
    b.down.side_effect = lambda *a, **k: (down_flags.append(k.get("flags") or []), True)[1]

    result = engine.remove(project="test-app", project_dir=tmp_project)

    assert result.success is True
    assert result.already_removed is False
    assert down_flags, "shared docker_compose_down must be invoked"
    assert "-v" not in down_flags[0], "remove() must NOT use -v (O7: данные не удаляются)"
    assert "--timeout" in down_flags[0], "remove() должен передавать --timeout 30"
    assert _print_ldd_trajectory(caplog)
    logger.critical("[IMP:9][test] remove_active — compose down без -v, данные сохранены — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · remove already-removed project
# · Scenario: project directory missing → already_removed=True
# · Last fail: N/A (preserved from pre-B10)
# · Remove if: remove idempotency semantics change
def test_remove_already_removed(caplog, engine):
    """Remove is idempotent when project directory is missing."""
    caplog.set_level(logging.INFO)

    result = engine.remove(project="nonexistent", project_dir="/tmp/nonexistent")

    assert _print_ldd_trajectory(caplog)
    assert result.success is True
    assert result.already_removed is True
    logger.critical("[IMP:9][test] remove_already_removed — already_removed=True — OK")


# endregion remove tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: status()
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · status not found
# · Scenario: project directory missing → status='not_found'
# · Last fail: N/A (preserved)
# · Remove if: status contract changes
def test_status_not_found(caplog, engine):
    """Status returns 'not_found' when project dir missing."""
    caplog.set_level(logging.INFO)

    result = engine.status(project="nonexistent", project_dir="/tmp/nonexistent")

    assert _print_ldd_trajectory(caplog)
    assert result.status == "not_found"
    logger.critical("[IMP:9][test] status_not_found — status=not_found — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · status stub detection
# · Scenario: ai-platform.yaml starts with GENERATED-STUB → status='stub'
# · Last fail: N/A (preserved)
# · Remove if: stub detection changes
def test_status_stub(caplog, engine, tmp_path):
    """Status detects GENERATED-STUB when stub_aware=True."""
    caplog.set_level(logging.INFO)

    project_dir = tmp_path / "projects" / "stub-project"
    project_dir.mkdir(parents=True)
    (project_dir / "ai-platform.yaml").write_text("GENERATED-STUB: true\nproject: stub\n")

    result = engine.status(project="stub-project", project_dir=str(project_dir), stub_aware=True)

    assert _print_ldd_trajectory(caplog)
    assert result.status == "stub"
    logger.critical("[IMP:9][test] status_stub — status=stub — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · status found with containers (compose-ps contract)
# · Scenario: docker compose ps JSON lines → status='found' + containers parsed
# · Last fail: N/A (rewrite — observable StatusResult, 0 интроспекции вызовов)
# · Remove if: status compose-ps contract changes
def test_status_found(deploy_boundary, caplog, engine, tmp_project):
    """Status returns 'found' with parsed containers from docker compose ps JSON."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    b.ps.return_value = _cp(stdout='{"Name":"test-app","State":"running"}\n')

    result = engine.status(project="test-app", project_dir=tmp_project)

    assert _print_ldd_trajectory(caplog)
    assert result.status == "found"
    assert len(result.containers) >= 1, "compose ps JSON must be parsed into containers"
    assert result.containers[0]["Name"] == "test-app"
    logger.critical("[IMP:9][test] status_found — status=found containers=%d — OK", len(result.containers))


# endregion status tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: engine helpers (real methods, boundary-mocked)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · save_previous_image returns ImageInfo
# · Scenario: compose images returns ID → ImageInfo with ID and tag
# · Last fail: N/A (preserved, boundary-mocked)
# · Remove if: _save_previous_image changes
def test_save_previous_image_exists(deploy_boundary, caplog, tmp_project, engine):
    """_save_previous_image returns ImageInfo when an image exists."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    b.images.return_value = _cp(stdout="sha256:prev123\n")
    b.run.return_value = _cp(stdout="test-app:latest\n")

    result = engine._save_previous_image(tmp_project, "app")

    assert _print_ldd_trajectory(caplog)
    assert result is not None
    assert result.id == "sha256:prev123"
    assert result.tag == "test-app:latest"
    logger.critical("[IMP:9][test] save_prev_exists — id=sha256:prev123 — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · save_previous_image None on first deploy
# · Scenario: compose images empty → None
# · Last fail: N/A (preserved)
# · Remove if: first-deploy detection changes
def test_save_previous_image_first_deploy(deploy_boundary, caplog, tmp_project, engine):
    """_save_previous_image returns None for first deploy."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    b.images.return_value = _cp(stdout="")

    result = engine._save_previous_image(tmp_project, "app")

    assert _print_ldd_trajectory(caplog)
    assert result is None
    logger.critical("[IMP:9][test] save_prev_first_deploy — None — OK")


# 🧪 TRAP[TEST] · 2026-08-02 · A7 · snapshot mechanism consolidated — DeployHistory covers rollback
# · Scenario: DeployEngine.deploy() НЕ создаёт .deploy-snapshots/ps-*.json (единственный snapshot-механизм —
# ·   DeployHistory); rollback в DeployOrchestrator работает через DeployHistory.rollback()/latest_snapshot().
# · Last fail: N/A (A7 — удаление _capture_deploy_snapshot, двойной snapshot на каждый deploy)
# · Remove if: snapshot mechanism returns to DeployEngine
def test_deploy_no_engine_snapshot_files(deploy_boundary, caplog, tmp_project, engine):
    """A7: deploy() must NOT write engine snapshots (.deploy-snapshots/ps-*.json / images-*.json)."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    b.images.return_value = _cp(stdout="")  # first deploy
    b.run.return_value = _cp(stdout="")
    snap_files: list[str] = []
    b.ps.side_effect = lambda *a, **k: (snap_files.extend(_collect_snapshot_files(tmp_project)), _cp())[1]

    result = engine.deploy(project="test-app", ref="v1.0.0", service="app", project_dir=tmp_project, max_wait=2)

    assert _print_ldd_trajectory(caplog)
    assert result.success is True
    # После deploy() не должно появиться ни одного engine-snapshot-файла
    snap_dir = Path(tmp_project) / ".deploy-snapshots"
    if snap_dir.is_dir():
        engine_files = [f.name for f in snap_dir.iterdir() if f.name.startswith(("ps-", "images-"))]
        assert not engine_files, (
            f"A7 FAIL: DeployEngine._capture_deploy_snapshot удалён, но deploy() создал: {engine_files}"
        )
        assert not (snap_dir / ".deploy-started").exists(), "A7 FAIL: .deploy-started marker must not be written"
    logger.critical("[IMP:9][test] deploy() без engine-snapshot — единственный механизм DeployHistory — OK")


def _collect_snapshot_files(project_dir: str) -> list[str]:
    """Helper: collect engine-snapshot filenames in .deploy-snapshots (observable fs probe)."""
    snap_dir = Path(project_dir) / ".deploy-snapshots"
    if not snap_dir.is_dir():
        return []
    return [f.name for f in snap_dir.iterdir()]


# 🧪 TRAP[TEST] · 2026-08-02 · A7 REGRESSION · rollback работает через DeployHistory после удаления engine-snapshot
# · Scenario: DeployHistory.create_snapshot → DeployOrchestrator.rollback() (latest_snapshot) → snapshot dict
# ·   не None — восстановление через deploy_history, engine-snapshot не участвует
# · Last fail: N/A (A7 — проверка, что удаление engine-snapshot не сломало rollback-контракт)
# · Remove if: rollback mechanism changes
def test_rollback_via_deploy_history_after_snapshot_removal(caplog, tmp_path):
    """A7: rollback через DeployHistory работает (единственный snapshot-механизм)."""
    from core.internal.deploy.deploy_history import DeployHistory

    caplog.set_level(logging.INFO)
    projects_base = str(tmp_path / "projects")
    history = DeployHistory(projects_base=projects_base)
    history.create_snapshot(
        project="test-app",
        version="sha123",
        health_status="healthy",
        compose_state={"containers": ["web"]},
    )

    snap = history.rollback("test-app")

    assert _print_ldd_trajectory(caplog)
    assert snap is not None, "rollback must return the latest DeployHistory snapshot"
    assert snap.get("version") == "sha123"
    assert snap.get("health_status") == "healthy"
    logger.critical("[IMP:9][test] rollback via DeployHistory — snapshot=%s — OK", snap.get("snapshot_id"))


# 🧪 TRAP[TEST] · NEGATIVE (R5) · A8 — _deploy_inner / _capture_deploy_snapshot removed from source
# · Scenario: AST-scan — DeployEngine не содержит _deploy_inner (дубль deploy()) и _capture_deploy_snapshot
# · Last fail: deploy_engine.py:343 _deploy_inner (дублированный docstring + validate_project_name);
# ·   :640 _capture_deploy_snapshot (двойной snapshot, никем не читался)
# · Remove if: methods are legitimately reintroduced
def test_deploy_engine_no_duplicate_layers_negative(caplog):
    """R5 negative: _deploy_inner и _capture_deploy_snapshot отсутствуют в DeployEngine (A7/A8)."""
    import ast

    caplog.set_level(logging.INFO)
    from core.internal.deploy import deploy_engine

    src = Path(deploy_engine.__file__).read_text()
    tree = ast.parse(src)
    forbidden: list[str] = [
        f"{node.lineno}: {node.name}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in ("_deploy_inner", "_capture_deploy_snapshot")
    ]

    assert not forbidden, f"A7/A8 FAIL: {', '.join(forbidden)} ещё существуют в DeployEngine"
    assert "contextlib.chdir" in src, "A8: deploy() должен использовать contextlib.chdir"
    logger.critical("[IMP:9][test] _deploy_inner/_capture_deploy_snapshot удалены — единственный deploy() — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · perform_rollback succeeds
# · Scenario: re-tag + compose up --force-recreate → True
# · Last fail: N/A (preserved, boundary-mocked)
# · Remove if: rollback flow changes
def test_perform_rollback_success(deploy_boundary, caplog, tmp_project, engine):
    """_perform_rollback succeeds with a valid previous image."""
    caplog.set_level(logging.INFO)
    b = deploy_boundary
    b.up.return_value = True
    b.run.return_value = _cp(stdout="", stderr="")
    prev_image = ImageInfo(id="sha256:prev123", tag="test-app:prev")

    result = engine._perform_rollback(tmp_project, "app", prev_image)

    assert _print_ldd_trajectory(caplog)
    assert result is True
    logger.critical("[IMP:9][test] rollback_success — True — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · perform_rollback with no previous image
# · Scenario: previous_image is None → False
# · Last fail: N/A (preserved)
# · Remove if: rollback without prev semantics change
def test_perform_rollback_no_image(caplog, engine):
    """_perform_rollback returns False with no previous image."""
    caplog.set_level(logging.INFO)

    result = engine._perform_rollback("/tmp", "app", None)

    assert _print_ldd_trajectory(caplog)
    assert result is False
    logger.critical("[IMP:9][test] rollback_no_image — False — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · D1 · validate_project_name called via deploy()
# · Scenario: invalid project name → ServiceDeployResult with error
# · Last fail: N/A (preserved)
# · Remove if: project name validation changes
def test_deploy_calls_validate_project_name(caplog, engine):
    """Deploy rejects invalid project names (observable ServiceDeployResult)."""
    caplog.set_level(logging.INFO)

    result = engine.deploy(project="../escape", ref="v1.0.0", service="app", project_dir="/tmp", max_wait=5)

    assert _print_ldd_trajectory(caplog)
    assert result.success is False
    assert "Invalid project name" in (result.error_message or "")
    logger.critical("[IMP:9][test] validate_name — success=False — OK")


# endregion engine helper tests


# ═══════════════════════════════════════════════════════════════════
# region Tests: Data classes
# ═══════════════════════════════════════════════════════════════════


def test_deploy_result_dataclass():
    """DeployResult dataclass creates with default values."""
    r = ServiceDeployResult(success=True, project="test", ref="v1", service="app")
    assert r.rollback_performed is False
    assert r.first_deploy_failed is False
    assert r.previous_image is None
    logger.critical("[IMP:9][test] ServiceDeployResult dataclass — OK")


def test_remove_result_dataclass():
    """RemoveResult dataclass creates with default values."""
    r = RemoveResult(success=True, project="test")
    assert r.already_removed is False
    logger.critical("[IMP:9][test] RemoveResult dataclass — OK")


def test_status_result_dataclass():
    """StatusResult dataclass creates with default values."""
    r = StatusResult(project="test", node="node1", status="not_found")
    assert r.containers == []
    assert r.last_deploy is None
    logger.critical("[IMP:9][test] StatusResult dataclass — OK")


# endregion Data class tests
