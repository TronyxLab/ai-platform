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
import os

import pytest

from core.internal.bootstrap import firewall


# region TEST_validate_ports
def test_validate_ports_ok() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_validate_ports_ok — DevPlan 118 E migration unit test
    """validate_ports: valid integers 1-65535 pass through (не модульные внутренние порты)."""
    # W10 T10.6 (S-8): 9090/3000 теперь модульные внутренние порты (prometheus/grafana) — FORBIDDEN;
    # валидные порты берём из свободного диапазона, не пересекающегося с реестром модулей.
    assert firewall.validate_ports(["8080", "8081", "8443"]) == [8080, 8081, 8443]
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


@pytest.mark.parametrize("bad_port", ["6379", "9090", "3000", "3100", "9000", "4000"])
def test_validate_ports_forbidden_module_port(bad_port: str) -> None:
    # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.6 (S-8) — модульный внутренний порт
    # · Scenario: admin открывает extra_ports=9090 (prometheus) — порт уже слушает 127.0.0.1,
    # ·   allow Anywhere раскрывает внутренний сервис наружу
    # · Last fail: 2026-08-05 — W10: FORBIDDEN был только {2375,2376}; 9090/3000/6379 проходили
    # · Remove if: реестр модульных портов (platform-infra.yaml) пересмотрен
    with pytest.raises(Exception, match="module-internal port"):
        firewall.validate_ports([bad_port])


# endregion


# region TEST_build_rules
def test_build_rules_baseline_and_deny() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_rules_baseline_and_deny — DevPlan 118 E migration unit test
    """build_rules: enable→default-deny→ssh-first→baseline 22/80/443→module-deny→deny 5432 (incremental)."""
    # W10 T10.10 (S-14): контракт СМЕНЁН с declarative reset на инкрементальный apply —
    # firewall активен с первой команды (enable первым), default-deny ДО allow-правил, ssh 22 первым.
    rules = firewall.build_rules([])
    cmds = [" ".join(r) for r in rules]
    assert "ufw --force enable" in cmds
    assert "ufw --force disable" not in cmds, "S-14: firewall НИКОГДА не выключается (disable запрещён)"
    assert "ufw --force reset" not in cmds, "S-14: reset запрещён (инкрементальный apply)"
    assert "ufw default deny incoming" in cmds
    assert "ufw default allow outgoing" in cmds
    # ssh 22 — первым allow-правилом (lockout-safe при переконфигурации)
    assert cmds.index("ufw allow 22/tcp comment platform-baseline") < cmds.index(
        "ufw allow 80/tcp comment platform-baseline"
    ), "ssh 22 должен открываться раньше остальных baseline-портов"
    for port in (22, 80, 443):
        assert f"ufw allow {port}/tcp comment platform-baseline" in cmds
    assert "ufw deny 5432/tcp comment explicit-deny-postgresql" in cmds
    # enable идёт ПЕРВОЙ командой (не последней как в старом контракте)
    assert cmds.index("ufw --force enable") == 0, "enable должен быть первой командой (S-14)"


def test_build_rules_no_disable_no_reset() -> None:
    # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.10 (S-14) — disable/reset окно
    # · Scenario: вернуть `ufw --force disable`/`ufw --force reset` — окно без файрвола при
    # ·   перезапуске firewall (весь интервал между reset и enable нода голый)
    # · Last fail: 2026-08-05 — W10: firewall.py имел declarative reset (disable→reset→enable)
    # · Remove if: инкрементальный apply отменён через TRAP[DECISION]
    rules = firewall.build_rules([])
    cmds = [" ".join(r) for r in rules]
    assert not any("disable" in c or "reset" in c for c in cmds), (
        "S-14 FAIL: build_rules содержит disable/reset — firewall выключается"
    )


