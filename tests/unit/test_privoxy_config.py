#!/usr/bin/env python3
# GREP_SUMMARY: test-privoxy-config write-config idempotent-mutation no-clobber listen-address permit-access forward-socks5t mutate-config
# STRUCTURE: ┌config content fixtures┐ → ◇ mutate_config (pure: listen/permit-access/forward guards) → ◇ write_privoxy_config (file I/O + идемпотентность) → ◇ CLI --config → ⎋ LDD IMP:9/10
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/bootstrap/privoxy_config.py (DevPlan 119 D3 — TEST-FIRST:
##           тесты задают контракт ПЕРЕД миграцией write_privoxy_config() из install-tor-proxy.sh
##           (172-213) в Python). Идемпотентная мутация (grep-guard + sed → mutate_config).
## @scope    Tests: mutate_config (чистая функция — listen-address/permit-access/forward-socks5t
##           guard-логика), write_privoxy_config (file I/O + идемпотентность: двойной вызов = no-op),
##           test_privoxy_config_no_clobber (существующий корректный конфиг не портится).
## @invariants
##   - mutate_config — чистая функция (no filesystem)
##   - write_privoxy_config тестируется через tmp_path (нет системного /etc/privoxy)
##   - R5 anti-survivorship: test_privoxy_config_idempotent_negative (двойной вызов = no-op)
##   - LDD: IMP:9 в успешных сценариях
## @rationale D3 (DevPlan 119): write_privoxy_config() — идемпотентная мутация (grep-guard + sed).
##   Условие DevPlan D3 step 2: unit-тесты ПЕРЕД миграцией — выполнено (test-first).
## @changes  2026-08-02 | DevPlan 119 D3 — Created (test-first)
# endregion MODULE_CONTRACT

import logging

import pytest

from core.internal.bootstrap import privoxy_config

logger = logging.getLogger(__name__)


def _assert_imp9(caplog: pytest.LogCaptureFixture, needle: str | None = None) -> None:
    """Assert at least one IMP:9 log (LDD telemetry standard)."""
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    found = False
    for record in caplog.records:
        if "[IMP:" in record.message:
            print(record.message)
            if needle and needle in record.message:
                found = True
    print("--- END LDD TRAJECTORY ---")
    if needle:
        assert found, f"Critical LDD Error: No IMP:9 log containing '{needle}'"
    else:
        assert any("[IMP:9]" in r.message for r in caplog.records), "Critical LDD Error: No IMP:9 log found"


# region TEST_mutate_config (чистая функция)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · mutate_config: свежий конфиг → все строки добавлены (D3)
# · Scenario: пустой контент → listen-address + permit-access ×2 + forward-socks5t
# · Last fail: N/A (new — D3 test-first)
# · Remove if: mutate_config semantics change
def test_mutate_config_fresh() -> None:
    """Пустой конфиг → 4 строки добавлены (listen-address, permit-access ×2, forward-socks5t)."""
    new_content, changed = privoxy_config.mutate_config("", "0.0.0.0:8118", "127.0.0.1:9050")
    assert changed is True
    assert "listen-address 0.0.0.0:8118" in new_content
    assert "permit-access 127.0.0.1" in new_content
    assert "permit-access 172.16.0.0/12" in new_content
    assert "forward-socks5t / 127.0.0.1:9050 ." in new_content


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · mutate_config: уже корректный → no-op (D3)
# · Scenario: конфиг содержит все 4 строки → changed=False, содержимое не тронуто
# · Last fail: N/A (new — D3 test-first)
# · Remove if: guard-логика меняется
def test_mutate_config_already_correct_noop() -> None:
    """Конфиг уже корректен → changed=False, содержимое идентично."""
    content = (
        "listen-address 0.0.0.0:8118\n"
        "permit-access 127.0.0.1\n"
        "permit-access 172.16.0.0/12\n"
        "forward-socks5t / 127.0.0.1:9050 .\n"
    )
    new_content, changed = privoxy_config.mutate_config(content, "0.0.0.0:8118", "127.0.0.1:9050")
    assert changed is False
    assert new_content == content


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · mutate_config: 127.0.0.1 listen-address апгрейдится (D3)
# · Scenario: listen-address 127.0.0.1:8118 → апгрейд до 0.0.0.0:8118 (TRAP[BUGFIX] 2026-06-24)
# · Last fail: 2026-06-24 HI — Docker контейнеры не могли достучаться до Privoxy на 127.0.0.1:8118
# · Remove if: listen-address upgrade удалён
def test_mutate_config_upgrade_listen_address() -> None:
    """listen-address 127.0.0.1:8118 → 0.0.0.0:8118 (Docker-доступ, TRAP[BUGFIX] 2026-06-24)."""
    content = "listen-address 127.0.0.1:8118\nsome other line\n"
    new_content, changed = privoxy_config.mutate_config(content, "0.0.0.0:8118", "127.0.0.1:9050")
    assert changed is True
    assert "listen-address 0.0.0.0:8118" in new_content
    assert "listen-address 127.0.0.1:8118" not in new_content
    assert "some other line" in new_content, "не связанные строки не должны теряться"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · mutate_config: permit-access вставляется перед forward (D3)
# · Scenario: есть forward-socks5t → permit-access вставляется ПЕРЕД ним (sed /^forward-socks5t/i эквивалент)
# · Last fail: N/A (new — D3 test-first)
# · Remove if: insertion order меняется
def test_mutate_config_permit_access_before_forward() -> None:
    """permit-access вставляется перед первой forward-socks5t строкой (sed -i эквивалент)."""
    content = "listen-address 0.0.0.0:8118\nforward-socks5t / 127.0.0.1:9050 .\n"
    new_content, _ = privoxy_config.mutate_config(content, "0.0.0.0:8118", "127.0.0.1:9050")
    lines = new_content.splitlines()
    fwd_idx = next(i for i, ln in enumerate(lines) if ln.startswith("forward-socks5t"))
    permit_127 = next(i for i, ln in enumerate(lines) if ln.startswith("permit-access 127.0.0.1"))
    permit_172 = next(i for i, ln in enumerate(lines) if ln.startswith("permit-access 172.16.0.0/12"))
    assert permit_127 < fwd_idx, "permit-access 127.0.0.1 должен быть ПЕРЕД forward-socks5t"
    assert permit_172 < fwd_idx, "permit-access 172.16.0.0/12 должен быть ПЕРЕД forward-socks5t"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · mutate_config: нет forward → permit-access аппендится (D3)
# · Scenario: конфиг без forward-socks5t → permit-access НЕ теряется (исправление silent-drop sed)
# · Last fail: N/A (new — D3 test-first; sed /^forward-socks5t/i молча дропал при отсутствии строки)
# · Remove if: append-fallback для permit-access удалён
def test_mutate_config_permit_access_appended_when_no_forward() -> None:
    """Нет forward-socks5t → permit-access аппендится в конец (не молча теряется, как в sed)."""
    content = "listen-address 0.0.0.0:8118\n"
    new_content, changed = privoxy_config.mutate_config(content, "0.0.0.0:8118", "127.0.0.1:9050")
    assert changed is True
    assert "permit-access 127.0.0.1" in new_content
    assert "permit-access 172.16.0.0/12" in new_content


# endregion TEST_mutate_config


# region TEST_write_privoxy_config (file I/O + идемпотентность)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · write_privoxy_config: запись в файл (D3, TEST_SPEC)
# · Scenario: свежий файл → записан, возвращено True
# · Last fail: N/A (new — D3 test-first)
# · Remove if: write_privoxy_config semantics change
def test_write_privoxy_config(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """write_privoxy_config: свежий конфиг → записан, True (изменения внесены)."""
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config"
    changed = privoxy_config.write_privoxy_config(str(config_path))
    assert changed is True
    content = config_path.read_text()
    assert "listen-address 0.0.0.0:8118" in content
    assert "permit-access 127.0.0.1" in content
    assert "forward-socks5t / 127.0.0.1:9050 ." in content
    _assert_imp9(caplog)


# 🧪 TRAP[TEST] · DevPlan 125 T9 (D-6) · privoxy config mode 0644 (сервис user privoxy читает)
# · Regression: tempfile.mkstemp (0600) + os.replace → root-only конфиг → privoxy.service
# ·   «Fatal error: can't open configuration file ... Permission denied» (rc=1)
# · Scenario: запись конфига → mode == 0644 (world-readable, канон dpkg — конфиг без секретов)
# · Last fail: 2026-08-03 — прод tronyx-vps privoxy failed 7h (D-6)
# · Remove if: privoxy_config writer изменён
def test_write_privoxy_config_mode_0644(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """write_privoxy_config: файл записывается с mode 0644 (D-6, DevPlan 125 T9)."""
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config"
    assert privoxy_config.write_privoxy_config(str(config_path)) is True

    mode = config_path.stat().st_mode & 0o777
    assert mode == 0o644, f"ожидался mode 0644 (читаемость сервисом privoxy), got {oct(mode)}"
    _assert_imp9(caplog)


# 🧪 TRAP[TEST] · NEGATIVE (R5) · test_privoxy_config_idempotent — двойной вызов = no-op (D3)
# · Scenario: второй вызов write_privoxy_config → False (никаких изменений), конфиг не повреждён
# · Last fail: N/A (new — D3 test-first; R5: идемпотентность shell grep-guard сохраняется в Python)
# · Remove if: write_privoxy_config перестаёт быть идемпотентным
def test_privoxy_config_idempotent_negative(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """R5 negative: двойной вызов = no-op (второй → False, содержимое идентично)."""
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config"
    assert privoxy_config.write_privoxy_config(str(config_path)) is True
    first_content = config_path.read_text()

    assert privoxy_config.write_privoxy_config(str(config_path)) is False, "второй вызов должен быть no-op"
    assert config_path.read_text() == first_content, "конфиг не должен меняться при повторной записи"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · write_privoxy_config: существующий корректный → no-op (D3)
# · Scenario: конфиг уже содержит нужные строки → False, контент не тронут (no-clobber)
# · Last fail: N/A (new — D3 test-first; TEST_SPEC test_privoxy_config_no_clobber)
# · Remove if: no-clobber семантика меняется
def test_privoxy_config_no_clobber(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """no-clobber: существующий корректный конфиг не перезаписывается (True→False→идентичность)."""
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config"
    config_path.write_text(
        "listen-address 0.0.0.0:8118\n"
        "permit-access 127.0.0.1\n"
        "permit-access 172.16.0.0/12\n"
        "forward-socks5t / 127.0.0.1:9050 .\n"
    )
    original = config_path.read_text()
    changed = privoxy_config.write_privoxy_config(str(config_path))
    assert changed is False
    assert config_path.read_text() == original, "корректный конфиг не должен быть перезаписан"


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · write_privoxy_config: кастомные адреса (D3)
# · Scenario: listen_addr/forward_addr параметры → используются в мутации
# · Last fail: N/A (new — D3 test-first)
# · Remove if: параметризация удалена
def test_write_privoxy_config_custom_addrs(caplog: pytest.LogCaptureFixture, tmp_path) -> None:
    """Кастомные listen_addr/forward_addr применяются при мутации."""
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config"
    privoxy_config.write_privoxy_config(str(config_path), listen_addr="127.0.0.1:8118", forward_addr="127.0.0.1:9051")
    content = config_path.read_text()
    assert "listen-address 127.0.0.1:8118" in content
    assert "forward-socks5t / 127.0.0.1:9051 ." in content


# endregion TEST_write_privoxy_config


# region TEST_CLI


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --config (D3, shell вызов python3 privoxy_config.py --config)
# · Scenario: main(["--config", path]) → exit 0, конфиг записан
# · Last fail: N/A (new — D3 test-first)
# · Remove if: CLI удалён
def test_cli_config(caplog: pytest.LogCaptureFixture, tmp_path, monkeypatch) -> None:
    """CLI --config: exit 0, конфиг записан (shell-фасад вызывает этот CLI)."""
    caplog.set_level(logging.INFO)
    config_path = tmp_path / "config"
    rc = privoxy_config.main(["--config", str(config_path)])
    assert rc == 0
    assert "listen-address 0.0.0.0:8118" in config_path.read_text()
    _assert_imp9(caplog)


# 🧪 TRAP[TEST] · 2026-08-02 · Regression · CLI --config: отсутствует аргумент → usage error (D3)
# · Scenario: main([]) → SystemExit(2) (argparse parser.error)
# · Last fail: N/A (new — D3 test-first)
# · Remove if: CLI удалён
def test_cli_missing_config_arg(caplog: pytest.LogCaptureFixture) -> None:
    """CLI без --config → parser.error (fail-fast)."""
    caplog.set_level(logging.INFO)
    with pytest.raises(SystemExit) as excinfo:
        privoxy_config.main([])
    assert excinfo.value.code == 2


# endregion TEST_CLI
