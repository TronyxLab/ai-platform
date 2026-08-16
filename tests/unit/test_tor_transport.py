# GREP_SUMMARY: test-tor-transport bridge-parsing webtunnel-degradation transport-dedup unknown-transport fail-fast write_torrc ClientTransportPlugin
# STRUCTURE: ┌bridge content fixtures┐ → ◇ parse_bridges (obfs4/webtunnel/mixed) → ◇ degradation (webtunnel absent → drop) → ◇ dedup transports → ◇ unknown transport fail-fast → ◇ render section → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/tor_transport.py (DevPlan 118 E1, D19 — TEST-FIRST:
##           тесты написаны ПЕРЕД миграцией write_torrc бизнес-логики из install-tor-proxy.sh в Python).
##           Native imports; pure parsing/degradation/dedup functions.
## @scope    Tests: Bridge-line parsing (obfs4/webtunnel), webtunnel degradation (binary absent → drop line),
##           transport dedup (уникальные ClientTransportPlugin), unknown transport fail-fast (exit 1 канон),
##           non-Bridge passthrough (комментарии/пустые), render_torrc_section (UseBridges 1 + CTP lines).
## @invariants
##   - Чистые функции — no subprocess, no filesystem
##   - R5 anti-survivorship: negative-тесты (unknown transport, webtunnel degradation)
##   - LDD: IMP:9 on success, IMP:10 on unknown-transport error
## @rationale E1 (D19): transport-парсинг и деградация — бизнес-логика write_torrc (install-tor-proxy.sh:147-196).
##   Условие мега-DevPlan D19: unit-тесты ПЕРЕД миграцией — выполнено (test-first).
## @changes  2026-08-02 | DevPlan 118 E1 — Created (test-first)
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.bootstrap import tor_transport

pytestmark = pytest.mark.static_audit


# region TEST_parse_bridges
def test_parse_bridges_obfs4_only() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_bridges_obfs4_only — DevPlan 118 E migration unit test
    """parse_bridges: obfs4 Bridge lines → filtered + transports=[obfs4]."""
    content = "Bridge obfs4 1.2.3.4:443\n# comment\nBridge obfs4 5.6.7.8:443\n"
    filtered, transports = tor_transport.parse_bridges(content, available_binaries={"obfs4proxy", "webtunnel"})
    assert transports == ["obfs4"], f"transports={transports}"
    assert "Bridge obfs4 1.2.3.4:443" in filtered
    assert "Bridge obfs4 5.6.7.8:443" in filtered
    assert "# comment" in filtered, "non-Bridge lines must pass through"


# GUARD-PRESERVE (168): единственное покрытие dedup-ветки parse_bridges (повтор транспортов → один CTP)
def test_parse_bridges_transport_dedup() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_bridges_transport_dedup — DevPlan 118 E migration unit test
    """parse_bridges: duplicate transports → emitted once (ClientTransportPlugin per unique transport)."""
    content = "Bridge obfs4 1.1.1.1:443\nBridge webtunnel 2.2.2.2:443\nBridge obfs4 3.3.3.3:443\n"
    _filtered, transports = tor_transport.parse_bridges(content, available_binaries={"obfs4proxy", "webtunnel"})
    assert transports == ["obfs4", "webtunnel"], f"dedup failed: {transports}"


def test_parse_bridges_webtunnel_absent_drops_lines() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_bridges_webtunnel_absent_drops_lines — DevPlan 118 E migration unit test
    """parse_bridges: webtunnel binary absent → webtunnel Bridge lines DROPPED, obfs4 kept (degradation)."""
    content = "Bridge obfs4 1.1.1.1:443\nBridge webtunnel 2.2.2.2:443\n"
    filtered, transports = tor_transport.parse_bridges(content, available_binaries={"obfs4proxy"})
    assert transports == ["obfs4"], f"webtunnel must degrade away, got {transports}"
    assert "Bridge obfs4 1.1.1.1:443" in filtered
    assert "webtunnel" not in filtered, "webtunnel Bridge line must be dropped when binary absent"


def test_parse_bridges_unknown_transport_fail_fast() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_bridges_unknown_transport_fail_fast — DevPlan 118 E migration unit test
    """parse_bridges: unknown transport (no registered binary) → TorTransportError (fail-fast канон)."""
    content = "Bridge sometransport 1.1.1.1:443\n"
    with pytest.raises(tor_transport.TorTransportError, match="Unknown transport 'sometransport'"):
        tor_transport.parse_bridges(content, available_binaries={"obfs4proxy"})


def test_parse_bridges_all_dropped_empty() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_bridges_all_dropped_empty — DevPlan 118 E migration unit test
    """parse_bridges: all bridges dropped (webtunnel absent) → empty filtered, empty transports."""
    content = "Bridge webtunnel 2.2.2.2:443\n"
    filtered, transports = tor_transport.parse_bridges(content, available_binaries={"obfs4proxy"})
    assert transports == []
    assert not filtered.strip()


