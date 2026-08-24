# GREP_SUMMARY: test-receive-flow-atomicity, T9.8, atomic-staging, payload-backup, rollback, payload-restore, T9.9, max-payload-bytes, streaming, reject, pre-deploy-gate
# STRUCTURE: ▶ test_*_atomic_staging → staging-copy → os.replace: 0 stage-мусора, файлы целы │ ▶ test_*_rollback_restores_payload → backup → _rollback_deploy → target = v1 │ ▶ test_*_max_payload_reject → stdin > MAX → JSON FAILED + exit 1 │ ▶ test_*_max_payload_ok → в пределах лимита → pass
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.8 (L-6) и T9.9 (L-7) DevPlan 136 W9: атомарная замена
##           payload (staging-copy → per-file os.replace, без частичных файлов), бэкап
##           существующих payload-файлов + восстановление при rollback (не только compose),
##           MAX_PAYLOAD_BYTES (env-configurable, потоковое чтение, reject при превышении).
##           DevPlan 176 A.2: staging-compose L1-валидный (receive исполняет pre-deploy L1-гейт).
## @scope    unit-тесты: tmp_path; fake-оркестратор для flow.deploy; monkeypatch
##           MAX_PAYLOAD_BYTES для reject-пути (не читаем 1GiB в тесте).
## @invariants
##   - Native imports; tmp_path; LDD IMP:9 в успешных сценариях
##   - Rollback восстанавливает payload-файлы из backup (T9.8) ДО compose-rollback
##   - Превышение MAX_PAYLOAD_BYTES → JSON {"status":"FAILED"} + exit 1 (R5-negative)
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: test_receive_flow_atomicity.py — rollback
##            восстанавливает payload-файлы; reject при превышении лимита.
## @changes  2026-08-05 · Created (DevPlan 136 W9)
## @changes  2026-08-16 · DevPlan 176 A.2 — staging-compose переведён на L1-валидный
##           (flow.deploy теперь исполняет pre-deploy L1-гейт)
# endregion MODULE_CONTRACT

import io
import json
import logging
from pathlib import Path

import pytest

