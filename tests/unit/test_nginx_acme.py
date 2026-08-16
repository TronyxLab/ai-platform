# GREP_SUMMARY: test nginx acme acme.sh tls http01-fallback ACME_CHALLENGE_MODE issue-cert issue_cert install-acme install_acme merge-fallback ecc D4 contract LDD IMP
# STRUCTURE: _BOOTSTRAP_DIR → helpers (AcmeFakeRunner, _make_ctx) → HTTP01_FALLBACK_TESTS(5 tests, issue_cert module)
#           → INSTALL_ACME_MERGE_FALLBACK(D4, install-acme.sh facade)
# region MODULE_CONTRACT
## @purpose  Contract tests for acme.sh TLS operations (DevPlan 164 W3.5-1): issue-cert.sh → issue_cert.py
##           (708 LOC shell → Python-модуль), install-acme.sh → тонкий фасад над install_acme.py.
##           HTTP-01 fallback-тесты тестируют Python-модуль (DI AcmeFakeRunner — запись вызовов
##           acme.sh + симуляция rc); D4 merge-fallback-тест гоняет реальный фасад bash install-acme.sh
##           с mock git (замена реального git/сети).
## @scope    subprocess-call to issue_cert module functions (DI runner) + bash install-acme.sh facade (D4).
##           No Docker, no acme.sh, no network access required. Tests guard clauses and error paths.
## @invariants
##   - All test_* functions use @ldd_trajectory decorator
##   - Each test logs IMP:9 business logic assertion
##   - Tests validate HTTP-01 fallback, ACME_CHALLENGE_MODE behavior, DNS-01 wildcard args
##   - WEBNAMES_API_KEY can be empty for HTTP-01 mode (bypasses DNS guard)
##   - D4-тест: mock git в PATH, ACME_HOME=tmp_path — никаких реальных git/сетевых вызовов
## @changes  2026-07-23 | DevPlan 058 — Added HTTP-01 fallback tests (5 tests) + issue-cert.sh helpers
## @changes  2026-07-26 | DevPlan 080 — Removed install.sh-dependent tests (dead code deletion)
## @changes  2026-08-05 | DevPlan 136 W1 T1.1 — Added install-acme.sh merge-fallback D4 test (mock git)
## @changes  2026-08-14 | DevPlan 164 W3.5-1 — issue-cert.sh → issue_cert.py: HTTP-01 тесты переведены
##            на Python-модуль (AcmeFakeRunner); install-acme.sh → фасад (D4-тест сохранён)
## @rationale  Contract tests call REAL business logic, validating issuance behavior without
##   requiring system-level dependencies (acme.sh/LE/setwork).
# endregion MODULE_CONTRACT

import logging
import os
import pathlib
import subprocess

import pytest

from core.internal.bootstrap import issue_cert
from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

_PROJECT_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent.parent
# Bootstrap dir (issue_cert.py, install-acme.sh facade) — issue-cert.sh удалён (W3.5-1)
_BOOTSTRAP_DIR: pathlib.Path = _PROJECT_ROOT / "core" / "internal" / "bootstrap"


# region HELPERS (issue_cert module)


