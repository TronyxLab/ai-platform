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
import pathlib
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

# DevPlan 101 D1: build_*_ssh_cmd извлечены из remote-cmd.sh в build-ssh-cmd.sh
BUILD_SSH_CMD_SH = (
    Path(__file__).resolve().parent.parent.parent / "core" / "internal" / "bootstrap" / "build-ssh-cmd.sh"
)


def _print_ldd(stderr: str, stdout: str) -> bool:
    """Print LDD trajectory and return whether IMP:9 was found."""
    found_imp9 = False
    logger.info("--- LDD TRAJECTORY (IMP:7-10) ---")
    for line in (stderr + "\n" + stdout).split("\n"):
        if "[IMP:" in line:
            try:
                imp_level = int(line.split("[IMP:")[1].split("]")[0])
                if imp_level >= 7:
                    logger.info("%s", line.strip())
                if imp_level >= 9:
                    found_imp9 = True
            except (ValueError, IndexError):
                logger.debug("[IMP:7][age-key] MALFORMED IMP tag: %s", line.strip())
    logger.info("--- END LDD TRAJECTORY ---")
    return found_imp9


def _extract_func(func_name: str, source_path: str) -> str:
    """Extract a bash function definition from a source file."""
    with pathlib.Path(source_path).open(encoding="utf-8") as f:
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
            if f"{func_name}()" in line or stripped.startswith(f"{func_name} "):  # ruff: ignore[SIM102] — cannot combine: outer else depends on A-or-B not being just A
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
    """Extract and run build_ssh_cmd() from build-ssh-cmd.sh with given arguments."""
    func_def = _extract_func("build_ssh_cmd", BUILD_SSH_CMD_SH)

    if not func_def:
        msg = f"Could not extract build_ssh_cmd from {BUILD_SSH_CMD_SH}"
        raise RuntimeError(msg)

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

    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False)
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


# endregion TEST_test_build_ssh_cmd_no_cli_age_key


# region TEST_test_build_ssh_cmd_has_env_export
# 🧪 TRAP[TEST] · 2026-07-15 · AGE key hardening — export присутствует В PRELUDE (REF-0007)
# · Prevents: regression where the AGE key is dropped from transport entirely (silent secrets
#   failure) OR re-embedded into remote command argv (ps aux visibility)
# · REF-0007 (2026-08-24): канал перенесён из тела команды в build_init_secret_prelude
#   (ssh-stdin `bash -s`); тело НЕ содержит export AGE_SECRET_KEY=
def test_build_ssh_cmd_has_env_export(caplog) -> None:
    """AGE key доставляется stdin-prelude'ом; тело build_ssh_cmd БЕЗ ключа."""
    caplog.set_level(logging.DEBUG)

    stdout, stderr, rc = _run_build_ssh_cmd(age_key="AGE-SECRET-KEY-12345")

    found_imp9 = _print_ldd(stderr, stdout)
    assert rc == 0, f"build_ssh_cmd failed: rc={rc}, stderr={stderr}"

    cmd = stdout.split("\n")[0]

    # Core assertion (REF-0007): значение ключа НЕ в теле команды
    assert "export AGE_SECRET_KEY=" not in cmd and "AGE-SECRET-KEY-12345" not in cmd, (
        f"build_ssh_cmd embeds AGE key in remote command: {cmd[:200]}...\n"
        "REF-0007: ключ доставляется ТОЛЬКО через build_init_secret_prelude (ssh-stdin)"
    )

    # Ключ присутствует в secret-prelude (export-строка для ssh-stdin)
    script = textwrap.dedent(f"""\
        set -euo pipefail
        source "{BUILD_SSH_CMD_SH}"
        prelude=$(build_init_secret_prelude "test-node" "owner" "" "AGE-SECRET-KEY-12345" "")
        echo "$prelude"
        echo "[IMP:9][test][prelude] Prelude constructed"
    """)
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30, check=False)
    assert proc.returncode == 0, f"build_init_secret_prelude failed: {proc.stderr}"
    # LDD echo-маркер тест-сниппета не входит в prelude
    prelude_lines = [line for line in proc.stdout.splitlines() if "[IMP:" not in line]
    assert prelude_lines == ["export AGE_SECRET_KEY=AGE-SECRET-KEY-12345"], f"unexpected prelude: {prelude_lines!r}"

    assert found_imp9, "Critical LDD Error: No IMP:9 business logic log found"
    logger.info("[IMP:9][test][age_key] AGE key confirmed in stdin-prelude; remote command is key-free")


# endregion TEST_test_build_ssh_cmd_has_env_export


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


# endregion TEST_test_build_ssh_cmd_without_key
