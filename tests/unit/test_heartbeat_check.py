# GREP_SUMMARY: test-heartbeat-check s3-list staleness threshold out-of-band notify-critical readonly-creds DI 003-A2
# STRUCTURE: ┌FakeS3 (list_objects_v2)┐ → ○ list_heartbeats ({prefix}/heartbeat/ → {node: LastModified}) → ○ find_stale (>2ч) → ◇ stale? notify critical (notify_fn DI) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scripts/heartbeat_check.py (DevPlan 003 A2):
##           read-only creds gate, S3-list + авто-обнаружение нод, stale-порог,
##           notify critical (DI notify_fn), dry-run, S3-fail → exit 1 (тихий отказ запрещён).
## @scope    Pure Python — boto3 заменяется FakeS3 (0 реальных S3-вызовов); DI через
##           main(env, client_factory, notify_fn) — 0 monkeypatch (DevPlan 167 D4).
## @invariants
##   - S3_READONLY_* отсутствуют → RuntimeError (конфиг-ошибка, R4: NO_SERVICE = FAIL)
##   - Ключ {prefix}/heartbeat/{node}/heartbeat.json → node (паритет writer 162 W6-1)
##   - stale: LastModified старше порога (>2ч default) → notify critical
##   - dry-run: 0 уведомлений
##   - S3-ошибка → exit 1 (heartbeat-reader не молчит)
## @rationale  DevPlan 003 §TEST_SPEC: S3-list/стальность/порог (DI boto3).
## @changes  2026-08-16 | DevPlan 003 A2 — created
# endregion MODULE_CONTRACT

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest

import core.internal.scripts.heartbeat_check as hb

logger = logging.getLogger(__name__)


# region FAKE_S3


