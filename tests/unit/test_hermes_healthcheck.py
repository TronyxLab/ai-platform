# GREP_SUMMARY: test-hermes-healthcheck deps aggregation required optional pg redis litellm healthy unhealthy negative
# STRUCTURE: ┌injectable checkers┐ → ◇ check_deps (required PG+LiteLLM / optional Redis) → ◇ check_pg/check_redis/check_litellm primitives (patch) → ◇ CLI exit → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/modules/hermes-agent/healthcheck_deps.py (DevPlan 119 D6 —
##           TEST-FIRST: deps-ветка healthcheck.sh 48-112 → Python). R5 negative: required missing
##           → unhealthy, optional missing → healthy.
## @scope    Tests: check_deps агрегация (injectable checkers — DI, никаких сетей), check_pg/
##           check_redis/check_litellm примитивы (patch subprocess/socket/urllib), CLI exit parity.
## @invariants
##   - check_deps — DI checkers (никаких реальных сетей/subprocess)
##   - check_* примитивы — patch модульных зависимостей (subprocess.run, socket, urllib)
##   - R5 anti-survivorship: test_hc_deps_aggregation_negative
##   - LDD: IMP:9 в успешных сценариях
## @rationale D6 (DevPlan 119, AUDIT-1 F8): healthcheck.sh deps-режим (48-112) — required/optional
##   агрегация без unit-тестов. Условие DevPlan D6 step 3-4: unit-тесты (test-first).
## @changes  2026-08-02 | DevPlan 119 D6 — Created (test-first)
# endregion MODULE_CONTRACT

import logging
import sys
from pathlib import Path
from unittest import mock

import pytest

logger = logging.getLogger(__name__)

# module-specific path (tests/AGENTS.md §sys.path policy)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "hermes-agent"))

from healthcheck_deps import (
    check_deps,
    check_litellm,
    check_pg,
    check_redis,
)


def _assert_imp9(caplog: pytest.LogCaptureFixture, needle: str | None = None) -> None:
    """Assert at least one IMP:9 log (LDD telemetry standard)."""
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in list(caplog.records):
        if "[IMP:" in record.message:
            logger.info("%s", record.message)
            if needle and needle in record.message:
                found = True
    logger.info("--- END LDD TRAJECTORY ---")
    if needle:
        assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}'"
    else:
        assert any("[IMP:9]" in r.message for r in caplog.records), "Critical LDD Error: No IMP:9 log found"


# region TEST_check_deps (агрегация, DI checkers)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_deps: все required ok → healthy (D6)
# · Scenario: PG + LiteLLM ok, Redis ok → healthy() True
# · Last fail: N/A (new — D6 test-first)
# · Remove if: агрегация меняется
def test_check_deps_all_ok(caplog: pytest.LogCaptureFixture) -> None:
    """Все зависимости ok → healthy() True."""
    caplog.set_level(logging.INFO)
    result = check_deps(
        "pg",
        6432,
        "redis",
        6379,
        "http://litellm:4000/health/liveliness",
        check_pg_fn=lambda _, __: True,
        check_redis_fn=lambda _, __: True,
        check_litellm_fn=lambda _: True,
    )
    assert result.healthy() is True
    assert result.pg_ok and result.litellm_ok and result.redis_ok
    _assert_imp9(caplog, "All required dependencies ok")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_hc_deps_aggregation — required missing → unhealthy (D6, TEST_SPEC)
