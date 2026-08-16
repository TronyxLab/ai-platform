# GREP_SUMMARY: test-tor-proxy-healthcheck 3-stage socks5 privoxy getMe mock-curl telegram_notifier timeout DI FakeCommandRunner
# STRUCTURE: ┌FakeCommandRunner + DI-параметры┐ → ◇ test check_tor_socks (200/fail) → ◇ test check_privoxy → ◇ test check_telegram_api (secrets read, skip, getMe fail) → ◇ test run_all (first-failure exit) → ⎋ LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/healthcheck/tor_proxy_check.py (DevPlan 118 E5 — Python-порт
##           tor-proxy-healthcheck.sh). Native imports; FakeCommandRunner (runner=) + get_me_fn DI.
## @scope    Tests: check_tor_socks (HTTP 200 / failure), check_privoxy, check_telegram_api
##           (secrets file parsing, SKIP semantics, getMe failure), run_all first-failure exit.
## @invariants
##   - Все curl-вызовы через FakeCommandRunner (runner=) — 0 monkeypatch subprocess.run
##   - telegram_notifier.get_me — через get_me_fn параметр (0 monkeypatch)
##   - os.path.isfile (secrets) — реальные tmp_path файлы (факты не патчатся)
##   - LDD: IMP:9 log on success, IMP:9 FAIL log on failure
## @rationale E5 Strangler: 3-stage проверка → Python. Stage-логика тестируема с fake runner.
## @changes  2026-08-02 | DevPlan 118 E5 — Created
##           2026-08-13 | E1 (160) — DI-конвертация (setattr 14 → 0, −100%)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.healthcheck import tor_proxy_check as tpc

pytestmark = pytest.mark.static_audit


class FakeCommandRunner:
    """Scripted CommandRunner (DI-канон W4b): результат из последовательности или дефолт.

    ## @purpose — Замена monkeypatch subprocess.run в тестах tor_proxy_check: каждый вызов
    ##            записывается (calls), возвращается scripted CompletedProcess.
    ## @complexity — O(1) — pop из списка / дефолт
    """

    def __init__(self, results=None, default=None):
        self._results = list(results) if results else []
        self.default = default if default is not None else subprocess.CompletedProcess([], 0, "", "")
        self.calls: list[list[str]] = []

    @property
    def last_cmd(self) -> list[str] | None:
        return self.calls[-1] if self.calls else None

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        if self._results:
            return self._results.pop(0)
        return self.default


def _http_code_runner(code: str | None) -> FakeCommandRunner:
    """Fake-раннер curl: stdout = HTTP-код (или rc=7 → None)."""
    if code is None:
        return FakeCommandRunner(default=subprocess.CompletedProcess([], 7, "", "curl error"))
    return FakeCommandRunner(default=subprocess.CompletedProcess([], 0, f"{code}\n", ""))


# region TEST_check_tor_socks
def test_check_tor_socks_http200(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_tor_socks_http200 — DevPlan 118 E migration unit test
    """check_tor_socks: curl SOCKS5 → HTTP 200 → True (IMP:9)."""
    caplog.set_level(logging.INFO)
    runner = _http_code_runner("200")
    assert tpc.check_tor_socks(runner=runner) is True
    assert "--socks5-hostname" in runner.last_cmd and "127.0.0.1:9050" in runner.last_cmd
    assert any("[IMP:9]" in r.message and "connected" in r.message for r in caplog.records)


def test_check_tor_socks_failure(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_tor_socks_failure — DevPlan 118 E migration unit test
    """check_tor_socks: curl failure → False (IMP:9 FAIL)."""
    caplog.set_level(logging.INFO)
    runner = _http_code_runner(None)
    assert tpc.check_tor_socks(runner=runner) is False
    assert any("[IMP:9]" in r.message and "FAIL" in r.message for r in caplog.records)


# endregion


# region TEST_check_privoxy
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("200", True),  # curl --proxy → HTTP 200 → True
        ("000", False),  # curl failure (bad code) → False
    ],
)
# 🧪 TRAP[TEST] · 2026-08-02 · Regression · check_privoxy: HTTP-код → True/False (DevPlan 118 E migration)
def test_check_privoxy(code, expected, caplog: pytest.LogCaptureFixture) -> None:
    """check_privoxy: curl --proxy через runner → True (200) / False (fail)."""
    caplog.set_level(logging.INFO)
    runner = _http_code_runner(code)
    assert tpc.check_privoxy("http://127.0.0.1:8118", runner=runner) is expected
    assert "--proxy" in runner.last_cmd and "http://127.0.0.1:8118" in runner.last_cmd


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


