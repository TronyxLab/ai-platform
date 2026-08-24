"""
# GREP_SUMMARY: test-healthcheck-failed-rc, REF-0003, unhealthy, timeout, FAILED, exit-code, PARTIAL, receive, severity-critical, DI
# STRUCTURE: ▶ fake-poller(unhealthy|timeout) → DeployOrchestrator.deploy → ⎋ FAILED (∉success)
#            │ ▶ ReceiveFlow.run(unhealthy) → rc=1 + JSON FAILED + notify critical + chain skipped
#            │ ▶ healthy-контроль → rc=0 + chain исполнен + notify НЕ вызван
# region MODULE_CONTRACT
## @purpose  REF-0003 (DevPlan 11 W0, P0-launch-blockers): неуспешный healthcheck больше НЕ зелёный.
##           unhealthy/timeout poller → OrchestratorDeployResult.status=FAILED (не PARTIAL);
##           is_success()=False → receive exit≠0; Telegram severity=critical на unhealthy-ветке;
##           post-deploy chain НЕ исполняется поверх больного деплоя. PARTIAL — внутренний статус,
##           никогда не success.
## @scope    DeployOrchestrator.deploy (DI: healthcheck_poller/compose_deployer — 167 D3),
##           ReceiveFlow.run (orchestrator_factory/failure_notifier DI), предикат is_success().
## @invariants
##   - Native imports; tmp_path; stdin через io.BytesIO (W-H); 0 setattr-патчей
##   - DeployStatus.PARTIAL остаётся в enum (freeze п.3 — rename запрещён), но ∉ success
##   - SKIPPED остаётся success (dry-run plan контракт DevPlan 089 AC10)
## @rationale Карточка REF-0003 Tests required: «DI-тест poller=unhealthy → rc≠0».
##            Root cause аудита: success-предикат шире health-факта («best-effort swallowing»).
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import io
import json
import logging
import tarfile
from pathlib import Path

import pytest

from core.internal.deploy.audit import DeployAuditLogger
from core.internal.deploy.channels import LocalChannel
from core.internal.deploy.healthcheck_poller import HealthcheckResult
from core.internal.deploy.orchestrator import DeployOrchestrator
from core.internal.deploy.receive_flow import ReceiveFlow
from core.internal.deploy.rollback import DeployStatus, OrchestratorDeployResult

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# L1-валидный compose (тот же канон, что test_receive_flow.py — pre-deploy gate пропускает)
_VALID_COMPOSE: str = """\
services:
  web:
    image: nginx:alpine
    env_file:
      - .env.platform
    healthcheck:
      test: ["CMD", "echo", "ok"]
    deploy:
      resources:
        limits:
          memory: "128M"
          cpus: "0.25"
    labels:
      - "platform.type=backend"
    networks:
      - proxy-net
networks:
  proxy-net:
    external: true
