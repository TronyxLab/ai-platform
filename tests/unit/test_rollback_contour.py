"""
# GREP_SUMMARY: test-rollback-contour, REF-0004, TEST-03, rollback, previous-image, ROLLED_BACK, double-rollback, require-healthy, BUG-0100, skip-pull, characterization
# STRUCTURE: ▶ history[require_healthy] → ▶ snapshot-anchor(previous_image до compose-up) →
#            ▶ unhealthy-contour[ROLLED_BACK | FAILED+"Rollback failed"] → ▶ no-double-rollback(engine rollback_performed) →
#            ▶ payload-restore-after-compose-only → ▶ manual-rollback characterization(DEPLOYED|FAILED) → ▶ engine[pull-fail≠FATAL · re-verify · skip_pull] → ⎋ LDD [IMP:9]
# region MODULE_CONTRACT
## @purpose  TEST-03 (карточка REF-0004, DevPlan 11 В1): characterization + поведенческий набор
##           rollback-контурa DeployOrchestrator/DeployEngine/DeployHistory. Написан ДО правки
##           кода (инвариант 4 плана): новые контракты фиксируются RED против текущего кода,
##           characterization существующего поведения (manual rollback DEPLOYED/FAILED) — GREEN
##           до и после.
## @scope    DeployHistory.latest_snapshot(require_healthy), DeployOrchestrator.deploy unhealthy-
##           ветка (ROLLED_BACK + один re-verify), RollbackMixin._rollback_deploy (payload только
##           после успешного compose-rollback), DeployEngine.deploy (BUG-0100 pull-fail при
##           существующем деплое ≠ FATAL; skip_pull при rollback; rollback_verified).
## @invariants
##   - Native imports; tmp_path; DI-швы конструктора (167 D3) + boundary-патчи holder'ов engine/
##     (прецедент test_deploy_engine.deploy_boundary); 0 setattr-патчей production-модулей
##   - ROLLED_BACK ∉ is_success (rc≠0) — честный красный CI после отката
##   - Double rollback запрещён: rollback_performed=True → второй snapshot-rollback не выполняется
##   - Payload восстанавливается ТОЛЬКО после успешного compose-rollback
## @rationale Карточка REF-0004 Tests required: «TEST-03 набор: compose_rollback=True→DEPLOYED+
##            audit-row; False→FAILED+"Rollback failed"; unhealthy→ROLLED_BACK сквозной».
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.internal.deploy.audit import DeployAuditLogger, DeployHistory
from core.internal.deploy.channels import LocalChannel
from core.internal.deploy.healthcheck_poller import HealthcheckResult
from core.internal.deploy.orchestrator import DeployOrchestrator
from core.internal.deploy.rollback import DeployStatus

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_project(tmp_path: Path, name: str = "proj", image: str = "BROKEN:v2") -> Path:
    """Minimal project dir (payload assembly needs compose + ai-platform.yaml)."""
    proj = tmp_path / name
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "docker-compose.yml").write_text(f"services:\n  web:\n    image: {image}\n", encoding="utf-8")
    (proj / "ai-platform.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return proj


class _SeqPoller:
    """Fake poller: выдаёт статусы по порядку (последний повторяется)."""

    def __init__(self, statuses: list[str]) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    def poll_until_healthy(self, project_name: str, _project_dir: str | None = None) -> HealthcheckResult:
        self.calls += 1
        idx = min(self.calls - 1, len(self.statuses) - 1)
        return HealthcheckResult(status=self.statuses[idx], project=project_name, method="test", attempts=self.calls)


class _RecorderRollback:
    """DI compose_rollback: пишет вызовы (project_dir, service, snapshot)."""

    def __init__(self, *, result: bool) -> None:
        self.result = result
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def __call__(self, project_dir: str, service: str, snapshot: dict[str, object]) -> bool:
        self.calls.append((project_dir, service, snapshot))
        return self.result


def _make_orch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    poller: object,
    compose_deployer=None,
    compose_rollback=None,
    previous_image_resolver=None,
) -> DeployOrchestrator:
    """DeployOrchestrator с DI-швами (167 D3): audit/history в tmp, lock-dir изолирован."""
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path / "locks"))
    kwargs: dict[str, object] = {
        "projects_base": str(tmp_path / "projects"),
        "audit_logger": DeployAuditLogger(log_file=str(tmp_path / "audit.log")),
        "deploy_history": DeployHistory(projects_base=str(tmp_path / "projects")),
        "healthcheck_poller": poller,
        "compose_deployer": compose_deployer,
        "compose_rollback": compose_rollback,
        # REF-0006: L1 pre-apply gate здесь НЕ тестируется (см.
        # test_verify_contracts_orchestrator_gate.py); characterization rollback-контура
        # использует минимальные non-contractual compose — гейт инжектится пермиссивным.
        "pre_apply_gate": _permissive_gate,
    }
    if previous_image_resolver is not None:
        kwargs["previous_image_resolver"] = previous_image_resolver
    return DeployOrchestrator(**kwargs)  # type: ignore[arg-type]


def _permissive_gate(project_dir: str, project_name: str):
    """Пермиссивный L1-гейт (DI REF-0006): 0 findings — compose тестов вне контрактов L1."""
    from core.internal.deploy.verify_contracts import VerifyReport

    return VerifyReport(project_dir=Path(project_dir), state="baseline", findings=())


def _audit_rows(path: Path) -> list[dict[str, object]]:
    if not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# ════════════════════════════════════════════════════════════════════════════
# A. DeployHistory.latest_snapshot(require_healthy=True) + WARN-fallback
# ════════════════════════════════════════════════════════════════════════════


# region FUNC_test_latest_snapshot_require_healthy
def test_latest_snapshot_require_healthy_prefers_healthy(tmp_path, caplog) -> None:
    """REF-0004: откат выбирает последний ЗДОРОВЫЙ снапшот, а не заведомо больный latest."""
    caplog.set_level(logging.DEBUG)
    base = tmp_path / "projects"
    base.mkdir()
    history = DeployHistory(projects_base=str(base))
    history.create_snapshot(project="p", version="v1-good", health_status="healthy")
    history.create_snapshot(project="p", version="v2-bad", health_status="unhealthy")

    snap = history.latest_snapshot("p", require_healthy=True)

    assert snap is not None, "здоровый снапшот обязан найтись"
    assert snap["version"] == "v1-good", f"require_healthy обязан выбрать здоровый v1, получен {snap['version']}"
    assert snap["health_status"] == "healthy"
    logger.info("[IMP:9][test] require_healthy выбрал здоровый снапшот v1")


def test_latest_snapshot_require_healthy_fallback_warns(tmp_path, caplog) -> None:
    """REF-0004: нет здоровых → WARN-fallback на newest (не None)."""
    caplog.set_level(logging.DEBUG)
    base = tmp_path / "projects"
    base.mkdir()
    history = DeployHistory(projects_base=str(base))
    history.create_snapshot(project="p", version="v1-bad", health_status="unhealthy")

    snap = history.latest_snapshot("p", require_healthy=True)

    assert snap is not None, "fallback на newest обязателен (WARN), не None"
    assert snap["version"] == "v1-bad"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "fallback обязан логировать WARNING"
    logger.info("[IMP:9][test] fallback на newest с WARNING")


# endregion FUNC_test_latest_snapshot_require_healthy


# ════════════════════════════════════════════════════════════════════════════
# B. Snapshot anchor: previous_image персистится в снапшот (до compose-up)
# ════════════════════════════════════════════════════════════════════════════


# region FUNC_test_snapshot_persists_previous_image
def test_deploy_persists_previous_image_anchor_in_snapshot(tmp_path, monkeypatch) -> None:
    """REF-0004: снапшот содержит compose_state.previous_image — без него rollback обречён
    пуллить локальный тег из GHCR (~135s ×5) и падать (doomed pull)."""
    proj = _write_project(tmp_path, "anchored")
    orch = _make_orch(
        tmp_path,
        monkeypatch,
        poller=_SeqPoller(["healthy"]),
        compose_deployer=lambda _d, _s, _v: True,
        previous_image_resolver=lambda _d, _s: "sha256:anchor1",
    )
    result = orch.deploy(project_name="anchored", channel=LocalChannel(), version="sha-new", project_dir=str(proj))

    assert result.status == DeployStatus.DEPLOYED
    snap = orch.deploy_history.read_snapshot("anchored", result.snapshot_id or "")
    assert snap is not None, "снапшот обязан существовать"
    compose_state = snap.get("compose_state")
    assert isinstance(compose_state, dict), f"compose_state обязан быть dict: {compose_state!r}"
    assert compose_state.get("previous_image") == "sha256:anchor1", (
        f"REF-0004 FAIL: якорь previous_image не персистится в снапшот: {compose_state!r}"
    )
    logger.info("[IMP:9][test] snapshot.compose_state.previous_image = sha256:anchor1")


# endregion FUNC_test_snapshot_persists_previous_image


# ════════════════════════════════════════════════════════════════════════════
# C. Unhealthy-контур деплоя: ROLLED_BACK / FAILED+"Rollback failed" / no-double
# ════════════════════════════════════════════════════════════════════════════


# region FUNC_test_unhealthy_contour
def test_unhealthy_rolls_back_reverifies_and_reports_rolled_back(tmp_path, monkeypatch, caplog) -> None:
    """Сквозной DI-тест: poller unhealthy → snapshot-rollback на здоровый снапшот →
    ОДИН re-verify healthy → ROLLED_BACK + audit-row + rollback_verified=True."""
    caplog.set_level(logging.INFO)
    proj = _write_project(tmp_path, "rollme")
    orch = _make_orch(
        tmp_path,
        monkeypatch,
        poller=_SeqPoller(["unhealthy", "healthy"]),
        compose_deployer=lambda _d, _s, _v: True,
        compose_rollback=_RecorderRollback(result=True),
    )
    # Предыдущий ЗДОРОВЫЙ релиз с якорем образа
    orch.deploy_history.create_snapshot(
        project="rollme",
        version="v1-good",
        health_status="healthy",
        compose_state={"previous_image": "sha256:good"},
    )

    result = orch.deploy(project_name="rollme", channel=LocalChannel(), version="v2-bad", project_dir=str(proj))

    assert result.status == DeployStatus.ROLLED_BACK, (
        f"REF-0004 FAIL: unhealthy при живом здоровом снапшоте обязан давать ROLLED_BACK, "
        f"получен {result.status} (error={result.error_info!r})"
    )
    assert result.is_success() is False, "ROLLED_BACK ∉ success — CI остаётся красным"
    assert result.rollback_verified is True, "после успешного отката обязан быть re-verify (rollback_verified)"
    assert result.healthcheck_status == "unhealthy", "факт о НОВОМ деплое сохраняется"

    rows = _audit_rows(tmp_path / "audit.log")
    assert any(r.get("result") == "ROLLED_BACK" for r in rows), f"audit-row ROLLED_BACK отсутствует: {rows}"
    logger.info("[IMP:9][test] unhealthy → ROLLED_BACK (verified) — сквозной контур OK")


def test_unhealthy_compose_rollback_failure_reports_failed_and_keeps_payload(tmp_path, monkeypatch) -> None:
    """compose-rollback неудачен → FAILED + «Rollback failed»; payload НЕ восстанавливался
    (восстановление ТОЛЬКО после успешного compose-rollback)."""
    proj = _write_project(tmp_path, "keepbrok", image="BROKEN:v2")
    orch = _make_orch(
        tmp_path,
        monkeypatch,
        poller=_SeqPoller(["unhealthy"]),
        compose_deployer=lambda _d, _s, _v: True,
        compose_rollback=(recorder := _RecorderRollback(result=False)),
    )
    orch.deploy_history.create_snapshot(project="keepbrok", version="v1", health_status="healthy")

    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "docker-compose.yml").write_text("services:\n  web:\n    image: WORKING:v1\n", encoding="utf-8")

    result = orch.deploy(
        project_name="keepbrok",
        channel=LocalChannel(),
        version="v2-bad",
        project_dir=str(proj),
        metadata={"payload_backup_dir": str(backup)},
    )

    assert result.status == DeployStatus.FAILED, f"неудачный rollback обязан давать FAILED, получен {result.status}"
    assert "Rollback failed" in (result.error_info or ""), (
        f"REF-0004 FAIL: в error_info ожидается «Rollback failed», получено {result.error_info!r}"
    )
    assert len(recorder.calls) == 1, f"rollback-попытка обязательна ровно одна: {len(recorder.calls)}"
    kept = (proj / "docker-compose.yml").read_text(encoding="utf-8")
    assert "BROKEN:v2" in kept, "payload восстанавливается ТОЛЬКО после успешного compose-rollback"
    logger.info("[IMP:9][test] compose_rollback=False → FAILED + «Rollback failed» + payload не тронут")


def test_engine_rolled_back_skips_second_snapshot_rollback(tmp_path, monkeypatch) -> None:
    """rollback_performed=True (engine уже откатил) → второй snapshot-rollback ЗАПРЕЩЁН
    (double rollback), статус ROLLED_BACK, poller повторно НЕ опрашивается."""
    proj = _write_project(tmp_path, "norepeat")

    class _NoPollPoller:
        def poll_until_healthy(self, *_a: object, **_k: object) -> HealthcheckResult:
            msg = "poller НЕ должен опрашиваться: engine уже сделал единственный re-verify"
            raise AssertionError(msg)

    recorder = _RecorderRollback(result=True)

    # Сигнал-канал пишется ТАК ЖЕ, как реальным _deploy_compose (stash из ServiceDeployResult,
    # затем success-флаг): engine «уже откатил и верифицировал», compose вернул False.
    def _engine_rolled_back_compose(_d: str, _s: str, _v: str) -> bool:
        orch._last_engine_rollback_performed = True
        orch._last_engine_rollback_verified = True
        return False

    orch = _make_orch(
        tmp_path,
        monkeypatch,
        poller=_NoPollPoller(),
        compose_deployer=_engine_rolled_back_compose,
        compose_rollback=recorder,
    )

    result = orch.deploy(project_name="norepeat", channel=LocalChannel(), version="v2", project_dir=str(proj))

    assert result.status == DeployStatus.ROLLED_BACK, (
        f"REF-0004 FAIL: engine уже откатил (verified) → ROLLED_BACK, получен {result.status}"
    )
    assert recorder.calls == [], f"двойной откат запрещён: snapshot-rollback вызван {len(recorder.calls)} раз"
    snap = orch.deploy_history.read_snapshot("norepeat", result.snapshot_id or "")
    assert snap is not None and snap.get("health_status") == "unhealthy", (
        "честная запись о неудачном деплое (unhealthy) обязательна в истории"
    )
    rows = _audit_rows(tmp_path / "audit.log")
    assert any(r.get("result") == "ROLLED_BACK" for r in rows), f"audit-row ROLLED_BACK отсутствует: {rows}"
    logger.info("[IMP:9][test] rollback_performed=True → второй rollback пропущен, ROLLED_BACK")


# endregion FUNC_test_unhealthy_contour


# ════════════════════════════════════════════════════════════════════════════
# D. Payload restore ТОЛЬКО после успешного compose-rollback (_rollback_deploy)
# ════════════════════════════════════════════════════════════════════════════


# region FUNC_test_payload_restore_ordering
@pytest.mark.parametrize(
    ("compose_ok", "expect_status"), [(False, DeployStatus.FAILED), (True, DeployStatus.ROLLED_BACK)]
)
def test_payload_restored_only_after_successful_compose_rollback(
    tmp_path, monkeypatch, compose_ok: bool, expect_status: DeployStatus
) -> None:
    """REF-0004: порядок rollback = compose СНАЧАЛА; payload-файлы — только при успехе compose."""
    target = tmp_path / "projects" / "ord"
    target.mkdir(parents=True)
    (target / "docker-compose.yml").write_text("services:\n  web:\n    image: BROKEN:v2\n", encoding="utf-8")
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "docker-compose.yml").write_text("services:\n  web:\n    image: WORKING:v1\n", encoding="utf-8")

    orch = _make_orch(
        tmp_path, monkeypatch, poller=_SeqPoller(["healthy"]), compose_rollback=_RecorderRollback(result=compose_ok)
    )

    start = time.monotonic()
    result = orch._rollback_deploy(
        "ord",
        LocalChannel(),
        "ord",
        str(target),
        snapshot={"snapshot_id": "snap-1", "compose_state": {"previous_image": "sha256:x"}},
        start=start,
        payload_backup_dir=str(backup),
    )

    assert result.status == expect_status, f"compose_rollback={compose_ok}: ожидался {expect_status}"
    kept = (target / "docker-compose.yml").read_text(encoding="utf-8")
    if compose_ok:
        assert "WORKING:v1" in kept, "успешный compose-rollback → payload восстанавливается"
    else:
        assert "BROKEN:v2" in kept, (
            "REF-0004 FAIL: payload восстановлен ПРИ НЕУДАЧНОМ compose-rollback (рассинхрон disk/container)"
        )
    logger.info("[IMP:9][test] payload-restore ordering OK (compose_ok=%s)", compose_ok)


# endregion FUNC_test_payload_restore_ordering


# ════════════════════════════════════════════════════════════════════════════
# E. Manual rollback — characterization (GREEN до и после правки)
# ════════════════════════════════════════════════════════════════════════════


# region FUNC_test_manual_rollback_characterization
def test_manual_rollback_compose_ok_deploys_and_audits(tmp_path, monkeypatch) -> None:
    """Characterization TEST-03: rollback() c compose_rollback=True → DEPLOYED + audit-row."""
    _write_project(tmp_path, "manok")
    orch = _make_orch(
        tmp_path, monkeypatch, poller=_SeqPoller(["healthy"]), compose_rollback=_RecorderRollback(result=True)
    )
    snap_id = orch.deploy_history.create_snapshot(project="manok", version="v1", health_status="healthy")

    result = orch.rollback("manok", snapshot_id=snap_id)

    assert result.status == DeployStatus.DEPLOYED, f"compose_rollback=True → DEPLOYED, получен {result.status}"
    rows = _audit_rows(tmp_path / "audit.log")
    assert any(r.get("operation") == "rollback" and r.get("result") == "DEPLOYED" for r in rows), (
        f"audit-row rollback/DEPLOYED отсутствует: {rows}"
    )
    logger.info("[IMP:9][test] manual rollback OK → DEPLOYED + audit-row (characterization)")


def test_manual_rollback_compose_fail_reports_rollback_failed(tmp_path, monkeypatch) -> None:
    """Characterization TEST-03: rollback() c compose_rollback=False → FAILED + «Rollback failed»."""
    _write_project(tmp_path, "manfail")
    orch = _make_orch(
        tmp_path, monkeypatch, poller=_SeqPoller(["healthy"]), compose_rollback=_RecorderRollback(result=False)
    )
    snap_id = orch.deploy_history.create_snapshot(project="manfail", version="v1", health_status="healthy")

    result = orch.rollback("manfail", snapshot_id=snap_id)

    assert result.status == DeployStatus.FAILED
    assert "Rollback failed" in (result.error_info or ""), f"ожидаётся «Rollback failed»: {result.error_info!r}"
    logger.info("[IMP:9][test] manual rollback FAIL → FAILED + «Rollback failed» (characterization)")


def test_unhealthy_without_snapshots_stays_failed_guard(tmp_path, monkeypatch) -> None:
    """Guard REF-0003: нет снапшотов → откат невозможен → FAILED (∉ success), rc≠0 сохраняется."""
    proj = _write_project(tmp_path, "lonely")
    orch = _make_orch(tmp_path, monkeypatch, poller=_SeqPoller(["unhealthy"]), compose_deployer=lambda _d, _s, _v: True)

    result = orch.deploy(project_name="lonely", channel=LocalChannel(), version="v1", project_dir=str(proj))

    assert result.status == DeployStatus.FAILED
    assert result.is_success() is False
    assert result.healthcheck_status == "unhealthy"
    logger.info("[IMP:9][test] unhealthy без снапшотов → FAILED (guard REF-0003)")


# endregion FUNC_test_manual_rollback_characterization


# ════════════════════════════════════════════════════════════════════════════
# F. DeployEngine: BUG-0100 rider + re-verify + skip_pull
# ════════════════════════════════════════════════════════════════════════════


def _cp(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


# region FIXTURE_engine_boundary
@pytest.fixture()
def engine_boundary():
    """Boundary-патчи holder'ов пакета engine/ (прецедент test_deploy_engine.deploy_boundary)."""
    mock_run = MagicMock(return_value=_cp())
    mock_retry_pull = MagicMock(return_value=True)
    mock_health = MagicMock(return_value="healthy")
    mock_up = MagicMock(return_value=True)
    mock_images = MagicMock(return_value=_cp())

    with (
        patch("subprocess.run", mock_run),
        patch("core.internal.deploy.engine.flow._shared_retry_pull", mock_retry_pull),
        patch("core.internal.deploy.engine.flow._shared_healthcheck_poll", mock_health),
        patch("core.internal.deploy.engine.flow.shared_docker_compose_up", mock_up),
        patch("core.internal.deploy.engine.lifecycle._shared_docker_compose_images", mock_images),
    ):
        yield type(
            "Boundary",
            (),
            {
                "run": mock_run,
                "retry_pull": mock_retry_pull,
                "health": mock_health,
                "up": mock_up,
                "images": mock_images,
            },
        )()