class FakeS3:
    """In-memory list_objects_v2: {key: LastModified} + optional get_object payloads + error injection."""

    def __init__(self, objects: dict[str, datetime] | None = None) -> None:
        self.objects: dict[str, datetime] = objects or {}
        self.payloads: dict[str, dict] = {}
        self.fail_with: Exception | None = None

    def list_objects_v2(self, **kwargs) -> dict:
        if self.fail_with is not None:
            raise self.fail_with
        prefix = kwargs.get("Prefix", "")
        contents = [
            {"Key": key, "LastModified": last} for key, last in sorted(self.objects.items()) if key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def get_object(self, Bucket: str | None = None, Key: str | None = None) -> dict:  # ruff: ignore[ARG002]
        """Возвращает payload объекта (003 A3) — {"Body": BytesIO}. Отсутствует → raise."""
        import io

        if Key not in self.payloads:
            msg = f"no payload for {Key}"
            raise ValueError(msg)
        return {"Body": io.BytesIO(json.dumps(self.payloads[Key]).encode("utf-8"))}


# endregion FAKE_S3


def _now() -> datetime:
    return datetime.now(timezone.utc)


# region READONLY_CREDS


# 🧪 TRAP[TEST] · Regression · Scenario: read-only креды обязательны (R4: отсутствие = FAIL, не skip)
# · Last fail: N/A (new — DevPlan 003 B5: отдельный read-only IAM, мастер-ключи не переиспользуются)
# · Remove if: контракт read-only кредов меняется
def test_build_s3_client_missing_readonly_creds(caplog) -> None:
    """S3_READONLY_* отсутствуют → RuntimeError (честный FAIL, не тихий skip)."""
    caplog.set_level(logging.INFO)
    from core.internal.shared.exceptions import PlatformError

    with pytest.raises(PlatformError, match=re.escape("S3_READONLY credentials missing")):
        hb.build_s3_client({"S3_BUCKET": "b"})
    logger.info("[IMP:9][test_hb_check] readonly-creds gate PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: build_s3_client с read-only кредами (fake Session)
# · Last fail: N/A (new — DevPlan 003 A2)
# · Remove if: build_s3_client переезжает на другой клиент
def test_build_s3_client_with_creds(caplog, monkeypatch) -> None:
    """С кредами build_s3_client доходит до boto3.Session (fake-session перехват)."""
    caplog.set_level(logging.INFO)
    import boto3

    fake_client = object()

    class _FakeSession:
        def __init__(self, **kwargs) -> None:
            self._kwargs = kwargs

        def client(self, *_args, **_kwargs):
            return fake_client

    monkeypatch.setattr(boto3, "Session", _FakeSession)
    client, bucket = hb.build_s3_client({
        "S3_READONLY_ACCESS_KEY": "ro-key",
        "S3_READONLY_SECRET_KEY": "ro-secret",
        "S3_BUCKET": "my-bucket",
    })
    assert client is fake_client and bucket == "my-bucket"
    logger.info("[IMP:9][test_hb_check] build_s3_client creds PASS")


# endregion READONLY_CREDS


# region LIST_HEARTBEATS


# 🧪 TRAP[TEST] · Regression · Scenario: list_heartbeats — авто-обнаружение нод из ключей
# · Last fail: N/A (new — DevPlan 003 A2: S3 list → node auto-detect)
# · Remove if: формат ключей heartbeat меняется
def test_list_heartbeats_node_detection(caplog) -> None:
    """{prefix}/heartbeat/{node}/heartbeat.json → {node: LastModified}; чужие объекты игнорируются."""
    caplog.set_level(logging.INFO)
    t1 = _now() - timedelta(hours=1)
    t2 = _now() - timedelta(hours=5)
    fake = FakeS3({
        "platform/backups/heartbeat/node-a/heartbeat.json": t1,
        "platform/backups/heartbeat/node-b/heartbeat.json": t2,
        "platform/backups/other/file.bin": _now(),  # не heartbeat — игнор
    })
    nodes = hb.list_heartbeats(fake, "bucket", "platform/backups")
    assert set(nodes) == {"node-a", "node-b"}
    assert nodes["node-a"] == t1 and nodes["node-b"] == t2
    logger.info("[IMP:9][test_hb_check] node auto-detection PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: list_heartbeats — S3-ошибка пробрасывается (exit 1)
# · Last fail: N/A (new — DevPlan 003 A2 инвариант 6: тихий отказ запрещён)
# · Remove if: S3-error контракт меняется
def test_list_heartbeats_s3_error_raises(caplog) -> None:
    """S3-ошибка → исключение (main → exit 1, тихий отказ запрещён)."""
    caplog.set_level(logging.INFO)
    fake = FakeS3()
    fake.fail_with = RuntimeError("s3 down")
    with pytest.raises(RuntimeError, match="s3 down"):
        hb.list_heartbeats(fake, "bucket", "platform/backups")
    logger.info("[IMP:9][test_hb_check] s3-error propagate PASS")


# endregion LIST_HEARTBEATS


# region FIND_STALE


# 🧪 TRAP[TEST] · Regression · Scenario: stale-порог >2ч (162 W6-1 validation)
# · Last fail: N/A (new — DevPlan 003 A2)
# · Remove if: порог стальности меняется
def test_find_stale_threshold(caplog) -> None:
    """LastModified старше порога → stale; ровно на границе — свежий."""
    caplog.set_level(logging.INFO)
    now = _now()
    nodes = {
        "fresh": now - timedelta(hours=1),
        "edge": now - timedelta(hours=2),  # ровно 2ч — НЕ stale (age > threshold)
        "dead": now - timedelta(hours=7),
    }
    stale = hb.find_stale(nodes, stale_hours=2.0, now=now)
    assert [n for n, _ in stale] == ["dead"], f"ожидался только dead, got {stale}"
    assert abs(stale[0][1] - 7.0) < 0.01
    logger.info("[IMP:9][test_hb_check] stale threshold PASS")


# endregion FIND_STALE


# region MAIN


# 🧪 TRAP[TEST] · Regression · Scenario: main — свежие ноды → exit 0, notify НЕ вызывается
# · Last fail: N/A (new — DevPlan 003 A2)
# · Remove if: main-контракт меняется
def test_main_fresh_nodes_no_notify(caplog) -> None:
    """Все ноды свежие → exit 0, notify_fn не вызывается."""
    caplog.set_level(logging.INFO)
    notified: list[list[str]] = []

    def _factory() -> tuple[object, str]:
        fresh = _now() - timedelta(minutes=30)
        return FakeS3({"platform/backups/heartbeat/node-a/heartbeat.json": fresh}), "bucket"

    rc = hb.main([], env={"S3_PREFIX": "platform/backups"}, client_factory=_factory, notify_fn=notified.append)
    assert rc == 0
    assert notified == [], "свежие ноды — без уведомлений"
    logger.info("[IMP:9][test_hb_check] fresh nodes PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: main — stale ноды → notify_fn с деталями, exit 0
# · Last fail: N/A (new — DevPlan 003 A2 AC-2: stale >2ч → критический алерт извне)
# · Remove if: main-контракт меняется
def test_main_stale_nodes_notify_critical(caplog) -> None:
    """Stale ноды → notify_fn (детали node+age), exit 0."""
    caplog.set_level(logging.INFO)
    notified: list[list[str]] = []

    def _factory() -> tuple[object, str]:
        dead = _now() - timedelta(hours=5)
        return FakeS3({"platform/backups/heartbeat/node-b/heartbeat.json": dead}), "bucket"

    rc = hb.main(
        ["--stale-hours", "2"],
        env={"S3_PREFIX": "platform/backups"},
        client_factory=_factory,
        notify_fn=notified.append,
    )
    assert rc == 0
    assert len(notified) == 1 and any("node-b" in d and "5.0h" in d for d in notified[0]), f"got {notified}"
    logger.info("[IMP:9][test_hb_check] stale notify PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: main — dry-run: план без уведомлений, exit 0
# · Last fail: N/A (new — DevPlan 003 A2)
# · Remove if: dry-run контракт меняется
def test_main_dry_run_no_notify(caplog) -> None:
    """--dry-run при stale: notify НЕ вызывается (0 мутаций), exit 0."""
    caplog.set_level(logging.INFO)
    notified: list[list[str]] = []

    def _factory() -> tuple[object, str]:
        dead = _now() - timedelta(hours=9)
        return FakeS3({"platform/backups/heartbeat/node-c/heartbeat.json": dead}), "bucket"

    rc = hb.main(
        ["--dry-run"], env={"S3_PREFIX": "platform/backups"}, client_factory=_factory, notify_fn=notified.append
    )
    assert rc == 0
    assert notified == [], "dry-run: 0 уведомлений"
    logger.info("[IMP:9][test_hb_check] dry-run PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: 003 A3 — нода жива, но tor_chain_down в payload
# · Expect: tor_notify_fn вызывается с деталями ноды ДАЖЕ при свежем heartbeat; notify_fn
#   (stale) НЕ вызывается; exit 0
# · Last fail: N/A (new — DevPlan 003 A3: canary читается out-of-band)
# · Remove if: tor.chain_down-контракт меняется
def test_main_tor_chain_down_notify_fresh_heartbeat(caplog) -> None:
    """Свежий heartbeat + tor_chain_down=true → tor_notify_fn, exit 0, без stale-нотификации."""
    caplog.set_level(logging.INFO)
    stale_notified: list[list[str]] = []
    tor_notified: list[list[str]] = []

    def _factory() -> tuple[object, str]:
        fresh = _now() - timedelta(minutes=10)
        fake = FakeS3({"platform/backups/heartbeat/node-a/heartbeat.json": fresh})
        fake.payloads["platform/backups/heartbeat/node-a/heartbeat.json"] = {
            "ts": fresh.isoformat(),
            "ok": True,
            "node": "node-a",
            "tor_chain_down": True,
        }
        return fake, "bucket"

    rc = hb.main(
        [],
        env={"S3_PREFIX": "platform/backups"},
        client_factory=_factory,
        notify_fn=stale_notified.append,
        tor_notify_fn=tor_notified.append,
    )
    assert rc == 0
    assert stale_notified == [], f"stale-нотификация не должна сработать: {stale_notified}"
    assert len(tor_notified) == 1, f"tor_notify_fn должен получить 1 вызов: {tor_notified}"
    assert any("node-a" in d and "tor-chain-state red" in d for d in tor_notified[0]), f"got {tor_notified}"
    logger.info("[IMP:9][test_hb_check] tor.chain_down notify PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · payload с tor_chain_down=false → тишина (003 A3)
# · Last fail: N/A (new — DevPlan 003 A3)
# · Remove if: canary-контракт меняется
def test_main_tor_chain_green_no_notify(caplog) -> None:
    """tor_chain_down=false → никаких tor-уведомлений."""
    caplog.set_level(logging.INFO)
    tor_notified: list[list[str]] = []

    def _factory() -> tuple[object, str]:
        fresh = _now() - timedelta(minutes=10)
        fake = FakeS3({"platform/backups/heartbeat/node-a/heartbeat.json": fresh})
        fake.payloads["platform/backups/heartbeat/node-a/heartbeat.json"] = {
            "ts": fresh.isoformat(),
            "ok": True,
            "node": "node-a",
            "tor_chain_down": False,
        }
        return fake, "bucket"

    rc = hb.main(
        [],
        env={"S3_PREFIX": "platform/backups"},
        client_factory=_factory,
        notify_fn=lambda _details: None,
        tor_notify_fn=tor_notified.append,
    )
    assert rc == 0
    assert tor_notified == [], f"green canary — 0 уведомлений: {tor_notified}"
    logger.info("[IMP:9][test_hb_check] tor green no-notify PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: main — S3-ошибка → exit 1 (heartbeat-reader не молчит)
# · Last fail: N/A (new — DevPlan 003 A2 инвариант 6)
# · Remove if: exit-контракт S3-ошибки меняется
def test_main_s3_error_exit_1(caplog) -> None:
    """S3-ошибка → exit 1 + IMP:10 (честный FAIL, не skip)."""
    caplog.set_level(logging.INFO)
    bad = FakeS3()
    bad.fail_with = RuntimeError("network down")
    rc = hb.main([], env={"S3_PREFIX": "platform/backups"}, client_factory=lambda: (bad, "bucket"))
    assert rc == 1
    assert any("S3 FAIL" in r.message for r in caplog.records), "IMP:10 S3 FAIL маркер"
    logger.info("[IMP:9][test_hb_check] s3-error exit 1 PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: main — конфиг-ошибка кредов → exit 1 (R4)
# · Last fail: N/A (new — DevPlan 003 B5)
# · Remove if: read-only creds контракт меняется
def test_main_missing_readonly_creds_exit_1(caplog) -> None:
    """Креды не заданы (client_factory=None → реальный build) → exit 1 + честная ошибка."""
    caplog.set_level(logging.INFO)
    rc = hb.main([], env={"S3_BUCKET": "b"})  # S3_READONLY_* отсутствуют
    assert rc == 1
    assert any("S3_READONLY" in r.message for r in caplog.records)
    logger.info("[IMP:9][test_hb_check] missing creds exit 1 PASS")


# endregion MAIN