"""


def _make_payload_tar(tmp_path: Path, project: str = "testproj") -> bytes:
    """tar.gz payload (ai-platform.yaml + docker-compose.yml + .env.platform) — как в test_receive_flow."""
    proj_dir = tmp_path / "payload"
    proj_dir.mkdir(exist_ok=True)
    (proj_dir / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")
    (proj_dir / "ai-platform.yaml").write_text(f"name: {project}\n", encoding="utf-8")
    (proj_dir / ".env.platform").write_text("PLATFORM_DOMAIN=example.com\n", encoding="utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", encoding="utf-8") as tar:
        for fname in ("docker-compose.yml", "ai-platform.yaml", ".env.platform"):
            tar.add(proj_dir / fname, arcname=fname)
    return buf.getvalue()


class _FakePoller:
    """Fake poller (167 D3-стиль): возвращает заданный health-статус без docker."""

    def __init__(self, status: str) -> None:
        self.status = status

    def poll_until_healthy(self, project_name: str, _project_dir: str | None = None) -> HealthcheckResult:
        return HealthcheckResult(status=self.status, project=project_name, method="test", attempts=2)


# 🧪 TRAP[TEST] · 2026-08-24 · unit · REF-0003 — success-предикат сужен
# · Regression: BUG-0602/FAIL-0102 — is_success включал PARTIAL → зелёный CI при больном деплое
# · Scenario: PARTIAL.is_success()=False (внутренний статус); DEPLOYED/SKIPPED — True; FAILED — False
# · Last fail: red до фикса — PARTIAL входил в {DEPLOYED, PARTIAL, SKIPPED}
# · Remove if: контракты статусов пересматриваются (freeze п.3 запрещает rename — только семантика)
def test_is_success_excludes_partial() -> None:
    """REF-0003: PARTIAL — внутренний статус, никогда не success; SKIPPED (dry-run) остаётся."""
    assert OrchestratorDeployResult(DeployStatus.DEPLOYED, "t").is_success() is True
    assert OrchestratorDeployResult(DeployStatus.SKIPPED, "t").is_success() is True
    assert OrchestratorDeployResult(DeployStatus.FAILED, "t").is_success() is False
    assert OrchestratorDeployResult(DeployStatus.PARTIAL, "t").is_success() is False, (
        "REF-0003 FAIL: PARTIAL не может быть success (fail-open swallowing вернулся)"
    )
    logger.info("[IMP:9][test] is_success predicate: PARTIAL excluded")


# 🧪 TRAP[TEST] · 2026-08-24 · unit · REF-0003 — unhealthy/timeout poller → FAILED
# · Regression: карточка REF-0003 Tests required «DI-тест poller=unhealthy → rc≠0»
# · Scenario: compose ok + poller unhealthy/timeout → deploy() возвращает FAILED (не PARTIAL),
# ·   healthcheck_status сохраняет факт («unhealthy»/«timeout»)
# · Last fail: red до фикса — _verify_deploy мапил unhealthy/timeout → DeployStatus.PARTIAL
# · Remove if: unhealthy-ветка переезжает в ROLLED_BACK (REF-0004) — обновить ожидаемый статус
@pytest.mark.parametrize(("poller_status"), ["unhealthy", "timeout"])
def test_unhealthy_poller_deploy_returns_failed(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    poller_status: str,
) -> None:
    """DI: fake poller unhealthy/timeout → DeployOrchestrator.deploy() = FAILED, ∉ success."""
    caplog.set_level(logging.INFO)

    proj_dir = tmp_path / "testproj"
    proj_dir.mkdir()
    (proj_dir / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")
    (proj_dir / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")

    audit_log = tmp_path / "audit.log"
    orch = DeployOrchestrator(
        projects_base=str(tmp_path),
        audit_logger=DeployAuditLogger(log_file=str(audit_log)),
        healthcheck_poller=_FakePoller(poller_status),
        compose_deployer=lambda _d, _s, _v: True,
    )
    result = orch.deploy(
        project_name="testproj",
        channel=LocalChannel(),
        version="abc123",
        project_dir=str(proj_dir),
    )

    assert result.status == DeployStatus.FAILED, (
        f"REF-0003 FAIL: {poller_status} должен давать FAILED, получен {result.status}"
    )
    assert result.is_success() is False
    assert result.healthcheck_status == poller_status
    assert "[IMP:9]" in caplog.text, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("--- LDD TRAJECTORY: deploy → %s (hc=%s) ---", result.status.value, result.healthcheck_status)


# 🧪 TRAP[TEST] · 2026-08-24 · unit · REF-0003 — receive: rc≠0 + critical-notify + chain skipped
# · Regression: карточка REF-0003 (exit 0 на PARTIAL; post_deploy_chain.py:15 PARTIAL→info;
# ·   «post-deploy chain исполняется поверх больного деплоя»)
# · Scenario: полный receive-флоу с unhealthy poller → rc=1, JSON status=FAILED,
# ·   failure-notify вызван ровно один раз (severity=critical решает mapping в hooks),
# ·   полная post-deploy chain НЕ исполняется; healthy-контроль — обратное
# · Last fail: red до фикса — rc=0 (PARTIAL∈success), notify шёл бы info по цепочке успеха
# · Remove if: receive перестаёт быть каналом деплоя (контракт forced-command)
@pytest.mark.parametrize(("poller_status"), ["unhealthy", "timeout"])
def test_receive_unhealthy_rc_nonzero_critical_notify_chain_skipped(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    poller_status: str,
) -> None:
    """ReceiveFlow.run с больным healthcheck: rc≠0, JSON FAILED, critical-notify, chain skipped."""
    caplog.set_level(logging.INFO)

    chain_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    notified: list[tuple[str, str, str]] = []

    class _ChainRecorderOrch(DeployOrchestrator):
        def _run_post_deploy_chain(self, *args: object, **kwargs: object) -> None:
            chain_calls.append((args, kwargs))

    def _factory(*args: object, **kwargs: object) -> DeployOrchestrator:
        if not args and "projects_base" not in kwargs:
            kwargs["projects_base"] = str(tmp_path)
        kwargs["healthcheck_poller"] = _FakePoller(poller_status)
        kwargs.setdefault("compose_deployer", lambda *_: True)
        return _ChainRecorderOrch(*args, **kwargs)  # type: ignore[arg-type]

    def _notifier(project: str, version: str, status: str) -> None:
        notified.append((project, version, status))

    flow = ReceiveFlow(
        projects_base=str(tmp_path),
        orchestrator_factory=_factory,  # type: ignore[arg-type]
        failure_notifier=_notifier,
    )
    tar_bytes = _make_payload_tar(tmp_path)
    rc = flow.run(project_name="testproj", version="abc123", stream=io.BytesIO(tar_bytes))

    assert rc == 1, f"REF-0003 FAIL: unhealthy ({poller_status}) receive обязан вернуть rc!=0"

    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] == "FAILED"
    assert payload["healthcheck_status"] == poller_status
    assert payload["project"] == "testproj"
    assert payload["version"] == "abc123"  # D5 sha-pinning сохранён

    # critical-notify: ровно один вызов на unhealthy-ветке (mapping → critical живёт в hooks)
    assert notified == [("testproj", "abc123", "FAILED")], f"critical-notify ожидался ровно один: {notified}"

    # post-deploy chain (catalog/reconfig/hooks) НЕ исполняется поверх больного деплоя
    assert chain_calls == [], f"REF-0003 FAIL: full chain не должен идти на failed-healthcheck: {chain_calls}"

    assert "[IMP:10]" in caplog.text, "IMP:10 fail-лог ожидался на unhealthy-ветке"
    logger.info("--- LDD TRAJECTORY: receive rc=%d status=%s notified=%s ---", rc, payload["status"], notified)


# 🧪 TRAP[TEST] · 2026-08-24 · unit · REF-0003 — healthy-контроль (анти-регресс happy path)
# · Regression: фикс не должен сломать успешный канал (CI-путь)
# · Scenario: healthy poller → rc=0, JSON DEPLOYED, полная chain исполняется, failure-notify молчит
# · Last fail: N/A (guard)
# · Remove if: receive happy-path контракт меняется
def test_receive_healthy_happy_path_guard(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Healthy poller: rc=0, chain исполнен, failure-notify НЕ вызван (контроль после фикса)."""
    caplog.set_level(logging.INFO)

    chain_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    notified: list[tuple[str, str, str]] = []

    class _ChainRecorderOrch(DeployOrchestrator):
        def _run_post_deploy_chain(self, *args: object, **kwargs: object) -> None:
            chain_calls.append((args, kwargs))

    def _factory(*args: object, **kwargs: object) -> DeployOrchestrator:
        if not args and "projects_base" not in kwargs:
            kwargs["projects_base"] = str(tmp_path)
        kwargs["healthcheck_poller"] = _FakePoller("healthy")
        kwargs.setdefault("compose_deployer", lambda *_: True)
        return _ChainRecorderOrch(*args, **kwargs)  # type: ignore[arg-type]

    def _notifier(project: str, version: str, status: str) -> None:
        notified.append((project, version, status))

    flow = ReceiveFlow(
        projects_base=str(tmp_path),
        orchestrator_factory=_factory,  # type: ignore[arg-type]
        failure_notifier=_notifier,
    )
    tar_bytes = _make_payload_tar(tmp_path)
    rc = flow.run(project_name="testproj", version="abc123", stream=io.BytesIO(tar_bytes))

    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["status"] == "DEPLOYED"
    assert len(chain_calls) == 1, "полная chain исполняется ТОЛЬКО на успехе (ровно один раз)"
    assert notified == [], "failure-notify не вызывается на healthy-деплое"
    logger.info("[IMP:9][test] happy-path guard OK: chain=%d notified=%d", len(chain_calls), len(notified))
