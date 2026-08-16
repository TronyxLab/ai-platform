#!/usr/bin/env python3
# GREP_SUMMARY: gate dead-code deprecated-markers ssl-provision-references memorial check-dead-code.sh historical
# STRUCTURE: ▶ test_no_deprecated_markers_stale (check-dead-code.sh DEPRECATED-маркеры ≤30 дней) → ▶ test_no_ssl_provision_references (0 refs ssl-provision.sh) → ⎋ PASS
# region MODULE_CONTRACT
## @purpose  Gate-остаток test_gate_dead_code.py ПОСЛЕ миграции dead-code reachability
##           в core/internal/static/dead_code.py (DevPlan 163 W-C P4, parity
##           files/static_parity_p4.md): dead-code call-graph семантика перенесена
##           в детектор (R5-пары test_static_dead_code.py); в файле остаются ДВА
##           не-migrated класса: (1) DEPRECATED-маркеры ≤30 дней через check-dead-code.sh
##           (в check-suite: make check-dead-code), (2) мемориал 0 ссылок ssl-provision.sh.
## @scope    Только оставшиеся memorial/маркерные проверки. Call-graph/достижимость —
##           core/internal/static/dead_code.py (rule dead-code).
## @invariants
##   - DEPRECATED-маркеры в коде ≤30 дней (check-dead-code.sh, exit 0)
##   - 0 ссылок ssl-provision.sh в проекте (файл удалён исторически)
## @rationale Прямое замещение (M1, DevPlan 163 §4.3): мигрированная часть удалена
##            в том же изменении; не-migrated проверки остаются pytest-гейтами.
## @changes 2026-08-13 | DevPlan 163 W-C P4 — удалены test_all_internal_scripts_reachable
##           + test_all_entrypoints_have_live_caller (migrated → dead_code.py) + helpers
# endregion MODULE_CONTRACT

import logging
import pathlib
import subprocess

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

PLATFORM_ROOT: str = str(pathlib.Path(__file__).resolve().parent.parent.parent)


# region FUNC_test_no_deprecated_markers_stale
## @purpose  Verify all DEPRECATED markers in project code are ≤30 days old.
##           Delegates to check-dead-code.sh CI gate. This test validates
##           that the gate script works correctly — it returns exit 0 on
##           a clean state (all DEPRECATED markers within grace period).
## @rationale  Preventing stale DEPRECATED marker accumulation (AC5).

# 🧪 TRAP[TEST] · REGRESSION(084) · SCENARIO(stale-deprecated-markers) · LAST_FAIL(N/A) · REMOVE_IF(check-dead-code.sh removed)


@pytest.mark.gate
@ldd_trajectory
def test_no_deprecated_markers_stale(caplog) -> None:
    """Verify all DEPRECATED markers in project code are ≤30 days old (via check-dead-code.sh).

    # ▶ run check-dead-code.sh → ◇ exit 0? → PASS
    #                              └→ FAIL: stale DEPRECATED markers detected
    """
    check_script = str(pathlib.Path(PLATFORM_ROOT) / "core" / "entrypoints" / "check-dead-code.sh")

    if not pathlib.Path(check_script).is_file():
        pytest.fail(f"check-dead-code.sh not found at {check_script}")

    logger.info("[IMP:8][test_no_deprecated_markers_stale] Running check-dead-code.sh...")
    result = subprocess.run(
        ["bash", check_script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    logger.info("[IMP:9][test_no_deprecated_markers_stale] Exit code: %d", result.returncode)
    for line in result.stdout.splitlines():
        logger.info("[IMP:7][check-dead-code] %s", line)
    for line in result.stderr.splitlines():
        logger.info("[IMP:7][check-dead-code] %s", line)

    assert result.returncode == 0, (
        f"[IMP:10][test_no_deprecated_markers_stale] FAIL: "
        f"check-dead-code.sh exited with code {result.returncode} — "
        f"stale DEPRECATED markers found:\n{result.stdout}"
    )
    logger.info("[IMP:9][test_no_deprecated_markers_stale] PASS: No stale DEPRECATED markers")


# endregion FUNC_test_no_deprecated_markers_stale


# region FUNC_test_no_ssl_provision_references
## @purpose  Verify no project code references ssl-provision.sh by path (AC3).
## @rationale  After file deletion, all path references must be cleaned up.

# 🧪 TRAP[TEST] · REGRESSION(084) · SCENARIO(no-ssl-provision-refs) · LAST_FAIL(N/A) · REMOVE_IF(no ssl-provision.sh refs)


@pytest.mark.gate
@ldd_trajectory
def test_no_ssl_provision_references(caplog) -> None:
    """Verify no code references ssl-provision.sh by path (AC3)."""
    logger.info("[IMP:8][test_no_ssl_provision_references] Grepping for ssl-provision.sh references...")

    result = subprocess.run(
        [
            "grep",
            "-r",
            "ssl-provision\\.sh",
            "--include=*.sh",
            "--include=*.py",
            "--include=*.yaml",
            ".",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=PLATFORM_ROOT,
        check=False,  # grep rc=1 (no match) is a valid pass — filtered below
    )

    # Filter out .ai/, .venv/, .git/, tests/gates/ (document cleanup) results
    filtered_lines: list[str] = []
    for line in result.stdout.splitlines():
        if ".ai/" in line or ".venv/" in line or ".git/" in line:
            continue
        # Gate files document the cleanup — not stale references
        if "tests/gates/" in line:
            continue
        filtered_lines.append(line)

    if filtered_lines:
        logger.error(
            "[IMP:10][test_no_ssl_provision_references] FAIL: %d reference(s) to ssl-provision.sh found",
            len(filtered_lines),
        )
        for ref in filtered_lines:
            print(f"  REF: {ref}")
        pytest.fail(
            f"{len(filtered_lines)} reference(s) to ssl-provision.sh remain in project code:\n"
            + "\n".join(filtered_lines)
        )
    else:
        msg = "PASS: No ssl-provision.sh references in project code"
        if result.stdout.strip():
            msg += f" (only .ai/ docs references: {result.stdout.count(chr(10))} line(s))"
        logger.info("[IMP:9][test_no_ssl_provision_references] %s", msg)


# endregion FUNC_test_no_ssl_provision_references
