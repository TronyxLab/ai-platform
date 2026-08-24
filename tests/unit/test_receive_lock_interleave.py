# GREP_SUMMARY: test-receive-lock-interleave, REF-0011, flock-before-copy, interleave, concurrent-receive, mixed-payload, serialization, DATA-302, locked-by-pid
# STRUCTURE: ▶ raw-flock holder («чужой» receive-процесс) → ◇ flow.run → flock poll (timeout↓DI) → ⚡ JSON FAILED «Concurrent deploy blocked» + factory НЕ вызван │ ▶ release → повторный run → rc=0, deploy прошёл
# region MODULE_CONTRACT
## @purpose  Interleave-тест copy-vs-lock (REF-0011 карточка): конкурентный receive ОБЯЗАН
##           блокироваться на per-project flock ДО входа в deploy-фазу (копирование payload)
##           и получить JSON FAILED по таймауту, пока другой процесс держит лок — интерливинг
##           os.replace = mixed payload (DATA-302≡DATA-806) невозможен.
## @scope    unit; второй receive симулируется RAW-FLOCK holder'ом (вне reentrant-реестра):
##           на ноде receives — отдельные ПРОЦЕССЫ (ssh forced-command per-connection),
##           process-wide реестр file_lock их не объединяет — flock межпроцессен по построению.
## @invariants
##   - Native imports; tmp_path; PLATFORM_LOCK_DIR → tmp; LDD IMP:9 (anti-illusion)
##   - Таймаут лока receive инжектируется monkeypatch модульной константы
##     (_RECEIVE_LOCK_TIMEOUT читается при вызове run()) — тест не ждёт DEPLOY_TIMEOUT
## @rationale  $TEST_SPEC REF-0011: interleave-тест copy-vs-lock — payload копируется
##            ПОД локом, а не до него; паттерн holder'а — test_deploy_concurrent_lock.py.
## @changes  2026-08-24 · Created (REF-0011, meta-refactoring В1)
# endregion MODULE_CONTRACT

import fcntl
import io
import json
import logging
import os
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.internal.deploy.receive_flow as rf_module
from core.internal.deploy.receive_flow import ReceiveFlow
from core.internal.shared.file_lock import platform_lock_path
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_VALID_COMPOSE = """\
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


def _tar_bytes(project: str) -> io.BytesIO:
    """In-memory tar.gz payload: ai-platform.yaml + L1-валидный compose."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        ai = json.dumps({"name": project}).encode()
        info = tarfile.TarInfo(name="ai-platform.yaml")
        info.size = len(ai)
        tar.addfile(info, io.BytesIO(ai))
        compose = _VALID_COMPOSE.encode()
        info2 = tarfile.TarInfo(name="docker-compose.yml")
        info2.size = len(compose)
        tar.addfile(info2, io.BytesIO(compose))
    buf.seek(0)
    return buf


def _hold_raw_flock(lock_path: str, holder_pid: int) -> int:
    """Raw-flock holder — имитация ЧУЖОГО receive-процесса (вне reentrant-реестра)."""
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.ftruncate(fd, 0)
    os.write(fd, str(holder_pid).encode())
    return fd


def _success_orch_factory(invoked: dict):
    """Фабрика оркестратора с счётчиком вызовов: invoke = вошли в deploy-фазу (за локом)."""

    def _factory(*_args, **_kwargs):
        invoked["n"] += 1
        orch = MagicMock()
        orch.deploy.return_value = MagicMock(
            is_success=lambda: True, to_dict=lambda: {"status": "DEPLOYED"}, version="sha"
        )
        return orch

    return _factory


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION (R5 negative) · REF-0011/DATA-302 — copy-vs-lock interleave
# · Scenario: чужой процесс держит per-project flock (активный receive); второй run()
# ·   поллит лок (укороченный таймаут DI) → JSON FAILED «Concurrent deploy blocked» +
# ·   orchestrator_factory НИ РАЗУ не вызван (копирование payload не началось).
# · Last fail: 2026-08-24 — payload копировался ДО взятия лока → mixed payload с зелёным CI.
# · Remove if: flock-perimeter semantics change
@ldd_trajectory
def test_concurrent_receive_blocked_before_copy_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """REF-0011: copy-vs-lock — второй receive блокируется ДО копирования payload."""
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("PLATFORM_LOCK_DIR", str(tmp_path / "locks"))
    # Укороченный poll-таймаут (константа читается в run() при вызове) — тест не ждёт 900s
    monkeypatch.setattr(rf_module, "_RECEIVE_LOCK_TIMEOUT", 0.7)

    fd_holder = _hold_raw_flock(platform_lock_path("interproj"), holder_pid=88888)
    try:
        invoked: dict = {"n": 0}
        flow_b = ReceiveFlow(
            projects_base=str(tmp_path / "projects"), orchestrator_factory=_success_orch_factory(invoked)
        )
        rc = flow_b.run(project_name="interproj", version="shaB", stream=_tar_bytes("interproj"))

        assert rc == 1, "конкурентный receive обязан завершиться rc=1"
        out_lines = capsys.readouterr().out.strip().splitlines()
        payload = json.loads(out_lines[-1])
        assert payload["status"] == "FAILED", f"JSON FAILED контракт: {payload}"
        assert "Concurrent deploy blocked" in payload["error"], payload["error"]
        assert "88888" in payload["error"], f"PID владельца в ошибке: {payload['error']}"
        assert invoked["n"] == 0, (
            "orchestrator_factory НЕ вызван: копирование payload не начинается под чужим локом "
            "(flock-before-copy, DATA-302)"
        )
        logger.critical("[IMP:9][test] concurrent receive blocked BEFORE copy phase (JSON FAILED + PID)")
    finally:
        fcntl.flock(fd_holder, fcntl.LOCK_UN)
        os.close(fd_holder)

    # После освобождения лока тот же flow проходит до deploy-фазы (сериализация, не вечный бан)
    invoked_after: dict = {"n": 0}
    flow_c = ReceiveFlow(
        projects_base=str(tmp_path / "projects"), orchestrator_factory=_success_orch_factory(invoked_after)
    )
    rc_ok = flow_c.run(project_name="interproj", version="shaC", stream=_tar_bytes("interproj"))
    assert rc_ok == 0, f"после release лок свободен — receive проходит: rc={rc_ok}"
    # Фабрика резолвится на каждом _make_orchestrator: deploy + post-deploy chain = 2 вызова
    assert invoked_after["n"] == 2, "deploy-фаза достигнута (deploy + post-chain)"
    logger.critical("[IMP:9][test] after holder release the queued receive proceeds (serialization OK)")