# · Scenario: PG недоступен (required) → healthy() False (exit 1), даже если LiteLLM ok
# · Last fail: N/A (new — D6; R5: required-семантика shell deps-ветки: PG && LiteLLM → exit 0)
# · Remove if: required-семантика меняется
def test_hc_deps_aggregation_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: required (PG) недоступен → unhealthy, даже при ok LiteLLM."""
    caplog.set_level(logging.INFO)
    result = check_deps(
        "pg",
        6432,
        "redis",
        6379,
        "http://litellm:4000/health/liveliness",
        check_pg_fn=lambda _, __: False,  # required MISSING
        check_redis_fn=lambda _, __: True,
        check_litellm_fn=lambda _: True,
    )
    assert result.healthy() is False, "PG (required) missing → unhealthy"
    assert any("[IMP:9]" in r.message and "FAIL: required" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · required LiteLLM missing → unhealthy (D6)
# · Scenario: LiteLLM недоступен (required) → healthy() False
# · Last fail: N/A (new — D6)
# · Remove if: required-семантика меняется
# GUARD-PRESERVE (168): R5 anti-survivorship — *_negative пара required-семантики LiteLLM (D6 TEST_SPEC)
def test_hc_deps_aggregation_litellm_required_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: required (LiteLLM) недоступен → unhealthy."""
    caplog.set_level(logging.INFO)
    result = check_deps(
        "pg",
        6432,
        "redis",
        6379,
        "http://litellm:4000/health/liveliness",
        check_pg_fn=lambda _, __: True,
        check_redis_fn=lambda _, __: True,
        check_litellm_fn=lambda _: False,  # required MISSING
    )
    assert result.healthy() is False


# 🧪 TRAP[TEST] · NEGATIVE (R5) · optional Redis missing → healthy (D6, TEST_SPEC)
# · Scenario: Redis недоступен (optional) → warn, НО healthy (не блокирует)
# · Last fail: N/A (new — D6; shell: Redis warn-only, PG && LiteLLM решают)
# · Remove if: optional-семантика меняется
# GUARD-PRESERVE (168): R5 anti-survivorship — *_negative пара optional-семантики Redis (D6 TEST_SPEC)
def test_hc_deps_aggregation_optional_redis_missing_negative(caplog: pytest.LogCaptureFixture) -> None:
    """R5 negative: optional (Redis) недоступен → ВСЁ РАВНО healthy (required ok)."""
    caplog.set_level(logging.INFO)
    result = check_deps(
        "pg",
        6432,
        "redis",
        6379,
        "http://litellm:4000/health/liveliness",
        check_pg_fn=lambda _, __: True,
        check_redis_fn=lambda _, __: False,  # optional MISSING → warn only
        check_litellm_fn=lambda _: True,
    )
    assert result.healthy() is True, "Redis (optional) не должен блокировать healthy"


# endregion TEST_check_deps