def test_check_telegram_api_getme_ok(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
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

    assert tpc.check_telegram_api(str(secrets), "http://127.0.0.1:8118", get_me_fn=fake_get_me) is True
    assert captured["token"] == "123:token"
    assert captured["proxy"] == "http://127.0.0.1:8118"
    assert any("[IMP:9]" in r.message and "reachable" in r.message for r in caplog.records)


def test_check_telegram_api_getme_fail(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_check_telegram_api_getme_fail — DevPlan 118 E migration unit test
    """check_telegram_api: getMe failure → False (IMP:9 FAIL)."""
    caplog.set_level(logging.INFO)
    secrets = tmp_path / "secrets.env"
    secrets.write_text("TELEGRAM_BOT_TOKEN=123:token\n")

    def fake_get_me_fail(bot_token=None, proxy_url=None):
        return False

    assert tpc.check_telegram_api(str(secrets), "http://127.0.0.1:8118", get_me_fn=fake_get_me_fail) is False
    assert any("[IMP:9]" in r.message and "FAIL" in r.message for r in caplog.records)


# endregion


# region TEST_run_all
def test_run_all_first_failure_stops(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_all_first_failure_stops — DevPlan 118 E migration unit test
    """run_all: stage-1 failure → False, stages 2/3 not executed (immediate exit semantics)."""
    caplog.set_level(logging.INFO)
    calls: list[str] = []
    runner = FakeCommandRunner(default=subprocess.CompletedProcess([], 7, "", "curl fail"))

    assert (
        tpc.run_all(
            "http://127.0.0.1:8118",
            "/tmp/none",
            runner=runner,
            get_me_fn=lambda **__: calls.append("tg") or True,
        )
        is False
    )
    # Только stage 1 (curl) выполнился — getMe не вызывался, tg не в calls
    assert "tg" not in calls, f"Stages 2/3 must not run after stage-1 failure, got {calls}"
    assert len(runner.calls) == 1, "только один curl-вызов (tor-socks)"


def test_run_all_all_pass(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_all_all_pass — DevPlan 118 E migration unit test
    """run_all: all 3 stages pass → True + IMP:9 ALL PASSED."""
    caplog.set_level(logging.INFO)
    # 2 curl-вызова (tor-socks + privoxy) → 200; secrets отсутствует → telegram SKIP (True)
    runner = FakeCommandRunner(
        results=[
            subprocess.CompletedProcess([], 0, "200\n", ""),
            subprocess.CompletedProcess([], 0, "200\n", ""),
        ]
    )

    assert tpc.run_all("http://127.0.0.1:8118", "/tmp/no-secrets.env", runner=runner) is True
    assert any("[IMP:9]" in r.message and "All healthchecks PASSED" in r.message for r in caplog.records)


# endregion


# region TEST_curl_http_code
class _TimeoutRunner:
    """runner-fake: каждый вызов → TimeoutExpired (curl_http_code не должен висеть)."""

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)


@pytest.mark.parametrize(
    ("runner", "expected"),
    [
        (_http_code_runner("200"), "200"),  # exit 0 → HTTP code
        (_http_code_runner(None), None),  # exit != 0 (rc=7) → None
        (_TimeoutRunner(), None),  # TimeoutExpired → None (no hang)
    ],
)
# 🧪 TRAP[TEST] · 2026-08-02 · Regression · curl_http_code: HTTP-код / fail / timeout (DevPlan 118 E migration)
def test_curl_http_code(runner, expected, caplog: pytest.LogCaptureFixture) -> None:
    """curl_http_code: exit 0 → HTTP code; curl fail / TimeoutExpired → None."""
    caplog.set_level(logging.INFO)
    assert tpc.curl_http_code([], runner=runner) == expected


# endregion
