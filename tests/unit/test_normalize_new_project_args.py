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

import pytest

from core.internal.scaffold.normalize_new_project_args import normalize_new_project_args
from tests._conftest.ldd import ldd_trajectory

pytestmark = pytest.mark.static_audit

# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
@pytest.mark.parametrize(
    ("args", "kwargs", "expected"),
    [
        # Position 0 → --name, position 1 → --template
        (["foo", "bar"], {}, ["--name", "foo", "--template", "bar"]),
        # Extra positionals pass through unchanged (shell parity)
        (
            ["foo", "bar", "extra1", "extra2"],
            {},
            ["--name", "foo", "--template", "bar", "extra1", "extra2"],
        ),
        # Shell-bridge quirk preserved (AC5): pre-existing named flags are NOT deduplicated
        (["--name", "x", "--template", "y"], {}, ["--name", "--name", "x", "--template", "--template", "y"]),
        # --org flag with positionals — flag kept, value mapped as positional
        (["foo", "bar", "--org", "custom"], {}, ["--name", "foo", "--template", "bar", "--org", "custom"]),
        # Non-empty env defaults are appended
        (
            ["foo", "bar"],
            {"org_default": "myorg", "node_default": "myvps"},
            ["--name", "foo", "--template", "bar", "--org", "myorg", "--node", "myvps"],
        ),
        # Empty defaults skipped — no bare --org/--node flags emitted
        (["foo", "bar"], {"org_default": "", "node_default": ""}, ["--name", "foo", "--template", "bar"]),
        # Explicit --org flag blocks PLATFORM_ORG default injection
        (
            ["foo", "bar", "--org", "custom"],
            {"org_default": "myorg"},
            ["--name", "foo", "--template", "bar", "--org", "custom"],
        ),
        # Explicit --node flag blocks PLATFORM_DEFAULT_NODE default injection
        (
            ["foo", "bar", "--node", "custom"],
            {"node_default": "myvps"},
            ["--name", "foo", "--template", "bar", "--node", "custom"],
        ),
    ],
)
def test_normalize_new_project_args_variants(caplog, args, kwargs, expected):
    """Parametrized: positional→named bridge, flag passthrough, env-default injection (F5-reduction)."""
    result = normalize_new_project_args(args, **kwargs)
    assert result == expected


# ═══════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════


@ldd_trajectory
def test_main_prints_normalized_args_and_exits_zero(caplog, monkeypatch, capsys):
    """main(argv) reads argv + env, prints space-joined normalized args, exits 0 (AF-4, 167 D1)."""
    monkeypatch.setenv("PLATFORM_ORG", "myorg")
    monkeypatch.setenv("PLATFORM_DEFAULT_NODE", "myvps")

    from core.internal.scaffold.normalize_new_project_args import main

    assert main(["normalize_new_project_args", "foo", "bar"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "--name foo --template bar --org myorg --node myvps"