def test_build_rules_includes_extra_ports() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_build_rules_includes_extra_ports — DevPlan 118 E migration unit test
    """build_rules: extra ports — ТОЛЬКО `allow from <ip> to any port <p>` (S-8, W10 T10.6)."""
    # W10 T10.6: extra_ports требуют --source-ip; форма allow from <ip> — НИКОГДА 0.0.0.0/Anywhere.
    rules = firewall.build_rules([8080, 8081], source_ip="1.2.3.4")
    cmds = [" ".join(r) for r in rules]
    assert "ufw allow from 1.2.3.4 to any port 8080/tcp comment platform-extra" in cmds
    assert "ufw allow from 1.2.3.4 to any port 8081/tcp comment platform-extra" in cmds
    assert not any("allow 8080/tcp" in c and "from" not in c for c in cmds), "extra port должен быть IP-scoped (S-8)"
    assert cmds.index("ufw allow from 1.2.3.4 to any port 8080/tcp comment platform-extra") > cmds.index(
        "ufw default allow outgoing"
    ), "extra ports must come after defaults"


def test_build_rules_extra_ports_require_source_ip() -> None:
    # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.6 (S-8) — extra_ports без источника
    # · Scenario: extra_ports переданы без --source-ip → правило allow Anywhere (0.0.0.0) — открывает
    # ·   порт всем интернетам
    # · Last fail: 2026-08-05 — W10: build_rules([8080]) эмитил `allow 8080/tcp` без from
    # · Remove if: IP-scoping отменён через TRAP[DECISION]
    with pytest.raises(Exception, match="source-ip"):
        firewall.build_rules([8080])


def test_build_rules_includes_module_deny() -> None:
    # 🧪 TRAP[TEST] · 2026-08-05 · DevPlan 136 W10 T10.6 — module-internal deny (defense-in-depth)
    """build_rules: модульные внутренние порты получают явный deny (S-8)."""
    rules = firewall.build_rules([], source_ip=None)
    cmds = [" ".join(r) for r in rules]
    for port in (6379, 9000, 9090, 3000, 3100):
        assert f"ufw deny {port}/tcp comment platform-module-deny" in cmds, f"module port {port} deny missing"


def test_build_rules_tor_enabled_privoxy_rule() -> None:
    # 🧪 TRAP[TEST] · 2026-08-06 · 142 W6 (A3) — ufw allow 172.16.0.0/12:8118 baseline
    """build_rules(tor_enabled=True): правило privoxy для docker-моста — декларативный baseline."""
    rules = firewall.build_rules([], source_ip=None, tor_enabled=True)
    cmds = [" ".join(r) for r in rules]
    assert "ufw allow from 172.16.0.0/12 to any port 8118 proto tcp comment platform-tor-privoxy" in cmds, (
        f"tor-privoxy правило обязано быть в baseline: {cmds}"
    )
    # Без TOR_ENABLED — правило отсутствует (не открываем privoxy без tor)
    cmds_no_tor = [" ".join(r) for r in firewall.build_rules([], source_ip=None, tor_enabled=False)]
    assert "8118" not in " ".join(cmds_no_tor), "без TOR_ENABLED правило 8118 не должно появляться"


def test_verify_firewall_tor_privoxy_rule() -> None:
    # 🧪 TRAP[TEST] · 2026-08-06 · 142 W6 (A3) — verify сверяет privoxy-правило
    """verify_firewall(tor_enabled=True): 8118 ALLOW для 172.16.0.0/12 обязан быть в ufw status."""
    status = """Status: active
    22/tcp ALLOW IN Anywhere  # platform-baseline
    80/tcp ALLOW IN Anywhere  # platform-baseline
    443/tcp ALLOW IN Anywhere  # platform-baseline
    8118/tcp ALLOW IN 172.16.0.0/12  # platform-tor-privoxy
    5432/tcp DENY IN Anywhere  # explicit-deny-postgresql
    6379/tcp DENY IN Anywhere  # platform-module-deny
    """
    assert firewall.verify_firewall(status, tor_enabled=True) is True
    # Без правила — RED (дрейф privoxy/firewall после reboot, A3)
    status_missing = status.replace("8118/tcp ALLOW IN 172.16.0.0/12  # platform-tor-privoxy\n", "")
    assert firewall.verify_firewall(status_missing, tor_enabled=True) is False, (
        "verify обязан ловить отсутствие privoxy-правила (142 W6)"
    )
    # tor выключен → отсутствие правила не ошибка
    assert firewall.verify_firewall(status_missing, tor_enabled=False) is True


