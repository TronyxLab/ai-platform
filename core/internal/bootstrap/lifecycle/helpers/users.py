#!/usr/bin/env python3
# GREP_SUMMARY: users-helpers, create-user, add-ssh-key, ensure-projects-base, useradd, authorized-keys, converge-r3
# STRUCTURE: ▶ create_user ┌id check → useradd --system┐ → ⚡ add_ssh_key ┌authorized_keys append + chmod 0600┐ → ⚡ ensure_projects_base ┌/opt/projects + converge R3┐ → ⎋
# region MODULE_CONTRACT
## @purpose  User-management I/O-хелперы bootstrap-фаз (пользователи, SSH-ключи, projects base) —
##           извлечены из state_machine (B9 T1, U-08). Все функции публичные.
## @scope    users.py: create_user, add_ssh_key, ensure_projects_base.
##           Используются phases.py (φ2 user_accounts).
## @invariants
##   - create_user идемпотентен (id check перед useradd); системный пользователь с home
##   - add_ssh_key: duplicate-check по содержимому authorized_keys; forced-command префикс
##     для ci-deploy (orchestrator_cli dispatch — SSH_ORIGINAL_COMMAND-диспетчер, B1;
##     единственный писатель ci-deploy ключа, волна 117 D1: setup-node.sh дубли удалены)
##   - ensure_projects_base: /opt/projects ownership ci-deploy + вызов converge R3 (non-fatal)
##   - Все subprocess через shared/subprocess_io.run_subprocess (единый канон, B4)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess

from core.internal.shared.subprocess_io import run_subprocess

logger = logging.getLogger(__name__)


# region FUNC_create_user
## @purpose  Idempotent user creation with optional group membership.
## @io       ⇥ username: str, groups: Optional[list[str]] → ⎋ None
## @complexity O(1)
def create_user(username: str, groups: list[str] | None = None) -> None:
    """Create a system user if not exists."""
    # Check if user exists
    result = subprocess.run(["id", username], capture_output=True, text=True, timeout=10)
    if result.returncode == 0:
        logger.info("[IMP:7][user] User '%s' already exists — skipping creation", username)
        return

    groups_str = ",".join(groups) if groups else ""
    cmd = [
        "useradd",
        "--system",
        "--shell",
        "/bin/bash",
        "--create-home",
        "--home-dir",
        f"/home/{username}",
    ]
    if groups_str:
        cmd.extend(["--groups", groups_str])
    cmd.append(username)
    # B4: единый канон shared/subprocess_io (check=True = lifecycle raise-семантика)
    run_subprocess(cmd, check=True)
    logger.info("[IMP:9][user] User '%s' created", username)


# endregion FUNC_create_user


# region FUNC_add_ssh_key
## @purpose  Add an SSH public key to user's authorized_keys (with forced-command support).
## @io       ⇥ username: str, key: str, forced_command_prefix: str | None = None → ⎋ None
## @complexity O(1)
def add_ssh_key(
    username: str,
    key: str,
    forced_command_prefix: str | None = None,
) -> None:
    """Add an SSH public key to user's authorized_keys."""
    home = f"/home/{username}"
    ssh_dir = os.path.join(home, ".ssh")
    auth_keys = os.path.join(ssh_dir, "authorized_keys")

    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
    # Ensure ownership (B4: non_fatal=True + fatal_rc=(127,) — exit=127 всегда fatal, TRAP[BUG])
    run_subprocess(["chown", f"{username}:{username}", ssh_dir], non_fatal=True, fatal_rc=(127,))

    # Check if key already present
    if os.path.isfile(auth_keys):
        try:
            with open(auth_keys) as f:
                content = f.read()
            if key in content:
                logger.info("[IMP:7][ssh_key] Key already present for %s — skipping", username)
                return
        except OSError:
            pass

    entry = f"{forced_command_prefix} {key}\n" if forced_command_prefix else f"{key}\n"
    with open(auth_keys, "a") as f:
        f.write(entry)
    os.chmod(auth_keys, 0o600)
    run_subprocess(["chown", f"{username}:{username}", auth_keys], non_fatal=True, fatal_rc=(127,))
    logger.info("[IMP:9][ssh_key] SSH key added for %s", username)


# endregion FUNC_add_ssh_key


# region FUNC_ensure_projects_base
## @purpose  Ensure /opt/projects base directory exists with correct ownership + converge R3.
## @io       ⇥ core_dir, node_name → ⎋ None
## @complexity O(1) + subprocess
def ensure_projects_base(core_dir: str, node_name: str) -> None:
    """Ensure /opt/projects base directory exists with correct ownership."""
    # B2: канонический корень проектов — shared/deploy_paths (литерал /opt/projects удалён)
    from core.internal.shared.deploy_paths import projects_base

    projects_dir = str(projects_base())
    os.makedirs(projects_dir, exist_ok=True)
    run_subprocess(["chown", "ci-deploy:ci-deploy", projects_dir], non_fatal=True, fatal_rc=(127,))
    logger.info("[IMP:9][projects_base] %s ownership set to ci-deploy:ci-deploy", projects_dir)

    # Call converge R3
    converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
    if os.path.isfile(converge_script) and node_name:
        logger.info("[IMP:8][projects_base] Calling converge R3 for project scaffold")
        run_subprocess(
            ["bash", converge_script, "--node", node_name, "--units", "R3"],
            non_fatal=True,
            fatal_rc=(127,),
            timeout=120,  # B4: legacy lifecycle default (120) — converge R3 может занимать >30s
        )


# endregion FUNC_ensure_projects_base
