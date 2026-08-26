# GREP_SUMMARY: platform-secrets unit-test pythonpath reboot-resilience service-content plan-012 T1 F-037
# STRUCTURE: ▶ MODULE_DIR → ∋ platform-secrets.service → ◇ test_unit_contains_pythonpath → ⎋ pass|fail
# @file test_platform_secrets_unit.py
# @purpose  Unit-content contract for platform-secrets.service: Environment=PYTHONPATH=/opt/platform
#           must be present in the unit itself (plan 012 T1, F-037) so that post-reboot
#           decrypt_secrets.py imports core.internal.* without manual drop-in workarounds.
# @scope    Pure file I/O — line-based .ini parsing of the systemd unit; no Docker, no subprocess.
# @invariants
##   - Unit contains Environment=PYTHONPATH=/opt/platform
##   - ExecStart references decrypt_secrets.py directly (DevPlan 173 W1.3 contract preserved)
##   - installer.py installs the unit verbatim (write_bytes(read_bytes())) — content here == node content
## @rationale One-command bootstrap (AC5): reboot → unit поднимает стек сам; PYTHONPATH drop-in
##            workaround удалён из операторского ранбука — юнит самодостаточен.
## @changes   CREATED 2026-08-26 | DevPlan 012 T1 — PYTHONPATH in unit (F-037)

# region MODULE_CONTRACT
## @purpose  Unit-content tests for platform-secrets.service (plan 012 T1).
## @scope    2 tests — pure file I/O, static_audit layer, runnable anywhere.
## @invariants
##   - Test 1: Environment=PYTHONPATH=/opt/platform present (RED before T1 fix)
##   - Test 2: ExecStart references decrypt_secrets.py (regression guard for W1.3)
##   - All tests use @ldd_trajectory decorator for IMP:9 verification
## @rationale Reboot-drill (F-037) показал: после reboot юнит без PYTHONPATH не мог импортировать
##            core.internal.* — операторы чинили ручным drop-in. Контент-тест дешевле e2e.
# endregion MODULE_CONTRACT

import logging
import pathlib
from pathlib import Path

import pytest
from conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)
MODULE_DIR: Path = Path(PLATFORM_ROOT) / "core" / "modules" / "platform-secrets"
SERVICE_PATH: Path = MODULE_DIR / "platform-secrets.service"


def _read_service() -> str:
    """Read the unit file; fail loud if missing."""
    assert SERVICE_PATH.is_file(), f"[IMP:9][ps-unit] FAIL: unit file not found: {SERVICE_PATH}"
    return SERVICE_PATH.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: PYTHONPATH present in unit (plan 012 T1 / F-037)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_unit_contains_pythonpath(caplog: pytest.LogCaptureFixture) -> None:
    """Generated/installed unit carries Environment=PYTHONPATH=/opt/platform.

    ## @purpose — F-037: после reboot decrypt_secrets.py (ExecStart) должен импортировать
    ##            core.internal.* без ручных drop-in обходов; PYTHONPATH задаётся самим юнитом.
    ## @io — ⇥ caplog → ⎋ None (asserts unit content)
    ## @complexity — O(1) — single file parse + line check
    ## @scenario — AC5: Reboot-resilience из сгенерированных юнитов
    """
    # 🧪 TRAP[TEST] · REGRESSION · F-037 reboot PYTHONPATH
    # · Scenario: reboot ноды → platform-secrets active → secrets.env расшифрован без drop-in
    # · Last fail: unit без PYTHONPATH → ImportError в decrypt_secrets.py после reboot
    # · Remove if: decrypt_secrets.py перестаёт импортировать core.internal.* (standalone)
    logger.info("[IMP:7][ps-unit] Checking PYTHONPATH in: %s", SERVICE_PATH)

    content = _read_service()
    pythonpath_lines = [ln.strip() for ln in content.splitlines() if ln.strip().startswith("Environment=PYTHONPATH=")]
    logger.info("[IMP:8][ps-unit] PYTHONPATH lines found: %s", pythonpath_lines)

    assert any(ln == "Environment=PYTHONPATH=/opt/platform" for ln in pythonpath_lines), (
        "[IMP:9][ps-unit] FAIL: unit does not declare Environment=PYTHONPATH=/opt/platform "
        "(plan 012 T1 / F-037) — post-reboot decrypt will fail on core.internal.* imports"
    )
    logger.info("[IMP:9][ps-unit] PASS: Environment=PYTHONPATH=/opt/platform present")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: ExecStart still references decrypt_secrets.py (W1.3 regression guard)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.static_audit
@ldd_trajectory
def test_execstart_references_decrypt_secrets(caplog: pytest.LogCaptureFixture) -> None:
    """ExecStart points at core/internal/secrets/decrypt_secrets.py.

    ## @purpose — PYTHONPATH полезен только если юнит запускает именно decrypt_secrets.py;
    ##            guard держит связку «PYTHONPATH ↔ целевой скрипт» атомарной (T1 AC).
    ## @io — ⇥ caplog → ⎋ None (asserts ExecStart target)
    ## @complexity — O(1)
    ## @scenario — AC5: юнит-контракт boot-chain (см. test_platform_secrets_static B2)
    """
    # 🧪 TRAP[TEST] · REGRESSION · T1 AC ExecStart↔PYTHONPATH pairing
    # · Scenario: unit declares PYTHONPATH AND executes decrypt_secrets.py
    # · Last fail: ExecStart drift (facade removal DevPlan 173 W1.3) ломал boot-chain
    # · Remove if: unit больше не запускает decrypt_secrets.py (другой механизм provision)
    logger.info("[IMP:7][ps-unit] Checking ExecStart in: %s", SERVICE_PATH)

    content = _read_service()
    execstart = next(
        (ln.strip() for ln in content.splitlines() if ln.strip().startswith("ExecStart=")),
        None,
    )

    assert execstart is not None, "[IMP:9][ps-unit] FAIL: ExecStart not found in unit"
    logger.info("[IMP:8][ps-unit] ExecStart = %s", execstart)

    assert execstart.endswith("/internal/secrets/decrypt_secrets.py"), (
        f"[IMP:9][ps-unit] FAIL: ExecStart '{execstart}' must point to "
        "/opt/platform/core/internal/secrets/decrypt_secrets.py (W1.3 contract)"
    )
    logger.info("[IMP:9][ps-unit] PASS: ExecStart targets decrypt_secrets.py")
