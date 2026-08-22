# GREP_SUMMARY: test-shared-ssh-opts SSH_OPTS build-rsync-ssh-opts cli --shell --rsync-e canonical sole-source-of-truth
# STRUCTURE: ▶ test_canonical_list → test_connect_timeout_from_timeouts → test_build_rsync_ssh_opts → test_cli_shell → test_cli_rsync_e → test_cli_requires_flag
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/shared/ssh_opts.py — единый SoT SSH-флагов (D1, U-15).
## @scope    Tests: канонический SSH_OPTS список, build_rsync_ssh_opts(), CLI --shell/--rsync-e.
## @invariants
##   - SSH_OPTS == канон (5 флагов, точный порядок)
##   - ConnectTimeout == timeouts.SSH_CONNECT_TIMEOUT (единый таймаут)
##   - CLI --shell печатает флаги через пробел; --rsync-e — строку ssh
##   - LDD: IMP:9 в успешных сценариях (assert в каждом тесте)
# endregion MODULE_CONTRACT

import logging
from unittest.mock import patch

import pytest

from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts, main
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT
from tests.helpers.gate_helpers import assert_ldd_imp9

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

_CANONICAL_SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
    "-o",
    "ServerAliveInterval=30",
    "-o",
    "ServerAliveCountMax=10",
]


# T2.16a: _assert_imp9 консолидирован в gate_helpers.assert_ldd_imp9
# region FUNC_test_ssh_opts_canonical
## @purpose — Verify SSH_OPTS равен канону (5 флагов, порядок, значения) — U-15.
## @complexity — O(1)
def test_ssh_opts_canonical(caplog: pytest.LogCaptureFixture) -> None:
    """SSH_OPTS должен быть РОВНО каноническим набором (D1, U-15)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: SSH_OPTS канон
    # · Last fail: N/A (new test — D1)
    # · Remove if: SSH_OPTS политика меняется осознанно (с обновлением этого теста)
    assert SSH_OPTS == _CANONICAL_SSH_OPTS, f"SSH_OPTS = {SSH_OPTS} != канон {_CANONICAL_SSH_OPTS}"
    assert len(SSH_OPTS) == 10  # 5 флагов × ("-o" + значение)
    assert "BatchMode=yes" in SSH_OPTS
    assert "StrictHostKeyChecking=accept-new" in SSH_OPTS
    assert f"ConnectTimeout={SSH_CONNECT_TIMEOUT}" in SSH_OPTS
    assert "ServerAliveInterval=30" in SSH_OPTS
    assert "ServerAliveCountMax=10" in SSH_OPTS
    logger.info("[IMP:9][test_ssh_opts_canonical] SSH_OPTS == канон: %s", SSH_OPTS)
    assert_ldd_imp9(caplog)


# endregion


# region FUNC_test_connect_timeout_from_timeouts
## @purpose — Verify ConnectTimeout в SSH_OPTS берётся из timeouts.SSH_CONNECT_TIMEOUT (U-11/U-15).
## @complexity — O(1)
def test_connect_timeout_from_timeouts(caplog: pytest.LogCaptureFixture) -> None:
    """ConnectTimeout в SSH_OPTS должен использовать timeouts.SSH_CONNECT_TIMEOUT."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: ConnectTimeout = единый таймаут
    # · Last fail: N/A (ConnectTimeout=10 outlier устранён — T2)
    # · Remove if: SSH_CONNECT_TIMEOUT значение меняется (обновить timeouts.py)
    ct_idx = SSH_OPTS.index(f"ConnectTimeout={SSH_CONNECT_TIMEOUT}")
    assert SSH_OPTS[ct_idx] == f"ConnectTimeout={SSH_CONNECT_TIMEOUT}"
    assert SSH_CONNECT_TIMEOUT == 30
    logger.info("[IMP:9][test_connect_timeout] ConnectTimeout=%s — из timeouts (U-11)", SSH_CONNECT_TIMEOUT)
    assert_ldd_imp9(caplog)


# endregion


# region FUNC_test_build_rsync_ssh_opts
## @purpose — Verify build_rsync_ssh_opts строит `ssh <flags>` (единственная реализация rsync -e).
## @complexity — O(k)
def test_build_rsync_ssh_opts(caplog: pytest.LogCaptureFixture) -> None:
    """build_rsync_ssh_opts() должен возвращать f"ssh {' '.join(SSH_OPTS)}"."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: rsync -e строка
    # · Last fail: N/A (new test — переезд из core_deliverer/overlay_deliverer)
    # · Remove if: build_rsync_ssh_opts меняет формат
    expected = f"ssh {' '.join(SSH_OPTS)}"
    result = build_rsync_ssh_opts()
    assert result == expected
    assert result.startswith("ssh -o BatchMode=yes")
    logger.info("[IMP:9][test_build_rsync_ssh_opts] rsync -e: %s", result)
    assert_ldd_imp9(caplog)


# endregion


# region FUNC_test_cli_shell
## @purpose — Verify CLI --shell печатает флаги через пробел (для bash `read -r -a`, 3.2).
## @complexity — O(1)
def test_cli_shell(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --shell должен печатать SSH_OPTS через пробел (bash read -r -a)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: python3 -m ... --shell
    # · Last fail: N/A (new test — D1 shell-фасад)
    # · Remove if: CLI формат меняется
    with patch("core.internal.shared.ssh_opts.sys.stdout") as mock_stdout:
        rc = main(["--shell"])
    assert rc == 0
    written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
    assert written.strip() == " ".join(SSH_OPTS)
    assert "BatchMode=yes" in written
    logger.info("[IMP:9][test_cli_shell] --shell output: %s", written.strip())
    assert_ldd_imp9(caplog)


# endregion


# region FUNC_test_cli_rsync_e
## @purpose — Verify CLI --rsync-e печатает строку ssh.
## @complexity — O(1)
def test_cli_rsync_e(caplog: pytest.LogCaptureFixture) -> None:
    """CLI --rsync-e должен печатать `ssh -o ...` (для rsync -e)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: python3 -m ... --rsync-e
    # · Last fail: N/A (new test)
    # · Remove if: CLI формат меняется
    with patch("core.internal.shared.ssh_opts.sys.stdout") as mock_stdout:
        rc = main(["--rsync-e"])
    assert rc == 0
    written = "".join(call.args[0] for call in mock_stdout.write.call_args_list)
    assert written.strip() == build_rsync_ssh_opts()
    assert written.startswith("ssh -o")
    logger.info("[IMP:9][test_cli_rsync_e] --rsync-e output: %s", written.strip())
    assert_ldd_imp9(caplog)


# endregion


# region FUNC_test_cli_requires_flag
## @purpose — Verify CLI без флага → exit 2 (fail-fast, mutually exclusive group).
## @complexity — O(1)
# GUARD-PRESERVE (168): единственное покрытие fail-fast ветки CLI ssh_opts (SystemExit(2) без
# --shell/--rsync-e) — контракт mutually-exclusive группы; тихий no-op сломал бы bash-фасады
def test_cli_requires_flag(caplog: pytest.LogCaptureFixture) -> None:
    """CLI без --shell/--rsync-e должен завершиться с кодом 2 (SystemExit)."""
    caplog.set_level(logging.INFO)

    # 🧪 TRAP[TEST] · Regression · Scenario: невалидный CLI вызов
    # · Last fail: N/A (new test)
    # · Remove if: argparse required-group меняется
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


# endregion
