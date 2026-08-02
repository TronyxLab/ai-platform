#!/usr/bin/env python3
# GREP_SUMMARY: test-firewall ufw validate-ports baseline 5432-deny forbidden 2375 declarative rules status-verify
# STRUCTURE: ┌pure functions┐ → ◇ validate_ports (valid/invalid/forbidden) → ◇ build_rules (baseline+extra+deny) → ◇ parse_ufw_status (active/actions) → ◇ verify_firewall (compliant/violations) → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/firewall.py (DevPlan 118 E3 — Python-порт firewall.sh).
##           Native imports; pure functions (validate_ports, build_rules, parse_ufw_status, verify_firewall)
##           — no real ufw subprocess.
## @scope    Tests: port validation (valid 1-65535, non-numeric, out-of-range, forbidden 2375/2376),
##           rule construction (baseline 22/80/443, extra ports, deny 5432, enable),
##           ufw status parsing, verify (active + baseline ALLOW + 5432 DENY + no forbidden ALLOW).
## @invariants
##   - Pure function tests — no subprocess, no Docker
##   - R5 anti-survivorship: negative-тесты на validate/verify (invalid port, forbidden, missing 5432 DENY)
##   - LDD: IMP:9 on verify pass, IMP:10 on violations
## @rationale E3 Strangler: ufw-оркестрация → Python. Валидация и verify — тестируемые pure functions.
## @changes  2026-08-02 | DevPlan 118 E3 — Created
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.bootstrap import firewall


# region TEST_validate_ports
def test_validate_ports_ok() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_ports_ok — DevPlan 118 E migration unit test
    """validate_ports: valid integers 1-65535 pass through."""
    assert firewall.validate_ports(["8080", "9090", "3000"]) == [8080, 9090, 3000]
    assert firewall.validate_ports([]) == []
    assert firewall.validate_ports(["65535", "1"]) == [65535, 1]


def test_validate_ports_invalid_non_numeric() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_ports_invalid_non_numeric — DevPlan 118 E migration unit test
    """validate_ports: non-numeric port → ValueError."""
    with pytest.raises(Exception, match="Invalid port 'abc'"):
        firewall.validate_ports(["abc"])


def test_validate_ports_invalid_out_of_range() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_ports_invalid_out_of_range — DevPlan 118 E migration unit test
    """validate_ports: out-of-range port → ValueError."""
    with pytest.raises(Exception, match="Invalid port '0'"):
        firewall.validate_ports(["0"])
    with pytest.raises(Exception, match="Invalid port '65536'"):
        firewall.validate_ports(["65536"])


@pytest.mark.parametrize("bad_port", ["2375", "2376"])
def test_validate_ports_forbidden_docker_api(bad_port: str) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_ports_forbidden_docker_api — DevPlan 118 E migration unit test
    """validate_ports: Docker API ports 2375/2376 → ValueError (SECURITY)."""
    with pytest.raises(Exception, match="Docker API port"):
        firewall.validate_ports([bad_port])


# endregion


# region TEST_build_rules
def test_build_rules_baseline_and_deny() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_rules_baseline_and_deny — DevPlan 118 E migration unit test
    """build_rules: reset→defaults→baseline 22/80/443→deny 5432→enable (declarative full-set)."""
    rules = firewall.build_rules([])
    cmds = [" ".join(r) for r in rules]
    assert "ufw --force reset" in cmds
    assert "ufw default deny incoming" in cmds
    assert "ufw default allow outgoing" in cmds
    for port in (22, 80, 443):
        assert f"ufw allow {port}/tcp comment platform-baseline" in cmds
    assert "ufw deny 5432/tcp comment explicit-deny-postgresql" in cmds
    assert "ufw --force enable" in cmds


def test_build_rules_includes_extra_ports() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_rules_includes_extra_ports — DevPlan 118 E migration unit test
    """build_rules: extra ports appended with platform-extra comment."""
    rules = firewall.build_rules([8080, 9090])
    cmds = [" ".join(r) for r in rules]
    assert "ufw allow 8080/tcp comment platform-extra" in cmds
    assert "ufw allow 9090/tcp comment platform-extra" in cmds
    assert cmds.index("ufw allow 8080/tcp comment platform-extra") > cmds.index("ufw default allow outgoing"), (
        "extra ports must come after defaults"
    )


