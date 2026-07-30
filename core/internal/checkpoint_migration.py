#!/usr/bin/env python3
# GREP_SUMMARY: checkpoint_migration.py, state.json, name-based-keys, SHELL_TO_PYTHON_STEP, legacy-migration, is-done, mark-done, force, reset, migrate-legacy
# STRUCTURE: ▶ ┌CLI dispatch (is-done|mark-done|force|reset|migrate-legacy)┐ → ◇ SHELL_TO_PYTHON_STEP mapping → ⊕ state.json read/write → ∑ atomic save → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Unified checkpoint management module — single source of truth for
##           bootstrap step tracking. Replaces the dual shell .done + Python state.json
##           system with name-based state.json keys (Rev 2, DevPlan 071).
##           Shell delegates all JSON operations to this module via CLI dispatch,
##           eliminating step-name/key misalignment (F1 fix).
## @scope    — SHELL_TO_PYTHON_STEP mapping table (hyphen→underscore name translation)
##           — CLI dispatch: is-done, mark-done, force, reset, migrate-legacy
##           — Atomic state.json read/write with validation
##           — Legacy .done file migration (.bootstrap-checkpoints → state.json)
## @invariants
##   1. State.json keys are Python step NAMES (underscores), NOT numeric indices
##   2. Shell passes hyphenated names; module maps via SHELL_TO_PYTHON_STEP
##   3. Atomic write: write to .json.tmp → rename → state.json
##   4. State.json format: {mode, node, current_step, steps: {name: StepState}, errors, warnings}
##   5. CLI exit codes: 0=done/found/success, 1=not-done/error
##   6. migrate_legacy is idempotent — after .done files are removed, it's a no-op
## @rationale — Extracted from inline python3 -c blocks per AGENTS.md language policy Tier 1
##             (Strangler trigger for new Python code in shell scripts). Eliminates F1
##             step-name/key misalignment by using actual step names as dict keys.
## @changes  2026-07-25 | DevPlan 071 Rev 2 — Created
##           2026-07-30 | DevPlan 086 — Removed secrets-init mapping (deleted in T11),
##                       added missing mappings: ensure-secrets, install-acme, node-update,
##                       converge, audit-log, telegram, deploy-context
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Default paths ────────────────────────────────────────────────
DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"
DEFAULT_LEGACY_DIR = "/var/lib/platform/.bootstrap-checkpoints"

# ── SHELL_TO_PYTHON_STEP mapping (F6 fix: hyphens→underscores) ──
# Maps shell step names (hyphens) to Python step names (underscores).
# This is the SINGLE source of truth for name translation.
# Any new step in either system MUST be added here.
SHELL_TO_PYTHON_STEP: dict[str, str] = {
    "ssh-access": "ssh_access",
    "apt-deps": "apt_deps",
    "tor-proxy": "tor_proxy",
    "install-docker": "install_docker",
    "docker-auth": "docker_auth",
    "user-platform": "create_platform_user",
    "user-ci-deploy": "create_ci_deploy_user",
    "projects-base": "create_projects_base",
    "firewall": "firewall",
    "verify-core": "verify_core",
    "verify-node-configs": "verify_node_configs",
    "decrypt-secrets": "decrypt_secrets",
    "ensure-secrets": "ensure_secrets",
    "read-node-yaml": "read_node_yaml",
    "ghcr-auth": "ghcr_auth",
    "sudoers": "sudoers",
    "install-acme": "install_acme",
    "node-update": "node_update",
    "converge": "converge",
    "audit-log": "audit_log",
    "telegram": "telegram",
    "deploy-context": "deploy_context",
    "metrics-cron": "metrics_cron",  # Shell-only step, no Python equivalent
}

# Reverse mapping: Python step name → shell step name
PYTHON_TO_SHELL_STEP: dict[str, str] = {v: k for k, v in SHELL_TO_PYTHON_STEP.items()}

# ── Valid Python step names (for validation) ──
PYTHON_STEP_NAMES: frozenset[str] = frozenset(SHELL_TO_PYTHON_STEP.values())


# region FUNC__read_state
def _read_state(state_file: str) -> dict:
    """Read and parse state.json. Returns empty dict on missing/corrupt file.

    ## @purpose — Safe state.json reader with graceful degradation.
    ## @io — ⇥ state_file: path to JSON → ⎋ dict (empty if missing/corrupt)
    ## @complexity — O(1) file read + parse
    """
    try:
        with open(state_file) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning("[IMP:7][checkpoint_migration] Cannot read state %s: %s", state_file, e)
        return {}


# endregion FUNC__read_state


# region FUNC__write_state
def _write_state(state_file: str, data: dict) -> bool:
    """Atomically write state.json (tmp + rename). Returns True on success.

    ## @purpose — Atomic state.json write via write-to-tmp-then-rename pattern.
    ## @io — ⇥ state_file: path, data: dict to serialize → ⎋ bool success
    ## @complexity — O(N) where N = step count
    """
    try:
        Path(state_file).parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, state_file)
        return True
    except OSError as e:
        logger.error("[IMP:10][checkpoint_migration] Failed to write %s: %s", state_file, e)
        return False


