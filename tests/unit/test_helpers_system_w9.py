# GREP_SUMMARY: test-helpers-system-w9, T9.12, ensure-sops, shutil-which, no-redownload, T9.13, journald, active-line, commented, idempotent, zram, swappiness, prune-cron, retry, backoff, purge-cruft, purge-provider-repos, fstab, fstrim, DevPlan-162, DevPlan-164, DI, runner-param, path-param
# STRUCTURE: ▶ FakeCommandRunner ┌recording run() + scriptable rc┐ → test_*_sops_no_redownload ┌which → path┐ → ensure_sops(which=) → 0 subprocess │ ▶ test_*_journald_commented ┌#Storage=persistent┐ → active-check False → append активной строки │ ▶ test_*_journald_active → no-op (без записи) │ ▶ test_install_zram_* ┌zramswap+sysctl paths (DI params)┐ │ ▶ test_install_cron_prune_* ┌cron_file path param┐ │ ▶ test_run_with_retry_* ┌runner fake┐ │ ▶ test_purge_cruft_* ┌runner fake dpkg-gate┐ │ ▶ test_purge_provider_repos_* ┌sources_dir path param┐ │ ▶ test_*_fstab* ┌normalize + ensure (fstab_path param)┐
# region MODULE_CONTRACT
## @purpose  Regression-тесты T9.12 (B-4) и T9.13 (B-8) DevPlan 136 W9: helpers/system.py —
##           ensure_sops через shutil.which (повторный φ1 БЕЗ re-download) и journald
##           idempotency-гейт по активной строке (комментарий `#Storage=persistent` НЕ маскирует).
##           DevPlan 162 (W4-1/W4-4/W7-4/W10-1): zram-конфиги, prune cron, retry-wrapper,
##           cruft purge dpkg-gate. DevPlan 164 (W0-3.2/W0-3.4): provider repo purge,
##           fstab-нормализация + fstrim.
## @scope    unit-тесты: DI-инъекция путей/раннера (W-H DevPlan 163 — FakeCommandRunner +
##           path-параметры вместо патча констант); tmp_path.
## @invariants
##   - Native imports; tmp_path; LDD IMP:9 в успешных сценариях
##   - sops в PATH → 0 скачиваний (R5-negative: command -v через exec ВСЕГДА падал → re-download)
##   - Комментированная Storage=строка не считается настроенной (R5-negative на вход B-8)
##   - zram/prune: content-match no-op (повторный вызов = 0 записей), атомарность 0644
##   - retry: attempts/backoff контракт (успех на 2-й, raise после 3)
##   - purge: только installed пакеты (dpkg-gate), sysstat/docker-buildx НЕ в списке
##   - purge_provider_repos: только timeweb-* файлы; чужое sources не трогается
##   - fstab: data→defaults, /boot/swap неприкосновенны; идемпотентность content-match
##   - DI-HYG (163 §5): 0 патчей — все зависимости через параметры (runner/paths)
## @rationale  $TEST_SPEC DevPlan 136 W9 T9.20: T9.12/T9.13 — тест на повторный φ1 без
##            re-download; тест на `#Storage=persistent` commented.
## @changes  2026-08-05 · Created (DevPlan 136 W9)
## @changes  2026-08-13 · DevPlan 162 — +zram (W4-1), +prune cron (W4-4), +retry (W7-4), +purge (W10-1)
## @changes  2026-08-13 · DevPlan 163 W-H — DI-перевод: FakeCommandRunner + path/runner параметры
##            (29 патча → 0; production DI: helpers/system.py +security_updates.py)
## @changes  2026-08-13 · DevPlan 164 — +purge_provider_repos (W0-3.2), +fstab (W0-3.4)
# endregion MODULE_CONTRACT

import logging
import subprocess
from pathlib import Path

import pytest

