# GREP_SUMMARY: backup-s3-client builder-overrides wal-sync-timeouts AI-0073 single-construction boto3 backup-cron
# STRUCTURE: ▶ monkeypatch boto3.client → ◇ build_boto3_s3_client(defaults|overrides) → ⊕ captured Config → ⎋ единая конструкция + RPO-константы wal_sync
# region MODULE_CONTRACT
## @purpose  AI-0073 (DevPlan 17 T2.4): один строитель s3_client.build_boto3_s3_client для
##           всего backup-cron; override-проброс работает; жёсткие wal_sync-тайминги
##           (10/30/×3) сохранены как именованная константа WAL_SYNC_S3_TIMEOUTS.
## @scope    tests/unit: monkeypatched boto3.client (без сети); source-scan AC.
## @invariants
##   - Дефолт строителя: connect 30 / read 60 / standard ×3
##   - Явные override доходят до botocore Config без искажений
##   - wal_sync тайминги НЕ изменились относительно прежних inline Config(10, 30, retries=3)
##   - boto3.client в коде backup-cron — только в s3_client.py
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path
from unittest import mock

import pytest

logger = logging.getLogger(__name__)

_SCRIPTS = Path(__file__).resolve().parents[2] / "core" / "modules" / "backup-cron" / "scripts"
sys.path.insert(0, str(_SCRIPTS))


def _capturing_boto3_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Подменяет модуль boto3 в sys.modules (builder импортирует boto3 лениво); захват kwargs."""
    captured: dict[str, object] = {}

    def _fake_client(service: str, **kwargs: object) -> object:
        captured["service"] = service
        captured.update(kwargs)
        return mock.MagicMock(name="s3-client")

    fake_boto3 = mock.MagicMock()
    fake_boto3.client = _fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    return captured


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · единый строитель с явными override (AI-0073)
# · Regression: клиент строился в трёх модулях с расползающимися таймаутами
#   (upload/retention 30/60 vs wal_sync 10/30) — «единственное место» было ложью вдвойне
# · Scenario: builder с дефолтами → Config(30/60/standard×3); builder с override →
#   значения пробрасываются без искажений; endpoint/креды передаются
# · Last fail: DevPlan 17 верификация @64c2090 (аудит AI-0073)
# · Remove if: backup-cron переезжает на общий platform S3-facade вне scripts/
def test_builder_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Дефолты и явные override строителя доходят до botocore Config."""
    captured = _capturing_boto3_client(monkeypatch)

    # ── дефолты ──
    from s3_client import build_boto3_s3_client

    build_boto3_s3_client(
        endpoint_url="https://s3.example.com",
        access_key="ak",
        secret_key="sk",
        region="eu-central-1",
    )
    assert captured["service"] == "s3"
    cfg = captured["config"]
    assert cfg.connect_timeout == 30, "дефолт connect_timeout=30"
    assert cfg.read_timeout == 60, "дефолт read_timeout=60"
    assert cfg.retries == {"max_attempts": 3, "mode": "standard"}
    assert captured["endpoint_url"] == "https://s3.example.com"

    # ── явный override (жёсткий wal_sync-бюджет) ──
    build_boto3_s3_client(
        endpoint_url=None,
        access_key=None,
        secret_key=None,
        region="ru",
        connect_timeout=10,
        read_timeout=30,
        max_attempts=3,
    )
    cfg2 = captured["config"]
    assert cfg2.connect_timeout == 10 and cfg2.read_timeout == 30, "override обязан пробрасываться без искажений"
    logger.critical("[IMP:9][test] builder defaults + overrides — OK (AI-0073)")


# 🧪 TRAP[TEST] · 2026-08-26 · P2 · wal_sync RPO-тайминги не изменились + AC grep
# · Regression: унификация могла бы незаметно ослабить wal_sync бюджет 10/30 → сдвиг RPO
# · Scenario: WAL_SYNC_S3_TIMEOUTS == (10, 30, 3); build_s3_client прокидывает их;
#   boto3.client встречается в коде backup-cron ТОЛЬКО в s3_client.py
# · Last fail: регрессия-охранник T2.4 (DevPlan 17)
# · Remove if: wal_sync получает отдельный transport-слой с собственным контрактом таймаутов
def test_wal_sync_timings_preserved() -> None:
    """Жёсткий RPO-бюджет wal_sync сохранён и прокидывается в строитель."""
    import wal_sync

    t = wal_sync.WAL_SYNC_S3_TIMEOUTS
    assert (t.connect, t.read, t.max_attempts) == (10, 30, 3), f"wal_sync тайминги обязаны остаться 10/30/×3: {t}"

    src = (_SCRIPTS / "wal_sync.py").read_text(encoding="utf-8")
    assert "WAL_SYNC_S3_TIMEOUTS.connect" in src and "WAL_SYNC_S3_TIMEOUTS.read" in src, (
        "wal_sync обязан прокидывать именованный бюджет в строитель"
    )

    # AC T2.4: boto3.client в КОДЕ backup-cron — только s3_client.py
    offenders: list[str] = []
    for py in _SCRIPTS.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "boto3.client(" in line and py.name != "s3_client.py":
                offenders.append(f"{py.name}:{line_no}")
            if "boto3.Session(" in line:
                offenders.append(f"{py.name}:{line_no} (Session)")
    assert not offenders, f"прямая конструкция клиента вне канона запрещена: {offenders}"
    logger.critical("[IMP:9][test] wal_sync timings preserved, single construction site — OK (T2.4)")
