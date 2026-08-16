"""
# GREP_SUMMARY: test_issue_cert, acme.sh, dns-01, http-01, webnames, shred, inject, retry, wildcard, project-certs, DI, runner, R5
# STRUCTURE: ▶ tmp_path + AcmeFakeRunner (DI) → ◇ DNS-01 wildcard → ◇ HTTP-01 standalone → ◇ webnames shred/inject
#           → ◇ retry (fail→retry→success) → ◇ R5 negatives (missing key/plugin, port80 busy) → ◇ run() executor → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Unit-тесты core/internal/bootstrap/issue_cert.py (DevPlan 164 W3.5-1) — критические
##           ветви acme.sh выпуска: DNS-01 vs HTTP-01 выбор, API-key shred-протокол (ключ не утекает
##           в лог/вывод и не остаётся на диске), retry на acme.sh failure, R5-негативы, run() executor.
## @scope    DI: AcmeFakeRunner (запись вызовов + симуляция acme.sh --issue/--install-cert rc);
##           cert_is_le_issuer monkeypatch'ится (реальный openssl не требуется). tmp_path — Zero Hardcode.
## @invariants
##   - Все subprocess через runner DI (0 monkeypatch subprocess)
##   - Каждый тест валидирует IMP:9 (ldd_trajectory)
##   - Секрет (WEBNAMES_API_KEY) НИКОГДА не проверяется в логах/выводе (shred-протокол инвариант)
## @rationale W3.5-1: issue-cert.sh 708 LOC → issue_cert.py; критические ветви (шred, ветвление,
##            retry) требуют юнит-покрытия (тест-спека W3.5-1).
## @changes  2026-08-14 | DevPlan 164 W3.5-1 — создан
# endregion MODULE_CONTRACT
"""

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap import issue_cert
from tests._conftest.ldd import ldd_trajectory

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.static_audit

_MOCK_KEY = "*test-key-123"


class AcmeFakeRunner:
    """DI-раннер acme.sh: запись вызовов + симуляция --issue (dns/standalone) rc.

    ## @purpose — Замена реального acme.sh: по содержимому команды определяется ветка
    ##            (--standalone = HTTP-01, --dns = DNS-01) и возвращается scripted rc.
    ##            dns01_fail_times=N — провал первых N DNS-01 --issue вызовов (retry-сценарии).
    ## @complexity — O(1) — подстрока-матчинг
    """

    def __init__(
        self,
        *,
        dns01_fail: bool = False,
        http01_fail: bool = False,
        dns01_fail_times: int = 0,
        port80_in_use: bool = False,
    ) -> None:
        self.dns01_fail = dns01_fail
        self.http01_fail = http01_fail
        self.dns01_fail_times = dns01_fail_times
        self.port80_in_use = port80_in_use
        self.calls: list[list[str]] = []
        self._issue_count = 0

    def run(self, cmd, *, timeout=30, check=False, non_fatal=False, fatal_rc=()):  # ruff: ignore[ARG002]
        self.calls.append(list(cmd))
        joined = " ".join(cmd)
        if cmd and cmd[0] == "ss":
            return subprocess.CompletedProcess(cmd, 0, ":80 0.0.0.0:80\n" if self.port80_in_use else "", "")
        if cmd and cmd[0] == "netstat":
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if cmd and cmd[0] == "shred":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd and "acme.sh" in cmd[0]:  # acme.sh binary path
            if "--issue" in joined:
                self._issue_count += 1
                rc = 0
                if "--standalone" in joined:
                    if self.http01_fail:
                        rc = 1
                elif self.dns01_fail or self._issue_count <= self.dns01_fail_times:
                    rc = 1
                return subprocess.CompletedProcess(cmd, rc, "", "")
            if "--install-cert" in joined:
                return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _make_ctx(tmp_path: Path, runner: AcmeFakeRunner, env: dict | None = None, **kw) -> issue_cert.IssueContext:
    """Собрать IssueContext с tmp-путями (acme home, letsencrypt dir, tmp_dir) + mock acme.sh."""
    acme_home = tmp_path / "acme"
    acme_home.mkdir(parents=True, exist_ok=True)
    acme_sh = acme_home / "acme.sh"
    acme_sh.write_text("#!/bin/sh\necho mock\n", encoding="utf-8")
    acme_sh.chmod(0o755)
    return issue_cert.IssueContext(
        runner=runner,
        facts=issue_cert.default_env_facts(),
        environ=env or {},
        acme_home=str(acme_home),
        letsencrypt_dir=str(tmp_path / "le"),
        tmp_dir=str(tmp_path),
        **kw,
    )