class _AcmeFakeRunner:
    """DI-раннер acme.sh: запись вызовов + симуляция --issue (dns/standalone) rc.

    ## @purpose — Замена реального acme.sh в contract-тестах: по содержимому команды
    ##            определяется ветка (--standalone = HTTP-01, --dns = DNS-01) и возвращается
    ##            scripted rc. dns01_fail/http01_fail — флаги провала ветки.
    ## @complexity — O(1) — подстрока-матчинг
    """

    def __init__(self, *, dns01_fail: bool = False, http01_fail: bool = False) -> None:
        self.dns01_fail = dns01_fail
        self.http01_fail = http01_fail
        self.calls: list[list[str]] = []

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if cmd and cmd[0] == "ss":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd and cmd[0] == "netstat":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if cmd and cmd[0] == "shred":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd and "acme.sh" in cmd[0]:
            if "--issue" in joined:
                rc = 0
                if "--standalone" in joined:
                    rc = 1 if self.http01_fail else 0
                elif self.dns01_fail:
                    rc = 1
                return subprocess.CompletedProcess(cmd, rc, "", "")
            if "--install-cert" in joined:
                return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _make_ctx(tmp_path: pathlib.Path, runner: _AcmeFakeRunner, env: dict | None = None) -> issue_cert.IssueContext:
    """Собрать IssueContext с mock acme.sh (tmp-пути, Zero Hardcode)."""
    acme_home = tmp_path / "acme-mock"
    acme_home.mkdir(parents=True, exist_ok=True)
    acme_sh = acme_home / "acme.sh"
    acme_sh.write_text("#!/bin/sh\necho mock\n", encoding="utf-8")
    acme_sh.chmod(0o755)

    # dnsapi/ + dnsapi_ext/ с dns_webnames.sh (DNS-01 тесты)
    dnsapi = acme_home / "dnsapi"
    dnsapi.mkdir(exist_ok=True)
    (dnsapi / "dns_webnames.sh").write_text('#!/bin/bash\necho "[MOCK-DNS] $@" >&2\nexit 0\n', encoding="utf-8")
    dnsapi_ext = acme_home / "dnsapi_ext"
    dnsapi_ext.mkdir(exist_ok=True)
    (dnsapi_ext / "dns_webnames.sh").write_text(
        '#!/bin/bash\nAPI_KEY=""\necho "[MOCK-DNS-EXT] $@" >&2\nexit 0\n', encoding="utf-8"
    )

    return issue_cert.IssueContext(
        runner=runner,
        facts=issue_cert.default_env_facts(),
        environ=env or {},
        acme_home=str(acme_home),
        letsencrypt_dir=str(tmp_path / "letsencrypt"),
        tmp_dir=str(tmp_path),
    )


# endregion HELPERS (issue_cert module)


# region HTTP01_FALLBACK_TESTS
## @purpose  Contract tests for HTTP-01 fallback in issue_cert.py (было issue-cert.sh).
##            Tests ACME_CHALLENGE_MODE env var behavior, _issue_http01_cert() function,
##            and DNS-01 → HTTP-01 graceful degradation.
## @scope    Calls issue_cert module functions with DI AcmeFakeRunner (no real acme.sh/network).
## @invariants
##   - Each test uses @ldd_trajectory decorator
##   - No root, no Docker, no network access required
##   - Mock acme.sh simulates DNS-01 (~dns) and HTTP-01 (--standalone) modes
##   - WEBNAMES_API_KEY can be empty for HTTP-01 mode (bypasses DNS guard)


# 🧪 TRAP[TEST] · Regression · DNS-01 success with ACME_CHALLENGE_MODE=auto issues wildcard
# · Scenario: DNS-01 succeeds (mock returns 0) → wildcard cert issued
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: DNS-01 + wildcard logic changes
@pytest.mark.contract
@ldd_trajectory
def test_dns01_success_wildcard(caplog, tmp_path) -> None:
    """Verify DNS-01 success with ACME_CHALLENGE_MODE=auto issues wildcard cert.

    ## @purpose  Baseline: when DNS-01 works, wildcard cert is issued.
    ## @scenario  ACME_CHALLENGE_MODE=auto, DNS-01 mock returns 0
    ## → acme.sh called with --dns and -d *.domain, no HTTP-01 fallback
    ## @regression  ACME_CHALLENGE_MODE breaks DNS-01 path
    """
    runner = _AcmeFakeRunner()
    env = {
        "WEBNAMES_API_KEY": "*test-key-123",
        "ACME_CHALLENGE_MODE": "auto",
    }
    ok = issue_cert.issue_tls_cert(
        "test.example.com",
        "admin@test.com",
        "webnames",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, env),
        challenge_mode="auto",
    )

    logger.critical("[IMP:9][test_dns01_success_wildcard] ASSERT: DNS-01 success with wildcard")
    assert ok is True, "DNS-01 success должен вернуть True"
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert issue_calls, "Mock acme.sh was not called"
    assert "--dns" in issue_calls[0] and "dns_webnames" in issue_calls[0], f"Expected DNS-01 mode: {issue_calls[0]}"
    assert "*.test.example.com" in issue_calls[0], f"Expected wildcard -d *.domain in DNS-01 mode: {issue_calls[0]}"

    logger.critical("[IMP:9][test_dns01_success_wildcard] PASS: DNS-01 success issues wildcard cert")