@pytest.fixture()
def engine(tmp_path):
    from core.internal.deploy.deploy_engine import DeployEngine

    proj = tmp_path / "e-proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "docker-compose.yml").write_text("services:\n  app:\n    image: t/app:v1\n", encoding="utf-8")
    return DeployEngine(projects_base=str(tmp_path)), str(proj)


# endregion FIXTURE_engine_boundary


# region FUNC_test_engine_bug0100_and_reverify
def test_engine_pull_failure_existing_deploy_not_fatal(engine_boundary, engine, caplog) -> None:
    """BUG-0100: pull-failure при СУЩЕСТВУЮЩЕМ деплое ≠ first-deploy FATAL — работающий
    контейнер сохраняется, честный ServiceDeployResult(success=False)."""
    caplog.set_level(logging.INFO)
    eng, proj_dir = engine
    b = engine_boundary
    b.retry_pull.return_value = False
    b.images.return_value = _cp(stdout="sha256:running\n")
    b.run.return_value = _cp(stdout="app:v1")

    result = eng.deploy(project="e-proj", ref="v2", service="app", project_dir=proj_dir, max_wait=2)

    assert result.success is False
    assert result.rollback_performed is False, "pull-fail не должен запускать rollback"
    assert result.first_deploy_failed is False, (
        "REF-0004 FAIL: pull-fail при существующем деплое помечен как first-deploy (FATAL-ветка)"
    )
    assert "Pull failed" in (result.error_message or "")
    logger.info("[IMP:9][test] BUG-0100: pull-fail при живом деплое → честный FAILED без FATAL")


