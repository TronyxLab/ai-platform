# GREP_SUMMARY: test-receive-flow-payload-tx, REF-0105, crash-injection, restore-from-backup, stale-compose, orphan-sweep, payload-backup, transactional, half-applied
# STRUCTURE: ▶ test_crash_injection ┌replace#2 raises → restore из backup ВНЕ target → backup rmtree┐ │ ▶ test_stale_compose_deleted │ ▶ test_orphan_sweep (age threshold) │ ▶ test_no_tmp_leftovers
# region MODULE_CONTRACT
## @purpose  Regression-тесты payload-транзакции receive (REF-0105, DevPlan 11 В1):
##           crash-injection между replace'ами → восстановление из backup (вне target_dir);
##           удаление канонических compose-имён, отсутствующих в staging (stale-compose
##           переживал переименование и побеждал по резолюции); prefix-sweep orphan tmpdir;
##           отсутствие tmp-мусора после успешной транзакции.
## @scope    unit; tmp_path; fake-оркестратор через orchestrator_factory DI; LDD IMP:9.
## @invariants
##   - Native imports; LDD IMP:9 в каждом сценарии (anti-illusion)
##   - Crash-injection использует RuntimeError (НЕ OSError) — restore обязан работать
##     для ЛЮБОГО типа исключения (finally-семантика, а не except-фильтр)
## @rationale  $TEST_SPEC REF-0105: crash-injection unit (исключение между replace'ами →
##            восстановление); stale-compose deletion unit; orphan-sweep unit.
## @changes  2026-08-24 · Created (REF-0105, meta-refactoring В1)
# endregion MODULE_CONTRACT

import logging
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.internal.deploy.receive_flow import (
    ReceiveFlow,
    sweep_orphan_payload_tmpdirs,
)
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


def _make_flow(tmp_path: Path, orch: MagicMock) -> ReceiveFlow:
    """ReceiveFlow с fake-оркестратором (конструкторный DI, 170 W10-B)."""
    return ReceiveFlow(
        projects_base=str(tmp_path / "projects"),
        orchestrator_factory=lambda *_, **__: orch,
    )


def _fake_orch_success() -> MagicMock:
    return MagicMock(
        deploy=MagicMock(
            return_value=MagicMock(
                is_success=lambda: True,
                to_dict=lambda: {"status": "DEPLOYED"},
                version="sha1",
            )
        )
    )


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION (R5 negative) · REF-0105/DATA-101 — crash-injection payload tx
# · Scenario: исключение (RuntimeError) на ВТОРОМ os.replace → restore_payload_from_backup
# ·   вернул OLD compose из backup (вне target_dir), НОВЫЙ файл упавшей tx удалён,
# ·   backup-dir прибран finally (раньше rmtree уничтожал единственную rollback-копию).
# · Last fail: 2026-08-24 — finally-rmtree убивал backup при любом исключении до
# ·   orchestrator-rollback → half-applied payload без средств отката (DATA-101≡DATA-704).
# · Remove if: payload transaction semantics change
@ldd_trajectory
def test_crash_between_replaces_restores_from_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REF-0105: исключение между replace'ами → восстановление из backup вне target."""
    caplog.set_level(logging.INFO)

    # Герметичность (xdist): tx-tmpdir создаются в ИЗОЛИРОВАННОМ tempdir этого теста,
    # иначе параллельные воркеры видят чужие активные payload-backup-* в общем TMPDIR.
    monkeypatch.setattr("core.internal.deploy.receive_flow.tempfile.gettempdir", lambda: str(tmp_path))

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    (target_dir / "docker-compose.yml").write_text("OLD-v1\n", encoding="utf-8")

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")  # заменяет OLD
    (staging / ".env.platform").write_text("NEW-ENV=1\n", encoding="utf-8")  # новый файл tx

    # Crash-injection: второй Path.replace кидает RuntimeError («между replace'ами»)
    real_replace = Path.replace
    replace_calls = {"n": 0}

    def fake_replace(self: Path, target: str):
        replace_calls["n"] += 1
        if replace_calls["n"] == 2:
            msg = "injected crash between replaces (REF-0105 test)"
            raise RuntimeError(msg)
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fake_replace)

    flow = _make_flow(tmp_path, _fake_orch_success())
    with pytest.raises(RuntimeError, match="injected crash"):
        flow.deploy("testproj", "testproj", "sha1", str(staging), str(target_dir), base=str(tmp_path / "projects"))

    assert replace_calls["n"] >= 2, "crash обязан произойти ПОСЛЕ первого replace"
    restored = (target_dir / "docker-compose.yml").read_text(encoding="utf-8")
    assert restored == "OLD-v1\n", f"payload обязан откатиться к OLD-v1 из backup: {restored!r}"
    assert not (target_dir / ".env.platform").exists(), "новый файл упавшей tx обязан быть удалён"

    # Backup/staging tmpdir не утекают: ни одного нового payload-* каталога в изолированном tmp
    leftovers = [d.name for d in tmp_path.glob("payload-*") if d.is_dir()]
    assert not leftovers, f"payload-tx tmpdir обязан прибираться даже при crash: {leftovers}"
    logger.critical("[IMP:9][test] crash between replaces → restored from OUT-of-target backup (REF-0105)")