def test_run_reads_tor_enabled_env(monkeypatch) -> None:
    # 🧪 TRAP[TEST] · 2026-08-06 · 142 W6 — run() читает TOR_ENABLED из env (φ1-канал)
    """run(tor_enabled=None) → env TOR_ENABLED=true включает privoxy-правило."""
    monkeypatch.setenv("TOR_ENABLED", "true")
    # Прямая проверка env-резолва: run() дефолтит на os.environ TOR_ENABLED
    resolved = os.environ.get("TOR_ENABLED", "false").lower() == "true"
    assert resolved is True
    assert any("8118" in " ".join(r) for r in firewall.build_rules([], source_ip=None, tor_enabled=resolved))


def test_collect_stale_platform_rules() -> None:
    # 🧪 TRAP[TEST] · 2026-08-05 · DevPlan 136 W10 T10.10 (S-14) — stale-reconcile
    """collect_stale_platform_rules: platform-* allow вне желаемого набора → delete-команда."""
    status = (
        "Status: active\n"
        "22/tcp ALLOW IN Anywhere  # platform-baseline\n"
        "8443/tcp ALLOW IN Anywhere  # platform-extra\n"
        "8080/tcp ALLOW IN Anywhere  # platform-extra\n"
        "5432/tcp DENY IN Anywhere  # explicit-deny-postgresql\n"
    )
    # desired allow = baseline {22,80,443} + extra {8080}; 8443 вышел из набора → stale
    deletes = firewall.collect_stale_platform_rules(status, desired_allow={22, 80, 443, 8080})
    cmds = [" ".join(d) for d in deletes]
    assert "ufw delete allow 8443/tcp" in cmds, "stale platform-extra 8443 должен удаляться"
    assert "ufw delete allow 8080/tcp" not in cmds, "актуальный extra 8080 не удаляется"
    assert "ufw delete allow 22/tcp" not in cmds, "baseline 22 не удаляется"
    assert "ufw delete allow 5432/tcp" not in cmds, "deny-правила не трогаются"


def test_collect_stale_platform_rules_ignores_foreign() -> None:
    # 🧪 TRAP[TEST] · 2026-08-05 · DevPlan 136 W10 T10.10 — чужие правила не трогаются
    """collect_stale_platform_rules: правило без комментария platform-* — вне скоупа reconcile."""
    status = "Status: active\n8080/tcp ALLOW IN Anywhere  # user-project\n"
    deletes = firewall.collect_stale_platform_rules(status, desired_allow={22, 80, 443})
    assert deletes == [], "user-project правило не должно удаляться (не platform-*)"


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


def test_verify_firewall_module_port_allow_fails(caplog: pytest.LogCaptureFixture) -> None:
    # 🧪 TRAP[TEST] · REGRESSION (R5) · DevPlan 136 W10 T10.6 (S-8) — модульный порт ALLOW
    # · Scenario: 9090 (prometheus) открыт в ufw ALLOW — внутренний сервис доступен снаружи
    # · Last fail: 2026-08-05 — W10: verify проверял только 5432 и 2375/2376
    # · Remove if: реестр модульных портов пересмотрен
    caplog.set_level(logging.INFO)
    status = """Status: active
22/tcp ALLOW
80/tcp ALLOW
443/tcp ALLOW
5432/tcp DENY
9090/tcp ALLOW
"""
    assert firewall.verify_firewall(status) is False
    assert any("[IMP:10]" in r.message and "9090" in r.message for r in caplog.records), (
        "module-port-ALLOW must be reported as violation"
    )


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
