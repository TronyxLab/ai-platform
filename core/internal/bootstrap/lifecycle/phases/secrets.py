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
## @changes  2026-08-06 · DevPlan 140 W4 — persist /etc/age/key.txt УДАЛЁН (W12-on-node-age-key):
##            ключ не пишется на диск ноды; канон env → tmpfs decrypt-only (S-13);
##            /etc/age/key.txt — только restore-first fallback (ручной)
# endregion MODULE_CONTRACT
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping
from types import ModuleType

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
## @io      ⇥ core_dir, node_name, node_yaml, env: Mapping | None (DI — SECRETS_ENV_FILE),
##              helpers: ModuleType | None (DI — 167 D4: namespace декрипт-хелперов;
##              None = lifecycle.helpers.secrets) → ⎋ bool
##          ⚡ raises PlatformFatalError if decryption fails — secrets are critical infrastructure
## @complexity O(S) where S = number of secrets in manifest
## @invariants
##   - Decryption failure is FATAL: continuing with CI defaults would deploy placeholder credentials
##   - secrets.env is sourced into os.environ after decryption
##   - Autogen secrets are managed by secrets_manager module
## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · helpers-namespace injection для decrypt/ensure
## · Rejected: прямой вызов helpers_secrets.decrypt_secrets/ensure_secrets_exist (тест патчил
## ·   2 функции namespace — 2 monkeypatch.setattr; shell-фасад lib/secrets.sh вне unit-скоупа)
## · Reason: seam = тестируемость реального вызова phase_secrets_provision с fake-helper namespace
## ·   (0 патчей, тот же assert на W4-контракте «φ4 не персистит /etc/age/key.txt»)
## · Rev: при формальном выделении SecretsDomainFacade (dataclass) — заменить ModuleType на него
def phase_secrets_provision(
    core_dir: str,
    node_name: str,  # ruff: ignore[ARG001]
    node_yaml: str,  # ruff: ignore[ARG001]
    *,
    env: Mapping[str, str] | None = None,
    helpers: ModuleType | None = None,
) -> bool:
    """φ4: Secrets provisioning — decrypt, ensure, init.

    Pre-check: core_dir exists.
    Execute: decrypt secrets → ensure secrets.env exists → source into environ → init autogen.
    Post-check: secrets.env file present (validated by _ensure_secrets_exist).
    """
    if not os.path.isdir(core_dir):
        msg = f"Core directory not found: {core_dir}"
        raise ConfigNotFoundError(msg)

    active = helpers if helpers is not None else helpers_secrets

    # ── 1. Decrypt AGE-encrypted secrets (FATAL on failure) ──
    try:
        active.decrypt_secrets(core_dir)
        logger.info("[IMP:9][phase:secrets_provision] Secrets decrypted successfully")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_provision] Secrets decryption FAILED — aborting: %s", e)
        msg = f"Secrets decryption failed: {e}"
        raise PlatformFatalError(msg) from e

    # ── 1.5 AGE key НЕ персистится на диск (DevPlan 140 W4, W12-on-node-age-key) ──
    # Канон: ключ приходит env (CI node-update: AGE_SECRET_KEY; bootstrap оператора:
    # AGE_SECRET_KEY_FILE) → tmpfs decrypt-only (/dev/shm temp-key + dd-wipe, S-13,
    # decrypt_secrets.py). /etc/age/key.txt допустим ТОЛЬКО как restore-first fallback
    # (ручной перенос ключа оператором при восстановлении ноды) — φ4 его НЕ создаёт.

    # ── 2. Ensure secrets.env exists + source into environ + generate autogen ──
    try:
        active.ensure_secrets_exist(core_dir, env=env)
        logger.info("[IMP:9][phase:secrets_provision] Secrets verified and autogen secrets generated")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_provision] Secrets verification failed — aborting: %s", e)
        msg = f"Secrets verification failed: {e}"
        raise PlatformFatalError(msg) from e

    # ── 3. Secrets init (placeholder — logic migrated to secrets_manager) ──
    logger.info("[IMP:9][phase:secrets_provision] Secrets init complete (managed by secrets_manager)")

    logger.info("[IMP:9][phase:secrets_provision] φ4 complete — secrets provisioned")
    return True


# endregion FUNC_phase_secrets_provision


# region FUNC_phase_secrets_update
## @purpose φ9: Secrets update (UPDATE mode) — decrypt AGE-encrypted secrets.
##           Corresponds to update step: decrypt_secrets (inside verify_core + provision chain).
## @io      ⇥ core_dir, node_name, node_yaml, env: Mapping | None (DI — SECRETS_ENV_FILE) → ⎋ bool
##          ⚡ raises PlatformFatalError if decryption fails
## @complexity O(1) + subprocess
## @invariants
##   - Same FATAL semantics as init mode: decrypt failure blocks deploy
##   - secrets.env is re-sourced after decryption for fresh env vars
def phase_secrets_update(
    core_dir: str,
    node_name: str,  # ruff: ignore[ARG001]
    node_yaml: str,  # ruff: ignore[ARG001]
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """φ9: Secrets update — decrypt secrets (UPDATE mode).

    Pre-check: core_dir exists.
    Execute: decrypt AGE-encrypted secrets.
    Post-check: secrets.env present after decryption.
    """
    if not os.path.isdir(core_dir):
        msg = f"Core directory not found: {core_dir}"
        raise ConfigNotFoundError(msg)

    # ── Decrypt secrets (FATAL on failure) ──
    try:
        helpers_secrets.decrypt_secrets(core_dir)
        logger.info("[IMP:9][phase:secrets_update] Secrets decrypted successfully (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_update] Secrets decryption FAILED — aborting update: %s", e)
        msg = f"Secrets decryption failed during update: {e}"
        raise PlatformFatalError(msg) from e

    # Re-source secrets into environ (same as init)
    try:
        helpers_secrets.ensure_secrets_exist(core_dir, env=env)
        logger.info("[IMP:9][phase:secrets_update] Secrets re-sourced and verified (update)")
    except (PlatformError, subprocess.TimeoutExpired) as e:
        logger.error("[IMP:10][phase:secrets_update] Secrets re-source FAILED — aborting update: %s", e)
        msg = f"Secrets re-source failed during update: {e}"
        raise PlatformFatalError(msg) from e

    logger.info("[IMP:9][phase:secrets_update] φ9 complete — secrets updated")
    return True


# endregion FUNC_phase_secrets_update