# 🧪 TRAP[TEST] · Regression · DNS-01 failure triggers HTTP-01 fallback
# · Scenario: ACME_CHALLENGE_MODE=auto, DNS-01 mock fails → HTTP-01 fallback called
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: fallback logic changes
@pytest.mark.contract
@ldd_trajectory
def test_dns01_fail_http01_fallback(caplog, tmp_path) -> None:
    """Verify DNS-01 failure triggers HTTP-01 fallback with ACME_CHALLENGE_MODE=auto.

    ## @purpose  Core fallback behavior: DNS-01 fails → HTTP-01 is called.
    ## @scenario  ACME_CHALLENGE_MODE=auto, DNS-01 mock fails (dns01_fail=True)
    ## → issue_tls_cert tries DNS-01 first → fails → fallback to _issue_http01_cert
    ## → acme.sh called with --standalone (HTTP-01)
    ## @regression  Fallback not triggered — cert issuance fails completely
    """
    runner = _AcmeFakeRunner(dns01_fail=True)
    env = {
        "WEBNAMES_API_KEY": "*test-key-123",
        "ACME_CHALLENGE_MODE": "auto",
    }
    ok = issue_cert.issue_tls_cert(
        "test.example.com",
        "admin@test.com",
        "webnames",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, env),
        challenge_mode="auto",
    )

    logger.critical("[IMP:9][test_dns01_fail_http01_fallback] ASSERT: HTTP-01 fallback on DNS-01 failure")
    # Should still succeed (HTTP-01 fallback works)
    assert ok is True, "HTTP-01 fallback should succeed"
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert any("--standalone" in c for c in issue_calls), f"Expected HTTP-01 fallback (--standalone): {issue_calls}"
    assert any("--dns" in c for c in issue_calls), f"Expected DNS-01 to be attempted first: {issue_calls}"

    logger.critical("[IMP:9][test_dns01_fail_http01_fallback] PASS: HTTP-01 fallback on DNS-01 failure")


# 🧪 TRAP[TEST] · Regression · ACME_CHALLENGE_MODE=http bypasses DNS-01
# · Scenario: ACME_CHALLENGE_MODE=http → DNS-01 skipped, HTTP-01 used directly
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: http mode logic changes
@pytest.mark.contract
@ldd_trajectory
def test_challenge_mode_http_bypasses_dns(caplog, tmp_path) -> None:
    """Verify ACME_CHALLENGE_MODE=http bypasses DNS-01 entirely.

    ## @purpose  HTTP-only mode: no DNS plugin required, no WEBNAMES_API_KEY needed.
    ## @scenario  ACME_CHALLENGE_MODE=http, no WEBNAMES_API_KEY, empty dns_plugin
    ## → issue_tls_cert skips all DNS-01 guards → calls _issue_http01_cert directly
    ## @regression  HTTP-01 mode still requires DNS plugin — defeats purpose
    """
    runner = _AcmeFakeRunner()
    env = {
        "ACME_CHALLENGE_MODE": "http",
        # WEBNAMES_API_KEY intentionally NOT set — should not matter for HTTP-01
    }
    ok = issue_cert.issue_tls_cert(
        "test.example.com",
        "admin@test.com",
        "",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, env),
        challenge_mode="http",
    )

    logger.critical("[IMP:9][test_challenge_mode_http_bypasses_dns] ASSERT: HTTP-01 bypasses DNS-01 guards")
    assert ok is True, "HTTP-01 mode should succeed without DNS plugin or API key"
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert issue_calls and "--standalone" in issue_calls[0], f"Expected HTTP-01 mode (--standalone): {issue_calls}"
    assert not any("--dns" in c for c in issue_calls), "HTTP-01 mode should NOT call DNS-01"

    logger.critical("[IMP:9][test_challenge_mode_http_bypasses_dns] PASS: HTTP-01 mode bypasses DNS-01")


# 🧪 TRAP[TEST] · Regression · ACME_CHALLENGE_MODE=auto fallback logs IMP:9 warning
# · Scenario: DNS-01 fails → HTTP-01 fallback → IMP:9 warning logged
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: fallback logging logic changes
@pytest.mark.contract
@ldd_trajectory
def test_challenge_mode_auto_fallback_logs_warning(caplog, tmp_path) -> None:
    """Verify ACME_CHALLENGE_MODE=auto logs WARN when DNS-01 fails and falls back to HTTP-01.

    ## @purpose  Traceability: operator must see clear IMP:9 log when fallback occurs.
    ## @scenario  ACME_CHALLENGE_MODE=auto, DNS-01 fails (dns01_fail=True)
    ## → issue_tls_cert falls back to HTTP-01 → log contains "falling back to HTTP-01"
    ## @regression  No warning → operator unaware of degraded mode
    """
    runner = _AcmeFakeRunner(dns01_fail=True)
    env = {
        "WEBNAMES_API_KEY": "*test-key-123",
        "ACME_CHALLENGE_MODE": "auto",
    }
    ok = issue_cert.issue_tls_cert(
        "test.example.com",
        "admin@test.com",
        "webnames",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, env),
        challenge_mode="auto",
    )

    logger.critical("[IMP:9][test_challenge_mode_auto_fallback_logs_warning] ASSERT: fallback warning in logs")
    assert ok is True, "Fallback should succeed"
    # Verify the IMP:9 warning message about fallback
    assert any("falling back to HTTP-01" in r.message for r in caplog.records), "fallback warning отсутствует в логах"
    assert any("does NOT support wildcard" in r.message for r in caplog.records), (
        "wildcard limitation warning отсутствует"
    )

    logger.critical("[IMP:9][test_challenge_mode_auto_fallback_logs_warning] PASS: fallback warning logged")


