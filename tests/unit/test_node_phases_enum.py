"""
# GREP_SUMMARY: test node-phases-enum BootstrapPhase INIT_PHASES UPDATE_PHASES state-json keys enum-parity B10-T6
# STRUCTURE: ▶ import BootstrapPhase + _conftest.node → ◇ INIT_PHASES == enum INIT_PHASE_ORDER → ◇ UPDATE_PHASES == enum UPDATE_PHASE_ORDER →
#            ◇ all enum values are plain str keys (state.json contract) → ◇ 9/5 counts → ⎋ IMP:9
# region MODULE_CONTRACT
## @purpose  Unit test enforcing enum↔state.json key parity for bootstrap phases (DevPlan 116 B10 T6, U-75):
##           tests/_conftest/node.py INIT_PHASES/UPDATE_PHASES must be derived from the canonical
##           BootstrapPhase enum (core/internal/bootstrap/lifecycle/state_machine.py), whose values
##           ARE the state.json keys. No literal phase strings may drift from the enum.
## @scope    Tests the _conftest/node.py phase lists against the BootstrapPhase enum. Native, no SSH.
## @invariants
##   - INIT_PHASES == list(BootstrapPhase.INIT_PHASE_ORDER) (9 keys)
##   - UPDATE_PHASES == list(BootstrapPhase.UPDATE_PHASE_ORDER) (5 keys)
##   - Every enum value is a str (state.json key) — no accidental int/object values
## @rationale  U-75: node.py had literal list[str] phase copies — a divergence risk from the enum
##             (the canonical state.json key source). Enum derivation makes the parity structural.
## @changes  2026-08-01 · Created (DevPlan 116 B10 T6)
# endregion MODULE_CONTRACT
"""

import pytest

from core.internal.bootstrap.lifecycle.state_machine import BootstrapPhase
from tests._conftest.node import INIT_PHASES, UPDATE_PHASES

logger = pytest.importorskip("logging").getLogger(__name__)


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T6 · INIT_PHASES parity with enum
# · Regression: U-75 — node.py literal phase list could drift from BootstrapPhase
# · Last fail: N/A (new enum-parity test)
# · Remove if: phase list derivation changes
def test_init_phases_match_enum() -> None:
    """INIT_PHASES must equal BootstrapPhase.INIT_PHASE_ORDER (9 init keys)."""
    expected = list(BootstrapPhase.INIT_PHASE_ORDER)
    assert expected == INIT_PHASES, (
        f"INIT_PHASES drifted from BootstrapPhase.INIT_PHASE_ORDER:\n  node.py: {INIT_PHASES}\n  enum:    {expected}"
    )
    assert len(INIT_PHASES) == 9, f"Expected 9 init phases, got {len(INIT_PHASES)}"
    logger.critical("[IMP:9][test] INIT_PHASES == BootstrapPhase.INIT_PHASE_ORDER (9 keys) — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T6 · UPDATE_PHASES parity with enum
# · Regression: U-75 — node.py literal update list could drift
# · Last fail: N/A (new enum-parity test)
# · Remove if: phase list derivation changes
def test_update_phases_match_enum() -> None:
    """UPDATE_PHASES must equal BootstrapPhase.UPDATE_PHASE_ORDER (5 update keys)."""
    expected = list(BootstrapPhase.UPDATE_PHASE_ORDER)
    assert expected == UPDATE_PHASES, (
        f"UPDATE_PHASES drifted from BootstrapPhase.UPDATE_PHASE_ORDER:\n  node.py: {UPDATE_PHASES}\n  enum:    {expected}"
    )
    assert len(UPDATE_PHASES) == 5, f"Expected 5 update phases, got {len(UPDATE_PHASES)}"
    logger.critical("[IMP:9][test] UPDATE_PHASES == BootstrapPhase.UPDATE_PHASE_ORDER (5 keys) — OK")


# 🧪 TRAP[TEST] · 2026-08-01 · B10 T6 · enum values are state.json str keys
# · Regression: U-75 — enum values must be the state.json keys (str), not ints/objects
# · Last fail: N/A (new contract test)
# · Remove if: enum value type changes
def test_enum_values_are_state_json_keys() -> None:
    """Every BootstrapPhase value is a str — the canonical state.json key format."""
    for phase in sorted(BootstrapPhase.ALL_PHASES):
        assert isinstance(phase, str), f"BootstrapPhase value must be a str (state.json key), got {phase!r}"
    assert len(BootstrapPhase.ALL_PHASES) == 14, (
        f"BootstrapPhase must have 14 values (9 init + 5 update), got {len(BootstrapPhase.ALL_PHASES)}"
    )
    logger.critical("[IMP:9][test] BootstrapPhase: 14 str values, state.json keys — OK")
