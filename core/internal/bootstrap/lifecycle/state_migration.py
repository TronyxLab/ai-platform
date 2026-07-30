#!/usr/bin/env python3
# GREP_SUMMARY: state-migration, state.json, migrate_state_to_phases, 23-to-14, composite-hash, bootstrap-phase-consolidation
# STRUCTURE: ▶ ┌old state.json (23 keys)┐ → ◇ MIGRATION_MAP lookup → ◇ composite hash: all sub_steps done → phase done → ⊕ new state (14 phase keys) → ⎋ idempotent (no-op if already migrated)
# region MODULE_CONTRACT
## @purpose  Migrate state.json from old 23-key format to new 14-phase format with
##           composite hash logic. Single-use migration for production node upgrade:
##           old state.json has one key per step (ssh_access, apt_deps, …), new format
##           has one key per phase (system_bootstrap, user_accounts, …) with sub_steps.
## @scope    Called ONCE at bootstrap startup after code update. After migration,
##           checkpoint_migration.py is deleted — all checkpoints through state.json directly.
## @invariants
##   1. Does NOT mutate old keys (preserves for rollback)
##   2. Adds only new phase keys (state[phase.value] = {done, sub_steps})
##   3. Composite hash: all sub_steps done → phase.done = True
##   4. Any sub_step missing/failed/pending → phase.done = False
##   5. Idempotent: if state already contains phase keys → no-op (return unchanged)
##   6. Missing old key = sub_step not done → contributes to phase.done = False
## @rationale Production nodes have ~23 old keys in state.json. After consolidation to
##            14 phases, the Python state machine looks for new phase keys. Without migration,
##            all phases would appear pending → full re-bootstrap → needless downtime.
##            Composite hash prevents re-running sub-steps that are already done.
## @changes  2026-07-30 | Created per DevPlan 087 §2.5 — replaces checkpoint_migration.py bridge
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── MIGRATION MAP: old keys → new phase ──
# Each new phase maps to a list of old keys (sub_steps) that comprise it.
# Composite hash logic: ALL old keys done → new phase = done.
MIGRATION_MAP: dict[str, list[str]] = {
    # ── INIT phases (φ1-φ8.5) ──
    "system_bootstrap": ["system_packages", "docker_install", "tor_proxy", "firewall"],
    "user_accounts": ["ssh_access", "user_platform", "user_ci_deploy", "projects_base"],
    "platform_setup": ["platform", "docker", "metrics_cron"],
    "secrets_provision": ["decrypt_secrets", "ensure_secrets", "secrets_init"],
    "node_configuration": ["read_node_yaml", "verify_core", "verify_node_configs"],
    "registry_auth": ["ghcr_auth", "docker_auth"],
    "certificates": ["install_acme", "ssl_provision"],
    "deploy_services": ["deploy_modules", "deploy_context"],
    "converge_services": ["converge"],
    # ── UPDATE phases (φ9-φ13) ──
    "secrets_update": ["decrypt_secrets"],
    "node_config_update": ["read_node_yaml", "verify_core"],
    "registry_update": [
        "ghcr_auth",
        "provision",
        "deliver_overlays",
        "provision_llm_keys",
        "healthcheck",
    ],
    "deploy_update": ["deploy_modules", "ssl_provision", "verify_core", "deploy_context"],
    "converge_update": ["converge"],
}


# region FUNC__is_sub_step_done
def _is_sub_step_done(state: dict, key: str) -> bool:
    """Check if a sub-step key is 'done' in the old state format.

    Supports two formats:
    1. Nested: state["steps"][key]["status"] == "done"
    2. Flat:   state[key]["status"] == "done" or state[key].get("done") == True

    ## @purpose — Flexible sub-step done check that handles both old state formats.
    ## @io — ⇥ state: dict (old state.json), key: str → ⎋ bool
    ## @complexity — O(1)
    """
    # Check nested format: state["steps"][key]
    steps = state.get("steps", {})
    step_entry = steps.get(key)
    if step_entry is not None:
        status = step_entry.get("status", "")
        if status == "done":
            return True
        # Also check boolean done field
        return step_entry.get("done") is True

    # Check flat format: state[key]
    flat_entry = state.get(key)
    if flat_entry is not None:
        if isinstance(flat_entry, dict):
            status = flat_entry.get("status", "")
            if status == "done":
                return True
            if flat_entry.get("done") is True:
                return True
        return False

    # Key not found → not done
    return False