def _write_webnames_plugin(acme_home: Path) -> None:
    """dnsapi_ext/dns_webnames.sh с API_KEY= строкой (мишень инъекции)."""
    ext = acme_home / "dnsapi_ext"
    ext.mkdir(parents=True, exist_ok=True)
    (ext / "dns_webnames.sh").write_text('#!/bin/bash\nAPI_KEY=""\necho mock\n', encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# region DNS-01 vs HTTP-01 selection
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · DNS-01 mode issues wildcard cert
# · Scenario: challenge_mode=dns, webnames → acme.sh --dns dns_webnames + -d *.domain, нет --standalone
# · Last fail: N/A (W3.5-1) · Remove if: DNS-01 ветка меняется
@ldd_trajectory
def test_dns01_issues_wildcard(caplog, tmp_path) -> None:
    """DNS-01: --dns dns_webnames + -d *.domain (wildcard), без --standalone."""
    caplog.set_level(logging.INFO)
    _write_webnames_plugin(tmp_path / "acme")
    runner = AcmeFakeRunner()

    ok = issue_cert.issue_tls_cert(
        "test.example.com",
        "admin@test.com",
        "webnames",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, {"WEBNAMES_API_KEY": _MOCK_KEY}),
    )

    assert ok is True
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert issue_calls, "acme.sh --issue не вызван"
    assert "--dns" in issue_calls[0] and "dns_webnames" in issue_calls[0], (
        f"ожидался DNS-01 --dns dns_webnames: {issue_calls[0]}"
    )
    assert "*.test.example.com" in issue_calls[0], f"wildcard -d *.domain отсутствует: {issue_calls[0]}"
    assert "--standalone" not in issue_calls[0], "DNS-01 не должен использовать --standalone"
    logger.critical("[IMP:9][test] DNS-01 wildcard: --dns dns_webnames + -d *.domain")


# 🧪 TRAP[TEST] · Regression · HTTP-01 mode (ACME_CHALLENGE_MODE=http) bypasses DNS-01
# · Scenario: challenge_mode=http → --standalone, -d domain (individual), без -d *.domain
# · Last fail: N/A (W3.5-1) · Remove if: HTTP-01 ветка меняется
@ldd_trajectory
def test_http01_mode_uses_standalone_individual(caplog, tmp_path) -> None:
    """HTTP-01: --standalone + -d domain (индивидуальный), без --dns и без *.domain."""
    caplog.set_level(logging.INFO)
    runner = AcmeFakeRunner()

    ok = issue_cert.issue_tls_cert(
        "test.example.com", "admin@test.com", "", wildcard=True, ctx=_make_ctx(tmp_path, runner), challenge_mode="http"
    )

    assert ok is True
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert issue_calls, "acme.sh --issue не вызван"
    assert "--standalone" in issue_calls[0], f"HTTP-01 должен использовать --standalone: {issue_calls[0]}"
    assert "--dns" not in issue_calls[0], "HTTP-01 не должен вызывать DNS-01"
    assert "*.test.example.com" not in issue_calls[0], "HTTP-01 не должен выпускать wildcard"
    logger.critical("[IMP:9][test] HTTP-01 standalone: -d domain individual, без DNS-01")


# 🧪 TRAP[TEST] · Regression · AUTO mode: DNS-01 fail → HTTP-01 fallback
# · Scenario: challenge_mode=auto, DNS-01 fail → --standalone fallback; IMP:9 fallback warning
# · Last fail: N/A (W3.5-1) · Remove if: auto-fallback логика меняется
@ldd_trajectory
def test_auto_mode_falls_back_to_http01(caplog, tmp_path) -> None:
    """AUTO: DNS-01 провал → HTTP-01 fallback (--standalone) + IMP:9 warning."""
    caplog.set_level(logging.INFO)
    _write_webnames_plugin(tmp_path / "acme")
    runner = AcmeFakeRunner(dns01_fail=True)

    ok = issue_cert.issue_tls_cert(
        "test.example.com",
        "admin@test.com",
        "webnames",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, {"WEBNAMES_API_KEY": _MOCK_KEY}),
        challenge_mode="auto",
    )

    assert ok is True
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert any("--standalone" in c for c in issue_calls), f"HTTP-01 fallback не вызван: {issue_calls}"
    assert any("falling back to HTTP-01" in r.message for r in caplog.records), "IMP:9 fallback warning отсутствует"
    logger.critical("[IMP:9][test] AUTO fallback: DNS-01 fail → HTTP-01 standalone")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region webnames shred protocol + inject
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · inject_webnames_key (чистая функция) — API_KEY замена
# · Scenario: content с API_KEY="" → API_KEY="*secret" (sed-канон, только первая строка)
# · Last fail: N/A (W3.5-1) · Remove if: инъекция меняется
@ldd_trajectory
def test_inject_webnames_key_replaces_api_key(caplog) -> None:
    """inject_webnames_key: API_KEY= строка заменяется на API_KEY="<key>" (первое вхождение)."""
    caplog.set_level(logging.INFO)
    content = '#!/bin/bash\nAPI_KEY=""\necho "$API_KEY"\n'
    injected = issue_cert.inject_webnames_key(content, _MOCK_KEY)
    assert f'API_KEY="{_MOCK_KEY}"' in injected
    assert 'API_KEY=""' not in injected
    logger.critical("[IMP:9][test] inject_webnames_key — API_KEY заменён (sed-канон)")


