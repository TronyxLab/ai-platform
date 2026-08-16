# GREP_SUMMARY: test-heartbeat dead-man-switch s3-object put-object overwrite dry-run node-namespace container-contract RPO 162-W6-1
# STRUCTURE: ┌FakeBoto (put/objects)┐ → ○ heartbeat key {prefix}/heartbeat/{node}/heartbeat.json → ○ put body {ts,ok,node} (overwrite) → ○ dry-run 0 мутаций → ○ S3-fail→exit 1
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/backup-cron/scripts/heartbeat.py (DevPlan 162 W6-1):
##           key construction, put body (ISO8601 ts/ok/node), overwrite-идемпотентность,
##           dry-run 0 мутаций, S3-fail → exit 1 (off-node сигнал жизни).
## @scope    Pure Python — boto3 заменяется FakeBoto (0 реальных S3-вызовов). scripts/ добавляется
##           в sys.path по канону module-specific paths (tests/AGENTS.md).
## @invariants
##   - Контейнерный контракт: heartbeat.py не импортирует core.internal (статическая проверка)
##   - S3-ключ: {prefix}/heartbeat/{node}/heartbeat.json (node-namespace)
##   - body JSON: ts = ISO8601 UTC, ok = true, node = NODE_NAME
##   - dry-run: 0 put_object (0 мутаций)
##   - S3-ошибка → exit 1 (heartbeat = off-node сигнал, тихий отказ запрещён)
## @rationale  DevPlan 162 W6-1 §TEST_SPEC: key construction + run logic с monkeypatched client
##            (паттерн test_wal_sync.py FakeBoto).
## @changes  2026-08-13 | DevPlan 162 W6-1 — created
# endregion MODULE_CONTRACT

import contextlib
import json
import logging
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "core" / "modules" / "backup-cron" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import heartbeat

logger = logging.getLogger(__name__)


# region FAKE_BOTO


class FakeBoto:
    """In-memory boto3 S3 client: put_object with real ClientError semantics."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []
        self.fail_put_with: Exception | None = None

    def put_object(self, Bucket: str | None = None, Key: str | None = None, Body=None) -> dict:  # ruff: ignore[ARG002]
        self.put_calls.append(Key)
        if self.fail_put_with is not None:
            raise self.fail_put_with
        self.objects[Key] = Body if isinstance(Body, bytes) else bytes(Body or b"")
        return {"ETag": "etag"}


# endregion FAKE_BOTO


# region CONTAINER_CONTRACT


# 🧪 TRAP[TEST] · Regression · Scenario: heartbeat.py не импортирует core.internal (контейнерный контракт)
# · Last fail: N/A (new test — DevPlan 162 W6-1; паттерн test_wal_sync.py)
# · Remove if: backup-cron образ начнёт включать core/internal (не планируется)
def test_heartbeat_no_core_internal_import(caplog) -> None:
    """Контейнерный контракт: 0 импортов core.internal в heartbeat.py."""
    caplog.set_level(logging.INFO)
    source = (_SCRIPTS_DIR / "heartbeat.py").read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("from core.internal", "import core.internal"))
    ]
    assert not import_lines, f"heartbeat.py импортирует core.internal: {import_lines}"
    assert "heartbeat" in sys.modules, "heartbeat должен импортироваться нативно (контейнерный runtime)"
    logger.info("[IMP:9][test_heartbeat] 0 core.internal imports (container contract PASS)")


# 🧪 TRAP[TEST] · Regression · Scenario: heartbeat нативно импортируется из scripts/ (как в контейнере)
# · Last fail: N/A (new test — DevPlan 162 W6-1)
# · Remove if: heartbeat переезжает в другой механизм доставки
def test_heartbeat_import_clean(caplog) -> None:
    """heartbeat импортируется чисто (цепочка как в образе)."""
    caplog.set_level(logging.INFO)
    assert "heartbeat" in sys.modules
    assert callable(heartbeat.heartbeat_run) and callable(heartbeat._heartbeat_key)
    logger.info("[IMP:9][test_heartbeat] native import chain OK")


# endregion CONTAINER_CONTRACT


# region KEY_CONSTRUCTION


# 🧪 TRAP[TEST] · Regression · Scenario: key construction {prefix}/heartbeat/{node}/heartbeat.json (162 W6-1)
# · Expect: prefix rstrip('/'), node-namespace, фиксированный файл heartbeat.json
# · Last fail: N/A (new test — DevPlan 162 W6-1)
# · Remove if: S3-структура heartbeat меняется
def test_heartbeat_key_construction(caplog) -> None:
    """_heartbeat_key: {prefix}/heartbeat/{node}/heartbeat.json (node-namespace)."""
    caplog.set_level(logging.INFO)
    assert heartbeat._heartbeat_key("platform/backups", "tronyx-vps") == (
        "platform/backups/heartbeat/tronyx-vps/heartbeat.json"
    )
    # prefix с trailing slash — rstrip('/')
    assert heartbeat._heartbeat_key("platform/backups/", "node2") == ("platform/backups/heartbeat/node2/heartbeat.json")
    logger.info("[IMP:9][test_heartbeat] key construction PASS")


# endregion KEY_CONSTRUCTION


# region RUN_LOGIC


# 🧪 TRAP[TEST] · Regression · Scenario: put_object с body {ts, ok, node} (162 W6-1)
# · Expect: S3-объект с ISO8601 ts, ok=true, node=NODE_NAME; возвращает key
# · Last fail: N/A (new test — DevPlan 162 W6-1)
# · Remove if: heartbeat body/контракт меняется
def test_heartbeat_run_puts_object(caplog) -> None:
    """heartbeat_run: put_object {prefix}/heartbeat/{node}/heartbeat.json с JSON body."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    key = heartbeat.heartbeat_run(fake, "test-bucket", "platform/backups", "tronyx-vps")

    assert key == "platform/backups/heartbeat/tronyx-vps/heartbeat.json"
    assert fake.put_calls == [key], f"put_calls: {fake.put_calls}"
    assert key in fake.objects, "объект должен быть в S3"
    body = json.loads(fake.objects[key].decode("utf-8"))
    assert body["ok"] is True, f"ok должен быть true: {body}"
    assert body["node"] == "tronyx-vps", f"node: {body}"
    assert "ts" in body and "T" in body["ts"] and body["ts"].endswith(("Z", "+00:00")), (
        f"ts не ISO8601 UTC: {body['ts']}"
    )
    logger.info("[IMP:9][test_heartbeat] heartbeat_run put PASS (key=%s)", key)


