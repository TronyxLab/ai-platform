"""
# GREP_SUMMARY: test normalize_new_project_args positional named bridge org node defaults scaffold
# STRUCTURE: ▶ positional→named 1× → ▶ flags passthrough 1× → ▶ env defaults injection 2× → ▶ main() capsys 1× → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  Unit tests for core/internal/scaffold/normalize_new_project_args.py (DevPlan 117 H D61).
##           Direct Python imports — no subprocess calls.
## @scope    Tests positional→named mapping, flag passthrough, --org/--node env-default
##           injection (only when absent + non-empty), and main() CLI output contract.
## @invariants
##   - Position 0 → --name, position 1 → --template, extra positionals pass through
##   - Flags (--*) pass through unchanged
##   - --org/--node injected only when absent from args AND default non-empty
##   - main() always exits 0 and prints space-joined args to stdout
##   - @ldd_trajectory asserts IMP:9 log presence (Anti-Illusion rule)
## @rationale DevPlan 09 §D61: unit coverage for the positional→named bridge extracted
##            from scaffold.sh into a pure Python function.
## @changes 2026-08-02 | Created (Brief H D61)
# endregion MODULE_CONTRACT
"""

from core.internal.scaffold.normalize_new_project_args import normalize_new_project_args
from tests._conftest.ldd import ldd_trajectory

# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_positional_args_mapped_to_name_and_template(caplog):
    """Position 0 → --name, position 1 → --template."""
    result = normalize_new_project_args(["foo", "bar"])
    assert result == ["--name", "foo", "--template", "bar"]


@ldd_trajectory
def test_extra_positionals_pass_through(caplog):
    """Positional args beyond the first two pass through unchanged (shell parity)."""
    result = normalize_new_project_args(["foo", "bar", "extra1", "extra2"])
    assert result == ["--name", "foo", "--template", "bar", "extra1", "extra2"]


@ldd_trajectory
def test_flags_and_positionals_shell_parity(caplog):
    """Named flags + positionals reproduce the shell bridge 1:1 (mapping quirk preserved).

    The original scaffold.sh bridge mapped positionals regardless of already-present
    named flags — this quirk is preserved for zero behavior change (AC5).
    """
    result = normalize_new_project_args(["--name", "x", "--template", "y"])
    assert result == ["--name", "--name", "x", "--template", "--template", "y"]


@ldd_trajectory
def test_flag_value_passthrough_with_positionals(caplog):
    """--org flag with positional name/template — flag kept, value mapped as positional."""
    result = normalize_new_project_args(["foo", "bar", "--org", "custom"])
    assert result == ["--name", "foo", "--template", "bar", "--org", "custom"]


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_org_and_node_injected_from_env_defaults(caplog):
    """Non-empty PLATFORM_ORG/PLATFORM_DEFAULT_NODE defaults are appended."""
    result = normalize_new_project_args(["foo", "bar"], org_default="myorg", node_default="myvps")
    assert result == ["--name", "foo", "--template", "bar", "--org", "myorg", "--node", "myvps"]


@ldd_trajectory
def test_empty_env_defaults_not_injected(caplog):
    """Empty defaults are skipped — no bare --org/--node flags emitted."""
    result = normalize_new_project_args(["foo", "bar"], org_default="", node_default="")
    assert result == ["--name", "foo", "--template", "bar"]


@ldd_trajectory
def test_explicit_org_flag_blocks_env_injection(caplog):
    """If --org is already present, PLATFORM_ORG default must NOT be injected."""
    result = normalize_new_project_args(["foo", "bar", "--org", "custom"], org_default="myorg")
    assert result == ["--name", "foo", "--template", "bar", "--org", "custom"]


@ldd_trajectory
def test_explicit_node_flag_blocks_env_injection(caplog):
    """If --node is already present, PLATFORM_DEFAULT_NODE default must NOT be injected."""
    result = normalize_new_project_args(["foo", "bar", "--node", "custom"], node_default="myvps")
    assert result == ["--name", "foo", "--template", "bar", "--node", "custom"]


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_main_prints_normalized_args_and_exits_zero(caplog, monkeypatch, capsys):
    """main() reads sys.argv + env, prints space-joined normalized args, exits 0."""
    monkeypatch.setattr("sys.argv", ["normalize_new_project_args", "foo", "bar"])
    monkeypatch.setenv("PLATFORM_ORG", "myorg")
    monkeypatch.setenv("PLATFORM_DEFAULT_NODE", "myvps")

    from core.internal.scaffold.normalize_new_project_args import main

    assert main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "--name foo --template bar --org myorg --node myvps"
