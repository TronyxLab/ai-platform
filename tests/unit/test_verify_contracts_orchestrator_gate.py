"""
# GREP_SUMMARY: test-verify-contracts-orchestrator-gate, REF-0006, L1-pre-apply-gate, DeployOrchestrator-deploy, socket-mount-blocked, root-bind-blocked, fail-closed, dry-run, TOCTOU, TEST-05
# STRUCTURE: ▶ _make_orch (DI-швы 167 D3/REF-0006) → ◇ malicious compose (socket-mount / /-bind) →
#            deploy() → ⊕ FAILED ДО delivery/compose (R5 C1-входы) · ▶ валидный compose → DEPLOYED
#            (нет ложного блока) · ▶ gate-error → fail-closed · ▶ dry_run → SKIPPED без гейта → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Интеграция REF-0006 (DevPlan 11 В2): L1 pre-apply gate внутри DeployOrchestrator.deploy
##           ПЕРЕД _apply_deploy. R5-негативы с точными C1-входами карточки (socket-mount,
##           «/»-bind): деплой блокируется ДО доставки/compose-up; валидный compose проходит
##           реальный гейт до DEPLOYED (отсутствие false-positive); ошибка гейта — fail-closed;
##           dry_run не исполняет гейт.
## @scope    DeployOrchestrator._run_l1_pre_apply_gate + дефолтный verify_project_contracts(l1_only).
##           ReceiveFlow-гейт staging'а (176 A.2) покрыт test_receive_flow*.py — здесь target_dir-
##           рубеж (TOCTOU-закрытие). TEST-05 traversal-негативы dispatch — test_orchestrator_cli_dispatch.py.
## @invariants
##   - Native imports; tmp_path; DI-швы конструктора (167 D3); 0 setattr-патчей production-модулей
##   - Реальный дефолтный гейт (None → verify_contracts l1_only) — НЕ подмена фейком в негативах
##   - PLATFORM_LOCK_DIR изолируется (tmp) — /var/lock не мутируется
##   - Audit в tmp-файл (DeployAuditLogger log_file) — НЕ /var/log/platform
##   - LDD: IMP:9/IMP:10 траектория гейта через assert_ldd_imp9
## @rationale Карточка REF-0006 Tests required: «R5-негативы с точным C1-input (socket-mount,
##            "/"-bind)»; Problem: «DeployOrchestrator.deploy исполняет compose от root вообще
##            без проверки» — здесь фиксируется блок ДО compose и отсутствие блоков легитимных
##            payload'ов (regression risk карточки).
# endregion MODULE_CONTRACT
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from core.internal.deploy.audit import DeployAuditLogger, DeployHistory
from core.internal.deploy.channels import DeliveryChannel, DeliveryResult, Payload
from core.internal.deploy.healthcheck_poller import HealthcheckResult
from core.internal.deploy.orchestrator import DeployOrchestrator, DeployStatus
from core.internal.deploy.verify_contracts import VerifyReport

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


class _RecordingChannel(DeliveryChannel):
    """Канал-рекордер: доставок не должно происходить при блоке гейта."""

    def __init__(self) -> None:
        super().__init__(timeout=5)
        self.deliver_calls: list[Payload] = []

    def deliver(self, payload: Payload) -> DeliveryResult:
        self.deliver_calls.append(payload)
        return DeliveryResult(success=True, stdout="delivered", exit_code=0, duration_s=0.01)

    def _retry_deliver(self, payload: Payload) -> DeliveryResult:
        return self.deliver(payload)


class _HealthyPoller:
    """Fake poller (прецедент FakeHealthcheckPoller) — мгновенный healthy."""

    def poll_until_healthy(self, project_name: str, _project_dir: str | None = None) -> HealthcheckResult:
        return HealthcheckResult(status="healthy", project=project_name, method="test", attempts=1)


def _write_project(
    base: Path, name: str, volumes_lines: list[str] | None = None, *, raw_compose: str | None = None
) -> Path:
    """Project dir with ai-platform.yaml + compose (malicious variants via lines/raw)."""
    proj = base / name
    proj.mkdir(parents=True, exist_ok=True)
    if raw_compose is not None:
        compose = raw_compose
    else:
        compose = "\n".join([
            "services:",
            "  app:",
            "    image: busybox:latest",
            *(["    volumes:", *map(str, volumes_lines)] if volumes_lines else []),
            "    env_file:",
            "      - .env.platform",
            "    healthcheck:",
            '      test: ["CMD", "echo", "ok"]',
            "    deploy:",
            "      resources:",
            "        limits:",
            '          memory: "128M"',
            '          cpus: "0.25"',
            "    labels:",
            '      - "platform.type=backend"',
            "    networks:",
            "      - proxy-net",
            "networks:",
            "  proxy-net:",
            "    external: true",
        ])
    (proj / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (proj / "ai-platform.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    (proj / ".env.platform").write_text("PLATFORM_DOMAIN=example.com\n", encoding="utf-8")
    return proj


def _make_orch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **overrides: object
) -> tuple[DeployOrchestrator, _RecordingChannel]:
    """Real-gate orchestrator (pre_apply_gate NOT overridden unless in overrides) + recorder channel."""
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path / "locks"))
    channel = _RecordingChannel()
    kwargs: dict[str, object] = {
        "projects_base": str(tmp_path / "projects"),
        "audit_logger": DeployAuditLogger(log_file=str(tmp_path / "audit.log")),
        "deploy_history": DeployHistory(projects_base=str(tmp_path / "projects")),
        "healthcheck_poller": _HealthyPoller(),
        "compose_deployer": lambda _project_dir, _service, _version: True,
        "compose_rollback": lambda _project_dir, _service, _snapshot: True,
    }
    kwargs.update(overrides)
    return DeployOrchestrator(**kwargs), channel  # type: ignore[arg-type]


def _audit_rows(path: Path) -> list[dict[str, object]]:
    if not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · REF-0006 C1-вход «socket-mount» в deploy()
# · Last fail: orchestrator.deploy исполнял compose от root БЕЗ проверки (receive-гейт гейтил
#   только staging; TOCTOU/прямой путь φ8/φ12 — без единой volumes-проверки)
# · Remove if: pre-apply gate удаляется/переносится из deploy()
@pytest.mark.parametrize(
    "volumes_lines",
    [
        ['      - "/var/run/docker.sock:/var/run/docker.sock"'],
        ['      - "/:/host"'],
    ],
    ids=["socket-mount", "root-bind"],
)
def test_deploy_blocks_malicious_volumes_before_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    volumes_lines: list[str],
) -> None:
    """Точные C1-входы (socket-mount, /-bind): FAILED до delivery/compose, контейнеры не запущены."""
    proj = _write_project(tmp_path / "projects" and tmp_path, "evilproj", volumes_lines=volumes_lines)
    orch, channel = _make_orch(tmp_path, monkeypatch)

    with caplog.at_level(logging.INFO):
        result = orch.deploy(project_name="evilproj", channel=channel, project_dir=str(proj))

    assert result.status == DeployStatus.FAILED, f"C1-вход обязан блокироваться: {result.to_dict()}"
    assert "[PRACTICES:BLOCK]" in (result.error_info or ""), f"error_info без PRACTICES:BLOCK: {result.error_info!r}"
    assert channel.deliver_calls == [], "delivery НЕ должен выполняться после блока гейта"
    rows = _audit_rows(tmp_path / "audit.log")
    assert rows and rows[-1]["result"] == "FAILED"
    assert "L1 pre-apply gate" in str(rows[-1].get("error", ""))
    assert any("[IMP:10]" in r.message and "BLOCKED" in r.message for r in caplog.records), (
        "LDD: нет IMP:10 BLOCKED лога гейта"
    )
    logger.critical("[IMP:9][test] REF-0006: malicious volumes blocked BEFORE delivery (%s)", result.error_info)


# 🧪 TRAP[TEST] · 2026-08-25 · unit · валидный compose проходит реальный гейт до DEPLOYED
# · Regression: расширение deny-set (REF-0006) не должно ложноблокировать легитимные payload
#   (named volume + все L1-контракты) — regression risk карточки «allowlist минимален»
# · Last fail: N/A
# · Remove if: состав L1-контрактов меняется
def test_deploy_valid_compose_passes_real_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Валидный проект (named volume) → gate PASS → delivery → compose → DEPLOYED."""
    proj = _write_project(tmp_path, "goodproj", volumes_lines=['      - "appdata:/var/lib/app"'])
    orch, channel = _make_orch(tmp_path, monkeypatch)

    with caplog.at_level(logging.INFO):
        result = orch.deploy(project_name="goodproj", channel=channel, project_dir=str(proj))

    assert result.status == DeployStatus.DEPLOYED, f"валидный проект обязан деплоиться: {result.to_dict()}"
    assert len(channel.deliver_calls) == 1, "delivery должен быть выполнен ровно один раз"
    assert any("[IMP:9]" in r.message and "l1-gate] PASS" in r.message for r in caplog.records), (
        "LDD: нет IMP:9 PASS лога гейта"
    )


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · сломанный YAML блокируется l1_only-гейтом deploy()
# · Last fail: «Сломанный YAML проходит L1 (parse filed as L2-warning)» — evidence REF-0006
# · Remove if: l1_only parse-fail severity меняется
def test_deploy_broken_yaml_blocked_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Непарсящийся compose в target_dir → FAILED до delivery (compose-config-valid elevated)."""
    broken = "services:\n  app:\n    image: busybox\n   broken_indent: [\n"
    proj = _write_project(tmp_path, "brokenproj", raw_compose=broken)
    orch, channel = _make_orch(tmp_path, monkeypatch)

    with caplog.at_level(logging.INFO):
        result = orch.deploy(project_name="brokenproj", channel=channel, project_dir=str(proj))

    assert result.status == DeployStatus.FAILED
    assert "compose-config-valid" in (result.error_info or ""), (
        f"error_info должен называть контракт: {result.error_info!r}"
    )
    assert channel.deliver_calls == []


# 🧪 TRAP[TEST] · 2026-08-25 · unit · ошибка гейта → fail-CLOSED
# · Regression: security-гейт, падающий в open, = дыра; любое исключение гейта блокирует деплой
# · Last fail: N/A (new contract)
# · Remove if: fail-closed семантика меняется (запрещено для security-гейтов)
def test_deploy_gate_error_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Исключение в pre_apply_gate → FAILED (fail-closed), delivery не выполняется."""

    def _exploding_gate(_project_dir: str, _project_name: str) -> VerifyReport:
        msg = "gate infrastructure exploded"
        raise RuntimeError(msg)

    proj = _write_project(tmp_path, "anyproj")
    orch, channel = _make_orch(tmp_path, monkeypatch, pre_apply_gate=_exploding_gate)

    with caplog.at_level(logging.INFO):
        result = orch.deploy(project_name="anyproj", channel=channel, project_dir=str(proj))

    assert result.status == DeployStatus.FAILED
    assert "failed closed" in (result.error_info or ""), f"error_info без fail-closed: {result.error_info!r}"
    assert channel.deliver_calls == []
    rows = _audit_rows(tmp_path / "audit.log")
    assert rows and rows[-1]["result"] == "FAILED"


# 🧪 TRAP[TEST] · 2026-08-25 · unit · dry_run не исполняет гейт
# · Regression: dry_run семантика (DevPlan 089 AC10) — план без side effects; malicious compose
#   в dry-run возвращает SKIPPED, а не gate-FAILED
# · Last fail: N/A
# · Remove if: dry_run short-circuit порядок меняется
def test_deploy_dry_run_skips_l1_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dry_run=True на malicious compose → SKIPPED (гейт не доходит), plan напечатан."""
    proj = _write_project(tmp_path, "dryproj", volumes_lines=['      - "/var/run/docker.sock:/sock"'])
    orch, channel = _make_orch(tmp_path, monkeypatch)

    result = orch.deploy(project_name="dryproj", channel=channel, project_dir=str(proj), dry_run=True)

    assert result.status == DeployStatus.SKIPPED
    assert channel.deliver_calls == [], "dry_run не должен доставлять payload"