# 🧪 TRAP[TEST] · Regression · HTTP-01 issues individual cert, not wildcard
# · Scenario: _issue_http01_cert called → single -d domain, no -d *.domain
# · Last fail: N/A (new test for DevPlan 058)
# · Remove if: HTTP-01 issue logic changes
@pytest.mark.contract
@ldd_trajectory
def test_http01_issues_individual_not_wildcard(caplog, tmp_path) -> None:
    """Verify HTTP-01 issues individual domain cert without *.domain wildcard.

    ## @purpose  HTTP-01 via --standalone does NOT support wildcard certs.
    ##            The acme.sh call should have -d "domain" but NOT -d "*.domain".
    ## @scenario  _issue_http01_cert "test.example.com" "admin@test.com"
    ## → acme.sh called with --standalone -d "test.example.com"
    ## → no -d "*.test.example.com" in args
    ## @regression  HTTP-01 accidentally issues wildcard (LE rejects it)
    """
    runner = _AcmeFakeRunner()
    ok = issue_cert._issue_http01_cert("test.example.com", "admin@test.com", _make_ctx(tmp_path, runner))

    logger.critical("[IMP:9][test_http01_issues_individual_not_wildcard] ASSERT: individual cert only")
    assert ok is True, "HTTP-01 should succeed"
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert issue_calls and "--standalone" in issue_calls[0], f"Expected HTTP-01 mode: {issue_calls}"
    # Should NOT have *.domain (wildcard)
    assert "*.test.example.com" not in issue_calls[0], f"HTTP-01 should NOT issue wildcard cert: {issue_calls[0]}"

    logger.critical("[IMP:9][test_http01_issues_individual_not_wildcard] PASS: HTTP-01 issues individual cert only")


# endregion HTTP01_FALLBACK_TESTS


# region INSTALL_ACME_MERGE_FALLBACK (D4)
## @purpose  File-fixture regression test for install-acme.sh idempotent re-run (DevPlan 136 W1 T1.1, D4).
##           install-acme.sh — тонкий фасад (W3.5-1: exec python3 -m core.internal.bootstrap.install_acme);
##           покрытие — интеграционная файловая фикстура: существующий /opt/acme.sh с cert-каталогами
##           *_ecc → повторный запуск install-acme (mock git) → merge-fallback, *_ecc сохранены, exit 0.
## @scope    subprocess bash install-acme.sh с mock git в PATH + ACME_HOME=tmp_path. Без сети/root.
## @invariants
##   - mock git: clone в непустой существующий каталог → FAIL (как реальный git), clone-tmp/dnsapi_ext → OK
##   - *_ecc cert stores (leftover от прошлого install) сохраняются после merge (cp -rn)
##   - WARN-лог «merged with fresh clone» в stderr; exit 0


