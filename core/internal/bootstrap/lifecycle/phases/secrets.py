#!/usr/bin/env python3
# GREP_SUMMARY: phases-secrets, secrets-provision, secrets-update, decrypt, ensure-secrets, bootstrap-phase, E3
# STRUCTURE: ▶ secrets-фазы (φ4 φ9) → ◇ each: pre-check → decrypt → ensure → ⊕ LDD logs → ⎋ bool/raise
# region MODULE_CONTRACT
## @purpose  Secrets-domain bootstrap phases (DevPlan 119 E3) — φ4 secrets_provision, φ9
##           secrets_update. Интерфейс (core_dir, node_name, node_yaml) -> bool сохранён.
## @scope    Consumed by lifecycle/phases/__init__.py (агрегатор) → state_machine.py execute_phase.
##           Извлечено из lifecycle/phases.py (DevPlan 119 E3, AUDIT-2 M3).
## @invariants
##   1. Every phase is idempotent — safe to re-run on a provisioned node.
##   2. Decryption failure is FATAL (PlatformFatalError) — secrets are critical infrastructure.
##   3. secrets.env re-sourced into os.environ after decryption.
## @rationale E3: phases.py 1080 LOC → доменные модули. secrets-фазы — decrypt/ensure-домен.
## @changes  2026-08-02 · DevPlan 119 E3 — экстракция из lifecycle/phases.py
# endregion MODULE_CONTRACT
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──
from core.internal.bootstrap.lifecycle.helpers import secrets as helpers_secrets


# region FUNC_phase_secrets_provision
## @purpose φ4: Decrypt and provision secrets — decrypt AGE-encrypted secrets, ensure secrets.env
##           exists, initialize autogen secrets. BLOCKS deploy if it fails.
##           Corresponds to init steps: decrypt_secrets (12), ensure_secrets (13), secrets_init (14).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
##          ⚡ raises PlatformFatalError if decryption fails — secrets are critical infrastructure
## @complexity O(S) where S = number of secrets in manifest
## @invariants
##   - Decryption failure is FATAL: continuing with CI defaults would deploy placeholder credentials
##   - secrets.env is sourced into os.environ after decryption
##   - Autogen secrets are managed by secrets_manager module
def phase_secrets_provision(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ4: Secrets provisioning — decrypt, ensure, init.

    Pre-check: core_dir exists.
    Execute: decrypt secrets → ensure secrets.env exists → source into environ → init autogen.
    Post-check: secrets.env file present (validated by _ensure_secrets_exist).
    """
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    # ── 1. Decrypt AGE-encrypted secrets (FATAL on failure) ──
    try:
        helpers_secrets.decrypt_secrets(core_dir)
        logger.info("[IMP:9][phase:secrets_provision] Secrets decrypted successfully")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_provision] Secrets decryption FAILED — aborting: %s", e)
        raise PlatformFatalError(f"Secrets decryption failed: {e}") from e

    # ── 1.5 Persist AGE key to /etc/age/key.txt (node canonical location) ──
    # CI node-update (core-deploy ssh) не несёт AGE_SECRET_KEY env — ключ обязан жить
    # на ноде (state_machine precondition φ4 и node_detect.detect_age_key читают его).
    # Pre-existing gap: fresh bootstrap → CI node-update decrypt fail (AGE_SECRET_KEY not set).
    age_key = os.environ.get("AGE_SECRET_KEY") or os.environ.get("SOPS_AGE_KEY")
    if age_key:
        try:
            age_dir = Path("/etc/age")
            age_dir.mkdir(mode=0o700, exist_ok=True)
            key_file = age_dir / "key.txt"
            tmp_path = key_file.with_suffix(".tmp")
            tmp_path.write_text(age_key.strip() + "\n", encoding="utf-8")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, key_file)
            logger.info("[IMP:9][phase:secrets_provision] AGE key persisted to %s", key_file)
        except OSError as e:
            logger.warning("[IMP:7][phase:secrets_provision] Cannot persist AGE key to /etc/age/key.txt: %s", e)

    # ── 2. Ensure secrets.env exists + source into environ + generate autogen ──
    try:
        helpers_secrets.ensure_secrets_exist(core_dir)
        logger.info("[IMP:9][phase:secrets_provision] Secrets verified and autogen secrets generated")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_provision] Secrets verification failed — aborting: %s", e)
        raise PlatformFatalError(f"Secrets verification failed: {e}") from e

    # ── 3. Secrets init (placeholder — logic migrated to secrets_manager) ──
    logger.info("[IMP:9][phase:secrets_provision] Secrets init complete (managed by secrets_manager)")

    logger.info("[IMP:9][phase:secrets_provision] φ4 complete — secrets provisioned")
    return True


# endregion FUNC_phase_secrets_provision


# region FUNC_phase_secrets_update
## @purpose φ9: Secrets update (UPDATE mode) — decrypt AGE-encrypted secrets.
##           Corresponds to update step: decrypt_secrets (inside verify_core + provision chain).
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
##          ⚡ raises PlatformFatalError if decryption fails
## @complexity O(1) + subprocess
## @invariants
##   - Same FATAL semantics as init mode: decrypt failure blocks deploy
##   - secrets.env is re-sourced after decryption for fresh env vars
def phase_secrets_update(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ9: Secrets update — decrypt secrets (UPDATE mode).

    Pre-check: core_dir exists.
    Execute: decrypt AGE-encrypted secrets.
    Post-check: secrets.env present after decryption.
    """
    if not os.path.isdir(core_dir):
        raise ConfigNotFoundError(f"Core directory not found: {core_dir}")

    # ── Decrypt secrets (FATAL on failure) ──
    try:
        helpers_secrets.decrypt_secrets(core_dir)
        logger.info("[IMP:9][phase:secrets_update] Secrets decrypted successfully (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_update] Secrets decryption FAILED — aborting update: %s", e)
        raise PlatformFatalError(f"Secrets decryption failed during update: {e}") from e

    # Re-source secrets into environ (same as init)
    try:
        helpers_secrets.ensure_secrets_exist(core_dir)
        logger.info("[IMP:9][phase:secrets_update] Secrets re-sourced and verified (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_update] Secrets re-source FAILED — aborting update: %s", e)
        raise PlatformFatalError(f"Secrets re-source failed during update: {e}") from e

    logger.info("[IMP:9][phase:secrets_update] φ9 complete — secrets updated")
    return True


# endregion FUNC_phase_secrets_update
