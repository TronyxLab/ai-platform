"""
# GREP_SUMMARY: test-phase-metrics-cron, install_cron_metrics, CRON_METRICS_LINE, cron.d, platform-metrics, flock, timeout, idempotent, atomic-write, tmp_path, ldd
# STRUCTURE: ┌tmp_path core_dir + monkeypatch CRON_METRICS_FILE → ◇ 5 scenarios ∋ (install+content / idempotency-mtime / mutation-rewrite / non-fatal-failure / contract-line) → ⎋ assert file content + mtime + LDD IMP:9
# region MODULE_CONTRACT
## @purpose  Unit tests for helpers/system.py::install_cron_metrics() — the φ3 metrics cron
##           installer (DevPlan 116 B3 T1, U-03). Verifies: file written with contract content
##           (flock + timeout 50 + absolute script path), content-idempotency (no-op on identical),
##           mutation → rewrite, and non-fatal failure semantics (False + WARN, never raises).
## @scope    Direct Python import of core.internal.bootstrap.lifecycle.helpers.system. Native
##           imports, tmp_path, monkeypatch — no root access, no Docker, no cron daemon.
## @invariants
##   - install target инжектится через cron_file= DI-параметр (167 D4) — never touches /etc/cron.d
##   - lock_dir инжектится (write_fn — для non-fatal контракта) — 0 setattr-патчей
##   - Each test includes caplog-based LDD trajectory via ldd_trajectory decorator
##   - Idempotency verified via mtime — second call must NOT rewrite the file
##   - Failure path: write_fn fake raising OSError → False (non-fatal), WARN logged
## @rationale  U-03 gate fix: install_cron_metrics must be unit-testable without root —
##             destination path инжектится cron_file= (DI-шов install_cron_metrics, 167 D4).
## @changes  2026-08-01 · Created (DevPlan 116 B3 T1)
# endregion MODULE_CONTRACT
"""

import logging
import sys
from pathlib import Path

import pytest

from tests._conftest.ldd import ldd_trajectory

# ── Import the module under test (repo root on sys.path via conftest) ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from core.internal.bootstrap.lifecycle.helpers import system as system_helpers

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def cron_file(tmp_path: Path) -> Path:
    """Redirect install target to tmp_path via cron_file= DI-параметр (167 D4) — never touches /etc/cron.d.

    ## @purpose — Isolate install_cron_metrics from the real system path (DI, 0 monkeypatch).
    ## @io — ⇥ tmp_path → ⎋ Path: tmp cron-файл (инжектится cron_file= в install_cron_metrics)
    ## @complexity — O(1)
    """
    target = tmp_path / "etc" / "cron.d" / "platform-metrics"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@pytest.fixture
def core_dir(tmp_path: Path) -> Path:
    """Create a tmp core directory (any existing dir works — cron line embeds its path).

    ## @purpose — Provide the core_dir argument for install_cron_metrics.
    ## @io — ⇥ tmp_path → ⎋ Path
    ## @complexity — O(1)
    """
    return tmp_path / "core"


