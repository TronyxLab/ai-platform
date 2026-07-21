# GREP_SUMMARY: test, gate, litellm, pg-enforcement, negative, R5, sqlite-detection
# STRUCTURE: ▶ test_litellm_sqlite_config_detected → ◇ tmp_path construct bad config → ⊕ call _check_sqlite_in_config_file → ⎋ assert violation reported
# region MODULE_CONTRACT
## @purpose  R5 anti-survivorship: companion to test_gate_litellm_pg_enforcement.
##           Если positive-тест детектит PG-enforcement, negative-тест детектит SQLite config.
## @scope    Конструирует LiteLLM config с sqlite:// URL, вызывает gate function,
##           ожидает detection (violation reported).
## @invariants
##   - Test Honesty R1: реально падает на конструируемом нарушении (не assert True)
##   - Test Honesty R5: companion to test_gate_litellm_pg_enforcement
## @changes
##   LAST_CHANGE: 2026-07-21 | Created (DevPlan 028 W1-E3)
# endregion MODULE_CONTRACT

import pathlib

import pytest

from tests.gates.test_gate_litellm_pg_enforcement import _check_sqlite_in_config_file


@pytest.mark.gate

# 🧪 TRAP[TEST] · 2026-07-21 · REGRESSION · R5 companion — MUST detect SQLite config in LiteLLM
# · Last fail: N/A (preventive)
# · Remove if: LiteLLM module is removed or PostgreSQL enforcement gate is superseded
def test_litellm_sqlite_config_detected(tmp_path: pathlib.Path) -> None:
    """R5: gate must DETECT SQLite config (LiteLLM invariant violation).

    Constructs a LiteLLM config with database_url=sqlite:///...,
    calls _check_sqlite_in_config_file, expects violation reported.
    """
    # Construct violating config
    litellm_config = tmp_path / "litellm_config.yaml"
    litellm_config.write_text(
        "model_list: []\ngeneral_settings:\n  database_url: 'sqlite:///./test.db'  # VIOLATION: must be PostgreSQL\n"
    )

    violations = _check_sqlite_in_config_file(config_path=litellm_config)

    assert violations, f"[IMP:9][gate][negative] gate FAILED to detect SQLite config — violations={violations!r}"
    assert any("sqlite" in v.lower() for v in violations), (
        f"[IMP:9][gate][negative] violations do not mention sqlite: {violations!r}"
    )
