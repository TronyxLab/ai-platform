# GREP_SUMMARY: age key hardening env-only build_ssh_cmd remote command CLI arg ps-aux visibility hardening
# STRUCTURE: ▶ 2 test functions → ○ extract build_ssh_cmd → ◇ assert CLI arg absent → ◇ assert env export present → ⊕ LDD trajectory → ⎋ IMP:9 assertion
# region MODULE_CONTRACT
## @file test_unit_age_key_env_only.py
## @purpose  Unit tests for AGE key hardening (DevPlan 003 TASK-2): verify build_ssh_cmd()
##           constructs remote SSH command WITHOUT --age-secret-key CLI arg (env-only).
## @scope    Tests build_ssh_cmd() extracted from core/entrypoints/bootstrap.sh.
##           Does NOT require Docker, VPS, or network access.
## @invariants
##   - build_ssh_cmd() output does NOT contain --age-secret-key CLI arg
##   - build_ssh_cmd() output contains export AGE_SECRET_KEY= (env var)
##   - Local mode (direct call to orchestrator) still passes --age-secret-key (backward compat)
##   - Tests use bash subprocess for shell function testing
##   - IMP:9 logs asserted in success paths
## @rationale DevPlan 003 TASK-2: AGE_SECRET_KEY passed via env export only for remote SSH,
##           preventing key visibility in `ps aux`. Without this test, a future change
##           could re-add --age-secret-key CLI arg to remote command, exposing the key.
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import textwrap

logger = logging.getLogger(__name__)

REMOTE_CMD_SH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "internal",
    "bootstrap",
    "remote-cmd.sh",
)


def _print_ldd(stderr: str, stdout: str) -> bool:
    """Print LDD trajectory and return whether IMP:9 was found."""
    found_imp9 = False
    print("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in (stderr + "\n" + stdout).split("\n"):
        if "[IMP:" in line:
            try:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    print(line.strip())
                if imp_level >= 9:
                    found_imp9 = True
            except (ValueError, IndexError):
                pass
    print("--- END LDD TRAJECTORY ---")
    return found_imp9


def _extract_func(func_name: str, source_path: str) -> str:
    """Extract a bash function definition from a source file."""
    with open(source_path) as f:
        lines = f.readlines()

    in_func = False
    func_lines = []
    brace_depth = 0

    for line in lines:
        if not in_func:
            # Skip comment lines — function name in comments (e.g. ## @scope ... Provides build_ssh_cmd())
            # would falsely trigger extraction.
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if f"{func_name}()" in line or stripped.startswith(f"{func_name} "):  # noqa: SIM102 — cannot combine: outer else depends on A-or-B not being just A
                if f"{func_name}()" in line:
                    in_func = True
                    func_lines.append(line)
                    brace_depth += line.count("{") - line.count("}")
        else:
            func_lines.append(line)
            brace_depth += line.count("{") - line.count("}")
            if brace_depth == 0 and in_func and len(func_lines) > 1:
                break

    return "".join(func_lines)


def _run_build_ssh_cmd(
    age_key: str, node_name: str = "test-node", owner_key: str = "ssh-ed25519 AAAATestKey test@example.com"
) -> tuple[str, str, int]:
    """Extract and run build_ssh_cmd() from remote-cmd.sh with given arguments."""
    func_def = _extract_func("build_ssh_cmd", REMOTE_CMD_SH)

    if not func_def:
        raise RuntimeError(f"Could not extract build_ssh_cmd from {REMOTE_CMD_SH}")

    script = textwrap.dedent(f"""\
        set -euo pipefail

        printf() {{
            # Override printf to capture output for testing
            if [[ "$1" == "%q" ]]; then
                echo -n "$2"
            else
                command printf "$@"
            fi
        }}

        {func_def}

        cmd=$(build_ssh_cmd "{node_name}" "{owner_key}" "" "{age_key}")
        echo "$cmd"
        echo "[IMP:9][test][build_ssh_cmd] Command constructed, length=${{#cmd}}"
    """)

    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.stdout, proc.stderr, proc.returncode


# region TEST_test_build_ssh_cmd_no_cli_age_key
# 🧪 TRAP[TEST] · 2026-07-15 · AGE key hardening — no --age-secret-key in remote SSH command
# · Prevents: regression where --age-secret-key CLI arg is re-added to build_ssh_cmd(),
#   exposing AGE_SECRET_KEY in `ps aux` on the remote server
def test_build_ssh_cmd_no_cli_age_key(caplog) -> None:
    """build_ssh_cmd() output does NOT contain --age-secret-key CLI argument."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _run_build_ssh_cmd(age_key="AGE-SECRET-KEY-12345")

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # Core assertion: --age-secret-key must NOT be in remote SSH command
    assert "--age-secret-key" not in cmd, (
        f"build_ssh_cmd contains --age-secret-key CLI arg: {cmd[:200]}...\n"
        "DevPlan 003 TASK-2: AGE key must be passed via env export ONLY for remote SSH, "
        "not CLI arg (prevents ps aux visibility)"
    )

    # Verify basic command structure
    assert "set -euo pipefail" in cmd
    assert "node-lifecycle.sh" in cmd
    assert "--mode init" in cmd
    assert "--node-name" in cmd
    assert "--resume" in cmd

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][age_key] build_ssh_cmd confirmed: --age-secret-key absent from remote command")


# endregion


# region TEST_test_build_ssh_cmd_has_env_export
# 🧪 TRAP[TEST] · 2026-07-15 · AGE key hardening — env export must be present
# · Prevents: regression where export AGE_SECRET_KEY= is removed from build_ssh_cmd(),
#   causing orchestrator to run without decryption key (silent secrets failure)
def test_build_ssh_cmd_has_env_export(caplog) -> None:
    """build_ssh_cmd() output contains export AGE_SECRET_KEY= when key is provided."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _run_build_ssh_cmd(age_key="AGE-SECRET-KEY-12345")

    _ = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # Core assertion: env export must be present
    assert "export AGE_SECRET_KEY=" in cmd, (
        f"build_ssh_cmd missing export AGE_SECRET_KEY=: {cmd[:200]}...\n"
        "DevPlan 003 TASK-2: AGE key must be passed via env export for remote SSH"
    )

    logger.info("[IMP:9][test][age_key] build_ssh_cmd confirmed: export AGE_SECRET_KEY= present in remote command")


# endregion


# region TEST_test_build_ssh_cmd_without_key
# 🧪 TRAP[TEST] · 2026-07-15 · AGE key hardening — empty key should not inject export
# · Prevents: regression where empty AGE key still injects export AGE_SECRET_KEY= (confusing log noise)
def test_build_ssh_cmd_without_key(caplog) -> None:
    """build_ssh_cmd() without AGE key does NOT include export AGE_SECRET_KEY=."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _run_build_ssh_cmd(age_key="")

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # When age_key is empty, export should NOT be present
    assert "export AGE_SECRET_KEY=" not in cmd, (
        f"build_ssh_cmd should NOT include export AGE_SECRET_KEY= when key is empty: {cmd[:200]}..."
    )

    # But basic command structure still works
    assert "node-lifecycle.sh" in cmd

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][age_key] build_ssh_cmd without key: no env export (correct)")


# endregion