# ═══════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · install_cron_metrics fresh install (DevPlan 116 B3 T1)
# · Scenario: tmp_path core_dir → file written with flock+timeout+script contract line
# · Last fail: U-03 — metrics cron installer did not exist anywhere in core
# · Remove if: install_cron_metrics contract changes
def test_install_writes_file_with_contract_content(
    cron_file: Path, core_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """install_cron_metrics writes /etc/cron.d/platform-metrics with flock+timeout+script."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_cron_metrics] Testing fresh install")

    # DI (167 D4): cron_file=/lock_dir= инжектятся — реальный вызов install_cron_metrics
    ok = system_helpers.install_cron_metrics(
        str(core_dir), cron_file=str(cron_file), lock_dir=str(tmp_path / "run" / "lock")
    )
    assert ok is True, "install_cron_metrics should return True on fresh install"

    assert cron_file.exists(), f"CRON_METRICS_FILE not created: {cron_file}"
    content = cron_file.read_text(encoding="utf-8")
    assert "flock -n" in content, f"Missing flock -n in cron line: {content!r}"
    assert "timeout 50" in content, f"Missing timeout 50 in cron line: {content!r}"
    assert "platform-export-metrics.sh" in content, f"Missing script path in cron line: {content!r}"
    assert str(core_dir) in content, f"core_dir not embedded in cron line: {content!r}"
    # Contract line format: * * * * * root <flock> <timeout> <script>
    assert content.startswith("* * * * * root "), f"Wrong cron schedule prefix: {content!r}"
    logger.info("[IMP:9][test_cron_metrics] Fresh install wrote contract cron line")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · idempotency (DevPlan 116 B3 T1)
# · Scenario: identical content on second call → no-op (mtime unchanged)
# · Last fail: N/A (new test)
# · Remove if: install_cron_metrics idempotency logic changes
def test_idempotent_second_call_does_not_rewrite(
    cron_file: Path, core_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Second call with identical content → no-op (mtime unchanged)."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_cron_metrics] Testing idempotency")

    assert (
        system_helpers.install_cron_metrics(
            str(core_dir), cron_file=str(cron_file), lock_dir=str(tmp_path / "run" / "lock")
        )
        is True
    )
    first_mtime = cron_file.stat().st_mtime_ns
    first_content = cron_file.read_text(encoding="utf-8")

    # Second call — must SKIP (no-op)
    assert (
        system_helpers.install_cron_metrics(
            str(core_dir), cron_file=str(cron_file), lock_dir=str(tmp_path / "run" / "lock")
        )
        is True
    )
    second_mtime = cron_file.stat().st_mtime_ns

    assert second_mtime == first_mtime, "Idempotent call must NOT rewrite the file (mtime changed)"
    assert cron_file.read_text(encoding="utf-8") == first_content, "Content must be unchanged on idempotent call"
    logger.info("[IMP:9][test_cron_metrics] Idempotent second call skipped rewrite (mtime unchanged)")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · mutation → rewrite (DevPlan 116 B3 T1)
# · Scenario: external edit of cron file → next call restores contract content
# · Last fail: N/A (new test)
# · Remove if: install_cron_metrics rewrite logic changes
def test_content_mutation_triggers_rewrite(
    cron_file: Path, core_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Content mutation (external edit) → next call rewrites the file."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_cron_metrics] Testing mutation → rewrite")

    assert (
        system_helpers.install_cron_metrics(
            str(core_dir), cron_file=str(cron_file), lock_dir=str(tmp_path / "run" / "lock")
        )
        is True
    )
    # Mutate the file externally (e.g., operator edited /etc/cron.d/platform-metrics)
    cron_file.write_text("* * * * * root /bin/echo MUTATED >/dev/null 2>&1\n", encoding="utf-8")

    assert (
        system_helpers.install_cron_metrics(
            str(core_dir), cron_file=str(cron_file), lock_dir=str(tmp_path / "run" / "lock")
        )
        is True
    )
    content = cron_file.read_text(encoding="utf-8")
    assert "flock -n" in content, "Mutation must be overwritten with contract content"
    assert "platform-export-metrics.sh" in content, "Mutation must be overwritten with contract content"
    assert "MUTATED" not in content, "External mutation must be replaced"
    logger.info("[IMP:9][test_cron_metrics] Mutation detected and cron line restored to contract")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · non-fatal write failure (DevPlan 116 B3 T1)
# · Scenario: os.replace raises PermissionError (simulated non-root) → False + WARN, no raise
# · Last fail: N/A (new test — φ3 non-fatality contract)
# · Remove if: install_cron_metrics failure semantics change
def test_write_failure_is_non_fatal(
    cron_file: Path, core_dir: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """write failure (e.g., non-root on read-only /etc) → False + WARN, never raises.

    Phase contract: install_cron_metrics is non-fatal — φ3 continues with WARN
    (write_fn DI-фейк симулирует permission-denied на реальной ноде, 167 D4).
    """
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_cron_metrics] Testing non-fatal write failure")

    def _fail_write(_path: str, _content: str, _mode: int) -> None:
        raise PermissionError(13, "Permission denied (simulated non-root)")

    # Must NOT raise — returns False with WARN log
    ok = system_helpers.install_cron_metrics(
        str(core_dir),
        cron_file=str(cron_file),
        lock_dir=str(tmp_path / "run" / "lock"),
        write_fn=_fail_write,
    )
    assert ok is False, "install_cron_metrics must return False on write failure (non-fatal)"

    warn_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING and "cron" in r.message.lower()]
    assert warn_msgs, "Expected a WARN log about cron install failure"
    logger.info("[IMP:9][test_cron_metrics] Write failure handled non-fatally (False + WARN)")


@ldd_trajectory

# 🧪 TRAP[TEST] · 2026-08-01 · REGRESSION · CRON_METRICS_LINE contract (DevPlan 116 B3 T1)
# · Scenario: module constant satisfies flock+timeout+script+core_dir template
# · Last fail: N/A (new test)
# · Remove if: CRON_METRICS_LINE contract changes
def test_cron_metrics_line_contract_constants(caplog: pytest.LogCaptureFixture) -> None:
    """CRON_METRICS_LINE module constant satisfies the contract at unit level too."""
    caplog.set_level(logging.DEBUG)
    logger.info("[IMP:7][test_cron_metrics] Testing CRON_METRICS_LINE constant")

    line = system_helpers.CRON_METRICS_LINE
    assert "flock -n" in line
    assert "timeout 50" in line
    assert "platform-export-metrics.sh" in line
    assert "{core_dir}" in line  # ruff: ignore[RUF027] — literal shell placeholder в cron-шаблоне (подстановка через .format при установке)
    assert system_helpers.CRON_METRICS_FILE.endswith("platform-metrics")
    logger.info("[IMP:9][test_cron_metrics] CRON_METRICS_LINE contract verified")