# endregion FUNC__write_state


# region FUNC__shell_to_python
def _shell_to_python(shell_name: str) -> str:
    """Convert shell step name (hyphens) to Python step name (underscores).

    If the name is already in underscore format, returns it unchanged.
    If the name is unknown, returns it unchanged (graceful fallback).

    ## @purpose — Single translation point for shell→Python step name mapping.
    ## @io — ⇥ shell_name: str like "ssh-access" → ⎋ str like "ssh_access"
    ## @complexity — O(1) dict lookup
    """
    if shell_name in SHELL_TO_PYTHON_STEP:
        return SHELL_TO_PYTHON_STEP[shell_name]
    # Already underscore or unknown — return as-is
    return shell_name


# endregion FUNC__shell_to_python


# region FUNC_is_done
def is_done(state_file: str, shell_step: str) -> int:
    """Check if a step is done. Returns 0 if done, 1 if pending/missing.

    ## @purpose — Shell-facing checkpoint query: does state.json show this
    ##            step as completed? Translates shell hyphen-name to Python
    ##            underscore-name, then looks up in state.json.
    ## @io — ⇥ state_file: str, shell_step: str → ⎋ int (0=done, 1=not done)
    ## @complexity — O(1)
    """
    python_step = _shell_to_python(shell_step)
    data = _read_state(state_file)
    steps = data.get("steps", {})
    step = steps.get(python_step)
    is_completed = step is not None and step.get("status") == "done"
    logger.info(
        "[IMP:8][checkpoint_migration][is_done] Step '%s' (→ '%s'): %s",
        shell_step,
        python_step,
        "done" if is_completed else "pending",
    )
    return 0 if is_completed else 1


# endregion FUNC_is_done


# region FUNC_mark_done
def mark_done(state_file: str, shell_step: str, hash_val: str = "") -> int:
    """Mark a step as done in state.json. Returns 0 on success, 1 on error.

    ## @purpose — Shell-facing checkpoint write: set step status="done"
    ##            in state.json. Translates shell hyphen-name → Python underscore-name.
    ##            Creates the step entry if it doesn't exist.
    ## @io — ⇥ state_file: str, shell_step: str, hash_val: str → ⎋ int (0=ok, 1=error)
    ## @complexity — O(1)
    """
    python_step = _shell_to_python(shell_step)
    data = _read_state(state_file)
    if "steps" not in data:
        data["steps"] = {}
    if python_step not in data["steps"]:
        data["steps"][python_step] = {"name": python_step, "status": "done"}
    else:
        data["steps"][python_step]["status"] = "done"
    if hash_val:
        data["steps"][python_step]["hash"] = hash_val
    if _write_state(state_file, data):
        logger.info(
            "[IMP:9][checkpoint_migration][mark_done] Step '%s' (→ '%s') marked done",
            shell_step,
            python_step,
        )
        return 0
    return 1


# endregion FUNC_mark_done


# region FUNC_force_step
def force_step(state_file: str, shell_step: str) -> int:
    """Force-reset a step: set status="pending" in state.json.

    ## @purpose — Allow shell to re-set a specific step to pending for re-execution.
    ##            Translates shell hyphen-name → Python underscore-name.
    ## @io — ⇥ state_file: str, shell_step: str → ⎋ int (0=ok, 1=error)
    ## @complexity — O(1)
    """
    python_step = _shell_to_python(shell_step)
    data = _read_state(state_file)
    if "steps" in data and python_step in data["steps"]:
        data["steps"][python_step]["status"] = "pending"
        if _write_state(state_file, data):
            logger.info(
                "[IMP:8][checkpoint_migration][force] Step '%s' (→ '%s') reset to pending",
                shell_step,
                python_step,
            )
            return 0
    logger.info(
        "[IMP:7][checkpoint_migration][force] Step '%s' (→ '%s'): not in state — no-op",
        shell_step,
        python_step,
    )
    return 0


# endregion FUNC_force_step


# region FUNC_reset_all
def reset_all(state_file: str) -> int:
    """Delete state.json entirely. Returns 0 on success, 1 on error.

    ## @purpose — Full checkpoint reset (--force mode). Removes the state file,
    ##            forcing a fresh bootstrap start.
    ## @io — ⇥ state_file: str → ⎋ int (0=ok, 1=error)
    ## @complexity — O(1)
    """
    try:
        p = Path(state_file)
        if p.exists():
            p.unlink()
            logger.info("[IMP:9][checkpoint_migration][reset] Removed %s", state_file)
        else:
            logger.info("[IMP:7][checkpoint_migration][reset] %s does not exist — no-op", state_file)
        return 0
    except OSError as e:
        logger.error("[IMP:10][checkpoint_migration][reset] Failed to remove %s: %s", state_file, e)
        return 1


# endregion FUNC_reset_all