# 🧪 TRAP[TEST] · Regression · webnames shred protocol — ключ не остаётся на диске и не в логах
# · Scenario: issue через webnames → после acme.sh tmp-файл + dnsapi/dns_webnames.sh уничтожены;
# ·   ключ отсутствует в caplog и в runner-вызовах (НЕ утекает)
# · Last fail: N/A (W3.5-1; TRAP[BUSINESS] 2026-06-11 — ключ на диске = уязвимость)
# · Remove if: shred-протокол удаляется/меняется
@ldd_trajectory
def test_webnames_key_shredded_and_not_leaked(caplog, tmp_path) -> None:
    """Shred-протокол: файлы с ключом уничтожены, ключ не в логах и не в командах."""
    caplog.set_level(logging.INFO)
    _write_webnames_plugin(tmp_path / "acme")
    runner = AcmeFakeRunner()

    ok = issue_cert._issue_acme_webnames(
        "test.example.com",
        "admin@test.com",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, {"WEBNAMES_API_KEY": _MOCK_KEY}),
    )

    assert ok is True
    # 1. Ни один файл под tmp_path не содержит ключ (все инъекции shred'нуты)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert _MOCK_KEY not in path.read_text(encoding="utf-8", errors="replace"), f"ключ остался на диске: {path}"
    # 2. Ключ не утекает в логи
    assert _MOCK_KEY not in caplog.text, "WEBNAMES_API_KEY не должен попадать в логи"
    # 3. Ключ не утекает в аргументы subprocess
    for call in runner.calls:
        assert _MOCK_KEY not in " ".join(call), f"ключ попал в subprocess args: {call}"
    # 4. shred вызван (shred-протокол выполнен)
    assert any(c and c[0] == "shred" for c in runner.calls), "shred -u не вызван"
    logger.critical("[IMP:9][test] webnames shred — ключ уничтожен, 0 утечек (лог/вывод/диск)")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region Retry on acme.sh failure
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · retry: DNS-01 fail → повторная попытка (2 --issue вызова) → False
# · Scenario: оба attempt fail → 2 --issue вызова, issue_tls_cert False
# · Last fail: N/A (W3.5-1 — retry новая возможность) · Remove if: retry убирается
@ldd_trajectory
def test_dns01_retries_then_fails(caplog, tmp_path) -> None:
    """Retry: оба attempt провалились → 2 --issue вызова, итог False."""
    caplog.set_level(logging.INFO)
    _write_webnames_plugin(tmp_path / "acme")
    runner = AcmeFakeRunner(dns01_fail=True)

    ok = issue_cert._issue_acme_webnames(
        "test.example.com",
        "admin@test.com",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, {"WEBNAMES_API_KEY": _MOCK_KEY}),
    )

    assert ok is False
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert len(issue_calls) == 2, f"ожидалось 2 retry-попытки, got {len(issue_calls)}: {issue_calls}"
    logger.critical("[IMP:9][test] retry: DNS-01 2 попытки → False (fail-fast после retry)")


