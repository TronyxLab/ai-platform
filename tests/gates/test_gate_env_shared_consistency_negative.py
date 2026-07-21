# GREP_SUMMARY: test, gate, env-shared, consistency, negative, R5, divergence
# STRUCTURE: ▶ test_env_shared_divergence_detected → ◇ tmp_path two module.yaml → ⊕ _check_env_shared_consistency → ⎋ assert divergence
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship: companion to test_gate_env_shared_consistency.
##           Детектит рассинхрон env_shared между модулями.
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E3)
# endregion MODULE_CONTRACT

import pathlib

import pytest

from tests.gates.test_gate_env_shared_consistency import _check_env_shared_consistency


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · R5 companion — MUST detect divergent env_shared declarations
# · Last fail: N/A (preventive)
# · Remove if: env_shared contract is removed from module.yaml
def test_env_shared_divergence_detected(tmp_path: pathlib.Path) -> None:
    """R5: consistency checker must DETECT divergent env_shared declarations."""
    # Module A declares SHARED_VAR with one value
    module_a = tmp_path / "moduleA" / "module.yaml"
    module_a.parent.mkdir(parents=True)
    module_a.write_text(
        "name: moduleA\ninstall_type: docker\ndescription: Test module A\nenv_shared:\n  SHARED_VAR: 'value-from-A'\n"
    )
    # Module B declares SHARED_VAR with different value (VIOLATION)
    module_b = tmp_path / "moduleB" / "module.yaml"
    module_b.parent.mkdir(parents=True)
    module_b.write_text(
        "name: moduleB\n"
        "install_type: docker\n"
        "description: Test module B\n"
        "env_shared:\n"
        "  SHARED_VAR: 'value-from-B'  # DIVERGENT\n"
    )

    divergences = _check_env_shared_consistency([module_a, module_b])

    assert divergences, "[IMP:9][gate][negative] checker FAILED to detect SHARED_VAR divergence"
    assert any("SHARED_VAR" in d for d in divergences), (
        f"[IMP:9][gate][negative] divergences do not mention SHARED_VAR: {divergences!r}"
    )