# region FUNC_migrate_legacy
def migrate_legacy(legacy_dir: str, state_file: str) -> int:
    """Migrate legacy .done files to name-based state.json. Idempotent.

    For each .bootstrap-step-<shell_name>.done file in legacy_dir:
    1. Extract shell step name from filename
    2. Map via SHELL_TO_PYTHON_STEP
    3. Read .hash file if exists
    4. Write to state.json with Python step name as key
    5. Remove both .done and .hash files

    ## @purpose — One-time migration from old .done file system to unified state.json.
    ##            After migration, legacy dir is empty → subsequent calls are no-op.
    ## @io — ⇥ legacy_dir: str path to old .done files, state_file: str → ⎋ int (0=ok)
    ## @complexity — O(N) where N = number of .done files
    """
    legacy_path = Path(legacy_dir)
    if not legacy_path.is_dir():
        logger.info("[IMP:7][checkpoint_migration][migrate] Legacy dir %s not found — no-op", legacy_dir)
        return 0

    done_files = sorted(legacy_path.glob(".bootstrap-step-*.done"))
    if not done_files:
        logger.info("[IMP:7][checkpoint_migration][migrate] No .done files in %s — no-op", legacy_dir)
        return 0

    # Read existing state or start fresh
    data = _read_state(state_file)
    if "steps" not in data:
        data["steps"] = {}

    migrated_count = 0
    for done_file in done_files:
        # Extract shell step name from filename: .bootstrap-step-ssh-access.done → ssh-access
        fname = done_file.name
        # Remove .bootstrap-step- prefix and .done suffix
        shell_name = fname.removeprefix(".bootstrap-step-").removesuffix(".done")
        python_name = _shell_to_python(shell_name)

        # Read hash if exists
        hash_file = done_file.with_suffix(".hash")
        hash_val = ""
        if hash_file.exists():
            with contextlib.suppress(OSError):
                hash_val = hash_file.read_text().strip()

        # Write to state.json (name-based key)
        entry: dict = {"name": python_name, "status": "done"}
        if hash_val:
            entry["hash"] = hash_val
        data["steps"][python_name] = entry

        # Remove legacy files
        try:
            done_file.unlink()
            if hash_file.exists():
                hash_file.unlink()
        except OSError as e:
            logger.warning("[IMP:7][checkpoint_migration][migrate] Failed to remove legacy files: %s", e)

        logger.info(
            "[IMP:8][checkpoint_migration][migrate] Migrated '%s' → '%s' (hash: %s)",
            shell_name,
            python_name,
            hash_val[:12] if hash_val else "(none)",
        )
        migrated_count += 1

    # Write updated state
    if _write_state(state_file, data):
        logger.info(
            "[IMP:9][checkpoint_migration][migrate] Migration complete: %d steps migrated",
            migrated_count,
        )
    else:
        logger.error("[IMP:10][checkpoint_migration][migrate] Failed to write state after migration")
        return 1

    return 0


# endregion FUNC_migrate_legacy


# region CLI_DISPATCH
def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Subcommands: is-done, mark-done, force, reset, migrate-legacy.

    ## @purpose — CLI dispatch for checkpoint_migration.py. Called from shell with
    ##            subcommand + args. All operations validate state_file exists before
    ##            proceeding (except reset which creates on write).
    ## @io — ⇥ argv: CLI arguments → ⎋ int exit code
    ## @complexity — O(1) dispatch, O(N) for migrate-legacy

    Usage:
        python3 checkpoint_migration.py is-done <state_file> <shell_step>
        python3 checkpoint_migration.py mark-done <state_file> <shell_step> [hash]
        python3 checkpoint_migration.py force <state_file> <shell_step>
        python3 checkpoint_migration.py reset <state_file>
        python3 checkpoint_migration.py migrate-legacy <legacy_dir> <state_file>
    """
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        _print_usage()
        return 1

    command = argv[0]
    args = argv[1:]

    if command == "is-done":
        if len(args) < 2:
            _print_usage()
            return 1
        return is_done(args[0], args[1])

    if command == "mark-done":
        if len(args) < 2:
            _print_usage()
            return 1
        hash_val = args[2] if len(args) > 2 else ""
        return mark_done(args[0], args[1], hash_val)

    if command == "force":
        if len(args) < 2:
            _print_usage()
            return 1
        return force_step(args[0], args[1])

    if command == "reset":
        if len(args) < 1:
            _print_usage()
            return 1
        return reset_all(args[0])

    if command == "migrate-legacy":
        if len(args) < 2:
            _print_usage()
            return 1
        return migrate_legacy(args[0], args[1])

    logger.error("[IMP:10][checkpoint_migration] Unknown command: %s", command)
    _print_usage()
    return 1


# endregion CLI_DISPATCH


# region FUNC__print_usage
def _print_usage() -> None:
    """Print usage information to stderr."""
    print(
        "Usage: python3 checkpoint_migration.py <command> [args...]\n"
        "\n"
        "Commands:\n"
        "  is-done <state_file> <shell_step>    → exit 0 if done, 1 if pending\n"
        "  mark-done <state_file> <shell_step> [hash] → write 'done' status\n"
        "  force <state_file> <shell_step>      → reset step to pending\n"
        "  reset <state_file>                   → delete state.json entirely\n"
        "  migrate-legacy <legacy_dir> <state_file> → import .done files → state.json\n",
        file=sys.stderr,
    )


# endregion FUNC__print_usage


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(main())