# 🧪 TRAP[TEST] · 2026-08-24 · REGRESSION (R5 negative) · REF-0105/DATA-703 — stale-compose deletion
# · Scenario: в target лежит СТАРЫЙ compose.yaml (переименование в docker-compose.yml),
# ·   staging доставляет только docker-compose.yml → stale compose.yaml УДАЛЯЕТСЯ
# ·   (иначе побеждает по резолюции COMPOSE_FILENAMES — нода гоняет старый конфиг).
# · Last fail: 2026-08-24 — переименованный compose.yaml переживал доставку с зелёным CI.
# · Remove if: stale-file policy change
@ldd_trajectory
def test_stale_canonical_compose_removed_when_absent_in_staging(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """REF-0105: канонические compose-имена вне staging удаляются из target."""
    caplog.set_level(logging.INFO)

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    (target_dir / "compose.yaml").write_text("STALE-RETIRED-COMPOSE\n", encoding="utf-8")

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")
    (staging / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")

    flow = _make_flow(tmp_path, _fake_orch_success())
    result = flow.deploy("testproj", "testproj", "sha1", str(staging), str(target_dir), base=str(tmp_path / "projects"))
    assert result.is_success()

    assert not (target_dir / "compose.yaml").exists(), "stale compose.yaml обязан быть удалён"
    delivered = (target_dir / "docker-compose.yml").read_text(encoding="utf-8")
    assert delivered == _VALID_COMPOSE, f"новый compose доставлен: {delivered[:60]!r}…"
    logger.critical("[IMP:9][test] stale canonical compose absent in staging removed (DATA-703 closed)")


# 🧪 TRAP[TEST] · 2026-08-24 · Regression · REF-0105 — prefix-sweep orphan tmpdir
# · Scenario: crashed receive оставил payload-backup-X/payload-stage-Y; sweep удаляет только
# ·   старше порога (защита активного параллельного receive), чужие префиксы не трогает.
# · Remove if: sweep policy change
@ldd_trajectory
def test_orphan_sweep_removes_only_aged_canonical_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REF-0105: sweep — возрастной порог + канонические префиксы + счётчик."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr("core.internal.deploy.receive_flow.tempfile.gettempdir", lambda: str(tmp_path))

    old_backup = tmp_path / "payload-backup-old"
    old_stage = tmp_path / "payload-stage-old"
    fresh_backup = tmp_path / "payload-backup-fresh"  # активный параллельный receive
    alien = tmp_path / "payload-alien"  # неканонический префикс — не наш
    for d in (old_backup, old_stage, fresh_backup, alien):
        d.mkdir()

    now = time.time()
    aged = now - 7200.0  # 2h > порога 1h
    os.utime(old_backup, (aged, aged))
    os.utime(old_stage, (aged, aged))
    os.utime(alien, (aged, aged))  # старый, но ЧУЖОЙ префикс

    removed = sweep_orphan_payload_tmpdirs(now=now)

    assert removed == 2, f"удалены ровно 2 aged-каталога канонических префиксов: got {removed}"
    assert not old_backup.exists() and not old_stage.exists(), "aged orphan-ы выметены"
    assert fresh_backup.exists(), "СВЕЖИЙ tmpdir активного receive НЕ тронут"
    assert alien.exists(), "чужой префикс не выметается"
    logger.critical("[IMP:9][test] orphan sweep: age-threshold + prefix-scoped removal")


# 🧪 TRAP[TEST] · 2026-08-24 · Regression · T9.8/REF-0105 — успех не оставляет мусора
# · Scenario: успешная tx — ни backup-, ни stage-catalogов в system tmp, target полон.
# · Remove if: tmp lifecycle change
@ldd_trajectory
def test_successful_tx_leaves_no_tmp_leftovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """REF-0105/T9.8: после успешного deploy нет payload-*-мусора, файлы целы."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr("core.internal.deploy.receive_flow.tempfile.gettempdir", lambda: str(tmp_path))
    before = {d.name for d in tmp_path.glob("payload-*")}

    target_dir = tmp_path / "projects" / "testproj"
    target_dir.mkdir(parents=True)
    (target_dir / "docker-compose.yml").write_text("OLD-v1\n", encoding="utf-8")

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "docker-compose.yml").write_text(_VALID_COMPOSE, encoding="utf-8")
    (staging / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")

    flow = _make_flow(tmp_path, _fake_orch_success())
    result = flow.deploy("testproj", "testproj", "sha1", str(staging), str(target_dir), base=str(tmp_path / "projects"))
    assert result.is_success()

    after = {d.name for d in tmp_path.glob("payload-*")}
    assert after == before, f"tx обязана не оставлять новых tmpdir: {after - before}"
    assert (target_dir / "docker-compose.yml").read_text(encoding="utf-8") == _VALID_COMPOSE
    logger.critical("[IMP:9][test] successful tx leaves zero tmp leftovers, files complete")
