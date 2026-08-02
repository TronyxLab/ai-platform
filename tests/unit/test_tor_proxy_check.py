#!/usr/bin/env python3
# GREP_SUMMARY: test-tor-proxy-healthcheck 3-stage socks5 privoxy getMe mock-curl telegram_notifier timeout
# STRUCTURE: ┌mock curl subprocess + telegram get_me┐ → ◇ test check_tor_socks (200/fail) → ◇ test check_privoxy → ◇ test check_telegram_api (secrets read, skip, getMe fail) → ◇ test run_all (first-failure exit) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/healthcheck/tor_proxy_check.py (DevPlan 118 E5 — Python-порт
##           tor-proxy-healthcheck.sh). Native imports; mock curl subprocess + telegram_notifier.get_me.
## @scope    Tests: check_tor_socks (HTTP 200 / failure), check_privoxy, check_telegram_api
##           (secrets file parsing, SKIP semantics, getMe failure), run_all first-failure exit.
## @invariants
##   - All tests mock subprocess.run (curl) and telegram_notifier.get_me — no network
##   - LDD: IMP:9 log on success, IMP:9 FAIL log on failure
## @rationale E5 Strangler: 3-stage проверка → Python. Stage-логика тестируема с mock curl.
## @changes  2026-08-02 | DevPlan 118 E5 — Created
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from core.internal.healthcheck import tor_proxy_check as tpc


# region TEST_check_tor_socks
def test_check_tor_socks_http200(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_tor_socks_http200 — DevPlan 118 E migration unit test
    """check_tor_socks: curl SOCKS5 → HTTP 200 → True (IMP:9)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(tpc, "curl_http_code", lambda args: "200")
    assert tpc.check_tor_socks() is True
    assert any("[IMP:9]" in r.message and "connected" in r.message for r in caplog.records)


def test_check_tor_socks_failure(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_tor_socks_failure — DevPlan 118 E migration unit test
    """check_tor_socks: curl failure → False (IMP:9 FAIL)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(tpc, "curl_http_code", lambda args: None)
    assert tpc.check_tor_socks() is False
    assert any("[IMP:9]" in r.message and "FAIL" in r.message for r in caplog.records)


# endregion


# region TEST_check_privoxy
def test_check_privoxy_http200(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_privoxy_http200 — DevPlan 118 E migration unit test
    """check_privoxy: curl --proxy → HTTP 200 → True."""
    caplog.set_level(logging.INFO)
    captured: list[list[str]] = []
    monkeypatch.setattr(tpc, "curl_http_code", lambda args: captured.append(args) or "200")
    assert tpc.check_privoxy("http://127.0.0.1:8118") is True
    assert captured and "--proxy" in captured[0] and "http://127.0.0.1:8118" in captured[0]


def test_check_privoxy_failure(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_privoxy_failure — DevPlan 118 E migration unit test
    """check_privoxy: curl failure → False."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(tpc, "curl_http_code", lambda args: "000")
    assert tpc.check_privoxy("http://127.0.0.1:8118") is False


# endregion


# region TEST_check_telegram_api
def test_check_telegram_api_missing_secrets_file(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_telegram_api_missing_secrets_file — DevPlan 118 E migration unit test
    """check_telegram_api: secrets file absent → SKIP (return True, не false-FAIL)."""
    caplog.set_level(logging.INFO)
    assert tpc.check_telegram_api(str(tmp_path / "no-secrets.env"), "http://127.0.0.1:8118") is True
    assert any("SKIP" in r.message for r in caplog.records)


def test_check_telegram_api_token_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_telegram_api_token_missing — DevPlan 118 E migration unit test
    """check_telegram_api: secrets file present but no TELEGRAM_BOT_TOKEN → SKIP."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("OTHER_KEY=value\n")
    assert tpc.check_telegram_api(str(secrets), "http://127.0.0.1:8118") is True
    assert any("TELEGRAM_BOT_TOKEN not set" in r.message for r in caplog.records)


def test_check_telegram_api_getme_ok(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_telegram_api_getme_ok — DevPlan 118 E migration unit test
    """check_telegram_api: getMe success → True (IMP:9), token passed from secrets."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:token\n")
    captured: dict = {}

    def fake_get_me(bot_token=None, proxy_url=None):
        captured["token"] = bot_token
        captured["proxy"] = proxy_url
        return True

    monkeypatch.setattr(tpc.telegram_notifier, "get_me", fake_get_me)
    assert tpc.check_telegram_api(str(secrets), "http://127.0.0.1:8118") is True
    assert captured["token"] == "123:token"
    assert captured["proxy"] == "http://127.0.0.1:8118"
    assert any("[IMP:9]" in r.message and "reachable" in r.message for r in caplog.records)


def test_check_telegram_api_getme_fail(tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_telegram_api_getme_fail — DevPlan 118 E migration unit test
    """check_telegram_api: getMe failure → False (IMP:9 FAIL)."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:token\n")
    monkeypatch.setattr(tpc.telegram_notifier, "get_me", lambda bot_token=None, proxy_url=None: False)
    assert tpc.check_telegram_api(str(secrets), "http://127.0.0.1:8118") is False
    assert any("[IMP:9]" in r.message and "FAIL" in r.message for r in caplog.records)


# endregion


# region TEST_run_all
def test_run_all_first_failure_stops(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_all_first_failure_stops — DevPlan 118 E migration unit test
    """run_all: stage-1 failure → False, stages 2/3 not executed (immediate exit semantics)."""
    caplog.set_level(logging.INFO)
    calls: list[str] = []
    monkeypatch.setattr(tpc, "check_tor_socks", lambda: calls.append("tor") or False)
    monkeypatch.setattr(tpc, "check_privoxy", lambda proxy: calls.append("privoxy") or True)
    monkeypatch.setattr(tpc, "check_telegram_api", lambda f, p: calls.append("tg") or True)

    assert tpc.run_all("http://127.0.0.1:8118", "/tmp/none") is False
    assert calls == ["tor"], f"Stages 2/3 must not run after stage-1 failure, got {calls}"


def test_run_all_all_pass(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_all_all_pass — DevPlan 118 E migration unit test
    """run_all: all 3 stages pass → True + IMP:9 ALL PASSED."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(tpc, "check_tor_socks", lambda: True)
    monkeypatch.setattr(tpc, "check_privoxy", lambda proxy: True)
    monkeypatch.setattr(tpc, "check_telegram_api", lambda f, p: True)

    assert tpc.run_all("http://127.0.0.1:8118", "/tmp/none") is True
    assert any("[IMP:9]" in r.message and "All healthchecks PASSED" in r.message for r in caplog.records)


# endregion


# region TEST_curl_http_code
def test_curl_http_code_subprocess(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_curl_http_code_subprocess — DevPlan 118 E migration unit test
    """curl_http_code: subprocess exit 0 → HTTP code; exit != 0 → None."""
    caplog.set_level(logging.INFO)
    result = mock.MagicMock(returncode=0, stdout="200\n", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: result)
    assert tpc.curl_http_code(["--socks5-hostname", "127.0.0.1:9050"]) == "200"

    result.returncode = 7
    assert tpc.curl_http_code(["--proxy", "x"]) is None


def test_curl_http_code_timeout(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_curl_http_code_timeout — DevPlan 118 E migration unit test
    """curl_http_code: TimeoutExpired → None (no hang)."""
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        subprocess,
        "run",
        mock.MagicMock(side_effect=subprocess.TimeoutExpired(cmd="curl", timeout=30)),
    )
    assert tpc.curl_http_code([]) is None


# endregion