# endregion


# region TEST_parse_ufw_status
def test_parse_ufw_status_active_with_actions() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_ufw_status_active_with_actions — DevPlan 118 E migration unit test
    """parse_ufw_status: active status + ALLOW/DENY port actions."""
    status = """Status: active

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
80/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
5432/tcp                   DENY        Anywhere
"""
    active, actions = firewall.parse_ufw_status(status)
    assert active is True
    assert actions[22] == "ALLOW"
    assert actions[80] == "ALLOW"
    assert actions[443] == "ALLOW"
    assert actions[5432] == "DENY"


def test_parse_ufw_status_inactive() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_ufw_status_inactive — DevPlan 118 E migration unit test
    """parse_ufw_status: Status inactive → active False."""
    active, actions = firewall.parse_ufw_status("Status: inactive")
    assert active is False
    assert actions == {}


# endregion


# region TEST_verify_firewall
def test_verify_firewall_compliant(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_firewall_compliant — DevPlan 118 E migration unit test
    """verify_firewall: active + baseline ALLOW + 5432 DENY + no forbidden → True (IMP:9)."""
    caplog.set_level(logging.INFO)
    status = """Status: active
22/tcp  ALLOW
80/tcp  ALLOW
443/tcp ALLOW
5432/tcp DENY
"""
    assert firewall.verify_firewall(status) is True
    assert any("[IMP:9]" in r.message for r in caplog.records), "IMP:9 verify-pass log expected"


def test_verify_firewall_inactive_fails(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_firewall_inactive_fails — DevPlan 118 E migration unit test
    """verify_firewall: inactive → False + IMP:10."""
    caplog.set_level(logging.INFO)
    assert firewall.verify_firewall("Status: inactive") is False
    assert any("[IMP:10]" in r.message and "NOT active" in r.message for r in caplog.records)


def test_verify_firewall_missing_5432_deny(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_firewall_missing_5432_deny — DevPlan 118 E migration unit test
    """verify_firewall: 5432 NOT denied → False (SECURITY, managed PostgreSQL guard)."""
    caplog.set_level(logging.INFO)
    status = """Status: active
22/tcp ALLOW
80/tcp ALLOW
443/tcp ALLOW
5432/tcp ALLOW
"""
    assert firewall.verify_firewall(status) is False
    assert any("[IMP:10]" in r.message and "5432" in r.message for r in caplog.records), (
        "5432-not-denied must be the violation reported"
    )


def test_verify_firewall_forbidden_open(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_firewall_forbidden_open — DevPlan 118 E migration unit test
    """verify_firewall: forbidden Docker port 2375 ALLOW → False (SECURITY)."""
    caplog.set_level(logging.INFO)
    status = """Status: active
22/tcp ALLOW
80/tcp ALLOW
443/tcp ALLOW
5432/tcp DENY
2375/tcp ALLOW
"""
    assert firewall.verify_firewall(status) is False
    assert any("[IMP:10]" in r.message and "2375" in r.message for r in caplog.records), (
        "forbidden-port-open must be the violation reported"
    )


def test_verify_firewall_missing_baseline(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_verify_firewall_missing_baseline — DevPlan 118 E migration unit test
    """verify_firewall: baseline port 443 missing → False."""
    caplog.set_level(logging.INFO)
    status = """Status: active
22/tcp ALLOW
80/tcp ALLOW
5432/tcp DENY
"""
    assert firewall.verify_firewall(status) is False


# endregion


# region TEST_run_integration
def test_run_validation_error_returns_false(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_run_validation_error_returns_false — DevPlan 118 E migration unit test
    """run: invalid extra_ports → False (validation fail-fast, no ufw subprocess)."""
    caplog.set_level(logging.INFO)
    called = []
    monkeypatch.setattr("core.internal.bootstrap.firewall.subprocess.run", lambda *a, **k: called.append(a))

    assert firewall.run(["2375"]) is False  # forbidden port
    assert called == [], "ufw must not be invoked when validation fails"
    assert any("[IMP:10]" in r.message and "Docker API port" in r.message for r in caplog.records)


# endregion