# 🧪 TRAP[TEST] · Regression · Scenario: overwrite-идемпотентность (162 W6-1)
# · Expect: повторный run перезаписывает объект (state-free), put_calls = 2
# · Last fail: N/A (new test — DevPlan 162 W6-1)
# · Remove if: heartbeat переходит на append/versioning (не планируется)
def test_heartbeat_run_overwrite_idempotent(caplog) -> None:
    """heartbeat_run идемпотентен: повторный прогон перезаписывает (не падает на существующем)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    heartbeat.heartbeat_run(fake, "test-bucket", "platform/backups", "node1")
    heartbeat.heartbeat_run(fake, "test-bucket", "platform/backups", "node1")

    assert len(fake.put_calls) == 2, f"должно быть 2 put (overwrite), got {fake.put_calls}"
    assert "platform/backups/heartbeat/node1/heartbeat.json" in fake.objects
    logger.info("[IMP:9][test_heartbeat] heartbeat_run overwrite idempotent PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: dry-run 0 мутаций (162 W6-1)
# · Expect: нет put_object, ключ возвращается, IMP:8 план
# · Last fail: N/A (new test — DevPlan 162 W6-1)
# · Remove if: dry-run семантика меняется
def test_heartbeat_dry_run_no_mutation(caplog) -> None:
    """dry_run=True: 0 put_object (0 мутаций)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    key = heartbeat.heartbeat_run(fake, "test-bucket", "platform/backups", "node1", dry_run=True)

    assert key == "platform/backups/heartbeat/node1/heartbeat.json"
    assert fake.put_calls == [], f"dry-run не должен PUT: {fake.put_calls}"
    assert fake.objects == {}, f"dry-run не должен создавать объекты: {fake.objects}"
    logger.info("[IMP:9][test_heartbeat] heartbeat dry-run 0 mutations PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · S3-ошибка → raise (exit 1 в main) — 162 W6-1
# · Last fail: N/A (new test — DevPlan 162 W6-1; тихий отказ = stale heartbeat без алерта)
# · Remove if: heartbeat перестаёт быть off-node сигналом (fallback на in-band)
def test_heartbeat_run_s3_failure_raises(caplog) -> None:
    """S3-ошибка в put_object → ClientError raise (main ловит → exit 1)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    fake.fail_put_with = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Denied"}, "ResponseMetadata": {}},
        "PutObject",
    )
    with pytest.raises(ClientError):
        heartbeat.heartbeat_run(fake, "test-bucket", "platform/backups", "node1")
    # put_object был вызван (не тихо пропущен) — объект НЕ создан
    assert fake.put_calls == ["platform/backups/heartbeat/node1/heartbeat.json"]
    assert "platform/backups/heartbeat/node1/heartbeat.json" not in fake.objects
    logger.info("[IMP:9][test_heartbeat] heartbeat S3-failure raises ClientError PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: main exit 0 на успехе (162 W6-1)
# · Expect: [IMP:9] HEARTBEAT OK + exit 0
# · Last fail: N/A (new test — DevPlan 162 W6-1)
# · Remove if: main контракт меняется
def test_heartbeat_main_exit_zero(caplog) -> None:
    """main: успешный прогон → exit 0, IMP:9 HEARTBEAT OK (DI: client_factory, 167 D4)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()

    with contextlib.redirect_stdout(Path("/dev/null").open("w", encoding="utf-8")):
        # DI (DevPlan 167 D4): client_factory → FakeBoto вместо monkeypatch build_s3_client/_env_str
        rc = heartbeat.main(["--dry-run"], client_factory=lambda: (fake, "test-bucket"))

    assert rc == 0, f"main должен вернуть 0 на dry-run, got {rc}"
    logger.info("[IMP:9][test_heartbeat] main exit 0 PASS")


# GUARD-PRESERVE (168): R5-negative (anti-survivorship) — S3-fail → main exit 1 (heartbeat = off-node сигнал, тихий отказ запрещён), пара test_heartbeat_main_exit_zero
# 🧪 TRAP[TEST] · NEGATIVE (R5) · S3-fail → main exit 1 (162 W6-1)
# · Last fail: N/A (new test — DevPlan 162 W6-1; heartbeat = off-node сигнал, exit 1 обязателен)
# · Remove if: heartbeat перестаёт требовать exit 1 при S3-ошибке
def test_heartbeat_main_s3_failure_exit_one(caplog) -> None:
    """main: S3-ошибка → [IMP:10] S3 FAIL + exit 1 (DI: client_factory, 167 D4)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    fake.fail_put_with = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Denied"}, "ResponseMetadata": {}},
        "PutObject",
    )

    # БЕЗ --dry-run — иначе put_object не вызывается и S3-ошибка не триггерится
    rc = heartbeat.main([], client_factory=lambda: (fake, "test-bucket"))

    assert rc == 1, f"main должен вернуть 1 при S3-ошибке, got {rc}"
    logger.info("[IMP:9][test_heartbeat] main S3-failure exit 1 PASS")


# endregion RUN_LOGIC


# region TOR_CANARY_003_A3


# 🧪 TRAP[TEST] · Regression · Scenario: tor-chain canary (003 A3) — red → tor_chain_down=true в payload
# · Expect: payload содержит "tor_chain_down": true при статусе red
# · Last fail: N/A (new — DevPlan 003 A3: canary мержится в S3-payload для out-of-band читателя)
# · Remove if: 003 A3-контракт canary меняется
def test_heartbeat_run_tor_chain_down_red(caplog, tmp_path: Path) -> None:
    """heartbeat_run: tor_chain_down=True → ключ в S3 body."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    key = heartbeat.heartbeat_run(fake, "test-bucket", "platform/backups", "tronyx-vps", tor_chain_down=True)

    body = json.loads(fake.objects[key].decode("utf-8"))
    assert body["tor_chain_down"] is True, f"tor_chain_down должен быть true: {body}"
    logger.info("[IMP:9][test_heartbeat] tor_chain_down=true payload PASS")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · canary отсутствует → ключ НЕ в payload (003 A3)
# · Last fail: N/A (new — DevPlan 003 A3)
# · Remove if: payload-контракт меняется
def test_heartbeat_run_tor_chain_unknown_absent(caplog) -> None:
    """tor_chain_down=None → ключ отсутствует в body (backward-compat со старым форматом)."""
    caplog.set_level(logging.INFO)
    fake = FakeBoto()
    key = heartbeat.heartbeat_run(fake, "test-bucket", "platform/backups", "tronyx-vps", tor_chain_down=None)

    body = json.loads(fake.objects[key].decode("utf-8"))
    assert "tor_chain_down" not in body, f"ключ не должен присутствовать: {body}"
    logger.info("[IMP:9][test_heartbeat] tor_chain_down absent PASS")


# 🧪 TRAP[TEST] · Regression · Scenario: canary-file парсинг (003 A3)
# · Expect: red → True, green → False, отсутствует/битый → None
# · Last fail: N/A (new — DevPlan 003 A3)
# · Remove if: canary-формат меняется
def test_read_tor_chain_down_states(caplog, tmp_path: Path) -> None:
    """_read_tor_chain_down: red/green/missing/invalid → True/False/None/None."""
    caplog.set_level(logging.INFO)
    red = tmp_path / "red.json"
    red.write_text('{"status": "red", "ts": "2026-08-16T00:00:00Z"}')
    green = tmp_path / "green.json"
    green.write_text('{"status": "green"}')
    missing = tmp_path / "nope.json"
    broken = tmp_path / "broken.json"
    broken.write_text("not json")

    assert heartbeat._read_tor_chain_down(str(red)) is True
    assert heartbeat._read_tor_chain_down(str(green)) is False
    assert heartbeat._read_tor_chain_down(str(missing)) is None
    assert heartbeat._read_tor_chain_down(str(broken)) is None
    logger.info("[IMP:9][test_heartbeat] tor-canary state parsing PASS")


# endregion TOR_CANARY_003_A3