def test_parse_bridges_empty_content() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_parse_bridges_empty_content — DevPlan 118 E migration unit test
    """parse_bridges: empty content → no transports, empty filtered."""
    filtered, transports = tor_transport.parse_bridges("", available_binaries={"obfs4proxy"})
    assert transports == []
    assert not filtered


# endregion TEST_parse_bridges


# region TEST_render_torrc_section
def test_render_section_uses_bridges_and_ctp() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_render_section_uses_bridges_and_ctp — DevPlan 118 E migration unit test
    """render_torrc_section: UseBridges 1 + ClientTransportPlugin per transport + filtered bridges."""
    filtered = "Bridge obfs4 1.1.1.1:443\n"
    section = tor_transport.render_torrc_section(filtered, ["obfs4"])
    assert "UseBridges 1" in section
    assert "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" in section
    assert "Bridge obfs4 1.1.1.1:443" in section


def test_render_section_multi_transport() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_render_section_multi_transport — DevPlan 118 E migration unit test
    """render_torrc_section: multiple transports → multiple ClientTransportPlugin lines."""
    filtered = "Bridge obfs4 1.1.1.1:443\nBridge webtunnel 2.2.2.2:443\n"
    section = tor_transport.render_torrc_section(filtered, ["obfs4", "webtunnel"])
    assert "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" in section
    assert "ClientTransportPlugin webtunnel exec /usr/bin/webtunnel" in section


def test_render_section_empty_transports() -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_render_section_empty_transports — DevPlan 118 E migration unit test
    """render_torrc_section: no usable transports → empty section (shell WARN branch)."""
    assert not tor_transport.render_torrc_section("", [])


# endregion TEST_render_torrc_section


# region TEST_resolve_available_binaries
class _FakeFacts:
    """EnvironmentFacts-fake (DevPlan 160 W4b): which/path_isfile через параметр."""

    def __init__(self, which_map: dict[str, str | None], isfile_map: dict[str, bool] | None = None) -> None:
        self._which = which_map
        self._isfile = isfile_map or {}

    def is_root(self) -> bool:  # pragma: no cover
        return True

    def which(self, binary: str) -> str | None:
        return self._which.get(binary)

    def path_isfile(self, path) -> bool:
        return self._isfile.get(str(path), False)


# 🧪 TRAP[TEST] · 2026-08-13 · Regression · W4b — resolve_available_binaries через facts (DI)
# · Scenario: (which_map, isfile_map) → {транспорты}: which-PATH / path_isfile-fallback для бинарников
# · Last fail: N/A (new — DevPlan 160 W4b T4.2 EnvironmentFacts)
# · Remove if: resolve_available_binaries сигнатура меняется
@pytest.mark.parametrize(
    ("which_map", "isfile_map", "expected"),
    [
        # which(/usr/bin/obfs4proxy) → путь → {obfs4}; webtunnel absent → drop
        (
            {"/usr/bin/obfs4proxy": "/usr/bin/obfs4proxy", "/usr/bin/webtunnel": None},
            {"/usr/bin/webtunnel": False},
            {"obfs4"},
        ),
        # which→None, но файл существует (isfile) → транспорт доступен (бинарник вне PATH)
        (
            {"/usr/bin/obfs4proxy": None, "/usr/bin/webtunnel": None},
            {"/usr/bin/obfs4proxy": True, "/usr/bin/webtunnel": False},
            {"obfs4"},
        ),
    ],
)
def test_resolve_available_binaries(which_map, isfile_map, expected) -> None:
    """resolve_available_binaries(facts): which-путь или path_isfile-fallback → {доступные транспорты}."""
    facts = _FakeFacts(which_map=which_map, isfile_map=isfile_map)
    assert tor_transport.resolve_available_binaries(facts) == expected


# endregion TEST_resolve_available_binaries


# region TEST_cli_emit_unknown_transport
def test_cli_emit_unknown_transport_exit1(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_cli_emit_unknown_transport_exit1 — DevPlan 118 E migration unit test
    """CLI emit: unknown transport → exit 1 (fail-fast канон, shell exit 1 parity)."""
    caplog.set_level(logging.INFO)
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge badtransport 1.1.1.1:443\n")
    rc = tor_transport.main(["emit", "--bridges-file", str(bridges)])
    assert rc == 1
    assert any("[IMP:10]" in r.message and "Unknown transport" in r.message for r in caplog.records)


def test_cli_emit_ok_section(caplog: pytest.LogCaptureFixture, tmp_path, capsys) -> None:
    # 🧪 TRAP[TEST] · 2026-08-02 · test_cli_emit_ok_section — DevPlan 118 E migration unit test
    """CLI emit: valid bridges → exit 0 + section on stdout."""
    caplog.set_level(logging.INFO)
    bridges = tmp_path / "bridges.txt"
    bridges.write_text("Bridge obfs4 1.1.1.1:443\n")
    rc = tor_transport.main(["emit", "--bridges-file", str(bridges)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "UseBridges 1" in out
    assert "ClientTransportPlugin obfs4 exec /usr/bin/obfs4proxy" in out
    assert any("[IMP:9]" in r.message for r in caplog.records)


# endregion TEST_cli_emit_unknown_transport