from core.internal.deploy.orchestrator import DeployOrchestrator, DeployStatus
from core.internal.deploy.receive_flow import ReceiveFlow, _read_stdin_limited
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# ── L1-валидный staging-compose (176 A.2: flow.deploy исполняет pre-deploy L1-гейт) ──
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


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.8 — атомарная замена payload
# · Scenario: staging-copy → per-file os.replace: после deploy 0 `.payload-stage-*` мусора,
# ·   файлы в target полные (не обрезанные); os.replace атомарен (нет partial state)
# · Last fail: 2026-08-05 — файлы копировались из staging напрямую: сбой на середине оставлял
# ·   частично перезаписанные файлы (L-6)
# · Remove if: receive copy-логика меняется
@ldd_trajectory
def test_receive_deploy_atomic_staging_no_leftovers(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.8: atomic staging — 0 stage-мусора, файлы в target целые."""
    caplog.set_level(logging.INFO)
    from unittest.mock import MagicMock

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    staging = tmp_path / "staging"
    staging.mkdir()
    # 176 A.2: L1-валидный compose (receive исполняет pre-deploy L1-гейт)
    (staging / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")
    (staging / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")
    (staging / ".env.platform").write_text("PLATFORM_DOMAIN=example.com\n", encoding="utf-8")

    fake_orch = MagicMock()
    fake_orch.deploy.return_value = MagicMock(
        is_success=lambda: True,
        status=type("S", (), {"value": "DEPLOYED"})(),
        to_dict=lambda: {"status": "DEPLOYED"},
        version="sha1",
    )
    # 170 W10-B: orchestrator_factory — конструкторный DI
    flow = ReceiveFlow(
        projects_base=str(tmp_path / "projects"),
        orchestrator_factory=lambda *_, **__: fake_orch,
    )
    flow.deploy(
        "testproj",
        "testproj",
        "sha1",
        str(staging),
        str(target_dir),
        base=str(tmp_path / "projects"),
    )

    # Инвариант атомарности: никакого stage/backup мусора после deploy
    leftovers = [f.name for f in target_dir.iterdir() if f.name.startswith(".payload-")]
    assert not leftovers, f"stage/backup мусор после deploy: {leftovers}"
    compose = (target_dir / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose == _VALID_COMPOSE, f"файл обязан быть ПОЛНЫМ payload (не обрезан): {compose!r}"
    logger.critical("[IMP:9][test] atomic staging: no leftovers, files complete — OK (T9.8)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.8 — rollback восстанавливает payload-файлы
# · Scenario: backup (v1 payload) + target (v2, сломан) → _rollback_deploy(payload_backup_dir) →
# ·   target = v1 файлы (не только compose-rollback)
# · Last fail: 2026-08-05 — rollback восстанавливал ТОЛЬКО compose_state/previous_image (L-6)
# · Remove if: rollback semantics change
@ldd_trajectory
def test_rollback_restores_payload_files_from_backup(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.8: _rollback_deploy восстанавливает payload-файлы из backup ДО compose-rollback."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path / "locks"))  # deploy_history lock изоляция
    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    (target_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: BROKEN:v2\n", encoding="utf-8")

    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "docker-compose.yml").write_text("services:\n  web:\n    image: WORKING:v1\n", encoding="utf-8")

    # DI (W-H): субкласс с _rollback_compose override (0 instance-патчей)
    class _RollbackOrch(DeployOrchestrator):
        def _rollback_compose(self, _project_dir, _service, _snapshot):
            return True

    orch = _RollbackOrch(projects_base=str(tmp_path / "projects"))

    from core.internal.deploy.channels import LocalChannel

    result = orch._rollback_deploy(
        "testproj",
        LocalChannel(),
        "testproj",
        str(target_dir),
        snapshot={"snapshot_id": "snap-1", "compose_state": {}},
        start=0.0,
        payload_backup_dir=str(backup_dir),
    )

    assert result.status == DeployStatus.ROLLED_BACK
    restored = (target_dir / "docker-compose.yml").read_text(encoding="utf-8")
    assert "WORKING:v1" in restored, f"payload обязан восстановиться из backup: {restored!r}"
    assert "BROKEN:v2" not in restored, "сломанный payload заменён рабочим"
    logger.critical("[IMP:9][test] rollback restores payload files from backup — OK (T9.8)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.9 — превышение MAX_PAYLOAD_BYTES → reject
# · Scenario: stdin > MAX_PAYLOAD_BYTES → run() печатает JSON FAILED и возвращает 1 (ДО распаковки)
# · Last fail: 2026-08-05 — receive читал stdin без лимита (L-7: гигантский payload в память)
# · Remove if: payload limit semantics change
@ldd_trajectory
def test_receive_max_payload_bytes_rejects(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """T9.9: payload превышает MAX_PAYLOAD_BYTES → JSON FAILED + exit 1."""
    caplog.set_level(logging.INFO)
    import core.internal.deploy.receive_flow as rf

    big_payload = b"x" * (2048)  # 2× лимита

    # W4a: import-time env убран — лимит инжектится конструктором ReceiveFlow(max_payload_bytes=...)
    # DI (W-H): stream= io.BytesIO (0 патчей sys.stdin)
    rc = rf.ReceiveFlow(projects_base=str(tmp_path), max_payload_bytes=1024).run(
        project_name="testproj", version="sha1", stream=io.BytesIO(big_payload)
    )
    out = capsys.readouterr().out
    assert rc == 1, "превышение лимита → exit 1"
    assert json.loads(out.strip().splitlines()[-1])["status"] == "FAILED"
    assert "MAX_PAYLOAD_BYTES" in out
    logger.critical("[IMP:9][test] MAX_PAYLOAD_BYTES reject — OK (T9.9)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.9 — потоковое чтение в пределах лимита
# · Scenario: _read_stdin_limited возвращает байты при stdin ≤ MAX (chunked, не весь сразу)
# · Remove if: payload read semantics change
@ldd_trajectory
def test_read_stdin_limited_ok(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """T9.9: потоковое чтение stdin в пределах лимита возвращает байты."""
    caplog.set_level(logging.INFO)
    payload = b"tar-data-" * 1000  # ~9 KiB
    data = _read_stdin_limited(stream=io.BytesIO(payload))
    assert data == payload, "чтение в пределах лимита возвращает полные байты"
    logger.critical("[IMP:9][test] streaming read within limit — OK (T9.9)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.8 — metadata payload_backup_dir прокидывается в deploy
# · Scenario: flow.deploy вызывает orchestrator.deploy с metadata={"payload_backup_dir": ...}
# · Remove if: receive deploy metadata change
@ldd_trajectory
def test_receive_deploy_passes_payload_backup_metadata(
    caplog: pytest.LogCaptureFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T9.8: payload_backup_dir попадает в metadata deploy (для snapshot/rollback)."""
    caplog.set_level(logging.INFO)
    from unittest.mock import MagicMock

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    (target_dir / "docker-compose.yml").write_text("OLD-v1\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text("NEW-v2\n", encoding="utf-8")

    captured: dict = {}
    backup_content: dict[str, str] = {}
    fake_orch = MagicMock()
    fake_result = MagicMock(
        is_success=lambda: True, status=type("S", (), {"value": "DEPLOYED"})(), to_dict=lambda: {"status": "DEPLOYED"}
    )

    def _capture_deploy(*args, **kwargs):
        captured.update(kwargs)
        # Бэкап живёт ТОЛЬКО во время deploy (finally удаляет) — читаем контент ВНУТРИ вызова
        bdir = kwargs.get("metadata", {}).get("payload_backup_dir")
        if bdir:
            for f in Path(bdir).iterdir():
                backup_content[f.name] = f.read_text(encoding="utf-8")
        return fake_result

    fake_orch.deploy.side_effect = _capture_deploy

    # 170 W10-B: orchestrator_factory — конструкторный DI.
    # REF-0006: staging "NEW-v2\n" не парсится как compose — l1_only-гейт теперь БЛОКИРУЕТ
    # parse-fail; цель теста — payload-tx metadata, поэтому receive-гейт инжектится
    # пермиссивным (DI 176 A.2; сам гейт покрыт test_verify_contracts*.py).
    from core.internal.deploy.verify_contracts import VerifyReport

    flow = ReceiveFlow(
        projects_base=str(tmp_path / "projects"),
        orchestrator_factory=lambda *_, **__: fake_orch,
        pre_deploy_gate=lambda d, _project: VerifyReport(project_dir=Path(d), state="baseline", findings=()),
    )
    flow.deploy(
        "testproj",
        "testproj",
        "sha1",
        str(staging),
        str(target_dir),
        base=str(tmp_path / "projects"),
    )

    metadata = captured.get("metadata", {})
    backup_dir = metadata.get("payload_backup_dir")
    assert backup_dir, "payload_backup_dir обязан передаваться в metadata"
    # Бэкап содержит ПРЕДЫДУЩИЙ payload (v1), а не новый
    assert backup_content.get("docker-compose.yml") == "OLD-v1\n", (
        f"бэкап обязан содержать предыдущий payload (v1): {backup_content!r}"
    )
    logger.critical("[IMP:9][test] payload_backup_dir in deploy metadata — OK (T9.8)")