def test_engine_healthfail_rollback_single_reverify_sets_flag(engine_boundary, engine, caplog) -> None:
    """После perform_rollback — ОДИН re-verify; поле rollback_verified (additive) отражает факт."""
    caplog.set_level(logging.INFO)
    eng, proj_dir = engine
    b = engine_boundary
    b.images.return_value = _cp(stdout="sha256:prev\n")
    b.run.return_value = _cp(stdout="app:v1")
    health_seq = iter(["unhealthy", "healthy"])
    b.health.side_effect = lambda *_a, **_k: next(health_seq)

    result = eng.deploy(project="e-proj", ref="v2", service="app", project_dir=proj_dir, max_wait=2)

    assert result.success is False
    assert result.rollback_performed is True
    assert getattr(result, "rollback_verified", None) is True, (
        "REF-0004 FAIL: после успешного rollback re-verify обязан выставить rollback_verified=True"
    )
    logger.info("[IMP:9][test] engine rollback + re-verify → rollback_verified=True")


def test_engine_rollback_up_fail_not_verified(engine_boundary, engine) -> None:
    """compose-up отката неудачен → rollback_performed=False, rollback_verified=False."""
    eng, proj_dir = engine
    b = engine_boundary
    b.images.return_value = _cp(stdout="sha256:prev\n")
    b.run.return_value = _cp(stdout="app:v1")
    b.health.return_value = "unhealthy"

    def _up_side_effect(*_a, **kwargs):
        return kwargs.get("flags") != ["--force-recreate"]

    b.up.side_effect = _up_side_effect

    result = eng.deploy(project="e-proj", ref="v2", service="app", project_dir=proj_dir, max_wait=2)

    assert result.success is False
    assert result.rollback_performed is False
    assert getattr(result, "rollback_verified", None) is False
    logger.info("[IMP:9][test] rollback-up fail → rollback_verified=False")


def test_previous_rollback_ref_skips_pull(engine_boundary, engine, caplog) -> None:
    """REF-0004: ref='previous-rollback' + skip_pull=True → doomed-pull из GHCR исчезает
    (образ уже перетегирован локально); compose up получает IMAGE_TAG=previous-rollback."""
    caplog.set_level(logging.INFO)
    eng, proj_dir = engine
    b = engine_boundary
    b.images.return_value = _cp(stdout="sha256:local\n")
    b.run.return_value = _cp(stdout="app:previous-rollback")
    pulled_kwargs: list[dict] = []

    def _capture_pull(*_, **kwargs):
        pulled_kwargs.append(kwargs)
        return True

    b.retry_pull.side_effect = _capture_pull

    result = eng.deploy(
        project="e-proj", ref="previous-rollback", service="app", project_dir=proj_dir, max_wait=2, skip_pull=True
    )

    assert result.success is True
    assert pulled_kwargs == [], f"REF-0004 FAIL: pull локального тега обязан быть пропущен: {pulled_kwargs}"
    logger.info("[IMP:9][test] skip_pull=True → doomed GHCR pull устранён")


# endregion FUNC_test_engine_bug0100_and_reverify