# endregion FUNC__is_sub_step_done


# region FUNC_migrate_state_to_phases
def migrate_state_to_phases(state: dict) -> dict:
    """Migrate old state.json (~23 keys) to new 14-phase format with composite hash.

    Returns a NEW dict with the old keys preserved and new phase keys added.
    Each new phase key has the structure:
        {"done": bool, "sub_steps": {sub_name: {"done": bool}}}

    ## @purpose — One-shot state migration for production node upgrade.
    ##            Composite hash prevents unnecessary re-execution of completed phases.
    ## @io — ⇥ state: dict (old state.json) → ⎋ dict (migrated state with new phase keys)
    ## @complexity — O(P * S) where P = 14 phases, S = sub-steps per phase (max 5)
    ## @invariants
    ##   - Does NOT strip old keys (preserves for rollback)
    ##   - Adds only new phase keys
    ##   - Idempotent: if state already has phase keys → return as-is
    ##   - Missing sub-step → phase.done = False
    """
    if not isinstance(state, dict):
        logger.warning("[IMP:7][MIGRATE] Invalid state type %s — returning empty dict", type(state).__name__)
        return {}

    # ── Check if already migrated: if ANY new phase key exists, assume done ──
    for phase_name in MIGRATION_MAP:
        if phase_name in state:
            logger.info(
                "[IMP:8][MIGRATE] State already contains phase key '%s' — migration already done, no-op",
                phase_name,
            )
            return state

    old_step_count = _count_old_steps(state)
    logger.info(
        "[IMP:9][MIGRATE] Mapping %d old step keys → 14 new phase keys",
        old_step_count,
    )

    # ── Compute composite hash for each phase ──
    for phase_name, sub_keys in MIGRATION_MAP.items():
        sub_steps: dict[str, dict[str, Any]] = {}
        all_done = True

        for sub_key in sub_keys:
            sub_done = _is_sub_step_done(state, sub_key)
            sub_steps[sub_key] = {"done": sub_done}
            if not sub_done:
                all_done = False

        state[phase_name] = {
            "done": all_done,
            "sub_steps": sub_steps,
        }

        done_count = sum(1 for s in sub_steps.values() if s["done"])
        logger.info(
            "[IMP:8][MIGRATE] Composite hash: %s=%s (%d/%d sub-steps done)",
            phase_name,
            all_done,
            done_count,
            len(sub_keys),
        )

    logger.info(
        "[IMP:10][MIGRATE] Migration complete — state has %d new phase keys, old keys preserved for rollback",
        len(MIGRATION_MAP),
    )

    return state


# endregion FUNC_migrate_state_to_phases


# region FUNC__count_old_steps
def _count_old_steps(state: dict) -> int:
    """Count old-style step keys in state dict.

    Counts keys from state["steps"] if present, or from state directly.
    Filters against MIGRATION_MAP values (old key names) for accurate count.

    ## @purpose — Determine how many old step keys exist before migration.
    ## @io — ⇥ state: dict → ⎋ int
    ## @complexity — O(S) where S = old step count
    """
    # Collect all known old keys from MIGRATION_MAP values
    old_keys: set[str] = set()
    for keys in MIGRATION_MAP.values():
        old_keys.update(keys)

    # Check nested and flat formats
    steps = state.get("steps", {})
    if isinstance(steps, dict):
        return sum(1 for k in steps if k in old_keys)

    return sum(1 for k in state if k in old_keys)


# endregion FUNC__count_old_steps