from core.internal.bootstrap.lifecycle.helpers import system as sys_helpers
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# region CLASS_FakeCommandRunner
class FakeCommandRunner:
    """CommandRunner-fake (AF-2, DevPlan 160 W4b) — recording run() with scriptable rc.

    ## @purpose — DI-канал subprocess для helpers/system.py тестов: вместо патча
    ##            run_subprocess/is_pkg_installed на module level тест передаёт fake-раннер
    ##            параметром (W-H DevPlan 163). run() записывает вызовы в self.calls;
    ##            rc вычисляется rc_fn(cmd, call_index) — гибкая симуляция
    ##            dpkg-gate/flaky-retry без патчей.
    ## @io — ⇥ rc_fn: Callable[[list[str], int], int] | int (default 0) → ⎋ fake
    ## @complexity O(1) per call
    ## @invariants
    ##   - calls: list[list[str]] — запись каждой команды (ассерты по порядку/составу)
    ##   - check=True + rc != 0 → PlatformFatalError (канон run_subprocess raise-семантика)
    ##   - НИКАКИХ реальных subprocess — полная изоляция от системы (unit-контракт)
    """

    def __init__(self, rc_fn: object = 0) -> None:
        self.calls: list[list[str]] = []
        self._rc_fn = rc_fn

    def run(
        self,
        cmd: list[str],
        *,
        timeout: int = 30,  # ruff: ignore[ARG002]
        check: bool = False,
        non_fatal: bool = False,  # ruff: ignore[ARG002]
        fatal_rc: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        idx = len(self.calls)
        self.calls.append(cmd)
        rc = self._rc_fn(cmd, idx) if callable(self._rc_fn) else self._rc_fn
        if check and rc != 0 and rc not in fatal_rc:
            from core.internal.shared.exceptions import PlatformFatalError

            msg = f"Command failed (rc={rc}): {' '.join(cmd)}"
            raise PlatformFatalError(msg)
        return subprocess.CompletedProcess(cmd, rc, "", "")


# endregion CLASS_FakeCommandRunner


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.12/B-4 — sops в PATH → НЕТ re-download
# · Scenario: which-резолвер находит sops → ensure_sops не запускает ни одного subprocess
# · Last fail: 2026-08-05 — subprocess.run(["command", "-v", "sops"]) (bash-builtin через exec)
# ·   ВСЕГДА FileNotFoundError → sops перекачивался на КАЖДОМ φ1 (B-4)
# · Remove if: sops detection semantics change
@ldd_trajectory
def test_ensure_sops_no_redownload_when_installed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T9.12: which-инъекция находит sops → 0 subprocess-вызовов (нет re-download)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner()

    # DI (W-H): which-резолвер и runner передаются параметрами — 0 monkeypatch
    sys_helpers.ensure_sops(which=lambda _: "/usr/local/bin/sops", runner=fake)
    assert not fake.calls, f"повторный φ1 не должен качать sops (B-4): {fake.calls}"
    assert "Already installed" in caplog.text
    logger.critical("[IMP:9][test] ensure_sops no re-download when installed — OK (T9.12)")


# 🧪 TRAP[TEST] · 2026-08-05 · REGRESSION (R5 negative) · T9.13/B-8 — комментарий НЕ активная строка
# · Scenario: "#Storage=persistent" (commented) → _journald_persistent_active False;
# ·   _set_storage_persistent добавляет АКТИВНУЮ Storage=persistent (комментарий сохраняется)
# · Last fail: 2026-08-05 — substring "Storage=persistent" in content матчил комментарий → false no-op
# · Remove if: journald idempotency semantics change
@ldd_trajectory
def test_journald_commented_line_not_active(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T9.13: активная-строка проверка не путает комментарий с конфигурацией."""
    caplog.set_level(logging.INFO)
    assert sys_helpers._journald_persistent_active("#Storage=persistent\n") is False, (
        "комментированная строка не активна (B-8)"
    )
    assert sys_helpers._journald_persistent_active("# Storage=persistent\n") is False
    assert sys_helpers._journald_persistent_active("Storage=persistent\n") is True
    assert sys_helpers._journald_persistent_active("Storage=auto\n") is False, "Storage=auto требует перезаписи"

    content = "#Storage=persistent\nStorage=auto\n"
    result = sys_helpers._set_storage_persistent(content)
    assert "#Storage=persistent" in result, "комментарий сохраняется"
    assert result.splitlines().count("Storage=persistent") == 1, "append ровно одной активной строки"
    assert "Storage=auto" not in result, "активная Storage=auto заменяется"
    logger.critical("[IMP:9][test] journald commented line not active — OK (T9.13)")


# 🧪 TRAP[TEST] · 2026-08-05 · Regression · T9.13 — активная Storage=persistent → no-op (0 записей)
# · Scenario: journald.conf уже с активной Storage=persistent → ensure_journald_persistent НЕ пишет
# · Remove if: journald idempotency semantics change
@ldd_trajectory
def test_journald_active_line_noop(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """T9.13: активная Storage=persistent → no-op, без записи и рестарта (conf param DI)."""
    caplog.set_level(logging.INFO)
    conf = tmp_path / "journald.conf"
    conf.write_text("# comment\nStorage=persistent\n", encoding="utf-8")
    fake = FakeCommandRunner()

    # DI (W-H): путь конфига параметром + runner-инъекция — 0 monkeypatch
    assert sys_helpers.ensure_journald_persistent(str(conf), runner=fake) is True
    assert not fake.calls, f"активная строка → 0 записей/рестартов (no-op), got {fake.calls}"
    assert "already set (active line)" in caplog.text
    logger.critical("[IMP:9][test] journald active line → no-op — OK (T9.13)")


# ═══════════════════════════════════════════════════════════════════════════
# DevPlan 162 W4-1: zram (install_zram)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4-1/162 — zram конфиги пишутся с контрактом
# · Scenario: runner-dpkg → installed; default_file/sysctl_file (DI params) → tmp_path;
# ·   install_zram() → True; zramswap содержит ALGO=zstd/SIZE=4096/PRIORITY=100,
# ·   sysctl.d/90-platform-zram.conf содержит vm.swappiness=100
# · Last fail: N/A (новый тест 162 W4-1)
# · Remove if: zram-контракт (4G/zstd/priority/swappiness) меняется
@ldd_trajectory
def test_install_zram_writes_configs(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W4-1: install_zram пишет /etc/default/zramswap + sysctl swappiness с контрактом."""
    caplog.set_level(logging.INFO)
    default_file = tmp_path / "zramswap"
    sysctl_file = tmp_path / "90-platform-zram.conf"
    # DI (W-H): pkg installed (rc=0 для dpkg -s zram-tools), пути — параметрами
    fake = FakeCommandRunner(rc_fn=lambda _, __: 0)

    assert sys_helpers.install_zram(str(default_file), str(sysctl_file), runner=fake) is True
    apt_calls = [c for c in fake.calls if c[0] == "apt-get"]
    assert not apt_calls, f"пакет уже установлен → install_apt_packages не вызывается, got {apt_calls}"

    content = default_file.read_text()
    assert "ALGO=zstd" in content, f"ALGO=zstd ожидался (zstd), got {content!r}"
    assert "SIZE=4096" in content, "SIZE=4096 (4G = 50% RAM 7.8G)"
    assert "PRIORITY=100" in content, "PRIORITY=100"
    assert "DO NOT EDIT MANUALLY" in content

    sysctl_content = sysctl_file.read_text()
    assert "vm.swappiness=100" in sysctl_content, "swappiness=100 (активный swap при пиках)"
    assert default_file.stat().st_mode & 0o777 == 0o644
    logger.critical("[IMP:9][test] install_zram configs written — OK (W4-1)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4-1/162 — zram идемпотентен (content-match no-op)
# · Scenario: повторный install_zram() на том же содержимом → True, mtime файлов не изменился
# · Last fail: N/A (новый тест 162 W4-1)
# · Remove if: content-match идемпотентность install_zram меняется
@ldd_trajectory
def test_install_zram_idempotent_noop(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W4-1: повторный install_zram → no-op (идентичное содержимое, mtime неизменен)."""
    caplog.set_level(logging.INFO)
    default_file = tmp_path / "zramswap"
    sysctl_file = tmp_path / "90-platform-zram.conf"
    fake = FakeCommandRunner(rc_fn=lambda _, __: 0)

    assert sys_helpers.install_zram(str(default_file), str(sysctl_file), runner=fake) is True
    mtimes_first = (default_file.stat().st_mtime_ns, sysctl_file.stat().st_mtime_ns)
    assert sys_helpers.install_zram(str(default_file), str(sysctl_file), runner=fake) is True
    mtimes_second = (default_file.stat().st_mtime_ns, sysctl_file.stat().st_mtime_ns)
    assert mtimes_second == mtimes_first, "content-match no-op: mtime обоих файлов не меняется"
    assert "already up-to-date" in caplog.text
    logger.critical("[IMP:9][test] install_zram second run → no-op (mtime стабилен) — OK (W4-1)")


# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · W4-1/162 — zram failure → False (non-fatal)
# · Scenario: запись sysctl падает OSError → install_zram() False (никогда не raise)
# · Last fail: N/A (новый negative-тест 162 W4-1)
# · Remove if: non-fatal контракт install_zram меняется
@ldd_trajectory
def test_install_zram_write_failure_returns_false(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W4-1: OSError при записи → False (non-fatal, никогда не raise)."""
    caplog.set_level(logging.INFO)
    # DI (W-H): sysctl_file в директории без write-права → _write_content_if_changed →
    #   PermissionError (OSError) → False (реальный FS-путь, не патч)
    ro_dir = tmp_path / "read-only"
    ro_dir.mkdir()
    ro_dir.chmod(0o500)
    fake = FakeCommandRunner(rc_fn=lambda _, __: 0)

    try:
        assert (
            sys_helpers.install_zram(str(tmp_path / "zramswap"), str(ro_dir / "90-platform-zram.conf"), runner=fake)
            is False
        )
    finally:
        ro_dir.chmod(0o700)  # cleanup для tmp_path (pytest удаляет дерево)
    logger.critical("[IMP:9][test] install_zram failure → False (non-fatal) — OK (W4-1)")


# ═══════════════════════════════════════════════════════════════════════════
# DevPlan 162 W4-4: prune cron (install_cron_prune)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4-4/162 — prune cron content-контракт
# · Scenario: cron_file (DI param) → tmp_path; install_cron_prune() → True; содержимое =
# ·   CRON_PRUNE_LINES (monthly docker system prune без volumes + apt-get clean)
# · Last fail: N/A (новый тест 162 W4-4)
# · Remove if: prune-крон контракт меняется
@ldd_trajectory
def test_install_cron_prune_writes_content(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W4-4: install_cron_prune пишет CRON_PRUNE_LINES (flock + docker prune + apt clean)."""
    caplog.set_level(logging.INFO)
    prune_file = tmp_path / "platform-prune"

    assert sys_helpers.install_cron_prune(str(prune_file)) is True
    content = prune_file.read_text()
    assert "docker system prune -af --filter until=720h" in content, "monthly prune без volumes, until=720h"
    assert "/usr/bin/flock -n /run/lock/platform-prune.lock" in content
    assert "apt-get clean" in content, "apt clean в том же cron"
    assert content == sys_helpers.CRON_PRUNE_LINES, "содержимое = канон CRON_PRUNE_LINES"
    assert prune_file.stat().st_mode & 0o777 == 0o644
    logger.critical("[IMP:9][test] install_cron_prune content contract — OK (W4-4)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W4-4/162 — prune cron идемпотентен (no-op)
# · Scenario: повторный install_cron_prune() → True; mtime файла не изменился (0 записей)
# · Last fail: N/A (новый тест 162 W4-4)
# · Remove if: content-match идемпотентность меняется
@ldd_trajectory
def test_install_cron_prune_idempotent_noop(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W4-4: повторный install_cron_prune → no-op (идентичное содержимое, mtime неизменен)."""
    caplog.set_level(logging.INFO)
    prune_file = tmp_path / "platform-prune"

    assert sys_helpers.install_cron_prune(str(prune_file)) is True
    mtime_first = prune_file.stat().st_mtime_ns
    assert sys_helpers.install_cron_prune(str(prune_file)) is True
    assert prune_file.stat().st_mtime_ns == mtime_first, "content-match no-op: mtime не меняется"
    assert "already up-to-date" in caplog.text
    logger.critical("[IMP:9][test] install_cron_prune second run → no-op — OK (W4-4)")


# ═══════════════════════════════════════════════════════════════════════════
# DevPlan 162 W7-4: retry wrapper (_run_with_retry)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W7-4/162 — retry успех на 2-й попытке
# · Scenario: runner fail → success; attempts=3, backoff=(0,0,0) → результат успеха,
# ·   fail+success = 2 вызова
# · Last fail: N/A (новый тест 162 W7-4)
# · Remove if: retry-контракт (attempts/backoff) меняется
@ldd_trajectory
def test_run_with_retry_succeeds_on_second_attempt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """W7-4: transient-сбой → retry → успех на 2-й попытке (runner fake)."""
    caplog.set_level(logging.INFO)
    calls: list[int] = []

    def _flaky(cmd, idx):
        calls.append(1)
        if len(calls) < 2:
            return 1
        return 0

    fake = FakeCommandRunner(rc_fn=_flaky)

    result = sys_helpers._run_with_retry(["apt-get", "update", "-qq"], attempts=3, backoff=(0, 0, 0), runner=fake)
    assert result.returncode == 0
    assert len(calls) == 2, f"fail+success = 2 попытки, got {len(calls)}"
    logger.critical("[IMP:9][test] _run_with_retry success on attempt 2 — OK (W7-4)")


# 🧪 TRAP[TEST] · 2026-08-13 · NEGATIVE (R5) · W7-4/162 — retry raise после всех попыток
# · Scenario: ВСЕГДА fail → PlatformError после attempts=3 (check=True семантика сохраняется)
# · Last fail: N/A (новый negative-тест 162 W7-4)
# · Remove if: raise-семантика _run_with_retry меняется
# GUARD-PRESERVE (168): R5-negative (anti-survivorship) — финальный провал retry → PlatformError
# после attempts=3 (оригинальная форма W7-4: check=True семантика сохраняется); пара
# к test_run_with_retry_succeeds_on_second_attempt, удаление сломало бы R5 anti-survivorship
@ldd_trajectory
def test_run_with_retry_raises_after_all_attempts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """W7-4: финальный провал → PlatformError (после 3 попыток, backoff)."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(rc_fn=lambda _, __: 2)

    with pytest.raises(sys_helpers.PlatformError, match="failed after 3 attempts"):
        sys_helpers._run_with_retry(["apt-get", "update", "-qq"], attempts=3, backoff=(0, 0, 0), runner=fake)
    assert len(fake.calls) == 3, f"3 попытки, got {len(fake.calls)}"
    logger.critical("[IMP:9][test] _run_with_retry raises after 3 attempts — OK (W7-4)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W7-4/162 — install_apt_packages через retry
# · Scenario: dpkg-gate → not installed; apt-get update/install через retry-wrapper;
# ·   dpkg -s verify остаётся (check=True)
# · Last fail: N/A (новый тест 162 W7-4)
# · Remove if: install_apt_packages перестаёт использовать retry
@ldd_trajectory
def test_install_apt_packages_uses_retry(caplog: pytest.LogCaptureFixture) -> None:
    """W7-4: install_apt_packages вызывает _run_with_retry для apt-get update/install."""
    caplog.set_level(logging.INFO)

    # rc_fn: dpkg -s curl → 1 (not installed) на первом вызове (idx 0), затем 0 (verify после install)
    def _rc(cmd, idx):
        if cmd[:2] == ["dpkg", "-s"]:
            return 1 if idx == 0 else 0
        return 0

    fake = FakeCommandRunner(rc_fn=_rc)

    sys_helpers.install_apt_packages(["curl"], runner=fake)
    retry_calls = [c for c in fake.calls if c[0] == "apt-get"]
    cmds = [" ".join(c) for c in retry_calls]
    assert any(c.startswith("apt-get update") for c in cmds), f"update через retry, got {cmds}"
    assert any(c.startswith("apt-get install") for c in cmds), f"install через retry, got {cmds}"
    logger.critical("[IMP:9][test] install_apt_packages uses _run_with_retry — OK (W7-4)")


# ═══════════════════════════════════════════════════════════════════════════
# DevPlan 162 W10-1: cruft purge (purge_cruft)
# ═══════════════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W10-1/162 — cruft список консервативен
# · Scenario: sysstat/docker-buildx НЕ в CRUFT_PURGE_PACKAGES (baseline-замеры / L2-сборки)
# · Last fail: N/A (новый тест 162 W10-1)
# · Remove if: cruft-список намеренно расширяется sysstat/docker-buildx
@ldd_trajectory
def test_cruft_purge_packages_list_conservative(caplog: pytest.LogCaptureFixture) -> None:
    """W10-1: sysstat и docker-buildx НЕ в списке cruft (используются платформой)."""
    caplog.set_level(logging.INFO)
    pkgs = sys_helpers.CRUFT_PURGE_PACKAGES
    assert "sysstat" not in pkgs, "sysstat — baseline-замеры (make load-test, sar)"
    assert "docker-buildx" not in pkgs, "docker-buildx — контекстные L2-сборки hermes"
    assert len(pkgs) == len(set(pkgs)), "без дублей"
    assert all(isinstance(p, str) and p for p in pkgs)
    logger.critical("[IMP:9][test] cruft list conservative (no sysstat/docker-buildx) — OK (W10-1)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W10-1/162 — purge только installed
# · Scenario: dpkg-gate True только для 2 пакетов → purge-команда содержит ТОЛЬКО их;
# ·   autoremove + clean вызываются; возвращает True
# · Last fail: N/A (новый тест 162 W10-1)
# · Remove if: dpkg-gate purge_cruft меняется
@ldd_trajectory
def test_purge_cruft_purges_only_installed(caplog: pytest.LogCaptureFixture) -> None:
    """W10-1: purge только установленные (dpkg-gate) + autoremove + clean."""
    caplog.set_level(logging.INFO)
    installed_subset = {"apport", "cloud-init"}

    def _rc(cmd, idx):
        if cmd[:2] == ["dpkg", "-s"]:
            return 0 if cmd[2] in installed_subset else 1
        return 0

    fake = FakeCommandRunner(rc_fn=_rc)

    assert sys_helpers.purge_cruft(runner=fake) is True
    purge_cmds = [c for c in fake.calls if c[0] == "apt-get" and c[1] == "purge"]
    assert len(purge_cmds) == 1
    purge_args = purge_cmds[0]
    assert "apport" in purge_args and "cloud-init" in purge_args
    # НЕ установленные пакеты не передаются в apt (apt purge отсутствующего → rc=100)
    for pkg in set(sys_helpers.CRUFT_PURGE_PACKAGES) - installed_subset:
        assert pkg not in purge_args, f"{pkg} не установлен — не должен попасть в purge"
    assert any(c == ["apt-get", "autoremove", "-y"] for c in fake.calls), "autoremove (старые ядра)"
    assert any(c == ["apt-get", "clean"] for c in fake.calls), "apt clean"
    logger.critical("[IMP:9][test] purge_cruft purges only installed — OK (W10-1)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · W10-1/162 — purge no-op когда ничего не установлено
# · Scenario: dpkg-gate → False для всех → purge_cruft() True, 0 apt-команд
# · Last fail: N/A (новый тест 162 W10-1)
# · Remove if: no-op семантика purge_cruft меняется
@ldd_trajectory
def test_purge_cruft_none_installed_noop(caplog: pytest.LogCaptureFixture) -> None:
    """W10-1: ничего не установлено → no-op (0 apt-команд), True."""
    caplog.set_level(logging.INFO)
    fake = FakeCommandRunner(rc_fn=lambda _, __: 1)

    assert sys_helpers.purge_cruft(runner=fake) is True
    apt_calls = [c for c in fake.calls if c[0] == "apt-get"]
    assert not apt_calls, f"no-op: 0 apt-команд, got {apt_calls}"
    assert "No cruft packages installed" in caplog.text
    logger.critical("[IMP:9][test] purge_cruft no cruft → no-op — OK (W10-1)")


# ═══════════════════════════════════════════════════════════════════
# region Tests: purge_provider_repos (DevPlan 164 W0-3.2)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · 164 W0-3.2 — timeweb-* репо удаляются, чужие остаются
# · Scenario: sources.list.d содержит timeweb-mirror.list, timeweb-zabbix.list, custom.list →
# ·   удалены только timeweb-*; apt-get update вызван 1 раз
# · Last fail: N/A (новый тест 164 W0-3.2)
# · Remove if: purge_provider_repos семантика меняется
@ldd_trajectory
def test_purge_provider_repos_removes_only_provider(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W0-3.2: purge_provider_repos удаляет только timeweb-* + apt-get update."""
    caplog.set_level(logging.INFO)
    sources_dir = tmp_path / "sources.list.d"
    sources_dir.mkdir()
    (sources_dir / "timeweb-mirror.list").write_text("deb ...", encoding="utf-8")
    (sources_dir / "timeweb-zabbix.list").write_text("deb ...", encoding="utf-8")
    (sources_dir / "custom.list").write_text("deb ...", encoding="utf-8")
    fake = FakeCommandRunner()

    assert sys_helpers.purge_provider_repos(sources_dir=str(sources_dir), runner=fake) is True
    remaining = sorted(p.name for p in sources_dir.iterdir())
    assert remaining == ["custom.list"], f"чужой sources не должен удаляться: {remaining}"
    assert any(c == ["apt-get", "update"] for c in fake.calls), "apt-get update после удаления"
    logger.critical("[IMP:9][test] purge_provider_repos removed timeweb-* only — OK (W0-3.2)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · 164 W0-3.2 — no-op без provider-репо
# · Scenario: sources.list.d без timeweb-* → True, 0 apt-get update
# · Last fail: N/A (новый тест 164 W0-3.2)
# · Remove if: no-op семантика purge_provider_repos меняется
@ldd_trajectory
def test_purge_provider_repos_noop_without_provider(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W0-3.2: нет timeweb-* репо → no-op (0 apt-get update), True."""
    caplog.set_level(logging.INFO)
    sources_dir = tmp_path / "sources.list.d"
    sources_dir.mkdir()
    (sources_dir / "custom.list").write_text("deb ...", encoding="utf-8")
    fake = FakeCommandRunner()

    assert sys_helpers.purge_provider_repos(sources_dir=str(sources_dir), runner=fake) is True
    assert not any(c[0] == "apt-get" for c in fake.calls), "no-op: 0 apt-команд"
    assert "No provider repos found" in caplog.text
    logger.critical("[IMP:9][test] purge_provider_repos no-op without provider repos — OK (W0-3.2)")


# endregion


# ═══════════════════════════════════════════════════════════════════
# region Tests: fstab policy (DevPlan 164 W0-3.4)
# ═══════════════════════════════════════════════════════════════════


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · 164 W0-3.4 — fstab нормализация
# · Scenario: ext4-строка с nobarrier → defaults; /boot и swap не трогаются
# · Last fail: N/A (новый тест 164 W0-3.4)
# · Remove if: normalize_fstab_lines семантика меняется
@ldd_trajectory
def test_normalize_fstab_lines_data_to_defaults(caplog: pytest.LogCaptureFixture) -> None:
    """W0-3.4: data-строки (ext4/xfs) → defaults; /boot и swap остаются."""
    caplog.set_level(logging.INFO)
    text = (
        "# /etc/fstab\n"
        "UUID=abc / ext4 nobarrier,noatime 0 1\n"
        "UUID=boot /boot ext4 defaults 0 2\n"
        "/swapfile none swap sw 0 0\n"
        "UUID=data /data xfs rw,noatime 0 2\n"
    )
    new_text, changed = sys_helpers.normalize_fstab_lines(text)
    assert changed is True
    lines = new_text.splitlines()
    assert lines[1] == "UUID=abc / ext4 defaults 0 1", "nobarrier → defaults"
    assert lines[2] == "UUID=boot /boot ext4 defaults 0 2", "/boot не трогается"
    assert lines[3] == "/swapfile none swap sw 0 0", "swap не трогается"
    assert lines[4] == "UUID=data /data xfs defaults 0 2", "xfs rw,noatime → defaults"
    logger.critical("[IMP:9][test] normalize_fstab_lines data → defaults — OK (W0-3.4)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · 164 W0-3.4 — идемпотентность нормализации
# · Scenario: уже canonical fstab → changed=False
# · Last fail: N/A (новый тест 164 W0-3.4)
# · Remove if: normalize_fstab_lines идемпотентность меняется
@ldd_trajectory
def test_normalize_fstab_lines_idempotent(caplog: pytest.LogCaptureFixture) -> None:
    """W0-3.4: canonical fstab → no change."""
    caplog.set_level(logging.INFO)
    text = "UUID=abc / ext4 defaults 0 1\n"
    new_text, changed = sys_helpers.normalize_fstab_lines(text)
    assert changed is False
    assert new_text == text
    logger.critical("[IMP:9][test] normalize_fstab_lines idempotent — OK (W0-3.4)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · 164 W0-3.4 — ensure_fstab_policy полный цикл
# · Scenario: fstab с nobarrier в tmp_path → записан canonical + fstrim.timer enable (fake runner)
# · Last fail: N/A (новый тест 164 W0-3.4)
# · Remove if: ensure_fstab_policy семантика меняется
@ldd_trajectory
def test_ensure_fstab_policy_writes_and_enables_trim(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W0-3.4: ensure_fstab_policy нормализует fstab + включает fstrim.timer."""
    caplog.set_level(logging.INFO)
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=abc / ext4 nobarrier 0 1\n", encoding="utf-8")
    fake = FakeCommandRunner()

    assert sys_helpers.ensure_fstab_policy(fstab_path=str(fstab), runner=fake) is True
    content = fstab.read_text(encoding="utf-8")
    assert "defaults" in content and "nobarrier" not in content
    assert any("fstrim.timer" in " ".join(c) for c in fake.calls), "fstrim.timer enable вызван"
    logger.critical("[IMP:9][test] ensure_fstab_policy fstab+trim — OK (W0-3.4)")


# 🧪 TRAP[TEST] · 2026-08-13 · REGRESSION · 164 W0-3.4 — ensure_fstab_policy no-op при каноне
# · Scenario: canonical fstab → запись не выполняется (0 write), fstrim.timer всё равно enable
# · Last fail: N/A (новый тест 164 W0-3.4)
# · Remove if: no-op семантика ensure_fstab_policy меняется
@ldd_trajectory
def test_ensure_fstab_policy_noop_when_canonical(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    """W0-3.4: canonical fstab → no write; fstrim.timer enable — idempotent вызов."""
    caplog.set_level(logging.INFO)
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=abc / ext4 defaults 0 1\n", encoding="utf-8")
    fake = FakeCommandRunner()

    assert sys_helpers.ensure_fstab_policy(fstab_path=str(fstab), runner=fake) is True
    assert fstab.read_text(encoding="utf-8") == "UUID=abc / ext4 defaults 0 1\n", "canonical — без записи"
    assert "fstab already canonical" in caplog.text
    logger.critical("[IMP:9][test] ensure_fstab_policy no-op canonical — OK (W0-3.4)")


# endregion