# 🧪 TRAP[TEST] · Regression · retry: первый fail, второй success → True (2 --issue)
# · Scenario: dns01_fail_times=1 → первая попытка fail, retry success → issue_tls_cert True
# · Last fail: N/A (W3.5-1) · Remove if: retry убирается
@ldd_trajectory
def test_dns01_succeeds_after_retry(caplog, tmp_path) -> None:
    """Retry recovery: 1-я попытка fail, 2-я success → True (транзиентный сбой пережит)."""
    caplog.set_level(logging.INFO)
    _write_webnames_plugin(tmp_path / "acme")
    runner = AcmeFakeRunner(dns01_fail_times=1)

    ok = issue_cert._issue_acme_webnames(
        "test.example.com",
        "admin@test.com",
        wildcard=True,
        ctx=_make_ctx(tmp_path, runner, {"WEBNAMES_API_KEY": _MOCK_KEY}),
    )

    assert ok is True
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert len(issue_calls) == 2, f"ожидалось 2 попытки (fail + retry), got {len(issue_calls)}"
    logger.critical("[IMP:9][test] retry recovery: 1-я fail, 2-я success → True")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region R5 negatives
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · NEGATIVE (R5) · WEBNAMES_API_KEY отсутствует → FAIL (исходный вход: webnames guard)
# · Scenario: webnames без ключа → issue_tls_cert False, 0 acme.sh вызовов
# · Last fail: N/A (W3.5-1) · Remove if: webnames key guard меняется
@ldd_trajectory
def test_missing_webnames_key_fails(caplog, tmp_path) -> None:
    """R5: WEBNAMES_API_KEY не задан → FAIL (guard), без вызова acme.sh."""
    caplog.set_level(logging.INFO)
    _write_webnames_plugin(tmp_path / "acme")
    runner = AcmeFakeRunner()

    ok = issue_cert.issue_tls_cert(
        "test.example.com", "admin@test.com", "webnames", wildcard=True, ctx=_make_ctx(tmp_path, runner, {})
    )

    assert ok is False
    issue_calls = [c for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert not issue_calls, "без ключа acme.sh вызываться не должен"
    logger.critical("[IMP:9][test] R5: missing WEBNAMES_API_KEY → FAIL, 0 acme.sh calls")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · пустой dns_plugin (dns-режим) → FAIL
# · Scenario: challenge_mode=dns, пустой плагин → issue_tls_cert False (BUSINESS INVARIANT)
# · Last fail: N/A (W3.5-1) · Remove if: dns-plugin guard меняется
@ldd_trajectory
def test_missing_dns_plugin_fails(caplog, tmp_path) -> None:
    """R5: пустой dns_plugin в dns-режиме → FAIL (DNS plugin required для wildcard)."""
    caplog.set_level(logging.INFO)
    runner = AcmeFakeRunner()

    ok = issue_cert.issue_tls_cert(
        "test.example.com", "admin@test.com", "", wildcard=True, ctx=_make_ctx(tmp_path, runner)
    )

    assert ok is False
    logger.critical("[IMP:9][test] R5: missing DNS plugin → FAIL")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · порт 80 занят → HTTP-01 FAIL (без --issue)
# · Scenario: ss показывает :80 → _issue_http01_cert False (standalone невозможен)
# · Last fail: N/A (W3.5-1) · Remove if: port80 guard меняется
@ldd_trajectory
def test_http01_port80_in_use_fails(caplog, tmp_path) -> None:
    """R5: порт 80 занят → HTTP-01 FAIL (stop nginx first), без --issue."""
    caplog.set_level(logging.INFO)
    runner = AcmeFakeRunner(port80_in_use=True)

    ok = issue_cert._issue_http01_cert("test.example.com", "admin@test.com", _make_ctx(tmp_path, runner))

    assert ok is False
    issue_calls = [c for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert not issue_calls, "при занятом порте 80 acme.sh вызываться не должен"
    logger.critical("[IMP:9][test] R5: port 80 busy → HTTP-01 FAIL, 0 --issue calls")


# 🧪 TRAP[TEST] · NEGATIVE (R5) · non-LE cert на диске → re-issue (не SKIP)
# · Scenario: fullchain.pem существует, issuer НЕ LE → WARN re-issue + acme.sh вызван
# · Last fail: 2026-07-22 P0 — mkcert certs survived bootstrap (issuer check отсутствовал)
# · Remove if: idempotency через issuer-проверку отменяется
@ldd_trajectory
def test_non_le_cert_triggers_reissue(caplog, tmp_path) -> None:
    """R5: существующий не-LE сертификат → re-issue (WARN), НЕ idempotent skip."""
    caplog.set_level(logging.INFO)
    _write_webnames_plugin(tmp_path / "acme")
    runner = AcmeFakeRunner()
    ctx = _make_ctx(tmp_path, runner, {"WEBNAMES_API_KEY": _MOCK_KEY})
    cert_path = Path(ctx.letsencrypt_dir) / "live" / "test.example.com" / "fullchain.pem"
    cert_path.parent.mkdir(parents=True)
    cert_path.write_text("mkcert cert\n", encoding="utf-8")

    ok = issue_cert.issue_tls_cert(
        "test.example.com",
        "admin@test.com",
        "webnames",
        wildcard=True,
        ctx=ctx,
        cert_is_le_issuer_fn=lambda _p: False,  # DI (167 D3) — issuer-стаб вместо monkeypatch
    )

    assert ok is True
    issue_calls = [c for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    assert issue_calls, "не-LE сертификат должен перевыпускаться"
    assert any("re-issuing" in r.message.lower() for r in caplog.records), "WARN re-issuing отсутствует"
    logger.critical("[IMP:9][test] R5: non-LE cert → re-issue (mkcert P0 guard)")


# endregion


# ═════════════════════════════════════════════════════════════════════════════
# region run() executor (env-контракт, project certs)
# ═════════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · Regression · run(): idempotent main cert → SKIP main, project certs обработаны
# · Scenario: PLATFORM_DOMAIN LE-валиден (mock) + project domains → run 0; botanika (subdomain)
# ·   пропущен, other.com выпущен (-d *.other.com)
# · Last fail: 2026-07-17 P1 — early exit блокировал project domains
# · Remove if: run() оркестрация меняется
@ldd_trajectory
def test_run_idempotent_skips_main_but_processes_projects(caplog, tmp_path) -> None:
    """run(): main LE-valid → SKIP, project certs обработаны (subdomain-skip + issue)."""
    caplog.set_level(logging.INFO)
    runner = AcmeFakeRunner()
    # Mock acme.sh binary (run() читает ACME_HOME из env)
    acme_home = tmp_path / "acme"
    acme_home.mkdir(parents=True, exist_ok=True)
    acme_sh = acme_home / "acme.sh"
    acme_sh.write_text("#!/bin/sh\necho mock\n", encoding="utf-8")
    acme_sh.chmod(0o755)
    env = {
        "PLATFORM_DOMAIN": "tronyx.ru",
        "PLATFORM_EMAIL": "admin@tronyx.ru",
        "PLATFORM_ACME_DNS_PLUGIN": "regru",
        "PLATFORM_PROJECT_DOMAINS": "botanika.tronyx.ru other.com",
        "ACME_HOME": str(tmp_path / "acme"),
        "LETSENCRYPT_DIR": str(tmp_path / "le"),
    }

    # DI (167 D3): cert_is_le_issuer_fn — main LE-валиден (SKIP), project certs — нет
    rc = issue_cert.run(
        env,
        runner=runner,
        cert_is_le_issuer_fn=lambda path: "tronyx.ru/fullchain" in str(path),
    )

    assert rc == 0
    issue_calls = [" ".join(c) for c in runner.calls if "acme.sh" in c[0] and "--issue" in c]
    # main пропущен (idempotent) — wildcard tronyx.ru НЕ выпускается
    assert not any("*.tronyx.ru" in c for c in issue_calls), "main cert должен быть SKIP (idempotent)"
    # botanika — поддомен tronyx.ru → пропущен
    assert not any("botanika" in c for c in issue_calls), "subdomain botanika должен быть пропущен"
    # other.com — независимый домен → wildcard выпущен
    assert any("*.other.com" in c for c in issue_calls), f"other.com wildcard должен выпускаться: {issue_calls}"
    logger.critical("[IMP:9][test] run(): main SKIP + project certs (subdomain-skip) — P1 guard")


# 🧪 TRAP[TEST] · Regression · run(): пустой PLATFORM_DOMAIN → exit 1 (validation guard)
# · Scenario: domain пуст → FAIL, return EXIT_GENERIC (1)
# · Last fail: N/A (W3.5-1) · Remove if: env validation меняется
@ldd_trajectory
def test_run_missing_domain_fails(caplog, tmp_path) -> None:
    """run(): PLATFORM_DOMAIN пуст → exit 1 (fail-fast validation)."""
    caplog.set_level(logging.INFO)
    runner = AcmeFakeRunner()

    rc = issue_cert.run({"PLATFORM_EMAIL": "admin@test.com"}, runner=runner)

    assert rc == 1
    logger.critical("[IMP:9][test] run(): missing PLATFORM_DOMAIN → exit 1")


# endregion