# region TEST_check_pg / check_redis / check_litellm (примитивы, patch)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_pg: pg_isready rc0 → True (D6)
# · Scenario: pg_isready в PATH, rc 0 → True
# · Last fail: N/A (new — D6 test-first)
# · Remove if: check_pg примитив меняется
def test_check_pg_isready_ok(caplog: pytest.LogCaptureFixture) -> None:
    """check_pg: pg_isready rc=0 → True."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=0, stdout="", stderr="")
    with (
        mock.patch("healthcheck_deps.shutil.which", return_value="/usr/bin/pg_isready"),
        mock.patch("healthcheck_deps.subprocess.run", return_value=fake),
    ):
        assert check_pg("pgbouncer", 6432) is True
    assert any("[IMP:8]" in r.message and "pg_isready" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_pg: pg_isready отсутствует → TCP fallback (D6)
# · Scenario: pg_isready НЕ в PATH → TCP socket probe (эквивалент shell elif-ветки /dev/tcp)
# · Last fail: N/A (new — D6; shell: `elif timeout 3 bash -c "echo > /dev/tcp/..."`)
# · Remove if: TCP fallback удалён
def test_check_pg_fallback_tcp(caplog: pytest.LogCaptureFixture) -> None:
    """check_pg: pg_isready отсутствует → TCP socket probe ok → True."""
    caplog.set_level(logging.INFO)
    with (
        mock.patch("healthcheck_deps.shutil.which", return_value=None),
        mock.patch("healthcheck_deps.socket.create_connection") as mock_sock,
    ):
        assert check_pg("pgbouncer", 6432) is True
    mock_sock.assert_called_once()
    assert any("[IMP:8]" in r.message and "TCP socket" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_redis: PONG → True (D6)
# · Scenario: redis-cli PING → "PONG" в stdout → True
# · Last fail: N/A (new — D6 test-first)
# · Remove if: check_redis примитив меняется
def test_check_redis_pong(caplog: pytest.LogCaptureFixture) -> None:
    """check_redis: redis-cli PING → PONG → True."""
    caplog.set_level(logging.INFO)
    fake = mock.MagicMock(returncode=0, stdout="PONG\n", stderr="")
    with (
        mock.patch("healthcheck_deps.shutil.which", return_value="/usr/bin/redis-cli"),
        mock.patch("healthcheck_deps.subprocess.run", return_value=fake),
    ):
        assert check_redis("redis", 6379) is True


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_litellm: HTTP 200 → True (D6)
# · Scenario: urlopen → status 200 → True
# · Last fail: N/A (new — D6; shell curl -sf → HTTP 200)
# · Remove if: check_litellm примитив меняется
def test_check_litellm_ok(caplog: pytest.LogCaptureFixture) -> None:
    """check_litellm: HTTP 200 → True."""
    caplog.set_level(logging.INFO)
    resp = mock.MagicMock()
    resp.status = 200
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    with mock.patch("healthcheck_deps.urllib.request.urlopen", return_value=resp):
        assert check_litellm("http://litellm:4000/health/liveliness") is True
    # Примитив логирует ok на IMP:8 (как shell log_imp 8); IMP:9 — в check_deps агрегации
    assert any("[IMP:8]" in r.message and "LiteLLM: ok" in r.message for r in caplog.records)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_litellm: HTTPError → False (D6)
# · Scenario: urlopen → HTTPError (non-200) → False (никогда не raise)
# · Last fail: N/A (new — D6)
# · Remove if: graceful-degradation меняется
def test_check_litellm_error_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    """check_litellm: HTTPError → False (graceful degradation, как shell curl -sf)."""
    caplog.set_level(logging.INFO)
    import urllib.error

    with mock.patch(
        "healthcheck_deps.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError("url", 503, "Service Unavailable", None, None),
    ):
        assert check_litellm("http://litellm:4000/health/liveliness") is False


# endregion TEST_check_pg / check_redis / check_litellm


# region TEST_CLI


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI: healthy → exit 0 (D6, healthcheck.sh exec)
# · Scenario: checkers ok → main() == 0
# · Last fail: N/A (new — D6)
# · Remove if: CLI удалён
def test_cli_healthy_exit0(caplog: pytest.LogCaptureFixture) -> None:
    """CLI: все required ok → exit 0 (healthcheck.sh deps exit passthrough)."""
    caplog.set_level(logging.INFO)
    from healthcheck_deps import main as hc_main

    with mock.patch(
        "healthcheck_deps.check_deps",
        return_value=mock.MagicMock(healthy=mock.MagicMock(return_value=True)),
    ):
        assert hc_main(["--pg-host", "pg", "--litellm-url", "http://litellm:4000/health/liveliness"]) == 0


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI: unhealthy → exit 1 (D6)
# · Scenario: required missing → main() == 1
# · Last fail: N/A (new — D6)
# · Remove if: CLI удалён
def test_cli_unhealthy_exit1(caplog: pytest.LogCaptureFixture) -> None:
    """CLI: required недоступен → exit 1."""
    caplog.set_level(logging.INFO)
    from healthcheck_deps import main as hc_main

    with mock.patch(
        "healthcheck_deps.check_deps",
        return_value=mock.MagicMock(healthy=mock.MagicMock(return_value=False)),
    ):
        assert hc_main(["--pg-host", "pg"]) == 1


# endregion TEST_CLI
