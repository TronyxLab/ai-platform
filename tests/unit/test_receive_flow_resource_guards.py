"""
# GREP_SUMMARY: test-receive-flow-resource-guards, tar-bomb, uncompressed-ceiling, entry-cap, statvfs, disk-guard, payload-cap-64mib, REF-0015
# STRUCTURE: ▶ fixtures (tar-бомба нулей / многочленный tar / валидный payload) → ◇ ReceiveFlow.unpack guards → ⎋ ConfigValidationError | True │ ▶ run() → JSON FAILED + rc 1
# region MODULE_CONTRACT
## @purpose  REF-0015 (DevPlan 11 В2): resource guards receive-канала — stream-extract
##           uncompressed ceiling (маленькая tar-бомба fixture), entry-count cap,
##           statvfs guard (mock low-free), default compressed cap 64 MiB,
##           run()-JSON контракт forced-command при отказе guard'а.
## @scope    ReceiveFlow.unpack/run + константы receive_flow/app_config. Нативные импорты,
##           tmp_path, DI потолка/cap (0 monkeypatch констант кроме run()-JSON теста).
## @invariants
##   - Tar-бомба: высокий коэффициент сжатия (>100:1) — отказ наступает ДО исчерпания диска
##     (проверка заголовка члена предшествует записи; staging остаётся пустым)
##   - Entry-cap: превышение числа членов детектируется независимо от суммарного размера
##   - statvfs: f_bavail×f_frsize < ceiling → ConfigValidationError до открытия tar
##   - run(): нарушение guard'а → JSON {"status": "FAILED"} в stdout + exit 1 (контракт
##     forced-command — traceback диспетчеру недопустим)
##   - LDD IMP:10 на каждой reject-ветке (guard-ветки = бизнес-ассерты)
## @rationale SEC-0046 (HIGH·B12): cap по compressed-размеру не защищает от развёртывания;
##           1 MiB нулей на FS с postgres WAL = ENOSPC mid-extract = node-wide outage.
# endregion MODULE_CONTRACT
"""

import io
import json
import logging
import os
import tarfile
from pathlib import Path

import pytest

import core.internal.deploy.receive_flow as rf_module
from core.internal.deploy.receive_flow import (
    _DEFAULT_MAX_PAYLOAD_BYTES,
    _MAX_TAR_ENTRIES,
    _MAX_UNCOMPRESSED_CEILING_BYTES,
    ReceiveFlow,
)
from core.internal.shared.app_config import AppConfig
from core.internal.shared.exceptions import ConfigValidationError

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024


def _make_tar(files: dict[str, bytes]) -> bytes:
    """Build an in-memory tar.gz from {arcname: content}."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_bomb_tar(declared_mb: float = 8.0) -> tuple[bytes, int]:
    """Классическая tar-бомба: declared_mb нулей → KB-scale gzip (~1000:1).

    Returns (tar_bytes, declared_size_bytes)."""
    declared = int(declared_mb * _MIB)
    bomb = _make_tar({"bomb.bin": b"\0" * declared})
    assert len(bomb) * 100 < declared, "fixture обязан иметь коэффициент сжатия >100:1"
    return bomb, declared


def _make_payload_tar(tmp_path: Path) -> bytes:
    """Валидный payload канала receive (ai-platform.yaml — обязателен для validate())."""
    proj_dir = tmp_path / "payload-src"
    proj_dir.mkdir(exist_ok=True)
    (proj_dir / "ai-platform.yaml").write_text("name: testproj\n", encoding="utf-8")
    return _make_tar({"ai-platform.yaml": b"name: testproj\n", "notes.txt": b"tiny\n"})


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · REF-0015/SEC-0046 — маленькая tar-бомба
# · Scenario: 8 MiB нулей сжимаются в ~KB; ceiling=1 MiB → отказ ДО записи члена на диск.
# · Last fail: prior — extractall без ceiling: распаковка шла до ENOSPC mid-extract
# ·   (staging на одной FS с postgres WAL/docker layers = node-wide outage).
# · Remove if: uncompressed ceiling снимается (запрещено — SEC-0046)
def test_unpack_tar_bomb_rejected_before_disk_exhaustion(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Stream-extract ceiling: бомба >ceiling → ConfigValidationError, staging НЕ загрязнён."""
    caplog.set_level(logging.INFO)
    bomb, declared = _make_bomb_tar(declared_mb=8.0)
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(ConfigValidationError, match="uncompressed ceiling"):
        ReceiveFlow.unpack(bomb, str(staging), max_uncompressed_bytes=_MIB)

    # Отказ ДО исчерпания диска: oversized член НЕ записан (pre-write проверка заголовка)
    assert list(staging.iterdir()) == [], "член больше потолка не должен попасть на диск"
    assert "[IMP:10][ReceiveFlow][unpack] Uncompressed ceiling exceeded" in caplog.text
    logger.critical(
        "[IMP:9][test] REF-0015 PASS: tar-бомба (%d declared bytes, %d compressed) отклонена при ceiling=%d",
        declared,
        len(bomb),
        _MIB,
    )