def _write_mock_git(mock_git_dir: pathlib.Path) -> pathlib.Path:
    """Write a mock `git` binary for install-acme.sh merge-fallback test (D4).

    ## @purpose — Emulates real git clone semantics for the D4 scenario: clone into an
    ##            existing non-empty dir FAILS (git: destination path already exists),
    ##            clone into ${ACME_HOME}.clone-tmp / dnsapi_ext SUCCEEDS. The mock
    ##            inspects the destination (last argv) — no network access.
    ## @io       ⇥ mock_git_dir: pathlib.Path → ⎋ pathlib.Path (executable mock git path)
    ## @complexity — O(1) — single script write + chmod
    """
    mock_git = mock_git_dir / "git"
    mock_git.write_text(
        "#!/bin/bash\n"
        "# Mock git for install-acme.sh merge-fallback test (D4, 017e1c1)\n"
        'dest="${@: -1}"\n'
        'case "$dest" in\n'
        "  *clone-tmp*)\n"
        '    mkdir -p "$dest"\n'
        '    printf \'#!/bin/sh\\necho "mock acme.sh"\\n\' > "$dest/acme.sh"\n'
        '    mkdir -p "$dest/dnsapi"\n'
        "    exit 0\n"
        "    ;;\n"
        "  *dnsapi_ext*)\n"
        '    mkdir -p "$dest"\n'
        "    printf 'mock dnsapi ext\\n' > \"$dest/README.md\"\n"
        "    exit 0\n"
        "    ;;\n"
        "  *)\n"
        "    echo \"fatal: destination path '$dest' already exists and is not an empty directory.\" >&2\n"
        "    exit 128\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    mock_git.chmod(0o755)
    return mock_git


# 🧪 TRAP[TEST] · Regression · D4 — install-acme.sh merge-fallback сохраняет *_ecc (017e1c1)
# · Scenario: /opt/acme.sh существует с cert-каталогами *_ecc (leftover от прошлого install);
# ·   git clone в него FAIL (непустой каталог) → merge-fallback в .clone-tmp → cp -rn
# · Last fail: 2026-08-04 — bare git clone падал на re-run (bootstrap φ7 blocker), cert stores терялись
# · Remove if: install-acme перестаёт использовать merge-fallback (другая идемпотентная стратегия)
@pytest.mark.contract
@ldd_trajectory
def test_install_acme_merge_fallback_preserves_ecc(caplog, tmp_path) -> None:
    """install-acme.sh facade: повторный запуск с существующим /opt/acme.sh (mock git) → *_ecc сохранены, exit 0.

    ## @purpose  R5 negative на точный вход D4: существующий непустой ACME_HOME с *_ecc cert stores.
    ##            Фикс (017e1c1): clone в .clone-tmp + cp -rn merge БЕЗ перезаписи существующего.
    ## @scenario  ACME_HOME=<tmp>/acme.sh (существует, *_ecc) + mock git (clone→fail, clone-tmp→ok)
    ##            → bash install-acme.sh (фасад → python3 -m install_acme) → merge-fallback WARN,
    ##            *_ecc сохранены, acme.sh скопирован, rc 0
    ## @regression  bare clone на re-run падает; *_ecc перезаписываются
    """
    acme_home = tmp_path / "acme.sh"
    (acme_home / "tronyx.ru_ecc").mkdir(parents=True)
    (acme_home / "example.com_ecc").mkdir(parents=True)
    cert_cer = acme_home / "tronyx.ru_ecc" / "tronyx.ru.cer"
    cert_cer.write_text("existing-cert-data\n", encoding="utf-8")

    mock_git_dir = tmp_path / "mock-git"
    mock_git_dir.mkdir()
    _write_mock_git(mock_git_dir)

    env = {
        "ACME_HOME": str(acme_home),
        "PATH": f"{mock_git_dir}:{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        ["bash", str(_BOOTSTRAP_DIR / "install-acme.sh")], capture_output=True, text=True, env=env, check=False
    )

    logger.info("--- INSTALL-ACME STDERR ---")
    logger.info("%s", result.stderr)
    logger.info("--- END STDERR ---")

    assert result.returncode == 0, f"install-acme merge-fallback should exit 0:\n{result.stderr}"
    # *_ecc cert stores сохранены (cp -rn без перезаписи)
    assert (acme_home / "tronyx.ru_ecc").is_dir(), "D4: *_ecc dir must survive merge-fallback"
    assert cert_cer.read_text(encoding="utf-8") == "existing-cert-data\n", "D4: cert data must NOT be overwritten"
    assert (acme_home / "example.com_ecc").is_dir(), "D4: second *_ecc dir must survive"
    # merge-fallback WARN-лог
    assert "merged with fresh clone" in result.stderr, f"D4: expected merge-fallback WARN in stderr:\n{result.stderr}"
    # свежий clone смержен в ACME_HOME (install завершён)
    assert (acme_home / "acme.sh").exists(), "D4: fresh clone must be merged into ACME_HOME"
    assert "[IMP:9]" in result.stderr, "install-acme должен логировать IMP:9 DONE"

    logger.critical("[IMP:9][test_install_acme_merge_fallback] PASS: *_ecc preserved, merge-fallback exit 0")


# endregion INSTALL_ACME_MERGE_FALLBACK (D4)