# 🧪 TRAP[TEST] · 2026-08-25 · unit · REF-0015 — потолок не режет легитимные payload'ы
# · Regression: ceiling ×3+ от текущих легитимных payload'ов (KB-масштаб) — проход под потолком.
# · Remove if: ceiling semantics change
def test_unpack_under_ceiling_passes(tmp_path: Path) -> None:
    """Payload ≤ ceiling распаковывается полностью (файлы на месте, размеры верны)."""
    ceiling = 4 * _MIB
    data = b"x" * (512 * 1024)  # 512 KiB < 4 MiB
    tar_bytes = _make_tar({"asset.bin": data, "ai-platform.yaml": b"name: testproj\n"})
    staging = tmp_path / "staging"
    staging.mkdir()

    assert ReceiveFlow.unpack(tar_bytes, str(staging), max_uncompressed_bytes=ceiling) is True
    assert (staging / "asset.bin").stat().st_size == len(data)
    assert (staging / "ai-platform.yaml").is_file()


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · REF-0015 — entry-count cap
# · Scenario: 6 крошечных членов при cap=5 → ConfigValidationError (inode/FD-бомба ловится
# ·   независимо от суммарного размера).
# · Last fail: prior — extractall без cap: тысячи членов создавались до исчерпания inodes.
# · Remove if: entry-count cap снимается
def test_unpack_entry_count_cap_rejected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Entry-count cap: членов >cap → ConfigValidationError, [IMP:10] guard-лог."""
    caplog.set_level(logging.INFO)
    files = {f"f{i}.txt": b"tiny" for i in range(6)}
    tar_bytes = _make_tar(files)
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(ConfigValidationError, match="entry-count cap"):
        ReceiveFlow.unpack(tar_bytes, str(staging), max_entries=5)

    extracted = list(staging.iterdir())
    assert len(extracted) <= 5, f"после cap новые члены не создаются: {len(extracted)}"
    assert "[IMP:10][ReceiveFlow][unpack] Tar entry-count cap exceeded" in caplog.text
    logger.critical("[IMP:9][test] REF-0015 PASS: entry-count cap=5 поймал 6-членную бомбу")


# 🧪 TRAP[TEST] · 2026-08-25 · unit · REF-0015 — дефолтный cap не режет легитимный payload
# · Regression: дефолт _MAX_TAR_ENTRIES=512 ≫ типового payload'а (≤ ~20 файлов).
# · Remove if: дефолтный cap меняется
def test_unpack_default_entry_cap_passes_legit_payload(tmp_path: Path) -> None:
    """Легитимный многочленный payload проходит с дефолтным cap (без DI-параметров)."""
    files = {f"f{i}.txt": b"data" for i in range(20)}
    files["ai-platform.yaml"] = b"name: testproj\n"
    tar_bytes = _make_tar(files)
    staging = tmp_path / "staging"
    staging.mkdir()

    assert ReceiveFlow.unpack(tar_bytes, str(staging)) is True
    assert (staging / "f19.txt").is_file()
    assert _MAX_TAR_ENTRIES == 512, "дефолт entry-cap зафиксирован контрактом REF-0015"


# 🧪 TRAP[TEST] · 2026-08-25 · NEGATIVE (R5) · REF-0015/SEC-0046 — statvfs guard
# · Scenario: mock low-free (bavail×frsize < ceiling) → ConfigValidationError ДО открытия
# ·   tar — extract даже не начинается на переполненной ноде.
# · Last fail: prior — extract стартовал без проверки свободного места → ENOSPC mid-extract.
# · Remove if: statvfs guard снимается
def test_unpack_statvfs_guard_blocks_low_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """statvfs guard: free < ceiling → ConfigValidationError до любой записи."""
    caplog.set_level(logging.INFO)

    def _fake_statvfs(_path: str) -> os.statvfs_result:
        # frsize=4096, bavail=10 → free=40 KiB ≪ дефолтного ceiling 200 MB
        return os.statvfs_result((4096, 4096, 1000, 1000, 10, 100, 100, 90, 0, 256))

    monkeypatch.setattr(rf_module.os, "statvfs", _fake_statvfs)

    tar_bytes = _make_tar({"ai-platform.yaml": b"name: testproj\n"})
    staging = tmp_path / "staging"
    staging.mkdir()

    with pytest.raises(ConfigValidationError, match="Insufficient disk space"):
        ReceiveFlow.unpack(tar_bytes, str(staging))

    assert list(staging.iterdir()) == [], "extract не должен начинаться при low-free"
    assert "[IMP:10][ReceiveFlow][unpack] Insufficient disk headroom" in caplog.text
    logger.critical("[IMP:9][test] REF-0015 PASS: statvfs guard остановил extract при low-free")


# 🧪 TRAP[TEST] · 2026-08-25 · unit · REF-0015 — run(): JSON FAILED контракт forced-command
# · Scenario: потолок пробит через run() (monkeypatch модульной константы — run() не
# ·   прокидывает DI) → rc=1, stdout — машинный JSON FAILED (traceback диспетчеру
# ·   недопустим), orchestrator не создаётся.
# · Remove if: run() error-contract меняется
def test_run_tar_bomb_json_failed_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """run() c tar-бомбой → exit 1 + JSON FAILED с упоминанием uncompressed ceiling."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(rf_module, "_MAX_UNCOMPRESSED_CEILING_BYTES", 64 * 1024)

    bomb, declared = _make_bomb_tar(declared_mb=2.0)
    flow = ReceiveFlow(projects_base=str(tmp_path), orchestrator_factory=None)
    rc = flow.run(project_name="testproj", version="sha1", stream=io.BytesIO(bomb))

    captured = capsys.readouterr()
    assert rc == 1, "нарушение потолка → exit 1 (контракт forced-command)"
    payload = json.loads(captured.out.strip().splitlines()[-1])
    assert payload["status"] == "FAILED"
    assert "uncompressed ceiling" in payload["error"], payload
    assert "[IMP:10][ReceiveFlow][unpack] Uncompressed ceiling exceeded" in caplog.text
    logger.critical("[IMP:9][test] REF-0015 PASS: run() JSON FAILED для бомбы (%d declared bytes)", declared)


# 🧪 TRAP[TEST] · 2026-08-25 · unit · REF-0015 — default payload cap ↓ 64 MiB
# · Regression: SoT дефолта — app_config._DEFAULT_MAX_PAYLOAD_BYTES; константы обоих
# ·   модулей согласованы (×1000 запас над KB-легитимными payload'ами).
# · Remove if: дефолт пересматривается (TRAP[DECISION] у константы)
def test_default_compressed_payload_cap_is_64_mib() -> None:
    """Дефолт compressed-капа = 64 MiB в AppConfig и receive_flow (согласованы)."""
    expected = 64 * 1024 * 1024
    assert AppConfig.from_env({}).max_payload_bytes == expected, "SoT дефолт AppConfig — 64 MiB"
    assert expected == _DEFAULT_MAX_PAYLOAD_BYTES, "fallback-константа receive_flow согласована"
    assert _MAX_UNCOMPRESSED_CEILING_BYTES == 200 * 1024 * 1024, "uncompressed ceiling — 200 MB (карточка REF-0015)"
